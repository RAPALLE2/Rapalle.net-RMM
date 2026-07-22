"""
routers/relay_routes.py
-----------------------
Explorer-Relay als minimaler WebDAV-Server.

Idee (aus Sicht des Users):
  1. Im Betriebssystem ein Netzlaufwerk verbinden, das auf das Backend zeigt:
        Windows:  \\\\<backend-ip>@<port>\\dav\\<client-id>   (bzw. http://<ip>:<port>/dav/<client-id>)
        Linux:    davs?://<backend-ip>:<port>/dav/<client-id>
     Angemeldet wird mit den GLEICHEN Zugangsdaten wie im Dashboard
     (lokaler Login ODER SSO/Realm über "user@realm-id" als Benutzername).
  2. Im Dashboard bei einem Client den Explorer auf "Relay" schalten -> dort
     wird die fertige URL angezeigt, die man ins Netzlaufwerk einträgt.

Ist das Laufwerk verbunden, erscheinen ALLE Laufwerke des Clients als Ordner:
  <netzlaufwerk>\\C\\Windows\\...   (Windows-Client)
  <netzlaufwerk>\\root\\etc\\...    (Linux-Client, Laufwerke = Mountpoints)

Technik: Wir implementieren nur die WebDAV-Methoden, die Windows-Explorer,
macOS Finder und der Linux-Dateimanager wirklich brauchen (OPTIONS, PROPFIND,
GET, PUT, MKCOL, DELETE, MOVE, HEAD). Jede Operation wird über den bestehenden
Agent-Kanal (request_fs_*) auf dem Ziel-Client ausgeführt - es wird KEINE
zusätzliche Bibliothek benötigt.

WICHTIG: Diese Routen dürfen NICHT hinter dem normalen JWT-Auth hängen, weil
Netzlaufwerk-Clients HTTP-Basic-Auth sprechen. Deshalb prüfen wir hier selbst.
"""

import base64
import hashlib
import logging
import os
import time
import uuid as _uuid
import xml.sax.saxutils as _xml
from datetime import datetime, timezone
from email.utils import formatdate

from fastapi import APIRouter, Request, Response
from starlette.responses import PlainTextResponse, StreamingResponse

from app import db
from app.auth import authenticate_local, authenticate_realm, can_access_client
from app.sockets import (
    state, request_fs_list, request_fs_read, request_fs_op,
)

router = APIRouter(tags=["relay"])
_log = logging.getLogger("relay")

RELAY_VERSION = "5"
REALM = db.RELAY_REALM

# Einfacher Nonce-Speicher für Digest-Auth (in-memory, reicht für den Zweck).
_nonces: dict[str, float] = {}
_NONCE_TTL = 300  # Sekunden


@router.get("/relay-health")
async def relay_health():
    """Health-Check OHNE Login (zum Prüfen, ob das Modul geladen ist)."""
    return {"relay": "ok", "version": RELAY_VERSION}


# ------------------------------------------------------------------
# Verwaltungs-API (normale Dashboard-Auth über JWT). Freigabe ERFOLGT PRO CLIENT.
# ------------------------------------------------------------------
from app.auth import get_current_user, require_admin, require_perm, user_has_permission, can_access_client  # noqa: E402
from fastapi import Depends, HTTPException  # noqa: E402


@router.get("/api/relay/status")
async def relay_status(client_id: str, user: dict = Depends(get_current_user)):
    """Ist der Relay für DIESEN Client freigegeben?"""
    if not can_access_client(user, client_id):
        raise HTTPException(403, "Kein Zugriff auf diesen Client")
    return {"enabled": db.is_client_relay_enabled(client_id),
            "client_id": client_id}


@router.post("/api/relay/toggle")
async def relay_toggle(client_id: str, auto_close_minutes: int = 0,
                       user: dict = Depends(get_current_user)):
    """Relay-Freigabe für diesen Client an/aus.

    Benötigt das Recht 'Relay starten' (c_relay) für diesen Client. Eine
    unbegrenzte Freigabe (auto_close_minutes = 0 = "nie") setzt zusätzlich
    'Relay unbegrenzt' voraus (global relay_unlimited ODER pro Client
    c_relay_unlimited).
    """
    require_perm(user, "c_relay", client_id)
    new_state = not db.is_client_relay_enabled(client_id)
    # "Nie schließen" nur mit Unbegrenzt-Recht erlauben.
    if new_state and (not auto_close_minutes or auto_close_minutes <= 0):
        may_unlimited = (user_has_permission(user, "relay_unlimited")
                         or user_has_permission(user, "c_relay_unlimited", client_id))
        if not may_unlimited:
            raise HTTPException(
                403, "Unbegrenzte Relay-Freigabe nicht erlaubt – bitte eine "
                     "automatische Schließzeit wählen.")
    expires_at = 0
    if new_state and auto_close_minutes and auto_close_minutes > 0:
        expires_at = int(time.time() * 1000) + int(auto_close_minutes) * 60_000
    db.set_client_relay_enabled(client_id, new_state, expires_at)
    if new_state:
        detail = "freigegeben"
        if auto_close_minutes and auto_close_minutes > 0:
            detail += f" (auto-schließen nach {int(auto_close_minutes)} min)"
    else:
        detail = "gesperrt"
    db.add_audit_entry(user["username"], "relay.toggle", target=client_id,
                       details=detail)
    return {"enabled": new_state, "client_id": client_id,
            "relay_expires_at": expires_at}


# ------------------------------------------------------------------
# Auth-Helfer
# ------------------------------------------------------------------
def _resolve_user(username: str):
    """username -> (user_dict|None). 'name@realm-id' wird als SSO-User erkannt.
    Zusätzlich werden DOMAIN\\user / RECHNER\\user abgefangen."""
    username = (username or "").strip()
    if "\\" in username:
        username = username.split("\\", 1)[1]
    # Realm-Suffix nur fürs Auffinden abschneiden - der User wird per DB gesucht.
    lookup = username.rsplit("@", 1)[0] if "@" in username else username
    return db.get_user_by_username_any(lookup)


def _new_nonce() -> str:
    now = time.time()
    # abgelaufene Nonces aufräumen
    for n, ts in list(_nonces.items()):
        if now - ts > _NONCE_TTL:
            _nonces.pop(n, None)
    nonce = hashlib.md5(f"{now}:{os.urandom(8).hex()}".encode()).hexdigest()
    _nonces[nonce] = now
    return nonce


def _parse_digest(header: str) -> dict:
    """Zerlegt einen 'Digest ...'-Authorization-Header in ein dict."""
    out = {}
    body = header[len("Digest "):]
    # Felder sind kommagetrennt; Werte teils in Anführungszeichen.
    import re
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|([^,]+))', body):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3).strip()
    return out


def _check_digest(header: str, method: str) -> tuple[dict | None, str]:
    """Prüft Digest-Auth. HA1 wird ZUR LAUFZEIT aus dem (entschlüsselten)
    Konto-Passwort und dem GENAU vom Client gesendeten Benutzernamen berechnet -
    so funktioniert es auch, wenn Windows 'RECHNER\\benutzer' o.ä. schickt."""
    d = _parse_digest(header)
    sent_user = d.get("username", "")
    nonce = d.get("nonce", "")
    uri = d.get("uri", "")
    _log.info("Digest-Versuch: username=%r realm=%r uri=%r nc=%r qop=%r",
              sent_user, d.get("realm"), uri, d.get("nc"), d.get("qop"))
    if nonce not in _nonces:
        return None, "nonce unbekannt/abgelaufen"
    user = _resolve_user(sent_user)
    if not user:
        return None, f"Benutzer '{sent_user}' nicht gefunden"
    password = db.get_relay_secret(user["id"])
    if not password:
        return None, "kein hinterlegtes Passwort (bitte einmal am Dashboard anmelden)"

    # HA1 exakt mit dem vom Client gesendeten Benutzernamen + Realm bilden.
    ha1 = hashlib.md5(f"{sent_user}:{d.get('realm','')}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    if d.get("qop"):
        resp = hashlib.md5(
            f"{ha1}:{nonce}:{d.get('nc','')}:{d.get('cnonce','')}:{d.get('qop')}:{ha2}".encode()
        ).hexdigest()
    else:
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()

    if resp == d.get("response"):
        return user, "ok (digest)"
    return None, "Digest-Antwort falsch (Passwort?)"


def _check_basic(header: str) -> tuple[dict | None, str]:
    """Basic-Auth mit dem normalen Konto-Passwort (lokal oder SSO name@realm-id)."""
    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8", "replace")
        username, _, password = raw.partition(":")
    except Exception as e:
        return None, f"Header nicht dekodierbar: {e}"

    username = username.strip()
    if "\\" in username:
        username = username.split("\\", 1)[1]

    if "@" in username:
        name, _, realm_id = username.rpartition("@")
        try:
            user = authenticate_realm(name, password, realm_id)
            if user:
                try: db.store_relay_secret(user["id"], password)
                except Exception: pass
                return user, "ok (basic/realm)"
        except Exception:
            pass
    try:
        user = authenticate_local(username, password)
        if user:
            try: db.store_relay_secret(user["id"], password)
            except Exception: pass
            return user, "ok (basic/lokal)"
    except Exception:
        pass
    return None, "Benutzername/Passwort abgelehnt"


def _authenticate(request: Request, method: str) -> tuple[dict | None, str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("digest "):
        return _check_digest(header, method)
    if header.lower().startswith("basic "):
        return _check_basic(header)
    return None, "kein Auth-Header"


def _need_auth() -> Response:
    """
    Fordert Authentifizierung an. Wir bieten Digest UND Basic an:
    Windows nutzt über HTTP automatisch Digest (ohne Registry-Änderung!),
    macOS/Linux/Browser können Basic verwenden.
    """
    nonce = _new_nonce()
    digest = (f'Digest realm="{REALM}", qop="auth", '
              f'nonce="{nonce}", algorithm=MD5')
    basic = f'Basic realm="{REALM}"'
    # Digest zuerst anbieten (Windows bevorzugt das über HTTP).
    resp = Response(status_code=401)
    resp.headers.append("WWW-Authenticate", digest)
    resp.headers.append("WWW-Authenticate", basic)
    return resp


# ------------------------------------------------------------------
# Pfad-Zerlegung:  /dav/<client_id>/<DRIVE>/<rest...>
# <DRIVE> ist ein "sicherer" Laufwerksname (z.B. "C" für "C:\\", "root" für "/").
# Wir bilden ihn eindeutig auf den echten Pfad des Clients ab.
# ------------------------------------------------------------------
def _drive_map(client_id: str, drives: list[dict]) -> dict:
    """Bildet einen url-sicheren Laufwerksnamen -> echter Root-Pfad ab (nur Windows)."""
    mapping = {}
    for d in drives:
        real = d.get("path", "")
        if not real:
            continue
        if real == "/":
            key = "root"
        elif len(real) >= 2 and real[1] == ":":
            key = real[0].upper()             # "C:\\" -> "C"
        else:
            key = real.strip("/").replace("/", "_") or "root"
        mapping[key] = real
    return mapping


def _is_posix(drives: list[dict]) -> bool:
    """True, wenn der Client ein POSIX-System ist (Linux/Mac): Pfade beginnen mit
    '/' und sind keine Windows-Laufwerke ('C:\\'). Dann hosten wir direkt '/'
    statt einer Zwischenebene 'root'."""
    for d in drives:
        p = d.get("path", "")
        if len(p) >= 2 and p[1] == ":":
            return False   # Windows-Laufwerk gefunden
    return True


def _client_display_names(clients: list[dict]) -> dict:
    """display_name -> client_id, für die als Relay freigegebenen Clients.
    Nutzt den Hostnamen (Anzeigename); bei Dubletten wird die Kurz-ID angehängt."""
    counts = {}
    for c in clients:
        h = (c.get("hostname") or c["id"]).strip() or c["id"]
        counts[h.lower()] = counts.get(h.lower(), 0) + 1
    mapping = {}
    for c in clients:
        h = (c.get("hostname") or c["id"]).strip() or c["id"]
        name = h if counts[h.lower()] == 1 else f"{h}-{c['id'][:8]}"
        mapping[name] = c["id"]
    return mapping


def _resolve_client_segment(segment: str, user) -> tuple[str | None, str]:
    """Übersetzt das erste Pfadsegment (Anzeigename ODER Client-ID) in die
    Client-ID. Rückgabe: (client_id|None, anzeige_segment)."""
    import urllib.parse
    seg = urllib.parse.unquote(segment or "")
    enabled = [c for c in db.list_relay_enabled_clients() if can_access_client(user, c["id"])]
    names = _client_display_names(enabled)
    # 1) exakter Anzeigename
    for name, cid in names.items():
        if name.lower() == seg.lower():
            return cid, name
    # 2) Rückwärtskompatibel: direkte Client-ID
    for c in enabled:
        if c["id"] == seg:
            # Anzeigenamen zu dieser ID zurückgeben
            for name, cid in names.items():
                if cid == c["id"]:
                    return c["id"], name
            return c["id"], seg
    return None, seg


async def _drives_for(client_id: str) -> list[dict]:
    """Die "Laufwerke" (Top-Level) eines Clients = fs-list mit leerem Pfad."""
    return await request_fs_list(client_id, "")


def _join(root: str, rest: str) -> str:
    """Fügt Root-Pfad + Rest zusammen, respektiert Windows- vs. POSIX-Trenner."""
    rest = rest.strip("/")
    if not rest:
        return root
    if "\\" in root or (len(root) >= 2 and root[1] == ":"):
        # Windows-Pfad
        base = root.rstrip("\\")
        return base + "\\" + rest.replace("/", "\\")
    return root.rstrip("/") + "/" + rest


def _parse(path: str) -> tuple[str | None, list[str]]:
    """/dav/<segment>/<a>/<b> -> (segment, [a, b]). segment = Anzeigename ODER ID."""
    parts = [p for p in path.split("/") if p not in ("", "dav")]
    if not parts:
        return None, []
    return parts[0], parts[1:]


# ==================================================================
# Hierarchie-Navigation: Tenant -> Location -> (Ordner...) -> Client
# ==================================================================
NO_TENANT = "(ohne Mandant)"
NO_LOCATION = "(ohne Standort)"


def _relay_clients(user) -> list[dict]:
    """Alle für den Relay freigegebenen und für den User sichtbaren Clients."""
    return [c for c in db.list_relay_enabled_clients() if can_access_client(user, c["id"])]


def _dedup(pairs: list[tuple[str, str]]) -> dict:
    """[(name, key)] -> {display_name: key}. Bei Namensdubletten wird die
    Kurz-Kennung angehängt, damit jeder Ordner eindeutig ist."""
    from collections import Counter
    cnt = Counter(n.lower() for n, _ in pairs)
    out = {}
    for n, k in pairs:
        disp = n if cnt[n.lower()] == 1 else f"{n}-{str(k)[:8]}"
        out[disp] = k
    return out


def _match(dispmap: dict, seg: str):
    """URL-decodiertes Segment gegen Anzeigenamen (case-insensitiv) auflösen."""
    import urllib.parse
    s = urllib.parse.unquote(seg or "").lower()
    for disp, key in dispmap.items():
        if disp.lower() == s:
            return disp, key
    return None, None


def _tenant_display(clients: list[dict]) -> dict:
    names = {t["id"]: t["name"] for t in db.list_tenants()}
    seen = {}
    for c in clients:
        tid = c.get("tenant_id") or ""
        seen[tid] = names.get(tid, NO_TENANT) if tid else NO_TENANT
    return _dedup([(n, k or "\u0000") for k, n in seen.items()])


def _location_display(clients: list[dict]) -> dict:
    names = {l["id"]: l["name"] for l in db.list_locations()}
    seen = {}
    for c in clients:
        lid = c.get("location_id") or ""
        seen[lid] = names.get(lid, NO_LOCATION) if lid else NO_LOCATION
    return _dedup([(n, k or "\u0000") for k, n in seen.items()])


def _folder_parent_map() -> dict:
    return {f["id"]: f.get("parent_folder_id") for f in db.list_folders()}


def _populated_folders(clients: list[dict], parent_map: dict) -> set:
    """Ordner-IDs, die (direkt oder über Unterordner) freigegebene Clients enthalten."""
    pop = set()
    for c in clients:
        fid = c.get("folder_id")
        while fid and fid not in pop:
            pop.add(fid)
            fid = parent_map.get(fid)
    return pop


def _folder_display(location_key: str, parent_folder, pop: set) -> dict:
    fnames = {f["id"]: f["name"] for f in db.list_folders()}
    pairs = []
    for f in db.list_folders(location_key or None):
        if f["id"] in pop and (f.get("parent_folder_id") or None) == (parent_folder or None):
            pairs.append((fnames[f["id"]], f["id"]))
    return _dedup(pairs)


def _client_display_at(clients: list[dict], folder) -> dict:
    pairs = [((c.get("hostname") or c["id"]), c["id"])
             for c in clients if (c.get("folder_id") or None) == (folder or None)]
    return _dedup(pairs)


def _key_real(key: str):
    """'\u0000'-Sentinel -> '' (kein Tenant/keine Location)."""
    return "" if key == "\u0000" else key


def _walk_relay(segments: list[str], user) -> dict:
    """Läuft die Pfadsegmente durch die Hierarchie. Rückgabe u.a.:
    node = 'root'|'tenant'|'location'|'folder'|'client'|None(=404),
    consumed = verbrauchte Anzeigenamen (für href-Aufbau),
    bei 'client': client_id, client_display, rest (Pfad danach),
    für Listen: die passende Client-Teilmenge + Ebenen-Infos."""
    clients = _relay_clients(user)
    consumed: list[str] = []

    if not segments:
        return {"node": "root", "consumed": consumed, "clients": clients}

    # --- Tenant ---
    tmap = _tenant_display(clients)
    tdisp, tkey = _match(tmap, segments[0])
    if tkey is None:
        return {"node": None}
    consumed.append(tdisp)
    tkey_r = _key_real(tkey)
    t_clients = [c for c in clients if (c.get("tenant_id") or "") == tkey_r]
    if len(segments) == 1:
        return {"node": "tenant", "consumed": consumed, "clients": t_clients}

    # --- Location ---
    lmap = _location_display(t_clients)
    ldisp, lkey = _match(lmap, segments[1])
    if lkey is None:
        return {"node": None}
    consumed.append(ldisp)
    lkey_r = _key_real(lkey)
    l_clients = [c for c in t_clients if (c.get("location_id") or "") == lkey_r]
    if len(segments) == 2:
        return {"node": "location", "consumed": consumed, "clients": l_clients,
                "location_key": lkey_r}

    # --- Ordner (verschachtelt) + Client ---
    parent_map = _folder_parent_map()
    pop = _populated_folders(l_clients, parent_map)
    cur_folder = None
    i = 2
    while i < len(segments):
        seg = segments[i]
        fmap = _folder_display(lkey_r, cur_folder, pop)
        fdisp, fkey = _match(fmap, seg)
        if fkey is not None:
            consumed.append(fdisp)
            cur_folder = fkey
            i += 1
            continue
        cmap = _client_display_at(l_clients, cur_folder)
        cdisp, ckey = _match(cmap, seg)
        if ckey is not None:
            consumed.append(cdisp)
            return {"node": "client", "consumed": consumed, "client_id": ckey,
                    "client_display": cdisp, "rest": segments[i + 1:]}
        return {"node": None}

    # Pfad endet auf einem Ordner -> dessen Inhalt auflisten
    return {"node": "folder", "consumed": consumed, "clients": l_clients,
            "location_key": lkey_r, "folder_key": cur_folder}


def _relay_children(walk: dict, user) -> list[str]:
    """Anzeigenamen der Kind-Ordner eines Zwischenknotens (alle sind Ordner)."""
    node = walk["node"]
    if node == "root":
        return list(_tenant_display(walk["clients"]).keys())
    if node == "tenant":
        return list(_location_display(walk["clients"]).keys())
    if node in ("location", "folder"):
        parent_map = _folder_parent_map()
        pop = _populated_folders(walk["clients"], parent_map)
        parent = walk.get("folder_key")
        folders = list(_folder_display(walk["location_key"], parent, pop).keys())
        cl = list(_client_display_at(walk["clients"], parent).keys())
        return folders + cl
    return []


async def _resolve_real_path(client_id: str, sub: list[str]) -> tuple[str | None, list[dict] | None]:
    """
    Übersetzt [laufwerk/pfad...] in den echten Client-Pfad.
    - POSIX (Linux/Mac): der Client-Ordner IST '/' -> kein 'root'-Zwischenordner.
    - Windows: erste Ebene = Laufwerk (C, D, ...).
    Rückgabe: (real_path oder None=Laufwerksliste, drives).
    """
    drives = await _drives_for(client_id)
    if _is_posix(drives):
        # '/' direkt hosten: /dav/<client>/etc -> /etc ; Root -> '/'
        return _join("/", "/".join(sub)), drives
    # Windows: Laufwerks-Ebene
    if not sub:
        return None, drives          # Root: Laufwerks-Liste (C:, D:, ...)
    mapping = _drive_map(client_id, drives)
    drive = sub[0]
    if drive not in mapping:
        return "__404__", drives
    return _join(mapping[drive], "/".join(sub[1:])), drives


# ------------------------------------------------------------------
# WebDAV-XML-Helfer
# ------------------------------------------------------------------
def _http_date(ms: int) -> str:
    if not ms:
        return formatdate(usegmt=True)
    return formatdate(ms / 1000, usegmt=True)


def _iso_date(ms: int) -> str:
    if not ms:
        ms = 0
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _propfind_response(href: str, name: str, is_dir: bool, size: int, mtime: int) -> str:
    href = _xml.escape(href)
    disp = _xml.escape(name)
    if is_dir:
        restype = "<D:collection/>"
        content = ""
    else:
        restype = ""
        content = (f"<D:getcontentlength>{size}</D:getcontentlength>"
                   f"<D:getcontenttype>application/octet-stream</D:getcontenttype>")
    return f"""<D:response>
  <D:href>{href}</D:href>
  <D:propstat>
    <D:prop>
      <D:displayname>{disp}</D:displayname>
      <D:resourcetype>{restype}</D:resourcetype>
      {content}
      <D:getlastmodified>{_http_date(mtime)}</D:getlastmodified>
      <D:creationdate>{_iso_date(mtime)}</D:creationdate>
    </D:prop>
    <D:status>HTTP/1.1 200 OK</D:status>
  </D:propstat>
</D:response>"""


def _multistatus(responses: list[str]) -> Response:
    body = ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<D:multistatus xmlns:D="DAV:">\n' + "\n".join(responses) + "\n</D:multistatus>")
    return Response(content=body, status_code=207,
                    media_type='application/xml; charset="utf-8"',
                    headers={"DAV": "1, 2", "MS-Author-Via": "DAV"})


# ------------------------------------------------------------------
# ------------------------------------------------------------------
# WICHTIG für "net use \\host@port\dav" (Windows-Mini-Redirector):
# Windows prüft VOR dem Verbinden mit "OPTIONS /" die SERVER-WURZEL,
# nicht /dav. Antwortet dort nur das statische Frontend (ohne DAV-Header),
# bricht Windows mit "Systemfehler 67: Netzwerkname nicht gefunden" ab.
# Deshalb beantworten wir OPTIONS und PROPFIND auf "/" hier als WebDAV-
# Server (GET "/" liefert weiterhin ganz normal das Dashboard aus).
# ------------------------------------------------------------------
@router.api_route("/", methods=["OPTIONS", "PROPFIND"], include_in_schema=False)
async def dav_root_probe(request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=200, headers={
            "DAV": "1, 2",
            "Allow": "OPTIONS, PROPFIND, GET, HEAD, PUT, MKCOL, DELETE, MOVE, PROPPATCH, LOCK, UNLOCK",
            "MS-Author-Via": "DAV",
        })
    # PROPFIND auf "/": minimale Antwort - Wurzel als Ordner, darin "dav".
    # Verrät nichts Sensibles und macht den Redirector glücklich.
    responses = [_propfind_response("/", "RMM", True, 0, 0)]
    if request.headers.get("depth", "1") != "0":
        responses.append(_propfind_response("/dav/", "dav", True, 0, 0))
    return _multistatus(responses)


# Der eigentliche WebDAV-Endpunkt: fängt ALLE Methoden unter /dav ab.
# ------------------------------------------------------------------
@router.api_route("/dav",
                  methods=["OPTIONS", "PROPFIND", "GET", "HEAD", "PUT",
                           "MKCOL", "DELETE", "MOVE", "PROPPATCH", "LOCK", "UNLOCK"])
@router.api_route("/dav/{full_path:path}",
                  methods=["OPTIONS", "PROPFIND", "GET", "HEAD", "PUT",
                           "MKCOL", "DELETE", "MOVE", "PROPPATCH", "LOCK", "UNLOCK"])
async def dav(request: Request, full_path: str = ""):
    method = request.method.upper()

    # OPTIONS beantwortet Windows/macOS oft VOR der Authentifizierung.
    if method == "OPTIONS":
        return Response(status_code=200, headers={
            "DAV": "1, 2",
            "Allow": "OPTIONS, PROPFIND, GET, HEAD, PUT, MKCOL, DELETE, MOVE, PROPPATCH, LOCK, UNLOCK",
            "MS-Author-Via": "DAV",
        })

    user, reason = _authenticate(request, method)
    has_auth = "authorization" in {k.lower() for k in request.headers.keys()}
    if not user:
        _log.warning("Relay-Auth abgelehnt [%s %s] auth_header=%s: %s",
                     method, full_path, has_auth, reason)
        return _need_auth()
    _log.info("Relay-Auth OK [%s %s] user=%s (%s)", method, full_path,
              user.get("username"), reason)

    import urllib.parse as _up
    segments = [p for p in full_path.split("/") if p not in ("", "dav")]
    walk = _walk_relay(segments, user)
    node = walk.get("node")

    if node is None:
        return PlainTextResponse("Not Found", status_code=404)

    def _href_base(consumed):
        return "/dav" + ("/" + "/".join(_up.quote(s) for s in consumed) if consumed else "")

    # --- Zwischenebenen (Mandant/Standort/Ordner) als Ordner auflisten ---
    if node in ("root", "tenant", "location", "folder"):
        base_href = _href_base(walk["consumed"])
        self_name = walk["consumed"][-1] if walk["consumed"] else "RMM Relay"
        children = _relay_children(walk, user)
        if method == "PROPFIND":
            responses = [_propfind_response(base_href + "/", self_name, True, 0, 0)]
            if request.headers.get("depth", "1") != "0":
                for n in children:
                    responses.append(_propfind_response(
                        f"{base_href}/{_up.quote(n)}/", n, True, 0, 0))
            return _multistatus(responses)
        if method in ("GET", "HEAD"):
            if method == "HEAD":
                return Response(status_code=200)
            rows = "".join(
                f'<li>📁 <a href="{base_href}/{_up.quote(n)}/">{_xml.escape(n)}</a></li>'
                for n in children)
            html = ("<!doctype html><meta charset=utf-8><title>RMM Relay</title>"
                    f"<body style='font-family:sans-serif'><h3>🔌 {_xml.escape(self_name)}</h3>"
                    f"<ul>{rows or '<li>(leer)</li>'}</ul></body>")
            return Response(content=html, media_type="text/html")
        return PlainTextResponse("Nur lesbar", status_code=403)

    # --- Konkreter Client ---
    client_id = walk["client_id"]
    display_name = walk["client_display"]
    sub = walk.get("rest", [])
    if not can_access_client(user, client_id):
        return PlainTextResponse("Kein Zugriff auf diesen Client", status_code=403)
    if not state.is_online(client_id):
        return PlainTextResponse("Client ist offline", status_code=503)

    try:
        real_path, drives = await _resolve_real_path(client_id, sub)
    except Exception as e:
        return PlainTextResponse(f"Fehler: {e}", status_code=502)
    if real_path == "__404__":
        return PlainTextResponse("Laufwerk nicht gefunden", status_code=404)

    base_href = _href_base(walk["consumed"])

    # ---------------- PROPFIND (Verzeichnis/Datei-Infos) ----------------
    if method == "PROPFIND":
        depth = request.headers.get("depth", "1")

        # Root des Clients -> Laufwerke als Ordner
        if real_path is None:
            mapping = _drive_map(client_id, drives)
            responses = [_propfind_response(base_href + "/", display_name, True, 0, 0)]
            if depth != "0":
                for key in mapping:
                    responses.append(_propfind_response(
                        f"{base_href}/{key}/", key, True, 0, 0))
            return _multistatus(responses)

        # Konkreter Pfad: erst versuchen, ihn als Ordner aufzulisten.
        href_self = base_href + "/" + "/".join(sub)
        try:
            entries = await request_fs_list(client_id, real_path)
            is_dir = True
        except Exception:
            entries = None
            is_dir = False

        if is_dir:
            responses = [_propfind_response(href_self.rstrip("/") + "/",
                                            sub[-1] if sub else display_name, True, 0, 0)]
            if depth != "0":
                for e in entries:
                    child_href = href_self.rstrip("/") + "/" + _xml.escape(e["name"])
                    if e.get("isDir"):
                        child_href += "/"
                    responses.append(_propfind_response(
                        child_href, e["name"], bool(e.get("isDir")),
                        e.get("size", 0), e.get("mtime", 0)))
            return _multistatus(responses)

        # Datei: Metadaten über das Parent-Listing holen. Existiert die Datei
        # dort NICHT, muss ein 404 zurückgehen - Windows fragt beim Erstellen
        # neuer Dateien den Zielnamen per PROPFIND ab und erwartet "gibt es
        # noch nicht". Ein Fake-207 (wie früher) bringt den Ablauf durcheinander.
        parent_real = real_path.rsplit("\\", 1)[0] if "\\" in real_path else real_path.rsplit("/", 1)[0]
        name = real_path.rsplit("\\", 1)[-1] if "\\" in real_path else real_path.rsplit("/", 1)[-1]
        size, mtime = 0, 0
        found = False
        try:
            for e in await request_fs_list(client_id, parent_real):
                if e["name"] == name:
                    size, mtime = e.get("size", 0), e.get("mtime", 0)
                    found = True
                    break
        except Exception:
            pass
        if not found:
            return Response(status_code=404)
        return _multistatus([_propfind_response(href_self, name, False, size, mtime)])

    # ---------------- GET / HEAD (Datei herunterladen / Ordner browsen) ----------------
    if method in ("GET", "HEAD"):
        # Erst prüfen, ob es ein Ordner (bzw. die Laufwerks-Wurzel) ist -> dann
        # im Browser eine klickbare Liste zeigen (praktisch zum TESTEN von Login).
        items = None
        title = ""
        try:
            if real_path is None:
                mapping = _drive_map(client_id, drives)
                items = [(k, f"{base_href}/{k}/", True) for k in mapping]
                title = f"Laufwerke von {client_id}"
            else:
                entries = await request_fs_list(client_id, real_path)
                href_self = base_href + "/" + "/".join(sub)
                items = [(e["name"],
                          href_self.rstrip("/") + "/" + e["name"] + ("/" if e.get("isDir") else ""),
                          bool(e.get("isDir"))) for e in entries]
                title = "/".join(sub)
        except Exception:
            items = None   # kein Ordner -> weiter unten als Datei behandeln

        if items is not None:
            if method == "HEAD":
                return Response(status_code=200)
            rows = "".join(
                f'<li>{"📁" if d else "📄"} <a href="{_xml.escape(h)}">{_xml.escape(n)}</a></li>'
                for n, h, d in items)
            html = (f"<!doctype html><meta charset=utf-8>"
                    f"<title>{_xml.escape(title)}</title>"
                    f"<body style='font-family:sans-serif'>"
                    f"<h3>✅ Relay verbunden — {_xml.escape(title)}</h3>"
                    f"<ul>{rows}</ul></body>")
            return Response(content=html, media_type="text/html")

        # Datei herunterladen
        try:
            res = await request_fs_read(client_id, real_path)
        except Exception as e:
            return PlainTextResponse(f"Lesefehler: {e}", status_code=404)
        data = base64.b64decode(res.get("data", ""))
        headers = {"Content-Length": str(len(data))}
        if method == "HEAD":
            return Response(status_code=200, headers=headers)
        return Response(content=data, media_type="application/octet-stream", headers=headers)

    # ---------------- PUT (Datei hochladen/überschreiben) ----------------
    if method == "PUT":
        if real_path is None:
            return PlainTextResponse("Kein Ziel", status_code=400)
        body = await request.body()
        try:
            await request_fs_op(client_id, "fs-write", {
                "path": real_path,
                "data": base64.b64encode(body).decode("ascii"),
            })
        except Exception as e:
            return PlainTextResponse(f"Schreibfehler: {e}", status_code=502)
        db.add_audit_entry(user["username"], "relay.put", target=client_id, details=real_path)
        return Response(status_code=201)

    # ---------------- MKCOL (Ordner anlegen) ----------------
    if method == "MKCOL":
        if real_path is None:
            return PlainTextResponse("Kein Ziel", status_code=400)
        try:
            await request_fs_op(client_id, "fs-mkdir", {"path": real_path})
        except Exception as e:
            return PlainTextResponse(f"Fehler: {e}", status_code=409)
        return Response(status_code=201)

    # ---------------- DELETE ----------------
    if method == "DELETE":
        if real_path is None:
            return PlainTextResponse("Root kann nicht gelöscht werden", status_code=403)
        try:
            await request_fs_op(client_id, "fs-delete", {"path": real_path})
        except Exception as e:
            return PlainTextResponse(f"Fehler: {e}", status_code=502)
        db.add_audit_entry(user["username"], "relay.delete", target=client_id, details=real_path)
        return Response(status_code=204)

    # ---------------- MOVE (umbenennen/verschieben) ----------------
    if method == "MOVE":
        dest = request.headers.get("destination", "")
        idx = dest.find("/dav/")
        if idx < 0 or real_path is None:
            return PlainTextResponse("Ungültiges Ziel", status_code=400)
        # Ziel durch die Hierarchie auflösen (muss innerhalb desselben Clients bleiben).
        dsegs = [p for p in dest[idx:].split("/") if p not in ("", "dav")]
        dwalk = _walk_relay(dsegs, user)
        if dwalk.get("node") != "client" or dwalk.get("client_id") != client_id:
            return PlainTextResponse("Ziel muss im selben Client liegen", status_code=400)
        try:
            dreal, _ = await _resolve_real_path(client_id, dwalk.get("rest", []))
        except Exception as e:
            return PlainTextResponse(f"Fehler: {e}", status_code=502)
        if not dreal or dreal == "__404__":
            return PlainTextResponse("Ungültiges Ziel", status_code=400)
        try:
            await request_fs_op(client_id, "fs-rename", {"src": real_path, "dst": dreal})
        except Exception as e:
            return PlainTextResponse(f"Fehler: {e}", status_code=502)
        db.add_audit_entry(user["username"], "relay.move", target=client_id,
                           details=f"{real_path} -> {dreal}")
        return Response(status_code=201)

    # ---------------- LOCK / UNLOCK / PROPPATCH ----------------
    # WICHTIG für Windows: Beim ERSTELLEN einer Datei sperrt der Windows-
    # WebClient sie zuerst per LOCK und erwartet zwingend eine korrekte
    # Antwort mit <D:lockdiscovery>-XML und einem Lock-Token-Header. Eine
    # leere 200-Antwort (wie früher) bringt den Windows-Explorer beim
    # "Neue Datei erstellen" zum Absturz. Wir vergeben deshalb ein echtes
    # (nicht weiter geprüftes) Token - der Relay ist ohnehin Single-Writer
    # pro Operation, echtes Lock-Management ist hier nicht nötig.
    if method == "LOCK":
        token = "opaquelocktoken:" + _uuid.uuid4().hex
        timeout = request.headers.get("timeout") or "Second-3600"
        # Bei Lock-Refresh (If-Header mit altem Token) das alte Token weiterverwenden.
        if_hdr = request.headers.get("if") or ""
        if "opaquelocktoken:" in if_hdr:
            try:
                token = "opaquelocktoken:" + if_hdr.split("opaquelocktoken:")[1].split(">")[0].strip()
            except Exception:
                pass
        href_lock = "/dav/" + "/".join(_up.quote(s) for s in segments)
        lock_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<D:prop xmlns:D="DAV:"><D:lockdiscovery><D:activelock>\n'
            '<D:locktype><D:write/></D:locktype>\n'
            '<D:lockscope><D:exclusive/></D:lockscope>\n'
            '<D:depth>infinity</D:depth>\n'
            f'<D:timeout>{_xml.escape(timeout)}</D:timeout>\n'
            f'<D:locktoken><D:href>{token}</D:href></D:locktoken>\n'
            f'<D:lockroot><D:href>{_xml.escape(href_lock)}</D:href></D:lockroot>\n'
            '</D:activelock></D:lockdiscovery></D:prop>')
        return Response(content=lock_xml, status_code=200,
                        media_type="application/xml; charset=utf-8",
                        headers={"Lock-Token": f"<{token}>"})

    if method == "UNLOCK":
        # Erfolgreiches Entsperren = 204 No Content (leere 200 verwirrt Clients).
        return Response(status_code=204)

    if method == "PROPPATCH":
        # Eigenschaften (Zeitstempel etc.) nehmen wir "erfolgreich" an -
        # korrekt als 207 Multi-Status mit 200-Propstat, nicht als leere 200.
        href_pp = "/dav/" + "/".join(_up.quote(s) for s in segments)
        pp_xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<D:multistatus xmlns:D="DAV:"><D:response>\n'
            f'<D:href>{_xml.escape(href_pp)}</D:href>\n'
            '<D:propstat><D:prop/><D:status>HTTP/1.1 200 OK</D:status></D:propstat>\n'
            '</D:response></D:multistatus>')
        return Response(content=pp_xml, status_code=207,
                        media_type="application/xml; charset=utf-8")

    return PlainTextResponse("Nicht unterstützt", status_code=405)

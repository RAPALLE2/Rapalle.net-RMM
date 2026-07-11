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
from app.auth import get_current_user, require_admin  # noqa: E402
from fastapi import Depends  # noqa: E402


@router.get("/api/relay/status")
async def relay_status(client_id: str, user: dict = Depends(get_current_user)):
    """Ist der Relay für DIESEN Client freigegeben?"""
    return {"enabled": db.is_client_relay_enabled(client_id),
            "client_id": client_id}


@router.post("/api/relay/toggle")
async def relay_toggle(client_id: str, user: dict = Depends(get_current_user)):
    """Relay-Freigabe für diesen Client an/aus. Nur Admins."""
    require_admin(user)
    new_state = not db.is_client_relay_enabled(client_id)
    db.set_client_relay_enabled(client_id, new_state)
    db.add_audit_entry(user["username"], "relay.toggle", target=client_id,
                       details="freigegeben" if new_state else "gesperrt")
    return {"enabled": new_state, "client_id": client_id}


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
    """Bildet einen url-sicheren Laufwerksnamen -> echter Root-Pfad ab."""
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
    """/dav/<client_id>/<drive>/<a>/<b> -> (client_id, [drive, a, b])."""
    parts = [p for p in path.split("/") if p not in ("", "dav")]
    if not parts:
        return None, []
    client_id = parts[0]
    return client_id, parts[1:]


async def _resolve_real_path(client_id: str, sub: list[str]) -> tuple[str | None, list[dict] | None]:
    """
    Übersetzt [drive, rest...] in den echten Client-Pfad.
    Rückgabe: (real_path oder None wenn Root/Drive-Liste, drives-Liste).
    """
    drives = await _drives_for(client_id)
    if not sub:
        return None, drives          # Root: Laufwerks-Liste
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
            "Allow": "OPTIONS, PROPFIND, GET, HEAD, PUT, MKCOL, DELETE, MOVE",
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

    client_id, sub = _parse(full_path)

    # /dav (Wurzel) -> nur die FREIGEGEBENEN, für den User sichtbaren Clients
    # als Ordner. Mehrere Clients erscheinen gleichzeitig nebeneinander.
    if not client_id:
        enabled_clients = [c for c in db.list_relay_enabled_clients()
                           if can_access_client(user, c["id"])]
        if method == "PROPFIND":
            responses = [_propfind_response("/dav/", "RMM Relay", True, 0, 0)]
            depth = request.headers.get("depth", "1")
            if depth != "0":
                for c in enabled_clients:
                    responses.append(_propfind_response(
                        f"/dav/{c['id']}/", c.get("hostname") or c["id"], True, 0, 0))
            return _multistatus(responses)
        # Browser: klickbare Übersicht der freigegebenen Clients
        rows = "".join(
            f'<li>🖥️ <a href="/dav/{_xml.escape(c["id"])}/">{_xml.escape(c.get("hostname") or c["id"])}</a></li>'
            for c in enabled_clients)
        html = (f"<!doctype html><meta charset=utf-8><title>RMM Relay</title>"
                f"<body style='font-family:sans-serif'><h3>🔌 RMM Relay — freigegebene Clients</h3>"
                f"<ul>{rows or '<li>(keine Clients freigegeben)</li>'}</ul></body>")
        return Response(content=html, media_type="text/html")

    # Ab hier: konkreter Client. Nur wenn für den Relay FREIGEGEBEN.
    if not db.is_client_relay_enabled(client_id):
        return PlainTextResponse("Not Found", status_code=404)
    # Kein Zugriff auf DIESEN Client -> 403 (NICHT 401!).
    if not can_access_client(user, client_id):
        _log.info("Relay: %s hat keinen Zugriff auf Client %s", user.get("username"), client_id)
        return PlainTextResponse("Kein Zugriff auf diesen Client", status_code=403)
    if not state.is_online(client_id):
        return PlainTextResponse("Client ist offline", status_code=503)

    try:
        real_path, drives = await _resolve_real_path(client_id, sub)
    except Exception as e:
        return PlainTextResponse(f"Fehler: {e}", status_code=502)

    if real_path == "__404__":
        return PlainTextResponse("Laufwerk nicht gefunden", status_code=404)

    base_href = "/dav/" + client_id

    # ---------------- PROPFIND (Verzeichnis/Datei-Infos) ----------------
    if method == "PROPFIND":
        depth = request.headers.get("depth", "1")

        # Root des Clients -> Laufwerke als Ordner
        if real_path is None:
            mapping = _drive_map(client_id, drives)
            responses = [_propfind_response(base_href + "/", client_id, True, 0, 0)]
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
                                            sub[-1] if sub else client_id, True, 0, 0)]
            if depth != "0":
                for e in entries:
                    child_href = href_self.rstrip("/") + "/" + _xml.escape(e["name"])
                    if e.get("isDir"):
                        child_href += "/"
                    responses.append(_propfind_response(
                        child_href, e["name"], bool(e.get("isDir")),
                        e.get("size", 0), e.get("mtime", 0)))
            return _multistatus(responses)

        # Datei: Metadaten über das Parent-Listing holen
        parent_real = real_path.rsplit("\\", 1)[0] if "\\" in real_path else real_path.rsplit("/", 1)[0]
        name = real_path.rsplit("\\", 1)[-1] if "\\" in real_path else real_path.rsplit("/", 1)[-1]
        size, mtime = 0, 0
        try:
            for e in await request_fs_list(client_id, parent_real):
                if e["name"] == name:
                    size, mtime = e.get("size", 0), e.get("mtime", 0)
                    break
        except Exception:
            pass
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
        # Destination ist eine volle URL; wir extrahieren den /dav-Teil.
        idx = dest.find("/dav/")
        if idx < 0 or real_path is None:
            return PlainTextResponse("Ungültiges Ziel", status_code=400)
        dcid, dsub = _parse(dest[idx:])
        try:
            dreal, _ = await _resolve_real_path(dcid or client_id, dsub)
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

    # LOCK/UNLOCK/PROPPATCH: höflich mit Erfolg antworten (viele Clients brauchen das)
    if method in ("LOCK", "UNLOCK", "PROPPATCH"):
        return Response(status_code=200)

    return PlainTextResponse("Nicht unterstützt", status_code=405)

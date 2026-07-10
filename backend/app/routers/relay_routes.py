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


# ------------------------------------------------------------------
# Auth: HTTP Basic. Benutzername "name" = lokal, "name@realm-id" = SSO/Realm.
# ------------------------------------------------------------------
def _basic_auth(request: Request) -> dict | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
        username, _, password = raw.partition(":")
    except Exception:
        return None

    if "@" in username:
        name, _, realm_id = username.rpartition("@")
        user = authenticate_realm(name, password, realm_id)
        if user:
            return user
    return authenticate_local(username, password)


def _need_auth() -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="RAPALLE.net RMM Relay"'},
    )


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
                    media_type='application/xml; charset="utf-8"')


# ------------------------------------------------------------------
# Der eigentliche WebDAV-Endpunkt: fängt ALLE Methoden unter /dav ab.
# ------------------------------------------------------------------
@router.api_route("/dav/{full_path:path}",
                  methods=["OPTIONS", "PROPFIND", "GET", "HEAD", "PUT",
                           "MKCOL", "DELETE", "MOVE", "PROPPATCH", "LOCK", "UNLOCK"])
async def dav(full_path: str, request: Request):
    method = request.method.upper()

    # OPTIONS beantwortet Windows/macOS oft VOR der Authentifizierung.
    if method == "OPTIONS":
        return Response(status_code=200, headers={
            "DAV": "1, 2",
            "Allow": "OPTIONS, PROPFIND, GET, HEAD, PUT, MKCOL, DELETE, MOVE",
            "MS-Author-Via": "DAV",
        })

    user = _basic_auth(request)
    if not user:
        return _need_auth()

    client_id, sub = _parse(full_path)

    # /dav (ohne Client) -> Liste der sichtbaren Clients als Ordner
    if not client_id:
        if method in ("PROPFIND",):
            responses = [_propfind_response("/dav/", "RMM Relay", True, 0, 0)]
            for c in db.list_clients():
                if not can_access_client(user, c["id"]):
                    continue
                responses.append(_propfind_response(
                    f"/dav/{c['id']}/", c.get("hostname") or c["id"], True, 0, 0))
            return _multistatus(responses)
        return PlainTextResponse("RAPALLE.net RMM Relay", status_code=200)

    if not can_access_client(user, client_id):
        return _need_auth()
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

    # ---------------- GET / HEAD (Datei herunterladen) ----------------
    if method in ("GET", "HEAD"):
        if real_path is None:
            return PlainTextResponse("Verzeichnis", status_code=200)
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

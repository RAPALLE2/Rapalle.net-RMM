# api/bridge.py - Bruecke von /api/v1 zu ALLEN Dashboard-Routen
# ------------------------------------------------------------
# Jede Funktion des Web-Dashboards laeuft ueber eine REST-Route unter /api/...
# Die Bruecke ruft genau diese Routen auf - im selben Prozess (ASGI), ohne Netzwerk -
# und zwar im Namen des API-Benutzers. Dadurch gelten automatisch dieselben
# Rechte wie im Dashboard: Wer z.B. einen Client loeschen will, braucht das Recht
# dafuer (c_delete) - die Pruefung macht die Dashboard-Route selbst.
#
# Die Anmeldung fuer den inneren Aufruf ist ein normales, nur intern verwendetes
# Dashboard-Token des API-Benutzers (verlaesst den Server nie).

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import Response

from app.auth import create_access_token

# Diese Pfade ruft die Bruecke NICHT auf (Rekursion / nicht sinnvoll ueber die API).
BLOCKED_PREFIXES = ("/api/v1", "/api/apimod")
HOP_HEADERS = {"content-length", "transfer-encoding", "connection", "keep-alive", "set-cookie"}


async def forward(request: Request, user: dict | None, method: str, path: str, query: str = "",
                  body: bytes | None = None, content_type: str | None = None) -> Response:
    """Dashboard-Route `path` (z.B. "/api/clients") als `user` aufrufen und die Antwort zurueckgeben."""
    if not path.startswith("/api/") or path.startswith(BLOCKED_PREFIXES):
        raise HTTPException(400, f"Pfad nicht über die API erreichbar: {path}")
    if ".." in path.split("/"):
        raise HTTPException(400, "Ungültiger Pfad")
    app = request.scope.get("app")
    if app is None:
        raise HTTPException(500, "Interne Anwendung nicht erreichbar")
    if body is None:
        body = await request.body()
    headers = [
        (b"accept", (request.headers.get("accept") or "application/json").encode()),
        (b"host", (request.headers.get("host") or "localhost").encode()),
        (b"x-rmm-api-bridge", b"1"),
        (b"user-agent", (request.headers.get("user-agent") or "rmm-api").encode()),
    ]
    if user is not None:                 # ohne Benutzer = oeffentliche Route (z.B. /api/auth/login)
        headers.insert(0, (b"authorization", f"Bearer {create_access_token(user)}".encode()))
    ct = content_type or request.headers.get("content-type")
    if ct:
        headers.append((b"content-type", ct.encode()))
    if body:
        headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": method.upper(), "scheme": request.url.scheme, "path": path, "raw_path": path.encode(),
        "root_path": "", "query_string": (query or "").encode(), "headers": headers,
        "client": request.scope.get("client"), "server": request.scope.get("server"),
    }
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body or b"", "more_body": False}
        return {"type": "http.disconnect"}

    status, resp_headers, chunks = 500, [], []

    async def send(message):
        nonlocal status, resp_headers
        if message["type"] == "http.response.start":
            status = message["status"]
            resp_headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    out = Response(content=b"".join(chunks), status_code=status)
    for k, v in resp_headers:
        name = k.decode().lower()
        if name not in HOP_HEADERS:
            out.headers[name] = v.decode()
    return out


def internal_routes(app) -> list[dict]:
    """Katalog aller Dashboard-Routen, die ueber die Bruecke erreichbar sind.
    Quelle: das OpenAPI-Schema der Anwendung (FastAPI haengt eingebundene Router
    verzoegert ein - app.routes allein zeigt sie nicht vollstaendig)."""
    out = []
    try:
        paths = app.openapi().get("paths", {})
    except Exception:
        paths = {}
    for path, ops in paths.items():
        if not path.startswith("/api/") or path.startswith(BLOCKED_PREFIXES):
            continue
        methods = sorted(m.upper() for m in ops if m.upper() not in ("HEAD", "OPTIONS"))
        summ = next((o.get("summary") or "" for o in ops.values() if isinstance(o, dict)), "")
        out.append({"methods": methods, "path": path, "v1": "/api/v1/call" + path[4:], "summary": summ[:160]})
    return sorted(out, key=lambda x: x["path"])

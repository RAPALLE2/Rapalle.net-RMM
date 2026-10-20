# apicall.py (Modul "console") - Dashboard-Schnittstellen aus der Konsole aufrufen
# -------------------------------------------------------------------------------
# Wie "pvesh" bei Proxmox: Die Konsole ruft die ECHTEN Dashboard-Routen intern auf
# (ASGI, ohne Netzwerk) - als der angemeldete Benutzer (eigenes Sitzungs-Token).
# Damit gelten exakt dieselben Rechte, Pruefungen und Audit-Eintraege wie im Dashboard.

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlencode


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


async def call(user: dict, method: str, path: str, query: dict | None = None, body=None, ip: str = "") -> object:
    """-> JSON-Antwort (oder Text). Fehler (>= 400) als ApiError mit der Meldung der Route."""
    from app import modules
    from app.auth import create_access_token
    app = modules._api
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/api/") or path.startswith(("/api/v1/", "/api/apimod")):
        raise ApiError(400, "Nur Dashboard-Pfade unter /api/… (ohne /api/v1 und /api/apimod)")
    data = b"" if body is None else json.dumps(body).encode()
    headers = [(b"authorization", f"Bearer {create_access_token(user)}".encode()), (b"host", b"localhost"),
               (b"accept", b"application/json"), (b"user-agent", b"rmm-console"), (b"x-rmm-console", b"1")]
    if data:
        headers += [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
    qs = urlencode({k: v for k, v in (query or {}).items() if v is not None}, doseq=True).encode()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method.upper(),
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "", "query_string": qs,
             "headers": headers, "client": (ip or "127.0.0.1", 0), "server": ("localhost", 0)}
    sent, status, chunks = False, 500, []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": data, "more_body": False}
        await asyncio.sleep(3600)

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))
    await app(scope, receive, send)
    raw = b"".join(chunks)
    try:
        out = json.loads(raw) if raw else None
    except ValueError:
        out = raw.decode("utf-8", errors="replace")
    if status >= 400:
        detail = out.get("detail") if isinstance(out, dict) else out
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        raise ApiError(status, detail or f"Fehler {status}")
    return out


def route_paths() -> list[tuple[str, list[str]]]:
    """Alle Dashboard-Routen (fuer "api ls" und die Tab-Vervollstaendigung)."""
    from app import modules
    out = {}
    for r in modules._api.router.routes:
        p = getattr(r, "path", "")
        if p.startswith("/api/") and not p.startswith(("/api/v1", "/api/apimod")) and getattr(r, "methods", None):
            out.setdefault(p, set()).update(m for m in r.methods if m not in ("HEAD", "OPTIONS"))
    return sorted((p, sorted(m)) for p, m in out.items())

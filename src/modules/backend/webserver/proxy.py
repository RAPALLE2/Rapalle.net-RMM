# proxy.py (Modul "webserver") - Websites der Art "Weiterleitung" (Reverse-Proxy)
# ------------------------------------------------------------------------------
# rmm.url.de/<name>/<pfad>  ->  <ziel>/<pfad>   (HTTP und WebSocket)
# Ziel (site["upstream"]):
#   {"url": "http://10.0.0.5:8080"}                       feste Adresse
#   {"provider": "docker", "ref": {...}}                  vom Modul "docker" aufgeloest
#                                                         (Container-Adresse kann sich aendern)
# Gesetzte Kopfzeilen: X-Forwarded-For/-Proto/-Host und X-Forwarded-Prefix=/<name>
# (viele Apps bauen damit ihre Links richtig). Location-Antworten des Ziels werden
# auf /<name>/... umgeschrieben.

from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.websockets import WebSocket

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}
_client = None


def _http():
    global _client
    if _client is None:
        import httpx
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False, verify=False)
    return _client


async def upstream_url(site: dict) -> str:
    up = site.get("upstream") or {}
    if up.get("url"):
        return up["url"].rstrip("/")
    if up.get("provider"):
        from app import modules
        if not modules.is_enabled(up["provider"]):
            raise RuntimeError(f"Modul „{up['provider']}“ ist aus")
        return (await modules.use(up["provider"], "publish").resolve(up.get("ref") or {})).rstrip("/")
    raise RuntimeError("Kein Ziel eingestellt")


def _fwd_headers(req_headers, client_host: str, scheme: str, host: str, name: str) -> dict:
    h = {k: v for k, v in req_headers.items() if k.lower() not in HOP and not k.lower().startswith("x-forwarded-")}
    h["X-Forwarded-For"] = client_host
    h["X-Forwarded-Proto"] = scheme
    h["X-Forwarded-Host"] = host
    h["X-Forwarded-Prefix"] = f"/{name}"
    return h


async def http(request: Request, name: str, site: dict, path: str):
    try:
        base = await upstream_url(site)
    except Exception as e:      # noqa: BLE001
        return PlainTextResponse(f"Ziel nicht erreichbar: {e}", status_code=502)
    url = f"{base}/{path}" + (f"?{request.url.query}" if request.url.query else "")
    headers = _fwd_headers(request.headers, request.client.host if request.client else "", request.url.scheme,
                           request.headers.get("host", ""), name)
    try:
        req = _http().build_request(request.method, url, headers=headers, content=request.stream())
        resp = await _http().send(req, stream=True)
    except Exception as e:      # noqa: BLE001
        return PlainTextResponse(f"Ziel nicht erreichbar: {e}", status_code=502)
    out_headers = []
    for k, v in resp.headers.multi_items():
        lk = k.lower()
        if lk in HOP:
            continue
        if lk == "location" and v.startswith(base):
            v = f"/{name}" + (v[len(base):] or "/")
        elif lk == "location" and v.startswith("/") and not v.startswith(f"/{name}/"):
            v = f"/{name}{v}"
        out_headers.append((k, v))

    async def body():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
    r = StreamingResponse(body(), status_code=resp.status_code)
    r.raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in out_headers]
    return r


async def websocket(ws: WebSocket, name: str, site: dict, path: str):
    try:
        base = await upstream_url(site)
    except Exception:
        await ws.close(code=1011)
        return
    target = ("wss" if base.startswith("https") else "ws") + base[base.index(":"):] + f"/{path}"
    if ws.url.query:
        target += f"?{ws.url.query}"
    protos = ws.scope.get("subprotocols") or []
    headers = {k: v for k, v in ws.headers.items() if k.lower() in ("cookie", "authorization", "origin", "user-agent")}
    from websockets.asyncio.client import connect
    try:
        async with connect(target, additional_headers=headers, subprotocols=protos or None, max_size=None,
                           open_timeout=10) as up:
            await ws.accept(subprotocol=up.subprotocol)

            async def a2b():
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    if msg.get("bytes") is not None:
                        await up.send(msg["bytes"])
                    elif msg.get("text") is not None:
                        await up.send(msg["text"])

            async def b2a():
                async for data in up:
                    if isinstance(data, bytes):
                        await ws.send_bytes(data)
                    else:
                        await ws.send_text(data)
            tasks = [asyncio.create_task(a2b()), asyncio.create_task(b2a())]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except Exception:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass

# node_module.py (Node-Teil von Modul "tenants")
# ---------------------------------------------
# Ein Mandanten-Backend ist vom RMM-Server aus nicht erreichbar (z.B. nur im
# Kunden-LAN)? Dann ruft das RMM dessen API UEBER DIESE NODE auf.
#   "tenants-http" {method, url, headers:{}, body(b64), verify, timeout}
#        -> {status, headers:[[k, v], ...], body(b64), final}
import base64
import ssl
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPSHandler, Request, build_opener

_MAX = 50 * 1024 * 1024


def fetch(ctx, data: dict) -> dict:
    url = str(data.get("url") or "")
    if urlparse(url).scheme not in ("http", "https"):
        raise RuntimeError("Nur http und https")
    method = str(data.get("method") or "GET").upper()
    body = base64.b64decode(data["body"]) if data.get("body") else None
    sslctx = ssl.create_default_context()
    if data.get("verify") is False:
        sslctx.check_hostname = False
        sslctx.verify_mode = ssl.CERT_NONE
    req = Request(url, data=body, headers=dict(data.get("headers") or {}), method=method)
    try:
        resp = build_opener(HTTPSHandler(context=sslctx)).open(req, timeout=float(data.get("timeout") or 25))
    except HTTPError as e:
        resp = e
    with resp:
        raw = resp.read(_MAX + 1)
        out = {"status": resp.getcode() or 200, "headers": [[k, v] for k, v in resp.headers.items()],
               "final": resp.geturl()}
    if len(raw) > _MAX:
        raise RuntimeError("Antwort zu gross")
    out["body"] = base64.b64encode(raw).decode()
    return out


OPS = {"tenants-http": fetch}


def start(ctx):
    return {"ready": True}


def stop(ctx):
    return {"stopped": True}

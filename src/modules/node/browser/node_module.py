# node_module.py (Node-Teil von Modul "browser")
# ---------------------------------------------
# Der interne Browser kann Seiten statt vom RMM-Server UEBER DIESE NODE abrufen -
# z.B. die Weboberflaeche eines Switches im Kunden-LAN. Das Umschreiben der Seite
# macht weiterhin das Backend; die Node holt nur die Bytes.
#   "browser-http" {session, method, url, headers:{}, body(b64), insecure, timeout, max_bytes}
#        -> {status, headers:[[k, v], ...], body(b64), final}
#   "browser-drop" {session}   Cookie-Behaelter einer Proxy-Sitzung verwerfen
# Cookies der Zielseite bleiben HIER (Behaelter je Proxy-Sitzung, 12 h ohne Nutzung -> weg).
import base64
import http.cookiejar
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener

_jars: dict[str, tuple[http.cookiejar.CookieJar, float]] = {}
_TTL = 12 * 3600


def _jar(session: str) -> http.cookiejar.CookieJar:
    now = time.time()
    for k in [k for k, (_, t) in _jars.items() if now - t > _TTL]:
        _jars.pop(k, None)
    jar = (_jars.get(session) or (http.cookiejar.CookieJar(), 0))[0]
    _jars[session] = (jar, now)
    return jar


def fetch(ctx, data: dict) -> dict:
    url = str(data.get("url") or "")
    if urlparse(url).scheme not in ("http", "https"):
        raise RuntimeError("Nur http und https")
    method = str(data.get("method") or "GET").upper()
    body = base64.b64decode(data["body"]) if data.get("body") else None
    max_bytes = int(data.get("max_bytes") or 25 * 1024 * 1024)
    sslctx = ssl.create_default_context()
    if data.get("insecure"):
        sslctx.check_hostname = False
        sslctx.verify_mode = ssl.CERT_NONE
    opener = build_opener(HTTPCookieProcessor(_jar(str(data.get("session") or "-"))), HTTPSHandler(context=sslctx))
    req = Request(url, data=body if method not in ("GET", "HEAD") else None,
                  headers=dict(data.get("headers") or {}), method=method)
    try:
        resp = opener.open(req, timeout=float(data.get("timeout") or 30))
    except HTTPError as e:
        resp = e
    except ssl.SSLError as e:
        raise RuntimeError(f"CERTIFICATE_VERIFY_FAILED SSL: {e}")
    except Exception as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            raise RuntimeError(f"CERTIFICATE_VERIFY_FAILED SSL: {e}")
        raise
    with resp:
        status = resp.getcode() or 200
        headers = [[k, v] for k, v in resp.headers.items()]
        raw = resp.read(max_bytes + 1)
        final = resp.geturl()
    if len(raw) > max_bytes:
        raise RuntimeError("Antwort zu gross für den internen Browser")
    return {"status": status, "headers": headers, "body": base64.b64encode(raw).decode(), "final": final}


def drop(ctx, data: dict) -> dict:
    _jars.pop(str(data.get("session") or ""), None)
    return {"ok": True}


OPS = {"browser-http": fetch, "browser-drop": drop}


def start(ctx):
    return {"ready": True}


def stop(ctx):
    _jars.clear()
    return {"stopped": True}

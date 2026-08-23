"""
node_proxy.py  -  NODE-MODUL (wird nur auf Nodes nachgeladen)
--------------------------------------------------------------
Der Reverse Proxy: holt Webseiten, die NUR im Netz hinter der Node
erreichbar sind, und reicht sie an das Dashboard weiter.

Zwei Wege, beide werden unterstuetzt:

  1. Ueber die bestehende Socket.IO-Verbindung. Der Benutzer braucht dafuer
     keinen VPN-Tunnel - er tippt im internen Browser eine Adresse ein, das
     Backend fragt die Node, die Node holt die Seite. Das ist der bequeme
     Weg fuer "mal eben ins Router-Webinterface schauen".

  2. Durch den VPN-Tunnel. Dann laeuft der Verkehr direkt zwischen Benutzer
     und Node, das Backend sieht nichts davon. Der Weg dafuer ist bereits
     da: der Tunnel leitet TCP ohnehin durch, ein Proxy ist gar nicht
     noetig - der Benutzer ruft die interne Adresse einfach direkt auf.

Diese Datei setzt Weg 1 um.

Warum nicht einfach requests?
-----------------------------
Der Agent soll ohne Fremdpakete auskommen. http.client aus der
Standardbibliothek reicht vollstaendig und laesst sich genauer steuern -
insbesondere beim Umschreiben der Kopfzeilen, das ein Reverse Proxy
zwingend braucht.
"""

from __future__ import annotations

import base64
import gzip
import http.client
import io
import ssl
import threading
import urllib.parse

# Kopfzeilen, die NIE weitergereicht werden. Sie beschreiben die Verbindung
# zwischen zwei bestimmten Rechnern und werden beim naechsten Sprung neu
# gebildet - kopiert man sie, bricht die Uebertragung an merkwuerdigen
# Stellen ab.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length",
}

MAX_BODY = 12 * 1024 * 1024      # 12 MB - darueber wird abgeschnitten
REQUEST_TIMEOUT = 25.0


def fetch(spec: dict, allowed=None, log=print) -> dict:
    """
    Holt EINE Ressource und gibt sie als Ergebnis-Dict zurueck.

    spec: {url, method, headers, body(base64), insecure}
    allowed: optionale Pruefung (host) -> bool. Fehlt sie, ist jedes Ziel
             erlaubt; das Backend prueft dann die Rechte.
    """
    url = (spec.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "Keine Adresse angegeben"}
    if "://" not in url:
        url = "http://" + url

    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return {"ok": False, "error": f"Nicht unterstuetztes Schema: {parts.scheme}"}
    host = parts.hostname or ""
    if allowed and not allowed(host):
        return {"ok": False, "error": f"Ziel {host} ist fuer diese Node gesperrt"}

    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query

    try:
        if parts.scheme == "https":
            ctx = ssl.create_default_context()
            if spec.get("insecure"):
                # Interne Geraete haben fast immer ein selbstsigniertes
                # Zertifikat. Das Abschalten ist deshalb eine bewusste
                # Wahl des Benutzers, keine stille Voreinstellung.
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(host, port, timeout=REQUEST_TIMEOUT,
                                               context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=REQUEST_TIMEOUT)

        headers = {k: v for k, v in (spec.get("headers") or {}).items()
                   if k.lower() not in HOP_BY_HOP}
        headers.setdefault("Host", parts.netloc)
        headers.setdefault("Accept-Encoding", "gzip")
        body = base64.b64decode(spec.get("body") or "") or None

        conn.request(spec.get("method", "GET").upper(), path, body=body,
                     headers=headers)
        resp = conn.getresponse()
        raw = resp.read(MAX_BODY + 1)
        truncated = len(raw) > MAX_BODY
        raw = raw[:MAX_BODY]

        if (resp.getheader("Content-Encoding") or "").lower() == "gzip":
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except OSError:
                pass   # kaputt komprimiert - lieber roh weiterreichen

        out_headers = {k: v for k, v in resp.getheaders()
                       if k.lower() not in HOP_BY_HOP}
        return {
            "ok": True,
            "status": resp.status,
            "headers": out_headers,
            "body": base64.b64encode(raw).decode(),
            "truncated": truncated,
            "final_url": url,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_async(spec: dict, done, allowed=None, log=print) -> None:
    """Wie fetch(), nur im Hintergrund - der Agent darf nicht blockieren."""
    def run():
        try:
            done(fetch(spec, allowed=allowed, log=log))
        except Exception as e:
            done({"ok": False, "error": str(e)})
    threading.Thread(target=run, daemon=True).start()

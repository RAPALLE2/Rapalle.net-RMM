"""
routers/webproxy_routes.py
--------------------------
Seitenproxy des internen Browsers.

WARUM ES DAS BRAUCHT
--------------------
Der interne Browser ist ein <iframe>. Ein Klick auf einen Link mit
target="_blank" darin öffnet einen echten Browser-Tab, und die umgebende
Seite kann das NICHT abfangen: fremde Seiten liegen in einem anderen Origin,
die Same-Origin-Policy verbietet jeden Zugriff auf ihr DOM. Um den Klick
umzulenken, muss man das HTML selbst in die Hand nehmen - also proxen.

WAS DIESE FASSUNG ANDERS MACHT
------------------------------
Die erste Fassung konnte ausschliesslich GET, kannte keine Cookies und
reichte keine Header durch. Damit war jede Anmeldung von vornherein
unmöglich: Proxmox meldet sich per POST auf /api2/json/access/ticket an,
bekommt eine PVEAuthCookie zurück und schickt bei Schreibzugriffen einen
CSRFPreventionToken-Header mit. Kein einziger dieser drei Schritte kam
durch. Jetzt:

  * ALLE HTTP-Methoden, mit Rumpf.
  * Cookie-Behälter je Proxy-Sitzung. Die Cookies der fremden Seite bleiben
    auf dem Server - sie werden ausdrücklich NICHT an den Browser
    weitergereicht, dort gälten sie sonst für unsere Domain.
  * Anfrage- und Antwort-Header werden durchgereicht, bis auf die, die
    zwischen zwei Verbindungen nichts verloren haben.
  * WebSocket-Weiterleitung (/ws), damit Konsolen und Live-Ansichten laufen.
  * Echte Statuscodes. Vorher kam JEDER Fehler als "200 mit Fehlerseite"
    zurück - ein Bild, das in Wahrheit scheiterte, sah im Log gesund aus
    und landete im Browser als HTML statt als PNG. Nur die oberste
    Seitennavigation (doc=1) bekommt weiterhin eine lesbare Fehlerseite.

WAS ER TROTZDEM NICHT LEISTET
-----------------------------
Kein umschreibender Proxy ist 1:1. Seiten, die ihre Adressen zur Laufzeit
aus location.origin/host zusammensetzen, zeigen auf uns statt auf den
Ursprungsserver. Für solche Fälle gibt es im Browser den Schalter 🛡:
ausgeschaltet wird direkt geladen, dann ist alles echt - nur greift dann
das Abfangen von target="_blank" nicht mehr.

SICHERHEIT
----------
Ein Proxy im Backend holt Adressen aus dem Netz des SERVERS. In einem RMM
ist das oft erwünscht (internes Wiki, Switch-Oberfläche, Hypervisor), aber
es ist auch der klassische Weg, an Cloud-Zugangsdaten zu kommen. Deshalb:
Anmeldung zwingend über eine kurzlebige Proxy-Sitzung, nur http/https, die
Cloud-Metadatenadressen fest gesperrt, abschaltbar über die Einstellung
'webproxy_enabled'.
"""

import asyncio
import gzip
import http.cookiejar
import ipaddress
import json
import re
import secrets
import socket
import ssl
import time
import zlib
from html import escape as html_escape
from urllib.parse import quote, urljoin, urlparse
from urllib.request import (HTTPCookieProcessor, HTTPSHandler, Request as UrlRequest,
                            build_opener)
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from app import db
from app.auth import get_current_user

router = APIRouter(prefix="/api/webproxy", tags=["webproxy"])

# Kurzlebige Sitzungen: token -> (username, ablauf_ts)
_sessions: dict[str, tuple[str, float]] = {}
_SESSION_TTL = 3600.0

# Cookie-Behälter je Proxy-Sitzung. Die Cookies der fremden Seite bleiben
# hier auf dem Server: an den Browser weitergereicht gälten sie für UNSERE
# Domain und wären damit sowohl wirkungslos als auch ein Datenleck zwischen
# zwei angemeldeten Benutzern.
_jars: dict[str, http.cookiejar.CookieJar] = {}

_MAX_BYTES = 25 * 1024 * 1024
_TIMEOUT = 30

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal",
                  "metadata.goog", "100.100.100.200"}

# Header, die zu EINER Verbindung gehören und niemals weitergereicht werden.
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate",
               "proxy-authorization", "te", "trailer", "transfer-encoding",
               "upgrade"}

# Anfrage-Header, die wir selbst setzen oder bewusst unterdrücken.
_DROP_REQUEST = _HOP_BY_HOP | {
    "host",             # muss zum Ziel passen, nicht zu uns
    "cookie",           # verwaltet der Behälter
    "content-length",   # setzt urllib
    "accept-encoding",  # wir wollen unkomprimiert weiterverarbeiten
    "origin",           # unser Origin würde CSRF-Prüfungen der Gegenseite stören
    "referer",          # setzen wir passend zum Ziel
    "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user",
    "authorization",    # unser eigenes JWT hat beim Ziel nichts zu suchen
}

# Antwort-Header, die nicht zum Browser dürfen.
_DROP_RESPONSE = _HOP_BY_HOP | {
    "set-cookie",               # bleibt im Behälter, siehe oben
    "content-encoding",         # wir liefern entpackt aus
    "content-length",           # Länge ändert sich beim Umschreiben
    "x-frame-options",          # sonst liesse sich die Seite nicht einbetten
    "content-security-policy",  # gälte gegen den falschen Origin
    "content-security-policy-report-only",
    "strict-transport-security",
    "public-key-pins",
}


def _purge() -> None:
    now = time.time()
    for token in [t for t, (_, exp) in _sessions.items() if exp < now]:
        _sessions.pop(token, None)
        _jars.pop(token, None)
        _session_insecure.pop(token, None)


def _check_session(token: str) -> str:
    """
    Sitzung prüfen und dabei verlängern.

    Gleitende Frist statt harter Ablauf: sonst fällt der Rahmen mitten im
    Surfen auf eine Fehlerseite - und die Anmeldung bei der fremden Seite
    wäre gleich mit weg, weil der Cookie-Behälter an der Sitzung hängt.
    """
    _purge()
    entry = _sessions.get(token or "")
    if not entry:
        raise HTTPException(401, "Proxy-Sitzung abgelaufen - Seite neu laden")
    _sessions[token] = (entry[0], time.time() + _SESSION_TTL)
    return entry[0]


def _enabled() -> bool:
    return db.get_setting("webproxy_enabled", "1") == "1"


# ==================================================================
# Adressprüfung und Zertifikatsausnahmen
# ==================================================================

_host_checked: dict[str, float] = {}

# Zertifikatsausnahmen, die NUR für die laufende Proxy-Sitzung gelten.
_session_insecure: dict[str, set] = {}


def _validate(url: str) -> str:
    """
    Adresse prüfen. Das Ergebnis wird je Host gemerkt: eine Seite zieht
    schnell hundert Ressourcen nach, und eine Namensauflösung pro Bild
    würde den Event-Loop blockieren - getaddrinfo ist blockierend.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "Nur http und https werden weitergereicht")
    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(400, "Adresse ohne Hostnamen")
    if host in _BLOCKED_HOSTS:
        raise HTTPException(403, "Diese Adresse ist gesperrt")

    now = time.time()
    if _host_checked.get(host, 0) > now:
        return url
    try:
        for info in socket.getaddrinfo(host, None):
            if ipaddress.ip_address(info[4][0]).is_link_local:
                raise HTTPException(403, "Diese Adresse ist gesperrt")
    except (socket.gaierror, ValueError):
        pass
    _host_checked[host] = now + 300
    return url


def _trusted_hosts(username: str) -> set:
    try:
        rows = db._conn.execute(
            "SELECT host FROM webproxy_trusted_hosts WHERE username = ?",
            (username,)).fetchall()
        return {r["host"] for r in rows}
    except Exception:
        return set()


def _allow_insecure(token: str, username: str, host: str) -> bool:
    if host in _session_insecure.get(token, set()):
        return True
    return host in _trusted_hosts(username)


def _remember_host(username: str, host: str, reason: str) -> None:
    db._conn.execute(
        "INSERT OR REPLACE INTO webproxy_trusted_hosts"
        " (id, username, host, reason, created_at) VALUES (?, ?, ?, ?, ?)",
        (secrets.token_hex(8), username, host, reason[:300], int(time.time() * 1000)))
    db._conn.commit()


def _cert_problem(err: Exception) -> str:
    """
    Steckt hinter dem Fehler ein Zertifikatsproblem? Dann den Grund
    zurückgeben, sonst leer. Nur dann wird "trotzdem verbinden" angeboten -
    bei einem Netzwerkfehler wäre der Knopf nur ein Angebot, die Prüfung
    ohne Not abzuschalten.
    """
    reason = getattr(err, "reason", err)
    if isinstance(reason, (ssl.SSLCertVerificationError, ssl.SSLError)):
        return str(reason)
    text = str(reason)
    return text if ("CERTIFICATE_VERIFY_FAILED" in text or "SSL:" in text) else ""


# ==================================================================
# Umschreiben
# ==================================================================

_SKIP_SCHEMES = ("data:", "blob:", "javascript:", "mailto:", "tel:", "about:",
                 "#", "cid:")


def _skip(url: str) -> bool:
    u = (url or "").strip()
    return not u or u.lower().startswith(_SKIP_SCHEMES)


def _proxied(session: str, base: str, url: str) -> str:
    """Eine Adresse aus der Seite in eine Proxy-Adresse übersetzen."""
    if _skip(url):
        return url
    try:
        absolute = urljoin(base, url.strip())
    except ValueError:
        return url
    low = absolute.lower()
    if low.startswith(("ws://", "wss://")):
        return f"/api/webproxy/ws?s={quote(session)}&url={quote(absolute, safe='')}"
    if not low.startswith(("http://", "https://")):
        return url
    return f"/api/webproxy/fetch?s={quote(session)}&url={quote(absolute, safe='')}"


_URL_ATTRS = ("href", "src", "action", "poster", "data-src", "formaction",
              "xlink:href")
_ATTR_RE = re.compile(r"""\b(%s)\s*=\s*(["'])(.*?)\2""" % "|".join(
    a.replace(":", r"\:") for a in _URL_ATTRS), flags=re.I | re.S)
_SRCSET_RE = re.compile(r"""\b(srcset|data-srcset|imagesrcset)\s*=\s*(["'])(.*?)\2""",
                        flags=re.I | re.S)
_STYLE_ATTR_RE = re.compile(r"""\bstyle\s*=\s*(["'])(.*?)\1""", flags=re.I | re.S)
_STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", flags=re.I | re.S)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", flags=re.I | re.S)
_CSS_URL_RE = re.compile(r"""url\(\s*(["']?)([^)"']+)\1\s*\)""", flags=re.I)
_CSS_IMPORT_RE = re.compile(r"""@import\s+(["'])([^"']+)\1""", flags=re.I)
_META_CSP_RE = re.compile(
    r"""<meta\b[^>]*http-equiv\s*=\s*["']?content-security-policy["']?[^>]*>""",
    flags=re.I)
_INTEGRITY_RE = re.compile(r"""\sintegrity\s*=\s*(["']).*?\1""", flags=re.I | re.S)


def rewrite_css(css: str, session: str, base: str) -> str:
    """url(...) und @import in einem Stylesheet auf den Proxy umbiegen."""
    css = _CSS_IMPORT_RE.sub(
        lambda m: f'@import {m.group(1)}{_proxied(session, base, m.group(2))}{m.group(1)}',
        css)
    return _CSS_URL_RE.sub(
        lambda m: f'url({m.group(1)}{_proxied(session, base, m.group(2))}{m.group(1)})',
        css)


def _rewrite_srcset(value: str, session: str, base: str) -> str:
    parts = []
    for chunk in value.split(","):
        bits = chunk.strip().split(None, 1)
        if not bits:
            continue
        target = _proxied(session, base, bits[0])
        parts.append(f"{target} {bits[1]}" if len(bits) > 1 else target)
    return ", ".join(parts)


def _rewrite(html: str, final_url: str, session: str) -> str:
    """
    HTML so umschreiben, dass die Seite im Rahmen lädt.

    Bewusst mit regulären Ausdrücken statt mit einem Parser: ein Parser
    würde kaputtes HTML "reparieren" und dabei Seiten zerlegen, die im
    Browser einwandfrei laufen.

    Skriptblöcke werden vorher HERAUSGENOMMEN und unverändert
    zurückgelegt. Ohne das griffen die Muster auch mitten in JavaScript
    hinein: ein harmloses foo.url(x) oder ein href="..." in einer
    Zeichenkette wurde umgeschrieben und das Programm damit zerstört.
    Adressen, die erst im Skript entstehen, erledigt das eingesetzte
    Skript zur Laufzeit.
    """
    html = re.sub(r"<base\b[^>]*>", "", html, flags=re.I)
    html = _META_CSP_RE.sub("", html)
    html = _INTEGRITY_RE.sub("", html)

    scripts: list[str] = []

    def stash(m):
        scripts.append(m.group(0))
        return f"\x00RMMSCRIPT{len(scripts) - 1}\x00"

    html = _SCRIPT_BLOCK_RE.sub(stash, html)

    html = _ATTR_RE.sub(
        lambda m: f'{m.group(1)}={m.group(2)}'
                  f'{_proxied(session, final_url, m.group(3))}{m.group(2)}', html)
    html = _SRCSET_RE.sub(
        lambda m: f'{m.group(1)}={m.group(2)}'
                  f'{_rewrite_srcset(m.group(3), session, final_url)}{m.group(2)}', html)
    html = _STYLE_ATTR_RE.sub(
        lambda m: f'style={m.group(1)}'
                  f'{rewrite_css(m.group(2), session, final_url)}{m.group(1)}', html)
    html = _STYLE_BLOCK_RE.sub(
        lambda m: m.group(1) + rewrite_css(m.group(2), session, final_url) + m.group(3),
        html)

    html = re.sub(r"\x00RMMSCRIPT(\d+)\x00",
                  lambda m: scripts[int(m.group(1))], html)

    inject = (_INJECT_TPL
              .replace("__BASE__", json.dumps(final_url)[1:-1])
              .replace("__SESSION__", json.dumps(session)[1:-1]))

    head = re.search(r"<head\b[^>]*>", html, flags=re.I)
    if head:
        return html[:head.end()] + inject + html[head.end():]
    return inject + html


# ------------------------------------------------------------------
# Das eingesetzte Skript
# ------------------------------------------------------------------
_INJECT_TPL = """
<script>
(function () {
  var TAG = "rmm-webproxy";
  var BASE = "__BASE__";
  var SESSION = "__SESSION__";
  var PREFIX = "/api/webproxy/fetch?s=" + encodeURIComponent(SESSION) + "&url=";
  var WSPREFIX = "/api/webproxy/ws?s=" + encodeURIComponent(SESSION) + "&url=";
  var SKIP = /^(data:|blob:|javascript:|mailto:|tel:|about:|#|cid:)/i;
  var MINE = /^\\/api\\/webproxy\\/(fetch|ws)\\?/;

  function absolute(u) {
    try { return new URL(u, BASE).href; } catch (e) { return null; }
  }
  function via(u) {
    if (!u) return u;
    u = String(u);
    if (SKIP.test(u) || MINE.test(u) || u.indexOf("/api/webproxy/") === 0) return u;
    var abs = absolute(u);
    if (!abs) return u;
    if (/^wss?:/i.test(abs)) return WSPREFIX + encodeURIComponent(abs);
    if (!/^https?:/i.test(abs)) return u;
    return PREFIX + encodeURIComponent(abs);
  }
  window.__rmmVia = via;

  // Aus einer Proxy-Adresse wieder die echte machen. Nötig, weil das HTML
  // beim Umschreiben ALLE Adressen auf den Proxy gebogen hat - auch die
  // der _blank-Links.
  function unwrap(u) {
    if (!u) return u;
    u = String(u);
    if (u.indexOf("/api/webproxy/") === -1) return u;
    var m = /[?&]url=([^&]+)/.exec(u);
    if (!m) return u;
    try { return decodeURIComponent(m[1]); } catch (e) { return u; }
  }
  function tell(url, how) {
    if (!url) return;
    var real = unwrap(url);
    if (!/^https?:/i.test(real)) real = absolute(real) || real;
    try { parent.postMessage({ source: TAG, type: "open", url: String(real),
                               how: how || "blank" }, "*"); } catch (e) {}
  }

  // ---- Klicks, die einen neuen Tab meinen ----
  document.addEventListener("click", function (ev) {
    var a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
    if (!a) return;
    var target = (a.getAttribute("target") || "").toLowerCase();
    var href = a.getAttribute("href") || "";
    if (!href || SKIP.test(href)) return;
    if (!(target === "_blank" || ev.ctrlKey || ev.metaKey || ev.button === 1)) return;
    ev.preventDefault(); ev.stopPropagation();
    tell(href, "blank");
  }, true);
  document.addEventListener("auxclick", function (ev) {
    if (ev.button !== 1) return;
    var a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
    if (!a) return;
    var href = a.getAttribute("href") || "";
    if (!href || SKIP.test(href)) return;
    ev.preventDefault(); tell(href, "blank");
  }, true);
  try { window.open = function (url) { tell(url, "blank"); return null; }; } catch (e) {}
  document.addEventListener("submit", function (ev) {
    var f = ev.target;
    if (f && (f.getAttribute("target") || "").toLowerCase() === "_blank") {
      f.setAttribute("target", "_self");
    }
  }, true);

  // ---- Laufzeit-Adressen über den Proxy führen ----
  try {
    var realFetch = window.fetch;
    if (realFetch) {
      window.fetch = function (input, init) {
        try {
          if (typeof input === "string") input = via(input);
          else if (input && input.url) input = new Request(via(input.url), input);
        } catch (e) {}
        return realFetch.call(this, input, init);
      };
    }
  } catch (e) {}
  try {
    var realOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function () {
      var args = [].slice.call(arguments);
      try { args[1] = via(args[1]); } catch (e) {}
      return realOpen.apply(this, args);
    };
  } catch (e) {}
  // WebSocket: Konsolen und Live-Ansichten laufen darüber. Ohne diese
  // Umleitung versuchte die Seite eine Direktverbindung zum Ursprungsserver
  // und scheiterte an dessen Zertifikat oder an der Erreichbarkeit.
  try {
    var RealWS = window.WebSocket;
    if (RealWS) {
      var WrappedWS = function (url, protocols) {
        var u = String(url);
        if (!MINE.test(u)) {
          var abs = absolute(u) || u;
          var scheme = location.protocol === "https:" ? "wss:" : "ws:";
          u = scheme + "//" + location.host + WSPREFIX + encodeURIComponent(abs);
        }
        return protocols === undefined ? new RealWS(u) : new RealWS(u, protocols);
      };
      WrappedWS.prototype = RealWS.prototype;
      ["CONNECTING", "OPEN", "CLOSING", "CLOSED"].forEach(function (k) {
        WrappedWS[k] = RealWS[k];
      });
      window.WebSocket = WrappedWS;
    }
  } catch (e) {}
  // Adressen, die erst per JavaScript gesetzt werden - dazu gehören die
  // Hintergrundbilder, die per style.backgroundImage zugewiesen werden.
  try {
    var realSetAttr = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      try {
        var n = String(name).toLowerCase();
        if (n === "src" || n === "href" || n === "poster" || n === "xlink:href") {
          value = via(value);
        } else if (n === "style" && String(value).indexOf("url(") !== -1) {
          value = String(value).replace(/url\\(\\s*(['"]?)([^)'"]+)\\1\\s*\\)/gi,
            function (_, q, u) { return "url(" + q + via(u) + q + ")"; });
        }
      } catch (e) {}
      return realSetAttr.call(this, name, value);
    };
  } catch (e) {}
  try {
    var realSetProp = CSSStyleDeclaration.prototype.setProperty;
    CSSStyleDeclaration.prototype.setProperty = function (prop, value, prio) {
      try {
        if (value && String(value).indexOf("url(") !== -1) {
          value = String(value).replace(/url\\(\\s*(['"]?)([^)'"]+)\\1\\s*\\)/gi,
            function (_, q, u) { return "url(" + q + via(u) + q + ")"; });
        }
      } catch (e) {}
      return realSetProp.call(this, prop, value, prio);
    };
  } catch (e) {}
  function fix(el) {
    if (!el || el.nodeType !== 1) return;
    ["src", "href", "poster"].forEach(function (attr) {
      var v = el.getAttribute && el.getAttribute(attr);
      if (v && !SKIP.test(v) && !MINE.test(v)) {
        var n = via(v);
        if (n !== v) el.setAttribute(attr, n);
      }
    });
    if (el.getAttribute && el.getAttribute("target") === "_blank") {
      el.setAttribute("target", "_self");
    }
  }
  try {
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          fix(added[j]);
          if (added[j].querySelectorAll) {
            var kids = added[j].querySelectorAll("[src],[href],[poster],[target]");
            for (var k = 0; k < kids.length; k++) fix(kids[k]);
          }
        }
      }
    }).observe(document.documentElement, { childList: true, subtree: true });
  } catch (e) {}

  try { parent.postMessage({ source: TAG, type: "url", url: BASE }, "*"); } catch (e) {}
})();
</script>
"""


# ==================================================================
# Transport
# ==================================================================

def _opener(session: str, insecure: bool):
    """
    Ein urllib-Opener mit dem Cookie-Behälter DIESER Proxy-Sitzung.

    Der Behälter ist der Grund, warum eine Anmeldung überhaupt halten kann:
    die Gegenseite setzt beim Anmelden ein Cookie, und es muss bei jeder
    Folgeanfrage wieder mitgehen.
    """
    jar = _jars.setdefault(session, http.cookiejar.CookieJar())
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return build_opener(HTTPCookieProcessor(jar), HTTPSHandler(context=ctx))


def _decode_body(data: bytes, encoding: str) -> bytes:
    """Antwort entpacken. Wir liefern immer unkomprimiert aus."""
    enc = (encoding or "").lower().strip()
    try:
        if enc == "gzip":
            return gzip.decompress(data)
        if enc == "deflate":
            return zlib.decompress(data, -zlib.MAX_WBITS)
    except Exception:
        pass
    return data


def _do_request(session: str, method: str, url: str, headers: dict,
                body: bytes | None, insecure: bool):
    """
    Anfrage stellen (blockierend, läuft im Thread).
    Rückgabe: (status, header-liste, rumpf, endgültige adresse)
    """
    parsed = urlparse(url)
    send = {k: v for k, v in headers.items() if k.lower() not in _DROP_REQUEST}
    send.setdefault("User-Agent", _UA)
    send.setdefault("Accept", "*/*")
    # Referer und Origin passend zum ZIEL setzen: viele Anwendungen prüfen
    # beides gegen sich selbst und lehnen sonst jeden Schreibzugriff ab.
    send["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    if method not in ("GET", "HEAD"):
        send["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

    req = UrlRequest(url, data=body if method not in ("GET", "HEAD") else None,
                     headers=send, method=method)
    try:
        resp = _opener(session, insecure).open(req, timeout=_TIMEOUT)
    except HTTPError as e:
        # Auch Fehlerantworten haben Rumpf und Header, die die Seite braucht -
        # ein 401 mit JSON-Rumpf ist für eine Anmeldemaske eine Information,
        # keine Panne.
        resp = e
    with resp:
        status = resp.getcode() or 200
        raw = dict(resp.headers.items())
        data = resp.read(_MAX_BYTES + 1)
        final = resp.geturl()
    if len(data) > _MAX_BYTES:
        raise HTTPException(413, "Antwort zu gross für den internen Browser")
    data = _decode_body(data, raw.get("Content-Encoding", ""))
    return status, raw, data, final


def _charset(ctype: str, data: bytes) -> str:
    m = re.search(r"charset=([\w-]+)", ctype, flags=re.I)
    if m:
        return m.group(1)
    m = re.search(rb'charset=["\']?([\w-]+)', data[:4096], flags=re.I)
    return m.group(1).decode("ascii", "ignore") if m else "utf-8"


def _pass_headers(raw: dict) -> dict:
    out = {}
    for k, v in raw.items():
        if k.lower() in _DROP_RESPONSE:
            continue
        out[k] = v
    return out


# ==================================================================
# Endpunkte
# ==================================================================

@router.post("/session")
async def create_session(user: dict = Depends(get_current_user)):
    """
    Kurzlebige Proxy-Sitzung anlegen.

    Warum nicht das JWT in die iframe-Adresse: die steht im Verlauf, im
    Referer und in jedem Serverlog der aufgerufenen Seite. An dieser Sitzung
    hängt ausserdem der Cookie-Behälter - eine neue Sitzung bedeutet also
    auch eine neue Anmeldung bei der fremden Seite.
    """
    if not _enabled():
        raise HTTPException(403, "Der interne Seitenproxy ist abgeschaltet")
    _purge()
    token = secrets.token_urlsafe(24)
    _sessions[token] = (user.get("username", ""), time.time() + _SESSION_TTL)
    _jars[token] = http.cookiejar.CookieJar()
    return {"token": token, "ttl": int(_SESSION_TTL)}


@router.api_route("/fetch",
                  methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def fetch_page(request: Request, s: str = Query(...), url: str = Query(...),
                     doc: int = Query(0)):
    """
    Anfrage an die Zieladresse durchreichen.

    Kein Depends(get_current_user): ein <iframe> kann keinen
    Authorization-Header setzen. Die Berechtigung steckt in der
    Proxy-Sitzung 's'.

    'doc=1' markiert die oberste Seitennavigation. Nur dort ergibt eine
    lesbare Fehlerseite Sinn; für Bilder und API-Aufrufe muss der echte
    Statuscode durch, sonst bekommt der Browser HTML, wo er ein PNG
    erwartet - und im Log sieht alles nach 200 OK aus.
    """
    if not _enabled():
        raise HTTPException(403, "Der interne Seitenproxy ist abgeschaltet")
    username = _check_session(s)
    target = _validate(url)
    host = (urlparse(target).hostname or "").lower()
    insecure = _allow_insecure(s, username, host)
    is_doc = bool(doc)

    body = await request.body()
    headers = dict(request.headers)

    loop = asyncio.get_event_loop()
    try:
        status, raw, data, final = await loop.run_in_executor(
            None, _do_request, s, request.method, target, headers, body, insecure)
    except HTTPException:
        raise
    except Exception as e:
        cert = _cert_problem(e)
        if cert and not insecure and is_doc:
            return HTMLResponse(_cert_page(target, host, cert, s), status_code=200)
        detail = cert or f"{e.__class__.__name__}: {e}"
        if is_doc:
            return HTMLResponse(_error_page(target, detail), status_code=200)
        # Für Unterressourcen ein ehrlicher Fehler statt einer 200er Attrappe.
        raise HTTPException(502, detail[:300])

    ctype = raw.get("Content-Type", "application/octet-stream")
    kind = ctype.lower()
    out_headers = _pass_headers(raw)

    if "css" in kind:
        css = rewrite_css(data.decode(_charset(ctype, data), errors="replace"), s, final)
        out_headers["Content-Type"] = "text/css"
        return Response(content=css, status_code=status, headers=out_headers)

    if "html" in kind and status < 400 or ("html" in kind and is_doc):
        text = data.decode(_charset(ctype, data), errors="replace")
        out_headers["Content-Type"] = "text/html; charset=utf-8"
        return HTMLResponse(content=_rewrite(text, final, s), status_code=status,
                            headers=out_headers)

    # Alles Übrige unverändert: Bilder, Schriften, JSON, JavaScript. Skripte
    # werden ABSICHTLICH nicht angefasst - Textersatz in Programmen zerstört
    # zuverlässig mehr als er repariert; dafür sorgt das eingesetzte Skript.
    return Response(content=data, status_code=status, headers=out_headers,
                    media_type=ctype.split(";")[0].strip())


@router.websocket("/ws")
async def proxy_ws(ws: WebSocket, s: str = Query(...), url: str = Query(...)):
    """
    WebSocket-Verbindungen durchreichen (Konsolen, Live-Ansichten).

    Ohne das bleibt zum Beispiel die noVNC-Konsole schwarz: sie läuft
    vollständig über WebSocket, und eine Direktverbindung aus dem Rahmen
    heraus scheitert am selbstsignierten Zertifikat des Zielservers.
    """
    if not _enabled():
        await ws.close(code=1008)
        return
    try:
        username = _check_session(s)
        target = _validate(url)
    except HTTPException:
        await ws.close(code=1008)
        return

    try:
        import websockets
    except ImportError:
        # Gehört zu uvicorn[standard]; fehlt sie, ist die Installation
        # unvollständig - das gehört gesagt, nicht stillschweigend geschluckt.
        print("[webproxy] Modul 'websockets' fehlt - WebSocket-Weiterleitung aus")
        await ws.close(code=1011)
        return

    host = (urlparse(target).hostname or "").lower()
    ctx = None
    if target.lower().startswith("wss://"):
        ctx = ssl.create_default_context()
        if _allow_insecure(s, username, host):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

    # Cookies aus dem Behälter mitgeben: die Konsole ist ohne die
    # Anmeldesitzung der Seite wertlos.
    cookies = []
    jar = _jars.get(s)
    if jar:
        for c in jar:
            if c.domain.lstrip(".") in host:
                cookies.append(f"{c.name}={c.value}")
    headers = {"Cookie": "; ".join(cookies)} if cookies else {}

    await ws.accept()
    try:
        async with websockets.connect(target, ssl=ctx, additional_headers=headers,
                                      open_timeout=20, max_size=None) as upstream:

            async def to_upstream():
                while True:
                    msg = await ws.receive()
                    if msg.get("type") == "websocket.disconnect":
                        return
                    if msg.get("text") is not None:
                        await upstream.send(msg["text"])
                    elif msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])

            async def to_browser():
                async for msg in upstream:
                    if isinstance(msg, bytes):
                        await ws.send_bytes(msg)
                    else:
                        await ws.send_text(msg)

            done, pending = await asyncio.wait(
                [asyncio.create_task(to_upstream()), asyncio.create_task(to_browser())],
                return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except Exception as e:
        print(f"[webproxy] WebSocket {target}: {e.__class__.__name__}: {e}")
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ------------------------------------------------------------------
# Zertifikatsausnahmen
# ------------------------------------------------------------------

class TrustBody(BaseModel):
    s: str
    host: str
    remember: bool = False
    reason: str = ""


@router.post("/trust")
async def trust_host(body: TrustBody):
    """
    Zertifikat eines Hosts akzeptieren.

    remember=False - gilt nur für die laufende Proxy-Sitzung.
    remember=True  - wird für DIESEN Benutzer gespeichert. Nicht für alle:
                     eine Ausnahme, die einer setzt, darf nicht
                     stillschweigend für die ganze Mannschaft gelten.
    """
    if not _enabled():
        raise HTTPException(403, "Der interne Seitenproxy ist abgeschaltet")
    username = _check_session(body.s)
    host = (body.host or "").strip().lower()
    if not host:
        raise HTTPException(400, "Kein Host angegeben")
    _session_insecure.setdefault(body.s, set()).add(host)
    if body.remember:
        _remember_host(username, host, body.reason)
    db.add_audit_entry(
        username, "webproxy.trust_certificate", target=host,
        details=("dauerhaft für diesen Benutzer" if body.remember
                 else "nur für diese Sitzung") + f" - {body.reason[:200]}")
    return {"ok": True, "host": host, "remembered": bool(body.remember)}


@router.get("/trusted")
async def list_trusted(user: dict = Depends(get_current_user)):
    rows = db._conn.execute(
        "SELECT host, reason, created_at FROM webproxy_trusted_hosts"
        " WHERE username = ? ORDER BY host", (user.get("username", ""),)).fetchall()
    return [dict(r) for r in rows]


@router.delete("/trusted/{host}")
async def drop_trusted(host: str, user: dict = Depends(get_current_user)):
    username = user.get("username", "")
    db._conn.execute(
        "DELETE FROM webproxy_trusted_hosts WHERE username = ? AND host = ?",
        (username, host.lower()))
    db._conn.commit()
    for allowed in _session_insecure.values():
        allowed.discard(host.lower())
    db.add_audit_entry(username, "webproxy.untrust_certificate", target=host)
    return {"ok": True}


# ------------------------------------------------------------------
# Fehlerseiten (nur für die oberste Seitennavigation)
# ------------------------------------------------------------------

def _error_page(url: str, message: str) -> str:
    return f"""<!doctype html><meta charset="utf-8">
<div style="font:14px/1.6 system-ui,sans-serif;padding:34px;color:#334">
  <div style="font-size:34px;margin-bottom:8px">🌐</div>
  <h2 style="margin:0 0 6px;font-size:16px">Seite konnte nicht geladen werden</h2>
  <p style="margin:0 0 4px;color:#667">{html_escape(message)}</p>
  <p style="margin:0;color:#889;font-size:12px">{html_escape(url)}</p>
</div>"""


def _cert_page(url: str, host: str, reason: str, session: str) -> str:
    """
    Zertifikats-Warnung mit den beiden Auswegen.

    Bewusst ohne Schönfärberei: es steht da, was nicht stimmt und was der
    Klick bedeutet.
    """
    esc_url = html_escape(url)
    esc_host = html_escape(host)
    esc_reason = html_escape(reason)

    # Werte mit json erzeugen, NICHT mit repr: die Adresse kommt vom
    # Benutzer, ein enthaltenes "</script>" bräche sonst aus dem Skriptblock
    # aus. Zusätzlich jedes "</" entschärfen - der HTML-Parser sieht den
    # Block vor dem JavaScript-Parser.
    def js(value: str) -> str:
        return json.dumps(str(value)).replace("</", "<\\/")

    j_session, j_host, j_url, j_reason = js(session), js(host), js(url), js(reason)
    return f"""<!doctype html><meta charset="utf-8">
<style>
  body {{ font:14px/1.6 system-ui,-apple-system,Segoe UI,sans-serif; margin:0;
          background:#faf9f7; color:#2b2b2b; }}
  .wrap {{ max-width:620px; margin:0 auto; padding:44px 26px; }}
  h1 {{ font-size:19px; margin:0 0 10px; }}
  .detail {{ background:#fff; border:1px solid #e5e1db; border-radius:8px;
             padding:11px 13px; font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;
             color:#6b6255; word-break:break-word; margin:14px 0 18px; }}
  .row {{ display:flex; gap:9px; flex-wrap:wrap; }}
  button {{ font:inherit; font-size:13px; padding:8px 15px; border-radius:7px;
            border:1px solid #d8d3cb; background:#fff; color:#2b2b2b; cursor:pointer; }}
  button.primary {{ background:#2b2b2b; color:#fff; border-color:#2b2b2b; }}
  .note {{ color:#7a7268; font-size:12px; margin-top:16px; }}
  .err {{ color:#b23; font-size:12px; margin-top:10px; }}
</style>
<div class="wrap">
  <div style="font-size:32px">🔒</div>
  <h1>Dem Zertifikat von <span style="font-weight:600">{esc_host}</span> wird nicht vertraut</h1>
  <p style="margin:0;color:#5c554c">
    Der Server konnte seine Identität nicht belegen. Bei internen Geräten mit
    selbstsigniertem Zertifikat ist das normal. Auf einer öffentlichen Seite
    wäre es ein Grund, hier abzubrechen.
  </p>
  <div class="detail">{esc_reason}<br>{esc_url}</div>
  <div class="row">
    <button class="primary" id="once">Trotzdem verbinden</button>
    <button id="always">Trotzdem verbinden und merken</button>
  </div>
  <div class="note">
    „Trotzdem verbinden" gilt nur, bis du dich abmeldest. „…und merken" gilt
    dauerhaft für dein Benutzerkonto – für andere Benutzer nicht. Beides steht
    im Prüfprotokoll und lässt sich jederzeit zurücknehmen.
  </div>
  <div class="err" id="err"></div>
</div>
<script>
(function () {{
  var S = {j_session}, HOST = {j_host}, URL_ = {j_url}, REASON = {j_reason};
  function go(remember, btn) {{
    btn.disabled = true;
    fetch("/api/webproxy/trust", {{
      method: "POST", headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ s: S, host: HOST, remember: remember, reason: REASON }})
    }}).then(function (r) {{
      if (!r.ok) throw new Error("HTTP " + r.status);
      location.replace("/api/webproxy/fetch?doc=1&s=" + encodeURIComponent(S)
                       + "&url=" + encodeURIComponent(URL_));
    }}).catch(function (e) {{
      btn.disabled = false;
      document.getElementById("err").textContent =
        "Ausnahme konnte nicht gesetzt werden: " + e.message;
    }});
  }}
  document.getElementById("once").onclick = function () {{ go(false, this); }};
  document.getElementById("always").onclick = function () {{ go(true, this); }};
}})();
</script>"""

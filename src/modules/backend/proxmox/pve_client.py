# pve_client.py (Modul "proxmox") - Verbindung zur Proxmox-VE-API (/api2/json)
# ----------------------------------------------------------------------------
# Nur Standardbibliothek (urllib). Pro Agent ein Client (Zwischenspeicher).
#
# Adresse:
#   local   https://<IP des Agenten>:<port>
#   custom  https://<eigene Adresse>:<port>
#   relay   Tunnel durch den Agenten: Modul "Netzwerk" legt eine Weiterleitung
#           Server:<freier Port> -> Agent -> <relay_host>:<port> an, erreichbar NUR
#           von 127.0.0.1. Das Backend spricht dann https://127.0.0.1:<freier Port>.
# Anmeldung:
#   token     Header "Authorization: PVEAPIToken=<user>!<tokenid>=<secret>"
#   password  POST /access/ticket -> Cookie PVEAuthCookie + CSRFPreventionToken (2 h gueltig,
#             wird nach 90 min automatisch erneuert)
# TLS: Zertifikat pruefen, ODER nur den SHA-256-Fingerabdruck vergleichen (selbst signiert),
#      ODER (nicht empfohlen) gar nicht pruefen.

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app import db

from . import pve_store

TIMEOUT = 30
_clients: dict[str, "PveClient"] = {}
_lock = threading.RLock()


class PveError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _fp(s: str) -> str:
    return (s or "").replace(":", "").replace(" ", "").lower()


class _PinnedHTTPS(urllib.request.HTTPSHandler):
    """HTTPS mit Fingerabdruck-Vergleich statt Zertifikatskette (selbst signierte Zertifikate).
    Geprueft wird DIREKT nach dem Verbindungsaufbau - bevor Zugangsdaten gesendet werden."""

    def __init__(self, fingerprint: str):
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        super().__init__(context=self._ctx)
        self.fingerprint = _fp(fingerprint)

    def https_open(self, req):
        expected = self.fingerprint

        class _Conn(http.client.HTTPSConnection):
            def connect(self):
                super().connect()
                der = self.sock.getpeercert(binary_form=True)
                if not der or hashlib.sha256(der).hexdigest() != expected:
                    self.sock.close()
                    raise PveError(495, "Zertifikats-Fingerabdruck stimmt nicht überein")

        return self.do_open(_Conn, req, context=self._ctx)


def fetch_fingerprint(host: str, port: int) -> str:
    """SHA-256-Fingerabdruck des Server-Zertifikats (zum Uebernehmen im Dialog)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ss:
            der = ss.getpeercert(binary_form=True)
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


class PveClient:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.cfg = pve_store.get(client_id, secrets=True)
        if not self.cfg:
            raise PveError(404, "Für diesen Agenten ist keine Proxmox-Verbindung eingerichtet")
        self._ticket = None
        self._csrf = None
        self._ticket_at = 0.0
        self._relay = None          # (listen_port, expires_ts, forward_id)
        self._opener = self._make_opener()

    # ------------------------------------------------------------ Adresse
    def _make_opener(self):
        c = self.cfg
        if c.get("fingerprint"):
            return urllib.request.build_opener(_PinnedHTTPS(c["fingerprint"]))
        ctx = ssl.create_default_context()
        if not c.get("verify_tls", False):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))

    def _relay_port(self) -> int:
        """Tunnel durch den Agenten bereitstellen (wiederverwenden, solange gueltig)."""
        from app import modules
        now = time.time()
        if self._relay and self._relay[1] - 300 > now:
            return self._relay[0]
        pf = modules.use("network", "portforward") if modules.is_enabled("network") else None
        if pf is None or not modules.feature("network", "portforward"):
            raise PveError(409, "RMM-Relay braucht das Modul „Netzwerk“ mit Port-Weiterleitung")
        # Die Weiterleitung spricht ueber die Live-Verbindung mit dem Agenten -> auf der
        # HAUPT-Ereignisschleife des Servers ausfuehren (wir laufen hier in einem Thread).
        main_loop = getattr(modules, "_loop", None)
        if main_loop is None:
            raise PveError(503, "Server noch nicht bereit")
        coro = pf.create(client_id=self.client_id, target_port=int(self.cfg.get("port") or 8006), username="system:proxmox",
                         minutes=720, target_host=self.cfg.get("relay_host") or "127.0.0.1", allow_from="127.0.0.1/32",
                         label="Proxmox-API (RMM-Relay)")
        try:
            rec = asyncio.run_coroutine_threadsafe(coro, main_loop).result(timeout=30)
        except ValueError as e:
            raise PveError(409, f"Agent nicht erreichbar: {e}")
        except RuntimeError as e:
            raise PveError(503, str(e))
        except TimeoutError:
            raise PveError(504, "Tunnel durch den Agenten konnte nicht aufgebaut werden (Zeitüberschreitung)")
        self._relay = (int(rec["listen_port"]), float(rec.get("expires_at") or now + 43200), rec.get("id"))
        return self._relay[0]

    def base(self) -> str:
        c = self.cfg
        port = int(c.get("port") or 8006)
        mode = c.get("addr_mode") or "local"
        if mode == "relay":
            return f"https://127.0.0.1:{self._relay_port()}/api2/json"
        if mode == "custom":
            host = (c.get("host") or "").strip()
            if not host:
                raise PveError(400, "Keine Adresse eingetragen")
        else:
            cl = db.get_client(self.client_id) or {}
            host = (cl.get("ip") or "").strip()
            if not host:
                raise PveError(409, "Der Agent hat keine lokale IP gemeldet – bitte eigene Adresse oder RMM-Relay wählen")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"https://{host}:{port}/api2/json"

    # ------------------------------------------------------------ Anmeldung
    def _auth_headers(self, method: str) -> dict:
        c = self.cfg
        if c.get("auth") == "token":
            return {"Authorization": f"PVEAPIToken={c.get('user')}!{c.get('token_id')}={c.get('secret')}"}
        if not self._ticket or time.time() - self._ticket_at > 90 * 60:
            self._login()
        h = {"Cookie": f"PVEAuthCookie={self._ticket}"}
        if method != "GET":
            h["CSRFPreventionToken"] = self._csrf
        return h

    def _login(self) -> None:
        data = urllib.parse.urlencode({"username": self.cfg.get("user", ""), "password": self.cfg.get("password", "")}).encode()
        res = self._send("POST", "/access/ticket", data, {"Content-Type": "application/x-www-form-urlencoded"}, auth=False)
        self._ticket, self._csrf, self._ticket_at = res["ticket"], res["CSRFPreventionToken"], time.time()

    def ticket(self) -> tuple[str, str]:
        """Ticket fuer Konsole/Weboberflaeche (bei Token-Anmeldung nicht verfuegbar)."""
        if self.cfg.get("auth") == "token":
            raise PveError(409, "Für die Konsole im Dashboard wird die Anmeldung mit Benutzer + Passwort gebraucht")
        if not self._ticket or time.time() - self._ticket_at > 90 * 60:
            self._login()
        return self._ticket, self._csrf

    # ------------------------------------------------------------ Aufrufe
    def _send(self, method: str, path: str, data: bytes | None, headers: dict, auth: bool = True):
        url = self.base() + path
        h = {"Accept": "application/json", **headers}
        if auth:
            h.update(self._auth_headers(method))
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self._opener.open(req, timeout=TIMEOUT) as r:
                body = json.loads(r.read() or b"{}")
                return body.get("data")
        except urllib.error.HTTPError as e:
            msg = e.reason
            try:
                j = json.loads(e.read() or b"{}")
                msg = j.get("message") or (json.dumps(j.get("errors")) if j.get("errors") else msg)
            except Exception:
                pass
            if e.code == 401 and auth and self.cfg.get("auth") != "token":
                self._ticket = None
            raise PveError(e.code, f"Proxmox: {str(msg).strip()}")
        except PveError:
            raise
        except ssl.SSLCertVerificationError:
            raise PveError(495, "Proxmox-Zertifikat nicht vertrauenswürdig – Fingerabdruck übernehmen oder Prüfung abschalten")
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                raise PveError(495, "Proxmox-Zertifikat nicht vertrauenswürdig (selbst signiert?) – "
                                    "Fingerabdruck übernehmen oder TLS-Prüfung abschalten")
            if isinstance(getattr(e, "reason", None), PveError):
                raise e.reason
            if self.cfg.get("addr_mode") == "relay":
                self._relay = None          # Tunnel beim naechsten Mal neu aufbauen
            raise PveError(502, f"Proxmox nicht erreichbar: {getattr(e, 'reason', e)}")

    def call(self, method: str, path: str, params: dict | None = None):
        method = method.upper()
        path = "/" + path.lstrip("/")
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if method in ("GET", "DELETE"):
            if params:
                path += "?" + urllib.parse.urlencode(params, doseq=True)
            return self._send(method, path, None, {})
        data = urllib.parse.urlencode(
            {k: (int(v) if isinstance(v, bool) else v) for k, v in params.items()}, doseq=True).encode()
        return self._send(method, path, data, {"Content-Type": "application/x-www-form-urlencoded"})

    def get(self, path, **params):
        return self.call("GET", path, params)

    def post(self, path, **params):
        return self.call("POST", path, params)


def client_for(client_id: str, fresh: bool = False) -> PveClient:
    with _lock:
        cl = _clients.get(client_id)
        if fresh or cl is None:
            cl = PveClient(client_id)
            _clients[client_id] = cl
        return cl


def forget(client_id: str) -> None:
    with _lock:
        _clients.pop(client_id, None)


def test(client_id: str) -> dict:
    cl = client_for(client_id, fresh=True)
    ver = cl.get("/version") or {}
    nodes = cl.get("/nodes") or []
    return {"version": ver.get("version"), "release": ver.get("release"), "nodes": [n.get("node") for n in nodes],
            "address": cl.base().replace("/api2/json", "")}

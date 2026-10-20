# sshd.py (Modul "console") - das Backend als SSH-Server
# ------------------------------------------------------
#   ssh <benutzer>@<rmm-server> -p <port>      (Standard-Port 2222)
# Anmeldung = die ECHTE Dashboard-Anmeldung (/api/auth/login intern): Passwort, AD/LDAP,
# Kontosperren und Zwei-Faktor-Code (SSH fragt dann nach dem Code). Zusaetzlich noetig:
# Recht "use_console". Optional nur von freigegebenen IPs (Einstellung ssh_allow_ips).
# Nach der Anmeldung startet die RMM-Konsole (shell.py) - kein Zugriff auf das Server-System.
# Host-Schluessel: data/modules/console/ssh_host_ed25519 (einmal erzeugt, dann immer gleich).
# Einstellungen werden alle 5 s geprueft; an/aus und Portwechsel wirken ohne Neustart.

from __future__ import annotations

import asyncio
import ipaddress
import json

from app import db, modules
from app.auth import is_super_admin, user_has_permission
from app.paths import MODULES_DATA_DIR

KEY_FILE = MODULES_DATA_DIR / "console" / "ssh_host_ed25519"
_server = None
_running: dict = {"port": None, "error": None, "fingerprint": None}
_watch_task: asyncio.Task | None = None


def _setting(key, default=None):
    return modules.setting("console", key, default)


def _host_key():
    import asyncssh
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        key = asyncssh.generate_private_key("ssh-ed25519")
        key.write_private_key(str(KEY_FILE))
        KEY_FILE.chmod(0o600)
    key = asyncssh.read_private_key(str(KEY_FILE))
    _running["fingerprint"] = key.get_fingerprint()
    return key


def _ip_allowed(ip: str) -> bool:
    raw = (_setting("ssh_allow_ips", "") or "").strip()
    if not raw:
        return True
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if a in ipaddress.ip_network(part, strict=False):
                return True
        except ValueError:
            continue
    return False


async def internal_login(username: str, password: str, code: str, ip: str) -> tuple[int, dict | None, str]:
    """Die echte Dashboard-Anmeldung intern aufrufen (Sperren, AD, 2FA). -> (status, user, detail)"""
    app = modules._api
    body = json.dumps({"username": username, "password": password, "realm": "local", **({"code": code} if code else {})}).encode()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/api/auth/login", "raw_path": b"/api/auth/login", "root_path": "", "query_string": b"",
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                         (b"user-agent", b"rmm-ssh"), (b"host", b"localhost")],
             "client": (ip, 0), "server": ("localhost", 0)}
    sent, status, chunks = False, 500, []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))
    await app(scope, receive, send)
    try:
        data = json.loads(b"".join(chunks) or b"{}")
    except ValueError:
        data = {}
    if status != 200:
        return status, None, str(data.get("detail") or "Anmeldung fehlgeschlagen")
    user = db.get_user_by_id((data.get("user") or {}).get("id", ""))
    return (200, user, "") if user else (401, None, "Anmeldung fehlgeschlagen")


def may_use(user: dict) -> bool:
    return bool(user) and (is_super_admin(user) or user_has_permission(user, "use_console"))


def _make_server_class():
    import asyncssh

    class RmmSSH(asyncssh.SSHServer):
        """Anmeldung:
        1. normale Passwort-Anmeldung (kann jeder SSH-Client)
        2. Konten mit Zwei-Faktor: "keyboard-interactive" mit EINER Rueckfrage (Passwort + Einmalcode)
        Kein Zustand zwischen den Runden - jede Antwort wird vollstaendig neu geprueft."""

        MAX_FAILS = 3              # danach Verbindung trennen (sonst unbegrenzte Versuche je Verbindung!)

        def __init__(self):
            self.ip, self.user, self.conn, self.fails = "", None, None, 0

        def connection_made(self, conn):
            self.conn = conn
            self.ip = (conn.get_extra_info("peername") or ("", 0))[0]
            if not _ip_allowed(self.ip):
                conn.close()

        def begin_auth(self, username):
            return True

        async def _check(self, username, password, code=""):
            status, user, detail = await internal_login(username, password, code, self.ip)
            if status != 200 or not user:
                self.fails += 1
                if self.fails >= self.MAX_FAILS and self.conn:
                    asyncio.get_running_loop().call_soon(self.conn.close)
                return False
            if not may_use(user):
                db.add_audit_entry(user["username"], "console.ssh_denied", details=f"ohne Recht use_console von {self.ip}")
                if self.conn:
                    asyncio.get_running_loop().call_soon(self.conn.close)   # weitere Versuche zwecklos
                return False
            self.user = user
            db.add_audit_entry(user["username"], "console.ssh_login", details=f"von {self.ip}")
            return True

        def password_auth_supported(self):
            return True

        async def validate_password(self, username, password):
            return await self._check(username, password)                                           # Konten mit 2FA scheitern hier -> keyboard-interactive

        def kbdint_auth_supported(self):
            return True

        def get_kbdint_challenge(self, username, lang, submethods):
            return ("RAPALLE.net RMM", "Anmeldung (Einmalcode nur bei Zwei-Faktor-Anmeldung)", "",
                    [("Passwort: ", False), ("Einmalcode (sonst leer lassen): ", True)])

        async def validate_kbdint_response(self, username, responses):
            if len(responses) < 2:
                return False
            return await self._check(username, responses[0], responses[1].strip())

    return RmmSSH


async def _session(process):
    """Eine SSH-Sitzung -> RMM-Konsole."""
    from asyncssh import TerminalSizeChanged
    from .shell import Console
    server = process.get_extra_info("connection").get_owner()
    user = getattr(server, "user", None)
    if not user:
        process.exit(1)
        return
    cols, rows, *_ = process.get_terminal_size() or (80, 24)

    def out(s: str):
        try:
            process.stdout.write(s)
        except Exception:
            pass

    done = asyncio.Event()
    con = Console(user, out, done.set, cols or 80, rows or 24, ip=server.ip, via="SSH")
    await con.start()
    try:
        while not done.is_set():
            try:
                data = await process.stdin.read(4096)
            except TerminalSizeChanged as e:
                con.resize(e.width, e.height)
                continue
            if not data:
                break
            await con.feed(data)
    except Exception:
        pass
    finally:
        con.close()
        db.add_audit_entry(user["username"], "console.ssh_logout", details=f"von {server.ip}")
        process.exit(0)


async def start() -> None:
    global _server
    await stop()
    if str(_setting("ssh_enabled", "no")) != "yes":
        _running.update(port=None, error=None)
        return
    try:
        import asyncssh
    except ImportError:
        _running.update(port=None, error="asyncssh fehlt (wird beim Aktivieren des Moduls installiert)")
        return
    port = int(_setting("ssh_port", 2222) or 2222)
    try:
        _server = await asyncssh.create_server(_make_server_class(), "", port, server_host_keys=[_host_key()],
                                               process_factory=_session, line_editor=False, encoding="utf-8",
                                               login_timeout=60, keepalive_interval=60)
        _running.update(port=port, error=None)
        print(f"[console] SSH-Server lauscht auf Port {port}")
    except OSError as e:
        _running.update(port=None, error=f"Port {port} nicht verfügbar: {e}")
        print(f"[console] SSH-Server: {_running['error']}")


async def stop() -> None:
    global _server
    if _server is not None:
        _server.close()
        try:
            await _server.wait_closed()
        except Exception:
            pass
        _server = None
    _running["port"] = None


async def _watch() -> None:
    last = None
    while True:
        cur = (str(_setting("ssh_enabled", "no")), int(_setting("ssh_port", 2222) or 2222))
        if cur != last:
            last = cur
            await start()
        await asyncio.sleep(5)


def begin() -> None:
    global _watch_task
    if _watch_task is None or _watch_task.done():
        _watch_task = asyncio.get_running_loop().create_task(_watch())


async def end() -> None:
    global _watch_task
    if _watch_task:
        _watch_task.cancel()
        _watch_task = None
    await stop()


def status() -> dict:
    if _running.get("fingerprint") is None and KEY_FILE.exists():
        try:
            _host_key()
        except Exception:
            pass
    return {"enabled": str(_setting("ssh_enabled", "no")) == "yes", "port": _running.get("port"),
            "configured_port": int(_setting("ssh_port", 2222) or 2222), "error": _running.get("error"),
            "fingerprint": _running.get("fingerprint")}

"""
sftp_relay.py
-------------
SFTP-Zugang zu den Client-Dateien - die Alternative zu FTP, mit denselben
Inhalten wie das WebDAV-Relay.

Warum das jetzt doch geht
-------------------------
Bisher hieß es: SFTP passt nicht auf denselben Port. Das stimmt, solange FTP
dort ebenfalls läuft. Beide sind "server-first": Nach dem Verbindungsaufbau
spricht der Server zuerst, der Client wartet still. Zwei solche Protokolle
lassen sich am Anfang einer Verbindung nicht auseinanderhalten.

Mit einem Schalter, der genau EINES von beiden zulässt, verschwindet das
Problem: Die Weiche (front_door.py) weiß dann, wen sie bei Stille rufen muss.
Deshalb ist die Einstellung bewusst eine Auswahl (aus / FTP / SFTP) und kein
Paar von Häkchen - so kann der Fall "beide gleichzeitig" gar nicht entstehen.

Technik
-------
SFTP ist ein Unterkanal von SSH. Den SSH-Teil übernimmt `paramiko`; darauf
setzt hier eine kleine Schicht auf, die Dateizugriffe an den Agenten
weiterreicht - dieselben Aufrufe wie WebDAV und FTP.

Fehlt paramiko, lässt sich SFTP nicht einschalten; die Oberfläche sagt dann,
was zu tun ist. FTP bleibt davon unberührt.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import stat
import threading
import time

from app import db

HOST_KEY_SETTING = "relay_sftp_host_key"


def available() -> tuple[bool, str]:
    """Ist paramiko da? Sonst Grund zurückgeben."""
    try:
        import paramiko  # noqa: F401
        return True, ""
    except ImportError:
        return False, ("Das Paket 'paramiko' fehlt. Es wird für den SSH-Teil "
                       "von SFTP gebraucht: pip install paramiko")


def _host_key():
    """
    Der Serverschlüssel, an dem Clients den Server wiedererkennen.

    Er wird einmal erzeugt und in den Einstellungen abgelegt. Das ist wichtig:
    Ein bei jedem Start neuer Schlüssel würde bei allen Clients die Warnung
    "REMOTE HOST IDENTIFICATION HAS CHANGED" auslösen - und genau diese Warnung
    soll etwas bedeuten.
    """
    import paramiko
    stored = db.get_setting(HOST_KEY_SETTING)
    if stored:
        try:
            return paramiko.RSAKey(file_obj=io.StringIO(stored))
        except Exception:
            pass          # unbrauchbar -> neu erzeugen
    key = paramiko.RSAKey.generate(3072)
    buf = io.StringIO()
    key.write_private_key(buf)
    db.set_setting(HOST_KEY_SETTING, buf.getvalue())
    return key


# ==========================================================================
# Zugriff auf die Client-Dateien (wie bei FTP, nur andere Verpackung)
# ==========================================================================

class _Files:
    """
    Übersetzt SFTP-Pfade in Agenten-Aufrufe.

    Läuft in einem eigenen Thread (paramiko ist synchron), die Agenten-Aufrufe
    sind aber asynchron. Deshalb werden sie über die Ereignisschleife des
    Backends eingeplant und das Ergebnis abgewartet.
    """

    def __init__(self, user, loop: asyncio.AbstractEventLoop):
        self.user = user
        self.loop = loop

    def _run(self, coro, timeout: float = 30.0):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout)

    # ---- Server-eigene Ordner (Storage / Deployment) -------------------
    def _server(self, vpath: str):
        """(sektion, restpfad) oder (None, None), wenn es ein Client-Pfad ist."""
        from app import relay_storage as _rs
        parts = [p for p in vpath.split("/") if p]
        if not parts:
            return None, None
        section = _rs.section_of(parts[0])
        return (section, parts[1:]) if section else (None, None)

    def _need_write(self, section: str) -> None:
        from app import relay_storage as _rs
        if not _rs.may_write(self.user, section):
            raise PermissionError(f"Schreiben in '{section}' nicht erlaubt")

    def listdir(self, vpath: str) -> list[dict]:
        from app.routers import relay_routes as rr
        from app.sockets import request_fs_list
        from app import relay_storage as _rs

        parts = [p for p in vpath.split("/") if p]
        if not parts:
            names = rr._client_display_names(rr._relay_clients(self.user))
            return ([{"name": n, "is_dir": True, "size": 0, "mtime": 0}
                     for n in _rs.display_names()]
                    + [{"name": n, "is_dir": True, "size": 0, "mtime": 0}
                       for n in sorted(names)])

        section, sub = self._server(vpath)
        if section:
            if not _rs.may_read(self.user):
                raise PermissionError("Kein Zugriff auf das Relay")
            return _rs.listdir(section, sub)

        client_id, err = rr._resolve_client_segment(parts[0], self.user)
        if not client_id:
            raise PermissionError(err or "Unbekannter Client")
        real, drives = self._run(rr._resolve_real_path(client_id, parts[1:]))
        if real is None:
            return [{"name": d.get("name") or d.get("letter", "C"), "is_dir": True,
                     "size": 0, "mtime": 0} for d in (drives or [])]
        raw = self._run(request_fs_list(client_id, real))
        return [{"name": e.get("name", ""),
                 "is_dir": bool(e.get("is_dir") or e.get("dir")),
                 "size": int(e.get("size") or 0),
                 "mtime": int(e.get("mtime") or 0)} for e in raw]

    def real(self, vpath: str) -> tuple[str, str]:
        from app.routers import relay_routes as rr
        parts = [p for p in vpath.split("/") if p]
        if not parts:
            raise PermissionError("Auf dieser Ebene nicht möglich")
        client_id, err = rr._resolve_client_segment(parts[0], self.user)
        if not client_id:
            raise PermissionError(err or "Unbekannter Client")
        real, _ = self._run(rr._resolve_real_path(client_id, parts[1:]))
        if real is None:
            raise PermissionError("Bitte zuerst ein Laufwerk wählen")
        return client_id, real

    def read(self, vpath: str) -> bytes:
        from app.sockets import request_fs_read
        section, sub = self._server(vpath)
        if section:
            from app import relay_storage as _rs
            return _rs.read(section, sub)
        client_id, real = self.real(vpath)
        res = self._run(request_fs_read(client_id, real), timeout=120)
        data = res.get("data") or ""
        return base64.b64decode(data) if isinstance(data, str) else bytes(data)

    def write(self, vpath: str, payload: bytes) -> None:
        from app.sockets import request_fs_op
        section, sub = self._server(vpath)
        if section:
            from app import relay_storage as _rs
            self._need_write(section)
            _rs.write(section, sub, payload)
            return
        client_id, real = self.real(vpath)
        self._run(request_fs_op(client_id, "fs_write",
                                {"path": real,
                                 "data": base64.b64encode(payload).decode()}),
                  timeout=120)

    def op(self, event: str, vpath: str, extra: dict | None = None) -> None:
        from app.sockets import request_fs_op
        section, sub = self._server(vpath)
        if section:
            from app import relay_storage as _rs
            self._need_write(section)
            if event == "fs_mkdir":
                _rs.mkdir(section, sub)
            elif event == "fs_delete":
                _rs.delete(section, sub)
            elif event == "fs_move":
                dst_section, dst_sub = self._server((extra or {}).get("dst", ""))
                if dst_section != section:
                    raise PermissionError("Nur innerhalb desselben Ordners möglich")
                _rs.move(section, sub, dst_sub)
            return
        client_id, real = self.real(vpath)
        data = {"path": real}
        data.update(extra or {})
        self._run(request_fs_op(client_id, event, data))


def _attrs(entry: dict):
    """Einen Verzeichniseintrag in die Form bringen, die SFTP erwartet."""
    import paramiko
    a = paramiko.SFTPAttributes()
    a.filename = entry["name"]
    a.st_size = 0 if entry["is_dir"] else entry["size"]
    ts = entry.get("mtime") or 0
    a.st_mtime = int(ts / 1000 if ts > 10_000_000_000 else ts)
    a.st_mode = (stat.S_IFDIR | 0o755) if entry["is_dir"] else (stat.S_IFREG | 0o644)
    return a


def _build_classes():
    """
    Die paramiko-Klassen erst bei Bedarf bauen - so lässt sich dieses Modul
    auch importieren, wenn paramiko gar nicht installiert ist.
    """
    import paramiko

    class Server(paramiko.ServerInterface):
        def __init__(self):
            self.user = None

        def check_auth_password(self, username, password):
            from app.ftp_relay import _authenticate
            from app.auth import user_has_permission
            user = _authenticate(username, password)
            if not user:
                db.add_audit_entry(username, "relay.sftp.login_failed")
                time.sleep(1.0)
                return paramiko.AUTH_FAILED
            if not user_has_permission(user, "use_relay"):
                return paramiko.AUTH_FAILED
            self.user = user
            db.add_audit_entry(user["username"], "relay.sftp.login")
            return paramiko.AUTH_SUCCESSFUL

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return (paramiko.OPEN_SUCCEEDED if kind == "session"
                    else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)

    class Handle(paramiko.SFTPHandle):
        """Eine geöffnete Datei. Gelesen und geschrieben wird immer ganz."""

        def __init__(self, files: _Files, path: str, writing: bool):
            super().__init__()
            self.files = files
            self.path = path
            self.writing = writing
            self.buf = bytearray() if writing else files.read(path)

        def read(self, offset, length):
            return bytes(self.buf[offset:offset + length])

        def write(self, offset, data):
            if len(self.buf) < offset:
                self.buf.extend(b"\0" * (offset - len(self.buf)))
            self.buf[offset:offset + len(data)] = data
            return paramiko.SFTP_OK

        def close(self):
            if self.writing:
                try:
                    self.files.write(self.path, bytes(self.buf))
                except Exception:
                    pass
            return paramiko.SFTP_OK

    class SFTP(paramiko.SFTPServerInterface):
        def __init__(self, server, *args, **kwargs):
            super().__init__(server, *args, **kwargs)
            self.files = _Files(server.user, kwargs.get("loop") or _LOOP[0])

        # --- Hilfsfunktion: Fehler in SFTP-Codes uebersetzen ---
        def _fail(self, e):
            if isinstance(e, PermissionError):
                return paramiko.SFTP_PERMISSION_DENIED
            return paramiko.SFTP_FAILURE

        def list_folder(self, path):
            try:
                return [_attrs(e) for e in self.files.listdir(path)]
            except Exception as e:
                return self._fail(e)

        def stat(self, path):
            try:
                parts = [p for p in path.split("/") if p]
                if not parts:
                    return _attrs({"name": "/", "is_dir": True, "size": 0, "mtime": 0})
                parent = "/" + "/".join(parts[:-1])
                for e in self.files.listdir(parent):
                    if e["name"] == parts[-1]:
                        return _attrs(e)
                return paramiko.SFTP_NO_SUCH_FILE
            except Exception as e:
                return self._fail(e)

        lstat = stat

        def open(self, path, flags, attr):
            try:
                writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
                return Handle(self.files, path, writing)
            except Exception as e:
                return self._fail(e)

        def remove(self, path):
            try:
                self.files.op("fs_delete", path)
                return paramiko.SFTP_OK
            except Exception as e:
                return self._fail(e)

        def rmdir(self, path):
            return self.remove(path)

        def mkdir(self, path, attr):
            try:
                self.files.op("fs_mkdir", path)
                return paramiko.SFTP_OK
            except Exception as e:
                return self._fail(e)

        def rename(self, oldpath, newpath):
            try:
                # Server-Ordner (Storage/Deployment) gehen über die lokale
                # Ablage, nicht über den Agenten.
                section, _ = self.files._server(oldpath)
                if section:
                    self.files.op("fs_move", oldpath, {"dst": newpath})
                    return paramiko.SFTP_OK
                client_id, src = self.files.real(oldpath)
                _, dst = self.files.real(newpath)
                from app.sockets import request_fs_op
                self.files._run(request_fs_op(client_id, "fs_move",
                                              {"src": src, "dst": dst}))
                return paramiko.SFTP_OK
            except Exception as e:
                return self._fail(e)

    return Server, SFTP


# Ereignisschleife des Backends - die Threads brauchen sie für die Agenten-Aufrufe.
_LOOP: list = [None]


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    _LOOP[0] = loop


# ==========================================================================
# Einstieg für die Weiche
# ==========================================================================

def handle_socket(sock) -> None:
    """
    Bedient eine SFTP-Verbindung auf einem bereits angenommenen Socket.
    Läuft in einem eigenen Thread, weil paramiko blockierend arbeitet.
    """
    import paramiko
    Server, SFTP = _build_classes()

    transport = paramiko.Transport(sock)
    try:
        transport.add_server_key(_host_key())
        transport.set_subsystem_handler("sftp", paramiko.SFTPServer, SFTP)
        server = Server()
        transport.start_server(server=server)
        chan = transport.accept(30)
        if chan is None:
            return
        # Solange die Verbindung steht, arbeitet paramiko im Hintergrund.
        while transport.is_active():
            time.sleep(1)
    except Exception as e:
        print(f"[sftp] Verbindung beendet: {e}")
    finally:
        try:
            transport.close()
        except Exception:
            pass


def serve_in_thread(sock) -> None:
    threading.Thread(target=handle_socket, args=(sock,), daemon=True).start()

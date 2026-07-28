"""
ftp_relay.py
------------
FTP-Zugang zu den Client-Dateien - dieselben Inhalte wie das WebDAV-Relay, nur
über FTP. Nützlich für Werkzeuge, die kein WebDAV sprechen (FileZilla, WinSCP,
Total Commander, Skripte mit `curl ftp://…`).

Warum nicht zusätzlich SFTP?
---------------------------
SFTP ist kein eigenes Protokoll, sondern ein Unterkanal von SSH: Der Server
müsste einen kompletten SSH-Handshake mit Schlüsselaustausch führen. Auf
demselben Port wie das Dashboard geht das nicht, denn dort spricht bei HTTP der
CLIENT zuerst, bei SSH und FTP dagegen der SERVER. Ein Port kann daher HTTP plus
GENAU EIN server-first-Protokoll bedienen - beim Verbindungsaufbau ließen sich
FTP und SSH sonst nicht auseinanderhalten. Deshalb: FTP auf Port 4000, SFTP
bleibt außen vor (siehe Einstellungen → Allgemein → Relay).

Wie FTP und HTTP sich einen Port teilen
---------------------------------------
Vor dem Dashboard sitzt eine kleine Weiche (`front_door.py`): Sie nimmt die
Verbindung an und wartet kurz auf Daten vom Client.
  * Es kommen Daten  -> HTTP. Die Verbindung wird an das Dashboard durchgereicht.
  * Es kommt nichts  -> FTP. Der Client wartet auf den 220-Gruß des Servers.

Umfang
------
Umgesetzt sind die Befehle, die gängige FTP-Programme wirklich benutzen:
USER, PASS, SYST, FEAT, PWD, CWD, CDUP, TYPE, PASV, EPSV, LIST, NLST, RETR,
STOR, DELE, MKD, RMD, SIZE, MDTM, RNFR/RNTO, NOOP, QUIT.
Aktiv-Modus (PORT) ist bewusst NICHT dabei: Dabei müsste der Server eine
Verbindung zum Client aufbauen, was hinter Firewalls fast nie funktioniert und
zusätzliche Angriffsfläche schafft. Alle Programme können passiv.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from app import db
from app.auth import verify_password, authenticate_realm
from app.sockets import request_fs_list, request_fs_read, request_fs_op

ENCODING = "utf-8"

# ==========================================================================
# Passiv-Datenverbindungen OHNE eigenen Portbereich
# --------------------------------------------------------------------------
# FTP braucht fuer jede Uebertragung eine ZWEITE Verbindung - die Steuer-
# verbindung ist waehrenddessen belegt. Klassisch oeffnet der Server dafuer
# einen Port aus einem festen Bereich (frueher hier 40100-40199), der in der
# Firewall freigegeben werden muss.
#
# Das entfaellt jetzt: Die Datenverbindung laeuft ueber DENSELBEN Port wie das
# Dashboard. Moeglich wird das, weil vor dem Port ohnehin eine Weiche sitzt
# (front_door.py). Ablauf:
#
#   1. Der Client schickt PASV/EPSV.
#   2. Die Sitzung "meldet an", dass gleich eine Datenverbindung von genau
#      dieser IP kommt (arm_data) und nennt dem Client den normalen Port.
#   3. Die Weiche sieht eine neue Verbindung von einer IP mit Anmeldung und
#      reicht sie als Datenverbindung durch (deliver_data), statt sie als
#      HTTP oder als neue FTP-Sitzung zu behandeln.
#
# Damit muss nur EIN Port offen sein - der, der ohnehin schon offen ist.
#
# Grenze der Methode: Die Zuordnung geht ueber die Absender-IP. Oeffnet
# jemand von derselben IP im exakt gleichen Moment eine normale Verbindung,
# koennte sie faelschlich als Datenverbindung gelten. Deshalb prueft die
# Weiche zusaetzlich, ob die ersten Bytes wie eine HTTP-Anfrage aussehen
# (siehe front_door._looks_like_http) - dann gewinnt HTTP. Das Zeitfenster
# ist ausserdem nur wenige Millisekunden gross.
# ==========================================================================

# peer_ip -> Liste wartender Warteschlangen (FIFO, eine je angemeldeter
# Datenverbindung).
_pending_data: dict[str, list] = {}


def arm_data(peer_ip: str) -> asyncio.Queue:
    """Meldet an, dass von dieser IP gleich eine Datenverbindung kommt."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    _pending_data.setdefault(peer_ip or "", []).append(q)
    return q


def disarm_data(peer_ip: str, q) -> None:
    """Anmeldung zuruecknehmen (Uebertragung fertig oder abgebrochen)."""
    lst = _pending_data.get(peer_ip or "")
    if not lst:
        return
    try:
        lst.remove(q)
    except ValueError:
        pass
    if not lst:
        _pending_data.pop(peer_ip or "", None)


def has_pending(peer_ip: str) -> bool:
    """Wartet fuer diese IP gerade eine Datenverbindung?"""
    return bool(_pending_data.get(peer_ip or ""))


def deliver_data(peer_ip: str, reader, writer, first: bytes = b"") -> bool:
    """Eine eingehende Verbindung als Datenverbindung uebergeben."""
    lst = _pending_data.get(peer_ip or "")
    if not lst:
        return False
    q = lst.pop(0)
    if not lst:
        _pending_data.pop(peer_ip or "", None)
    try:
        q.put_nowait((reader, writer, first))
    except Exception:
        return False
    return True


def mode() -> str:
    """
    Welcher Datei-Zugang läuft neben dem Dashboard? "off", "ftp" oder "sftp".

    Bewusst EIN Wert statt zweier Schalter: FTP und SFTP sind beide
    server-first und lassen sich auf einem gemeinsamen Port nicht
    unterscheiden. Ein einzelner Wert macht "beide gleichzeitig" unmöglich -
    das ist keine Einschränkung der Oberfläche, sondern der einzige Weg, der
    technisch aufgeht.
    """
    try:
        value = str(db.get_setting("relay_file_mode") or "").strip().lower()
        if value in ("off", "ftp", "sftp"):
            return value
        # Altbestand: früher gab es nur den An/Aus-Schalter für FTP.
        if str(db.get_setting("relay_ftp_enabled") or "0") in ("1", "true", "True"):
            return "ftp"
    except Exception:
        pass
    return "off"


def enabled() -> bool:
    """True, wenn irgendein Datei-Zugang neben dem Dashboard laufen soll."""
    return mode() in ("ftp", "sftp")


# ==========================================================================
# Anmeldung
# ==========================================================================

def _authenticate(username: str, password: str):
    """
    Prüft die Zugangsdaten des Dashboards. Konten aus einem Verzeichnis
    (AD/LDAP) werden dort geprüft - lokal haben sie kein Passwort.
    """
    from app.routers.relay_routes import _resolve_user
    user = _resolve_user(username)
    if not user:
        return None
    realm = user.get("auth_realm")
    try:
        if realm:
            return user if authenticate_realm(user["username"], password, realm) else None
        if verify_password(password, user.get("password_hash") or ""):
            return user
    except Exception:
        return None
    return None


# ==========================================================================
# Eine FTP-Sitzung
# ==========================================================================

class FTPSession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 host: str, port: int = 0):
        self.reader = reader
        self.writer = writer
        self.host = host              # Adresse, die wir dem Client für PASV nennen
        self.port = int(port or 0)    # Datenport = derselbe Port wie das Dashboard
        self.user = None
        self.pending_user = ""
        self.cwd = "/"                # virtueller Pfad wie im WebDAV: /<client>/...
        self.pasv_queue = None        # wartet auf die angemeldete Datenverbindung
        self.rename_from = None
        self.peer = ""
        self.peer_ip = ""

    # ---------------- Grundlagen ----------------

    async def send(self, line: str) -> None:
        self.writer.write((line + "\r\n").encode(ENCODING, "replace"))
        await self.writer.drain()

    def _log(self, action: str, details: str = "") -> None:
        try:
            db.add_audit_entry(self.user["username"] if self.user else "",
                               f"relay.ftp.{action}", details=details)
        except Exception:
            pass

    # ---------------- Virtuelle Pfade ----------------

    def _abs(self, path: str) -> str:
        """Eingegebenen Pfad auf einen absoluten virtuellen Pfad bringen."""
        path = (path or "").strip().replace("\\", "/")
        if not path:
            return self.cwd
        base = "/" if path.startswith("/") else self.cwd
        parts: list[str] = []
        for seg in (base + "/" + path).split("/"):
            if seg in ("", "."):
                continue
            if seg == "..":
                if parts:
                    parts.pop()          # nie über die Wurzel hinaus
                continue
            parts.append(seg)
        return "/" + "/".join(parts)

    async def _entries(self, vpath: str) -> list[dict]:
        """
        Verzeichnisinhalt eines virtuellen Pfades.

        Die oberste Ebene sind die freigegebenen Clients - genau wie im
        WebDAV-Relay. Darunter kommen die echten Dateien vom Agenten.
        """
        from app.routers import relay_routes as rr

        parts = [p for p in vpath.split("/") if p]
        if not parts:
            names = rr._client_display_names(rr._relay_clients(self.user))
            return [{"name": n, "is_dir": True, "size": 0, "mtime": 0}
                    for n in sorted(names)]

        client_id, err = rr._resolve_client_segment(parts[0], self.user)
        if not client_id:
            raise PermissionError(err or "Unbekannter Client")

        real, drives = await rr._resolve_real_path(client_id, parts[1:])
        if real is None:
            # Windows ohne Laufwerksangabe -> Laufwerke auflisten
            return [{"name": d.get("name") or d.get("letter", "C"), "is_dir": True,
                     "size": 0, "mtime": 0} for d in (drives or [])]
        raw = await request_fs_list(client_id, real)
        out = []
        for e in raw:
            out.append({
                "name": e.get("name", ""),
                "is_dir": bool(e.get("is_dir") or e.get("dir")),
                "size": int(e.get("size") or 0),
                "mtime": int(e.get("mtime") or 0),
            })
        return out

    async def _real(self, vpath: str) -> tuple[str, str]:
        """virtueller Pfad -> (client_id, echter Pfad auf dem Client)."""
        from app.routers import relay_routes as rr
        parts = [p for p in vpath.split("/") if p]
        if not parts:
            raise PermissionError("Auf dieser Ebene nicht möglich")
        client_id, err = rr._resolve_client_segment(parts[0], self.user)
        if not client_id:
            raise PermissionError(err or "Unbekannter Client")
        real, _ = await rr._resolve_real_path(client_id, parts[1:])
        if real is None:
            raise PermissionError("Bitte zuerst ein Laufwerk wählen")
        return client_id, real

    # ---------------- Datenverbindung (nur passiv) ----------------

    async def _open_pasv(self) -> int:
        """
        Passivmodus vorbereiten: KEIN eigener Port mehr, sondern eine Anmeldung
        bei der Weiche. Zurueckgegeben wird der ganz normale Dashboard-Port.
        """
        await self._close_pasv()
        self.pasv_queue = arm_data(self.peer_ip)
        return self.port

    async def _close_pasv(self) -> None:
        if self.pasv_queue is not None:
            disarm_data(self.peer_ip, self.pasv_queue)
            # Eine bereits gelieferte, aber nie benutzte Verbindung schliessen.
            while not self.pasv_queue.empty():
                try:
                    _, w, _ = self.pasv_queue.get_nowait()
                    w.close()
                except Exception:
                    break
            self.pasv_queue = None

    async def _data(self, timeout: float = 30.0):
        """
        Wartet auf die Datenverbindung, die der Client nach PASV/EPSV aufbaut.
        Rueckgabe: (reader, writer, first) - 'first' sind bereits gelesene
        Bytes (bei Uploads schickt der Client sofort los).
        """
        if self.pasv_queue is None:
            raise RuntimeError("Erst PASV/EPSV senden")
        try:
            return await asyncio.wait_for(self.pasv_queue.get(), timeout)
        except asyncio.TimeoutError:
            raise RuntimeError("Datenverbindung kam nicht zustande")

    # ---------------- Hauptschleife ----------------

    async def run(self) -> None:
        try:
            info = self.writer.get_extra_info("peername")
            self.peer = str(info or "")
            if info:
                self.peer_ip = info[0]
        except Exception:
            pass
        await self.send("220 RAPALLE.net RMM Relay (FTP)")
        try:
            while True:
                raw = await self.reader.readline()
                if not raw:
                    break
                line = raw.decode(ENCODING, "replace").strip()
                if not line:
                    continue
                cmd, _, arg = line.partition(" ")
                cmd = cmd.upper()
                # Passwörter nie ins Log
                if cmd not in ("PASS",):
                    pass
                handler = getattr(self, f"cmd_{cmd}", None)
                if not self.user and cmd not in ("USER", "PASS", "QUIT", "FEAT",
                                                 "SYST", "NOOP", "AUTH", "OPTS"):
                    await self.send("530 Bitte zuerst anmelden.")
                    continue
                if not handler:
                    await self.send(f"502 Befehl '{cmd}' wird nicht unterstützt.")
                    continue
                try:
                    if await handler(arg.strip()) is False:
                        break
                except PermissionError as e:
                    await self.send(f"550 {e}")
                except Exception as e:
                    await self.send(f"550 Fehler: {e}")
        finally:
            await self._close_pasv()
            try:
                self.writer.close()
            except Exception:
                pass

    # ---------------- Befehle: Anmeldung ----------------

    async def cmd_USER(self, arg: str):
        self.pending_user = arg
        await self.send("331 Passwort erforderlich.")

    async def cmd_PASS(self, arg: str):
        user = _authenticate(self.pending_user, arg)
        if not user:
            db.add_audit_entry(self.pending_user, "relay.ftp.login_failed",
                               details=self.peer)
            await asyncio.sleep(1.0)     # Erraten unattraktiv machen
            await self.send("530 Anmeldung fehlgeschlagen.")
            return
        # Dasselbe Recht wie beim WebDAV-Relay.
        from app.auth import user_has_permission
        if not user_has_permission(user, "use_relay"):
            await self.send("530 Kein Recht zur Relay-Nutzung.")
            return
        self.user = user
        self._log("login", self.peer)
        await self.send(f"230 Angemeldet als {user['username']}.")

    async def cmd_QUIT(self, arg: str):
        await self.send("221 Auf Wiedersehen.")
        return False

    # ---------------- Befehle: Auskunft ----------------

    async def cmd_SYST(self, arg: str):
        await self.send("215 UNIX Type: L8")

    async def cmd_FEAT(self, arg: str):
        await self.send("211-Unterstützt:")
        for f in ("UTF8", "PASV", "EPSV", "SIZE", "MDTM", "REST STREAM"):
            await self.send(f" {f}")
        await self.send("211 Ende")

    async def cmd_OPTS(self, arg: str):
        await self.send("200 OK" if arg.upper().startswith("UTF8") else "501 Unbekannt")

    async def cmd_NOOP(self, arg: str):
        await self.send("200 OK")

    async def cmd_TYPE(self, arg: str):
        # Wir übertragen immer binär - Textumwandlung würde Dateien beschädigen.
        await self.send("200 Binärmodus.")

    async def cmd_PWD(self, arg: str):
        await self.send(f'257 "{self.cwd}" ist das aktuelle Verzeichnis.')

    async def cmd_CWD(self, arg: str):
        target = self._abs(arg)
        await self._entries(target)      # wirft, wenn es das nicht gibt
        self.cwd = target
        await self.send(f'250 Verzeichnis gewechselt nach "{target}".')

    async def cmd_CDUP(self, arg: str):
        return await self.cmd_CWD("..")

    # ---------------- Befehle: Auflisten ----------------

    async def cmd_PASV(self, arg: str):
        port = await self._open_pasv()
        ip = (self._advertise_ip() or "127.0.0.1").replace(".", ",")
        await self.send(f"227 Passivmodus ({ip},{port >> 8},{port & 255})")

    def _advertise_ip(self) -> str:
        """
        Adresse, die wir dem Client fuer die Datenverbindung nennen.

        Das ist der haeufigste Grund, warum FTP "nicht geht": Genannt wird eine
        Adresse, die der Client gar nicht erreichen kann. Im Container ist das
        die Container-IP (z.B. 172.17.0.2), hinter NAT die interne. Das FTP-
        Programm meldet dann Zeitueberschreitung oder "Verbindung abgelehnt" -
        obwohl Anmeldung und Verzeichniswechsel vorher funktioniert haben.

        Reihenfolge:
          1. Server-Adresse aus den Einstellungen (server_host, sonst der Host
             aus server_url). Das ist genau die Adresse, unter der die Leute
             das RMM aufrufen - und damit die einzige, die sicher passt.
          2. Lokale Seite DIESER Steuerverbindung (stimmt ohne NAT immer).
          3. Der konfigurierte Host, sofern er keine Platzhalter-Adresse ist.
        """
        def _ok(v: str) -> bool:
            v = (v or "").strip()
            return bool(v) and v not in ("0.0.0.0", "::", "*", "127.0.0.1", "localhost")

        try:
            from app import db as _db
            host = (_db.get_setting("server_host") or "").strip()
            if not host:
                url = (_db.get_setting("server_url") or "").strip()
                if url:
                    import urllib.parse as _up
                    host = (_up.urlparse(url).hostname or "").strip()
            # Die klassische 227-Antwort kann nur IPv4 (vier Zahlen).
            if _ok(host) and host.count(".") == 3 and host.replace(".", "").isdigit():
                return host
        except Exception:
            pass

        try:
            sockname = self.writer.get_extra_info("sockname")
            if sockname and _ok(str(sockname[0])):
                return str(sockname[0])
        except Exception:
            pass

        host = (self.host or "").strip()
        if _ok(host):
            return host
        return "127.0.0.1"

    async def cmd_EPSV(self, arg: str):
        port = await self._open_pasv()
        await self.send(f"229 Passivmodus (|||{port}|)")

    def _line(self, e: dict) -> str:
        """Eine Zeile im UNIX-Format - das verstehen alle FTP-Programme."""
        perm = "drwxr-xr-x" if e["is_dir"] else "-rw-r--r--"
        size = 0 if e["is_dir"] else e["size"]
        ts = e.get("mtime") or 0
        try:
            dt = datetime.fromtimestamp(ts / 1000 if ts > 10_000_000_000 else ts,
                                        tz=timezone.utc)
            stamp = dt.strftime("%b %d %H:%M")
        except Exception:
            stamp = "Jan 01 00:00"
        return f"{perm} 1 rmm rmm {size:>12} {stamp} {e['name']}"

    async def _send_data(self, payload: bytes):
        r, w, _first = await self._data()
        try:
            w.write(payload)
            await w.drain()
        finally:
            w.close()
            await self._close_pasv()

    async def cmd_LIST(self, arg: str):
        path = self._abs(arg if arg and not arg.startswith("-") else "")
        entries = await self._entries(path)
        await self.send("150 Verzeichnisliste folgt.")
        body = "\r\n".join(self._line(e) for e in entries)
        await self._send_data((body + "\r\n").encode(ENCODING, "replace") if body else b"")
        await self.send("226 Übertragung abgeschlossen.")

    async def cmd_NLST(self, arg: str):
        path = self._abs(arg if arg and not arg.startswith("-") else "")
        entries = await self._entries(path)
        await self.send("150 Verzeichnisliste folgt.")
        body = "\r\n".join(e["name"] for e in entries)
        await self._send_data((body + "\r\n").encode(ENCODING, "replace") if body else b"")
        await self.send("226 Übertragung abgeschlossen.")

    # ---------------- Befehle: Dateien ----------------

    async def cmd_RETR(self, arg: str):
        client_id, real = await self._real(self._abs(arg))
        await self.send("150 Datei wird gesendet.")
        res = await request_fs_read(client_id, real)
        import base64
        data = res.get("data") or ""
        payload = base64.b64decode(data) if isinstance(data, str) else bytes(data)
        await self._send_data(payload)
        self._log("download", f"{client_id}:{real} ({len(payload)} Bytes)")
        await self.send("226 Übertragung abgeschlossen.")

    async def cmd_STOR(self, arg: str):
        client_id, real = await self._real(self._abs(arg))
        await self.send("150 Bereit für die Daten.")
        r, w, first = await self._data()
        try:
            # 'first' sind Bytes, die die Weiche schon gelesen hatte.
            chunks = [first] if first else []
            while True:
                chunk = await r.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            w.close()
            await self._close_pasv()
        import base64
        payload = b"".join(chunks)
        await request_fs_op(client_id, "fs_write",
                            {"path": real,
                             "data": base64.b64encode(payload).decode()})
        self._log("upload", f"{client_id}:{real} ({len(payload)} Bytes)")
        await self.send("226 Übertragung abgeschlossen.")

    async def cmd_SIZE(self, arg: str):
        vpath = self._abs(arg)
        parent = vpath.rsplit("/", 1)[0] or "/"
        name = vpath.rsplit("/", 1)[-1]
        for e in await self._entries(parent):
            if e["name"] == name and not e["is_dir"]:
                await self.send(f"213 {e['size']}")
                return
        await self.send("550 Datei nicht gefunden.")

    async def cmd_MDTM(self, arg: str):
        vpath = self._abs(arg)
        parent = vpath.rsplit("/", 1)[0] or "/"
        name = vpath.rsplit("/", 1)[-1]
        for e in await self._entries(parent):
            if e["name"] == name:
                ts = e.get("mtime") or 0
                dt = datetime.fromtimestamp(ts / 1000 if ts > 10_000_000_000 else ts,
                                            tz=timezone.utc)
                await self.send("213 " + dt.strftime("%Y%m%d%H%M%S"))
                return
        await self.send("550 Nicht gefunden.")

    async def cmd_DELE(self, arg: str):
        client_id, real = await self._real(self._abs(arg))
        await request_fs_op(client_id, "fs_delete", {"path": real})
        self._log("delete", f"{client_id}:{real}")
        await self.send("250 Gelöscht.")

    async def cmd_MKD(self, arg: str):
        client_id, real = await self._real(self._abs(arg))
        await request_fs_op(client_id, "fs_mkdir", {"path": real})
        await self.send(f'257 "{arg}" erstellt.')

    async def cmd_RMD(self, arg: str):
        client_id, real = await self._real(self._abs(arg))
        await request_fs_op(client_id, "fs_delete", {"path": real})
        await self.send("250 Gelöscht.")

    async def cmd_RNFR(self, arg: str):
        self.rename_from = self._abs(arg)
        await self.send("350 Bereit für das Ziel.")

    async def cmd_RNTO(self, arg: str):
        if not self.rename_from:
            await self.send("503 Erst RNFR senden.")
            return
        client_id, src = await self._real(self.rename_from)
        _, dst = await self._real(self._abs(arg))
        self.rename_from = None
        await request_fs_op(client_id, "fs_move", {"src": src, "dst": dst})
        await self.send("250 Umbenannt.")


# ==========================================================================
# Einstieg für die Weiche
# ==========================================================================

async def handle_connection(reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter,
                            advertise_host: str = "127.0.0.1",
                            advertise_port: int = 0) -> None:
    """Wird von front_door.py aufgerufen, wenn eine FTP-Verbindung erkannt wurde.
    'advertise_port' ist der Port, den wir dem Client fuer die Datenverbindung
    nennen - derselbe, auf dem auch das Dashboard laeuft."""
    session = FTPSession(reader, writer, advertise_host, advertise_port)
    await session.run()

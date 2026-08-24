"""
wg_userspace.py
===============
WireGuard über die Referenzumsetzung `wireguard-go` statt über eigene Krypto.

Warum dieser Umbau
------------------
Die handgeschriebene Umsetzung in `wireguard.py` besteht jeden Selbsttest,
scheitert aber reproduzierbar am Handschlag echter Clients - und der Fehler
liess sich über mehrere Durchgänge nicht finden. Krypto selbst zu schreiben
ist genau die Sorte Aufgabe, bei der man das besser nicht tut: Ein Fehler
zeigt sich nicht als Fehlermeldung, sondern als "geht nicht", und die
Fehlersuche kostet mehr Zeit als der ganze Rest zusammen.

`wireguard-go` ist die Referenzumsetzung der WireGuard-Entwickler selbst.
Sie läuft vollständig im Userspace und braucht KEIN Kernelmodul - wichtig
auf NAS-Systemen mit altem Kernel (WireGuard kam erst mit Linux 5.6).
Gebraucht wird nur ein TUN-Gerät.

Der Aufbau
----------
Zwei Netzwerkschnittstellen, jede mit einer klaren Aufgabe:

    rmm0   gehört wireguard-go. Hier enden die Tunnel der Benutzer.
           Adresse: 10.77.0.1/16 (der Router im virtuellen Netz)

    rmm1   gehört diesem Programm. Alles, was zu einem CLIENT gehört
           (10.77.1.x) oder in ein Netz hinter einem Client, wird
           hierher geleitet. Wir lesen die IP-Pakete und reichen sie
           über die Agenten-Verbindung weiter - genau wie bisher.

Der Kernel routet dazwischen. Die Krypto macht wireguard-go, die
Weiterleitung an die Agenten machen wir. Alles darüber - virtuelles Netz,
DNS, Rechte, Port-Weiterleitung - bleibt unverändert.

Was auf welchem System gebraucht wird
-------------------------------------
  Docker/Linux  wireguard-go + wireguard-tools (`wg`) + /dev/net/tun.
                Der Container braucht NET_ADMIN (hat er bereits) und das
                Gerät /dev/net/tun (siehe docker-compose.yml).
  Windows       Dort übernimmt der offizielle WireGuard-Dienst die Arbeit;
                siehe windows_hint(). Ein automatischer Aufbau ist dort
                noch nicht umgesetzt.

Fehlt etwas davon, bleibt es bei der bisherigen Python-Umsetzung. Diese
Datei erzwingt nichts - sie meldet ehrlich, was fehlt.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import platform
import shutil
import subprocess
import tempfile

from app.errors import Codes, report

IS_WINDOWS = platform.system().lower().startswith("win")

WG_IFACE = os.getenv("RMM_WG_IFACE", "rmm0")
RMM_IFACE = os.getenv("RMM_TUN_IFACE", "rmm1")

TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000


# ----------------------------------------------------------------------
# Voraussetzungen
# ----------------------------------------------------------------------

def requirements() -> dict:
    """
    Was ist da, was fehlt? Ohne irgendetwas zu verändern.

    Wird beim Start protokolliert und in der Oberfläche angezeigt, damit
    man nicht raten muss, warum der schnellere Weg nicht genommen wurde.
    """
    result = {
        "platform": platform.system(),
        "tun": os.path.exists("/dev/net/tun"),
        "wireguard_go": shutil.which("wireguard-go") or "",
        "wg": shutil.which("wg") or "",
        "ip": shutil.which("ip") or "",
        "root": (not IS_WINDOWS) and hasattr(os, "geteuid") and os.geteuid() == 0,
    }
    fehlt = []
    if IS_WINDOWS:
        fehlt.append("Unter Windows ist der automatische Aufbau noch nicht "
                     "umgesetzt - siehe Hinweis in der Oberfläche")
    else:
        if not result["tun"]:
            fehlt.append("/dev/net/tun fehlt (auf dem Wirt 'modprobe tun', "
                         "im Container das Gerät durchreichen)")
        if not result["wireguard_go"]:
            fehlt.append("wireguard-go ist nicht installiert")
        if not result["wg"]:
            fehlt.append("wireguard-tools ('wg') ist nicht installiert")
        if not result["ip"]:
            fehlt.append("iproute2 ('ip') ist nicht installiert")
        if not result["root"]:
            fehlt.append("keine Root-Rechte bzw. NET_ADMIN fehlt")
    result["missing"] = fehlt
    result["usable"] = not fehlt
    return result


def windows_hint() -> str:
    return (
        "Läuft das Backend unter Windows, übernimmt der offizielle "
        "WireGuard-Dienst die Arbeit: Die ausgestellte .conf mit "
        "'wireguard.exe /installtunnelservice <datei>' als Dienst "
        "einrichten. Der automatische Aufbau aus dem Dashboard heraus ist "
        "dort noch nicht umgesetzt - unter Docker/Linux dagegen schon.")


# ----------------------------------------------------------------------
# Hilfsbefehle
# ----------------------------------------------------------------------

def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> {proc.returncode}: "
                           f"{(proc.stderr or proc.stdout).strip()}")
    return proc


def _host_networks() -> list:
    """
    Die Netze, in denen das Backend selbst hängt.

    Sie dürfen NIEMALS in den Tunnel umgeleitet werden. Würde man eine
    Route für das eigene Netz setzen, verlöre der Server seine eigene
    Erreichbarkeit - im Zweifel mitten im Betrieb und ohne Weg zurück.
    """
    nets = []
    try:
        import psutil
        import socket as _s
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if getattr(a, "family", None) != _s.AF_INET:
                    continue
                if not a.address or not getattr(a, "netmask", None):
                    continue
                if a.address.startswith("127."):
                    continue
                try:
                    nets.append(ipaddress.ip_network(
                        f"{a.address}/{a.netmask}", strict=False))
                except ValueError:
                    continue
    except Exception:
        pass
    return nets


def safe_routes(wanted: list[str]) -> tuple[list[str], list[str]]:
    """Trennt Routen in 'darf man setzen' und 'würde den Server abschneiden'."""
    ok, blocked = [], []
    host = _host_networks()
    for entry in wanted:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if any(net.overlaps(h) for h in host):
            blocked.append(entry)
        else:
            ok.append(entry)
    return ok, blocked


# ----------------------------------------------------------------------
# Das eigene TUN-Gerät (Richtung Clients)
# ----------------------------------------------------------------------

class TunDevice:
    """
    Ein TUN-Gerät, aus dem rohe IP-Pakete gelesen werden.

    Bewusst per ioctl statt mit einem Fremdpaket: Es sind zwanzig Zeilen,
    und eine zusätzliche Abhängigkeit für zwanzig Zeilen lohnt nicht.
    """

    def __init__(self, name: str):
        import fcntl
        import struct
        self.name = name
        self.fd = os.open("/dev/net/tun", os.O_RDWR)
        ifr = struct.pack("16sH", name.encode(), IFF_TUN | IFF_NO_PI)
        fcntl.ioctl(self.fd, TUNSETIFF, ifr)
        os.set_blocking(self.fd, False)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


# ----------------------------------------------------------------------
# Der Endpunkt
# ----------------------------------------------------------------------

class UserspaceWireGuard:
    """Betreibt wireguard-go und das eigene TUN-Gerät."""

    def __init__(self, private_key: str, listen_port: int, address: str,
                 on_packet=None, log=print):
        self.private_key = private_key
        self.listen_port = listen_port
        self.address = address          # z.B. "10.77.0.1/16"
        self.on_packet = on_packet      # callable(ip_packet)
        self.log = log
        self.proc: subprocess.Popen | None = None
        self.tun: TunDevice | None = None
        self.running = False
        self.routes: list[str] = []

    # -- Start ---------------------------------------------------------

    async def start(self) -> bool:
        req = requirements()
        if not req["usable"]:
            for m in req["missing"]:
                self.log(f"[wg-go] nicht verfügbar: {m}")
            return False

        try:
            self._start_wireguard_go()
            self._configure_wg()
            self._start_own_tun()
        except Exception as e:
            report(Codes.VPN_ENDPOINT, e, "wireguard-go konnte nicht starten")
            await self.stop()
            return False

        self.running = True
        asyncio.get_event_loop().add_reader(self.tun.fd, self._readable)
        self.log(f"[wg-go] Läuft: {WG_IFACE} (UDP {self.listen_port}), "
                 f"Weiterleitung über {RMM_IFACE}")
        return True

    def _start_wireguard_go(self) -> None:
        env = dict(os.environ)
        # Im Vordergrund bleiben, damit wir den Prozess überwachen können.
        env["WG_PROCESS_FOREGROUND"] = "1"
        self.proc = subprocess.Popen(
            [shutil.which("wireguard-go"), "-f", WG_IFACE],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True)
        # Kurz warten, bis die Schnittstelle da ist.
        import time
        for _ in range(50):
            if self.proc.poll() is not None:
                out = (self.proc.stdout.read() or "")[:400]
                raise RuntimeError(f"wireguard-go beendete sich sofort: {out}")
            probe = subprocess.run(["ip", "link", "show", WG_IFACE],
                                   capture_output=True)
            if probe.returncode == 0:
                return
            time.sleep(0.1)
        raise RuntimeError(f"Schnittstelle {WG_IFACE} erschien nicht")

    def _configure_wg(self) -> None:
        # Der private Schlüssel geht über eine Datei, nicht über die
        # Befehlszeile - dort wäre er für jeden Prozess sichtbar.
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".key") as f:
            f.write(self.private_key + "\n")
            keyfile = f.name
        try:
            os.chmod(keyfile, 0o600)
            _run(["wg", "set", WG_IFACE, "private-key", keyfile,
                  "listen-port", str(self.listen_port)])
        finally:
            try:
                os.remove(keyfile)
            except OSError:
                pass
        _run(["ip", "address", "add", self.address, "dev", WG_IFACE], check=False)
        _run(["ip", "link", "set", "up", "dev", WG_IFACE])

    def _start_own_tun(self) -> None:
        self.tun = TunDevice(RMM_IFACE)
        # Eine Adresse braucht das Gerät nur, damit der Kernel es benutzt.
        _run(["ip", "address", "add", "169.254.77.1/30", "dev", RMM_IFACE],
             check=False)
        _run(["ip", "link", "set", "up", "dev", RMM_IFACE])
        _run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=False)

    # -- Routen --------------------------------------------------------

    def set_routes(self, wanted: list[str]) -> dict:
        """
        Legt fest, welche Ziele über unser TUN-Gerät laufen.

        Netze, in denen der Server selbst hängt, werden ABGELEHNT - eine
        solche Route würde ihm die eigene Erreichbarkeit nehmen.
        """
        ok, blocked = safe_routes(wanted)
        for entry in blocked:
            self.log(f"[wg-go] Route {entry} abgelehnt - der Server hängt "
                     f"selbst in diesem Netz")
        for entry in ok:
            if entry in self.routes:
                continue
            proc = _run(["ip", "route", "replace", entry, "dev", RMM_IFACE],
                        check=False)
            if proc.returncode == 0:
                self.routes.append(entry)
            else:
                self.log(f"[wg-go] Route {entry} nicht setzbar: "
                         f"{proc.stderr.strip()}")
        return {"gesetzt": self.routes, "abgelehnt": blocked}

    # -- Gegenstellen --------------------------------------------------

    def add_peer(self, public_key: str, preshared_key: str | None,
                 allowed_ips: str) -> None:
        args = ["wg", "set", WG_IFACE, "peer", public_key,
                "allowed-ips", allowed_ips]
        keyfile = None
        if preshared_key:
            with tempfile.NamedTemporaryFile("w", delete=False) as f:
                f.write(preshared_key + "\n")
                keyfile = f.name
            os.chmod(keyfile, 0o600)
            args += ["preshared-key", keyfile]
        try:
            _run(args)
        finally:
            if keyfile:
                try:
                    os.remove(keyfile)
                except OSError:
                    pass

    def remove_peer(self, public_key: str) -> None:
        _run(["wg", "set", WG_IFACE, "peer", public_key, "remove"], check=False)

    def peers(self) -> list[dict]:
        """Zustand der Gegenstellen - direkt von wireguard-go."""
        proc = _run(["wg", "show", WG_IFACE, "dump"], check=False)
        if proc.returncode != 0:
            return []
        out = []
        for line in proc.stdout.strip().splitlines()[1:]:   # erste Zeile = wir
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            out.append({
                "public_key": parts[0], "endpoint": parts[2],
                "allowed_ips": parts[3],
                "last_handshake": int(parts[4] or 0),
                "rx_bytes": int(parts[5] or 0), "tx_bytes": int(parts[6] or 0),
            })
        return out

    # -- Datenweg ------------------------------------------------------

    def _readable(self) -> None:
        """Ein Paket liegt auf dem TUN-Gerät - abholen und weiterreichen."""
        try:
            while True:
                try:
                    packet = os.read(self.tun.fd, 65535)
                except BlockingIOError:
                    return
                if not packet:
                    return
                if self.on_packet:
                    self.on_packet(packet)
        except Exception as e:
            report(Codes.VPN_PACKET, e, "Paket vom TUN-Gerät")

    def send(self, packet: bytes) -> bool:
        """Ein IP-Paket zurück Richtung Benutzer schreiben."""
        if not self.tun:
            return False
        try:
            os.write(self.tun.fd, packet)
            return True
        except OSError as e:
            report(Codes.VPN_PACKET, e, "Paket auf das TUN-Gerät schreiben")
            return False

    # -- Ende ----------------------------------------------------------

    async def stop(self) -> None:
        self.running = False
        if self.tun:
            try:
                asyncio.get_event_loop().remove_reader(self.tun.fd)
            except Exception:
                pass
            self.tun.close()
            self.tun = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.routes.clear()

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

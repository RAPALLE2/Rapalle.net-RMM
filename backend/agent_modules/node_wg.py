"""
node_wg.py  -  NODE-MODUL (wird nur auf Nodes nachgeladen)
==========================================================
Richtet ECHTES WireGuard auf der Node ein und verwaltet dort die Tunnel.

Der Unterschied zu allem davor
------------------------------
Bisher endeten die Tunnel im Backend, und die Nutzdaten liefen als
Socket.IO-Ereignisse zur Node weiter. Das Backend sah dabei alles im
Klartext und war bei jedem Paket beteiligt.

Jetzt endet der Tunnel auf der NODE. Der Benutzer verbindet sich direkt
dorthin; das Backend vermittelt nur die Adressen und Schluessel. Damit
laeuft der Verkehr am Backend vorbei - schneller, und niemand in der Mitte.

Auf einer NODE darf dafuer WireGuard installiert werden. Das ist der
ausdrueckliche Unterschied zwischen einem gewoehnlichen Client (dort wird
nichts installiert) und einer Node (Bruueckenkopf, ausdruecklich
aufgewertet, im Dashboard bestaetigt).

Was installiert wird
--------------------
  Linux    Bevorzugt das Kernelmodul (ab Kernel 5.6 enthalten), sonst
           wireguard-go. Dazu wireguard-tools fuer 'wg'.
  Windows  Der offizielle WireGuard-Dienst. Er bringt den Wintun-Treiber
           mit und braucht Administratorrechte.

Erreichbarkeit
--------------
Eine Node hinter NAT hat keinen offenen Port. Damit ein Benutzer sie
trotzdem direkt erreicht, meldet sie ihren UDP-Port beim Backend an
(Signalisierung) und haelt die NAT-Zuordnung durch regelmaessige Pakete
offen. Klappt der direkte Weg nicht - etwa bei symmetrischem NAT -,
laeuft die Verbindung ueber den Relay des Backends. Beides steuert
`node_relay.py`; dieses Modul kuemmert sich nur um WireGuard selbst.
"""

from __future__ import annotations

import ipaddress
import os
import platform
import shutil
import subprocess
import tempfile

IS_WINDOWS = platform.system().lower().startswith("win")

# Name der Schnittstelle auf der Node.
IFACE = "rmmwg0"

# Offizielle Quelle fuer Windows. Ein VPN-Treiber aus zweiter Hand waere
# unverantwortlich.
WG_WINDOWS_URL = "https://download.wireguard.com/windows-client/wireguard-installer.exe"


class Result:
    """Ergebnis eines Schrittes - immer MIT Begruendung."""

    def __init__(self, ok: bool, reason: str = "", **extra):
        self.ok = ok
        self.reason = reason
        self.extra = extra

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, **self.extra}


# ----------------------------------------------------------------------
# Was ist da?
# ----------------------------------------------------------------------

def probe() -> dict:
    """Prueft, ohne etwas zu veraendern, was auf dieser Node vorhanden ist."""
    info = {
        "platform": platform.system(),
        "wg": shutil.which("wg") or "",
        "wg_quick": shutil.which("wg-quick") or "",
        "wireguard_go": shutil.which("wireguard-go") or "",
        "kernel_module": False,
        "windows_service": False,
        "admin": _is_admin(),
        "installed": False,
    }
    if IS_WINDOWS:
        exe = _windows_wireguard_exe()
        info["windows_service"] = bool(exe)
        info["wireguard_exe"] = exe
        info["installed"] = bool(exe)
    else:
        info["kernel_module"] = _kernel_module_available()
        info["installed"] = bool(info["wg"]) and (
            info["kernel_module"] or bool(info["wireguard_go"]))
    return info


def _is_admin() -> bool:
    if IS_WINDOWS:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _kernel_module_available() -> bool:
    """Ist WireGuard im Kernel? Ab Linux 5.6 in der Regel ja."""
    try:
        # 'modinfo' schlaegt fehl, wenn es das Modul nicht gibt - und wenn
        # WireGuard fest eingebaut ist, existiert /sys/module/wireguard.
        if os.path.isdir("/sys/module/wireguard"):
            return True
        proc = subprocess.run(["modprobe", "-n", "wireguard"],
                              capture_output=True, timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


def _windows_wireguard_exe() -> str:
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        path = os.path.join(base, "WireGuard", "wireguard.exe")
        if os.path.isfile(path):
            return path
    return shutil.which("wireguard") or ""


# ----------------------------------------------------------------------
# Installation
# ----------------------------------------------------------------------

def install(download, log=print) -> Result:
    """
    Installiert WireGuard auf dieser Node.

    Wird NUR aufgerufen, wenn im Dashboard ausdruecklich zugestimmt wurde -
    dort steht auch, dass dabei ein Treiber ins System kommt. Von sich aus
    installiert dieses Modul nichts.
    """
    state = probe()
    if state["installed"]:
        return Result(True, "WireGuard war bereits vorhanden", **state)
    if not state["admin"]:
        return Result(False, "Für die Installation fehlen Administrator- bzw. "
                             "Root-Rechte auf diesem Gerät.")

    if IS_WINDOWS:
        return _install_windows(download, log)
    return _install_linux(log)


def _install_windows(download, log) -> Result:
    log("[node-wg] Lade den offiziellen WireGuard-Installer ...")
    try:
        installer = download(WG_WINDOWS_URL)
    except Exception as e:
        return Result(False, f"Download fehlgeschlagen: {e}")
    try:
        # /S = still. Der Installer bringt den Wintun-Treiber mit.
        proc = subprocess.run([installer, "/S"], capture_output=True, timeout=600)
    except Exception as e:
        return Result(False, f"Installer nicht startbar: {e}")
    finally:
        try:
            os.remove(installer)
        except OSError:
            pass
    if proc.returncode != 0:
        return Result(False, f"Installer endete mit Code {proc.returncode}")

    import time
    for _ in range(20):
        if _windows_wireguard_exe():
            return Result(True, "WireGuard installiert", **probe())
        time.sleep(1)
    return Result(False, "Installiert, aber wireguard.exe nicht auffindbar - "
                         "ein Neustart der Node behebt das meist")


def _install_linux(log) -> Result:
    """
    Auf Linux wird bewusst NICHT automatisch installiert.

    Der Grund: Die Paketverwaltung unterscheidet sich je nach
    Distribution, und ein Skript, das ungefragt 'apt install' aufruft,
    kann auf einem Produktivsystem mehr kaputtmachen als es hilft -
    etwa wenn gerade ein anderes Update laeuft. Stattdessen wird genau
    gesagt, was zu tun ist.
    """
    hints = {
        "debian": "apt install wireguard-tools",
        "rhel": "dnf install wireguard-tools",
        "arch": "pacman -S wireguard-tools",
    }
    state = probe()
    if not state["kernel_module"] and not state["wireguard_go"]:
        extra = (" Der Kernel bringt WireGuard nicht mit (erst ab 5.6) – "
                 "zusätzlich 'wireguard-go' installieren.")
    else:
        extra = ""
    return Result(False,
                  "Unter Linux wird nicht automatisch installiert. Bitte auf "
                  "der Node ausführen: " + hints["debian"] + "." + extra,
                  hints=hints, **state)


# ----------------------------------------------------------------------
# Die Schnittstelle
# ----------------------------------------------------------------------

class NodeWireGuard:
    """Verwaltet die WireGuard-Schnittstelle dieser Node."""

    def __init__(self, log=print):
        self.log = log
        self.up = False
        self.private_key = ""
        self.listen_port = 0
        self.address = ""
        self.peers: dict[str, dict] = {}
        self.masquerade: list[str] = []

    # -- Start / Stopp -------------------------------------------------

    def start(self, private_key: str, listen_port: int, address: str,
              routes: list[str] | None = None) -> Result:
        """
        Bringt die Schnittstelle hoch.

        'address' ist die Adresse der Node im Tunnelnetz, 'routes' sind
        die Netze, die sie fuer Benutzer erreichbar machen soll
        (Site-to-Site).
        """
        state = probe()
        if not state["installed"]:
            return Result(False, "WireGuard ist auf dieser Node nicht "
                                 "installiert", **state)
        if not state["admin"]:
            return Result(False, "Ohne Administrator- bzw. Root-Rechte lässt "
                                 "sich keine Netzwerkschnittstelle anlegen")

        self.private_key = private_key
        self.listen_port = int(listen_port)
        self.address = address
        try:
            if IS_WINDOWS:
                self._start_windows(routes or [])
            else:
                self._start_linux(routes or [])
        except Exception as e:
            return Result(False, f"Schnittstelle nicht startbar: {e}")

        self.up = True
        self.log(f"[node-wg] {IFACE} läuft auf UDP {self.listen_port}, "
                 f"Adresse {address}")
        return Result(True, "aktiv", iface=IFACE, port=self.listen_port)

    def _start_linux(self, routes: list[str]) -> None:
        self._run(["ip", "link", "del", IFACE], check=False)
        if _kernel_module_available():
            self._run(["ip", "link", "add", IFACE, "type", "wireguard"])
        else:
            # wireguard-go legt die Schnittstelle selbst an.
            subprocess.Popen([shutil.which("wireguard-go"), IFACE],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            import time
            for _ in range(50):
                if self._run(["ip", "link", "show", IFACE],
                             check=False).returncode == 0:
                    break
                time.sleep(0.1)

        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(self.private_key + "\n")
            keyfile = f.name
        try:
            os.chmod(keyfile, 0o600)
            self._run(["wg", "set", IFACE, "private-key", keyfile,
                       "listen-port", str(self.listen_port)])
        finally:
            try:
                os.remove(keyfile)
            except OSError:
                pass

        self._run(["ip", "address", "add", self.address, "dev", IFACE],
                  check=False)
        self._run(["ip", "link", "set", "up", "dev", IFACE])

        if routes:
            self._enable_masquerade(routes)

    def _start_windows(self, routes: list[str]) -> None:
        """
        Unter Windows uebernimmt der WireGuard-Dienst die Arbeit.

        Er wird ueber eine Konfigurationsdatei eingerichtet - einzelne
        Gegenstellen nachtraeglich zu setzen geht dort nicht so bequem wie
        mit 'wg'. Deshalb wird die Datei bei jeder Aenderung neu
        geschrieben und der Dienst neu geladen.
        """
        self._write_windows_config(routes)
        exe = _windows_wireguard_exe()
        path = self._windows_config_path()
        # Vorhandenen Dienst entfernen, damit die neue Datei sicher greift.
        subprocess.run([exe, "/uninstalltunnelservice", IFACE],
                       capture_output=True, timeout=60)
        proc = subprocess.run([exe, "/installtunnelservice", path],
                              capture_output=True, timeout=120, text=True)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout).strip()[:300])

    def _windows_config_path(self) -> str:
        base = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                            "RapalleRMM")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{IFACE}.conf")

    def _write_windows_config(self, routes: list[str]) -> None:
        lines = ["[Interface]",
                 f"PrivateKey = {self.private_key}",
                 f"Address = {self.address}",
                 f"ListenPort = {self.listen_port}"]
        for pub, peer in self.peers.items():
            lines += ["", "[Peer]", f"PublicKey = {pub}"]
            if peer.get("psk"):
                lines.append(f"PresharedKey = {peer['psk']}")
            lines.append(f"AllowedIPs = {peer['allowed_ips']}")
        with open(self._windows_config_path(), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    # -- Gegenstellen --------------------------------------------------

    def add_peer(self, public_key: str, psk: str | None,
                 allowed_ips: str) -> Result:
        self.peers[public_key] = {"psk": psk or "", "allowed_ips": allowed_ips}
        try:
            if IS_WINDOWS:
                self._start_windows(self.masquerade)
            else:
                args = ["wg", "set", IFACE, "peer", public_key,
                        "allowed-ips", allowed_ips]
                keyfile = None
                if psk:
                    with tempfile.NamedTemporaryFile("w", delete=False) as f:
                        f.write(psk + "\n")
                        keyfile = f.name
                    os.chmod(keyfile, 0o600)
                    args += ["preshared-key", keyfile]
                try:
                    self._run(args)
                finally:
                    if keyfile:
                        try:
                            os.remove(keyfile)
                        except OSError:
                            pass
        except Exception as e:
            return Result(False, str(e))
        return Result(True, "aufgenommen")

    def remove_peer(self, public_key: str) -> None:
        self.peers.pop(public_key, None)
        if IS_WINDOWS:
            try:
                self._start_windows(self.masquerade)
            except Exception:
                pass
        else:
            self._run(["wg", "set", IFACE, "peer", public_key, "remove"],
                      check=False)

    # -- Site-to-Site: NAT ---------------------------------------------

    def _enable_masquerade(self, routes: list[str]) -> None:
        """
        Verkehr aus dem Tunnel bekommt die Adresse der Node als Absender.

        Das ist der uebliche Weg (bei NetBird heisst er "Exit Route"): Der
        Benutzer erreicht alles, was auch die Node erreicht. Die Geraete im
        Netz sehen dabei die Node, nicht ihn.
        Die Alternative - eine echte Adresse im fremden Netz - braucht
        ARP-Antworten auf Ethernet-Ebene und damit Rohsockets bzw. unter
        Windows einen weiteren Treiber. Sie ist bewusst nicht umgesetzt.
        """
        self.masquerade = list(routes)
        self._run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=False)
        out_if = _default_interface()
        for net in routes:
            try:
                ipaddress.ip_network(net, strict=False)
            except ValueError:
                continue
            # Erst loeschen, dann setzen - so entstehen bei mehrfachem
            # Start keine doppelten Regeln.
            for action in ("-D", "-A"):
                self._run(["iptables", "-t", "nat", action, "POSTROUTING",
                           "-s", self.address.split("/")[0] + "/32",
                           "-o", out_if, "-j", "MASQUERADE"], check=False)
            self._run(["iptables", "-D", "FORWARD", "-i", IFACE,
                       "-d", net, "-j", "ACCEPT"], check=False)
            self._run(["iptables", "-A", "FORWARD", "-i", IFACE,
                       "-d", net, "-j", "ACCEPT"], check=False)
        self.log(f"[node-wg] NAT aktiv für {', '.join(routes)} über {out_if}")

    # -- Zustand -------------------------------------------------------

    def status(self) -> dict:
        info = {"up": self.up, "iface": IFACE, "port": self.listen_port,
                "address": self.address, "peers": []}
        if not self.up or IS_WINDOWS:
            info["peers"] = [{"public_key": k, **v} for k, v in self.peers.items()]
            return info
        proc = self._run(["wg", "show", IFACE, "dump"], check=False)
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines()[1:]:
                parts = line.split("\t")
                if len(parts) >= 7:
                    info["peers"].append({
                        "public_key": parts[0], "endpoint": parts[2],
                        "allowed_ips": parts[3],
                        "last_handshake": int(parts[4] or 0),
                        "rx_bytes": int(parts[5] or 0),
                        "tx_bytes": int(parts[6] or 0)})
        return info

    def stop(self) -> None:
        if IS_WINDOWS:
            exe = _windows_wireguard_exe()
            if exe:
                subprocess.run([exe, "/uninstalltunnelservice", IFACE],
                               capture_output=True, timeout=60)
        else:
            self._run(["ip", "link", "del", IFACE], check=False)
        self.up = False
        self.peers.clear()

    # -- Hilfe ---------------------------------------------------------

    def _run(self, args, check=True):
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if check and proc.returncode != 0:
            raise RuntimeError(f"{' '.join(args)}: "
                               f"{(proc.stderr or proc.stdout).strip()[:200]}")
        return proc


def _default_interface() -> str:
    try:
        with open("/proc/net/route", encoding="utf-8") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        pass
    return "eth0"

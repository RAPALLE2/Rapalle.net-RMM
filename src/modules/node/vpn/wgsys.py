# wgsys.py (Modul "vpn") - WireGuard auf dem System bedienen
# ---------------------------------------------------------
# EINE Datei fuer alle Stellen, die WireGuard wirklich einrichten: Node-Modul "vpn"
# (Host oder Teilnehmer), das Backend selbst (Host/Teilnehmer, laedt diese Datei per
# Pfad) und - als Vorlage - der Agent (dort kompakt nachgebaut, weil der Agent eine
# einzelne Datei ist). Nur Standardbibliothek.
#
# Linux:   wireguard-tools (wg, wg-quick) + Root. Konfiguration /etc/wireguard/<name>.conf.
#          Laeuft die Schnittstelle schon, werden Aenderungen per "wg syncconf" ohne
#          Unterbrechung uebernommen.
# Windows: WireGuard fuer Windows (wireguard.exe) - Tunnel als Dienst
#          (/installtunnelservice, /uninstalltunnelservice).
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
_NAME_RE = re.compile(r"^[A-Za-z0-9_=+.-]{1,15}$")


def _wg_exe() -> str | None:
    if IS_WINDOWS:
        for p in (os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "WireGuard", "wireguard.exe"),
                  shutil.which("wireguard.exe") or ""):
            if p and os.path.exists(p):
                return p
        return None
    return shutil.which("wg-quick")


def available() -> dict:
    exe = _wg_exe()
    root = IS_WINDOWS or (hasattr(os, "geteuid") and os.geteuid() == 0)
    hint = ""
    if not exe:
        hint = ("WireGuard für Windows installieren (https://www.wireguard.com/install/)" if IS_WINDOWS
                else "Paket „wireguard-tools“ installieren (apt install wireguard-tools)")
    elif not root:
        hint = "Root-Rechte nötig"
    return {"ok": bool(exe and root), "exe": exe or "", "hint": hint, "os": platform.system()}


def _run(cmd: list[str], timeout: float = 60, input_text: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input_text)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError as e:
        return 127, str(e)
    except subprocess.TimeoutExpired:
        return 124, "Zeitüberschreitung"


def _check(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError("Ungültiger Tunnelname")
    return name


def _conf_dir() -> Path:
    if IS_WINDOWS:
        d = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Rapalle" / "wireguard"
    else:
        d = Path("/etc/wireguard")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iface_exists(name: str) -> bool:
    if IS_WINDOWS:
        code, out = _run(["sc", "query", f"WireGuardTunnel${name}"], timeout=15)
        return code == 0 and "RUNNING" in out
    return Path(f"/sys/class/net/{name}").exists()


def _warnings(conf: str, out: str = "") -> list[str]:
    """Was nach dem Hochfahren fehlt (Tunnel laeuft trotzdem)."""
    # nur echte Meldungen - nicht die von wg-quick wiederholten Befehlszeilen ("[#] ...")
    w = [ln.split("RMM-VPN Warnung:", 1)[1].strip() for ln in out.splitlines()
         if "RMM-VPN Warnung:" in ln and not ln.lstrip().startswith("[#]")]
    w = [x for x in w if "iptables fehlt" not in x or "MASQUERADE" in x]
    if any("ip_forward" in x for x in w):
        w = [x for x in w if "ip_forward" not in x]          # ersetzt durch den ausfuehrlichen Hinweis unten
    if not IS_WINDOWS:
        try:
            if Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() != "1":
                w.append("IP-Weiterleitung (net.ipv4.ip_forward) ist aus – Teilnehmer erreichen nur den Host, "
                         "nicht einander bzw. das Netz dahinter. Docker: in docker-compose.yml unter "
                         "„sysctls: - net.ipv4.ip_forward=1“ setzen und den Container neu erstellen.")
        except OSError:
            pass
        if "MASQUERADE" in conf and not shutil.which("iptables"):
            w.append("iptables fehlt – ohne NAT-Regel erreichen Teilnehmer das Netz hinter dem Host nicht. "
                     "In der VPN-App „Werkzeuge installieren“ ausführen oder das Einrichtungsskript benutzen.")
    if any("ohne NAT-Regel" in x for x in w):
        w = [x for x in w if not x.startswith("iptables fehlt – Regel nicht gesetzt")]
    return list(dict.fromkeys(w))


def up(name: str, conf: str) -> dict:
    """Tunnel anlegen/aktualisieren und verbinden."""
    name = _check(name)
    av = available()
    if not av["ok"]:
        raise RuntimeError(f"WireGuard nicht verfügbar: {av['hint']}")
    path = _conf_dir() / f"{name}.conf"
    running = _iface_exists(name)
    path.write_text(conf, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if IS_WINDOWS:
        if running:
            _run([av["exe"], "/uninstalltunnelservice", name], timeout=60)
        code, out = _run([av["exe"], "/installtunnelservice", str(path)], timeout=90)
        if code != 0:
            raise RuntimeError(out.strip()[-400:] or f"wireguard.exe endete mit {code}")
        return {"ok": True, "changed": "installed"}
    if running:
        # ohne Unterbrechung uebernehmen (Peers/Schluessel); Adressen/Regeln bleiben
        code, stripped = _run(["wg-quick", "strip", name], timeout=30)
        if code == 0:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".conf") as f:
                f.write(stripped)
                tmp = f.name
            try:
                code, out = _run(["wg", "syncconf", name, tmp], timeout=30)
            finally:
                os.unlink(tmp)
            if code == 0:
                return {"ok": True, "changed": "synced", "warnings": _warnings(conf)}
        _run(["wg-quick", "down", name], timeout=60)
    code, out = _run(["wg-quick", "up", name], timeout=90)
    if code != 0:
        raise RuntimeError(out.strip()[-600:] or f"wg-quick endete mit {code}")
    return {"ok": True, "changed": "up", "warnings": _warnings(conf, out)}


def down(name: str) -> dict:
    name = _check(name)
    av = available()
    path = _conf_dir() / f"{name}.conf" if av["exe"] else None
    if IS_WINDOWS:
        if av["exe"]:
            _run([av["exe"], "/uninstalltunnelservice", name], timeout=60)
    else:
        if _iface_exists(name):
            _run(["wg-quick", "down", name], timeout=60)
    try:
        if path and path.exists():
            path.unlink()
    except OSError:
        pass
    return {"ok": True}


def status(name: str) -> dict:
    """Peers mit letztem Handshake/Traffic -> {up, peers:{pubkey:{endpoint, handshake, rx, tx}}}"""
    name = _check(name)
    exe = shutil.which("wg") or (os.path.join(os.path.dirname(_wg_exe() or ""), "wg.exe") if IS_WINDOWS else None)
    if not exe or not _iface_exists(name):
        return {"up": False, "peers": {}}
    code, out = _run([exe, "show", name, "dump"], timeout=15)
    peers = {}
    if code == 0:
        for i, line in enumerate(out.strip().splitlines()):
            parts = line.split("\t")
            if i == 0 or len(parts) < 8:
                continue
            peers[parts[0]] = {"endpoint": parts[2] if parts[2] != "(none)" else "",
                               "handshake": int(parts[4] or 0), "rx": int(parts[5] or 0), "tx": int(parts[6] or 0)}
    return {"up": True, "peers": peers}


def list_rmm() -> list[str]:
    """Vom RMM angelegte Tunnel (Name beginnt mit 'rmm')."""
    try:
        return sorted(p.stem for p in _conf_dir().glob("rmm*.conf"))
    except OSError:
        return []

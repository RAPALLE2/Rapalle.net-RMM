# features.py - Plattform-Funktionen: Relay als Laufwerk, Fernbild per RDP/VNC
# ----------------------------------------------------------------------------
# Die Befehle werden von REINEN Funktionen gebaut (build_*), damit sie sich ohne
# Windows/Android testen lassen (tests/test_logic.py). Ausgefuehrt wird mit
# subprocess OHNE Shell (keine Probleme mit Leer-/Sonderzeichen).
#
# Relay als Laufwerk (WebDAV, Anmeldung mit Benutzername + GERAETETOKEN):
#   Windows  net use <X>: \\host@SSL@port\dav  -> erscheint im Explorer
#   Linux    gio mount davs://user@host:port/dav/ -> erscheint im Dateimanager (GVFS)
# Fernbild:
#   Windows  mstsc (.rdp-Datei) / VNC-Viewer (TightVNC, RealVNC, UltraVNC, TigerVNC)
#   Linux    xfreerdp3/xfreerdp/remmina  bzw. vncviewer/remmina
#   (Android: eigene App unter src/client/android)

from __future__ import annotations

import os
import platform
import shutil
import string
import subprocess
import tempfile
import urllib.parse
from pathlib import Path



def system() -> str:
    return {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(platform.system(), "other")


class FeatureError(Exception):
    pass


# ============================================================== Relay-Laufwerk
def server_parts(server: str) -> tuple[str, str, int]:
    p = urllib.parse.urlparse(server)
    scheme = p.scheme or "https"
    port = p.port or (443 if scheme == "https" else 80)
    return scheme, p.hostname or "", port


def build_windows_unc(server: str) -> str:
    """https://rmm.firma.de:8443 -> \\\\rmm.firma.de@SSL@8443\\dav"""
    scheme, host, port = server_parts(server)
    ssl = scheme == "https"
    default = 443 if ssl else 80
    unc = "\\\\" + host + ("@SSL" if ssl else "") + (f"@{port}" if port != default else "") + "\\dav"
    return unc


def build_windows_mount(server: str, letter: str, username: str, token: str) -> list[str]:
    return ["net", "use", f"{letter}:", build_windows_unc(server), token, f"/user:{username}", "/persistent:yes"]


def build_windows_unmount(letter: str) -> list[str]:
    return ["net", "use", f"{letter}:", "/delete", "/y"]


def build_linux_uri(server: str, username: str) -> str:
    scheme, host, port = server_parts(server)
    dav = "davs" if scheme == "https" else "dav"
    default = 443 if scheme == "https" else 80
    user = urllib.parse.quote(username, safe="")
    return f"{dav}://{user}@{host}" + (f":{port}" if port != default else "") + "/dav/"


def free_drive_letter(used: set[str] | None = None) -> str:
    """Freien Laufwerksbuchstaben von Z abwaerts suchen."""
    if used is None:
        used = {c for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")}
    for c in reversed(string.ascii_uppercase[3:]):   # Z..D
        if c not in used:
            return c
    raise FeatureError("Kein freier Laufwerksbuchstabe")


_current: subprocess.Popen | None = None      # laufender Befehl (fuer den Stopp-Knopf)


def cancel_current() -> None:
    """Laufenden Befehl abbrechen (Stopp-Knopf im Kontrollfenster)."""
    p = _current
    if p is not None and p.poll() is None:
        try:
            p.kill()
        except Exception:
            pass


def _run(cmd: list[str], input_text: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    global _current
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if input_text is not None else None, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, **kw)
    _current = p
    try:
        out, err = p.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    finally:
        _current = None
    if p.returncode is not None and p.returncode < 0:
        raise FeatureError("Abgebrochen")
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def mount_relay(server: str, username: str, token: str, store) -> str:
    """Verbindet das Relay als Laufwerk. -> Meldung fuer den Benutzer."""
    sysname = system()
    if sysname == "windows":
        letter = store.get("drive_letter") or free_drive_letter()
        scheme, _, _ = server_parts(server)
        r = _run(build_windows_mount(server, letter, username, token))
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).strip()
            hint = ""
            if scheme == "http":
                hint = ("\n\nHinweis: Windows erlaubt WebDAV-Anmeldungen ohne HTTPS standardmäßig nicht "
                        "(Registry: HKLM\\SYSTEM\\CurrentControlSet\\Services\\WebClient\\Parameters\\BasicAuthLevel = 2). "
                        "Besser: den Server per HTTPS erreichbar machen.")
            raise FeatureError(f"Laufwerk konnte nicht verbunden werden:\n{msg}{hint}")
        store.set(drive_letter=letter)
        try:
            os.startfile(f"{letter}:\\")   # type: ignore[attr-defined]
        except Exception:
            pass
        return f"Das Relay ist jetzt Laufwerk {letter}: im Explorer."
    if sysname == "linux":
        if not shutil.which("gio"):
            raise FeatureError("„gio“ fehlt (Paket glib2/libglib2.0-bin). Damit wird das Laufwerk im Dateimanager eingebunden.")
        uri = build_linux_uri(server, username)
        # gio fragt je nach Version nur das Passwort oder Benutzer + Passwort ab.
        for answer in (f"{token}\n", f"{username}\n{token}\n", f"{username}\n\n{token}\n"):
            r = _run(["gio", "mount", uri], input_text=answer)
            out = (r.stderr or "") + (r.stdout or "")
            if r.returncode == 0 or "already mounted" in out.lower() or "bereits eingeh" in out.lower():
                store.set(linux_mount=uri)
                if shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "Das Relay ist im Dateimanager eingebunden (Seitenleiste / „Andere Orte“)."
        raise FeatureError(f"Einbinden fehlgeschlagen:\n{out.strip()}")
    raise FeatureError("Auf diesem System wird das Laufwerk nicht unterstützt.")


def unmount_relay(store) -> str:
    sysname = system()
    if sysname == "windows":
        letter = store.get("drive_letter")
        if not letter:
            return "Es ist kein Laufwerk verbunden."
        _run(build_windows_unmount(letter))
        store.set(drive_letter="")
        return f"Laufwerk {letter}: getrennt."
    if sysname == "linux":
        uri = store.get("linux_mount")
        if not uri:
            return "Es ist nichts eingebunden."
        _run(["gio", "mount", "-u", uri])
        store.set(linux_mount="")
        return "Relay ausgehängt."
    return "Nichts zu trennen."


# ============================================================== Fernbild
WIN_VNC_VIEWERS = [
    r"C:\Program Files\TightVNC\tvnviewer.exe",
    r"C:\Program Files\RealVNC\VNC Viewer\vncviewer.exe",
    r"C:\Program Files\uvnc bvba\UltraVNC\vncviewer.exe",
    r"C:\Program Files\TigerVNC\vncviewer.exe",
    r"C:\Program Files (x86)\TightVNC\tvnviewer.exe",
    r"C:\Program Files (x86)\uvnc bvba\UltraVNC\vncviewer.exe",
]


def build_rdp_file(host: str, port: int, username: str = "") -> str:
    lines = [f"full address:s:{host}:{port}", "prompt for credentials:i:1", "screen mode id:i:2",
             "authentication level:i:2", "redirectclipboard:i:1"]
    if username:
        lines.append(f"username:s:{username}")
    return "\r\n".join(lines) + "\r\n"


def linux_screen_candidates(protocol: str, host: str, port: int) -> list[list[str]]:
    if protocol == "rdp":
        return [["xfreerdp3", f"/v:{host}:{port}", "/dynamic-resolution"],
                ["xfreerdp", f"/v:{host}:{port}", "/dynamic-resolution"],
                ["remmina", "-c", f"rdp://{host}:{port}"]]
    return [["vncviewer", f"{host}::{port}"],
            ["remmina", "-c", f"vnc://{host}:{port}"],
            ["xdg-open", f"vnc://{host}:{port}"]]


def android_screen_uri(protocol: str, host: str, port: int) -> str:
    if protocol == "rdp":
        # Microsoft Remote Desktop / "Windows App" versteht dieses URI-Schema
        return "rdp://" + urllib.parse.quote(f"full address=s:{host}:{port}", safe="=:")
    return f"vnc://{host}:{port}"


def launch_screen(protocol: str, host: str, port: int) -> str:
    sysname = system()
    if sysname == "windows":
        if protocol == "rdp":
            f = Path(tempfile.gettempdir()) / f"rapalle-{host}-{port}.rdp"
            f.write_text(build_rdp_file(host, port), encoding="utf-8")
            os.startfile(str(f))   # type: ignore[attr-defined]
            return "Remotedesktop wird geöffnet …"
        for exe in WIN_VNC_VIEWERS:
            if os.path.exists(exe):
                subprocess.Popen([exe, f"{host}::{port}"])
                return f"{Path(exe).stem} wird geöffnet …"
        try:
            os.startfile(f"vnc://{host}:{port}")   # type: ignore[attr-defined]
            return "VNC-Viewer wird geöffnet …"
        except OSError:
            raise FeatureError(f"Kein VNC-Viewer gefunden. Bitte z. B. TightVNC Viewer installieren.\nAdresse: {host}::{port}")
    if sysname == "linux":
        for cmd in linux_screen_candidates(protocol, host, port):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"{cmd[0]} wird geöffnet …"
        need = "FreeRDP oder Remmina" if protocol == "rdp" else "einen VNC-Viewer (TigerVNC) oder Remmina"
        raise FeatureError(f"Bitte {need} installieren.\nAdresse: {host}:{port}")
    raise FeatureError(f"Bitte manuell verbinden: {host}:{port}")

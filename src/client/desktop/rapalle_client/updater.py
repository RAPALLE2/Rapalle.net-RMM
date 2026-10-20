# updater.py - Updates der Desktop-App (Windows/Linux), nur Standardbibliothek
# ----------------------------------------------------------------------------
# Quelle: GET <server>/client-dist/info (oeffentlich) - neueste Pakete je Format.
# Die App erkennt selbst, wie sie installiert wurde, und nimmt das passende Paket:
#   exe   Windows, einzelne RapalleClient.exe (z.B. per PowerShell-Befehl installiert)
#         -> neue .exe laden, Hilfsskript tauscht sie nach dem Beenden aus und startet neu
#   msi   Windows, installiert unter "Programme" (.msi)
#         -> neues MSI still installieren (MajorUpgrade), danach neu starten
#   deb   Linux, /opt/rapalle-client (.deb) -> pkexec apt-get install (Admin-Abfrage)
#   rpm   Linux, /opt/rapalle-client (.rpm) -> pkexec dnf/zypper/yum/rpm (Admin-Abfrage)
#   pkg   Linux, /opt/rapalle-client (.pkg.tar.xz, Arch) -> pkexec pacman -U (Admin-Abfrage)
#   tar   Linux, ~/.local/share/rapalle-client (.tar.gz) -> Dateien ersetzen, neu starten
# Downloads lassen sich abbrechen (cancel()).

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

from . import __version__
from .features import FeatureError

_cancel = threading.Event()
EXT = {"exe": ".exe", "msi": ".msi", "deb": ".deb", "rpm": ".rpm", "pkg": ".pkg.tar.xz", "tar": ".tar.gz"}


class Cancelled(Exception):
    pass


def cancel() -> None:
    _cancel.set()


def vtuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in (v or "0").split("."))
    except ValueError:
        return (0,)


def app_dir() -> Path:
    return Path(__file__).resolve().parents[1]


MARKER = ".rapalle-install"      # liegt NUR in installierten Paketen (.deb/.rpm/.pkg/.tar) - Inhalt: deb | rpm | pkg | tar


def install_kind() -> str | None:
    """Wie ist die App installiert? None = aus dem Quellcode gestartet (dann KEIN Auto-Update -
    sonst wuerde ein Update den Quellcode-Ordner ueberschreiben)."""
    if os.name == "nt":
        if not getattr(sys, "frozen", False):
            return None
        exe = str(Path(sys.executable).resolve()).lower()
        pf = [os.environ.get(k, "").lower() for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
        return "msi" if any(p and exe.startswith(p) for p in pf) else "exe"
    try:
        kind = (app_dir() / MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return kind if kind in ("deb", "rpm", "pkg", "tar") else None


def check(api) -> dict:
    """-> {current, latest, newer: bool, kind, file: {...}|None, reason}"""
    kind = install_kind()
    out = {"current": __version__, "latest": None, "newer": False, "kind": kind, "file": None, "reason": ""}
    if kind is None:
        out["reason"] = "Aus dem Quellcode gestartet – Updates bitte über das Paket installieren."
        return out
    info = api.request("GET", "/client-dist/info", auth=False)
    plat = "windows" if os.name == "nt" else "linux"
    cand = [f for f in info.get("files", []) if f.get("platform") == plat and f.get("ext") == EXT[kind]]
    if not cand:
        out["reason"] = "Auf dem Server liegt kein passendes Paket."
        return out
    best = max(cand, key=lambda f: vtuple(f.get("version")))
    out["latest"], out["file"] = best.get("version"), best
    out["newer"] = vtuple(best.get("version")) > vtuple(__version__)
    return out


def download(api, file: dict, progress=None) -> Path:
    """Paket in einen Temp-Ordner laden (abbrechbar)."""
    _cancel.clear()
    dest = Path(tempfile.mkdtemp(prefix="rapalle-update-")) / file["name"]
    req = urllib.request.Request(api.server + file["url"], headers={"User-Agent": f"RapalleClient/{__version__}"})
    with urllib.request.urlopen(req, timeout=60, context=api._ctx()) as r, open(dest, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if _cancel.is_set():
                fh.close()
                shutil.rmtree(dest.parent, ignore_errors=True)
                raise Cancelled()
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done, total)
    return dest


def apply(kind: str, pkg: Path) -> str:
    """Update einspielen. -> "exit" (App muss sich jetzt beenden) | "restart" (App startet sich neu)."""
    if kind == "exe":
        exe = Path(sys.executable).resolve()
        bat = pkg.parent / "update.cmd"
        bat.write_text(
            "@echo off\r\n"
            f":warten\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul && (timeout /t 1 /nobreak >nul & goto warten)\r\n"
            f"move /y \"{pkg}\" \"{exe}\" >nul\r\nstart \"\" \"{exe}\"\r\n(goto) 2>nul & del \"%~f0\"\r\n", encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x00000008 | 0x08000000, close_fds=True)
        return "exit"
    if kind == "msi":
        exe = Path(sys.executable).resolve()
        bat = pkg.parent / "update.cmd"
        bat.write_text("@echo off\r\n"
                       f"msiexec /i \"{pkg}\" /passive /norestart\r\n"
                       f"start \"\" \"{exe}\"\r\n", encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x00000008 | 0x08000000, close_fds=True)
        return "exit"
    if kind == "tar":
        tmp = Path(tempfile.mkdtemp(prefix="rapalle-tar-"))
        with tarfile.open(pkg) as tf:
            for m in tf.getmembers():                 # nur saubere Pfade entpacken
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise FeatureError("Ungültiges Update-Paket")
            tf.extractall(tmp)
        src = next(tmp.glob("rapalle-client-*/app"), None)
        if not src:
            raise FeatureError("Update-Paket unvollständig")
        shutil.copytree(src, app_dir(), dirs_exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        return "restart"
    if kind == "deb":
        if not shutil.which("pkexec"):
            raise FeatureError(f"Bitte das Update als Administrator installieren:\nsudo apt install {pkg}")
        r = subprocess.run(["pkexec", "apt-get", "install", "-y", str(pkg)], capture_output=True, text=True)
        if r.returncode != 0:
            raise FeatureError(f"Installation abgebrochen oder fehlgeschlagen:\n{(r.stderr or r.stdout)[-500:]}")
        return "restart"
    if kind in ("rpm", "pkg"):
        if kind == "pkg":
            cmd, hint = ["pacman", "-U", "--noconfirm", str(pkg)], f"sudo pacman -U {pkg}"
        elif shutil.which("dnf"):
            cmd, hint = ["dnf", "install", "-y", str(pkg)], f"sudo dnf install {pkg}"
        elif shutil.which("zypper"):
            cmd, hint = ["zypper", "--non-interactive", "install", "--allow-unsigned-rpm", str(pkg)], f"sudo zypper install {pkg}"
        elif shutil.which("yum"):
            cmd, hint = ["yum", "install", "-y", str(pkg)], f"sudo yum install {pkg}"
        else:
            cmd, hint = ["rpm", "-U", "--replacepkgs", str(pkg)], f"sudo rpm -U {pkg}"
        if not shutil.which("pkexec"):
            raise FeatureError(f"Bitte das Update als Administrator installieren:\n{hint}")
        r = subprocess.run(["pkexec", *cmd], capture_output=True, text=True)
        if r.returncode != 0:
            raise FeatureError(f"Installation abgebrochen oder fehlgeschlagen:\n{(r.stderr or r.stdout)[-500:]}")
        return "restart"
    raise FeatureError("Unbekannte Installationsart")


def restart() -> None:
    """App neu starten (Linux)."""
    launcher = "/usr/bin/rapalle-client" if install_kind() in ("deb", "rpm", "pkg") else str(app_dir() / "rapalle-client.py")
    args = [launcher] if launcher.startswith("/usr/bin") else [sys.executable, launcher]
    os.execv(args[0], args)

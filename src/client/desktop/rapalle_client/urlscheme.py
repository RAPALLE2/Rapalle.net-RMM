# urlscheme.py - Link-Schema rapalle-rmm:// fuer "aus dem Dashboard extern oeffnen"
# --------------------------------------------------------------------------------
# Das Dashboard (im App-Fenster) oeffnet rapalle-rmm://launch?s=<server>&t=<startschein>;
# das Betriebssystem startet dann diese App mit dem Link als Argument (launcher.py).
#   Windows  HKCU\Software\Classes\rapalle-rmm   (nur dieser Benutzer, ohne Admin)
#   Linux    ~/.local/share/applications/rapalle-client-link.desktop + xdg-mime
# Wird bei jedem Start der App geprueft/erneuert (Pfad kann sich nach Updates aendern).
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SCHEME = "rapalle-rmm"


def _command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    launcher = Path(sys.argv[0]).resolve()
    if os.name == "nt":
        pyw = Path(sys.executable).with_name("pythonw.exe")
        return [str(pyw if pyw.exists() else sys.executable), str(launcher)]
    if launcher.name == "rapalle-client" or os.access(launcher, os.X_OK):
        return [str(launcher)]
    return [sys.executable, str(launcher)]


def register() -> str:
    """-> kurze Statusmeldung (Fehler werden geschluckt - das Schema ist ein Zusatz)."""
    try:
        if os.name == "nt":
            import winreg
            cmd = " ".join(f'"{c}"' for c in _command()) + ' "%1"'
            base = rf"Software\Classes\{SCHEME}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:RAPALLE.net RMM")
                winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
            return "registriert"
        apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
        apps.mkdir(parents=True, exist_ok=True)
        exe = " ".join(f'"{c}"' if " " in c else c for c in _command())
        f = apps / "rapalle-client-link.desktop"
        text = ("[Desktop Entry]\nType=Application\nName=RAPALLE.net RMM (Links)\nNoDisplay=true\n"
                f"Exec={exe} %u\nMimeType=x-scheme-handler/{SCHEME};\nTerminal=false\n")
        if not f.exists() or f.read_text(encoding="utf-8") != text:
            f.write_text(text, encoding="utf-8")
            if shutil.which("update-desktop-database"):
                subprocess.run(["update-desktop-database", str(apps)], capture_output=True, timeout=20)
        if shutil.which("xdg-mime"):
            subprocess.run(["xdg-mime", "default", f.name, f"x-scheme-handler/{SCHEME}"], capture_output=True, timeout=20)
        return "registriert"
    except Exception as e:      # noqa: BLE001
        return f"nicht registriert: {e}"

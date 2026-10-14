# autostart.py - "Mit der Anmeldung am Computer starten" (nur Standardbibliothek)
#   Windows  HKCU\Software\Microsoft\Windows\CurrentVersion\Run  (nur dieser Benutzer)
#   Linux    ~/.config/autostart/rapalle-client.desktop           (XDG, alle gaengigen Desktops)
from __future__ import annotations

import os
import sys
from pathlib import Path

NAME = "RapalleRmmClient"


def _command() -> str:
    if getattr(sys, "frozen", False):                      # PyInstaller-.exe
        return f'"{sys.executable}" --minimized'
    launcher = Path(sys.argv[0]).resolve()
    if os.name == "nt":
        pyw = Path(sys.executable).with_name("pythonw.exe")
        return f'"{pyw if pyw.exists() else sys.executable}" "{launcher}" --minimized'
    return f'"{launcher}" --minimized' if os.access(launcher, os.X_OK) else f'"{sys.executable}" "{launcher}" --minimized'


def _desktop_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "rapalle-client.desktop"


def is_enabled() -> bool:
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, NAME)
                return True
        except OSError:
            return False
    return _desktop_file().exists()


def set_enabled(on: bool) -> None:
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except OSError:
                    pass
        return
    f = _desktop_file()
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("[Desktop Entry]\nType=Application\nName=RAPALLE.net RMM\n"
                     f"Exec={_command()}\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    else:
        f.unlink(missing_ok=True)

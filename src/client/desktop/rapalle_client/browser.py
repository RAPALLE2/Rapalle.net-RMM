# browser.py - das Dashboard als App-Fenster ("kleiner Browser")
# --------------------------------------------------------------
# Kein eingebauter Browser noetig: Windows 10/11 bringt Microsoft Edge immer
# mit; Linux-Desktops haben fast immer Chromium/Chrome/Edge/Brave/Firefox.
# Chromium-Browser starten mit --app=<url> als randloses App-Fenster (ohne
# Adressleiste/Tabs) und einem EIGENEN Profil - die Anmeldung der App ist so
# von deinem normalen Browser getrennt.
# Die Sitzung wird per "#client-token=..." uebergeben (das Dashboard uebernimmt
# sie sofort und entfernt sie aus der Adresse).

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.parse
import webbrowser
from pathlib import Path

WIN_CANDIDATES = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
]
LINUX_CHROMIUM = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "microsoft-edge",
                  "microsoft-edge-stable", "brave-browser", "vivaldi-stable", "vivaldi"]


def find_browser() -> tuple[str, str] | None:
    """-> (Pfad, Art) mit Art = "chromium" | "firefox"; None = nur Standardbrowser."""
    if os.name == "nt":
        for c in WIN_CANDIDATES:
            p = os.path.expandvars(c)
            if os.path.isfile(p):
                return p, "chromium"
        return None
    for name in LINUX_CHROMIUM:
        p = shutil.which(name)
        if p:
            return p, "chromium"
    for name in ("firefox", "firefox-esr"):
        p = shutil.which(name)
        if p:
            return p, "firefox"
    return None


def build_command(browser: tuple[str, str], url: str, profile_dir: Path, size=(1280, 820)) -> list[str]:
    path, kind = browser
    if kind == "chromium":
        return [path, f"--app={url}", f"--user-data-dir={profile_dir}", "--no-first-run",
                "--no-default-browser-check", f"--window-size={size[0]},{size[1]}", "--disable-features=Translate"]
    return [path, "--new-window", url]


def allow_protocol(profile_dir: Path, url: str, scheme: str = "rapalle-rmm") -> None:
    """Im EIGENEN Profil der App erlauben, dass das Dashboard rapalle-rmm:// ohne
    Rueckfrage oeffnet (Chromium: protocol_handler.allowed_origin_protocol_pairs)."""
    try:
        p = urllib.parse.urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        f = profile_dir / "Default" / "Preferences"
        f.parent.mkdir(parents=True, exist_ok=True)
        prefs = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        pairs = prefs.setdefault("protocol_handler", {}).setdefault("allowed_origin_protocol_pairs", {})
        if (pairs.get(origin) or {}).get(scheme) is True:
            return
        pairs.setdefault(origin, {})[scheme] = True
        f.write_text(json.dumps(prefs), encoding="utf-8")
    except Exception:
        pass


def open_app_window(url: str, profile_dir: Path, size=(1280, 820)) -> str:
    """Oeffnet das Fenster. -> Name des Browsers (fuer Meldungen)."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    allow_protocol(profile_dir, url)
    b = find_browser()
    if not b:
        webbrowser.open(url)
        return "Standardbrowser"
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(build_command(b, url, profile_dir, size), stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, **kw)
    return Path(b[0]).stem

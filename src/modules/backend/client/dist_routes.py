# dist_routes.py (Modul "client")
# -------------------------------
# Installation der Client-App - wie beim Agenten auf drei Wegen:
#
#   1) Befehl (Einzeiler, laedt das passende Paket und richtet alles ein):
#        PowerShell  irm <server>/client-dist/install.ps1 | iex
#        CMD         curl -fsSLo %TEMP%\rmmc.cmd <server>/client-dist/install.cmd && %TEMP%\rmmc.cmd
#        Linux       curl -fsSL <server>/client-dist/install.sh | sh
#   2) Paket:  .zip (Windows), .tar / .tar.gz (Linux)
#   3) Datei:  .exe, .msi (Windows), .deb (Linux), .apk (Android)
#
# Alle Wege tragen die Server-Adresse gleich in die App ein (settings.json),
# damit man sich nur noch anmelden muss. Die Dateien enthalten KEINE
# Geheimnisse - deshalb sind diese Adressen ohne Anmeldung erreichbar
# (genau wie /agent-dist beim Agenten).
#
# Woher die Pakete kommen (neuere/gleichnamige Dateien in storage gewinnen):
#   src/client/release/        mitgeliefert, gebaut von src/client/build_installers.py
#   backend/storage/client/    hochgeladen (Modul-Einstellungen) oder selbst gebaut

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from app.paths import STORAGE_DIR

router = APIRouter(tags=["client-dist"])

RELEASE_DIR = Path(__file__).resolve().parents[3] / "client" / "release"
UPLOAD_DIR = STORAGE_DIR / "client"

PLATFORM_EXT = {
    "windows": (".exe", ".msi", ".msix", ".zip"),
    "linux": (".deb", ".tar.gz", ".tgz", ".tar", ".rpm", ".appimage", ".flatpak", ".run"),
    "android": (".apk", ".aab"),
}
KIND = {".exe": "Datei", ".msi": "Datei", ".msix": "Datei", ".deb": "Datei", ".rpm": "Datei", ".appimage": "Datei",
        ".apk": "Datei", ".aab": "Datei", ".zip": "Paket", ".tar": "Paket", ".tar.gz": "Paket", ".tgz": "Paket",
        ".run": "Paket", ".flatpak": "Paket"}


def ext_of(name: str) -> str:
    low = name.lower()
    for e in (".tar.gz",):
        if low.endswith(e):
            return e
    return Path(low).suffix


def platform_of(name: str) -> str | None:
    low = name.lower()
    for plat, exts in PLATFORM_EXT.items():
        if low.endswith(exts):
            if low.endswith(".zip") and "windows" not in low and "win" not in low:
                continue
            return plat
    return None


def files() -> list[dict]:
    """Alle verfuegbaren Pakete (hochgeladene ueberschreiben mitgelieferte)."""
    found: dict[str, Path] = {}
    for d in (RELEASE_DIR, UPLOAD_DIR):
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file() and platform_of(f.name):
                    found[f.name] = f
    import re
    out = []
    for name, f in found.items():
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", name)
        out.append({"name": name, "platform": platform_of(name), "ext": ext_of(name), "kind": KIND.get(ext_of(name), "Datei"),
                    "version": m.group(1) if m else "", "size": f.stat().st_size, "mtime": int(f.stat().st_mtime * 1000),
                    "url": f"/client-dist/files/{name}", "source": "upload" if f.parent == UPLOAD_DIR else "release"})
    # empfohlenes Format zuerst (z.B. Windows: .exe vor .msi vor .zip)
    pref = {p: list(e) for p, e in PLATFORM_EXT.items()}
    rank = lambda x: pref[x["platform"]].index(x["ext"]) if x["ext"] in pref[x["platform"]] else 99   # noqa: E731
    return sorted(out, key=lambda x: (x["platform"], rank(x), -x["mtime"]))


def path_of(name: str) -> Path:
    if "/" in name or "\\" in name or name.startswith(".") or not platform_of(name):
        raise HTTPException(400, "Ungültiger Dateiname")
    for d in (UPLOAD_DIR, RELEASE_DIR):
        p = d / name
        if p.is_file():
            return p
    raise HTTPException(404, "Nicht gefunden")


def newest(plat: str, exts: tuple[str, ...]) -> dict | None:
    for e in exts:
        cand = [f for f in files() if f["platform"] == plat and f["ext"] == e]
        if cand:
            return sorted(cand, key=lambda x: -x["mtime"])[0]
    return None


def _base(request: Request) -> str:
    try:
        from router.enrollment_routes import _backend_url
        return _backend_url(request).rstrip("/")
    except Exception:
        return str(request.base_url).rstrip("/")


@router.get("/client-dist/files/{name}")
def dist_file(name: str):
    p = path_of(name)
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


# ------------------------------------------------------------------ Windows
@router.get("/client-dist/install.ps1", response_class=PlainTextResponse)
def install_ps1(request: Request):
    base = _base(request)
    exe = newest("windows", (".exe",))
    if not exe:
        return PlainTextResponse('Write-Host "Auf dem Server liegt noch kein Windows-Paket (.exe)." -ForegroundColor Red\n')
    settings = json.dumps({"server": base})
    return PlainTextResponse(f"""# RAPALLE.net RMM Client - Installation fuer den aktuellen Benutzer (Windows 10/11)
#   irm {base}/client-dist/install.ps1 | iex
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$dir  = Join-Path $env:LOCALAPPDATA 'Programs\\RapalleClient'
$exe  = Join-Path $dir 'RapalleClient.exe'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Write-Host 'Lade RAPALLE.net RMM Client {exe["version"]} ...'
Get-Process RapalleClient -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Invoke-WebRequest -UseBasicParsing -Uri '{base}{exe["url"]}' -OutFile $exe
# Server-Adresse vorbelegen (nur wenn noch nichts eingerichtet ist)
$cfgDir = Join-Path $env:APPDATA 'RapalleClient'
$cfg = Join-Path $cfgDir 'settings.json'
if (-not (Test-Path $cfg)) {{
  New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
  [IO.File]::WriteAllText($cfg, '{settings}')
}}
# Verknuepfungen: Startmenue + Desktop
$sh = New-Object -ComObject WScript.Shell
foreach ($lnkDir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {{
  $l = $sh.CreateShortcut((Join-Path $lnkDir 'RAPALLE.net RMM.lnk'))
  $l.TargetPath = $exe; $l.WorkingDirectory = $dir; $l.IconLocation = "$exe,0"; $l.Save()
}}
Write-Host 'Installiert. Starte den Client ...' -ForegroundColor Green
Start-Process $exe
""", media_type="text/plain; charset=utf-8")


@router.get("/client-dist/install.cmd", response_class=PlainTextResponse)
def install_cmd(request: Request):
    base = _base(request)
    return PlainTextResponse(
        "@echo off\r\n"
        "rem RAPALLE.net RMM Client - Installation (ruft das PowerShell-Skript auf)\r\n"
        f"powershell -NoProfile -ExecutionPolicy Bypass -Command \"irm '{base}/client-dist/install.ps1' | iex\"\r\n"
        "if errorlevel 1 pause\r\n", media_type="text/plain; charset=utf-8")


# ------------------------------------------------------------------ Linux
@router.get("/client-dist/install.sh", response_class=PlainTextResponse)
def install_sh(request: Request):
    base = _base(request)
    deb = newest("linux", (".deb",))
    tgz = newest("linux", (".tar.gz", ".tar"))
    if not (deb or tgz):
        return PlainTextResponse('echo "Auf dem Server liegt noch kein Linux-Paket."; exit 1\n')
    settings = json.dumps({"server": base})
    return PlainTextResponse(f"""#!/bin/sh
# RAPALLE.net RMM Client - Installation (Linux)
#   curl -fsSL {base}/client-dist/install.sh | sh
# Debian/Ubuntu mit sudo: .deb inkl. Abhaengigkeiten (python3-tk).
# Sonst: .tar.gz fuer den aktuellen Benutzer (~/.local, ohne root).
set -e
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
dl() {{ if command -v curl >/dev/null; then curl -fsSL "$1" -o "$2"; else wget -qO "$2" "$1"; fi; }}
SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"
if [ -n "{deb['url'] if deb else ''}" ] && command -v apt-get >/dev/null && {{ [ "$(id -u)" -eq 0 ] || [ -n "$SUDO" ]; }}; then
  echo "Lade {deb['name'] if deb else ''} ..."
  dl "{base}{deb['url'] if deb else ''}" "$TMP/rapalle-client.deb"
  $SUDO apt-get install -y "$TMP/rapalle-client.deb"
else
  echo "Lade {tgz['name'] if tgz else ''} ..."
  dl "{base}{tgz['url'] if tgz else ''}" "$TMP/client.tar.gz"
  tar xf "$TMP/client.tar.gz" -C "$TMP"
  sh "$TMP"/rapalle-client-*/install.sh
  command -v python3 >/dev/null && python3 -c "import tkinter" 2>/dev/null || \\
    echo "Bitte noch Python-Tk installieren: sudo apt install python3-tk  |  sudo dnf install python3-tkinter"
fi
CFG="${{XDG_CONFIG_HOME:-$HOME/.config}}/rapalle-client"
if [ ! -f "$CFG/settings.json" ]; then mkdir -p "$CFG"; printf '%s' '{settings}' > "$CFG/settings.json"; fi
echo "Fertig. Start über das Anwendungsmenü: „RAPALLE.net RMM“ (oder: rapalle-client)."
""", media_type="text/plain; charset=utf-8")


@router.get("/client-dist/info")
def dist_info(request: Request):
    """Fuer die Profilseite: Befehle + alle Pakete."""
    base = _base(request)
    return {
        "base": base,
        "commands": {
            "powershell": f"irm {base}/client-dist/install.ps1 | iex",
            "cmd": f'curl -fsSLo "%TEMP%\\rmm-client.cmd" {base}/client-dist/install.cmd && "%TEMP%\\rmm-client.cmd"',
            "shell": f"curl -fsSL {base}/client-dist/install.sh | sh",
        },
        "files": files(),
    }

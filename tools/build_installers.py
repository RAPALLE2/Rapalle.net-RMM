#!/usr/bin/env python3
"""
build_installers.py
===================
Baut LOKAL fertige Installationspakete fuer den RMM-Agenten - fuer alle, die
den PowerShell-/Bash-Einzeiler aus dem Dashboard nicht nutzen koennen oder
wollen (Softwareverteilung, GPO, Intune, apt/dnf-Repos, Offline-Rollout).

Erzeugte Pakete (je nach Bausystem, siehe unten):

  Windows   RapalleRmmAgent-Setup.exe   PyInstaller-Onefile, fragt/kennt die
                                        Backend-URL, installiert Python bei
                                        Bedarf, legt den SYSTEM-Autostart-Task
                                        an. Unterstuetzt "/S" fuer still.
            RapalleRmmAgent.msi         Nur wenn WiX (candle/light) da ist -
                                        die MSI kapselt die Setup.exe (still),
                                        damit sie per GPO/Intune verteilbar ist.
  Linux     rapalle-rmm-agent_<v>.deb   Debian/Ubuntu, postinst richtet venv +
                                        systemd-Dienst ein.
            rapalle-rmm-agent-<v>.rpm   Fedora/RHEL/openSUSE (braucht rpmbuild).
            rapalle-rmm-agent.run       Selbstentpackendes Shell-Skript -
                                        laeuft ueberall und laesst sich IMMER
                                        bauen (auch auf Windows).

Alle Pakete richten denselben Autostart ein wie die Web-Installer:
  Windows -> Aufgabenplanung, Principal SYSTEM, Trigger "Beim Systemstart"
             (laeuft also OHNE Anmeldung; Bildschirmaufnahme startet bei Bedarf
             einen Helfer in der aktiven Benutzersitzung)
  Linux   -> systemd-Unit rapalle-agent.service, User=root, multi-user.target

Aufruf
------
    python tools/build_installers.py --backend-url https://rmm.example.com
    python tools/build_installers.py --targets sh,deb --out dist
    python tools/build_installers.py --list

Ohne --backend-url fragt der Installer die Adresse beim Ausfuehren ab
(bzw. nimmt sie aus dem Parameter -BackendUrl / RMM_BACKEND_URL).

Der AGENT_TOKEN wird - wenn vorhanden - automatisch aus backend/.env gelesen,
damit sich die verteilten Agenten sofort anmelden koennen.
"""

from __future__ import annotations

import argparse
import base64
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "agent"
BACKEND_ENV = ROOT / "backend" / ".env"

PKG_NAME = "rapalle-rmm-agent"
WIN_INSTALL_DIR = r"C:\Program Files\RapalleRmmAgent"
LINUX_INSTALL_DIR = "/opt/rapalle-rmm-agent"
TASK_NAME = "RapalleRmmAgent"


# ==========================================================================
# Hilfsfunktionen
# ==========================================================================

def info(msg: str) -> None:
    print(f"[build] {msg}")


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


# --------------------------------------------------------------------------
# Bau-Werkzeuge bei Bedarf automatisch nachruesten
# --------------------------------------------------------------------------
# EXE und MSI brauchen Werkzeuge, die nicht ueberall vorhanden sind:
#   PyInstaller  -> Python-Paket, per pip nachinstallierbar
#   WiX v3       -> kein Python-Paket; wir laden die offiziellen Binaries
#                   (candle.exe/light.exe) einmalig herunter und legen sie im
#                   Cache-Ordner ab. Kein Setup, keine Adminrechte noetig.
# Abschaltbar mit --no-auto-install.

AUTO_INSTALL = True
BUILD_REQUIREMENTS = Path(__file__).resolve().parent / "requirements-build.txt"
CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / ".rmm-build"
# Offizielle WiX-v3-Binaries (enthaelt candle.exe + light.exe, ~10 MB).
WIX_URL = "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip"


def pip_install(spec: str) -> bool:
    """Installiert ein Python-Paket in die LAUFENDE Umgebung."""
    info(f"Installiere Bau-Abhaengigkeit: {spec} ...")
    base = [sys.executable, "-m", "pip", "install", "--quiet", spec]
    for extra in ([], ["--break-system-packages"], ["--user"]):
        if subprocess.run(base + extra).returncode == 0:
            return True
    info(f"pip install {spec} fehlgeschlagen.")
    return False


def ensure_pyinstaller() -> bool:
    """PyInstaller vorhanden? Sonst per pip nachziehen."""
    if have("pyinstaller"):
        return True
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        pass
    if not AUTO_INSTALL:
        info("PyInstaller fehlt (Auto-Installation ist abgeschaltet). "
             "Nachruesten mit: pip install -r tools/requirements-build.txt")
        return False
    if not pip_install("pyinstaller>=6.0"):
        return False
    try:
        import PyInstaller  # noqa: F401
        return True
    except ImportError:
        return have("pyinstaller")


def pyinstaller_cmd() -> list[str]:
    """
    Aufrufweg fuer PyInstaller. Nach "pip install --user" liegt die .exe nicht
    zwingend im PATH - dann klappt der Modul-Aufruf trotzdem.
    """
    if have("pyinstaller"):
        return ["pyinstaller"]
    return [sys.executable, "-m", "PyInstaller"]


def ensure_wix() -> tuple[str, str] | None:
    """
    Liefert (candle, light). Reihenfolge: PATH -> Cache -> Download.
    Nur unter Windows sinnvoll (candle/light sind .exe).
    """
    if have("candle") and have("light"):
        return "candle", "light"

    wix_dir = CACHE_DIR / "wix314"
    candle, light = wix_dir / "candle.exe", wix_dir / "light.exe"
    if candle.is_file() and light.is_file():
        return str(candle), str(light)

    if platform.system() != "Windows":
        info("WiX gibt es nur fuer Windows -> MSI uebersprungen.")
        return None
    if not AUTO_INSTALL:
        info("WiX fehlt (Auto-Installation abgeschaltet) -> MSI uebersprungen.")
        return None

    info("Lade WiX v3 Binaries herunter (einmalig, ~10 MB) ...")
    import urllib.request
    import zipfile as _zip
    try:
        wix_dir.mkdir(parents=True, exist_ok=True)
        archive = wix_dir / "wix-binaries.zip"
        urllib.request.urlretrieve(WIX_URL, archive)
        with _zip.ZipFile(archive) as zf:
            zf.extractall(wix_dir)
        archive.unlink(missing_ok=True)
    except Exception as e:
        info(f"WiX-Download fehlgeschlagen ({e}) -> MSI uebersprungen.")
        return None

    if candle.is_file() and light.is_file():
        info(f"WiX bereit: {wix_dir}")
        return str(candle), str(light)
    info("WiX-Archiv enthielt candle/light nicht -> MSI uebersprungen.")
    return None


def agent_version() -> str:
    """Version aus agent/version.txt (wird nur GELESEN, nie geschrieben)."""
    try:
        v = (AGENT_DIR / "version.txt").read_text(encoding="utf-8").strip()
        return v.lstrip("v") or "0.0.0"
    except OSError:
        return "0.0.0"


def read_agent_token() -> str:
    """AGENT_TOKEN aus backend/.env holen (leer, wenn nicht vorhanden)."""
    try:
        for line in BACKEND_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("AGENT_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def agent_files() -> list[Path]:
    """Alle Dateien des Agenten (ohne .env, __pycache__, Logs)."""
    out = []
    for p in sorted(AGENT_DIR.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(AGENT_DIR))
        if "__pycache__" in rel or p.name.startswith(".env") or p.suffix == ".log":
            continue
        out.append(p)
    return out


def copy_agent_into(dest: Path, backend_url: str, token: str) -> None:
    """Agent-Dateien + vorbereitete .env nach dest kopieren."""
    dest.mkdir(parents=True, exist_ok=True)
    for p in agent_files():
        target = dest / p.relative_to(AGENT_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    # Logo fuer den Zustimmungs-Dialog mitnehmen (wie im Web-Installer).
    for cand in (ROOT / "backend" / "branding" / "logo_r.png",
                 ROOT / "frontend" / "images" / "logo_r.png"):
        if cand.is_file():
            shutil.copy2(cand, dest / "logo.png")
            break
    (dest / ".env").write_text(
        f"BACKEND_URL={backend_url}\n"
        f"AGENT_TOKEN={token}\n"
        f"ENROLLMENT_TOKEN=\n"
        f"DEVICE_NAME=\n",
        encoding="utf-8",
    )


# ==========================================================================
# 1) Windows: Setup.exe (PyInstaller)
# ==========================================================================

INSTALLER_STUB = r'''
"""
RAPALLE.net RMM - Agent-Installer (Windows).
Wird von tools/build_installers.py zu einer .exe gepackt.

Aufruf:
    RapalleRmmAgent-Setup.exe                      -> interaktiv
    RapalleRmmAgent-Setup.exe /S                   -> still (nutzt die
                                                      eingebackene Backend-URL)
    RapalleRmmAgent-Setup.exe /S /URL=https://...  -> still, eigene Adresse
    RapalleRmmAgent-Setup.exe /TOKEN=abc123        -> Onboarding-Token (ordnet
                                                      das Geraet direkt dem im
                                                      Dashboard gewaehlten
                                                      Tenant/Standort zu)
    RapalleRmmAgent-Setup.exe /UNINSTALL           -> deinstallieren
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

INSTALL_DIR = Path(r"__INSTALL_DIR__")
TASK_NAME = "__TASK_NAME__"
BAKED_URL = "__BACKEND_URL__"


def say(msg):
    print(msg)


def run(args, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    return subprocess.run(args, **kw)


def powershell(script: str):
    return run(["powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script])


def payload_dir() -> Path:
    """Der eingebettete agent/-Ordner (PyInstaller entpackt nach _MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "payload"


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def find_python() -> str | None:
    for exe in ("python", "py"):
        p = shutil.which(exe)
        if p:
            return p
    for c in (r"C:\Program Files\Python312\python.exe",
              r"C:\Program Files\Python311\python.exe"):
        if os.path.isfile(c):
            return c
    return None


def install_python() -> str | None:
    """Python still nachinstallieren - erst winget, dann offizieller Installer."""
    say("Python nicht gefunden - installiere es automatisch ...")
    if shutil.which("winget"):
        run(["winget", "install", "-e", "--id", "Python.Python.3.12", "--silent",
             "--accept-package-agreements", "--accept-source-agreements"], timeout=1800)
    if not find_python():
        url = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
        dst = Path(os.environ["TEMP"]) / "python-setup.exe"
        powershell(f'Invoke-WebRequest -Uri "{url}" -OutFile "{dst}"')
        if dst.is_file():
            run([str(dst), "/quiet", "InstallAllUsers=1", "PrependPath=1"], timeout=1800)
    # PATH der laufenden Sitzung auffrischen
    machine = os.popen('powershell -NoProfile -Command '
                       '"[System.Environment]::GetEnvironmentVariable(\'Path\',\'Machine\')"').read().strip()
    if machine:
        os.environ["PATH"] = machine + ";" + os.environ.get("PATH", "")
    return find_python()


def stop_running_agent():
    say("Stoppe evtl. laufenden Agenten ...")
    run(["schtasks", "/end", "/tn", TASK_NAME])
    powershell(
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*agent.py*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    time.sleep(2)


def register_task(pythonw: Path):
    """
    Autostart als DIENST: Principal SYSTEM, Trigger 'Beim Systemstart'.
    Damit laeuft der Agent auch dann, wenn sich NIEMAND anmeldet (Server).
    Die Bildschirmaufnahme startet bei Bedarf einen Helfer in der aktiven
    Benutzersitzung (agent.py --screen-helper, siehe session_bridge.py) -
    das geht ausschliesslich aus einem SYSTEM-Prozess heraus.
    """
    say("Richte Autostart als Dienst ein (SYSTEM, beim Systemstart) ...")
    ps = f"""
$Action = New-ScheduledTaskAction -Execute "{pythonw}" -Argument '"{INSTALL_DIR}\\agent.py"' -WorkingDirectory "{INSTALL_DIR}"
$TrigBoot  = New-ScheduledTaskTrigger -AtStartup
$TrigWatch = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
try {{ Unregister-ScheduledTask -TaskName "{TASK_NAME}" -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
Register-ScheduledTask -TaskName "{TASK_NAME}" -Action $Action -Trigger $TrigBoot,$TrigWatch -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName "{TASK_NAME}"
"""
    r = powershell(ps)
    if r.returncode != 0:
        say("FEHLER beim Einrichten des Autostarts:")
        say((r.stderr or r.stdout or "").strip())
        return False
    return True


def uninstall():
    say("Deinstalliere RAPALLE.net RMM Agent ...")
    stop_running_agent()
    run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    for t in ("RapalleRmmUpdate", "RapalleRmmUninstall"):
        run(["schtasks", "/delete", "/tn", t, "/f"])
    shutil.rmtree(INSTALL_DIR, ignore_errors=True)
    say("Fertig - der Agent wurde entfernt.")
    return 0


def main() -> int:
    argv = [a for a in sys.argv[1:]]
    silent = any(a.upper() == "/S" for a in argv)
    if any(a.upper() in ("/UNINSTALL", "/U") for a in argv):
        return uninstall()

    if not is_admin():
        # PyInstaller wird zwar mit --uac-admin gebaut (Windows fragt dann von
        # selbst), aber falls das Manifest fehlt oder umgangen wurde: hier per
        # ShellExecute "runas" neu starten und alle Parameter weiterreichen.
        say("Starte mit Administratorrechten neu (UAC-Abfrage) ...")
        try:
            import ctypes
            params = " ".join(f'"{a}"' if " " in a else a for a in argv)
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable,
                                                     params, None, 1)
            if rc > 32:
                return 0
        except Exception:
            pass
        say("Rechteerhoehung abgelehnt oder fehlgeschlagen - "
            "bitte als Administrator ausfuehren.")
        if not silent:
            input("Weiter mit [Enter] ...")
        return 1

    url = BAKED_URL
    enroll = ""
    for a in argv:
        if a.upper().startswith("/URL="):
            url = a.split("=", 1)[1]
        elif a.upper().startswith("/TOKEN="):
            enroll = a.split("=", 1)[1]
    enroll = enroll or os.environ.get("RMM_ENROLLMENT_TOKEN", "")
    url = url or os.environ.get("RMM_BACKEND_URL", "")
    if not url and not silent:
        url = input("Adresse des RMM-Servers (z.B. https://rmm.firma.de): ").strip()
    if not url:
        say("Keine Backend-Adresse angegeben (Parameter /URL=... oder RMM_BACKEND_URL).")
        return 1
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url = url.rstrip("/")

    stop_running_agent()

    say(f"Kopiere Agent nach {INSTALL_DIR} ...")
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    old_env = INSTALL_DIR / ".env"
    keep_env = old_env.read_text(encoding="utf-8", errors="replace") if old_env.is_file() else ""
    for item in payload_dir().iterdir():
        target = INSTALL_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    # Bestehende .env (Device-Zuordnung!) behalten, nur URL/Token aktualisieren.
    base = keep_env or (INSTALL_DIR / ".env").read_text(encoding="utf-8", errors="replace")
    lines = []
    for ln in base.splitlines():
        if ln.startswith("BACKEND_URL="):
            ln = f"BACKEND_URL={url}"
        elif ln.startswith("ENROLLMENT_TOKEN=") and enroll:
            ln = f"ENROLLMENT_TOKEN={enroll}"
        lines.append(ln)
    (INSTALL_DIR / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    py = find_python() or install_python()
    if not py:
        say("Python konnte nicht installiert werden - bitte manuell nachholen.")
        return 1

    say("Installiere Python-Abhaengigkeiten (kann 1-2 Minuten dauern) ...")
    venv = INSTALL_DIR / "venv"
    run([py, "-m", "venv", str(venv)], timeout=900)
    pip = venv / "Scripts" / "pip.exe"
    r = run([str(pip), "install", "-r", str(INSTALL_DIR / "requirements.txt"), "--quiet"],
            timeout=1800)
    if r.returncode != 0:
        say("FEHLER bei der Paket-Installation:")
        say((r.stderr or r.stdout or "").strip()[:2000])
        return 1

    if not register_task(venv / "Scripts" / "pythonw.exe"):
        return 1

    say("")
    say("=== FERTIG ===")
    say("Der Agent laeuft als Dienst unter SYSTEM und startet beim Booten -")
    say("auch wenn sich niemand anmeldet.")
    say(f"Log-Datei bei Problemen: {INSTALL_DIR}\\agent.log")
    if not silent:
        input("Weiter mit [Enter] ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def build_windows_exe(out: Path, backend_url: str, token: str) -> Path | None:
    if platform.system() != "Windows":
        info("Windows-EXE kann nur AUF Windows gebaut werden -> uebersprungen.")
        return None
    if not ensure_pyinstaller():
        return None

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        copy_agent_into(tmp / "payload", backend_url, token)
        stub = tmp / "installer_main.py"
        stub.write_text(
            INSTALLER_STUB
            .replace("__INSTALL_DIR__", WIN_INSTALL_DIR)
            .replace("__TASK_NAME__", TASK_NAME)
            .replace("__BACKEND_URL__", backend_url),
            encoding="utf-8",
        )
        info("Baue RapalleRmmAgent-Setup.exe (PyInstaller) ...")
        r = subprocess.run(
            pyinstaller_cmd() + ["--onefile", "--console", "--uac-admin",
             "--name", "RapalleRmmAgent-Setup",
             "--distpath", str(out), "--workpath", str(tmp / "work"),
             "--specpath", str(tmp),
             "--add-data", f"{tmp / 'payload'};payload",
             str(stub)],
            text=True,
        )
        if r.returncode != 0:
            info("PyInstaller fehlgeschlagen.")
            return None
    exe = out / "RapalleRmmAgent-Setup.exe"
    return exe if exe.is_file() else None


# --------------------------------------------------------------------------
# Vorlagen fuer die Windows-Skript-Pakete (.ps1 / .bat)
# --------------------------------------------------------------------------
# Bewusst als .format()-Vorlagen (keine f-Strings): PowerShell und Batch
# wimmeln von geschweiften Klammern - so muss nichts verdoppelt werden.
# Platzhalter: {backend_url} {install_dir} {task}

PS_SETUP_TEMPLATE = '''param(
    [string]$BackendUrl = "{backend_url}",
    [string]$Token = "",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$InstallDir = "{install_dir}"
$TaskName   = "{task}"
$Payload    = $PSScriptRoot

function Stop-Agent {{
    try {{ schtasks /end /tn $TaskName 2>$null | Out-Null }} catch {{}}
    try {{
        Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
            Where-Object {{ $_.CommandLine -like "*agent.py*" }} |
            ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
    }} catch {{}}
    Start-Sleep -Seconds 2
}}

if ($Uninstall) {{
    Stop-Agent
    schtasks /delete /tn $TaskName /f 2>$null | Out-Null
    foreach ($t in @("RapalleRmmUpdate", "RapalleRmmUninstall")) {{
        schtasks /delete /tn $t /f 2>$null | Out-Null
    }}
    Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue
    Write-Host "Agent entfernt."
    exit 0
}}

# --- Adminrechte: notfalls SELBST neu starten -----------------------------
# Ohne Administratorrechte laesst sich weder nach "Program Files" schreiben
# noch ein SYSTEM-Task anlegen. Statt abzubrechen starten wir uns per UAC
# einfach neu und geben alle Parameter weiter.
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    Write-Host "Starte mit Administratorrechten neu (UAC-Abfrage) ..." -ForegroundColor Yellow
    $relaunch = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($BackendUrl) {{ $relaunch += @("-BackendUrl", "`"$BackendUrl`"") }}
    if ($Token)      {{ $relaunch += @("-Token", "`"$Token`"") }}
    if ($Uninstall)  {{ $relaunch += "-Uninstall" }}
    try {{
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList $relaunch -Verb RunAs -PassThru -Wait
        exit $p.ExitCode
    }} catch {{
        Write-Host "Rechteerhoehung abgelehnt oder fehlgeschlagen." -ForegroundColor Red
        exit 1
    }}
}}

if (-not $BackendUrl) {{ $BackendUrl = $env:RMM_BACKEND_URL }}
if (-not $BackendUrl) {{ $BackendUrl = Read-Host "Adresse des RMM-Servers (z.B. https://rmm.firma.de)" }}
if (-not $BackendUrl) {{ Write-Host "Keine Backend-Adresse angegeben." -ForegroundColor Red; exit 1 }}
if ($BackendUrl -notmatch "^https?://") {{ $BackendUrl = "https://$BackendUrl" }}
$BackendUrl = $BackendUrl.TrimEnd("/")
if (-not $Token) {{ $Token = $env:RMM_ENROLLMENT_TOKEN }}

# --- 1. Python sicherstellen ---------------------------------------------
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {{
    Write-Host "Python nicht gefunden - installiere es automatisch ..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {{
        winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    }} else {{
        $pyUrl = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
        Invoke-WebRequest -Uri $pyUrl -OutFile "$env:TEMP\\python-setup.exe"
        Start-Process -Wait -FilePath "$env:TEMP\\python-setup.exe" -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1"
    }}
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}}
if (-not $python) {{ Write-Host "Python fehlt weiterhin - bitte manuell installieren." -ForegroundColor Red; exit 1 }}

# --- 2. Dateien kopieren (vorhandene .env behalten!) ----------------------
Stop-Agent
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$keepEnv = ""
if (Test-Path "$InstallDir\\.env") {{ $keepEnv = Get-Content "$InstallDir\\.env" -Raw }}
Get-ChildItem $Payload -Exclude "_setup.ps1" | Copy-Item -Destination $InstallDir -Recurse -Force
if ($keepEnv) {{ Set-Content "$InstallDir\\.env" $keepEnv -NoNewline }}

$envLines = @()
foreach ($line in (Get-Content "$InstallDir\\.env")) {{
    if ($line -like "BACKEND_URL=*") {{ $line = "BACKEND_URL=$BackendUrl" }}
    elseif ($Token -and $line -like "ENROLLMENT_TOKEN=*") {{ $line = "ENROLLMENT_TOKEN=$Token" }}
    $envLines += $line
}}
Set-Content "$InstallDir\\.env" ($envLines -join "`n")

# --- 3. Abhaengigkeiten ---------------------------------------------------
Write-Host "Installiere Python-Abhaengigkeiten (kann 1-2 Minuten dauern) ..."
& $python -m venv "$InstallDir\\venv"
& "$InstallDir\\venv\\Scripts\\python.exe" -m pip install --upgrade pip --quiet
& "$InstallDir\\venv\\Scripts\\pip.exe" install -r "$InstallDir\\requirements.txt" --quiet
if ($LASTEXITCODE -ne 0) {{ Write-Host "FEHLER bei der Paket-Installation." -ForegroundColor Red; exit 1 }}

# --- 4. Autostart als Dienst (SYSTEM, beim Systemstart) -------------------
# So laeuft der Agent auch ohne Anmeldung (Server). Die Bildschirmaufnahme
# startet bei Bedarf einen Helfer in der aktiven Benutzersitzung.
Write-Host "Richte Autostart als Dienst ein (SYSTEM, beim Systemstart) ..."
$Action    = New-ScheduledTaskAction -Execute "$InstallDir\\venv\\Scripts\\pythonw.exe" -Argument "`"$InstallDir\\agent.py`"" -WorkingDirectory $InstallDir
$TrigBoot  = New-ScheduledTaskTrigger -AtStartup
$TrigWatch = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
try {{ Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $TrigBoot,$TrigWatch -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "=== FERTIG ===" -ForegroundColor Green
Write-Host "Der Agent laeuft als Dienst unter SYSTEM und startet beim Booten."
Write-Host "Log-Datei bei Problemen: $InstallDir\\agent.log"
'''


PS_WRAPPER_TEMPLATE = '''<#
RAPALLE.net RMM - Agent-Installation (Windows, eigenstaendig)
Der komplette Agent steckt als base64-ZIP am Ende dieser Datei - es wird also
weder Internet noch Zugriff auf das Backend gebraucht.

Aufruf (PowerShell als Administrator):
    .\\install-agent.ps1
    .\\install-agent.ps1 -BackendUrl https://rmm.firma.de -Token <onboarding-token>
    .\\install-agent.ps1 -Uninstall

Falls die Ausfuehrung blockiert wird:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\\install-agent.ps1
#>
param(
    [string]$BackendUrl = "{backend_url}",
    [string]$Token = "",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

# --- Adminrechte: notfalls SELBST per UAC neu starten ---------------------
# So genuegt ein Rechtsklick "Mit PowerShell ausfuehren" bzw. ein normaler
# Aufruf - die Rechteabfrage kommt automatisch.
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    Write-Host "Starte mit Administratorrechten neu (UAC-Abfrage) ..." -ForegroundColor Yellow
    $relaunch = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($BackendUrl) {{ $relaunch += @("-BackendUrl", "`"$BackendUrl`"") }}
    if ($Token)      {{ $relaunch += @("-Token", "`"$Token`"") }}
    if ($Uninstall)  {{ $relaunch += "-Uninstall" }}
    try {{
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList $relaunch -Verb RunAs -PassThru -Wait
        exit $p.ExitCode
    }} catch {{
        Write-Host "Rechteerhoehung abgelehnt oder fehlgeschlagen." -ForegroundColor Red
        exit 1
    }}
}}

$work = Join-Path $env:TEMP ("rmm-agent-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {{
    # --- Eingebettetes ZIP herausloesen und entpacken ---------------------
    $self = Get-Content -LiteralPath $PSCommandPath -Raw
    $mark = "#__PAYLOAD__#"
    $b64  = $self.Substring($self.LastIndexOf($mark) + $mark.Length)
    $zip  = Join-Path $work "payload.zip"
    [IO.File]::WriteAllBytes($zip, [Convert]::FromBase64String(($b64 -replace "\\s", "")))
    Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $work "agent") -Force

    # --- Setup ausfuehren -------------------------------------------------
    $setup = Join-Path $work "agent\\_setup.ps1"
    $argv = @("-BackendUrl", $BackendUrl)
    if ($Token)     {{ $argv += @("-Token", $Token) }}
    if ($Uninstall) {{ $argv += "-Uninstall" }}
    & $setup @argv
    exit $LASTEXITCODE
}} finally {{
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}}
#__PAYLOAD__#
'''


BAT_TEMPLATE = '''@echo off
REM ========================================================================
REM RAPALLE.net RMM - Agent-Installation (Windows, eigenstaendig)
REM Der komplette Agent steckt als base64-ZIP am Ende dieser Datei.
REM
REM Aufruf (Eingabeaufforderung als Administrator):
REM     install-agent.bat
REM     install-agent.bat https://rmm.firma.de <onboarding-token>
REM     install-agent.bat /uninstall
REM ========================================================================
setlocal EnableDelayedExpansion

REM --- Adminrechte: notfalls SELBST per UAC neu starten -------------------
REM Damit reicht ein Doppelklick - Windows fragt dann nach den Rechten.
net session >nul 2>&1
if errorlevel 1 (
    echo Starte mit Administratorrechten neu ^(UAC-Abfrage^) ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)

set "UNINST="
set "BACKEND=%~1"
set "TOKEN=%~2"
if /i "%~1"=="/uninstall" set "UNINST=1" & set "BACKEND="
if "%BACKEND%"=="" set "BACKEND={backend_url}"

set "WORK=%TEMP%\\rmm-agent-%RANDOM%%RANDOM%"
mkdir "%WORK%" >nul 2>&1

REM --- Zeilennummer der Payload-Marke suchen, alles danach herausschneiden -
for /f "delims=:" %%a in ('findstr /n /b /c:":PAYLOAD_BELOW" "%~f0"') do set "LN=%%a"
more +%LN% "%~f0" > "%WORK%\\payload.b64"
certutil -f -decode "%WORK%\\payload.b64" "%WORK%\\payload.zip" >nul
if errorlevel 1 (
    echo FEHLER beim Entpacken des eingebetteten Pakets.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%WORK%\\payload.zip' -DestinationPath '%WORK%\\agent' -Force"

if "%UNINST%"=="1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%WORK%\\agent\\_setup.ps1" -Uninstall
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%WORK%\\agent\\_setup.ps1" -BackendUrl "%BACKEND%" -Token "%TOKEN%"
)
set "RC=%ERRORLEVEL%"

rd /s /q "%WORK%" >nul 2>&1
if "%RC%"=="0" (echo. & echo Installation abgeschlossen.) else (echo. & echo Installation fehlgeschlagen [Code %RC%].)
if not "%UNINST%"=="1" pause
exit /b %RC%

:PAYLOAD_BELOW
'''


# ==========================================================================
# 1b) Windows: Setup-Logik als PowerShell (fuer .ps1 und .bat)
# ==========================================================================

def windows_setup_ps1(backend_url: str) -> str:
    """
    Der eigentliche Installer als PowerShell-Skript. Er liegt IM Paket
    (_setup.ps1) und wird von install-agent.ps1 / install-agent.bat nach dem
    Entpacken aufgerufen. Gleiche Schritte wie Setup.exe und der Web-Installer:
    Python pruefen/nachinstallieren, venv + Abhaengigkeiten, Autostart-Task
    unter SYSTEM mit Trigger "Beim Systemstart".
    """
    return PS_SETUP_TEMPLATE.format(backend_url=backend_url,
                                    install_dir=WIN_INSTALL_DIR,
                                    task=TASK_NAME)


def _windows_payload_b64(backend_url: str, token: str) -> str:
    """Agent + _setup.ps1 als ZIP, base64-kodiert (fuer .ps1/.bat)."""
    import zipfile as _zip
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "agent"
        copy_agent_into(src, backend_url, token)
        (src / "_setup.ps1").write_text(windows_setup_ps1(backend_url), encoding="utf-8")
        zpath = tmp / "payload.zip"
        with _zip.ZipFile(zpath, "w", _zip.ZIP_DEFLATED) as zf:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    zf.write(f, arcname=str(f.relative_to(src)))
        return base64.b64encode(zpath.read_bytes()).decode("ascii")


def _wrap_b64(b64: str, width: int = 76) -> str:
    return "\n".join(b64[i:i + width] for i in range(0, len(b64), width))


def build_windows_ps1(out: Path, backend_url: str, token: str) -> Path:
    """
    Eigenstaendiges PowerShell-Installationsskript mit eingebettetem Agenten.
    Braucht kein Internet und keinen Zugriff auf das Backend.
    """
    b64 = _wrap_b64(_windows_payload_b64(backend_url, token))
    script = PS_WRAPPER_TEMPLATE.format(backend_url=backend_url) + b64 + "\n"
    dest = out / "install-agent.ps1"
    info(f"Baue {dest.name} ...")
    dest.write_text(script, encoding="utf-8")
    return dest


def build_windows_bat(out: Path, backend_url: str, token: str) -> Path:
    """
    Eigenstaendige .bat - fuer Umgebungen, in denen .ps1 blockiert ist oder in
    denen per Doppelklick installiert werden soll. Das base64-ZIP steht hinter
    der Marke :PAYLOAD_BELOW; "more +N" schneidet es heraus, "certutil -decode"
    macht wieder ein ZIP daraus. Danach uebernimmt _setup.ps1.
    """
    b64 = _wrap_b64(_windows_payload_b64(backend_url, token), 76)
    head = BAT_TEMPLATE.format(backend_url=backend_url)
    dest = out / "install-agent.bat"
    info(f"Baue {dest.name} ...")
    # CRLF: cmd.exe verarbeitet reine LF-Skripte unzuverlaessig.
    data = (head.replace("\n", "\r\n") + b64.replace("\n", "\r\n") + "\r\n")
    dest.write_bytes(data.encode("utf-8"))
    return dest



# ==========================================================================
# 2) Windows: MSI (nur mit WiX v3 - candle/light)
# ==========================================================================

WXS = r'''<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="RAPALLE.net RMM Agent" Language="1031"
           Version="__VERSION__" Manufacturer="RAPALLE.net"
           UpgradeCode="7B2F1C64-9A3D-4F51-8E2A-0C7A1D4E9B31">
    <Package InstallerVersion="300" Compressed="yes" InstallScope="perMachine" />
    <MajorUpgrade DowngradeErrorMessage="Eine neuere Version ist bereits installiert." />
    <MediaTemplate EmbedCab="yes" />

    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="ProgramFiles64Folder">
        <Directory Id="INSTALLFOLDER" Name="RapalleRmmAgent" />
      </Directory>
    </Directory>

    <ComponentGroup Id="Payload" Directory="INSTALLFOLDER">
      <Component Id="SetupExe" Guid="*">
        <File Id="SetupExeFile" Source="__SETUP_EXE__" KeyPath="yes" />
      </Component>
    </ComponentGroup>

    <Feature Id="Main" Title="RMM Agent" Level="1">
      <ComponentGroupRef Id="Payload" />
    </Feature>

    <!-- Nach dem Kopieren: Setup still ausfuehren (richtet venv + SYSTEM-Task ein). -->
    <CustomAction Id="RunSetup" FileKey="SetupExeFile" ExeCommand="/S"
                  Execute="deferred" Impersonate="no" Return="check" />
    <!-- Bei der Deinstallation den Agenten sauber entfernen. -->
    <CustomAction Id="RunUninstall" FileKey="SetupExeFile" ExeCommand="/UNINSTALL"
                  Execute="deferred" Impersonate="no" Return="ignore" />

    <InstallExecuteSequence>
      <Custom Action="RunSetup" After="InstallFiles">NOT Installed</Custom>
      <Custom Action="RunUninstall" Before="RemoveFiles">REMOVE="ALL"</Custom>
    </InstallExecuteSequence>
  </Product>
</Wix>
'''


def build_windows_msi(out: Path, setup_exe: Path | None) -> Path | None:
    if setup_exe is None or not setup_exe.is_file():
        info("MSI uebersprungen (Setup.exe fehlt).")
        return None
    wix = ensure_wix()
    if not wix:
        return None
    candle, light = wix
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        wxs = tmp / "agent.wxs"
        wxs.write_text(
            WXS.replace("__VERSION__", agent_version())
               .replace("__SETUP_EXE__", str(setup_exe)),
            encoding="utf-8",
        )
        info("Baue RapalleRmmAgent.msi (WiX) ...")
        if subprocess.run([candle, "-nologo", "-out", str(tmp / "agent.wixobj"), str(wxs)]).returncode:
            return None
        msi = out / "RapalleRmmAgent.msi"
        if subprocess.run([light, "-nologo", "-out", str(msi), str(tmp / "agent.wixobj")]).returncode:
            return None
        return msi if msi.is_file() else None


# ==========================================================================
# 3) Linux: gemeinsame Installationslogik (postinst / .run)
# ==========================================================================

def linux_setup_script(backend_url: str) -> str:
    """
    Wird nach dem Entpacken ausgefuehrt (deb-postinst, rpm-post, .run).
    Richtet venv + Abhaengigkeiten + systemd-Dienst ein.

    Der Dienst laeuft als root unter multi-user.target - er startet also beim
    Booten, ohne dass sich jemand anmelden muss.
    """
    return f'''#!/bin/bash
set -e
INSTALL_DIR="{LINUX_INSTALL_DIR}"
DEFAULT_URL="{backend_url}"

# Backend-Adresse: Paket-Vorgabe, Umgebungsvariable oder Nachfrage.
URL="${{RMM_BACKEND_URL:-$DEFAULT_URL}}"
if [ -z "$URL" ] && [ -t 0 ]; then
  read -r -p "Adresse des RMM-Servers (z.B. https://rmm.firma.de): " URL
fi
if [ -z "$URL" ]; then
  echo "Keine Backend-Adresse gesetzt. Nachtraeglich in $INSTALL_DIR/.env eintragen"
  echo "und dann: systemctl restart rapalle-agent"
else
  sed -i "s|^BACKEND_URL=.*|BACKEND_URL=${{URL%/}}|" "$INSTALL_DIR/.env" 2>/dev/null || true
fi

# Onboarding-Token (ordnet das Geraet direkt dem gewaehlten Tenant/Standort zu).
if [ -n "${{RMM_ENROLLMENT_TOKEN:-}}" ]; then
  sed -i "s|^ENROLLMENT_TOKEN=.*|ENROLLMENT_TOKEN=$RMM_ENROLLMENT_TOKEN|" "$INSTALL_DIR/.env" 2>/dev/null || true
fi

# --- Systemvoraussetzungen (python3, venv, pip) ---------------------------
PYV="$(python3 -c 'import sys;print(f"{{sys.version_info.major}}.{{sys.version_info.minor}}")' 2>/dev/null || echo 3)"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y -qq >/dev/null 2>&1 || true
  apt-get install -y -qq "python${{PYV}}-venv" >/dev/null 2>&1 \\
    || apt-get install -y -qq python3-venv >/dev/null 2>&1 || true
  apt-get install -y -qq python3-pip >/dev/null 2>&1 || true
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q python3 python3-pip >/dev/null 2>&1 || true
elif command -v zypper >/dev/null 2>&1; then
  zypper --non-interactive install -y python3 python3-pip >/dev/null 2>&1 || true
elif command -v pacman >/dev/null 2>&1; then
  pacman -Sy --noconfirm python python-pip >/dev/null 2>&1 || true
fi

# --- Abhaengigkeiten in einer venv (Fallback: systemweit) -----------------
cd "$INSTALL_DIR"
if python3 -m venv venv >/dev/null 2>&1; then
  ./venv/bin/pip install --quiet --upgrade pip || true
  ./venv/bin/pip install --quiet -r requirements.txt
  PYEXEC="$INSTALL_DIR/venv/bin/python"
else
  python3 -m pip install --quiet --break-system-packages -r requirements.txt \\
    || python3 -m pip install --quiet -r requirements.txt
  PYEXEC="$(command -v python3)"
fi

# --- systemd-Dienst -------------------------------------------------------
cat > /etc/systemd/system/rapalle-agent.service <<UNIT
[Unit]
Description=RAPALLE.net RMM Agent
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYEXEC $INSTALL_DIR/agent.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now rapalle-agent
echo "Fertig - Status: systemctl status rapalle-agent"
'''


LINUX_REMOVE = f'''#!/bin/bash
systemctl stop rapalle-agent 2>/dev/null || true
systemctl disable rapalle-agent 2>/dev/null || true
rm -f /etc/systemd/system/rapalle-agent.service
systemctl daemon-reload 2>/dev/null || true
rm -rf {LINUX_INSTALL_DIR}/venv
exit 0
'''


# ==========================================================================
# 4) Linux: .deb
# ==========================================================================

def build_deb(out: Path, backend_url: str, token: str) -> Path | None:
    if not have("dpkg-deb"):
        info("dpkg-deb fehlt -> .deb uebersprungen.")
        return None
    version = agent_version()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        copy_agent_into(root / LINUX_INSTALL_DIR.lstrip("/"), backend_url, token)
        debian = root / "DEBIAN"
        debian.mkdir(parents=True)
        (debian / "control").write_text(
            f"Package: {PKG_NAME}\n"
            f"Version: {version}\n"
            "Section: admin\n"
            "Priority: optional\n"
            "Architecture: all\n"
            "Depends: python3 (>= 3.9), python3-venv | python3-virtualenv, systemd\n"
            "Maintainer: RAPALLE.net <noreply@rapalle.net>\n"
            "Description: RAPALLE.net RMM Agent\n"
            " Verbindet dieses Geraet mit dem RAPALLE.net RMM Dashboard.\n"
            " Laeuft als systemd-Dienst (root) und startet beim Booten.\n",
            encoding="utf-8",
        )
        (debian / "postinst").write_text(linux_setup_script(backend_url), encoding="utf-8")
        (debian / "prerm").write_text(LINUX_REMOVE, encoding="utf-8")
        for f in ("postinst", "prerm"):
            os.chmod(debian / f, 0o755)
        # .env darf beim Update nicht ueberschrieben werden.
        (debian / "conffiles").write_text(f"{LINUX_INSTALL_DIR}/.env\n", encoding="utf-8")

        deb = out / f"{PKG_NAME}_{version}_all.deb"
        info(f"Baue {deb.name} ...")
        if subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                           str(root), str(deb)]).returncode:
            return None
        return deb if deb.is_file() else None


# ==========================================================================
# 5) Linux: .rpm
# ==========================================================================

def build_rpm(out: Path, backend_url: str, token: str) -> Path | None:
    if not have("rpmbuild"):
        info("rpmbuild fehlt -> .rpm uebersprungen (Paket: rpm / rpm-build).")
        return None
    version = agent_version()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        top = tmp / "rpmbuild"
        for d in ("SPECS", "SOURCES", "BUILD", "RPMS", "SRPMS", "BUILDROOT"):
            (top / d).mkdir(parents=True)

        src = tmp / f"{PKG_NAME}-{version}"
        copy_agent_into(src, backend_url, token)
        tar = top / "SOURCES" / f"{PKG_NAME}-{version}.tar.gz"
        with tarfile.open(tar, "w:gz") as tf:
            tf.add(src, arcname=f"{PKG_NAME}-{version}")

        post = linux_setup_script(backend_url).replace("#!/bin/bash\nset -e\n", "")
        spec = f"""Name:           {PKG_NAME}
Version:        {version}
Release:        1
Summary:        RAPALLE.net RMM Agent
License:        MIT
BuildArch:      noarch
Source0:        {PKG_NAME}-{version}.tar.gz
Requires:       python3, systemd

%description
Verbindet dieses Geraet mit dem RAPALLE.net RMM Dashboard.
Laeuft als systemd-Dienst (root) und startet beim Booten.

%prep
%setup -q

%install
mkdir -p %{{buildroot}}{LINUX_INSTALL_DIR}
cp -a . %{{buildroot}}{LINUX_INSTALL_DIR}/

%files
%config(noreplace) {LINUX_INSTALL_DIR}/.env
{LINUX_INSTALL_DIR}

%post
{post}

%preun
if [ $1 -eq 0 ]; then
{LINUX_REMOVE.replace("#!/bin/bash", "").replace("exit 0", "")}
fi
"""
        specfile = top / "SPECS" / f"{PKG_NAME}.spec"
        specfile.write_text(spec, encoding="utf-8")
        info(f"Baue {PKG_NAME}-{version}.rpm ...")
        r = subprocess.run(["rpmbuild", "-bb", "--define", f"_topdir {top}", str(specfile)])
        if r.returncode:
            return None
        for f in (top / "RPMS").rglob("*.rpm"):
            dest = out / f.name
            shutil.copy2(f, dest)
            return dest
    return None


# ==========================================================================
# 6) Ueberall: selbstentpackendes .run-Skript
# ==========================================================================

def build_run(out: Path, backend_url: str, token: str) -> Path:
    """
    Selbstentpackender Installer: Shell-Kopf + base64-kodiertes tar.gz.
    Laesst sich auf JEDEM System bauen (auch Windows) und laeuft auf jedem
    Linux mit bash + tar - ganz ohne Paketmanager.
    """
    version = agent_version()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "agent"
        copy_agent_into(src, backend_url, token)
        tgz = tmp / "payload.tar.gz"
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(src, arcname=".")
        b64 = base64.b64encode(tgz.read_bytes()).decode("ascii")

    setup = linux_setup_script(backend_url).replace("#!/bin/bash\nset -e\n", "")
    header = f'''#!/bin/bash
# RAPALLE.net RMM Agent {version} - selbstentpackender Installer
# Aufruf:   sudo ./install-agent.sh          (fragt ggf. nach der Adresse)
#           sudo RMM_BACKEND_URL=https://rmm.firma.de ./install-agent.sh
#           sudo RMM_ENROLLMENT_TOKEN=<token> ./install-agent.sh
#           sudo ./install-agent.sh --uninstall
set -e
INSTALL_DIR="{LINUX_INSTALL_DIR}"

# --- Rootrechte: notfalls SELBST ueber sudo neu starten -------------------
# "-E" haelt Umgebungsvariablen wie RMM_BACKEND_URL / RMM_ENROLLMENT_TOKEN am
# Leben, sonst waeren sie nach dem sudo-Wechsel weg.
if [ "$(id -u)" != "0" ]; then
  if command -v sudo >/dev/null 2>&1; then
    echo "Starte mit Rootrechten neu (sudo) ..."
    exec sudo -E "$0" "$@"
  fi
  echo "Bitte als root ausfuehren (sudo ist nicht installiert)."; exit 1
fi

if [ "$1" = "--uninstall" ]; then
{LINUX_REMOVE.replace("#!/bin/bash", "").rstrip()}
  rm -rf "$INSTALL_DIR"
  echo "Agent entfernt."
  exit 0
fi

echo "Entpacke Agent nach $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
# Vorhandene .env (Geraete-Zuordnung!) sichern.
KEEP=""
[ -f "$INSTALL_DIR/.env" ] && KEEP="$(cat "$INSTALL_DIR/.env")"
ARCHIVE_LINE=$(awk '/^__PAYLOAD_BELOW__/ {{print NR + 1; exit 0}}' "$0")
tail -n +"$ARCHIVE_LINE" "$0" | base64 -d | tar xzf - -C "$INSTALL_DIR"
[ -n "$KEEP" ] && printf '%s\\n' "$KEEP" > "$INSTALL_DIR/.env"

{setup}
exit 0
__PAYLOAD_BELOW__
'''
    dest = out / "install-agent.sh"
    info(f"Baue {dest.name} ...")
    dest.write_text(header + b64 + "\n", encoding="utf-8", newline="\n")
    os.chmod(dest, 0o755)
    # Zusaetzlich unter dem klassischen .run-Namen ablegen (identischer Inhalt) -
    # manche Verteilwerkzeuge erwarten diese Endung.
    run_copy = out / f"{PKG_NAME}.run"
    shutil.copy2(dest, run_copy)
    os.chmod(run_copy, 0o755)
    return dest


# ==========================================================================
# 6b) Linux: schlichtes Tarball-Paket (.tar.gz)
# ==========================================================================

def build_tgz(out: Path, backend_url: str, token: str) -> Path:
    """
    Klassisches Archiv: agent-Dateien + install.sh + uninstall.sh.
    Fuer alle, die lieber selbst schauen, was installiert wird - oder das
    Paket per Ansible/Salt ausrollen:

        tar xzf rapalle-rmm-agent-<v>.tar.gz
        cd rapalle-rmm-agent-<v> && sudo ./install.sh
    """
    version = agent_version()
    name = f"{PKG_NAME}-{version}"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / name
        copy_agent_into(src, backend_url, token)

        install = src / "install.sh"
        install.write_text(
            "#!/bin/bash\n"
            "# Installiert den RMM-Agenten aus DIESEM Ordner nach "
            f"{LINUX_INSTALL_DIR}\n"
            "# Aufruf:  sudo ./install.sh   bzw.\n"
            "#          sudo RMM_BACKEND_URL=https://rmm.firma.de "
            "RMM_ENROLLMENT_TOKEN=<token> ./install.sh\n"
            "set -e\n"
            # Rootrechte: notfalls per sudo neu starten (-E haelt RMM_*-Variablen).
            'if [ "$(id -u)" != "0" ]; then\n'
            '  if command -v sudo >/dev/null 2>&1; then exec sudo -E "$0" "$@"; fi\n'
            '  echo "Bitte als root ausfuehren (sudo fehlt)."; exit 1\n'
            'fi\n'
            'HERE="$(cd "$(dirname "$0")" && pwd)"\n'
            f'INSTALL_DIR="{LINUX_INSTALL_DIR}"\n'
            'mkdir -p "$INSTALL_DIR"\n'
            '# Vorhandene .env (Geraete-Zuordnung!) behalten.\n'
            'KEEP=""; [ -f "$INSTALL_DIR/.env" ] && KEEP="$(cat "$INSTALL_DIR/.env")"\n'
            'cp -a "$HERE/." "$INSTALL_DIR/"\n'
            'rm -f "$INSTALL_DIR/install.sh" "$INSTALL_DIR/uninstall.sh"\n'
            '[ -n "$KEEP" ] && printf \'%s\\n\' "$KEEP" > "$INSTALL_DIR/.env"\n'
            + linux_setup_script(backend_url).replace("#!/bin/bash\nset -e\n", ""),
            encoding="utf-8",
        )
        uninstall = src / "uninstall.sh"
        uninstall.write_text(
            LINUX_REMOVE.replace(
                "#!/bin/bash\n",
                '#!/bin/bash\n'
                '# Rootrechte: notfalls per sudo neu starten.\n'
                'if [ "$(id -u)" != "0" ]; then\n'
                '  if command -v sudo >/dev/null 2>&1; then exec sudo -E "$0" "$@"; fi\n'
                '  echo "Bitte als root ausfuehren (sudo fehlt)."; exit 1\n'
                'fi\n',
            ).replace("exit 0", f'rm -rf {LINUX_INSTALL_DIR}\necho "Agent entfernt."\nexit 0'),
            encoding="utf-8",
        )
        os.chmod(install, 0o755)
        os.chmod(uninstall, 0o755)

        dest = out / f"{name}.tar.gz"
        info(f"Baue {dest.name} ...")
        with tarfile.open(dest, "w:gz") as tf:
            tf.add(src, arcname=name)
    return dest


# ==========================================================================
# 6c) Arch Linux: .pkg.tar.xz
# ==========================================================================

def build_pkg(out: Path, backend_url: str, token: str) -> Path:
    """
    Arch-/Manjaro-Paket. Wird von Hand gebaut (tar.xz mit .PKGINFO + .INSTALL),
    damit KEIN makepkg noetig ist - das Paket laesst sich also auch auf einem
    Debian- oder Windows-Server erzeugen.

        sudo pacman -U rapalle-rmm-agent-<v>-any.pkg.tar.xz

    Hinweis: Ohne makepkg fehlt die (optionale) .MTREE-Datei. pacman gibt dazu
    eine Warnung aus und installiert normal - nur die Dateipruefsummen fallen
    weg.
    """
    version = agent_version().replace("-", "_")   # pacman mag kein "-" in der Version
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "pkgroot"
        copy_agent_into(root / LINUX_INSTALL_DIR.lstrip("/"), backend_url, token)

        size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
        (root / ".PKGINFO").write_text(
            f"pkgname = {PKG_NAME}\n"
            f"pkgver = {version}-1\n"
            "pkgdesc = RAPALLE.net RMM Agent\n"
            "url = https://github.com/juliusbrussee\n"
            "builddate = 0\n"
            "packager = RAPALLE.net\n"
            f"size = {size}\n"
            "arch = any\n"
            "license = MIT\n"
            "depend = python\n"
            "depend = systemd\n"
            f"backup = {LINUX_INSTALL_DIR.lstrip('/')}/.env\n",
            encoding="utf-8",
        )
        # pacman-Hooks: post_install/post_upgrade richten den Dienst ein.
        setup = linux_setup_script(backend_url).replace("#!/bin/bash\nset -e\n", "")
        remove = LINUX_REMOVE.replace("#!/bin/bash\n", "").replace("exit 0", "")
        (root / ".INSTALL").write_text(
            "post_install() {\n" + setup + "\n}\n\n"
            "post_upgrade() {\n  post_install\n}\n\n"
            "pre_remove() {\n" + remove + "\n}\n",
            encoding="utf-8",
        )

        dest = out / f"{PKG_NAME}-{version}-1-any.pkg.tar.xz"
        info(f"Baue {dest.name} ...")
        with tarfile.open(dest, "w:xz") as tf:
            # .PKGINFO muss als ERSTER Eintrag im Archiv stehen.
            tf.add(root / ".PKGINFO", arcname=".PKGINFO")
            tf.add(root / ".INSTALL", arcname=".INSTALL")
            for f in sorted(root.rglob("*")):
                if f.name in (".PKGINFO", ".INSTALL") and f.parent == root:
                    continue
                tf.add(f, arcname=str(f.relative_to(root)), recursive=False)
    return dest



# ==========================================================================
# CLI
# ==========================================================================

ALL_TARGETS = ["exe", "msi", "bat", "ps1", "sh", "tgz", "deb", "rpm", "pkg"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Baut lokale Agent-Installationspakete.")
    ap.add_argument("--backend-url", default="",
                    help="Adresse des RMM-Servers, die ins Paket eingebacken wird "
                         "(leer = der Installer fragt beim Ausfuehren nach)")
    ap.add_argument("--token", default="",
                    help="AGENT_TOKEN (Standard: aus backend/.env)")
    ap.add_argument("--targets", default="auto",
                    help=f"Komma-Liste aus {','.join(ALL_TARGETS)} oder 'auto' "
                         "(alles, was das System bauen kann)")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="Ausgabeordner")
    ap.add_argument("--no-auto-install", action="store_true",
                    help="Fehlende Bau-Werkzeuge (PyInstaller, WiX) NICHT automatisch "
                         "nachinstallieren")
    ap.add_argument("--list", action="store_true",
                    help="Nur zeigen, welche Pakete hier gebaut werden koennen")
    args = ap.parse_args()

    global AUTO_INSTALL
    AUTO_INSTALL = not args.no_auto_install

    if not AGENT_DIR.is_dir():
        info(f"Agent-Ordner nicht gefunden: {AGENT_DIR}")
        return 1

    # exe/msi: Werkzeuge werden bei Bedarf automatisch nachgezogen (PyInstaller
    # per pip, WiX als Download) - deshalb reicht hier "laeuft auf Windows".
    is_win = platform.system() == "Windows"
    can = {
        "exe": is_win,
        "msi": is_win,
        # Skript- und Archivpakete brauchen KEIN externes Werkzeug - sie
        # lassen sich auf jedem System bauen, auch fuer die jeweils andere
        # Plattform (z.B. das Windows-.bat auf einem Linux-Server).
        "bat": True,
        "ps1": True,
        "sh": True,
        "tgz": True,
        "pkg": True,
        "deb": have("dpkg-deb"),
        "rpm": have("rpmbuild"),
    }
    if args.list:
        print("Baubar auf diesem System:")
        for t in ALL_TARGETS:
            print(f"  {t:4} {'ja' if can[t] else 'nein'}")
        return 0

    targets = ([t for t in ALL_TARGETS if can[t]] if args.targets == "auto"
               else [t.strip() for t in args.targets.split(",") if t.strip()])
    unknown = [t for t in targets if t not in ALL_TARGETS]
    if unknown:
        info(f"Unbekannte Ziele: {', '.join(unknown)}")
        return 1

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    url = args.backend_url.rstrip("/")
    token = args.token or read_agent_token()
    if not token:
        info("WARNUNG: kein AGENT_TOKEN gefunden (backend/.env) - die Agenten "
             "koennen sich damit nicht anmelden. Mit --token nachreichen.")

    info(f"Agent-Version {agent_version()} | Ziele: {', '.join(targets)} | Ausgabe: {out}")
    built: list[Path] = []
    setup_exe = None
    if "exe" in targets:
        setup_exe = build_windows_exe(out, url, token)
        if setup_exe:
            built.append(setup_exe)
    if "msi" in targets:
        # Die MSI kapselt die Setup.exe - fehlt sie, wird sie zuerst gebaut.
        if setup_exe is None:
            existing = out / "RapalleRmmAgent-Setup.exe"
            setup_exe = existing if existing.is_file() else build_windows_exe(out, url, token)
        msi = build_windows_msi(out, setup_exe)
        if msi:
            built.append(msi)
    if "bat" in targets:
        built.append(build_windows_bat(out, url, token))
    if "ps1" in targets:
        built.append(build_windows_ps1(out, url, token))
    if "tgz" in targets:
        built.append(build_tgz(out, url, token))
    if "pkg" in targets:
        built.append(build_pkg(out, url, token))
    if "deb" in targets:
        d = build_deb(out, url, token)
        if d:
            built.append(d)
    if "rpm" in targets:
        r = build_rpm(out, url, token)
        if r:
            built.append(r)
    if "sh" in targets:
        built.append(build_run(out, url, token))

    print()
    if not built:
        info("Es wurde nichts gebaut - siehe Hinweise oben.")
        return 1
    info("Fertige Pakete:")
    for b in built:
        info(f"  {b}  ({b.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

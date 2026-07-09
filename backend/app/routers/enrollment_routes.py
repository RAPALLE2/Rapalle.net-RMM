"""
routers/enrollment_routes.py
------------------------------
Deckt die 3 gewünschten Wege ab, einen neuen Client anzulegen:

  1. Download-Seite mit Link (+ Schritt-für-Schritt-Code daneben)
  2. Fertiger wget/curl-Einzeiler (installiert den Agenten inkl. Autostart)
  3. Manueller ZIP-Download

Ablauf: Der Admin klickt im Dashboard auf "Client hinzufügen" (ggf. mit
vorausgewähltem Tenant/Standort) -> das Frontend ruft POST /api/enrollment/tokens
auf -> bekommt einen Token zurück -> zeigt dann die Landing-Page-URL,
die install.sh/install.ps1-URLs und den Rein-Download-Link an.

Diese Routen liegen bewusst NICHT unter /api und brauchen KEIN Login-Token,
da sie ja gerade von einem brandneuen Rechner ohne Zugang zum Dashboard
aufgerufen werden - die Sicherheit kommt stattdessen vom zufälligen,
schwer zu erratenden Token in der URL selbst.
"""

import io
import ipaddress
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_admin
from app.config import AGENT_TOKEN

router = APIRouter(tags=["enrollment"])

# Pfad zum Agent-Quellcode-Ordner (wird für den ZIP-Download eingepackt)
AGENT_SOURCE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "agent"


class CreateEnrollmentBody(BaseModel):
    tenant_id: str | None = None
    location_id: str | None = None
    client_name: str | None = None


@router.post("/api/enrollment/tokens")
async def create_enrollment_token(body: CreateEnrollmentBody, request: Request, user: dict = Depends(get_current_user)):
    """
    Erzeugt einen neuen Onboarding-Token und gibt fertige URLs dafür zurück.

    WICHTIG: Die URLs werden hier ABSOLUT gebaut (via _backend_url: Settings
    server_url/server_domain/server_host haben Vorrang). Vorher hat das
    Frontend window.location.origin vorangestellt - wer das Dashboard über
    localhost/127.0.0.1 öffnet, bekam damit unbrauchbare "localhost"-Links.
    """
    require_admin(user)
    token = db.create_enrollment_token(body.tenant_id, body.location_id, body.client_name)
    base = _backend_url(request)
    return {
        "token": token,
        "base_url": base,
        "landing_url": f"{base}/enroll/{token}",
        "install_sh_url": f"{base}/enroll/{token}/install.sh",
        "install_ps1_url": f"{base}/enroll/{token}/install.ps1",
        "download_url": f"{base}/enroll/{token}/agent.zip",
    }


def _require_valid_token(token: str) -> dict:
    row = db.get_enrollment_token(token)
    if not row:
        raise HTTPException(404, "Unbekannter oder abgelaufener Onboarding-Link")
    return row


def _ensure_scheme(url: str) -> str:
    """
    Sorgt dafür, dass eine URL ein Schema hat. Ohne 'http(s)://' kann weder
    Invoke-WebRequest (Windows) noch curl (Linux) eine Verbindung aufbauen -
    genau daran scheitert der Client-Install, wenn im Feld 'Server-URL' nur
    eine nackte IP/Domain steht.
    """
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


def _configured_backend_port(default: int = 4000) -> int:
    try:
        return int(db.get_setting("server_backend_port"))
    except (TypeError, ValueError):
        return default


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _backend_url(request: Request) -> str:
    """
    Liefert die Basis-Adresse, die in Install-Befehle/Skripte eingebaut wird.

    Reihenfolge:
      1. Vollständige URL (server_url) - falls gesetzt, hat Vorrang.
      2. Domain oder IP/Host + Backend-Port (aus den Allgemein-Einstellungen).
      3. Automatisch aus dem Request abgeleitet (ergab bisher oft "localhost").
    """
    full = (db.get_setting("server_url") or "").strip()
    if full:
        # Fehlendes Schema ergänzen (sonst kann Invoke-WebRequest/curl nicht verbinden).
        full = _ensure_scheme(full)
        parts = urlsplit(full)
        has_port = parts.port is not None
        has_path = parts.path not in ("", "/")
        # Häufigster Konfig-Fehler: im Feld "Server-URL" steht nur eine nackte IP
        # (z.B. 10.10.32.249) ohne Port -> landet sonst auf Port 80, das Backend
        # hört aber auf 4000. Bei einer BLANKEN IP ohne Port/Pfad ergänzen wir
        # daher automatisch den konfigurierten Backend-Port. Domains bleiben
        # unangetastet (dort steckt praktisch immer ein Reverse-Proxy dahinter),
        # ebenso bereits mit Port/Pfad angegebene URLs und https://.
        if (parts.scheme == "http" and not has_port and not has_path
                and parts.hostname and _is_ip_literal(parts.hostname)):
            bport = _configured_backend_port()
            if bport not in (0, 80):
                full = urlunsplit((parts.scheme, f"{parts.hostname}:{bport}", "", "", ""))
        return full.rstrip("/")

    domain = (db.get_setting("server_domain") or "").strip()
    host = (db.get_setting("server_host") or "").strip()
    target = domain or host
    if target:
        # Ein evtl. mit eingegebenes Schema entfernen (bauen wir selbst).
        target = target.replace("https://", "").replace("http://", "").rstrip("/")
        try:
            port = int(db.get_setting("server_backend_port"))
        except (TypeError, ValueError):
            port = 4000
        scheme = "https" if port == 443 else "http"
        if port in (80, 443):
            return f"{scheme}://{target}"
        return f"{scheme}://{target}:{port}"

    # 3. Automatisch aus dem Request ableiten - dabei Reverse-Proxy-Header
    #    beachten. Läuft das Backend hinter einem Proxy (https://domain ->
    #    http://ip:4000), setzt der Proxy üblicherweise X-Forwarded-Proto/Host.
    #    Ohne diese Header sähe request.base_url nur die interne Adresse
    #    (http://ip:4000), die ein öffentlicher Client NICHT erreichen kann.
    fproto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    fhost = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host = fhost or request.headers.get("host") or request.url.netloc
    scheme = fproto or request.url.scheme or "http"
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/enroll/{token}", response_class=HTMLResponse)
async def enrollment_landing_page(token: str, request: Request):
    """
    Die eigentliche Download-Seite: zeigt Download-Button UND die
    Schritt-für-Schritt-Befehle für Linux/Windows direkt daneben an.
    """
    _require_valid_token(token)
    backend_url = _backend_url(request)

    html = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
      <meta charset="UTF-8">
      <title>RAPALLE.net RMM — Agent-Installation</title>
      <style>
        body {{ font-family: system-ui, sans-serif; background: #0c1420; color: #dbe4f0;
                max-width: 720px; margin: 40px auto; padding: 0 20px; line-height: 1.5; }}
        h1 {{ color: #2dd4bf; }}
        code, pre {{ background: #141d2b; padding: 10px 14px; border-radius: 8px;
                     display: block; overflow-x: auto; color: #7ff; }}
        .btn {{ display: inline-block; background: #2dd4bf; color: #0c1420; font-weight: 600;
                padding: 12px 22px; border-radius: 8px; text-decoration: none; margin: 10px 0; }}
        .note {{ color: #7f93ad; font-size: 14px; }}
        h3 {{ margin-top: 30px; }}
      </style>
    </head>
    <body>
      <h1>RAPALLE.net RMM — Agent installieren</h1>
      <p>Dieser Link installiert den Überwachungs-Agenten auf diesem Rechner
         und verbindet ihn automatisch mit deinem RMM-Dashboard.</p>

      <h3>Option A — Direkt herunterladen</h3>
      <a class="btn" id="dl" href="/enroll/{token}/agent.zip">Agent herunterladen (.zip)</a>
      <p class="note">Danach: entpacken, Anleitung in der beiliegenden README.txt befolgen.</p>

      <h3>Option B — Ein-Zeiler (Linux)</h3>
      <pre>curl -sSL {backend_url}/enroll/{token}/install.sh | bash</pre>

      <h3>Option B — Ein-Zeiler (Windows PowerShell, als Administrator)</h3>
      <pre>iwr {backend_url}/enroll/{token}/install.ps1 -UseBasicParsing | iex</pre>

      <p class="note">Dieser Link ist einmalig gültig und läuft nach der ersten
         erfolgreichen Verbindung des Agenten automatisch ab.</p>

      <script>
        // Nach Klick auf den Download-Button kurz Bescheid geben,
        // dass die Seite jetzt geschlossen werden kann.
        document.getElementById('dl').addEventListener('click', function () {{
          setTimeout(function () {{
            document.body.innerHTML = '<h1>Fertig!</h1><p>Der Download wurde gestartet. Du kannst dieses Fenster jetzt schließen.</p>';
            try {{ window.close(); }} catch (e) {{}}
          }}, 800);
        }});
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)


def _build_agent_zip_bytes(backend_url: str, enrollment_token: str = "") -> io.BytesIO:
    """Baut das Agent-ZIP inkl. vorausgefüllter .env (Backend-URL/Token)."""
    env_content = (
        f"BACKEND_URL={backend_url}\n"
        f"AGENT_TOKEN={AGENT_TOKEN}\n"
        f"ENROLLMENT_TOKEN={enrollment_token}\n"
        f"DEVICE_NAME=\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in AGENT_SOURCE_DIR.rglob("*"):
            if file_path.is_file() and ".env" not in file_path.name and "__pycache__" not in str(file_path):
                zf.write(file_path, arcname=f"agent/{file_path.relative_to(AGENT_SOURCE_DIR)}")
        zf.writestr("agent/.env", env_content)
        zf.writestr(
            "agent/README.txt",
            "1. BACKEND_URL in .env auf die echte Adresse deines Servers anpassen\n"
            "2. pip install -r requirements.txt\n"
            "3. python agent.py\n",
        )
    buffer.seek(0)
    return buffer


@router.get("/enroll/{token}/agent.zip")
async def download_agent_zip(token: str, request: Request):
    """Packt den Agent-Ordner inkl. einer vorausgefüllten .env-Datei in ein ZIP."""
    _require_valid_token(token)
    buffer = _build_agent_zip_bytes(_backend_url(request), token)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=rapalle-rmm-agent.zip"},
    )


@router.get("/enroll/{token}/install.sh", response_class=PlainTextResponse)
async def install_script_linux(token: str, request: Request):
    """Fertiger Bash-Einzeiler: installiert den Agenten inkl. systemd-Autostart (Linux)."""
    _require_valid_token(token)
    backend_url = _backend_url(request)

    script = f"""#!/bin/bash
# RAPALLE.net RMM - automatische Agent-Installation (Linux)
set -e
INSTALL_DIR="/opt/rapalle-rmm-agent"

# --- Systemvoraussetzungen automatisch installieren -----------------------
# Benötigt: python3, python3-venv (ensurepip!), python3-pip, unzip, curl.
# Wir erkennen den Paketmanager und installieren fehlende Pakete selbst, damit
# der bekannte Fehler "ensurepip is not available / install python3-venv"
# nicht mehr auftritt.
ensure_pkgs() {{
  # Python-Minor bestimmen (z.B. 3.13) für das passende venv-Paket auf Debian/Ubuntu.
  PYV="$(python3 -c 'import sys;print(f"{{sys.version_info.major}}.{{sys.version_info.minor}}")' 2>/dev/null || echo 3)"
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true
    export TERM="${{TERM:-dumb}}"
    sudo -E apt-get update -y -qq >/dev/null 2>&1 || true
    # Erst das versionsspezifische venv-Paket, sonst das generische.
    sudo -E apt-get install -y -qq "python${{PYV}}-venv" >/dev/null 2>&1 \
      || sudo -E apt-get install -y -qq python3-venv >/dev/null 2>&1 || true
    sudo -E apt-get install -y -qq python3-pip unzip curl >/dev/null 2>&1 || true
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 python3-pip unzip curl || true
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3 python3-pip unzip curl || true
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper install -y python3 python3-pip unzip curl || true
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm python python-pip unzip curl || true
  elif command -v apk >/dev/null 2>&1; then
    sudo apk add --no-cache python3 py3-pip unzip curl || true
  else
    echo "WARNUNG: Kein bekannter Paketmanager gefunden - stelle sicher, dass python3-venv, pip, unzip und curl installiert sind."
  fi
}}
ensure_pkgs

echo "Lade Agent herunter..."
sudo mkdir -p "$INSTALL_DIR"
curl -sSL "{backend_url}/enroll/{token}/agent.zip" -o /tmp/rapalle-agent.zip
sudo unzip -o /tmp/rapalle-agent.zip -d /tmp/rapalle-agent-extract
# Laufenden Agenten stoppen, bevor Dateien ersetzt werden (sonst bleibt die
# alte Version aktiv, bis der Dienst manuell neu startet).
sudo systemctl stop rapalle-agent 2>/dev/null || true
sudo cp -r /tmp/rapalle-agent-extract/agent/. "$INSTALL_DIR/"

# .env auf die echte Backend-Adresse setzen (der Rechner, der dieses Skript ausführt,
# kennt die Backend-Adresse ja schon, weil er sie gerade aufgerufen hat)
sudo sed -i "s#BACKEND_URL=.*#BACKEND_URL={backend_url}#" "$INSTALL_DIR/.env"

echo "Installiere Python-Abhängigkeiten..."
cd "$INSTALL_DIR"
# venv anlegen; falls das trotz Paketinstallation scheitert, ohne venv direkt
# ins System installieren (--break-system-packages für PEP-668-Distributionen).
if sudo python3 -m venv venv; then
  sudo ./venv/bin/python -m pip install --quiet --upgrade pip || true
  sudo ./venv/bin/pip install --quiet -r requirements.txt
  PYEXEC="$INSTALL_DIR/venv/bin/python"
else
  echo "venv nicht verfügbar - installiere Abhängigkeiten systemweit..."
  sudo python3 -m pip install --quiet --break-system-packages -r requirements.txt \
    || sudo python3 -m pip install --quiet -r requirements.txt
  PYEXEC="$(command -v python3)"
fi

echo "Richte Autostart-Dienst ein..."
sudo tee /etc/systemd/system/rapalle-agent.service > /dev/null <<EOF
[Unit]
Description=RAPALLE.net RMM Agent
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYEXEC $INSTALL_DIR/agent.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now rapalle-agent

echo "Fertig! Der Agent läuft jetzt und verbindet sich mit {backend_url}"
echo "Status prüfen mit: sudo systemctl status rapalle-agent"
"""
    return PlainTextResponse(script, media_type="text/x-sh")


@router.get("/enroll/{token}/install.ps1", response_class=PlainTextResponse)
async def install_script_windows(token: str, request: Request):
    """Fertiges PowerShell-Skript: installiert den Agenten inkl. Autostart (Windows)."""
    _require_valid_token(token)
    backend_url = _backend_url(request)

    script = f"""# RAPALLE.net RMM - automatische Agent-Installation (Windows)
# Als Administrator ausfuehren!
$ErrorActionPreference = "Stop"
$InstallDir = "C:\\Program Files\\RapalleRmmAgent"

# --- 1. Pruefen ob Python vorhanden ist, sonst automatisch installieren ---
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {{
    Write-Host "Python nicht gefunden - installiere es automatisch..." -ForegroundColor Yellow
    try {{
        # Bevorzugt ueber winget (auf aktuellen Windows-Versionen vorhanden)
        if (Get-Command winget -ErrorAction SilentlyContinue) {{
            winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        }} else {{
            # Fallback: offiziellen Installer herunterladen und still installieren
            $pyUrl = "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe"
            Invoke-WebRequest -Uri $pyUrl -OutFile "$env:TEMP\\python-setup.exe"
            Start-Process -Wait -FilePath "$env:TEMP\\python-setup.exe" -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1"
        }}
        # PATH der aktuellen Sitzung aktualisieren
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    }} catch {{
        Write-Host "Automatische Python-Installation fehlgeschlagen." -ForegroundColor Red
        Write-Host "Bitte Python manuell installieren: https://www.python.org/downloads/" -ForegroundColor Red
        Write-Host "(Beim Installieren 'Add Python to PATH' anhaken!) Danach dieses Skript erneut ausfuehren."
        exit 1
    }}
}}

# --- 2. Agent herunterladen und entpacken ---
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Write-Host "Lade Agent herunter..."
Invoke-WebRequest -Uri "{backend_url}/enroll/{token}/agent.zip" -OutFile "$env:TEMP\\rapalle-agent.zip"
if (Test-Path "$env:TEMP\\rapalle-agent-extract") {{ Remove-Item -Recurse -Force "$env:TEMP\\rapalle-agent-extract" }}
Expand-Archive -Path "$env:TEMP\\rapalle-agent.zip" -DestinationPath "$env:TEMP\\rapalle-agent-extract" -Force

# --- Laufenden Agenten STOPPEN, bevor Dateien ersetzt werden ---------------
# WICHTIG: Sonst laeuft nach dem Update weiter die ALTE agent.py im Speicher
# (neue Funktionen wie das interaktive Terminal fehlen dann). Erst Task
# anhalten, dann alle pythonw/python-Prozesse killen, die agent.py ausfuehren.
Write-Host "Stoppe evtl. laufenden Agenten..."
try {{ Stop-ScheduledTask -TaskName "RapalleRmmAgent" -ErrorAction SilentlyContinue }} catch {{}}
try {{
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object {{ $_.CommandLine -like "*agent.py*" }} |
        ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
}} catch {{}}
Start-Sleep -Seconds 2

Copy-Item -Path "$env:TEMP\\rapalle-agent-extract\\agent\\*" -Destination $InstallDir -Recurse -Force

# .env auf die echte Backend-Adresse setzen
(Get-Content "$InstallDir\\.env") -replace 'BACKEND_URL=.*', 'BACKEND_URL={backend_url}' | Set-Content "$InstallDir\\.env"

# --- 3. Python-Abhaengigkeiten installieren ---
Write-Host "Installiere Python-Abhaengigkeiten (kann 1-2 Minuten dauern)..."
python -m venv "$InstallDir\\venv"
& "$InstallDir\\venv\\Scripts\\python.exe" -m pip install --upgrade pip --quiet
& "$InstallDir\\venv\\Scripts\\pip.exe" install -r "$InstallDir\\requirements.txt"
if ($LASTEXITCODE -ne 0) {{
    Write-Host "FEHLER bei der Paket-Installation! Der Agent kann so nicht starten." -ForegroundColor Red
    exit 1
}}

# --- 4. Autostart einrichten (versteckt, in der Benutzer-Sitzung) ---
# WICHTIG: Der Agent muss in der INTERAKTIVEN Benutzer-Sitzung laufen, nicht als
# SYSTEM - sonst kann er den Bildschirm nicht erfassen ("BitBlt: Access denied").
# Deshalb: Trigger = AtLogOn des aktuell angemeldeten Benutzers, RunLevel Highest.
# pythonw.exe = Python OHNE Konsolenfenster -> laeuft unsichtbar im Hintergrund.
Write-Host "Richte Autostart ein (unsichtbar, in der Benutzer-Sitzung)..."
$CurrentUser = "$env:USERDOMAIN\\$env:USERNAME"
$Action = New-ScheduledTaskAction -Execute "$InstallDir\\venv\\Scripts\\pythonw.exe" -Argument "`"$InstallDir\\agent.py`"" -WorkingDirectory $InstallDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "RapalleRmmAgent" -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null

# --- 5. Agent SOFORT starten (versteckt, in der aktuellen Sitzung) ---
Start-ScheduledTask -TaskName "RapalleRmmAgent"

# --- 6. Elevated Wartungs-Tasks (Update/Uninstall als SYSTEM, per Event) ---
# Damit funktionieren Dashboard-Update/-Uninstall auch, obwohl der Agent selbst
# in der (nicht-privilegierten) Benutzersitzung laeuft.
Write-Host "Richte elevated Wartungs-Tasks (Update/Uninstall) ein..."
try {{ iwr "{backend_url}/agent-dist/elevate.ps1" -UseBasicParsing | iex }} catch {{ Write-Host "  (Wartungs-Tasks konnten nicht eingerichtet werden: $_)" }}

Write-Host ""
Write-Host "=== FERTIG! ===" -ForegroundColor Green
Write-Host "Der Agent laeuft jetzt unsichtbar im Hintergrund und startet automatisch mit Windows."
Write-Host "Du kannst dieses Fenster schliessen - der Agent laeuft weiter."
Write-Host "Der Client sollte in wenigen Sekunden im Dashboard erscheinen."
Write-Host ""
Write-Host "(Log-Datei bei Problemen: $InstallDir\\agent.log)"
"""
    return PlainTextResponse(script, media_type="text/plain")


# ==========================================================================
# TOKENLOSE AGENT-DISTRIBUTION: Update & Uninstall
# --------------------------------------------------------------------------
# Diese Routen liegen bewusst unter /agent-dist/... (NICHT unter /enroll/{token},
# sonst würde "agent-dist" als Token interpretiert). Sie brauchen keinen
# Onboarding-Token, weil ein bereits installierter Agent seine Identität über
# die Datei ".device-id" behält - ein Update/Deinstall ändert daran nichts.
# Der Agent ruft diese Skripte selbst auf, wenn er die Events "update-agent"
# bzw. "uninstall-agent" empfängt (siehe agent.py, _run_dist_command).
#
# WICHTIG (Zuverlässigkeit):
#  - Update MUSS den Dienst am Ende NEU STARTEN (Autostart-Unit ggf. neu
#    schreiben, dann restart/Start-ScheduledTask).
#  - Uninstall läuft SYNCHRON (kein Hintergrund-Job): Der Agent startet das
#    Skript in einem eigenen systemd-Scope bzw. als losgelöster Prozess, sodass
#    es den Stop des eigenen Dienstes überlebt und die Dateien wirklich löscht.
# ==========================================================================

@router.get("/agent-dist/agent.zip")
async def agent_dist_zip(request: Request):
    """Aktuelles Agent-ZIP OHNE Onboarding-Token (für Selbst-Update)."""
    buffer = _build_agent_zip_bytes(_backend_url(request), "")
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=rapalle-rmm-agent.zip"},
    )


@router.get("/agent-dist/update.sh", response_class=PlainTextResponse)
async def agent_update_sh(request: Request):
    """
    Aktualisiert den Agenten (Linux) und startet den Autostart-Dienst NEU.
    Die systemd-Unit wird sicherheitshalber neu geschrieben, damit der Neustart
    auch dann klappt, wenn die Unit fehlte/veraltet war. Bestehende .device-id
    bleibt erhalten -> gleiche Client-Identität.
    """
    backend_url = _backend_url(request)
    script = f"""#!/bin/bash
# RAPALLE.net RMM - Agent-Update (Linux)
set -e
INSTALL_DIR="/opt/rapalle-rmm-agent"
echo "Lade neueste Agent-Version..."
curl -sSL "{backend_url}/agent-dist/agent.zip" -o /tmp/rapalle-agent.zip
sudo mkdir -p "$INSTALL_DIR"
rm -rf /tmp/rapalle-agent-extract
mkdir -p /tmp/rapalle-agent-extract
unzip -o /tmp/rapalle-agent.zip -d /tmp/rapalle-agent-extract >/dev/null

# Laufenden Agenten stoppen, damit die alte agent.py nicht weiterlaeuft.
sudo systemctl stop rapalle-agent 2>/dev/null || true
sudo pkill -f "$INSTALL_DIR/agent.py" 2>/dev/null || true

# Alle Dateien AUSSER .env ersetzen (.env behaelt Konfiguration).
sudo find /tmp/rapalle-agent-extract/agent -maxdepth 1 -type f ! -name '.env' -exec cp {{}} "$INSTALL_DIR/" ';'
# .env anlegen, falls noch keine da ist; danach BACKEND_URL aktualisieren.
if [ ! -f "$INSTALL_DIR/.env" ] && [ -f /tmp/rapalle-agent-extract/agent/.env ]; then
  sudo cp /tmp/rapalle-agent-extract/agent/.env "$INSTALL_DIR/.env"
fi
if [ -f "$INSTALL_DIR/.env" ]; then
  sudo sed -i "s#BACKEND_URL=.*#BACKEND_URL={backend_url}#" "$INSTALL_DIR/.env"
fi

cd "$INSTALL_DIR"
# Python-Interpreter bestimmen (venv bevorzugt) und Abhaengigkeiten nachziehen.
if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
  PYEXEC="$INSTALL_DIR/venv/bin/python"
  sudo ./venv/bin/pip install --quiet -r requirements.txt || true
else
  PYEXEC="$(command -v python3)"
fi

# Autostart-Unit (neu) schreiben -> garantiert vorhanden fuer den Neustart.
sudo tee /etc/systemd/system/rapalle-agent.service > /dev/null <<UNIT
[Unit]
Description=RAPALLE.net RMM Agent
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYEXEC $INSTALL_DIR/agent.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable rapalle-agent 2>/dev/null || true
# Marker anlegen: nach dem Neustart meldet der Agent dem Backend "updated".
sudo touch "$INSTALL_DIR/.updated"
# NEU STARTEN (nicht nur start) - bringt den Agenten mit neuer Version hoch.
sudo systemctl restart rapalle-agent
echo "Update fertig - Agent neu gestartet."
"""
    return PlainTextResponse(script, media_type="text/x-sh")


@router.get("/agent-dist/update.ps1", response_class=PlainTextResponse)
async def agent_update_ps1(request: Request):
    """Aktualisiert den Agenten (Windows) und startet den Autostart-Task NEU
    (Task wird bei Bedarf neu registriert)."""
    backend_url = _backend_url(request)
    script = f"""# RAPALLE.net RMM - Agent-Update (Windows) - als Administrator ausfuehren
$ErrorActionPreference = "Stop"
$InstallDir = "C:\\Program Files\\RapalleRmmAgent"
Write-Host "Lade neueste Agent-Version..."
Invoke-WebRequest -Uri "{backend_url}/agent-dist/agent.zip" -OutFile "$env:TEMP\\rapalle-agent.zip"
if (Test-Path "$env:TEMP\\rapalle-agent-extract") {{ Remove-Item -Recurse -Force "$env:TEMP\\rapalle-agent-extract" }}
Expand-Archive -Path "$env:TEMP\\rapalle-agent.zip" -DestinationPath "$env:TEMP\\rapalle-agent-extract" -Force

# Laufenden Agenten stoppen (Task + Prozesse), damit die alte agent.py endet.
try {{ Stop-ScheduledTask -TaskName "RapalleRmmAgent" -ErrorAction SilentlyContinue }} catch {{}}
try {{
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object {{ $_.CommandLine -like "*RapalleRmmAgent*" -or $_.CommandLine -like "*agent.py*" }} |
        ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
}} catch {{}}
Start-Sleep -Seconds 2

# Alle Dateien AUSSER .env ersetzen (behaelt Konfiguration/Device-ID).
Get-ChildItem "$env:TEMP\\rapalle-agent-extract\\agent" -File | Where-Object {{ $_.Name -ne ".env" }} |
    ForEach-Object {{ Copy-Item $_.FullName -Destination $InstallDir -Force }}
if (Test-Path "$InstallDir\\.env") {{
    (Get-Content "$InstallDir\\.env") -replace 'BACKEND_URL=.*', 'BACKEND_URL={backend_url}' | Set-Content "$InstallDir\\.env"
}}
if (Test-Path "$InstallDir\\venv\\Scripts\\pip.exe") {{
    & "$InstallDir\\venv\\Scripts\\pip.exe" install -r "$InstallDir\\requirements.txt" --quiet
}}

# Autostart-Task neu registrieren, falls er fehlt.
$task = Get-ScheduledTask -TaskName "RapalleRmmAgent" -ErrorAction SilentlyContinue
if (-not $task) {{
    $CurrentUser = "$env:USERDOMAIN\\$env:USERNAME"
    $Action = New-ScheduledTaskAction -Execute "$InstallDir\\venv\\Scripts\\pythonw.exe" -Argument "`"$InstallDir\\agent.py`"" -WorkingDirectory $InstallDir
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
    $Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Highest
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "RapalleRmmAgent" -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
}}

# NEU STARTEN.
New-Item -ItemType File -Force "$InstallDir\\.updated" | Out-Null
Start-ScheduledTask -TaskName "RapalleRmmAgent"
Write-Host "Update fertig - Agent neu gestartet."
"""
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/agent-dist/elevate.ps1", response_class=PlainTextResponse)
async def agent_elevate_ps1(request: Request):
    """
    Richtet EINMALIG (elevated auszuführen) zwei vorautorisierte SYSTEM-Tasks ein,
    die per Windows-Event ausgelöst werden:
        RapalleRmmUpdate    (EventID 812)  -> update.ps1
        RapalleRmmUninstall (EventID 811)  -> uninstall.ps1
    Danach kann der (nicht-privilegierte) Agent Update/Uninstall auslösen, indem
    er nur ein Event schreibt - die Aufgabenplanung führt das Skript dann als
    SYSTEM (elevated) aus. Der Agent selbst bleibt in der Benutzersitzung (damit
    Remote-Screen weiter funktioniert).

    Aufruf auf dem Client (als Administrator):
        powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr '<BACKEND>/agent-dist/elevate.ps1' -UseBasicParsing | iex"
    """
    backend_url = _backend_url(request)
    template = r'''$ErrorActionPreference = "Continue"
$Backend = "__BACKEND__"

Write-Host "RapalleRMM: richte elevated Update/Uninstall (SYSTEM, per Event ausgeloest) ein..."

# Event-Quelle anlegen (idempotent). Der Agent schreibt spaeter Events dieser
# Quelle; die SYSTEM-Tasks werden dadurch getriggert.
try {
    if (-not [System.Diagnostics.EventLog]::SourceExists("RapalleRMM")) {
        New-EventLog -LogName Application -Source "RapalleRMM" -ErrorAction Stop
        Write-Host "  Event-Quelle 'RapalleRMM' angelegt."
    } else {
        Write-Host "  Event-Quelle 'RapalleRMM' vorhanden."
    }
} catch {
    Write-Host "  WARNUNG: Event-Quelle konnte nicht angelegt werden: $_"
}

function Register-RmmSystemTask($name, $eventId, $scriptName) {
    # Task-Definition als XML (robust ueber alle Windows-Versionen; kein CIM noetig).
    # SYSTEM = SID S-1-5-18. EventTrigger horcht auf Application-Events der Quelle
    # 'RapalleRMM' mit der passenden EventID.
    $argLine = "-NoProfile -ExecutionPolicy Bypass -Command ""iwr '$Backend/agent-dist/$scriptName' -UseBasicParsing | iex"""
    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>RapalleRMM $name</Description>
  </RegistrationInfo>
  <Triggers>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="Application"&gt;&lt;Select Path="Application"&gt;*[System[Provider[@Name='RapalleRMM'] and (EventID=$eventId)]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription>
    </EventTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$argLine</Arguments>
    </Exec>
  </Actions>
</Task>
"@
    $tmp = Join-Path $env:TEMP ("rmm_task_" + $name + ".xml")
    [System.IO.File]::WriteAllText($tmp, $xml, [System.Text.Encoding]::Unicode)
    # Vorhandenen Task entfernen, dann neu aus XML anlegen (als SYSTEM).
    schtasks /delete /tn $name /f 2>$null | Out-Null
    $out = schtasks /create /tn $name /xml "$tmp" /ru "SYSTEM" /f 2>&1
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    # Verifizieren.
    schtasks /query /tn $name 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  -> Task '$name' (EventID $eventId) OK."
        return $true
    } else {
        Write-Host "  -> Task '$name' FEHLER: $out"
        return $false
    }
}

$ok1 = Register-RmmSystemTask "RapalleRmmUpdate" 812 "update.ps1"
$ok2 = Register-RmmSystemTask "RapalleRmmUninstall" 811 "uninstall.ps1"

Write-Host ""
if ($ok1 -and $ok2) {
    Write-Host "FERTIG. Dashboard-Update/-Uninstall funktionieren jetzt auch bei NICHT-Admin-Agent." -ForegroundColor Green
} else {
    Write-Host "ACHTUNG: Mindestens ein Task konnte nicht angelegt werden. Bitte dieses Fenster ALS ADMINISTRATOR ausfuehren." -ForegroundColor Yellow
}
Write-Host "Pruefen mit:  schtasks /query /tn RapalleRmmUninstall"
'''
    script = template.replace("__BACKEND__", backend_url)
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/agent-dist/uninstall.sh", response_class=PlainTextResponse)
async def agent_uninstall_sh(request: Request):
    """
    Deinstalliert den Agenten (Linux) SYNCHRON: Dienst stoppen/entfernen,
    Prozesse killen, dann Programmordner löschen. Läuft in einem eigenen
    systemd-Scope (siehe agent.py), daher überlebt das Skript den Stop des
    eigenen Dienstes und löscht die Dateien tatsächlich. KEIN Hintergrund-Job.
    """
    script = """#!/bin/bash
# RAPALLE.net RMM - Agent-Deinstallation (Linux)
# Laeuft in einem eigenen systemd-Scope (siehe agent.py) und ueberlebt daher
# den Stop des eigenen Dienstes. Reihenfolge: Dienst stoppen/entfernen ->
# Prozesse killen -> Programmordner loeschen (mit Wiederholung).
INSTALL_DIR="/opt/rapalle-rmm-agent"

echo "1) Stoppe und entferne Agent-Dienst..."
sudo systemctl stop rapalle-agent 2>/dev/null || true
sudo systemctl disable rapalle-agent 2>/dev/null || true
sudo systemctl kill rapalle-agent 2>/dev/null || true
sudo rm -f /etc/systemd/system/rapalle-agent.service
sudo systemctl daemon-reload 2>/dev/null || true
sudo systemctl reset-failed rapalle-agent 2>/dev/null || true

echo "2) Beende Agent-Prozesse..."
# Mehrere Muster, damit der Prozess sicher getroffen wird (venv-Pfad, agent.py,
# Installationsordner). Zwei Runden, dann Kontrolle.
for i in 1 2 3; do
  sudo pkill -9 -f "$INSTALL_DIR/agent.py" 2>/dev/null || true
  sudo pkill -9 -f "$INSTALL_DIR" 2>/dev/null || true
  sudo pkill -9 -f "rapalle-rmm-agent" 2>/dev/null || true
  sleep 1
  if ! pgrep -f "$INSTALL_DIR/agent.py" >/dev/null 2>&1; then break; fi
done

echo "3) Loesche alle Agent-Daten..."
# Bis zu 5 Versuche, falls eine Datei noch kurz gehalten wird.
for i in 1 2 3 4 5; do
  sudo rm -rf "$INSTALL_DIR" 2>/dev/null || true
  [ ! -e "$INSTALL_DIR" ] && break
  sleep 1
done

if [ -e "$INSTALL_DIR" ]; then
  echo "WARNUNG: $INSTALL_DIR konnte nicht vollstaendig entfernt werden."
else
  echo "Agent deinstalliert - alle Daten entfernt."
fi
"""
    return PlainTextResponse(script, media_type="text/x-sh")


@router.get("/agent-dist/uninstall.ps1", response_class=PlainTextResponse)
async def agent_uninstall_ps1(request: Request):
    """
    Deinstalliert den Agenten (Windows) SYNCHRON: Task entfernen, Prozesse
    killen, dann Programmordner löschen (kein Start-Job, der beim Beenden der
    Shell abgebrochen würde).
    """
    script = """# RAPALLE.net RMM - Agent-Deinstallation (Windows)
# Wird bevorzugt als SYSTEM-Task ausgefuehrt (elevated). Reihenfolge:
# Autostart entfernen -> Prozesse hart beenden -> alle Daten loeschen.
$ErrorActionPreference = "SilentlyContinue"
$InstallDir = "C:\\Program Files\\RapalleRmmAgent"

Write-Host "1) Stoppe und entferne Agent-Autostart..."
schtasks /end /tn "RapalleRmmAgent" 2>$null | Out-Null
schtasks /delete /tn "RapalleRmmAgent" /f 2>$null | Out-Null
Unregister-ScheduledTask -TaskName "RapalleRmmAgent" -Confirm:$false

Write-Host "2) Beende Agent-Prozesse..."
for ($i = 0; $i -lt 4; $i++) {
    $ids = @()
    $ids += (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like "$InstallDir*" }).Id
    $ids += (Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like "*RapalleRmmAgent*" -or $_.CommandLine -like "*agent.py*" -or
        ($_.ExecutablePath -and $_.ExecutablePath -like "$InstallDir*")
    }).ProcessId
    $ids = $ids | Where-Object { $_ } | Sort-Object -Unique
    if (-not $ids) { break }
    foreach ($id in $ids) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        cmd /c "taskkill /PID $id /F /T" 2>$null | Out-Null
    }
    Start-Sleep -Seconds 2
}
Start-Sleep -Seconds 2

Write-Host "3) Loesche alle Agent-Daten..."
for ($i = 0; $i -lt 6; $i++) {
    if (-not (Test-Path $InstallDir)) { break }
    # Schreibschutz/Attribute entfernen, dann mit zwei Methoden loeschen.
    cmd /c "attrib -r -s -h `"$InstallDir\\*.*`" /s /d" 2>$null | Out-Null
    Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) { cmd /c "rmdir /s /q `"$InstallDir`"" 2>$null | Out-Null }
    Start-Sleep -Seconds 2
}

# Falls per SYSTEM-Task/Event gestartet: Hilfstasks + Event-Quelle wieder entfernen.
schtasks /delete /tn "RapalleRmmUninstall" /f 2>$null | Out-Null
schtasks /delete /tn "RapalleRmmUpdate" /f 2>$null | Out-Null
try { Remove-EventLog -Source "RapalleRMM" -ErrorAction SilentlyContinue } catch {}

if (Test-Path $InstallDir) {
    Write-Host "WARNUNG: $InstallDir konnte nicht vollstaendig entfernt werden (Datei evtl. noch gesperrt). Nach einem Neustart erneut versuchen."
} else {
    Write-Host "Agent deinstalliert - alle Daten entfernt."
}
"""
    return PlainTextResponse(script, media_type="text/plain")

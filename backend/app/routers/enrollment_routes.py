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
import zipfile
from pathlib import Path

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
async def create_enrollment_token(body: CreateEnrollmentBody, user: dict = Depends(get_current_user)):
    """Erzeugt einen neuen Onboarding-Token und gibt fertige URLs dafür zurück."""
    require_admin(user)
    token = db.create_enrollment_token(body.tenant_id, body.location_id, body.client_name)
    return {
        "token": token,
        "landing_url": f"/enroll/{token}",
        "install_sh_url": f"/enroll/{token}/install.sh",
        "install_ps1_url": f"/enroll/{token}/install.ps1",
        "download_url": f"/enroll/{token}/agent.zip",
    }


def _require_valid_token(token: str) -> dict:
    row = db.get_enrollment_token(token)
    if not row:
        raise HTTPException(404, "Unbekannter oder abgelaufener Onboarding-Link")
    return row


def _backend_url(request: Request) -> str:
    """
    Liefert die Basis-Adresse, die in Install-Befehle/Skripte eingebaut wird.
    Ist in den Einstellungen (Allgemein) eine feste Server-Adresse hinterlegt,
    wird diese benutzt - sonst wird sie automatisch aus dem Request abgeleitet
    (das ergab bisher oft "localhost", wenn das Dashboard lokal geöffnet wurde).
    """
    configured = (db.get_setting("server_url") or "").strip()
    if configured:
        return configured.rstrip("/")
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


@router.get("/enroll/{token}/agent.zip")
async def download_agent_zip(token: str):
    """Packt den Agent-Ordner inkl. einer vorausgefüllten .env-Datei in ein ZIP."""
    _require_valid_token(token)

    env_content = (
        f"BACKEND_URL=http://ANPASSEN:4000\n"
        f"AGENT_TOKEN={AGENT_TOKEN}\n"
        f"ENROLLMENT_TOKEN={token}\n"
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

echo "Lade Agent herunter..."
sudo mkdir -p "$INSTALL_DIR"
curl -sSL "{backend_url}/enroll/{token}/agent.zip" -o /tmp/rapalle-agent.zip
sudo unzip -o /tmp/rapalle-agent.zip -d /tmp/rapalle-agent-extract
sudo cp -r /tmp/rapalle-agent-extract/agent/. "$INSTALL_DIR/"

# .env auf die echte Backend-Adresse setzen (der Rechner, der dieses Skript ausführt,
# kennt die Backend-Adresse ja schon, weil er sie gerade aufgerufen hat)
sudo sed -i "s#BACKEND_URL=.*#BACKEND_URL={backend_url}#" "$INSTALL_DIR/.env"

echo "Installiere Python-Abhängigkeiten..."
cd "$INSTALL_DIR"
sudo python3 -m venv venv
sudo ./venv/bin/pip install --quiet -r requirements.txt

echo "Richte Autostart-Dienst ein..."
sudo tee /etc/systemd/system/rapalle-agent.service > /dev/null <<EOF
[Unit]
Description=RAPALLE.net RMM Agent
After=network.target

[Service]
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/agent.py
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

Write-Host ""
Write-Host "=== FERTIG! ===" -ForegroundColor Green
Write-Host "Der Agent laeuft jetzt unsichtbar im Hintergrund und startet automatisch mit Windows."
Write-Host "Du kannst dieses Fenster schliessen - der Agent laeuft weiter."
Write-Host "Der Client sollte in wenigen Sekunden im Dashboard erscheinen."
Write-Host ""
Write-Host "(Log-Datei bei Problemen: $InstallDir\\agent.log)"
"""
    return PlainTextResponse(script, media_type="text/plain")

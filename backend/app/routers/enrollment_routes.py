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
from app.auth import get_current_user, require_admin, require_perm
from app.config import AGENT_TOKEN
# Sprache der erzeugten Skripte: Die laufen SPAETER auf einem fremden
# Rechner - dort gibt es weder Anmeldung noch Servereinstellung. Die
# Sprache muss deshalb beim Erzeugen feststehen (?lang=de|en).
from app.i18n import t as _t, _norm as _lang, server_lang

router = APIRouter(tags=["enrollment"])

# Pfad zum Agent-Quellcode-Ordner (wird für den ZIP-Download eingepackt)
AGENT_SOURCE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "agent"


class CreateEnrollmentBody(BaseModel):
    tenant_id: str | None = None
    location_id: str | None = None
    client_name: str | None = None


@router.post("/api/enrollment/tokens")
def create_enrollment_token(body: CreateEnrollmentBody, request: Request, user: dict = Depends(get_current_user)):
    """
    Erzeugt einen neuen Onboarding-Token und gibt fertige URLs dafür zurück.

    WICHTIG: Die URLs werden hier ABSOLUT gebaut (via _backend_url: Settings
    server_url/server_domain/server_host haben Vorrang). Vorher hat das
    Frontend window.location.origin vorangestellt - wer das Dashboard über
    localhost/127.0.0.1 öffnet, bekam damit unbrauchbare "localhost"-Links.
    """
    # Eigenes Recht statt Admin-Zwang: 'add_client' erlaubt das Aufnehmen
    # neuer Clients, ohne sonstige Admin-Befugnisse zu vergeben.
    require_perm(user, "add_client")
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


@router.get("/api/public-base")
def public_base(request: Request, user: dict = Depends(get_current_user)):
    """
    Die KANONISCHE Adresse dieser Installation - also die, unter der Clients
    und Netzlaufwerke den Server erreichen sollen.

    Warum das nicht im Browser bestimmt werden kann: `location.host` ist nur
    die Adresse, ueber die der Admin GERADE zufaellig zugreift. Wer das
    Dashboard intern per IP oeffnet, bekaeme im Relay-Manager eine WebDAV-
    Adresse, die von aussen niemand erreicht - und in den Installations-
    befehlen eine, die der neue Client nicht kennt. Hier gilt dieselbe
    Reihenfolge wie fuer die Installations-Skripte (siehe _backend_url):
    konfigurierte Server-URL, dann Domain/Host + Port, dann die Anfrage
    selbst (inklusive X-Forwarded-*).

    Absichtlich nur `get_current_user` und kein Admin-Recht: Auch normale
    Benutzer sehen Relay-Adressen und brauchen deshalb den richtigen Wert.
    """
    base = _backend_url(request)
    parts = urlsplit(base)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return {
        "base_url": base,
        "scheme": parts.scheme,
        "host": parts.hostname or "",
        "port": port,
        # netloc enthaelt den Port nur, wenn er nicht der Standard ist - genau
        # das, was man fuer eine anzeigbare Adresse braucht.
        "netloc": parts.netloc,
        # Ist die Adresse geraten (kein server_url/server_domain gesetzt)?
        # Das Dashboard kann darauf hinweisen, statt eine falsche Adresse
        # kommentarlos als Wahrheit auszugeben.
        "configured": bool((db.get_setting("server_url") or "").strip()
                           or (db.get_setting("server_domain") or "").strip()
                           or (db.get_setting("server_host") or "").strip()),
    }


@router.get("/enroll/{token}", response_class=HTMLResponse)
def enrollment_landing_page(token: str, request: Request):
    """
    Die eigentliche Download-Seite: zeigt Download-Button UND die
    Schritt-für-Schritt-Befehle für Linux/Windows direkt daneben an.
    """
    _require_valid_token(token)
    backend_url = _backend_url(request)

    # Fertige Installationspakete (falls lokal gebaut) direkt mit anbieten -
    # der neue Rechner hat ja keinen Zugang zum Dashboard.
    pkgs = _installer_entries()
    if pkgs:
        rows = []
        for p in pkgs:
            hint = _installer_hint(p["name"], token, backend_url)
            rows.append(
                f'<p><a class="btn" href="/enroll/{token}/installer/{p["name"]}">'
                f'{p["icon"]} {p["label"]} ({p["size"] // 1024} KB)</a><br>'
                f'<code>{hint}</code></p>'
            )
        packages_block = ("<h3>Option C — Fertiges Installationspaket</h3>"
                          + "".join(rows))
    else:
        packages_block = ""

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

      {packages_block}

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
        # Sprache der Dialoge AM GERAET (Zustimmung zum Remote-Bildschirm).
        # Das ist NICHT die Dashboard-Sprache: den Dialog sieht die Person vor
        # dem Geraet, und mehrere Dashboard-Benutzer mit verschiedenen Sprachen
        # koennen dasselbe Geraet ansehen. de|en, Voreinstellung de.
        f"AGENT_LANG=de\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in AGENT_SOURCE_DIR.rglob("*"):
            if file_path.is_file() and ".env" not in file_path.name and "__pycache__" not in str(file_path):
                zf.write(file_path, arcname=f"agent/{file_path.relative_to(AGENT_SOURCE_DIR)}")
        zf.writestr("agent/.env", env_content)
        # Logo mitliefern: logo_r.png (bevorzugt die Branding-Version) landet
        # als agent/logo.png neben agent.py und wird vom Tkinter-Zustimmungs-
        # fenster als Icon/Logo benutzt. Hinweis: favicon.ico ist in Wahrheit
        # ein PNG und wird von Tk-iconbitmap nicht gelesen (blankes Icon).
        _root = AGENT_SOURCE_DIR.parent
        for _cand in (_root / "backend" / "branding" / "logo_r.png",
                      _root / "frontend" / "images" / "logo_r.png"):
            if _cand.is_file():
                zf.write(_cand, arcname="agent/logo.png")
                break
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
def install_script_linux(token: str, request: Request, lang: str = ""):
    """Fertiger Bash-Einzeiler: installiert den Agenten inkl. systemd-Autostart (Linux)."""
    _require_valid_token(token)
    backend_url = _backend_url(request)

    # Sprache dieses Skripts: ?lang=de|en, sonst die Server-Sprache.
    _L = _lang(lang) if lang else server_lang()
    def L(key, **kw):
        return _t(key, _L, **kw)

    script = f"""#!/bin/bash
# RAPALLE.net RMM - automatische Agent-Installation (Linux)
set -e
INSTALL_DIR="/opt/rapalle-rmm-agent"

# --- Rootrechte: notfalls SELBST ueber sudo neu starten -------------------
# Wird das Skript aus einer Pipe gelesen ("curl ... | bash"), gibt es kein $0
# zum Neustarten - dann nur ein klarer Hinweis. Ansonsten starten wir uns
# selbst mit sudo neu ("-E" haelt RMM_*-Variablen am Leben).
if [ "$(id -u)" != "0" ]; then
  if [ -f "$0" ] && command -v sudo >/dev/null 2>&1; then
    echo "Starte mit Rootrechten neu (sudo) ..."
    exec sudo -E "$0" "$@"
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Bitte als root ausfuehren (sudo ist nicht installiert)."; exit 1
  fi
  echo "Hinweis: Aufruf ohne Rootrechte - die einzelnen Schritte nutzen sudo."
fi

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
def install_script_windows(token: str, request: Request, lang: str = ""):
    """Fertiges PowerShell-Skript: installiert den Agenten inkl. Autostart (Windows)."""
    _require_valid_token(token)
    backend_url = _backend_url(request)

    # Sprache dieses Skripts: ?lang=de|en, sonst die Server-Sprache.
    _L = _lang(lang) if lang else server_lang()
    def L(key, **kw):
        return _t(key, _L, **kw)

    script = f"""# RAPALLE.net RMM - automatische Agent-Installation (Windows)
$ErrorActionPreference = "Stop"
$InstallDir = "C:\\Program Files\\RapalleRmmAgent"

# --- Adminrechte: notfalls SELBST per UAC neu starten ---------------------
# Wichtig fuer den Ein-Zeiler "iwr ... | iex": dabei gibt es keine Skriptdatei,
# die man neu starten koennte. Deshalb laden wir das Skript in der erhoehten
# Sitzung einfach noch einmal vom Server.
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    Write-Host "Starte mit Administratorrechten neu (UAC-Abfrage) ..." -ForegroundColor Yellow
    $cmd = "iwr '{backend_url}/enroll/{token}/install.ps1' -UseBasicParsing | iex"
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
    try {{
        $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -PassThru -Wait `
             -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $enc)
        exit $p.ExitCode
    }} catch {{
        Write-Host "Rechteerhoehung abgelehnt - bitte PowerShell als Administrator oeffnen." -ForegroundColor Red
        exit 1
    }}
}}

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

# --- 4. Autostart als DIENST einrichten (SYSTEM, beim Systemstart) ---
# WICHTIG (Server!): Frueher lief der Task unter dem installierenden Benutzer
# mit Trigger "AtLogOn". Auf Servern, an denen sich niemand (oder ein anderer
# Benutzer) anmeldet, startete der Agent dadurch NIE.
# Jetzt: Principal = SYSTEM (LogonType ServiceAccount), Trigger = AtStartup.
# Der Agent laeuft damit ab dem Boot - ganz ohne Anmeldung.
#
# Bildschirmaufnahme: SYSTEM sitzt in Session 0 und hat keinen Desktop. Der
# Agent startet deshalb bei Bedarf selbst einen Helfer-Prozess in der aktiven
# Benutzersitzung (agent.py --screen-helper, siehe session_bridge.py). Nur
# SYSTEM darf das (WTSQueryUserToken) - der Dienst-Betrieb ist also sogar
# Voraussetzung dafuer.
# pythonw.exe = Python OHNE Konsolenfenster -> laeuft unsichtbar im Hintergrund.
Write-Host "Richte Autostart als Dienst ein (SYSTEM, startet beim Booten)..."
$Action = New-ScheduledTaskAction -Execute "$InstallDir\\venv\\Scripts\\pythonw.exe" -Argument "`"$InstallDir\\agent.py`"" -WorkingDirectory $InstallDir
# Zwei Trigger: beim Systemstart + alle 5 Minuten als Wiederanlauf-Netz.
# MultipleInstances=IgnoreNew verhindert dabei doppelte Agenten.
$TrigBoot  = New-ScheduledTaskTrigger -AtStartup
$TrigWatch = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)  # 0 = KEIN Zeitlimit! Ohne das killt der Task-Scheduler die Aufgabe nach 72h LAUTLOS (kein Log, kein Neustart, da kein "Fehler")
# Alten (benutzergebundenen) Task entfernen, damit kein zweiter Agent startet.
try {{ Unregister-ScheduledTask -TaskName "RapalleRmmAgent" -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
Register-ScheduledTask -TaskName "RapalleRmmAgent" -Action $Action -Trigger $TrigBoot,$TrigWatch -Principal $Principal -Settings $Settings -Force | Out-Null

# --- 5. Agent SOFORT starten (unsichtbar, als SYSTEM) ---
Start-ScheduledTask -TaskName "RapalleRmmAgent"
Start-Sleep -Seconds 3
$chk = Get-ScheduledTask -TaskName "RapalleRmmAgent" -ErrorAction SilentlyContinue
if ($chk -and $chk.Principal.UserId -notmatch "SYSTEM") {{
    Write-Host "WARNUNG: Autostart laeuft nicht als SYSTEM - der Agent startet dann nur nach Anmeldung." -ForegroundColor Yellow
}}

# --- 6. Elevated Wartungs-Tasks (Update/Uninstall als SYSTEM, per Event) ---
# Rueckfallebene: der Agent laeuft zwar bereits als SYSTEM, aber diese Tasks
# erlauben Update/Uninstall auch dann, wenn der Agent-Prozess selbst gerade
# nicht mehr laeuft oder (Altinstallation) nur in einer Benutzersitzung haengt.
Write-Host "Richte elevated Wartungs-Tasks (Update/Uninstall) ein..."
try {{ iwr "{backend_url}/agent-dist/elevate.ps1" -UseBasicParsing | iex }} catch {{ Write-Host "  (Wartungs-Tasks konnten nicht eingerichtet werden: $_)" }}

Write-Host ""
Write-Host "=== FERTIG! ===" -ForegroundColor Green
Write-Host "Der Agent laeuft jetzt als Dienst unter SYSTEM und startet beim Booten -"
Write-Host "auch wenn sich NIEMAND anmeldet (Server)."
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
def agent_update_sh(request: Request, lang: str = ""):
    """
    Aktualisiert den Agenten (Linux) und startet den Autostart-Dienst NEU.
    Die systemd-Unit wird sicherheitshalber neu geschrieben, damit der Neustart
    auch dann klappt, wenn die Unit fehlte/veraltet war. Bestehende .device-id
    bleibt erhalten -> gleiche Client-Identität.
    """
    backend_url = _backend_url(request)
    # Sprache dieses Skripts: ?lang=de|en, sonst die Server-Sprache.
    _L = _lang(lang) if lang else server_lang()
    def L(key, **kw):
        return _t(key, _L, **kw)

    script = f"""#!/bin/bash
# RAPALLE.net RMM - Agent-Update (Linux)
set -e
INSTALL_DIR="/opt/rapalle-rmm-agent"

# Rootrechte: notfalls selbst per sudo neu starten (wenn es eine Skriptdatei
# gibt - beim Aufruf durch eine Pipe greifen die sudo-Aufrufe weiter unten).
if [ "$(id -u)" != "0" ] && [ -f "$0" ] && command -v sudo >/dev/null 2>&1; then
  exec sudo -E "$0" "$@"
fi

# Alles mitprotokollieren - das Skript laeuft losgeloest ohne Terminal.
sudo mkdir -p "$INSTALL_DIR"
exec > >(sudo tee -a "$INSTALL_DIR/update.log") 2>&1
echo "=== {L('ins_upd_title')} ($(date)) ==="

# --- 1. Neues Paket holen ---------------------------------------------------
echo "{L('ins_downloading')}"
curl -sSL "{backend_url}/agent-dist/agent.zip" -o /tmp/rapalle-agent.zip
rm -rf /tmp/rapalle-agent-extract
mkdir -p /tmp/rapalle-agent-extract
unzip -o /tmp/rapalle-agent.zip -d /tmp/rapalle-agent-extract >/dev/null

# --- 2. Laufenden Agenten stoppen -------------------------------------------
echo "{L('ins_stopping')}"
sudo systemctl stop rapalle-agent 2>/dev/null || true
sudo pkill -f "$INSTALL_DIR/agent.py" 2>/dev/null || true
sleep 2

# --- 3. Identitaet sichern --------------------------------------------------
# .env und .device-id MUESSEN erhalten bleiben, sonst erscheint das Geraet
# nach dem Neuausrollen als NEUER Client im Dashboard.
KEEP=/tmp/rapalle-agent-keep
sudo rm -rf "$KEEP"; sudo mkdir -p "$KEEP"
for f in .env .device-id; do
  [ -f "$INSTALL_DIR/$f" ] && sudo cp "$INSTALL_DIR/$f" "$KEEP/$f"
done

# --- 4. Programmordner leeren (KOMPLETTES Neuausrollen) ---------------------
# Alte .pyc, verwaiste Skripte und eine halb kaputte venv sollen weg. Nur die
# Logdateien bleiben fuer die Fehlersuche stehen.
echo "{L('ins_cleaning')}"
sudo find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 \
     ! -name 'agent.log' ! -name 'update.log' -exec rm -rf {{}} ';' 2>/dev/null || true

# --- 5. Neue Dateien auslegen (rekursiv) ------------------------------------
echo "{L('ins_deploying')}"
sudo cp -a /tmp/rapalle-agent-extract/agent/. "$INSTALL_DIR/"
for f in .env .device-id; do
  [ -f "$KEEP/$f" ] && sudo cp "$KEEP/$f" "$INSTALL_DIR/$f"
done
if [ -f "$INSTALL_DIR/.env" ]; then
  sudo sed -i "s#BACKEND_URL=.*#BACKEND_URL={backend_url}#" "$INSTALL_DIR/.env"
fi

cd "$INSTALL_DIR"
# --- 6. Virtuelle Umgebung frisch bauen -------------------------------------
# Erst daneben bauen, dann tauschen. Scheitert es (kein python3-venv-Paket),
# faellt der Dienst auf das System-Python zurueck statt gar nicht zu starten.
SYSPY="$(command -v python3 || true)"
if [ -n "$SYSPY" ]; then
  echo "{L('ins_venv', py='$SYSPY')}"
  sudo rm -rf "$INSTALL_DIR/venv.new"
  if sudo "$SYSPY" -m venv "$INSTALL_DIR/venv.new" 2>/dev/null; then
    sudo rm -rf "$INSTALL_DIR/venv"
    sudo mv "$INSTALL_DIR/venv.new" "$INSTALL_DIR/venv"
  else
    echo "{L('ins_warn_venv')}"
    sudo rm -rf "$INSTALL_DIR/venv.new"
  fi
fi
if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
  PYEXEC="$INSTALL_DIR/venv/bin/python"
  echo "{L('ins_deps')}"
  sudo "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet || true
  sudo "$INSTALL_DIR/venv/bin/pip" install -r requirements.txt
else
  PYEXEC="$(command -v python3)"
  sudo "$PYEXEC" -m pip install -r requirements.txt --break-system-packages 2>/dev/null || \
    sudo "$PYEXEC" -m pip install -r requirements.txt || true
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
def agent_update_ps1(request: Request, lang: str = ""):
    """Aktualisiert den Agenten (Windows) und startet den Autostart-Task NEU
    (Task wird bei Bedarf neu registriert)."""
    backend_url = _backend_url(request)
    # Sprache dieses Skripts: ?lang=de|en, sonst die Server-Sprache.
    _L = _lang(lang) if lang else server_lang()
    def L(key, **kw):
        return _t(key, _L, **kw)

    script = f"""# RAPALLE.net RMM - Agent-Update (Windows) - als Administrator ausfuehren
$ErrorActionPreference = "Stop"
$InstallDir = "C:\\Program Files\\RapalleRmmAgent"
# Adminrechte: notfalls per UAC neu starten (Program Files + Aufgabenplanung).
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    $cmd = "iwr '{backend_url}/agent-dist/update.ps1' -UseBasicParsing | iex"
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
    $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -PassThru -Wait `
         -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $enc)
    exit $p.ExitCode
}}
# Alles mitprotokollieren - das Skript laeuft als SYSTEM ohne sichtbares
# Fenster. Ohne diese Datei war nach einem misslungenen Update nicht
# nachvollziehbar, WO es klemmte.
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
try {{ Start-Transcript -Path "$InstallDir\\update.log" -Force | Out-Null }} catch {{}}
Write-Host "=== {L('ins_upd_title')} ($(Get-Date)) ==="

# --- 1. Neues Paket holen ---------------------------------------------------
Write-Host "{L('ins_downloading')}"
Invoke-WebRequest -Uri "{backend_url}/agent-dist/agent.zip" -OutFile "$env:TEMP\\rapalle-agent.zip"
if (Test-Path "$env:TEMP\\rapalle-agent-extract") {{ Remove-Item -Recurse -Force "$env:TEMP\\rapalle-agent-extract" }}
Expand-Archive -Path "$env:TEMP\\rapalle-agent.zip" -DestinationPath "$env:TEMP\\rapalle-agent-extract" -Force
$Src = "$env:TEMP\\rapalle-agent-extract\\agent"

# --- 2. Laufenden Agenten UND Bildschirm-Helfer beenden ---------------------
# Der Helfer (_screen_helper.py) laeuft in der Benutzersitzung und haelt sonst
# eine alte Datei offen -> die neue Fassung wuerde erst nach Reboot greifen.
Write-Host "{L('ins_stopping')}"
try {{ Stop-ScheduledTask -TaskName "RapalleRmmAgent" -ErrorAction SilentlyContinue }} catch {{}}
try {{
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object {{ $_.CommandLine -like "*RapalleRmmAgent*" -or $_.CommandLine -like "*agent.py*" -or $_.CommandLine -like "*_screen_helper.py*" }} |
        ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
}} catch {{}}
Start-Sleep -Seconds 3

# --- 3. Identitaet sichern --------------------------------------------------
# .env (Token/Backend) und .device-id (Client-Identitaet) MUESSEN das
# Neuausrollen ueberleben, sonst taucht das Geraet als NEUER Client auf.
$Keep = "$env:TEMP\\rapalle-agent-keep"
if (Test-Path $Keep) {{ Remove-Item -Recurse -Force $Keep }}
New-Item -ItemType Directory -Force -Path $Keep | Out-Null
foreach ($f in @(".env", ".device-id")) {{
    if (Test-Path "$InstallDir\\$f") {{ Copy-Item "$InstallDir\\$f" "$Keep\\$f" -Force }}
}}

# --- 4. Programmordner leeren (KOMPLETTES Neuausrollen) ---------------------
# Bewusst alles weg: alte .pyc, verwaiste Helfer-Skripte und eine womoeglich
# halb kaputte venv. Nur agent.log bleibt fuer die Fehlersuche stehen.
Write-Host "{L('ins_cleaning')}"
Get-ChildItem -Path $InstallDir -Force -ErrorAction SilentlyContinue |
    Where-Object {{ $_.Name -notin @("agent.log", "update.log") }} |
    ForEach-Object {{ Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }}

# --- 5. Neue Dateien auslegen (rekursiv, inkl. Unterordner) -----------------
Write-Host "{L('ins_deploying')}"
Copy-Item -Path "$Src\\*" -Destination $InstallDir -Recurse -Force
foreach ($f in @(".env", ".device-id")) {{
    if (Test-Path "$Keep\\$f") {{ Copy-Item "$Keep\\$f" "$InstallDir\\$f" -Force }}
}}
if (Test-Path "$InstallDir\\.env") {{
    (Get-Content "$InstallDir\\.env") -replace 'BACKEND_URL=.*', 'BACKEND_URL={backend_url}' | Set-Content "$InstallDir\\.env"
}}

# --- 6. Virtuelle Umgebung frisch bauen -------------------------------------
# Wichtig: Erst NEBEN die alte bauen ("venv.new"). Nur wenn das klappt, wird
# getauscht. Findet sich kein System-Python (SYSTEM hat oft einen anderen
# PATH als der installierende Benutzer), bleibt die vorhandene venv bestehen -
# ein kaputtes Update ist schlimmer als eine alte Bibliothek.
$SysPy = $null
foreach ($cand in @("python.exe", "python3.exe")) {{
    $c = Get-Command $cand -ErrorAction SilentlyContinue
    if ($c) {{ $SysPy = $c.Source; break }}
}}
if (-not $SysPy) {{
    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($py) {{ $SysPy = $py.Source }}
}}
if (-not $SysPy) {{
    foreach ($g in @("$env:LOCALAPPDATA\\Programs\\Python\\Python3*\\python.exe",
                     "$env:ProgramFiles\\Python3*\\python.exe",
                     "C:\\Python3*\\python.exe")) {{
        $hit = Get-ChildItem $g -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) {{ $SysPy = $hit.FullName; break }}
    }}
}}
if ($SysPy) {{
    Write-Host "{L('ins_venv', py='$SysPy')}"
    if (Test-Path "$InstallDir\\venv.new") {{ Remove-Item -Recurse -Force "$InstallDir\\venv.new" }}
    & $SysPy -m venv "$InstallDir\\venv.new"
    if ($LASTEXITCODE -eq 0 -and (Test-Path "$InstallDir\\venv.new\\Scripts\\python.exe")) {{
        if (Test-Path "$InstallDir\\venv") {{ Remove-Item -Recurse -Force "$InstallDir\\venv" -ErrorAction SilentlyContinue }}
        Rename-Item "$InstallDir\\venv.new" "venv"
    }} else {{
        Write-Host "{L('ins_warn_venv')}" -ForegroundColor Yellow
        Remove-Item -Recurse -Force "$InstallDir\\venv.new" -ErrorAction SilentlyContinue
    }}
}} else {{
    Write-Host "{L('ins_warn_nopy')}" -ForegroundColor Yellow
}}
if (-not (Test-Path "$InstallDir\\venv\\Scripts\\python.exe")) {{
    Write-Host "{L('ins_err_novenv')}" -ForegroundColor Red
    try {{ Stop-Transcript | Out-Null }} catch {{}}
    exit 1
}}
Write-Host "{L('ins_deps')}"
& "$InstallDir\\venv\\Scripts\\python.exe" -m pip install --upgrade pip --quiet
& "$InstallDir\\venv\\Scripts\\pip.exe" install -r "$InstallDir\\requirements.txt"
if ($LASTEXITCODE -ne 0) {{
    Write-Host "{L('ins_err_deps')}" -ForegroundColor Red
    try {{ Stop-Transcript | Out-Null }} catch {{}}
    exit 1
}}

# --- 7. Autostart-Task IMMER neu registrieren -------------------------------
# Frueher wurde der Task nur angefasst, wenn er nicht auf SYSTEM lief. Dadurch
# behielten Altinstallationen fehlerhafte Einstellungen (z.B. das 72-Stunden-
# Zeitlimit) und der "neue" Agent verhielt sich weiter wie der alte.
Write-Host "{L('ins_task')}"
$Action = New-ScheduledTaskAction -Execute "$InstallDir\\venv\\Scripts\\pythonw.exe" -Argument "`"$InstallDir\\agent.py`"" -WorkingDirectory $InstallDir
$TrigBoot  = New-ScheduledTaskTrigger -AtStartup
$TrigWatch = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0)  # 0 = KEIN Zeitlimit (sonst stiller Kill nach 72h)
try {{ Unregister-ScheduledTask -TaskName "RapalleRmmAgent" -Confirm:$false -ErrorAction SilentlyContinue }} catch {{}}
Register-ScheduledTask -TaskName "RapalleRmmAgent" -Action $Action -Trigger $TrigBoot,$TrigWatch -Principal $Principal -Settings $Settings -Force | Out-Null

# --- 8. Wartungs-Tasks nachziehen -------------------------------------------
try {{ iwr "{backend_url}/agent-dist/elevate.ps1" -UseBasicParsing | iex }} catch {{ Write-Host "  (Wartungs-Tasks: $_)" }}

# --- 9. NEU STARTEN ---------------------------------------------------------
New-Item -ItemType File -Force "$InstallDir\\.updated" | Out-Null
Start-ScheduledTask -TaskName "RapalleRmmAgent"
Write-Host "=== {L('ins_done')} ==="
try {{ Stop-Transcript | Out-Null }} catch {{}}
"""
    return PlainTextResponse(script, media_type="text/plain")


@router.get("/agent-dist/elevate.ps1", response_class=PlainTextResponse)
def agent_elevate_ps1(request: Request):
    """
    Richtet EINMALIG (elevated auszuführen) zwei vorautorisierte SYSTEM-Tasks ein,
    die per Windows-Event ausgelöst werden:
        RapalleRmmUpdate    (EventID 812)  -> update.ps1
        RapalleRmmUninstall (EventID 811)  -> uninstall.ps1
    Danach kann der Agent Update/Uninstall auslösen, indem er nur ein Event
    schreibt - die Aufgabenplanung führt das Skript dann als SYSTEM aus.

    Hinweis: Seit der Agent selbst als SYSTEM-Dienst läuft (Autostart-Task mit
    Trigger "AtStartup"), braucht er diesen Umweg nicht mehr zwingend. Die
    Tasks bleiben als Rückfallebene bestehen - z.B. für Altinstallationen, die
    noch in einer Benutzersitzung laufen.

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
def agent_uninstall_sh(request: Request, lang: str = ""):
    """
    Deinstalliert den Agenten (Linux) SYNCHRON: Dienst stoppen/entfernen,
    Prozesse killen, dann Programmordner löschen. Läuft in einem eigenen
    systemd-Scope (siehe agent.py), daher überlebt das Skript den Stop des
    eigenen Dienstes und löscht die Dateien tatsächlich. KEIN Hintergrund-Job.
    """
    # Sprache dieses Skripts: ?lang=de|en, sonst die Server-Sprache.
    _L = _lang(lang) if lang else server_lang()
    def L(key, **kw):
        return _t(key, _L, **kw)

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
def agent_uninstall_ps1(request: Request, lang: str = ""):
    """
    Deinstalliert den Agenten (Windows) SYNCHRON: Task entfernen, Prozesse
    killen, dann Programmordner löschen (kein Start-Job, der beim Beenden der
    Shell abgebrochen würde).
    """
    # Sprache dieses Skripts: ?lang=de|en, sonst die Server-Sprache.
    _L = _lang(lang) if lang else server_lang()
    def L(key, **kw):
        return _t(key, _L, **kw)

    script = """# RAPALLE.net RMM - Agent-Deinstallation (Windows)
# Wird bevorzugt als SYSTEM-Task ausgefuehrt (elevated). Reihenfolge:
# Autostart entfernen -> Prozesse hart beenden -> alle Daten loeschen.
$ErrorActionPreference = "SilentlyContinue"
$InstallDir = "C:\\Program Files\\RapalleRmmAgent"

# Adminrechte: notfalls per UAC neu starten. Als SYSTEM-Task laeuft das Skript
# ohnehin schon erhoeht - dann greift der Block nicht.
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Starte mit Administratorrechten neu (UAC-Abfrage) ..."
    if ($PSCommandPath) {
        $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -PassThru -Wait `
             -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
        exit $p.ExitCode
    }
    Write-Host "Bitte PowerShell als Administrator oeffnen." -ForegroundColor Red
    exit 1
}

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


# ==========================================================================
# FERTIGE INSTALLATIONSPAKETE (.exe / .msi / .deb / .rpm / .run)
# --------------------------------------------------------------------------
# Gebaut werden sie lokal mit "python tools/build_installers.py" - die Pakete
# landen im Ordner dist/ im Projekt-Root. Diese Routen machen sie im Dashboard
# nutzbar: auflisten, per Knopfdruck bauen und über die Onboarding-Seite
# herunterladen.
#
# Der Onboarding-Token steckt NICHT im Paket (das wäre pro Token ein eigener
# Build). Stattdessen bekommt der Installer ihn beim Aufruf mit:
#     Windows:  RapalleRmmAgent-Setup.exe /S /TOKEN=<token>
#     Linux:    sudo RMM_ENROLLMENT_TOKEN=<token> ./rapalle-rmm-agent.run
# Ohne Token landet das Gerät in "Uncategorized" und kann später zugeordnet
# werden.
# ==========================================================================

def _installer_hint(name: str, token: str, backend_url: str = "") -> str:
    """
    Passender Aufrufbefehl je Paketart - mit den ECHTEN Werten dieser
    Installation (Backend-Adresse, Onboarding-Token). Der Befehl laesst sich
    also unveraendert kopieren; niemand muss noch Platzhalter ersetzen.
    """
    n = name.lower()
    if n.endswith(".exe"):
        return f"RapalleRmmAgent-Setup.exe /S /TOKEN={token}"
    if n.endswith(".msi"):
        # Der Windows Installer reicht eigene Eigenschaften nicht an die
        # (deferred) Aktion durch -> das Geraet landet in "Uncategorized".
        return f"msiexec /i {name} /qn"
    if n.endswith(".bat"):
        return f"{name} {backend_url} {token}"
    if n.endswith(".ps1"):
        return f"powershell -ExecutionPolicy Bypass -File {name} -Token {token}"
    if n.endswith(".pkg.tar.xz"):
        return f"sudo RMM_ENROLLMENT_TOKEN={token} pacman -U {name}"
    if n.endswith(".deb"):
        return f"sudo RMM_ENROLLMENT_TOKEN={token} apt install ./{name}"
    if n.endswith(".rpm"):
        return f"sudo RMM_ENROLLMENT_TOKEN={token} dnf install ./{name}"
    if n.endswith((".tar.gz", ".tgz")):
        return f"tar xzf {name} && cd */ && sudo RMM_ENROLLMENT_TOKEN={token} ./install.sh"
    return f"sudo RMM_ENROLLMENT_TOKEN={token} ./{name}"


PROJECT_ROOT_DIR = AGENT_SOURCE_DIR.parent
INSTALLER_DIST_DIR = PROJECT_ROOT_DIR / "dist"
BUILD_SCRIPT = PROJECT_ROOT_DIR / "tools" / "build_installers.py"

# Dateiname-Muster -> Anzeige im Dashboard.
# Reihenfolge zaehlt: die spezielleren Endungen zuerst pruefen.
_INSTALLER_KINDS = [
    (".exe", "Windows-Setup", "🪟", "exe"),
    (".msi", "Windows MSI (GPO/Intune)", "🪟", "msi"),
    (".bat", "Windows-Batch (Doppelklick)", "🪟", "bat"),
    (".ps1", "Windows PowerShell-Skript", "🪟", "ps1"),
    (".pkg.tar.xz", "Arch / Manjaro", "🐧", "pkg"),
    (".deb", "Debian / Ubuntu", "🐧", "deb"),
    (".rpm", "Fedora / RHEL / SUSE", "🐧", "rpm"),
    (".tar.gz", "Linux-Archiv (tar.gz)", "🐧", "tgz"),
    (".tgz", "Linux-Archiv (tgz)", "🐧", "tgz"),
    (".sh", "Linux (selbstentpackend)", "🐧", "sh"),
    (".run", "Linux (selbstentpackend, .run)", "🐧", "sh"),
]


def _installer_entries() -> list[dict]:
    """Alle gebauten Pakete in dist/ (neueste zuerst)."""
    out = []
    if not INSTALLER_DIST_DIR.is_dir():
        return out
    for f in sorted(INSTALLER_DIST_DIR.iterdir()):
        if not f.is_file():
            continue
        for suffix, label, icon, target in _INSTALLER_KINDS:
            if f.name.lower().endswith(suffix):
                st = f.stat()
                out.append({
                    "name": f.name,
                    "label": label,
                    "icon": icon,
                    "target": target,
                    "size": st.st_size,
                    "built_at": int(st.st_mtime),
                })
                break
    out.sort(key=lambda e: e["built_at"], reverse=True)
    return out


@router.get("/api/enrollment/installers")
def list_installers(user: dict = Depends(get_current_user)):
    """
    Was liegt an fertigen Paketen bereit - und was liesse sich hier bauen?
    Wird vom "Client hinzufügen"-Fenster benutzt.
    """
    require_perm(user, "add_client")
    can = {}
    try:
        import shutil as _sh
        import platform as _pl
        # exe/msi: die Werkzeuge zieht das Build-Skript bei Bedarf selbst nach
        # (PyInstaller per pip, WiX als Download) - Windows genuegt also.
        # Skript-/Archivpakete brauchen gar kein Werkzeug.
        is_win = _pl.system() == "Windows"
        can = {
            "exe": is_win,
            "msi": is_win,
            "bat": True,
            "ps1": True,
            "sh": True,
            "tgz": True,
            "pkg": True,
            "deb": bool(_sh.which("dpkg-deb")),
            "rpm": bool(_sh.which("rpmbuild")),
        }
    except Exception:
        can = {"sh": True}
    return {
        "installers": _installer_entries(),
        "can_build": can,
        "build_available": BUILD_SCRIPT.is_file(),
        "dist_dir": str(INSTALLER_DIST_DIR),
    }


class BuildInstallersBody(BaseModel):
    targets: str = "auto"       # "auto" oder z.B. "sh,deb"


@router.post("/api/enrollment/installers/build")
def build_installers(body: BuildInstallersBody, request: Request,
                           user: dict = Depends(get_current_user)):
    """
    Startet tools/build_installers.py und wartet auf das Ergebnis.

    Absichtlich synchron: der Build dauert je nach Ziel Sekunden bis ~2 Minuten
    und das Fenster zeigt danach direkt die neuen Downloads. Nur für Admins -
    hier wird ein Prozess auf dem Server gestartet.
    """
    require_admin(user)
    if not BUILD_SCRIPT.is_file():
        raise HTTPException(404, "tools/build_installers.py nicht gefunden")

    import subprocess
    import sys as _sys

    targets = (body.targets or "auto").strip()
    if not all(c.isalnum() or c in ",_-" for c in targets):
        raise HTTPException(400, "Ungültige Ziel-Angabe")

    cmd = [_sys.executable, str(BUILD_SCRIPT),
           "--targets", targets,
           "--backend-url", _backend_url(request),
           "--out", str(INSTALLER_DIST_DIR)]
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT_DIR), capture_output=True,
                              text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Build-Zeitlimit (30 Minuten) überschritten")
    except Exception as e:
        raise HTTPException(500, f"Build konnte nicht gestartet werden: {e}")

    log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    db.add_audit_entry(user["username"], "enrollment.build_installers",
                       details=f"targets={targets} rc={proc.returncode}")
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "log": log[-8000:], "installers": _installer_entries()}


@router.get("/enroll/{token}/installer/{name}")
def download_installer(token: str, name: str):
    """
    Download eines fertigen Pakets über die Onboarding-URL (kein Login nötig -
    der Token in der URL ist der Nachweis, genau wie bei agent.zip).
    """
    _require_valid_token(token)
    # Kein Pfad-Ausbruch: nur genau die Dateien, die in dist/ als Paket gelten.
    if name not in {e["name"] for e in _installer_entries()}:
        raise HTTPException(404, "Paket nicht gefunden")
    path = INSTALLER_DIST_DIR / name
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=name, media_type="application/octet-stream")

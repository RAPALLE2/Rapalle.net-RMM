"""
paths.py
--------
Zentrale Pfad-Definitionen des Projekts (Single Source of Truth).

Projektstruktur:

    <root>/
      src/
        agent/      Agent (main.py, requirements.txt, version.txt, lib/)
        backend/    Backend
          app/      Kernlogik
          router/   FastAPI-Router (ehemals app/routers)
          api/      (reserviert)
          lib/      eigene Bibliotheken
          data/     Datenbank, .env, version.txt, requirements.txt, repo.txt
          logs/     Logdateien
          media/    images, recordings, audio, tickets
          storage/  deployment, spaces (Relay/Explorer)
          temp/     temporaere Dateien (z.B. dist = gebaute Installer)
        frontend/   Webconsole (css, image, js/app, js/feature, js/lib, js/vendor)
        node/       Node-Module (werden an Agents ausgeliefert)
        files/      Explorer / Teamspace (kommt spaeter)

Alle anderen Module importieren ihre Pfade von hier, damit eine
Strukturaenderung nur noch an EINER Stelle angepasst werden muss.
"""

from pathlib import Path

# …/src/backend/app/paths.py
BACKEND_DIR = Path(__file__).resolve().parents[1]      # …/src/backend
SRC_DIR = BACKEND_DIR.parent                           # …/src
PROJECT_ROOT = SRC_DIR.parent                          # Projekt-Wurzel

AGENT_DIR = SRC_DIR / "agent"
FRONTEND_DIR = SRC_DIR / "frontend"
BRANDING_DIR = FRONTEND_DIR / "image"                  # Branding = frontend/image (Defaults UND Uploads)
NODE_DIR = SRC_DIR / "node"                            # Node-Module (node_*.py)
MODULES_DIR = SRC_DIR / "modules"                      # Optionale Module: modules/<bereich>/<name>/module.json
FILES_DIR = SRC_DIR / "files"                          # Explorer/Teamspace (spaeter)

# --- Backend-Unterordner ---------------------------------------------------
APP_DIR = BACKEND_DIR / "app"
ROUTER_DIR = BACKEND_DIR / "router"
LIB_DIR = BACKEND_DIR / "lib"

DATA_DIR = BACKEND_DIR / "data"
DB_FILE = DATA_DIR / "data.sqlite"
DB_BACKUP_DIR = DATA_DIR / "backup"
DB_BACKUP_FILE = DB_BACKUP_DIR / "data.sqlite"         # Online-Backup der DB
DBCONFIG_FILE = DATA_DIR / "dbconfig.json"
MODULES_STATE_FILE = DATA_DIR / "modules.json"          # aktivierte Module, Modul-Einstellungen, Crash-Zusammenfassung
MODULES_DATA_DIR = DATA_DIR / "modules"                # eigener Datenordner je Modul
ENV_FILE = DATA_DIR / ".env"
BACKEND_VERSION_FILE = DATA_DIR / "version.txt"
BACKEND_REQUIREMENTS_FILE = DATA_DIR / "requirements.txt"
REPO_FILE = DATA_DIR / "repo.txt"

STORAGE_DIR = BACKEND_DIR / "storage"
# Einheitliche storage-Struktur:
#   storage/web/<name>/     alle Websites (Modul Webserver); web/deployment = Deployment
#   storage/dist/<ordner>/  Verteilung (/dist: enrollment, agent, client, ...)
#   storage/spaces/...      Spaces (persoenlich/teams/public)
#   storage/docker/<projekt>/  Docker-Projekte (compose) des Docker-Moduls
WEB_DIR = STORAGE_DIR / "web"                          # alle Website-Ordner
DEPLOYMENT_DIR = WEB_DIR / "deployment"                # Deployment-Seite (Website-Ordner)
SPACES_DIR = STORAGE_DIR / "spaces"
DOCKER_PROJECTS_DIR = STORAGE_DIR / "docker"           # eigene Docker-Projekte je Name

LOGS_DIR = BACKEND_DIR / "logs"
BACKEND_LOG_DIR = LOGS_DIR / "backend"                 # Diagnose-Logs, startup.log
MODULE_CRASH_DIR = LOGS_DIR / "modules"                # Crashberichte je Modul

MEDIA_DIR = BACKEND_DIR / "media"
RECORDINGS_DIR = MEDIA_DIR / "recordings"              # Screen-/Terminal-Aufzeichnungen
MEDIA_IMAGES_DIR = MEDIA_DIR / "images"                # reserviert (kein Branding!)
AUDIO_DIR = MEDIA_DIR / "audio"                        # Medien-Bibliothek des Audio-Players
TICKET_FILES_DIR = MEDIA_DIR / "tickets"               # Ticket-Anhaenge

TEMP_DIR = BACKEND_DIR / "temp"
DIST_DIR = STORAGE_DIR / "dist"                        # Verteilung (/dist): enrollment, agent, client, ...

# --- Agent -----------------------------------------------------------------
AGENT_MAIN_FILE = AGENT_DIR / "main.py"
AGENT_VERSION_FILE = AGENT_DIR / "version.txt"

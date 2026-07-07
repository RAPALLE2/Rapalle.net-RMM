"""
main.py
-------
Der zentrale Einstiegspunkt des Backends. Verbindet drei Dinge zu EINER
Anwendung, die mit einem einzigen Befehl gestartet wird:

  1. FastAPI          -> die REST-API (/api/...)
  2. Socket.IO         -> die Echtzeit-Verbindungen (/socket.io/...)
  3. Statische Dateien -> das komplette Frontend (HTML/CSS/JS) aus dem
                          "frontend"-Ordner, unter der Wurzel "/"

Das bedeutet: du brauchst NUR diesen einen Python-Prozess zu starten
(siehe run.py) - kein separater Webserver, kein Node/npm nötig, um das
Dashboard im Browser zu sehen. Öffne einfach http://localhost:4000
"""

from pathlib import Path

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import CORS_ORIGIN
from app.sockets import sio
from app.routers import (
    auth_routes,
    users_routes,
    hierarchy_routes,
    clients_routes,
    network_routes,
    files_routes,
    enrollment_routes,
    audit_routes,
    recordings_routes,
    scripts_routes,
    admin_routes,
    agent_update_routes,
    guac_routes,
)

# 1) Datenbank initialisieren (legt Tabellen an, erzeugt admin/admin falls nötig)
db.init_db()

# Beim Start aufräumen: alte Audit-Einträge (30 Tage) und alte
# Screen-Aufzeichnungen (10 Tage) entfernen.
db.cleanup_old_audit_entries(30)
try:
    from app import recording
    removed = recording.cleanup_old_recordings()
    if removed:
        print(f"[cleanup] {removed} alte Aufzeichnung(en) gelöscht")
except Exception as e:
    print(f"[cleanup] Konnte alte Aufzeichnungen nicht aufräumen: {e}")

# 2) FastAPI-App erstellen und alle Routen-Module einhängen
api = FastAPI(title="RAPALLE.net RMM Backend")

api.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN] if CORS_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api.include_router(auth_routes.router)
api.include_router(users_routes.router)
api.include_router(hierarchy_routes.router)
api.include_router(clients_routes.router)
api.include_router(network_routes.router)
api.include_router(files_routes.router)
api.include_router(enrollment_routes.router)
api.include_router(audit_routes.router)
api.include_router(recordings_routes.router)
api.include_router(scripts_routes.router)
api.include_router(admin_routes.router)
api.include_router(agent_update_routes.router)
api.include_router(guac_routes.router)

# Guacamole-WebSocket-Tunnel (Browser <-> guacd). Muss VOR dem statischen
# Frontend-Mount registriert werden, damit die Route greift.
api.add_api_websocket_route("/guac/tunnel", guac_routes.tunnel_endpoint)


# ------------------------------------------------------------------
# Versionen: Single Source of Truth sind die version.txt-Dateien
# (backend/version.txt und agent/version.txt). Grundlage für das
# spätere Auto-Update: Agents/Frontends vergleichen ihre Version
# gegen /api/version und aktualisieren sich bei Abweichung.
# ------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _read_version(path: Path, fallback: str = "0.0.0") -> str:
    try:
        v = path.read_text(encoding="utf-8").strip()
        return v or fallback
    except OSError:
        return fallback


def get_backend_version() -> str:
    return _read_version(_PROJECT_ROOT / "backend" / "version.txt")


def get_agent_version() -> str:
    return _read_version(_PROJECT_ROOT / "agent" / "version.txt")


@api.get("/api/health")
async def health_check():
    """Einfacher Endpunkt zum Prüfen, ob das Backend läuft."""
    return {"ok": True, "name": "RAPALLE.net RMM", "version": get_backend_version()}


@api.get("/api/version")
async def versions():
    """
    Zentrale Versionsauskunft (kein Auth - enthält keine Geheimnisse):
    Backend- und Agent-Version aus den jeweiligen version.txt-Dateien.
    """
    return {"backend": get_backend_version(), "agent": get_agent_version()}


# ------------------------------------------------------------------
# Automation-Engine: prüft jede Minute, ob geplante Automationen fällig
# sind, und führt deren Befehl auf den Ziel-Clients aus. Läuft als
# Hintergrund-Task im selben Prozess.
# ------------------------------------------------------------------
import asyncio as _asyncio
from app.sockets import request_exec as _request_exec


async def _automation_engine():
    while True:
        try:
            for auto in db.get_due_automations():
                client_ids = [c for c in (auto["client_ids"] or "").split(",") if c]
                for cid in client_ids:
                    try:
                        await _request_exec(cid, auto["command"], timeout_seconds=60)
                    except Exception as e:
                        print(f"[automation] '{auto['name']}' auf {cid} fehlgeschlagen: {e}")
                db.mark_automation_run(auto["id"])
                db.add_audit_entry("system", "automation.executed", target=auto["id"], details=auto["name"])
        except Exception as e:
            print(f"[automation] Engine-Fehler: {e}")
        await _asyncio.sleep(30)  # alle 30 Sekunden prüfen


@api.on_event("startup")
async def _start_background_tasks():
    _asyncio.create_task(_automation_engine())


# 3) Frontend-Ordner als statische Dateien einhängen.
#    Wichtig: das muss NACH den API-Routen passieren, sonst würden die
#    statischen Dateien alle Anfragen "wegschnappen" bevor die API sie sieht.
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
api.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


# 4) Socket.IO und FastAPI zu einer einzigen ASGI-Anwendung zusammenfügen.
#    "socket_app" ist das, was uvicorn am Ende tatsächlich startet (siehe run.py).
socket_app = socketio.ASGIApp(sio, other_asgi_app=api, socketio_path="socket.io")

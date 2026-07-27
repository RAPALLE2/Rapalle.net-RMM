"""
routers/docker_routes.py
------------------------
Zusatzdienste im Container-Betrieb dazuschalten (Settings -> Allgemein ->
Container-Dienste). Siehe app/docker_services.py für die Technik dahinter.

Endpunkte (nur Admin):
  GET  /api/admin/docker/services              -> Zustand aller Dienste
  POST /api/admin/docker/services/{key}/enable -> anlegen + starten
  POST /api/admin/docker/services/{key}/disable-> stoppen + entfernen
  GET  /api/admin/docker/db-credentials        -> Zugangsdaten der SQL-Datenbank
                                                  (füllt das DB-Formular vor)

Läuft das Backend NICHT im Container, liefert der Status einfach
"is_docker: false" - die Oberfläche blendet den Bereich dann aus.
"""

from fastapi import APIRouter, Depends, HTTPException

from app import docker_services, db
from app.auth import get_current_user
from app.routers.admin_routes import require_admin

router = APIRouter(prefix="/api/admin/docker", tags=["docker"])


@router.get("/services")
async def list_services(user: dict = Depends(get_current_user)):
    require_admin(user)
    return docker_services.status()


@router.post("/services/{key}/enable")
async def enable_service(key: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    try:
        result = docker_services.enable(key)
    except docker_services.DockerError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Dienst konnte nicht gestartet werden: {e}")
    db.add_audit_entry(user["username"], "docker.enable", target=key)
    # Die frisch gesetzten Werte gleich mitliefern, damit das Dashboard die
    # Formulare ohne zweiten Aufruf vorausfüllen kann.
    result["status"] = docker_services.status()
    result["db"] = docker_services.db_credentials()
    result["guacd"] = {
        "host": db.get_setting("guacd_host") or "",
        "port": int(db.get_setting("guacd_port") or 4822),
    }
    return result


@router.post("/services/{key}/disable")
async def disable_service(key: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    try:
        result = docker_services.disable(key)
    except docker_services.DockerError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Dienst konnte nicht gestoppt werden: {e}")
    db.add_audit_entry(user["username"], "docker.disable", target=key)
    result["status"] = docker_services.status()
    return result


@router.get("/db-credentials")
async def db_credentials(user: dict = Depends(get_current_user)):
    require_admin(user)
    return docker_services.db_credentials()

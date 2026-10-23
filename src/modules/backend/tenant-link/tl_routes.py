# tl_routes.py (Modul "tenant-link") - Gegenstueck auf dem MANDANTEN-Backend
# ------------------------------------------------------------------------
# Erlaubt, dass ein Haupt-RMM dieses Backend einbettet und per Autologin oeffnet.
# Autologin selbst laeuft ueber die normale Login-API (der Benutzer hinterlegt im
# Haupt-RMM seine Zugangsdaten); hier kommen nur die Freigaben dazu:
#   GET  /api/tenant-link/info     bestaetigt Einbettbarkeit + Anzeigename/Branding
# Die erlaubten Eltern-Adressen (frame-ancestors) stehen in den Modul-Einstellungen
# und werden beim Aktivieren als Response-Header gesetzt (siehe module.py).
from fastapi import APIRouter, Depends

from app import db
from app.auth import get_current_user

router = APIRouter(prefix="/api/tenant-link", tags=["tenant-link"])


@router.get("/info")
def info(user: dict = Depends(get_current_user)):
    name = db.get_setting("branding_name") or db.get_setting("server_name") or "RMM"
    return {"ok": True, "embeddable": True, "name": name, "user": user["username"]}

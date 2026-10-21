# hs_routes.py (Modul "headless-screen")
#   GET /api/headless-screen/driver.zip   Treiberpaket - fuer den AGENTEN (X-Agent-Token)
#                                         ODER einen angemeldeten Admin (Browser-Test)
#   GET /api/headless-screen/status       Zustand der Paket-Bereitstellung
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse

from app.auth import get_current_user
from app.config import AGENT_TOKEN

from . import vdriver

router = APIRouter(prefix="/api/headless-screen", tags=["headless-screen"])


@router.get("/status")
def status(user: dict = Depends(get_current_user)):
    return vdriver.status()


@router.get("/driver.zip")
def driver_zip(x_agent_token: str | None = Header(default=None),
               authorization: str | None = Header(default=None)):
    # Der Agent laedt mit dem Agent-Token (er ist nicht als Benutzer angemeldet).
    agent_ok = bool(x_agent_token) and hmac.compare_digest(x_agent_token, AGENT_TOKEN)
    if not agent_ok and not authorization:
        raise HTTPException(403, "Agent-Token fehlt oder ist falsch")
    if not vdriver.have_local():
        raise HTTPException(404, "Kein Treiberpaket auf dem Server hinterlegt")
    return FileResponse(str(vdriver.ZIP), media_type="application/zip",
                        filename="rapalle-vdisplay.zip")

"""
routers/agent_update_routes.py
-------------------------------
Stellt die neueste Version des Agent-Codes bereit, damit sich Agents selbst
aktualisieren können. Der Agent lädt bei einem "update-agent"-Befehl die
Datei von /api/agent/latest, ersetzt seine eigene agent.py und startet neu.

Kein Auth nötig für den Download selbst (der Code ist ohnehin auf jedem Client),
aber die Version ist über /api/agent/version einsehbar.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(prefix="/api/agent", tags=["agent"])

# Version des ausgelieferten Agents. Bei Änderungen am Agent hochzählen.
AGENT_VERSION = "1.1.0"

# Pfad zur agent.py im Projekt (…/backend/app/routers/ -> …/agent/agent.py)
_AGENT_PY = Path(__file__).resolve().parents[3] / "agent" / "agent.py"


@router.get("/version")
async def agent_version():
    return {"version": AGENT_VERSION}


@router.get("/latest", response_class=PlainTextResponse)
async def agent_latest():
    """Liefert den aktuellen Agent-Quellcode als Text zurück."""
    try:
        code = _AGENT_PY.read_text(encoding="utf-8")
    except FileNotFoundError:
        return PlainTextResponse("# agent.py nicht gefunden", status_code=404)
    return PlainTextResponse(code, media_type="text/x-python")

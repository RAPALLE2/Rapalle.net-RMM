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

# Pfad zur agent.py im Projekt (…/backend/app/routers/ -> …/agent/agent.py)
_AGENT_DIR = Path(__file__).resolve().parents[3] / "agent"
_AGENT_PY = _AGENT_DIR / "agent.py"
_AGENT_VERSION_FILE = _AGENT_DIR / "version.txt"


def _agent_version() -> str:
    """
    Version des ausgelieferten Agents - Single Source of Truth ist
    agent/version.txt (bei Änderungen am Agent dort hochzählen).
    Wird bei jedem Aufruf frisch gelesen, damit ein Update der Datei
    ohne Backend-Neustart wirkt (wichtig fürs spätere Auto-Update).
    """
    try:
        v = _AGENT_VERSION_FILE.read_text(encoding="utf-8").strip()
        return v or "0.0.0"
    except OSError:
        return "0.0.0"


@router.get("/version")
def agent_version():
    return {"version": _agent_version()}


@router.get("/latest", response_class=PlainTextResponse)
def agent_latest():
    """Liefert den aktuellen Agent-Quellcode als Text zurück."""
    try:
        code = _AGENT_PY.read_text(encoding="utf-8")
    except FileNotFoundError:
        return PlainTextResponse("# agent.py nicht gefunden", status_code=404)
    return PlainTextResponse(code, media_type="text/x-python")

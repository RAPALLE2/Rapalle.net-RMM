"""
routers/audit_routes.py
--------------------------
Zeigt das Audit-Log an (wer hat wann was gemacht - Logins, Terminal-Befehle,
Client-Änderungen, Benutzer-Änderungen). Aufbewahrung: 30 Tage, siehe
db.cleanup_old_audit_entries(), die beim Start und danach periodisch läuft.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def get_audit_log(limit: int = 200, user: dict = Depends(get_current_user)):
    require_perm(user, "see_audit")
    return db.list_audit_log(limit)


class ClientErrorBody(BaseModel):
    message: str
    level: str = "error"     # "error" | "warn" | "info"
    context: str | None = None   # z.B. "vnc", "terminal", "explorer"
    action: str | None = None    # optionale, freigegebene Aktion (z.B. Crash-Meldung)


# Nur diese vom Frontend gemeldeten Aktionen sind erlaubt – so kann das
# Frontend Crash-Berichte (analog zu den Agents) mit sprechender Aktion
# ins Audit-Log schreiben, ohne beliebige Aktionsnamen zu ermöglichen.
_ALLOWED_CLIENT_ACTIONS = {"frontend.crash_recovered", "frontend.crash"}


@router.post("/log-error")
def log_client_error(body: ClientErrorBody, user: dict = Depends(get_current_user)):
    """
    Ermöglicht dem Frontend, wichtige Fehler (die dem Benutzer als
    Benachrichtigung angezeigt werden) auch dauerhaft im Audit-Log zu erfassen.
    """
    if body.action in _ALLOWED_CLIENT_ACTIONS:
        action = body.action
    else:
        action = "error.warn" if body.level == "warn" else "error.reported"
    details = f"[{body.context}] {body.message}" if body.context else body.message
    db.add_audit_entry(user["username"], action, details=details[:500])
    return {"ok": True}

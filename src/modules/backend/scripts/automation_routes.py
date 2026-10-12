"""Automationen (geplante Befehle) - REST. Gehoert zur Teilfunktion 'automation'."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

router = APIRouter(prefix="/api/admin", tags=["automation"])


class AutomationBody(BaseModel):
    name: str
    command: str
    client_ids: list[str] = []
    interval_seconds: int


@router.get("/automations")
def list_automations(user: dict = Depends(get_current_user)):
    require_perm(user, "automation")
    autos = db.list_automations()
    for a in autos:
        a["client_ids"] = [c for c in (a["client_ids"] or "").split(",") if c]
    return autos


@router.post("/automations")
def create_automation(body: AutomationBody, user: dict = Depends(get_current_user)):
    require_perm(user, "automation")
    a = db.create_automation(body.name, body.command, body.client_ids, body.interval_seconds)
    db.add_audit_entry(user["username"], "automation.created", target=a["id"], details=body.name)
    return a


@router.post("/automations/{auto_id}/toggle")
def toggle_automation(auto_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "automation")
    auto = db.get_automation(auto_id)
    if not auto:
        raise HTTPException(404, "Automation nicht gefunden")
    db.set_automation_enabled(auto_id, not auto["enabled"])
    return {"ok": True, "enabled": not auto["enabled"]}


@router.delete("/automations/{auto_id}")
def delete_automation(auto_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "automation")
    db.delete_automation(auto_id)
    db.add_audit_entry(user["username"], "automation.deleted", target=auto_id)
    return {"ok": True}


@router.get("/automations/{auto_id}/runs")
def get_automation_runs(auto_id: str, user: dict = Depends(get_current_user)):
    """Liefert die letzten Durchläufe einer Automation mit Ergebnis je Client."""
    require_perm(user, "automation")
    return {"runs": db.list_automation_runs(auto_id)}

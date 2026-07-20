"""
routers/scripts_routes.py
---------------------------
Verwaltung gespeicherter Skripte (Scripts-App). Ein Skript hat einen Namen,
einen Befehl (kann mehrzeilig sein, z.B. "apt update && apt upgrade -y") und
ein Ziel-Betriebssystem (windows/linux/any).

Endpunkte:
  GET    /api/scripts        -> alle Skripte
  POST   /api/scripts        -> neues Skript anlegen
  PUT    /api/scripts/{id}   -> Skript bearbeiten
  DELETE /api/scripts/{id}   -> Skript löschen
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


class ScriptBody(BaseModel):
    name: str
    command: str
    os: str = "any"   # "windows" | "linux" | "any"
    folder: str = ""  # Ordner in der Scripts-App ("" = kein Ordner)


@router.get("")
async def list_scripts(user: dict = Depends(get_current_user)):
    require_perm(user, "use_scripts")
    return db.list_scripts()


@router.post("")
async def create_script(body: ScriptBody, user: dict = Depends(get_current_user)):
    require_perm(user, "create_scripts")
    script = db.create_script(body.name, body.command, body.os, body.folder)
    db.add_audit_entry(user["username"], "script.created", target=script["id"], details=body.name)
    return script


@router.put("/{script_id}")
async def update_script(script_id: str, body: ScriptBody, user: dict = Depends(get_current_user)):
    require_perm(user, "create_scripts")
    script = db.update_script(script_id, body.name, body.command, body.os, body.folder)
    db.add_audit_entry(user["username"], "script.updated", target=script_id, details=body.name)
    return script


@router.delete("/{script_id}")
async def delete_script(script_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "create_scripts")
    db.delete_script(script_id)
    db.add_audit_entry(user["username"], "script.deleted", target=script_id)
    return {"ok": True}

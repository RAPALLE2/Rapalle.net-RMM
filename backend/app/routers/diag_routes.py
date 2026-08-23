"""
diag_routes.py
--------------
Der Wartungsmodus im Dashboard (Einstellungen → Source → Diagnose).

  GET  /api/diag/status              läuft er? welche Dateien gibt es?
  POST /api/diag/enable              einschalten (optional mit Zeitlimit)
  POST /api/diag/disable             ausschalten
  GET  /api/diag/tail?lines=300      die letzten Zeilen live
  GET  /api/diag/bundle              alles als ZIP herunterladen
  POST /api/diag/clear               Logs leeren

Alles hinter 'see_source' - wer die Quellen sehen darf, darf auch die
Diagnose bedienen. Die Logs können Pfade, Hostnamen und Fehlermeldungen
aus dem Kundennetz enthalten; das ist nichts für gewöhnliche Benutzer.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from app import db, diagnostics
from app.auth import get_current_user, user_has_permission

router = APIRouter()


def _require(user: dict) -> None:
    if not user_has_permission(user, "see_source"):
        raise HTTPException(403, "Dafür fehlt die Berechtigung 'Quellen sehen'")


class EnableBody(BaseModel):
    # 0 = ohne Ende. Ein Zeitlimit ist die Voreinstellung, damit ein
    # vergessener Wartungsmodus nicht wochenlang die Platte füllt.
    minutes: int = 120
    reason: str = ""
    # Sollen die Agenten ebenfalls alles mitschreiben und hochladen?
    include_agents: bool = True


@router.get("/api/diag/status")
def diag_status(user: dict = Depends(get_current_user)):
    _require(user)
    data = diagnostics.status()
    data["agents_included"] = db.get_setting("maintenance_agents", "0") == "1"
    return data


@router.post("/api/diag/enable")
async def diag_enable(body: EnableBody, user: dict = Depends(get_current_user)):
    _require(user)
    result = diagnostics.enable(body.minutes, body.reason)
    db.set_setting("maintenance_agents", "1" if body.include_agents else "0")
    db.add_audit_entry(user["username"], "diag.enabled",
                       details=f"{body.minutes} min – {body.reason}")
    if body.include_agents:
        await _broadcast_agents(True, body.minutes)
    result["agents_included"] = body.include_agents
    return result


@router.post("/api/diag/disable")
async def diag_disable(user: dict = Depends(get_current_user)):
    _require(user)
    result = diagnostics.disable()
    db.set_setting("maintenance_agents", "0")
    db.add_audit_entry(user["username"], "diag.disabled")
    await _broadcast_agents(False, 0)
    return result


@router.get("/api/diag/tail", response_class=PlainTextResponse)
def diag_tail(lines: int = 300, user: dict = Depends(get_current_user)):
    _require(user)
    return "\n".join(diagnostics.tail(lines))


@router.get("/api/diag/bundle")
def diag_bundle(user: dict = Depends(get_current_user)):
    _require(user)
    data = diagnostics.bundle()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    db.add_audit_entry(user["username"], "diag.downloaded")
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="rmm-diagnose-{stamp}.zip"'})


@router.post("/api/diag/clear")
def diag_clear(user: dict = Depends(get_current_user)):
    _require(user)
    diagnostics.clear()
    db.add_audit_entry(user["username"], "diag.cleared")
    return diagnostics.status()


async def _broadcast_agents(enabled: bool, minutes: int) -> None:
    """
    Sagt allen Agenten Bescheid.

    Kein Fehler, wenn ein Agent gerade offline ist: Er bekommt den Zustand
    bei der nächsten Anmeldung ohnehin mitgeteilt (siehe sockets.py).
    """
    try:
        from app.sockets import sio
        await sio.emit("diag-mode", {"enabled": enabled, "minutes": minutes},
                       namespace="/agent")
    except Exception as e:
        print(f"[diag] Agenten nicht erreicht: {e}")

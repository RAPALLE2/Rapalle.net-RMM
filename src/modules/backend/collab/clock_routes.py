# clock_routes.py (Modul "collab", Teilfunktion "clock")
# -----------------------------------------------------
# Wecker pro Benutzer (Stoppuhr und Timer laufen im Browser).
#
#   GET /api/collab/clock/alarms     {alarms: [...]}
#   PUT /api/collab/clock/alarms     {alarms: [...]}  komplette Liste speichern
#
# Wecker: {id, time "HH:MM", label, days [0..6] (0 = Montag; leer = einmalig), enabled}
# Geklingelt wird im Dashboard (init-Skript clockcore.js), solange es offen ist.

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user

from . import userstore

router = APIRouter(prefix="/api/collab/clock", tags=["clock"])

AREA = "clock"
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Alarm(BaseModel):
    id: str
    time: str
    label: str = ""
    days: list[int] = []
    enabled: bool = True


class AlarmsBody(BaseModel):
    alarms: list[Alarm]


@router.get("/alarms")
def get_alarms(user: dict = Depends(get_current_user)):
    return userstore.load(AREA, user["id"], {"alarms": []})


@router.put("/alarms")
def put_alarms(body: AlarmsBody, user: dict = Depends(get_current_user)):
    if len(body.alarms) > 50:
        raise HTTPException(400, "Höchstens 50 Wecker")
    out = []
    for a in body.alarms:
        if not _TIME.match(a.time):
            raise HTTPException(400, f"Ungültige Uhrzeit: {a.time}")
        out.append({"id": a.id[:40], "time": a.time, "label": a.label[:80],
                    "days": sorted({d for d in a.days if 0 <= d <= 6}), "enabled": a.enabled})
    data = {"alarms": out}
    userstore.save(AREA, user["id"], data)
    return data

"""
routers/recordings_routes.py
------------------------------
REST-Schnittstelle für die aufgezeichneten Remote-Screen-Sessions.

Endpunkte:
  GET    /api/recordings              -> Liste aller Aufzeichnungen (Metadaten)
  GET    /api/recordings/{id}/frames  -> alle Frames einer Aufzeichnung (zum Abspielen)
  DELETE /api/recordings/{id}         -> eine Aufzeichnung löschen (Datei + Eintrag)
"""

import json
import pathlib

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.auth import get_current_user, require_perm

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


@router.get("")
async def list_recordings(user: dict = Depends(get_current_user)):
    """Alle Aufzeichnungen als Liste (neueste zuerst)."""
    require_perm(user, "see_replay")
    recordings = db.list_recordings()
    # Dauer berechnen für die Anzeige
    for r in recordings:
        if r["ended_at"]:
            r["duration_ms"] = r["ended_at"] - r["started_at"]
        else:
            r["duration_ms"] = None
    return recordings


@router.get("/{rec_id}/frames")
async def get_recording_frames(rec_id: str, user: dict = Depends(get_current_user)):
    """
    Liefert alle Frames einer Aufzeichnung zum Abspielen.
    Jeder Frame: {t: Zeit-Offset in ms, w, h, img: Base64-JPEG}.
    """
    require_perm(user, "see_replay")
    rec = db.get_recording(rec_id)
    if not rec:
        raise HTTPException(404, "Aufzeichnung nicht gefunden")

    path = pathlib.Path(rec["file_path"])
    if not path.exists():
        raise HTTPException(404, "Aufzeichnungs-Datei fehlt (evtl. bereits gelöscht)")

    frames = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    frames.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    return {"id": rec_id, "hostname": rec["client_hostname"], "frames": frames}


@router.delete("/{rec_id}")
async def delete_recording(rec_id: str, user: dict = Depends(get_current_user)):
    """Löscht eine Aufzeichnung (Datei + Datenbank-Eintrag)."""
    require_perm(user, "delete_replay")
    rec = db.delete_recording(rec_id)
    if rec:
        try:
            pathlib.Path(rec["file_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        db.add_audit_entry(user["username"], "recording.deleted", target=rec_id)
    return {"ok": True}

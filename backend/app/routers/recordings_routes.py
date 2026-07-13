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
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app import db
from app.auth import get_current_user, require_perm, can_access_client
from app.recording import RECORDINGS_DIR

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


@router.post("/upload")
async def upload_recording(
    request: Request,
    client_id: str,
    started_at: int,
    ended_at: int,
    hostname: str = "",
    user: dict = Depends(get_current_user),
):
    """Nimmt eine im Browser aufgenommene 1:1-Video-Aufzeichnung (WebM) als
    Roh-Body entgegen und speichert sie. Metadaten als Query-Parameter -
    so wird keine multipart-Bibliothek benötigt."""
    if not can_access_client(user, client_id):
        raise HTTPException(403, "Kein Zugriff auf diesen Client")

    data = await request.body()
    if not data:
        raise HTTPException(400, "Leere Aufzeichnung")

    RECORDINGS_DIR.mkdir(exist_ok=True)
    dest = RECORDINGS_DIR / f"{client_id}_{int(time.time())}.webm"
    with open(dest, "wb") as f:
        f.write(data)

    rec_id = db.create_video_recording(
        client_id, hostname or client_id, user["username"],
        str(dest), int(started_at), int(ended_at),
    )
    db.add_audit_entry(user["username"], "guac.recording", target=client_id,
                       details=f"Video: /api/recordings/{rec_id}")
    return {"id": rec_id}


@router.get("/{rec_id}/video")
async def get_recording_video(rec_id: str, user: dict = Depends(get_current_user)):
    """Streamt die WebM-Datei einer Video-Aufzeichnung."""
    require_perm(user, "see_replay")
    rec = db.get_recording(rec_id)
    if not rec:
        raise HTTPException(404, "Aufzeichnung nicht gefunden")
    path = pathlib.Path(rec["file_path"])
    if not path.exists():
        raise HTTPException(404, "Aufzeichnungs-Datei fehlt")
    return FileResponse(str(path), media_type="video/webm", filename=path.name)


@router.get("")
async def list_recordings(user: dict = Depends(get_current_user)):
    """Alle Aufzeichnungen als Liste (neueste zuerst)."""
    require_perm(user, "see_replay")
    # Aufräumen: Replays, deren Datei nicht mehr existiert (gelöscht wurde),
    # aus der Datenbank entfernen. Das Audit-Log bleibt unberührt.
    pruned = db.prune_missing_recordings()
    if pruned:
        print(f"[recordings] {pruned} Replay-Eintrag/-Einträge ohne Datei aus der DB entfernt.")
    recordings = db.list_recordings()
    # Dauer berechnen für die Anzeige + prüfen, ob die Datei (noch) existiert.
    # (Nach dem Prune oben normalerweise immer true - der Flag schützt gegen
    # Dateien, die WÄHREND der Sitzung verschwinden.)
    for r in recordings:
        try:
            r["file_exists"] = bool(r.get("file_path")) and pathlib.Path(r["file_path"]).exists()
        except OSError:
            r["file_exists"] = True   # nicht prüfbar -> nicht fälschlich sperren
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

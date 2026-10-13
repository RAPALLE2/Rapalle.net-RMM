# notepad_routes.py (Modul "collab", Teilfunktion "notepad")
# ---------------------------------------------------------
# Notizheft pro Benutzer.
#   * Notizen OHNE Ordner sind "temporaer" (Schmierzettel). Sie bleiben bei
#     Neustart/Update erhalten, bis man auf "Aufraeumen" (clean) klickt.
#   * Ordner speichern Notizen dauerhaft - "Aufraeumen" laesst sie in Ruhe.
#
#   GET    /api/collab/notepad                  {folders, notes}
#   POST   /api/collab/notepad/notes            {title, text, folder_id}
#   PATCH  /api/collab/notepad/notes/{id}       {title?, text?, folder_id?}
#   DELETE /api/collab/notepad/notes/{id}
#   POST   /api/collab/notepad/clean            alle temporaeren Notizen loeschen
#   POST   /api/collab/notepad/folders          {name}
#   PATCH  /api/collab/notepad/folders/{id}     {name}
#   DELETE /api/collab/notepad/folders/{id}     Notizen darin werden wieder temporaer
#
# Recht: angemeldet sein (jeder sieht nur seine eigenen Notizen).

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user

from . import userstore

router = APIRouter(prefix="/api/collab/notepad", tags=["notepad"])

AREA = "notepad"
MAX_NOTES = 1000
MAX_FOLDERS = 200
MAX_TEXT = 200_000
MAX_TITLE = 200
_EMPTY = {"folders": [], "notes": []}


def _now() -> int:
    return int(time.time() * 1000)


def _load(user) -> dict:
    return userstore.load(AREA, user["id"], _EMPTY)


def _save(user, data) -> None:
    userstore.save(AREA, user["id"], data)


def _folder_ok(data: dict, folder_id: str | None) -> str | None:
    if not folder_id:
        return None
    if not any(f["id"] == folder_id for f in data["folders"]):
        raise HTTPException(404, "Ordner nicht gefunden")
    return folder_id


class NoteBody(BaseModel):
    title: str | None = None
    text: str | None = None
    folder_id: str | None = None


class FolderBody(BaseModel):
    name: str


@router.get("")
def get_all(user: dict = Depends(get_current_user)):
    return _load(user)


@router.post("/notes")
def create_note(body: NoteBody, user: dict = Depends(get_current_user)):
    data = _load(user)
    if len(data["notes"]) >= MAX_NOTES:
        raise HTTPException(400, f"Höchstens {MAX_NOTES} Notizen")
    note = {
        "id": uuid.uuid4().hex[:12],
        "title": (body.title or "")[:MAX_TITLE],
        "text": (body.text or "")[:MAX_TEXT],
        "folder_id": _folder_ok(data, body.folder_id),
        "created": _now(), "updated": _now(),
    }
    data["notes"].insert(0, note)
    _save(user, data)
    return note


@router.patch("/notes/{note_id}")
def update_note(note_id: str, body: NoteBody, user: dict = Depends(get_current_user)):
    data = _load(user)
    note = next((n for n in data["notes"] if n["id"] == note_id), None)
    if not note:
        raise HTTPException(404, "Notiz nicht gefunden")
    fields = body.model_dump(exclude_unset=True)
    if "title" in fields:
        note["title"] = (fields["title"] or "")[:MAX_TITLE]
    if "text" in fields:
        note["text"] = (fields["text"] or "")[:MAX_TEXT]
    if "folder_id" in fields:
        note["folder_id"] = _folder_ok(data, fields["folder_id"])
    note["updated"] = _now()
    _save(user, data)
    return note


@router.delete("/notes/{note_id}")
def delete_note(note_id: str, user: dict = Depends(get_current_user)):
    data = _load(user)
    data["notes"] = [n for n in data["notes"] if n["id"] != note_id]
    _save(user, data)
    return {"ok": True}


@router.post("/clean")
def clean(user: dict = Depends(get_current_user)):
    """Alle temporaeren Notizen (ohne Ordner) loeschen."""
    data = _load(user)
    before = len(data["notes"])
    data["notes"] = [n for n in data["notes"] if n.get("folder_id")]
    _save(user, data)
    return {"ok": True, "deleted": before - len(data["notes"])}


@router.post("/folders")
def create_folder(body: FolderBody, user: dict = Depends(get_current_user)):
    name = body.name.strip()[:80]
    if not name:
        raise HTTPException(400, "Name fehlt")
    data = _load(user)
    if len(data["folders"]) >= MAX_FOLDERS:
        raise HTTPException(400, f"Höchstens {MAX_FOLDERS} Ordner")
    folder = {"id": uuid.uuid4().hex[:12], "name": name, "created": _now()}
    data["folders"].append(folder)
    _save(user, data)
    return folder


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: str, body: FolderBody, user: dict = Depends(get_current_user)):
    data = _load(user)
    folder = next((f for f in data["folders"] if f["id"] == folder_id), None)
    if not folder:
        raise HTTPException(404, "Ordner nicht gefunden")
    name = body.name.strip()[:80]
    if not name:
        raise HTTPException(400, "Name fehlt")
    folder["name"] = name
    _save(user, data)
    return folder


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str, user: dict = Depends(get_current_user)):
    """Ordner loeschen - seine Notizen gehen nicht verloren, sie werden wieder temporaer."""
    data = _load(user)
    data["folders"] = [f for f in data["folders"] if f["id"] != folder_id]
    moved = 0
    for n in data["notes"]:
        if n.get("folder_id") == folder_id:
            n["folder_id"] = None
            moved += 1
    _save(user, data)
    return {"ok": True, "moved": moved}

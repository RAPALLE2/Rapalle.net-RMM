# notepad_routes.py (Modul "collab", Teilfunktion "notepad")
# ---------------------------------------------------------
# Notizheft - die Notizen sind Dateien im eigenen Space (Modul "spaces"):
#
#   Mein Space/Notizen/Schmierzettel/<Titel>.md   temporaere Notizen
#   Mein Space/Notizen/<Ordner>/<Titel>.md        dauerhaft gespeicherte Notizen
#
# Dadurch sind sie auch im Explorer und per Relay (Netzlaufwerk) erreichbar.
# "Aufraeumen" leert nur den Schmierzettel-Ordner. Der Dateiname folgt dem Titel.
#
# API (unveraendert fuer notepad.js; id = Pfad relativ zu "Notizen"):
#   GET    /api/collab/notepad                  {folders, notes}
#   POST   /api/collab/notepad/notes            {title, text, folder_id}
#   PATCH  /api/collab/notepad/notes/{id}       {title?, text?, folder_id?}  -> Notiz (id kann sich aendern!)
#   DELETE /api/collab/notepad/notes/{id}
#   POST   /api/collab/notepad/clean
#   POST   /api/collab/notepad/folders          {name}
#   PATCH  /api/collab/notepad/folders/{id}     {name}
#   DELETE /api/collab/notepad/folders/{id}     Notizen darin werden wieder temporaer
#
# Recht: "Eigenen Space benutzen" (get_user_space).

import json
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import modules
from app.auth import get_current_user
from app.paths import MODULES_DATA_DIR

router = APIRouter(prefix="/api/collab/notepad", tags=["notepad"])

NOTES = "Notizen"
TEMP = "Schmierzettel"
EXTS = (".md", ".txt")
MAX_TEXT = 200_000
_BAD = '\\/:*?"<>|\x00'


def _sp():
    vfs = modules.use("spaces", "vfs") if modules.is_enabled("spaces") else None
    if vfs is None:
        raise HTTPException(409, "Das Notizheft braucht das Modul „Spaces“.")
    return vfs


def _root(user) -> Path:
    """Mein Space/Notizen (+ Schmierzettel) - prueft das Recht und legt an."""
    vfs = _sp()
    try:
        vfs.mkdir(user, ["Mein Space", NOTES, TEMP])
        root = vfs.resolve(user, ["Mein Space", NOTES]).path
    except PermissionError as e:
        raise HTTPException(403, str(e))
    _migrate_json(user, root)
    return root


def _clean_name(name: str, fallback: str) -> str:
    n = "".join("_" if c in _BAD else c for c in (name or "")).strip().strip(".")
    return (n or fallback)[:120]


def _note_path(root: Path, note_id: str) -> Path:
    parts = [p for p in note_id.split("/") if p]
    if len(parts) != 2 or any(p in (".", "..") for p in parts) or not parts[1].lower().endswith(EXTS):
        raise HTTPException(400, "Ungültige Notiz")
    p = root / parts[0] / parts[1]
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(400, "Ungültige Notiz")
    return p


def _folder_dir(root: Path, folder_id: str | None) -> Path:
    name = folder_id or TEMP
    if name in (".", "..") or any(c in name for c in _BAD):
        raise HTTPException(400, "Ungültiger Ordner")
    d = root / name
    if folder_id and not d.is_dir():
        raise HTTPException(404, "Ordner nicht gefunden")
    return d


def _unique(d: Path, stem: str, ext: str = ".md", keep: Path | None = None) -> Path:
    p, i = d / f"{stem}{ext}", 2
    while p.exists() and p != keep:
        p, i = d / f"{stem} ({i}){ext}", i + 1
    return p


def _note(root: Path, p: Path) -> dict:
    st = p.stat()
    folder = p.parent.name
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return {"id": f"{folder}/{p.name}", "title": p.stem, "text": text,
            "folder_id": None if folder == TEMP else folder,
            "created": int(getattr(st, "st_birthtime", st.st_ctime) * 1000), "updated": int(st.st_mtime * 1000)}


def _write(user, root: Path, p: Path, text: str) -> None:
    """Ueber das Spaces-Modul schreiben (Kontingent!)."""
    rel = p.relative_to(root).parts
    try:
        _sp().write(user, ["Mein Space", NOTES, *rel], text.encode("utf-8"))
    except PermissionError as e:
        raise HTTPException(403, str(e))


def _migrate_json(user, root: Path) -> None:
    """Notizen der Vorversion (JSON je Benutzer) einmalig in Dateien umwandeln."""
    old = MODULES_DATA_DIR / "collab" / "notepad" / f"{''.join(c for c in str(user['id']) if c.isalnum() or c in '-_')}.json"
    if not old.exists():
        return
    try:
        data = json.loads(old.read_text(encoding="utf-8"))
        names = {f["id"]: _clean_name(f.get("name", ""), "Ordner") for f in data.get("folders", [])}
        for n in names.values():
            (root / n).mkdir(parents=True, exist_ok=True)
        for n in data.get("notes", []):
            d = root / (names.get(n.get("folder_id")) or TEMP)
            d.mkdir(parents=True, exist_ok=True)
            title = _clean_name(n.get("title") or (n.get("text") or "").split("\n")[0][:60], "Notiz")
            _unique(d, title).write_text(n.get("text") or "", encoding="utf-8")
        old.rename(old.with_suffix(".migrated"))
        print(f"[collab] Notizheft von {user['username']} in den Space übernommen")
    except Exception as exc:
        print(f"[collab] Notizheft-Übernahme fehlgeschlagen ({exc})")


class NoteBody(BaseModel):
    title: str | None = None
    text: str | None = None
    folder_id: str | None = None


class FolderBody(BaseModel):
    name: str


@router.get("")
def get_all(user: dict = Depends(get_current_user)):
    root = _root(user)
    folders, notes = [], []
    for d in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name != TEMP:
            folders.append({"id": d.name, "name": d.name})
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in EXTS:
                notes.append(_note(root, f))
    notes.sort(key=lambda n: n["updated"], reverse=True)
    return {"folders": folders, "notes": notes, "location": f"Mein Space/{NOTES}"}


@router.post("/notes")
def create_note(body: NoteBody, user: dict = Depends(get_current_user)):
    root = _root(user)
    d = _folder_dir(root, body.folder_id)
    title = _clean_name(body.title or "", "Notiz " + time.strftime("%Y-%m-%d %H.%M.%S"))
    p = _unique(d, title)
    _write(user, root, p, (body.text or "")[:MAX_TEXT])
    return _note(root, p)


@router.patch("/notes/{note_id:path}")
def update_note(note_id: str, body: NoteBody, user: dict = Depends(get_current_user)):
    root = _root(user)
    p = _note_path(root, note_id)
    if not p.is_file():
        raise HTTPException(404, "Notiz nicht gefunden")
    fields = body.model_dump(exclude_unset=True)
    if "text" in fields:
        _write(user, root, p, (fields["text"] or "")[:MAX_TEXT])
    target_dir = _folder_dir(root, fields["folder_id"]) if "folder_id" in fields else p.parent
    stem = p.stem
    if "title" in fields and (fields["title"] or "").strip():
        stem = _clean_name(fields["title"], p.stem)
    if target_dir != p.parent or stem != p.stem:
        new = _unique(target_dir, stem, p.suffix, keep=p)
        if new != p:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(new))
            p = new
    return _note(root, p)


@router.delete("/notes/{note_id:path}")
def delete_note(note_id: str, user: dict = Depends(get_current_user)):
    root = _root(user)
    p = _note_path(root, note_id)
    if p.is_file():
        p.unlink()
    return {"ok": True}


@router.post("/clean")
def clean(user: dict = Depends(get_current_user)):
    """Schmierzettel leeren (Ordner bleiben unangetastet)."""
    root = _root(user)
    n = 0
    for f in (root / TEMP).iterdir():
        if f.is_file():
            f.unlink(); n += 1
        elif f.is_dir():
            shutil.rmtree(f); n += 1
    return {"ok": True, "deleted": n}


@router.post("/folders")
def create_folder(body: FolderBody, user: dict = Depends(get_current_user)):
    root = _root(user)
    name = _clean_name(body.name, "")
    if not name or name == TEMP:
        raise HTTPException(400, "Ungültiger Name")
    d = root / name
    if d.exists():
        raise HTTPException(409, "Ordner existiert bereits")
    d.mkdir(parents=True)
    return {"id": name, "name": name}


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: str, body: FolderBody, user: dict = Depends(get_current_user)):
    root = _root(user)
    d = _folder_dir(root, folder_id)
    name = _clean_name(body.name, "")
    if not name or name == TEMP:
        raise HTTPException(400, "Ungültiger Name")
    if name != folder_id:
        if (root / name).exists():
            raise HTTPException(409, "Ordner existiert bereits")
        d.rename(root / name)
    return {"id": name, "name": name}


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str, user: dict = Depends(get_current_user)):
    """Ordner loeschen - die Notizen darin werden wieder temporaer (Schmierzettel)."""
    root = _root(user)
    d = _folder_dir(root, folder_id)
    moved = 0
    # ALLES verschieben (auch per Explorer abgelegte Dateien) - nichts geht verloren.
    for f in list(d.iterdir()):
        stem, ext = (f.stem, f.suffix) if f.is_file() else (f.name, "")
        shutil.move(str(f), str(_unique(root / TEMP, stem, ext)))
        moved += 1
    d.rmdir()
    return {"ok": True, "moved": moved}

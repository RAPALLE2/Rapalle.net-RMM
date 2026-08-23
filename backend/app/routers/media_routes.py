"""
routers/media_routes.py
-----------------------
Medien-Bibliothek des Audio-Players (Media-Hub).

  GET    /api/media                 -> eigene + geteilte Einträge
  POST   /api/media                 -> Link merken (youtube | spotify | radio)
  POST   /api/media/upload          -> MP3/MP4 hochladen (roher Body, wie bei
                                       Ticket-Anhängen; Dateiname als Query)
  GET    /api/media/{id}/file       -> Datei ausliefern, MIT Range-Support
                                       (nötig zum Spulen in Audio/Video)
  PATCH  /api/media/{id}            -> Titel ändern / Freigabe umschalten
  DELETE /api/media/{id}            -> Eintrag (und Datei) löschen

Rechte: 'use_media' - wer den Audio-Player benutzen darf, darf auch seine
eigene Bibliothek pflegen. Fremde Einträge sieht man nur, wenn sie als
"für alle" freigegeben sind; ändern/löschen darf sie nur der Besitzer
(Admins zum Aufräumen ebenfalls).
"""

import pathlib
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app import db
from app.auth import (get_current_user, is_super_admin, require_perm,
                      decode_access_token, user_has_permission)

router = APIRouter(prefix="/api/media", tags=["media"])

FILES_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "media_files"
MAX_BYTES = 300 * 1024 * 1024        # 300 MB pro Datei
VALID_KINDS = ("local", "youtube", "spotify", "radio")

MIME_BY_EXT = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/opus",
    ".wav": "audio/wav", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".mov": "video/quicktime",
}


def _conn():
    return db._conn


def _now() -> int:
    return int(time.time() * 1000)


def _row_or_404(item_id: str) -> dict:
    row = _conn().execute("SELECT * FROM media_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Eintrag nicht gefunden")
    return dict(row)


def _may_see(user: dict, item: dict) -> bool:
    return bool(item["shared"]) or item["owner_id"] == user["id"] or is_super_admin(user)


def _may_edit(user: dict, item: dict) -> bool:
    return item["owner_id"] == user["id"] or is_super_admin(user)


def _check_share(user: dict, shared: bool) -> int:
    """
    Etwas "für alle" bereitstellen ist ein eigenes Recht ('share_media').
    Wer es nicht hat, darf trotzdem seine eigene Bibliothek pflegen - der
    Eintrag bleibt dann eben privat, statt die Aktion ganz abzulehnen wäre
    das aber irreführend. Deshalb: klare Fehlermeldung.
    """
    if not shared:
        return 0
    if not (is_super_admin(user) or user_has_permission(user, "share_media")):
        raise HTTPException(403, "Fehlendes Recht: share_media "
                                 "(Medien für alle bereitstellen)")
    return 1


# ------------------------------------------------------------------
# Lesen
# ------------------------------------------------------------------

@router.get("")
def list_media(kind: str | None = None, favorite: bool = False,
                     user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    sql = ("SELECT * FROM media_items WHERE (owner_id = ? OR shared = 1)")
    args: list = [user["id"]]
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if favorite:
        sql += " AND favorite = 1"
    sql += " ORDER BY favorite DESC, created_at DESC"
    out = []
    for r in _conn().execute(sql, tuple(args)).fetchall():
        item = dict(r)
        item["can_edit"] = _may_edit(user, item)
        out.append(item)
    return out


# ------------------------------------------------------------------
# Link merken
# ------------------------------------------------------------------

class MediaBody(BaseModel):
    kind: str                     # youtube | spotify | radio
    title: str
    subtitle: str = ""
    url: str = ""
    shared: bool = False


@router.post("")
def create_media(body: MediaBody, user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    if body.kind not in ("youtube", "spotify", "radio"):
        raise HTTPException(400, "Ungültige Art (youtube|spotify|radio)")
    title = (body.title or "").strip()
    url = (body.url or "").strip()
    if not title or not url:
        raise HTTPException(400, "Titel und URL erforderlich")
    mid = uuid.uuid4().hex
    _conn().execute(
        "INSERT INTO media_items (id, owner_id, owner_name, kind, title, subtitle,"
        " url, shared, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, user["id"], user["username"], body.kind, title,
         (body.subtitle or "").strip(), url, int(bool(body.shared)), _now()))
    _conn().commit()
    return _row_or_404(mid)


# ------------------------------------------------------------------
# Upload (roher Body)
# ------------------------------------------------------------------

@router.post("/upload")
async def upload_media(request: Request, filename: str = "datei.mp3",
                       shared: bool = False, user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    data = await request.body()
    if not data:
        raise HTTPException(400, "Leere Datei")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"Datei zu groß (max. {MAX_BYTES // (1024*1024)} MB)")

    safe_name = re.sub(r"[^\w.\- ()\[\]äöüÄÖÜß]", "_", filename)[:180] or "datei"
    ext = pathlib.Path(safe_name).suffix.lower()
    if ext not in MIME_BY_EXT:
        raise HTTPException(400, "Nicht unterstütztes Format "
                                 f"(erlaubt: {', '.join(sorted(MIME_BY_EXT))})")

    mid = uuid.uuid4().hex
    user_dir = FILES_DIR / user["id"]
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / mid).write_bytes(data)

    _conn().execute(
        "INSERT INTO media_items (id, owner_id, owner_name, kind, title, subtitle,"
        " url, filename, size, mime, shared, created_at)"
        " VALUES (?, ?, ?, 'local', ?, '', '', ?, ?, ?, ?, ?)",
        (mid, user["id"], user["username"], pathlib.Path(safe_name).stem,
         safe_name, len(data), MIME_BY_EXT[ext], _check_share(user, shared), _now()))
    _conn().commit()
    return _row_or_404(mid)


# ------------------------------------------------------------------
# Datei ausliefern - mit Range-Support (Spulen in Audio/Video)
# ------------------------------------------------------------------

def _user_from_token(request: Request, token: str | None) -> dict:
    """<audio>/<video> können KEINE Authorization-Header setzen. Deshalb wird
    das Token bei dieser Route auch als Query-Parameter akzeptiert."""
    raw = token
    if not raw:
        auth = request.headers.get("authorization") or ""
        if auth.startswith("Bearer "):
            raw = auth.removeprefix("Bearer ").strip()
    if not raw:
        raise HTTPException(401, "Nicht angemeldet")
    payload = decode_access_token(raw)
    user = db.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(401, "Benutzer existiert nicht mehr")
    return user


@router.get("/{item_id}/file")
async def get_media_file(item_id: str, request: Request, token: str | None = None):
    user = _user_from_token(request, token)
    if not user_has_permission(user, "use_media"):
        raise HTTPException(403, "Fehlendes Recht: use_media")
    item = _row_or_404(item_id)
    if not _may_see(user, item):
        raise HTTPException(403, "Kein Zugriff auf diesen Eintrag")
    if item["kind"] != "local":
        raise HTTPException(400, "Dieser Eintrag ist keine Datei")
    path = FILES_DIR / (item["owner_id"] or "") / item_id
    if not path.exists():
        raise HTTPException(404, "Datei fehlt auf der Platte")

    size = path.stat().st_size
    mime = item["mime"] or "application/octet-stream"
    range_header = request.headers.get("range") or request.headers.get("Range")
    if not range_header:
        return FileResponse(path, media_type=mime, filename=item["filename"] or "datei",
                            headers={"Accept-Ranges": "bytes"})

    # "bytes=START-[END]" auswerten; der Browser nutzt das zum Spulen.
    m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not m:
        raise HTTPException(416, "Ungültiger Range-Header")
    start = int(m.group(1)) if m.group(1) else 0
    end = int(m.group(2)) if m.group(2) else size - 1
    start = max(0, min(start, size - 1))
    end = max(start, min(end, size - 1))
    length = end - start + 1

    def chunks(chunk_size: int = 512 * 1024):
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                data = fh.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(chunks(), status_code=206, media_type=mime, headers={
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    })


# ------------------------------------------------------------------
# Ändern / Löschen
# ------------------------------------------------------------------

class MediaPatch(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    shared: bool | None = None
    favorite: bool | None = None


@router.patch("/{item_id}")
def update_media(item_id: str, body: MediaPatch,
                       user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    item = _row_or_404(item_id)
    if not _may_edit(user, item):
        raise HTTPException(403, "Nur der Besitzer darf diesen Eintrag ändern")
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "shared" in fields:
        fields["shared"] = _check_share(user, bool(fields["shared"]))
    if "favorite" in fields:
        fields["favorite"] = int(bool(fields["favorite"]))
    if fields:
        clause = ", ".join(f"{k} = ?" for k in fields)
        _conn().execute(f"UPDATE media_items SET {clause} WHERE id = ?",
                        (*fields.values(), item_id))
        _conn().commit()
    return _row_or_404(item_id)


@router.post("/{item_id}/favorite")
def toggle_favorite(item_id: str, user: dict = Depends(get_current_user)):
    """
    Stern an/aus. Anders als das Bearbeiten darf das JEDER, der den Eintrag
    sehen darf - auch bei fremden, freigegebenen Einträgen.
    """
    require_perm(user, "use_media")
    item = _row_or_404(item_id)
    if not _may_see(user, item):
        raise HTTPException(403, "Kein Zugriff auf diesen Eintrag")
    new_value = 0 if item.get("favorite") else 1
    _conn().execute("UPDATE media_items SET favorite = ? WHERE id = ?", (new_value, item_id))
    _conn().commit()
    return _row_or_404(item_id)


@router.delete("/{item_id}")
def delete_media(item_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    item = _row_or_404(item_id)
    if not _may_edit(user, item):
        raise HTTPException(403, "Nur der Besitzer darf diesen Eintrag löschen")
    _conn().execute("DELETE FROM media_items WHERE id = ?", (item_id,))
    _conn().commit()
    if item["kind"] == "local":
        try:
            (FILES_DIR / (item["owner_id"] or "") / item_id).unlink()
        except OSError:
            pass
    return {"ok": True}


# ==================================================================
# WIEDERGABELISTEN
# ------------------------------------------------------------------
# Eine Liste bündelt beliebige Einträge der Bibliothek: Radiosender,
# hochgeladene Dateien, YouTube- und Spotify-Links - auch gemischt.
# Der Player spielt sie der Reihe nach ab.
#
#   GET    /api/media/playlists              -> eigene + freigegebene Listen
#   POST   /api/media/playlists              -> Liste anlegen
#   GET    /api/media/playlists/{id}         -> Liste samt Einträgen
#   PATCH  /api/media/playlists/{id}         -> Name/Freigabe/Favorit
#   POST   /api/media/playlists/{id}/items   -> Einträge setzen (ganze Reihenfolge)
#   DELETE /api/media/playlists/{id}         -> Liste löschen (Einträge bleiben)
# ==================================================================

class PlaylistBody(BaseModel):
    name: str
    description: str = ""
    shared: bool = False
    item_ids: list[str] = []


class PlaylistPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    shared: bool | None = None
    favorite: bool | None = None


class PlaylistItems(BaseModel):
    item_ids: list[str] = []


def _playlist_or_404(pid: str) -> dict:
    row = _conn().execute("SELECT * FROM media_playlists WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "Wiedergabeliste nicht gefunden")
    return dict(row)


def _playlist_items(pid: str, user: dict) -> list[dict]:
    """Einträge der Liste in der gespeicherten Reihenfolge - nur sichtbare."""
    rows = _conn().execute(
        "SELECT m.* FROM media_playlist_items p"
        " JOIN media_items m ON m.id = p.item_id"
        " WHERE p.playlist_id = ? ORDER BY p.position", (pid,)).fetchall()
    return [dict(r) for r in rows if _may_see(user, dict(r))]


def _set_items(pid: str, item_ids: list[str]) -> None:
    _conn().execute("DELETE FROM media_playlist_items WHERE playlist_id = ?", (pid,))
    for pos, iid in enumerate(item_ids):
        _conn().execute(
            "INSERT OR REPLACE INTO media_playlist_items (playlist_id, item_id, position)"
            " VALUES (?, ?, ?)", (pid, iid, pos))
    _conn().commit()


@router.get("/playlists")
def list_playlists(favorite: bool = False, user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    sql = "SELECT * FROM media_playlists WHERE (owner_id = ? OR shared = 1)"
    args: list = [user["id"]]
    if favorite:
        sql += " AND favorite = 1"
    sql += " ORDER BY favorite DESC, created_at DESC"
    out = []
    for r in _conn().execute(sql, tuple(args)).fetchall():
        pl = dict(r)
        pl["can_edit"] = pl["owner_id"] == user["id"] or is_super_admin(user)
        pl["items"] = _playlist_items(pl["id"], user)
        pl["count"] = len(pl["items"])
        out.append(pl)
    return out


@router.post("/playlists")
def create_playlist(body: PlaylistBody, user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name erforderlich")
    pid = uuid.uuid4().hex
    _conn().execute(
        "INSERT INTO media_playlists (id, owner_id, owner_name, name, description,"
        " shared, favorite, created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        (pid, user["id"], user["username"], name, (body.description or "").strip(),
         _check_share(user, body.shared), _now()))
    _conn().commit()
    if body.item_ids:
        _set_items(pid, body.item_ids)
    pl = _playlist_or_404(pid)
    pl["items"] = _playlist_items(pid, user)
    pl["count"] = len(pl["items"])
    pl["can_edit"] = True
    return pl


@router.get("/playlists/{pid}")
def get_playlist(pid: str, user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    pl = _playlist_or_404(pid)
    if not (pl["shared"] or pl["owner_id"] == user["id"] or is_super_admin(user)):
        raise HTTPException(403, "Kein Zugriff auf diese Wiedergabeliste")
    pl["items"] = _playlist_items(pid, user)
    pl["count"] = len(pl["items"])
    pl["can_edit"] = pl["owner_id"] == user["id"] or is_super_admin(user)
    return pl


@router.patch("/playlists/{pid}")
def update_playlist(pid: str, body: PlaylistPatch,
                          user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    pl = _playlist_or_404(pid)
    own = pl["owner_id"] == user["id"] or is_super_admin(user)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    # Den Stern darf jeder setzen, der die Liste sieht; alles andere nur der
    # Besitzer.
    if set(fields) - {"favorite"} and not own:
        raise HTTPException(403, "Nur der Besitzer darf diese Liste ändern")
    if "favorite" in fields:
        if not (pl["shared"] or own):
            raise HTTPException(403, "Kein Zugriff auf diese Wiedergabeliste")
        fields["favorite"] = int(bool(fields["favorite"]))
    if "shared" in fields:
        fields["shared"] = _check_share(user, bool(fields["shared"]))
    if fields:
        clause = ", ".join(f"{k} = ?" for k in fields)
        _conn().execute(f"UPDATE media_playlists SET {clause} WHERE id = ?",
                        (*fields.values(), pid))
        _conn().commit()
    # get_playlist() ist jetzt synchron - kein 'await' mehr.
    return get_playlist(pid, user)


@router.post("/playlists/{pid}/items")
def set_playlist_items(pid: str, body: PlaylistItems,
                             user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    pl = _playlist_or_404(pid)
    if not (pl["owner_id"] == user["id"] or is_super_admin(user)):
        raise HTTPException(403, "Nur der Besitzer darf diese Liste ändern")
    # Nur Einträge aufnehmen, die dieser Benutzer auch sehen darf.
    valid = []
    for iid in body.item_ids:
        row = _conn().execute("SELECT * FROM media_items WHERE id = ?", (iid,)).fetchone()
        if row and _may_see(user, dict(row)):
            valid.append(iid)
    _set_items(pid, valid)
    # get_playlist() ist jetzt synchron - kein 'await' mehr.
    return get_playlist(pid, user)


@router.delete("/playlists/{pid}")
def delete_playlist(pid: str, user: dict = Depends(get_current_user)):
    require_perm(user, "use_media")
    pl = _playlist_or_404(pid)
    if not (pl["owner_id"] == user["id"] or is_super_admin(user)):
        raise HTTPException(403, "Nur der Besitzer darf diese Liste löschen")
    _conn().execute("DELETE FROM media_playlist_items WHERE playlist_id = ?", (pid,))
    _conn().execute("DELETE FROM media_playlists WHERE id = ?", (pid,))
    _conn().commit()
    return {"ok": True}

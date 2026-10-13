# spaces_routes.py (Modul "spaces")
# ---------------------------------
# REST-API fuer Explorer, Spaces-App, Notizheft, Chat/Tickets und Audioplayer.
# Pfade ("path") sind die virtuellen Pfade aus vfs.py, z.B. "Mein Space/Projekte/a.txt".
#
# Dateien
#   GET    /api/spaces/fs/list?path=            Ordnerinhalt
#   GET    /api/spaces/fs/stat?path=
#   GET    /api/spaces/fs/download?path=        Datei (inline=1 -> im Browser anzeigen)
#   POST   /api/spaces/fs/upload                multipart: path (Zielordner), files[]
#   POST   /api/spaces/fs/write                 {path, text}     Textdatei schreiben
#   POST   /api/spaces/fs/mkdir                 {path}
#   POST   /api/spaces/fs/delete                {path}
#   POST   /api/spaces/fs/move                  {src, dst}
# Spaces
#   GET    /api/spaces                          meine Spaces + Rechte
#   POST   /api/spaces                          {kind: team|public, name, description, members}
#   PATCH  /api/spaces/{id}                     {name, description, members}
#   DELETE /api/spaces/{id}
#   GET    /api/spaces/directory                Benutzer + Gruppen (fuer Mitglieder/Freigaben)
# Freigaben
#   GET    /api/spaces/shares                   meine Freigaben (+ an mich)
#   POST   /api/spaces/shares                   {path, kind: users|link, users, groups, write}
#   PATCH  /api/spaces/shares/{id}
#   DELETE /api/spaces/shares/{id}
#   GET    /api/spaces/s/{token}                Link-Freigabe: Info  (Anmeldung noetig!)
#   GET    /api/spaces/s/{token}/list?path=
#   GET    /api/spaces/s/{token}/download?path=
# Anhaenge (Chat/Tickets)
#   POST   /api/spaces/attach                   multipart: file, area (chat|tickets) -> {token, url, name}

from __future__ import annotations

import mimetypes
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db
from app.auth import get_current_user

from . import access, store, vfs

router = APIRouter(prefix="/api/spaces", tags=["spaces"])

MAX_UPLOAD = 2 * 1024 * 1024 * 1024        # 2 GB je Datei
ATTACH_DIRS = {"chat": "Chat-Bilder", "tickets": "Ticket-Anhänge"}
ATTACH_TYPES = ("image/", "audio/", "video/", "application/pdf", "text/")


def _parts(path: str) -> list[str]:
    return [p for p in (path or "").replace("\\", "/").split("/") if p]


def _err(e: Exception):
    if isinstance(e, PermissionError):
        raise HTTPException(403, str(e) or "Kein Zugriff")
    if isinstance(e, (FileNotFoundError, KeyError)):
        raise HTTPException(404, "Nicht gefunden")
    if isinstance(e, FileExistsError):
        raise HTTPException(409, f"Existiert bereits: {e}")
    if isinstance(e, (NotADirectoryError, IsADirectoryError, ValueError)):
        raise HTTPException(400, str(e))
    raise HTTPException(500, f"Fehler: {e}")


def _audit(user, action: str, detail: str) -> None:
    try:
        db.add_audit_entry(user["username"], f"spaces.{action}", details=detail[:300])
    except Exception:
        pass


def _send(p: Path, inline: bool):
    mt = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    disp = "inline" if inline else "attachment"
    return FileResponse(p, media_type=mt, filename=p.name, content_disposition_type=disp)


# ================================================================== Dateien
class PathBody(BaseModel):
    path: str


class WriteBody(BaseModel):
    path: str
    text: str


class MoveBody(BaseModel):
    src: str
    dst: str


@router.get("/fs/list")
def fs_list(path: str = "", user: dict = Depends(get_current_user)):
    try:
        t = vfs.resolve(user, _parts(path))
        entries = vfs.listdir(user, _parts(path))
        return {"path": path, "entries": entries, "virtual": t.virtual is not None,
                "writable": t.virtual is None and t.level >= access.WRITE,
                "space_id": t.space_id or None, "space_rel": t.space_rel if t.virtual is None else None,
                "can_share": t.virtual is None and t.level >= access.WRITE and not t.root_rel}
    except Exception as e:
        _err(e)


@router.get("/fs/stat")
def fs_stat(path: str, user: dict = Depends(get_current_user)):
    e = vfs.stat(user, _parts(path))
    if not e:
        raise HTTPException(404, "Nicht gefunden")
    return e


@router.get("/fs/download")
def fs_download(path: str, inline: int = 0, user: dict = Depends(get_current_user)):
    try:
        return _send(vfs.file_path(user, _parts(path)), bool(inline))
    except Exception as e:
        _err(e)


@router.post("/fs/upload")
async def fs_upload(path: str = Form(""), files: list[UploadFile] = File(...),
                    user: dict = Depends(get_current_user)):
    saved = []
    for f in files:
        name = store.safe_segment(Path(f.filename or "datei").name)
        data = await f.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"{name}: zu groß")
        try:
            vfs.write(user, _parts(path) + [name], data)
        except Exception as e:
            _err(e)
        saved.append(name)
    _audit(user, "upload", f"{path}: {', '.join(saved)}")
    return {"ok": True, "saved": saved}


@router.post("/fs/write")
def fs_write(body: WriteBody, user: dict = Depends(get_current_user)):
    try:
        vfs.write(user, _parts(body.path), body.text.encode("utf-8"))
    except Exception as e:
        _err(e)
    return {"ok": True}


@router.post("/fs/mkdir")
def fs_mkdir(body: PathBody, user: dict = Depends(get_current_user)):
    try:
        vfs.mkdir(user, _parts(body.path))
    except Exception as e:
        _err(e)
    return {"ok": True}


@router.post("/fs/delete")
def fs_delete(body: PathBody, user: dict = Depends(get_current_user)):
    try:
        vfs.delete(user, _parts(body.path))
    except Exception as e:
        _err(e)
    _audit(user, "delete", body.path)
    return {"ok": True}


@router.post("/fs/move")
def fs_move(body: MoveBody, user: dict = Depends(get_current_user)):
    try:
        vfs.move(user, _parts(body.src), _parts(body.dst))
    except Exception as e:
        _err(e)
    _audit(user, "move", f"{body.src} -> {body.dst}")
    return {"ok": True}


# ================================================================== Spaces
class SpaceBody(BaseModel):
    kind: str = "team"
    name: str
    description: str = ""
    members: list[dict] = []


class SpacePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    members: list[dict] | None = None


def _public_space(user, sp: dict, gids) -> dict:
    lv = access.level(user, sp, gids)
    d = {k: sp.get(k) for k in ("id", "kind", "name", "description", "owner", "members", "created")}
    d["level"] = lv
    d["owner_name"] = vfs._user_label(sp.get("owner")) if sp.get("owner") else ""
    return d


@router.get("")
def my_spaces(user: dict = Depends(get_current_user)):
    gids = access.group_ids(user)
    out = []
    for sp in store.list_spaces():
        if access.level(user, sp, gids) >= access.READ:
            out.append(_public_space(user, sp, gids))
    personal = None
    if access.has(user, "get_user_space"):
        sid = store.personal_id(user["id"])
        personal = {"id": sid, "kind": "personal", "name": vfs.MY,
                    "used": store.dir_size(store.space_dir(sid))}
    try:
        from app import modules
        quota = float(modules.setting("spaces", "quota_mb", 0) or 0)
    except Exception:
        quota = 0
    return {"personal": personal, "spaces": out, "quota_mb": quota,
            "perms": {p: access.has(user, p) for p in access.PERMS},
            "can_create": {"team": access.may_create(user, "team"), "public": access.may_create(user, "public")}}


@router.post("")
def create_space(body: SpaceBody, user: dict = Depends(get_current_user)):
    if body.kind not in ("team", "public"):
        raise HTTPException(400, "Art muss team oder public sein")
    if not access.may_create(user, body.kind):
        raise HTTPException(403, "Keine Berechtigung, diese Art von Space anzulegen")
    try:
        sp = store.create_space(body.kind, body.name, user["id"], body.members, body.description)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _audit(user, "space.create", f"{sp['id']} {sp['name']}")
    return _public_space(user, sp, access.group_ids(user))


def _admin_of(user, space_id: str) -> dict:
    sp = store.get_space(space_id)
    if not sp or sp["kind"] == "personal":
        raise HTTPException(404, "Space nicht gefunden")
    if access.level(user, sp) < access.ADMIN:
        raise HTTPException(403, "Nur Space-Admins dürfen das")
    return sp


@router.patch("/{space_id}")
def patch_space(space_id: str, body: SpacePatch, user: dict = Depends(get_current_user)):
    _admin_of(user, space_id)
    try:
        sp = store.update_space(space_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    _audit(user, "space.update", space_id)
    return _public_space(user, sp, access.group_ids(user))


@router.delete("/{space_id}")
def remove_space(space_id: str, user: dict = Depends(get_current_user)):
    sp = _admin_of(user, space_id)
    store.delete_space(space_id)
    _audit(user, "space.delete", f"{space_id} {sp['name']}")
    return {"ok": True}


@router.get("/directory")
def directory(user: dict = Depends(get_current_user)):
    return {"users": [{"id": u["id"], "username": u["username"], "display_name": u.get("display_name") or ""}
                      for u in db.list_users()],
            "groups": [{"id": g["id"], "name": g["name"]} for g in db.list_groups()]}


# ================================================================== Freigaben
class ShareBody(BaseModel):
    path: str
    kind: str = "users"            # users | link
    users: list[str] = []
    groups: list[str] = []
    write: bool = False
    expires_days: int | None = None


class SharePatch(BaseModel):
    users: list[str] | None = None
    groups: list[str] | None = None
    write: bool | None = None


def _share_out(sh: dict) -> dict:
    d = dict(sh)
    d["owner_name"] = vfs._user_label(sh["owner"])
    sp = store.get_space(sh["space_id"])
    d["space_name"] = sp["name"] if sp else "?"
    d["url"] = f"/api/spaces/s/{sh['token']}" if sh.get("token") else None
    p = store.space_dir(sh["space_id"]) / sh["path"]
    d["is_dir"] = p.is_dir()
    d["exists"] = p.exists()
    return d


@router.get("/shares")
def list_my_shares(user: dict = Depends(get_current_user)):
    gids = access.group_ids(user)
    mine, to_me = [], []
    for sh in store.list_shares():
        if sh["kind"] == "attachment":
            continue
        if sh["owner"] == user["id"]:
            mine.append(_share_out(sh))
        elif access.share_applies(user, sh, gids):
            to_me.append(_share_out(sh))
    return {"mine": mine, "to_me": to_me}


@router.post("/shares")
def create_share(body: ShareBody, user: dict = Depends(get_current_user)):
    if body.kind not in ("users", "link"):
        raise HTTPException(400, "Art muss users oder link sein")
    try:
        t = vfs.resolve(user, _parts(body.path))
    except Exception as e:
        _err(e)
    if t.virtual is not None or t.level < access.WRITE or t.root_rel:
        raise HTTPException(403, "Freigeben geht nur in eigenen Spaces bzw. mit Schreibrecht")
    if not t.rel:
        raise HTTPException(400, "Bitte einen Ordner oder eine Datei im Space wählen")
    if not t.path.exists():
        raise HTTPException(404, "Nicht gefunden")
    if body.kind == "users" and not (body.users or body.groups):
        raise HTTPException(400, "Bitte mindestens einen Benutzer oder eine Gruppe wählen")
    exp = int((time.time() + body.expires_days * 86400) * 1000) if body.expires_days else None
    sh = store.create_share(space_id=t.space_id, path=t.space_rel, owner=user["id"], kind=body.kind,
                            users=body.users, groups=body.groups, write=body.write, expires=exp)
    _audit(user, "share.create", f"{body.kind} {t.space_id}:{t.space_rel}")
    return _share_out(sh)


def _own_share(user, share_id: str) -> dict:
    sh = store.get_share(share_id)
    if not sh:
        raise HTTPException(404, "Freigabe nicht gefunden")
    if sh["owner"] != user["id"] and not access.has(user, "edit_all_spaces"):
        raise HTTPException(403, "Nur der Besitzer kann die Freigabe ändern")
    return sh


@router.patch("/shares/{share_id}")
def patch_share(share_id: str, body: SharePatch, user: dict = Depends(get_current_user)):
    _own_share(user, share_id)
    return _share_out(store.update_share(share_id, body.model_dump(exclude_unset=True)))


@router.delete("/shares/{share_id}")
def remove_share(share_id: str, user: dict = Depends(get_current_user)):
    _own_share(user, share_id)
    store.delete_share(share_id)
    _audit(user, "share.delete", share_id)
    return {"ok": True}


# --- Link-Freigaben: jeder ANGEMELDETE Benutzer mit dem Link ---------------
def _by_token(token: str) -> tuple[dict, Path]:
    sh = store.share_by_token(token)
    if not sh or (sh.get("expires") and sh["expires"] < time.time() * 1000):
        raise HTTPException(404, "Freigabe nicht gefunden oder abgelaufen")
    root = store.space_dir(sh["space_id"])
    for seg in [s for s in sh["path"].split("/") if s]:
        root = root / store.safe_segment(seg)
    if not root.exists():
        raise HTTPException(404, "Das freigegebene Ziel existiert nicht mehr")
    return sh, root


def _inside(root: Path, path: str) -> Path:
    p = root
    for seg in _parts(path):
        p = p / store.safe_segment(seg)
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(403, "Pfad außerhalb der Freigabe")
    return p


@router.get("/s/{token}")
def link_info(token: str, user: dict = Depends(get_current_user)):
    sh, root = _by_token(token)
    return {"name": sh["name"], "owner_name": vfs._user_label(sh["owner"]), "is_dir": root.is_dir(),
            "size": 0 if root.is_dir() else root.stat().st_size, "kind": sh["kind"]}


@router.get("/s/{token}/list")
def link_list(token: str, path: str = "", user: dict = Depends(get_current_user)):
    _, root = _by_token(token)
    p = _inside(root, path)
    if not p.is_dir():
        raise HTTPException(400, "Kein Ordner")
    return {"entries": [vfs._entry(c) for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))]}


@router.get("/s/{token}/download")
def link_download(token: str, path: str = "", inline: int = 0, user: dict = Depends(get_current_user)):
    _, root = _by_token(token)
    p = _inside(root, path) if path else root
    if not p.is_file():
        raise HTTPException(404, "Keine Datei")
    return _send(p, bool(inline))


# ================================================================== Anhaenge
@router.post("/attach")
async def attach(file: UploadFile = File(...), area: str = Form("chat"), user: dict = Depends(get_current_user)):
    """Bild/Datei aus Chat oder Ticket: landet im Space des Absenders, Empfaenger
    lesen es ueber einen Anhang-Link (nur angemeldet)."""
    folder = ATTACH_DIRS.get(area)
    if not folder:
        raise HTTPException(400, "Unbekannter Bereich")
    if not access.has(user, "get_user_space"):
        raise HTTPException(403, "Für Anhänge wird ein eigener Space benötigt (Recht „Eigenen Space benutzen“)")
    mt = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if not mt.startswith(ATTACH_TYPES):
        raise HTTPException(400, "Dieser Dateityp kann nicht angehängt werden")
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, "Anhang zu groß (max. 50 MB)")
    base = store.safe_segment(Path(file.filename or "bild").name)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    name = f"{stamp}_{base}"
    rel = f"{folder}/{time.strftime('%Y-%m')}/{name}"
    try:
        vfs.write(user, [vfs.MY] + rel.split("/"), data)
    except Exception as e:
        _err(e)
    sh = store.create_share(space_id=store.personal_id(user["id"]), path=rel, owner=user["id"],
                            kind="attachment", name=base)
    return {"token": sh["token"], "url": f"/api/spaces/s/{sh['token']}/download?inline=1",
            "name": base, "mime": mt, "size": len(data)}

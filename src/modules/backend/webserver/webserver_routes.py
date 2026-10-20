# webserver_routes.py (Modul "webserver") - Websites verwalten
# ------------------------------------------------------------
#   GET    /api/webserver/sites                 meine Websites (bzw. alle mit manage_websites)
#   POST   /api/webserver/sites                 {name, title}               (manage_websites)
#   PATCH  /api/webserver/sites/{name}          {title, public, listing, members, new_name}  (Website-Admin)
#   DELETE /api/webserver/sites/{name}          (manage_websites) - loescht auch die Dateien
#   GET    /api/webserver/reserved              gesperrte Namen (fuer den Anlege-Dialog)
#   Dateien (Mitglied "editor" oder mehr) - gleiche Form wie /api/deployment/...:
#   GET    /api/webserver/sites/{name}/files?path=      POST .../upload (multipart path, files[])
#   POST   .../mkdir {path}   POST .../delete {path}   POST .../move {src, dst}   GET .../download?path=
# Die oeffentlichen Seiten (rmm.url.de/<name>) haengt sites.sync_routes() ein.

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.responses import FileResponse

from app import db
from app.auth import get_current_user, require_perm

from . import sites, webhost

router = APIRouter(prefix="/api/webserver", tags=["webserver"])


def _err(e: Exception):
    if isinstance(e, PermissionError):
        raise HTTPException(403, str(e))
    if isinstance(e, (FileNotFoundError, KeyError)):
        raise HTTPException(404, "Nicht gefunden")
    if isinstance(e, FileExistsError):
        raise HTTPException(409, f"Existiert bereits: {e}")
    if isinstance(e, ValueError):
        raise HTTPException(400, str(e))
    raise HTTPException(500, str(e))


def _site(user, name: str, need: int = sites.EDIT) -> dict:
    s = sites.get(name)
    if not s or sites.level(user, s) < need:
        raise HTTPException(404 if not s or not sites.level(user, s) else 403,
                            "Website nicht gefunden" if not s or not sites.level(user, s) else "Nur Website-Admins")
    return s


def _public(s: dict, user) -> dict:
    return {"name": s["name"], "title": s.get("title"), "public": bool(s.get("public")), "listing": bool(s.get("listing", True)),
            "kind": s.get("kind") or "static", "upstream": s.get("upstream"),
            "members": s.get("members") or [], "created": s.get("created"), "created_by": s.get("created_by"),
            "level": sites.level(user, s), "url": f"/{s['name']}/"}


# ------------------------------------------------------------------ Websites
class CreateBody(BaseModel):
    name: str
    title: str = ""


class PatchBody(BaseModel):
    title: str | None = None
    public: bool | None = None
    listing: bool | None = None
    members: list[dict] | None = None
    new_name: str | None = None


@router.get("/sites")
def list_sites(user: dict = Depends(get_current_user)):
    return {"sites": [_public({**s}, user) for s in sites.visible(user)], "manage": sites.manage_all(user)}


@router.get("/directory")
def directory(user: dict = Depends(get_current_user)):
    """Benutzer + Gruppen (nur Namen) fuer die Mitglieder-Auswahl - fuer Website-Admins."""
    if not sites.manage_all(user) and not any(s["level"] >= sites.ADMIN for s in sites.visible(user)):
        raise HTTPException(403, "Nur für Website-Admins")
    return {"users": [{"id": u["id"], "username": u["username"], "display_name": u.get("display_name") or ""}
                      for u in db.list_users()],
            "groups": [{"id": g["id"], "name": g["name"], "is_ad_group": g.get("is_ad_group"),
                        "permissions": g.get("permissions")} for g in db.list_groups()]}


@router.get("/reserved")
def reserved(user: dict = Depends(get_current_user)):
    return {"reserved": sorted(sites.reserved())}


@router.post("/sites")
def create_site(body: CreateBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_websites")
    try:
        s = sites.create(body.name, body.title, user)
    except Exception as e:
        _err(e)
    db.add_audit_entry(user["username"], "webserver.site_created", details=s["name"])
    return _public(s, user)


@router.patch("/sites/{name}")
def patch_site(name: str, body: PatchBody, user: dict = Depends(get_current_user)):
    _site(user, name, sites.ADMIN)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if "new_name" in changes and not sites.manage_all(user):
        raise HTTPException(403, "Umbenennen nur mit dem Recht „Websites verwalten“")
    try:
        s = sites.update(name, changes)
    except Exception as e:
        _err(e)
    db.add_audit_entry(user["username"], "webserver.site_changed", details=f"{name}: {', '.join(changes)}")
    return _public(s, user)


@router.delete("/sites/{name}")
def delete_site(name: str, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_websites")
    try:
        sites.remove(name)
    except Exception as e:
        _err(e)
    db.add_audit_entry(user["username"], "webserver.site_deleted", details=name)
    return {"ok": True}


# ------------------------------------------------------------------ Dateien
class PathBody(BaseModel):
    path: str


class MoveBody(BaseModel):
    src: str
    dst: str


def _root(user, name) -> Path:
    s = _site(user, name)
    if s.get("kind") == "proxy":
        raise HTTPException(409, "Diese Website ist eine Weiterleitung – sie hat keine Dateien")
    return sites.folder(s["name"])


@router.get("/sites/{name}/files")
def files(name: str, path: str = "", user: dict = Depends(get_current_user)):
    root = _root(user, name)
    s = sites.get(name)
    try:
        return {"path": path, "entries": webhost.listdir(root, path), "public": bool(s.get("public")),
                "url": f"/{name}/"}
    except Exception as e:
        _err(e)


@router.post("/sites/{name}/upload")
async def upload(name: str, path: str = Form(""), files: list[UploadFile] = File(...), user: dict = Depends(get_current_user)):
    root = _root(user, name)
    saved = []
    for f in files:
        fn = Path(f.filename or "datei").name
        try:
            webhost.write(root, path.rstrip("/") + "/" + fn, await f.read())
        except Exception as e:
            _err(e)
        saved.append(fn)
    db.add_audit_entry(user["username"], "webserver.upload", details=f"{name}/{path}: {', '.join(saved)}"[:300])
    return {"ok": True, "saved": saved}


@router.post("/sites/{name}/mkdir")
def mkdir(name: str, body: PathBody, user: dict = Depends(get_current_user)):
    try:
        webhost.mkdir(_root(user, name), body.path)
    except HTTPException:
        raise
    except Exception as e:
        _err(e)
    return {"ok": True}


@router.post("/sites/{name}/delete")
def delete(name: str, body: PathBody, user: dict = Depends(get_current_user)):
    try:
        webhost.delete(_root(user, name), body.path)
    except HTTPException:
        raise
    except Exception as e:
        _err(e)
    db.add_audit_entry(user["username"], "webserver.delete", details=f"{name}/{body.path}"[:300])
    return {"ok": True}


@router.post("/sites/{name}/move")
def move(name: str, body: MoveBody, user: dict = Depends(get_current_user)):
    try:
        webhost.move(_root(user, name), body.src, body.dst)
    except HTTPException:
        raise
    except Exception as e:
        _err(e)
    return {"ok": True}


@router.get("/sites/{name}/download")
def download(name: str, path: str, user: dict = Depends(get_current_user)):
    root = _root(user, name)
    try:
        p = webhost.resolve(root, path)
    except Exception as e:
        _err(e)
    if not p.is_file():
        raise HTTPException(404, "Nicht gefunden")
    return FileResponse(p, filename=p.name)

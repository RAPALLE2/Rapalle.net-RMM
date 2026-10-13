"""
routers/storage_routes.py
-------------------------
Zwei Dinge:

1. Eine schlanke REST-API fuer die server-eigenen Relay-Ordner
   (Storage / Deployment) - damit der Explorer im Dashboard sie anzeigen,
   hoch- und herunterladen kann, ohne den Umweg ueber WebDAV.

2. (Die oeffentliche Deployment-Seite /deployment und ihre Einstellungen
   gehoeren jetzt zum Modul "deployment" - src/modules/backend/deployment.)
"""

import mimetypes
import urllib.parse as _up

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from starlette.responses import HTMLResponse, PlainTextResponse, Response

from app import db
from rmm_mod_relay import relay_storage as rs
from app.auth import get_current_user, require_perm

router = APIRouter(tags=["storage"])

def deployment_public() -> bool:
    """Ist die oeffentliche Deployment-Seite freigeschaltet? (Modul "deployment")"""
    try:
        from app import modules
        dep = modules.use("deployment", "deploy_store") if modules.is_enabled("deployment") else None
        return bool(dep and dep.is_public())
    except Exception:
        return False


# ==================================================================
# REST-API (Dashboard-Login)
# ==================================================================

def _section(name: str) -> str:
    sec = rs.section_of(name)
    if not sec:
        raise HTTPException(404, "Unbekannter Ordner")
    return sec


@router.get("/api/storage/sections")
def storage_sections(user: dict = Depends(get_current_user)):
    """Welche Ordner gibt es und darf ich darin schreiben?"""
    if not rs.may_read(user):
        raise HTTPException(403, "Fehlendes Recht: use_relay")
    return {
        # Nur Ordner auflisten, die dieser Benutzer auch lesen darf -
        # "Source" braucht 'see_source'.
        "sections": [
            {"name": name, "writable": rs.may_write(user, name),
             "public": name == "Deployment" and deployment_public()}
            for name in rs.display_names() if rs.may_read(user, name)
        ],
        "deployment_public": deployment_public(),
    }


@router.get("/api/storage/list")
def storage_list(section: str, path: str = "",
                       user: dict = Depends(get_current_user)):
    sec = _section(section)
    if not rs.may_read(user, sec):
        raise HTTPException(403, "Fehlendes Recht: use_relay")
    try:
        return {"section": sec, "path": path, "entries": rs.listdir(sec, path, user=user),
                "writable": rs.may_write(user, sec)}
    except FileNotFoundError:
        raise HTTPException(404, "Ordner nicht gefunden")
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.get("/api/storage/download")
def storage_download(section: str, path: str,
                           user: dict = Depends(get_current_user)):
    sec = _section(section)
    if not rs.may_read(user, sec):
        raise HTTPException(403, "Fehlendes Recht: use_relay")
    try:
        data = rs.read(sec, path, user=user)
    except FileNotFoundError:
        raise HTTPException(404, "Datei nicht gefunden")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    name = path.replace("\\", "/").rsplit("/", 1)[-1] or "download"
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return Response(content=data, media_type=ctype, headers={
        "Content-Disposition": f'attachment; filename="{_up.quote(name)}"'})


@router.post("/api/storage/upload")
async def storage_upload(section: str, path: str = "",
                         file: UploadFile = File(...),
                         user: dict = Depends(get_current_user)):
    sec = _section(section)
    if not rs.may_write(user, sec):
        raise HTTPException(403, f"Schreiben in '{sec}' nicht erlaubt")
    target = (path.rstrip("/") + "/" + (file.filename or "datei")).lstrip("/")
    try:
        rs.write(sec, target, await file.read(), user=user)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    db.add_audit_entry(user["username"], "relay.storage.upload",
                       target=sec, details=target)
    return {"ok": True, "path": target}


class PathBody(BaseModel):
    section: str
    path: str = ""
    dst: str | None = None


@router.post("/api/storage/mkdir")
def storage_mkdir(body: PathBody, user: dict = Depends(get_current_user)):
    sec = _section(body.section)
    if not rs.may_write(user, sec):
        raise HTTPException(403, f"Schreiben in '{sec}' nicht erlaubt")
    try:
        rs.mkdir(sec, body.path, user=user)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


@router.post("/api/storage/delete")
def storage_delete(body: PathBody, user: dict = Depends(get_current_user)):
    sec = _section(body.section)
    if not rs.may_write(user, sec):
        raise HTTPException(403, f"Schreiben in '{sec}' nicht erlaubt")
    try:
        rs.delete(sec, body.path, user=user)
    except FileNotFoundError:
        raise HTTPException(404, "Nicht gefunden")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    db.add_audit_entry(user["username"], "relay.storage.delete",
                       target=sec, details=body.path)
    return {"ok": True}


@router.post("/api/storage/move")
def storage_move(body: PathBody, user: dict = Depends(get_current_user)):
    sec = _section(body.section)
    if not rs.may_write(user, sec):
        raise HTTPException(403, f"Schreiben in '{sec}' nicht erlaubt")
    if not body.dst:
        raise HTTPException(400, "Ziel fehlt")
    try:
        rs.move(sec, body.path, body.dst, user=user)
    except FileNotFoundError:
        raise HTTPException(404, "Nicht gefunden")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}

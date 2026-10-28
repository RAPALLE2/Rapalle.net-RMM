# deployment_routes.py (Modul "deployment")
# -----------------------------------------
# Deployment-Server (frueher Teil des Relay-Moduls):
#
#   Oeffentliche Seite (OHNE Anmeldung, nur wenn freigeschaltet):
#     GET /deployment                 eigene HTML-Seite oder automatische Dateiliste
#     GET /deployment/<pfad>          Datei/Unterordner
#   Einstellungen (Recht manage_deployment; URLs unveraendert fuer api.js):
#     GET/POST /api/storage/deployment    {html, title, public}
#   Dateien verwalten (Recht manage_deployment) - geht auch ohne Relay-Modul:
#     GET  /api/deployment/files?path=     Liste
#     POST /api/deployment/upload          multipart: path, files[]
#     POST /api/deployment/mkdir           {path}
#     POST /api/deployment/delete          {path}
#     POST /api/deployment/move            {src, dst}
#     GET  /api/deployment/download?path=
#
# ACHTUNG: Alles im Ordner ist bei aktiver Freigabe fuer jeden mit dem Link
# abrufbar. Der Schalter steht standardmaessig auf AUS.

import mimetypes
import urllib.parse as _up
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from app import db
from app.auth import get_current_user, require_perm, user_has_permission

from . import deploy_store as store

router = APIRouter(tags=["deployment"])


def _manage(user) -> None:
    # Recht "Deployment-Server verwalten" (Rechteverwaltung -> Allgemein)
    require_perm(user, "manage_deployment")


def _err(e: Exception):
    if isinstance(e, PermissionError):
        raise HTTPException(403, str(e))
    if isinstance(e, FileNotFoundError):
        raise HTTPException(404, "Nicht gefunden")
    if isinstance(e, FileExistsError):
        raise HTTPException(409, f"Existiert bereits: {e}")
    raise HTTPException(500, str(e))


class PathBody(BaseModel):
    path: str


class MoveBody(BaseModel):
    src: str
    dst: str


@router.get("/api/deployment/files")
def files(path: str = "", user: dict = Depends(get_current_user)):
    _manage(user)
    try:
        return {"path": path, "entries": store.listdir(path), "public": store.is_public()}
    except Exception as e:
        _err(e)


@router.post("/api/deployment/upload")
async def upload(path: str = Form(""), files: list[UploadFile] = File(...), user: dict = Depends(get_current_user)):
    _manage(user)
    saved = []
    for f in files:
        name = Path(f.filename or "datei").name
        try:
            store.write(path.rstrip("/") + "/" + name, await f.read())
        except Exception as e:
            _err(e)
        saved.append(name)
    db.add_audit_entry(user["username"], "deployment.upload", details=f"{path}: {', '.join(saved)}"[:300])
    return {"ok": True, "saved": saved}


@router.post("/api/deployment/mkdir")
def mkdir(body: PathBody, user: dict = Depends(get_current_user)):
    _manage(user)
    try:
        store.mkdir(body.path)
    except Exception as e:
        _err(e)
    return {"ok": True}


@router.post("/api/deployment/delete")
def delete(body: PathBody, user: dict = Depends(get_current_user)):
    _manage(user)
    try:
        store.delete(body.path)
    except Exception as e:
        _err(e)
    db.add_audit_entry(user["username"], "deployment.delete", details=body.path[:300])
    return {"ok": True}


@router.post("/api/deployment/move")
def move(body: MoveBody, user: dict = Depends(get_current_user)):
    _manage(user)
    try:
        store.move(body.src, body.dst)
    except Exception as e:
        _err(e)
    return {"ok": True}


@router.get("/api/deployment/download")
def download(path: str, user: dict = Depends(get_current_user)):
    _manage(user)
    try:
        p = store.resolve(path)
    except Exception as e:
        _err(e)
    if not p.is_file():
        raise HTTPException(404, "Nicht gefunden")
    return FileResponse(p, filename=p.name)


# ------------------------------------------------------------------
# Einstellungen der Deployment-Seite
# ------------------------------------------------------------------

class DeploySettings(BaseModel):
    html: str | None = None
    public: bool | None = None
    title: str | None = None


@router.get("/api/storage/deployment")
def get_deployment(user: dict = Depends(get_current_user)):
    _manage(user)
    return store.settings()


@router.post("/api/storage/deployment")
def set_deployment(body: DeploySettings,
                         user: dict = Depends(get_current_user)):
    _manage(user)
    if body.html is not None:
        db.set_setting(store.SETTING_HTML, body.html)
    if body.title is not None:
        db.set_setting(store.SETTING_TITLE, body.title.strip() or "Deployment")
    if body.public is not None:
        db.set_setting(store.SETTING_PUBLIC, "1" if body.public else "0")
        db.add_audit_entry(user["username"], "deployment.public",
                           details="an" if body.public else "aus")
    return {"ok": True, "public": store.is_public()}


# ==================================================================
# Oeffentliche Seite: /deployment  und  /deployment/<datei>
# ==================================================================
# KEIN Login. Deshalb haengt alles am Schalter 'deployment_public'.

_OFF = PlainTextResponse("Die Deployment-Seite ist nicht freigegeben.",
                         status_code=404)


def _serve(parts: list[str]):
    """Ausliefern ueber den Hosting-Kern des Moduls "webserver" (gleicher Code wie fuer Websites):
    eigene HTML-Startseite (Einstellung), sonst index.html, sonst automatische Dateiliste."""
    from app import modules
    wh = modules.use("webserver", "webhost")
    return wh.serve(store.base(), parts, "/deployment",
                    title=db.get_setting(store.SETTING_TITLE) or "Deployment",
                    custom_html=db.get_setting(store.SETTING_HTML) or "", listing=True)


@router.get("/deployment", include_in_schema=False)
@router.get("/deployment/", include_in_schema=False)
def deployment_index():
    if not store.is_public():
        return _OFF
    return _serve([])


@router.get("/deployment/{full_path:path}", include_in_schema=False)
def deployment_file(full_path: str, request: Request):
    if not store.is_public():
        return _OFF
    return _serve([p for p in full_path.split("/") if p])

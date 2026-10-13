# deployment_routes.py (Modul "deployment")
# -----------------------------------------
# Deployment-Server (frueher Teil des Relay-Moduls):
#
#   Oeffentliche Seite (OHNE Anmeldung, nur wenn freigeschaltet):
#     GET /deployment                 eigene HTML-Seite oder automatische Dateiliste
#     GET /deployment/<pfad>          Datei/Unterordner
#   Einstellungen (Recht manage_settings; URLs unveraendert fuer api.js):
#     GET/POST /api/storage/deployment    {html, title, public}
#   Dateien verwalten (Recht manage_settings) - geht auch ohne Relay-Modul:
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
    if user_has_permission(user, "admin_settings"):
        return
    require_perm(user, "manage_settings")


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


def _default_page(title: str) -> str:
    """Einfache Dateiliste, solange kein eigener HTML-Code hinterlegt ist."""
    try:
        entries = store.listdir("")
    except Exception:
        entries = []
    import xml.sax.saxutils as _x
    rows = "".join(
        f'<li><a href="/deployment/{_up.quote(e["name"])}">'
        f'{"📁" if e["is_dir"] else "📄"} {_x.escape(e["name"])}</a></li>'
        for e in entries)
    return (f"<!doctype html><meta charset=utf-8><title>{_x.escape(title)}</title>"
            "<body style=\"font-family:system-ui,sans-serif;max-width:720px;"
            "margin:40px auto;padding:0 16px\">"
            f"<h1>{_x.escape(title)}</h1>"
            f"<ul>{rows or '<li>(noch nichts abgelegt)</li>'}</ul></body>")


@router.get("/deployment", include_in_schema=False)
@router.get("/deployment/", include_in_schema=False)
def deployment_index():
    if not store.is_public():
        return _OFF
    html = (db.get_setting(store.SETTING_HTML) or "").strip()
    title = db.get_setting(store.SETTING_TITLE) or "Deployment"
    if not html:
        html = _default_page(title)
    return HTMLResponse(content=html)


@router.get("/deployment/{full_path:path}", include_in_schema=False)
def deployment_file(full_path: str, request: Request):
    if not store.is_public():
        return _OFF
    parts = [p for p in full_path.split("/") if p]
    if not parts:
        # deployment_index() ist jetzt synchron - kein 'await' mehr.
        return deployment_index()
    try:
        p = store.resolve(parts)
    except PermissionError:
        return PlainTextResponse("Nicht erlaubt", status_code=403)
    if not p.exists():
        return PlainTextResponse("Nicht gefunden", status_code=404)
    if p.is_dir():
        # Unterordner ebenfalls als Liste anzeigen.
        import xml.sax.saxutils as _x
        base = "/deployment/" + "/".join(_up.quote(p) for p in parts)
        rows = "".join(
            f'<li><a href="{base}/{_up.quote(e["name"])}">'
            f'{"📁" if e["is_dir"] else "📄"} {_x.escape(e["name"])}</a></li>'
            for e in store.listdir(parts))
        return HTMLResponse(
            f"<!doctype html><meta charset=utf-8><title>{_x.escape(parts[-1])}</title>"
            "<body style=\"font-family:system-ui,sans-serif;max-width:720px;"
            f"margin:40px auto\"><h1>{_x.escape(parts[-1])}</h1><ul>{rows}</ul></body>")
    ctype = mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
    # Bilder/Text direkt anzeigen, alles andere laden lassen (FileResponse: Range/Streaming).
    return FileResponse(p, media_type=ctype)

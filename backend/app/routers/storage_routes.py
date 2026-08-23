"""
routers/storage_routes.py
-------------------------
Zwei Dinge:

1. Eine schlanke REST-API fuer die server-eigenen Relay-Ordner
   (Storage / Deployment) - damit der Explorer im Dashboard sie anzeigen,
   hoch- und herunterladen kann, ohne den Umweg ueber WebDAV.

2. Die oeffentliche Deployment-Seite unter /deployment.
   Sie zeigt entweder eigenen HTML-Code aus den Einstellungen oder - solange
   keiner hinterlegt ist - eine einfache Dateiliste. Dateien darunter sind
   direkt per Link erreichbar: /deployment/logo.png, /deployment/setup.ps1, ...

   ACHTUNG: Genau das ist der Zweck, aber es heisst auch: Alles im Ordner
   'Deployment' ist ohne Anmeldung abrufbar, sobald die Freigabe aktiv ist.
   Der Schalter dafuer liegt in den Einstellungen (Standard: AUS), damit
   niemand versehentlich etwas veroeffentlicht.
"""

import mimetypes
import urllib.parse as _up

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel
from starlette.responses import HTMLResponse, PlainTextResponse, Response

from app import db, relay_storage as rs
from app.auth import get_current_user, require_perm

router = APIRouter(tags=["storage"])

SETTING_HTML = "deployment_html"
SETTING_PUBLIC = "deployment_public"
SETTING_TITLE = "deployment_title"


def deployment_public() -> bool:
    """Ist die oeffentliche Seite freigeschaltet? Standard: nein."""
    raw = db.get_setting(SETTING_PUBLIC)
    return str(raw or "0").strip().lower() in ("1", "true", "yes", "on")


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
        return {"section": sec, "path": path, "entries": rs.listdir(sec, path),
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
        data = rs.read(sec, path)
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
        rs.write(sec, target, await file.read())
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
        rs.mkdir(sec, body.path)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


@router.post("/api/storage/delete")
def storage_delete(body: PathBody, user: dict = Depends(get_current_user)):
    sec = _section(body.section)
    if not rs.may_write(user, sec):
        raise HTTPException(403, f"Schreiben in '{sec}' nicht erlaubt")
    try:
        rs.delete(sec, body.path)
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
        rs.move(sec, body.path, body.dst)
    except FileNotFoundError:
        raise HTTPException(404, "Nicht gefunden")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": True}


# ------------------------------------------------------------------
# Einstellungen der Deployment-Seite
# ------------------------------------------------------------------

class DeploySettings(BaseModel):
    html: str | None = None
    public: bool | None = None
    title: str | None = None


@router.get("/api/storage/deployment")
def get_deployment(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    return {
        "html": db.get_setting(SETTING_HTML) or "",
        "title": db.get_setting(SETTING_TITLE) or "Deployment",
        "public": deployment_public(),
    }


@router.post("/api/storage/deployment")
def set_deployment(body: DeploySettings,
                         user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    if body.html is not None:
        db.set_setting(SETTING_HTML, body.html)
    if body.title is not None:
        db.set_setting(SETTING_TITLE, body.title.strip() or "Deployment")
    if body.public is not None:
        db.set_setting(SETTING_PUBLIC, "1" if body.public else "0")
        db.add_audit_entry(user["username"], "deployment.public",
                           details="an" if body.public else "aus")
    return {"ok": True, "public": deployment_public()}


# ==================================================================
# Oeffentliche Seite: /deployment  und  /deployment/<datei>
# ==================================================================
# KEIN Login. Deshalb haengt alles am Schalter 'deployment_public'.

_OFF = PlainTextResponse("Die Deployment-Seite ist nicht freigegeben.",
                         status_code=404)


def _default_page(title: str) -> str:
    """Einfache Dateiliste, solange kein eigener HTML-Code hinterlegt ist."""
    try:
        entries = rs.listdir("Deployment", "")
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
    if not deployment_public():
        return _OFF
    html = (db.get_setting(SETTING_HTML) or "").strip()
    title = db.get_setting(SETTING_TITLE) or "Deployment"
    if not html:
        html = _default_page(title)
    return HTMLResponse(content=html)


@router.get("/deployment/{full_path:path}", include_in_schema=False)
def deployment_file(full_path: str, request: Request):
    if not deployment_public():
        return _OFF
    parts = [p for p in full_path.split("/") if p]
    if not parts:
        # deployment_index() ist jetzt synchron - kein 'await' mehr.
        return deployment_index()
    try:
        entry = rs.stat_entry("Deployment", parts)
    except PermissionError:
        return PlainTextResponse("Nicht erlaubt", status_code=403)
    if not entry:
        return PlainTextResponse("Nicht gefunden", status_code=404)
    if entry["is_dir"]:
        # Unterordner ebenfalls als Liste anzeigen.
        import xml.sax.saxutils as _x
        base = "/deployment/" + "/".join(_up.quote(p) for p in parts)
        rows = "".join(
            f'<li><a href="{base}/{_up.quote(e["name"])}">'
            f'{"📁" if e["is_dir"] else "📄"} {_x.escape(e["name"])}</a></li>'
            for e in rs.listdir("Deployment", parts))
        return HTMLResponse(
            f"<!doctype html><meta charset=utf-8><title>{_x.escape(parts[-1])}</title>"
            "<body style=\"font-family:system-ui,sans-serif;max-width:720px;"
            f"margin:40px auto\"><h1>{_x.escape(parts[-1])}</h1><ul>{rows}</ul></body>")
    data = rs.read("Deployment", parts)
    ctype = mimetypes.guess_type(parts[-1])[0] or "application/octet-stream"
    # Bilder/Text direkt anzeigen, alles andere laden lassen.
    return Response(content=data, media_type=ctype)

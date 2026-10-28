# dist_routes.py (Modul "dist") - einheitliche Verteilung unter /dist
# ------------------------------------------------------------------
# OEFFENTLICH (ohne Anmeldung), je Ordner per Einstellung freigegeben (Standard: an):
#   GET /dist                      Uebersicht aller oeffentlichen Ordner (HTML)
#   GET /dist/{folder}             Dateiliste eines Ordners (HTML)
#   GET /dist/{folder}/{file}      Datei herunterladen
# VERWALTUNG (Recht manage_dist):
#   GET    /api/dist/folders               alle Ordner (auch private) + Metadaten
#   POST   /api/dist/folders               {name,label,public} anlegen
#   PATCH  /api/dist/folders/{name}        {public?,label?}
#   DELETE /api/dist/folders/{name}        (nur Nicht-Kern-Ordner)
#   GET    /api/dist/folders/{name}/files  Dateien
#   POST   /api/dist/folders/{name}/upload Dateien hochladen
#   DELETE /api/dist/folders/{name}/files/{file}
from __future__ import annotations

import html
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

from . import store

router = APIRouter(tags=["dist"])
_OFF = PlainTextResponse("Dieser Bereich ist nicht freigegeben.", status_code=403)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n/1:.0f} {unit}" if n < 1024 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _sz(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == "GB":
            return (f"{int(f)} {u}" if u == "B" else f"{f:.1f} {u}")
        f /= 1024
    return f"{f:.1f} GB"


_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>:root{{color-scheme:dark light}}body{{margin:0;font:15px system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
background:#0b1220;color:#e6edf6}}@media(prefers-color-scheme:light){{body{{background:#f5f7fb;color:#10233f}}}}
.wrap{{max-width:820px;margin:0 auto;padding:28px 18px}}h1{{font-size:22px;display:flex;align-items:center;gap:10px}}
.logo{{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#2dd4bf,#38bdf8);display:grid;
place-items:center;color:#052521;font-weight:900}}a{{color:#38bdf8;text-decoration:none}}a:hover{{text-decoration:underline}}
.card{{display:flex;align-items:center;gap:12px;border:1px solid #243249;border-radius:11px;padding:12px 14px;margin:8px 0;
background:#121c2e}}@media(prefers-color-scheme:light){{.card{{background:#fff;border-color:#d9e1ec}}}}
.card .i{{font-size:22px}}.card .t{{flex:1}}.card .t b{{display:block}}.card .m{{color:#8fa1ba;font-size:12.5px}}
.dl{{background:#2dd4bf;color:#052521;border-radius:8px;padding:6px 12px;font-weight:700}}
.bc{{color:#8fa1ba;font-size:13px;margin-bottom:10px}}.empty{{color:#8fa1ba}}</style></head>
<body><div class="wrap"><h1><span class="logo">R</span>{title}</h1>{body}</div></body></html>"""


def _render(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=html.escape(title), body=body))


@router.get("/dist", include_in_schema=False)
@router.get("/dist/", include_in_schema=False)
def dist_index():
    pub = [f for f in store.folders() if f["public"]]
    if not pub:
        rows = '<p class="empty">Keine öffentlichen Bereiche.</p>'
    else:
        rows = "".join(
            f'<a class="card" href="/dist/{html.escape(f["name"])}"><span class="i">📦</span>'
            f'<span class="t"><b>{html.escape(f["label"])}</b>'
            f'<span class="m">{f["files"]} Datei(en) · {_sz(f["size"])}</span></span>'
            f'<span class="dl">Öffnen</span></a>' for f in pub)
    return _render("RAPALLE.net RMM · Downloads", rows)


@router.get("/dist/{folder}", include_in_schema=False)
@router.get("/dist/{folder}/", include_in_schema=False)
def dist_folder(folder: str):
    if not store.is_public(folder):
        return _OFF
    m = store.meta(folder)
    items = store.listdir(folder)
    if not items:
        body = '<div class="bc"><a href="/dist">← Downloads</a></div><p class="empty">Dieser Bereich ist noch leer.</p>'
    else:
        rows = "".join(
            f'<a class="card" href="/dist/{html.escape(folder)}/{html.escape(it["name"])}">'
            f'<span class="i">📄</span><span class="t"><b>{html.escape(it["name"])}</b>'
            f'<span class="m">{_sz(it["size"])} · {time.strftime("%d.%m.%Y", time.localtime(it["mtime"]))}</span></span>'
            f'<span class="dl">⬇ Laden</span></a>' for it in items)
        body = f'<div class="bc"><a href="/dist">← Downloads</a></div>{rows}'
    return _render(m["label"], body)


@router.get("/dist/{folder}/{filename}", include_in_schema=False)
def dist_file(folder: str, filename: str):
    if not store.is_public(folder):
        return _OFF
    p = store.file_path(folder, filename)
    if not p:
        return PlainTextResponse("Nicht gefunden", status_code=404)
    return FileResponse(str(p), filename=p.name, media_type="application/octet-stream")


# --- Kurz-Aliasse: /enrollment /agent /client zeigen auf /dist/<name> ---
from starlette.responses import RedirectResponse as _Redirect

def _alias(folder):
    def idx():
        return _Redirect(f"/dist/{folder}", status_code=307)
    def sub(filename: str):
        return _Redirect(f"/dist/{folder}/{filename}", status_code=307)
    return idx, sub

for _name in ("enrollment", "agent", "client"):
    _idx, _sub = _alias(_name)
    router.add_api_route(f"/{_name}", _idx, methods=["GET"], include_in_schema=False)
    router.add_api_route(f"/{_name}/", _idx, methods=["GET"], include_in_schema=False)
    router.add_api_route(f"/{_name}/{{filename}}", _sub, methods=["GET"], include_in_schema=False)


# ====================================================== Verwaltung
def _perm(user):
    require_perm(user, "manage_dist")


@router.get("/api/dist/folders")
def api_folders(user: dict = Depends(get_current_user)):
    _perm(user)
    return {"folders": store.folders()}


class FolderBody(BaseModel):
    name: str = ""
    label: str = ""
    public: bool = True


@router.post("/api/dist/folders")
def api_create(body: FolderBody, user: dict = Depends(get_current_user)):
    _perm(user)
    if not store.valid_name(body.name):
        raise HTTPException(400, "Ungültiger Ordnername")
    if not store.create_folder(body.name, body.label, body.public):
        raise HTTPException(400, "Konnte nicht angelegt werden")
    db.add_audit_entry(user["username"], "dist.folder_created", details=body.name)
    return {"ok": True}


class MetaBody(BaseModel):
    public: bool | None = None
    label: str | None = None


@router.patch("/api/dist/folders/{name}")
def api_meta(name: str, body: MetaBody, user: dict = Depends(get_current_user)):
    _perm(user)
    if not store.folder_path(name):
        raise HTTPException(404, "Ordner nicht gefunden")
    store.set_meta(name, public=body.public, label=body.label)
    return {"ok": True, "meta": store.meta(name)}


@router.delete("/api/dist/folders/{name}")
def api_delete(name: str, user: dict = Depends(get_current_user)):
    _perm(user)
    if not store.delete_folder(name):
        raise HTTPException(400, "Kern-Ordner können nicht gelöscht werden")
    db.add_audit_entry(user["username"], "dist.folder_deleted", details=name)
    return {"ok": True}


@router.get("/api/dist/folders/{name}/files")
def api_files(name: str, request: Request, user: dict = Depends(get_current_user)):
    _perm(user)
    if not store.folder_path(name):
        raise HTTPException(404, "Ordner nicht gefunden")
    try:
        from router.enrollment_routes import _backend_url
        base = _backend_url(request).rstrip("/")
    except Exception:
        base = ""
    items = store.listdir(name)
    for it in items:
        it["url"] = f"{base}/dist/{name}/{it['name']}"
    return {"files": items, "meta": store.meta(name)}


@router.post("/api/dist/folders/{name}/upload")
async def api_upload(name: str, files: list[UploadFile] = File(...), user: dict = Depends(get_current_user)):
    _perm(user)
    if not store.folder_path(name) and not store.valid_name(name):
        raise HTTPException(404, "Ordner nicht gefunden")
    saved = []
    for f in files:
        data = await f.read()
        if store.save_file(name, f.filename, data):
            saved.append(f.filename)
    db.add_audit_entry(user["username"], "dist.upload", details=f"{name}: {', '.join(saved)}"[:300])
    return {"ok": True, "saved": saved}


@router.delete("/api/dist/folders/{name}/files/{filename}")
def api_delete_file(name: str, filename: str, user: dict = Depends(get_current_user)):
    _perm(user)
    if not store.delete_file(name, filename):
        raise HTTPException(404, "Datei nicht gefunden")
    return {"ok": True}

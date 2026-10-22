# canary_routes.py (Modul "canary") - Verwaltung (Recht manage_canary)
# --------------------------------------------------------------------
#   GET    /api/canary/base                         oeffentliche Basis-Adresse (fuer die Links)
#   GET    /api/canary/trackers                      IP-Tracker auflisten
#   POST   /api/canary/trackers                      {name, redirect, open_url?} anlegen
#   PATCH  /api/canary/trackers/{id}                 bearbeiten
#   DELETE /api/canary/trackers/{id}                 loeschen
#   GET    /api/canary/tokens                         Canary-Tokens auflisten
#   POST   /api/canary/tokens                         {name, kind, note, redirect?, host?, file_kind?} anlegen
#   DELETE /api/canary/tokens/{id}                    loeschen
#   GET    /api/canary/tokens/{id}/qr.png             QR-Bild (kind qr)
#   GET    /api/canary/tokens/{id}/snippets          fertige Links/HTML zum Kopieren
#   GET    /api/canary/hits?ref=                      Treffer (alle oder zu einem Objekt)
#   DELETE /api/canary/hits?ref=                      Treffer loeschen
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

from . import files, store

router = APIRouter(prefix="/api/canary", tags=["canary"])


def _perm(user):
    require_perm(user, "manage_canary")


def _base(request: Request) -> str:
    try:
        from router.enrollment_routes import _backend_url
        return _backend_url(request).rstrip("/")
    except Exception:
        return str(request.base_url).rstrip("/")


@router.get("/base")
def base(request: Request, user: dict = Depends(get_current_user)):
    _perm(user)
    return {"base": _base(request)}


# ------------------------------------------------------------------ Tracker
class TrackerBody(BaseModel):
    name: str = ""
    redirect: str = ""
    enabled: bool = True


def _tracker_view(t: dict, base: str) -> dict:
    return {**t, "url": f"{base}/t/{t['slug']}"}


@router.get("/trackers")
def list_trackers(request: Request, user: dict = Depends(get_current_user)):
    _perm(user)
    b = _base(request)
    return {"trackers": [_tracker_view(t, b) for t in store.data()["trackers"].values()]}


@router.post("/trackers")
def create_tracker(body: TrackerBody, request: Request, user: dict = Depends(get_current_user)):
    _perm(user)
    if not body.name.strip():
        raise HTTPException(400, "Name fehlt")
    red = body.redirect.strip()
    if red and "://" not in red:
        red = "https://" + red

    def upd(d):
        tid = store.new_id()
        t = {"id": tid, "name": body.name.strip()[:80], "slug": store.new_slug(), "redirect": red,
             "enabled": bool(body.enabled), "created": int(time.time()), "created_by": user["username"],
             "count": 0, "last": None}
        d["trackers"][tid] = t
        return t
    t = store.update(upd)
    db.add_audit_entry(user["username"], "canary.tracker_created", details=t["name"])
    return {"ok": True, "tracker": _tracker_view(t, _base(request))}


@router.patch("/trackers/{tid}")
def update_tracker(tid: str, body: TrackerBody, request: Request, user: dict = Depends(get_current_user)):
    _perm(user)

    def upd(d):
        t = d["trackers"].get(tid)
        if not t:
            return None
        if body.name.strip():
            t["name"] = body.name.strip()[:80]
        red = body.redirect.strip()
        t["redirect"] = ("https://" + red if red and "://" not in red else red)
        t["enabled"] = bool(body.enabled)
        return t
    t = store.update(upd)
    if not t:
        raise HTTPException(404, "Tracker nicht gefunden")
    return {"ok": True, "tracker": _tracker_view(t, _base(request))}


@router.delete("/trackers/{tid}")
def delete_tracker(tid: str, user: dict = Depends(get_current_user)):
    _perm(user)
    store.update(lambda d: d["trackers"].pop(tid, None))
    store.clear_hits(tid)
    return {"ok": True}


# ------------------------------------------------------------------ Tokens
KINDS = ("web", "email", "file", "qr", "dns")
FILE_KINDS = ("docx", "pdf", "xlsx")


class TokenBody(BaseModel):
    name: str = ""
    kind: str = "web"
    note: str = ""
    redirect: str = ""          # web/qr: optionale Weiterleitung nach dem Treffer
    host: str = ""              # dns: Token-Domain (z. B. canary.firma.de)
    file_kind: str = "docx"     # file: docx|pdf|xlsx


def _token_view(t: dict, base: str) -> dict:
    v = {**t}
    slug = t["slug"]
    k = t["kind"]
    if k in ("web", "qr"):
        v["url"] = f"{base}/c/{slug}"
    elif k == "email":
        v["url"] = f"{base}/c/{slug}/pixel.png"
    elif k == "file":
        v["url"] = f"{base}/cf/{slug}"
    elif k == "dns":
        v["url"] = t.get("host") or ""
    return v


@router.get("/tokens")
def list_tokens(request: Request, user: dict = Depends(get_current_user)):
    _perm(user)
    b = _base(request)
    return {"tokens": [_token_view(t, b) for t in store.data()["tokens"].values()], "file_kinds": list(FILE_KINDS)}


@router.post("/tokens")
def create_token(body: TokenBody, request: Request, user: dict = Depends(get_current_user)):
    _perm(user)
    if body.kind not in KINDS:
        raise HTTPException(400, "Unbekannte Token-Art")
    if not body.name.strip():
        raise HTTPException(400, "Name fehlt")
    base = _base(request)
    red = body.redirect.strip()
    if red and "://" not in red:
        red = "https://" + red

    def upd(d):
        tid = store.new_id()
        t = {"id": tid, "name": body.name.strip()[:80], "kind": body.kind, "slug": store.new_slug(),
             "note": body.note.strip()[:200], "redirect": red, "enabled": True,
             "created": int(time.time()), "created_by": user["username"], "count": 0, "last": None}
        if body.kind == "dns":
            host = body.host.strip().strip(".").lower()
            if not host or "." not in host:
                raise ValueError("Für eine DNS-Canary bitte eine Domain angeben (z. B. canary.firma.de)")
            t["host"] = host
        if body.kind == "file":
            if body.file_kind not in FILE_KINDS:
                raise ValueError("Dateityp muss docx, pdf oder xlsx sein")
            t["file_kind"] = body.file_kind
        d["tokens"][tid] = t
        return t
    try:
        t = store.update(upd)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if t["kind"] == "file":
        try:
            fname = files.generate_file(t, base)
            store.update(lambda d: d["tokens"][t["id"]].update(file_name=fname))
            t["file_name"] = fname
        except Exception as e:      # noqa: BLE001
            store.update(lambda d: d["tokens"].pop(t["id"], None))
            raise HTTPException(500, f"Datei konnte nicht erzeugt werden: {e}")
    db.add_audit_entry(user["username"], "canary.token_created", details=f"{t['kind']}: {t['name']}")
    return {"ok": True, "token": _token_view(t, base)}


@router.delete("/tokens/{tid}")
def delete_token(tid: str, user: dict = Depends(get_current_user)):
    _perm(user)
    t = store.get_token(tid)
    if t and t.get("file_name"):
        try:
            (store.FILES_DIR / t["file_name"]).unlink(missing_ok=True)
        except Exception:
            pass
    store.update(lambda d: d["tokens"].pop(tid, None))
    store.clear_hits(tid)
    return {"ok": True}


@router.get("/tokens/{tid}/qr.png")
def token_qr(tid: str, request: Request, user: dict = Depends(get_current_user)):
    _perm(user)
    t = store.get_token(tid)
    if not t or t["kind"] != "qr":
        raise HTTPException(404, "Kein QR-Token")
    png = files.make_qr_png(_base(request), t["slug"])
    return Response(png, media_type="image/png")


@router.get("/tokens/{tid}/snippets")
def token_snippets(tid: str, request: Request, user: dict = Depends(get_current_user)):
    _perm(user)
    t = store.get_token(tid)
    if not t:
        raise HTTPException(404, "Token nicht gefunden")
    base = _base(request)
    slug = t["slug"]
    out = {"kind": t["kind"]}
    if t["kind"] == "email":
        px = f"{base}/c/{slug}/pixel.png"
        out["pixel_url"] = px
        out["html"] = f'<img src="{px}" width="1" height="1" style="display:none" alt="">'
    elif t["kind"] in ("web", "qr"):
        out["url"] = f"{base}/c/{slug}"
    elif t["kind"] == "dns":
        out["host"] = t.get("host")
        out["hint"] = ("Diese Domain per NS-Eintrag auf diesen Server delegieren. "
                       "Ausgelöst wird der Alarm bei jeder DNS-Auflösung unterhalb der Domain.")
    elif t["kind"] == "file":
        out["download_url"] = f"{base}/cf/{slug}"
        out["file_name"] = t.get("file_name")
    return out


# ------------------------------------------------------------------ Treffer
@router.get("/hits")
def list_hits(ref: str = "", limit: int = 200, user: dict = Depends(get_current_user)):
    _perm(user)
    return {"hits": store.hits(ref or None, min(max(1, limit), 1000))}


@router.delete("/hits")
def delete_hits(ref: str = "", user: dict = Depends(get_current_user)):
    _perm(user)
    store.clear_hits(ref or None)
    return {"ok": True}

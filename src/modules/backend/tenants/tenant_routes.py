# tenant_routes.py (Modul "tenants") - Mandanten-Backends verwalten und wechseln
# -----------------------------------------------------------------------------
# Verwaltung (Recht manage_tenants):
#   GET/POST            /api/tenants/admin                 alle Mandanten / anlegen
#   PUT/DELETE          /api/tenants/admin/{id}            aendern / loeschen
#   GET                 /api/tenants/directory             Benutzer+Gruppen (fuer allow-Liste)
# Benutzer (jeder mit Recht use_tenants):
#   GET                 /api/tenants                       sichtbare Mandanten + Status (Zugangsdaten?)
#   PUT/DELETE          /api/tenants/{id}/credentials      eigene Zugangsdaten setzen/loeschen
#   POST                /api/tenants/{id}/open             Autologin -> Einbettungs-URL (#client-token=)
#   GET                 /api/tenants/{id}/spaces?path=     Spaces/Relay des Mandanten (via eigene Zugangsdaten)
#   GET                 /api/tenants/{id}/spaces/file?path= Datei herunterladen (Proxy)
from __future__ import annotations

import time
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

from . import access, client, store
from .client import TenantError

router = APIRouter(prefix="/api/tenants", tags=["tenants"])


# ====================================================== Verwaltung
class TenantBody(BaseModel):
    name: str = ""
    url: str = ""
    verify_tls: bool = True
    color: str = ""
    note: str = ""
    allow: list = []
    via: str = ""          # "" = vom RMM-Server, sonst Node-ID (Node-Modul "tenants")


def _norm_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    if u and "://" not in u:
        u = "https://" + u
    return u


@router.get("/admin")
def admin_list(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_tenants")
    return {"tenants": [store.public(t) for t in store.list_tenants()]}


@router.get("/directory")
def directory(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_tenants")
    return {"users": [{"id": u["id"], "username": u["username"]} for u in db.list_users()],
            "groups": [{"id": g["id"], "name": g["name"]} for g in db.list_groups()]}


def _clean_allow(allow: list) -> list:
    return [{"type": a["type"], "id": a["id"]} for a in allow
            if isinstance(a, dict) and a.get("type") in ("user", "group") and a.get("id")]


@router.post("/admin")
def admin_create(body: TenantBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_tenants")
    if not body.name.strip() or not body.url.strip():
        raise HTTPException(400, "Name und URL sind nötig")

    def upd(d):
        tid = store.new_id()
        t = {"id": tid, "name": body.name.strip()[:80], "url": _norm_url(body.url),
             "verify_tls": bool(body.verify_tls), "color": body.color.strip()[:16],
             "note": body.note.strip()[:300], "allow": _clean_allow(body.allow),
             "via": body.via.strip()[:64],
             "created": int(time.time()), "created_by": user["username"]}
        d["tenants"][tid] = t
        return t
    t = store.update(upd)
    db.add_audit_entry(user["username"], "tenant.created", details=t["name"])
    return {"ok": True, "tenant": store.public(t)}


@router.put("/admin/{tid}")
def admin_update(tid: str, body: TenantBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_tenants")

    def upd(d):
        t = d["tenants"].get(tid)
        if not t:
            return None
        t.update(name=body.name.strip()[:80] or t["name"], url=_norm_url(body.url) or t["url"],
                 verify_tls=bool(body.verify_tls), color=body.color.strip()[:16],
                 note=body.note.strip()[:300], allow=_clean_allow(body.allow), via=body.via.strip()[:64])
        return t
    t = store.update(upd)
    if not t:
        raise HTTPException(404, "Mandant nicht gefunden")
    return {"ok": True, "tenant": store.public(t)}


@router.delete("/admin/{tid}")
def admin_delete(tid: str, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_tenants")
    store.update(lambda d: d["tenants"].pop(tid, None))
    store.purge_tenant_creds(tid)
    db.add_audit_entry(user["username"], "tenant.deleted", details=tid)
    return {"ok": True}


# ====================================================== Benutzer-Sicht
def _visible(user: dict) -> list[dict]:
    require_perm(user, "use_tenants")
    return access.visible_tenants(user, store.list_tenants())


def _tenant_or_403(tid: str, user: dict) -> dict:
    t = store.get(tid)
    if not t or not access.may_open(user, t):
        raise HTTPException(403, "Kein Zugriff auf diesen Mandanten")
    return t


@router.get("")
def my_tenants(user: dict = Depends(get_current_user)):
    out = []
    for t in _visible(user):
        c = store.get_cred(t["id"], user["id"])
        out.append({"id": t["id"], "name": t["name"], "url": t["url"], "color": t.get("color", ""),
                    "note": t.get("note", ""), "has_credentials": bool(c and c.get("pw_enc")),
                    "username": (c or {}).get("username", "")})
    return {"tenants": out}


class CredBody(BaseModel):
    username: str
    password: str
    realm: str = "local"
    code: str = ""          # 2FA, falls der Mandant es verlangt


@router.put("/{tid}/credentials")
async def set_credentials(tid: str, body: CredBody, user: dict = Depends(get_current_user)):
    t = _tenant_or_403(tid, user)
    if not body.username.strip() or not body.password:
        raise HTTPException(400, "Benutzername und Passwort sind nötig")
    try:
        await client.verify_credentials(t, body.username.strip(), body.password, body.realm, body.code)
    except TenantError as e:
        if str(e) == "2fa_required":
            raise HTTPException(401, "2fa_required")
        raise HTTPException(e.status or 400, str(e))
    store.set_cred(tid, user["id"], body.username.strip(), body.password, body.realm)
    db.add_audit_entry(user["username"], "tenant.credentials_set", details=t["name"])
    return {"ok": True}


@router.delete("/{tid}/credentials")
def clear_credentials(tid: str, user: dict = Depends(get_current_user)):
    _tenant_or_403(tid, user)
    store.clear_cred(tid, user["id"])
    return {"ok": True}


class OpenBody(BaseModel):
    code: str = ""


@router.post("/{tid}/open")
async def open_tenant(tid: str, body: OpenBody, user: dict = Depends(get_current_user)):
    """Autologin: holt am Mandanten einen Token und baut die Einbettungs-URL."""
    t = _tenant_or_403(tid, user)
    try:
        token = await client.token_for(t, user, body.code)
    except TenantError as e:
        if str(e) == "no_credentials":
            raise HTTPException(412, "Für diesen Mandanten sind noch keine Zugangsdaten hinterlegt")
        if str(e) == "2fa_required":
            raise HTTPException(401, "2fa_required")
        raise HTTPException(e.status or 400, str(e))
    base = t["url"].rstrip("/")
    embed = f"{base}/#client-token={urllib.parse.quote(token)}&embedded=1"
    db.add_audit_entry(user["username"], "tenant.opened", details=t["name"])
    return {"ok": True, "url": embed, "name": t["name"], "base": base}


# ====================================================== Spaces/Relay des Mandanten (Proxy)
async def _tenant_token(t: dict, user: dict) -> str:
    try:
        return await client.token_for(t, user)
    except TenantError as e:
        code = 412 if str(e) == "no_credentials" else (e.status or 400)
        raise HTTPException(code, "Keine Zugangsdaten für diesen Mandanten" if str(e) == "no_credentials" else str(e))


@router.get("/{tid}/spaces")
async def tenant_spaces(tid: str, path: str = "", user: dict = Depends(get_current_user)):
    """Liste des Mandanten-Spaces-Browsers (dessen eigene Spaces-API, im Namen des Benutzers)."""
    t = _tenant_or_403(tid, user)
    token = await _tenant_token(t, user)
    try:
        r = await client.api(t, token, "GET", "/api/spaces/fs/list?path=" + urllib.parse.quote(path))
    except TenantError as e:
        raise HTTPException(502, str(e))
    if r.status_code == 404:
        raise HTTPException(409, "Der Mandant hat das Modul „Spaces“ nicht aktiv")
    if r.status_code != 200:
        raise HTTPException(502, f"Mandant antwortete mit {r.status_code}")
    return r.json()


@router.get("/{tid}/spaces/file")
async def tenant_space_file(tid: str, path: str, user: dict = Depends(get_current_user)):
    t = _tenant_or_403(tid, user)
    token = await _tenant_token(t, user)
    try:
        r = await client.api(t, token, "GET", "/api/spaces/fs/download?path=" + urllib.parse.quote(path))
    except TenantError as e:
        raise HTTPException(502, str(e))
    if r.status_code != 200:
        raise HTTPException(r.status_code if r.status_code in (403, 404) else 502, "Datei nicht verfügbar")
    fname = path.rsplit("/", 1)[-1] or "download"
    return Response(r.content, media_type=r.headers.get("content-type", "application/octet-stream"),
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})

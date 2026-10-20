# match_routes.py (Modul "proxmox") - Agenten <-> Proxmox-Gaeste
# --------------------------------------------------------------
#   GET    /api/proxmox/guest-map                    Zuordnungen + offene Vorschlaege (die ich uebernehmen darf)
#   POST   /api/proxmox/scan                         {host?} jetzt abgleichen (pve_manage am Host)
#   POST   /api/proxmox/suggestions/{cid}/accept     Vorschlag uebernehmen: als VM/LXC markieren + dem Host zuordnen
#   POST   /api/proxmox/suggestions/{cid}/ignore     Vorschlag verwerfen (kommt nicht wieder)
#   DELETE /api/proxmox/guest-map/{cid}              Zuordnung aufheben
# Uebernehmen braucht "Proxmox verwalten" am Host UND "Clients verwalten" am Gast (wie im Dashboard).

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import can_access_client, get_current_user, is_super_admin, user_has_permission

from . import match, pve_store

router = APIRouter(prefix="/api/proxmox", tags=["proxmox-match"])


def _can(user, perm, cid) -> bool:
    return is_super_admin(user) or (can_access_client(user, cid) and user_has_permission(user, perm, cid))


def _may_apply(user, s: dict, cid: str) -> bool:
    return _can(user, "pve_manage", s["host"]) and _can(user, "manage_clients", cid)


def _hostname(cid: str) -> str:
    return (db.get_client(cid) or {}).get("hostname") or cid


@router.get("/guest-map")
def guest_map(user: dict = Depends(get_current_user)):
    d = match.data()
    mp = {cid: {**m, "host_name": _hostname(m["host"]), "can_open": _can(user, "pve_access", m["host"])}
          for cid, m in d["map"].items() if can_access_client(user, cid)}
    sug = [{"client_id": cid, "client_name": _hostname(cid), **s, "host_name": _hostname(s["host"])}
           for cid, s in d["suggestions"].items() if db.get_client(cid) and _may_apply(user, s, cid)]
    return {"map": mp, "suggestions": sorted(sug, key=lambda x: (x["host_name"], x["vmid"])),
            "scanned": {h: t for h, t in d["scanned"].items() if _can(user, "pve_access", h)}}


class ScanBody(BaseModel):
    host: str = ""


@router.post("/scan")
async def scan(body: ScanBody, user: dict = Depends(get_current_user)):
    hosts = [body.host] if body.host else pve_store.connected_ids()
    hosts = [h for h in hosts if pve_store.is_connected(h) and _can(user, "pve_manage", h)]
    if not hosts:
        raise HTTPException(403 if body.host else 404, "Kein Proxmox-Host, den du abgleichen darfst")
    res = {}
    for h in hosts:
        res[_hostname(h)] = await match.scan(h)
    db.add_audit_entry(user["username"], "proxmox.scan", details=", ".join(res))
    return {"results": res}


@router.post("/suggestions/{cid}/{act}")
def suggestion(cid: str, act: str, user: dict = Depends(get_current_user)):
    s = match.data()["suggestions"].get(cid)
    if not s:
        raise HTTPException(404, "Kein Vorschlag für dieses Gerät")
    if not _may_apply(user, s, cid):
        raise HTTPException(403, "Dafür brauchst du „Proxmox verwalten“ am Host und „Clients verwalten“ am Gerät")
    if act == "accept":
        entry = match.accept(cid)
        db.add_audit_entry(user["username"], "proxmox.guest_linked", target=cid,
                           details=f"{entry['type']} {entry['vmid']} auf {_hostname(entry['host'])}")
        return {"ok": True, "entry": entry, "client": db.get_client(cid)}
    if act == "ignore":
        match.ignore(cid)
        return {"ok": True}
    raise HTTPException(400, "Unbekannte Aktion")


@router.delete("/guest-map/{cid}")
def unlink(cid: str, user: dict = Depends(get_current_user)):
    if not _can(user, "manage_clients", cid):
        raise HTTPException(403, "Keine Berechtigung")
    match.unlink(cid)
    return {"ok": True}

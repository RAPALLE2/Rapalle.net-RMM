# match_routes.py (Modul "proxmox") - Agenten <-> Proxmox-Gaeste
# --------------------------------------------------------------
#   GET    /api/proxmox/guest-map                    Zuordnungen + offene Vorschlaege (die ich uebernehmen darf)
#   POST   /api/proxmox/scan                         {host?} jetzt abgleichen (pve_manage am Host)
#   POST   /api/proxmox/suggestions/{cid}/accept     Vorschlag uebernehmen: als VM/LXC markieren + dem Host zuordnen
#   POST   /api/proxmox/suggestions/{cid}/ignore     Vorschlag verwerfen (kommt nicht wieder)
#   DELETE /api/proxmox/guest-map/{cid}              Zuordnung aufheben
#   PUT    /api/proxmox/heads                        {host, node, head} Head eines Proxmox-Nodes festlegen
#                                                    (gilt fuer alle Gaeste dieses Nodes, auch bestehende)
# accept nimmt optional {head, remember}: uebergeordneter Agent (Head) + fuer den Node merken.
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


def _head_candidates(user, sug: list[dict]) -> list[dict]:
    """Agenten, die als Head (Proxmox-Node) in Frage kommen: physische Geraete, die ich sehe."""
    skip = {s["client_id"] for s in sug}
    out = []
    for c in db.list_clients():
        if c["id"] in skip or (c.get("device_type") or "physical") != "physical":
            continue
        if not can_access_client(user, c["id"]):
            continue
        out.append({"id": c["id"], "hostname": c.get("hostname") or c["id"],
                    "connected": pve_store.is_connected(c["id"])})
    return sorted(out, key=lambda x: (not x["connected"], x["hostname"].lower()))


@router.get("/guest-map")
def guest_map(user: dict = Depends(get_current_user)):
    d = match.data()
    mp = {cid: {**m, "host_name": _hostname(m["host"]), "can_open": _can(user, "pve_access", m["host"])}
          for cid, m in d["map"].items() if can_access_client(user, cid)}
    sug = [{"client_id": cid, "client_name": _hostname(cid), **s, "host_name": _hostname(s["host"]),
            "head_name": _hostname(s["head"])}
           for cid, s in match.suggestions_with_heads().items() if db.get_client(cid) and _may_apply(user, s, cid)]
    heads = {h: {n: {"id": c, "name": _hostname(c)} for n, c in nodes.items()}
             for h, nodes in (d.get("heads") or {}).items() if _can(user, "pve_access", h)}
    return {"map": mp, "suggestions": sorted(sug, key=lambda x: (x["host_name"], x["node"], x["vmid"])),
            "heads": heads, "candidates": _head_candidates(user, sug) if sug else [],
            "scanned": {h: t for h, t in d["scanned"].items() if _can(user, "pve_access", h)}}


class HeadBody(BaseModel):
    host: str
    node: str
    head: str


@router.put("/heads")
def set_head(body: HeadBody, user: dict = Depends(get_current_user)):
    if not pve_store.is_connected(body.host) or not _can(user, "pve_manage", body.host):
        raise HTTPException(403, "Dafür brauchst du „Proxmox verwalten“ am Host")
    if not _can(user, "manage_clients", body.head):
        raise HTTPException(403, "Keine Berechtigung für den gewählten Head")
    try:
        moved = match.set_head(body.host, body.node, body.head)
    except KeyError:
        raise HTTPException(404, "Agent nicht gefunden")
    db.add_audit_entry(user["username"], "proxmox.head_set", target=body.head,
                       details=f"Node {body.node} von {_hostname(body.host)} -> {_hostname(body.head)}, {moved} umgehaengt")
    return {"ok": True, "moved": moved}


class AcceptBody(BaseModel):
    head: str = ""
    remember: bool = True


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
        res[_hostname(h)] = await match.scan(h)        # manuell: laeuft auch bei auto_match aus
    db.add_audit_entry(user["username"], "proxmox.scan", details=", ".join(res))
    return {"results": res}


@router.post("/suggestions/{cid}/{act}")
def suggestion(cid: str, act: str, body: AcceptBody | None = None, user: dict = Depends(get_current_user)):
    s = match.data()["suggestions"].get(cid)
    if not s:
        raise HTTPException(404, "Kein Vorschlag für dieses Gerät")
    if not _may_apply(user, s, cid):
        raise HTTPException(403, "Dafür brauchst du „Proxmox verwalten“ am Host und „Clients verwalten“ am Gerät")
    if act == "accept":
        head = (body.head if body else "") or None
        if head and not can_access_client(user, head):
            raise HTTPException(403, "Keine Berechtigung für den gewählten Head")
        try:
            entry = match.accept(cid, head, body.remember if body else True)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.add_audit_entry(user["username"], "proxmox.guest_linked", target=cid,
                           details=f"{entry['type']} {entry['vmid']} auf {_hostname(entry['host'])} "
                                   f"(Node {entry['node']}, Head {_hostname(entry['head'])})")
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

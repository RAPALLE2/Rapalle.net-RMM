# power.py (Modul "proxmox") - Gast eines Agenten ueber Proxmox starten
# --------------------------------------------------------------------
# Fuer andere Module (z.B. "wol"): Laeuft ein Agent als VM/CT in Proxmox und ist
# zugeordnet (guest_map), kann er direkt ueber die Proxmox-API eingeschaltet
# werden - ohne Wake-on-LAN, MAC oder Broadcast.
#   guest_of(client_id)          -> {host, node, type, vmid, name, host_name} | None
#   may_start(user, client_id)   -> bool  (Recht pve_power am Host + Gast sichtbar)
#   start(user, client_id)       -> {ok, upid, ...}   (wirft HTTPException)
from __future__ import annotations

from fastapi import HTTPException

from app import db

from . import match, pve_store


def guest_of(client_id: str) -> dict | None:
    m = match.mapping(client_id)
    if not m or not pve_store.is_connected(m.get("host") or ""):
        return None
    host = db.get_client(m["host"]) or {}
    return {**m, "host_name": host.get("hostname") or m["host"]}


def may_start(user: dict, client_id: str) -> bool:
    g = guest_of(client_id)
    if not g:
        return False
    from . import proxmox_routes as pr
    try:
        pr._need(user, g["host"], "pve_power")
    except HTTPException:
        return False
    return pr._guest_visible(user, g["host"], g["type"], g["vmid"])


def start(user: dict, client_id: str) -> dict:
    g = guest_of(client_id)
    if not g:
        raise HTTPException(409, "Dieses Gerät ist keiner Proxmox-VM/-CT zugeordnet.")
    from . import proxmox_routes as pr
    cl = pr._guest(user, g["host"], g["node"], g["type"], int(g["vmid"]), "pve_power")
    upid = pr._pve(cl.post, pr._gpath(g["node"], g["type"], int(g["vmid"])) + "/status/start")
    pr._audit(user, g["host"], "guest.start", f"{g['type']}/{g['vmid']} auf {g['node']} (Einschalten)")
    return {"ok": True, "upid": upid, "via": "proxmox",
            "detail": f"{'VM' if g['type'] == 'qemu' else 'CT'} {g['vmid']} auf {g['host_name']} (Node {g['node']}) gestartet"}

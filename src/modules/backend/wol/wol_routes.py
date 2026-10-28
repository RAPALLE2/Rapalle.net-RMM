# wol_routes.py (Modul "wol") - Wake-on-LAN
#   GET  /api/wol/{cid}            Status: MACs (erkannt+manuell), Konfig, verfuegbare Absender
#   PUT  /api/wol/{cid}            Konfig {macs?,broadcast?,port?,via?,enabled?}
#   POST /api/wol/{cid}/wake       PC wecken {via?}  (Recht c_power auf dem Client)
# Recht: c_power je Client (wie Herunterfahren/Neustarten).
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import can_access_client, get_current_user, require_perm

from . import store, wake

router = APIRouter(prefix="/api/wol", tags=["wol"])


def _client(user, cid):
    c = db.get_client(cid)
    if not c or not can_access_client(user, cid):
        raise HTTPException(404, "Client nicht gefunden")
    require_perm(user, "c_power", cid)
    return c


def _nodes_online() -> list[dict]:
    try:
        from app.node_manager import list_states
        import asyncio
        states = asyncio.get_event_loop().run_until_complete(list_states())  # noqa
    except Exception:
        states = []
    return states


@router.get("/{cid}")
def status(cid: str, user: dict = Depends(get_current_user)):
    _client(user, cid)
    cfg = store.get(cid)
    auto = wake.known_macs(cid)
    manual = cfg["macs"]
    # verfuegbare Absender: Backend + online-Nodes
    try:
        from app.node_manager import _sid_by_client
        nodes = [{"id": n, "name": (db.get_client(n) or {}).get("hostname") or n[:8]} for n in _sid_by_client.keys()]
    except Exception:
        nodes = []
    return {"enabled": cfg["enabled"], "config": cfg, "macs": auto, "manual_macs": manual,
            "nodes": nodes, "can_backend": True}


class CfgBody(BaseModel):
    macs: list[str] | None = None
    broadcast: str | None = None
    port: int | None = None
    via: str | None = None
    enabled: bool | None = None


@router.put("/{cid}")
def set_cfg(cid: str, body: CfgBody, user: dict = Depends(get_current_user)):
    _client(user, cid)
    if body.macs is not None:
        for m in body.macs:
            if m and not store.valid_mac(m):
                raise HTTPException(400, f"Ungültige MAC: {m}")
    cfg = store.set_config(cid, macs=body.macs, broadcast=(body.broadcast or "").strip() or None if body.broadcast is not None else None,
                           port=body.port, via=body.via, enabled=body.enabled)
    return {"ok": True, "config": cfg}


class WakeBody(BaseModel):
    via: str | None = None


@router.post("/{cid}/wake")
async def do_wake(cid: str, body: WakeBody, user: dict = Depends(get_current_user)):
    _client(user, cid)
    try:
        res = await wake.wake(cid, body.via)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:      # noqa: BLE001
        raise HTTPException(502, str(e))
    db.add_audit_entry(user["username"], "wol.wake", target=cid,
                       details=f"via {res.get('via')}, {res.get('ok_count', 0)} Pakete")
    return res

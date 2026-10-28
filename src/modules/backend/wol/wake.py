# wake.py (Modul "wol") - MACs ermitteln und Magic Packet senden (Backend oder Node)
# ---------------------------------------------------------------------------------
# MACs: automatisch aus den Live-Messwerten des Agenten (solange er mal online war)
#       + manuell gepflegte aus dem Store. Broadcast: gespeichert, sonst aus der
#       bekannten IP (x.y.z.255), sonst 255.255.255.255.
# Absender: "backend" (vom RMM-Server, nur sinnvoll wenn im selben LAN/Broadcast
#           erreichbar), eine Node-ID (Node sendet im Netz des Ziels - empfohlen),
#           oder "auto" (Node bevorzugt, sonst Backend).
from __future__ import annotations

import socket

from app import db
from app.sockets import state

from . import store


def known_macs(cid: str) -> list[str]:
    """Automatisch erkannte + manuell gepflegte MACs (eindeutig)."""
    macs = set(store.get(cid)["macs"])
    m = state.live_metrics.get(cid) or {}
    macs.add(store.norm_mac(m.get("mac") or ""))
    for i in m.get("interfaces") or []:
        if isinstance(i, dict):
            macs.add(store.norm_mac(i.get("mac") or ""))
    return sorted(x for x in macs if x and x != "00:00:00:00:00:00")


def _broadcasts(cid: str) -> list[str]:
    cfg = store.get(cid)
    out = []
    if cfg["broadcast"]:
        out.append(cfg["broadcast"])
    # aus bekannter IP ein /24-Broadcast ableiten
    ip = (db.get_client(cid) or {}).get("ip") or ""
    m = state.live_metrics.get(cid) or {}
    ips = [ip] + [str(i.get("ipv4") or "") for i in (m.get("interfaces") or []) if isinstance(i, dict)]
    for a in ips:
        parts = a.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            bc = ".".join(parts[:3] + ["255"])
            if bc not in out:
                out.append(bc)
    out.append("255.255.255.255")
    return out


def _backend_send(macs: list[str], broadcasts: list[str], port: int) -> dict:
    sent, errors = [], []
    for mac in macs:
        h = store.norm_mac(mac).replace(":", "")
        pkt = b"\xff" * 6 + bytes.fromhex(h) * 16
        for bc in broadcasts:
            for p in (port, 7):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                        sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        sk.sendto(pkt, (bc, int(p)))
                    sent.append(f"{mac} -> {bc}:{p}")
                except OSError as e:
                    errors.append(f"{mac} -> {bc}:{p}: {e}")
    return {"sent": sent, "errors": errors, "ok_count": len(sent)}


def _online_nodes() -> list[str]:
    try:
        from app.node_manager import _sid_by_client
        return list(_sid_by_client.keys())
    except Exception:
        return []


async def wake(cid: str, via: str | None = None) -> dict:
    """Weckt den PC. Rueckgabe: {ok, via, sent, errors, macs}."""
    cfg = store.get(cid)
    if not cfg["enabled"]:
        raise ValueError("Wake-on-LAN ist für dieses Gerät nicht aktiviert.")
    macs = known_macs(cid)
    if not macs:
        raise ValueError("Keine MAC-Adresse bekannt. Bitte eine MAC hinterlegen "
                         "(der Agent meldet sie automatisch, sobald er einmal online war).")
    broadcasts = _broadcasts(cid)
    port = cfg["port"]
    via = via or cfg["via"] or "auto"

    async def via_node(node_id: str) -> dict:
        from app.node_manager import command
        data = {"macs": macs, "broadcasts": broadcasts, "ports": [port, 7]}
        res = await command(node_id, "wol", data, timeout=20)
        return {"via": f"node:{node_id}", **(res or {})}

    nodes = _online_nodes()
    if via == "backend":
        return {"ok": True, "via": "backend", "macs": macs, **_backend_send(macs, broadcasts, port)}
    if via not in ("auto", "backend"):
        # bestimmte Node
        if via not in nodes:
            raise ValueError("Die gewählte Node ist offline.")
        r = await via_node(via)
        return {"ok": True, "macs": macs, **r}
    # auto: bevorzugt eine Node (am besten die des Ziel-LANs / der Host), sonst Backend
    prefer = []
    parent = (db.get_client(cid) or {}).get("parent_client_id")
    if parent and parent in nodes:
        prefer.append(parent)
    prefer += [n for n in nodes if n not in prefer]
    for node_id in prefer:
        try:
            r = await via_node(node_id)
            if r.get("ok_count"):
                return {"ok": True, "macs": macs, **r}
        except Exception:
            continue
    # keine (funktionierende) Node -> Backend
    return {"ok": True, "via": "backend", "macs": macs, **_backend_send(macs, broadcasts, port)}

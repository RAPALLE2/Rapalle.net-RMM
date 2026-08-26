"""
node_routes.py
--------------
REST-Schnittstelle rund um die Node-Stufe.

  GET  /api/nodes                       alle Nodes mit Zustand
  GET  /api/nodes/{client_id}           Zustand einer einzelnen Node
  POST /api/nodes/{client_id}/promote   Client zur Node aufwerten
  POST /api/nodes/{client_id}/demote    Node zurückstufen
  POST /api/nodes/{client_id}/l2        L2-Brücke einrichten (Treiber!)
  POST /api/nodes/{client_id}/probe     Erreichbarkeit neu prüfen lassen

  GET  /api/nodes/modules/{name}        Moduldatei (nur für Agenten)

  POST /api/nodeproxy/{client_id}       Reverse Proxy: eine Seite holen

Die Modulauslieferung läuft bewusst über den Agent-Token und nicht über
die Benutzeranmeldung - der Agent hat kein Dashboard-Konto.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app import db, node_manager
from app.auth import get_current_user, require_perm, can_access_client
from app.config import AGENT_TOKEN

router = APIRouter()


# ----------------------------------------------------------------------
# Modulauslieferung (Agent-Token)
# ----------------------------------------------------------------------

@router.get("/api/nodes/modules/{name}", response_class=PlainTextResponse)
def node_module(name: str, x_agent_token: str = Header(default="")):
    """
    Liefert eine Node-Moduldatei aus.

    Nur Dateien aus der festen Liste in node_manager.NODE_MODULES - der
    Name kommt aus dem Netz, und ein Pfad aus dem Netz darf niemals
    ungeprüft auf die Platte zeigen.
    """
    if x_agent_token != AGENT_TOKEN:
        raise HTTPException(401, "Ungültiges Agent-Token")
    path = node_manager.module_path(name)
    if not path:
        raise HTTPException(404, "Unbekanntes Modul")
    return path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# Node-Verwaltung (Dashboard)
# ----------------------------------------------------------------------

@router.get("/api/nodes")
def list_nodes(user: dict = Depends(get_current_user)):
    out = []
    for node in db.list_nodes():
        if not can_access_client(user, node["id"]):
            continue
        info = node_manager.node_info(node["id"])
        info.update({"id": node["id"], "hostname": node.get("hostname"),
                     "ip": node.get("ip"), "online": bool(node.get("online")),
                     "platform": node.get("platform")})
        out.append(info)
    return {"nodes": out}


@router.get("/api/nodes/{client_id}")
def node_state(client_id: str, user: dict = Depends(get_current_user)):
    if not can_access_client(user, client_id):
        raise HTTPException(403, "Kein Zugriff auf diesen Client")
    return node_manager.node_info(client_id)


@router.post("/api/nodes/{client_id}/promote")
async def node_promote(client_id: str, user: dict = Depends(get_current_user)):
    """
    Wertet einen Client zur Node auf.

    Bewusst an 'manage_agent' gebunden: Es ist derselbe Vorgang wie ein
    Agent-Update - es wird Code auf das Gerät gebracht.
    """
    require_perm(user, "manage_agent", client_id)
    try:
        result = await node_manager.promote(client_id, user["username"])
    except ValueError as e:
        raise HTTPException(404, str(e))
    await _broadcast(client_id)
    return result


@router.post("/api/nodes/{client_id}/demote")
async def node_demote(client_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_agent", client_id)
    try:
        result = await node_manager.demote(client_id, user["username"])
    except ValueError as e:
        raise HTTPException(404, str(e))
    await _broadcast(client_id)
    return result


class WgBody(BaseModel):
    # Ohne ausdrueckliche Zustimmung wird nichts installiert. Das Feld hat
    # deshalb bewusst KEINEN Standardwert True.
    install: bool = False


@router.post("/api/nodes/{client_id}/wireguard")
async def node_wireguard(client_id: str, body: WgBody,
                         user: dict = Depends(get_current_user)):
    """
    Richtet WireGuard auf der Node ein.

    Damit enden Tunnel direkt auf dem Geraet statt im Backend - schneller,
    und die Nutzdaten laufen am Server vorbei. Unter Windows wird dabei der
    offizielle WireGuard-Dienst installiert (Treiber, Administratorrechte);
    unter Linux wird NICHT automatisch installiert, sondern gesagt, was zu
    tun ist.
    """
    require_perm(user, "manage_agent", client_id)
    if not db.is_client_node(client_id):
        raise HTTPException(400, "Nur Nodes koennen WireGuard betreiben")

    from app.sockets import request_node_wg
    from app import vpn, vpn_net

    try:
        if body.install:
            result = await request_node_wg(client_id, "install")
            if not result.get("ok"):
                return result
        priv, _pub = vpn.node_keys(client_id)
        result = await request_node_wg(client_id, "start", {
            "private_key": priv,
            "wg_port": vpn.node_port(),
            "address": f"{vpn_net.client_address(client_id)}/32",
            "probe": {
                "host": vpn.endpoint_host(),
                "relay_port": __import__("app.wg_relay", fromlist=["x"]).relay_port(),
                "token": "",
            },
        })
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    db.add_audit_entry(user["username"], "node.wireguard",
                       target=client_id,
                       details=f"install={body.install}, "
                               f"Ergebnis={result.get('reason')}")
    await _broadcast(client_id)
    return result


@router.post("/api/nodes/{client_id}/probe")
async def node_probe(client_id: str, user: dict = Depends(get_current_user)):
    """
    Lässt die Node ihre Erreichbarkeit von aussen neu prüfen.

    Die Node schickt ein Paket an den UDP-Port des Backends; die dort
    beobachtete Absenderadresse ist die, unter der sie erreichbar ist.
    Kommt nichts an, bleibt es beim Betrieb über das Backend.
    """
    require_perm(user, "manage_agent", client_id)
    ok = await node_manager.push_config(client_id)
    if not ok:
        raise HTTPException(503, "Node ist offline oder keine Node")
    return {"ok": True, "hint": "Ergebnis erscheint in wenigen Sekunden"}


# ----------------------------------------------------------------------
# Reverse Proxy
# ----------------------------------------------------------------------

class ProxyBody(BaseModel):
    url: str
    method: str = "GET"
    headers: dict = {}
    body_b64: str = ""
    insecure: bool = False


@router.post("/api/nodeproxy/{client_id}")
async def node_proxy(client_id: str, body: ProxyBody,
                     user: dict = Depends(get_current_user)):
    """
    Holt eine Seite aus dem Netz hinter der Node.

    Damit lassen sich im internen Browser Adressen öffnen, die nur dort
    erreichbar sind - ohne dass der Benutzer einen VPN-Tunnel starten muss.
    Wer den Tunnel ohnehin offen hat, ruft die Adresse einfach direkt auf;
    dann läuft nichts über dieses Backend.
    """
    require_perm(user, "c_nodeproxy", client_id)
    if not db.is_client_node(client_id):
        raise HTTPException(400, "Der Reverse Proxy läuft nur auf Nodes")
    from app.sockets import request_node_proxy
    try:
        result = await request_node_proxy(client_id, {
            "url": body.url, "method": body.method,
            "headers": body.headers, "body": body.body_b64,
            "insecure": body.insecure,
        })
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    if not result.get("ok"):
        raise HTTPException(502, result.get("error") or "Abruf fehlgeschlagen")
    db.add_audit_entry(user["username"], "nodeproxy.fetch", target=client_id,
                       details=body.url[:300])
    return result


async def _broadcast(client_id: str) -> None:
    try:
        from app.sockets import sio
        await sio.emit("node-changed", {"client_id": client_id},
                       namespace="/dashboard")
    except Exception:
        pass

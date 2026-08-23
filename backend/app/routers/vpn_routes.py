"""
vpn_routes.py
-------------
REST-Schnittstelle der VPN-App.

  GET  /api/vpn/info                  Serverzustand + Auswahlmöglichkeiten
  GET  /api/vpn/tunnels               Übersicht aller offenen Tunnel
  POST /api/vpn/tunnels               neuen Tunnel ausstellen (liefert .conf)
  GET  /api/vpn/tunnels/{id}/config   .conf erneut holen  -> NICHT möglich
  POST /api/vpn/tunnels/{id}/revoke   Tunnel sofort schliessen

Zur nicht möglichen Wiederausgabe: Der private Schlüssel eines Tunnels wird
nirgends gespeichert. Er entsteht beim Ausstellen, wandert einmal in die
Antwort und ist danach weg. Wer die Datei verliert, stellt einen neuen
Tunnel aus - das ist der Punkt, nicht ein Versehen.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app import db, vpn
from app.auth import (get_current_user, require_perm, user_has_permission,
                      can_access_client)

router = APIRouter()


class TunnelBody(BaseModel):
    client_id: str
    minutes: int = 60          # 0 = unbegrenzt (setzt das Unbegrenzt-Recht voraus)
    name: str = ""
    routes: str = ""           # zusätzliche Netze, kommagetrennt


@router.get("/api/vpn/info")
def vpn_info(user: dict = Depends(get_current_user)):
    """Grunddaten für die Oberfläche: läuft der Endpunkt, wie heisst er?"""
    _, server_pub = vpn.server_keys()
    return {
        "enabled": vpn.vpn_enabled(),
        "running": vpn.rt.started,
        "port": vpn.vpn_port(),
        "subnet": vpn.vpn_subnet(),
        "server_public_key": server_pub,
        "endpoint_host": vpn.endpoint_host(),
        "may_unlimited": user_has_permission(user, "vpn_unlimited"),
    }


@router.get("/api/vpn/tunnels")
def vpn_tunnels(client_id: str | None = None,
                      user: dict = Depends(get_current_user)):
    """
    Übersicht aller offenen Tunnel.

    Sichtbar ist immer nur, was der Benutzer auch sehen darf: Tunnel auf
    Clients ohne Zugriffsrecht werden ausgeblendet.
    """
    items = [t for t in vpn.tunnel_overview()
             if can_access_client(user, t.get("client_id"))]
    if client_id:
        items = [t for t in items if t.get("client_id") == client_id]
    # Hostnamen für die Anzeige mitliefern.
    for t in items:
        c = db.get_client(t.get("client_id"))
        t["hostname"] = (c or {}).get("hostname") or t.get("client_id")
        t["client_online"] = bool((c or {}).get("online"))
    return {"tunnels": items, "now": int(time.time() * 1000)}


@router.post("/api/vpn/tunnels")
async def vpn_create(body: TunnelBody, user: dict = Depends(get_current_user)):
    """Stellt einen Tunnel aus und gibt die fertige .conf im Klartext zurück."""
    require_perm(user, "c_vpn", body.client_id)
    if not vpn.vpn_enabled():
        raise HTTPException(400, "VPN ist in den Einstellungen deaktiviert")
    if not vpn.rt.started:
        raise HTTPException(
            503, "Der VPN-Endpunkt läuft nicht – ist der UDP-Port "
                 f"{vpn.vpn_port()} im Container freigegeben?")

    minutes = int(body.minutes or 0)
    if minutes <= 0:
        may = (user_has_permission(user, "vpn_unlimited")
               or user_has_permission(user, "c_vpn_unlimited", body.client_id))
        if not may:
            raise HTTPException(
                403, "Unbegrenzte Tunnel sind nicht erlaubt – bitte eine "
                     "Laufzeit wählen.")

    try:
        record = vpn.create_tunnel(
            client_id=body.client_id, username=user["username"],
            minutes=minutes, name=body.name.strip(), routes=body.routes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(507, str(e))

    db.add_audit_entry(user["username"], "vpn.tunnel_created",
                       target=body.client_id,
                       details=(f"{record['name']} – "
                                + (f"{minutes} min" if minutes > 0 else "unbegrenzt")))
    try:
        from app.sockets import sio
        await sio.emit("vpn-changed", {"tunnel_id": record["id"]},
                       namespace="/dashboard")
    except Exception:
        pass

    # Dateiname, den die Oberfläche zum Herunterladen verwendet. WireGuard
    # erlaubt nur Buchstaben, Ziffern, Unterstrich, Bindestrich und Punkt -
    # und höchstens 15 Zeichen vor dem ".conf".
    safe = "".join(ch for ch in (record["name"] or "tunnel")
                   if ch.isalnum() or ch in "-_")[:15] or "tunnel"
    record["filename"] = f"{safe}.conf"
    return record


@router.post("/api/vpn/tunnels/{tunnel_id}/revoke")
async def vpn_revoke(tunnel_id: str, user: dict = Depends(get_current_user)):
    """Schliesst einen Tunnel sofort. Die ausgestellte Datei ist danach tot."""
    row = db.get_vpn_tunnel(tunnel_id)
    if not row:
        raise HTTPException(404, "Tunnel unbekannt")
    require_perm(user, "c_vpn", row["client_id"])
    vpn.revoke_tunnel(tunnel_id)
    db.add_audit_entry(user["username"], "vpn.tunnel_revoked",
                       target=row["client_id"], details=row.get("name"))
    try:
        from app.sockets import sio
        await sio.emit("vpn-changed", {"tunnel_id": tunnel_id},
                       namespace="/dashboard")
    except Exception:
        pass
    return {"ok": True, "tunnel_id": tunnel_id}


@router.get("/api/vpn/tunnels/{tunnel_id}/config", response_class=PlainTextResponse)
def vpn_config_gone(tunnel_id: str, user: dict = Depends(get_current_user)):
    """
    Absichtlich kein Download: Der private Schlüssel wird nicht gespeichert.

    Diese Route existiert nur, damit ein Aufruf eine verständliche Antwort
    bekommt statt eines 404, das nach einem Fehler aussieht.
    """
    raise HTTPException(
        410, "Die Tunnel-Datei wird nur einmal beim Ausstellen ausgegeben. "
             "Bitte einen neuen Tunnel ausstellen.")

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
    # 'peer' (nur dieses Geraet), 'site' (ganzes Netz), 'net' (virtuelles Netz)
    mode: str = "peer"
    name: str = ""
    routes: str = ""           # zusaetzliche Netze, kommagetrennt


@router.get("/api/vpn/info")
def vpn_info(user: dict = Depends(get_current_user)):
    """Zustand des VPN: Betriebsarten, virtuelles Netz, Relay, Nodes."""
    data = vpn.status()
    data["may_unlimited"] = user_has_permission(user, "vpn_unlimited")
    return data


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
    if body.mode == vpn.MODE_NET and not vpn.rt.wg:
        raise HTTPException(
            503, "Das virtuelle Netz läuft nicht. Es braucht wireguard-go "
                 "und /dev/net/tun im Container. Peer-to-Peer und "
                 "Site-to-Site funktionieren davon unabhängig.")

    # Ohne bekannte Server-Adresse waere die .conf UNBRAUCHBAR: In der Zeile
    # 'Endpoint' stuende nur ein Platzhalter, und der WireGuard-Client haette
    # gar kein Ziel zum Verbinden. Frueher wurde die Datei trotzdem
    # ausgestellt - sie sah vollstaendig aus, es kam nur nie ein Handschlag
    # zustande, und im Protokoll stand dazu nichts. Das ist genau die Art
    # Fehler, an der man stundenlang sucht.
    if body.mode == vpn.MODE_NET and not vpn.endpoint_host():
        raise HTTPException(
            400,
            "Die Adresse dieses Servers ist nicht hinterlegt – ohne sie "
            "enthält die Tunnel-Datei kein gültiges Ziel und der Tunnel "
            "kann nicht zustande kommen. Bitte unter Einstellungen → "
            "Allgemein die Server-Adresse setzen (oder für das VPN "
            "abweichend unter 'Adresse des VPN-Endpunkts').")

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
            minutes=minutes, mode=body.mode, name=body.name.strip(),
            routes=body.routes)
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

    record["filename"] = _tunnel_filename(record)
    return record


def _tunnel_filename(record: dict) -> str:
    """
    Baut den Dateinamen aus Bezeichnung, Ausstellungs- und Ablaufzeit.

    Warum die Zeiten in den Namen gehören: Tunnel-Dateien sammeln sich im
    Download-Ordner an, sehen alle gleich aus und laufen ab. Ohne Datum
    weiss nach zwei Tagen niemand mehr, welche davon noch gilt - und man
    probiert der Reihe nach durch. Mit den Zeiten im Namen ist das auf
    einen Blick klar.

    WireGuard ist beim Namen der Verbindung streng: Er darf nur
    Buchstaben, Ziffern, Unterstrich, Bindestrich und Punkt enthalten und
    höchstens 15 Zeichen lang sein (der Teil vor '.conf'). Deshalb wird die
    Bezeichnung stark gekürzt, damit die Zeiten hineinpassen.
    """
    import time as _t

    def stamp(ms: int) -> str:
        return _t.strftime("%y%m%d-%H%M", _t.localtime(ms / 1000))

    base = "".join(ch for ch in (record.get("name") or "tunnel")
                   if ch.isalnum() or ch in "-_")
    created = stamp(record.get("created_at") or int(_t.time() * 1000))
    expires = stamp(record["expires_at"]) if record.get("expires_at") else "unbegrenzt"

    # 15 Zeichen sind das Limit für den Verbindungsnamen. Beide Zeiten
    # zusammen sind bereits länger - der volle Name landet deshalb im
    # DATEINAMEN, und für WireGuard selbst wird zusätzlich ein kurzer
    # Verbindungsname mitgeliefert.
    full = f"{base or 'tunnel'}_{created}_bis_{expires}"
    record["display_name"] = full
    record["wg_name"] = (base[:15] or "tunnel")
    return f"{full}.conf"


@router.get("/api/vpn/network")
def vpn_network(user: dict = Depends(get_current_user)):
    """
    Das virtuelle Netz: wer ist drin, unter welcher Adresse, welcher Name.

    Sichtbar ist nur, worauf der Benutzer ohnehin Zugriff hat - eine
    Netzuebersicht darf nicht zur Geraeteliste fuer Unbefugte werden.
    """
    from app import vpn_net
    visible = []
    for m in vpn_net.members():
        if m["kind"] == "client" and not can_access_client(user, m["ref"]):
            continue
        visible.append(m)
    return {
        "subnet": vpn.vpn_subnet(),
        "router": vpn_net.router_address(),
        "zone": vpn_net.zone(),
        "members": visible,
        "clients": sum(1 for m in visible if m["kind"] == "client"),
        "users": sum(1 for m in visible if m["kind"] == "user"),
    }


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

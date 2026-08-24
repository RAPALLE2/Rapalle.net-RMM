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
        # Die Adresse, unter der Dienste auf dem Geraet selbst erreichbar
        # sind. 'localhost' funktioniert dafuer NICHT - das zeigt auf den
        # Rechner des Benutzers und geht nie durch den Tunnel.
        "loopback_alias": vpn.loopback_alias(),
        "server_public_key": server_pub,
        "endpoint_host": vpn.endpoint_host(),
        "may_unlimited": user_has_permission(user, "vpn_unlimited"),
        # Sagt, an welcher der drei moeglichen Stellen es haengt.
        "check": vpn.endpoint_check(),
        # Welche Umsetzung laeuft: 'wireguard-go' (Referenz) oder 'python'
        # (eigene Rueckfallebene). Das gehoert sichtbar - die beiden
        # verhalten sich nicht gleich zuverlaessig.
        "engine": vpn.rt.engine,
        "engine_check": _engine_info(),
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

    # Ohne bekannte Server-Adresse waere die .conf UNBRAUCHBAR: In der Zeile
    # 'Endpoint' stuende nur ein Platzhalter, und der WireGuard-Client haette
    # gar kein Ziel zum Verbinden. Frueher wurde die Datei trotzdem
    # ausgestellt - sie sah vollstaendig aus, es kam nur nie ein Handschlag
    # zustande, und im Protokoll stand dazu nichts. Das ist genau die Art
    # Fehler, an der man stundenlang sucht.
    if not vpn.endpoint_host():
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


def _engine_info() -> dict:
    from app import wg_userspace
    req = wg_userspace.requirements()
    req["hint"] = (wg_userspace.windows_hint() if req["platform"].lower()
                   .startswith("win") else "")
    return req


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


@router.get("/api/vpn/selftest")
def vpn_selftest(user: dict = Depends(get_current_user)):
    """
    Prüft die WireGuard-Umsetzung gegen sich selbst und spielt – falls
    vorhanden – das letzte gescheiterte Handschlag-Paket Schritt für
    Schritt durch.

    Das beantwortet die Frage, die man sonst nur durch Codelesen klären
    kann: Liegt es am Server oder am Client? Der Selbsttest braucht dafür
    keinen Client.
    """
    if not vpn.rt.server:
        raise HTTPException(503, "Der VPN-Endpunkt läuft nicht.")
    result = vpn.rt.server.selftest()
    captured = getattr(vpn.rt.server, "captured", None)
    if captured:
        result["gescheitertes_paket"] = {
            "von": captured.get("from"),
            "schritt": captured.get("step"),
            "hex": captured.get("hex"),
            "schritte": vpn.rt.server.replay(captured.get("hex", "")),
        }
    return result


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

# connect_routes.py (Modul "client")
# ----------------------------------
# Wohin verbindet die Client-App beim Fernbild (Remotedesktop / VNC) bzw. per SSH?
#
#   Ziel (host_mode)    gateway  RMM-Gateway: der Server richtet eine Weiterleitung ein,
#                                erreichbar nur von der IP des anfragenden Geraets
#                       local    lokale IP des Zielgeraets (vom Agenten gemeldet) - z.B. im selben Netz
#                       custom   eigene Adresse (VPN, Portfreigabe, DNS-Name ...)
#                       last     zuletzt benutzte Verbindung
#                       tunnel   ueber RMM per Cloudflare-Tunnel (Modul "cloudflare"): Backend leitet zum
#                                Agent weiter, die App verbindet mit cloudflared + Service-Token.
#                                Lebensdauer (lifetime): session (bis Nutzungsende) | 10|30|60|120 Min. | 0 dauerhaft
#   Port (port_mode)    standard 3389 (RDP) / 5900 (VNC) / 22 (SSH)
#                       gateway  Port der RMM-Weiterleitung (nur mit Ziel "gateway")
#                       custom   eigener Port
#
# Pro BENUTZER und ZIELGERAET (und Protokoll) wird gespeichert:
#   default  die Voreinstellung ("Als Standard fuer dieses Geraet merken")
#            - ohne Voreinstellung ist "lokale IP + Standard-Port" vorgewaehlt
#   last     die zuletzt benutzte Verbindung ("Letzte Verbindung wieder verwenden")
# Ablage: data/modules/client/targets/<user_id>.json - gilt auf ALLEN Geraeten des Benutzers.
#
#   GET    /api/client/targets/{client_id}?protocol=rdp|vnc
#   PUT    /api/client/targets/{client_id}          {protocol, host_mode, host, port_mode, port}
#   DELETE /api/client/targets/{client_id}?protocol=
#   POST   /api/client/connect                      {client_id, protocol, host_mode, host, port_mode, port, lifetime, user, remember}
#   POST   /api/client/tunnel/{lease}/ping | DELETE /api/client/tunnel/{lease}   Nutzung melden / beenden
#   POST   /api/client/tunnels/{route_id}/close     Fernzugriff sofort schliessen
#   GET|PUT /api/client/prefs                        {open_mode: web|external|ask} (Profil)
#   POST   /api/client/launch                        Dashboard -> Startschein fuer die Client-App (rapalle-rmm://)
#   POST   /api/client/launch/{ticket}               Client-App loest den Startschein ein (einmalig, 2 Min.)

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db, modules
from app.auth import can_access_client, get_current_user, require_perm, user_has_permission
from app.paths import MODULES_DATA_DIR

router = APIRouter(prefix="/api/client", tags=["client-connect"])

STANDARD_PORTS = {"rdp": 3389, "vnc": 5900, "ssh": 22}
HOST_MODES = ("gateway", "local", "custom", "last", "tunnel")
LIFETIMES = ("session", "10", "30", "60", "120", "0")
PROTO_PERM = {"rdp": "c_screen", "vnc": "c_screen", "ssh": "c_terminal_console"}
_USER_RE = re.compile(r"^[A-Za-z0-9._@\\-]{0,64}$")
PORT_MODES = ("standard", "gateway", "custom")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]_]{1,253}$")
_lock = threading.RLock()


# ------------------------------------------------------------------ Ablage
def _file(user_id: str):
    safe = "".join(c for c in str(user_id) if c.isalnum() or c in "-_")
    d = MODULES_DATA_DIR / "client" / "targets"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.json"


def _load(user_id: str) -> dict:
    try:
        d = json.loads(_file(user_id).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(user_id: str, data: dict) -> None:
    f = _file(user_id)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def _entry(user_id: str, client_id: str, protocol: str) -> dict:
    return (_load(user_id).get(client_id) or {}).get(protocol) or {}


def _store(user_id: str, client_id: str, protocol: str, key: str, value: dict | None) -> None:
    with _lock:
        data = _load(user_id)
        node = data.setdefault(client_id, {}).setdefault(protocol, {})
        if value is None:
            node.pop(key, None)
        else:
            node[key] = value
        _save(user_id, data)


# ------------------------------------------------------------------ Pruefung
def _proto(p: str) -> str:
    p = (p or "rdp").lower()
    if p not in STANDARD_PORTS:
        raise HTTPException(400, "Protokoll muss rdp, vnc oder ssh sein")
    return p


def _client(user: dict, client_id: str) -> dict:
    c = db.get_client(client_id) if hasattr(db, "get_client") else None
    if not c or not can_access_client(user, client_id):
        raise HTTPException(404, "Gerät nicht gefunden")
    return c


def _gateway_available() -> bool:
    return modules.is_enabled("network") and modules.feature("network", "portforward")


def _tunnel_status() -> dict:
    if not modules.is_enabled("cloudflare"):
        return {"ok": False, "reason": "Modul „Cloudflare Tunnel“ ist nicht aktiv"}
    if not _gateway_available():
        return {"ok": False, "reason": "Modul „Netzwerk“ (Weiterleitungen) ist nicht aktiv"}
    try:
        return modules.use("cloudflare", "remote").ready()
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "reason": str(e)}


def _may_unlimited(user: dict, client_id: str) -> bool:
    return user_has_permission(user, "relay_unlimited") or user_has_permission(user, "c_relay_unlimited", client_id)


def _need_perm(user: dict, proto: str, client_id: str) -> None:
    perm = PROTO_PERM.get(proto)
    if perm and not user_has_permission(user, perm, client_id):
        raise HTTPException(403, f"Fehlendes Recht: {perm}")


class Pref(BaseModel):
    protocol: str = "rdp"
    host_mode: str = "local"
    host: str = ""
    port_mode: str = "standard"
    port: int | None = None
    lifetime: str = "session"
    user: str = ""


def _clean(p: Pref) -> dict:
    hm, pm = p.host_mode, p.port_mode
    if hm not in HOST_MODES or hm == "last":
        raise HTTPException(400, "Ziel muss gateway, local, custom oder tunnel sein")
    if str(p.lifetime) not in LIFETIMES:
        raise HTTPException(400, "Lebensdauer: session, 10, 30, 60, 120 oder 0")
    if not _USER_RE.match(p.user or ""):
        raise HTTPException(400, "Ungültiger Benutzername")
    if pm not in PORT_MODES:
        raise HTTPException(400, "Port muss standard, gateway oder custom sein")
    if hm == "gateway":
        pm = "gateway"                       # Gateway-Ziel = immer Gateway-Port
    elif pm == "gateway":
        raise HTTPException(400, "Den Gateway-Port gibt es nur mit dem Ziel „RMM-Gateway“")
    host = (p.host or "").strip()
    if hm == "custom":
        if not host or not _HOST_RE.match(host):
            raise HTTPException(400, "Bitte eine gültige Adresse (IP oder Name) eingeben")
    else:
        host = ""
    port = None
    if pm == "custom":
        if not p.port or not 1 <= int(p.port) <= 65535:
            raise HTTPException(400, "Port muss zwischen 1 und 65535 liegen")
        port = int(p.port)
    return {"host_mode": hm, "host": host, "port_mode": pm, "port": port,
            "lifetime": str(p.lifetime) if hm == "tunnel" else "session", "user": (p.user or "").strip()}


_KEYS = ("host_mode", "host", "port_mode", "port", "lifetime", "user")


# ------------------------------------------------------------------ Routen
@router.get("/targets/{client_id}")
def target_info(client_id: str, protocol: str = "rdp", user: dict = Depends(get_current_user)):
    proto = _proto(protocol)
    c = _client(user, client_id)
    e = _entry(user["id"], client_id, proto)
    local_ip = (c.get("ip") or "").strip()
    default = e.get("default") or ({"host_mode": "local", "host": "", "port_mode": "standard", "port": None}
                                   if local_ip else {"host_mode": "gateway", "host": "", "port_mode": "gateway", "port": None})
    return {"client": {"id": c["id"], "hostname": c.get("hostname"), "online": bool(c.get("online"))},
            "protocol": proto, "local_ip": local_ip, "standard_port": STANDARD_PORTS[proto],
            "gateway_available": _gateway_available(), "default": default, "has_saved_default": bool(e.get("default")),
            "last": e.get("last"), "tunnel": _tunnel_status(), "may_unlimited": _may_unlimited(user, client_id),
            "tunnels": _tunnels_of(client_id)}


def _tunnels_of(client_id: str) -> list:
    if not modules.is_enabled("cloudflare"):
        return []
    try:
        return modules.use("cloudflare", "remote").list_for(client_id)
    except Exception:
        return []


@router.put("/targets/{client_id}")
def save_default(client_id: str, body: Pref, user: dict = Depends(get_current_user)):
    proto = _proto(body.protocol)
    _client(user, client_id)
    pref = _clean(body)
    if pref["lifetime"] == "0" and not _may_unlimited(user, client_id):
        raise HTTPException(403, "Dauerhaft offen braucht das Recht „Relay unbegrenzt“")
    _store(user["id"], client_id, proto, "default", pref)
    return {"ok": True, "default": pref}


@router.delete("/targets/{client_id}")
def reset_default(client_id: str, protocol: str = "rdp", user: dict = Depends(get_current_user)):
    proto = _proto(protocol)
    _client(user, client_id)
    _store(user["id"], client_id, proto, "default", None)
    return {"ok": True}


class ConnectBody(Pref):
    client_id: str
    remember: bool = False


@router.post("/connect")
async def connect(body: ConnectBody, request: Request, user: dict = Depends(get_current_user)):
    """Ziel + Port aufloesen. Nur beim RMM-Gateway richtet der Server eine Weiterleitung ein."""
    proto = _proto(body.protocol)
    c = _client(user, body.client_id)
    _need_perm(user, proto, c["id"])
    std = STANDARD_PORTS[proto]
    if body.host_mode == "last":
        last = _entry(user["id"], body.client_id, proto).get("last")
        if not last:
            raise HTTPException(404, "Noch keine letzte Verbindung für dieses Gerät")
        pref = {k: last.get(k) for k in _KEYS}
        pref["lifetime"] = str(pref.get("lifetime") or "session")
        pref["user"] = pref.get("user") or ""
        if pref["host_mode"] in ("gateway", "tunnel"):
            pref["host"] = ""
    else:
        pref = _clean(body)
    if pref["host_mode"] == "tunnel" and pref["lifetime"] == "0" and not _may_unlimited(user, c["id"]):
        raise HTTPException(403, "Dauerhaft offen braucht das Recht „Relay unbegrenzt“")
    result = {"protocol": proto, "client_id": c["id"], "host_mode": pref["host_mode"], "port_mode": pref["port_mode"],
              "user": pref.get("user") or ""}
    if pref["host_mode"] == "tunnel":
        ts = _tunnel_status()
        if not ts["ok"]:
            raise HTTPException(409, f"Über RMM (Tunnel) nicht möglich: {ts['reason']}")
        port = pref["port"] if pref["port_mode"] == "custom" else std
        try:
            t = await asyncio.to_thread(modules.use("cloudflare", "remote").open_tunnel, user, c["id"], proto, port,
                                        "127.0.0.1", pref["lifetime"])
        except (ValueError, LookupError) as e:
            raise HTTPException(409, str(e))
        result.update({"tunnel": True, "hostname": t["hostname"], "lease": t["lease"], "token_id": t["token_id"],
                       "token_secret": t["token_secret"], "lifetime": t["lifetime"], "expires": t["expires"],
                       "agent_port": port})
        last = {"host_mode": "tunnel", "host": "", "port_mode": pref["port_mode"], "port": pref["port"],
                "lifetime": pref["lifetime"], "user": pref.get("user") or ""}
        result["host"], result["port"] = t["hostname"], port
    elif pref["host_mode"] == "gateway":
        # Bestehende Logik des Moduls: Weiterleitung nur fuer die IP dieses Geraets
        from .client_routes import screen, ScreenBody
        fw = await screen(ScreenBody(client_id=c["id"], protocol=proto), request, user)
        result.update({"host": fw["host"], "port": fw["port"], "forward_id": fw["forward_id"],
                       "expires": fw.get("expires"), "allow_from": fw.get("allow_from")})
        last = {"host_mode": "gateway", "host": "", "port_mode": "gateway", "port": None, "user": pref.get("user") or ""}
    else:
        if pref["host_mode"] == "local":
            host = (c.get("ip") or "").strip()
            if not host:
                raise HTTPException(409, "Der Agent hat keine lokale IP gemeldet – bitte eigene Adresse oder RMM-Gateway wählen")
        else:
            host = pref["host"]
        port = pref["port"] if pref["port_mode"] == "custom" else std
        try:
            is_ip = bool(ipaddress.ip_address(host.strip("[]")))
        except ValueError:
            is_ip = False
        result.update({"host": host, "port": port, "is_ip": is_ip})
        last = {"host_mode": pref["host_mode"], "host": host if pref["host_mode"] == "custom" else "",
                "port_mode": pref["port_mode"], "port": pref["port"], "user": pref.get("user") or ""}
    last.update({"display": {"gateway": "RMM-Gateway", "tunnel": "Über RMM (Tunnel)"}.get(pref["host_mode"],
                                                                                         f"{result['host']}:{result['port']}"),
                 "at": int(time.time())})
    _store(user["id"], c["id"], proto, "last", last)
    if body.remember and body.host_mode != "last":
        _store(user["id"], c["id"], proto, "default", {k: pref.get(k) for k in _KEYS})
    db.add_audit_entry(user["username"], "client.connect", target=c["id"],
                       details=f"{proto} {pref['host_mode']} {result['host']}:{result['port']}")
    return result


# ------------------------------------------------------------------ Tunnel-Nutzung (Leases)
def _remote():
    if not modules.is_enabled("cloudflare"):
        raise HTTPException(409, "Modul „Cloudflare Tunnel“ ist nicht aktiv")
    return modules.use("cloudflare", "remote")


@router.post("/tunnel/{lease}/ping")
async def tunnel_ping(lease: str, user: dict = Depends(get_current_user)):
    try:
        return await asyncio.to_thread(_remote().ping, lease, user)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.delete("/tunnel/{lease}")
async def tunnel_release(lease: str, user: dict = Depends(get_current_user)):
    await asyncio.to_thread(_remote().release, lease, user)
    return {"ok": True}


@router.post("/tunnels/{route_id}/close")
async def tunnel_close(route_id: str, user: dict = Depends(get_current_user)):
    _remote()
    data = modules.use("cloudflare", "store").load()
    r = next((x for x in data["routes"] if x["id"] == route_id and x.get("remote")), None)
    if not r:
        raise HTTPException(404, "Fernzugriff nicht gefunden")
    _client(user, r["ref"]["client_id"])
    _need_perm(user, r["ref"].get("proto", "rdp"), r["ref"]["client_id"])
    sync = modules.use("cloudflare", "sync")
    await asyncio.to_thread(sync.remove_route, route_id)
    await asyncio.to_thread(sync.apply, user["username"])
    db.add_audit_entry(user["username"], "client.tunnel_closed", target=r["ref"]["client_id"], details=r.get("hostname", ""))
    return {"ok": True}


# ------------------------------------------------------------------ Profil: Standard Web/Extern
class PrefsBody(BaseModel):
    open_mode: str = "external"


@router.get("/prefs")
def get_prefs(user: dict = Depends(get_current_user)):
    return {"open_mode": (_load(user["id"]).get("_prefs") or {}).get("open_mode", "external")}


@router.put("/prefs")
def put_prefs(body: PrefsBody, user: dict = Depends(get_current_user)):
    if body.open_mode not in ("web", "external", "ask"):
        raise HTTPException(400, "open_mode: web, external oder ask")
    with _lock:
        data = _load(user["id"])
        data["_prefs"] = {**(data.get("_prefs") or {}), "open_mode": body.open_mode}
        _save(user["id"], data)
    return {"ok": True, "open_mode": body.open_mode}


# ------------------------------------------------------------------ Startschein Dashboard -> Client-App
# Das Dashboard (im App-Fenster der Client-App) holt einen Startschein und oeffnet
# rapalle-rmm://launch?s=<server>&t=<schein>. Die App loest ihn mit IHRER Anmeldung ein -
# fremde Webseiten koennen so nichts starten (der Schein gehoert zum angemeldeten Benutzer).
_tickets: dict[str, dict] = {}


class LaunchBody(Pref):
    client_id: str
    remember: bool = False
    vpn_tunnel: str = ""        # Ziel ueber VPN: die App richtet diesen Tunnel vorher selbst ein (Modul "vpn")


@router.post("/launch")
def launch(body: LaunchBody, request: Request, user: dict = Depends(get_current_user)):
    proto = (body.protocol or "").lower()
    c = _client(user, body.client_id)
    if proto == "shell":
        if not user_has_permission(user, "c_terminal_console", c["id"]):
            raise HTTPException(403, "Fehlendes Recht: c_terminal_console")
        params = {"protocol": "shell", "client_id": c["id"]}
    else:
        proto = _proto(proto)
        _need_perm(user, proto, c["id"])
        pref = _clean(body) if body.host_mode != "last" else {"host_mode": "last"}
        if pref.get("lifetime") == "0" and not _may_unlimited(user, c["id"]):
            raise HTTPException(403, "Dauerhaft offen braucht das Recht „Relay unbegrenzt“")
        params = {"protocol": proto, "client_id": c["id"], **pref, "remember": bool(body.remember)}
        if body.vpn_tunnel:
            params["vpn_tunnel"] = body.vpn_tunnel[:32]
    now = time.time()
    for k in [k for k, v in _tickets.items() if v["exp"] < now]:
        _tickets.pop(k, None)
    t = secrets.token_urlsafe(24)
    _tickets[t] = {"user_id": user["id"], "exp": now + 120, "params": params}
    try:
        from router.enrollment_routes import _backend_url
        base = _backend_url(request).rstrip("/")
    except Exception:
        base = str(request.base_url).rstrip("/")
    from urllib.parse import urlencode
    return {"ticket": t, "url": "rapalle-rmm://launch?" + urlencode({"s": base, "t": t}), "params": params}


class LaunchVpnBody(BaseModel):
    tunnel: str


@router.post("/launch-vpn")
def launch_vpn(body: LaunchVpnBody, request: Request, user: dict = Depends(get_current_user)):
    """Startschein: die PC-App holt die eigene WireGuard-Datei dieses Tunnels und aktiviert sie sofort."""
    now = time.time()
    for k in [k for k, v in _tickets.items() if v["exp"] < now]:
        _tickets.pop(k, None)
    t = secrets.token_urlsafe(24)
    _tickets[t] = {"user_id": user["id"], "exp": now + 120, "params": {"protocol": "vpn", "vpn_tunnel": body.tunnel[:32]}}
    try:
        from router.enrollment_routes import _backend_url
        base = _backend_url(request).rstrip("/")
    except Exception:
        base = str(request.base_url).rstrip("/")
    from urllib.parse import urlencode
    return {"ticket": t, "url": "rapalle-rmm://launch?" + urlencode({"s": base, "t": t})}


@router.post("/launch/{ticket}")
def redeem(ticket: str, user: dict = Depends(get_current_user)):
    t = _tickets.pop(ticket, None)
    if not t or t["exp"] < time.time() or t["user_id"] != user["id"]:
        raise HTTPException(404, "Startschein ungültig oder abgelaufen – bitte im Dashboard erneut klicken")
    return t["params"]

# connect_routes.py (Modul "client")
# ----------------------------------
# Wohin verbindet die Client-App beim Fernbild (Remotedesktop / VNC)?
#
#   Ziel (host_mode)    gateway  RMM-Gateway: der Server richtet eine Weiterleitung ein,
#                                erreichbar nur von der IP des anfragenden Geraets
#                       local    lokale IP des Zielgeraets (vom Agenten gemeldet) - z.B. im selben Netz
#                       custom   eigene Adresse (VPN, Portfreigabe, DNS-Name ...)
#                       last     zuletzt benutzte Verbindung
#   Port (port_mode)    standard 3389 (RDP) bzw. 5900 (VNC)
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
#   POST   /api/client/connect                      {client_id, protocol, host_mode, host, port_mode, port, remember}

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db, modules
from app.auth import can_access_client, get_current_user, require_perm
from app.paths import MODULES_DATA_DIR

router = APIRouter(prefix="/api/client", tags=["client-connect"])

STANDARD_PORTS = {"rdp": 3389, "vnc": 5900}
HOST_MODES = ("gateway", "local", "custom", "last")
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
        raise HTTPException(400, "Protokoll muss rdp oder vnc sein")
    return p


def _client(user: dict, client_id: str) -> dict:
    c = db.get_client(client_id) if hasattr(db, "get_client") else None
    if not c or not can_access_client(user, client_id):
        raise HTTPException(404, "Gerät nicht gefunden")
    return c


def _gateway_available() -> bool:
    return modules.is_enabled("network") and modules.feature("network", "portforward")


class Pref(BaseModel):
    protocol: str = "rdp"
    host_mode: str = "local"
    host: str = ""
    port_mode: str = "standard"
    port: int | None = None


def _clean(p: Pref) -> dict:
    hm, pm = p.host_mode, p.port_mode
    if hm not in HOST_MODES or hm == "last":
        raise HTTPException(400, "Ziel muss gateway, local oder custom sein")
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
    return {"host_mode": hm, "host": host, "port_mode": pm, "port": port}


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
            "last": e.get("last")}


@router.put("/targets/{client_id}")
def save_default(client_id: str, body: Pref, user: dict = Depends(get_current_user)):
    proto = _proto(body.protocol)
    _client(user, client_id)
    pref = _clean(body)
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
    std = STANDARD_PORTS[proto]
    if body.host_mode == "last":
        last = _entry(user["id"], body.client_id, proto).get("last")
        if not last:
            raise HTTPException(404, "Noch keine letzte Verbindung für dieses Gerät")
        pref = {k: last.get(k) for k in ("host_mode", "host", "port_mode", "port")}
        if pref["host_mode"] == "gateway":
            pref["host"] = ""
    else:
        pref = _clean(body)
    result = {"protocol": proto, "client_id": c["id"], "host_mode": pref["host_mode"], "port_mode": pref["port_mode"]}
    if pref["host_mode"] == "gateway":
        # Bestehende Logik des Moduls: Weiterleitung nur fuer die IP dieses Geraets
        from .client_routes import screen, ScreenBody
        fw = await screen(ScreenBody(client_id=c["id"], protocol=proto), request, user)
        result.update({"host": fw["host"], "port": fw["port"], "forward_id": fw["forward_id"],
                       "expires": fw.get("expires"), "allow_from": fw.get("allow_from")})
        last = {"host_mode": "gateway", "host": "", "port_mode": "gateway", "port": None}
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
                "port_mode": pref["port_mode"], "port": pref["port"]}
    last.update({"display": f"{result['host']}:{result['port']}" if pref["host_mode"] != "gateway" else "RMM-Gateway",
                 "at": int(time.time())})
    _store(user["id"], c["id"], proto, "last", last)
    if body.remember and body.host_mode != "last":
        _store(user["id"], c["id"], proto, "default", {k: pref[k] for k in ("host_mode", "host", "port_mode", "port")})
    db.add_audit_entry(user["username"], "client.connect", target=c["id"],
                       details=f"{proto} {pref['host_mode']} {result['host']}:{result['port']}")
    return result

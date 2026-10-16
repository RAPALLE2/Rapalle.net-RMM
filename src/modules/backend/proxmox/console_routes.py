# console_routes.py (Modul "proxmox") - Konsole von VMs/LXC
# ---------------------------------------------------------
# 1) Konsole IM DASHBOARD (noVNC, durch das RMM getunnelt)
#    POST /api/proxmox/{cid}/guests/{node}/{type}/{vmid}/console
#         -> Proxmox: POST .../vncproxy {websocket: 1}  => VNC-Port + VNC-Ticket
#         -> einmalige Sitzung (60 s) {sid, ws, password}
#    WS   /api/proxmox/{cid}/console/ws?sid=...   Dashboard-Sitzung im WebSocket-Protokoll
#         "rmmtoken.<JWT>" (NICHT in der Adresse - die landet im Server-Log)
#         -> Backend verbindet sich mit wss://<proxmox>/api2/json/nodes/../vncwebsocket
#            (Cookie PVEAuthCookie) und reicht die Daten in beide Richtungen durch.
#    Braucht die Anmeldung mit Benutzer + Passwort: Proxmox stellt Konsolen nur
#    fuer Benutzer-Sitzungen (Ticket) aus, nicht fuer API-Tokens.
#
# 2) Proxmox-WEBOBERFLAECHE im Browser
#    POST /api/proxmox/{cid}/guests/{node}/{type}/{vmid}/webui {via: direct|forward}
#         direct  -> Adresse der Weboberflaeche (nur bei "lokal"/"eigene Adresse"; dein Browser
#                    muss Proxmox erreichen)
#         forward -> Weiterleitung ueber den RMM-Server, erreichbar NUR von deiner IP (60 min)
#    In der Weboberflaeche meldet man sich mit seinem Proxmox-Konto an.
#
# Recht: pve_console (+ Gast sichtbar: pve_vms_all/pve_lxc_all oder Gast-Freigabe).

from __future__ import annotations

import asyncio
import ipaddress
import secrets
import ssl
import time
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel

from app import db, modules
from app.auth import decode_access_token, get_current_user

from . import pve_client, pve_store
from .proxmox_routes import _audit, _check_ids, _guest, _guest_visible, _need, _pve

router = APIRouter(prefix="/api/proxmox", tags=["proxmox-console"])

_sessions: dict[str, dict] = {}
SESSION_TTL = 60


def _purge():
    now = time.time()
    for k in [k for k, v in _sessions.items() if v["expires"] < now]:
        _sessions.pop(k, None)


# ------------------------------------------------------------------ Konsole anfordern
@router.post("/{cid}/guests/{node}/{gtype}/{vmid}/console")
def console_start(cid: str, node: str, gtype: str, vmid: int, user: dict = Depends(get_current_user)):
    cl = _guest(user, cid, node, gtype, vmid, "pve_console")
    _pve(cl.ticket)                                          # Passwort-Anmeldung noetig (409 bei Token)
    res = _pve(cl.call, "POST", f"/nodes/{node}/{gtype}/{int(vmid)}/vncproxy", {"websocket": 1})
    if not res or "port" not in res or "ticket" not in res:
        raise HTTPException(502, "Proxmox hat keinen Konsolen-Zugang geliefert")
    _purge()
    sid = secrets.token_urlsafe(24)
    _sessions[sid] = {"user_id": user["id"], "cid": cid, "node": node, "gtype": gtype, "vmid": int(vmid),
                      "port": int(res["port"]), "vncticket": res["ticket"], "expires": time.time() + SESSION_TTL}
    _audit(user, cid, "console", f"{gtype}/{vmid} auf {node}")
    return {"sid": sid, "ws": f"/api/proxmox/{cid}/console/ws?sid={sid}", "password": res["ticket"]}


# ------------------------------------------------------------------ WebSocket-Tunnel
def _ssl_ctx(cfg: dict) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if cfg.get("fingerprint") or not cfg.get("verify_tls"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _check_fingerprint(host: str, port: int, expected: str) -> None:
    """Vor dem Senden des Tickets: Zertifikat der Gegenstelle mit dem gespeicherten Abdruck vergleichen."""
    got = pve_client.fetch_fingerprint(host, port).replace(":", "").lower()
    if got != expected.replace(":", "").replace(" ", "").lower():
        raise pve_client.PveError(495, "Zertifikats-Fingerabdruck stimmt nicht überein")


@router.websocket("/{cid}/console/ws")
async def console_ws(websocket: WebSocket, cid: str, sid: str = ""):
    # Anmeldung: Dashboard-Sitzung + einmalige Konsolen-Sitzung (sid, 60 s, nur einmal nutzbar).
    # Browser-WebSockets koennen keine Header setzen -> die Sitzung kommt als Unterprotokoll
    # "rmmtoken.<JWT>" (wird nicht protokolliert, anders als die Adresse).
    offered = websocket.scope.get("subprotocols") or []
    token = next((p[len("rmmtoken."):] for p in offered if p.startswith("rmmtoken.")), "")
    try:
        payload = decode_access_token(token)
        user = db.get_user_by_id(payload.get("sub"))
    except Exception:
        user = None
    sess = _sessions.pop(sid, None)
    if not user or not sess or sess["user_id"] != user["id"] or sess["cid"] != cid or sess["expires"] < time.time():
        await websocket.close(code=4401)
        return
    try:
        _need(user, cid, "pve_console")
        if not _guest_visible(user, cid, sess["gtype"], sess["vmid"]):
            raise HTTPException(404, "Gast nicht freigegeben")
    except HTTPException:
        await websocket.close(code=4403)
        return
    proto = "binary" if "binary" in offered else None
    await websocket.accept(subprotocol=proto)
    loop = asyncio.get_running_loop()
    try:
        cl = pve_client.client_for(cid)
        base = await loop.run_in_executor(None, cl.base)                 # (Relay-Tunnel ggf. aufbauen)
        pve_ticket, _ = await loop.run_in_executor(None, cl.ticket)
        u = urllib.parse.urlparse(base)
        cfg = cl.cfg
        if cfg.get("fingerprint"):
            await loop.run_in_executor(None, _check_fingerprint, u.hostname, u.port or 8006, cfg["fingerprint"])
        path = (f"/api2/json/nodes/{sess['node']}/{sess['gtype']}/{sess['vmid']}/vncwebsocket"
                f"?port={sess['port']}&vncticket={urllib.parse.quote(sess['vncticket'], safe='')}")
        uri = f"wss://{u.netloc}{path}"
        from websockets.asyncio.client import connect
        async with connect(uri, ssl=_ssl_ctx(cfg), subprotocols=["binary"], max_size=None, open_timeout=15,
                           additional_headers={"Cookie": f"PVEAuthCookie={pve_ticket}"}) as pve:

            async def browser_to_pve():
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    data = msg.get("bytes") if msg.get("bytes") is not None else (msg.get("text") or "").encode()
                    await pve.send(data)

            async def pve_to_browser():
                async for data in pve:
                    if isinstance(data, str):
                        data = data.encode()
                    await websocket.send_bytes(data)

            tasks = [asyncio.create_task(browser_to_pve()), asyncio.create_task(pve_to_browser())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except Exception as e:      # noqa: BLE001 - Verbindung beenden, Grund ins Log
        print(f"[proxmox] Konsole {cid} {sess['gtype']}/{sess['vmid']}: {type(e).__name__}: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ------------------------------------------------------------------ Weboberflaeche
class WebUiBody(BaseModel):
    via: str = "direct"          # direct | forward


@router.post("/{cid}/guests/{node}/{gtype}/{vmid}/webui")
async def webui(cid: str, node: str, gtype: str, vmid: int, body: WebUiBody, request: Request,
                user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_console")
    _check_ids(node, gtype, vmid)
    if not _guest_visible(user, cid, gtype, vmid):
        raise HTTPException(404, "Gast nicht gefunden oder nicht freigegeben")
    cfg = pve_store.get(cid) or {}
    port = int(cfg.get("port") or 8006)
    query = urllib.parse.urlencode({"console": "kvm" if gtype == "qemu" else "lxc", "novnc": 1, "vmid": int(vmid),
                                    "node": node, "resize": "off"})
    if body.via == "direct":
        if cfg.get("addr_mode") == "relay":
            raise HTTPException(409, "Bei „RMM-Relay“ ist Proxmox für deinen Browser nicht direkt erreichbar – „über Weiterleitung“ wählen")
        host = cfg.get("host") if cfg.get("addr_mode") == "custom" else ((db.get_client(cid) or {}).get("ip") or "")
        if not host:
            raise HTTPException(409, "Keine Adresse bekannt")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        _audit(user, cid, "webui", f"direkt {gtype}/{vmid}")
        return {"url": f"https://{host}:{port}/?{query}", "via": "direct"}
    if body.via != "forward":
        raise HTTPException(400, "via muss direct oder forward sein")
    pf = modules.use("network", "portforward") if modules.is_enabled("network") else None
    if pf is None or not modules.feature("network", "portforward"):
        raise HTTPException(409, "Die Weiterleitung braucht das Modul „Netzwerk“ mit Port-Weiterleitung")
    ip = request.client.host if request.client else ""
    try:
        a = ipaddress.ip_address(ip)
        allow = f"{a}/{32 if a.version == 4 else 128}"
    except ValueError:
        raise HTTPException(400, "Deine IP-Adresse ist nicht bestimmbar")
    # Ziel aus Sicht des Agenten
    target = cfg.get("relay_host") if cfg.get("addr_mode") == "relay" else (
        cfg.get("host") if cfg.get("addr_mode") == "custom" else "127.0.0.1")
    try:
        rec = await pf.create(client_id=cid, target_port=port, username=user["username"], minutes=60,
                              target_host=target or "127.0.0.1", allow_from=allow, label="Proxmox-Weboberfläche")
    except ValueError as e:
        raise HTTPException(409, f"Agent nicht erreichbar: {e}")
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    host = request.url.hostname or ""
    _audit(user, cid, "webui", f"Weiterleitung {gtype}/{vmid} Port {rec['listen_port']} nur {allow}")
    return {"url": f"https://{host}:{rec['listen_port']}/?{query}", "via": "forward", "expires": rec.get("expires_at"),
            "note": "Die Weiterleitung ist 60 Minuten nur von deiner IP erreichbar. Proxmox zeigt sein Zertifikat "
                    "(selbst signiert) – im Browser einmal bestätigen und mit dem Proxmox-Konto anmelden."}

# vpn_routes.py (Modul "vpn") - REST-API fuer WireGuard-Tunnel
# -----------------------------------------------------------
# Rechte:
#   use_vpn            (global)  Tunnel anlegen/verwalten (eigene), selbst als Benutzer teilnehmen
#   vpn_unlimited      (global)  Tunnel/Teilnehmer ohne Ablaufzeit
#   c_vpn              (Client, auch im Standard-Client)  diesen Agent/diese Node in Tunnel holen
#                                bzw. diese Node als VPN-Host benutzen
#   c_vpn_unlimited    (Client)  diesen Agent ohne Ablaufzeit
#   Admins: alles; Backend als Host/Teilnehmer nur fuer Admins.
# Benutzer, die nur TEILNEHMER sind, sehen ihre Tunnel und laden ihre eigene Datei.
from __future__ import annotations

import io
import ipaddress
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from app import db, modules
from app.auth import get_current_user, is_super_admin, user_has_permission, can_access_client

from . import hosting, store, wgconf

router = APIRouter(prefix="/api/vpn", tags=["vpn"])


def _set(key, default):
    v = modules.setting("vpn", key, default)
    return default if v in (None, "") else v


def _uname(uid: str) -> str:
    u = db.get_user_by_id(uid) or {}
    return u.get("display_name") or u.get("username") or uid[:8]


def _cname(cid: str) -> str:
    return (db.get_client(cid) or {}).get("hostname") or cid[:8]


def _may_manage(user: dict, t: dict) -> bool:
    return is_super_admin(user) or (user_has_permission(user, "use_vpn") and t.get("created_by") == user["username"])


def _is_member(user: dict, t: dict) -> bool:
    return any(p["kind"] == "user" and p["ref"] == user["id"] for p in t["peers"].values())


def _tunnel_or_404(tid: str) -> dict:
    t = store.get(tid)
    if not t:
        raise HTTPException(404, "Tunnel nicht gefunden")
    return t


def _check_hours(user: dict, hours: float, agents: list[str]) -> int:
    """Ablaufzeitpunkt (0 = unbegrenzt) mit Rechtepruefung."""
    if not hours:
        if not user_has_permission(user, "vpn_unlimited"):
            raise HTTPException(403, "Tunnel ohne Ablaufzeit brauchen das Recht „VPN unbegrenzt“")
        for cid in agents:
            if not user_has_permission(user, "c_vpn_unlimited", cid):
                raise HTTPException(403, f"Kein Recht „VPN ohne Ablaufzeit“ für {_cname(cid)}")
        return 0
    mx = float(_set("max_hours", 168))
    if hours > mx and not user_has_permission(user, "vpn_unlimited"):
        raise HTTPException(403, f"Höchstens {mx:g} Stunden (mehr braucht „VPN unbegrenzt“)")
    return int(time.time() + hours * 3600)


def _view(user: dict, t: dict, full: bool) -> dict:
    out = {k: v for k, v in t.items() if k not in ("host_priv_enc", "peers")}
    out["host_name"] = "RMM-Server (Backend)" if t["host"] == "backend" else _cname(t["host"])
    out["iface"] = wgconf.iface(t)
    out["peer_iface"] = wgconf.peer_iface(t)
    out["can_manage"] = _may_manage(user, t)
    if "endpoint_warnings" not in t and t.get("state") != "expired":
        out["endpoint_warnings"] = endpoint_problems(t.get("endpoint", ""))      # aeltere Tunnel
    peers = []
    for p in t["peers"].values():
        if p["kind"] == "test":
            continue                                   # kurzlebige Endpunkt-Test-Teilnehmer nicht anzeigen
        if not full and not (p["kind"] == "user" and p["ref"] == user["id"]):
            continue
        st = hosting.peer_state.get(f"{t['id']}:{p['id']}") or {}
        peers.append({k: v for k, v in p.items() if k != "priv_enc"} |
                     {"join_ok": st.get("ok"), "join_error": st.get("error", ""),
                      "mine": p["kind"] == "user" and p["ref"] == user["id"]})
    out["peers"] = sorted(peers, key=lambda x: (x["kind"], x["name"].lower()))
    return out


# ------------------------------------------------------------------ Hosts
@router.get("/hosts")
async def hosts(user: dict = Depends(get_current_user)):
    """Wer kann VPN-Host sein: Backend (Admins) + Nodes mit aktivem Node-Modul "vpn"."""
    from app import node_manager
    out = []
    if is_super_admin(user):
        av = hosting.wgsys().available()
        out.append({"id": "backend", "name": "RMM-Server (Backend)", "available": av["ok"], "hint": av["hint"]})
    for n in node_manager.node_choices("vpn", user):
        if not user_has_permission(user, "c_vpn", n["id"]):
            continue
        c = db.get_client(n["id"]) or {}
        out.append({"id": n["id"], "name": n["name"], "available": n["active"], "installed": n["installed"],
                    "outdated": n.get("outdated"), "ip": c.get("ip") or "",
                    "hint": "" if n["active"] else ("Node-Modul „vpn“ installieren und aktivieren" if not n["installed"]
                                                    else "Node-Modul „vpn“ ist nicht aktiviert")})
    return {"hosts": out}


# ------------------------------------------------------------------ Tunnel
@router.get("/tunnels")
def list_tunnels(user: dict = Depends(get_current_user)):
    out = []
    for t in store.load()["tunnels"].values():
        if _may_manage(user, t) or (is_super_admin(user)):
            out.append(_view(user, t, True))
        elif _is_member(user, t):
            out.append(_view(user, t, False))
    return {"tunnels": sorted(out, key=lambda x: (x["state"] != "active", -x["created"])),
            "can_create": is_super_admin(user) or user_has_permission(user, "use_vpn"),
            "unlimited": user_has_permission(user, "vpn_unlimited"),
            "max_hours": float(_set("max_hours", 168))}


class LanBody(BaseModel):
    iface: str = ""
    cidr: str = ""
    pool_start: str = ""
    pool_end: str = ""


class MembersBody(BaseModel):
    users: list[str] = []
    agents: list[str] = []
    backend: bool = False
    hours: float | None = None          # nur fuer nachtraeglich hinzugefuegte: eigene Ablaufzeit


class TunnelBody(BaseModel):
    name: str
    host: str = "backend"
    endpoint: str = ""
    port: int = 0
    mode: str = "virtual"               # virtual | real
    ip_mode: str = "nat"                # own | nat  (nur real)
    subnet: str = ""
    lan: LanBody = LanBody()
    dns: str = ""
    routes: list[str] = []
    hours: float = 8
    members: MembersBody = MembersBody()
    auto: bool = True                   # Client-App richtet Benutzer-Teilnehmer selbst ein


def _default_endpoint(req: Request, host: str, port: int) -> str:
    """Oeffentliche Adresse des Hosts. Backend: VPN-Einstellung > Server-Adresse aus den
    Einstellungen (Domain, IP, volle URL) > Adresse, ueber die das Dashboard gerade laeuft."""
    if host == "backend":
        h = str(_set("public_host", "")).strip()
        if not h:
            h = (db.get_setting("server_domain", "") or "").strip() or (db.get_setting("server_host", "") or "").strip()
        if not h:
            h = urlparse((db.get_setting("server_url", "") or "").strip()).hostname or ""
        if not h:
            h = urlparse(str(req.base_url)).hostname or ""
    else:
        c = db.get_client(host) or {}
        h = (c.get("node_endpoint") or "").split(":")[0] if c.get("node_endpoint") else (c.get("ip") or "")
    return f"{h}:{port}" if h else ""


# Cloudflare leitet nur HTTP(S) weiter, KEIN UDP -> WireGuard kommt dort nie an
_CF_NETS = ["173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
            "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
            "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22"]


def endpoint_problems(endpoint: str) -> list[str]:
    """Haeufige Gruende, warum Teilnehmer den Host nie erreichen (Tunnel 'aktiv', aber nie verbunden)."""
    import socket
    host = endpoint.rsplit(":", 1)[0].strip("[]")
    out = []
    if not host or host in ("localhost",) or host.startswith("127."):
        return [f"Der Endpunkt „{endpoint}“ zeigt auf diesen Rechner selbst – von außen nicht erreichbar."]
    try:
        ips = {ai[4][0] for ai in socket.getaddrinfo(host, None, socket.AF_INET)}
    except OSError:
        return [f"„{host}“ lässt sich nicht auflösen."]
    for ip in ips:
        a = ipaddress.ip_address(ip)
        if any(a in ipaddress.ip_network(n) for n in _CF_NETS):
            out.append(f"„{host}“ läuft über den Cloudflare-Proxy ({ip}). Cloudflare leitet kein UDP weiter – "
                       f"als Endpunkt die echte öffentliche IP bzw. einen DNS-Namen ohne Proxy (graue Wolke) angeben.")
            break
        if a.is_private or a.is_loopback or ip.startswith("100.64."):
            out.append(f"„{host}“ ist eine interne Adresse ({ip}) – Teilnehmer außerhalb dieses Netzes erreichen sie nicht. "
                       f"Für Zugriff von außen die öffentliche IP/DynDNS angeben und den UDP-Port im Router weiterleiten.")
            break
    return out


def _new_peers(user: dict, t: dict, m: MembersBody, expires: int) -> list[dict]:
    peers = []
    exist = {(p["kind"], p["ref"]) for p in t["peers"].values()}

    def add(kind, ref, name):
        if (kind, ref) in exist:
            return
        priv, pub = wgconf.keypair()
        p = {"id": store.new_id(), "kind": kind, "ref": ref, "name": name, "pub": pub, "priv_enc": store.enc(priv),
             "ip": wgconf.next_ip(t), "added": int(time.time()), "added_by": user["username"],
             "expires": expires, "auto": bool(t.get("auto", True))}
        t["peers"][p["id"]] = p
        exist.add((kind, ref))
        peers.append(p)

    for uid in m.users:
        if not db.get_user_by_id(uid):
            raise HTTPException(404, "Benutzer nicht gefunden")
        add("user", uid, _uname(uid))
    for cid in m.agents:
        if cid == t["host"]:
            continue                                   # der Host ist schon drin
        if not db.get_client(cid):
            raise HTTPException(404, "Agent nicht gefunden")
        if not user_has_permission(user, "c_vpn", cid):
            raise HTTPException(403, f"Kein Recht „VPN-Tunnel ausstellen“ für {_cname(cid)}")
        add("agent", cid, _cname(cid))
    if m.backend and t["host"] != "backend":
        if not is_super_admin(user):
            raise HTTPException(403, "Das Backend dürfen nur Administratoren in einen Tunnel holen")
        add("backend", "backend", "RMM-Server (Backend)")
    return peers


@router.post("/tunnels")
async def create_tunnel(body: TunnelBody, req: Request, user: dict = Depends(get_current_user)):
    if not (is_super_admin(user) or user_has_permission(user, "use_vpn")):
        raise HTTPException(403, "Fehlendes Recht: use_vpn")
    if not body.name.strip():
        raise HTTPException(400, "Name fehlt")
    host = body.host or "backend"
    if host == "backend":
        if not is_super_admin(user):
            raise HTTPException(403, "Das Backend als VPN-Host dürfen nur Administratoren wählen")
    else:
        if not user_has_permission(user, "c_vpn", host):
            raise HTTPException(403, "Kein Recht „VPN-Tunnel ausstellen“ auf dieser Node")
        from app import node_manager
        try:
            node_manager.require_module(host, "vpn")
        except node_manager.NodeError as e:
            raise HTTPException(409, str(e))
    mode = body.mode if body.mode in ("virtual", "real") else "virtual"
    ip_mode = body.ip_mode if body.ip_mode in ("own", "nat") else "nat"
    expires = _check_hours(user, body.hours, body.members.agents)
    d = store.load()
    used_subnets = {t["subnet"] for t in d["tunnels"].values() if t.get("state") != "expired"}
    try:
        subnet = str(ipaddress.ip_network(body.subnet, strict=False)) if body.subnet.strip() \
            else wgconf.pick_subnet(str(_set("pool", "10.77.0.0/16")), used_subnets)
        lan = {}
        if mode == "real":
            if not body.lan.iface.strip() or not body.lan.cidr.strip():
                raise ValueError("Beim reellen Netz Schnittstelle (z.B. eth0) und Netz (z.B. 192.168.1.0/24) angeben")
            lan = {"iface": body.lan.iface.strip(), "cidr": str(ipaddress.ip_network(body.lan.cidr, strict=False))}
            if ip_mode == "own":
                a, b = ipaddress.ip_address(body.lan.pool_start), ipaddress.ip_address(body.lan.pool_end)
                net = ipaddress.ip_network(lan["cidr"])
                if a not in net or b not in net or int(b) < int(a):
                    raise ValueError("Der Adressbereich muss im reellen Netz liegen (Start ≤ Ende)")
                lan.update(pool_start=str(a), pool_end=str(b))
        for r in body.routes:
            ipaddress.ip_network(r, strict=False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    used_ports = {t["port"] for t in d["tunnels"].values() if t["host"] == host and t.get("state") != "expired"}
    port = body.port or next(p for p in range(int(_set("base_port", 51820)), 65535) if p not in used_ports)
    if port in used_ports:
        raise HTTPException(409, f"Port {port} ist auf diesem Host schon belegt")
    priv, pub = wgconf.keypair()
    t = {"id": store.new_id(), "name": body.name.strip()[:60], "host": host, "port": port,
         "endpoint": body.endpoint.strip() or _default_endpoint(req, host, port),
         "mode": mode, "ip_mode": ip_mode, "subnet": subnet, "lan": lan, "dns": body.dns.strip()[:100],
         "routes": [str(ipaddress.ip_network(r, strict=False)) for r in body.routes], "keepalive": 25,
         "host_pub": pub, "host_priv_enc": store.enc(priv), "created": int(time.time()),
         "created_by": user["username"], "expires": expires, "state": "pending", "error": "",
         "auto": body.auto, "peers": {}}
    if not t["endpoint"]:
        raise HTTPException(400, "Öffentliche Adresse (Endpunkt) des Hosts angeben, z.B. vpn.firma.de:51820")
    # Liegt der Endpunkt IM weitergeleiteten Netz, schickt der Teilnehmer seine eigenen
    # WireGuard-Pakete in den Tunnel -> Verbindung bricht. Dann eine oeffentliche Adresse noetig.
    ep_host = t["endpoint"].rsplit(":", 1)[0].strip("[]")
    try:
        ep_ip = ipaddress.ip_address(ep_host)
        for net in [n.strip() for n in wgconf.peer_allowed(t).split(",")]:
            if ep_ip in ipaddress.ip_network(net):
                raise HTTPException(400, f"Der Endpunkt {ep_host} liegt im Tunnel-Netz {net} – bitte die öffentliche "
                                         f"Adresse des Hosts angeben (z.B. vpn.firma.de:{port}).")
    except ValueError:
        pass                                             # Hostname -> kann hier nicht geprueft werden
    try:
        t["host_ip"] = wgconf.host_ip(t)
        joined = _new_peers(user, t, body.members, 0)
    except ValueError as e:
        raise HTTPException(400, str(e))
    t["endpoint_warnings"] = endpoint_problems(t["endpoint"])
    store.update(lambda dd: dd["tunnels"].__setitem__(t["id"], t))
    res = await hosting.apply(t["id"], joined=joined)
    db.add_audit_entry(user["username"], "vpn.tunnel_created",
                       details=f"{t['name']} auf {_view(user, t, True)['host_name']}, {len(joined)} Teilnehmer")
    return {"ok": res["ok"], "error": res.get("error", ""), "tunnel": _view(user, store.get(t["id"]), True)}


class EditBody(BaseModel):
    name: str | None = None
    hours: float | None = None          # neue Laufzeit AB JETZT (0 = unbegrenzt)
    endpoint: str | None = None
    dns: str | None = None
    auto: bool | None = None


@router.put("/tunnels/{tid}")
async def edit_tunnel(tid: str, body: EditBody, user: dict = Depends(get_current_user)):
    t = _tunnel_or_404(tid)
    if not _may_manage(user, t):
        raise HTTPException(403, "Nur Ersteller oder Administratoren")
    if t.get("state") == "expired":
        raise HTTPException(409, "Der Tunnel ist bereits beendet")
    expires = None if body.hours is None else _check_hours(
        user, body.hours, [p["ref"] for p in t["peers"].values() if p["kind"] == "agent"])

    def upd(d):
        x = d["tunnels"][tid]
        if body.name:
            x["name"] = body.name.strip()[:60]
        if expires is not None:
            x["expires"] = expires
        if body.endpoint:
            ep = body.endpoint.strip()
            if ":" not in ep:
                ep = f"{ep}:{x['port']}"
            x["endpoint"] = ep
            x["endpoint_warnings"] = endpoint_problems(ep)
        if body.dns is not None:
            x["dns"] = body.dns.strip()[:100]
        if body.auto is not None:
            x["auto"] = body.auto
    store.update(upd)
    if body.endpoint or body.dns is not None:          # Teilnehmer-Konfigurationen haben sich geaendert
        t = store.get(tid)
        for p in t["peers"].values():
            await hosting.peer_join(t, p)
    return {"ok": True, "tunnel": _view(user, store.get(tid), True)}


@router.post("/tunnels/{tid}/expire")
async def expire_tunnel(tid: str, user: dict = Depends(get_current_user)):
    t = _tunnel_or_404(tid)
    if not _may_manage(user, t):
        raise HTTPException(403, "Nur Ersteller oder Administratoren")
    await hosting.end_tunnel(tid, f"beendet von {user['username']}")
    return {"ok": True}


@router.delete("/tunnels/{tid}")
async def delete_tunnel(tid: str, user: dict = Depends(get_current_user)):
    t = _tunnel_or_404(tid)
    if not _may_manage(user, t):
        raise HTTPException(403, "Nur Ersteller oder Administratoren")
    if t.get("state") != "expired":
        await hosting.end_tunnel(tid, f"gelöscht von {user['username']}")
    store.update(lambda d: d["tunnels"].pop(tid, None))
    db.add_audit_entry(user["username"], "vpn.tunnel_deleted", details=t["name"])
    return {"ok": True}


@router.post("/tunnels/{tid}/retry")
async def retry_tunnel(tid: str, user: dict = Depends(get_current_user)):
    t = _tunnel_or_404(tid)
    if not _may_manage(user, t):
        raise HTTPException(403, "Nur Ersteller oder Administratoren")
    res = await hosting.apply(tid, joined=list(t["peers"].values()))
    return res


# ------------------------------------------------------------------ Teilnehmer
@router.post("/tunnels/{tid}/peers")
async def add_peers(tid: str, body: MembersBody, user: dict = Depends(get_current_user)):
    t = _tunnel_or_404(tid)
    if not _may_manage(user, t):
        raise HTTPException(403, "Nur Ersteller oder Administratoren")
    if t.get("state") == "expired":
        raise HTTPException(409, "Der Tunnel ist bereits beendet")
    exp = 0 if body.hours is None else _check_hours(user, body.hours, body.agents)
    joined = []

    def upd(d):
        x = d["tunnels"][tid]
        joined.extend(_new_peers(user, x, body, exp))
    try:
        store.update(upd)
    except ValueError as e:
        raise HTTPException(400, str(e))
    res = await hosting.apply(tid, joined=joined)
    db.add_audit_entry(user["username"], "vpn.peers_added", details=f"{t['name']}: {', '.join(p['name'] for p in joined)}")
    return {**res, "added": len(joined)}


@router.delete("/tunnels/{tid}/peers/{pid}")
async def remove_peer(tid: str, pid: str, user: dict = Depends(get_current_user)):
    t = _tunnel_or_404(tid)
    p = t["peers"].get(pid)
    if not p:
        raise HTTPException(404, "Teilnehmer nicht gefunden")
    if not (_may_manage(user, t) or (p["kind"] == "user" and p["ref"] == user["id"])):
        raise HTTPException(403, "Keine Berechtigung")
    store.update(lambda d: d["tunnels"][tid]["peers"].pop(pid, None))
    if t.get("state") != "expired":
        await hosting.apply(tid, left=[p])
    return {"ok": True}


@router.get("/tunnels/{tid}/status")
async def tunnel_status(tid: str, user: dict = Depends(get_current_user)):
    t = _tunnel_or_404(tid)
    if not (_may_manage(user, t) or _is_member(user, t)):
        raise HTTPException(403, "Keine Berechtigung")
    st = await hosting.host_status(t) if t.get("state") == "active" else {"up": False, "peers": {}}
    now = int(time.time())
    peers = {}
    for p in t["peers"].values():
        s = st.get("peers", {}).get(p["pub"]) or {}
        hs = s.get("handshake") or 0
        connected = bool(hs and now - hs < 180)
        rx = s.get("rx", 0)
        # Diagnose aus Sicht des Hosts
        if connected:
            hint = ""
        elif not st.get("up"):
            hint = "Host-Tunnel läuft nicht"
        elif not hs and p["kind"] == "user":
            # (WireGuard zaehlt fehlgeschlagene Handshakes keinem Teilnehmer zu - daher nur dieser Hinweis)
            hint = ("Noch nie verbunden: Tunnel in WireGuard aktiviert? Die App zeigt „aktiv“ auch ohne Verbindung. "
                    "Sonst Endpunkt/UDP-Port prüfen; nach einer Endpunkt-Änderung die Datei neu laden.")
        elif not hs:
            hint = "Noch nie verbunden – Endpunkt/UDP-Port prüfen bzw. Agent aktualisieren."
        else:
            hint = "Verbindung eingeschlafen (letzter Handshake älter als 3 min)."
        peers[p["id"]] = {"connected": connected, "handshake": hs, "hint": hint,
                          "endpoint": s.get("endpoint", ""), "rx": rx, "tx": s.get("tx", 0)}
    return {"up": st.get("up", False), "error": st.get("error", ""), "peers": peers}


@router.get("/tunnels/{tid}/peers/{pid}/config")
def peer_config(tid: str, pid: str, qr: bool = False, user: dict = Depends(get_current_user)):
    """WireGuard-Datei eines Teilnehmers (eigene immer; fremde nur fuer Verwalter)."""
    t = _tunnel_or_404(tid)
    p = t["peers"].get(pid)
    if not p:
        raise HTTPException(404, "Teilnehmer nicht gefunden")
    if not ((p["kind"] == "user" and p["ref"] == user["id"]) or _may_manage(user, t)):
        raise HTTPException(403, "Nur die eigene Konfiguration")
    if t.get("state") == "expired":
        raise HTTPException(410, "Der Tunnel ist beendet")
    conf = wgconf.peer_conf(t, p)
    db.add_audit_entry(user["username"], "vpn.config_downloaded", details=f"{t['name']} / {p['name']}")
    if qr:
        import qrcode
        buf = io.BytesIO()
        qrcode.make(conf).save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")
    fname = f"{wgconf.peer_iface(t)}.conf"
    return PlainTextResponse(conf, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/my")
def my_tunnels(user: dict = Depends(get_current_user)):
    """Fuer die Client-App: alle aktiven Tunnel, in denen ICH Teilnehmer bin (mit Datei).
    Die App legt fehlende Tunnel an, verbindet sie und loescht, was hier nicht mehr steht."""
    out = []
    now = time.time()
    for t in store.load()["tunnels"].values():
        if t.get("state") != "active":
            continue
        for p in t["peers"].values():
            if p["kind"] == "user" and p["ref"] == user["id"]:
                exp = min([x for x in (t.get("expires"), p.get("expires")) if x] or [0])
                if exp and exp <= now:
                    continue
                out.append({"tunnel": t["id"], "peer": p["id"], "name": t["name"], "iface": wgconf.peer_iface(t),
                            "expires": exp, "auto": bool(p.get("auto", True)), "conf": wgconf.peer_conf(t, p)})
    return {"tunnels": out, "server_time": int(now)}


@router.get("/candidates")
def candidates(user: dict = Depends(get_current_user)):
    """Auswahl fuer den Tunnel-Dialog: Benutzer + Agents (mit c_vpn) + Backend (Admins)."""
    if not (is_super_admin(user) or user_has_permission(user, "use_vpn")):
        raise HTTPException(403, "Fehlendes Recht: use_vpn")
    users = [{"id": u["id"], "name": u.get("display_name") or u["username"]} for u in db.list_users()]
    agents = [{"id": c["id"], "name": c.get("hostname") or c["id"][:8], "node": bool(c.get("is_node")),
               "platform": c.get("platform") or ""}
              for c in db.list_clients() if can_access_client(user, c["id"]) and user_has_permission(user, "c_vpn", c["id"])]
    return {"users": sorted(users, key=lambda x: x["name"].lower()),
            "agents": sorted(agents, key=lambda x: x["name"].lower()), "backend": is_super_admin(user)}


# ------------------------------------------------------------------ Endpunkt-Test
@router.get("/tunnels/{tid}/testers")
def testers(tid: str, user: dict = Depends(get_current_user)):
    """Wer kann den Endpunkt testen: Online-Agents/Nodes (mit c_vpn), Backend (Admins, wenn eine Node Host ist).
    Aussagekraeftig ist der Test nur von AUSSERHALB des Host-Netzes (sonst greift das Router-Hairpin)."""
    t = _tunnel_or_404(tid)
    if not _may_manage(user, t):
        raise HTTPException(403, "Nur Ersteller oder Administratoren")
    from app.sockets import state
    ep_ips = set()
    try:
        import socket
        ep_ips = {ai[4][0] for ai in socket.getaddrinfo(t["endpoint"].rsplit(":", 1)[0].strip("[]"), None, socket.AF_INET)}
    except OSError:
        pass
    host_ip = (db.get_client(t["host"]) or {}).get("ip") if t["host"] != "backend" else ""
    out = []
    if t["host"] != "backend" and is_super_admin(user):
        out.append({"id": "backend", "name": "RMM-Server (Backend)", "same_net": False})
    for c in db.list_clients():
        if c["id"] == t["host"] or not state.is_online(c["id"]):
            continue
        if not (can_access_client(user, c["id"]) and user_has_permission(user, "c_vpn", c["id"])):
            continue
        ip = c.get("ip") or ""
        out.append({"id": c["id"], "name": c.get("hostname") or c["id"][:8], "ip": ip,
                    "same_net": bool(ip and (ip in ep_ips or ip == host_ip))})
    return {"testers": sorted(out, key=lambda x: (x["same_net"], x["name"].lower())), "endpoint_ips": sorted(ep_ips)}


class TestBody(BaseModel):
    via: str


@router.post("/tunnels/{tid}/test")
async def test_endpoint(tid: str, body: TestBody, user: dict = Depends(get_current_user)):
    t = _tunnel_or_404(tid)
    if not _may_manage(user, t):
        raise HTTPException(403, "Nur Ersteller oder Administratoren")
    if body.via == "backend":
        if not is_super_admin(user) or t["host"] == "backend":
            raise HTTPException(403, "Das Backend kann nur testen, wenn eine Node Host ist (Admins)")
    elif not user_has_permission(user, "c_vpn", body.via):
        raise HTTPException(403, "Kein Recht „VPN-Tunnel ausstellen“ für diesen Tester")
    res = await hosting.endpoint_test(tid, body.via)
    res["endpoint_warnings"] = endpoint_problems(t["endpoint"])
    db.add_audit_entry(user["username"], "vpn.endpoint_test", details=f"{t['name']}: {'ok' if res.get('ok') else res.get('detail', '')[:120]}")
    return res


# ------------------------------------------------------------------ Werkzeuge / Einrichtungsskript
@router.post("/hosts/{host}/tools")
async def install_tools(host: str, user: dict = Depends(get_current_user)):
    """Fehlende Werkzeuge (wireguard-tools, iptables, iproute2, ggf. wireguard-go) auf dem Host nachladen."""
    import asyncio
    if host == "backend":
        if not is_super_admin(user):
            raise HTTPException(403, "Nur Administratoren")
        from .module import ensure_tools_now
        logs: list[str] = []
        res = await asyncio.get_running_loop().run_in_executor(None, ensure_tools_now, logs.append)
        return {"ok": all(res.values()), "tools": res, "log": logs, **hosting.wgsys().available()}
    if not user_has_permission(user, "c_vpn", host):
        raise HTTPException(403, "Kein Recht „VPN-Tunnel ausstellen“ auf dieser Node")
    from app import node_manager
    try:
        node_manager.require_module(host, "vpn")
        return await node_manager.command(host, "vpn-tools", {}, timeout=1000)
    except node_manager.NodeError as e:
        raise HTTPException(409, str(e))


@router.get("/setup-script")
def setup_script(user: dict = Depends(get_current_user)):
    """Einrichtungsskript fuer einen VPN-Host (Linux, Docker-Host, Proxmox-CT) zum Herunterladen."""
    from pathlib import Path
    sh = (Path(__file__).with_name("vpn-host-setup.sh")).read_text(encoding="utf-8")
    return PlainTextResponse(sh, media_type="text/x-shellscript",
                             headers={"Content-Disposition": 'attachment; filename="vpn-host-setup.sh"'})

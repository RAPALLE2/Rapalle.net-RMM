# remote.py (Modul "cloudflare") - Fernzugriff der Client-App ueber den Tunnel
# ----------------------------------------------------------------------------
# Die Client-App will RDP/VNC/SSH zu einem Agent oeffnen, aber kein Port ist erreichbar und
# es gibt (noch) kein VPN: Das Backend legt eine Route "Agent ueber RMM-Server" an
# (dauerhafte Weiterleitung RMM -> Agent im Modul network + Tunnel-Adresse), geschuetzt mit
# einem EIGENEN Service-Token je Route. Die Client-App verbindet mit
#   cloudflared access tcp --hostname <adresse> --url 127.0.0.1:<port>  (+ Service-Token)
# und startet das lokale Programm auf 127.0.0.1:<port>.
#
# Lebensdauer je Agent + Protokoll (wie beim Relay im Explorer):
#   "session"   bis Nutzungsende: weg, sobald keine Client-App mehr verbunden ist (Lease)
#   10|30|60|120 Minuten nach dem Oeffnen (verlaengert sich nicht; laufende Nutzung bleibt)
#   0           dauerhaft (Recht relay_unlimited bzw. c_relay_unlimited)
# Leases: jede verbundene Client-App meldet sich alle 60 s (ping); nach 3 Minuten ohne
# Meldung gilt die Nutzung als beendet.

from __future__ import annotations

import re
import secrets
import threading
import time

from app import db

from . import store, sync

LEASE_TIMEOUT = 180
SESSION_GRACE = 90           # "bis Nutzungsende": so lange nach der letzten Nutzung noch offen
_lock = threading.Lock()


def ready() -> dict:
    """Ist Fernzugriff ueber Cloudflare moeglich? -> {ok, reason, zone}"""
    data = store.load()
    if not (data.get("account") or {}).get("id"):
        return {"ok": False, "reason": "Cloudflare-Zugang ist nicht eingerichtet"}
    zone = (data.get("remote") or {}).get("zone") or (data.get("zones") or [{}])[0].get("name")
    if not zone:
        return {"ok": False, "reason": "Keine Zone für den Fernzugriff gewählt"}
    return {"ok": True, "reason": "", "zone": zone}


def _rid(client_id: str, proto: str) -> str:
    return "rx-" + re.sub(r"[^a-z0-9]", "", client_id.lower())[:12] + "-" + proto


def _hostname(client_id: str, proto: str, zone: str) -> str:
    short = re.sub(r"[^a-z0-9]", "", client_id.lower())[:10] or secrets.token_hex(4)
    return f"rmm-{proto}-{short}.{zone}"


def open_tunnel(user: dict, client_id: str, proto: str, port: int, target_host: str, lifetime) -> dict:
    """Route anlegen/wiederverwenden, Lease vergeben. -> {hostname, lease, token_id, token_secret, expires, lifetime}"""
    rd = ready()
    if not rd["ok"]:
        raise ValueError(rd["reason"])
    life = "session" if lifetime in (None, "", "session") else int(lifetime)
    c = db.get_client(client_id) or {}
    rid = _rid(client_id, proto)
    now = time.time()
    lease = secrets.token_urlsafe(18)
    with _lock:
        def upd(d):
            r = store.route(d, rid)
            ref = {"client_id": client_id, "proto": proto, "port": int(port)}
            if target_host and target_host not in ("127.0.0.1", "localhost"):
                ref.update(host_mode="custom", host=target_host)
            if not r:
                zone = (d.get("remote") or {}).get("zone") or rd["zone"]
                r = {"id": rid, "created": int(now), "created_by": user["username"], "state": {},
                     "name": f"Client-App: {c.get('hostname') or client_id[:8]} {proto.upper()}",
                     "kind": "agent", "endpoint": "server", "node_id": "", "mode": "client",
                     "hostname": _hostname(client_id, proto, zone), "access": "service", "allow": [], "emails": [],
                     "remote": {"lifetime": life, "expires": 0, "leases": {}, "last_used": now}}
                d["routes"].append(r)
            rm = r.setdefault("remote", {"leases": {}})
            if r.get("ref") != ref:
                r["ref"] = ref
            # Lebensdauer: die laengere gewinnt (dauerhaft > Minuten > Nutzungsende)
            cur = rm.get("lifetime", "session")
            rank = lambda v: 10**9 if v == 0 else (-1 if v == "session" else int(v))   # noqa: E731
            if rank(life) >= rank(cur) or rm.get("expires", 0) and rm["expires"] < now:
                rm["lifetime"] = life
                rm["expires"] = int(now + life * 60) if isinstance(life, int) and life > 0 else 0
            rm.setdefault("leases", {})[lease] = {"user": user["username"], "ping": now}
            rm["last_used"] = now
            return r
        store.update(upd)
        rep = sync.apply(user["username"])
    data = store.load()
    r = store.route(data, rid)
    err = (r.get("state") or {}).get("error")
    if err:
        raise ValueError(f"Tunnel konnte nicht eingerichtet werden: {err}")
    stk = r.get("service_token") or {}
    if not stk.get("id"):
        raise ValueError("Service-Token fehlt – " + "; ".join(rep.get("errors") or []))
    return {"hostname": r["hostname"], "lease": lease, "route_id": rid, "token_id": stk["client_id"],
            "token_secret": store.dec(stk["secret_enc"]), "lifetime": r["remote"]["lifetime"],
            "expires": r["remote"].get("expires") or 0, "service": r["state"].get("service")}


def _find_lease(data: dict, lease: str):
    for r in data["routes"]:
        if lease in ((r.get("remote") or {}).get("leases") or {}):
            return r
    return None


def ping(lease: str, user: dict) -> dict:
    def upd(d):
        r = _find_lease(d, lease)
        if not r:
            return None
        L = r["remote"]["leases"][lease]
        if L["user"] != user["username"]:
            return None
        L["ping"] = r["remote"]["last_used"] = time.time()
        return {"ok": True, "expires": r["remote"].get("expires") or 0}
    res = store.update(upd)
    if not res:
        raise LookupError("Verbindung nicht (mehr) bekannt")
    return res


def release(lease: str, user: dict) -> None:
    def upd(d):
        r = _find_lease(d, lease)
        if r and r["remote"]["leases"][lease]["user"] == user["username"]:
            r["remote"]["leases"].pop(lease, None)
            r["remote"]["last_used"] = time.time()
    store.update(upd)


def set_lifetime(rid: str, lifetime) -> dict:
    life = "session" if lifetime in (None, "", "session") else int(lifetime)

    def upd(d):
        r = store.route(d, rid)
        if not r or "remote" not in r:
            raise LookupError("Fernzugriff nicht gefunden")
        r["remote"]["lifetime"] = life
        r["remote"]["expires"] = int(time.time() + life * 60) if isinstance(life, int) and life > 0 else 0
        return r["remote"]
    return store.update(upd)


def due() -> list[str]:
    """Routen, deren Zeit abgelaufen ist (ohne laufende Nutzung)."""
    now = time.time()
    out = []

    def upd(d):
        for r in d["routes"]:
            rm = r.get("remote")
            if not rm:
                continue
            leases = rm.get("leases") or {}
            for k in [k for k, v in leases.items() if now - v.get("ping", 0) > LEASE_TIMEOUT]:
                leases.pop(k)
            active = bool(leases)
            life = rm.get("lifetime", "session")
            if active or life == 0:
                continue
            if life == "session" and now - rm.get("last_used", 0) > SESSION_GRACE:
                out.append(r["id"])
            elif isinstance(life, int) and life > 0 and rm.get("expires") and rm["expires"] < now:
                out.append(r["id"])
    store.update(upd)
    return out


def cleanup() -> int:
    ids = due()
    for rid in ids:
        sync.remove_route(rid)
    if ids:
        try:
            sync.apply("")
        except Exception:
            pass
    return len(ids)


def list_for(client_id: str) -> list[dict]:
    out = []
    for r in store.load()["routes"]:
        rm = r.get("remote")
        if rm and (r.get("ref") or {}).get("client_id") == client_id:
            out.append({"route_id": r["id"], "proto": r["ref"].get("proto"), "hostname": r["hostname"],
                        "lifetime": rm.get("lifetime"), "expires": rm.get("expires") or 0,
                        "active": len(rm.get("leases") or {}), "error": (r.get("state") or {}).get("error", "")})
    return out

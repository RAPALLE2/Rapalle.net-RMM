# hosting.py (Modul "vpn") - Konfiguration an Host und Teilnehmer bringen
# ----------------------------------------------------------------------
# Host:        "backend" -> WireGuard direkt auf dem RMM-Server (wgsys.py lokal)
#              Node-ID   -> Node-Modul "vpn" (Befehle vpn-up/-down/-status)
# Teilnehmer:  agent     -> Agent-Ereignis "wg-join"/"wg-leave" (Antwort "wg-result")
#              backend   -> lokal (wenn der Host eine Node ist)
#              user      -> laedt seine Datei selbst bzw. die Client-App richtet sie ein
# Alles wird hier gebuendelt erzeugt; Host/Agent wenden nur an und melden zurueck.
from __future__ import annotations

import asyncio
import importlib.util
import time

from app import db
from app.paths import MODULES_DIR

from . import store, wgconf

_wg = None
peer_state: dict[str, dict] = {}        # "<tid>:<pid>" -> {ok, error, ts}  (Rueckmeldung der Agents)


def wgsys():
    """Gemeinsame WireGuard-Systemfunktionen (modules/node/vpn/wgsys.py) fuer das Backend."""
    global _wg
    if _wg is None:
        spec = importlib.util.spec_from_file_location("rmm_backend_wgsys", MODULES_DIR / "node" / "vpn" / "wgsys.py")
        _wg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_wg)
    return _wg


def _node_call(node_id: str, op: str, data: dict, timeout: float = 120):
    from app import node_manager
    node_manager.require_module(node_id, "vpn")
    return node_manager.command(node_id, op, data, timeout=timeout)


async def host_up(t: dict) -> dict:
    conf = wgconf.host_conf(t)
    name = wgconf.iface(t)
    if t["host"] == "backend":
        return await asyncio.get_running_loop().run_in_executor(None, wgsys().up, name, conf)
    return await _node_call(t["host"], "vpn-up", {"name": name, "conf": conf})


async def host_down(t: dict) -> None:
    name = wgconf.iface(t)
    try:
        if t["host"] == "backend":
            await asyncio.get_running_loop().run_in_executor(None, wgsys().down, name)
        else:
            await _node_call(t["host"], "vpn-down", {"name": name}, timeout=60)
    except Exception as e:  # noqa: BLE001 - Host weg/offline: Tunnel trotzdem als beendet fuehren
        print(f"[vpn] Host-Abbau {name}: {e}")


async def host_status(t: dict) -> dict:
    name = wgconf.iface(t)
    try:
        if t["host"] == "backend":
            return await asyncio.get_running_loop().run_in_executor(None, wgsys().status, name)
        return await _node_call(t["host"], "vpn-status", {"name": name}, timeout=20)
    except Exception as e:  # noqa: BLE001
        return {"up": False, "peers": {}, "error": str(e)}


async def peer_join(t: dict, p: dict) -> None:
    """Agent/Backend in den Tunnel holen (Benutzer machen das selbst/ueber die Client-App)."""
    key = f"{t['id']}:{p['id']}"
    conf = wgconf.peer_conf(t, p)
    name = wgconf.peer_iface(t)
    if p["kind"] == "agent":
        from app.sockets import send_to_agent
        ok = await send_to_agent(p["ref"], "wg-join", {"tunnel": t["id"], "peer": p["id"], "name": name, "conf": conf})
        peer_state[key] = {"ok": None if ok else False, "error": "" if ok else "Agent offline – wird beim nächsten Verbinden nachgeholt",
                           "ts": int(time.time())}
    elif p["kind"] == "backend":
        try:
            await asyncio.get_running_loop().run_in_executor(None, wgsys().up, name, conf)
            peer_state[key] = {"ok": True, "error": "", "ts": int(time.time())}
        except Exception as e:  # noqa: BLE001
            peer_state[key] = {"ok": False, "error": str(e), "ts": int(time.time())}


async def peer_leave(t: dict, p: dict) -> None:
    name = wgconf.peer_iface(t)
    peer_state.pop(f"{t['id']}:{p['id']}", None)
    if p["kind"] == "agent":
        from app.sockets import send_to_agent
        await send_to_agent(p["ref"], "wg-leave", {"tunnel": t["id"], "name": name})
    elif p["kind"] == "backend":
        try:
            await asyncio.get_running_loop().run_in_executor(None, wgsys().down, name)
        except Exception:  # noqa: BLE001
            pass


async def apply(tid: str, joined: list[dict] | None = None, left: list[dict] | None = None) -> dict:
    """Host-Konfiguration neu anwenden, neue Teilnehmer holen, entfernte rauswerfen."""
    t = store.get(tid)
    if not t:
        raise KeyError(tid)
    for p in left or []:
        await peer_leave(t, p)
    warn = []
    try:
        res = await host_up(t)
        warn = list((res or {}).get("warnings") or []) if isinstance(res, dict) else []
        err = ""
    except Exception as e:  # noqa: BLE001
        err = str(e)

    def upd(d):
        x = d["tunnels"].get(tid)
        if x:
            x["state"], x["error"] = ("error", err) if err else ("active", "")
            x["warnings"] = [] if err else warn
    store.update(upd)
    if err:
        return {"ok": False, "error": err}
    for p in joined or []:
        await peer_join(t, p)
    return {"ok": True, "warnings": warn}


async def end_tunnel(tid: str, reason: str = "abgelaufen") -> None:
    t = store.get(tid)
    if not t:
        return
    for p in list(t["peers"].values()):
        await peer_leave(t, p)
    await host_down(t)

    def upd(d):
        x = d["tunnels"].get(tid)
        if x:
            x["state"], x["error"], x["ended"] = "expired", reason, int(time.time())
    store.update(upd)
    db.add_audit_entry("system", "vpn.tunnel_ended", details=f"{t['name']} ({reason})")


async def rejoin_agent(client_id: str) -> None:
    """Agent ist (wieder) online -> in alle aktiven Tunnel holen, in denen er Teilnehmer ist."""
    for t in store.load()["tunnels"].values():
        if t.get("state") != "active":
            continue
        for p in t["peers"].values():
            if p["kind"] == "agent" and p["ref"] == client_id:
                await peer_join(t, p)


async def expiry_loop():
    """Abgelaufene Tunnel beenden, abgelaufene Teilnehmer entfernen. Alle 30 s."""
    while True:
        await asyncio.sleep(30)
        now = time.time()
        d = store.load()
        for tid, t in list(d["tunnels"].items()):
            if t.get("state") == "expired":
                if now - t.get("ended", now) > 7 * 86400:          # beendete Tunnel nach 7 Tagen wegraeumen
                    store.update(lambda dd: dd["tunnels"].pop(tid, None))
                continue
            if t.get("expires") and now >= t["expires"]:
                await end_tunnel(tid)
                continue
            gone = [p for p in t["peers"].values() if p.get("expires") and now >= p["expires"]]
            if gone:
                def upd(dd, ids={p["id"] for p in gone}):
                    x = dd["tunnels"].get(tid)
                    for pid in ids:
                        x["peers"].pop(pid, None)
                store.update(upd)
                await apply(tid, left=gone)


# ---------------------------------------------------------------------------
# Endpunkt-Test: echter WireGuard-Handshake von einem Tester (Agent/Node oder Backend)
# ---------------------------------------------------------------------------
def _clean(out: str) -> str:
    """wg-quick-Ausgabe auf die eigentliche Fehlermeldung kuerzen (ohne die "[#] Befehl"-Zeilen)."""
    lines = [ln.strip() for ln in (out or "").replace("[#]", "\n[#]").splitlines()]
    msg = [ln for ln in lines if ln and not ln.startswith("[#]") and not ln.startswith(("│", "┌", "└"))]
    return " · ".join(msg[-3:]) or (out or "").strip()[-200:]


async def endpoint_test(tid: str, via: str, endpoint: str | None = None) -> dict:
    """Legt einen kurzlebigen Test-Teilnehmer an, laesst den Tester (Agent/Backend) sich
    damit verbinden und prueft am Host, ob ein Handshake ankommt. Danach wird alles wieder
    entfernt. Nur so laesst sich zuverlaessig pruefen, ob Endpunkt-IP UND UDP-Port von dort
    aus erreichbar sind (WireGuard antwortet auf fremde Pakete nicht)."""
    t = store.get(tid)
    if not t or t.get("state") != "active":
        return {"ok": False, "step": "host", "detail": "Der Tunnel läuft auf dem Host nicht (Zustand nicht „aktiv“)."}
    endpoint = endpoint or t["endpoint"]
    now = int(time.time())
    priv, pub = wgconf.keypair()
    pid = "test" + store.new_id()
    p = {"id": pid, "kind": "test", "ref": via, "name": "Endpunkt-Test", "pub": pub, "priv_enc": store.enc(priv),
         "ip": wgconf.next_ip(t), "added": now, "added_by": "test", "expires": now + 180, "auto": False}
    store.update(lambda d: d["tunnels"][tid]["peers"].__setitem__(pid, p))
    name = f"rmt{tid}"[:15]
    steps = []
    t = store.get(tid)
    try:
        try:
            await host_up(t)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "step": "host", "detail": f"Host konnte den Test-Teilnehmer nicht übernehmen: {e}"}
        st = await host_status(t)
        if not st.get("up"):
            return {"ok": False, "step": "host", "detail": f"WireGuard-Schnittstelle auf dem Host läuft nicht. {st.get('error', '')}"}
        steps.append(f"Host lauscht auf UDP {t['port']}")
        # Table = off: wg-quick legt KEINE Routen an. Fuer den Handshake braucht es keine (der Keepalive
        # loest ihn aus) - und so gibt es keinen Konflikt "RTNETLINK answers: File exists", wenn der Tester
        # schon eine Route ins VPN-Netz hat (anderer Tunnel, Reste eines frueheren Tests, gleiche Maschine).
        conf = "\n".join(["[Interface]", f"PrivateKey = {priv}", f"Address = {p['ip']}/32", "Table = off", "",
                          "[Peer]", f"PublicKey = {t['host_pub']}", f"Endpoint = {endpoint}",
                          f"AllowedIPs = {t['host_ip']}/32", "PersistentKeepalive = 3"]) + "\n"
        key = f"{tid}:{pid}"
        if via == "backend":
            try:
                await asyncio.get_running_loop().run_in_executor(None, wgsys().up, name, conf)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "step": "tester", "detail": f"Backend konnte den Test-Tunnel nicht starten: {_clean(str(e))}"}
        else:
            from app.sockets import send_to_agent
            if not await send_to_agent(via, "wg-join", {"tunnel": tid, "peer": pid, "name": name, "conf": conf}):
                return {"ok": False, "step": "tester", "detail": "Der Tester-Agent ist offline."}
        steps.append(f"Tester verbindet sich mit {endpoint}")
        t0 = time.time()
        while time.time() - t0 < 25:
            await asyncio.sleep(2)
            ps = peer_state.get(key) or {}
            if ps.get("ok") is False:
                return {"ok": False, "step": "tester", "steps": steps,
                        "detail": f"Tester konnte WireGuard nicht starten: {_clean(ps.get('error', ''))}"}
            st = await host_status(store.get(tid) or t)
            hs = (st.get("peers", {}).get(pub) or {}).get("handshake") or 0
            if hs:
                steps.append(f"Handshake nach {int(time.time() - t0)} s")
                return {"ok": True, "steps": steps, "seconds": int(time.time() - t0),
                        "detail": f"Endpunkt {endpoint} ist vom Tester aus per UDP erreichbar – WireGuard-Handshake erfolgreich."}
        if via != "backend" and peer_state.get(key, {}).get("ok") is None:
            return {"ok": False, "step": "tester", "steps": steps,
                    "detail": "Der Tester hat nicht geantwortet – Agent aktualisieren (kennt den VPN-Beitritt noch nicht)."}
        return {"ok": False, "step": "endpoint", "steps": steps,
                "detail": f"Kein Handshake in 25 s: Beim Host kommt über {endpoint} nichts an. Endpunkt-IP prüfen und "
                          f"UDP-Port {endpoint.rsplit(':', 1)[-1]} im Router/der Firewall zum Host weiterleiten "
                          f"(Docker: Port-Freigabe /udp, Container neu erstellen)."}
    finally:
        try:
            if via == "backend":
                await asyncio.get_running_loop().run_in_executor(None, wgsys().down, name)
            else:
                from app.sockets import send_to_agent
                await send_to_agent(via, "wg-leave", {"tunnel": tid, "name": name})
        except Exception:  # noqa: BLE001
            pass
        peer_state.pop(f"{tid}:{pid}", None)
        store.update(lambda d: d["tunnels"].get(tid, {}).get("peers", {}).pop(pid, None))
        x = store.get(tid)
        if x and x.get("state") == "active":
            try:
                await host_up(x)
            except Exception:  # noqa: BLE001
                pass

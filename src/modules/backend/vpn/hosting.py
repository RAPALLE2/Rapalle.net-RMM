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
    try:
        await host_up(t)
        err = ""
    except Exception as e:  # noqa: BLE001
        err = str(e)

    def upd(d):
        x = d["tunnels"].get(tid)
        if x:
            x["state"], x["error"] = ("error", err) if err else ("active", "")
    store.update(upd)
    if err:
        return {"ok": False, "error": err}
    for p in joined or []:
        await peer_join(t, p)
    return {"ok": True}


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

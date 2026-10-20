# match.py (Modul "proxmox") - Agenten mit Proxmox-Gaesten abgleichen
# --------------------------------------------------------------------
# Fuer jeden Agenten mit verbundenem Proxmox (= Host) werden alle VMs/LXC samt Konfiguration
# gelesen und mit den uebrigen Agenten verglichen:
#   1. MAC-Adresse  (net0..: "virtio=BC:24:11:..", LXC "hwaddr=BC:24:11:..")  -> eindeutig
#   2. Hostname     (LXC "hostname", sonst Gastname) == Agent-Hostname      -> nur wenn eindeutig
# Die MACs der Agenten stammen aus ihren Live-Messwerten (mac, interfaces[].mac) - abgeglichen
# werden also Agenten, die gerade online sind; das Ergebnis wird gespeichert.
#
# Ablage data/modules/proxmox/guest_map.json:
#   {"map": {client_id: {host, node, type, vmid, name}},          uebernommene Zuordnungen
#    "suggestions": {client_id: {host, node, type, vmid, name, reason, found}},
#    "ignored": {client_id: "<host>:<vmid>"},                     abgelehnt -> nicht erneut vorschlagen
#    "scanned": {host: ts}}

from __future__ import annotations

import asyncio
import json
import re
import threading
import time

from app import db
from app.paths import MODULES_DATA_DIR

from . import pve_client, pve_store
from .pve_client import PveError

FILE = MODULES_DATA_DIR / "proxmox" / "guest_map.json"
MAC_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")
_lock = threading.RLock()


def _load() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    for k in ("map", "suggestions", "ignored", "scanned"):
        d.setdefault(k, {})
    return d


def _save(d: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def data() -> dict:
    return _load()


def mapping(client_id: str) -> dict | None:
    return _load()["map"].get(client_id)


def _norm_mac(m: str) -> str:
    return (m or "").strip().lower().replace("-", ":")


def _agent_macs(client_id: str) -> set[str]:
    from app.sockets import state
    m = state.live_metrics.get(client_id) or {}
    out = {_norm_mac(m.get("mac") or "")}
    out |= {_norm_mac(i.get("mac") or "") for i in m.get("interfaces") or [] if isinstance(i, dict)}
    return {x for x in out if x and x not in ("00:00:00:00:00:00",)}


def _guest_info(cfg: dict, res: dict) -> dict:
    macs, ips = set(), set()
    for k, v in cfg.items():
        if re.fullmatch(r"net\d+", k) and isinstance(v, str):
            macs |= {_norm_mac(x) for x in MAC_RE.findall(v)}
            ips |= set(re.findall(r"ip=(\d+\.\d+\.\d+\.\d+)", v))
    host = (cfg.get("hostname") or res.get("name") or "").strip().lower()
    return {"macs": macs, "ips": ips, "hostname": host}


def _read_guests(host_cid: str) -> list[dict]:
    """Alle Gaeste eines Hosts mit MACs/Hostname (blockierend - im Executor aufrufen)."""
    cl = pve_client.client_for(host_cid)
    out = []
    for r in cl.get("/cluster/resources", type="vm") or []:
        if r.get("type") not in ("qemu", "lxc") or r.get("template"):
            continue
        try:
            cfg = cl.get(f"/nodes/{r['node']}/{r['type']}/{int(r['vmid'])}/config") or {}
        except PveError:
            cfg = {}
        out.append({"node": r["node"], "type": r["type"], "vmid": int(r["vmid"]), "name": r.get("name") or "",
                    **_guest_info(cfg, r)})
    return out


async def scan(host_cid: str) -> dict:
    """Einen Host abgleichen -> {"found": n, "new": n, "error": str|None}"""
    loop = asyncio.get_running_loop()
    try:
        guests = await loop.run_in_executor(None, _read_guests, host_cid)
    except PveError as e:
        return {"found": 0, "new": 0, "error": str(e)}
    by_mac = {}
    for g in guests:
        for m in g["macs"]:
            by_mac[m] = g
    names = {}
    for g in guests:
        if g["hostname"]:
            names.setdefault(g["hostname"], []).append(g)
    found, new = 0, 0
    with _lock:
        d = _load()
        for c in db.list_clients():
            cid = c["id"]
            if cid == host_cid:
                continue
            g, reason = None, ""
            for m in _agent_macs(cid):
                if m in by_mac:
                    g, reason = by_mac[m], "MAC-Adresse"
                    break
            if g is None:
                cands = names.get((c.get("hostname") or "").strip().lower()) or []
                if len(cands) == 1:
                    g, reason = cands[0], "Hostname"
            if g is None:
                continue
            found += 1
            entry = {"host": host_cid, "node": g["node"], "type": g["type"], "vmid": g["vmid"], "name": g["name"]}
            cur = d["map"].get(cid)
            if cur and all(cur.get(k) == entry[k] for k in ("host", "type", "vmid")):
                cur.update(entry)                                     # schon zugeordnet (Node/Name aktualisieren)
                continue
            dt = c.get("device_type") or "physical"
            if not cur and dt == ("vm" if g["type"] == "qemu" else "lxc") and c.get("parent_client_id") == host_cid:
                d["map"][cid] = entry                                 # von Hand schon passend eingeordnet
                continue
            if d["ignored"].get(cid) == f"{host_cid}:{g['vmid']}":
                continue
            if cid not in d["suggestions"]:
                new += 1
            d["suggestions"][cid] = {**entry, "reason": reason, "found": int(time.time())}
        d["scanned"][host_cid] = int(time.time())
        _save(d)
    return {"found": found, "new": new, "error": None}


async def scan_all() -> dict:
    out = {}
    for host in pve_store.connected_ids():
        out[host] = await scan(host)
    return out


def accept(client_id: str) -> dict:
    with _lock:
        d = _load()
        s = d["suggestions"].pop(client_id, None)
        if not s:
            raise KeyError(client_id)
        entry = {k: s[k] for k in ("host", "node", "type", "vmid", "name")}
        d["map"][client_id] = entry
        _save(d)
    db.update_client(client_id, {"device_type": "vm" if entry["type"] == "qemu" else "lxc",
                                 "parent_client_id": entry["host"], "device_type_ack": 1})
    return entry


def ignore(client_id: str) -> None:
    with _lock:
        d = _load()
        s = d["suggestions"].pop(client_id, None)
        if s:
            d["ignored"][client_id] = f"{s['host']}:{s['vmid']}"
            _save(d)


def unlink(client_id: str) -> None:
    """Zuordnung aufheben (Agent bleibt als VM/LXC markiert, bis man ihn umstellt)."""
    with _lock:
        d = _load()
        if d["map"].pop(client_id, None) is not None:
            _save(d)


def forget_host(host_cid: str) -> None:
    """Proxmox-Verbindung entfernt -> Zuordnungen/Vorschlaege dieses Hosts loeschen."""
    with _lock:
        d = _load()
        for k in ("map", "suggestions"):
            d[k] = {c: v for c, v in d[k].items() if v.get("host") != host_cid}
        d["scanned"].pop(host_cid, None)
        _save(d)


# ------------------------------------------------------------------ automatisch alle 10 Minuten
_task: asyncio.Task | None = None


async def _loop():
    await asyncio.sleep(60)                     # nach dem Start erst Agenten verbinden lassen
    while True:
        try:
            await scan_all()
        except Exception as e:      # noqa: BLE001
            print(f"[proxmox] Abgleich: {e}")
        await asyncio.sleep(600)


def begin() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


def end() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None

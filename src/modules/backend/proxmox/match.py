# match.py (Modul "proxmox") - Agenten mit Proxmox-Gaesten abgleichen
# --------------------------------------------------------------------
# Fuer jeden Agenten mit verbundenem Proxmox (= Host) werden alle VMs/LXC samt Konfiguration
# gelesen und mit den uebrigen Agenten verglichen:
#   1. MAC-Adresse  (net0..: "virtio=BC:24:11:..", LXC "hwaddr=BC:24:11:..")  -> eindeutig ("sicher")
#   2. IP-Adresse   (LXC ip=..; nur wenn genau EIN Gast diese IP hat)        -> eindeutig ("sicher")
#   3. Hostname     (LXC "hostname", sonst Gastname) == Agent-Hostname      -> nur wenn eindeutig (Vorschlag)
# Methoden, automatischer Abgleich, Intervall und "sichere Treffer sofort zuordnen" stehen in den
# Modul-Einstellungen (auto_match, match_mac, match_ip, match_hostname, auto_assign, scan_minutes).
#
# HEAD (uebergeordneter Agent): Ein Proxmox-Cluster hat mehrere Nodes (pve1, pve2, ...), oft hat aber nur
# EIN Agent die Proxmox-Verbindung. Ein Gast wird deshalb nicht stur dem verbundenen Agenten untergeordnet,
# sondern dem HEAD seines Proxmox-Nodes: heads[<verbundener Agent>][<Node-Name>] = <Agent-ID>.
# Ohne Festlegung wird geraten: Agent, dessen Hostname dem Node-Namen entspricht, sonst der verbundene Agent.
# Wird der Head eines Nodes festgelegt (im Vorschlagsdialog je Gast oder je Node), gilt er fuer ALLE Gaeste
# dieses Nodes - auch fuer bereits zugeordnete.
# Die MACs der Agenten stammen aus ihren Live-Messwerten (mac, interfaces[].mac) - abgeglichen
# werden also Agenten, die gerade online sind; das Ergebnis wird gespeichert.
#
# Ablage data/modules/proxmox/guest_map.json:
#   {"map": {client_id: {host, node, type, vmid, name}},          uebernommene Zuordnungen
#    "suggestions": {client_id: {host, node, type, vmid, name, reason, found}},
#    "ignored": {client_id: "<host>:<vmid>"},                     abgelehnt -> nicht erneut vorschlagen
#    "heads": {host: {node: client_id}},                          festgelegter Head je Proxmox-Node
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
    for k in ("map", "suggestions", "ignored", "scanned", "heads"):
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


def _cfg() -> dict:
    """Modul-Einstellungen mit Standardwerten."""
    from app import modules

    def g(k, d):
        v = modules.setting("proxmox", k, d)
        return d if v is None else v
    try:
        minutes = int(g("scan_minutes", 10))
    except (TypeError, ValueError):
        minutes = 10
    return {"auto_match": bool(g("auto_match", True)), "match_mac": bool(g("match_mac", True)),
            "match_ip": bool(g("match_ip", True)), "match_hostname": bool(g("match_hostname", True)),
            "auto_assign": bool(g("auto_assign", False)), "scan_minutes": max(2, min(1440, minutes))}


def _agent_ips(client: dict) -> set[str]:
    from app.sockets import state
    m = state.live_metrics.get(client["id"]) or {}
    ips = {str(client.get("ip") or "")}
    ips |= {str(i.get("ipv4") or "") for i in m.get("interfaces") or [] if isinstance(i, dict)}
    return {i for i in ips if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", i) and not i.startswith(("127.", "0.", "169.254."))}


def _short(h: str) -> str:
    return (h or "").strip().lower().split(".")[0]


def guess_head(d: dict, host_cid: str, node: str, clients: list[dict] | None = None) -> tuple[str, str]:
    """Head fuer einen Proxmox-Node -> (client_id, quelle 'set'|'hostname'|'host')."""
    h = (d.get("heads") or {}).get(host_cid, {}).get(node)
    if h and db.get_client(h):
        return h, "set"
    want = _short(node)
    host = db.get_client(host_cid) or {}
    if want and _short(host.get("hostname")) == want:
        return host_cid, "hostname"
    for c in clients if clients is not None else db.list_clients():
        if (c.get("device_type") or "physical") == "physical" and _short(c.get("hostname")) == want:
            return c["id"], "hostname"
    return host_cid, "host"


def _set_parent(client_id: str, entry: dict, head: str) -> None:
    db.update_client(client_id, {"device_type": "vm" if entry["type"] == "qemu" else "lxc",
                                 "parent_client_id": head, "device_type_ack": 1})


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


async def scan(host_cid: str, cfg: dict | None = None) -> dict:
    """Einen Host abgleichen -> {"found": n, "new": n, "assigned": n, "error": str|None}"""
    cfg = cfg or _cfg()
    loop = asyncio.get_running_loop()
    try:
        guests = await loop.run_in_executor(None, _read_guests, host_cid)
    except PveError as e:
        return {"found": 0, "new": 0, "assigned": 0, "error": str(e)}
    by_mac, ip_count, by_ip, names = {}, {}, {}, {}
    for g in guests:
        for m in g["macs"]:
            by_mac[m] = g
        for ip in g["ips"]:
            ip_count[ip] = ip_count.get(ip, 0) + 1
            by_ip[ip] = g
        if g["hostname"]:
            names.setdefault(g["hostname"], []).append(g)
    found, new, assigned = 0, 0, 0
    clients = db.list_clients()
    with _lock:
        d = _load()
        heads_of_host = set((d.get("heads") or {}).get(host_cid, {}).values())
        for c in clients:
            cid = c["id"]
            if cid == host_cid or cid in heads_of_host:
                continue
            g, reason, sure = None, "", False
            if cfg["match_mac"]:
                for m in _agent_macs(cid):
                    if m in by_mac:
                        g, reason, sure = by_mac[m], "MAC-Adresse", True
                        break
            if g is None and cfg["match_ip"]:
                for ip in _agent_ips(c):
                    if ip_count.get(ip) == 1:
                        g, reason, sure = by_ip[ip], "IP-Adresse", True
                        break
            if g is None and cfg["match_hostname"]:
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
            head, src = guess_head(d, host_cid, g["node"], clients)
            dt = c.get("device_type") or "physical"
            if not cur and dt == ("vm" if g["type"] == "qemu" else "lxc") and c.get("parent_client_id") in (host_cid, head):
                d["map"][cid] = entry                                 # von Hand schon passend eingeordnet
                continue
            if d["ignored"].get(cid) == f"{host_cid}:{g['vmid']}":
                continue
            if sure and cfg["auto_assign"]:
                d["map"][cid] = entry                                 # sicherer Treffer -> sofort zuordnen
                d["suggestions"].pop(cid, None)
                _set_parent(cid, entry, head)
                assigned += 1
                continue
            if cid not in d["suggestions"]:
                new += 1
            d["suggestions"][cid] = {**entry, "reason": reason, "found": int(time.time())}
        d["scanned"][host_cid] = int(time.time())
        _save(d)
    return {"found": found, "new": new, "assigned": assigned, "error": None}


async def scan_all(force: bool = False) -> dict:
    cfg = _cfg()
    if not force and not cfg["auto_match"]:
        return {}
    out = {}
    for host in pve_store.connected_ids():
        out[host] = await scan(host, cfg)
    return out


def suggestions_with_heads() -> dict:
    """Vorschlaege inkl. aktuellem Head (geraten oder festgelegt) -> {cid: {..., head, head_source}}"""
    d = _load()
    clients = db.list_clients()
    out = {}
    for cid, s in d["suggestions"].items():
        head, src = guess_head(d, s["host"], s["node"], clients)
        out[cid] = {**s, "head": head, "head_source": src}
    return out


def set_head(host_cid: str, node: str, head: str) -> int:
    """Head eines Proxmox-Nodes festlegen. Bereits zugeordnete Gaeste dieses Nodes werden umgehaengt.
    Rueckgabe: Anzahl umgehaengter Agenten."""
    if not db.get_client(head):
        raise KeyError(head)
    moved = []
    with _lock:
        d = _load()
        d["heads"].setdefault(host_cid, {})[node] = head
        for cid, m in d["map"].items():
            if m.get("host") == host_cid and m.get("node") == node and cid != head:
                moved.append((cid, m))
        d["suggestions"].pop(head, None)
        _save(d)
    for cid, m in moved:
        c = db.get_client(cid)
        if c and c.get("parent_client_id") != head:
            _set_parent(cid, m, head)
    return len(moved)


def accept(client_id: str, head: str | None = None, remember: bool = True) -> dict:
    """Vorschlag uebernehmen. head = uebergeordneter Agent (Standard: Head des Proxmox-Nodes).
    remember: gewaehlten Head fuer den ganzen Node merken (gilt dann fuer alle Gaeste dieses Nodes)."""
    with _lock:
        d = _load()
        s = d["suggestions"].get(client_id)
        if not s:
            raise KeyError(client_id)
        if head and (head == client_id or not db.get_client(head)):
            raise ValueError("Ungültiger Head")
        auto_head, _src = guess_head(d, s["host"], s["node"])
        head = head or auto_head
        d["suggestions"].pop(client_id, None)
        entry = {k: s[k] for k in ("host", "node", "type", "vmid", "name")}
        d["map"][client_id] = entry
        if remember and head != auto_head:
            d["heads"].setdefault(s["host"], {})[s["node"]] = head
        _save(d)
    _set_parent(client_id, entry, head)
    return {**entry, "head": head}


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
        d["heads"].pop(host_cid, None)
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
        await asyncio.sleep(_cfg()["scan_minutes"] * 60)


_soon: asyncio.Task | None = None


def scan_soon(delay: float = 45.0) -> None:
    """Neuer/umgezogener Agent ist da -> bald abgleichen (entprellt), damit die Frage zum
    Einsortieren nicht erst nach dem naechsten Intervall kommt."""
    global _soon
    if _soon and not _soon.done():
        return

    async def _run():
        await asyncio.sleep(delay)              # Live-Messwerte (MAC/IP) abwarten
        try:
            await scan_all()
        except Exception as e:  # noqa: BLE001
            print(f"[proxmox] Abgleich (neuer Agent): {e}")
    try:
        _soon = asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        pass


def begin() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


def end() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None

# proxmox_routes.py (Modul "proxmox") - Proxmox-Steuerung pro Agent
# -------------------------------------------------------------------
# Alle Routen gelten fuer EINEN Agenten ({cid}) mit eingerichteter Proxmox-Verbindung.
# Rechte (Client-Rechte, auch ueber "Standard-Proxmox" pve:* vergebbar):
#   pve_access   Dashboard/Uebersicht        pve_vms_all / pve_lxc_all  alle VMs / LXCs
#   pve_power    starten/stoppen/...          pve_console  Konsole
#   pve_snapshot Snapshots                    pve_backup   Backups
#   pve_config   Konfiguration aendern        pve_storage  Storage
#   pve_users    Proxmox-Benutzer/ACL         pve_nodes    Nodes/Aufgaben
#   pve_api      beliebige API-Aufrufe        pve_manage   Verbindung + Gast-Freigaben
# "Bestimmte VMs/LXCs": Gast-Freigaben pro Benutzer/Gruppe (GET/PUT /{cid}/acl).
#
#   GET    /api/proxmox/clients                         Agenten mit Proxmox (fuer mich sichtbar)
#   GET    /api/proxmox/{cid}/connection   PUT  DELETE  Verbindung (pve_manage)
#   POST   /api/proxmox/{cid}/test                      Verbindung testen (pve_manage)
#   POST   /api/proxmox/{cid}/fingerprint               Zertifikats-Fingerabdruck holen (pve_manage)
#   GET    /api/proxmox/{cid}/overview                  Version, Nodes, Zaehler
#   GET    /api/proxmox/{cid}/guests?type=qemu|lxc      Gaeste (gefiltert nach Rechten/Freigaben)
#   GET    /api/proxmox/{cid}/guests/{node}/{type}/{vmid}           Status + Konfiguration
#   POST   .../action/{start|stop|shutdown|reboot|suspend|resume|reset}
#   PUT    .../config                                   Konfiguration aendern
#   GET POST .../snapshots   POST .../snapshots/{name}/rollback   DELETE .../snapshots/{name}
#   GET POST .../backups                                vzdump-Backups
#   GET    /api/proxmox/{cid}/storage   .../storage/{node}/{storage}/content
#   GET    /api/proxmox/{cid}/users     .../nodes     .../tasks     .../tasks/{node}/{upid}
#   GET PUT /api/proxmox/{cid}/acl                      Gast-Freigaben
#   *      /api/proxmox/{cid}/api/{pfad}                API-Explorer (pve_api)

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db
from app.auth import can_access_client, get_current_user, is_super_admin, require_perm, user_has_permission

from . import pve_client, pve_store
from .pve_client import PveError

router = APIRouter(prefix="/api/proxmox", tags=["proxmox"])

_NODE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STORE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SNAP = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
ACTIONS = {"start", "stop", "shutdown", "reboot", "suspend", "resume", "reset"}


# ------------------------------------------------------------------ Hilfen
def _pve(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except PveError as e:
        # 401/403 von PROXMOX nicht durchreichen: das Dashboard wuerde 401 als "RMM-Sitzung
        # abgelaufen" (Abmeldung!) und 403 als fehlendes RMM-Recht deuten.
        if e.status == 401:
            raise HTTPException(502, f"Proxmox lehnt die Anmeldung ab – Benutzer/Token/Passwort prüfen ({e})")
        if e.status == 403:
            raise HTTPException(502, f"Dem Proxmox-Benutzer fehlen Rechte in Proxmox ({e})")
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))


def _need(user, cid: str, perm: str) -> None:
    if not pve_store.is_connected(cid):
        raise HTTPException(404, "Für diesen Agenten ist kein Proxmox verbunden")
    if not can_access_client(user, cid):
        raise HTTPException(404, "Gerät nicht gefunden")
    require_perm(user, perm, cid)


def _check_ids(node: str, gtype: str, vmid: int) -> None:
    if not _NODE.match(node) or gtype not in ("qemu", "lxc") or not (100 <= int(vmid) <= 999999999):
        raise HTTPException(400, "Ungültige Gast-Angabe")


def _groups(user) -> set[str]:
    try:
        return set(db.get_user_group_ids(user["id"]))
    except Exception:
        return set()


def _guest_visible(user, cid: str, gtype: str, vmid: int, acl_ids: set[int] | None = None) -> bool:
    if is_super_admin(user):
        return True
    if user_has_permission(user, "pve_vms_all" if gtype == "qemu" else "pve_lxc_all", cid):
        return True
    if acl_ids is None:
        acl_ids = pve_store.allowed_vmids(cid, user["id"], _groups(user))
    return int(vmid) in acl_ids


def _guest(user, cid: str, node: str, gtype: str, vmid: int, perm: str = "pve_access") -> pve_client.PveClient:
    _need(user, cid, perm)
    _check_ids(node, gtype, vmid)
    if not _guest_visible(user, cid, gtype, vmid):
        raise HTTPException(404, "Gast nicht gefunden oder nicht freigegeben")
    return _pve(pve_client.client_for, cid)


def _gpath(node, gtype, vmid) -> str:
    return f"/nodes/{node}/{gtype}/{int(vmid)}"


def _audit(user, cid, action, detail=""):
    db.add_audit_entry(user["username"], f"proxmox.{action}", target=cid, details=detail[:300])


# ------------------------------------------------------------------ Agenten
@router.get("/clients")
def pve_clients(user: dict = Depends(get_current_user)):
    out = []
    for cid in pve_store.connected_ids():
        if not can_access_client(user, cid) or not user_has_permission(user, "pve_access", cid):
            continue
        c = db.get_client(cid) or {}
        cfg = pve_store.get(cid) or {}
        out.append({"client_id": cid, "hostname": c.get("hostname"), "label": cfg.get("label") or "",
                    "addr_mode": cfg.get("addr_mode"), "manage": user_has_permission(user, "pve_manage", cid)})
    return {"clients": sorted(out, key=lambda x: (x["hostname"] or "").lower())}


# ------------------------------------------------------------------ Verbindung
class ConnBody(BaseModel):
    addr_mode: str = "local"          # local | custom | relay
    host: str = ""
    relay_host: str = "127.0.0.1"
    port: int = 8006
    auth: str = "token"               # token | password
    user: str = ""                    # z.B. rmm@pve
    token_id: str = ""                # z.B. rmm
    secret: str = ""                  # leer = gespeichertes behalten
    password: str = ""                # leer = gespeichertes behalten
    verify_tls: bool = False
    fingerprint: str = ""
    label: str = ""


def _conn_check(user, cid):
    if not can_access_client(user, cid):
        raise HTTPException(404, "Gerät nicht gefunden")
    require_perm(user, "pve_manage", cid)


@router.get("/{cid}/connection")
def get_connection(cid: str, user: dict = Depends(get_current_user)):
    _conn_check(user, cid)
    c = db.get_client(cid) or {}
    return {"connection": pve_store.get(cid), "agent_ip": c.get("ip") or "", "hostname": c.get("hostname")}


@router.put("/{cid}/connection")
def put_connection(cid: str, body: ConnBody, user: dict = Depends(get_current_user)):
    _conn_check(user, cid)
    if body.addr_mode not in ("local", "custom", "relay") or body.auth not in ("token", "password"):
        raise HTTPException(400, "Ungültige Adress- oder Anmeldeart")
    if not 1 <= body.port <= 65535:
        raise HTTPException(400, "Ungültiger Port")
    if body.addr_mode == "custom" and not re.match(r"^[A-Za-z0-9.\-:\[\]]{1,253}$", body.host.strip()):
        raise HTTPException(400, "Bitte eine gültige Adresse eingeben")
    if not re.match(r"^[^@\s]+@[A-Za-z0-9._-]+$", body.user.strip()):
        raise HTTPException(400, "Benutzer bitte als name@realm angeben (z. B. rmm@pve)")
    old = pve_store.get(cid) or {}
    if body.auth == "token":
        if not re.match(r"^[A-Za-z][A-Za-z0-9._-]{1,63}$", body.token_id.strip()):
            raise HTTPException(400, "Token-ID ungültig (nur der Name hinter dem !, z. B. rmm)")
        if not body.secret and not old.get("has_secret"):
            raise HTTPException(400, "Token-Secret fehlt")
    elif not body.password and not old.get("has_password"):
        raise HTTPException(400, "Passwort fehlt")
    cfg = body.model_dump(exclude={"secret", "password"})
    cfg["host"], cfg["user"], cfg["token_id"] = body.host.strip(), body.user.strip(), body.token_id.strip()
    saved = pve_store.save(cid, cfg, body.secret or None, body.password or None)
    pve_client.forget(cid)
    _audit(user, cid, "connection_saved", f"{body.addr_mode} {body.auth} {body.user}")
    return {"connection": saved}


@router.delete("/{cid}/connection")
def delete_connection(cid: str, user: dict = Depends(get_current_user)):
    _conn_check(user, cid)
    pve_store.remove(cid)
    pve_client.forget(cid)
    _audit(user, cid, "connection_removed")
    return {"ok": True}


@router.post("/{cid}/test")
def test_connection(cid: str, user: dict = Depends(get_current_user)):
    _conn_check(user, cid)
    if not pve_store.is_connected(cid):
        raise HTTPException(404, "Bitte zuerst die Verbindung speichern")
    return _pve(pve_client.test, cid)


@router.post("/{cid}/fingerprint")
def fingerprint(cid: str, user: dict = Depends(get_current_user)):
    """Fingerabdruck des Proxmox-Zertifikats ueber die eingestellte Adresse holen."""
    _conn_check(user, cid)
    cl = _pve(pve_client.client_for, cid, fresh=True)
    from urllib.parse import urlparse
    u = urlparse(_pve(cl.base))
    try:
        return {"fingerprint": pve_client.fetch_fingerprint(u.hostname, u.port or 8006)}
    except OSError as e:
        raise HTTPException(502, f"Zertifikat nicht abrufbar: {e}")


# ------------------------------------------------------------------ Uebersicht & Gaeste
def _resources(cl):
    return _pve(cl.get, "/cluster/resources") or []


@router.get("/{cid}/overview")
def overview(cid: str, user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_access")
    cl = _pve(pve_client.client_for, cid)
    res = _resources(cl)
    acl_ids = pve_store.allowed_vmids(cid, user["id"], _groups(user))
    guests = [r for r in res if r.get("type") in ("qemu", "lxc") and _guest_visible(user, cid, r["type"], r.get("vmid", 0), acl_ids)]
    nodes = [{k: r.get(k) for k in ("node", "status", "cpu", "maxcpu", "mem", "maxmem", "disk", "maxdisk", "uptime")}
             for r in res if r.get("type") == "node"]
    storage = []
    if user_has_permission(user, "pve_storage", cid):
        storage = [{k: r.get(k) for k in ("storage", "node", "status", "disk", "maxdisk", "plugintype", "content", "shared")}
                   for r in res if r.get("type") == "storage"]
    ver = _pve(cl.get, "/version") or {}
    cnt = lambda t, st=None: sum(1 for g in guests if g["type"] == t and (st is None or g.get("status") == st))   # noqa: E731
    return {"version": ver.get("version"), "nodes": nodes, "storage": storage,
            "counts": {"vms": cnt("qemu"), "vms_running": cnt("qemu", "running"),
                       "lxc": cnt("lxc"), "lxc_running": cnt("lxc", "running")},
            "perms": {p: user_has_permission(user, p, cid) for p in
                      ("pve_power", "pve_console", "pve_snapshot", "pve_backup", "pve_config", "pve_storage",
                       "pve_users", "pve_nodes", "pve_api", "pve_manage", "pve_vms_all", "pve_lxc_all")}}


@router.get("/{cid}/guests")
def guests(cid: str, type: str = "", user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_access")
    cl = _pve(pve_client.client_for, cid)
    acl_ids = pve_store.allowed_vmids(cid, user["id"], _groups(user))
    out = []
    for r in _resources(cl):
        t = r.get("type")
        if t not in ("qemu", "lxc") or (type and t != type):
            continue
        if not _guest_visible(user, cid, t, r.get("vmid", 0), acl_ids):
            continue
        out.append({k: r.get(k) for k in ("vmid", "name", "type", "node", "status", "cpu", "maxcpu", "mem", "maxmem",
                                          "disk", "maxdisk", "uptime", "template", "tags", "lock", "netin", "netout")})
    return {"guests": sorted(out, key=lambda g: g.get("vmid") or 0)}


@router.get("/{cid}/guests/{node}/{gtype}/{vmid}")
def guest_detail(cid: str, node: str, gtype: str, vmid: int, user: dict = Depends(get_current_user)):
    cl = _guest(user, cid, node, gtype, vmid)
    p = _gpath(node, gtype, vmid)
    status = _pve(cl.get, p + "/status/current") or {}
    config = _pve(cl.get, p + "/config") or {}
    return {"status": status, "config": config}


@router.post("/{cid}/guests/{node}/{gtype}/{vmid}/action/{action}")
def guest_action(cid: str, node: str, gtype: str, vmid: int, action: str, user: dict = Depends(get_current_user)):
    if action not in ACTIONS or (action == "reset" and gtype != "qemu"):
        raise HTTPException(400, "Unbekannte Aktion")
    cl = _guest(user, cid, node, gtype, vmid, "pve_power")
    upid = _pve(cl.post, _gpath(node, gtype, vmid) + f"/status/{action}")
    _audit(user, cid, f"guest.{action}", f"{gtype}/{vmid} auf {node}")
    return {"ok": True, "upid": upid}


class ConfigBody(BaseModel):
    params: dict
    delete: list[str] = []


@router.put("/{cid}/guests/{node}/{gtype}/{vmid}/config")
def guest_config(cid: str, node: str, gtype: str, vmid: int, body: ConfigBody, user: dict = Depends(get_current_user)):
    cl = _guest(user, cid, node, gtype, vmid, "pve_config")
    params = {k: v for k, v in body.params.items() if re.match(r"^[a-z][a-z0-9_]*$", str(k))}
    if body.delete:
        params["delete"] = ",".join(d for d in body.delete if re.match(r"^[a-z][a-z0-9_]*$", d))
    res = _pve(cl.call, "PUT", _gpath(node, gtype, vmid) + "/config", params)
    _audit(user, cid, "guest.config", f"{gtype}/{vmid}: {', '.join(params)}")
    return {"ok": True, "result": res}


# --- Snapshots ---------------------------------------------------------
class SnapBody(BaseModel):
    name: str
    description: str = ""
    vmstate: bool = False


@router.get("/{cid}/guests/{node}/{gtype}/{vmid}/snapshots")
def snapshots(cid: str, node: str, gtype: str, vmid: int, user: dict = Depends(get_current_user)):
    cl = _guest(user, cid, node, gtype, vmid, "pve_snapshot")
    return {"snapshots": _pve(cl.get, _gpath(node, gtype, vmid) + "/snapshot") or []}


@router.post("/{cid}/guests/{node}/{gtype}/{vmid}/snapshots")
def snapshot_create(cid: str, node: str, gtype: str, vmid: int, body: SnapBody, user: dict = Depends(get_current_user)):
    if not _SNAP.match(body.name):
        raise HTTPException(400, "Snapshot-Name: Buchstabe am Anfang, danach Buchstaben/Ziffern/_/- (max. 40)")
    cl = _guest(user, cid, node, gtype, vmid, "pve_snapshot")
    params = {"snapname": body.name, "description": body.description}
    if gtype == "qemu" and body.vmstate:
        params["vmstate"] = 1
    upid = _pve(cl.call, "POST", _gpath(node, gtype, vmid) + "/snapshot", params)
    _audit(user, cid, "snapshot.create", f"{gtype}/{vmid} {body.name}")
    return {"ok": True, "upid": upid}


@router.post("/{cid}/guests/{node}/{gtype}/{vmid}/snapshots/{name}/rollback")
def snapshot_rollback(cid: str, node: str, gtype: str, vmid: int, name: str, user: dict = Depends(get_current_user)):
    if not _SNAP.match(name):
        raise HTTPException(400, "Ungültiger Snapshot-Name")
    cl = _guest(user, cid, node, gtype, vmid, "pve_snapshot")
    upid = _pve(cl.post, _gpath(node, gtype, vmid) + f"/snapshot/{name}/rollback")
    _audit(user, cid, "snapshot.rollback", f"{gtype}/{vmid} {name}")
    return {"ok": True, "upid": upid}


@router.delete("/{cid}/guests/{node}/{gtype}/{vmid}/snapshots/{name}")
def snapshot_delete(cid: str, node: str, gtype: str, vmid: int, name: str, user: dict = Depends(get_current_user)):
    if not _SNAP.match(name):
        raise HTTPException(400, "Ungültiger Snapshot-Name")
    cl = _guest(user, cid, node, gtype, vmid, "pve_snapshot")
    upid = _pve(cl.call, "DELETE", _gpath(node, gtype, vmid) + f"/snapshot/{name}")
    _audit(user, cid, "snapshot.delete", f"{gtype}/{vmid} {name}")
    return {"ok": True, "upid": upid}


# --- Backups -------------------------------------------------------------
class BackupBody(BaseModel):
    storage: str
    mode: str = "snapshot"            # snapshot | suspend | stop
    compress: str = "zstd"            # zstd | lzo | gzip | 0
    notes: str = ""


@router.get("/{cid}/guests/{node}/{gtype}/{vmid}/backups")
def backups(cid: str, node: str, gtype: str, vmid: int, user: dict = Depends(get_current_user)):
    cl = _guest(user, cid, node, gtype, vmid, "pve_backup")
    stores = [s for s in (_pve(cl.get, f"/nodes/{node}/storage", content="backup") or []) if s.get("active", 1)]
    out = []
    for s in stores:
        try:
            for item in cl.get(f"/nodes/{node}/storage/{s['storage']}/content", content="backup", vmid=int(vmid)) or []:
                out.append({**item, "storage": s["storage"]})
        except PveError:
            continue
    return {"backups": sorted(out, key=lambda b: b.get("ctime") or 0, reverse=True),
            "storages": [s["storage"] for s in stores]}


@router.post("/{cid}/guests/{node}/{gtype}/{vmid}/backups")
def backup_create(cid: str, node: str, gtype: str, vmid: int, body: BackupBody, user: dict = Depends(get_current_user)):
    if not _STORE.match(body.storage) or body.mode not in ("snapshot", "suspend", "stop") \
            or body.compress not in ("zstd", "lzo", "gzip", "0"):
        raise HTTPException(400, "Ungültige Backup-Angaben")
    cl = _guest(user, cid, node, gtype, vmid, "pve_backup")
    params = {"vmid": int(vmid), "storage": body.storage, "mode": body.mode, "compress": body.compress}
    if body.notes:
        params["notes-template"] = body.notes[:200]
    upid = _pve(cl.call, "POST", f"/nodes/{node}/vzdump", params)
    _audit(user, cid, "backup.create", f"{gtype}/{vmid} -> {body.storage}")
    return {"ok": True, "upid": upid}


# ------------------------------------------------------------------ Storage, Benutzer, Nodes, Aufgaben
@router.get("/{cid}/storage")
def storage(cid: str, user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_storage")
    cl = _pve(pve_client.client_for, cid)
    return {"storage": [r for r in _resources(cl) if r.get("type") == "storage"], "config": _pve(cl.get, "/storage") or []}


@router.get("/{cid}/storage/{node}/{store}/content")
def storage_content(cid: str, node: str, store: str, content: str = "", user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_storage")
    if not _NODE.match(node) or not _STORE.match(store):
        raise HTTPException(400, "Ungültige Angabe")
    cl = _pve(pve_client.client_for, cid)
    return {"content": _pve(cl.get, f"/nodes/{node}/storage/{store}/content", content=content or None) or []}


@router.get("/{cid}/users")
def users(cid: str, user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_users")
    cl = _pve(pve_client.client_for, cid)
    return {"users": _pve(cl.get, "/access/users") or [], "groups": _pve(cl.get, "/access/groups") or [],
            "roles": _pve(cl.get, "/access/roles") or [], "acl": _pve(cl.get, "/access/acl") or []}


@router.get("/{cid}/nodes")
def nodes(cid: str, user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_nodes")
    cl = _pve(pve_client.client_for, cid)
    out = []
    for n in _pve(cl.get, "/nodes") or []:
        try:
            st = cl.get(f"/nodes/{n['node']}/status") or {}
        except PveError:
            st = {}
        out.append({**n, "detail": {k: st.get(k) for k in ("pveversion", "kversion", "loadavg", "cpuinfo", "memory", "rootfs", "uptime")}})
    return {"nodes": out}


@router.get("/{cid}/tasks")
def tasks(cid: str, limit: int = 50, user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_nodes")
    cl = _pve(pve_client.client_for, cid)
    return {"tasks": (_pve(cl.get, "/cluster/tasks") or [])[:max(1, min(limit, 500))]}


@router.get("/{cid}/tasks/{node}/{upid:path}")
def task_status(cid: str, node: str, upid: str, user: dict = Depends(get_current_user)):
    """Status + Log einer Aufgabe (z.B. nach Start/Backup) - mit pve_access sichtbar."""
    _need(user, cid, "pve_access")
    if not _NODE.match(node) or not upid.startswith("UPID:"):
        raise HTTPException(400, "Ungültige Aufgabe")
    cl = _pve(pve_client.client_for, cid)
    from urllib.parse import quote
    u = quote(upid, safe="")
    return {"status": _pve(cl.get, f"/nodes/{node}/tasks/{u}/status") or {},
            "log": _pve(cl.get, f"/nodes/{node}/tasks/{u}/log", limit=200) or []}


# ------------------------------------------------------------------ Gast-Freigaben
class AclBody(BaseModel):
    subject: str            # user:<id> | group:<id>
    vmids: list[int] = []


@router.get("/{cid}/acl")
def get_acl(cid: str, user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_manage")
    users_ = {u["id"]: u["username"] for u in db.list_users()}
    groups_ = {g["id"]: g["name"] for g in db.list_groups()}
    return {"acl": pve_store.acl(cid), "users": users_, "groups": groups_}


@router.put("/{cid}/acl")
def put_acl(cid: str, body: AclBody, user: dict = Depends(get_current_user)):
    _need(user, cid, "pve_manage")
    kind, _, sid = body.subject.partition(":")
    if kind not in ("user", "group") or not sid:
        raise HTTPException(400, "subject muss user:<id> oder group:<id> sein")
    pve_store.set_acl(cid, body.subject, body.vmids)
    _audit(user, cid, "acl", f"{body.subject}: {body.vmids}")
    return {"ok": True, "acl": pve_store.acl(cid)}


# ------------------------------------------------------------------ API-Explorer
@router.api_route("/{cid}/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def api_explorer(cid: str, path: str, request: Request, user: dict = Depends(get_current_user)):
    """Beliebiger Proxmox-API-Aufruf (/api2/json/<pfad>) - Recht pve_api."""
    _need(user, cid, "pve_api")
    if ".." in path.split("/"):
        raise HTTPException(400, "Ungültiger Pfad")
    params = dict(request.query_params)
    if request.method in ("POST", "PUT"):
        try:
            body = await request.json()
            if isinstance(body, dict):
                params.update(body)
        except Exception:
            pass
    cl = _pve(pve_client.client_for, cid)
    import asyncio
    data = await asyncio.get_running_loop().run_in_executor(None, lambda: _pve(cl.call, request.method, "/" + path, params))
    if request.method != "GET":
        _audit(user, cid, "api", f"{request.method} /{path}")
    return {"data": data}

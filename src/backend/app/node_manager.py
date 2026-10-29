"""
node_manager.py - Node-Verwaltung (Core)
========================================

Eine NODE ist ein eigener kleiner Prozess auf einem Client (src/node/main.py),
der UNABHAENGIG vom Agenten laeuft:

    Backend --(Befehl)--> Agent --(laedt & startet)--> Node-Prozess
    Backend <--(eigene Socket.IO-Verbindung /node)---- Node-Prozess

  * Der Agent installiert/entfernt/ueberwacht die Node nur (Dienst, Prozess).
  * Die Node prueft Docker (oder meldet, dass es installiert werden muss),
    nimmt Node-MODULE entgegen (docker-compose-Dateien u.a. aus
    src/modules/node/<name>/) und fuehrt sie aus.
  * Eigenes Token je Node (gespeichert wird nur der Hash).

Module (src/modules/...) benutzen diese Datei ueber ctx.nodes (siehe modules.py).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import secrets
import time
import uuid
import zipfile

import socketio
from collections import deque
from pathlib import Path
from typing import Any

from app import db, modules
from app.paths import NODE_DIR
from app.sockets import sio, state, send_to_agent

NS = "/node"
LOG_LINES = 300
PACKAGE_EXCLUDE = {"__pycache__", "data", "node.json", ".hash"}

# --- Laufzeit ----------------------------------------------------------------
_sid_by_client: dict[str, str] = {}
_client_by_sid: dict[str, str] = {}
_pending: dict[str, asyncio.Future] = {}
_logs: dict[str, deque] = {}


class NodeError(Exception):
    """Fehler, der dem Benutzer angezeigt werden darf."""


# =========================================================================
# Token
# =========================================================================

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


# =========================================================================
# Node-Paket (src/node/ -> ZIP, wird vom Agenten geholt)
# =========================================================================

def _package_files() -> list[Path]:
    out = []
    if not NODE_DIR.is_dir():
        return out
    for p in sorted(NODE_DIR.rglob("*")):
        rel = p.relative_to(NODE_DIR)
        if p.is_file() and not (set(rel.parts) & PACKAGE_EXCLUDE):
            out.append(p)
    return out


def package_hash() -> str:
    h = hashlib.sha256()
    for p in _package_files():
        h.update(p.relative_to(NODE_DIR).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def package_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _package_files():
            z.write(p, p.relative_to(NODE_DIR).as_posix())
    return buf.getvalue()


def node_version() -> str:
    """Version aus src/node/main.py (__version__ = "x.y.z")."""
    try:
        for line in (NODE_DIR / "main.py").read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return "0"


# =========================================================================
# Zustand fuer die Oberflaeche
# =========================================================================

def _public(rec: dict | None, client_id: str | None = None) -> dict:
    cid = client_id or (rec or {}).get("client_id")
    online = cid in _sid_by_client
    if not rec:
        return {"client_id": cid, "enabled": False, "state": "off", "online": False,
                "version": None, "docker": {}, "modules": {}, "error": None, "last_seen": 0,
                "update_available": False}
    info = rec.get("info") or {}
    return {
        "client_id": cid,
        "enabled": rec["state"] != "removing",
        "state": rec["state"],
        "online": online,
        "version": rec.get("version"),
        "docker": info.get("docker") or {},
        "os": info.get("os"),
        "hostname": info.get("hostname"),
        "modules": rec.get("modules") or {},
        "error": rec.get("error"),
        "last_seen": rec.get("last_seen") or 0,
        "update_available": bool(rec.get("version")) and rec.get("version") != node_version(),
    }


async def get_state(client_id: str) -> dict:
    return _public(await db.call(db.node_get, client_id), client_id)


async def list_states() -> list[dict]:
    return [_public(r) for r in await db.call(db.node_list)]


async def _broadcast(client_id: str) -> None:
    try:
        await sio.emit("node:update", await get_state(client_id), namespace="/dashboard")
    except Exception:
        pass


def logs(client_id: str) -> list[str]:
    return list(_logs.get(client_id, []))


def _add_log(client_id: str, line: str) -> None:
    _logs.setdefault(client_id, deque(maxlen=LOG_LINES)).append(line)


# =========================================================================
# Aktivieren / Deaktivieren (ueber den Agenten)
# =========================================================================

async def enable(client_id: str) -> dict:
    client = await db.call(db.get_client, client_id)
    if not client:
        raise NodeError("Unbekannter Client")
    if client_id not in state.client_id_to_sid:
        raise NodeError("Der Agent dieses Clients ist offline - die Node kann nur bei verbundenem Agenten installiert werden.")
    token = _new_token()
    await db.call(db.node_create, client_id, _hash(token))
    _add_log(client_id, f"{_ts()} Node wird installiert ...")
    ok = await send_to_agent(client_id, "node-install", {
        "path": "/api/nodes/package", "token": token, "client_id": client_id,
        "hash": package_hash(), "version": node_version(),
    })
    if not ok:
        await db.call(db.node_update, client_id, state="error", error="Agent nicht erreichbar")
        raise NodeError("Agent nicht erreichbar")
    await _broadcast(client_id)
    return await get_state(client_id)


async def disable(client_id: str) -> dict:
    rec = await db.call(db.node_get, client_id)
    if not rec:
        return await get_state(client_id)
    # Token sofort entwerten, Verbindung trennen, Agent raeumt auf.
    await db.call(db.node_update, client_id, state="removing", error=None,
                  token_hash=_hash(secrets.token_urlsafe(16)))
    sid = _sid_by_client.get(client_id)
    if sid:
        try:
            await sio.disconnect(sid, namespace=NS)
        except Exception:
            pass
    if not await send_to_agent(client_id, "node-remove", {"client_id": client_id}):
        _add_log(client_id, f"{_ts()} Agent offline - die Node wird entfernt, sobald er wieder verbunden ist.")
    await _broadcast(client_id)
    return await get_state(client_id)


async def on_agent_online(client_id: str) -> None:
    """Wird aufgerufen, wenn der Agent eines Clients sich (neu) verbindet."""
    rec = await db.call(db.node_get, client_id)
    if not rec:
        return
    if rec["state"] == "removing":
        await send_to_agent(client_id, "node-remove", {"client_id": client_id})
    elif client_id not in _sid_by_client:
        # Soll laufen, tut es aber nicht (oder ist veraltet): Agent stellt das sicher.
        await send_to_agent(client_id, "node-ensure", {
            "path": "/api/nodes/package", "hash": package_hash(), "version": node_version()})


@sio.on("node-agent-result", namespace="/agent")
async def _agent_result(sid, payload):
    """Antwort des Agenten auf node-install / node-remove / node-ensure."""
    cid = state.sid_to_client_id.get(sid)
    if not cid or not isinstance(payload, dict):
        return
    op, ok, err = payload.get("op"), bool(payload.get("ok")), str(payload.get("error") or "")[:300]
    _add_log(cid, f"{_ts()} Agent: {op} -> {'ok' if ok else 'FEHLER: ' + err}")
    if op == "remove" and ok:
        await db.call(db.node_delete, cid)
        _sid_by_client.pop(cid, None)
        await modules.emit("node_removed", client_id=cid)
    elif not ok:
        await db.call(db.node_update, cid, state="error", error=err or "Fehlgeschlagen")
    await _broadcast(cid)


# =========================================================================
# Verbindung der Node selbst (/node)
# =========================================================================

@sio.on("connect", namespace=NS)
async def _node_connect(sid, environ, auth):
    auth = auth or {}
    cid, token = str(auth.get("client_id") or ""), str(auth.get("token") or "")
    rec = await db.call(db.node_get, cid) if cid else None
    if (not rec or rec["state"] == "removing" or not token
            or not hmac.compare_digest(rec["token_hash"], _hash(token))):
        raise socketio.exceptions.ConnectionRefusedError("unauthorized")
    old = _sid_by_client.get(cid)
    if old and old != sid:                       # alte Verbindung ablösen
        try:
            await sio.disconnect(old, namespace=NS)
        except Exception:
            pass
    _sid_by_client[cid] = sid
    _client_by_sid[sid] = cid
    await db.call(db.node_update, cid, state="installed", error=None, last_seen=int(time.time() * 1000))
    _add_log(cid, f"{_ts()} Node verbunden")
    await _broadcast(cid)
    await modules.emit("node_online", client_id=cid)


@sio.on("disconnect", namespace=NS)
async def _node_disconnect(sid):
    cid = _client_by_sid.pop(sid, None)
    if not cid:
        return
    if _sid_by_client.get(cid) == sid:
        _sid_by_client.pop(cid, None)
        _caps.pop(cid, None)
        _add_log(cid, f"{_ts()} Node getrennt")
        await _broadcast(cid)
        await modules.emit("node_offline", client_id=cid)


async def _apply_report(cid: str, payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    _caps[cid] = {"version": str(payload.get("version") or _caps.get(cid, {}).get("version") or ""),
                  "modules": {k: (v or {}).get("state") for k, v in (payload.get("modules") or {}).items()}
                  if isinstance(payload.get("modules"), dict) else _caps.get(cid, {}).get("modules", {}),
                  "ops": set(payload.get("ops") or [])}
    fields: dict[str, Any] = {"last_seen": int(time.time() * 1000)}
    if payload.get("version"):
        fields["version"] = str(payload["version"])[:40]
    info = {"os": payload.get("os"), "hostname": payload.get("hostname"),
            "docker": payload.get("docker") or {}, "python": payload.get("python")}
    fields["info"] = info
    if isinstance(payload.get("modules"), dict):
        fields["modules"] = payload["modules"]
    await db.call(db.node_update, cid, **fields)
    await _broadcast(cid)


@sio.on("node-hello", namespace=NS)
async def _node_hello(sid, payload):
    cid = _client_by_sid.get(sid)
    if cid:
        await _apply_report(cid, payload)


@sio.on("node-status", namespace=NS)
async def _node_status(sid, payload):
    cid = _client_by_sid.get(sid)
    if cid:
        await _apply_report(cid, payload)


@sio.on("node-log", namespace=NS)
async def _node_log(sid, payload):
    cid = _client_by_sid.get(sid)
    if cid and isinstance(payload, dict):
        for line in str(payload.get("text") or "").splitlines()[-50:]:
            _add_log(cid, line[:400])


@sio.on("node-result", namespace=NS)
async def _node_result(sid, payload):
    if not isinstance(payload, dict):
        return
    fut = _pending.pop(str(payload.get("requestId") or ""), None)
    if fut and not fut.done():
        fut.set_result(payload)


# =========================================================================
# Befehle an die Node
# =========================================================================

async def command(client_id: str, op: str, data: dict | None = None, timeout: float = 60) -> Any:
    """Fuehrt einen Befehl auf der Node aus und wartet auf die Antwort."""
    sid = _sid_by_client.get(client_id)
    if not sid:
        raise NodeError("Die Node ist offline.")
    rid = uuid.uuid4().hex
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending[rid] = fut
    await sio.emit("node-cmd", {"requestId": rid, "op": op, "data": data or {}}, to=sid, namespace=NS)
    try:
        res = await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        _pending.pop(rid, None)
        raise NodeError(f"Zeitueberschreitung bei '{op}'")
    if not res.get("ok"):
        raise NodeError(str(res.get("error") or f"'{op}' fehlgeschlagen"))
    return res.get("data")


async def _refresh_quietly(client_id: str) -> None:
    """Nach einer Aenderung den Zustand gleich neu holen (Fehler sind hier egal)."""
    try:
        await refresh(client_id)
    except Exception:
        pass


async def update(client_id: str) -> dict:
    """Neues Node-Paket auf den Client bringen (der Agent aktualisiert und startet die Node neu)."""
    if not await db.call(db.node_get, client_id):
        raise NodeError("Keine Node auf diesem Client")
    if not await send_to_agent(client_id, "node-ensure", {
            "path": "/api/nodes/package", "hash": package_hash(), "version": node_version()}):
        raise NodeError("Der Agent dieses Clients ist offline")
    _add_log(client_id, f"{_ts()} Node-Update angefordert")
    return await get_state(client_id)


async def refresh(client_id: str) -> dict:
    """Zustand von der Node neu abfragen (Docker, Module) und speichern."""
    data = await command(client_id, "status", timeout=60)
    await _apply_report(client_id, data)
    return await get_state(client_id)


async def docker_install(client_id: str) -> Any:
    return await command(client_id, "docker-install", timeout=900)


async def deploy_module(client_id: str, module_id: str, params: dict | None = None,
                        activate: bool = False) -> Any:
    """Schickt den Node-Teil eines Moduls (src/modules/node/<id>/) an die Node und installiert ihn.
    Seit Node 0.3.0 ist er danach AUS (je Node einzeln aktivieren). activate=True nur, wenn der
    Benutzer genau diese Node fuer genau dieses Modul ausdruecklich gewaehlt hat (z.B. Cloudflare-Tunnel)."""
    if not modules.is_enabled(module_id):
        raise NodeError(f"Das Modul '{module_id}' ist nicht aktiv (Modul-App).")
    bundle = modules.node_bundle(module_id)
    if bundle is None:
        raise NodeError(f"Das Modul '{module_id}' hat keinen Node-Teil.")
    res = await command(client_id, "module-install", {"bundle": bundle, "params": params or {}}, timeout=300)
    if activate and not (isinstance(res, dict) and res.get("start")):
        res = {**(res if isinstance(res, dict) else {}),
               "start": await command(client_id, "module-action", {"id": module_id, "action": "up", "data": {}}, timeout=180)}
    await _refresh_quietly(client_id)
    return res


async def remove_module(client_id: str, module_id: str) -> Any:
    res = await command(client_id, "module-remove", {"id": module_id}, timeout=120)
    await _refresh_quietly(client_id)
    return res


async def module_action(client_id: str, module_id: str, action: str, data: dict | None = None) -> Any:
    res = await command(client_id, "module-action", {"id": module_id, "action": action, "data": data or {}}, timeout=180)
    if action in ("up", "down", "restart"):
        await _refresh_quietly(client_id)
    return res


async def module_disabled(module_id: str) -> None:
    """Ein Backend-Modul wurde ausgeschaltet: seinen Node-Teil von allen Nodes entfernen."""
    for cid in list(_sid_by_client):
        rec = await db.call(db.node_get, cid)
        if rec and module_id in (rec.get("modules") or {}):
            try:
                await remove_module(cid, module_id)
            except Exception as exc:
                _add_log(cid, f"{_ts()} Modul {module_id} konnte nicht entfernt werden: {exc}")


def _ts() -> str:
    return time.strftime("%H:%M:%S")


# =========================================================================
# Welche Node kann was? (Backend ODER Node waehlen - nur Nodes mit Modul)
# =========================================================================
# Docker-API, WOL, Browser-, Mandanten- und Netzwerk-Abruf sind NODE-MODULE. Seit Node 0.3.0
# ist jedes Modul nach dem Installieren AUS und wird je Node einzeln aktiviert. Eine Node ist
# fuer ein Modul nur waehlbar, wenn es dort installiert UND aktiv (running) ist. Aeltere Nodes
# mit fest eingebautem Docker/WOL zaehlen bewusst NICHT mehr (alles aus, bis aktiviert).
_caps: dict[str, dict] = {}                     # client_id -> {version, modules{id: state}, ops}


def _mod_state(client_id: str, module_id: str) -> str:
    return ((_caps.get(client_id) or {}).get("modules") or {}).get(module_id) or ""


def _too_old(client_id: str) -> bool:
    """Node vor 0.3.0: kennt die Einzel-Aktivierung nicht -> erst aktualisieren."""
    try:
        v = (_caps.get(client_id) or {}).get("version") or "0"
        return tuple(int(x) for x in v.split(".")[:3]) < (0, 3, 0)
    except ValueError:
        return False


def node_has(client_id: str, module_id: str) -> bool:
    """Ist das Node-Modul auf dieser (verbundenen) Node installiert UND aktiviert?"""
    return not _too_old(client_id) and _mod_state(client_id, module_id) in ("running", "partial")


def node_choices(module_id: str, user: dict | None = None) -> list[dict]:
    """Alle verbundenen Nodes fuer eine Backend/Node-Auswahl:
    [{id, name, online, installed, active, state}] - nur active=True ist waehlbar."""
    from app.auth import can_access_client
    out = []
    for cid in list(_sid_by_client):
        if user is not None and not can_access_client(user, cid):
            continue
        st = _mod_state(cid, module_id)
        out.append({"id": cid, "name": (db.get_client(cid) or {}).get("hostname") or cid[:8],
                    "online": True, "installed": bool(st), "active": node_has(cid, module_id), "state": st,
                    "outdated": _too_old(cid)})
    return sorted(out, key=lambda x: (not x["active"], x["name"].lower()))


def require_module(client_id: str, module_id: str) -> None:
    if client_id not in _sid_by_client:
        raise NodeError("Die Node ist offline.")
    name = (db.get_client(client_id) or {}).get("hostname") or client_id[:8]
    if _too_old(client_id):
        raise NodeError(f"Die Node {name} ist veraltet – bitte zuerst „Node aktualisieren“ und das Modul "
                        f"„{module_id}“ danach einzeln aktivieren.")
    st = _mod_state(client_id, module_id)
    if not st:
        raise NodeError(f"Auf der Node {name} ist das Modul „{module_id}“ nicht installiert. "
                        f"Installieren und aktivieren: Node-App → Module oder Modul-App → {module_id} → Nodes.")
    if not node_has(client_id, module_id):
        raise NodeError(f"Das Modul „{module_id}“ ist auf der Node {name} nicht aktiviert – "
                        f"in der Node-App oder Modul-App → {module_id} → Nodes aktivieren.")


# =========================================================================
# Schmale API fuer Module (ctx.nodes)
# =========================================================================

class NodesAPI:
    """Das, was ein Modul ueber ctx.nodes von den Nodes sehen und tun darf."""

    def __init__(self, module_id: str):
        self.module_id = module_id

    async def list(self) -> list[dict]:
        return await list_states()

    async def get(self, client_id: str) -> dict:
        return await get_state(client_id)

    def online(self) -> list[str]:
        return list(_sid_by_client)

    async def deploy(self, client_id: str, params: dict | None = None, activate: bool = False):
        return await deploy_module(client_id, self.module_id, params, activate)

    async def remove(self, client_id: str):
        return await remove_module(client_id, self.module_id)

    async def action(self, client_id: str, action: str, data: dict | None = None):
        return await module_action(client_id, self.module_id, action, data)

    async def command(self, client_id: str, op: str, data: dict | None = None, timeout: float = 60):
        return await command(client_id, op, data, timeout)

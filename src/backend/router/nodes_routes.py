# router/nodes_routes.py
# ----------------------
# REST-API der Nodes (Core). Siehe app/node_manager.py.
#
#   GET    /api/nodes                          alle Nodes mit Zustand
#   GET    /api/nodes/package                  Node-Paket (ZIP) - NUR fuer Agenten (X-Agent-Token)
#   GET    /api/nodes/modules                  Module mit Node-Teil (+ ob aktiv)
#   GET    /api/nodes/{client_id}              Zustand einer Node
#   POST   /api/nodes/{client_id}/enable       Node auf dem Client installieren (ueber den Agenten)
#   POST   /api/nodes/{client_id}/disable      Node entfernen
#   POST   /api/nodes/{client_id}/update       neues Node-Paket einspielen (Agent aktualisiert die Node)
#   POST   /api/nodes/{client_id}/refresh      Zustand von der Node neu abfragen
#   POST   /api/nodes/{client_id}/docker-install   Docker auf der Node installieren (Linux)
#   GET    /api/nodes/{client_id}/logs         letzte Logzeilen der Node
#   POST   /api/nodes/{client_id}/modules/{id}           Node-Modul bereitstellen (Body: {params})
#   DELETE /api/nodes/{client_id}/modules/{id}           Node-Modul entfernen
#   POST   /api/nodes/{client_id}/modules/{id}/action    Aktion (up/down/restart/status/logs ...)

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response

from app import db, modules, node_manager
from app.auth import get_current_user, is_super_admin, require_perm, user_has_permission
from app.config import AGENT_TOKEN

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


# Recht "manage_nodes" ist ein CLIENT-Recht: Es gilt pro Client (bzw. ueber den
# Standard-Client "*" oder global fuer alle). Wer es auf mindestens einem
# Client hat, sieht die Uebersicht - aber nur mit diesen Clients - und den
# Katalog der Node-Module.

def _perm(user: dict, client_id: str) -> None:
    require_perm(user, "manage_nodes", client_id)


def _allowed_ids(user: dict) -> set[str] | None:
    """Clients, deren Node der Benutzer verwalten darf. None = alle (Super-Admin)."""
    if is_super_admin(user):
        return None
    return {c["id"] for c in db.list_clients()
            if user_has_permission(user, "manage_nodes", c["id"])}


def _perm_any(user: dict) -> set[str] | None:
    ids = _allowed_ids(user)
    if ids is not None and not ids:
        raise HTTPException(status_code=403, detail="Fehlendes Recht: manage_nodes")
    return ids


def _wrap(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
async def list_nodes(user: dict = Depends(get_current_user)):
    ids = await db.call(_perm_any, user)
    nodes = await node_manager.list_states()
    if ids is not None:
        nodes = [n for n in nodes if n.get("client_id") in ids]
    return {"nodes": nodes, "package_hash": node_manager.package_hash(),
            "version": node_manager.node_version()}


@router.get("/package")
def package(x_agent_token: str | None = Header(default=None)):
    """Das Node-Paket holt der AGENT (nicht der Browser) - geschuetzt durch das Agent-Token."""
    if not x_agent_token or not hmac.compare_digest(x_agent_token, AGENT_TOKEN):
        raise HTTPException(status_code=403, detail="Agent-Token fehlt oder ist falsch")
    return Response(node_manager.package_zip(), media_type="application/zip",
                    headers={"X-Node-Hash": node_manager.package_hash()})


@router.get("/modules")
def node_modules(user: dict = Depends(get_current_user)):
    _perm_any(user)
    return {"modules": modules.node_modules()}


@router.get("/choices/{module_id}")
def node_choices(module_id: str, user: dict = Depends(get_current_user)):
    """Verbundene Nodes fuer die Auswahl "Backend oder Node" eines Moduls.
    installed=False -> Node-Modul muss erst nachgeladen werden."""
    return {"nodes": node_manager.node_choices(module_id, user),
            "has_node_part": any(m["id"] == module_id for m in modules.node_modules())}


@router.get("/{client_id}")
async def get_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    return await node_manager.get_state(client_id)


@router.post("/{client_id}/enable")
async def enable_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    try:
        res = await node_manager.enable(client_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.enable", target=client_id)
    return res


@router.post("/{client_id}/disable")
async def disable_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    res = await node_manager.disable(client_id)
    db.add_audit_entry(user["username"], "node.disable", target=client_id)
    return res


@router.post("/{client_id}/update")
async def update_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    try:
        res = await node_manager.update(client_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.update", target=client_id)
    return res


@router.post("/{client_id}/refresh")
async def refresh_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    try:
        return await node_manager.refresh(client_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)


@router.post("/{client_id}/docker-install")
async def docker_install(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    try:
        data = await node_manager.docker_install(client_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.docker_install", target=client_id)
    return {"result": data, "node": await node_manager.get_state(client_id)}


@router.get("/{client_id}/logs")
def node_logs(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    return {"lines": node_manager.logs(client_id)}


@router.post("/{client_id}/modules/{module_id}")
async def deploy_module(client_id: str, module_id: str, body: dict | None = None,
                        user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    try:
        data = await node_manager.deploy_module(client_id, module_id, (body or {}).get("params") or {})
    except (node_manager.NodeError, modules.ModuleError) as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.module_deploy", target=client_id, details=module_id)
    return {"result": data, "node": await node_manager.get_state(client_id)}


@router.delete("/{client_id}/modules/{module_id}")
async def remove_module(client_id: str, module_id: str, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    try:
        data = await node_manager.remove_module(client_id, module_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.module_remove", target=client_id, details=module_id)
    return {"result": data, "node": await node_manager.get_state(client_id)}


@router.post("/{client_id}/modules/{module_id}/action")
async def module_action(client_id: str, module_id: str, body: dict, user: dict = Depends(get_current_user)):
    _perm(user, client_id)
    action = str(body.get("action") or "")
    if not action:
        raise HTTPException(status_code=400, detail="action fehlt")
    try:
        data = await node_manager.module_action(client_id, module_id, action, body.get("data") or {})
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    return {"result": data, "node": await node_manager.get_state(client_id)}

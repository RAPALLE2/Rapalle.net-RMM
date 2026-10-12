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
from app.auth import get_current_user, require_perm
from app.config import AGENT_TOKEN

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


def _perm(user: dict) -> None:
    require_perm(user, "manage_nodes")


def _wrap(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
async def list_nodes(user: dict = Depends(get_current_user)):
    _perm(user)
    return {"nodes": await node_manager.list_states(), "package_hash": node_manager.package_hash(),
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
    _perm(user)
    return {"modules": modules.node_modules()}


@router.get("/{client_id}")
async def get_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user)
    return await node_manager.get_state(client_id)


@router.post("/{client_id}/enable")
async def enable_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        res = await node_manager.enable(client_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.enable", target=client_id)
    return res


@router.post("/{client_id}/disable")
async def disable_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user)
    res = await node_manager.disable(client_id)
    db.add_audit_entry(user["username"], "node.disable", target=client_id)
    return res


@router.post("/{client_id}/update")
async def update_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        res = await node_manager.update(client_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.update", target=client_id)
    return res


@router.post("/{client_id}/refresh")
async def refresh_node(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        return await node_manager.refresh(client_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)


@router.post("/{client_id}/docker-install")
async def docker_install(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        data = await node_manager.docker_install(client_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.docker_install", target=client_id)
    return {"result": data, "node": await node_manager.get_state(client_id)}


@router.get("/{client_id}/logs")
def node_logs(client_id: str, user: dict = Depends(get_current_user)):
    _perm(user)
    return {"lines": node_manager.logs(client_id)}


@router.post("/{client_id}/modules/{module_id}")
async def deploy_module(client_id: str, module_id: str, body: dict | None = None,
                        user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        data = await node_manager.deploy_module(client_id, module_id, (body or {}).get("params") or {})
    except (node_manager.NodeError, modules.ModuleError) as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.module_deploy", target=client_id, details=module_id)
    return {"result": data, "node": await node_manager.get_state(client_id)}


@router.delete("/{client_id}/modules/{module_id}")
async def remove_module(client_id: str, module_id: str, user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        data = await node_manager.remove_module(client_id, module_id)
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "node.module_remove", target=client_id, details=module_id)
    return {"result": data, "node": await node_manager.get_state(client_id)}


@router.post("/{client_id}/modules/{module_id}/action")
async def module_action(client_id: str, module_id: str, body: dict, user: dict = Depends(get_current_user)):
    _perm(user)
    action = str(body.get("action") or "")
    if not action:
        raise HTTPException(status_code=400, detail="action fehlt")
    try:
        data = await node_manager.module_action(client_id, module_id, action, body.get("data") or {})
    except node_manager.NodeError as exc:
        raise _wrap(exc)
    return {"result": data, "node": await node_manager.get_state(client_id)}

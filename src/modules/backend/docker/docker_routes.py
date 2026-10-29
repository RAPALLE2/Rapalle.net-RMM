# docker_routes.py (Modul "docker") - Container verwalten
# ------------------------------------------------------
#   GET    /api/docker/targets                         RMM-Server + Nodes (mit Docker-Stand)
#   GET    /api/docker/stacks                          meine Stacks (Container-Stand je Stack)
#   POST   /api/docker/stacks                          {name, target, compose, deploy}   (manage_docker)
#   GET    /api/docker/stacks/{id}                     Details + Container
#   PUT    /api/docker/stacks/{id}                     {name?, compose?}                 (Stack-Admin)
#   POST   /api/docker/stacks/{id}/action/{deploy|start|stop|restart|pull}
#   POST   /api/docker/stacks/{id}/down                {volumes}                          (Stack-Admin)
#   DELETE /api/docker/stacks/{id}?volumes=            Container entfernen + Stack loeschen
#   PATCH  /api/docker/stacks/{id}/members             {members}                          (Stack-Admin)
#   GET    /api/docker/containers?target=              alle Container (manage_docker) / die meiner Stacks
#   POST   /api/docker/containers/{target}/{cid}/action/{start|stop|restart|pause|unpause|kill|remove}
#   GET    .../logs?tail=   .../stats   .../inspect    POST .../exec {cmd}
#   GET    /api/docker/images?target=   POST /api/docker/images/pull {target, image}   DELETE /api/docker/images/{target}/{id}
#   GET    /api/docker/volumes?target=  GET /api/docker/networks?target=               (manage_docker)
#   GET    /api/docker/catalog          GET /api/docker/hub?q=      GET /api/docker/directory

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import can_access_client, get_current_user, require_perm

from . import catalog, compose, engine, publish, stacks
from .engine import DockerError, q

router = APIRouter(prefix="/api/docker", tags=["docker"])
CT_ACTIONS = {"start", "stop", "restart", "pause", "unpause", "kill", "remove"}


def _err(e: Exception):
    if isinstance(e, HTTPException):
        raise e
    if isinstance(e, DockerError):
        raise HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))
    if isinstance(e, (compose.ComposeError, ValueError)):
        raise HTTPException(400, str(e))
    if isinstance(e, KeyError):
        raise HTTPException(404, "Nicht gefunden")
    raise HTTPException(500, str(e))


def _stack(user, sid: str, need: int) -> dict:
    st = stacks.get(sid)
    lv = stacks.level(user, st) if st else 0
    if not st or not lv:
        raise HTTPException(404, "Stack nicht gefunden")
    if lv < need:
        raise HTTPException(403, {1: "", 2: "Nur mit Rolle „Bedienen“", 3: "Nur mit Rolle „Admin“"}[need] or "Keine Berechtigung")
    return st


def _audit(user, act, details=""):
    db.add_audit_entry(user["username"], f"docker.{act}", details=details[:300])


# ------------------------------------------------------------------ Ziele
@router.get("/targets")
async def targets(user: dict = Depends(get_current_user)):
    out = [{"target": "local", "name": "RMM-Server", **(await engine.available("local"))}]
    try:
        from app import node_manager
        for n in await node_manager.list_states():
            cid = n["client_id"]
            if not can_access_client(user, cid):
                continue
            c = db.get_client(cid) or {}
            # Node-Modul "docker" muss auf der Node nachgeladen sein (seit Node 0.2.0 nicht mehr eingebaut)
            has = node_manager.node_has(cid, "docker")
            out.append({"target": f"node:{cid}", "name": f"Node {c.get('hostname') or n.get('hostname') or cid}",
                        "ok": bool(n.get("online") and has and (n.get("docker") or {}).get("running", (n.get("docker") or {}).get("installed"))),
                        "online": n.get("online"), "docker": n.get("docker"), "update_available": n.get("update_available"),
                        "module_installed": has})
    except Exception:
        pass
    return {"targets": out, "manage": stacks.manage_all(user)}


# ------------------------------------------------------------------ Stacks
class StackBody(BaseModel):
    name: str
    target: str = "local"
    compose: str = ""
    source: str = "inline"          # "inline" = eigene Compose, "file" = Compose-Datei im Projektordner
    compose_file: str = "docker-compose.yml"
    deploy: bool = True


class StackPut(BaseModel):
    name: str | None = None
    compose: str | None = None
    source: str | None = None
    compose_file: str | None = None


class MembersBody(BaseModel):
    members: list[dict]


class DownBody(BaseModel):
    volumes: bool = False


def _pub(st: dict, user) -> dict:
    out = {k: st.get(k) for k in ("id", "name", "project", "target", "members", "publish", "created", "created_by",
                                  "updated", "deployed")} | {"level": stacks.level(user, st)}
    out["source"] = st.get("source", "inline")
    out["compose_file"] = st.get("compose_file", "docker-compose.yml")
    out["project_dir"] = f"storage/docker/{st.get('project')}"
    return out


@router.get("/stacks")
async def list_stacks(user: dict = Depends(get_current_user)):
    out = []
    for st in stacks.visible(user):
        item = _pub(st, user)
        try:
            cs = await stacks.containers(st)
            item["containers"], item["running"] = len(cs), sum(1 for c in cs if c["state"] == "running")
        except DockerError as e:
            item["error"] = str(e)
        out.append(item)
    return {"stacks": out, "manage": stacks.manage_all(user)}


@router.post("/stacks")
async def create_stack(body: StackBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_docker")
    if not stacks.target_ok(user, body.target):
        raise HTTPException(404, "Ziel nicht gefunden")
    try:
        st = stacks.create(body.name, body.target, body.compose, user, source=body.source, compose_file=body.compose_file)
    except Exception as e:
        _err(e)
    _audit(user, "stack_created", f"{st['project']} auf {st['target']}")
    log = []
    if body.deploy:
        try:
            log = await stacks.deploy(st)
        except Exception as e:
            return {"stack": _pub(stacks.get(st["id"]), user), "log": [f"❌ {e}"], "ok": False}
    return {"stack": _pub(stacks.get(st["id"]), user), "log": log, "ok": True}


@router.get("/stacks/{sid}")
async def stack_detail(sid: str, user: dict = Depends(get_current_user)):
    st = _stack(user, sid, stacks.VIEW)
    try:
        cs = await stacks.containers(st)
        err = None
    except DockerError as e:
        cs, err = [], str(e)
    try:
        ctext = stacks.compose_text(st)
    except Exception:
        ctext = st.get("compose", "")
    return {"stack": {**_pub(st, user), "compose": ctext}, "containers": cs, "error": err}


@router.put("/stacks/{sid}")
def put_stack(sid: str, body: StackPut, user: dict = Depends(get_current_user)):
    _stack(user, sid, stacks.ADMIN)
    try:
        st = stacks.update(sid, {k: v for k, v in body.model_dump().items() if v is not None})
    except Exception as e:
        _err(e)
    _audit(user, "stack_changed", st["project"])
    return {"stack": _pub(st, user)}


@router.patch("/stacks/{sid}/members")
def stack_members(sid: str, body: MembersBody, user: dict = Depends(get_current_user)):
    _stack(user, sid, stacks.ADMIN)
    st = stacks.update(sid, {"members": body.members})
    _audit(user, "stack_members", st["project"])
    return {"stack": _pub(st, user)}


@router.post("/stacks/{sid}/action/{act}")
async def stack_action(sid: str, act: str, user: dict = Depends(get_current_user)):
    if act not in ("deploy", "pull", "start", "stop", "restart"):
        raise HTTPException(400, "Unbekannte Aktion")
    st = _stack(user, sid, stacks.ADMIN if act in ("deploy", "pull") else stacks.OPERATE)
    try:
        log = await stacks.deploy(st, pull_images=(act == "pull")) if act in ("deploy", "pull") else await stacks.action(st, act)
    except Exception as e:
        _audit(user, f"stack_{act}_failed", f"{st['project']}: {e}")
        return {"ok": False, "log": [f"❌ {e}"]}
    _audit(user, f"stack_{act}", st["project"])
    return {"ok": True, "log": log}


@router.post("/stacks/{sid}/down")
async def stack_down(sid: str, body: DownBody, user: dict = Depends(get_current_user)):
    st = _stack(user, sid, stacks.ADMIN)
    try:
        log = await stacks.down(st, body.volumes)
    except Exception as e:
        _err(e)
    _audit(user, "stack_down", f"{st['project']} (Volumes: {body.volumes})")
    return {"ok": True, "log": log}


@router.delete("/stacks/{sid}")
async def delete_stack(sid: str, volumes: bool = False, user: dict = Depends(get_current_user)):
    st = _stack(user, sid, stacks.ADMIN)
    log = []
    try:
        log = await stacks.down(st, volumes)
    except DockerError as e:
        log.append(f"⚠ Container konnten nicht entfernt werden: {e}")
    remove_publications(st)
    stacks.remove_entry(sid)
    _audit(user, "stack_deleted", f"{st['project']} (Volumes: {volumes})")
    return {"ok": True, "log": log}


# ------------------------------------------------------------------ Veroeffentlichen
#   GET    /api/docker/stacks/{id}/publish          Eintraege + Dienste mit ihren Container-Ports
#   POST   /api/docker/stacks/{id}/publish          {mode: path|port, service, port, name?, public_port?}
#   DELETE /api/docker/stacks/{id}/publish/{pid}
class PublishBody(BaseModel):
    mode: str
    service: str
    port: int
    name: str = ""
    public_port: int = 0


def _webserver():
    from app import modules
    if not modules.is_enabled("webserver"):
        raise HTTPException(409, "Dafür muss das Modul „Webserver“ aktiv sein")
    return modules.use("webserver", "sites")


def _site_url(name: str) -> str:
    return f"/{name}/"


@router.get("/stacks/{sid}/publish")
async def publish_list(sid: str, user: dict = Depends(get_current_user)):
    st = _stack(user, sid, stacks.VIEW)
    try:
        data, _ = compose.parse(st["compose"])
        services = {}
        for name, svc in data["services"].items():
            ports = set()
            for c in compose.plan(data, st["project"], sid)["containers"]:
                if c["service"] == name:
                    ports |= {int(k.split("/")[0]) for k in (c["config"].get("ExposedPorts") or {})}
            services[name] = sorted(ports)
    except Exception:
        services = {}
    # Ports, die das Image selbst meldet (EXPOSE), aus den laufenden Containern ergaenzen
    try:
        for c in await stacks.containers(st):
            if c.get("service") in services:
                services[c["service"]] = sorted(set(services[c["service"]]) | {p["PrivatePort"] for p in c.get("ports") or [] if p.get("PrivatePort")})
    except DockerError:
        pass
    entries = []
    for p in st.get("publish") or []:
        e = dict(p)
        if p["mode"] == "path":
            e["url"] = _site_url(p["name"])
        else:
            kind, cid = engine.parse_target(st["target"])
            host = "rmm" if kind == "local" else ((db.get_client(cid) or {}).get("ip") or "Node")
            e["url"] = f":{p['public_port']}" if kind == "local" else f"{host}:{p['public_port']}"
        entries.append(e)
    return {"publish": entries, "services": services, "target": st["target"]}


@router.post("/stacks/{sid}/publish")
async def publish_add(sid: str, body: PublishBody, user: dict = Depends(get_current_user)):
    st = _stack(user, sid, stacks.ADMIN)
    if body.mode not in ("path", "port") or not (1 <= body.port <= 65535):
        raise HTTPException(400, "Ungültige Angaben")
    data, _ = compose.parse(st["compose"])
    if body.service not in data["services"]:
        raise HTTPException(400, f"Dienst „{body.service}“ gibt es im Stack nicht")
    if body.mode == "path":
        ws = _webserver()
        try:
            ws.create(body.name, f"{st['name']} ({body.service})", user, kind="proxy", public=True,
                      upstream={"provider": "docker", "ref": {"stack": sid, "service": body.service, "port": body.port}})
        except ValueError as e:
            raise HTTPException(400, str(e))
        entry = publish.new_entry("path", body.service, body.port, name=body.name.strip().lower())
    else:
        if not (1 <= body.public_port <= 65535):
            raise HTTPException(400, "Bitte einen öffentlichen Port angeben")
        for other in stacks.all_stacks():
            if other["target"] == st["target"] and any(p.get("mode") == "port" and int(p.get("public_port", 0)) == body.public_port
                                                        for p in other.get("publish") or []):
                raise HTTPException(409, f"Port {body.public_port} ist auf diesem Ziel schon vergeben (Stack „{other['name']}“)")
        entry = publish.new_entry("port", body.service, body.port, public_port=body.public_port)
    st = stacks.update(sid, {"publish": (st.get("publish") or []) + [entry]})
    _audit(user, "publish_add", f"{st['project']}/{body.service}:{body.port} -> "
                                + (f"/{entry.get('name')}/" if body.mode == "path" else f"Port {body.public_port}"))
    log = []
    if body.mode == "port" and st.get("deployed"):
        try:
            log = await stacks.deploy(st, pull_images=False)
        except Exception as e:
            log = [f"❌ {e}"]
    return {"ok": True, "entry": entry, "log": log}


@router.delete("/stacks/{sid}/publish/{pid}")
async def publish_remove(sid: str, pid: str, user: dict = Depends(get_current_user)):
    st = _stack(user, sid, stacks.ADMIN)
    entry = next((p for p in st.get("publish") or [] if p.get("id") == pid), None)
    if not entry:
        raise HTTPException(404, "Nicht gefunden")
    if entry["mode"] == "path":
        try:
            _webserver().remove(entry["name"])
        except (KeyError, HTTPException):
            pass
    st = stacks.update(sid, {"publish": [p for p in st.get("publish") or [] if p.get("id") != pid]})
    _audit(user, "publish_remove", f"{st['project']}: {entry.get('name') or entry.get('public_port')}")
    log = []
    if entry["mode"] == "port" and st.get("deployed"):
        try:
            log = await stacks.deploy(st, pull_images=False)
        except Exception as e:
            log = [f"❌ {e}"]
    return {"ok": True, "log": log}


def remove_publications(st: dict) -> None:
    """Beim Loeschen eines Stacks seine Weiterleitungen im Webserver mit entfernen."""
    for p in st.get("publish") or []:
        if p.get("mode") == "path":
            try:
                _webserver().remove(p["name"])
            except Exception:
                pass


# ------------------------------------------------------------------ Container
async def _ct_level(user, target: str, cid: str) -> tuple[int, dict]:
    if not stacks.target_ok(user, target):
        raise HTTPException(404, "Ziel nicht gefunden")
    try:
        info = await engine.call(target, "GET", f"/containers/{q(cid)}/json")
    except Exception as e:
        _err(e)
    sid = (info.get("Config", {}).get("Labels") or {}).get("rmm.stack")
    if stacks.manage_all(user):
        return stacks.ADMIN, info
    st = stacks.get(sid) if sid else None
    lv = stacks.level(user, st) if st else 0
    if not lv:
        raise HTTPException(404, "Container nicht gefunden")
    return lv, info


def _ct_public(c: dict) -> dict:
    lbl = c.get("Labels") or {}
    return {"id": c["Id"][:12], "name": (c.get("Names") or ["?"])[0].lstrip("/"), "image": c.get("Image"),
            "state": c.get("State"), "status": c.get("Status"), "stack": lbl.get("rmm.stack"),
            "project": lbl.get("com.docker.compose.project"), "service": lbl.get("com.docker.compose.service"),
            "ports": c.get("Ports") or [], "created": c.get("Created")}


@router.get("/containers")
async def containers(target: str = "local", user: dict = Depends(get_current_user)):
    if not stacks.target_ok(user, target):
        raise HTTPException(404, "Ziel nicht gefunden")
    try:
        lst = await engine.call(target, "GET", "/containers/json?all=1")
    except Exception as e:
        _err(e)
    allowed = None if stacks.manage_all(user) else {s["id"] for s in stacks.visible(user) if s["target"] == target}
    out = [_ct_public(c) for c in lst or []]
    if allowed is not None:
        out = [c for c in out if c["stack"] in allowed]
    return {"containers": out, "manage": allowed is None}


@router.post("/containers/{target}/{cid}/action/{act}")
async def ct_action(target: str, cid: str, act: str, user: dict = Depends(get_current_user)):
    if act not in CT_ACTIONS:
        raise HTTPException(400, "Unbekannte Aktion")
    lv, info = await _ct_level(user, target, cid)
    if lv < (stacks.ADMIN if act == "remove" else stacks.OPERATE):
        raise HTTPException(403, "Keine Berechtigung")
    try:
        if act == "remove":
            await engine.call(target, "DELETE", f"/containers/{q(cid)}?force=1")
        else:
            await engine.call(target, "POST", f"/containers/{q(cid)}/{act}", timeout=120)
    except Exception as e:
        _err(e)
    _audit(user, f"container_{act}", f"{info.get('Name', '').lstrip('/')} auf {target}")
    return {"ok": True}


@router.get("/containers/{target}/{cid}/logs")
async def ct_logs(target: str, cid: str, tail: int = 300, user: dict = Depends(get_current_user)):
    await _ct_level(user, target, cid)
    try:
        raw = await engine.call(target, "GET", f"/containers/{q(cid)}/logs?stdout=1&stderr=1&timestamps=1&tail={max(1, min(tail, 5000))}", raw=True)
    except Exception as e:
        _err(e)
    return {"log": engine.demux(raw)}


@router.get("/containers/{target}/{cid}/stats")
async def ct_stats(target: str, cid: str, user: dict = Depends(get_current_user)):
    await _ct_level(user, target, cid)
    try:
        s = await engine.call(target, "GET", f"/containers/{q(cid)}/stats?stream=false", timeout=30)
    except Exception as e:
        _err(e)
    cpu = 0.0
    try:
        cd = s["cpu_stats"]["cpu_usage"]["total_usage"] - s["precpu_stats"]["cpu_usage"]["total_usage"]
        sd = s["cpu_stats"].get("system_cpu_usage", 0) - s["precpu_stats"].get("system_cpu_usage", 0)
        n = s["cpu_stats"].get("online_cpus") or len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
        cpu = round(cd / sd * n * 100, 1) if sd > 0 else 0.0
    except Exception:
        pass
    mem = s.get("memory_stats") or {}
    net = s.get("networks") or {}
    return {"cpu": cpu, "mem": mem.get("usage", 0) - (mem.get("stats") or {}).get("inactive_file", 0),
            "mem_limit": mem.get("limit", 0), "net_rx": sum(v.get("rx_bytes", 0) for v in net.values()),
            "net_tx": sum(v.get("tx_bytes", 0) for v in net.values()), "pids": (s.get("pids_stats") or {}).get("current")}


@router.get("/containers/{target}/{cid}/inspect")
async def ct_inspect(target: str, cid: str, user: dict = Depends(get_current_user)):
    lv, info = await _ct_level(user, target, cid)
    if lv < stacks.ADMIN:          # Umgebungsvariablen koennen Passwoerter enthalten
        info = {k: v for k, v in info.items() if k in ("Id", "Name", "State", "Created", "Image", "RestartCount")}
    return {"inspect": info}


class ExecBody(BaseModel):
    cmd: str
    user: str = ""


@router.post("/containers/{target}/{cid}/exec")
async def ct_exec(target: str, cid: str, body: ExecBody, user: dict = Depends(get_current_user)):
    """Befehl im Container ausfuehren (ohne interaktive Sitzung) - Rolle "Bedienen"."""
    lv, info = await _ct_level(user, target, cid)
    if lv < stacks.OPERATE:
        raise HTTPException(403, "Keine Berechtigung")
    if not body.cmd.strip():
        raise HTTPException(400, "Befehl fehlt")
    try:
        ex = await engine.call(target, "POST", f"/containers/{q(cid)}/exec", {
            "Cmd": ["/bin/sh", "-c", body.cmd], "AttachStdout": True, "AttachStderr": True, "User": body.user or ""})
        raw = await engine.call(target, "POST", f"/exec/{ex['Id']}/start", {"Detach": False, "Tty": False}, raw=True, timeout=120)
        res = await engine.call(target, "GET", f"/exec/{ex['Id']}/json")
    except Exception as e:
        _err(e)
    _audit(user, "container_exec", f"{info.get('Name', '').lstrip('/')}: {body.cmd[:150]}")
    return {"output": engine.demux(raw), "exit_code": res.get("ExitCode")}


# ------------------------------------------------------------------ Images, Volumes, Netzwerke
class PullBody(BaseModel):
    target: str = "local"
    image: str


def _need_manage(user, target):
    require_perm(user, "manage_docker")
    if not stacks.target_ok(user, target):
        raise HTTPException(404, "Ziel nicht gefunden")


@router.get("/images")
async def images(target: str = "local", user: dict = Depends(get_current_user)):
    _need_manage(user, target)
    try:
        lst = await engine.call(target, "GET", "/images/json")
    except Exception as e:
        _err(e)
    return {"images": [{"id": i["Id"].split(":")[-1][:12], "tags": i.get("RepoTags") or ["<none>"], "size": i.get("Size"),
                        "created": i.get("Created")} for i in lst or []]}


@router.post("/images/pull")
async def image_pull(body: PullBody, user: dict = Depends(get_current_user)):
    _need_manage(user, body.target)
    log = []
    try:
        await stacks.pull(body.target, body.image.strip(), log)
    except Exception as e:
        _err(e)
    _audit(user, "image_pull", f"{body.image} auf {body.target}")
    return {"ok": True, "log": log}


@router.delete("/images/{target}/{iid}")
async def image_delete(target: str, iid: str, user: dict = Depends(get_current_user)):
    _need_manage(user, target)
    try:
        await engine.call(target, "DELETE", f"/images/{q(iid)}")
    except Exception as e:
        _err(e)
    return {"ok": True}


@router.get("/volumes")
async def volumes(target: str = "local", user: dict = Depends(get_current_user)):
    _need_manage(user, target)
    try:
        res = await engine.call(target, "GET", "/volumes") or {}
    except Exception as e:
        _err(e)
    return {"volumes": [{"name": v["Name"], "driver": v.get("Driver"), "project": (v.get("Labels") or {}).get("com.docker.compose.project")}
                        for v in res.get("Volumes") or []]}


@router.get("/networks")
async def networks(target: str = "local", user: dict = Depends(get_current_user)):
    _need_manage(user, target)
    try:
        lst = await engine.call(target, "GET", "/networks")
    except Exception as e:
        _err(e)
    return {"networks": [{"id": n["Id"][:12], "name": n["Name"], "driver": n.get("Driver"),
                          "project": (n.get("Labels") or {}).get("com.docker.compose.project")} for n in lst or []]}


# ------------------------------------------------------------------ Container-Browser + Verzeichnis
@router.get("/catalog")
def get_catalog(user: dict = Depends(get_current_user)):
    return {"templates": catalog.templates()}


@router.get("/hub")
async def hub(q: str, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_docker")
    if len(q.strip()) < 2:
        return {"results": []}
    try:
        res = await asyncio.get_running_loop().run_in_executor(None, catalog.hub_search, q.strip())
    except Exception as e:
        raise HTTPException(502, f"Docker Hub nicht erreichbar: {e}")
    return {"results": res}


@router.get("/hub/template")
def hub_template(image: str, port: int = 0, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_docker")
    return {"compose": catalog.image_template(image, port or None)}


@router.get("/directory")
def directory(user: dict = Depends(get_current_user)):
    if not stacks.manage_all(user) and not any(s["level"] >= stacks.ADMIN for s in stacks.visible(user)):
        raise HTTPException(403, "Nur für Stack-Admins")
    return {"users": [{"id": u["id"], "username": u["username"]} for u in db.list_users()],
            "groups": [{"id": g["id"], "name": g["name"], "is_ad_group": g.get("is_ad_group"), "permissions": g.get("permissions")}
                       for g in db.list_groups()]}


# ------------------------------------------------------------------ Interaktive Konsole (WebSocket)
# WS /api/docker/containers/{target}/{cid}/console  - Dashboard-Sitzung als Unterprotokoll "rmmtoken.<JWT>"
# Docker exec mit TTY (bash, sonst sh). Browser -> Server: {"t":"i","d":"<Eingabe>"} | {"t":"r","c":cols,"r":rows}
# Server -> Browser: Bytes der Terminal-Ausgabe. Nur Ziel "local" (Nodes: Befehlsmodus /exec).
from fastapi import WebSocket  # noqa: E402


@router.websocket("/containers/{target}/{cid}/console")
async def ct_console(websocket: WebSocket, target: str, cid: str):
    from app.auth import decode_access_token
    offered = websocket.scope.get("subprotocols") or []
    token = next((p[len("rmmtoken."):] for p in offered if p.startswith("rmmtoken.")), "")
    try:
        user = db.get_user_by_id(decode_access_token(token).get("sub"))
        lv, info = await _ct_level(user, target, cid) if user else (0, {})
    except Exception:
        user, lv, info = None, 0, {}
    if not user or lv < stacks.OPERATE:
        await websocket.close(code=4403)
        return
    await websocket.accept(subprotocol="rmm-console" if "rmm-console" in offered else None)
    if target != "local":
        await websocket.send_bytes("\r\nDie interaktive Konsole gibt es für Container auf dem RMM-Server.\r\n"
                                   "Für Node-Container bitte den Befehlsmodus nutzen.\r\n".encode())
        await websocket.close()
        return
    if not (info.get("State") or {}).get("Running"):
        await websocket.send_bytes("\r\nDer Container läuft nicht.\r\n".encode())
        await websocket.close()
        return
    writer = None
    try:
        ex = await engine.call("local", "POST", f"/containers/{q(cid)}/exec", {
            "AttachStdin": True, "AttachStdout": True, "AttachStderr": True, "Tty": True, "Env": ["TERM=xterm-256color"],
            "Cmd": ["/bin/sh", "-c", "if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi"]})
        eid = ex["Id"]
        reader, writer = await asyncio.open_unix_connection(engine.DOCKER_SOCKET)
        payload = json.dumps({"Detach": False, "Tty": True}).encode()
        writer.write((f"POST /exec/{eid}/start HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\n"
                      f"Connection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: {len(payload)}\r\n\r\n").encode() + payload)
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0] and b" 200 " not in head.split(b"\r\n", 1)[0]:
            raise DockerError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        _audit(user, "container_console", f"{info.get('Name', '').lstrip('/')}")

        async def docker_to_ws():
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)

        async def ws_to_docker():
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                try:
                    m = json.loads(msg.get("text") or "{}")
                except Exception:
                    continue
                if m.get("t") == "i":
                    writer.write(str(m.get("d", "")).encode())
                    await writer.drain()
                elif m.get("t") == "r":
                    try:
                        await engine.call("local", "POST", f"/exec/{eid}/resize?h={int(m['r'])}&w={int(m['c'])}")
                    except Exception:
                        pass
        tasks = [asyncio.create_task(docker_to_ws()), asyncio.create_task(ws_to_docker())]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except Exception as e:      # noqa: BLE001
        try:
            await websocket.send_bytes(f"\r\nKonsole beendet: {e}\r\n".encode())
        except Exception:
            pass
    finally:
        if writer:
            writer.close()
        try:
            await websocket.close()
        except Exception:
            pass

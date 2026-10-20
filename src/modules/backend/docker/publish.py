# publish.py (Modul "docker") - Container oeffentlich machen
# ----------------------------------------------------------
# Eintraege je Stack (stack["publish"]):
#   {"id", "mode": "path", "service", "port", "name"}          rmm.url.de/<name>/  (Weiterleitung im Modul "webserver")
#   {"id", "mode": "port", "service", "port", "public_port"}   <host>:<public_port> (Port-Freigabe auf dem Docker-Host)
#
# "path": Der Webserver fragt bei JEDEM Abruf resolve() nach der aktuellen Adresse (Container-IPs
#   aendern sich beim Neubereitstellen):
#   - Ziel "local", RMM laeuft selbst in Docker: RMM-Container wird ins Netz des Stacks gehaengt,
#     Adresse = http://<containername>:<port>
#   - Ziel "local", RMM laeuft direkt auf dem Host: http://<Container-IP>:<port>
#   - Ziel "node:<client>": Tunnel durch den Agenten (Modul "Netzwerk"/Port-Weiterleitung) zur
#     Container-IP auf dem Node, Adresse = http://127.0.0.1:<Tunnel-Port>
# "port": wird beim Bereitstellen als PortBindings in den Container eingetragen (inject()).

from __future__ import annotations

import asyncio
import json
import secrets
import time

from . import engine, stacks
from .engine import DockerError, q

_tunnels: dict[str, tuple[int, float]] = {}


async def _container(st: dict, service: str) -> dict:
    flt = q(json.dumps({"label": [f"rmm.stack={st['id']}", f"com.docker.compose.service={service}"]}))
    lst = await engine.call(st["target"], "GET", f"/containers/json?filters={flt}")
    if not lst:
        raise DockerError(f"Dienst „{service}“ läuft nicht – Stack bereitstellen/starten", 503)
    return await engine.call(st["target"], "GET", f"/containers/{lst[0]['Id']}/json")


def _first_net(info: dict) -> tuple[str, dict]:
    nets = (info.get("NetworkSettings") or {}).get("Networks") or {}
    for name, n in nets.items():
        if n.get("IPAddress"):
            return name, n
    raise DockerError("Der Container hat keine Netzwerk-Adresse", 503)


async def resolve(ref: dict) -> str:
    """Aktuelle Adresse eines veroeffentlichten Dienstes (fuer die Weiterleitung des Webservers)."""
    st = stacks.get(ref.get("stack", ""))
    if not st:
        raise DockerError("Stack gibt es nicht mehr", 404)
    port = int(ref.get("port") or 80)
    info = await _container(st, ref.get("service", ""))
    net_name, net = _first_net(info)
    name = info["Name"].lstrip("/")
    if st["target"] == "local":
        from app import docker_services
        me = await asyncio.get_running_loop().run_in_executor(None, docker_services._self_container)
        if me:                                       # RMM laeuft selbst in Docker -> ins Stack-Netz haengen
            if net_name not in ((me.get("NetworkSettings") or {}).get("Networks") or {}):
                try:
                    await engine.call("local", "POST", f"/networks/{q(net_name)}/connect", {"Container": me["Id"]})
                except DockerError as e:
                    if "already exists" not in str(e):
                        raise
            return f"http://{name}:{port}"
        return f"http://{net['IPAddress']}:{port}"
    # Node: Tunnel durch den Agenten zur Container-IP auf dem Node
    key = f"{st['id']}:{ref.get('service')}:{port}:{net['IPAddress']}"
    cached = _tunnels.get(key)
    if cached and cached[1] - 300 > time.time():
        return f"http://127.0.0.1:{cached[0]}"
    from app import modules
    pf = modules.use("network", "portforward") if modules.is_enabled("network") else None
    if pf is None or not modules.feature("network", "portforward"):
        raise DockerError("Veröffentlichen von Node-Containern braucht das Modul „Netzwerk“ mit Port-Weiterleitung", 409)
    _, cid = engine.parse_target(st["target"])
    rec = await pf.create(client_id=cid, target_port=port, username="system:docker", minutes=720,
                          target_host=net["IPAddress"], allow_from="127.0.0.1/32", label=f"Docker {st['project']}/{ref.get('service')}")
    _tunnels[key] = (int(rec["listen_port"]), float(rec.get("expires_at") or time.time() + 43200))
    return f"http://127.0.0.1:{rec['listen_port']}"


def inject(st: dict, plan: dict) -> None:
    """Port-Freigaben ("port") in den Bauplan eintragen (vor dem Bereitstellen)."""
    for p in st.get("publish") or []:
        if p.get("mode") != "port":
            continue
        for c in plan["containers"]:
            if c["service"] != p["service"]:
                continue
            key = f"{int(p['port'])}/{p.get('protocol', 'tcp')}"
            c["config"].setdefault("ExposedPorts", {})[key] = {}
            hb = c["config"]["HostConfig"].setdefault("PortBindings", {}).setdefault(key, [])
            if not any(str(b.get("HostPort")) == str(p["public_port"]) for b in hb):
                hb.append({"HostIp": "", "HostPort": str(p["public_port"])})


def new_entry(mode: str, service: str, port: int, name: str = "", public_port: int = 0) -> dict:
    e = {"id": secrets.token_hex(4), "mode": mode, "service": service, "port": int(port), "created": int(time.time())}
    if mode == "path":
        e["name"] = name
    else:
        e["public_port"] = int(public_port)
    return e

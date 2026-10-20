# compose.py (Modul "docker") - docker-compose.yml einlesen und in Docker-API-Aufrufe uebersetzen
# --------------------------------------------------------------------------------------------
# Die Docker-Engine kennt kein Compose. Diese Datei versteht die gaengigen Angaben und baut daraus
# Container/Netzwerke/Volumes - mit den offiziellen Compose-Labels, damit auch `docker compose`
# auf dem Host die Container als Projekt erkennt.
#
# Unterstuetzt je Service: image, container_name, command, entrypoint, environment (Liste/Map),
#   ports (kurz/lang, /udp, IP), volumes (benannt, absolute Bind-Mounts, :ro), restart, depends_on,
#   networks (Liste/Map), labels, hostname, user, working_dir, privileged, cap_add, cap_drop,
#   devices, extra_hosts, dns, mem_limit, cpus, shm_size, tty, stdin_open, expose, healthcheck(ignoriert)
# Oberste Ebene: services, volumes, networks (version wird ignoriert).
# NICHT unterstuetzt (verstaendliche Meldung): build, env_file, configs, secrets, deploy, extends.

from __future__ import annotations

import re

UNSUPPORTED = {"build": "Selbst bauen (build) geht hier nicht – bitte ein fertiges Image angeben",
               "env_file": "env_file wird nicht unterstützt – Variablen bitte unter environment eintragen",
               "configs": "configs werden nicht unterstützt", "secrets": "secrets werden nicht unterstützt",
               "extends": "extends wird nicht unterstützt"}
IGNORED = {"healthcheck", "deploy", "logging", "stop_grace_period", "profiles", "pull_policy", "platform"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class ComposeError(ValueError):
    pass


def project_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9_-]+", "-", (name or "").lower()).strip("-_")
    if not NAME_RE.match(n or ""):
        raise ComposeError("Stack-Name: Buchstaben a–z, Ziffern, - und _ (max. 63 Zeichen)")
    return n


def parse(text: str) -> tuple[dict, list[str]]:
    """-> (normalisierte Compose-Daten, Hinweise)"""
    try:
        import yaml
    except ImportError:
        raise ComposeError("PyYAML fehlt (wird beim Aktivieren des Moduls installiert)")
    try:
        data = yaml.safe_load(text or "") or {}
    except yaml.YAMLError as e:
        raise ComposeError(f"YAML-Fehler: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict) or not data["services"]:
        raise ComposeError("Die Datei braucht einen Abschnitt „services:“ mit mindestens einem Dienst")
    notes = []
    for name, svc in data["services"].items():
        if not NAME_RE.match(str(name)):
            raise ComposeError(f"Ungültiger Dienstname „{name}“")
        if not isinstance(svc, dict):
            raise ComposeError(f"Dienst „{name}“ ist leer")
        for k in svc:
            if k in UNSUPPORTED:
                raise ComposeError(f"{name}: {UNSUPPORTED[k]}")
            if k in IGNORED:
                notes.append(f"{name}: „{k}“ wird ignoriert")
        if not svc.get("image"):
            raise ComposeError(f"Dienst „{name}“ braucht ein image")
    return data, notes


def order(services: dict) -> list[str]:
    """Reihenfolge nach depends_on (Abhaengigkeiten zuerst)."""
    out, seen = [], set()

    def deps(s):
        d = services[s].get("depends_on") or []
        return list(d.keys()) if isinstance(d, dict) else list(d)

    def visit(s, stack):
        if s in seen:
            return
        if s in stack:
            raise ComposeError(f"Zirkuläre Abhängigkeit bei „{s}“")
        for d in deps(s):
            if d not in services:
                raise ComposeError(f"„{s}“ hängt von unbekanntem Dienst „{d}“ ab")
            visit(d, stack | {s})
        seen.add(s)
        out.append(s)
    for s in services:
        visit(s, frozenset())
    return out


def _env(v) -> list[str]:
    if isinstance(v, dict):
        return [f"{k}={'' if val is None else val}" for k, val in v.items()]
    return [str(x) for x in (v or [])]


def _cmd(v):
    if v is None:
        return None
    if isinstance(v, list):
        return [str(x) for x in v]
    import shlex
    return shlex.split(str(v))


def _ports(items) -> tuple[dict, dict]:
    """-> (ExposedPorts, PortBindings)"""
    exposed, bind = {}, {}
    for p in items or []:
        if isinstance(p, dict):                      # lange Schreibweise
            proto = p.get("protocol", "tcp")
            key = f"{p['target']}/{proto}"
            exposed[key] = {}
            if p.get("published"):
                bind.setdefault(key, []).append({"HostIp": p.get("host_ip", ""), "HostPort": str(p["published"])})
            continue
        s = str(p)
        proto = "tcp"
        if "/" in s:
            s, proto = s.split("/", 1)
        parts = s.split(":")
        if len(parts) == 1:
            exposed[f"{parts[0]}/{proto}"] = {}
        elif len(parts) == 2:
            key = f"{parts[1]}/{proto}"
            exposed[key] = {}
            bind.setdefault(key, []).append({"HostIp": "", "HostPort": parts[0]})
        elif len(parts) == 3:
            key = f"{parts[2]}/{proto}"
            exposed[key] = {}
            bind.setdefault(key, []).append({"HostIp": parts[0], "HostPort": parts[1]})
        else:
            raise ComposeError(f"Port-Angabe nicht verstanden: {p}")
    return exposed, bind


def _volumes(items, project: str, declared: dict) -> tuple[list[str], list[str]]:
    """-> (Binds fuer HostConfig, benannte Volumes die angelegt werden muessen)"""
    binds, named = [], []
    for v in items or []:
        if isinstance(v, dict):
            src, dst, ro = v.get("source", ""), v.get("target", ""), v.get("read_only")
            if v.get("type") == "tmpfs":
                continue
        else:
            parts = str(v).split(":")
            if len(parts) == 1:                      # anonymes Volume (legt Docker selbst an)
                src, dst, ro = "", parts[0], False
            else:
                src, dst = parts[0], parts[1]
                ro = len(parts) > 2 and "ro" in parts[2].split(",")
        if not dst:
            raise ComposeError(f"Volume-Angabe nicht verstanden: {v}")
        if not src:
            continue                                  # anonym: Docker legt es selbst an
        if src.startswith(("./", "../", "~")):
            raise ComposeError(f"Relative Pfade werden nicht unterstützt ({src}) – benanntes Volume oder absoluten Pfad nutzen")
        if src.startswith("/"):
            binds.append(f"{src}:{dst}" + (":ro" if ro else ""))
        else:
            ext = (declared.get(src) or {}) if isinstance(declared.get(src), dict) else {}
            real = ext.get("name") or (src if ext.get("external") else f"{project}_{src}")
            if not ext.get("external"):              # externe Volumes gibt es schon - nicht anlegen
                named.append(real)
            binds.append(f"{real}:{dst}" + (":ro" if ro else ""))
    return binds, named


def _mem(v) -> int | None:
    if v is None:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([bkmg]?)b?", str(v).lower())
    if not m:
        raise ComposeError(f"Speicherangabe nicht verstanden: {v}")
    return int(float(m.group(1)) * {"": 1, "b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2)])


def plan(data: dict, project: str, stack_id: str) -> dict:
    """Uebersetzt Compose in Bauplan: {networks: [...], volumes: [...], containers: [{name, service, image, config}]}"""
    services = data["services"]
    decl_vols = data.get("volumes") or {}
    decl_nets = data.get("networks") or {}
    default_net = f"{project}_default"
    networks, volumes, containers, external_nets = {default_net}, set(), [], set()
    for svc_name in order(services):
        s = services[svc_name]
        exposed, bindings = _ports(s.get("ports"))
        for e in s.get("expose") or []:
            exposed.setdefault(f"{e}/tcp" if "/" not in str(e) else str(e), {})
        binds, named = _volumes(s.get("volumes"), project, decl_vols)
        volumes.update(named)
        nets = s.get("networks")
        net_names = list(nets.keys()) if isinstance(nets, dict) else list(nets or [])
        real_nets = []
        for n in net_names or ["default"]:
            if n == "default":
                real_nets.append(default_net)
            else:
                ext = decl_nets.get(n) if isinstance(decl_nets.get(n), dict) else {}
                real = (ext or {}).get("name") or (n if (ext or {}).get("external") else f"{project}_{n}")
                real_nets.append(real)
                if (ext or {}).get("external"):       # externe Netzwerke gibt es schon - nicht anlegen
                    external_nets.add(real)
        networks.update(real_nets)
        restart = str(s.get("restart") or "no")
        rp = {"no": "", "always": "always", "unless-stopped": "unless-stopped", "on-failure": "on-failure"}.get(restart.split(":")[0], "")
        labels = {"com.docker.compose.project": project, "com.docker.compose.service": svc_name,
                  "com.docker.compose.container-number": "1", "rmm.stack": stack_id}
        lbl = s.get("labels") or {}
        labels.update(lbl if isinstance(lbl, dict) else dict(str(x).split("=", 1) for x in lbl if "=" in str(x)))
        host = {"Binds": binds, "PortBindings": bindings, "RestartPolicy": {"Name": rp},
                "Privileged": bool(s.get("privileged")), "CapAdd": s.get("cap_add") or None, "CapDrop": s.get("cap_drop") or None,
                "ExtraHosts": s.get("extra_hosts") or None, "Dns": s.get("dns") if isinstance(s.get("dns"), list) else ([s["dns"]] if s.get("dns") else None),
                "NetworkMode": real_nets[0]}
        if s.get("devices"):
            host["Devices"] = [{"PathOnHost": d.split(":")[0], "PathInContainer": (d.split(":") + [d])[1], "CgroupPermissions": "rwm"} for d in s["devices"]]
        if s.get("mem_limit"):
            host["Memory"] = _mem(s["mem_limit"])
        if s.get("cpus"):
            host["NanoCpus"] = int(float(s["cpus"]) * 1e9)
        if s.get("shm_size"):
            host["ShmSize"] = _mem(s["shm_size"])
        cfg = {"Image": s["image"], "Env": _env(s.get("environment")), "Labels": labels, "ExposedPorts": exposed,
               "HostConfig": {k: v for k, v in host.items() if v not in (None, [], {})},
               "NetworkingConfig": {"EndpointsConfig": {real_nets[0]: {"Aliases": [svc_name]}}}}
        if s.get("command") is not None:
            cfg["Cmd"] = _cmd(s["command"])
        if s.get("entrypoint") is not None:
            cfg["Entrypoint"] = _cmd(s["entrypoint"])
        for src, dst in (("hostname", "Hostname"), ("user", "User"), ("working_dir", "WorkingDir")):
            if s.get(src):
                cfg[dst] = str(s[src])
        if s.get("tty"):
            cfg["Tty"] = True
        if s.get("stdin_open"):
            cfg["OpenStdin"] = True
        containers.append({"service": svc_name, "name": s.get("container_name") or f"{project}-{svc_name}-1",
                           "image": s["image"], "config": cfg, "extra_networks": real_nets[1:]})
    used = {c["config"]["HostConfig"]["NetworkMode"] for c in containers} | {n for c in containers for n in c["extra_networks"]}
    return {"networks": sorted((networks & used) - external_nets), "volumes": sorted(volumes), "containers": containers}

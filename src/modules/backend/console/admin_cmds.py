# admin_cmds.py (Modul "console") - Verwaltung wie in der Proxmox-Shell
# ---------------------------------------------------------------------
# api      wie "pvesh": jede Dashboard-Route aufrufen (alles, was das Dashboard kann)
# pve      wie qm/pct: Proxmox-Gaeste auflisten, steuern, BETRETEN (pct enter / qm terminal)
# docker   enter/logs/deploy (zusaetzlich zu ls/ps/start/stop/restart aus commands.py)
# storage  Relay-Speicher (Spaces, Websites, Deployment, ...) wie ein Dateisystem
# net      Port-Weiterleitungen
# user, group, module, settings, web
# Alle Befehle rufen intern die Dashboard-Routen auf (apicall.call) -> gleiche Rechte + Pruefungen.

from __future__ import annotations

import asyncio
import json
import shlex

from app import db, modules

from . import apicall
from .commands import REGISTRY, Command, RemoteShell, command, resolve, table
from .shell import BLUE, BOLD, CYAN, GREEN, GREY, RED, RST, YELLOW, CommandError, UsageError


async def api(con, method, path, query=None, body=None):
    try:
        return await apicall.call(con.user, method, path, query, body, con.ip)
    except apicall.ApiError as e:
        raise CommandError(f"{e} (HTTP {e.status})")


def kv(args: list[str]) -> dict:
    """key=value ... -> dict (Zahlen/true/false/null werden umgewandelt)."""
    out = {}
    for a in args:
        if "=" not in a:
            raise UsageError(f"„{a}“: bitte als schluessel=wert angeben")
        k, v = a.split("=", 1)
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def show_json(con, data, indent=0) -> None:
    txt = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    import re
    txt = re.sub(r'^(\s*)"([^"]+)":', lambda m: f'{m.group(1)}{CYAN}"{m.group(2)}"{RST}:', txt, flags=re.M)
    con.line(txt)


# ------------------------------------------------------------------ api (wie pvesh)
_METHODS = ["ls", "get", "post", "put", "patch", "delete"]


def _api_paths(con, done):
    if not done or done[0] == "ls":
        return []
    meth = done[0].upper()
    return [p for p, ms in apicall.route_paths() if meth in ms]


@command("api", "api ls [filter] | api <methode> <pfad> [k=v …]",
         "Jede Dashboard-Schnittstelle aufrufen – wie pvesh (gleiche Rechte wie im Dashboard)",
         args=(_METHODS, _api_paths))
async def _api(con, argv):
    if not argv:
        raise UsageError("Unterbefehl fehlt")
    sub = argv[0].lower()
    if sub == "ls":
        f = (argv[1] if len(argv) > 1 else "").lower()
        rows = [[" ".join(ms), p] for p, ms in apicall.route_paths() if f in p.lower()]
        table(con, rows, ["Methoden", "Pfad"])
        con.line(f"{GREY}{len(rows)} Pfade · Platzhalter wie {{client_id}} durch Werte ersetzen{RST}")
        return
    if sub not in _METHODS or len(argv) < 2:
        raise UsageError("Methode und Pfad angeben")
    path = argv[1] if argv[1].startswith("/") else "/" + argv[1]
    rest = argv[2:]
    if rest and rest[0].lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(" ".join(rest))
        except ValueError as e:
            raise UsageError(f"JSON nicht verstanden: {e}")
    else:
        payload = kv(rest)
    if "{" in path:
        raise UsageError(f"Platzhalter im Pfad ersetzen: {path}")
    if sub in ("get", "delete"):
        res = await api(con, sub, path, query=payload or None)
    else:
        if sub in ("post", "put", "patch", "delete") and not await con.confirm(f"{sub.upper()} {path} ausführen?"):
            return
        res = await api(con, sub, path, body=payload)
    show_json(con, res) if res is not None else con.line(f"{GREEN}OK{RST}")


# ------------------------------------------------------------------ module
def _module_ids(con, done):
    return [m["id"] for m in modules.list_all()["modules"]]


@command("module", "module ls | enable|disable <id> | settings <id> [schluessel=wert …]", "Module verwalten",
         perm="manage_modules", args=(["ls", "enable", "disable", "settings"], _module_ids))
async def _module(con, argv):
    sub = (argv[0] if argv else "ls").lower()
    if sub == "ls":
        mods = (await api(con, "get", "/api/modules"))["modules"]
        table(con, [[f"{GREEN}●{RST}" if m.get("enabled") else f"{GREY}○{RST}", m["id"], m.get("name", ""), m.get("version", "")]
                    for m in mods], ["", "ID", "Name", "Version"])
        return
    if len(argv) < 2:
        raise UsageError("Modul-ID fehlt")
    mid = argv[1]
    if sub in ("enable", "disable"):
        if sub == "disable" and not await con.confirm(f"Modul {mid} ausschalten?"):
            return
        await api(con, "post", f"/api/modules/{mid}/{sub}", body={})
        con.line(f"{GREEN}Modul {mid} {'eingeschaltet' if sub == 'enable' else 'ausgeschaltet'}{RST}")
    elif sub == "settings":
        if len(argv) > 2:
            await api(con, "put", f"/api/modules/{mid}/settings", body={"settings": kv(argv[2:])})
            con.line(f"{GREEN}gespeichert{RST}")
        show_json(con, await api(con, "get", f"/api/modules/{mid}/settings"))
    else:
        raise UsageError(f"Unbekannt: {sub}")


# ------------------------------------------------------------------ user / group
def _usernames(con, done):
    return [u["username"] for u in db.list_users()]


def _user_id(name: str) -> str:
    u = db.get_user_by_username(name)
    if not u:
        raise CommandError(f"Benutzer „{name}“ gibt es nicht")
    return u["id"]


ROLES = ["viewer", "support", "admin"]


def _user_arg(con, done):
    return _usernames(con, done) if done and done[0] in ("del", "groups") else []


def _user_third(con, done):
    if done and done[0] == "add":
        return ROLES
    if done and done[0] == "groups":
        return [g["name"] for g in db.list_groups()]
    return []


@command("user", "user ls | add <name> [viewer|support|admin] [passwort] | del <name> | groups <name> [gruppe …]",
         "Benutzer verwalten", perm="manage_users", args=(["ls", "add", "del", "groups"], _user_arg, _user_third))
async def _user(con, argv):
    sub = (argv[0] if argv else "ls").lower()
    if sub == "ls":
        users = await api(con, "get", "/api/users")
        table(con, [[u["username"], u.get("display_name") or "", u.get("role") or "", u.get("realm") or "local"] for u in users],
              ["Benutzer", "Name", "Rolle", "Realm"])
        return
    if len(argv) < 2:
        raise UsageError("Benutzername fehlt")
    name = argv[1]
    if sub == "add":
        body = {"username": name, "display_name": name, "role": argv[2] if len(argv) > 2 else "viewer"}
        if len(argv) > 3:
            body["password"] = argv[3]
        res = await api(con, "post", "/api/users", body=body)
        con.line(f"{GREEN}Benutzer {name} angelegt{RST}")
        if isinstance(res, dict) and (res.get("one_time_password") or res.get("password")):
            con.line(f"Einmal-Passwort: {BOLD}{res.get('one_time_password') or res.get('password')}{RST}")
    elif sub == "del":
        uid = _user_id(name)                                  # erst pruefen, dann fragen
        if not await con.confirm(f"Benutzer {name} löschen?"):
            return
        await api(con, "delete", f"/api/users/{uid}")
        con.line(f"{GREEN}gelöscht{RST}")
    elif sub == "groups":
        uid = _user_id(name)
        if len(argv) > 2:
            gmap = {g["name"]: g["id"] for g in db.list_groups()}
            missing = [g for g in argv[2:] if g not in gmap]
            if missing:
                raise CommandError(f"Gruppe(n) unbekannt: {', '.join(missing)}")
            await api(con, "put", f"/api/admin/users/{uid}/groups", body={"group_ids": [gmap[g] for g in argv[2:]]})
        res = await api(con, "get", f"/api/admin/users/{uid}/groups")
        ids = res.get("group_ids", res) if isinstance(res, dict) else res
        names = {g["id"]: g["name"] for g in db.list_groups()}
        con.line(", ".join(names.get(i, str(i)) for i in (ids or [])) or f"{GREY}(keine Gruppen){RST}")
    else:
        raise UsageError(f"Unbekannt: {sub}")


@command("group", "group ls | add <name> | del <name>", "Gruppen verwalten", perm="manage_users",
         args=(["ls", "add", "del"], lambda con, d: [g["name"] for g in db.list_groups()]))
async def _group(con, argv):
    sub = (argv[0] if argv else "ls").lower()
    if sub == "ls":
        gs = await api(con, "get", "/api/admin/groups")
        table(con, [[g["name"], g.get("auto_assign") or "–", "AD" if g.get("is_ad_group") else "",
                     ", ".join((g.get("permissions") or [])[:5]) or "–"] for g in gs],
              ["Gruppe", "automatisch für Rolle", "Art", "Rechte (Auszug)"])
        return
    if len(argv) < 2:
        raise UsageError("Gruppenname fehlt")
    if sub == "add":
        await api(con, "post", "/api/admin/groups", body={"name": argv[1], "permissions": []})
        con.line(f"{GREEN}Gruppe {argv[1]} angelegt{RST}")
    elif sub == "del":
        g = next((x for x in db.list_groups() if x["name"] == argv[1]), None)
        if not g:
            raise CommandError("Gruppe nicht gefunden")
        if await con.confirm(f"Gruppe {argv[1]} löschen?"):
            await api(con, "delete", f"/api/admin/groups/{g['id']}")
            con.line(f"{GREEN}gelöscht{RST}")


# ------------------------------------------------------------------ settings
@command("settings", "settings [schluessel] | settings set <schluessel> <wert>", "Server-Einstellungen anzeigen/ändern",
         perm="manage_settings", args=(["set"],))
async def _settings(con, argv):
    if argv and argv[0] == "set":
        if len(argv) < 3:
            raise UsageError("Schlüssel und Wert angeben")
        val = " ".join(argv[2:])
        try:
            val = json.loads(val)
        except ValueError:
            pass
        await api(con, "put", "/api/admin/settings", body={argv[1]: val})
        con.line(f"{GREEN}{argv[1]} gespeichert{RST}")
        return
    s = await api(con, "get", "/api/admin/settings")
    if argv:
        con.line(f"{argv[0]} = {json.dumps(s.get(argv[0]), ensure_ascii=False)}")
        return
    table(con, [[k, json.dumps(v, ensure_ascii=False)[:60]] for k, v in sorted(s.items())], ["Einstellung", "Wert"])


# ------------------------------------------------------------------ storage (Relay)
def _sections(con, done):
    try:
        return list(modules.use("relay", "relay_storage").display_names())
    except Exception:
        return ["Spaces", "Websites", "Deployment"]


def _split(spec: str) -> tuple[str, str]:
    spec = spec.strip("/")
    sec, _, path = spec.partition("/")
    return sec, path


@command("storage", "storage sections | ls [sektion/pfad] | mkdir|rm <sektion/pfad> | mv <sektion/alt> <neu>",
         "Relay-Speicher verwalten (Spaces, Websites, Deployment …)", module="relay",
         args=(["sections", "ls", "mkdir", "rm", "mv"], _sections))
async def _storage(con, argv):
    sub = (argv[0] if argv else "sections").lower()
    if sub == "sections" or (sub == "ls" and len(argv) < 2):
        res = await api(con, "get", "/api/storage/sections")
        table(con, [[s.get("name", ""), "ja" if s.get("writable") else "nein"] for s in res.get("sections", [])], ["Bereich", "schreibbar"])
        return
    if len(argv) < 2:
        raise UsageError("Pfad fehlt (sektion/pfad)")
    sec, path = _split(argv[1])
    if sub == "ls":
        res = await api(con, "get", "/api/storage/list", query={"section": sec, "path": path})
        rows = [[f"{BLUE}{e['name']}/{RST}" if e.get("is_dir") else e["name"], "" if e.get("is_dir") else str(e.get("size", ""))]
                for e in res.get("entries", [])]
        table(con, rows, ["Name", "Bytes"]) if rows else con.line(f"{GREY}(leer){RST}")
    elif sub == "mkdir":
        await api(con, "post", "/api/storage/mkdir", body={"section": sec, "path": path})
        con.line(f"{GREEN}Ordner angelegt{RST}")
    elif sub == "rm":
        if await con.confirm(f"{sec}/{path} löschen?"):
            await api(con, "post", "/api/storage/delete", body={"section": sec, "path": path})
            con.line(f"{GREEN}gelöscht{RST}")
    elif sub == "mv":
        if len(argv) < 3:
            raise UsageError("Ziel fehlt")
        await api(con, "post", "/api/storage/move", body={"section": sec, "path": path, "dst": argv[2].strip("/")})
        con.line(f"{GREEN}verschoben{RST}")
    else:
        raise UsageError(f"Unbekannt: {sub}")


# ------------------------------------------------------------------ net (Port-Weiterleitungen)
@command("net", "net fw ls | fw add <gerät> <port> [zielhost] [minuten] [erlaubt-von] | fw del <id>",
         "Netzwerk: Port-Weiterleitungen über die Agenten", module="network", args=(["fw"], ["ls", "add", "del"]))
async def _net(con, argv):
    if not argv or argv[0] != "fw":
        raise UsageError("net fw …")
    sub = (argv[1] if len(argv) > 1 else "ls").lower()
    if sub == "ls":
        res = await api(con, "get", "/api/portforward")
        rows = [[f.get("id", "")[:8], f.get("hostname") or f.get("client_id", "")[:8], f"{f.get('target_host')}:{f.get('target_port')}",
                 str(f.get("listen_port", "")), f.get("allow_from") or "alle", f.get("label") or ""] for f in res.get("forwards", [])]
        table(con, rows, ["ID", "Gerät", "Ziel", "Port (Server)", "erlaubt von", "Bezeichnung"]) if rows else con.line(f"{GREY}Keine Weiterleitungen.{RST}")
    elif sub == "add":
        if len(argv) < 4 or not argv[3].isdigit():
            raise UsageError("Gerät und Port angeben")
        c = resolve(con, argv[2])
        body = {"client_id": c["id"], "target_port": int(argv[3]), "target_host": argv[4] if len(argv) > 4 else "127.0.0.1",
                "minutes": int(argv[5]) if len(argv) > 5 else 240, "allow_from": argv[6] if len(argv) > 6 else ""}
        res = await api(con, "post", "/api/portforward", body=body)
        con.line(f"{GREEN}Weiterleitung aktiv:{RST} Server-Port {BOLD}{res.get('listen_port')}{RST} → {c['hostname']} {body['target_host']}:{body['target_port']}")
    elif sub == "del":
        if len(argv) < 3:
            raise UsageError("ID angeben")
        res = await api(con, "get", "/api/portforward")
        f = next((x for x in res.get("forwards", []) if x.get("id", "").startswith(argv[2])), None)
        if not f:
            raise CommandError("Weiterleitung nicht gefunden")
        await api(con, "delete", f"/api/portforward/{f['id']}")
        con.line(f"{GREEN}beendet{RST}")
    else:
        raise UsageError(f"Unbekannt: {sub}")


# ------------------------------------------------------------------ web (Websites)
@command("web", "web ls | public <name> on|off | rm <name>", "Websites des Webservers", module="webserver",
         args=(["ls", "public", "rm"], lambda con, d: [s["name"] for s in modules.use("webserver", "sites").visible(con.user)]))
async def _web(con, argv):
    sub = (argv[0] if argv else "ls").lower()
    if sub == "ls":
        res = await api(con, "get", "/api/webserver/sites")
        table(con, [[s["name"], s.get("title") or "", "Weiterleitung" if s.get("kind") == "proxy" else "Dateien",
                     f"{GREEN}öffentlich{RST}" if s.get("public") else f"{GREY}privat{RST}"] for s in res.get("sites", [])],
              ["Name", "Titel", "Art", "Status"])
        return
    if len(argv) < 2:
        raise UsageError("Website-Name fehlt")
    if sub == "public":
        on = (argv[2] if len(argv) > 2 else "on").lower() in ("on", "an", "ja", "1", "true")
        if on and not await con.confirm(f"/{argv[1]}/ öffentlich (ohne Anmeldung) erreichbar machen?"):
            return
        await api(con, "patch", f"/api/webserver/sites/{argv[1]}", body={"public": on})
        con.line(f"{GREEN}/{argv[1]}/ ist jetzt {'öffentlich' if on else 'privat'}{RST}")
    elif sub == "rm":
        if await con.confirm(f"Website {argv[1]} mit allen Dateien löschen?"):
            await api(con, "delete", f"/api/webserver/sites/{argv[1]}")
            con.line(f"{GREEN}gelöscht{RST}")


# ------------------------------------------------------------------ pve (wie qm / pct)
def _pve_agents(con, done):
    try:
        st = modules.use("proxmox", "pve_store")
        out = []
        for cid in st.connected_ids():
            c = db.get_client(cid) or {}
            out.append(c.get("hostname") or cid)
        return out
    except Exception:
        return []


async def _guests(con, c) -> list[dict]:
    return (await api(con, "get", f"/api/proxmox/{c['id']}/guests"))["guests"]


def _find_guest(guests, vmid: str) -> dict:
    g = next((x for x in guests if str(x.get("vmid")) == vmid or (x.get("name") or "") == vmid), None)
    if not g:
        raise CommandError(f"Gast {vmid} nicht gefunden (pve ls <agent>)")
    return g


@command("pve", "pve ls <agent> | start|stop|shutdown|reboot <agent> <vmid> | snap <agent> <vmid> <name> | enter <agent> <vmid>",
         "Proxmox wie qm/pct: Gäste auflisten, steuern, betreten (LXC: pct enter, VM: serielle Konsole)", module="proxmox",
         args=(["ls", "start", "stop", "shutdown", "reboot", "snap", "enter"], _pve_agents))
async def _pve(con, argv):
    if len(argv) < 2:
        raise UsageError("Unterbefehl und Agent angeben")
    sub, c = argv[0].lower(), resolve(con, argv[1])
    if sub == "ls":
        gs = await _guests(con, c)
        table(con, [[str(g["vmid"]), "VM" if g["type"] == "qemu" else "LXC", g.get("name") or "", g.get("node") or "",
                     f"{GREEN}{g['status']}{RST}" if g.get("status") == "running" else f"{GREY}{g.get('status')}{RST}"] for g in gs],
              ["VMID", "Art", "Name", "Node", "Status"])
        return
    if len(argv) < 3:
        raise UsageError("VMID angeben")
    g = _find_guest(await _guests(con, c), argv[2])
    base = f"/api/proxmox/{c['id']}/guests/{g['node']}/{g['type']}/{g['vmid']}"
    if sub in ("start", "stop", "shutdown", "reboot"):
        if sub == "stop" and not await con.confirm(f"{g.get('name') or g['vmid']} hart stoppen?"):
            return
        await api(con, "post", f"{base}/action/{sub}")
        con.line(f"{GREEN}{g.get('name') or g['vmid']}: {sub} ausgelöst{RST}")
    elif sub == "snap":
        if len(argv) < 4:
            raise UsageError("Snapshot-Name angeben")
        await api(con, "post", f"{base}/snapshots", body={"name": argv[3]})
        con.line(f"{GREEN}Snapshot {argv[3]} wird angelegt{RST}")
    elif sub == "enter":
        # Wie auf dem Proxmox-Node: Shell auf dem Agenten (laeuft auf dem Node) und dort pct enter / qm terminal
        from .commands import need, need_online
        need(con, c, "c_terminal")
        need_online(c)
        cmd = f"pct enter {g['vmid']}" if g["type"] == "lxc" else f"qm terminal {g['vmid']}"
        con.line(f"{GREY}Betrete {g.get('name') or g['vmid']} über {c['hostname']} ({cmd}) … Strg+] beendet{RST}")
        rs = RemoteShell(con, c)
        await rs.open()
        await asyncio.sleep(1.0)
        await rs.send(cmd + "\r")
        db.add_audit_entry(con.user["username"], "console.pve_enter", target=c["id"], details=f"{g['type']}/{g['vmid']}")
        await con.run_remote(rs)
    else:
        raise UsageError(f"Unbekannt: {sub}")


# ------------------------------------------------------------------ docker enter / logs / deploy
class DockerShell:
    """Interaktive Shell in einem Container (Ziel "local"): docker exec -it … sh/bash."""

    def __init__(self, con, cid: str):
        self.con, self.cid, self.done, self.writer, self.eid = con, cid, asyncio.Event(), None, None

    async def open(self):
        eng = modules.use("docker", "engine")
        ex = await eng.call("local", "POST", f"/containers/{self.cid}/exec", {
            "AttachStdin": True, "AttachStdout": True, "AttachStderr": True, "Tty": True, "Env": ["TERM=xterm-256color"],
            "Cmd": ["/bin/sh", "-c", "if command -v bash >/dev/null 2>&1; then exec bash; else exec sh; fi"]})
        self.eid = ex["Id"]
        reader, self.writer = await asyncio.open_unix_connection(eng.DOCKER_SOCKET)
        payload = json.dumps({"Detach": False, "Tty": True}).encode()
        self.writer.write((f"POST /exec/{self.eid}/start HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\n"
                           f"Connection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: {len(payload)}\r\n\r\n").encode() + payload)
        await self.writer.drain()
        await reader.readuntil(b"\r\n\r\n")
        self.resize(self.con.cols, self.con.rows)

        async def pump():
            try:
                while True:
                    d = await reader.read(65536)
                    if not d:
                        break
                    self.con.w(d.decode("utf-8", errors="replace"))
            finally:
                self.done.set()
        asyncio.create_task(pump())

    async def send(self, data):
        if self.writer:
            self.writer.write(data.encode())
            await self.writer.drain()

    def resize(self, cols, rows):
        eng = modules.use("docker", "engine")
        if self.eid:
            asyncio.create_task(eng.call("local", "POST", f"/exec/{self.eid}/resize?h={rows}&w={cols}"))

    def close(self):
        if self.writer:
            self.writer.close()
        self.done.set()

    async def wait(self):
        await self.done.wait()


async def _stack_container(con, argv):
    st = modules.use("docker", "stacks")
    s = next((x for x in st.visible(con.user) if x["project"] == argv[1] or x["name"] == argv[1]), None)
    if not s:
        raise CommandError(f"Stack „{argv[1]}“ nicht gefunden")
    cs = await st.containers(s)
    if len(argv) > 2:
        cs = [c for c in cs if c.get("service") == argv[2] or c["name"] == argv[2]]
    if not cs:
        raise CommandError("Container nicht gefunden")
    return s, cs[0]


_docker_base = REGISTRY["docker"]


async def _docker_ext(con, argv):
    sub = (argv[0] if argv else "ls").lower()
    if sub not in ("enter", "logs", "deploy"):
        return await _docker_base.fn(con, argv)
    if len(argv) < 2:
        raise UsageError("Stack angeben")
    st = modules.use("docker", "stacks")
    if sub == "deploy":
        s = next((x for x in st.visible(con.user) if x["project"] == argv[1] or x["name"] == argv[1]), None)
        if not s:
            raise CommandError("Stack nicht gefunden")
        res = await api(con, "post", f"/api/docker/stacks/{s['id']}/action/deploy")
        for ln in res.get("log", []):
            con.line(ln)
        return
    s, c = await _stack_container(con, argv)
    if sub == "logs":
        res = await api(con, "get", f"/api/docker/containers/{s['target']}/{c['id']}/logs", query={"tail": 100})
        con.line(res.get("log", "").rstrip())
        return
    if s["level"] < st.OPERATE:
        raise CommandError("Keine Berechtigung (Rolle „Bedienen“ nötig)")
    if s["target"] != "local":
        raise CommandError("Container betreten geht für Container auf dem RMM-Server – auf Nodes: docker logs / exec über shell")
    con.line(f"{GREY}Betrete {c['name']} … (Strg+] beendet){RST}")
    sh = DockerShell(con, c["id"])
    await sh.open()
    db.add_audit_entry(con.user["username"], "console.docker_enter", details=c["name"])
    await con.run_remote(sh)


REGISTRY["docker"] = Command("docker", _docker_ext,
                             "docker ls | ps <stack> | start|stop|restart|deploy <stack> | logs <stack> [dienst] | enter <stack> [dienst]",
                             "Docker-Stacks anzeigen, steuern, Container betreten", module="docker",
                             args=(["ls", "ps", "start", "stop", "restart", "deploy", "logs", "enter"], _docker_base.args[1]))

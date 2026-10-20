# stacks.py (Modul "docker") - Stacks (Compose-Projekte), Rechte, Bereitstellen
# ----------------------------------------------------------------------------
# Verzeichnis: data/modules/docker/stacks.json
#   {"<id>": {"name", "project", "target": "local" | "node:<client>", "compose", "members": [...],
#             "publish": [...], "created", "created_by", "updated", "deployed"}}
# Rechte:
#   manage_docker (global)   alles: alle Stacks, auch Container AUSSERHALB von Stacks, Images, Volumes, Netzwerke
#   Mitglied "viewer"        Stack/Container sehen, Logs, Statistik
#   Mitglied "operator"      + Starten/Stoppen/Neustarten, Konsole (Befehle im Container)
#   Mitglied "admin"         + Compose bearbeiten, Bereitstellen, Mitglieder, Veroeffentlichen, Entfernen
# Ziel "node:<client>" zusaetzlich nur mit Zugriff auf diesen Client.

from __future__ import annotations

import json
import secrets
import threading
import time

from app import db
from app.auth import can_access_client, is_super_admin, user_has_permission
from app.paths import MODULES_DATA_DIR

from . import compose, engine
from .engine import DockerError, q

REG = MODULES_DATA_DIR / "docker" / "stacks.json"
NONE, VIEW, OPERATE, ADMIN = 0, 1, 2, 3
ROLE = {"viewer": VIEW, "operator": OPERATE, "admin": ADMIN}
_lock = threading.RLock()


# ------------------------------------------------------------------ Verzeichnis
def _load() -> dict:
    try:
        d = json.loads(REG.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    REG.parent.mkdir(parents=True, exist_ok=True)
    tmp = REG.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(REG)


def get(sid: str) -> dict | None:
    s = _load().get(sid)
    return {"id": sid, **s} if s else None


def all_stacks() -> list[dict]:
    return [{"id": k, **v} for k, v in _load().items()]


# ------------------------------------------------------------------ Rechte
def manage_all(user) -> bool:
    return is_super_admin(user) or user_has_permission(user, "manage_docker")


def target_ok(user, target: str) -> bool:
    kind, cid = engine.parse_target(target)
    return kind == "local" or can_access_client(user, cid)


def level(user, st: dict) -> int:
    if not target_ok(user, st["target"]):
        return NONE
    if manage_all(user):
        return ADMIN
    try:
        gids = set(db.get_user_group_ids(user["id"]))
    except Exception:
        gids = set()
    lv = NONE
    for m in st.get("members") or []:
        if (m.get("type") == "user" and m.get("id") == user["id"]) or (m.get("type") == "group" and m.get("id") in gids):
            lv = max(lv, ROLE.get(m.get("role"), VIEW))
    return lv


def visible(user) -> list[dict]:
    out = []
    for st in all_stacks():
        lv = level(user, st)
        if lv:
            out.append({**st, "level": lv})
    return sorted(out, key=lambda x: x["name"].lower())


# ------------------------------------------------------------------ Aendern
def create(name: str, target: str, text: str, user: dict) -> dict:
    engine.parse_target(target)
    project = compose.project_name(name)
    data, _ = compose.parse(text)
    compose.plan(data, project, "pruefung")          # kompletten Bauplan pruefen (Ports, Volumes, Abhaengigkeiten ...)
    with _lock:
        d = _load()
        if any(v["project"] == project and v["target"] == target for v in d.values()):
            raise ValueError(f"Auf diesem Ziel gibt es schon einen Stack „{project}“")
        sid = secrets.token_hex(6)
        d[sid] = {"name": name.strip()[:80], "project": project, "target": target, "compose": text, "members": [],
                  "publish": [], "created": int(time.time()), "created_by": user["username"], "updated": int(time.time()),
                  "deployed": None}
        _save(d)
    return get(sid)


def update(sid: str, changes: dict) -> dict:
    with _lock:
        d = _load()
        if sid not in d:
            raise KeyError(sid)
        st = d[sid]
        if "compose" in changes:
            data, _ = compose.parse(changes["compose"])
            compose.plan(data, st["project"], sid)
            st["compose"] = changes["compose"]
        if "name" in changes:
            st["name"] = (changes["name"] or st["project"]).strip()[:80]
        if "members" in changes:
            st["members"] = [{"type": m["type"], "id": str(m["id"]), "role": m.get("role") if m.get("role") in ROLE else "viewer"}
                             for m in changes["members"] or [] if m.get("type") in ("user", "group") and m.get("id")]
        if "publish" in changes:
            st["publish"] = changes["publish"]
        for k in ("deployed",):
            if k in changes:
                st[k] = changes[k]
        st["updated"] = int(time.time())
        _save(d)
    return get(sid)


def remove_entry(sid: str) -> None:
    with _lock:
        d = _load()
        d.pop(sid, None)
        _save(d)


# ------------------------------------------------------------------ Docker
def _filters(sid: str) -> str:
    return q(json.dumps({"label": [f"rmm.stack={sid}"]}))


async def containers(st: dict) -> list[dict]:
    lst = await engine.call(st["target"], "GET", f"/containers/json?all=1&filters={_filters(st['id'])}")
    return [{"id": c["Id"][:12], "name": (c.get("Names") or ["?"])[0].lstrip("/"), "image": c.get("Image"),
             "state": c.get("State"), "status": c.get("Status"), "service": (c.get("Labels") or {}).get("com.docker.compose.service"),
             "ports": c.get("Ports") or [], "created": c.get("Created")} for c in lst or []]


async def _image_present(target: str, image: str) -> bool:
    try:
        await engine.call(target, "GET", f"/images/{q(image)}/json", timeout=15)
        return True
    except DockerError:
        return False


async def pull(target: str, image: str, log: list) -> None:
    name, tag = image, "latest"
    if "@" not in image and ":" in image.rsplit("/", 1)[-1]:
        name, tag = image.rsplit(":", 1)
    try:
        await engine.call(target, "POST", f"/images/create?fromImage={q(name)}&tag={q(tag)}", timeout=900)
        log.append(f"✔ Image {image} geladen")
    except DockerError as e:
        if await _image_present(target, image):
            log.append(f"⚠ Image {image}: neu laden fehlgeschlagen ({e}) – vorhandenes wird benutzt")
        else:
            raise DockerError(f"Image {image} konnte nicht geladen werden: {e}")


async def deploy(st: dict, pull_images: bool = True) -> list[str]:
    """Stack anlegen/aktualisieren (vorhandene Container des Stacks werden neu erstellt)."""
    data, notes = compose.parse(st["compose"])
    pl = compose.plan(data, st["project"], st["id"])
    from . import publish
    publish.inject(st, pl)                          # Port-Freigaben ("eigener Port") eintragen
    t = st["target"]
    log = [f"ℹ {n}" for n in notes]
    for img in sorted({c["image"] for c in pl["containers"]}):
        if pull_images or not await _image_present(t, img):
            await pull(t, img, log)
    for net in pl["networks"]:
        try:
            await engine.call(t, "GET", f"/networks/{q(net)}")
        except DockerError:
            await engine.call(t, "POST", "/networks/create", {"Name": net, "Labels": {
                "com.docker.compose.project": st["project"], "rmm.stack": st["id"]}})
            log.append(f"✔ Netzwerk {net} angelegt")
    for vol in pl["volumes"]:
        try:
            await engine.call(t, "GET", f"/volumes/{q(vol)}")
        except DockerError:
            await engine.call(t, "POST", "/volumes/create", {"Name": vol, "Labels": {
                "com.docker.compose.project": st["project"], "rmm.stack": st["id"]}})
            log.append(f"✔ Volume {vol} angelegt")
    for c in pl["containers"]:
        try:
            await engine.call(t, "DELETE", f"/containers/{q(c['name'])}?force=1")
            log.append(f"↻ {c['name']} wird neu erstellt")
        except DockerError:
            pass
        res = await engine.call(t, "POST", f"/containers/create?name={q(c['name'])}", c["config"])
        for extra in c["extra_networks"]:
            await engine.call(t, "POST", f"/networks/{q(extra)}/connect", {"Container": res["Id"],
                              "EndpointConfig": {"Aliases": [c["service"]]}})
        await engine.call(t, "POST", f"/containers/{res['Id']}/start")
        log.append(f"✔ {c['name']} gestartet")
    update(st["id"], {"deployed": int(time.time())})
    return log


async def action(st: dict, act: str) -> list[str]:
    lst = await containers(st)
    if act == "stop":
        lst = list(reversed(lst))
    out = []
    for c in lst:
        try:
            await engine.call(st["target"], "POST", f"/containers/{c['id']}/{act}", timeout=120)
            out.append(f"✔ {c['name']}: {act}")
        except DockerError as e:
            out.append(f"⚠ {c['name']}: {e}")
    return out


async def down(st: dict, volumes: bool = False) -> list[str]:
    t, out = st["target"], []
    for c in await containers(st):
        try:
            await engine.call(t, "DELETE", f"/containers/{c['id']}?force=1&v={1 if volumes else 0}")
            out.append(f"✔ {c['name']} entfernt")
        except DockerError as e:
            out.append(f"⚠ {c['name']}: {e}")
    flt = _filters(st["id"])
    for n in await engine.call(t, "GET", f"/networks?filters={flt}") or []:
        try:
            await engine.call(t, "DELETE", f"/networks/{n['Id']}")
            out.append(f"✔ Netzwerk {n['Name']} entfernt")
        except DockerError:
            pass
    if volumes:
        res = await engine.call(t, "GET", f"/volumes?filters={flt}") or {}
        for v in res.get("Volumes") or []:
            try:
                await engine.call(t, "DELETE", f"/volumes/{q(v['Name'])}")
                out.append(f"✔ Volume {v['Name']} entfernt")
            except DockerError as e:
                out.append(f"⚠ Volume {v['Name']}: {e}")
    return out

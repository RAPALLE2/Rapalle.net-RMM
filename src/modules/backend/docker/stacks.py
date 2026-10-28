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

from pathlib import Path as _Path

from app.paths import DOCKER_PROJECTS_DIR

from . import compose, engine


def project_dir(project: str) -> _Path:
    """storage/docker/<projektname> - der Projekt-Root eines Stacks."""
    d = DOCKER_PROJECTS_DIR / project
    return d


def compose_text(st: dict) -> str:
    """Compose-Inhalt eines Stacks je nach Quelle:
       source == "file": aus <project_dir>/<compose_file> lesen (Standard docker-compose.yml);
       sonst: der gespeicherte Inline-Text ("eigene compose")."""
    if st.get("source") == "file":
        root = project_dir(st["project"])
        fname = st.get("compose_file") or "docker-compose.yml"
        f = (root / fname)
        try:
            f.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            raise ValueError("Compose-Datei liegt außerhalb des Projektordners")
        if not f.is_file():
            raise ValueError(f"Compose-Datei nicht gefunden: {root.name}/{fname}")
        return f.read_text(encoding="utf-8")
    return st.get("compose", "")
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
def create(name: str, target: str, text: str, user: dict, source: str = "inline",
           compose_file: str = "docker-compose.yml") -> dict:
    engine.parse_target(target)
    project = compose.project_name(name)
    source = "file" if source == "file" else "inline"
    # Projektordner immer anlegen (storage/docker/<projekt>) - dient als Projekt-Root
    # fuer relative Volumes und, bei Quelle "file", fuer die Compose-Datei.
    root = project_dir(project)
    root.mkdir(parents=True, exist_ok=True)
    if source == "inline":
        text_eff = text
    else:
        f = root / (compose_file or "docker-compose.yml")
        if text and text.strip():          # mitgelieferten Text als Datei anlegen, falls noch keine da
            f.parent.mkdir(parents=True, exist_ok=True)
            if not f.is_file():
                f.write_text(text, encoding="utf-8")
        if not f.is_file():
            raise ValueError(f"Lege zuerst eine Compose-Datei an: storage/docker/{project}/{compose_file or 'docker-compose.yml'}")
        text_eff = f.read_text(encoding="utf-8")
    data, _ = compose.parse(text_eff)
    compose.plan(data, project, "pruefung",
                 project_root=str(root) if target == "local" else None)  # Bauplan + Pfade pruefen
    with _lock:
        d = _load()
        if any(v["project"] == project and v["target"] == target for v in d.values()):
            raise ValueError(f"Auf diesem Ziel gibt es schon einen Stack „{project}“")
        sid = secrets.token_hex(6)
        d[sid] = {"name": name.strip()[:80], "project": project, "target": target,
                  "source": source, "compose_file": (compose_file or "docker-compose.yml"),
                  "compose": text if source == "inline" else "", "members": [],
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
        if "source" in changes and changes["source"] in ("inline", "file"):
            st["source"] = changes["source"]
        if "compose_file" in changes and changes["compose_file"]:
            st["compose_file"] = str(changes["compose_file"]).strip()
        if "compose" in changes:
            if st.get("source") == "file":
                # Inhalt in die Datei im Projektordner schreiben
                root = project_dir(st["project"]); root.mkdir(parents=True, exist_ok=True)
                f = root / (st.get("compose_file") or "docker-compose.yml")
                try:
                    f.resolve().relative_to(root.resolve())
                except (ValueError, OSError):
                    raise ValueError("Compose-Datei liegt außerhalb des Projektordners")
                data, _ = compose.parse(changes["compose"])
                compose.plan(data, st["project"], sid)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(changes["compose"], encoding="utf-8")
            else:
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
    text = compose_text(st)
    data, notes = compose.parse(text)
    # Relative Volumes gegen den Projekt-Root aufloesen (nur sinnvoll fuer das lokale Ziel).
    root = str(project_dir(st["project"])) if st.get("target") == "local" else None
    pl = compose.plan(data, st["project"], st["id"], project_root=root)
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

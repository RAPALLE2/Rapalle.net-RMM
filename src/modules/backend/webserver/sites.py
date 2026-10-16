# sites.py (Modul "webserver") - Websites, Rechte, oeffentliche Adressen
# ----------------------------------------------------------------------
# Website = Ordner backend/storage/websites/<name>/, oeffentlich unter rmm.url.de/<name>
# (nur wenn "oeffentlich" an ist - sonst 404, wie beim Deployment).
#
# Verzeichnis: data/modules/webserver/sites.json
#   {"<name>": {"title", "public", "listing", "members": [{"type": user|group, "id", "role": editor|admin}],
#               "created", "created_by"}}
# Rechte:
#   manage_websites (global)  Websites anlegen/loeschen, ALLE Websites bearbeiten
#   Mitglied "editor"         Dateien der Website bearbeiten (App "Webserver", Explorer/Relay "Websites")
#   Mitglied "admin"          zusaetzlich Einstellungen + Mitglieder der Website
#
# Adressen: Jede Website bekommt ihre EIGENE feste Route (/<name> und /<name>/...),
# eingehaengt VOR dem Dashboard. Namen, die das Dashboard oder andere Module schon
# belegen (api, css, js, deployment, dav ...), sind gesperrt.

from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from app import db
from app.auth import is_super_admin, user_has_permission
from app.paths import MODULES_DATA_DIR, STORAGE_DIR

from . import webhost

ROOT = STORAGE_DIR / "websites"
REG = MODULES_DATA_DIR / "webserver" / "sites.json"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
# Immer gesperrt (zusaetzlich zu allen belegten Adressen, siehe reserved())
RESERVED = {"api", "dav", "deployment", "agent-dist", "client-dist", "modules", "css", "js", "image", "images",
            "socket.io", "ws", "docs", "redoc", "openapi.json", "static", "assets", "favicon.ico", "index.html",
            "login", "logout", "admin", "dashboard", "robots.txt", "health", "metrics", "relay", "websites"}
NONE, EDIT, ADMIN = 0, 1, 2
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


def all_sites() -> dict:
    return _load()


def get(name: str) -> dict | None:
    s = _load().get(name)
    return {"name": name, **s} if s else None


def folder(name: str) -> Path:
    p = ROOT / name
    p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------------ gesperrte Namen
def reserved() -> set[str]:
    """Alle ersten Pfadteile, die schon belegt sind (Dashboard-Dateien, API, Module ...)."""
    out = set(RESERVED)
    try:
        from app.paths import FRONTEND_DIR
        out |= {p.name.lower() for p in Path(FRONTEND_DIR).iterdir()}
    except Exception:
        pass
    try:
        from app import modules
        for r in modules._api.router.routes:
            path = getattr(r, "path", "") or ""
            first = path.strip("/").split("/")[0].lower()
            if first and not first.startswith("{") and first not in _load():
                out.add(first)
    except Exception:
        pass
    return out


def check_name(name: str) -> str:
    name = (name or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("Name: 2–63 Zeichen, nur a–z, 0–9 und Bindestrich, nicht mit Bindestrich beginnen")
    if name in reserved():
        raise ValueError(f"„{name}“ ist schon belegt (Dashboard, Schnittstelle oder anderes Modul)")
    return name


# ------------------------------------------------------------------ Rechte
def _groups(user) -> set[str]:
    try:
        return set(db.get_user_group_ids(user["id"]))
    except Exception:
        return set()


def manage_all(user) -> bool:
    return is_super_admin(user) or user_has_permission(user, "manage_websites")


def level(user, site: dict) -> int:
    if manage_all(user):
        return ADMIN
    gids = _groups(user)
    lv = NONE
    for m in site.get("members") or []:
        if (m.get("type") == "user" and m.get("id") == user["id"]) or (m.get("type") == "group" and m.get("id") in gids):
            lv = max(lv, ADMIN if m.get("role") == "admin" else EDIT)
    return lv


def visible(user) -> list[dict]:
    out = []
    for name, s in _load().items():
        lv = level(user, {"name": name, **s})
        if lv:
            out.append({"name": name, **s, "level": lv})
    return sorted(out, key=lambda x: x["name"])


# ------------------------------------------------------------------ Aendern
def create(name: str, title: str, user: dict) -> dict:
    name = check_name(name)
    with _lock:
        d = _load()
        if name in d:
            raise ValueError(f"„{name}“ gibt es schon")
        d[name] = {"title": (title or name).strip()[:80], "public": False, "listing": True, "members": [],
                   "created": int(time.time()), "created_by": user["username"]}
        _save(d)
    f = folder(name)
    if not any(f.iterdir()):
        (f / "index.html").write_text(
            f"<!doctype html><meta charset=utf-8><title>{d[name]['title']}</title>"
            f"<h1>{d[name]['title']}</h1><p>Diese Website wird mit dem RAPALLE.net RMM betrieben.</p>\n", encoding="utf-8")
    sync_routes()
    return get(name)


def update(name: str, changes: dict) -> dict:
    with _lock:
        d = _load()
        if name not in d:
            raise KeyError(name)
        s = d[name]
        if "title" in changes:
            s["title"] = (changes["title"] or name).strip()[:80]
        for k in ("public", "listing"):
            if k in changes:
                s[k] = bool(changes[k])
        if "members" in changes:
            mem = []
            for m in changes["members"] or []:
                if m.get("type") in ("user", "group") and m.get("id"):
                    mem.append({"type": m["type"], "id": str(m["id"]), "role": "admin" if m.get("role") == "admin" else "editor"})
            s["members"] = mem
        new = (changes.get("new_name") or "").strip().lower()
        if new and new != name:
            check_name(new)
            if new in d:
                raise ValueError(f"„{new}“ gibt es schon")
            d[new] = d.pop(name)
            if (ROOT / name).exists():
                (ROOT / name).rename(ROOT / new)
            name = new
        _save(d)
    sync_routes()
    return get(name)


def remove(name: str) -> None:
    with _lock:
        d = _load()
        if d.pop(name, None) is None:
            raise KeyError(name)
        _save(d)
    shutil.rmtree(ROOT / name, ignore_errors=True)
    sync_routes()


# ------------------------------------------------------------------ oeffentliche Routen
_site_routes: list = []


def _handler(name: str):
    async def handler(path: str = ""):
        s = get(name)
        if not s or not s.get("public"):
            from starlette.responses import PlainTextResponse
            return PlainTextResponse("Diese Website ist nicht freigegeben.", status_code=404)
        return webhost.serve(folder(name), [p for p in path.split("/") if p], f"/{name}",
                             title=s.get("title") or name, listing=bool(s.get("listing", True)))
    return handler


def sync_routes() -> None:
    """Routen aller Websites (neu) einhaengen - VOR dem Dashboard."""
    try:
        from app import modules
        api = modules._api
    except Exception:
        return
    if api is None:
        return
    from fastapi.routing import APIRoute
    from starlette.responses import RedirectResponse
    routes = api.router.routes
    for r in list(_site_routes):
        if r in routes:
            routes.remove(r)
    _site_routes.clear()
    new = []
    for name in _load():
        h = _handler(name)

        async def to_slash(_n=name):
            return RedirectResponse(f"/{_n}/", status_code=308)
        new.append(APIRoute(f"/{name}", endpoint=to_slash, methods=["GET", "HEAD"], include_in_schema=False))
        new.append(APIRoute(f"/{name}/{{path:path}}", endpoint=h, methods=["GET", "HEAD"], include_in_schema=False))
    if new:
        modules._place_before_static(new)
    else:
        modules._routes_changed()
    _site_routes.extend(new)


def unmount_routes() -> None:
    try:
        from app import modules
        routes = modules._api.router.routes
        for r in list(_site_routes):
            if r in routes:
                routes.remove(r)
        _site_routes.clear()
        modules._routes_changed()
    except Exception:
        pass

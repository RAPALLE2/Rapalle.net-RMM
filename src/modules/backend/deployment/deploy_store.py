# deploy_store.py (Modul "deployment")
# ------------------------------------
# Der Deployment-Ordner (backend/storage/deployment) und seine Einstellungen.
# Was hier liegt, ist bei eingeschalteter Freigabe OHNE Anmeldung unter
# /deployment/<datei> abrufbar - genau dafuer ist der Ordner gedacht
# (Installer, Skripte, Logos per Link weitergeben).
#
# Die Einstellungen liegen wie frueher in der DB (deployment_html/_public/_title),
# dadurch bleibt eine bestehende Konfiguration beim Umzug ins Modul erhalten.

from __future__ import annotations

import shutil
from pathlib import Path

from app import db
from app.paths import STORAGE_DIR

DIR = STORAGE_DIR / "deployment"
SETTING_HTML = "deployment_html"
SETTING_PUBLIC = "deployment_public"
SETTING_TITLE = "deployment_title"


def base() -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    return DIR


def is_public() -> bool:
    raw = db.get_setting(SETTING_PUBLIC)
    return str(raw or "0").strip().lower() in ("1", "true", "yes", "on")


def settings() -> dict:
    return {"html": db.get_setting(SETTING_HTML) or "",
            "title": db.get_setting(SETTING_TITLE) or "Deployment",
            "public": is_public()}


def resolve(sub: list[str] | str) -> Path:
    """Pfad im Deployment-Ordner - kann ihn nie verlassen."""
    if isinstance(sub, str):
        sub = sub.replace("\\", "/").split("/")
    root = base().resolve()
    parts = []
    for seg in sub:
        seg = (seg or "").strip()
        if seg in ("", "."):
            continue
        if seg == ".." or "/" in seg or "\\" in seg or seg.startswith("~") or "\x00" in seg:
            raise PermissionError("Ungültiger Name")
        parts.append(seg)
    p = (root / Path(*parts)).resolve() if parts else root
    try:
        p.relative_to(root)
    except ValueError:
        raise PermissionError("Pfad außerhalb des Ordners")
    return p


def entry(p: Path) -> dict:
    try:
        st = p.stat()
        d = p.is_dir()
        return {"name": p.name, "is_dir": d, "size": 0 if d else int(st.st_size), "mtime": int(st.st_mtime * 1000)}
    except OSError:
        return {"name": p.name, "is_dir": False, "size": 0, "mtime": 0}


def listdir(sub) -> list[dict]:
    p = resolve(sub)
    if not p.is_dir():
        raise FileNotFoundError(p.name)
    return [entry(c) for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))]


def write(sub, data: bytes) -> None:
    p = resolve(sub)
    if p == base().resolve():
        raise PermissionError("Ungültiges Ziel")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data or b"")


def mkdir(sub) -> None:
    resolve(sub).mkdir(parents=True, exist_ok=True)


def delete(sub) -> None:
    p = resolve(sub)
    if p == base().resolve():
        raise PermissionError("Der Hauptordner kann nicht gelöscht werden")
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    else:
        raise FileNotFoundError(p.name)


def move(src, dst) -> None:
    a, b = resolve(src), resolve(dst)
    if a == base().resolve():
        raise PermissionError("Der Hauptordner kann nicht verschoben werden")
    if b.exists():
        raise FileExistsError(b.name)
    b.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(a), str(b))

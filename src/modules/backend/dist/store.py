# store.py (Modul "dist") - einheitliche Verteil-Ordner unter backend/dist
# -----------------------------------------------------------------------
# Auf der Platte: backend/dist/<ordner>/...   (ersetzt die alten Einzel-Systeme)
#   enrollment/  frei hochgeladene Installer/Dateien fuer Clients (exe, msi, scripts ...)
#   agent/       Agent-Pakete
#   client/      Client-App-Pakete
#   ... weitere Ordner frei anlegbar
# Je Ordner eine .dist.json: {"public": bool, "label": str}. Standard: public = True.
# Oeffentlich erreichbar unter  rmm.url.de/dist/<ordner>/<datei>  (wenn public).
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from app.paths import DIST_DIR  # storage/dist
META = ".dist.json"
# Diese Ordner gibt es immer (werden bei Bedarf angelegt).
CORE_FOLDERS = {
    "enrollment": "Enrollment (Installer & Dateien für Clients)",
    "agent": "Agent-Pakete",
    "client": "Client-App-Pakete",
}
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9 _.-]{0,48}$")


def ensure_dirs() -> None:
    for name, label in CORE_FOLDERS.items():
        d = DIST_DIR / name
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
        m = d / META
        if not m.exists():
            _write_meta(d, {"public": True, "label": label})


def _write_meta(folder: Path, meta: dict) -> None:
    (folder / META).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _read_meta(folder: Path) -> dict:
    try:
        m = json.loads((folder / META).read_text(encoding="utf-8"))
    except Exception:
        m = {}
    return {"public": bool(m.get("public", True)),
            "label": m.get("label") or CORE_FOLDERS.get(folder.name, folder.name)}


def valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or "")) and "/" not in name and "\\" not in name and ".." not in name


def folders() -> list[dict]:
    ensure_dirs()
    out = []
    for d in sorted(p for p in DIST_DIR.iterdir() if p.is_dir()):
        meta = _read_meta(d)
        files = [f for f in d.iterdir() if f.is_file() and f.name != META]
        out.append({"name": d.name, "label": meta["label"], "public": meta["public"],
                    "files": len(files), "size": sum(f.stat().st_size for f in files),
                    "core": d.name in CORE_FOLDERS})
    return out


def folder_path(name: str) -> Path | None:
    if not valid_name(name):
        return None
    d = DIST_DIR / name
    return d if d.is_dir() else None


def is_public(name: str) -> bool:
    d = folder_path(name)
    return bool(d and _read_meta(d)["public"])


def meta(name: str) -> dict:
    d = folder_path(name)
    return _read_meta(d) if d else {"public": False, "label": name}


def set_meta(name: str, public: bool | None = None, label: str | None = None) -> None:
    d = folder_path(name)
    if not d:
        return
    m = _read_meta(d)
    if public is not None:
        m["public"] = bool(public)
    if label is not None:
        m["label"] = label.strip()[:60] or m["label"]
    _write_meta(d, m)


def create_folder(name: str, label: str = "", public: bool = True) -> bool:
    if not valid_name(name):
        return False
    d = DIST_DIR / name
    d.mkdir(parents=True, exist_ok=True)
    _write_meta(d, {"public": public, "label": label.strip()[:60] or name})
    return True


def delete_folder(name: str) -> bool:
    if name in CORE_FOLDERS:
        return False
    d = folder_path(name)
    if not d:
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def listdir(name: str) -> list[dict]:
    d = folder_path(name)
    if not d:
        return []
    out = []
    for f in sorted(d.iterdir()):
        if f.name == META or not f.is_file():
            continue
        st = f.stat()
        out.append({"name": f.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    return out


def file_path(name: str, filename: str) -> Path | None:
    d = folder_path(name)
    if not d or not filename or "/" in filename or "\\" in filename or filename.startswith(".") or ".." in filename:
        return None
    p = (d / filename).resolve()
    try:
        p.relative_to(d.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def save_file(name: str, filename: str, data: bytes) -> bool:
    d = folder_path(name) or (DIST_DIR / name if valid_name(name) else None)
    if d is None:
        return False
    d.mkdir(parents=True, exist_ok=True)
    safe = Path(filename).name
    if not safe or safe == META:
        return False
    (d / safe).write_bytes(data)
    return True


def delete_file(name: str, filename: str) -> bool:
    p = file_path(name, filename)
    if not p:
        return False
    p.unlink()
    return True

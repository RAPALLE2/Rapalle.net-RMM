# site_vfs.py (Modul "webserver") - Bereich "Websites" im Relay/Explorer/Netzlaufwerk
# ---------------------------------------------------------------------------------
# Wurzel: je eine Website, an der der Benutzer mitarbeiten darf (Mitglied oder
# manage_websites). Darunter der Ordner der Website (= src der Website).
# Gleiche Schnittstelle wie spaces/vfs.py, damit relay_storage beide gleich behandelt:
#   listdir stat file_path read write mkdir delete move   (user, parts)

from __future__ import annotations

from pathlib import Path

from . import sites, webhost


def _vdir(name: str) -> dict:
    return {"name": name, "is_dir": True, "size": 0, "mtime": 0, "writable": False}


def _split(user, parts: list[str]) -> tuple[dict, Path, list[str]]:
    clean = [p for p in parts if p not in ("", ".")]
    if not clean:
        raise IsADirectoryError("Wurzel")
    s = sites.get(clean[0])
    if not s or not sites.level(user, s) or s.get("kind") == "proxy":
        raise FileNotFoundError(clean[0])
    return s, sites.folder(s["name"]), clean[1:]


def listdir(user, parts: list[str]) -> list[dict]:
    clean = [p for p in parts if p not in ("", ".")]
    if not clean:
        return [_vdir(s["name"]) for s in sites.visible(user) if s.get("kind") != "proxy"]
    s, root, rest = _split(user, parts)
    return [e for e in webhost.listdir(root, rest)]


def stat(user, parts: list[str]) -> dict | None:
    clean = [p for p in parts if p not in ("", ".")]
    if not clean:
        return _vdir("Websites")
    try:
        s, root, rest = _split(user, parts)
        p = webhost.resolve(root, rest)
    except (FileNotFoundError, PermissionError):
        return None
    if not p.exists():
        return None
    e = webhost.entry(p)
    if not rest:
        e["name"] = s["name"]
    e["writable"] = True
    return e


def file_path(user, parts: list[str]) -> Path:
    s, root, rest = _split(user, parts)
    p = webhost.resolve(root, rest)
    if not p.is_file():
        raise FileNotFoundError(p.name)
    return p


def read(user, parts: list[str]) -> bytes:
    return file_path(user, parts).read_bytes()


def write(user, parts: list[str], data: bytes) -> None:
    s, root, rest = _split(user, parts)
    if not rest:
        raise PermissionError("Hier kann nichts abgelegt werden")
    webhost.write(root, rest, data)


def mkdir(user, parts: list[str]) -> None:
    clean = [p for p in parts if p not in ("", ".")]
    if len(clean) == 1:
        raise PermissionError("Neue Websites legst du in der App „Webserver“ an")
    s, root, rest = _split(user, parts)
    webhost.mkdir(root, rest)


def delete(user, parts: list[str]) -> None:
    s, root, rest = _split(user, parts)
    if not rest:
        raise PermissionError("Ganze Websites löschst du in der App „Webserver“")
    webhost.delete(root, rest)


def move(user, src: list[str], dst: list[str]) -> None:
    s1, root1, rest1 = _split(user, src)
    s2, root2, rest2 = _split(user, dst)
    if s1["name"] != s2["name"]:
        raise PermissionError("Verschieben zwischen Websites ist nicht möglich")
    if not rest1 or not rest2:
        raise PermissionError("Websites umbenennen geht in der App „Webserver“")
    webhost.move(root1, rest1, rest2)

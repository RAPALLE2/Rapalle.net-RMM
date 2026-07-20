"""
routers/files_routes.py
--------------------------
File Station für den Backend-Server SELBST (also "dieser Server" in der
File-Explorer-App). Für Remote-Clients läuft das Browsing stattdessen über
den Agenten (siehe clients_routes.py -> /api/clients/{id}/fs).

Endpunkte:
  GET  /api/server-files            -> Ordnerinhalt des Backend-Rechners
  GET  /api/server-files/read       -> Datei base64 lesen (Download / Bild / Edit)
  POST /api/server-files/write      -> Datei hochladen / editiert zurückschreiben
  POST /api/server-files/mkdir      -> Ordner anlegen
  POST /api/server-files/delete     -> Datei/Ordner löschen
  POST /api/server-files/rename     -> umbenennen / verschieben
"""

import base64
import os
import platform
import shutil
import stat as _stat
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

router = APIRouter(prefix="/api/server-files", tags=["files"])
IS_WINDOWS = platform.system() == "Windows"
MAX_READ = 25 * 1024 * 1024


# ------------------------------------------------------------------
# Rechte/Besitzer im "ls -al"-Stil (identisch zum Agenten)
# ------------------------------------------------------------------
def _perm_string(mode: int, is_dir: bool, is_link: bool = False) -> str:
    if is_link:
        tc = "l"
    elif is_dir:
        tc = "d"
    elif _stat.S_ISCHR(mode):
        tc = "c"
    elif _stat.S_ISBLK(mode):
        tc = "b"
    elif _stat.S_ISFIFO(mode):
        tc = "p"
    elif _stat.S_ISSOCK(mode):
        tc = "s"
    else:
        tc = "-"
    perms = ""
    for r, w, x in (
        (_stat.S_IRUSR, _stat.S_IWUSR, _stat.S_IXUSR),
        (_stat.S_IRGRP, _stat.S_IWGRP, _stat.S_IXGRP),
        (_stat.S_IROTH, _stat.S_IWOTH, _stat.S_IXOTH),
    ):
        perms += "r" if mode & r else "-"
        perms += "w" if mode & w else "-"
        perms += "x" if mode & x else "-"
    return tc + perms


def _owner_group(st) -> tuple[str, str]:
    uid = getattr(st, "st_uid", 0)
    gid = getattr(st, "st_gid", 0)
    owner, group = str(uid), str(gid)
    if not IS_WINDOWS:
        try:
            import pwd
            owner = pwd.getpwuid(uid).pw_name
        except Exception:
            pass
        try:
            import grp
            group = grp.getgrgid(gid).gr_name
        except Exception:
            pass
    return owner, group


def _entry_meta(full_path: str, is_dir: bool) -> dict:
    meta = {"size": 0, "mtime": 0, "perms": "", "owner": "", "group": "",
            "mode": "", "is_link": False}
    try:
        is_link = os.path.islink(full_path)
        st = os.lstat(full_path) if is_link else os.stat(full_path)
        meta["size"] = st.st_size
        meta["mtime"] = int(st.st_mtime * 1000)
        meta["is_link"] = is_link
        meta["perms"] = _perm_string(st.st_mode, is_dir, is_link)
        meta["mode"] = oct(_stat.S_IMODE(st.st_mode))[-3:]
        o, g = _owner_group(st)
        meta["owner"], meta["group"] = o, g
    except Exception:
        pass
    return meta


def _list_windows_drives() -> list[dict]:
    """Ermittelt alle Laufwerke unter Windows (lokal + Netzlaufwerke) via psutil."""
    import psutil
    drives = []
    try:
        for part in psutil.disk_partitions(all=True):
            mount = part.mountpoint
            if not mount:
                continue
            is_network = "remote" in (part.opts or "").lower() or part.fstype == ""
            try:
                size = psutil.disk_usage(mount).total
            except Exception:
                size = 0
            label = mount.rstrip("\\") + ("  (Netzlaufwerk)" if is_network else "")
            drives.append({
                "name": label,
                "path": mount if mount.endswith("\\") else mount + "\\",
                "isDir": True, "size": size, "mtime": 0,
            })
    except Exception:
        pass
    return drives or [{"name": "C:", "path": "C:\\", "isDir": True, "size": 0, "mtime": 0}]


@router.get("")
async def list_dir(path: str = "", user: dict = Depends(get_current_user)):
    require_perm(user, "see_source")
    # Kein Pfad angegeben -> "Wurzel"-Ansicht zeigen (Laufwerke bzw. / und Home)
    if not path:
        if IS_WINDOWS:
            return {"path": "", "entries": _list_windows_drives()}
        home = str(Path.home())
        return {
            "path": "",
            "entries": [
                {"name": "/ (Root)", "path": "/", "isDir": True, "size": 0, "mtime": 0},
                {"name": f"Home ({home})", "path": home, "isDir": True, "size": 0, "mtime": 0},
            ],
        }

    try:
        entries = []
        with os.scandir(path) as it:
            for entry in it:
                try:
                    is_dir = entry.is_dir()
                except Exception:
                    is_dir = False
                meta = _entry_meta(entry.path, is_dir)
                entries.append({
                    "name": entry.name,
                    "path": os.path.join(path, entry.name),
                    "isDir": is_dir,
                    **meta,
                })
        entries.sort(key=lambda e: (not e["isDir"], e["name"].lower()))
        return {"path": path, "entries": entries}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.get("/read")
async def read_file(path: str, user: dict = Depends(get_current_user)):
    """Liest eine Datei base64-kodiert (Download, Bild-Vorschau, Editor)."""
    require_perm(user, "see_source")
    try:
        size = os.path.getsize(path)
        if size > MAX_READ:
            raise HTTPException(413, f"Datei zu groß ({size} Bytes, max. {MAX_READ})")
        with open(path, "rb") as f:
            content = f.read()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    db.add_audit_entry(user["username"], "server.file.download", details=path)
    return {"name": os.path.basename(path),
            "data": base64.b64encode(content).decode("ascii")}


class WriteBody(BaseModel):
    path: str
    data: str   # base64


class PathBody(BaseModel):
    path: str


class RenameBody(BaseModel):
    src: str
    dst: str


@router.post("/write")
async def write_file(body: WriteBody, user: dict = Depends(get_current_user)):
    require_perm(user, "edit_source")
    try:
        content = base64.b64decode(body.data)
        os.makedirs(os.path.dirname(body.path) or ".", exist_ok=True)
        with open(body.path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(400, str(e))
    db.add_audit_entry(user["username"], "server.file.upload", details=body.path)
    return {"ok": True, "path": body.path, "size": len(content)}


@router.post("/mkdir")
async def mkdir(body: PathBody, user: dict = Depends(get_current_user)):
    require_perm(user, "edit_source")
    try:
        os.makedirs(body.path, exist_ok=False)
    except Exception as e:
        raise HTTPException(400, str(e))
    db.add_audit_entry(user["username"], "server.file.mkdir", details=body.path)
    return {"ok": True, "path": body.path}


@router.post("/delete")
async def delete_path(body: PathBody, user: dict = Depends(get_current_user)):
    require_perm(user, "delete_source")
    try:
        if os.path.isdir(body.path) and not os.path.islink(body.path):
            shutil.rmtree(body.path)
        else:
            os.remove(body.path)
    except Exception as e:
        raise HTTPException(400, str(e))
    db.add_audit_entry(user["username"], "server.file.delete", details=body.path)
    return {"ok": True, "path": body.path}


@router.post("/rename")
async def rename_path(body: RenameBody, user: dict = Depends(get_current_user)):
    require_perm(user, "edit_source")
    try:
        os.rename(body.src, body.dst)
    except Exception as e:
        raise HTTPException(400, str(e))
    db.add_audit_entry(user["username"], "server.file.rename", details=f"{body.src} -> {body.dst}")
    return {"ok": True, "src": body.src, "dst": body.dst}

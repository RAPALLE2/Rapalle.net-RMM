"""
routers/files_routes.py
--------------------------
File Station für den Backend-Server SELBST (also "dieser Server" in der
File-Explorer-App). Für Remote-Clients läuft das Browsing stattdessen über
den Agenten (siehe clients_routes.py -> /api/clients/{id}/fs).

Endpunkt:
  GET /api/server-files    -> Ordnerinhalt des Backend-Rechners auflisten
"""

import os
import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user

router = APIRouter(prefix="/api/server-files", tags=["files"])
IS_WINDOWS = platform.system() == "Windows"


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
                    stat = entry.stat()
                    size, mtime = stat.st_size, int(stat.st_mtime * 1000)
                except Exception:
                    size, mtime = 0, 0
                entries.append(
                    {
                        "name": entry.name,
                        "path": os.path.join(path, entry.name),
                        "isDir": entry.is_dir(),
                        "size": size,
                        "mtime": mtime,
                    }
                )
        # Ordner zuerst, dann alphabetisch
        entries.sort(key=lambda e: (not e["isDir"], e["name"].lower()))
        return {"path": path, "entries": entries}
    except Exception as e:
        raise HTTPException(400, str(e))

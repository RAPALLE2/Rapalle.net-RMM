# build_routes.py (Modul "client") - Client-Pakete auf dem Server neu bauen
# --------------------------------------------------------------------------
# Startet src/client/build_installers.py als eigenen Prozess (Hintergrund) und
# legt die Pakete nach backend/storage/client/ - dort haben sie Vorrang vor den
# mitgelieferten aus src/client/release und ueberleben Server-Updates.
#
#   GET  /api/client/build          Stand: laeuft?, Log, Ergebnis, Werkzeuge, aktuelle Version
#   POST /api/client/build          {mode: "replace" | "new", version?: "1.2.1"}
#                                   replace = gleiche Version, alte Pakete werden ersetzt
#                                   new     = neue Versionsnummer, aeltere Pakete werden entfernt
#   POST /api/client/build/stop     Bauvorgang abbrechen
#
# Gebaut wird, wofuer auf dem Server Werkzeuge da sind (--available):
#   .deb/.tar/.tar.gz immer; .exe/.msi/.zip mit Wine + Windows-Python; .apk mit Android-Werkzeugen.
# Recht: manage_settings.

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm
from app.paths import STORAGE_DIR

router = APIRouter(prefix="/api/client", tags=["client-build"])

SCRIPT = Path(__file__).resolve().parents[3] / "client" / "build_installers.py"
OUT_DIR = STORAGE_DIR / "client"
_lock = threading.Lock()
_job: dict = {"running": False, "log": [], "started": None, "finished": None, "result": None,
              "user": "", "version": "", "proc": None}


def _script_json(*args) -> dict:
    try:
        r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:      # noqa: BLE001
        return {"error": str(e)}


def _bump(v: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}" if m else "1.0.1"


def _run(cmd: list[str], user: str, version: str) -> None:
    started = time.time()
    try:
        env = dict(os.environ, PYTHONUNBUFFERED="1", WINEDEBUG="-all",
                   PATH=os.environ.get("PATH", "") + ":/usr/lib/wine")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                cwd=str(SCRIPT.parent), start_new_session=True)
        _job["proc"] = proc
        for line in proc.stdout:
            line = line.rstrip()
            if line.lstrip().startswith("$ "):            # Werkzeug-Aufrufe nicht ins Log
                continue
            _job["log"].append(line)
            del _job["log"][:-400]
        rc = proc.wait()
        built = [ln.split("-> ", 1)[1].split(" (")[0] for ln in _job["log"] if "-> " in ln and "(" in ln]
        failed = [ln.strip()[3:] for ln in _job["log"] if ln.strip().startswith("!!")]
        if rc < 0 or _job.get("stopped"):
            _job["result"] = {"ok": False, "stopped": True, "built": built, "failed": failed}
        else:
            _job["result"] = {"ok": rc == 0 and bool(built), "built": built, "failed": failed}
    except Exception as e:      # noqa: BLE001
        _job["log"].append(f"Fehler: {e}")
        _job["result"] = {"ok": False, "built": [], "failed": [str(e)]}
    finally:
        _job["running"] = False
        _job["finished"] = time.time()
        _job["proc"] = None
        res = _job["result"] or {}
        db.add_audit_entry(user, "client.packages_built",
                           details=f"Version {version}: {len(res.get('built', []))} Paket(e)"
                                   + (" (abgebrochen)" if res.get("stopped") else "")
                                   + f", {int(time.time() - started)} s")


@router.get("/build")
def build_status(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    info = _script_json("--tools") if not _job["running"] else {}
    return {"running": _job["running"], "log": _job["log"][-200:], "started": _job["started"],
            "finished": _job["finished"], "result": _job["result"], "by": _job["user"], "building_version": _job["version"],
            "tools": info.get("tools"), "version": info.get("version"), "suggested": _bump(info.get("version", "")),
            "available": SCRIPT.exists()}


class BuildBody(BaseModel):
    mode: str = "replace"          # replace | new
    version: str = ""


@router.post("/build")
def build_start(body: BuildBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    if not SCRIPT.exists():
        raise HTTPException(409, "Der Client-Quellcode (src/client) fehlt auf dem Server")
    if body.mode not in ("replace", "new"):
        raise HTTPException(400, "mode muss replace oder new sein")
    with _lock:
        if _job["running"]:
            raise HTTPException(409, "Es läuft bereits ein Bauvorgang")
        cur = _script_json("--tools").get("version", "")
        version = cur
        cmd = [sys.executable, "-u", str(SCRIPT), "--available", "--out", str(OUT_DIR)]
        if body.mode == "new":
            version = (body.version or _bump(cur)).strip()
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise HTTPException(400, "Version im Format 1.2.3 angeben")
            cmd += ["--set-version", version]
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        _job.update({"running": True, "log": [f"Starte Bau von Version {version} …"], "started": time.time(),
                     "finished": None, "result": None, "user": user["username"], "version": version, "stopped": False})
        threading.Thread(target=_run, args=(cmd, user["username"], version), daemon=True).start()
    return {"ok": True, "version": version}


@router.post("/build/stop")
def build_stop(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    proc = _job.get("proc")
    if not _job["running"] or proc is None:
        return {"ok": True, "running": False}
    _job["stopped"] = True
    _job["log"].append("Abbruch angefordert …")
    try:
        os.killpg(proc.pid, 15)          # ganze Prozessgruppe (inkl. Wine/PyInstaller)
    except Exception:
        proc.terminate()
    return {"ok": True}

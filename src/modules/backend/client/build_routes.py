# build_routes.py (Modul "client") - Client-Pakete auf dem Server neu bauen
# --------------------------------------------------------------------------
# Wie beim Agenten per Knopfdruck - aber fuer ALLE Formate:
#   Windows .exe .msi .zip | Linux .deb .rpm .pkg.tar.xz .tar .tar.gz .tgz | Android .apk
#
# Docker-Betrieb: gebaut wird im Bau-Image (buildenv.py), das beim Aktivieren des
#                 Moduls angelegt wird -> alle Formate.
# Nativ:          gebaut wird direkt mit dem Backend-Python -> was mit den lokalen
#                 Werkzeugen geht (Linux: deb/tar/tgz/pkg, Windows: zusaetzlich exe/zip/msi).
# Ausgabe: backend/storage/client/ (Vorrang vor src/client/release, uebersteht Updates).
#
#   GET  /api/client/build          Stand: laeuft?, Log, Ergebnis, Formate, Version, Bau-Umgebung
#   POST /api/client/build          {mode: "replace" | "new", version?: "1.2.1"}
#                                   replace = gleiche Version, vorhandene Pakete werden ersetzt
#                                   new     = neue Versionsnummer, aeltere Pakete werden entfernt
#   POST /api/client/build/stop     Bauvorgang abbrechen
#   POST /api/client/build/prepare  Bau-Umgebung (neu) vorbereiten {force?: true}
# Recht: manage_settings.

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm
from app.paths import STORAGE_DIR

from . import buildenv

router = APIRouter(prefix="/api/client", tags=["client-build"])

SCRIPT = buildenv.SCRIPT
OUT_DIR = STORAGE_DIR / "client"
_lock = threading.Lock()
_job: dict = {"running": False, "log": [], "started": None, "finished": None, "result": None,
              "user": "", "version": "", "proc": None, "container": None, "where": ""}


def _script_json(*args) -> dict:
    try:
        r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, RMM_CLIENT_TOOLS=str(buildenv.TOOLS_DIR)))
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:      # noqa: BLE001
        return {"error": str(e)}


def _bump(v: str) -> str:
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}" if m else "1.0.1"


def _formats(env: dict, local: dict) -> dict:
    """Was laesst sich bauen? Docker + fertiges Bau-Image = alles."""
    if env["mode"] == "docker":
        return {k: bool(env.get("ready")) for k in buildenv.ALL_FORMATS}
    return {k: bool(local.get(k)) for k in buildenv.ALL_FORMATS}


def _line(line: str) -> None:
    line = line.rstrip()
    if not line or line.lstrip().startswith("$ "):          # Werkzeug-Aufrufe nicht ins Log
        return
    _job["log"].append(line)
    del _job["log"][:-400]


def _finish(rc: int, user: str, version: str, started: float) -> None:
    built = [ln.split("-> ", 1)[1].split(" (")[0] for ln in _job["log"] if "-> " in ln and "(" in ln]
    failed = [ln.strip()[3:] for ln in _job["log"] if ln.strip().startswith("!!")]
    if rc < 0 or _job.get("stopped"):
        _job["result"] = {"ok": False, "stopped": True, "built": built, "failed": failed}
    else:
        _job["result"] = {"ok": rc == 0 and bool(built), "built": built, "failed": failed}
    res = _job["result"]
    db.add_audit_entry(user, "client.packages_built",
                       details=f"Version {version}: {len(res.get('built', []))} Paket(e)"
                               + (" (abgebrochen)" if res.get("stopped") else "")
                               + f", {_job['where']}, {int(time.time() - started)} s")


def _run_native(args: list[str], user: str, version: str) -> None:
    started = time.time()
    rc = 1
    try:
        env = dict(os.environ, PYTHONUNBUFFERED="1", WINEDEBUG="-all", RMM_CLIENT_TOOLS=str(buildenv.TOOLS_DIR),
                   PATH=os.environ.get("PATH", "") + (os.pathsep + "/usr/lib/wine" if os.name != "nt" else ""))
        kw = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000200}
        proc = subprocess.Popen([sys.executable, "-u", str(SCRIPT), "--out", str(OUT_DIR), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                cwd=str(SCRIPT.parent), **kw)
        _job["proc"] = proc
        for line in proc.stdout:
            _line(line)
        rc = proc.wait()
    except Exception as e:      # noqa: BLE001
        _line(f"!! Fehler: {e}")
    finally:
        _job["proc"] = None
        _finish(rc, user, version, started)
        _job["running"] = False
        _job["finished"] = time.time()


def _run_docker(args: list[str], user: str, version: str) -> None:
    started = time.time()
    rc = 1
    try:
        rc = buildenv.run_in_container(args, OUT_DIR, _line, lambda cid: _job.update(container=cid))
    except Exception as e:      # noqa: BLE001
        _line(f"!! {e}")
        rc = -1 if _job.get("stopped") else 1
    finally:
        _job["container"] = None
        _finish(rc, user, version, started)
        _job["running"] = False
        _job["finished"] = time.time()


@router.get("/build")
def build_status(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    env = buildenv.status()
    info = _script_json("--tools") if not _job["running"] else {}
    return {"running": _job["running"], "log": _job["log"][-200:], "started": _job["started"],
            "finished": _job["finished"], "result": _job["result"], "by": _job["user"],
            "building_version": _job["version"], "where": _job["where"],
            "tools": _formats(env, info.get("tools") or {}) if info else None,
            "version": info.get("version"), "suggested": _bump(info.get("version", "")),
            "available": SCRIPT.exists(), "env": env}


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
    env = buildenv.status()
    if env["preparing"]:
        raise HTTPException(409, "Die Bau-Umgebung wird gerade vorbereitet – bitte warten, bis sie fertig ist")
    if env["mode"] == "docker" and not env["ready"]:
        raise HTTPException(409, "Die Bau-Umgebung ist noch nicht vorbereitet („Bau-Umgebung vorbereiten“)")
    with _lock:
        if _job["running"]:
            raise HTTPException(409, "Es läuft bereits ein Bauvorgang")
        cur = _script_json("--tools").get("version", "")
        version = cur
        if body.mode == "new":
            version = (body.version or _bump(cur)).strip()
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise HTTPException(400, "Version im Format 1.2.3 angeben")
            # Version im Quellcode setzen (gilt fuer diesen und alle folgenden Bauvorgaenge)
            res = _script_json("--set-version", version, "--tools")
            if res.get("version") != version:
                raise HTTPException(500, f"Version konnte nicht gesetzt werden: {res.get('error', '')}")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        where = "Docker-Bau-Image" if env["mode"] == "docker" else "nativ"
        _job.update({"running": True, "log": [f"Starte Bau von Version {version} ({where}) …"], "started": time.time(),
                     "finished": None, "result": None, "user": user["username"], "version": version,
                     "stopped": False, "where": where})
        target = _run_docker if env["mode"] == "docker" else _run_native
        threading.Thread(target=target, args=(["--available"], user["username"], version), daemon=True).start()
    return {"ok": True, "version": version, "where": where}


@router.post("/build/stop")
def build_stop(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    if not _job["running"]:
        return {"ok": True, "running": False}
    _job["stopped"] = True
    _job["log"].append("Abbruch angefordert …")
    if _job.get("container"):
        buildenv.kill_container(_job["container"])
        return {"ok": True}
    proc = _job.get("proc")
    if proc is not None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:
                os.killpg(proc.pid, 15)          # ganze Prozessgruppe (inkl. Wine/PyInstaller)
        except Exception:
            proc.terminate()
    return {"ok": True}


class PrepareBody(BaseModel):
    force: bool = False


@router.post("/build/prepare")
def build_prepare(body: PrepareBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    if _job["running"]:
        raise HTTPException(409, "Während eines Bauvorgangs nicht möglich")
    env = buildenv.env_info()
    if env["mode"] == "native-linux":
        raise HTTPException(409, "Ohne Docker gibt es nichts vorzubereiten – gebaut wird mit den lokalen Werkzeugen")
    started = buildenv.prepare_async(force=body.force)
    if started:
        db.add_audit_entry(user["username"], "client.buildenv_prepare", details=buildenv.IMAGE if env["mode"] == "docker" else "native")
    return {"ok": True, "started": started}

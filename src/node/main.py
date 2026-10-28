#!/usr/bin/env python3
"""
RAPALLE.net RMM - NODE
======================

Eine Node ist ein kleiner, EIGENSTAENDIGER Prozess auf einem Client. Sie laeuft
unabhaengig vom Agenten (eigener Dienst/Prozess, eigene Verbindung, eigenes Token).
Der Agent installiert, startet und ueberwacht sie nur.

Was die Node tut
----------------
1. Verbindet sich mit dem Backend (Socket.IO, Namespace /node) und meldet sich an.
2. Prueft DOCKER und meldet den Zustand (oder dass Docker installiert werden muss).
3. Nimmt NODE-MODULE entgegen (Dateien aus src/modules/node/<name>/ des Backends,
   z.B. eine docker-compose.yml) und fuehrt sie aus.
4. Meldet regelmaessig Zustand und Logs.

Sicherheit: Die Node fuehrt NUR die unten definierten Befehle aus (keine freie Shell).
Compose-Dateien und Module kommen ausschliesslich vom Backend.

Konfiguration: node.json neben dieser Datei (schreibt der Agent bei der Installation):
    {"backend_url": "https://rmm.example.com", "client_id": "...", "token": "..."}
Optional per Umgebung: NODE_BACKEND_URL, NODE_CLIENT_ID, NODE_TOKEN.

Node-Module (siehe src/modules/backend/example/GUIDE.md, Abschnitt "Node-Teil")
-----------------------------------------------------------------------------
  kind "compose": docker-compose.yml wird mit `docker compose` verwaltet.
  kind "python":  node_module.py mit start(ctx) / stop(ctx) / action(ctx, name, data).
"""

__version__ = "0.1.0"

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

import socketio

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "node.json"
DATA = HERE / "data"
MODS = DATA / "modules"
LOG_FILE = DATA / "node.log"
PID_FILE = DATA / "node.pid"
STATUS_INTERVAL = 30
MAX_LOG_BYTES = 1_000_000

sio = socketio.AsyncClient(reconnection=False)
_log_buffer: list[str] = []
_python_modules: dict[str, object] = {}      # id -> geladenes node_module.py


# =========================================================================
# Hilfen
# =========================================================================

def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    _log_buffer.append(line)
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config() -> dict:
    cfg: dict = {}
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    cfg["backend_url"] = os.getenv("NODE_BACKEND_URL") or cfg.get("backend_url") or ""
    cfg["client_id"] = os.getenv("NODE_CLIENT_ID") or cfg.get("client_id") or ""
    cfg["token"] = os.getenv("NODE_TOKEN") or cfg.get("token") or ""
    return cfg


async def run(cmd: list[str], timeout: float = 120, cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    """Fuehrt einen Befehl aus (ohne Shell). Gibt (Exitcode, Ausgabe) zurueck."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd) if cwd else None, env={**os.environ, **(env or {})},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError:
        return 127, f"Programm nicht gefunden: {cmd[0]}"
    except OSError as exc:
        return 126, str(exc)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 124, f"Zeitueberschreitung nach {timeout:.0f}s"
    return proc.returncode or 0, (out or b"").decode(errors="replace")


def safe_join(base: Path, rel: str) -> Path:
    """Verhindert Pfad-Tricks (../) in mitgeschickten Dateinamen."""
    target = (base / rel).resolve()
    target.relative_to(base.resolve())
    return target


# =========================================================================
# Docker
# =========================================================================

def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


async def docker_status() -> dict:
    exe = shutil.which("docker")
    st = {"installed": bool(exe), "daemon": False, "compose": False, "version": None,
          "compose_version": None, "can_install": False, "hint": ""}
    system = platform.system()
    if exe:
        code, out = await run([exe, "version", "--format", "{{.Server.Version}}"], timeout=15)
        if code == 0 and out.strip():
            st["daemon"], st["version"] = True, out.strip().splitlines()[0]
        code, out = await run([exe, "compose", "version", "--short"], timeout=15)
        if code == 0 and out.strip():
            st["compose"], st["compose_version"] = True, out.strip().splitlines()[0]
        if not st["daemon"]:
            st["hint"] = "Docker ist installiert, aber der Dienst laeuft nicht (z.B. 'systemctl start docker' bzw. Docker Desktop starten)."
        elif not st["compose"]:
            st["hint"] = "Das Docker-Compose-Plugin fehlt (Paket 'docker-compose-plugin')."
    else:
        if system == "Linux":
            st["can_install"] = _is_root() and bool(shutil.which("curl") or shutil.which("wget"))
            st["hint"] = ("Docker ist nicht installiert. Es kann automatisch installiert werden."
                          if st["can_install"] else
                          "Docker ist nicht installiert. Die Node laeuft ohne Root-Rechte bzw. ohne curl/wget - bitte Docker manuell installieren (https://docs.docker.com/engine/install/).")
        elif system == "Windows":
            st["hint"] = "Docker ist nicht installiert. Bitte Docker Desktop installieren: https://docs.docker.com/desktop/setup/install/windows-install/"
        elif system == "Darwin":
            st["hint"] = "Docker ist nicht installiert. Bitte Docker Desktop installieren: https://docs.docker.com/desktop/setup/install/mac-install/"
        else:
            st["hint"] = "Docker ist nicht installiert."
    return st


async def docker_install() -> dict:
    """Installiert Docker (nur Linux, nur als Root). Windows/macOS: Hinweis."""
    if platform.system() != "Linux":
        raise RuntimeError("Automatische Installation gibt es nur unter Linux. Bitte Docker Desktop manuell installieren.")
    if not _is_root():
        raise RuntimeError("Die Node laeuft nicht als Root - Docker bitte manuell installieren.")
    if shutil.which("docker"):
        return {"already": True, "docker": await docker_status()}
    fetch = ("curl -fsSL https://get.docker.com -o /tmp/rmm-get-docker.sh" if shutil.which("curl")
             else "wget -qO /tmp/rmm-get-docker.sh https://get.docker.com")
    code, out = await run(["sh", "-c", fetch], timeout=120)
    if code != 0:
        raise RuntimeError("Installationsskript konnte nicht geladen werden: " + out[-300:])
    log("Docker wird installiert (get.docker.com) ...")
    code, out = await run(["sh", "/tmp/rmm-get-docker.sh"], timeout=900)
    if code != 0:
        raise RuntimeError("Docker-Installation fehlgeschlagen: " + out[-500:])
    if shutil.which("systemctl"):
        await run(["systemctl", "enable", "--now", "docker"], timeout=60)
    return {"installed": True, "docker": await docker_status()}


# =========================================================================
# Node-Module
# =========================================================================

def _meta_path(mid: str) -> Path:
    return MODS / mid / ".rmm-module.json"


def _read_meta(mid: str) -> dict:
    try:
        return json.loads(_meta_path(mid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_meta(mid: str, meta: dict) -> None:
    _meta_path(mid).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _valid_id(mid: str) -> str:
    mid = str(mid or "")
    if not mid or not all(c.isalnum() or c in "-_" for c in mid) or len(mid) > 32:
        raise ValueError("ungueltige Modul-ID")
    return mid


def _node_section(mid: str) -> dict:
    return _read_meta(mid).get("manifest", {}).get("node", {}) or {}


async def _compose(mid: str, *args: str, timeout: float = 300) -> tuple[int, str]:
    d = MODS / mid
    sec = _node_section(mid)
    cfile = sec.get("compose") or "docker-compose.yml"
    st = await docker_status()
    if not st["installed"] or not st["daemon"] or not st["compose"]:
        raise RuntimeError("Docker ist nicht einsatzbereit: " + (st["hint"] or "unbekannter Grund"))
    return await run(["docker", "compose", "-p", f"rmm-{mid}", "-f", cfile, *args], cwd=d, timeout=timeout)


class ModuleContext:
    """Wird python-Node-Modulen uebergeben."""

    def __init__(self, mid: str):
        self.id = mid
        self.dir = MODS / mid
        self.data_dir = MODS / mid / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.params = _read_meta(mid).get("params", {})

    def log(self, msg: str) -> None:
        log(f"[{self.id}] {msg}")

    async def run(self, cmd: list[str], timeout: float = 120):
        return await run(cmd, timeout=timeout, cwd=self.dir)


async def _py_call(mid: str, fn: str, *args):
    mod = _python_modules.get(mid)
    f = getattr(mod, fn, None) if mod else None
    if not callable(f):
        return None
    res = f(ModuleContext(mid), *args)
    if asyncio.iscoroutine(res):
        res = await res
    return res


def _py_load(mid: str) -> None:
    path = MODS / mid / "node_module.py"
    if not path.is_file():
        raise RuntimeError("node_module.py fehlt")
    spec = importlib.util.spec_from_file_location(f"rmm_node_{mid}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _python_modules[mid] = mod


async def module_install(data: dict) -> dict:
    bundle = data.get("bundle") or {}
    mid = _valid_id(bundle.get("id"))
    params = data.get("params") or {}
    d = MODS / mid
    if d.exists():                                   # Neu einspielen: alten Stand sauber stoppen
        try:
            await module_action({"id": mid, "action": "down"})
        except Exception:
            pass
        for item in d.iterdir():
            if item.name == "data":
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    d.mkdir(parents=True, exist_ok=True)
    for rel, b64 in (bundle.get("files") or {}).items():
        target = safe_join(d, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(b64))
    # Parameter als .env fuer docker compose (${NAME} in der compose-Datei)
    env_lines = [f"{str(k).upper()}={str(v).replace(chr(10), ' ')}" for k, v in params.items()
                 if str(k).replace("_", "").isalnum()]
    (d / ".env").write_text("\n".join(env_lines) + ("\n" if env_lines else ""), encoding="utf-8")
    _write_meta(mid, {"id": mid, "version": bundle.get("version"), "hash": bundle.get("hash"),
                      "manifest": bundle.get("manifest") or {}, "params": params,
                      "desired": "up", "installed_at": int(time.time())})
    log(f"Modul '{mid}' v{bundle.get('version')} installiert")
    sec = _node_section(mid)
    if sec.get("autostart", True):
        try:
            return {"installed": True, "start": await module_action({"id": mid, "action": "up"})}
        except Exception as exc:
            return {"installed": True, "start_error": str(exc)}
    return {"installed": True}


async def module_action(data: dict) -> dict:
    mid = _valid_id(data.get("id"))
    action = str(data.get("action") or "")
    if not (MODS / mid).is_dir():
        raise RuntimeError(f"Modul '{mid}' ist nicht installiert")
    sec = _node_section(mid)
    kind = sec.get("kind", "compose")
    allowed = sec.get("actions") or ["up", "down", "restart", "status", "logs"]
    if action not in allowed:
        raise RuntimeError(f"Aktion '{action}' ist fuer dieses Modul nicht erlaubt")
    meta = _read_meta(mid)
    if kind == "compose":
        if action == "up":
            code, out = await _compose(mid, "up", "-d", "--remove-orphans")
            meta["desired"] = "up"
        elif action == "down":
            code, out = await _compose(mid, "down")
            meta["desired"] = "down"
        elif action == "restart":
            code, out = await _compose(mid, "restart")
        elif action == "status":
            code, out = await _compose(mid, "ps", "--format", "json", timeout=60)
            return {"exit": code, "containers": _parse_ps(out)}
        elif action == "logs":
            code, out = await _compose(mid, "logs", "--tail", str(int(data.get("data", {}).get("lines", 100))), "--no-color", timeout=60)
            return {"exit": code, "log": out[-20000:]}
        else:
            raise RuntimeError(f"Unbekannte Aktion '{action}'")
        _write_meta(mid, meta)
        if code != 0:
            raise RuntimeError(out[-600:] or f"docker compose endete mit {code}")
        return {"exit": code, "output": out[-600:]}
    if kind == "python":
        if mid not in _python_modules:
            _py_load(mid)
        if action == "up":
            meta["desired"] = "up"; _write_meta(mid, meta)
            return {"result": await _py_call(mid, "start")}
        if action == "down":
            meta["desired"] = "down"; _write_meta(mid, meta)
            return {"result": await _py_call(mid, "stop")}
        return {"result": await _py_call(mid, "action", action, data.get("data") or {})}
    raise RuntimeError(f"Unbekannte Modulart '{kind}'")


def _parse_ps(out: str) -> list[dict]:
    items = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                c = json.loads(line)
                items.append({"name": c.get("Name"), "service": c.get("Service"), "state": c.get("State"),
                              "status": c.get("Status"), "ports": c.get("Publishers") or c.get("Ports")})
            except ValueError:
                pass
        elif line.startswith("["):
            try:
                for c in json.loads(line):
                    items.append({"name": c.get("Name"), "service": c.get("Service"), "state": c.get("State"),
                                  "status": c.get("Status"), "ports": c.get("Publishers") or c.get("Ports")})
            except ValueError:
                pass
    return items


async def module_remove(data: dict) -> dict:
    mid = _valid_id(data.get("id"))
    d = MODS / mid
    if not d.is_dir():
        return {"removed": False}
    try:
        await module_action({"id": mid, "action": "down"})
    except Exception as exc:
        log(f"Modul '{mid}': Stoppen beim Entfernen: {exc}")
    if mid in _python_modules:
        _python_modules.pop(mid, None)
    shutil.rmtree(d, ignore_errors=True)
    log(f"Modul '{mid}' entfernt")
    return {"removed": True}


async def module_list() -> dict:
    """Installierte Node-Module mit Zustand - Teil jedes Statusberichts."""
    out: dict[str, dict] = {}
    if not MODS.is_dir():
        return out
    for d in sorted(MODS.iterdir()):
        meta = _read_meta(d.name)
        if not meta:
            continue
        sec = meta.get("manifest", {}).get("node", {}) or {}
        entry = {"version": meta.get("version"), "kind": sec.get("kind", "compose"),
                 "desired": meta.get("desired"), "state": "unknown"}
        if entry["kind"] == "compose":
            try:
                st = await docker_status()
                if st["daemon"] and st["compose"]:
                    code, o = await _compose(d.name, "ps", "--format", "json", timeout=30)
                    cs = _parse_ps(o)
                    entry["containers"] = cs
                    entry["state"] = ("running" if cs and all(c["state"] == "running" for c in cs)
                                      else "stopped" if not cs else "partial")
                else:
                    entry["state"] = "no-docker"
            except Exception as exc:
                entry["state"], entry["error"] = "error", str(exc)[:200]
        else:
            entry["state"] = "running" if d.name in _python_modules else "stopped"
        out[d.name] = entry
    return out


async def reconcile() -> None:
    """Beim Start: Module, die laufen sollen, wieder hochfahren (idempotent)."""
    if not MODS.is_dir():
        return
    for d in sorted(MODS.iterdir()):
        meta = _read_meta(d.name)
        if meta.get("desired") == "up":
            try:
                await module_action({"id": d.name, "action": "up"})
                log(f"Modul '{d.name}' gestartet")
            except Exception as exc:
                log(f"Modul '{d.name}' konnte nicht gestartet werden: {exc}")


# =========================================================================
# Befehle vom Backend
# =========================================================================

async def _full_report() -> dict:
    return {
        "version": __version__, "os": f"{platform.system()} {platform.release()}",
        "hostname": socket.gethostname(), "python": sys.version.split()[0],
        "docker": await docker_status(), "modules": await module_list(),
    }


# -------------------------------------------------------------------------
# Docker-Engine-API durchreichen (Modul "docker" im RMM steuert damit die
# Container dieses Nodes: Stacks, Start/Stopp, Logs, Statistik, Images ...)
# -------------------------------------------------------------------------
DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")


def _docker_api_sync(method: str, path: str, body, raw: bool, timeout: float) -> dict:
    import http.client

    class _Conn(http.client.HTTPConnection):
        def connect(self):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(DOCKER_SOCK)
            self.sock = s

    if not path.startswith("/") or ".." in path:
        raise RuntimeError("Ungültiger Docker-Pfad")
    conn = _Conn("localhost", timeout=timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"} if payload else {})
        r = conn.getresponse()
        data = r.read()
        return {"status": r.status, "body": base64.b64encode(data).decode()}
    finally:
        conn.close()


async def docker_api(data: dict) -> dict:
    return await asyncio.get_running_loop().run_in_executor(
        None, _docker_api_sync, str(data.get("method") or "GET").upper(), str(data.get("path") or ""),
        data.get("body"), bool(data.get("raw")), float(data.get("timeout") or 60))


def _magic_packet(mac: str) -> bytes:
    """Magic Packet: 6x 0xFF + 16x die 6-Byte-MAC."""
    hexmac = mac.replace(":", "").replace("-", "").replace(".", "").strip()
    if len(hexmac) != 12:
        raise ValueError(f"Ungueltige MAC: {mac}")
    raw = bytes.fromhex(hexmac)
    return b"\xff" * 6 + raw * 16


def send_wol(data: dict) -> dict:
    """Wake-on-LAN: Magic Packet an eine oder mehrere MACs senden.
    data: {macs:[...], broadcasts:[...], port:int}. Sendet an alle angegebenen
    Broadcast-Adressen (Standard 255.255.255.255) auf Port 9 (und 7)."""
    macs = [m for m in (data.get("macs") or ([data["mac"]] if data.get("mac") else [])) if m]
    if not macs:
        raise ValueError("Keine MAC-Adresse angegeben")
    ports = data.get("ports") or [int(data.get("port") or 9), 7]
    targets = data.get("broadcasts") or ["255.255.255.255"]
    sent = []
    errors = []
    for mac in macs:
        try:
            pkt = _magic_packet(mac)
        except ValueError as e:
            errors.append(str(e))
            continue
        for bc in targets:
            for port in ports:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                        sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        sk.sendto(pkt, (bc, int(port)))
                    sent.append(f"{mac} -> {bc}:{port}")
                except OSError as e:
                    errors.append(f"{mac} -> {bc}:{port}: {e}")
    return {"sent": sent, "errors": errors, "ok_count": len(sent)}


async def handle(op: str, data: dict):
    if op == "wol":
        return send_wol(data)
    if op == "ping":
        return {"pong": True, "time": int(time.time())}
    if op == "status":
        return await _full_report()
    if op == "docker-status":
        return await docker_status()
    if op == "docker-install":
        return await docker_install()
    if op == "module-install":
        return await module_install(data)
    if op == "module-remove":
        return await module_remove(data)
    if op == "module-action":
        return await module_action(data)
    if op == "module-list":
        return await module_list()
    if op == "docker-api":
        return await docker_api(data)
    if op == "logs":
        try:
            return {"log": LOG_FILE.read_text(encoding="utf-8")[-20000:]}
        except OSError:
            return {"log": ""}
    raise RuntimeError(f"Unbekannter Befehl: {op}")


@sio.on("node-cmd", namespace="/node")
async def on_cmd(payload):
    rid = (payload or {}).get("requestId")
    op = str((payload or {}).get("op") or "")
    try:
        result = await handle(op, (payload or {}).get("data") or {})
        await sio.emit("node-result", {"requestId": rid, "ok": True, "data": result}, namespace="/node")
    except Exception as exc:
        log(f"Befehl '{op}' fehlgeschlagen: {exc}")
        await sio.emit("node-result", {"requestId": rid, "ok": False, "error": str(exc)[:800]}, namespace="/node")
    await _flush_logs()


async def _flush_logs() -> None:
    if _log_buffer and sio.connected:
        text, _log_buffer[:] = "\n".join(_log_buffer[-50:]), []
        try:
            await sio.emit("node-log", {"text": text}, namespace="/node")
        except Exception:
            pass


# =========================================================================
# Hauptschleife
# =========================================================================

async def status_loop() -> None:
    while True:
        await asyncio.sleep(STATUS_INTERVAL)
        if sio.connected:
            try:
                await sio.emit("node-status", await _full_report(), namespace="/node")
                await _flush_logs()
            except Exception as exc:
                log(f"Status senden: {exc}")


async def connection_loop(cfg: dict) -> None:
    backoff = 3.0
    while True:
        try:
            log(f"Verbinde zu {cfg['backend_url']} (Namespace /node) ...")
            await sio.connect(cfg["backend_url"], namespaces=["/node"], wait_timeout=30,
                              auth={"client_id": cfg["client_id"], "token": cfg["token"]})
            backoff = 3.0
            await sio.emit("node-hello", await _full_report(), namespace="/node")
            log("verbunden")
            await sio.wait()
            log("Verbindung getrennt")
        except Exception as exc:
            log(f"Verbindung fehlgeschlagen: {exc}")
            if "unauthorized" in str(exc).lower():
                log("Das Backend lehnt dieses Node-Token ab (Node wurde entfernt?). Neuer Versuch in 60 s.")
                backoff = 60.0
        try:
            await sio.disconnect()
        except Exception:
            pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.7, 60.0)


async def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    MODS.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    if not (cfg["backend_url"] and cfg["client_id"] and cfg["token"]):
        print("node.json unvollstaendig (backend_url, client_id, token)", file=sys.stderr)
        sys.exit(2)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    log(f"Node {__version__} startet (Client {cfg['client_id']})")
    asyncio.create_task(status_loop())
    asyncio.create_task(reconcile())
    await connection_loop(cfg)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)
    finally:
        try:
            PID_FILE.unlink()
        except OSError:
            pass

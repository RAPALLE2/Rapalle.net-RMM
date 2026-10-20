# connector.py (Modul "cloudflare") - cloudflared fuer den Tunnel des RMM-Servers
# -------------------------------------------------------------------------------
# Beim Aktivieren des Moduls wird cloudflared (feste Version) nach
# data/modules/cloudflare/bin geladen - Docker UND nativ (Linux amd64/arm64, Windows).
# Laeuft als Kindprozess des Backends ("tunnel run --token ..."), wird bei Absturz neu
# gestartet (Wartezeit waechst bis 60 s). Das Token geht per Umgebung (TUNNEL_TOKEN),
# nicht ueber die Kommandozeile (waere sonst in "ps" sichtbar).
# Die Nodes betreiben ihren eigenen Connector (Node-Teil des Moduls, Docker-Image).

from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
import urllib.request

from . import store

VERSION = "2026.9.1"
_BASE = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/"
_state = {"proc": None, "token": "", "log": [], "started": None, "restarts": 0, "want": False,
          "download": "", "error": ""}
_lock = threading.Lock()


def _asset() -> tuple[str, str]:
    m = platform.machine().lower()
    arch = "arm64" if m in ("aarch64", "arm64") else ("arm" if m.startswith("arm") else "amd64")
    if os.name == "nt":
        return "cloudflared-windows-amd64.exe", "cloudflared.exe"
    return f"cloudflared-linux-{arch}", "cloudflared"


def binary():
    return store.BIN_DIR / _asset()[1]


def _log(line: str) -> None:
    line = line.rstrip()
    if line:
        _state["log"].append(time.strftime("%H:%M:%S ") + line)
        del _state["log"][:-300]


def installed_version() -> str:
    b = binary()
    if not b.exists():
        return ""
    try:
        r = subprocess.run([str(b), "--version"], capture_output=True, text=True, timeout=20)
        return (r.stdout or r.stderr).strip().split("\n")[0]
    except Exception:
        return ""


def ensure_binary() -> None:
    """cloudflared laden, falls die feste Version fehlt (beim Aktivieren des Moduls)."""
    b = binary()
    marker = b.with_suffix(".version")
    if b.exists() and marker.exists() and marker.read_text().strip() == VERSION:
        return
    store.BIN_DIR.mkdir(parents=True, exist_ok=True)
    _state["download"] = f"lade cloudflared {VERSION} …"
    tmp = b.with_name(b.name + ".part")
    try:
        urllib.request.urlretrieve(_BASE + _asset()[0], tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, b)
        marker.write_text(VERSION)
        _state["download"] = ""
        _log(f"cloudflared {VERSION} geladen")
    except Exception as e:
        _state["download"] = ""
        _state["error"] = f"cloudflared konnte nicht geladen werden: {e}"
        raise


def ensure_binary_async() -> None:
    threading.Thread(target=lambda: _safe(ensure_binary), daemon=True, name="cf-download").start()


def _safe(fn):
    try:
        fn()
    except Exception as e:      # noqa: BLE001
        _log(f"Fehler: {e}")


def status() -> dict:
    p = _state["proc"]
    return {"running": bool(p and p.poll() is None), "want": _state["want"], "started": _state["started"],
            "restarts": _state["restarts"], "log": _state["log"][-80:], "version": VERSION,
            "installed": binary().exists(), "download": _state["download"], "error": _state["error"],
            "connected": any("Registered tunnel connection" in ln for ln in _state["log"][-200:])}


def start(token: str) -> None:
    with _lock:
        changed = token != _state["token"]
        _state.update(want=True, token=token, error="")
        th = _state.get("thread")
        if th and th.is_alive():               # Waechter laeuft schon (auch waehrend der Neustart-Pause)
            if changed and _state["proc"] and _state["proc"].poll() is None:
                _state["proc"].terminate()     # neues Token -> mit neuem Token neu starten
            return
        th = threading.Thread(target=_supervise, daemon=True, name="cloudflared")
        _state["thread"] = th
        th.start()


def restart(token: str) -> None:
    stop()
    time.sleep(0.5)
    start(token)


def stop() -> None:
    with _lock:
        _state["want"] = False
        p = _state["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except Exception:
            p.kill()
    th = _state.get("thread")
    if th and th.is_alive() and th is not threading.current_thread():
        th.join(5)


def _supervise() -> None:
    delay = 2
    while _state["want"]:
        try:
            ensure_binary()
        except Exception:
            time.sleep(30)
            continue
        env = dict(os.environ, TUNNEL_TOKEN=_state["token"])
        args = [str(binary()), "tunnel", "--no-autoupdate", "--metrics", "127.0.0.1:0", "run"]
        try:
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                 stdin=subprocess.DEVNULL)
        except Exception as e:
            _state["error"] = f"cloudflared startet nicht: {e}"
            time.sleep(30)
            continue
        _state["proc"], _state["started"] = p, time.time()
        _log("cloudflared gestartet")
        for line in p.stdout:
            _log(line.replace(_state["token"], "***") if _state["token"] else line)
        rc = p.wait()
        _state["proc"] = None
        if not _state["want"]:
            _log("cloudflared beendet")
            break
        _state["restarts"] += 1
        _log(f"cloudflared beendet (Code {rc}) – Neustart in {delay} s")
        for _ in range(int(delay * 2)):
            if not _state["want"]:
                break
            time.sleep(0.5)
        delay = min(delay * 2, 60) if time.time() - (_state["started"] or 0) < 120 else 2

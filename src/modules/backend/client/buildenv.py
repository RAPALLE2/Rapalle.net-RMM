# buildenv.py (Modul "client") - Bau-Umgebung fuer die Client-Pakete
# ------------------------------------------------------------------
# Docker-Betrieb (Standard, alle Formate):
#   Beim Aktivieren des Moduls wird EIN Bau-Image angelegt:
#       rapalle/rmm-client-builder:<kennung>
#   Inhalt: Ubuntu 24.04 + Wine + Windows-Python mit PyInstaller + wixl/msitools (MSI-Assistent) + rpmbuild
#           + JDK 17 + Android-Werkzeuge (aapt, dx, zipalign, apksigner, android-23).
#   Angelegt wird es OHNE Dockerfile-Build (der Build-Endpunkt aendert sich je nach
#   Docker-Version): Basis-Image holen -> Einrichtungs-Container laufen lassen ->
#   als Image speichern (commit). Das klappt mit jeder Docker-Version.
#   Gebaut wird dann je Knopfdruck in einem Wegwerf-Container (ohne Netzwerk):
#   Quellcode src/client + bisherige Pakete hineinkopieren -> build_installers.py
#   -> Ergebnis zurueck nach backend/storage/client. Das RMM-Image bleibt klein.
#   Modul aus -> Bau-Image wird wieder entfernt (enthaelt keine Daten).
#
# Ohne Docker (nativ):
#   Linux   -> .deb/.tar/.tar.gz/.tgz/.pkg.tar.xz immer, .rpm/.exe/.msi/.apk nur
#              wenn die Werkzeuge zufaellig installiert sind.
#   Windows -> zusaetzlich .exe/.zip/.msi (PyInstaller per pip, WiX als Download -
#              wird beim Aktivieren nach data/modules/client/buildtools geholt).

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import sys
import tarfile
import threading
import time
import urllib.parse
from pathlib import Path

from app.paths import MODULES_DATA_DIR

CLIENT_DIR = Path(__file__).resolve().parents[3] / "client"
SCRIPT = CLIENT_DIR / "build_installers.py"
TOOLS_DIR = MODULES_DATA_DIR / "client" / "buildtools"      # nativ Windows: PyInstaller-Cache/WiX

BASE_IMAGE = os.environ.get("RMM_CLIENT_BUILDER_BASE", "ubuntu:24.04")
REPO = "rapalle/rmm-client-builder"
LABEL = "net.rapalle.rmm.client-builder"
NETWORK = os.environ.get("RMM_CLIENT_BUILDER_NET", "")       # leer = Docker-Standard (bridge)
WIN_PY_FALLBACK = ("https://github.com/astral-sh/python-build-standalone/releases/download/20241016/"
                   "cpython-3.12.7+20241016-x86_64-pc-windows-msvc-install_only.tar.gz")
ALL_FORMATS = ["exe", "zip", "msi", "deb", "rpm", "pkg", "tar", "tgz", "apk"]

# Einrichtung des Bau-Images (laeuft EINMAL beim Aktivieren in einem Container)
SETUP_SCRIPT = r"""set -e
export DEBIAN_FRONTEND=noninteractive
echo "== Systempakete (Wine, wixl, rpm, JDK, Android-Werkzeuge) ..."
apt-get update -q
apt-get install -y -q --no-install-recommends ca-certificates curl python3 xz-utils \
    wine64 wixl msitools rpm openjdk-17-jdk-headless \
    android-sdk-platform-23 aapt apksigner zipalign dalvik-exchange
echo "== Windows-Python (python-build-standalone) ..."
URL=$(python3 - <<'PY' || true
import json, re, urllib.request
try:
    r = urllib.request.urlopen("https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest", timeout=30)
    for a in json.load(r).get("assets", []):
        if re.fullmatch(r"cpython-3\.12\.\d+\+\d+-x86_64-pc-windows-msvc-install_only\.tar\.gz", a["name"]):
            print(a["browser_download_url"]); break
except Exception:
    pass
PY
)
[ -n "$URL" ] || URL="__WIN_PY_FALLBACK__"
echo "   $URL"
mkdir -p /opt/winpy
curl -fsSL -o /tmp/winpy.tgz "$URL"
tar xzf /tmp/winpy.tgz -C /opt/winpy
rm -f /tmp/winpy.tgz
echo "== PyInstaller unter Wine ..."
export WINEDEBUG=-all WINEPREFIX=/opt/wineprefix PATH="$PATH:/usr/lib/wine"
WINE=$(command -v wine64 || echo /usr/lib/wine/wine64)
"$WINE" wineboot -i 2>&1 | cat || true
"$WINE" /opt/winpy/python/python.exe -m pip install -q --disable-pip-version-check --no-warn-script-location "pyinstaller>=6.0,<7" 2>&1 | cat
"$WINE" /opt/winpy/python/python.exe -c "import PyInstaller; print('   PyInstaller', PyInstaller.__version__)" 2>&1 | cat
test -d /opt/winpy/python/Lib/site-packages/PyInstaller || { echo "PyInstaller fehlt nach der Installation"; exit 3; }
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/* 2>/dev/null || true
echo "RMM-BUILDER-READY"
""".replace("__WIN_PY_FALLBACK__", WIN_PY_FALLBACK)

BUILD_ENV = ["WINEDEBUG=-all", "WINEPREFIX=/opt/wineprefix", "RMM_WINPYTHON=/opt/winpy/python/python.exe",
             "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/wine",
             "PYTHONUNBUFFERED=1", "HOME=/root", "LANG=C.UTF-8"]

TAG = hashlib.sha256((BASE_IMAGE + SETUP_SCRIPT).encode()).hexdigest()[:12]
IMAGE = f"{REPO}:{TAG}"

_state: dict = {"preparing": False, "log": [], "error": "", "started": None, "finished": None,
                "container": None, "cancel": False}
_lock = threading.Lock()


# ===================================================================== Docker-API
def _ds():
    from app import docker_services
    return docker_services


def _raw(method: str, path: str, body: bytes | None = None, ctype: str = "application/json",
         timeout: float | None = 120, stream: bool = False):
    """Roher Docker-Aufruf (auch fuer tar-Archive und Datenstroeme)."""
    ds = _ds()
    conn = ds._UnixHTTPConnection(ds.DOCKER_SOCKET, timeout=timeout)
    headers = {"Content-Type": ctype} if body is not None else {}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    if resp.status >= 400:
        raw = resp.read()
        conn.close()
        try:
            msg = json.loads(raw).get("message", raw.decode(errors="replace"))
        except Exception:
            msg = raw.decode(errors="replace")
        raise RuntimeError(f"Docker: {msg} (HTTP {resp.status})")
    if stream:
        return conn, resp
    data = resp.read()
    conn.close()
    return data


def _json(method: str, path: str, body=None, timeout: float | None = 120):
    data = _raw(method, path, json.dumps(body).encode() if body is not None else None, timeout=timeout)
    try:
        return json.loads(data) if data else None
    except Exception:
        return data


def mode() -> str:
    """docker | native-windows | native-linux (+ Grund, falls Docker nicht nutzbar)."""
    return env_info()["mode"]


def env_info() -> dict:
    try:
        from app.runtime_env import is_docker
        docker = is_docker()
    except Exception:
        docker = False
    sock = False
    if docker:
        try:
            sock = _ds().socket_available()
        except Exception:
            sock = False
    if docker and sock:
        return {"mode": "docker", "docker": True, "socket": True}
    return {"mode": "native-windows" if os.name == "nt" else "native-linux", "docker": docker, "socket": sock}


def image_ready() -> bool:
    try:
        _json("GET", f"/images/{urllib.parse.quote(IMAGE, safe='')}/json", timeout=15)
        return True
    except Exception:
        return False


def image_size() -> int:
    try:
        return int((_json("GET", f"/images/{urllib.parse.quote(IMAGE, safe='')}/json", timeout=15) or {}).get("Size") or 0)
    except Exception:
        return 0


def _log(line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    _state["log"].append(line)
    del _state["log"][:-300]


def _demux(resp, on_line) -> None:
    """Docker-Logstrom ohne TTY (8-Byte-Rahmen) zeilenweise weiterreichen."""
    buf = b""
    while True:
        hdr = resp.read(8)
        if len(hdr) < 8:
            break
        size = struct.unpack(">I", hdr[4:8])[0]
        chunk = resp.read(size)
        buf += chunk
        while b"\n" in buf:
            ln, buf = buf.split(b"\n", 1)
            on_line(ln.decode("utf-8", "replace").replace("\r", ""))
    if buf:
        on_line(buf.decode("utf-8", "replace"))


def _host_config(network: str | None) -> dict:
    hc: dict = {"AutoRemove": False}
    if network:
        hc["NetworkMode"] = network
    return hc


def _remove_container(cid: str | None) -> None:
    if cid:
        try:
            _raw("DELETE", f"/containers/{cid}?force=1&v=1", timeout=60)
        except Exception:
            pass


# ===================================================================== Bau-Image anlegen
def status() -> dict:
    info = env_info()
    out = {**info, "image": IMAGE, "preparing": _state["preparing"], "log": _state["log"][-120:],
           "error": _state["error"], "started": _state["started"], "finished": _state["finished"]}
    if info["mode"] == "docker":
        out["ready"] = image_ready() if not _state["preparing"] else False
        out["size"] = image_size() if out["ready"] else 0
    else:
        out["ready"] = not _state["preparing"]
    return out


def remove_image_async() -> None:
    t = threading.Thread(target=remove_image, daemon=True, name="client-buildenv-rm")
    _state["remover"] = t
    t.start()


def prepare_async(force: bool = False) -> bool:
    """Bau-Umgebung im Hintergrund vorbereiten. -> False, wenn schon in Arbeit/fertig."""
    rm = _state.get("remover")
    if rm is not None and rm.is_alive():          # Modul eben aus- und wieder eingeschaltet:
        def later():                              # erst fertig loeschen lassen, dann neu anlegen
            rm.join(timeout=600)
            prepare_async(force)
        threading.Thread(target=later, daemon=True, name="client-buildenv-wait").start()
        return True
    with _lock:
        if _state["preparing"]:
            return False
        info = env_info()
        if info["mode"] == "docker" and image_ready() and not force:
            return False
        if info["mode"] == "native-linux":
            return False                # nichts herunterzuladen
        _state.update({"preparing": True, "log": [], "error": "", "started": time.time(), "finished": None,
                       "cancel": False, "apt_done": False})
    target = _prepare_docker if info["mode"] == "docker" else _prepare_windows
    threading.Thread(target=_guarded, args=(target, force), daemon=True, name="client-buildenv").start()
    return True


def _guarded(fn, force) -> None:
    try:
        fn(force)
    except Exception as e:      # noqa: BLE001
        _state["error"] = str(e)
        _log(f"!! {e}")
        print(f"[client] Bau-Umgebung: {e}")
    finally:
        _state["preparing"] = False
        _state["finished"] = time.time()
        _state["container"] = None


def _prepare_windows(_force) -> None:
    import subprocess
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    _log("Hole PyInstaller (pip) und WiX 3 (Download) …")
    p = subprocess.run([sys.executable, str(SCRIPT), "--prepare"], capture_output=True, text=True,
                       env=dict(os.environ, RMM_CLIENT_TOOLS=str(TOOLS_DIR)), cwd=str(CLIENT_DIR), timeout=1800)
    for ln in (p.stdout + p.stderr).splitlines():
        _log(ln)
    if p.returncode:
        raise RuntimeError("Werkzeuge konnten nicht geladen werden")
    _log("Bau-Umgebung bereit.")


def _prepare_docker(force) -> None:
    name, _, tag = BASE_IMAGE.partition(":")
    _log(f"Lade Basis-Image {BASE_IMAGE} …")
    try:
        conn, resp = _raw("POST", f"/images/create?fromImage={urllib.parse.quote(name)}&tag={tag or 'latest'}",
                          body=b"", timeout=1800, stream=True)
        try:
            for raw in resp:
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                st = ev.get("status", "")
                if st and "Downloading" not in st and "Extracting" not in st and "Waiting" not in st:
                    _log(f"  {st} {ev.get('id', '')}".rstrip())
        finally:
            conn.close()
    except Exception as e:      # noqa: BLE001 - offline? dann reicht ein lokal vorhandenes Basis-Image
        try:
            _json("GET", f"/images/{urllib.parse.quote(BASE_IMAGE, safe='')}/json", timeout=15)
            _log(f"  Laden nicht möglich ({e}) – nutze das lokal vorhandene {BASE_IMAGE}")
        except Exception:
            raise RuntimeError(f"Basis-Image {BASE_IMAGE} konnte nicht geladen werden: {e}") from None
    if _state["cancel"]:
        raise RuntimeError("abgebrochen")
    _log("Richte Werkzeuge ein (Wine, Windows-Python, PyInstaller, wixl, rpm, JDK, Android) – dauert einige Minuten …")
    cfg = {"Image": BASE_IMAGE, "Cmd": ["bash", "-c", SETUP_SCRIPT], "Labels": {LABEL: "setup"},
           "HostConfig": _host_config(NETWORK or None)}
    cid = _json("POST", "/containers/create", cfg)["Id"]
    _state["container"] = cid
    try:
        _raw("POST", f"/containers/{cid}/start", body=b"", timeout=60)
        conn, resp = _raw("GET", f"/containers/{cid}/logs?follow=1&stdout=1&stderr=1", timeout=None, stream=True)
        try:
            _demux(resp, _setup_line)
        finally:
            conn.close()
        rc = (_json("POST", f"/containers/{cid}/wait", body={}, timeout=None) or {}).get("StatusCode", 1)
        if _state["cancel"]:
            raise RuntimeError("abgebrochen")
        if rc != 0 or not any("RMM-BUILDER-READY" in ln for ln in _state["log"]):
            raise RuntimeError(f"Einrichtung fehlgeschlagen (Code {rc}) – siehe Protokoll")
        _log("Speichere das Bau-Image (kann einige Minuten dauern) …")
        q = urllib.parse.urlencode({"container": cid, "repo": REPO, "tag": TAG,
                                    "comment": "RAPALLE.net RMM Client-Bau-Umgebung"})
        _json("POST", f"/commit?{q}", {"Env": BUILD_ENV, "WorkingDir": "/work/client", "Cmd": ["bash"],
                                       "Labels": {LABEL: TAG}}, timeout=600)
    finally:
        _remove_container(cid)
    _remove_stale_images()
    _log(f"Bau-Umgebung bereit: {IMAGE}")


def _setup_line(ln: str) -> None:
    low = ln.lower()
    if ln.startswith("== Windows-Python"):
        _state["apt_done"] = True
    # apt-Fortschritt kuerzen, Rest durchreichen
    if not _state.get("apt_done") and ln.startswith(("Get:", "Hit:", "Selecting previously", "Preparing to unpack", "Unpacking ", "Setting up ",
                      "Processing triggers", "(Reading database", "  ", "The following", "Suggested packages",
                      "Recommended packages", "Reading package lists", "Building dependency tree",
                      "Reading state information", "update-alternatives:", "Created symlink", "invoke-rc.d",
                      "Adding ", "debconf:", "Current default time zone", "Local time is", "Universal Time is",
                      "Run 'dpkg-reconfigure")):
        return
    if "debconf" in low and "falling back" in low:
        return
    _log(ln)


def _remove_stale_images() -> None:
    try:
        imgs = _json("GET", "/images/json?filters=" + urllib.parse.quote(json.dumps({"label": [LABEL]})), timeout=30) or []
    except Exception:
        return
    for im in imgs:
        tags = im.get("RepoTags") or []
        if IMAGE in tags:
            continue
        try:
            _raw("DELETE", f"/images/{im['Id']}?force=1", timeout=120)
        except Exception:
            pass


def remove_image() -> None:
    """Modul aus: Bau-Image (und Reste) entfernen - es enthaelt keine Daten."""
    _state["cancel"] = True
    _remove_container(_state.get("container"))
    if env_info()["mode"] != "docker":
        return
    try:
        cs = _json("GET", "/containers/json?all=1&filters=" + urllib.parse.quote(json.dumps({"label": [LABEL]})),
                   timeout=30) or []
        for c in cs:
            _remove_container(c.get("Id"))
    except Exception:
        pass
    try:
        imgs = _json("GET", "/images/json?filters=" + urllib.parse.quote(json.dumps({"label": [LABEL]})), timeout=30) or []
        for im in imgs:
            _raw("DELETE", f"/images/{im['Id']}?force=1", timeout=300)
    except Exception as e:      # noqa: BLE001
        print(f"[client] Bau-Image konnte nicht entfernt werden: {e}")


# ===================================================================== Bauen im Container
def _is_pkg(name: str) -> bool:
    low = name.lower()
    return low.startswith("rapalle") and low.endswith(
        (".exe", ".msi", ".zip", ".deb", ".rpm", ".pkg.tar.xz", ".tar.gz", ".tgz", ".tar", ".apk"))


def _source_tar(out_dir: Path) -> bytes:
    """tar mit work/client (Quellcode, ohne release/tests) und out/ (bisherige Pakete)."""
    buf = io.BytesIO()
    skip = {"release", "tests", "__pycache__", ".buildtools", ".pytest_cache"}
    with tarfile.open(fileobj=buf, mode="w") as tf:
        def add_dir(arc):
            ti = tarfile.TarInfo(arc)
            ti.type, ti.mode, ti.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
            tf.addfile(ti)
        add_dir("work")
        add_dir("work/client")
        add_dir("out")
        for p in sorted(CLIENT_DIR.rglob("*")):
            rel = p.relative_to(CLIENT_DIR)
            if any(part in skip for part in rel.parts):
                continue
            tf.add(p, arcname=f"work/client/{rel.as_posix()}", recursive=False)
        if out_dir.is_dir():
            for p in out_dir.iterdir():
                if p.is_file() and _is_pkg(p.name):
                    tf.add(p, arcname=f"out/{p.name}", recursive=False)
    return buf.getvalue()


def run_in_container(args: list[str], out_dir: Path, on_line, on_container) -> int:
    """build_installers.py im Bau-Image ausfuehren und out_dir mit dem Ergebnis abgleichen.
    on_container(cid) bekommt die Container-ID (fuer Stopp). Rueckgabe: Exit-Code (-1 = abgebrochen)."""
    if not image_ready():
        raise RuntimeError("Die Bau-Umgebung ist noch nicht bereit (Modul-Einstellungen → „Bau-Umgebung vorbereiten“).")
    # /out vorab anlegen: scheitert der Bau frueh, findet der Abgleich sonst "/out" nicht
    cfg = {"Image": IMAGE, "Cmd": ["sh", "-c", 'mkdir -p /out && exec python3 -u /work/client/build_installers.py --out /out "$@"',
                                   "sh", *args],
           "WorkingDir": "/work/client", "Env": BUILD_ENV, "Labels": {LABEL: "build"},
           "HostConfig": {"AutoRemove": False, "NetworkMode": "none"}}
    cid = _json("POST", "/containers/create", cfg)["Id"]
    on_container(cid)
    try:
        _raw("PUT", f"/containers/{cid}/archive?path=/", body=_source_tar(out_dir), ctype="application/x-tar",
             timeout=600)
        _raw("POST", f"/containers/{cid}/start", body=b"", timeout=60)
        conn, resp = _raw("GET", f"/containers/{cid}/logs?follow=1&stdout=1&stderr=1", timeout=None, stream=True)
        try:
            _demux(resp, on_line)
        finally:
            conn.close()
        try:
            rc = (_json("POST", f"/containers/{cid}/wait", body={}, timeout=None) or {}).get("StatusCode", 1)
        except Exception:
            return -1                    # Container wurde (per Stopp) entfernt
        if rc in (137, 143):             # SIGKILL/SIGTERM -> Stopp
            return -1
        # Ergebnis holen: /out komplett, dann storage/client abgleichen
        try:
            conn, resp = _raw("GET", f"/containers/{cid}/archive?path=/out", timeout=600, stream=True)
        except Exception as e:  # noqa: BLE001
            on_line(f"Keine Pakete abholbar (/out): {e}")
            return rc or 1
        try:
            data = resp.read()
        finally:
            conn.close()
        result: dict[str, bytes] = {}
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            for m in tf.getmembers():
                name = Path(m.name).name
                if m.isfile() and _is_pkg(name) and "/" not in name:
                    f = tf.extractfile(m)
                    if f:
                        result[name] = f.read()
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in out_dir.iterdir():                  # vom Skript entfernte (alte) Versionen auch hier entfernen
            if p.is_file() and _is_pkg(p.name) and p.name not in result:
                p.unlink()
        for name, b in result.items():
            dest = out_dir / name
            if not dest.exists() or dest.read_bytes() != b:
                tmp = dest.with_name("." + name + ".tmp")
                tmp.write_bytes(b)
                os.replace(tmp, dest)
        return rc
    finally:
        _remove_container(cid)


def kill_container(cid: str | None) -> None:
    if not cid:
        return
    try:
        _raw("POST", f"/containers/{cid}/kill", body=b"", timeout=30)
    except Exception:
        pass

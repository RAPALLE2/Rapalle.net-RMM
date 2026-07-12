"""
agent.py
--------
Das Programm, das auf JEDEM Rechner läuft, den du über RAPALLE.net RMM
verwalten willst (dein PC, ein Server, ein anderer Windows/Linux-Rechner).

Was der Agent tut:
  1. Verbindet sich per Socket.IO zum Backend (Namespace "/agent")
  2. Meldet sich einmalig an ("register") mit Hostname/OS/IP
  3. Schickt alle 5 Sekunden einen "heartbeat" mit aktuellen Metriken
     (CPU-Last, RAM, Festplatte, Laufzeit) - dafür nutzen wir die
     Bibliothek "psutil", die auf Windows UND Linux gleich funktioniert
  4. Wartet auf Befehle vom Backend:
       - "exec"    -> führt einen Shell-Befehl aus, schickt das Ergebnis zurück
       - "fs-list" -> listet einen Ordner auf, schickt die Liste zurück

Startet man den Agenten mehrfach hintereinander, behält er über die Datei
".device-id" immer dieselbe eindeutige ID - so "vergisst" das Backend den
Client nicht bei jedem Neustart.
"""

# WICHTIG: macht alle Typ-Annotationen "lazy" (werden als Strings gespeichert und
# nicht zur Laufzeit ausgewertet). Dadurch funktioniert die moderne Schreibweise
# "str | None" auch auf Python 3.8/3.9 (viele Windows-Installationen), wo sie
# sonst einen TypeError wirft. MUSS die allererste Anweisung nach dem Docstring sein.
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path


# --------------------------------------------------------------
# Selbst-Bootstrap: fehlende Bibliotheken automatisch nachinstallieren.
# Der Agent besteht (nach Installation) aus agent.py + requirements.txt.
# Fehlt eine Lib (z.B. weil requirements.txt erweitert wurde), installieren
# wir sie hier NACH, bevor wir sie importieren - sonst stürzt der Import ab.
# --------------------------------------------------------------
def _bootstrap_deps() -> None:
    # PyPI-Name -> Importname (nur wo abweichend)
    needed = {
        "python-socketio[asyncio_client]": "socketio",
        "aiohttp": "aiohttp",
        "psutil": "psutil",
        "python-dotenv": "dotenv",
        "mss": "mss",
        "Pillow": "PIL",
        "pynput": "pynput",
        "pyperclip": "pyperclip",
    }
    # Windows: pywinpty für das interaktive Terminal (ConPTY).
    if os.name == "nt":
        needed["pywinpty"] = "winpty"
    import importlib
    missing_specs = []
    for spec, mod in needed.items():
        try:
            importlib.import_module(mod)
        except Exception:
            missing_specs.append(spec)
    if not missing_specs:
        return
    print(f"[agent-bootstrap] Installiere fehlende Pakete: {', '.join(missing_specs)}")
    req = Path(__file__).resolve().parent / "requirements.txt"
    # Auch hier kein Konsolenfenster aufblitzen lassen (Windows). _run ist an
    # dieser frühen Stelle noch nicht definiert, daher die Flags inline.
    _boot_kw = {}
    if platform.system() == "Windows":
        _si = subprocess.STARTUPINFO()
        _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        _si.wShowWindow = 0
        _boot_kw = {"startupinfo": _si, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    try:
        if req.is_file():
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)],
                           check=False, timeout=600, **_boot_kw)
        else:
            subprocess.run([sys.executable, "-m", "pip", "install", *missing_specs],
                           check=False, timeout=600, **_boot_kw)
    except Exception as e:
        print(f"[agent-bootstrap] pip fehlgeschlagen: {e}")


_bootstrap_deps()

import psutil
import socketio
from dotenv import load_dotenv

# --- Logging einrichten: schreibt sowohl auf die Konsole als auch in eine
# Datei "agent.log" neben diesem Skript. So kann man Fehler auch dann sehen,
# wenn der Agent unsichtbar als Hintergrunddienst/geplante Aufgabe läuft. ---
_LOG_FILE = Path(__file__).resolve().parent / "agent.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("agent")


def _print(msg):
    """Ersetzt print() - schreibt in Konsole UND Log-Datei."""
    log.info(msg)


# Optionale Bibliotheken für Remote Screen. Der Agent funktioniert auch ohne sie
# (dann ist nur die Fernsteuerung deaktiviert, alles andere läuft normal weiter).
# Wir fangen bewusst JEDEN Fehler ab (nicht nur ImportError), weil z.B. pynput
# beim Import in einer Umgebung ohne grafische Sitzung (Windows-Dienst als
# SYSTEM, Linux ohne X11) mit anderen Fehlern abbrechen kann - das darf den
# Agenten NICHT komplett lahmlegen.
try:
    import mss  # Screenshots aufnehmen (schnell, plattformübergreifend)
    from PIL import Image  # zum Verkleinern/Kodieren der Screenshots
    _SCREEN_AVAILABLE = True
except Exception:
    _SCREEN_AVAILABLE = False

try:
    from pynput.mouse import Controller as MouseController, Button as MouseButton
    from pynput.keyboard import Controller as KeyboardController, Key as KeyboardKey
    _INPUT_AVAILABLE = True
except Exception:
    _INPUT_AVAILABLE = False

# .env Datei aus demselben Ordner wie dieses Skript laden
load_dotenv(Path(__file__).resolve().parent / ".env")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:4000")
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "change-me-super-secret")
ENROLLMENT_TOKEN = os.getenv("ENROLLMENT_TOKEN", "").strip() or None
DEVICE_NAME = os.getenv("DEVICE_NAME") or socket.gethostname()

IS_WINDOWS = platform.system() == "Windows"

# --------------------------------------------------------------------------
# Subprozesse IMMER ohne sichtbares Konsolenfenster starten. Unter Windows
# öffnet subprocess.run() sonst bei jedem Aufruf kurz ein schwarzes CMD-/
# PowerShell-Fenster - das darf beim 5-Sekunden-Heartbeat niemals passieren.
# _run() kapselt die nötigen Flags (CREATE_NO_WINDOW + versteckte STARTUPINFO)
# und wird ab hier ÜBERALL statt subprocess.run() verwendet.
# --------------------------------------------------------------------------
def _no_window_kwargs() -> dict:
    if not IS_WINDOWS:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return {"startupinfo": si, "creationflags": flags}


def _run(cmd, **kwargs):
    """subprocess.run-Ersatz, der unter Windows kein Fenster aufblitzen lässt."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    for k, v in _no_window_kwargs().items():
        kwargs.setdefault(k, v)
    return subprocess.run(cmd, **kwargs)


# TTL-Cache: teure Abfragen (nvidia-smi, WMI, Ping) NICHT bei jedem 5s-Heartbeat
# ausführen, sondern höchstens alle `ttl` Sekunden. Zwischenzeitlich wird der
# gemerkte Wert zurückgegeben.
_ttl_cache: dict = {}


def _ttl(key: str, ttl: float, fn):
    now = time.time()
    ent = _ttl_cache.get(key)
    if ent and (now - ent[0]) < ttl:
        return ent[1]
    val = fn()
    _ttl_cache[key] = (now, val)
    return val


def get_os_release() -> str:
    """
    Ermittelt einen aussagekräftigen OS-Namen:
    - Linux: liest /etc/os-release und gibt z.B. "Ubuntu 22.04" oder
      "Debian GNU/Linux 12" oder "Proxmox VE" zurück (statt nur Kernel-Version).
    - Windows: gibt z.B. "Windows 11" bzw. "Windows Server 2022" zurück.
    - Sonst: platform.release() als Fallback.
    """
    try:
        if IS_WINDOWS:
            ver = platform.version()      # z.B. "10.0.22621"
            release = platform.release()  # "10" / "11" / "Server..."
            try:
                build = int(ver.split(".")[2])
                if "server" in release.lower():
                    return f"Windows Server ({ver})"
                if build >= 22000:
                    return "Windows 11"
                return "Windows 10"
            except (IndexError, ValueError):
                return f"Windows {release}"
        else:
            info = {}
            try:
                with open("/etc/os-release", "r", encoding="utf-8") as f:
                    for line in f:
                        if "=" in line:
                            k, v = line.rstrip("\n").split("=", 1)
                            info[k] = v.strip().strip('"')
            except FileNotFoundError:
                pass
            if info.get("PRETTY_NAME"):
                return info["PRETTY_NAME"]
            if info.get("NAME"):
                return f"{info['NAME']} {info.get('VERSION_ID', '')}".strip()
    except Exception:
        pass
    return platform.release()


OS_RELEASE = get_os_release()

# Datei, in der wir uns die eigene, dauerhafte Geräte-ID merken
ID_FILE = Path(__file__).resolve().parent / ".device-id"


def get_or_create_device_id() -> str:
    """Liest die gespeicherte Geräte-ID, oder erzeugt beim allerersten Start eine neue."""
    if ID_FILE.exists():
        return ID_FILE.read_text().strip()
    new_id = str(uuid.uuid4())
    ID_FILE.write_text(new_id)
    return new_id


DEVICE_ID = get_or_create_device_id()

# Nach einem Update legt das Update-Skript die Markerdatei ".updated" an. Beim
# nächsten Start liest der Agent sie, meldet dem Backend "updated: true" (damit
# das Dashboard das Update als erfolgreich bestätigen kann) und löscht sie.
UPDATED_MARKER = Path(__file__).resolve().parent / ".updated"


def _consume_update_marker() -> bool:
    try:
        if UPDATED_MARKER.exists():
            UPDATED_MARKER.unlink()
            return True
    except Exception:
        pass
    return False


_JUST_UPDATED = _consume_update_marker()

# Eigene Agent-Version (aus version.txt neben diesem Skript), damit das Dashboard
# "veraltete" Agenten erkennen kann.
def _read_agent_version() -> str:
    try:
        return (Path(__file__).resolve().parent / "version.txt").read_text(encoding="utf-8").strip() or "unbekannt"
    except Exception:
        return "unbekannt"


AGENT_VERSION = _read_agent_version()

# Der Socket.IO-Client, über den die gesamte Kommunikation läuft
# EINEN festen Event-Loop erzeugen und als aktuellen setzen, BEVOR der
# socketio-Client erstellt wird. Grund: 'sio' wird beim Import gebaut; würde man
# main() später über asyncio.run() starten, liefe alles auf einem ANDEREN Loop
# -> "Future attached to a different loop". Wir laufen main() unten explizit auf
# genau diesem Loop (run_until_complete).
try:
    _AGENT_LOOP = asyncio.get_event_loop()
    if _AGENT_LOOP.is_closed():
        raise RuntimeError("closed")
except RuntimeError:
    _AGENT_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_AGENT_LOOP)

# reconnection=False: Wir steuern das Wiederverbinden SELBST (siehe main()).
# Sonst kämpfen die interne Auto-Reconnection und unsere Schleife gegeneinander
# -> Fehler "Already connected" und "Future attached to a different loop", weil
# sich zwei Verbindungs-Lebenszyklen überlappen.
sio = socketio.AsyncClient(reconnection=False)


def get_local_ip() -> str | None:
    """Ermittelt die eigene lokale IP-Adresse (siehe Erklärung in network_scan.py im Backend)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


# --------------------------------------------------------------
# Verbindungsaufbau & Registrierung
# --------------------------------------------------------------

@sio.event(namespace="/agent")
async def connect():
    """Wird automatisch aufgerufen, sobald die Verbindung zum Backend steht."""
    global _JUST_UPDATED
    _print(f"[agent] Verbunden mit {BACKEND_URL} als '{DEVICE_NAME}' ({DEVICE_ID})")
    await sio.emit(
        "register",
        {
            "id": DEVICE_ID,
            "hostname": DEVICE_NAME,
            "platform": platform.system(),       # "Windows" oder "Linux"
            "arch": platform.machine(),           # z.B. "AMD64", "x86_64"
            "release": OS_RELEASE,                # z.B. "Ubuntu 22.04" / "Windows 11"
            "ip": get_local_ip(),
            "enrollment_token": ENROLLMENT_TOKEN,  # nur beim ersten Mal relevant
            "updated": _JUST_UPDATED,              # true = kommt frisch aus einem Update
            "agent_version": AGENT_VERSION,        # eigene Version (für "veraltet"-Hinweis)
        },
        namespace="/agent",
    )
    if _JUST_UPDATED:
        _print("[agent] Update-Bestätigung an das Backend gesendet.")
        _JUST_UPDATED = False  # nur einmal melden, nicht bei jedem Reconnect


@sio.event(namespace="/agent")
async def connect_error(data):
    _print(f"[agent] Verbindungsfehler zu {BACKEND_URL}: {data!r} "
           f"(Prüfen: erreichbar? TLS-Zertifikat gültig? Proxy leitet /socket.io weiter?)")


# --------------------------------------------------------------
# Regelmäßige Metriken (Heartbeat)
# --------------------------------------------------------------

# Merker für die Netzwerk-Durchsatz-Berechnung (Differenz zwischen zwei Messungen)
_last_net = {"ts": None, "bytes_sent": 0, "bytes_recv": 0}


# ------------------------------------------------------------------
# Erweiterte Hardware-/Telemetrie-Erfassung
# ------------------------------------------------------------------
# Alle Zusatzwerte sind BEST-EFFORT: Fehlt ein Sensor/Modul auf einer
# Plattform, wird der Wert einfach weggelassen statt einen Fehler zu werfen.
# Statische Infos (CPU-/RAM-/GPU-Modell) werden einmalig ermittelt und
# zwischengespeichert, dynamische Werte (Temp, Takt, IO, Ping) je Zyklus.

_static_hw_cache = None            # einmalig ermittelte, unveränderliche Infos
_last_diskio = {"ts": None, "read": 0, "write": 0}
_ping_targets = {"google": "8.8.8.8", "cloudflare": "1.1.1.1"}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _cpu_model() -> str:
    # Plattformübergreifend den CPU-Namen ermitteln.
    if IS_WINDOWS:
        # platform.processor() liefert nur die Kennung ("AMD64 Family 25 ...").
        # Der echte Marketing-Name steht in der Registry - ohne Subprozess/Fenster.
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                               r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
            winreg.CloseKey(k)
            if name and name.strip():
                return name.strip()
        except Exception:
            pass
        # Fallback: WMI-Name (hidden window über _run).
        txt = _safe(lambda: _run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_Processor).Name"], timeout=6).stdout) or ""
        for line in txt.splitlines():
            if line.strip():
                return line.strip()
        return _safe(lambda: __import__("platform").processor()) or "unbekannt"
    else:
        info = _safe(lambda: open("/proc/cpuinfo").read()) or ""
        for line in info.splitlines():
            if "model name" in line:
                return line.split(":", 1)[1].strip()
    return _safe(lambda: __import__("platform").processor()) or "unbekannt"


def _gpu_models() -> list:
    # GPU-Namen best effort. Auf Linux via lspci, auf Windows via WMI/cim.
    out = []
    if IS_WINDOWS:
        txt = _safe(lambda: _run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=6).stdout) or ""
        out = [l.strip() for l in txt.splitlines() if l.strip()]
    else:
        txt = _safe(lambda: _run(["lspci"], capture_output=True, text=True, timeout=6).stdout) or ""
        for line in txt.splitlines():
            low = line.lower()
            if "vga compatible" in low or "3d controller" in low or " display " in low:
                out.append(line.split(":", 2)[-1].strip())
    return out[:4]


def _ram_modules() -> list:
    # RAM-Riegel (Hersteller/Größe/Takt) best effort.
    mods = []
    if IS_WINDOWS:
        txt = _safe(lambda: _run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_PhysicalMemory | ForEach-Object { \"$($_.Manufacturer)|$($_.Capacity)|$($_.Speed)|$($_.PartNumber)\" }"],
            capture_output=True, text=True, timeout=6).stdout) or ""
        for line in txt.splitlines():
            p = line.strip().split("|")
            if len(p) >= 3 and p[1].isdigit():
                vendor = (p[0] or "").strip()
                part = (p[3].strip() if len(p) > 3 else "")
                # Manufacturer ist bei vielen Riegeln "Unknown"/leer -> dann die
                # Teilenummer als aussagekräftigeren Namen verwenden.
                if not vendor or vendor.lower() in ("unknown", "n/a", "0"):
                    vendor = part or vendor or "?"
                mods.append({"vendor": vendor, "size": int(p[1]),
                             "speed": _safe(lambda: int(p[2])) or 0,
                             "part": part})
    else:
        # dmidecode braucht i.d.R. root; nur nutzen, wenn verfügbar.
        txt = _safe(lambda: _run(["dmidecode", "-t", "memory"],
                    capture_output=True, text=True, timeout=6).stdout) or ""
        cur = {}
        for line in txt.splitlines():
            s = line.strip()
            if s.startswith("Size:") and "No Module" not in s:
                cur = {"raw_size": s.split(":", 1)[1].strip()}
            elif s.startswith("Manufacturer:") and cur:
                cur["vendor"] = s.split(":", 1)[1].strip()
            elif s.startswith("Speed:") and cur:
                cur["speed_str"] = s.split(":", 1)[1].strip()
                mods.append({"vendor": cur.get("vendor", "?"),
                             "size_str": cur.get("raw_size", "?"),
                             "speed_str": cur.get("speed_str", "?")})
                cur = {}
    return mods[:8]


def _static_hardware() -> dict:
    global _static_hw_cache
    if _static_hw_cache is not None:
        return _static_hw_cache
    plat = __import__("platform")
    info = {
        "cpuModel": _cpu_model(),
        "arch": _safe(lambda: plat.machine()) or "",
        "gpuModels": _gpu_models(),
        "ramModules": _ram_modules(),
        "cpuMaxFreq": _safe(lambda: round(psutil.cpu_freq().max)) if _safe(lambda: psutil.cpu_freq()) else None,
    }
    _static_hw_cache = info
    return info


def _cpu_temp() -> float | None:
    temps = _safe(lambda: psutil.sensors_temperatures())
    if temps:
        # Bevorzugt CPU-nahe Sensoren, sonst den ersten verfügbaren.
        for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz", "zenpower"):
            if key in temps and temps[key]:
                return round(temps[key][0].current, 1)
        for arr in temps.values():
            if arr:
                return round(arr[0].current, 1)
    if IS_WINDOWS:
        # Windows: psutil kann keine Temperaturen -> WMI-Thermalzone (Zehntel-
        # Kelvin). Teuer -> nur alle 30 s (TTL-Cache). Für echte CPU-Temperatur
        # auf AMD/Intel-Desktops zusätzlich LibreHardwareMonitor (siehe _win_hw_sensors).
        return _ttl("cputemp_win", 30, _cpu_temp_windows)
    # Linux-Fallback: hwmon direkt (k10temp/coretemp liefern hier Tctl/Package).
    hw_temps, _ = _read_hwmon()
    for pref in ("tctl", "tdie", "package", "cpu"):
        for name, v in hw_temps.items():
            if pref in name.lower():
                return v
    return next(iter(hw_temps.values()), None)


def _cpu_temp_windows() -> float | None:
    txt = _safe(lambda: _run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
         "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty CurrentTemperature "
         "| Measure-Object -Maximum).Maximum"],
        capture_output=True, text=True, timeout=6).stdout) or ""
    v = _safe(lambda: int(txt.strip()))
    if v and v > 0:
        return round(v / 10.0 - 273.15, 1)   # Zehntel-Kelvin -> °C
    return None


def _read_hwmon():
    """Linux-Fallback: Temperaturen & Lüfter direkt aus /sys/class/hwmon lesen
    (falls psutil.sensors_* nichts liefert, z.B. ohne lm-sensors-Konfig).
    Gibt (temps{Name:°C}, fans{Name:U/min}) zurück."""
    import glob as _glob
    temps, fans = {}, {}
    for hw in _glob.glob("/sys/class/hwmon/hwmon*"):
        name = _safe(lambda hw=hw: open(hw + "/name").read().strip()) or os.path.basename(hw)
        for tf in _glob.glob(hw + "/temp*_input"):
            v = _safe(lambda tf=tf: int(open(tf).read().strip()))
            if v is None:
                continue
            lp = tf.replace("_input", "_label")
            lbl = _safe(lambda lp=lp: open(lp).read().strip()) or os.path.basename(tf).replace("_input", "")
            temps[f"{name}: {lbl}"] = round(v / 1000.0, 1)
        for ff in _glob.glob(hw + "/fan*_input"):
            v = _safe(lambda ff=ff: int(open(ff).read().strip()))
            if v is None:
                continue
            fans[f"{name}: {os.path.basename(ff).replace('_input', '')}"] = int(v)
    return temps, fans


def _all_temps() -> dict:
    """Alle Temperatur-Sensoren als {Name: °C}. Erst psutil, dann hwmon-Fallback."""
    out = {}
    temps = _safe(lambda: psutil.sensors_temperatures()) or {}
    for chip, arr in temps.items():
        for i, s in enumerate(arr):
            label = (s.label or f"{chip} {i}").strip()
            name = f"{chip}: {label}" if label and label.lower() not in chip.lower() else chip
            if s.current is not None:
                out[name] = round(s.current, 1)
    if not out and not IS_WINDOWS:
        hw_temps, _ = _read_hwmon()
        out.update(hw_temps)
    return out


def _all_fans() -> dict:
    """Alle Lüfter als {Name: U/min}. Erst psutil, dann hwmon-Fallback."""
    out = {}
    fans = _safe(lambda: psutil.sensors_fans()) or {}
    for chip, arr in fans.items():
        for i, f in enumerate(arr):
            label = (f.label or f"{chip} {i}").strip()
            if f.current is not None:
                out[label] = int(f.current)
    if not out and not IS_WINDOWS:
        _, hw_fans = _read_hwmon()
        out.update(hw_fans)
    return out


def _fan_speed() -> int | None:
    fans = _safe(lambda: psutil.sensors_fans())
    if not fans:
        # Linux-Fallback aus hwmon
        if not IS_WINDOWS:
            _, hw_fans = _read_hwmon()
            if hw_fans:
                return int(next(iter(hw_fans.values())))
        return None
    for arr in fans.values():
        if arr:
            return int(arr[0].current)
    return None


def _gpus() -> list:
    """GPU-Telemetrie über nvidia-smi (falls vorhanden): Name, Auslastung %,
    VRAM belegt/gesamt, Temperatur, Leistungsaufnahme. Funktioniert plattform-
    übergreifend, wenn NVIDIA-Treiber installiert sind."""
    txt = _safe(lambda: _run(
        ["nvidia-smi",
         "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=4).stdout)
    if not txt:
        return []
    gpus = []
    for line in txt.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        def _f(x):
            try: return float(x)
            except Exception: return None
        gpus.append({
            "name": parts[0],
            "load": _f(parts[1]),                                  # %
            "memUsed": int(_f(parts[2]) * 1024 * 1024) if _f(parts[2]) is not None else None,  # MiB->Bytes
            "memTotal": int(_f(parts[3]) * 1024 * 1024) if _f(parts[3]) is not None else None,
            "temp": _f(parts[4]),                                  # °C
            "power": _f(parts[5]),                                 # W
        })
    return gpus[:4]


def _amd_gpus_sysfs() -> list:
    """AMD-GPU-Telemetrie über sysfs (Linux, amdgpu-Treiber): Auslastung,
    Temperatur, Leistungsaufnahme, VRAM. Keine externen Tools nötig."""
    if IS_WINDOWS:
        return []
    import glob as _glob
    gpus = []
    for card in sorted(_glob.glob("/sys/class/drm/card[0-9]")):
        dev = card + "/device"
        busy = _safe(lambda dev=dev: int(open(dev + "/gpu_busy_percent").read().strip()))
        if busy is None:
            continue   # kein amdgpu-Sensor -> überspringen
        temp = power = None
        for hw in _glob.glob(dev + "/hwmon/hwmon*"):
            t = _safe(lambda hw=hw: int(open(hw + "/temp1_input").read().strip()))
            if t is not None:
                temp = round(t / 1000.0, 1)
            p = _safe(lambda hw=hw: int(open(hw + "/power1_average").read().strip()))
            if p is not None:
                power = round(p / 1_000_000.0, 1)
        mem_total = _safe(lambda dev=dev: int(open(dev + "/mem_info_vram_total").read().strip()))
        mem_used = _safe(lambda dev=dev: int(open(dev + "/mem_info_vram_used").read().strip()))
        name = _safe(lambda dev=dev: open(dev + "/product_name").read().strip()) or "AMD GPU"
        gpus.append({"name": name, "load": float(busy), "temp": temp, "power": power,
                     "memUsed": mem_used, "memTotal": mem_total})
    return gpus[:4]


def _win_hw_sensors() -> dict:
    """Windows: Sensoren (CPU-Temp, Lüfter, Package-Power, GPU von AMD/NVIDIA/
    Intel) über LibreHardwareMonitor bzw. OpenHardwareMonitor auslesen - die
    zuverlässigste Quelle für diese Werte unter Windows. Voraussetzung: eines
    der Tools läuft. Ohne das kann Windows CPU-Temperatur, Lüfter und
    Leistungsaufnahme technisch nicht bereitstellen (psutil unterstützt keine
    Windows-Sensoren). Rückgabe (nur vorhandene Schlüssel): cpuTemp, temps{},
    fans{}, powerWatts, gpus[]."""
    if not IS_WINDOWS:
        return {}
    data = None
    for ns in ("root/LibreHardwareMonitor", "root/OpenHardwareMonitor"):
        txt = _safe(lambda ns=ns: _run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance -Namespace {ns} -ClassName Sensor -ErrorAction SilentlyContinue | "
             "Select-Object Name,SensorType,Value,Identifier,Parent | ConvertTo-Json -Compress"],
            timeout=8).stdout)
        if txt and txt.strip():
            try:
                parsed = json.loads(txt)
                data = parsed if isinstance(parsed, list) else [parsed]
                if data:
                    break
            except Exception:
                data = None
    if not data:
        return {}

    temps, fans = {}, {}
    gpu_acc = {}
    cpu_temp_candidates = {}
    pkg_power = None

    for s in data:
        st = (s.get("SensorType") or "").lower()
        nm = (s.get("Name") or "").strip()
        val = s.get("Value")
        ident = (s.get("Identifier") or "").lower()
        parent = (s.get("Parent") or ident).lower()
        if val is None:
            continue
        try: val = round(float(val), 1)
        except Exception: continue

        is_gpu = "gpu" in ident or "gpu" in parent
        if st == "temperature":
            temps[nm] = val
            if is_gpu:
                gpu_acc.setdefault(parent, {})["temp"] = val
            elif "cpu" in ident or "core" in nm.lower() or "tctl" in nm.lower() or "package" in nm.lower():
                cpu_temp_candidates[nm] = val
        elif st == "fan":
            fans[nm] = int(val)
        elif st == "power":
            if is_gpu:
                gpu_acc.setdefault(parent, {})["power"] = val
            elif "package" in nm.lower() or "cpu" in nm.lower() or "total" in nm.lower():
                pkg_power = max(pkg_power or 0, val)
        elif st == "load":
            if is_gpu and ("core" in nm.lower() or "gpu" in nm.lower()):
                gpu_acc.setdefault(parent, {}).setdefault("load", val)
        elif st in ("smalldata", "data"):
            low = nm.lower()
            if is_gpu and "memory" in low:
                g = gpu_acc.setdefault(parent, {})
                if "used" in low: g["memUsed"] = int(val * 1024 * 1024)
                elif "total" in low: g["memTotal"] = int(val * 1024 * 1024)

    out = {}
    if temps: out["temps"] = temps
    if fans: out["fans"] = fans
    if pkg_power is not None: out["powerWatts"] = pkg_power
    if cpu_temp_candidates:
        pick = None
        for key in ("tctl", "tdie", "cpu package", "core (tctl", "cpu total"):
            for nm, v in cpu_temp_candidates.items():
                if key in nm.lower(): pick = v; break
            if pick is not None: break
        if pick is None:
            pick = round(sum(cpu_temp_candidates.values()) / len(cpu_temp_candidates), 1)
        out["cpuTemp"] = pick
    gpus = []
    for parent, g in gpu_acc.items():
        name = parent.split("/")[-2] if "/" in parent else "GPU"
        gpus.append({"name": name.upper(), "load": g.get("load"), "temp": g.get("temp"),
                     "power": g.get("power"), "memUsed": g.get("memUsed"), "memTotal": g.get("memTotal")})
    if gpus: out["gpus"] = gpus
    return out


def _battery() -> dict | None:
    b = _safe(lambda: psutil.sensors_battery())
    if not b:
        return None
    return {"percent": round(b.percent, 1),
            "plugged": bool(b.power_plugged),
            "secsleft": (b.secsleft if b.secsleft not in (None, psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else None)}


def _power_watts() -> float | None:
    # Momentane Leistungsaufnahme (W), best effort. Zwei Quellen (Linux):
    #  1) RAPL-Energiezähler ALLER Pakete (intel-rapl:0, :1, ...) -> dJ/dt.
    #  2) hwmon power*_input (Mikrowatt, momentan) als Fallback.
    if IS_WINDOWS:
        return None
    import glob as _glob
    # (1) RAPL: nur die Paket-Zonen (intel-rapl:N), nicht die Sub-Domains :N:M.
    pkgs = sorted(p for p in _glob.glob("/sys/class/powercap/intel-rapl:*/energy_uj")
                  if p.count(":") == 1)
    total_uj = 0
    found = False
    for path in pkgs:
        uj = _safe(lambda p=path: int(open(p).read().strip()))
        if uj is not None:
            total_uj += uj
            found = True
    if found:
        now = time.time()
        prev = getattr(_power_watts, "_prev", None)
        _power_watts._prev = (now, total_uj)
        if prev:
            dt = now - prev[0]
            dj = (total_uj - prev[1]) / 1_000_000.0
            if dt > 0 and dj >= 0:
                return round(dj / dt, 1)
        # beim ersten Aufruf noch kein dt -> auf hwmon ausweichen
    # (2) hwmon momentane Leistung (Mikrowatt).
    watts = 0.0
    any_hw = False
    for path in _glob.glob("/sys/class/hwmon/hwmon*/power*_input"):
        uw = _safe(lambda p=path: int(open(p).read().strip()))
        if uw:
            watts += uw / 1_000_000.0
            any_hw = True
    if any_hw:
        return round(watts, 1)
    return None


def _disk_io_speed() -> dict:
    # Lese-/Schreibrate (Bytes/s) aus den Gesamt-Zählern ableiten.
    io = _safe(lambda: psutil.disk_io_counters())
    if not io:
        return {}
    now = time.time()
    res = {}
    if _last_diskio["ts"] is not None:
        dt = now - _last_diskio["ts"]
        if dt > 0:
            res["diskRead"] = round((io.read_bytes - _last_diskio["read"]) / dt)
            res["diskWrite"] = round((io.write_bytes - _last_diskio["write"]) / dt)
    _last_diskio["ts"] = now
    _last_diskio["read"] = io.read_bytes
    _last_diskio["write"] = io.write_bytes
    return res


def _ping_ms(host: str) -> float | None:
    # Ein einzelner Ping über das System-Tool. Parsing mehrsprachig
    # (time=/Zeit=/tiempo=/temps=) mit Durchschnitts- und Zahl-Fallback.
    param = "-n" if IS_WINDOWS else "-c"
    timeout_param = "-w" if IS_WINDOWS else "-W"
    tval = "1000" if IS_WINDOWS else "1"
    try:
        r = _run(["ping", param, "1", timeout_param, tval, host], timeout=4, errors="ignore")
        out = (r.stdout or "").lower()
        import re as _re
        m = _re.search(r"(?:time|zeit|tiempo|temps|tempo|czas)[=<]\s*([\d.,]+)\s*ms", out)
        if not m:
            m = _re.search(r"(?:average|mittelwert|moyenne|promedio|media)\s*[=:]\s*([\d.,]+)\s*ms", out)
        if not m:
            m = _re.search(r"[=<]\s*([\d.,]+)\s*ms", out)   # letzter Ausweg
        if m:
            return round(float(m.group(1).replace(",", ".")), 1)
    except Exception:
        return None
    return None


def collect_extended_metrics() -> dict:
    """Zusatz-Telemetrie (Hardware, Temperaturen, IO, Ping). Alles best effort;
    fehlende Sensoren erscheinen einfach nicht im Ergebnis."""
    ext = {}
    ext.update(_static_hardware())          # cpuModel, gpuModels, ramModules, ...

    freq = _safe(lambda: psutil.cpu_freq())
    if freq:
        ext["cpuFreq"] = round(freq.current)
    # Takt je Kern. Windows liefert meist nur einen Wert -> auf alle Kerne
    # spiegeln, damit das Panel Daten zeigt (Näherung).
    per = _safe(lambda: psutil.cpu_freq(percpu=True))
    if per and len(per) > 1:
        ext["cpuFreqPerCore"] = [round(f.current) for f in per]
    elif freq:
        n = _safe(lambda: psutil.cpu_count(logical=True)) or 0
        if n:
            ext["cpuFreqPerCore"] = [round(freq.current)] * n

    # Load Average: Linux nativ; Windows als eigener EWMA-Schätzer (psutil.get-
    # loadavg ist dort nur emuliert und anfangs 0). Wird in collect_metrics
    # gesetzt; hier nur für Nicht-Windows aus psutil.
    if not IS_WINDOWS:
        la = _safe(lambda: psutil.getloadavg())
        if la:
            ext["load1"], ext["load5"], ext["load15"] = [round(x, 2) for x in la]

    ext["procCount"] = _safe(lambda: len(psutil.pids())) or 0

    for name, fn in (("cpuTemp", _cpu_temp), ("fanSpeed", _fan_speed)):
        v = fn()
        if v is not None:
            ext[name] = v
    # Alle Temperatur-/Lüftersensoren (Linux via psutil).
    temps = _all_temps()
    if temps:
        ext["temps"] = temps
    fans = _all_fans()
    if fans:
        ext["fans"] = fans
    # GPU-Telemetrie: nvidia-smi (teuer, alle 15 s) oder AMD-sysfs (Linux, günstig).
    gpus = _ttl("gpus", 15, _gpus) or _amd_gpus_sysfs()
    if gpus:
        ext["gpus"] = gpus

    # Windows-Sensoren via LibreHardwareMonitor/OpenHardwareMonitor (CPU-Temp,
    # Lüfter, Package-Power, AMD-/NVIDIA-GPU). Alle 10 s (WMI-Aufruf). Ergänzt
    # bzw. überschreibt die obigen Felder, wenn dort nichts kam.
    if IS_WINDOWS:
        hw = _ttl("winhw", 10, _win_hw_sensors)
        if hw:
            if hw.get("temps"): ext["temps"] = {**ext.get("temps", {}), **hw["temps"]}
            if hw.get("fans"): ext["fans"] = {**ext.get("fans", {}), **hw["fans"]}
            if "cpuTemp" not in ext and hw.get("cpuTemp") is not None: ext["cpuTemp"] = hw["cpuTemp"]
            if "fanSpeed" not in ext and hw.get("fans"):
                ext["fanSpeed"] = next(iter(hw["fans"].values()))
            if "powerWatts" not in ext and hw.get("powerWatts") is not None: ext["powerWatts"] = hw["powerWatts"]
            if not ext.get("gpus") and hw.get("gpus"): ext["gpus"] = hw["gpus"]

    bat = _battery()
    if bat:
        ext["battery"] = bat
    pw = _power_watts()
    if pw is not None:
        ext["powerWatts"] = pw

    ext.update(_disk_io_speed())            # diskRead, diskWrite (Bytes/s)

    # Ping-Ziele (Netzqualität) - startet je Ziel einen ping-Prozess, daher
    # nur alle 15 s messen (dazwischen letzten Wert wiederverwenden).
    def _measure_pings():
        out = {}
        for label, host in _ping_targets.items():
            v = _ping_ms(host)
            if v is not None:
                out[label] = v
        return out
    pings = _ttl("pings", 15, _measure_pings)
    if pings:
        ext["ping"] = pings

    return ext


def collect_metrics() -> dict:
    """
    Sammelt die aktuellen System-Metriken. Läuft in einem Hintergrund-Thread
    (siehe heartbeat_loop), weil psutil.cpu_percent() kurz "blockiert"
    (es misst die Last über ein kleines Zeitfenster).
    """
    # CPU-Last gesamt UND pro Kern in einem Rutsch messen (percpu teilt das
    # 0,5s-Fenster auf alle Kerne auf; der Gesamtwert ist deren Mittel).
    per_core = psutil.cpu_percent(interval=0.5, percpu=True) or []
    cpu_percent = round(sum(per_core) / len(per_core)) if per_core else psutil.cpu_percent()
    memory = psutil.virtual_memory()
    # Swap / Auslagerungsspeicher (auf Linux die "swap partition/file", auf
    # Windows die Auslagerungsdatei). swap_memory() gibt es auf beiden Systemen.
    try:
        swap = psutil.swap_memory()
        swap_used = int(swap.used)
        swap_total = int(swap.total)
    except Exception:
        swap_used = 0
        swap_total = 0
    disk_path = "C:\\" if IS_WINDOWS else "/"
    disk = psutil.disk_usage(disk_path)

    # --- Netzwerk-Durchsatz berechnen ---
    # psutil liefert Gesamt-Zähler (Bytes seit Systemstart). Der Durchsatz ist
    # die Differenz zum letzten Messwert, geteilt durch die vergangene Zeit.
    net = psutil.net_io_counters()
    now = time.time()
    net_in_per_s = 0.0
    net_out_per_s = 0.0
    if _last_net["ts"] is not None:
        elapsed = now - _last_net["ts"]
        if elapsed > 0:
            net_in_per_s = (net.bytes_recv - _last_net["bytes_recv"]) / elapsed
            net_out_per_s = (net.bytes_sent - _last_net["bytes_sent"]) / elapsed
    _last_net["ts"] = now
    _last_net["bytes_recv"] = net.bytes_recv
    _last_net["bytes_sent"] = net.bytes_sent

    # --- Alle Festplatten/Partitionen einzeln (für den Disk-Tab im Dashboard) ---
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "used": usage.used,
                "total": usage.total,
            })
        except (PermissionError, OSError):
            continue  # z.B. nicht bereite CD-Laufwerke überspringen

    base = {
        "cpuLoad": round(cpu_percent),
        "cpuPerCore": [round(x) for x in per_core],           # Last je Kern (%)
        "cpuCores": psutil.cpu_count(logical=False) or 0,   # physische Kerne
        "cpuThreads": psutil.cpu_count(logical=True) or 0,  # logische Kerne (Threads)
        "memUsed": memory.total - memory.available,
        "memTotal": memory.total,
        "memAvailable": memory.available,   # freier/verfügbarer RAM
        "memCached": int(getattr(memory, "cached", 0) or 0),     # Cache (Linux)
        "memBuffers": int(getattr(memory, "buffers", 0) or 0),   # Puffer (Linux)
        "swapUsed": swap_used,               # belegter Auslagerungsspeicher (Bytes)
        "swapTotal": swap_total,             # gesamter Auslagerungsspeicher (Bytes)
        "diskUsed": disk.used,
        "diskTotal": disk.total,
        "disks": disks,
        "netIn": round(net_in_per_s),      # Bytes pro Sekunde empfangen
        "netOut": round(net_out_per_s),    # Bytes pro Sekunde gesendet
        "uptime": int(time.time() - psutil.boot_time()),
    }
    # Load Average unter Windows selbst schätzen (EWMA aus der CPU-Auslastung *
    # Kernzahl), da es dort kein echtes Load-Average gibt.
    if IS_WINDOWS:
        try:
            ncpu = psutil.cpu_count(logical=True) or 1
            inst = (cpu_percent / 100.0) * ncpu
            g = collect_metrics
            prev = getattr(g, "_load", None) or {"1": inst, "5": inst, "15": inst}
            import math as _math
            a1, a5, a15 = _math.exp(-5/60), _math.exp(-5/300), _math.exp(-5/900)
            nl = {"1": prev["1"]*a1 + inst*(1-a1),
                  "5": prev["5"]*a5 + inst*(1-a5),
                  "15": prev["15"]*a15 + inst*(1-a15)}
            g._load = nl
            base["load1"] = round(nl["1"], 2)
            base["load5"] = round(nl["5"], 2)
            base["load15"] = round(nl["15"], 2)
        except Exception:
            pass
    # Erweiterte Telemetrie anhängen (best effort, darf den Heartbeat nie stören).
    try:
        base.update(collect_extended_metrics())
    except Exception:
        pass
    return base


async def heartbeat_loop():
    """Läuft dauerhaft im Hintergrund und schickt alle 5 Sekunden Metriken."""
    loop = asyncio.get_event_loop()
    while True:
        if sio.connected:
            try:
                # collect_metrics() blockiert kurz -> in einem Thread ausführen,
                # damit der Rest des Programms (z.B. eingehende Befehle) nicht wartet
                metrics = await loop.run_in_executor(None, collect_metrics)
                await sio.emit("heartbeat", {"id": DEVICE_ID, "metrics": metrics}, namespace="/agent")
            except Exception as e:
                _print(f"[agent] Fehler beim Senden der Metriken: {e}")
        await asyncio.sleep(5)


# --------------------------------------------------------------
# Fernbefehle: Shell-Kommandos ausführen (Terminal-App im Dashboard)
# --------------------------------------------------------------

# Pro Terminal-Session (= ein Terminal-Fenster im Dashboard) merken wir uns das
# aktuelle Arbeitsverzeichnis, damit "cd" & Co. über mehrere Befehle hinweg
# erhalten bleiben - genau wie in einer echten Shell.
_terminal_sessions: dict[str, dict] = {}
_terminal_sessions_lock = threading.Lock()


def _session_cwd(session_id: str | None) -> str | None:
    """Aktuelles Arbeitsverzeichnis einer Terminal-Session (legt sie bei Bedarf an)."""
    if not session_id:
        return None
    with _terminal_sessions_lock:
        sess = _terminal_sessions.setdefault(session_id, {"cwd": os.getcwd()})
        cwd = sess.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        cwd = os.getcwd()
    return cwd


def _update_session_cwd(session_id: str | None, new_cwd: str) -> None:
    """Speichert das neue Arbeitsverzeichnis nach einem Befehl (falls es sich änderte)."""
    if not session_id or not new_cwd:
        return
    with _terminal_sessions_lock:
        _terminal_sessions.setdefault(session_id, {})["cwd"] = new_cwd


def _run_shell_command(command: str, session_id: str | None = None,
                       shell: str = "auto", elevated: bool = False) -> tuple[str, str, int]:
    """
    Führt einen Shell-Befehl aus und gibt (stdout, stderr, exit_code) zurück.

    shell:    'cmd' | 'powershell' | 'auto' (auto = cmd auf Windows, sh auf POSIX)
    elevated: True -> auf Windows als Administrator ausführen (UAC-Elevation).

    Ist eine session_id gesetzt, läuft der Befehl im gemerkten Arbeitsverzeichnis
    dieser Terminal-Session, und ein evtl. per "cd" geändertes Verzeichnis wird
    für den nächsten Befehl übernommen.
    """
    start_cwd = _session_cwd(session_id)
    cwd_file = None
    script_file = None
    is_win = os.name == "nt"
    use_ps = (shell == "powershell")
    try:
        fd, cwd_file = tempfile.mkstemp(prefix="rmm_cwd_")
        os.close(fd)

        # Windows + Administrator: eigener Elevations-Pfad (UAC).
        if is_win and elevated:
            return _run_elevated_windows(command, start_cwd, use_ps)

        if is_win and use_ps:
            # PowerShell robust ausführen: -EncodedCommand (Base64/UTF-16LE)
            # umgeht ExecutionPolicy, Quoting- UND Encoding-Probleme, die bei
            # "-File script.ps1" auf gesperrten/lokalisierten Maschinen dazu
            # führen, dass Befehle wie 'irm' scheinbar "nicht gefunden" werden.
            # Wir kapseln den Nutzerbefehl, hängen Exit-Code- und CWD-Ausgabe an
            # und setzen die Konsolen-Ausgabe explizit auf UTF-8.
            ps_script = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\r\n"
                + (f'Set-Location -LiteralPath "{start_cwd}"\r\n' if start_cwd else "")
                + command + "\r\n"
                + "$__rmm_rc = $LASTEXITCODE; if ($null -eq $__rmm_rc) { $__rmm_rc = 0 }\r\n"
                + f'(Get-Location).Path | Out-File -Encoding utf8 -FilePath "{cwd_file}"\r\n'
                + "exit $__rmm_rc\r\n"
            )
            encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
            argv = ["powershell", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
        elif is_win:
            # cmd: temporäre .bat.
            fd, script_file = tempfile.mkstemp(prefix="rmm_cmd_", suffix=".bat")
            os.close(fd)
            with open(script_file, "w", encoding="utf-8") as f:
                f.write("@echo off\r\n")
                f.write(command + "\r\n")
                f.write('set "__rmm_rc=%ERRORLEVEL%"\r\n')
                f.write(f'cd > "{cwd_file}"\r\n')
                f.write("exit /b %__rmm_rc%\r\n")
            argv = ["cmd", "/c", script_file]
        else:
            # POSIX: sh -c mit Wrapper, der nach dem Befehl das PWD wegschreibt.
            wrapper = (
                f"{command}\n"
                f"__rmm_rc=$?\n"
                f"printf '%s' \"$PWD\" > '{cwd_file}' 2>/dev/null\n"
                f"exit $__rmm_rc\n"
            )
            argv = ["/bin/sh", "-c", wrapper]

        result = _run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            cwd=start_cwd if start_cwd else None,
        )

        # Neues Arbeitsverzeichnis übernehmen (falls sich etwas geändert hat).
        try:
            with open(cwd_file, "r", encoding="utf-8", errors="replace") as f:
                new_cwd = f.read().strip().strip('"')
            if new_cwd and os.path.isdir(new_cwd):
                _update_session_cwd(session_id, new_cwd)
        except OSError:
            pass

        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Zeitüberschreitung: Befehl hat zu lange gedauert (>15s)", 1
    except Exception as e:
        return "", str(e), 1
    finally:
        for path in (cwd_file, script_file):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def _ps_single_quote(s: str) -> str:
    """Escaped einen String für PowerShell-Single-Quotes ('' verdoppeln)."""
    return "'" + s.replace("'", "''") + "'"


def _run_elevated_windows(command: str, start_cwd: str | None, use_ps: bool) -> tuple[str, str, int]:
    """
    Führt einen Befehl auf Windows ALS ADMINISTRATOR aus (UAC-Elevation via
    PowerShell Start-Process -Verb RunAs). Da der elevierte Prozess ein eigener
    ist, werden stdout/stderr/Exit-Code über Temp-Dateien eingesammelt.

    Läuft der Agent bereits als SYSTEM/Administrator (Autostart als Dienst),
    erscheint KEIN UAC-Dialog - die Elevation ist dann transparent.
    """
    out_file = tempfile.mkstemp(prefix="rmm_out_", suffix=".txt")[1]
    err_file = tempfile.mkstemp(prefix="rmm_err_", suffix=".txt")[1]
    rc_file = tempfile.mkstemp(prefix="rmm_rc_", suffix=".txt")[1]
    inner = tempfile.mkstemp(prefix="rmm_inner_", suffix=(".ps1" if use_ps else ".bat"))[1]
    try:
        if use_ps:
            with open(inner, "w", encoding="utf-8") as f:
                f.write("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\r\n")
                if start_cwd:
                    f.write(f'Set-Location -LiteralPath "{start_cwd}"\r\n')
                f.write(f'& {{ {command} }} > "{out_file}" 2> "{err_file}"\r\n')
                f.write(f'$c = $LASTEXITCODE; if ($null -eq $c) {{ $c = 0 }}; $c | Out-File -Encoding ascii "{rc_file}"\r\n')
            inner_exec = f'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{inner}"'
        else:
            with open(inner, "w", encoding="utf-8") as f:
                f.write("@echo off\r\n")
                if start_cwd:
                    f.write(f'cd /d "{start_cwd}"\r\n')
                f.write(f'call {command} > "{out_file}" 2> "{err_file}"\r\n')
                f.write(f'echo %ERRORLEVEL% > "{rc_file}"\r\n')
            inner_exec = f'cmd /c "{inner}"'

        # Start-Process -Verb RunAs hebt den inneren Befehl auf Admin-Rechte an.
        # Der Launcher selbst wird als EncodedCommand übergeben, damit die
        # verschachtelten Anführungszeichen sauber bleiben.
        launch_script = (
            f"$p = Start-Process -FilePath cmd.exe -ArgumentList '/c',{_ps_single_quote(inner_exec)} "
            f"-Verb RunAs -WindowStyle Hidden -PassThru -Wait; exit $p.ExitCode"
        )
        encoded = base64.b64encode(launch_script.encode("utf-16-le")).decode("ascii")
        _run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )

        def _read(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                return ""
        stdout = _read(out_file)
        stderr = _read(err_file)
        rc_raw = _read(rc_file).strip()
        try:
            code = int(rc_raw)
        except ValueError:
            code = 0
        if not stdout and not stderr and code != 0:
            stderr = "(Elevierter Befehl fehlgeschlagen oder UAC abgelehnt.)"
        return stdout, stderr, code
    except subprocess.TimeoutExpired:
        return "", "Zeitüberschreitung beim elevierten Befehl (>120s)", 1
    except Exception as e:
        return "", f"Elevation fehlgeschlagen: {e}", 1
    finally:
        for path in (out_file, err_file, rc_file, inner):
            try:
                os.unlink(path)
            except OSError:
                pass


@sio.on("exec", namespace="/agent")
async def on_exec(data):
    """Wird aufgerufen, wenn das Backend einen Terminal-Befehl schicken will."""
    request_id = data.get("requestId")
    command = data.get("command", "")
    session_id = data.get("session")
    shell = data.get("shell", "auto")        # 'cmd' | 'powershell' | 'auto'
    elevated = bool(data.get("elevated", False))

    loop = asyncio.get_event_loop()
    stdout, stderr, exit_code = await loop.run_in_executor(
        None, _run_shell_command, command, session_id, shell, elevated
    )

    await sio.emit(
        "exec-result",
        {"requestId": request_id, "stdout": stdout, "stderr": stderr, "code": exit_code},
        namespace="/agent",
    )


# ==============================================================
# INTERAKTIVES TERMINAL (echte PTY-Session)
# ==============================================================
# Statt einzelne Befehle auszuführen, hält der Agent pro Terminal-Fenster eine
# ECHTE Shell offen (bash/sh auf Linux, cmd/powershell auf Windows). Damit
# funktioniert alles wie in einem lokalen Terminal: 'cd' wirkt dauerhaft,
# Editoren wie nano/vim laufen, Verlauf, Farben, interaktive Programme.
#
# Linux/macOS: Python-Standardbibliothek 'pty' (kein Zusatzpaket nötig).
# Windows:     'pywinpty' (ConPTY). Wird per Bootstrap automatisch installiert;
#              fehlt es, wird eine klare Meldung ausgegeben.
#
# Datenfluss:
#   Dashboard --term-open {session, shell, cols, rows}--> Agent startet Shell
#   Dashboard --term-input {session, data}-------------> in die Shell schreiben
#   Dashboard --term-resize {session, cols, rows}------> PTY-Größe anpassen
#   Dashboard --term-close {session}-------------------> Shell beenden
#   Agent     --term-output {session, data}------------> Ausgabe zum Dashboard
#   Agent     --term-exit {session}--------------------> Shell wurde beendet
# --------------------------------------------------------------

_terminals: dict = {}   # session_id -> Handle (plattformabhängig)


def _resolve_shell_cmd(shell: str, elevated: bool) -> list:
    """Bestimmt das Startkommando der Shell je nach Wunsch und Plattform."""
    if IS_WINDOWS:
        if shell == "powershell":
            return ["powershell.exe", "-NoLogo", "-NoProfile"]
        return ["cmd.exe"]
    # POSIX: bevorzugt bash, sonst sh. 'shell' spielt hier keine Rolle.
    for candidate in ("/bin/bash", "/bin/sh"):
        if os.path.exists(candidate):
            return [candidate, "-i"] if candidate.endswith("bash") else [candidate]
    return ["/bin/sh"]


class _PosixTerminal:
    """Interaktive Shell über ein echtes PTY (Linux/macOS)."""

    def __init__(self, session_id, shell, cols, rows, loop):
        import pty, fcntl, termios, struct
        self.session_id = session_id
        self.loop = loop
        self.alive = True
        argv = _resolve_shell_cmd(shell, False)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            # Kindprozess: Umgebung setzen und Shell starten.
            os.environ["TERM"] = "xterm-256color"
            try:
                os.execvp(argv[0], argv)
            except Exception:
                os._exit(1)
        # Elternprozess: PTY-Größe setzen.
        self._set_size(cols, rows)
        # Leser-Thread: liest PTY-Ausgabe und schickt sie ans Dashboard.
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _set_size(self, cols, rows):
        try:
            import fcntl, termios, struct
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass

    def _read_loop(self):
        while self.alive:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            text = data.decode("utf-8", errors="replace")
            asyncio.run_coroutine_threadsafe(
                sio.emit("term-output", {"id": DEVICE_ID, "session": self.session_id,
                                         "data": text}, namespace="/agent"),
                self.loop,
            )
        self.alive = False
        asyncio.run_coroutine_threadsafe(
            sio.emit("term-exit", {"id": DEVICE_ID, "session": self.session_id},
                     namespace="/agent"),
            self.loop,
        )

    def write(self, data: str):
        try:
            os.write(self.fd, data.encode("utf-8"))
        except OSError:
            pass

    def resize(self, cols, rows):
        self._set_size(cols, rows)

    def close(self):
        self.alive = False
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.kill(self.pid, 9)
        except OSError:
            pass


class _WinTerminal:
    """Interaktive Shell über ConPTY (Windows, benötigt pywinpty)."""

    def __init__(self, session_id, shell, cols, rows, loop):
        from winpty import PtyProcess
        self.session_id = session_id
        self.loop = loop
        self.alive = True
        # winpty.PtyProcess.spawn erwartet einen KOMMANDOSTRING (keine Liste!).
        # Eine Liste führt zu stillem Fehlschlag/Exception.
        if shell == "powershell":
            cmd = "powershell.exe -NoLogo -NoProfile"
        else:
            cmd = "cmd.exe"
        self.proc = PtyProcess.spawn(cmd, dimensions=(rows, cols))
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        while self.alive:
            try:
                data = self.proc.read(65536)
            except EOFError:
                break
            except Exception:
                break
            if not data:
                if not self.proc.isalive():
                    break
                continue
            asyncio.run_coroutine_threadsafe(
                sio.emit("term-output", {"id": DEVICE_ID, "session": self.session_id,
                                         "data": data}, namespace="/agent"),
                self.loop,
            )
        self.alive = False
        asyncio.run_coroutine_threadsafe(
            sio.emit("term-exit", {"id": DEVICE_ID, "session": self.session_id},
                     namespace="/agent"),
            self.loop,
        )

    def write(self, data: str):
        try:
            self.proc.write(data)
        except Exception:
            pass

    def resize(self, cols, rows):
        try:
            self.proc.setwinsize(rows, cols)
        except Exception:
            pass

    def close(self):
        self.alive = False
        try:
            self.proc.terminate(force=True)
        except Exception:
            pass


@sio.on("term-open", namespace="/agent")
async def on_term_open(data):
    """Startet eine echte interaktive Shell-Session."""
    session_id = data.get("session")
    shell = data.get("shell", "auto")
    cols = int(data.get("cols", 80))
    rows = int(data.get("rows", 24))
    if not session_id:
        return

    # SOFORT-Marker: beweist, dass DIESER (neue) Agent-Code läuft. Sieht der
    # Nutzer diese Zeile nicht, läuft auf dem Client noch die ALTE agent.py
    # (Prozess nach dem Update nicht neu gestartet).
    await sio.emit("term-output", {
        "id": DEVICE_ID, "session": session_id,
        "data": f"\x1b[90m[Agent startet {shell}-Shell…]\x1b[0m\r\n",
    }, namespace="/agent")

    # Schon offen? Erst schließen (Neustart).
    old = _terminals.pop(session_id, None)
    if old:
        try:
            old.close()
        except Exception:
            pass

    loop = asyncio.get_event_loop()
    try:
        if IS_WINDOWS:
            term = _WinTerminal(session_id, shell, cols, rows, loop)
        else:
            term = _PosixTerminal(session_id, shell, cols, rows, loop)
        _terminals[session_id] = term
    except ImportError as e:
        await sio.emit("term-output", {
            "id": DEVICE_ID, "session": session_id,
            "data": "\r\n\x1b[31mInteraktives Terminal benoetigt 'pywinpty' "
                    f"(Import fehlgeschlagen: {e}).\x1b[0m\r\n"
                    "Installiere es manuell mit:  pip install pywinpty\r\n"
                    "oder starte den Agenten neu (er versucht es automatisch).\r\n",
        }, namespace="/agent")
        await sio.emit("term-exit", {"id": DEVICE_ID, "session": session_id}, namespace="/agent")
    except Exception as e:
        import traceback
        _print(f"[term] Fehler beim Shell-Start: {e}\n{traceback.format_exc()}")
        await sio.emit("term-output", {
            "id": DEVICE_ID, "session": session_id,
            "data": f"\r\n\x1b[31mTerminal-Fehler: {e}\x1b[0m\r\n",
        }, namespace="/agent")
        await sio.emit("term-exit", {"id": DEVICE_ID, "session": session_id}, namespace="/agent")


@sio.on("term-input", namespace="/agent")
async def on_term_input(data):
    term = _terminals.get(data.get("session"))
    if term:
        term.write(data.get("data", ""))


@sio.on("term-resize", namespace="/agent")
async def on_term_resize(data):
    term = _terminals.get(data.get("session"))
    if term:
        term.resize(int(data.get("cols", 80)), int(data.get("rows", 24)))


@sio.on("term-close", namespace="/agent")
async def on_term_close(data):
    term = _terminals.pop(data.get("session"), None)
    if term:
        term.close()


# --------------------------------------------------------------
# Fernbefehle: Dateisystem auflisten (File Station im Dashboard)
# --------------------------------------------------------------

def _list_windows_drives() -> list[dict]:
    """
    Ermittelt ALLE Laufwerke unter Windows (lokale Festplatten, USB, CD,
    und gemappte Netzlaufwerke) über psutil - zuverlässiger als das veraltete
    'wmic', das auf neueren Windows-Versionen oft gar nicht mehr existiert.
    """
    drives = []
    try:
        # all=True liefert auch Netzlaufwerke und Wechseldatenträger mit
        for part in psutil.disk_partitions(all=True):
            mount = part.mountpoint
            if not mount:
                continue
            # Typ-Kennzeichnung für ein hübsches Label (lokal vs. Netzlaufwerk)
            is_network = "remote" in (part.opts or "").lower() or part.fstype == ""
            try:
                usage = psutil.disk_usage(mount)
                size = usage.total
            except Exception:
                size = 0
            label = mount.rstrip("\\")
            if is_network:
                label += "  (Netzlaufwerk)"
            drives.append({
                "name": label,
                "path": mount if mount.endswith("\\") else mount + "\\",
                "isDir": True,
                "size": size,
                "mtime": 0,
            })
    except Exception:
        pass

    return drives or [{"name": "C:", "path": "C:\\", "isDir": True, "size": 0, "mtime": 0}]


def _perm_string(mode: int, is_dir: bool, is_link: bool = False) -> str:
    """Baut einen 'ls -al'-artigen Rechte-String, z.B. 'drwxr-xr-x'."""
    import stat as _stat
    if is_link:
        type_char = "l"
    elif is_dir:
        type_char = "d"
    elif _stat.S_ISCHR(mode):
        type_char = "c"
    elif _stat.S_ISBLK(mode):
        type_char = "b"
    elif _stat.S_ISFIFO(mode):
        type_char = "p"
    elif _stat.S_ISSOCK(mode):
        type_char = "s"
    else:
        type_char = "-"
    perms = ""
    for who, r, w, x in (
        ("USR", _stat.S_IRUSR, _stat.S_IWUSR, _stat.S_IXUSR),
        ("GRP", _stat.S_IRGRP, _stat.S_IWGRP, _stat.S_IXGRP),
        ("OTH", _stat.S_IROTH, _stat.S_IWOTH, _stat.S_IXOTH),
    ):
        perms += "r" if mode & r else "-"
        perms += "w" if mode & w else "-"
        perms += "x" if mode & x else "-"
    return type_char + perms


def _owner_group(stat_res) -> tuple[str, str]:
    """Ermittelt Besitzer- und Gruppennamen (Linux/Mac). Unter Windows bzw.
    bei fehlenden Modulen wird die numerische UID/GID zurückgegeben."""
    uid = getattr(stat_res, "st_uid", 0)
    gid = getattr(stat_res, "st_gid", 0)
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
    """Sammelt Größe, mtime, Rechte, Besitzer, Gruppe und Oktal-Modus einer
    Datei/eines Ordners - so, wie es 'ls -al' anzeigen würde."""
    import stat as _stat
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
        owner, group = _owner_group(st)
        meta["owner"], meta["group"] = owner, group
    except Exception:
        pass
    return meta


def _list_directory(path: str) -> list[dict]:
    """Listet den Inhalt eines Ordners auf. Bei leerem Pfad: Laufwerke bzw. Root/Home zeigen."""
    if not path:
        if IS_WINDOWS:
            return _list_windows_drives()
        # Linux/Mac: Root, Home und alle gemounteten Partitionen anzeigen
        home = str(Path.home())
        entries = [
            {"name": "/ (Root)", "path": "/", "isDir": True, "size": 0, "mtime": 0},
            {"name": f"🏠 Home ({home})", "path": home, "isDir": True, "size": 0, "mtime": 0},
        ]
        seen = {"/", home}
        try:
            for part in psutil.disk_partitions(all=False):
                mp = part.mountpoint
                if mp and mp not in seen:
                    seen.add(mp)
                    try:
                        size = psutil.disk_usage(mp).total
                    except Exception:
                        size = 0
                    entries.append({
                        "name": f"💽 {mp} ({part.device})",
                        "path": mp, "isDir": True, "size": size, "mtime": 0,
                    })
        except Exception:
            pass
        return entries

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
    return entries


@sio.on("fs-list", namespace="/agent")
async def on_fs_list(data):
    """Wird aufgerufen, wenn das Backend einen Ordnerinhalt sehen will."""
    request_id = data.get("requestId")
    req_path = data.get("path", "")

    loop = asyncio.get_event_loop()
    try:
        entries = await loop.run_in_executor(None, _list_directory, req_path)
        await sio.emit("fs-result", {"requestId": request_id, "entries": entries}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


def _read_file_b64(path: str, max_bytes: int = 25 * 1024 * 1024) -> dict:
    """Liest eine Datei und gibt sie base64-kodiert zurück (für den Download)."""
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ValueError(f"Datei zu groß ({size} Bytes, max. {max_bytes})")
    with open(path, "rb") as f:
        content = f.read()
    return {
        "name": os.path.basename(path),
        "data": base64.b64encode(content).decode("ascii"),
    }


@sio.on("fs-read", namespace="/agent")
async def on_fs_read(data):
    """Liest eine einzelne Datei für den Download im Dashboard."""
    request_id = data.get("requestId")
    path = data.get("path", "")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _read_file_b64, path)
        await sio.emit("fs-read-result", {"requestId": request_id, **result}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-read-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


# --------------------------------------------------------------
# Schreibende Datei-Operationen (Upload, Ordner, Löschen, Umbenennen, Editieren)
# --------------------------------------------------------------

def _write_file_b64(path: str, data_b64: str) -> dict:
    """Schreibt eine (hochgeladene oder editierte) Datei an den Zielpfad."""
    content = base64.b64decode(data_b64)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return {"ok": True, "path": path, "size": len(content)}


def _make_dir(path: str) -> dict:
    os.makedirs(path, exist_ok=False)
    return {"ok": True, "path": path}


def _delete_path(path: str) -> dict:
    """Löscht eine Datei oder einen (auch nicht-leeren) Ordner."""
    import shutil
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
    return {"ok": True, "path": path}


def _rename_path(src: str, dst: str) -> dict:
    os.rename(src, dst)
    return {"ok": True, "src": src, "dst": dst}


@sio.on("fs-write", namespace="/agent")
async def on_fs_write(data):
    """Datei hochladen oder eine editierte Datei zurückschreiben."""
    request_id = data.get("requestId")
    path = data.get("path", "")
    payload = data.get("data", "")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _write_file_b64, path, payload)
        await sio.emit("fs-op-result", {"requestId": request_id, **result}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-op-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


@sio.on("fs-mkdir", namespace="/agent")
async def on_fs_mkdir(data):
    request_id = data.get("requestId")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _make_dir, data.get("path", ""))
        await sio.emit("fs-op-result", {"requestId": request_id, **result}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-op-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


@sio.on("fs-delete", namespace="/agent")
async def on_fs_delete(data):
    request_id = data.get("requestId")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _delete_path, data.get("path", ""))
        await sio.emit("fs-op-result", {"requestId": request_id, **result}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-op-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


@sio.on("fs-rename", namespace="/agent")
async def on_fs_rename(data):
    request_id = data.get("requestId")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _rename_path,
                                             data.get("src", ""), data.get("dst", ""))
        await sio.emit("fs-op-result", {"requestId": request_id, **result}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-op-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


# --------------------------------------------------------------
# Task-Manager: strukturierte Prozessliste + Prozess beenden
# --------------------------------------------------------------

def _collect_processes() -> list[dict]:
    """Sammelt die laufenden Prozesse mit CPU-/RAM-Nutzung über psutil."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "memory_percent"]):
        try:
            info = p.info
            procs.append({
                "pid": info["pid"],
                "name": info["name"] or "?",
                "username": info.get("username") or "",
                "cpu": p.cpu_percent(interval=0),   # seit letztem Aufruf
                "mem": round(info.get("memory_percent") or 0, 1),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # Nach Speicherverbrauch sortieren, die größten zuerst
    procs.sort(key=lambda x: x["mem"], reverse=True)
    return procs[:60]  # auf die Top 60 begrenzen


@sio.on("proc-list", namespace="/agent")
async def on_proc_list(data):
    """Liefert die Prozessliste an den Task-Manager im Dashboard."""
    request_id = data.get("requestId")
    loop = asyncio.get_event_loop()
    try:
        processes = await loop.run_in_executor(None, _collect_processes)
        await sio.emit("proc-result", {"requestId": request_id, "processes": processes}, namespace="/agent")
    except Exception as e:
        await sio.emit("proc-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


@sio.on("proc-kill", namespace="/agent")
async def on_proc_kill(data):
    """Beendet einen Prozess anhand seiner PID."""
    request_id = data.get("requestId")
    pid = data.get("pid")
    try:
        psutil.Process(int(pid)).terminate()
        await sio.emit("proc-kill-result", {"requestId": request_id, "ok": True}, namespace="/agent")
    except Exception as e:
        await sio.emit("proc-kill-result", {"requestId": request_id, "ok": False, "error": str(e)}, namespace="/agent")


# --------------------------------------------------------------
# Remote Screen: Bildschirm streamen + Maus/Tastatur fernsteuern
# --------------------------------------------------------------
# Ablauf:
#   - Dashboard schickt "screen-start" -> wir starten einen Hintergrund-Thread,
#     der laufend Screenshots macht, verkleinert, als JPEG/Base64 kodiert und
#     einzeln als "screen-frame" ans Backend schickt (das leitet sie ans
#     Dashboard weiter).
#   - Dashboard schickt "screen-stop" -> Thread beenden.
#   - Dashboard schickt "screen-input" (Maus-Bewegung/Klick/Tastatur) -> wir
#     simulieren die Eingabe lokal über pynput.
#
# Hinweis zur Latenz: Wir nutzen hier bewusst simples JPEG-Streaming über
# Socket.IO (leicht verständlich, überall lauffähig). Für noch flüssigeres Bild
# könnte man später auf WebRTC umstellen - die Dashboard-Seite müsste dann nur
# die Frames anders empfangen, der Rest bliebe gleich.

_screen_stream = {"active": False, "thread": None, "sid_loop": None,
                  "quality": 55, "fps": 10,
                  "monitor": 1,          # gewählter Bildschirm (1 = primär)
                  "mon_left": 0, "mon_top": 0}   # Offset des gewählten Bildschirms


def _detect_from_xorg_process():
    """
    Sucht per psutil den laufenden X-Server und liest DISPLAY + Xauthority-Datei
    aus dessen Kommandozeile. So kann der Agent auch als Dienst (ohne eigenes
    DISPLAY) den angemeldeten Desktop erfassen. Rückgabe: (display, authfile).
    """
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            name = (proc.info.get("name") or "").lower()
            if name not in ("xorg", "x", "xwayland") and "xorg" not in name:
                continue
            cmd = proc.info.get("cmdline") or []
            display = None
            auth = None
            for i, tok in enumerate(cmd):
                # Display sieht aus wie ":0" oder ":0.0"
                if tok.startswith(":") and tok[1:].split(".")[0].isdigit():
                    display = ":" + tok[1:].split(".")[0]
                if tok == "-auth" and i + 1 < len(cmd):
                    auth = cmd[i + 1]
            if display:
                return display, auth
    except Exception:
        pass
    return None, None


def _detect_graphical_display():
    """
    Prüft, ob ein erfassbarer grafischer Bildschirm verfügbar ist, und setzt auf
    Linux bei Bedarf DISPLAY/XAUTHORITY. Rückgabe: (verfügbar: bool, hinweis: str).
    """
    system = platform.system()
    # Windows/macOS haben praktisch immer einen erfassbaren Desktop.
    if system in ("Windows", "Darwin"):
        return True, ""

    # Linux: DISPLAY schon gesetzt? Dann direkt versuchen.
    if os.environ.get("DISPLAY"):
        return True, ""

    # 1) X-Server-Prozess finden (liefert DISPLAY + Xauthority)
    display, auth = _detect_from_xorg_process()
    if display:
        os.environ["DISPLAY"] = display
        if auth and os.path.isfile(auth):
            os.environ["XAUTHORITY"] = auth
        return True, f"Display {display} erkannt"

    # 2) Ersatz: aktive X-Sockets in /tmp/.X11-unix suchen
    try:
        socket_dir = "/tmp/.X11-unix"
        nums = []
        if os.path.isdir(socket_dir):
            for entry in os.listdir(socket_dir):
                if entry.startswith("X") and entry[1:].isdigit():
                    nums.append(int(entry[1:]))
        if nums:
            os.environ["DISPLAY"] = f":{sorted(nums)[0]}"
            return True, f"Display :{sorted(nums)[0]} erkannt"
    except Exception:
        pass

    # Kein grafischer Bildschirm -> Shell-only.
    return False, ("Kein grafischer Bildschirm gefunden (headless VM/Server). "
                   "Es wird stattdessen eine Shell geöffnet.")

# Maus-/Tastatur-Controller erst hier erstellen. Auch das kann fehlschlagen
# (z.B. keine grafische Sitzung) - dann deaktivieren wir die Fernsteuerung,
# statt den ganzen Agenten abstürzen zu lassen.
_mouse = None
_keyboard = None
if _INPUT_AVAILABLE:
    try:
        _mouse = MouseController()
        _keyboard = KeyboardController()
    except Exception as e:
        _INPUT_AVAILABLE = False
        _print(f"[agent] Fernsteuerung deaktiviert (Controller-Init fehlgeschlagen: {e})")


def _screen_capture_loop(loop):
    """Läuft in einem eigenen Thread und schickt fortlaufend Bildschirm-Frames."""
    is_linux = platform.system() == "Linux"
    try:
        sct = mss.mss()
    except Exception as e:
        # Kein Display erfassbar. Auf Linux/headless direkt auf Shell umschalten,
        # sonst normale Fehlermeldung (Windows -> RDP-Angebot im Dashboard).
        if is_linux:
            _notify_screen_mode(loop, "shell", f"Bildschirm nicht erfassbar: {e}")
        else:
            _notify_screen_error(loop, f"Bildschirmaufnahme nicht möglich: {e}")
        _screen_stream["active"] = False
        return

    # Verfügbare Einzel-Bildschirme: sct.monitors[0] ist die Gesamtfläche,
    # ab Index 1 die einzelnen Monitore. Anzahl = len - 1.
    mon_count = max(1, len(sct.monitors) - 1)
    cur_idx = -1
    monitor = None
    screen_w = screen_h = 0

    def _select_monitor(idx):
        nonlocal monitor, screen_w, screen_h, cur_idx
        # Index 0 = Gesamtfläche ALLER Monitore (mss.monitors[0]),
        # 1..mon_count = einzelne Bildschirme.
        idx = max(0, min(int(idx), mon_count))
        monitor = sct.monitors[idx]
        screen_w, screen_h = monitor["width"], monitor["height"]
        _screen_stream["mon_left"] = monitor.get("left", 0)
        _screen_stream["mon_top"] = monitor.get("top", 0)
        cur_idx = idx
        return idx

    _select_monitor(_screen_stream.get("monitor", 1))
    consecutive_errors = 0

    while _screen_stream["active"]:
        try:
            # Live-Wechsel: hat das Dashboard einen anderen Monitor gewählt?
            want = max(0, min(int(_screen_stream.get("monitor", 1)), mon_count))
            if want != cur_idx:
                _select_monitor(want)

            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            consecutive_errors = 0  # erfolgreich -> Fehlerzähler zurücksetzen

            # Ziel-Breite skaliert mit der gewählten Qualität: hohe Qualität ->
            # bis 1920px (schärfer), niedrige -> 1280px (spart Bandbreite).
            _q_now = int(_screen_stream.get("quality", 55))
            max_width = 1920 if _q_now >= 70 else 1280
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)))

            buffer = io.BytesIO()
            _q = int(_screen_stream.get("quality", 55))
            _q = max(10, min(_q, 95))
            img.save(buffer, format="JPEG", quality=_q)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

            asyncio.run_coroutine_threadsafe(
                sio.emit("screen-frame", {
                    "id": DEVICE_ID,
                    "image": b64,
                    "width": screen_w,
                    "height": screen_h,
                    "monitor_index": cur_idx,      # aktuell gestreamter Monitor
                    "monitor_count": mon_count,    # Anzahl verfügbarer Monitore
                }, namespace="/agent"),
                loop,
            )
            # Sende-Rate aus der Einstellung (Bilder/Sekunde).
            _fps = int(_screen_stream.get("fps", 10))
            _fps = max(1, min(_fps, 30))
            time.sleep(1.0 / _fps)
        except Exception as e:
            consecutive_errors += 1
            msg = str(e)
            # Linux/headless: kein Sinn weiterzuprobieren -> Shell anbieten.
            if is_linux:
                _notify_screen_mode(loop, "shell", f"Bildschirmaufnahme abgebrochen: {msg}")
                _screen_stream["active"] = False
                break
            # Windows: typischer Fall headless/kein Desktop -> RDP-Angebot.
            if "denied" in msg.lower() or "bitblt" in msg.lower():
                _notify_screen_error(
                    loop,
                    "Bildschirm kann nicht erfasst werden. Häufige Ursache: Der PC ist "
                    "eine headless VM/Server OHNE aktive Grafiksitzung (kein Monitor, "
                    "keine Anmeldung, oder nur per RDP erreichbar). Ohne echte "
                    "Bildschirmsitzung gibt es nichts zu übertragen. Lösung: am Gerät "
                    "angemeldet bleiben, einen (virtuellen) Monitor bereitstellen oder "
                    "einen virtuellen Displaytreiber installieren."
                )
                _screen_stream["active"] = False
                break
            # Andere Fehler: ein paar Mal tolerieren, dann aufgeben
            if consecutive_errors >= 5:
                _notify_screen_error(loop, f"Bildschirmaufnahme abgebrochen: {msg}")
                _screen_stream["active"] = False
                break
            time.sleep(1)

    try:
        sct.close()
    except Exception:
        pass


def _notify_screen_error(loop, message):
    """Schickt eine Fehlermeldung ans Dashboard (einmalig)."""
    _print(f"[agent] Screen-Fehler: {message}")
    try:
        asyncio.run_coroutine_threadsafe(
            sio.emit("screen-error", {"id": DEVICE_ID, "error": message}, namespace="/agent"),
            loop,
        )
    except Exception:
        pass


def _notify_screen_mode(loop, mode, reason=""):
    """
    Teilt dem Dashboard mit, WIE Remote-Zugriff möglich ist. Wichtigster Fall:
    mode='shell' -> das Dashboard öffnet direkt eine Shell (headless/Shell-only).
    """
    _print(f"[agent] Screen-Modus: {mode} ({reason})")
    try:
        asyncio.run_coroutine_threadsafe(
            sio.emit("screen-mode", {"id": DEVICE_ID, "mode": mode, "reason": reason}, namespace="/agent"),
            loop,
        )
    except Exception:
        pass


@sio.on("screen-start", namespace="/agent")
async def on_screen_start(data):
    """Startet das Bildschirm-Streaming - oder signalisiert Shell-Modus bei headless."""
    loop = asyncio.get_event_loop()

    # Aufnahme-Qualität/FPS vom Backend übernehmen (aus den Einstellungen).
    if isinstance(data, dict):
        if data.get("quality"):
            _screen_stream["quality"] = int(data["quality"])
        if data.get("fps"):
            _screen_stream["fps"] = int(data["fps"])

    # Ohne Bildaufnahme-Pakete gibt es nichts zu streamen -> Shell anbieten.
    if not _SCREEN_AVAILABLE:
        _notify_screen_mode(loop, "shell",
                            "Bildaufnahme-Pakete (mss/Pillow) fehlen - es wird eine Shell geöffnet.")
        return

    # Ist überhaupt ein grafischer Bildschirm da? (setzt auf Linux ggf. DISPLAY)
    available, reason = _detect_graphical_display()
    if not available:
        _notify_screen_mode(loop, "shell", reason)
        return

    if _screen_stream["active"]:
        return  # läuft schon
    _screen_stream["active"] = True
    _screen_stream["thread"] = threading.Thread(target=_screen_capture_loop, args=(loop,), daemon=True)
    _screen_stream["thread"].start()
    _print("[agent] Bildschirm-Streaming gestartet")


@sio.on("screen-stop", namespace="/agent")
async def on_screen_stop(data):
    """Stoppt das Bildschirm-Streaming."""
    _screen_stream["active"] = False
    _print("[agent] Bildschirm-Streaming gestoppt")


@sio.on("screen-set-monitor", namespace="/agent")
async def on_screen_set_monitor(data):
    """Wechselt den gestreamten Bildschirm (Multi-Monitor). Der Capture-Loop
    übernimmt die Auswahl beim nächsten Frame automatisch."""
    if isinstance(data, dict) and data.get("monitor") is not None:
        try:
            # 0 = "Alle Bildschirme" (mss.monitors[0] = Gesamtfläche aller Monitore)
            _screen_stream["monitor"] = max(0, int(data["monitor"]))
            _print(f"[agent] Bildschirm gewechselt auf #{_screen_stream['monitor']}")
        except Exception:
            pass


# Umrechnungstabelle für Sondertasten vom Browser zu pynput
_SPECIAL_KEYS = {}
if _INPUT_AVAILABLE:
    _SPECIAL_KEYS = {
        "Enter": KeyboardKey.enter, "Backspace": KeyboardKey.backspace,
        "Tab": KeyboardKey.tab, "Escape": KeyboardKey.esc, " ": KeyboardKey.space,
        "ArrowUp": KeyboardKey.up, "ArrowDown": KeyboardKey.down,
        "ArrowLeft": KeyboardKey.left, "ArrowRight": KeyboardKey.right,
        "Delete": KeyboardKey.delete, "Home": KeyboardKey.home, "End": KeyboardKey.end,
        "PageUp": KeyboardKey.page_up, "PageDown": KeyboardKey.page_down,
        "Control": KeyboardKey.ctrl, "Alt": KeyboardKey.alt, "Shift": KeyboardKey.shift,
        "Meta": KeyboardKey.cmd,
    }


@sio.on("update-agent", namespace="/agent")
async def on_update_agent(data):
    """
    Aktualisiert den Agenten, indem der Client-Install-/Update-Befehl in einer
    eigenen Shell-Session ausgeführt wird (genau wie bei der Erstinstallation).
    Das Update-Skript stoppt den laufenden Agenten, ersetzt agent.py und startet
    den Dienst neu - deshalb läuft der Befehl LOSGELÖST vom Agent-Prozess.
    """
    _print("[agent] Update angefordert - starte Update-Befehl in eigener Shell...")
    await _emit_action_log("update", "received", "Update-Befehl empfangen")
    loop = asyncio.get_event_loop()
    detail = await loop.run_in_executor(None, _run_dist_command, "update")
    await _emit_action_log("update", detail.get("stage", "launched"), detail.get("detail", ""))


@sio.on("uninstall-agent", namespace="/agent")
async def on_uninstall_agent(data):
    """
    Deinstalliert den Agenten: führt das Uninstall-Skript in einer eigenen Shell
    aus (Dienst/Task entfernen, Programmordner löschen). Läuft losgelöst, da es
    den eigenen Prozess beendet.
    """
    _print("[agent] Deinstallation angefordert - starte Uninstall-Befehl in eigener Shell...")
    await _emit_action_log("uninstall", "received", "Uninstall-Befehl empfangen")
    loop = asyncio.get_event_loop()
    detail = await loop.run_in_executor(None, _run_dist_command, "uninstall")
    await _emit_action_log("uninstall", detail.get("stage", "launched"), detail.get("detail", ""))


async def _emit_action_log(kind: str, stage: str, detail: str = ""):
    """Meldet dem Backend (und damit dem Dashboard), was bei Update/Uninstall passiert."""
    try:
        await sio.emit("agent-action-log", {
            "id": DEVICE_ID, "kind": kind, "stage": stage, "detail": detail,
            "agent_version": AGENT_VERSION,
        }, namespace="/agent")
    except Exception:
        pass


def _run_dist_command(kind: str):
    """
    Startet den Update- bzw. Uninstall-Befehl als EIGENSTÄNDIGEN Prozess, der den
    Tod des Agent-Prozesses überlebt (das Skript stoppt den Agenten selbst).

    Linux: Wir starten den Befehl als TRANSIENTEN systemd-Dienst
    (`systemd-run --collect`). Ein solcher Dienst läuft in einem EIGENEN cgroup,
    unabhängig vom Dienst des Agenten. Dadurch überlebt er zuverlässig das
    `systemctl stop rapalle-agent` (dessen KillMode=control-group sonst alle
    Kindprozesse mitreißt - das war der Grund, warum der Uninstall zuvor
    scheinbar nichts tat).

    kind: 'update' | 'uninstall'

    Der exakte Befehl wird ins Agent-Log geschrieben, damit man ihn zum Testen
    auch von Hand auf dem Client ausführen kann.
    """
    import shutil

    try:
        if IS_WINDOWS:
            import base64
            fname = "update.ps1" if kind == "update" else "uninstall.ps1"
            url = f"{BACKEND_URL}/agent-dist/{fname}"
            inner_ps = f"iwr '{url}' -UseBasicParsing | iex"
            enc = base64.b64encode(inner_ps.encode("utf-16-le")).decode()
            taskname = "RapalleRmmUpdate" if kind == "update" else "RapalleRmmUninstall"
            event_id = "812" if kind == "update" else "811"
            tr = f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {enc}"

            _print(f"[agent] {kind}: exakter Befehl (Windows, ELEVATED PowerShell):")
            _print(f'         powershell -NoProfile -ExecutionPolicy Bypass -Command "{inner_ps}"')

            # (A) Bevorzugt: vorautorisierten SYSTEM-Task per EVENT auslösen. Das
            #     braucht KEINE Admin-Rechte im Agenten - die Aufgabenplanung führt
            #     das Skript als SYSTEM aus. Voraussetzung: elevate.ps1 wurde einmal
            #     ausgeführt (bzw. der Installer hat die Tasks eingerichtet).
            try:
                q = _run(["schtasks", "/query", "/tn", taskname],
                                   capture_output=True, text=True)
                task_exists = (q.returncode == 0)
            except Exception:
                task_exists = False
            if task_exists:
                triggered = False
                trig_err = ""
                # Primär: Write-EventLog -> Provider ist garantiert 'RapalleRMM'
                # (matcht die Event-Subscription des SYSTEM-Tasks sicher).
                try:
                    w = _run(
                        ["powershell", "-NoProfile", "-Command",
                         f"Write-EventLog -LogName Application -Source 'RapalleRMM' "
                         f"-EventId {event_id} -EntryType Information -Message 'rmm {kind}'"],
                        capture_output=True, text=True,
                    )
                    triggered = (w.returncode == 0)
                    if not triggered:
                        trig_err = (w.stderr or w.stdout or "").strip()
                except Exception as e:
                    trig_err = str(e)
                # Fallback: eventcreate (falls Write-EventLog nicht verfügbar/fehlerhaft)
                if not triggered:
                    try:
                        ev = _run(
                            ["eventcreate", "/L", "Application", "/SO", "RapalleRMM",
                             "/T", "INFORMATION", "/ID", event_id, "/D", f"rmm {kind}"],
                            capture_output=True, text=True,
                        )
                        triggered = (ev.returncode == 0)
                        if not triggered:
                            trig_err = (ev.stderr or ev.stdout or "").strip()
                    except Exception as e:
                        trig_err = str(e)
                if triggered:
                    _print(f"[agent] {kind}: SYSTEM-Task '{taskname}' per Event {event_id} ausgelöst (elevated).")
                    return {"stage": "launched",
                            "detail": f"SYSTEM-Task '{taskname}' per Event ausgelöst (elevated)."}
                else:
                    _print(f"[agent] Event-Auslösung fehlgeschlagen: {trig_err}")

            # (B) Sonst: SYSTEM-Task direkt anlegen (klappt nur, wenn Agent elevated).
            created = False
            schtasks_err = ""
            try:
                r = _run(
                    ["schtasks", "/create", "/tn", taskname, "/ru", "SYSTEM",
                     "/rl", "HIGHEST", "/sc", "ONCE", "/st", "00:00", "/f", "/tr", tr],
                    capture_output=True, text=True,
                )
                if r.returncode == 0:
                    _run(["schtasks", "/run", "/tn", taskname],
                                   capture_output=True, text=True)
                    created = True
                    _print(f"[agent] {kind}-Befehl als SYSTEM-Task '{taskname}' gestartet.")
                else:
                    schtasks_err = (r.stderr or r.stdout or "").strip()
                    _print(f"[agent] schtasks (SYSTEM) fehlgeschlagen: {schtasks_err} - Fallback.")
            except Exception as e:
                schtasks_err = str(e)
                _print(f"[agent] schtasks-Aufruf fehlgeschlagen ({e}) - Fallback.")
            if created:
                return {"stage": "launched", "detail": f"SYSTEM-Task '{taskname}' gestartet."}

            # (C) Fallback: losgelöste PowerShell mit Agent-Rechten (reicht nur, wenn
            #     der Agent schon elevated läuft).
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-Command", inner_ps],
                creationflags=flags, close_fds=True,
            )
            _print(f"[agent] {kind}-Befehl (Windows Fallback, losgelöst) gestartet.")
            return {"stage": "launched-fallback",
                    "detail": ("Kein elevated Weg verfügbar (Agent nicht als Admin und "
                               "keine Wartungs-Tasks installiert). EINMALIG als Administrator "
                               f"ausführen: powershell -NoProfile -ExecutionPolicy Bypass -Command "
                               f"\"iwr '{BACKEND_URL}/agent-dist/elevate.ps1' -UseBasicParsing | iex\"")}
        else:
            fname = "update.sh" if kind == "update" else "uninstall.sh"
            url = f"{BACKEND_URL}/agent-dist/{fname}"
            inner = f"curl -fsSL '{url}' | bash"
            logf = f"/tmp/rapalle-agent-{kind}.log"
            _print(f"[agent] {kind}: exakter Befehl (Linux, als root):")
            _print(f"         {inner}   (Log: {logf})")

            systemd_run = shutil.which("systemd-run") or (
                "/usr/bin/systemd-run" if os.path.exists("/usr/bin/systemd-run") else None
            )
            launched = False
            if systemd_run:
                try:
                    # Transienter, unabhängiger Dienst (eigener cgroup) -> überlebt
                    # das Stoppen des Agent-Dienstes. --collect räumt ihn nach Ende auf.
                    subprocess.Popen(
                        [systemd_run, "--collect", "--quiet",
                         "bash", "-c", f"{inner} >{logf} 2>&1"],
                        start_new_session=True, close_fds=True,
                    )
                    launched = True
                    _print(f"[agent] {kind}-Befehl über systemd-run (transienter Dienst) gestartet.")
                except Exception as e:
                    _print(f"[agent] systemd-run fehlgeschlagen ({e}) - nutze Fallback.")
            if not launched:
                # Fallback ohne systemd: vollständig losgelöste Shell (setsid+nohup+&).
                subprocess.Popen(
                    ["setsid", "bash", "-c", f"nohup {inner} >{logf} 2>&1 &"],
                    start_new_session=True, close_fds=True,
                )
                _print(f"[agent] {kind}-Befehl über setsid/nohup (Fallback) gestartet.")
                return {"stage": "launched-fallback",
                        "detail": f"ohne systemd gestartet (Log: {logf})"}
            return {"stage": "launched", "detail": f"systemd-run gestartet (Log: {logf})"}
    except Exception as e:
        _print(f"[agent] {kind}-Befehl fehlgeschlagen: {e}")
        return {"stage": "error", "detail": str(e)}
    return {"stage": "launched", "detail": ""}


# --------------------------------------------------------------
# Zwischenablage-Synchronisierung (Remote Screen)
# --------------------------------------------------------------
# Das Dashboard kann die lokale Zwischenablage an diesen PC senden
# (screen-input type="clipboard-set") oder die hiesige Zwischenablage
# abfragen (screen-clipboard-get -> Antwort-Event "screen-clipboard").
# Genutzt wird pyperclip (wird per Bootstrap automatisch nachinstalliert);
# schlaegt das fehl, wird ein klarer Fehler zurueckgemeldet.

def _clipboard_get() -> str:
    import pyperclip
    return pyperclip.paste() or ""


def _clipboard_set(text: str) -> None:
    import pyperclip
    pyperclip.copy(text or "")


@sio.on("screen-clipboard-get", namespace="/agent")
async def on_screen_clipboard_get(data):
    """Liest die lokale Zwischenablage und schickt sie ans Dashboard zurueck."""
    loop = asyncio.get_event_loop()
    try:
        text = await loop.run_in_executor(None, _clipboard_get)
        await sio.emit("screen-clipboard", {"id": DEVICE_ID, "text": text},
                       namespace="/agent")
    except Exception as e:
        await sio.emit("screen-clipboard", {"id": DEVICE_ID, "error": str(e)},
                       namespace="/agent")


@sio.on("screen-input", namespace="/agent")
async def on_screen_input(data):
    """
    Simuliert eine Maus-/Tastatureingabe, die vom Dashboard kommt.
    data.type ist einer von: "move", "click", "scroll", "key", "text", "combo"
    """
    if not _INPUT_AVAILABLE:
        return

    loop = asyncio.get_event_loop()
    # Input-Simulation ist blockierend -> in einen Thread auslagern
    await loop.run_in_executor(None, _apply_input, data)


def _apply_input(data):
    """Führt die eigentliche Eingabe-Simulation aus (läuft im Thread-Pool)."""
    try:
        kind = data.get("type")
        # Der gestreamte Monitor kann einen Offset zur Gesamtfläche haben
        # (z.B. zweiter Monitor rechts). Browser-Koordinaten sind relativ zum
        # gestreamten Bild -> Offset addieren, damit der Klick auf dem richtigen
        # Monitor landet.
        ox = int(_screen_stream.get("mon_left", 0) or 0)
        oy = int(_screen_stream.get("mon_top", 0) or 0)

        def _pos(d):
            return (int(d["x"]) + ox, int(d["y"]) + oy)

        if kind == "move":
            _mouse.position = _pos(data)

        elif kind == "click":
            _mouse.position = _pos(data)
            button = MouseButton.right if data.get("button") == "right" else MouseButton.left
            _mouse.click(button, 1)

        elif kind == "down":
            # Maustaste DRÜCKEN und gedrückt halten (Beginn eines Drag)
            _mouse.position = _pos(data)
            button = MouseButton.right if data.get("button") == "right" else MouseButton.left
            _mouse.press(button)

        elif kind == "up":
            # Maustaste LOSLASSEN (Ende eines Drag)
            _mouse.position = _pos(data)
            button = MouseButton.right if data.get("button") == "right" else MouseButton.left
            _mouse.release(button)

        elif kind == "double":
            # Doppelklick
            _mouse.position = _pos(data)
            _mouse.click(MouseButton.left, 2)

        elif kind == "scroll":
            _mouse.scroll(0, int(data.get("dy", 0)))

        elif kind == "text":
            # einzelnes Zeichen/kurzer Text tippen
            _keyboard.type(data.get("text", ""))

        elif kind == "key":
            # eine Sondertaste (Enter, Backspace, Pfeile, ...)
            key = _SPECIAL_KEYS.get(data.get("key"))
            if key:
                _keyboard.press(key)
                _keyboard.release(key)

        elif kind == "clipboard-set":
            # Zwischenablage des Dashboards auf diesem PC setzen (Clipboard-Sync)
            _clipboard_set(data.get("text", ""))

        elif kind == "combo":
            # Tastenkombination, z.B. Strg+C: data.keys = ["Control", "c"]
            keys = data.get("keys", [])
            resolved = [_SPECIAL_KEYS.get(k, k) for k in keys]
            for k in resolved:
                _keyboard.press(k)
            for k in reversed(resolved):
                _keyboard.release(k)
    except Exception as e:
        _print(f"[agent] Input-Fehler: {e}")


# --------------------------------------------------------------
# Programmstart
# --------------------------------------------------------------

async def main():
    _print(f"[agent] Gerätename : {DEVICE_NAME}")
    _print(f"[agent] Geräte-ID  : {DEVICE_ID}")
    _print(f"[agent] Backend    : {BACKEND_URL}")
    _print(f"[agent] Remote Screen: {'verfügbar' if _SCREEN_AVAILABLE else 'deaktiviert (mss/Pillow fehlt)'}")
    _print(f"[agent] Fernsteuerung: {'verfügbar' if _INPUT_AVAILABLE else 'deaktiviert (pynput fehlt)'}")

    # Heartbeat läuft als eigene Hintergrund-Aufgabe, unabhängig von der Verbindung
    asyncio.create_task(heartbeat_loop())

    # Verbindungsschleife: WIR steuern das Wiederverbinden selbst. Vor jedem
    # Versuch sauber trennen, damit die interne Client-Zustand nicht auf
    # "Already connected" hängen bleibt.
    if any(x in BACKEND_URL for x in ("localhost", "127.0.0.1")):
        _print(f"[agent] WARNUNG: BACKEND_URL zeigt auf {BACKEND_URL} - ein entfernter/"
               f"öffentlicher Client kann das NICHT erreichen. In der .env die öffentliche "
               f"Adresse eintragen (z.B. https://domain) bzw. im Dashboard unter "
               f"Einstellungen -> Allgemein -> 'Server-URL' setzen.")
    while True:
        try:
            if sio.connected:
                try:
                    await sio.disconnect()
                except Exception:
                    pass
            _print(f"[agent] Verbinde zu {BACKEND_URL} (Namespace /agent)...")
            await sio.connect(BACKEND_URL, auth={"token": AGENT_TOKEN},
                              namespaces=["/agent"], wait_timeout=15)
            await sio.wait()  # blockiert, solange die Verbindung steht
            _print("[agent] Verbindung getrennt.")
        except Exception as e:
            _print(f"[agent] Verbindung zu {BACKEND_URL} fehlgeschlagen ({e!r}), neuer Versuch in 3s...")
        # Immer sauber trennen, bevor der nächste Versuch startet.
        try:
            await sio.disconnect()
        except Exception:
            pass
        await asyncio.sleep(3)


if __name__ == "__main__":
    # WICHTIG: auf dem OBEN gesetzten Loop laufen (nicht asyncio.run(), das einen
    # neuen Loop anlegen würde) -> derselbe Loop, auf dem 'sio' erstellt wurde.
    asyncio.set_event_loop(_AGENT_LOOP)
    try:
        _AGENT_LOOP.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _AGENT_LOOP.close()
        except Exception:
            pass

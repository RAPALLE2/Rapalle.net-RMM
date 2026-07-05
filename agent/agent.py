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

import asyncio
import base64
import io
import logging
import os
import platform
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

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

# Der Socket.IO-Client, über den die gesamte Kommunikation läuft
sio = socketio.AsyncClient(reconnection=True, reconnection_delay=3)


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
        },
        namespace="/agent",
    )


@sio.event(namespace="/agent")
async def connect_error(data):
    _print(f"[agent] Verbindungsfehler: {data}")


# --------------------------------------------------------------
# Regelmäßige Metriken (Heartbeat)
# --------------------------------------------------------------

# Merker für die Netzwerk-Durchsatz-Berechnung (Differenz zwischen zwei Messungen)
_last_net = {"ts": None, "bytes_sent": 0, "bytes_recv": 0}


def collect_metrics() -> dict:
    """
    Sammelt die aktuellen System-Metriken. Läuft in einem Hintergrund-Thread
    (siehe heartbeat_loop), weil psutil.cpu_percent() kurz "blockiert"
    (es misst die Last über ein kleines Zeitfenster).
    """
    cpu_percent = psutil.cpu_percent(interval=0.5)
    memory = psutil.virtual_memory()
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

    return {
        "cpuLoad": round(cpu_percent),
        "cpuCores": psutil.cpu_count(logical=False) or 0,   # physische Kerne
        "cpuThreads": psutil.cpu_count(logical=True) or 0,  # logische Kerne (Threads)
        "memUsed": memory.total - memory.available,
        "memTotal": memory.total,
        "memAvailable": memory.available,   # freier/verfügbarer RAM
        "diskUsed": disk.used,
        "diskTotal": disk.total,
        "disks": disks,
        "netIn": round(net_in_per_s),      # Bytes pro Sekunde empfangen
        "netOut": round(net_out_per_s),    # Bytes pro Sekunde gesendet
        "uptime": int(time.time() - psutil.boot_time()),
    }


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


def _run_shell_command(command: str, session_id: str | None = None) -> tuple[str, str, int]:
    """
    Führt einen Shell-Befehl aus und gibt (stdout, stderr, exit_code) zurück.

    Ist eine session_id gesetzt, läuft der Befehl im gemerkten Arbeitsverzeichnis
    dieser Terminal-Session, und ein evtl. per "cd" geändertes Verzeichnis wird
    für den nächsten Befehl übernommen. Das neue Verzeichnis wird über eine
    kleine Temp-Datei zurückgemeldet - so bleibt die eigentliche Ausgabe (stdout)
    sauber und Exit-Codes sowie mehrzeilige Befehle funktionieren auf Linux
    UND Windows korrekt.
    """
    start_cwd = _session_cwd(session_id)
    cwd_file = None
    bat_file = None
    try:
        fd, cwd_file = tempfile.mkstemp(prefix="rmm_cwd_")
        os.close(fd)

        if os.name == "nt":
            # Windows: temporäre .bat für saubere Mehrzeilen-/Fehlercode-Behandlung.
            fd, bat_file = tempfile.mkstemp(prefix="rmm_cmd_", suffix=".bat")
            os.close(fd)
            with open(bat_file, "w", encoding="utf-8") as f:
                f.write("@echo off\r\n")
                f.write(command + "\r\n")
                f.write('set "__rmm_rc=%ERRORLEVEL%"\r\n')
                f.write(f'cd > "{cwd_file}"\r\n')
                f.write("exit /b %__rmm_rc%\r\n")
            argv = ["cmd", "/c", bat_file]
        else:
            # POSIX: sh -c mit Wrapper, der nach dem Befehl das PWD wegschreibt.
            wrapper = (
                f"{command}\n"
                f"__rmm_rc=$?\n"
                f"printf '%s' \"$PWD\" > '{cwd_file}' 2>/dev/null\n"
                f"exit $__rmm_rc\n"
            )
            argv = ["/bin/sh", "-c", wrapper]

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
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
        for path in (cwd_file, bat_file):
            if path:
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

    loop = asyncio.get_event_loop()
    stdout, stderr, exit_code = await loop.run_in_executor(
        None, _run_shell_command, command, session_id
    )

    await sio.emit(
        "exec-result",
        {"requestId": request_id, "stdout": stdout, "stderr": stderr, "code": exit_code},
        namespace="/agent",
    )


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
                stat = entry.stat()
                size, mtime = stat.st_size, int(stat.st_mtime * 1000)
            except Exception:
                size, mtime = 0, 0  # z.B. bei kaputten Symlinks - einfach ignorieren
            entries.append({
                "name": entry.name,
                "path": os.path.join(path, entry.name),
                "isDir": entry.is_dir(),
                "size": size,
                "mtime": mtime,
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

_screen_stream = {"active": False, "thread": None, "sid_loop": None}


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

    monitor = sct.monitors[1]  # der primäre Bildschirm
    screen_w, screen_h = monitor["width"], monitor["height"]
    consecutive_errors = 0

    while _screen_stream["active"]:
        try:
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            consecutive_errors = 0  # erfolgreich -> Fehlerzähler zurücksetzen

            # Auf max. 1280px Breite herunterskalieren (spart Bandbreite)
            max_width = 1280
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)))

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=55)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

            asyncio.run_coroutine_threadsafe(
                sio.emit("screen-frame", {
                    "id": DEVICE_ID,
                    "image": b64,
                    "width": screen_w,
                    "height": screen_h,
                }, namespace="/agent"),
                loop,
            )
            time.sleep(0.1)  # ~10 Bilder pro Sekunde
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
    Aktualisiert den Agenten selbst: lädt die neueste agent.py vom Backend,
    überschreibt die eigene Datei und startet den Prozess neu.
    """
    _print("[agent] Update angefordert - lade neueste Version...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _self_update)


def _self_update():
    """
    Lädt die neue agent.py herunter, ersetzt die eigene Datei und startet neu.
    Läuft im Thread-Pool, da urllib und Prozess-Neustart blockierend sind.
    """
    import sys
    import urllib.request

    try:
        url = f"{BACKEND_URL}/api/agent/latest"
        with urllib.request.urlopen(url, timeout=30) as resp:
            new_code = resp.read().decode("utf-8")

        if not new_code.strip() or "agent.py nicht gefunden" in new_code:
            _print("[agent] Update abgebrochen: leerer/ungültiger Download")
            return

        own_path = Path(__file__).resolve()
        # Sicherheitshalber die alte Version als .bak behalten
        backup = own_path.with_suffix(".py.bak")
        try:
            backup.write_text(own_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

        own_path.write_text(new_code, encoding="utf-8")
        _print("[agent] Neue Version gespeichert - starte neu...")

        # Prozess durch sich selbst ersetzen (gleicher Interpreter, gleiches Skript).
        # Der geplante Autostart-Task bleibt davon unberührt; hier startet der
        # laufende Prozess einfach mit dem neuen Code neu.
        os.execv(sys.executable, [sys.executable, str(own_path)])
    except Exception as e:
        _print(f"[agent] Selbst-Update fehlgeschlagen: {e}")


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

        if kind == "move":
            _mouse.position = (int(data["x"]), int(data["y"]))

        elif kind == "click":
            _mouse.position = (int(data["x"]), int(data["y"]))
            button = MouseButton.right if data.get("button") == "right" else MouseButton.left
            _mouse.click(button, 1)

        elif kind == "down":
            # Maustaste DRÜCKEN und gedrückt halten (Beginn eines Drag)
            _mouse.position = (int(data["x"]), int(data["y"]))
            button = MouseButton.right if data.get("button") == "right" else MouseButton.left
            _mouse.press(button)

        elif kind == "up":
            # Maustaste LOSLASSEN (Ende eines Drag)
            _mouse.position = (int(data["x"]), int(data["y"]))
            button = MouseButton.right if data.get("button") == "right" else MouseButton.left
            _mouse.release(button)

        elif kind == "double":
            # Doppelklick
            _mouse.position = (int(data["x"]), int(data["y"]))
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

    # Verbindungsschleife: falls die Verbindung abbricht, wird es alle 3s erneut versucht
    while True:
        try:
            await sio.connect(BACKEND_URL, auth={"token": AGENT_TOKEN}, namespaces=["/agent"])
            await sio.wait()  # hält das Programm am Laufen, solange die Verbindung steht
        except Exception as e:
            _print(f"[agent] Verbindung fehlgeschlagen ({e}), neuer Versuch in 3s...")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())

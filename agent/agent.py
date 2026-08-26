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
import functools
import hashlib
import io
import json
import logging
import os
import platform
import random
import re
import shutil
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


# ------------------------------------------------------------------
# WATCHDOG (Selbst-Neustart auch bei HARTEN Abstuerzen)
# ------------------------------------------------------------------
# Der bisherige Crash-Schutz unten (os.execv nach Traceback) faengt nur
# PYTHON-Fehler. Stirbt der Prozess dagegen nativ (z.B. Segfault in
# mss/Pillow beim Bildschirm-Grabben nach Sitzungswechsel/Sperrbildschirm,
# OOM-Kill, Scheduler-Kill), steht im Log NICHTS und niemand startet neu -
# exakt das beobachtete "am Ende gecrasht, kein Selbst-Restart".
#
# Loesung: Beim Start laeuft dieser Prozess zuerst als winziger WATCHDOG
# (nur Standardbibliothek, kann selbst nicht nativ crashen) und startet den
# eigentlichen Agenten als KINDPROZESS (Umgebungsvariable RMM_SUPERVISED=1).
# Stirbt das Kind - egal wie -, wird der Exit-Code geloggt, in
# last_crash.txt vermerkt und nach kurzer Wartezeit neu gestartet
# (Backoff bei Crash-Schleifen, max. 60s). Exit-Code 0 = gewolltes Ende,
# dann beendet sich auch der Watchdog.
# Deaktivierbar mit RMM_NO_WATCHDOG=1 (z.B. fuer Debugging in der Konsole).
def _run_watchdog() -> "int":
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    crash_file = os.path.join(here, "last_crash.txt")
    fast_fails = 0
    log.info("[agent] Watchdog aktiv (PID %s) - ueberwacht den Agent-Prozess.", os.getpid())
    while True:
        env = dict(os.environ)
        env["RMM_SUPERVISED"] = "1"
        started = time.time()
        try:
            child = subprocess.Popen([sys.executable] + sys.argv, cwd=here, env=env)
            code = child.wait()
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            log.error("[agent] Watchdog: Agent-Start fehlgeschlagen: %r", e)
            code = -1
        ran = time.time() - started
        if code == 0:
            log.info("[agent] Watchdog: Agent regulaer beendet (Exit 0) - Watchdog endet.")
            return 0
        log.error("[agent] Watchdog: Agent-Prozess gestorben (Exit-Code %s) nach %.0fs Laufzeit.", code, ran)
        try:
            # Nur schreiben, wenn der Agent selbst keinen Traceback hinterlassen
            # hat (harter/nativer Tod) - Python-Crashes schreiben die Datei unten.
            if ran > 2:
                with open(crash_file, "w", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S")
                            + f"\nHarter Prozess-Tod ohne Python-Traceback (Exit-Code {code},"
                              f" Laufzeit {ran:.0f}s).\nTypische Ursachen: nativer Absturz in"
                              " mss/Pillow/pynput, OOM-Kill, externes Beenden.\n")
        except OSError:
            pass
        fast_fails = fast_fails + 1 if ran < 300 else 0
        delay = min(60, 5 * (fast_fails + 1))
        log.info("[agent] Watchdog: Neustart in %ss...", delay)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return 0


if (__name__ == "__main__"
        and os.environ.get("RMM_SUPERVISED") != "1"
        and os.environ.get("RMM_NO_WATCHDOG") != "1"
        # Sicherheitsnetz: Sollte agent.py doch einmal mit --screen-helper
        # aufgerufen werden (alte Aufgabe, Skript von Hand), darf daraus kein
        # zweiter vollwertiger Agent entstehen.
        and "--screen-helper" not in sys.argv):
    sys.exit(_run_watchdog())


def _print(msg):
    """Ersetzt print() - schreibt in Konsole UND Log-Datei."""
    log.info(msg)


# --- Agent-Konsole für das Dashboard -------------------------------------
# Rollierender Puffer ALLER Log-Zeilen dieses Agenten. Das Dashboard kann per
# "agent-console-open" die Historie abrufen und danach live mitlesen
# (Client-Terminal -> Schalter "Agent-Konsole"). Der Handler hängt am
# Root-Logger, erfasst also alles, was über log/_print läuft.
import collections as _collections

_CONSOLE_BUFFER = _collections.deque(maxlen=2000)
_console_stream = {"on": False}


class _ConsoleBufferHandler(logging.Handler):
    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:
            return
        _CONSOLE_BUFFER.append(line)
        if not _console_stream["on"]:
            return
        # Live an das Backend weiterreichen (best effort; sio/Loop existieren
        # erst nach dem Startup - vorher wird nur gepuffert).
        try:
            if "sio" in globals() and sio.connected and "_AGENT_LOOP" in globals():
                asyncio.run_coroutine_threadsafe(
                    sio.emit("agent-console", {"id": DEVICE_ID, "data": line + "\r\n"},
                             namespace="/agent"),
                    _AGENT_LOOP,
                )
        except Exception:
            pass


_console_handler = _ConsoleBufferHandler()
_console_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logging.getLogger().addHandler(_console_handler)


# Optionale Bibliotheken für Remote Screen. Der Agent funktioniert auch ohne sie
# (dann ist nur die Fernsteuerung deaktiviert, alles andere läuft normal weiter).
# Wir fangen bewusst JEDEN Fehler ab (nicht nur ImportError), weil z.B. pynput
# beim Import in einer Umgebung ohne grafische Sitzung (Windows-Dienst als
# SYSTEM, Linux ohne X11) mit anderen Fehlern abbrechen kann - das darf den
# Agenten NICHT komplett lahmlegen.
# WICHTIG: Den Grund des Fehlschlags merken. Frueher wurde die Ausnahme
# stillschweigend verschluckt - der Benutzer sah dann nur "mss/Pillow fehlen"
# und hatte keinerlei Anhaltspunkt, WARUM. Meist fehlen die Pakete gar nicht,
# sondern sind kaputt (halbe Installation, falscher Interpreter, fehlende
# System-Bibliothek). Genau das steht jetzt in _SCREEN_ERROR.
_SCREEN_ERROR = ""
_INPUT_ERROR = ""

try:
    import mss  # Screenshots aufnehmen (schnell, plattformübergreifend)
    from PIL import Image  # zum Verkleinern/Kodieren der Screenshots
    _SCREEN_AVAILABLE = True
except Exception as _e:
    _SCREEN_AVAILABLE = False
    _SCREEN_ERROR = f"{type(_e).__name__}: {_e}"

try:
    from pynput.mouse import Controller as MouseController, Button as MouseButton
    from pynput.keyboard import Controller as KeyboardController, Key as KeyboardKey
    _INPUT_AVAILABLE = True
except Exception as _e:
    _INPUT_AVAILABLE = False
    _INPUT_ERROR = f"{type(_e).__name__}: {_e}"

# .env Datei aus demselben Ordner wie dieses Skript laden
load_dotenv(Path(__file__).resolve().parent / ".env")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:4000")
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "change-me-super-secret")
ENROLLMENT_TOKEN = os.getenv("ENROLLMENT_TOKEN", "").strip() or None
DEVICE_NAME = os.getenv("DEVICE_NAME") or socket.gethostname()

# ---------------------------------------------------------------------------
# Sprache der GERAETE-Dialoge (Zustimmung zum Remote-Bildschirm)
# ---------------------------------------------------------------------------
# Diese Texte sieht die Person, die VOR dem Geraet sitzt - nicht der Benutzer
# im Dashboard. Deshalb kann hier NICHT die Dashboard-Sprache gelten: mehrere
# Personen mit unterschiedlichen Spracheinstellungen koennen dasselbe Geraet
# ansehen, und der Agent erfaehrt beim Verbindungsaufbau nicht, welche das ist.
#
# Die Sprache haengt also am Geraet und wird in der .env gesetzt:
#     AGENT_LANG=en
# Voreinstellung ist bewusst "de" - damit aendert ein Update die Dialoge auf
# bestehenden Geraeten NICHT stillschweigend um.
AGENT_LANG = (os.getenv("AGENT_LANG", "de") or "de").strip().lower()[:2]
if AGENT_LANG not in ("de", "en"):
    AGENT_LANG = "de"

_AGENT_TEXTS = {
    "de": {
        "consent_window": "RAPALLE.net RMM - Remote-Bildschirm",
        "consent_title": "Remote-Bildschirm zulassen?",
        "consent_requested": "Remote-Bildschirm angefragt",
        "consent_body": "{who} möchte den Bildschirm dieses Computers\nsehen und steuern.",
        "consent_body_1line": "{who} möchte den Bildschirm dieses Computers sehen und steuern.",
        "consent_ask": "Zulassen? (Ohne Antwort wird nach {secs} Sekunden automatisch abgelehnt.)",
        "allow": "Zulassen",
        "allow_check": "✓ Zulassen",
        "deny": "Ablehnen",
        "auto_deny_in": "Automatisch abgelehnt in {secs} s",
        # ---- Log-Ausgaben (agent.log + Agent-Konsole im Dashboard) ----
        # Auch die Logs folgen AGENT_LANG: Wer den Agenten auf Deutsch
        # ausrollt, soll sie nicht auf Englisch lesen muessen.
        "log_device_name": "Gerätename : {name}",
        "log_device_id": "Geräte-ID  : {id}",
        "log_backend": "Backend    : {url}",
        "log_remote_ctl": "Fernsteuerung: {state}",
        "log_available": "verfügbar",
        "log_disabled": "deaktiviert",
        "log_session": "Sitzung {sid} | aktive Konsolensitzung {csid} | Bildschirm-Helfer: {helper}",
        "log_helper_used": "wird verwendet (Dienst in Sitzung 0)",
        "log_helper_unused": "nicht nötig",
        "log_connected": "Verbunden mit {url} als '{name}' ({id})",
        "log_disconnected": "Verbindung getrennt.",
        "log_conn_failed": "Verbindung zu {url} fehlgeschlagen ({err}), neuer Versuch in 3s...",
        "log_screen_started": "Bildschirm-Streaming gestartet",
        "log_screen_stopped": "Bildschirm-Streaming gestoppt",
        "log_screen_allowed": "Remote-Bildschirm am Gerät ZUGELASSEN",
        "log_screen_denied": "Remote-Bildschirm am Gerät ABGELEHNT (oder keine Antwort)",
        "log_nobody_home": "Remote-Bildschirm: niemand angemeldet - verbinde ohne Abfrage",
        "log_monitor_switched": "Bildschirm gewechselt auf #{n}",
        "log_update_sent": "Update-Bestätigung an das Backend gesendet.",
        "log_metrics_failed": "Fehler beim Senden der Metriken: {err}",
        "log_input_error": "Eingabe-Fehler: {err}",
    },
    "en": {
        "consent_window": "RAPALLE.net RMM - Remote screen",
        "consent_title": "Allow remote screen access?",
        "consent_requested": "Remote screen requested",
        "consent_body": "{who} would like to view and control\nthe screen of this computer.",
        "consent_body_1line": "{who} would like to view and control the screen of this computer.",
        "consent_ask": "Allow? (Without an answer this is denied automatically after {secs} seconds.)",
        "allow": "Allow",
        "allow_check": "✓ Allow",
        "deny": "Deny",
        "auto_deny_in": "Automatically denied in {secs} s",
        # ---- Log output (agent.log + agent console in the dashboard) ----
        "log_device_name": "Device name : {name}",
        "log_device_id": "Device ID   : {id}",
        "log_backend": "Backend     : {url}",
        "log_remote_ctl": "Remote control: {state}",
        "log_available": "available",
        "log_disabled": "disabled",
        "log_session": "Session {sid} | active console session {csid} | screen helper: {helper}",
        "log_helper_used": "in use (service in session 0)",
        "log_helper_unused": "not needed",
        "log_connected": "Connected to {url} as '{name}' ({id})",
        "log_disconnected": "Disconnected.",
        "log_conn_failed": "Connection to {url} failed ({err}), retrying in 3s...",
        "log_screen_started": "Screen streaming started",
        "log_screen_stopped": "Screen streaming stopped",
        "log_screen_allowed": "Remote screen ALLOWED on the device",
        "log_screen_denied": "Remote screen DENIED on the device (or no answer)",
        "log_nobody_home": "Remote screen: nobody logged in - connecting without asking",
        "log_monitor_switched": "Switched to monitor #{n}",
        "log_update_sent": "Update confirmation sent to the backend.",
        "log_metrics_failed": "Failed to send metrics: {err}",
        "log_input_error": "Input error: {err}",
    },
}


def _at(key: str, **kw) -> str:
    """Text fuer die Geraete-Dialoge in der Sprache aus AGENT_LANG."""
    tbl = _AGENT_TEXTS.get(AGENT_LANG) or _AGENT_TEXTS["de"]
    txt = tbl.get(key) or _AGENT_TEXTS["de"].get(key) or key
    return txt.format(**kw) if kw else txt

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
    """
    subprocess.run-Ersatz, der unter Windows kein Fenster aufblitzen lässt.

    WICHTIG - Zeichensatz: text=True allein benutzt die Locale-Codepage
    (auf deutschen Systemen cp1252). winget und PowerShell schreiben aber
    UTF-8 bzw. Zeichen, die es in cp1252 gar nicht gibt (0x81, 0x8d, 0x90,
    0x9d ...). Das Ergebnis war ein UnicodeDecodeError MITTEN im Scan -
    die aufrufende Funktion fing ihn ab und meldete brav "0 Aktualisierungen
    gefunden". Deshalb hier fest UTF-8 mit errors='replace': lieber ein
    kaputtes Zeichen im Namen als ein stillschweigend leeres Scan-Ergebnis.
    """
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    if kwargs.get("text"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
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


def _read_agent_code_hash() -> str:
    """
    Prüfsumme des eigenen Quellcodes.

    version.txt wird beim Entwickeln regelmäßig vergessen. Steht dort weiter
    dieselbe Nummer, hält das Backend einen uralten Agenten für aktuell und
    schickt nie ein Update - genau deshalb fehlten auf den Clients zuletzt
    die Patch-Handler, obwohl agent.py im Projekt längst neuer war. Die
    Prüfsumme sagt dagegen die Wahrheit über den Code, der wirklich läuft.
    """
    try:
        import hashlib
        data = Path(__file__).resolve().read_bytes()
        return hashlib.sha256(data).hexdigest()[:16]
    except Exception:
        return ""


AGENT_CODE_HASH = _read_agent_code_hash()

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

# Wird gesetzt, sobald das Backend unsere Anmeldung BESTÄTIGT hat
# ('register-ack'). Solange es nicht gesetzt ist, schweigt der Agent -
# er belegt dann keine Server-Kapazität, die gerade ein anderer Agent
# für seine Anmeldung braucht.
_REGISTERED = asyncio.Event()

# Damit nicht alle Agenten im selben Moment anklopfen (typisch nach einem
# Server-Neustart), verzögert jeder Agent seinen ERSTEN Versuch um eine
# feste, aus der eigenen Geräte-ID abgeleitete Spanne. Fest statt zufällig,
# damit die Reihenfolge über Neustarts hinweg stabil bleibt und sich die
# Agenten nicht immer wieder gegenseitig in die Quere kommen.
CONNECT_SPREAD_S = max(0, int(os.getenv("AGENT_CONNECT_SPREAD_S", "30") or 30))


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

def _detect_device_type():
    """
    Best-effort-Erkennung, ob dieser Rechner eine VM, ein LXC-Container oder
    physische Hardware ist. Ergebnis: "vm" | "lxc" | "physical" | None
    (None = unbekannt, dann wird am Backend NICHTS geändert).
    Damit ist im Bearbeiten-Dialog automatisch der richtige Gerätetyp
    vorausgewählt, statt fälschlich "Physisches Gerät".
    """
    try:
        sysname = platform.system().lower()
        if sysname == "linux":
            # 1) systemd-detect-virt ist die zuverlässigste Quelle.
            try:
                out = subprocess.run(["systemd-detect-virt"], capture_output=True,
                                     text=True, timeout=5).stdout.strip().lower()
                if out and out != "none":
                    if out in ("lxc", "lxc-libvirt", "openvz", "systemd-nspawn", "docker", "podman"):
                        return "lxc"
                    return "vm"
                if out == "none":
                    return "physical"
            except Exception:
                pass
            # 2) Container-Marker (PID 1-Umgebung / /run-Dateien)
            try:
                env = Path("/proc/1/environ").read_bytes()
                if b"container=lxc" in env or Path("/run/systemd/container").exists():
                    return "lxc"
            except Exception:
                pass
            # 3) Hypervisor-Flag der CPU + DMI-Produktname
            try:
                if "hypervisor" in Path("/proc/cpuinfo").read_text(errors="ignore"):
                    return "vm"
            except Exception:
                pass
            try:
                prod = Path("/sys/class/dmi/id/product_name").read_text(errors="ignore").lower()
                if any(k in prod for k in ("kvm", "qemu", "vmware", "virtualbox", "virtual machine", "hyper-v")):
                    return "vm"
                if prod.strip():
                    return "physical"
            except Exception:
                pass
            return None
        if sysname == "windows":
            # Hersteller/Modell abfragen - typische VM-Signaturen matchen.
            try:
                out = subprocess.run(
                    ["wmic", "computersystem", "get", "manufacturer,model"],
                    capture_output=True, text=True, timeout=10).stdout.lower()
                if any(k in out for k in ("vmware", "virtualbox", "qemu", "kvm", "virtual machine", "xen", "parallels")):
                    return "vm"
                if out.strip():
                    return "physical"
            except Exception:
                pass
            return None
    except Exception:
        pass
    return None


DETECTED_DEVICE_TYPE = _detect_device_type()


def _read_and_clear_last_crash():
    """
    Liest last_crash.txt (vom Crash-Schutz in __main__ geschrieben), löscht
    die Datei und gibt den Inhalt (gekürzt) zurück - wird beim Registrieren
    ans Backend gemeldet und dort dem Nutzer angezeigt.
    """
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_crash.txt")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
        try:
            os.remove(path)
        except OSError:
            pass
        return content[-4000:] if content else None
    except Exception:
        return None


@sio.event(namespace="/agent")
async def connect():
    """Wird automatisch aufgerufen, sobald die Verbindung zum Backend steht."""
    global _JUST_UPDATED
    _print(f"[agent] {_at('log_connected', url=BACKEND_URL, name=DEVICE_NAME, id=DEVICE_ID)}")
    await sio.emit(
        "register",
        {
            "id": DEVICE_ID,
            "hostname": DEVICE_NAME,
            "platform": platform.system(),       # "Windows" oder "Linux"
            "arch": platform.machine(),           # z.B. "AMD64", "x86_64"
            "release": OS_RELEASE,                # z.B. "Ubuntu 22.04" / "Windows 11"
            "ip": get_local_ip(),
            # Die Netze, in denen dieses Geraet haengt - fuer Site-to-Site.
            # Ohne diese Angabe muesste das Backend das Netz raten (bisher:
            # pauschal /24). Bei einem /16 oder /22 fehlten dann Teile des
            # Netzes in den Routen, und der Benutzer erreichte einen Teil
            # der Geraete nicht - ohne erkennbaren Grund.
            "subnets": get_local_subnets(),
            "enrollment_token": ENROLLMENT_TOKEN,  # nur beim ersten Mal relevant
            "updated": _JUST_UPDATED,              # true = kommt frisch aus einem Update
            "agent_version": AGENT_VERSION,        # eigene Version (für "veraltet"-Hinweis)
            "agent_code_hash": AGENT_CODE_HASH,     # Prüfsumme des laufenden Codes
            # Merkmal dieser Fassung: Kennt der Agent den Bildschirm-Helfer
            # fuer Windows-Sitzung 0? Damit sieht man im Dashboard sofort, ob
            # auf dem Client wirklich die neue Datei laeuft - eine Versions-
            # nummer allein hat das zuletzt nicht verlaesslich gezeigt.
            "screen_helper": True,
            # Patch-Fähigkeit ungefragt mitteilen. Das Backend muss dadurch
            # nicht erst einen Ping schicken, um zu wissen, ob ein
            # Patch-Auftrag auf diesem Client überhaupt Sinn ergibt.
            "patch_protocol": PATCH_PROTOCOL,
            "patch_sources": _patch_sources(),
            "device_type": DETECTED_DEVICE_TYPE,   # "vm"/"lxc"/"physical"/None (Auto-Erkennung)
            "last_crash": _read_and_clear_last_crash(),  # Traceback des letzten Absturzes (falls vorhanden)
        },
        namespace="/agent",
    )
    if _JUST_UPDATED:
        _print(f"[agent] {_at('log_update_sent')}")
        _JUST_UPDATED = False  # nur einmal melden, nicht bei jedem Reconnect


@sio.on("register-ack", namespace="/agent")
async def on_register_ack(data=None):
    """
    Das Backend hat unsere Anmeldung fertig verarbeitet.

    Erst ab hier schicken wir Heartbeats. Vorher wäre jeder Heartbeat nur
    zusätzliche Last auf einem Server, der gerade reihum alle anderen
    Agenten aufnimmt - genau die Überlast, die den Container umgebracht hat.
    """
    _REGISTERED.set()
    _print("[agent] Anmeldung vom Server bestätigt - Heartbeats starten")


@sio.event(namespace="/agent")
async def disconnect():
    """Verbindung weg -> Heartbeats pausieren, bis wir wieder angemeldet sind."""
    _REGISTERED.clear()


@sio.event(namespace="/agent")
async def connect_error(data):
    _print(f"[agent] Verbindungsfehler zu {BACKEND_URL}: {data!r} "
           f"(check: reachable? TLS certificate valid? does the proxy forward /socket.io?)")


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


def _mac_and_interfaces() -> dict:
    """
    Ermittelt die primäre MAC-Adresse und eine Liste der Netzwerk-Interfaces
    (Name, MAC, IPv4). Rein informativ für die Client-Panels.
    """
    result = {"mac": None, "interfaces": []}
    try:
        import uuid as _uuid
        node = _uuid.getnode()
        # getnode() liefert bei zufälligem Fallback ein Bit gesetzt -> dann unklar.
        mac = ":".join(f"{(node >> ele) & 0xff:02x}" for ele in range(40, -8, -8))
        result["mac"] = mac
    except Exception:
        pass
    try:
        addrs = psutil.net_if_addrs()
        for name, entries in addrs.items():
            nic = {"name": name, "mac": None, "ipv4": None}
            for e in entries:
                fam = getattr(e, "family", None)
                famname = getattr(fam, "name", str(fam))
                if famname in ("AF_LINK", "AF_PACKET") or (hasattr(__import__("socket"), "AF_LINK") and fam == __import__("socket").AF_LINK):
                    nic["mac"] = e.address
                elif fam == __import__("socket").AF_INET:
                    nic["ipv4"] = e.address
            if nic["ipv4"] or nic["mac"]:
                result["interfaces"].append(nic)
        # Primäre MAC bevorzugt vom Interface mit der Standard-IP nehmen.
        primary_ip = get_local_ip()
        for nic in result["interfaces"]:
            if nic["ipv4"] == primary_ip and nic["mac"]:
                result["mac"] = nic["mac"]
                break
    except Exception:
        pass
    return result


def _static_hardware() -> dict:
    global _static_hw_cache
    if _static_hw_cache is not None:
        return _static_hw_cache
    plat = __import__("platform")
    net = _mac_and_interfaces()
    info = {
        "cpuModel": _cpu_model(),
        "arch": _safe(lambda: plat.machine()) or "",
        "gpuModels": _gpu_models(),
        "ramModules": _ram_modules(),
        "cpuMaxFreq": _safe(lambda: round(psutil.cpu_freq().max)) if _safe(lambda: psutil.cpu_freq()) else None,
        "mac": net.get("mac"),
        "interfaces": net.get("interfaces", []),
        "hostname": DEVICE_NAME,
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


def get_local_subnets() -> list:
    """
    Ermittelt die IPv4-Netze, in denen dieses Geraet haengt.

    Beispiel: Adresse 192.168.178.79 mit Maske 255.255.255.0 ergibt
    "192.168.178.0/24".

    Ausgelassen werden Loopback (127.x), automatische Adressen (169.254.x)
    und die Tunnel-Netze selbst - Letztere wuerden eine Route auf sich
    selbst erzeugen.
    """
    out = []
    try:
        import ipaddress as _ip
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if getattr(a, "family", None) != socket.AF_INET:
                    continue
                ip, mask = a.address, getattr(a, "netmask", None)
                if not ip or not mask:
                    continue
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                try:
                    net = _ip.ip_network(f"{ip}/{mask}", strict=False)
                except ValueError:
                    continue
                # Ein /32 ist kein Netz, sondern eine einzelne Adresse.
                if net.prefixlen >= 31:
                    continue
                text = str(net)
                if text not in out:
                    out.append(text)
    except Exception as e:
        _print(f"[agent] Netze nicht ermittelbar: {e}")
    return out[:8]


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
        # Nur senden, wenn die Verbindung steht UND die Anmeldung bestätigt
        # ist. Beides zusammen verhindert, dass ein frisch verbundener Agent
        # dem Server in die laufende Aufnahme anderer Agenten hineinfunkt.
        if sio.connected and _REGISTERED.is_set():
            try:
                # collect_metrics() blockiert kurz -> in einem Thread ausführen,
                # damit der Rest des Programms (z.B. eingehende Befehle) nicht wartet
                metrics = await loop.run_in_executor(None, collect_metrics)
                await sio.emit("heartbeat", {"id": DEVICE_ID, "metrics": metrics}, namespace="/agent")
            except Exception as e:
                _print(f"[agent] {_at('log_metrics_failed', err=e)}")
        # Kleiner Zufalls-Anteil im Takt: Ohne ihn treffen die Heartbeats
        # aller Agenten dauerhaft im selben Sekundentakt ein und erzeugen
        # regelmässige Lastspitzen statt gleichmässiger Grundlast.
        await asyncio.sleep(5 + random.uniform(0, 1.5))


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


# ======================================================================
# VPN-GEGENSTELLE  (WireGuard-kompatibler Tunnel, Client-Seite)
# ----------------------------------------------------------------------
# Wichtig zum Verständnis: Auf DIESEM Gerät läuft kein WireGuard. Nichts
# wird installiert, kein Treiber, kein TUN-Gerät, keine Administratorrechte.
#
# Der eigentliche WireGuard-Tunnel endet im Backend (dort in reinem Python,
# siehe backend/app/wireguard.py). Was hier ankommt, ist bereits ausgepackt:
# schlichte Anweisungen der Art "öffne eine TCP-Verbindung nach 192.168.1.5:445
# und schiebe diese Bytes hin und her". Das erledigt der Agent mit dem
# Standard-Modul 'socket' - mehr braucht es nicht.
#
# Aus Sicht des Zielrechners im Netz kommt die Verbindung damit von diesem
# Gerät. Genau das ist gewollt: Der Client ist der Brückenkopf ins Netz.
#
# Ereignisse vom Backend:
#   vpn-open   {tunnel, stream, host, port}  -> Verbindung aufbauen
#   vpn-data   {stream, data(base64)}        -> Bytes zum Ziel schicken
#   vpn-close  {stream}                      -> Verbindung schliessen
#   vpn-udp    {tunnel, host, port, sport, src, data}
#   vpn-ping   {tunnel, host, src, ident, seq}
#
# Antworten zurück: vpn-open-result, vpn-data, vpn-close, vpn-udp,
# vpn-ping-result.
# ======================================================================

# Wie viele Verbindungen darf ein Tunnel gleichzeitig offen halten? Die
# Grenze schützt das Gerät davor, dass ein Portscan durch den Tunnel
# tausende Sockets aufreisst.
VPN_MAX_STREAMS = int(os.getenv("AGENT_VPN_MAX_STREAMS", "128") or 128)
# Zeitlimit für den Verbindungsaufbau zum Ziel.
VPN_CONNECT_TIMEOUT = 8.0
# Nach so langer Untätigkeit wird eine UDP-Zuordnung wieder abgeräumt.
VPN_UDP_IDLE = 60.0

_vpn_streams: dict[str, dict] = {}          # stream-ID -> {sock, thread, tunnel}
_vpn_streams_lock = threading.Lock()
_vpn_udp: dict[tuple, dict] = {}            # (tunnel, host, port, sport) -> Zuordnung
_vpn_udp_lock = threading.Lock()


def _vpn_emit(event: str, payload: dict) -> None:
    """Ereignis ans Backend schicken - auch aus einem Hintergrund-Thread."""
    try:
        asyncio.run_coroutine_threadsafe(
            sio.emit(event, payload, namespace="/agent"), _AGENT_LOOP)
    except Exception:
        pass


# ----------------------------------------------------------------------
# TCP
# ----------------------------------------------------------------------

def _vpn_reader(stream_id: str, sock: socket.socket, tunnel: str) -> None:
    """
    Liest alles, was vom Ziel zurückkommt, und schickt es ans Backend.

    Läuft in einem eigenen Thread. Das ist hier der ehrlichere Weg als
    asyncio: die Sockets sind gewöhnliche, blockierende Sockets, und ein
    Thread pro Verbindung ist bei den überschaubaren Stückzahlen eines
    Tunnels unproblematisch - anders als hunderte Dauerverbindungen.
    """
    try:
        while True:
            try:
                chunk = sock.recv(32768)
            except (OSError, socket.timeout):
                break
            if not chunk:
                break
            _vpn_emit("vpn-data", {
                "tunnel": tunnel, "stream": stream_id,
                "data": base64.b64encode(chunk).decode(),
            })
    finally:
        _vpn_drop_stream(stream_id, notify=True)


def _vpn_drop_stream(stream_id: str, notify: bool) -> None:
    with _vpn_streams_lock:
        entry = _vpn_streams.pop(stream_id, None)
    if not entry:
        return
    try:
        entry["sock"].close()
    except Exception:
        pass
    if notify:
        _vpn_emit("vpn-close", {"tunnel": entry.get("tunnel", ""),
                                "stream": stream_id})


@sio.on("vpn-open", namespace="/agent")
async def on_vpn_open(payload):
    """Neue Verbindung durch den Tunnel aufbauen."""
    stream_id = payload.get("stream") or ""
    tunnel = payload.get("tunnel") or ""
    host = payload.get("host") or ""
    port = int(payload.get("port") or 0)
    if not stream_id or not host or not port:
        return

    with _vpn_streams_lock:
        too_many = len(_vpn_streams) >= VPN_MAX_STREAMS
    if too_many:
        _print(f"[vpn] Grenze von {VPN_MAX_STREAMS} Verbindungen erreicht - "
               f"{host}:{port} abgelehnt")
        await sio.emit("vpn-open-result",
                       {"tunnel": tunnel, "stream": stream_id, "ok": False,
                        "error": "zu viele offene Verbindungen"},
                       namespace="/agent")
        return

    def _connect():
        try:
            sock = socket.create_connection((host, port), VPN_CONNECT_TIMEOUT)
        except Exception as e:
            _vpn_emit("vpn-open-result", {"tunnel": tunnel, "stream": stream_id,
                                          "ok": False, "error": str(e)})
            return
        sock.settimeout(None)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        reader = threading.Thread(target=_vpn_reader,
                                  args=(stream_id, sock, tunnel), daemon=True)
        with _vpn_streams_lock:
            _vpn_streams[stream_id] = {"sock": sock, "thread": reader,
                                       "tunnel": tunnel}
        _vpn_emit("vpn-open-result", {"tunnel": tunnel, "stream": stream_id,
                                      "ok": True})
        reader.start()

    # Der Verbindungsaufbau blockiert bis zu VPN_CONNECT_TIMEOUT Sekunden -
    # das gehört in einen Thread, sonst steht der ganze Agent still.
    threading.Thread(target=_connect, daemon=True).start()


@sio.on("vpn-data", namespace="/agent")
async def on_vpn_data(payload):
    """Bytes aus dem Tunnel an das Ziel weiterreichen."""
    stream_id = payload.get("stream") or ""
    with _vpn_streams_lock:
        entry = _vpn_streams.get(stream_id)
    if not entry:
        return
    try:
        data = base64.b64decode(payload.get("data") or "")
    except Exception:
        return
    try:
        entry["sock"].sendall(data)
    except Exception:
        _vpn_drop_stream(stream_id, notify=True)


@sio.on("vpn-close", namespace="/agent")
async def on_vpn_close(payload):
    """Verbindung schliessen (Gegenstelle im Tunnel ist fertig)."""
    _vpn_drop_stream(payload.get("stream") or "", notify=False)


# ----------------------------------------------------------------------
# UDP
# ----------------------------------------------------------------------

def _vpn_udp_reader(key: tuple, sock: socket.socket, tunnel: str,
                    src: str, sport: int, host: str, port: int) -> None:
    """Wartet auf Antwort-Datagramme und schickt sie durch den Tunnel zurück."""
    try:
        while True:
            sock.settimeout(VPN_UDP_IDLE)
            try:
                data, _addr = sock.recvfrom(65535)
            except (OSError, socket.timeout):
                break
            if not data:
                break
            _vpn_emit("vpn-udp", {
                "tunnel": tunnel,
                # 'host/port' ist hier der Absender im Netz des Clients,
                # 'dst/dport' der Benutzer im Tunnel.
                "host": host, "port": port,
                "dst": src, "dport": sport,
                "data": base64.b64encode(data).decode(),
            })
    finally:
        with _vpn_udp_lock:
            entry = _vpn_udp.pop(key, None)
        if entry:
            try:
                entry["sock"].close()
            except Exception:
                pass


@sio.on("vpn-udp", namespace="/agent")
async def on_vpn_udp(payload):
    """Ein UDP-Datagramm aus dem Tunnel verschicken (z.B. DNS)."""
    tunnel = payload.get("tunnel") or ""
    host = payload.get("host") or ""
    port = int(payload.get("port") or 0)
    src = payload.get("src") or ""
    sport = int(payload.get("sport") or 0)
    if not host or not port:
        return
    try:
        data = base64.b64decode(payload.get("data") or "")
    except Exception:
        return

    key = (tunnel, host, port, sport)
    with _vpn_udp_lock:
        entry = _vpn_udp.get(key)
        if entry is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except Exception as e:
                _print(f"[vpn] UDP-Socket fehlgeschlagen: {e}")
                return
            entry = {"sock": sock}
            _vpn_udp[key] = entry
            threading.Thread(target=_vpn_udp_reader,
                             args=(key, sock, tunnel, src, sport, host, port),
                             daemon=True).start()
    try:
        entry["sock"].sendto(data, (host, port))
    except Exception as e:
        _print(f"[vpn] UDP an {host}:{port} fehlgeschlagen: {e}")


# ----------------------------------------------------------------------
# ICMP (ping)
# ----------------------------------------------------------------------

@sio.on("vpn-ping", namespace="/agent")
async def on_vpn_ping(payload):
    """
    Erreichbarkeitsprüfung durch den Tunnel.

    Echte ICMP-Pakete zu bauen setzt einen Raw-Socket und damit Root- bzw.
    Administratorrechte voraus - die hat der Agent bewusst nicht überall.
    Deshalb wird das System-Werkzeug 'ping' benutzt; das Ergebnis (erreichbar
    ja/nein) reicht für die Antwort, die das Backend im Tunnel erzeugt.
    """
    tunnel = payload.get("tunnel") or ""
    host = payload.get("host") or ""
    if not host:
        return

    def _run():
        try:
            if IS_WINDOWS:
                cmd = ["ping", "-n", "1", "-w", "1500", host]
            else:
                cmd = ["ping", "-c", "1", "-W", "2", host]
            res = subprocess.run(cmd, capture_output=True, timeout=6,
                                 **_no_window_kwargs())
            ok = res.returncode == 0
        except Exception:
            ok = False
        _vpn_emit("vpn-ping-result", {
            "tunnel": tunnel, "host": host, "src": payload.get("src") or "",
            "ident": payload.get("ident") or 0, "seq": payload.get("seq") or 0,
            "payload_len": payload.get("payload_len") or 32, "ok": ok,
        })

    threading.Thread(target=_run, daemon=True).start()


# ======================================================================
# NODE-STUFE  (Zusatzmodule, die NUR auf Nodes laufen)
# ----------------------------------------------------------------------
# Ein gewoehnlicher Client bleibt schlank: Metriken melden, Befehle
# ausfuehren. Eine NODE ist ein aufgewerteter Client, der zusaetzlich als
# Brueckenkopf ins Netz dient - eigener VPN-Endpunkt, Reverse Proxy und
# optional eine L2-Bruecke.
#
# Diese Faehigkeiten kommen NICHT mit dem Agenten mit. Sie werden erst
# nachgeladen, wenn das Backend dieses Geraet ausdruecklich zur Node
# erklaert hat ('node-enable'). Vorher liegt hier keine einzige Zeile
# davon auf der Platte. Wird die Node zurueckgestuft ('node-disable'),
# werden die Dateien wieder entfernt.
#
# Warum nachladen statt alles in agent.py?
#   * Ein Client, der nie Node wird, soll den Code gar nicht erst haben.
#   * Module lassen sich einzeln aktualisieren, ohne den ganzen Agenten
#     auszutauschen - die Pruefsumme entscheidet, nicht eine Versionsnummer.
# ======================================================================

NODE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "node_modules")

# Zustand der Node zur Laufzeit. 'active' bleibt False, solange das Backend
# dieses Geraet nicht aufgewertet hat.
_node = {
    "active": False,
    "wg": None,           # node_wg.NodeWireGuard - echtes WireGuard
    "wg_mod": None,       # das Modul selbst (fuer probe/install)
    "relay": None,        # node_relay.NodeRelay - Erreichbarkeit hinter NAT
    "relay_mod": None,
    "proxy": None,        # Modul node_proxy
    "modules": {},        # Name -> Pruefsumme
    "probe": None,        # {host, port, token}
}


def _node_pkg():
    """
    Legt den Modulordner an und macht ihn zu einem importierbaren Paket.

    Die Node-Module importieren einander relativ ('from . import ...'),
    deshalb braucht es eine __init__.py und einen Eintrag im Suchpfad.
    """
    os.makedirs(NODE_DIR, exist_ok=True)
    init = os.path.join(NODE_DIR, "__init__.py")
    if not os.path.isfile(init):
        with open(init, "w", encoding="utf-8") as f:
            f.write("# Von RAPALLE.net RMM angelegt - Node-Zusatzmodule.\n")
    parent = os.path.dirname(NODE_DIR)
    if parent not in sys.path:
        sys.path.insert(0, parent)


def _node_module_hash(name: str) -> str:
    path = os.path.join(NODE_DIR, name)
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def _node_download(url: str) -> bytes:
    """Laedt eine Datei vom Backend - mit Agent-Token, ohne Fremdpakete."""
    import urllib.request
    req = urllib.request.Request(url, headers={"X-Agent-Token": AGENT_TOKEN})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _node_sync_modules(manifest: list) -> bool:
    """
    Holt fehlende oder veraltete Module. Gibt True zurueck, wenn danach
    ALLE Module vorhanden sind.

    Verglichen wird die Pruefsumme, nicht ein Datum: Nur so faellt auch
    eine Aenderung auf, bei der jemand vergessen hat, eine Nummer
    hochzuzaehlen.
    """
    _node_pkg()
    ok = True
    for entry in manifest or []:
        name = entry.get("name") or ""
        want = entry.get("hash") or ""
        if not name.endswith(".py") or "/" in name or "\\" in name:
            continue   # nur einfache Dateinamen - nie ein Pfad aus dem Netz
        if _node_module_hash(name) == want and want:
            continue
        url = f"{BACKEND_URL.rstrip('/')}/api/nodes/modules/{name}"
        try:
            data = _node_download(url)
            with open(os.path.join(NODE_DIR, name), "wb") as f:
                f.write(data)
            _print(f"[node] Modul {name} geladen")
        except Exception as e:
            _print(f"[node] Modul {name} konnte nicht geladen werden: {e}")
            ok = False
            continue
        _node["modules"][name] = _node_module_hash(name)
    return ok


@sio.on("node-enable", namespace="/agent")
async def on_node_enable(payload):
    """
    Dieses Geraet wurde zur Node aufgewertet (oder meldet sich neu an).

    Ablauf: Module holen -> WireGuard-Zustand pruefen -> Bericht ans
    Backend. Installiert oder gestartet wird hier NICHTS von selbst: Das
    passiert nur auf ausdrueckliche Anweisung aus dem Dashboard, denn
    dabei kommt ein Netzwerktreiber ins System des Kunden.
    """
    payload = payload or {}
    if not _node_sync_modules(payload.get("modules")):
        _print("[node] Nicht alle Module verfuegbar - Node bleibt inaktiv")
        await _node_report(error="Module unvollstaendig")
        return

    try:
        import importlib
        pkg = os.path.basename(NODE_DIR)
        node_wg = importlib.import_module(f"{pkg}.node_wg")
        node_relay = importlib.import_module(f"{pkg}.node_relay")
        node_proxy = importlib.import_module(f"{pkg}.node_proxy")
        for m in (node_wg, node_relay, node_proxy):
            importlib.reload(m)
    except Exception as e:
        _print(f"[node] Module nicht ladbar: {e}")
        await _node_report(error=f"Module nicht ladbar: {e}")
        return

    _node["wg_mod"] = node_wg
    _node["relay_mod"] = node_relay
    _node["proxy"] = node_proxy
    _node["probe"] = payload.get("probe") or {}
    _node["active"] = True

    # Laeuft schon eine Schnittstelle? Dann weiterbenutzen statt neu
    # aufzubauen - ein Neustart wuerde bestehende Tunnel abreissen.
    if _node.get("wg") is None:
        _node["wg"] = node_wg.NodeWireGuard(log=_print)

    # Wenn das Backend Schluessel und Port mitschickt UND WireGuard bereits
    # installiert ist, die Schnittstelle direkt hochbringen.
    if payload.get("private_key") and node_wg.probe().get("installed"):
        res = _node["wg"].start(payload["private_key"],
                                int(payload.get("wg_port") or 51822),
                                payload.get("address") or "",
                                payload.get("routes") or [])
        if not res.ok:
            _print(f"[node] WireGuard nicht gestartet: {res.reason}")

    _node_start_relay(payload)
    await _node_report()
    _print("[node] Node-Betrieb aktiv")


def _node_start_relay(payload) -> None:
    """Erreichbarkeit herstellen: Probe zum Backend, Relay bei Bedarf."""
    relay_mod = _node.get("relay_mod")
    if not relay_mod:
        return
    probe = payload.get("probe") or _node.get("probe") or {}
    wg = _node.get("wg")
    try:
        if _node.get("relay"):
            _node["relay"].stop()
        _node["relay"] = relay_mod.NodeRelay(
            backend_host=probe.get("host") or "",
            relay_port=int(probe.get("relay_port") or 51821),
            wg_port=int(payload.get("wg_port") or (wg.listen_port if wg else 51822)),
            token=probe.get("token") or "",
            send_via_agent=_relay_via_agent,
            log=_print)
        _node["relay"].start()
    except Exception as e:
        _print(f"[node] Relay nicht startbar: {e}")


def _relay_via_agent(token: str, data: bytes) -> None:
    """Ein WireGuard-Paket ueber die Socket.IO-Verbindung zum Backend."""
    _vpn_emit("wg-relay", {"token": token,
                           "data": base64.b64encode(data).decode()})


@sio.on("wg-relay", namespace="/agent")
async def on_wg_relay_in(payload):
    """Ein WireGuard-Paket kam ueber die Agent-Verbindung vom Backend."""
    relay = _node.get("relay")
    if not relay:
        return
    try:
        relay.inject(base64.b64decode((payload or {}).get("data") or ""))
    except Exception as e:
        _print(f"[node] Relay-Paket verworfen: {e}")


@sio.on("node-wg", namespace="/agent")
async def on_node_wg(payload):
    """
    Einrichten, starten oder pruefen - immer auf ausdrueckliche Anweisung.

    'install' ist der einzige Weg, auf dem ein Treiber ins System kommt.
    Ohne diesen Aufruf passiert das nie.
    """
    payload = payload or {}
    request_id = payload.get("requestId")
    action = payload.get("action") or "probe"

    def reply(data: dict):
        data["requestId"] = request_id
        asyncio.run_coroutine_threadsafe(
            sio.emit("node-wg-result", data, namespace="/agent"), _AGENT_LOOP)

    node_wg = _node.get("wg_mod")
    if not node_wg:
        reply({"ok": False, "reason": "Node-Module sind nicht geladen"})
        return

    def run():
        try:
            if action == "probe":
                reply({"ok": True, "reason": "geprueft", **node_wg.probe()})
            elif action == "install":
                _print("[node] Installation wurde im Dashboard bestaetigt")
                reply(node_wg.install(_node_driver_download, log=_print).as_dict())
            elif action == "start":
                if _node.get("wg") is None:
                    _node["wg"] = node_wg.NodeWireGuard(log=_print)
                res = _node["wg"].start(payload.get("private_key") or "",
                                        int(payload.get("wg_port") or 51822),
                                        payload.get("address") or "",
                                        payload.get("routes") or [])
                if res.ok:
                    _node_start_relay(payload)
                reply(res.as_dict())
            elif action == "stop":
                if _node.get("wg"):
                    _node["wg"].stop()
                if _node.get("relay"):
                    _node["relay"].stop()
                reply({"ok": True, "reason": "beendet"})
            else:
                reply({"ok": False, "reason": f"Unbekannte Aktion: {action}"})
        except Exception as e:
            reply({"ok": False, "reason": f"{type(e).__name__}: {e}"})

    threading.Thread(target=run, daemon=True).start()


@sio.on("node-wg-peer", namespace="/agent")
async def on_node_wg_peer(payload):
    """Das Backend traegt eine Gegenstelle ein oder entfernt sie."""
    payload = payload or {}
    wg = _node.get("wg")
    if not wg or not wg.up:
        _print("[node] Gegenstelle abgelehnt - WireGuard laeuft hier nicht")
        return
    try:
        if payload.get("remove"):
            wg.remove_peer(payload.get("public_key") or "")
        else:
            wg.add_peer(payload.get("public_key") or "",
                        payload.get("preshared_key"),
                        payload.get("allowed_ips") or "")
            routes = payload.get("routes") or []
            if payload.get("mode") == "site" and routes:
                # Site-to-Site: Der Verkehr bekommt die Adresse dieser Node
                # als Absender. Eine echte Adresse im fremden Netz waere
                # aufwendiger (ARP auf Ethernet-Ebene) und ist bewusst nicht
                # umgesetzt.
                wg._enable_masquerade([r for r in routes if "/" in r])
    except Exception as e:
        _print(f"[node] Gegenstelle nicht uebernommen: {e}")
    await _node_report()


@sio.on("node-net-packet", namespace="/agent")
async def on_node_net_packet(payload):
    """
    Ein Paket aus dem virtuellen Netz an dieses Geraet.

    Im virtuellen Netz ist das Backend die Gegenstelle; das Geraet selbst
    braucht dafuer kein WireGuard. Das Paket wird hier lokal zugestellt.
    """
    relay = _node.get("relay")
    if relay:
        try:
            relay.deliver_local(base64.b64decode((payload or {}).get("data") or ""))
        except Exception as e:
            _print(f"[node] Netzpaket verworfen: {e}")


@sio.on("node-disable", namespace="/agent")
async def on_node_disable(payload=None):
    """Zurueckgestuft: alles beenden und die Zusatzmodule entfernen."""
    for key, stopper in (("wg", "stop"), ("relay", "stop")):
        obj = _node.get(key)
        if obj:
            try:
                getattr(obj, stopper)()
            except Exception:
                pass
    _node.update({"active": False, "wg": None, "relay": None, "proxy": None,
                  "wg_mod": None, "relay_mod": None, "modules": {},
                  "probe": None})
    try:
        shutil.rmtree(NODE_DIR, ignore_errors=True)
        _print("[node] Zusatzmodule entfernt - wieder ein gewoehnlicher Client")
    except Exception as e:
        _print(f"[node] Aufraeumen fehlgeschlagen: {e}")


@sio.on("node-proxy", namespace="/agent")
async def on_node_proxy(payload):
    """Reverse Proxy: eine Seite aus dem Netz dieser Node holen."""
    request_id = (payload or {}).get("requestId")
    proxy = _node.get("proxy")
    if not proxy:
        await sio.emit("node-proxy-result",
                       {"requestId": request_id, "ok": False,
                        "error": "Reverse Proxy ist auf dieser Node nicht aktiv"},
                       namespace="/agent")
        return

    def done(result):
        result["requestId"] = request_id
        asyncio.run_coroutine_threadsafe(
            sio.emit("node-proxy-result", result, namespace="/agent"),
            _AGENT_LOOP)

    proxy.fetch_async(payload, done, log=_print)


def _node_driver_download(url: str) -> str:
    """Laedt eine Installationsdatei herunter und gibt den lokalen Pfad zurueck."""
    import urllib.request
    target = os.path.join(tempfile.gettempdir(), os.path.basename(url))
    with urllib.request.urlopen(url, timeout=600) as resp, \
            open(target, "wb") as f:
        shutil.copyfileobj(resp, f)
    return target


async def _node_report(error: str = "") -> None:
    """Meldet dem Backend, wie es dieser Node geht."""
    wg = _node.get("wg")
    relay = _node.get("relay")
    node_wg = _node.get("wg_mod")
    try:
        status = wg.status() if wg else {}
        await sio.emit("node-wg-state", {
            "id": DEVICE_ID,
            "modules": _node.get("modules") or {},
            "installed": bool(node_wg and node_wg.probe().get("installed")),
            "admin": bool(node_wg and node_wg.probe().get("admin")),
            "wg": status,
            "peers": status.get("peers", []),
            "public_endpoint": relay.public_endpoint if relay else "",
            "relay": relay.stats() if relay else {},
            "local_ips": [get_local_ip() or ""],
            "subnets": get_local_subnets(),
            "error": error,
        }, namespace="/agent")
    except Exception as e:
        _print(f"[node] Bericht fehlgeschlagen: {e}")


async def node_keepalive_loop():
    """
    Haelt die NAT-Zuordnung offen und meldet regelmaessig den Zustand.

    Ohne die wiederholte Probe schliesst das NAT der Node ihren UDP-Port
    nach ein bis zwei Minuten wieder - und der Endpunkt in der .conf eines
    Benutzers zeigt ins Leere.
    """
    while True:
        await asyncio.sleep(45)
        if not _node.get("active") or not sio.connected:
            continue
        try:
            _node_send_probe()
            await _node_report()
        except Exception as e:
            _print(f"[node] Keepalive: {e}")


# ======================================================================
# WARTUNGSMODUS / DIAGNOSE (Agent-Seite)
# ----------------------------------------------------------------------
# Wenn das Backend den Wartungsmodus einschaltet, schreibt der Agent alles
# mit und liefert es regelmaessig ab. Damit liegen Backend- und
# Agent-Ausgabe in EINER Zeitachse - bei einem Absturz kurz nach dem
# Verbindungsaufbau ist genau das die entscheidende Frage: was ist auf
# beiden Seiten in derselben Sekunde passiert?
#
# Mitgeschrieben wird alles, was sonst still verschwindet:
#   * jede Zeile ueber log/_print (haengt bereits am Root-Logger)
#   * Ausnahmen in Threads (threading.excepthook)
#   * Ausnahmen in asyncio-Aufgaben (Loop-Exception-Handler)
#   * regelmaessige Messwerte (Speicher, Threads, Dateideskriptoren)
#
# Der Puffer laeuft IMMER mit, auch ohne Wartungsmodus. Nur das Hochladen
# haengt am Schalter. So sind beim Einschalten die letzten Minuten sofort
# dabei, statt erst ab dem Moment der Aktivierung.
# ======================================================================

_DIAG = {
    "enabled": False,
    "queue": _collections.deque(maxlen=4000),   # noch nicht abgeliefert
    "lock": threading.Lock(),
}


def _diag_add(line: str, level: str = "INFO") -> None:
    """Eine Zeile in die Abliefer-Warteschlange. Darf nie werfen."""
    try:
        stamp = time.strftime("%H:%M:%S")
        with _DIAG["lock"]:
            _DIAG["queue"].append(f"{stamp} [{level}] {line}")
    except Exception:
        pass


class _DiagHandler(logging.Handler):
    """Haengt am Root-Logger und faengt damit alles ab, was _print schreibt."""

    def emit(self, record):
        try:
            _diag_add(record.getMessage(), record.levelname)
            if record.exc_info:
                import traceback as _tb
                _diag_add("".join(_tb.format_exception(*record.exc_info)), "ERROR")
        except Exception:
            pass


def _diag_thread_hook(args):
    import traceback as _tb
    text = "".join(_tb.format_exception(args.exc_type, args.exc_value,
                                        args.exc_traceback))
    name = args.thread.name if args.thread else "?"
    _print(f"[diag] Unbehandelte Ausnahme im Thread {name}:\n{text}")


def _diag_loop_handler(loop, context):
    """
    Ausnahmen aus asyncio-Aufgaben.

    Ohne diesen Haken meldet Python nur irgendwann spaeter "Task exception
    was never retrieved" - zu einem Zeitpunkt, der mit der Ursache nichts
    mehr zu tun hat.
    """
    exc = context.get("exception")
    msg = context.get("message") or "asyncio-Fehler"
    if exc:
        import traceback as _tb
        detail = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
    else:
        detail = str(context)
    _print(f"[diag] {msg}\n{detail}")


def _diag_install() -> None:
    """Einmalig beim Start. Laeuft unabhaengig vom Schalter."""
    try:
        logging.getLogger().addHandler(_DiagHandler())
        threading.excepthook = _diag_thread_hook
        _AGENT_LOOP.set_exception_handler(_diag_loop_handler)
    except Exception as e:
        print(f"[diag] Einhaengen fehlgeschlagen: {e}")
    _start_loop_watchdog()


# ----------------------------------------------------------------------
# Waechter fuer die Ereignisschleife des Agenten
# ----------------------------------------------------------------------
# Dasselbe Prinzip wie im Backend, aus demselben Grund: Ein Agent, dessen
# Schleife haengt, ist nicht abgestuerzt - er lebt, meldet aber nichts mehr
# und reagiert auf keinen Befehl. Im Dashboard steht er als "online" und
# tut trotzdem nichts. Das ist die aergerlichste aller Stoerungen, weil sie
# nach einem Fehler des Servers aussieht.
#
# Ein Thread misst von aussen. Steht die Schleife zu lange, beendet sich der
# Agent - der Dienst bzw. die Neustart-Logik in main() faengt ihn auf, und
# nach Sekunden ist er zurueck.

_AGENT_BEAT = {"t": 0.0, "worst": 0.0}
# Ab hier gilt der Agent als haengend und startet neu.
AGENT_LOOP_KILL_S = float(os.getenv("AGENT_LOOP_KILL_S", "120") or 120)


async def loop_heartbeat_agent():
    """Setzt zweimal pro Sekunde einen Zeitstempel."""
    while True:
        _AGENT_BEAT["t"] = time.monotonic()
        await asyncio.sleep(0.5)


def _agent_watchdog_thread() -> None:
    warned = 0.0
    while True:
        time.sleep(1.0)
        last = _AGENT_BEAT["t"]
        if not last:
            continue
        lag = time.monotonic() - last
        if lag > _AGENT_BEAT["worst"]:
            _AGENT_BEAT["worst"] = lag
        if lag < 5.0:
            continue
        if time.monotonic() - warned > 30:
            warned = time.monotonic()
            _print(f"[diag] Ereignisschleife blockiert seit {lag:.0f}s")
            try:
                import traceback as _tb
                for tid, frame in sys._current_frames().items():
                    _print(f"[diag] Thread {tid}:\n"
                           + "".join(_tb.format_stack(frame))[:2000])
            except Exception:
                pass
        if AGENT_LOOP_KILL_S > 0 and lag >= AGENT_LOOP_KILL_S:
            _print(f"[diag] NOTBREMSE: Schleife steht seit {lag:.0f}s - "
                   f"der Agent startet sich neu.")
            try:
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "last_crash.txt")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S")
                            + f"\nNotbremse: Ereignisschleife stand {lag:.0f}s\n")
            except OSError:
                pass
            # Neu starten statt nur beenden: Ein beendeter Agent kaeme ohne
            # Dienstverwaltung nicht von allein zurueck.
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception:
                os._exit(75)


def _start_loop_watchdog() -> None:
    threading.Thread(target=_agent_watchdog_thread, name="loop-watchdog",
                     daemon=True).start()


def _diag_sample() -> str:
    """Ein Messpunkt - billig genug fuer alle 30 Sekunden."""
    parts = []
    try:
        p = psutil.Process()
        with p.oneshot():
            parts.append(f"rss={p.memory_info().rss // 1048576}MB")
            parts.append(f"threads={p.num_threads()}")
            try:
                parts.append(f"fds={p.num_fds()}")
            except Exception:
                parts.append(f"handles={getattr(p, 'num_handles', lambda: 0)()}")
            parts.append(f"conns={len(p.net_connections(kind='inet'))}")
    except Exception:
        pass
    try:
        parts.append(f"tasks={len(asyncio.all_tasks(_AGENT_LOOP))}")
    except Exception:
        pass
    parts.append(f"connected={sio.connected}")
    parts.append(f"registered={_REGISTERED.is_set()}")
    return "MESSWERT " + " ".join(parts)


@sio.on("diag-mode", namespace="/agent")
async def on_diag_mode(payload):
    """Das Backend schaltet den Wartungsmodus ein oder aus."""
    enabled = bool((payload or {}).get("enabled"))
    was = _DIAG["enabled"]
    _DIAG["enabled"] = enabled
    if enabled and not was:
        _print(f"[diag] Wartungsmodus EIN - Agent {AGENT_VERSION}, "
               f"PID {os.getpid()}, Python {sys.version.split()[0]}, "
               f"{platform.platform()}")
        _diag_report_last_crash()
    elif not enabled and was:
        _print("[diag] Wartungsmodus AUS")


def _diag_report_last_crash() -> None:
    """
    Meldet einen frueheren Absturz nach.

    Der Agent schreibt bei einem Absturz last_crash.txt und startet sich
    neu. Diese Datei ist oft das einzige, was von einem Absturz uebrig
    bleibt - sie gehoert ins Diagnosepaket.
    """
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "last_crash.txt")
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            _print("[diag] Letzter Absturz dieses Agenten:\n" + f.read()[:8000])
    except Exception as e:
        _print(f"[diag] last_crash.txt nicht lesbar: {e}")


async def diag_upload_loop():
    """
    Liefert die gesammelten Zeilen ab.

    In Haeppchen und mit Abstand: Ein Agent, der im Sekundentakt Logs
    schickt, waere selbst eine Last - und der Wartungsmodus soll das
    Problem finden, nicht vergroessern.
    """
    while True:
        await asyncio.sleep(20)
        try:
            if not _DIAG["enabled"] or not sio.connected or not _REGISTERED.is_set():
                # Nicht verbunden: Zeilen bleiben in der Warteschlange und
                # gehen beim naechsten Mal mit. Genau die Zeilen aus einer
                # Verbindungsstoerung sind die interessanten.
                continue
            _diag_add(_diag_sample(), "DEBUG")
            with _DIAG["lock"]:
                lines = list(_DIAG["queue"])
                _DIAG["queue"].clear()
            if not lines:
                continue
            # In Blöcken senden, damit keine übergrosse Nachricht entsteht.
            for i in range(0, len(lines), 200):
                await sio.emit("diag-log", {"id": DEVICE_ID,
                                            "lines": lines[i:i + 200]},
                               namespace="/agent")
        except Exception as e:
            print(f"[diag] Hochladen fehlgeschlagen: {e}")


# ======================================================================
# SELBSTHEILUNG: Hintergrundschleifen, die sich nie verabschieden
# ----------------------------------------------------------------------
# Eine mit create_task() gestartete Schleife ist nach einer Ausnahme WEG -
# lautlos. Der Agent laeuft dann scheinbar weiter, meldet aber keine
# Metriken mehr oder reagiert nicht auf Befehle. Von aussen sieht das aus
# wie "der Agent ist abgestuerzt", obwohl der Prozess noch lebt.
#
# Diese Huelle faengt die Ausnahme, schreibt sie ins Log und startet die
# Schleife neu - mit wachsendem Abstand, damit eine dauerhaft kaputte
# Schleife nicht die CPU auslastet.
# ======================================================================

def _agent_supervise(name: str, factory):
    async def runner():
        delay = 2.0
        while True:
            started = time.monotonic()
            try:
                await factory()
                _print(f"[aufseher] '{name}' regulaer beendet")
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                import traceback as _tb
                _print(f"[aufseher] '{name}' abgestuerzt:\n{_tb.format_exc()}")
                # Lief die Schleife lange, war es vermutlich ein Einzelfall.
                # Stirbt sie sofort wieder, wird der Abstand groesser.
                delay = 2.0 if time.monotonic() - started > 120 else min(delay * 2, 300.0)
                _print(f"[aufseher] '{name}' startet in {delay:.0f}s neu")
                await asyncio.sleep(delay)

    return asyncio.create_task(runner(), name=f"supervised:{name}")


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


def _pip(*args):
    """pip im GLEICHEN Interpreter aufrufen, ohne Konsolenfenster."""
    kw = {}
    if platform.system() == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        kw = {"startupinfo": si,
              "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    try:
        return subprocess.run([sys.executable, "-m", "pip", *args],
                              capture_output=True, text=True, timeout=600, **kw)
    except Exception as e:
        _print(f"[repair] pip {' '.join(args)} fehlgeschlagen: {e}")
        return None


_screen_repair_tried = False


def _repair_screen() -> bool:
    """
    Einmaliger Versuch, mss/Pillow (und pynput) wieder lauffaehig zu machen.

    Der haeufigste Fall ist nicht "Paket fehlt", sondern "Paket ist kaputt":
    ein abgebrochener Download, eine halbe Installation oder ein Rest, den pip
    fuer vorhanden haelt. Ein schlichtes 'pip install' meldet dann
    "already satisfied" und aendert nichts - deshalb --force-reinstall.
    """
    global _screen_repair_tried, _SCREEN_AVAILABLE, _INPUT_AVAILABLE
    global _SCREEN_ERROR, _INPUT_ERROR, mss, Image
    global MouseController, MouseButton, KeyboardController, KeyboardKey
    if _screen_repair_tried:
        return _SCREEN_AVAILABLE
    _screen_repair_tried = True

    _print(f"[repair] Bildaufnahme-Pakete neu installieren (Grund: {_SCREEN_ERROR})")
    _pip("install", "--no-cache-dir", "--force-reinstall", "mss>=9.0.0", "Pillow>=10.0.0")
    if not _INPUT_AVAILABLE:
        _pip("install", "--no-cache-dir", "--force-reinstall", "pynput>=1.7.0")

    import importlib
    importlib.invalidate_caches()
    for name in [m for m in list(sys.modules)
                 if m in ("mss", "PIL", "pynput") or m.startswith(("mss.", "PIL.", "pynput."))]:
        sys.modules.pop(name, None)
    try:
        import mss as _mss
        from PIL import Image as _Image
        mss, Image = _mss, _Image
        _SCREEN_AVAILABLE = True
        _SCREEN_ERROR = ""
        _print("[repair] mss/Pillow erfolgreich nachinstalliert.")
    except Exception as e:
        _SCREEN_AVAILABLE = False
        _SCREEN_ERROR = f"{type(e).__name__}: {e}"
        _print(f"[repair] mss/Pillow still not loadable: {e}")
    try:
        from pynput.mouse import Controller as _MC, Button as _MB
        from pynput.keyboard import Controller as _KC, Key as _KK
        MouseController, MouseButton, KeyboardController, KeyboardKey = _MC, _MB, _KC, _KK
        _INPUT_AVAILABLE = True
        _INPUT_ERROR = ""
    except Exception as e:
        _INPUT_AVAILABLE = False
        _INPUT_ERROR = f"{type(e).__name__}: {e}"
    return _SCREEN_AVAILABLE


_winpty_repair_tried = False


def _winpty_diagnose() -> str:
    """
    Sammelt, was man braucht, um ein kaputtes pywinpty einzuordnen.

    Der typische Fehler 'No module named winpty._winpty' bedeutet fast immer:
    Es liegt ein Paket namens 'winpty' im Suchpfad, dessen kompilierter Teil
    (_winpty.pyd) fehlt - entweder ein Rest einer alten pywinpty-1.x-Version
    oder das alte, andere PyPI-Paket 'winpty'. Ein 'pip install pywinpty'
    aendert daran nichts, weil pip das vorhandene Verzeichnis als installiert
    ansieht bzw. der Rest weiter davorliegt.

    Zweiter haeufiger Fall: 'pip install' lief in einem ANDEREN Python als dem,
    mit dem der Agent laeuft. Deshalb steht der Interpreterpfad mit in der
    Ausgabe - damit sieht man den Unterschied sofort.
    """
    lines = [f"Python: {sys.executable}"]
    try:
        import importlib.util
        spec = importlib.util.find_spec("winpty")
        lines.append(f"winpty gefunden unter: {getattr(spec, 'origin', None)}")
    except Exception as e:
        lines.append(f"winpty nicht auffindbar: {e}")
    try:
        from importlib.metadata import version, distributions
        try:
            lines.append(f"pywinpty-Version: {version('pywinpty')}")
        except Exception:
            lines.append("pywinpty: nicht als Paket registriert")
        # Das ALTE, gleichnamige Paket 'winpty' ist die haeufigste Ursache.
        for d in distributions():
            if (d.metadata.get("Name") or "").lower() == "winpty":
                lines.append(f"ACHTUNG: altes Paket 'winpty' {d.version} installiert "
                             f"- das kollidiert mit pywinpty")
    except Exception:
        pass
    return "\n".join(lines)


def _winpty_repair() -> bool:
    """
    Einmaliger Reparaturversuch: kollidierendes 'winpty' entfernen, Reste
    loeschen und pywinpty sauber neu installieren. Gibt True zurueck, wenn
    der Import danach klappt.
    """
    global _winpty_repair_tried
    if _winpty_repair_tried:
        return False
    _winpty_repair_tried = True

    pip = _pip
    _print("[term] Repariere pywinpty…")
    # 1) Beide Pakete sauber entfernen (das alte 'winpty' ist der Stoerenfried).
    pip("uninstall", "-y", "winpty")
    pip("uninstall", "-y", "pywinpty")
    # 2) Uebrig gebliebenes Verzeichnis loeschen - ohne das findet Python
    #    weiterhin ein 'winpty' ohne den kompilierten Teil.
    try:
        import site
        import shutil
        for base in set(site.getsitepackages() + [site.getusersitepackages()]):
            leftover = Path(base) / "winpty"
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
                _print(f"[term] Rest entfernt: {leftover}")
    except Exception as e:
        _print(f"[term] Aufraeumen uebersprungen: {e}")
    # 3) Neu installieren.
    pip("install", "--no-cache-dir", "--force-reinstall", "pywinpty>=2.0.0")

    try:
        import importlib
        importlib.invalidate_caches()
        for name in [m for m in list(sys.modules) if m == "winpty" or m.startswith("winpty.")]:
            sys.modules.pop(name, None)
        importlib.import_module("winpty")
        _print("[term] pywinpty erfolgreich repariert.")
        return True
    except Exception as e:
        _print(f"[term] Reparatur fehlgeschlagen: {e}")
        return False


class _WinPipeTerminal:
    """
    Ersatz-Terminal fuer Windows OHNE pywinpty.

    Statt eines echten ConPTY laeuft die Shell an normalen Pipes. Das ist
    bewusst ein Kompromiss, aber ein brauchbarer: Befehle, Ausgabe und
    Eingabe funktionieren. Was NICHT geht, sind Vollbild-Anwendungen
    (nano-artige Editoren, Fortschrittsbalken mit Cursorsteuerung) und die
    Groessenanpassung - dafuer braucht es ein echtes PTY.

    Der Sinn: Lieber ein eingeschraenktes Terminal als gar keins, wenn
    pywinpty auf dem Rechner partout nicht laeuft.
    """

    LIMITED_NOTE = ("\x1b[33m[Eingeschränkter Modus: ohne pywinpty läuft die Shell "
                    "on plain pipes. Full-screen programs and resizing "
                    "do not work here.]\x1b[0m\r\n")

    def __init__(self, session_id, shell, cols, rows, loop):
        self.session_id = session_id
        self.loop = loop
        self.alive = True
        if shell == "powershell":
            cmd = ["powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-Command", "-"]
        else:
            cmd = ["cmd.exe", "/Q"]
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=0, startupinfo=si,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        self._emit(self.LIMITED_NOTE)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _emit(self, text):
        asyncio.run_coroutine_threadsafe(
            sio.emit("term-output", {"id": DEVICE_ID, "session": self.session_id,
                                     "data": text}, namespace="/agent"),
            self.loop,
        )

    def _read_loop(self):
        while self.alive:
            try:
                chunk = self.proc.stdout.read(1)
                if not chunk:
                    break
                # Alles nachlesen, was schon bereitliegt, damit die Ausgabe
                # nicht Zeichen fuer Zeichen durchs Netz tropft.
                try:
                    import msvcrt  # noqa: F401  (nur Windows)
                except Exception:
                    pass
                extra = self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1") else b""
                data = (chunk + (extra or b"")).decode("utf-8", errors="replace")
            except Exception:
                break
            self._emit(data.replace("\n", "\r\n") if "\r\n" not in data else data)
        self.alive = False
        asyncio.run_coroutine_threadsafe(
            sio.emit("term-exit", {"id": DEVICE_ID, "session": self.session_id},
                     namespace="/agent"),
            self.loop,
        )

    def write(self, data: str):
        try:
            self.proc.stdin.write(data.replace("\r", "\r\n").encode("utf-8", "replace"))
            self.proc.stdin.flush()
        except Exception:
            pass

    def resize(self, cols, rows):
        pass          # ohne PTY nicht moeglich

    def close(self):
        self.alive = False
        try:
            self.proc.kill()
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
        # pywinpty fehlt oder ist kaputt. Erst einmal selbst reparieren -
        # das deckt den haeufigsten Fall ab (Reste einer alten Version bzw.
        # das kollidierende Paket 'winpty'). Klappt das nicht, laeuft die
        # Shell im eingeschraenkten Pipe-Modus weiter, statt gar nicht.
        diag = _winpty_diagnose()
        _print(f"[term] pywinpty-Import fehlgeschlagen: {e}\n{diag}")
        term = None
        if _winpty_repair():
            try:
                term = _WinTerminal(session_id, shell, cols, rows, loop)
                await sio.emit("term-output", {
                    "id": DEVICE_ID, "session": session_id,
                    "data": "\x1b[32m[pywinpty wurde repariert.]\x1b[0m\r\n",
                }, namespace="/agent")
            except Exception as e2:
                _print(f"[term] still no ConPTY after repair: {e2}")
                term = None
        if term is None:
            await sio.emit("term-output", {
                "id": DEVICE_ID, "session": session_id,
                "data": "\r\n\x1b[33mNo ConPTY available "
                        f"({e}).\x1b[0m\r\n"
                        + "".join(f"\x1b[90m{l}\x1b[0m\r\n" for l in diag.split("\n"))
                        + "\x1b[90mHinweis: Ein 'pip install pywinpty' hilft hier meist "
                          "is not enough - a leftover is on the search path. Manually: "
                          "pip uninstall -y winpty pywinpty && pip install pywinpty"
                          "\x1b[0m\r\n",
            }, namespace="/agent")
            try:
                term = _WinPipeTerminal(session_id, shell, cols, rows, loop)
            except Exception as e3:
                await sio.emit("term-output", {
                    "id": DEVICE_ID, "session": session_id,
                    "data": f"\r\n\x1b[31mAuch der Ersatz-Modus scheiterte: {e3}\x1b[0m\r\n",
                }, namespace="/agent")
                await sio.emit("term-exit", {"id": DEVICE_ID, "session": session_id},
                               namespace="/agent")
                return
        _terminals[session_id] = term
    except Exception as e:
        import traceback
        _print(f"[term] shell start failed: {e}\n{traceback.format_exc()}")
        await sio.emit("term-output", {
            "id": DEVICE_ID, "session": session_id,
            "data": f"\r\n\x1b[31mTerminal error: {e}\x1b[0m\r\n",
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
# Agent-Konsole: Dashboard liest den Log dieses Agenten mit
# (Historie aus dem rollierenden Puffer + Live-Zeilen).
# --------------------------------------------------------------

@sio.on("agent-console-open", namespace="/agent")
async def on_agent_console_open(data=None):
    _console_stream["on"] = True
    history = "\r\n".join(_CONSOLE_BUFFER)
    if history:
        history += "\r\n"
    await sio.emit("agent-console-history",
                   {"id": DEVICE_ID, "data": history}, namespace="/agent")


@sio.on("agent-console-close", namespace="/agent")
async def on_agent_console_close(data=None):
    _console_stream["on"] = False


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
        entries = await loop.run_in_executor(None, _listdir_admin, req_path)
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
        result = await loop.run_in_executor(None, _read_file_admin, path)
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


# --------------------------------------------------------------
# Datei-Operationen mit ADMIN-RECHTEN (Web-Explorer + Explorer-Relay)
# --------------------------------------------------------------
# Läuft der Agent NICHT bereits als SYSTEM/root, schlagen Dateizugriffe auf
# geschützte Pfade mit "Zugriff verweigert" fehl (und der Relay-Client zeigt
# Fehler bzw. bricht die Verbindung ab). Deshalb gilt für ALLES, was über den
# Web-Explorer oder das Explorer-Relay läuft: Erst der normale (schnelle)
# Versuch - schlägt der mit Zugriff-verweigert fehl, wird die Operation
# automatisch ELEVIERT wiederholt:
#   Windows: Start-Process -Verb RunAs (läuft der Agent als Dienst/SYSTEM,
#            ist das transparent ohne UAC-Dialog)
#   Linux:   sudo -n (setzt eine NOPASSWD-Regel für den Agent-Benutzer voraus;
#            läuft der Agent als root, greift schon der Direktversuch)
# So werden die Operationen effektiv IMMER mit Admin-Rechten ausgeführt und
# ein "Datei ohne Rechte editieren" bringt den Relay nicht mehr zum Absturz.

def _is_access_denied(exc: Exception) -> bool:
    """Erkennt 'Zugriff verweigert'-Fehler plattformübergreifend."""
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "winerror", None) == 5:          # ERROR_ACCESS_DENIED
            return True
        import errno as _errno
        return exc.errno in (_errno.EACCES, _errno.EPERM)
    return False


# Eigenständiges Mini-Skript, das die Datei-Operation im ELEVIERTEN Prozess
# ausführt. Austausch über zwei JSON-Dateien (Request/Response), damit keine
# Kommandozeilen-Quoting-Probleme entstehen.
_FS_ELEV_SOURCE = r'''
import base64, json, os, shutil, sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    req = json.load(f)
op = req.get("op"); res = {}
try:
    if op == "list":
        out = []
        base = req["path"]
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            try:
                st = os.stat(full, follow_symlinks=False)
                is_dir = os.path.isdir(full)
                out.append({"name": name, "path": full, "isDir": is_dir,
                            "size": 0 if is_dir else int(st.st_size),
                            "mtime": int(st.st_mtime * 1000)})
            except Exception:
                out.append({"name": name, "path": full, "isDir": False,
                            "size": 0, "mtime": 0})
        res = {"entries": out}
    elif op == "read":
        with open(req["path"], "rb") as f:
            data = f.read()
        res = {"name": os.path.basename(req["path"]),
               "data": base64.b64encode(data).decode("ascii")}
    elif op == "write":
        content = base64.b64decode(req.get("data", ""))
        os.makedirs(os.path.dirname(req["path"]) or ".", exist_ok=True)
        with open(req["path"], "wb") as f:
            f.write(content)
        res = {"ok": True, "path": req["path"], "size": len(content)}
    elif op == "mkdir":
        os.makedirs(req["path"], exist_ok=False)
        res = {"ok": True, "path": req["path"]}
    elif op == "delete":
        p = req["path"]
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
        res = {"ok": True, "path": p}
    elif op == "rename":
        os.rename(req["src"], req["dst"])
        res = {"ok": True, "src": req["src"], "dst": req["dst"]}
    else:
        res = {"error": "Unbekannte Operation: %s" % op}
except Exception as e:
    res = {"error": str(e)}
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(res, f)
'''


def _fs_elevated(op: dict) -> dict:
    """Führt eine Datei-Operation ELEVIERT (Admin/root) aus. Blockierend -
    wird wie die Direkt-Varianten im Executor aufgerufen."""
    import json as _json_mod
    import shutil as _shutil
    tmpdir = tempfile.mkdtemp(prefix="rmm-fsadmin-")
    script = os.path.join(tmpdir, "fsop.py")
    req_f = os.path.join(tmpdir, "req.json")
    res_f = os.path.join(tmpdir, "res.json")
    try:
        with open(script, "w", encoding="utf-8") as f:
            f.write(_FS_ELEV_SOURCE)
        with open(req_f, "w", encoding="utf-8") as f:
            _json_mod.dump(op, f)

        if IS_WINDOWS:
            # Gleiches Muster wie _run_elevated_windows: Start-Process -Verb RunAs.
            inner_exec = f'"{sys.executable}" "{script}" "{req_f}" "{res_f}"'
            launch_script = (
                f"$p = Start-Process -FilePath cmd.exe -ArgumentList '/c',{_ps_single_quote(inner_exec)} "
                f"-Verb RunAs -WindowStyle Hidden -PassThru -Wait; exit $p.ExitCode"
            )
            encoded = base64.b64encode(launch_script.encode("utf-16-le")).decode("ascii")
            _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                  "-EncodedCommand", encoded],
                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        else:
            # Linux/macOS: sudo ohne Passwort-Abfrage (-n). Ohne passende
            # sudoers-Regel schlägt das kontrolliert fehl (kein Hänger).
            _run(["sudo", "-n", sys.executable, script, req_f, res_f],
                 capture_output=True, timeout=120)

        if not os.path.exists(res_f):
            raise PermissionError(
                "Access denied - running with admin rights is not possible "
                "(UAC declined or sudo without a NOPASSWD rule).")
        with open(res_f, "r", encoding="utf-8") as f:
            res = _json_mod.load(f)
        if res.get("error"):
            raise PermissionError(res["error"])
        return res
    finally:
        _shutil.rmtree(tmpdir, ignore_errors=True)


# ------------------------------------------------------------------
# Elevation-Bremse: Explorer-Relay-Browsing kann pro Ordner DUTZENDE
# "Zugriff verweigert"-Fälle auslösen (folder.jpg, Thumbs.db, System-
# Junctions, ...). Ohne Bremse würde für jeden einzelnen ein RunAs-
# PowerShell-Prozess gestartet (bis 120s Timeout) - das hat den Agent
# in der Vergangenheit destabilisiert (harter Prozess-Tod 0x80000003).
#   - _ELEV_LOCK:     nie mehr als EINE Elevation gleichzeitig
#   - _elev_failed:   pro Pfad 10min Sperre nach fehlgeschlagener Elevation
#   - _elev_last_fail: nach JEDEM Fehlschlag 60s globale Pause
#   - System-Junctions (Documents and Settings, $Recycle.Bin, ...) werden
#     bei "list" gar nicht erst eleviert - dort hilft Elevation ohnehin nicht.
# ------------------------------------------------------------------
_ELEV_LOCK = threading.Lock()
_elev_failed: dict[str, float] = {}
_elev_last_fail = 0.0
_ELEV_PATH_COOLDOWN = 600.0   # Sekunden pro Pfad
_ELEV_GLOBAL_COOLDOWN = 60.0  # Sekunden nach beliebigem Fehlschlag
_WIN_SYSTEM_JUNCTIONS = {
    "documents and settings", "$recycle.bin", "system volume information",
    "recovery", "config.msi", "dfsrprivate", "perflogs",
}


def _elev_key(op: dict) -> str:
    return f"{op.get('op')}::{op.get('path') or op.get('src') or ''}".lower()


def _fs_with_admin(direct, op: dict):
    """Direktversuch; bei 'Zugriff verweigert' automatisch eleviert wiederholen.
    Mit Drossel, damit Explorer-Relay-Browsing keinen Elevation-Sturm auslöst."""
    global _elev_last_fail
    try:
        return direct()
    except Exception as e:
        if not _is_access_denied(e):
            raise
        # Bekannte System-Junctions: Elevation bringt beim Auflisten nichts.
        if op.get("op") == "list":
            base = os.path.basename((op.get("path") or "").rstrip("\\/")).lower()
            if base in _WIN_SYSTEM_JUNCTIONS:
                raise
        now = time.time()
        key = _elev_key(op)
        ts = _elev_failed.get(key)
        if ts and now - ts < _ELEV_PATH_COOLDOWN:
            raise  # gleicher Pfad ist erst kürzlich gescheitert
        if now - _elev_last_fail < _ELEV_GLOBAL_COOLDOWN:
            raise  # globale Schonfrist nach letztem Fehlschlag
        _print(f"[agent] file operation '{op.get('op')}' without permission "
               f"({e}) -> wiederhole mit Admin-Rechten")
        with _ELEV_LOCK:
            try:
                res = _fs_elevated(op)
                _elev_failed.pop(key, None)
                return res
            except Exception:
                _elev_failed[key] = time.time()
                _elev_last_fail = time.time()
                raise


def _listdir_admin(path: str):
    # Leerer Pfad = Laufwerks-/Root-Übersicht, braucht nie Elevation.
    if not path:
        return _list_directory(path)
    res = _fs_with_admin(lambda: _list_directory(path), {"op": "list", "path": path})
    return res["entries"] if isinstance(res, dict) and "entries" in res else res


def _read_file_admin(path: str) -> dict:
    return _fs_with_admin(lambda: _read_file_b64(path), {"op": "read", "path": path})


def _write_file_admin(path: str, data_b64: str) -> dict:
    return _fs_with_admin(lambda: _write_file_b64(path, data_b64),
                          {"op": "write", "path": path, "data": data_b64})


def _mkdir_admin(path: str) -> dict:
    return _fs_with_admin(lambda: _make_dir(path), {"op": "mkdir", "path": path})


def _delete_admin(path: str) -> dict:
    return _fs_with_admin(lambda: _delete_path(path), {"op": "delete", "path": path})


def _rename_admin(src: str, dst: str) -> dict:
    return _fs_with_admin(lambda: _rename_path(src, dst),
                          {"op": "rename", "src": src, "dst": dst})


@sio.on("fs-write", namespace="/agent")
async def on_fs_write(data):
    """Datei hochladen oder eine editierte Datei zurückschreiben."""
    request_id = data.get("requestId")
    path = data.get("path", "")
    payload = data.get("data", "")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _write_file_admin, path, payload)
        await sio.emit("fs-op-result", {"requestId": request_id, **result}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-op-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


@sio.on("fs-mkdir", namespace="/agent")
async def on_fs_mkdir(data):
    request_id = data.get("requestId")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _mkdir_admin, data.get("path", ""))
        await sio.emit("fs-op-result", {"requestId": request_id, **result}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-op-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


@sio.on("fs-delete", namespace="/agent")
async def on_fs_delete(data):
    request_id = data.get("requestId")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _delete_admin, data.get("path", ""))
        await sio.emit("fs-op-result", {"requestId": request_id, **result}, namespace="/agent")
    except Exception as e:
        await sio.emit("fs-op-result", {"requestId": request_id, "error": str(e)}, namespace="/agent")


@sio.on("fs-rename", namespace="/agent")
async def on_fs_rename(data):
    request_id = data.get("requestId")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _rename_admin,
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
                  "mon_left": 0, "mon_top": 0,   # Offset des gewählten Bildschirms
                  # Verbindung zum Aufnahme-Helfer in der Benutzersitzung
                  # (nur Windows/Sitzung 0, sonst immer None).
                  "helper": None,
                  # Zaehler laufender Sitzungswuensche. Trifft waehrend einer
                  # offenen Zustimmungsabfrage ein "screen-stop" ein (Dashboard-
                  # Fenster geschlossen), wird der Zaehler erhoeht - die
                  # Abfrage weiss dann, dass niemand mehr zusieht, und startet
                  # KEINEN Stream mehr. Vorher lief der Stream sonst blind los.
                  "epoch": 0}


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
    Linux bei Bedarf DISPLAY/XAUTHORITY.

    Rueckgabe: (verfuegbar: bool, hinweis: str, code: str|None, params: dict)
    Der Hinweis ist die ENGLISCHE Rueckfallebene fuer Protokoll und aeltere
    Dashboards; `code`/`params` werden im Browser uebersetzt (siehe
    _notify_screen_error).
    """
    system = platform.system()
    # Windows/macOS haben praktisch immer einen erfassbaren Desktop.
    if system in ("Windows", "Darwin"):
        return True, "", None, {}

    # Linux: DISPLAY schon gesetzt? Dann direkt versuchen.
    if os.environ.get("DISPLAY"):
        return True, "", None, {}

    # 1) X-Server-Prozess finden (liefert DISPLAY + Xauthority)
    display, auth = _detect_from_xorg_process()
    if display:
        os.environ["DISPLAY"] = display
        if auth and os.path.isfile(auth):
            os.environ["XAUTHORITY"] = auth
        else:
            _find_xauthority_fallback()
        return True, f"Display {display} detected", "agent_display_found", {"display": display}

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
            _find_xauthority_fallback()
            return True, f"Display :{sorted(nums)[0]} detected", \
                   "agent_display_found", {"display": f":{sorted(nums)[0]}"}
    except Exception:
        pass

    # 3) Wayland-Sitzung? Dann klar sagen, WARUM keine Aufnahme möglich ist
    #    (mss kann Wayland nicht erfassen - X11-Sitzung nötig).
    try:
        run_dir = "/run/user"
        if os.path.isdir(run_dir):
            for uid in os.listdir(run_dir):
                udir = os.path.join(run_dir, uid)
                if any(e.startswith("wayland-") for e in os.listdir(udir)):
                    return (False,
                            "Wayland session detected - screen capture needs X11. "
                            "On Ubuntu: pick 'Ubuntu on Xorg' at the login screen (gear icon). "
                            "A shell will be opened instead.",
                            "agent_wayland", {})
    except Exception:
        pass

    # Kein grafischer Bildschirm -> Shell-only.
    return (False,
            "No graphical screen found (headless VM/server). "
            "A shell will be opened instead.",
            "agent_no_display", {})


def _find_xauthority_fallback():
    """
    Setzt XAUTHORITY, wenn der X-Server keinen '-auth'-Parameter verraten hat.
    Ohne das richtige Cookie verweigert der X-Server (z.B. Ubuntu Desktop mit
    GDM) dem als root laufenden Agenten sonst Bildaufnahme UND Tkinter-Dialog.
    Kandidaten: GDM-Xauthority unter /run/user/<uid>/, ~/.Xauthority der
    angemeldeten Benutzer, Mutter-XWayland-Auth.
    """
    if os.environ.get("XAUTHORITY") and os.path.isfile(os.environ["XAUTHORITY"]):
        return
    candidates = []
    try:
        run_dir = "/run/user"
        if os.path.isdir(run_dir):
            for uid in os.listdir(run_dir):
                udir = os.path.join(run_dir, uid)
                candidates.append(os.path.join(udir, "gdm", "Xauthority"))
                try:
                    for e in os.listdir(udir):
                        if e.startswith(".mutter-Xwaylandauth"):
                            candidates.append(os.path.join(udir, e))
                except OSError:
                    pass
    except Exception:
        pass
    try:
        import pwd
        for u in pwd.getpwall():
            if u.pw_uid >= 1000 and os.path.isdir(u.pw_dir):
                candidates.append(os.path.join(u.pw_dir, ".Xauthority"))
    except Exception:
        pass
    for c in candidates:
        try:
            if os.path.isfile(c):
                os.environ["XAUTHORITY"] = c
                _print(f"[agent] XAUTHORITY gesetzt: {c}")
                return
        except OSError:
            continue

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
        _print(f"[agent] {_at('log_remote_ctl', state=_at('log_disabled'))} (controller init failed: {e})")


# ======================================================================
# Bildschirmaufnahme aus Sitzung 0 (Windows-Dienst) - Helfer-Prozess
# ======================================================================
# Ausgangslage: Der Windows-Agent wird als geplante Aufgabe unter dem Konto
# SYSTEM eingerichtet und startet beim Hochfahren. Solche Prozesse laufen in
# "Sitzung 0" - einer eigenen, unsichtbaren Sitzung, die seit Windows Vista
# strikt vom Desktop des angemeldeten Benutzers getrennt ist. Eine Aufnahme
# aus Sitzung 0 sieht den Benutzer-Desktop nicht; je nach Bibliothek kommt ein
# schwarzes Bild oder ein Fehler ("BitBlt failed", "access denied").
#
# Das laesst sich nicht wegkonfigurieren - man braucht einen zweiten Prozess
# IN der Sitzung des Benutzers. Genau das passiert hier:
#
#   1. Der Agent (Sitzung 0) oeffnet einen Server auf 127.0.0.1 mit zufaelligem
#      Port und zufaelligem Schluessel.
#   2. Er startet sich selbst noch einmal - diesmal mit dem Token des
#      angemeldeten Benutzers, also in dessen Sitzung - mit den Argumenten
#      --screen-helper <port> <schluessel>.
#   3. Der Helfer verbindet sich zurueck, weist sich mit dem Schluessel aus und
#      schickt von da an JPEG-Bilder. Eingaben (Maus/Tastatur) gehen den
#      umgekehrten Weg, denn auch die funktionieren aus Sitzung 0 nicht.
#
# Der Server lauscht ausschliesslich auf 127.0.0.1 und akzeptiert genau eine
# Verbindung, die den Schluessel kennt - von aussen ist da nichts erreichbar.


# ----------------------------------------------------------------------
# Der Helfer als EIGENSTAENDIGES Miniprogramm
# ----------------------------------------------------------------------
# Erster Versuch war, agent.py einfach noch einmal mit --screen-helper zu
# starten. Das schlug fehl: Der Helfer laeuft als normaler BENUTZER, agent.py
# schreibt beim Import aber unter anderem seine Logdatei nach
# "C:\Program Files\RapalleRmmAgent\agent.log" - dort hat ein Benutzer kein
# Schreibrecht. Der Prozess startete (die PID stand im Log), starb dann aber
# beim Import, noch bevor er sich verbinden konnte. Genau das sah man als
# "Helfer hat sich nicht gemeldet: timed out".
#
# Deshalb wird der Helfer jetzt als kleines, unabhaengiges Skript neben
# agent.py geschrieben. Es importiert nur, was es wirklich braucht, schreibt
# sein eigenes Protokoll ins TEMP-Verzeichnis des Benutzers und kennt weder
# Backend noch Watchdog.
_HELPER_FILENAME = "_screen_helper.py"

_HELPER_SOURCE = r"""# Automatisch erzeugt von agent.py - nicht von Hand bearbeiten.
# Laeuft in der Sitzung des angemeldeten Benutzers und liefert dem Agenten
# (Sitzung 0) Bildschirminhalte, Eingaben und die Zustimmungsabfrage.
import io, json, os, socket, sys, tempfile, threading, time, traceback

LOG = os.path.join(tempfile.gettempdir(), "rmm-screen-helper.log")

def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass

# Auch hier senden ZWEI Threads auf denselben Socket: der Steuer-Thread
# (Antwort der Zustimmungsabfrage) und die Aufnahmeschleife (Bilder). Ohne
# Schloss ueberlappen die sendall() und zerstoeren die Laengen-Praefixe.
_SEND_LOCK = threading.Lock()

def send(sock, payload):
    with _SEND_LOCK:
        sock.sendall(len(payload).to_bytes(4, "big") + payload)

def recv(sock):
    def exact(n):
        buf = b""
        while len(buf) < n:
            c = sock.recv(n - len(buf))
            if not c:
                return None
            buf += c
        return buf
    head = exact(4)
    return exact(int.from_bytes(head, "big")) if head else None

LANG = "de"

# Nur die Texte, die die Person VOR dem Geraet sieht. ACHTUNG: In
# _HELPER_SOURCE darf kein Docstring mit dreifachen Anfuehrungszeichen stehen -
# das wuerde den r-String beenden und agent.py zerlegen.
TEXTS = {
    "de": {
        "window": "RAPALLE.net RMM - Remote-Bildschirm",
        "title": "Remote-Bildschirm zulassen?",
        "body": "{who} moechte den Bildschirm dieses Computers\nsehen und steuern.",
        "allow": "\u2713 Zulassen",
        "deny": "Ablehnen",
        "auto": "Automatisch abgelehnt in {secs} s",
        "somebody": "Ein Administrator",
    },
    "en": {
        "window": "RAPALLE.net RMM - Remote screen",
        "title": "Allow remote screen access?",
        "body": "{who} would like to view and control\nthe screen of this computer.",
        "allow": "\u2713 Allow",
        "deny": "Deny",
        "auto": "Automatically denied in {secs} s",
        "somebody": "An administrator",
    },
}


def ht(key, **kw):
    tbl = TEXTS.get(LANG) or TEXTS["de"]
    txt = tbl.get(key) or TEXTS["de"].get(key) or key
    return txt.format(**kw) if kw else txt


def consent_dialog(who, timeout_s):
    import tkinter as tk
    result = {"ok": False}
    root = tk.Tk()
    root.title(ht("window"))
    root.attributes("-topmost", True)
    root.configure(bg="#131c2b")
    root.resizable(False, False)

    # Logo: logo.png liegt seit Install/Update neben agent.py - also im
    # gleichen Ordner wie dieses Helfer-Skript. Es wird sowohl als Fenster-
    # symbol (iconphoto, kann PNG) als auch sichtbar im Dialog verwendet.
    # Ohne diesen Block war der Dialog des Helfers bildlos - der alte, direkt
    # im Agenten gezeichnete Dialog hatte das Logo noch.
    logo_big = logo_small = None
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.isfile(p):
            logo_big = tk.PhotoImage(file=p)
            f = max(1, logo_big.width() // 64)
            logo_small = logo_big.subsample(f, f)
            icons = []
            for target in (64, 32, 16):
                s = max(1, logo_big.width() // target)
                icons.append(logo_big.subsample(s, s))
            root._icon_refs = [logo_big, logo_small] + icons   # GC-Schutz
            try:
                root.iconphoto(True, *icons)
            except Exception:
                pass
    except Exception as e:
        log("logo not loadable: %s" % e)
        logo_big = logo_small = None

    if logo_small is not None:
        tk.Label(root, image=logo_small, bg="#131c2b").pack(pady=(20, 4))
    else:
        tk.Label(root, text="\U0001F5A5", font=("Segoe UI Emoji", 30),
                 bg="#131c2b", fg="#e8eef7").pack(pady=(20, 4))
    tk.Label(root, text=ht("title"), bg="#131c2b", fg="#e8eef7",
             font=("Segoe UI", 14, "bold")).pack(pady=(4, 8))
    tk.Label(root, text=ht("body", who=str(who)), bg="#131c2b", fg="#8fa3bd",
             font=("Segoe UI", 10), justify="center").pack(padx=28)
    countdown = tk.Label(root, text="", bg="#131c2b", fg="#8fa3bd", font=("Segoe UI", 9))
    countdown.pack(pady=(10, 0))
    row = tk.Frame(root, bg="#131c2b"); row.pack(pady=18)
    def yes():
        result["ok"] = True; root.destroy()
    def no():
        root.destroy()
    tk.Button(row, text=ht("deny"), command=no, width=14,
              bg="#243043", fg="#e8eef7", relief="flat").pack(side="left", padx=6)
    tk.Button(row, text=ht("allow"), command=yes, width=14,
              bg="#2f6fed", fg="#ffffff", relief="flat").pack(side="left", padx=6)
    left = {"n": int(timeout_s)}
    def tick():
        if left["n"] <= 0:
            try: root.destroy()
            except Exception: pass
            return
        countdown.config(text=ht("auto", secs=left["n"]))
        left["n"] -= 1
        root.after(1000, tick)
    tick()
    root.protocol("WM_DELETE_WINDOW", no)
    # Erst nach dem Aufbau zentrieren - vorher steht die Fenstergroesse noch
    # nicht fest und der Dialog landete oben links.
    try:
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 3
        root.geometry("+%d+%d" % (x, y))
        root.lift()
        root.focus_force()
    except Exception:
        pass
    root.mainloop()
    return result["ok"]

def main():
    port, key = int(sys.argv[1]), sys.argv[2]
    # Drittes Argument ist die Sprache (siehe AGENT_LANG im Agenten).
    # Faellt es weg (alter Agent startet neuen Helfer), bleibt Deutsch.
    global LANG
    LANG = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] in ("de", "en") else "de"
    log("Start (PID %s, Python %s)" % (os.getpid(), sys.executable))
    sock = socket.create_connection(("127.0.0.1", port), timeout=15)
    send(sock, json.dumps({"key": key, "pid": os.getpid()}).encode())
    log("connected to the agent on port %s" % port)

    state = {"quality": 60, "fps": 10, "monitor": 1,
             "left": 0, "top": 0, "stream": False, "stop": False}

    mouse = keyboard = None
    try:
        from pynput.mouse import Controller as MC, Button as MB
        from pynput.keyboard import Controller as KC, Key as KK
        mouse, keyboard = MC(), KC()
    except Exception as e:
        log("input not available: %s" % e)
        MB = KK = None

    # Tastennamen des Browsers -> pynput. MUSS zur Tabelle _SPECIAL_KEYS im
    # Agenten passen. Vorher stand hier nur getattr(Key, name): Das Dashboard
    # schickt aber Browser-Namen ("Enter", "Escape", "Control", "ArrowUp"),
    # pynput heisst die Dinger anders ("enter", "esc", "ctrl", "up"). Jeder
    # Treffer war None, danach versuchte pynput die ZEICHENKETTE zu druecken
    # und warf - die Tastatur-Fernsteuerung tat deshalb gar nichts.
    SPECIAL = {}
    if KK is not None:
        SPECIAL = {
            "Enter": KK.enter, "Backspace": KK.backspace, "Tab": KK.tab,
            "Escape": KK.esc, " ": KK.space,
            "ArrowUp": KK.up, "ArrowDown": KK.down,
            "ArrowLeft": KK.left, "ArrowRight": KK.right,
            "Delete": KK.delete, "Home": KK.home, "End": KK.end,
            "PageUp": KK.page_up, "PageDown": KK.page_down,
            "Control": KK.ctrl, "Alt": KK.alt, "Shift": KK.shift,
            "Meta": KK.cmd,
        }

    def to_key(name):
        # Browser-Name -> pynput-Taste. Einzelne Zeichen bleiben Zeichen.
        # ACHTUNG: Dieser Code steht in _HELPER_SOURCE, einem r-String mit
        # dreifachen Anfuehrungszeichen. Ein Docstring darin wuerde den String
        # beenden und agent.py zerlegen - deshalb nur Kommentare, keine
        # Docstrings.
        if not name:
            return None
        if name in SPECIAL:
            return SPECIAL[name]
        if len(name) == 1:
            return name
        # F1..F12 und alles andere, was pynput unter kleinem Namen kennt.
        return getattr(KK, name.lower(), None)

    def set_clipboard(text):
        # Zwischenablage der Benutzersitzung setzen (ohne Zusatzpaket).
        import tkinter as tk
        r = tk.Tk(); r.withdraw()
        r.clipboard_clear(); r.clipboard_append(text)
        r.update()          # erst dadurch uebernimmt Windows den Inhalt
        r.destroy()

    def get_clipboard():
        import tkinter as tk
        r = tk.Tk(); r.withdraw()
        try:
            return r.clipboard_get()
        finally:
            r.destroy()

    def do_input(d):
        k = d.get("type")
        # Tastatur/Zwischenablage brauchen keine Maus - deshalb VOR der
        # Maus-Pruefung behandeln.
        if k == "clipboard-set":
            try: set_clipboard(d.get("text", ""))
            except Exception as e: log("Zwischenablage setzen: %s" % e)
            return
        if k in ("key", "combo", "text") and keyboard is None:
            return
        if k == "text":
            keyboard.type(d.get("text", ""))
            return
        if k in ("key", "combo"):
            names = d.get("keys") or [d.get("key")]
            keys = [to_key(n) for n in names]
            keys = [x for x in keys if x is not None]
            for x in keys:
                keyboard.press(x)
            for x in reversed(keys):
                keyboard.release(x)
            return

        if mouse is None:
            return
        ox, oy = state["left"], state["top"]
        def pos(x):
            return (int(x["x"]) + ox, int(x["y"]) + oy)
        btn = {"left": "left", "right": "right", "middle": "middle"}.get(
            d.get("button", "left"), "left")
        b = getattr(MB, btn)
        if k == "move":
            mouse.position = pos(d)
        elif k == "click":
            mouse.position = pos(d); mouse.click(b, 1)
        elif k == "down":
            mouse.position = pos(d); mouse.press(b)
        elif k == "up":
            mouse.position = pos(d); mouse.release(b)
        elif k in ("double", "dblclick"):
            # Das Dashboard schickt "double" - der Helfer kannte bisher nur
            # "dblclick", ein Doppelklick kam also nie an.
            mouse.position = pos(d); mouse.click(MB.left, 2)
        elif k == "scroll":
            mouse.scroll(0, int(d.get("dy", 0)))

    def control():
        while not state["stop"]:
            try:
                msg = recv(sock)
            except Exception as e:
                log("Steuerkanal beendet: %s" % e); break
            if msg is None or msg[:1] != b"C":
                if msg is None:
                    break
                continue
            try:
                o = json.loads(msg[1:].decode())
            except Exception:
                continue
            c = o.get("cmd")
            if c == "config":
                state["quality"] = int(o.get("quality", state["quality"]))
                state["fps"] = int(o.get("fps", state["fps"]))
                state["monitor"] = int(o.get("monitor", state["monitor"]))
                state["stream"] = bool(o.get("stream", state["stream"]))
            elif c == "input":
                try: do_input(o.get("data") or {})
                except Exception as e: log("Eingabe: %s" % e)
            elif c == "consent":
                ok = False
                try:
                    ok = consent_dialog(o.get("who") or ht("somebody"),
                                        int(o.get("timeout", 30)))
                except Exception as e:
                    log("Zustimmungsdialog fehlgeschlagen: %s" % e)
                try:
                    send(sock, b"R" + json.dumps({"consent": bool(ok)}).encode())
                except Exception:
                    pass
            elif c == "stop":
                state["stop"] = True
                break
        state["stop"] = True

    threading.Thread(target=control, daemon=True).start()

    try:
        import mss
        from PIL import Image
    except Exception as e:
        log("mss/Pillow fehlen: %s" % e)
        try:
            send(sock, b"F" + json.dumps({"error": "mss/Pillow fehlen: %s" % e}).encode() + b"\0")
        except Exception:
            pass
        return

    with mss.mss() as sct:
        while not state["stop"]:
            if not state["stream"]:
                time.sleep(0.15)
                continue
            try:
                mons = sct.monitors
                count = max(1, len(mons) - 1)
                idx = state["monitor"] if 0 < state["monitor"] <= count else 1
                mon = mons[idx]
                state["left"], state["top"] = mon.get("left", 0), mon.get("top", 0)
                shot = sct.grab(mon)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                # Gleiche Regel wie im direkten Aufnahmeweg des Agenten:
                # hohe Qualitaet -> bis 1920 px, sonst 1280 px. Vorher schickte
                # der Helfer IMMER die volle Aufloesung - das kostete unnoetig
                # Bandbreite und ignorierte die Qualitaetseinstellung.
                q = max(10, min(95, state["quality"]))
                max_width = 1920 if q >= 70 else 1280
                if img.width > max_width:
                    ratio = max_width / img.width
                    img = img.resize((max_width, int(img.height * ratio)))
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=q)
                # Gemeldet wird die ECHTE Bildschirmgroesse (nicht die des
                # verkleinerten JPEG): Das Dashboard rechnet Mausklicks damit
                # zurueck. Das Seitenverhaeltnis bleibt gleich, deshalb passt
                # die Zuordnung weiterhin.
                meta = {"width": shot.width, "height": shot.height, "index": idx,
                        "count": count, "left": state["left"], "top": state["top"]}
                send(sock, b"F" + json.dumps(meta).encode() + b"\0" + buf.getvalue())
            except Exception as e:
                log("Aufnahme: %s" % e)
                try:
                    send(sock, b"F" + json.dumps({"error": str(e)}).encode() + b"\0")
                except Exception:
                    pass
                break
            time.sleep(1.0 / max(1, min(30, state["fps"])))
    log("Ende")

try:
    main()
except Exception:
    log(traceback.format_exc())
"""


def _write_helper_script() -> str:
    """Schreibt das Helfer-Skript neben agent.py und gibt den Pfad zurueck.
    Der Agent laeuft als SYSTEM und darf dort schreiben; der Benutzer darf
    lesen und ausfuehren - genau das wird gebraucht."""
    path = Path(__file__).resolve().parent / _HELPER_FILENAME
    try:
        if not path.is_file() or path.read_text(encoding="utf-8") != _HELPER_SOURCE:
            path.write_text(_HELPER_SOURCE, encoding="utf-8")
    except OSError as e:
        _print(f"[screen-helper] script not writable ({e}) - using TEMP")
        import tempfile
        path = Path(tempfile.gettempdir()) / _HELPER_FILENAME
        path.write_text(_HELPER_SOURCE, encoding="utf-8")
    return str(path)



def _win_session_id() -> int:
    """Sitzungs-ID dieses Prozesses (-1, wenn nicht ermittelbar)."""
    if platform.system() != "Windows":
        return -1
    try:
        import ctypes
        from ctypes import wintypes
        sid = wintypes.DWORD()
        ok = ctypes.windll.kernel32.ProcessIdToSessionId(
            ctypes.windll.kernel32.GetCurrentProcessId(), ctypes.byref(sid))
        return sid.value if ok else -1
    except Exception:
        return -1


def _win_console_session_id() -> int:
    """
    ID der aktiven Konsolensitzung (die Sitzung mit Bildschirm/Tastatur).
    Nur fuer die Diagnose: Weicht sie von der eigenen Sitzung ab, sitzt der
    Agent in einer Sitzung OHNE sichtbaren Desktop - genau die Lage, in der
    BitBlt scheitert. -1, wenn nicht ermittelbar.
    """
    if platform.system() != "Windows":
        return -1
    try:
        import ctypes
        sid = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
        return -1 if sid == 0xFFFFFFFF else int(sid)
    except Exception:
        return -1


# Sitzung, in der zuletzt ein Helfer gestartet wurde. Der Start laeuft ueber
# mehrere Funktionen (Prozessstart, dann Socket-Annahme), deshalb hier abgelegt
# statt durchgereicht.
_LAST_HELPER_SESSION = [None]


def _win_user_sessions() -> list[int]:
    """
    Alle Windows-Sitzungen, in denen ein Benutzer angemeldet ist - sortiert
    nach Eignung fuer eine Bildschirmaufnahme.

    Warum nicht einfach WTSGetActiveConsoleSessionId()? Weil das NUR die
    physische Konsole liefert (Monitor/Tastatur am Geraet). Bei einer
    RDP-Verbindung - auch ueber das Guacamole-Gateway des RMM - hat der
    Benutzer eine eigene Sitzung, und die Konsole ist leer. Der Helfer wurde
    dann nie gestartet, obwohl jemand angemeldet war.

    Reihenfolge:
      1. WTSActive  (angemeldet UND verbunden - hier ist wirklich ein Desktop)
      2. WTSDisconnected (angemeldet, aber getrennt - Desktop existiert weiter,
         die Aufnahme liefert das zuletzt gezeigte Bild)
      3. Konsolensitzung zuerst innerhalb derselben Stufe, weil sie am
         ehesten einen echten Monitor hat.
    Sitzung 0 bleibt immer aussen vor - dort laufen die Dienste, kein Desktop.
    """
    if platform.system() != "Windows":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        class _WTS_SESSION_INFOW(ctypes.Structure):
            _fields_ = [("SessionId", wintypes.DWORD),
                        ("pWinStationName", wintypes.LPWSTR),
                        ("State", ctypes.c_int)]

        wts = ctypes.windll.wtsapi32
        count = wintypes.DWORD(0)
        arr = ctypes.POINTER(_WTS_SESSION_INFOW)()
        # 0 = WTS_CURRENT_SERVER_HANDLE
        if not wts.WTSEnumerateSessionsW(0, 0, 1, ctypes.byref(arr), ctypes.byref(count)):
            return []
        try:
            console = _win_console_session_id()
            active, disconnected = [], []
            for i in range(count.value):
                info = arr[i]
                sid, state = int(info.SessionId), int(info.State)
                if sid == 0:
                    continue                    # Dienst-Sitzung, kein Desktop
                if state == 0:                  # WTSActive
                    active.append(sid)
                elif state == 4:                # WTSDisconnected
                    disconnected.append(sid)
            # Konsolensitzung innerhalb ihrer Stufe nach vorne holen.
            key = lambda x: (x != console, x)   # noqa: E731
            return sorted(active, key=key) + sorted(disconnected, key=key)
        finally:
            try:
                wts.WTSFreeMemory(arr)
            except Exception:
                pass
    except Exception as e:
        _print(f"[screen-helper] Sitzungsliste nicht lesbar: {e}")
        # Rueckfallebene: wenigstens die Konsolensitzung versuchen.
        cid = _win_console_session_id()
        return [cid] if cid > 0 else []


def _needs_session_helper() -> bool:
    """Muessen wir ueber einen Helfer gehen? Nur Windows, nur aus Sitzung 0."""
    if platform.system() != "Windows":
        return False
    if os.environ.get("RMM_SCREEN_HELPER") == "1":
        return False          # wir SIND der Helfer
    return _win_session_id() == 0


def _win_start_in_user_session(args: list) -> bool:
    """
    Startet ein Programm in der Sitzung des angemeldeten Benutzers.

    Der Weg ist der uebliche fuer Dienste: Token der aktiven Konsolensitzung
    holen (WTSQueryUserToken), duplizieren, Umgebungsblock erzeugen und den
    Prozess auf dem sichtbaren Desktop "winsta0\\default" starten.
    Voraussetzung ist, dass wir als SYSTEM laufen - sonst fehlt das Recht.
    """
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        advapi = ctypes.windll.advapi32
        wtsapi = ctypes.windll.wtsapi32
        userenv = ctypes.windll.userenv

        # Sitzung suchen, in der wirklich jemand angemeldet ist.
        #
        # WICHTIG: Frueher stand hier nur WTSGetActiveConsoleSessionId(). Das
        # liefert ausschliesslich die PHYSISCHE Konsolensitzung (Monitor +
        # Tastatur am Geraet). Wer sich per RDP - und damit auch ueber das
        # Guacamole-Gateway des RMM - verbindet, sitzt aber in einer EIGENEN
        # Sitzung; die Konsole ist dann leer und liefert 0 oder die ID einer
        # Sitzung ohne Benutzer. Ergebnis: "Keine aktive Benutzersitzung
        # gefunden", obwohl jemand angemeldet ist. Genau dieser Fall.
        #
        # Deshalb werden jetzt ALLE Sitzungen durchgegangen und die erste
        # genommen, fuer die sich ein Benutzertoken holen laesst.
        candidates = _win_user_sessions()
        if not candidates:
            _print("[screen-helper] Keine Sitzung mit angemeldetem Benutzer gefunden "
                   "(niemand angemeldet?)")
            return False

        session = None
        token = wintypes.HANDLE()
        last_err = 0
        for cand in candidates:
            if wtsapi.WTSQueryUserToken(wintypes.DWORD(cand), ctypes.byref(token)):
                session = cand
                break
            last_err = ctypes.GetLastError()
            _print(f"[screen-helper] Sitzung {cand}: WTSQueryUserToken "
                   f"fehlgeschlagen (error {last_err})")
        if session is None:
            _print(f"[screen-helper] Kein Benutzertoken erhalten "
                   f"(zuletzt error {last_err}) - laeuft der Agent als SYSTEM?")
            return False
        _print(f"[screen-helper] Nutze Sitzung {session} von {candidates}")
        _LAST_HELPER_SESSION[0] = session

        dup = wintypes.HANDLE()
        # 2 = SecurityImpersonation, 1 = TokenPrimary
        if not advapi.DuplicateTokenEx(token, 0x02000000, None, 2, 1, ctypes.byref(dup)):
            _print(f"[screen-helper] DuplicateTokenEx fehlgeschlagen "
                   f"(error {ctypes.GetLastError()})")
            k32.CloseHandle(token)
            return False
        k32.CloseHandle(token)

        env = ctypes.c_void_p()
        userenv.CreateEnvironmentBlock(ctypes.byref(env), dup, False)

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                        ("hStdError", wintypes.HANDLE)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = "winsta0\\default"     # der sichtbare Desktop
        si.dwFlags = 0x00000001               # STARTF_USESHOWWINDOW
        si.wShowWindow = 0                    # SW_HIDE
        pi = PROCESS_INFORMATION()

        cmdline = " ".join(f'"{a}"' for a in args)
        CREATE_UNICODE_ENVIRONMENT = 0x00000400
        CREATE_NO_WINDOW = 0x08000000
        ok = advapi.CreateProcessAsUserW(
            dup, None, ctypes.create_unicode_buffer(cmdline), None, None, False,
            CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, env,
            str(Path(args[1]).resolve().parent) if len(args) > 1 else None,
            ctypes.byref(si), ctypes.byref(pi))
        if not ok:
            _print(f"[screen-helper] CreateProcessAsUser fehlgeschlagen "
                   f"(error {ctypes.GetLastError()})")
            return False
        _print(f"[screen-helper] helper started in session {session} "
               f"(PID {pi.dwProcessId})")
        k32.CloseHandle(pi.hProcess)
        k32.CloseHandle(pi.hThread)
        return True
    except Exception as e:
        _print(f"[screen-helper] Start fehlgeschlagen: {e}")
        return False


def _sock_send(sock, payload: bytes) -> None:
    sock.sendall(len(payload).to_bytes(4, "big") + payload)


def _sock_recv(sock) -> bytes | None:
    def _exact(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf
    head = _exact(4)
    if not head:
        return None
    return _exact(int.from_bytes(head, "big"))


class _ScreenHelper:
    """Verbindung des Agenten (Sitzung 0) zu seinem Helfer in der Benutzersitzung."""

    def __init__(self, server, conn):
        self.server = server
        self.conn = conn
        self.alive = True
        # In welcher Windows-Sitzung laeuft dieser Helfer? _ensure_helper()
        # vergleicht das mit der aktuellen Benutzersitzung.
        self.session = None
        # Der Socket wird von ZWEI Threads benutzt: der Event-Loop schickt
        # Eingaben (jede Mausbewegung!), der Aufnahme-Thread liest Bilder und
        # schickt Konfigurationen. Ohne Schloss ueberlappen zwei sendall() und
        # zerstoeren die Laengen-Praefixe -> der Helfer liest Muell, der
        # Steuerkanal stirbt lautlos. Genau so fiel die Fernsteuerung aus.
        self._send_lock = threading.Lock()

    def _readable(self, timeout: float) -> bool:
        """Wartet mit select() darauf, dass Daten anliegen.
        Wichtig: NICHT socket.settimeout() benutzen. Das gilt fuer den ganzen
        Socket - also auch fuer sendall() im anderen Thread, das dann mitten
        in einer Nachricht abbrechen kann."""
        import select as _select
        try:
            r, _w, _e = _select.select([self.conn], [], [], max(0.0, timeout))
            return bool(r)
        except Exception:
            return False

    @classmethod
    def start(cls, loop, timeout: float = 25.0):
        import secrets
        import socket as _socket

        server = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        server.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))       # nur lokal, freier Port
        server.listen(1)
        server.settimeout(timeout)
        port = server.getsockname()[1]
        key = secrets.token_hex(16)

        # NICHT agent.py erneut starten - siehe Kommentar bei _HELPER_SOURCE.
        script = _write_helper_script()
        exe = sys.executable
        # Die Sprache wird als drittes Argument mitgegeben - der Helfer laeuft in
        # einem eigenen Prozess und liest die .env des Agenten nicht.
        if not _win_start_in_user_session([exe, script, str(port), key, AGENT_LANG]):
            server.close()
            return None

        try:
            conn, _addr = server.accept()
        except Exception as e:
            _print(f"[screen-helper] helper did not report in: {e}")
            server.close()
            return None
        conn.settimeout(20.0)
        try:
            hello = _sock_recv(conn)
            if not hello or json.loads(hello.decode()).get("key") != key:
                _print("[screen-helper] wrong key - connection discarded")
                conn.close()
                server.close()
                return None
        except Exception as e:
            _print(f"[screen-helper] Anmeldung fehlgeschlagen: {e}")
            conn.close()
            server.close()
            return None
        _print("[screen-helper] Helfer verbunden.")
        # Ab jetzt blockierend: Wartezeiten regelt select() pro Leserichtung,
        # damit ein Timeout nie ein laufendes sendall() zerreisst.
        try:
            conn.settimeout(None)
        except Exception:
            pass
        obj = cls(server, conn)
        # Sitzung merken: _ensure_helper() vergleicht sie spaeter mit der
        # aktuellen Benutzersitzung und startet den Helfer neu, wenn der
        # Benutzer inzwischen woanders sitzt (Konsole <-> RDP).
        obj.session = _LAST_HELPER_SESSION[0]
        return obj

    def send(self, obj) -> bool:
        try:
            with self._send_lock:
                _sock_send(self.conn, b"C" + json.dumps(obj).encode())
            return True
        except Exception:
            self.alive = False
            return False

    def recv(self, timeout: float | None = None):
        """
        Naechste Nachricht vom Helfer.
        Rueckgabe: ("frame", meta, jpeg) | ("reply", obj, None) | None
        """
        try:
            if timeout is not None and not self._readable(timeout):
                return None
            msg = _sock_recv(self.conn)
        except Exception:
            return None
        if not msg:
            return None
        if msg[:1] == b"F":
            sep = msg.index(b"\0", 1)
            return "frame", json.loads(msg[1:sep].decode()), msg[sep + 1:]
        if msg[:1] == b"R":
            return "reply", json.loads(msg[1:].decode()), None
        return None

    def drain(self, seconds: float = 1.5) -> None:
        """
        Restnachrichten der VORHERIGEN Sitzung wegwerfen.
        Nach einem Stop liegen oft noch Bilder im Socket-Puffer. Wurden die
        nicht abgeraeumt, verbrauchte die naechste Zustimmungsabfrage ihre
        Wartezeit mit dem Lesen alter Frames - im Dashboard stand dann
        'abgelehnt', obwohl niemand etwas angeklickt hatte.
        """
        end = time.time() + seconds
        while time.time() < end:
            if not self._readable(0.2):
                return          # nichts mehr da - fertig
            try:
                if _sock_recv(self.conn) is None:
                    self.alive = False
                    return
            except Exception:
                self.alive = False
                return

    def ask_consent(self, who: str, timeout_s: int):
        """Zustimmungsdialog IN der Benutzersitzung anzeigen lassen.
        Aus Sitzung 0 ist ein Fenster fuer den Benutzer unsichtbar - genau
        deshalb kam bisher 'Warte auf Bestaetigung', ohne dass am Bildschirm
        etwas erschien.

        Rueckgabe: True = zugelassen, False = abgelehnt/Zeit abgelaufen,
        None = Verbindung zum Helfer gestoert. Nur bei None darf der Aufrufer
        den Helfer neu starten und es erneut versuchen - eine echte Ablehnung
        wird NICHT wiederholt.
        """
        # Stream sicher anhalten und Reste abraeumen, damit die Antwort des
        # Dialogs nicht hinter alten Bildern haengt.
        self.send({"cmd": "config", "stream": False})
        self.drain()
        if not self.alive:
            return None
        if not self.send({"cmd": "consent", "who": who, "timeout": int(timeout_s)}):
            return None
        deadline = time.time() + timeout_s + 15
        while time.time() < deadline:
            got = self.recv(timeout=max(1.0, deadline - time.time()))
            if got is None:
                self.alive = False
                return None      # Leitung tot -> KEINE stille Ablehnung
            kind, obj, _ = got
            if kind == "reply" and "consent" in obj:
                return bool(obj["consent"])
        return False

    def close(self):
        self.alive = False
        for s in (self.conn, self.server):
            try:
                s.close()
            except Exception:
                pass


def _screen_helper_loop(loop, helper):
    """Nimmt die Bilder vom Helfer entgegen und schickt sie ans Dashboard."""
    helper.send({"cmd": "config", "stream": True,
                 "quality": int(_screen_stream.get("quality", 60)),
                 "fps": int(_screen_stream.get("fps", 10)),
                 "monitor": int(_screen_stream.get("monitor", 1) or 1)})
    last_cfg = None
    while _screen_stream["active"] and helper.alive:
        cfg = (int(_screen_stream.get("quality", 60)),
               int(_screen_stream.get("fps", 10)),
               int(_screen_stream.get("monitor", 1) or 1))
        if cfg != last_cfg:
            helper.send({"cmd": "config", "stream": True, "quality": cfg[0],
                         "fps": cfg[1], "monitor": cfg[2]})
            last_cfg = cfg
        got = helper.recv(timeout=30.0)
        if got is None:
            # Leitung weg: Als tot markieren, damit die NAECHSTE Sitzung einen
            # frischen Helfer startet, statt auf einen toten Socket zu warten.
            helper.alive = False
            break
        kind, meta, jpeg = got
        if kind != "frame":
            continue
        if meta.get("error"):
            _notify_screen_error(loop, f"Helper reports: {meta['error']}",
                                 code="agent_err_helper", params={"err": meta["error"]})
            break
        _screen_stream["mon_left"] = meta.get("left", 0)
        _screen_stream["mon_top"] = meta.get("top", 0)
        b64 = base64.b64encode(jpeg).decode()
        asyncio.run_coroutine_threadsafe(
            sio.emit("screen-frame", {
                "id": DEVICE_ID, "image": b64,
                "width": meta.get("width"), "height": meta.get("height"),
                "monitor_index": meta.get("index", 0),
                "monitor_count": meta.get("count", 1),
            }, namespace="/agent"),
            loop,
        )
    _screen_stream["active"] = False
    _print("[screen-helper] transfer finished.")


def _ensure_helper(loop):
    """
    Verbindung zum Helfer in der Benutzersitzung holen (oder aufbauen).
    Wird sowohl fuer die Zustimmungsabfrage als auch fuer die Aufnahme
    gebraucht - deshalb an einer Stelle gebuendelt.
    """
    helper = _screen_stream.get("helper")
    if helper is not None and helper.alive:
        # Sitzt der laufende Helfer noch in der RICHTIGEN Sitzung?
        # Wechselt der Benutzer zwischen Konsole und RDP (oder meldet sich neu
        # an), bekommt er eine ANDERE Sitzungs-ID. Der alte Helfer laeuft dann
        # zwar noch, zeichnet aber einen Desktop auf, den niemand mehr sieht -
        # und sein Zustimmungsdialog erscheint auf einem unsichtbaren
        # Bildschirm. Deshalb hier vergleichen und ihn notfalls ersetzen.
        want = _win_user_sessions()
        have = getattr(helper, "session", None)
        if not want or have is None or have == want[0]:
            return helper
        _print(f"[screen-helper] Benutzer ist jetzt in Sitzung {want[0]}, "
               f"der Helfer laeuft in {have} - starte ihn neu.")
        try:
            helper.close()
        except Exception:
            pass
        _screen_stream["helper"] = None
    helper = _ScreenHelper.start(loop)
    _screen_stream["helper"] = helper
    return helper


def _screen_capture_loop(loop):
    """Läuft in einem eigenen Thread und schickt fortlaufend Bildschirm-Frames."""
    is_linux = platform.system() == "Linux"

    # Windows-Sonderfall: Der Agent läuft als geplante Aufgabe unter SYSTEM und
    # damit in Sitzung 0. Von dort ist der Desktop des angemeldeten Benutzers
    # grundsätzlich nicht erreichbar ("Session-0-Isolation"). Deshalb starten
    # wir einen Helfer IN der Benutzersitzung und lassen uns die Bilder von
    # dort schicken. Details siehe _ScreenHelper.
    if _needs_session_helper():
        helper = _ensure_helper(loop)
        if helper is None:
            _notify_screen_error(
                loop,
                "The agent runs as a service in session 0 and cannot capture the "
                "desktop from there. The helper for the user session did not "
                "report in. Check whether anyone is signed in on the device. The "
                "helper log is in the signed-in user's TEMP folder as "
                "rmm-screen-helper.log (type %TEMP% in Explorer). "
                f"[Sessions with a signed-in user: {_win_user_sessions() or 'none'}]",
                code="agent_err_no_helper",
                # Die gefundenen Sitzungen mitschicken: Ohne diese Angabe war
                # nicht zu unterscheiden, ob NIEMAND angemeldet ist oder ob der
                # Start des Helfers in einer gefundenen Sitzung scheiterte -
                # zwei voellig verschiedene Ursachen mit demselben Text.
                params={"sessions": ", ".join(str(x) for x in _win_user_sessions()) or "-"})
            _screen_stream["active"] = False
            return
        try:
            _screen_helper_loop(loop, helper)
        finally:
            # Verbindung offen lassen: Die naechste Sitzung braucht sie fuer
            # die Zustimmungsabfrage sofort wieder. Nur die Uebertragung
            # anhalten.
            helper.send({"cmd": "config", "stream": False})
        return

    try:
        sct = mss.mss()
    except Exception as e:
        # Kein Display erfassbar. Auf Linux/headless direkt auf Shell umschalten,
        # sonst normale Fehlermeldung (Windows -> RDP-Angebot im Dashboard).
        if is_linux:
            _notify_screen_mode(loop, "shell", f"Screen not capturable: {e}",
                                code="agent_mode_no_screen", params={"err": str(e)})
        else:
            _notify_screen_error(loop, f"Screen capture not possible: {e}",
                                 code="agent_err_capture", params={"err": str(e)})
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
    helper_retry_done = False   # Fallback auf die Benutzersitzung nur einmal

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
                _notify_screen_mode(loop, "shell", f"Screen capture aborted: {msg}",
                                    code="agent_mode_aborted", params={"err": msg})
                _screen_stream["active"] = False
                break
            # Windows: typischer Fall headless/kein Desktop -> RDP-Angebot.
            if "denied" in msg.lower() or "bitblt" in msg.lower():
                # ZWEITER VERSUCH ueber die Benutzersitzung.
                # Grund: Genau dieselbe Fehlermeldung ("BitBlt failed",
                # "access denied") entsteht auch dann, wenn der Prozess zwar
                # NICHT in Sitzung 0 sitzt (_needs_session_helper() sagt also
                # "nicht noetig"), aber trotzdem keinen sichtbaren Desktop
                # besitzt - z.B. bei einer getrennten RDP-Sitzung, einem
                # Task-Scheduler-Start "unabhaengig von der Anmeldung" oder
                # einer verwaisten Sitzung nach Benutzerwechsel. In diesen
                # Faellen liefert der Helfer in der aktiven Konsolensitzung
                # sehr wohl ein Bild. Nur EINMAL versuchen, sonst Endlosschleife.
                if IS_WINDOWS and not helper_retry_done and os.environ.get("RMM_SCREEN_HELPER") != "1":
                    helper_retry_done = True
                    _print(f"[agent] Direkte Aufnahme scheitert ({msg}) - "
                           f"versuche Helfer in der aktiven Benutzersitzung.")
                    helper = _ensure_helper(loop)
                    if helper is not None:
                        try:
                            _screen_helper_loop(loop, helper)
                        finally:
                            helper.send({"cmd": "config", "stream": False})
                        break
                    _print("[agent] helper unreachable - giving up.")
                tech = (f"{msg} | session {_win_session_id()} | "
                        f"active console session {_win_console_session_id()} | "
                        f"helper path {'yes' if _needs_session_helper() else 'no'}")
                _notify_screen_error(
                    loop,
                    "The screen cannot be captured. Common cause: the PC is a "
                    "headless VM/server WITHOUT an active graphical session (no "
                    "monitor, nobody signed in, or reachable only via RDP). Without "
                    "a real screen session there is nothing to transmit. Fix: stay "
                    "signed in on the device, provide a (virtual) monitor, or "
                    f"install a virtual display driver. [Technical: {tech}]",
                    code="agent_err_no_display", params={"tech": tech}
                )
                _screen_stream["active"] = False
                break
            # Andere Fehler: ein paar Mal tolerieren, dann aufgeben
            if consecutive_errors >= 5:
                _notify_screen_error(loop, f"Screen capture aborted: {msg}",
                                     code="agent_err_aborted", params={"err": msg})
                _screen_stream["active"] = False
                break
            time.sleep(1)

    try:
        sct.close()
    except Exception:
        pass


def _notify_screen_error(loop, message, code=None, params=None):
    """
    Schickt eine Fehlermeldung ans Dashboard (einmalig).

    WARUM code/params zusaetzlich zum Text?
    Der Agent kennt die Sprache des Dashboard-Benutzers nicht - mehrere Personen
    mit verschiedenen Spracheinstellungen koennen dasselbe Geraet ansehen. Statt
    eines fertigen Satzes schickt der Agent daher einen Uebersetzungs-SCHLUESSEL
    (`code`) und die Einsetzwerte (`params`); uebersetzt wird erst im Browser.

    `message` bleibt als Rueckfallebene erhalten und ist bewusst ENGLISCH:
      - Ein aelteres Dashboard, das `code` nicht kennt, zeigt weiterhin Text.
      - Die Agent-Konsole und das Protokoll bleiben lesbar.
    Das Backend reicht die Nutzlast unveraendert weiter, es braucht also KEINE
    Anpassung fuer die neuen Felder.
    """
    _print(f"[agent] Screen error: {message}")
    payload = {"id": DEVICE_ID, "error": message}
    if code:
        payload["code"] = code
        payload["params"] = params or {}
    try:
        asyncio.run_coroutine_threadsafe(
            sio.emit("screen-error", payload, namespace="/agent"),
            loop,
        )
    except Exception:
        pass


def _notify_screen_mode(loop, mode, reason="", code=None, params=None):
    """
    Teilt dem Dashboard mit, WIE Remote-Zugriff möglich ist. Wichtigster Fall:
    mode='shell' -> das Dashboard öffnet direkt eine Shell (headless/Shell-only).

    `reason` ist die englische Rueckfallebene, `code`/`params` werden im
    Dashboard uebersetzt (siehe _notify_screen_error).
    """
    _print(f"[agent] Screen mode: {mode} ({reason})")
    payload = {"id": DEVICE_ID, "mode": mode, "reason": reason}
    if code:
        payload["code"] = code
        payload["params"] = params or {}
    try:
        asyncio.run_coroutine_threadsafe(
            sio.emit("screen-mode", payload, namespace="/agent"),
            loop,
        )
    except Exception:
        pass


def _someone_logged_in() -> bool:
    """
    Prüft, ob am Gerät gerade jemand angemeldet ist. Nur dann wird um
    Zustimmung gebeten - an unbeaufsichtigten Geräten (niemand angemeldet)
    verbindet der Remote-Bildschirm direkt. Bei unklarer Lage wird zur
    Sicherheit "angemeldet" angenommen (dann wird gefragt).
    """
    try:
        if IS_WINDOWS:
            r = _run(["quser"])           # Exit-Code 1 + leere Liste, wenn niemand angemeldet
            out = (r.stdout or "").strip()
            return r.returncode == 0 and len(out.splitlines()) > 1
        # Linux: 'who' erfasst klassische Logins; 'loginctl' zusätzlich
        # xrdp-/Wayland-/Seat-Sitzungen, die 'who' teils nicht listet.
        r = _run(["who"])
        if (r.stdout or "").strip():
            return True
        r = _run(["loginctl", "list-sessions", "--no-legend"])
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except Exception:
        return True


def _tk_consent_dialog(who: str, timeout_s: int) -> bool:
    """
    Schön gestaltetes Tkinter-Zustimmungsfenster (dunkles Design, Countdown,
    Zulassen/Ablehnen). Läuft blockierend im Executor-Thread. Wirft bei
    Problemen (kein Tkinter/kein Display) - der Aufrufer fällt dann auf die
    nativen Dialoge (MessageBox/zenity/...) zurück.
    """
    import tkinter as tk

    BG = "#0f1520"; PANEL = "#161e2c"; TEXT = "#e6ecf5"; SUB = "#8ea0b8"
    ACCENT = "#4f8cff"; DANGER = "#3a4356"

    result = {"allow": False}
    # Windows-Taskleiste: Ohne eigene AppUserModelID gruppiert Windows das
    # Fenster unter pythonw.exe und zeigt in der TASKLEISTE das Python-Icon,
    # egal was iconphoto setzt. Mit eigener ID zieht die Taskleiste das
    # Fenster-Icon (unser Logo).
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RapalleRMM.Agent.Consent")
        except Exception:
            pass
    root = tk.Tk()
    root.withdraw()
    root.title("RAPALLE.net RMM")
    # Logo: logo.png (PNG!) liegt seit Install/Update neben agent.py.
    # iconphoto kann PNG auf Windows UND Linux - iconbitmap dagegen nur echte
    # .ico-Dateien (das alte favicon.ico war ein PNG -> blankes Icon).
    # Mehrere Größen mitgeben, damit Titelleiste (klein) UND Taskleiste
    # (mittel) jeweils eine scharfe Variante bekommen.
    _logo_img = None
    try:
        _logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
        if os.path.isfile(_logo_path):
            _logo_img = tk.PhotoImage(file=_logo_path)
            _sizes = []
            for target in (64, 32, 16):
                f = max(1, _logo_img.width() // target)
                _sizes.append(_logo_img.subsample(f, f))
            root._icon_refs = [_logo_img, *_sizes]   # GC-Schutz
            root.iconphoto(True, *_sizes, _logo_img)
    except Exception:
        _logo_img = None
    root.configure(bg=BG)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    try:
        root.overrideredirect(False)
    except Exception:
        pass

    frame = tk.Frame(root, bg=BG, padx=28, pady=22)
    frame.pack()
    if _logo_img is not None:
        try:
            # 1024er-Logo auf ~64 px fürs Fenster herunterrechnen.
            _factor = max(1, _logo_img.width() // 64)
            _logo_small = _logo_img.subsample(_factor, _factor)
            root._logo_refs = (_logo_img, _logo_small)   # GC-Schutz
            tk.Label(frame, image=_logo_small, bg=BG).pack()
        except Exception:
            tk.Label(frame, text="🖥️", font=("Segoe UI Emoji", 30), bg=BG, fg=TEXT).pack()
    else:
        tk.Label(frame, text="🖥️", font=("Segoe UI Emoji", 30), bg=BG, fg=TEXT).pack()
    tk.Label(frame, text=_at("consent_requested"), font=("Segoe UI", 14, "bold"),
             bg=BG, fg=TEXT, pady=6).pack()
    tk.Label(frame,
             text=_at("consent_body", who=who),
             font=("Segoe UI", 10), bg=BG, fg=SUB, justify="center").pack()
    countdown = tk.Label(frame, text="", font=("Segoe UI", 9), bg=BG, fg=SUB, pady=8)
    countdown.pack()

    btns = tk.Frame(frame, bg=BG)
    btns.pack(pady=(4, 0))

    def _finish(allow):
        result["allow"] = allow
        try:
            root.destroy()
        except Exception:
            pass

    def _mkbtn(parent, text, bg, fg, cmd):
        b = tk.Label(parent, text=text, font=("Segoe UI", 10, "bold"),
                     bg=bg, fg=fg, padx=22, pady=8, cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        return b

    deny_btn = _mkbtn(btns, _at("deny"), DANGER, TEXT, lambda: _finish(False))
    allow_btn = _mkbtn(btns, _at("allow_check"), ACCENT, "#ffffff", lambda: _finish(True))
    deny_btn.pack(side="left", padx=6)
    allow_btn.pack(side="left", padx=6)

    remaining = {"s": timeout_s}
    def _tick():
        remaining["s"] -= 1
        if remaining["s"] <= 0:
            _finish(False)   # keine Antwort -> ablehnen
            return
        countdown.config(text=_at("auto_deny_in", secs=remaining["s"]))
        root.after(1000, _tick)
    countdown.config(text=_at("auto_deny_in", secs=timeout_s))
    root.after(1000, _tick)

    # Zentrieren + in den Vordergrund
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"+{x}+{y}")
    root.deiconify()
    try:
        root.lift(); root.focus_force()
    except Exception:
        pass
    root.protocol("WM_DELETE_WINDOW", lambda: _finish(False))   # X = ablehnen
    root.mainloop()
    return result["allow"]


def _ask_screen_consent(requested_by: str, timeout_s: int = 30) -> bool:
    """
    Zeigt am Gerät eine Abfrage "Remote-Bildschirm zulassen?" und gibt True
    zurück, wenn der Benutzer zustimmt. Keine Antwort innerhalb des Timeouts,
    Ablehnung oder ein Fehler (kein Dialog möglich) bedeuten NEIN - verbunden
    wird nur bei ausdrücklicher Bestätigung.
    Läuft blockierend und wird deshalb im Executor ausgeführt.
    """
    who = requested_by or "Ein Administrator"

    # Sitzung 0: Ein Fenster, das WIR hier oeffnen, sieht der Benutzer nie -
    # der Desktop gehoert einer anderen Sitzung. Deshalb laesst der Helfer den
    # Dialog dort anzeigen. Genau das war der Grund, warum im Dashboard
    # "Warte auf Bestaetigung" stand, am Bildschirm aber nichts erschien.
    if _needs_session_helper():
        # Zwei Anlaeufe: Beim ersten kann die Leitung zum Helfer noch von der
        # vorherigen Sitzung her tot sein (Helfer beendet, Abmeldung,
        # Benutzerwechsel). Frueher wurde das als "abgelehnt" gewertet -
        # deshalb stand nach Stop/Neustart einer Sitzung IMMER "abgelehnt" da.
        for attempt in (1, 2):
            try:
                helper = _ensure_helper(_AGENT_LOOP)
                if helper is None:
                    _print("[agent] no helper in the user session - "
                           "cannot ask for consent (nobody logged in?)")
                    return False
                answer = helper.ask_consent(who, timeout_s)
                if answer is not None:
                    return bool(answer)
                _print(f"[agent] helper not responding (attempt {attempt}) - "
                       f"reconnecting.")
            except Exception as e:
                _print(f"[agent] consent via the helper failed "
                       f"(Versuch {attempt}): {e}")
            # Tote Verbindung wegwerfen, damit _ensure_helper einen neuen
            # Helfer in der aktuellen Benutzersitzung startet.
            old = _screen_stream.get("helper")
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
            _screen_stream["helper"] = None
        _print("[agent] consent could not be obtained on the second attempt "
               "- session is denied.")
        return False

    title = _at("consent_window")
    text = (_at("consent_body_1line", who=who) + "\n\n"
            + _at("consent_ask", secs=timeout_s))
    # 1) Bevorzugt: das schön gestaltete Tkinter-Fenster (Windows + Linux).
    try:
        return _tk_consent_dialog(who, timeout_s)
    except Exception as e:
        _print(f"[agent] tkinter dialog not possible ({e}) - using native dialog")
    # 2) Fallbacks: native Dialoge des jeweiligen Systems.
    try:
        if IS_WINDOWS:
            import ctypes
            MB_YESNO = 0x4; MB_ICONQUESTION = 0x20
            MB_SYSTEMMODAL = 0x1000; MB_SETFOREGROUND = 0x10000; MB_TOPMOST = 0x40000
            flags = MB_YESNO | MB_ICONQUESTION | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST
            user32 = ctypes.windll.user32
            try:
                # MessageBoxTimeoutW: wie MessageBox, aber mit Timeout (ms).
                # Rückgabe 6 = Ja, 7 = Nein, 32000 = Timeout.
                res = user32.MessageBoxTimeoutW(0, text, title, flags, 0, timeout_s * 1000)
            except AttributeError:
                res = user32.MessageBoxW(0, text, title, flags)
            return res == 6
        # Linux (Desktop): zenity -> kdialog -> xmessage, was verfügbar ist.
        import shutil as _shutil
        if _shutil.which("zenity"):
            r = _run(["zenity", "--question", f"--title={title}", f"--text={text}",
                      f"--timeout={timeout_s}", f"--ok-label={_at('allow')}", f"--cancel-label={_at('deny')}"],
                     capture_output=True)
            return r.returncode == 0   # 0=Ja, 1=Nein, 5=Timeout
        if _shutil.which("kdialog"):
            try:
                r = _run(["kdialog", "--title", title, "--yesno", text],
                         capture_output=True, timeout=timeout_s + 5)
                return r.returncode == 0
            except Exception:
                return False           # Timeout/Fehler -> abgelehnt
        if _shutil.which("xmessage"):
            # Exit-Code = Button-Wert; Timeout liefert 0 -> deshalb 101/102.
            r = _run(["xmessage", "-center", "-timeout", str(timeout_s),
                      "-buttons", f"{_at('allow')}:101,{_at('deny')}:102", text],
                     capture_output=True)
            return r.returncode == 101
        _print("[agent] no dialog tool (zenity/kdialog/xmessage) found - remote screen denied")
        return False
    except Exception as e:
        _print(f"[agent] consent dialog failed ({e}) - remote screen denied")
        return False


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

    # Ohne Bildaufnahme-Pakete gibt es nichts zu streamen. Vorher aber EINMAL
    # selbst reparieren - meist sind die Pakete nicht weg, sondern kaputt.
    if not _SCREEN_AVAILABLE:
        await loop.run_in_executor(None, _repair_screen)
    if not _SCREEN_AVAILABLE:
        _notify_screen_mode(loop, "shell",
                            "Screen streaming not possible: mss/Pillow cannot be "
                            f"loaded ({_SCREEN_ERROR}). Python: {sys.executable}. "
                            "A shell will be opened instead.",
                            code="agent_mode_no_packages",
                            params={"err": str(_SCREEN_ERROR), "python": sys.executable})
        return

    # Ist überhaupt ein grafischer Bildschirm da? (setzt auf Linux ggf. DISPLAY)
    available, reason, dcode, dparams = _detect_graphical_display()
    if not available:
        _notify_screen_mode(loop, "shell", reason, code=dcode, params=dparams)
        return

    # Läuft bereits ein Stream? Früher wurde hier einfach zurückgekehrt -
    # dadurch bekam eine NEU gestartete Sitzung KEINE erneute Zustimmungs-
    # abfrage und hängte sich still an den laufenden Stream. Jetzt beenden wir
    # den alten Stream und lassen den normalen Ablauf (inkl. Consent) neu
    # durchlaufen, sodass jede neue Sitzung sauber bestätigt werden muss.
    if _screen_stream["active"]:
        _print("[agent] screen-start bei bereits aktivem Stream -> alten Stream beenden, Consent neu einholen")
        _screen_stream["active"] = False
        old = _screen_stream.get("thread")
        if old and old.is_alive():
            try:
                old.join(timeout=2.0)
            except Exception:
                pass

    # Zustimmung am Gerät einholen (nur physische Geräte; das Backend schickt
    # require_consent entsprechend - und als doppelte Absicherung fragen VMs
    # und LXCs auch bei gesetztem Flag nie).
    require_consent = bool(isinstance(data, dict) and data.get("require_consent"))
    _print(f"[agent] screen-start empfangen (require_consent={require_consent}, "
           f"device_type={DETECTED_DEVICE_TYPE or 'physical'})")
    # Nur LXC-Container fragen nie (dort meldet sich niemand grafisch an).
    # VMs fragen sehr wohl - z.B. wenn jemand per RDP in der VM arbeitet.
    if DETECTED_DEVICE_TYPE == "lxc":
        require_consent = False
    # Niemand angemeldet? Dann gibt es niemanden, der zustimmen könnte ->
    # direkt verbinden (typisch: unbeaufsichtigter physischer Rechner).
    if require_consent and not await loop.run_in_executor(None, _someone_logged_in):
        _print(f"[agent] {_at('log_nobody_home')}")
        require_consent = False
    # Marke fuer DIESE Anfrage. Kommt waehrend der Abfrage ein "screen-stop"
    # (Dashboard-Fenster zu), erhoeht on_screen_stop den Zaehler und wir
    # brechen danach ab, statt ins Leere zu streamen.
    _screen_stream["epoch"] += 1
    my_epoch = _screen_stream["epoch"]

    if require_consent:
        requested_by = (data or {}).get("requested_by") or ""
        _notify_screen_mode(loop, "consent", "Waiting for confirmation on the device…",
                            code="agent_mode_consent")
        allowed = await loop.run_in_executor(None, _ask_screen_consent, requested_by)
        if _screen_stream["epoch"] != my_epoch:
            _print("[agent] consent arrived too late - the session has meanwhile "
                   "ended or been requested again. No stream is started.")
            return
        if _screen_stream["active"]:
            return  # in der Zwischenzeit anderweitig gestartet
        if not allowed:
            _print(f"[agent] {_at('log_screen_denied')}")
            try:
                await sio.emit("screen-error", {
                    "id": DEVICE_ID, "consent_denied": True,
                    "error": "The user at the device did not confirm remote screen access.",
        "code": "agent_err_consent_denied", "params": {},
                }, namespace="/agent")
            except Exception:
                pass
            return
        _print(f"[agent] {_at('log_screen_allowed')}")

    _screen_stream["active"] = True
    _screen_stream["thread"] = threading.Thread(target=_screen_capture_loop, args=(loop,), daemon=True)
    _screen_stream["thread"].start()
    _print(f"[agent] {_at('log_screen_started')}")


@sio.on("screen-stop", namespace="/agent")
async def on_screen_stop(data):
    """Stoppt das Bildschirm-Streaming."""
    _screen_stream["active"] = False
    # Auch eine noch offene Zustimmungsabfrage entwerten (siehe "epoch"):
    # sonst startete eine verspaetete Zustimmung einen Stream, den niemand
    # mehr sieht - und blockierte die naechste Sitzung.
    _screen_stream["epoch"] += 1
    _print(f"[agent] {_at('log_screen_stopped')}")


@sio.on("screen-set-monitor", namespace="/agent")
async def on_screen_set_monitor(data):
    """Wechselt den gestreamten Bildschirm (Multi-Monitor). Der Capture-Loop
    übernimmt die Auswahl beim nächsten Frame automatisch."""
    if isinstance(data, dict) and data.get("monitor") is not None:
        try:
            # 0 = "Alle Bildschirme" (mss.monitors[0] = Gesamtfläche aller Monitore)
            _screen_stream["monitor"] = max(0, int(data["monitor"]))
            _print(f"[agent] {_at('log_monitor_switched', n=_screen_stream['monitor'])}")
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
                            "detail": f"SYSTEM task '{taskname}' triggered via event (elevated)."}
                else:
                    _print(f"[agent] event trigger failed: {trig_err}")

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
                    "detail": ("No elevated path available (agent not running as admin and "
                               "no maintenance tasks installed). Run ONCE as administrator "
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
                        "detail": f"started without systemd (log: {logf})"}
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
    # Laeuft ein Helfer in der Benutzersitzung? Dann MUSS die Eingabe dort
    # ausgefuehrt werden - aus Sitzung 0 erreicht kein Klick den Desktop.
    helper = _screen_stream.get("helper")
    if helper is not None and helper.alive:
        helper.send({"cmd": "input", "data": data})
        return

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
        _print(f"[agent] {_at('log_input_error', err=e)}")


# --------------------------------------------------------------
# Programmstart
# --------------------------------------------------------------

def _install_exception_logging():
    """Sorgt dafuer, dass NICHTS mehr stumm stirbt: unbehandelte Fehler aus
    Threads und asyncio-Tasks landen im agent.log statt im Nirwana."""
    def _thread_hook(args):
        log.error("[agent] Unbehandelter Fehler in Thread %s: %r",
                  getattr(args.thread, "name", "?"), args.exc_value,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass
    def _loop_handler(loop, context):
        exc = context.get("exception")
        log.error("[agent] Unbehandelter Fehler im Event-Loop: %s%s",
                  context.get("message", ""), f" ({exc!r})" if exc else "")
    try:
        _AGENT_LOOP.set_exception_handler(_loop_handler)
    except Exception:
        pass


async def main():
    _install_exception_logging()
    _print(f"[agent] {_at('log_device_name', name=DEVICE_NAME)}")
    _print(f"[agent] {_at('log_device_id', id=DEVICE_ID)}")
    _print(f"[agent] {_at('log_backend', url=BACKEND_URL)}")
    _print(f"[agent] Remote Screen: "
           + ("verfügbar" if _SCREEN_AVAILABLE
              else f"deaktiviert - mss/Pillow nicht ladbar ({_SCREEN_ERROR})"))
    if not _INPUT_AVAILABLE:
        _print(f"[agent] {_at('log_remote_ctl', state=_at('log_disabled'))} - pynput ({_INPUT_ERROR})")
    _print(f"[agent] {_at('log_remote_ctl', state=_at('log_available') if _INPUT_AVAILABLE else _at('log_disabled'))}")
    # Beweis-Zeile: Steht sie NICHT im Log, laeuft auf dem Client eine aeltere
    # agent.py - egal was die Versionsnummer behauptet.
    if IS_WINDOWS:
        _print("[agent] " + _at("log_session",
               sid=_win_session_id(), csid=_win_console_session_id(),
               helper=_at("log_helper_used") if _needs_session_helper()
                      else _at("log_helper_unused")))

    # Heartbeat läuft als eigene Hintergrund-Aufgabe, unabhängig von der Verbindung
    # Diagnose einhaengen, BEVOR irgendetwas anderes startet - alles was
    # danach schiefgeht, soll bereits im Puffer landen.
    _diag_install()

    # Alle Dauerschleifen laufen unter dem Aufseher. Stirbt eine an einer
    # Ausnahme, wird sie neu gestartet statt lautlos zu verschwinden.
    _agent_supervise("loop-herzschlag", loop_heartbeat_agent)
    _agent_supervise("heartbeat", heartbeat_loop)
    _agent_supervise("node-keepalive", node_keepalive_loop)
    _agent_supervise("diag-upload", diag_upload_loop)

    # Verbindungsschleife: WIR steuern das Wiederverbinden selbst. Vor jedem
    # Versuch sauber trennen, damit die interne Client-Zustand nicht auf
    # "Already connected" hängen bleibt.
    if any(x in BACKEND_URL for x in ("localhost", "127.0.0.1")):
        _print(f"[agent] WARNUNG: BACKEND_URL zeigt auf {BACKEND_URL} - ein entfernter/"
               f"public client can NOT reach that. Set the public "
               f"Adresse eintragen (z.B. https://domain) bzw. im Dashboard unter "
               f"Einstellungen -> Allgemein -> 'Server-URL' setzen.")
    # --- Gestaffelter Erststart -------------------------------------
    # Aus der Geräte-ID wird eine feste Wartezeit innerhalb von
    # CONNECT_SPREAD_S abgeleitet. Damit klopfen nach einem Server-Neustart
    # nicht alle Agenten in derselben Sekunde an, sondern verteilt über das
    # Fenster - und der Server nimmt sie der Reihe nach auf.
    if CONNECT_SPREAD_S > 0:
        import hashlib as _hl
        _fingerprint = int(_hl.sha256(str(DEVICE_ID).encode("utf-8")).hexdigest()[:8], 16)
        offset = (_fingerprint % (CONNECT_SPREAD_S * 1000)) / 1000.0
        _print(f"[agent] Gestaffelter Start: warte {offset:.1f}s vor dem ersten Verbindungsversuch")
        await asyncio.sleep(offset)

    backoff = 3.0            # aktuelle Wartezeit zwischen zwei Versuchen
    attempts = 0             # Fehlversuche in Folge
    # Obergrenze der Wartezeit. Nach einigen Fehlversuchen geht der Agent in
    # einen ruhigen 10-Minuten-Takt ueber. Er gibt dabei NIE auf: Ist das
    # Backend tagelang weg, klopft er weiterhin alle zehn Minuten an und ist
    # binnen zehn Minuten wieder da, sobald es zurueckkommt. Ein Agent, der
    # aufgibt, muesste von Hand angefasst werden - genau das soll ein RMM
    # ersparen.
    BACKOFF_MAX = 600.0

    while True:
        _REGISTERED.clear()
        refused_busy = False
        try:
            if sio.connected:
                try:
                    await sio.disconnect()
                except Exception:
                    pass
            _print(f"[agent] Verbinde zu {BACKEND_URL} (Namespace /agent)...")
            await sio.connect(BACKEND_URL, auth={"token": AGENT_TOKEN},
                              namespaces=["/agent"], wait_timeout=30)
            backoff = 3.0    # Verbindung stand -> Backoff zurücksetzen
            attempts = 0
            await sio.wait()  # blockiert, solange die Verbindung steht
            _print(f"[agent] {_at('log_disconnected')}")
        except Exception as e:
            # Der Server weist uns ab, weil er gerade andere Agenten aufnimmt.
            # Das ist KEIN Fehler, sondern die Warteschlange bei der Arbeit -
            # entsprechend ruhig protokollieren und in Ruhe erneut versuchen.
            refused_busy = "busy" in repr(e).lower()
            if refused_busy:
                _print("[agent] Server nimmt gerade andere Agenten auf - warte und versuche es erneut")
            else:
                _print(f"[agent] {_at('log_conn_failed', url=BACKEND_URL, err=repr(e))}")
        finally:
            _REGISTERED.clear()

        # Immer sauber trennen, bevor der nächste Versuch startet.
        try:
            await sio.disconnect()
        except Exception:
            pass

        # Wartezeit vor dem nächsten Versuch: schrittweise länger, plus ein
        # Zufallsanteil. Ohne den Zufallsanteil laufen abgewiesene Agenten
        # synchron und stürmen den Server im Gleichtakt erneut ("Thundering
        # Herd") - genau das Verhalten, das den Container umgebracht hat.
        attempts += 1
        wait_s = min(BACKOFF_MAX, backoff) + random.uniform(0, min(30.0, backoff))
        if attempts in (5, 20) or attempts % 50 == 0:
            # Nicht jede Runde protokollieren - sonst laeuft bei einem
            # laengeren Ausfall das Log voll und verdeckt das Wesentliche.
            _print(f"[agent] {attempts} Verbindungsversuche erfolglos, "
                   f"naechster in {wait_s / 60:.1f} min - der Agent gibt nicht auf")
        await asyncio.sleep(wait_s)
        backoff = min(BACKOFF_MAX, backoff * 2 if refused_busy else backoff * 1.5)


# ==========================================================================
# SOFTWARE-PATCHING  (Protokoll 2)
# --------------------------------------------------------------------------
# Leitgedanke dieser Fassung: Der Agent sagt von sich aus, was er kann und
# was er tut. Die vorige Fassung schwieg im Fehlerfall - jede Quelle, die
# nicht ansprang, lieferte einfach eine leere Liste zurück. Ob "keine
# Updates vorhanden" oder "winget gar nicht erst gestartet" gemeint war,
# liess sich von aussen nicht unterscheiden. Deshalb jetzt:
#
#   * Jede Quelle liefert ein Ergebnis MIT Zustand (ok / Fehlertext), nie
#     nur eine leere Liste.
#   * Der Agent meldet seine Fähigkeiten schon beim Anmelden mit
#     (PATCH_PROTOCOL im 'register'), nicht erst auf Nachfrage. Das Backend
#     weiss dadurch VOR jedem Auftrag, ob der Client überhaupt patchen kann.
#   * Jeder Handler antwortet IMMER - auch wenn er selbst scheitert.
#     Ein stiller Handler ist von aussen nicht von einem fehlenden zu
#     unterscheiden, und genau diese Verwechslung hat zuletzt viel Zeit
#     gekostet.
#   * Während eines Scans laufend Fortschrittsmeldungen, damit niemand
#     minutenlang auf einen stehenden Balken schaut.
#
# Quellen: Windows Update (COM-API), winget, apt, dnf.
# Stufen:  security | critical | important | moderate | low | feature | other
# ==========================================================================

# Fähigkeitsstufe dieses Agenten. Wird beim Anmelden mitgeschickt; das
# Backend entscheidet daran, ob es einen Patch-Auftrag überhaupt schickt.
#   1 = erste Fassung (patch-scan/patch-apply, kein patch-ping)
#   2 = diese Fassung (Selbstauskunft, Fortschritt, Fehler je Quelle)
PATCH_PROTOCOL = 2

PATCH_EVENTS = ("patch-ping", "patch-scan", "patch-apply", "patch-selftest")

_PATCH_LEVELS = ("security", "critical", "important", "moderate",
                 "low", "feature", "other")


def _patch_log(msg: str) -> None:
    _print(f"[patch] {msg}")


# --------------------------------------------------------------------------
# Werkzeuge
# --------------------------------------------------------------------------

def _winget_exe() -> "str | None":
    """
    Pfad zu winget.exe.

    Der Agent läuft als Dienst unter SYSTEM. Dort liegt winget NICHT im PATH:
    die App-Execution-Aliase unter %LOCALAPPDATA%\\Microsoft\\WindowsApps gibt
    es nur für angemeldete Benutzer. Ein blosses "winget" endete deshalb im
    FileNotFoundError - der früher stillschweigend zu "keine Updates" wurde.
    """
    if not IS_WINDOWS:
        return None
    found = shutil.which("winget")
    if found:
        return found
    base = Path(os.environ.get("ProgramW6432") or r"C:\Program Files") / "WindowsApps"
    try:
        for pattern in ("Microsoft.DesktopAppInstaller_*_x64__*/winget.exe",
                        "Microsoft.DesktopAppInstaller_*/winget.exe"):
            for cand in sorted(base.glob(pattern), reverse=True):
                if cand.exists():
                    return str(cand)
    except Exception:
        pass
    return None


def _source(name: str, ok: bool, patches=None, error: str = "",
            note: str = "") -> dict:
    """
    Einheitliches Ergebnis EINER Quelle.

    'ok=False' heisst: die Quelle konnte nicht befragt werden. Das ist
    ausdrücklich etwas anderes als 'ok=True' mit leerer Liste ("befragt,
    nichts gefunden"). Genau diese Unterscheidung fehlte vorher.
    """
    return {"source": name, "ok": bool(ok), "patches": patches or [],
            "count": len(patches or []), "error": error[:500], "note": note[:300]}


def _is_admin() -> bool:
    """Läuft der Agent mit den Rechten, die zum Installieren nötig sind?"""
    try:
        if IS_WINDOWS:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Windows Update (COM)
# --------------------------------------------------------------------------
# Über die COM-API statt über das Modul PSWindowsUpdate: das ist auf den
# meisten Systemen nicht installiert, und eine Ferninstallation richtet mehr
# Schaden an als sie hilft.

_WU_SEARCH_PS = r"""
$ErrorActionPreference = 'Stop'
try {
  $session  = New-Object -ComObject Microsoft.Update.Session
  $searcher = $session.CreateUpdateSearcher()
  $result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
  $out = @()
  foreach ($u in $result.Updates) {
    $cats = @()
    foreach ($c in $u.Categories) { $cats += $c.Name }
    $kb = @()
    foreach ($k in $u.KBArticleIDs) { $kb += "KB$k" }
    $out += [PSCustomObject]@{
      id        = $u.Identity.UpdateID
      name      = $u.Title
      version   = ($kb -join ',')
      severity  = [string]$u.MsrcSeverity
      categories= ($cats -join ',')
      size      = [int64]$u.MaxDownloadSize
      reboot    = [int]$u.InstallationBehavior.RebootBehavior
    }
  }
  # Immer ein Array ausgeben: ohne den Komma-Operator macht PowerShell aus
  # einem einzelnen Element ein Objekt und aus null Elementen gar nichts.
  ConvertTo-Json -InputObject @($out) -Compress -Depth 3
} catch {
  Write-Output ('{"__error__":"' + ($_.Exception.Message -replace '"','') + '"}')
}
"""

_WU_INSTALL_PS = r"""
$ErrorActionPreference = 'Stop'
$wanted = '__IDS__'.Split(',') | Where-Object { $_ -ne '' }
try {
  $session  = New-Object -ComObject Microsoft.Update.Session
  $searcher = $session.CreateUpdateSearcher()
  $result   = $searcher.Search("IsInstalled=0 and IsHidden=0")
  $coll = New-Object -ComObject Microsoft.Update.UpdateColl
  foreach ($u in $result.Updates) {
    if ($wanted -contains $u.Identity.UpdateID) {
      if (-not $u.EulaAccepted) { $u.AcceptEula() | Out-Null }
      $coll.Add($u) | Out-Null
    }
  }
  if ($coll.Count -eq 0) {
    Write-Output '{"installed":0,"reboot":false,"code":0,"note":"nichts passendes gefunden"}'
    exit
  }
  $dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll; $dl.Download() | Out-Null
  $inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll
  $r = $inst.Install()
  $reboot = [bool]$r.RebootRequired
  Write-Output ('{"installed":' + $coll.Count + ',"reboot":' + $reboot.ToString().ToLower() + ',"code":' + $r.ResultCode + '}')
} catch {
  Write-Output ('{"__error__":"' + ($_.Exception.Message -replace '"','') + '"}')
}
"""


def _ps(script: str, timeout: int = 600):
    """
    PowerShell ausführen.

    Die Ausgabekodierung wird ausdrücklich auf UTF-8 gestellt. Ohne das
    liefert PowerShell die Konsolen-Codepage (deutsch: cp850) - Update-Titel
    mit Umlauten kamen als Buchstabensalat an oder brachten das Einlesen
    ganz zum Scheitern.
    """
    prelude = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    return _run(["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", prelude + script],
                timeout=timeout)


def _level_from_windows(severity: str, categories: str) -> str:
    """
    Schweregrad einer Windows-Aktualisierung bestimmen.

    Die Kategorie hat Vorrang vor MsrcSeverity: Microsoft lässt den
    Schweregrad bei vielen Sicherheitsupdates leer. Wer nur nach
    MsrcSeverity filtert, verpasst genau die Updates, die er wollte.
    """
    cats = (categories or "").lower()
    sev = (severity or "").strip().lower()
    if "security" in cats:
        return "security"
    if "critical" in cats:
        return "critical"
    if sev in ("critical", "important", "moderate", "low"):
        return sev
    if "definition" in cats:
        return "security"          # Virendefinitionen: sicherheitsrelevant
    if "driver" in cats:
        return "other"
    if "feature pack" in cats or "upgrade" in cats:
        return "feature"
    return "other"


def _scan_windows_os() -> dict:
    if not IS_WINDOWS:
        return _source("windows-update", True, note="kein Windows")
    try:
        res = _ps(_WU_SEARCH_PS, timeout=420)
    except subprocess.TimeoutExpired:
        return _source("windows-update", False,
                       error="Zeitüberschreitung bei der Windows-Update-Abfrage (>7 min)")
    except Exception as e:
        return _source("windows-update", False, error=f"{e.__class__.__name__}: {e}")

    raw = (res.stdout or "").strip()
    if not raw:
        err = (res.stderr or "").strip()[:400]
        if err:
            return _source("windows-update", False,
                           error=f"PowerShell meldete: {err}")
        return _source("windows-update", True, note="keine Ausgabe - nichts gefunden")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _source("windows-update", False,
                       error=f"Antwort war kein JSON: {raw[:200]}")

    if isinstance(data, dict):
        if data.get("__error__"):
            return _source("windows-update", False, error=str(data["__error__"]))
        data = [data]

    out = []
    for u in data or []:
        uid = str(u.get("id") or "").strip()
        if not uid:
            continue
        out.append({
            "uid": uid,
            "name": u.get("name") or "Unbenannte Aktualisierung",
            "current_version": "",
            "available_version": u.get("version") or "",
            "source": "windows-update",
            "level": _level_from_windows(u.get("severity"), u.get("categories")),
            "size": int(u.get("size") or 0),
            # RebootBehavior: 0 = nie, 1 = immer, 2 = kann sein
            "needs_reboot": 1 if int(u.get("reboot") or 0) else 0,
        })
    return _source("windows-update", True, out)


# --------------------------------------------------------------------------
# winget
# --------------------------------------------------------------------------

def _winget_columns(header: str) -> "dict | None":
    """
    Spaltenanfänge aus der Kopfzeile ableiten - ohne die Spalten beim Namen
    zu nennen.

    Der frühere Weg suchte die Wörter "Id" und "Available". Auf einem
    deutschen Windows heissen die Spalten "Kennung" und "Verfügbar", auf
    einem französischen wieder anders: die Kopfzeile wurde nie erkannt und
    der Scan meldete grundsätzlich null Anwendungsupdates.

    Womit man rechnen kann, ist die Formatierung: winget trennt seine
    Spalten immer durch MINDESTENS zwei Leerzeichen und richtet die Werte
    darunter linksbündig an derselben Position aus. Wir lesen also nur die
    Startspalten ab und ignorieren, was in der Überschrift steht.

    Spaltenfolge bei winget stabil: Name, Kennung, Version, Verfügbar, Quelle.
    """
    starts = [m.start() for m in re.finditer(r"(?<=\s)\S", "  " + header)]
    # "  " vorangestellt, damit auch die erste Spalte erkannt wird; die
    # Verschiebung um zwei Zeichen wieder herausrechnen.
    starts = sorted({max(0, s - 2) for s in starts})
    # Nur echte Spaltenanfänge behalten: solche, vor denen zwei Leerzeichen
    # stehen (innerhalb einer Überschrift wie "Verfügbare Version" steht nur
    # eines).
    cols = [0] + [s for s in starts if s >= 2 and header[s - 2:s] == "  "]
    cols = sorted(set(cols))
    if len(cols) < 4:
        return None
    return {"name": cols[0], "id": cols[1], "cur": cols[2],
            "avail": cols[3], "end": cols[4] if len(cols) > 4 else None}


def _scan_winget() -> dict:
    if not IS_WINDOWS:
        return _source("winget", True, note="kein Windows")
    exe = _winget_exe()
    if not exe:
        return _source("winget", False, error=(
            "winget was not found. Under the SYSTEM service account it is not "
            "on the PATH; the 'App Installer' package may also be missing."))
    try:
        res = _run([exe, "upgrade", "--include-unknown",
                    "--accept-source-agreements", "--disable-interactivity"],
                   timeout=240)
    except subprocess.TimeoutExpired:
        return _source("winget", False, error="Zeitüberschreitung bei winget (>4 min)")
    except Exception as e:
        return _source("winget", False, error=f"{e.__class__.__name__}: {e}")

    lines = [l.rstrip() for l in (res.stdout or "").splitlines()]
    # Die Trennzeile dient nur dazu, die Kopfzeile zu FINDEN: sie besteht in
    # jeder Sprache aus Bindestrichen. Die Spaltenpositionen kommen dann aus
    # der Zeile darüber.
    sep_idx = next((i for i, l in enumerate(lines)
                    if l.strip() and set(l.strip()) == {"-"}
                    and len(l.strip()) > 20), None)
    if sep_idx is None or sep_idx == 0:
        # Kein Tabellenkopf: entweder wirklich nichts zu tun, oder winget hat
        # sich beschwert. Beides unterscheidbar machen statt zu schweigen.
        text = " ".join(l.strip() for l in lines if l.strip())
        if res.returncode != 0:
            return _source("winget", False,
                           error=f"winget endete mit Code {res.returncode}: {text[:300]}")
        return _source("winget", True,
                       note="keine Tabelle in der Ausgabe - nichts zu aktualisieren")

    cols = _winget_columns(lines[sep_idx - 1])
    if not cols:
        return _source("winget", False, error=(
            "Could not detect the column layout of the winget table. Header: "
            f"{lines[sep_idx - 1][:160]}"))

    out = []
    for line in lines[sep_idx + 1:]:
        if not line.strip() or set(line.strip()) <= {"-", " "}:
            continue
        name = line[cols["name"]:cols["id"]].strip()
        pkg = line[cols["id"]:cols["cur"]].strip()
        cur = line[cols["cur"]:cols["avail"]].strip()
        avail_field = line[cols["avail"]:cols["end"]] if cols["end"] else line[cols["avail"]:]
        avail = avail_field.strip().split(" ")[0] if avail_field.strip() else ""
        # Fusszeilen ("2 Upgrades verfügbar.") laufen quer über die Spalten:
        # in der Kennung-Spalte stünde dann Text mit Leerzeichen.
        if not pkg or not name or " " in pkg:
            continue
        if not avail or avail.lower() in ("unknown", "unbekannt"):
            continue
        out.append({
            "uid": pkg, "name": name,
            "current_version": cur, "available_version": avail,
            "source": "winget",
            # winget kennt keine Schweregrade. Als 'security' zu raten wäre
            # gefährlich - dann würden Sicherheitsregeln beliebige Apps
            # mitaktualisieren.
            "level": "other",
            "size": 0, "needs_reboot": 0,
        })
    return _source("winget", True, out)


# --------------------------------------------------------------------------
# apt / dnf
# --------------------------------------------------------------------------

def _has(binary: str) -> bool:
    return bool(shutil.which(binary))


def _scan_apt() -> dict:
    """
    LC_ALL=C erzwingt englische Ausgabe - sonst heisst es auf deutschen
    Systemen "aktualisierbar von:" statt "upgradable from:". Beide
    Schreibweisen werden trotzdem erkannt, falls die Locale nicht greift.
    """
    env = {**os.environ, "LC_ALL": "C", "LANG": "C",
           "DEBIAN_FRONTEND": "noninteractive"}
    note = ""
    try:
        upd = _run(["apt-get", "update", "-qq"], timeout=240, env=env)
        if upd.returncode != 0:
            # Kein Abbruch: die Paketliste kann veraltet, aber brauchbar sein.
            note = f"apt-get update meldete Code {upd.returncode}"
    except Exception as e:
        note = f"apt-get update übersprungen ({e})"
    try:
        res = _run(["apt", "list", "--upgradable"], timeout=180, env=env)
    except Exception as e:
        return _source("apt", False, error=f"{e.__class__.__name__}: {e}")

    pattern = re.compile(
        r"^(?P<pkg>[^/\s]+)/(?P<repo>\S+)\s+(?P<avail>\S+)\s+\S+"
        r"\s+\[(?:upgradable from|aktualisierbar von):\s*(?P<cur>[^\]]+)\]")
    out = []
    for line in (res.stdout or "").splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        out.append({
            "uid": m.group("pkg"), "name": m.group("pkg"),
            "current_version": m.group("cur").strip(),
            "available_version": m.group("avail"),
            "source": "apt",
            # Debian/Ubuntu kennzeichnen Sicherheitsaktualisierungen über die
            # Quelle ("...-security"). Das ist die einzige verlässliche Angabe.
            "level": "security" if "security" in m.group("repo").lower() else "other",
            "size": 0, "needs_reboot": 0,
        })
    return _source("apt", True, out, note=note)


def _scan_dnf() -> dict:
    try:
        # check-update endet planmässig mit Code 100, wenn es Updates gibt.
        res = _run(["dnf", "-q", "check-update"], timeout=240)
    except Exception as e:
        return _source("dnf", False, error=f"{e.__class__.__name__}: {e}")
    if res.returncode not in (0, 100):
        return _source("dnf", False, error=(
            f"dnf check-update endete mit Code {res.returncode}: "
            f"{(res.stderr or '').strip()[:300]}"))

    sec = set()
    try:
        s = _run(["dnf", "-q", "updateinfo", "list", "security"], timeout=240)
        for line in (s.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 3:
                sec.add(parts[-1].rsplit("-", 2)[0])
    except Exception:
        pass

    out = []
    for line in (res.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 3 or line.startswith(("Last metadata", "Obsoleting", "Security")):
            continue
        pkg = parts[0].rsplit(".", 1)[0]
        out.append({
            "uid": pkg, "name": pkg, "current_version": "",
            "available_version": parts[1], "source": "dnf",
            "level": "security" if pkg in sec else "other",
            "size": 0, "needs_reboot": 0,
        })
    return _source("dnf", True, out)


# --------------------------------------------------------------------------
# Selbstauskunft
# --------------------------------------------------------------------------

def _patch_sources() -> list:
    """Welche Quellen kommen auf DIESEM System überhaupt in Frage?"""
    if IS_WINDOWS:
        src = ["windows-update"]
        if _winget_exe():
            src.append("winget")
        return src
    src = []
    if _has("apt"):
        src.append("apt")
    elif _has("dnf"):
        src.append("dnf")
    return src


def _patch_capabilities() -> dict:
    """
    Wozu ist dieser Agent in Sachen Patching in der Lage? Wird beim Anmelden
    und bei jedem 'patch-ping' gemeldet - das Backend muss dadurch nicht mehr
    raten, ob ein Auftrag überhaupt Sinn ergibt.
    """
    return {
        "protocol": PATCH_PROTOCOL,
        "agent_version": AGENT_VERSION,
        "agent_code_hash": AGENT_CODE_HASH,
        "platform": platform.system(),
        "release": OS_RELEASE,
        "sources": _patch_sources(),
        "winget": _winget_exe() or "",
        "elevated": _is_admin(),
        "events": list(PATCH_EVENTS),
    }


# --------------------------------------------------------------------------
# Sammeln und Installieren
# --------------------------------------------------------------------------

def _collect_patches(progress=None) -> dict:
    """
    Alle Quellen befragen (blockierend, läuft im Thread).

    'progress' ist ein Rückruf(stage, detail, done, total) für die
    Fortschrittsmeldungen. Er darf niemals eine Ausnahme nach aussen tragen -
    ein kaputter Fortschrittsbalken darf keinen Scan abbrechen.
    """
    def tell(stage, detail, done, total):
        if not progress:
            return
        try:
            progress(stage, detail, done, total)
        except Exception:
            pass

    scanners = []
    if IS_WINDOWS:
        scanners.append(("windows-update", _scan_windows_os))
        scanners.append(("winget", _scan_winget))
    elif _has("apt"):
        scanners.append(("apt", _scan_apt))
    elif _has("dnf"):
        scanners.append(("dnf", _scan_dnf))

    total = len(scanners)
    sources, patches = [], []
    for idx, (name, fn) in enumerate(scanners):
        tell("scanning", name, idx, total)
        try:
            result = fn()
        except Exception as e:
            result = _source(name, False, error=f"{e.__class__.__name__}: {e}")
        sources.append({k: v for k, v in result.items() if k != "patches"})
        patches += result["patches"]
        state = f"{result['count']}" if result["ok"] else f"FEHLER: {result['error'][:120]}"
        _patch_log(f"{name}: {state}")
        tell("scanned", name, idx + 1, total)

    # Doppelte (gleiche Quelle + gleiche Kennung) entfernen.
    seen, unique = set(), []
    for p in patches:
        key = (p["source"], p["uid"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    return {
        "patches": unique,
        "sources": sources,
        "capabilities": _patch_capabilities(),
        "scanned_at": int(time.time() * 1000),
    }


def _apply_patches(items: list, progress=None) -> dict:
    """
    Ausgewählte Aktualisierungen installieren.

    'items' sind Einträge aus dem letzten Scan ({uid, source, name}). Es wird
    NIE etwas installiert, das nicht ausdrücklich benannt wurde - kein
    "alles aktualisieren" per Fernsteuerung.
    """
    def tell(stage, detail, done, total):
        if not progress:
            return
        try:
            progress(stage, detail, done, total)
        except Exception:
            pass

    installed, failed, reboot = [], [], False
    total = len(items)
    done = 0

    # --- Windows Update: alle in EINEM Durchgang, das ist deutlich schneller
    wu = [i for i in items if i.get("source") == "windows-update"]
    if wu and IS_WINDOWS:
        names = ", ".join(str(i.get("name") or i.get("uid"))[:40] for i in wu[:3])
        tell("installing", f"Windows Update ({len(wu)}): {names}", done, total)
        try:
            ids = ",".join(str(i["uid"]) for i in wu)
            res = _ps(_WU_INSTALL_PS.replace("__IDS__", ids), timeout=5400)
            lines = [l for l in (res.stdout or "").strip().splitlines() if l.strip()]
            data = json.loads(lines[-1]) if lines else {}
            if data.get("__error__"):
                for i in wu:
                    failed.append({"name": i.get("name") or i.get("uid"),
                                   "error": str(data["__error__"])})
            else:
                installed += [i.get("name") or i.get("uid") for i in wu]
                reboot = reboot or bool(data.get("reboot"))
        except Exception as e:
            for i in wu:
                failed.append({"name": i.get("name") or i.get("uid"),
                               "error": f"{e.__class__.__name__}: {e}"})
        done += len(wu)
        tell("installed", f"Windows Update: {len(wu)}", done, total)

    # --- alles Übrige einzeln
    for item in items:
        src = item.get("source")
        uid = item.get("uid")
        name = item.get("name") or uid or "?"
        if src == "windows-update":
            continue
        tell("installing", str(name), done, total)
        try:
            if src == "winget":
                exe = _winget_exe()
                if not exe:
                    raise RuntimeError("winget ist auf diesem Client nicht verfügbar")
                r = _run([exe, "upgrade", "--id", uid, "--exact", "--silent",
                          "--accept-package-agreements", "--accept-source-agreements",
                          "--disable-interactivity"], timeout=3600)
            elif src == "apt":
                env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive",
                       "LC_ALL": "C", "LANG": "C"}
                r = _run(["apt-get", "install", "-y", "--only-upgrade", uid],
                         timeout=3600, env=env)
            elif src == "dnf":
                r = _run(["dnf", "-y", "upgrade", uid], timeout=3600)
            else:
                raise RuntimeError(f"Unbekannte Quelle: {src}")

            if r.returncode == 0:
                installed.append(name)
            else:
                err = (r.stderr or r.stdout or "").strip()[-400:]
                failed.append({"name": name, "error": err or f"Code {r.returncode}"})
        except Exception as e:
            failed.append({"name": name, "error": f"{e.__class__.__name__}: {e}"})
        done += 1
        tell("installed", str(name), done, total)

    if IS_WINDOWS:
        # Ein ausstehender Neustart lässt sich zuverlässig an diesem
        # Registrierungsschlüssel ablesen.
        try:
            chk = _ps("if (Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\"
                      "CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired')"
                      " { 'yes' } else { 'no' }", timeout=60)
            if "yes" in (chk.stdout or ""):
                reboot = True
        except Exception:
            pass
    elif Path("/var/run/reboot-required").exists():
        reboot = True

    return {"installed": installed, "failed": failed, "needs_reboot": reboot}


# --------------------------------------------------------------------------
# Ereignis-Handler
# --------------------------------------------------------------------------
# Grundregel: JEDER Handler antwortet, auch wenn er selbst scheitert. Ein
# stiller Handler ist von aussen nicht von einem fehlenden zu unterscheiden -
# das Backend meldet dann fälschlich "Agent zu alt", obwohl in Wahrheit eine
# Ausnahme geflogen ist.

async def _patch_reply(event: str, payload: dict) -> None:
    try:
        await sio.emit(event, payload, namespace="/agent")
    except Exception as e:
        _patch_log(f"could not send reply '{event}': {e}")


def _patch_progress_sender(request_id: str, kind: str):
    """
    Baut einen Rückruf, der aus dem Arbeits-Thread heraus Fortschritt meldet.
    Die Emission muss zurück auf den Event-Loop - deshalb
    run_coroutine_threadsafe.
    """
    loop = _AGENT_LOOP

    def send(stage: str, detail: str, done: int, total: int) -> None:
        payload = {"id": DEVICE_ID, "requestId": request_id, "kind": kind,
                   "stage": stage, "detail": detail, "done": done, "total": total}
        try:
            asyncio.run_coroutine_threadsafe(
                sio.emit("patch-progress", payload, namespace="/agent"), loop)
        except Exception:
            pass
    return send


@sio.on("patch-ping", namespace="/agent")
async def on_patch_ping(data):
    """Selbstauskunft. Antwortet sofort, ohne irgendetwas zu starten."""
    request_id = (data or {}).get("requestId")
    _patch_log("capability query received")
    try:
        caps = _patch_capabilities()
    except Exception as e:
        caps = {"protocol": PATCH_PROTOCOL, "error": f"{e.__class__.__name__}: {e}"}
    await _patch_reply("patch-ping-result",
                       {"requestId": request_id, "ok": True, **caps})


@sio.on("patch-selftest", namespace="/agent")
async def on_patch_selftest(data):
    """
    Kurzer Funktionstest OHNE vollständigen Scan: prüft, ob die Quellen
    ansprechbar sind. Dauert Sekunden statt Minuten und beantwortet die
    Frage "liegt es am Agenten oder am System?".
    """
    request_id = (data or {}).get("requestId")
    _patch_log("Selbsttest angefordert")
    checks = []

    def check(name, fn):
        try:
            checks.append({"name": name, **fn()})
        except Exception as e:
            checks.append({"name": name, "ok": False,
                           "detail": f"{e.__class__.__name__}: {e}"})

    if IS_WINDOWS:
        def wu():
            r = _ps("try { $s = New-Object -ComObject Microsoft.Update.Session; "
                    "if ($s) { 'ok' } } catch { 'ERR ' + $_.Exception.Message }",
                    timeout=90)
            txt = (r.stdout or "").strip()
            return {"ok": txt.startswith("ok"), "detail": txt[:300] or "keine Ausgabe"}
        check("Windows-Update-COM", wu)

        def wg():
            exe = _winget_exe()
            if not exe:
                return {"ok": False, "detail": "winget.exe nicht gefunden"}
            r = _run([exe, "--version"], timeout=60)
            return {"ok": r.returncode == 0,
                    "detail": (r.stdout or r.stderr or "").strip()[:200] or exe}
        check("winget", wg)
    else:
        for tool in ("apt", "dnf"):
            if _has(tool):
                check(tool, lambda t=tool: {
                    "ok": True, "detail": shutil.which(t) or t})

    check("Rechte", lambda: {"ok": _is_admin(),
                             "detail": "elevated" if _is_admin() else
                                       "NOT elevated - installing will fail"})

    await _patch_reply("patch-selftest-result", {
        "requestId": request_id, "ok": True,
        "capabilities": _patch_capabilities(), "checks": checks})


@sio.on("patch-scan", namespace="/agent")
async def on_patch_scan(data):
    """Nach verfügbaren Aktualisierungen suchen und melden."""
    request_id = (data or {}).get("requestId")
    _patch_log("Suche nach Aktualisierungen gestartet")
    progress = _patch_progress_sender(request_id, "scan")
    try:
        result = await _AGENT_LOOP.run_in_executor(
            None, functools.partial(_collect_patches, progress))
        _patch_log(f"Suche beendet: {len(result['patches'])} Aktualisierung(en)")
        await _patch_reply("patch-scan-result", {"requestId": request_id, **result})
    except Exception as e:
        _patch_log(f"Suche fehlgeschlagen: {e.__class__.__name__}: {e}")
        await _patch_reply("patch-scan-result", {
            "requestId": request_id,
            "error": f"{e.__class__.__name__}: {e}",
            "capabilities": _patch_capabilities()})


@sio.on("patch-apply", namespace="/agent")
async def on_patch_apply(data):
    """Benannte Aktualisierungen installieren."""
    request_id = (data or {}).get("requestId")
    items = (data or {}).get("items") or []
    if not items:
        await _patch_reply("patch-apply-result", {
            "requestId": request_id, "error": "Keine Aktualisierungen angegeben"})
        return

    _patch_log(f"Installiere {len(items)} Aktualisierung(en)")
    await _emit_action_log("patch", "started", f"{len(items)} Aktualisierung(en)")
    progress = _patch_progress_sender(request_id, "apply")
    try:
        result = await _AGENT_LOOP.run_in_executor(
            None, functools.partial(_apply_patches, items, progress))
        note = (f"{len(result['installed'])} installiert, "
                f"{len(result['failed'])} fehlgeschlagen")
        _patch_log(note)
        await _emit_action_log("patch", "finished", note)
        await _patch_reply("patch-apply-result", {"requestId": request_id, **result})
    except Exception as e:
        msg = f"{e.__class__.__name__}: {e}"
        _patch_log(f"Installation fehlgeschlagen: {msg}")
        await _emit_action_log("patch", "failed", msg)
        await _patch_reply("patch-apply-result", {"requestId": request_id, "error": msg})


# Beim Laden einmal ausgeben, WELCHE Patch-Ereignisse dieser Agent kennt.
# Damit steht in der Agent-Konsole schwarz auf weiss, ob die neue Fassung
# wirklich läuft - genau die Frage, die sich aus der Ferne sonst nicht
# beantworten lässt.
_patch_log(f"Modul geladen - Protokoll {PATCH_PROTOCOL}, "
           f"Ereignisse: {', '.join(PATCH_EVENTS)}")


if __name__ == "__main__":
    # Helfer-Modus: Der Agent hat sich selbst in der Benutzersitzung gestartet,
    # um dort den Bildschirm aufzunehmen (siehe _ScreenHelper). In diesem Modus
    # wird KEINE Verbindung zum Backend aufgebaut - der Helfer redet nur mit
    # dem Haupt-Agenten auf 127.0.0.1.
    # WICHTIG: auf dem OBEN gesetzten Loop laufen (nicht asyncio.run(), das einen
    # neuen Loop anlegen würde) -> derselbe Loop, auf dem 'sio' erstellt wurde.
    asyncio.set_event_loop(_AGENT_LOOP)
    try:
        _AGENT_LOOP.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except BaseException:
        # CRASH-SCHUTZ: Traceback in last_crash.txt sichern (wird beim nächsten
        # erfolgreichen Registrieren ans Backend gemeldet und dem Nutzer
        # angezeigt), kurz warten und den Agenten NEU STARTEN.
        import traceback
        tb = traceback.format_exc()
        try:
            _crash_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_crash.txt")
            with open(_crash_file, "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + tb)
        except OSError:
            pass
        # NEUSTART-SCHUTZ: Stuerzt der Agent immer wieder SOFORT ab, wuerde
        # ein 5-Sekunden-Takt die Maschine dauerhaft belasten und das Log
        # zumuellen. Deshalb wird mitgezaehlt, wie oft kurz hintereinander
        # neu gestartet wurde, und die Wartezeit waechst bis auf 10 Minuten.
        # Aufgegeben wird trotzdem NIE - der Agent kommt immer wieder.
        _restart_state = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "restart_count.txt")
        _count, _last = 0, 0.0
        try:
            with open(_restart_state, "r", encoding="utf-8") as f:
                _parts = f.read().split(",")
                _count, _last = int(_parts[0]), float(_parts[1])
        except (OSError, ValueError, IndexError):
            pass
        # Laeuft der Agent laenger als 10 Minuten stabil, zaehlt der naechste
        # Absturz wieder als Einzelfall.
        _count = _count + 1 if (time.time() - _last) < 600 else 1
        try:
            with open(_restart_state, "w", encoding="utf-8") as f:
                f.write(f"{_count},{time.time()}")
        except OSError:
            pass
        _wait = min(5 * (2 ** min(_count - 1, 7)), 600)
        _print(f"[agent] ABSTURZ Nr. {_count} - Neustart in {_wait} Sekunden:\n" + tb)
        try:
            time.sleep(_wait)
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
    finally:
        try:
            _AGENT_LOOP.close()
        except Exception:
            pass


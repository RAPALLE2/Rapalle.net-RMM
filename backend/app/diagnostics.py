"""
diagnostics.py
--------------
Wartungsmodus: alles mitschreiben, damit ein Absturz danach erklärbar ist.

Warum dieses Modul überhaupt
----------------------------
Ein Absturz, der nur "irgendwann nach kurzer Zeit" passiert, ist fast nie
an der Stelle zu finden, an der er auffällt. Die üblichen Ursachen sind
schleichend: Speicher, der nicht mehr freigegeben wird; Dateideskriptoren,
die offen bleiben; Hintergrundaufgaben, die still sterben; Ausnahmen in
Threads, die niemand sieht. Nichts davon steht in einer gewöhnlichen
Fehlermeldung.

Deshalb schreibt der Wartungsmodus drei Dinge mit:

  1. ALLES an Ausgabe - stdout, stderr, das logging-Modul, Ausnahmen aus
     Threads und aus asyncio-Aufgaben. Auch das, was sonst still
     verschluckt wird.
  2. Regelmässige Messwerte: Speicher, offene Dateideskriptoren, Threads,
     laufende asyncio-Aufgaben, Socket.IO-Verbindungen. Eine Kurve, die
     stetig steigt, zeigt das Leck deutlicher als jeder Stacktrace.
  3. Die Berichte der Agenten, damit beide Seiten in EINER Zeitachse
     liegen.

Dazu kommt faulthandler: Stirbt der Prozess hart (Speicherzugriffsfehler,
vom Kernel beendet), schreibt er noch die Stacktraces aller Threads weg.
Genau diese Fälle hinterlassen sonst gar nichts.

Der Modus ist bewusst NICHT dauerhaft an: Er kostet Platz und etwas
Rechenzeit. Er läuft, bis er abgeschaltet wird oder die eingestellte Zeit
abgelaufen ist - so bleibt eine vergessene Sitzung nicht ewig aktiv.
"""

from __future__ import annotations

import asyncio
import faulthandler
import io
import json
import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

LOG_DIR = Path(os.getenv("RMM_LOG_DIR", "")
               or Path(__file__).resolve().parents[1] / "logs")
MAIN_LOG = "backend.log"
FAULT_LOG = "backend-fault.log"
AGENT_LOG = "agents.log"
BLOCK_LOG = "backend-blockaden.log"

# Wie viele Zeilen im Arbeitsspeicher für die Live-Ansicht vorgehalten werden.
RING_SIZE = 4000
# Grösse, ab der eine Logdatei umgebrochen wird, und wie viele Altstände
# aufgehoben werden. 20 MB reichen für viele Stunden, ohne die Platte zu
# füllen.
MAX_BYTES = 20 * 1024 * 1024
BACKUPS = 3
# Abstand der Messpunkte.
SAMPLE_SECONDS = 15


class _State:
    def __init__(self):
        self.active = False
        self.until = 0.0            # 0 = ohne Ende
        self.started = 0.0
        self.ring: deque = deque(maxlen=RING_SIZE)
        self.lock = threading.Lock()
        self.samples: deque = deque(maxlen=2000)
        self.fault_file = None
        self.installed = False
        self.orig_stdout = None
        self.orig_stderr = None
        self.counters = {"errors": 0, "warnings": 0, "agent_errors": 0}


st = _State()

# Wiederverwendete psutil-Instanz (siehe sample()).
_proc = None


# ----------------------------------------------------------------------
# Schreiben
# ----------------------------------------------------------------------

def _ensure_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _rotate(path: Path) -> None:
    """Einfacher Umbruch - kein logging.handlers, weil hier auch roher
    stdout-Text ankommt, der nie durch das logging-Modul läuft."""
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            for i in range(BACKUPS - 1, 0, -1):
                older, newer = path.with_suffix(f".{i}"), path.with_suffix(f".{i+1}")
                if older.exists():
                    older.replace(newer)
            path.replace(path.with_suffix(".1"))
    except OSError:
        pass


def write(line: str, target: str = MAIN_LOG, level: str = "INFO",
          to_console: bool = False, from_stream: bool = False) -> None:
    """
    Eine Zeile in Ring und Datei. Darf NIEMALS werfen.

    Das ist keine Übervorsicht: Diese Funktion hängt an stdout. Wirft sie,
    reisst sie jeden print()-Aufruf im ganzen Programm mit - also genau
    dann, wenn man die Ausgabe am dringendsten braucht.

    'to_console' schreibt zusätzlich in die echte Container-Ausgabe.

    Warum das nötig wurde: Bis eben landete ALLES nur im Ringpuffer und in
    der Logdatei. Im 'docker logs' stand deshalb keine einzige Zeile der
    Diagnose - weder dass sie überhaupt eingehängt ist, noch eine gemeldete
    Blockade. Bei einem Container, an dessen Logdateien man gerade nicht
    herankommt, ist das die eine Ausgabe, die man wirklich braucht. Wichtige
    Meldungen gehen deshalb zusätzlich nach stdout.
    """
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"{stamp} [{level}] {line.rstrip()}"
        with st.lock:
            st.ring.append(entry)
            if level == "ERROR":
                st.counters["errors"] += 1
            elif level in ("WARN", "WARNING"):
                st.counters["warnings"] += 1
        # Direkt auf das ORIGINAL schreiben, nicht über sys.stdout: Letzteres
        # ist unser eigener Mitschnitt und würde die Zeile ein zweites Mal
        # durch write() schicken.
        # 'from_stream' bedeutet: Die Zeile kam gerade AUS stdout/stderr und
        # steht dort bereits. Sie ein zweites Mal auszugeben würde jede
        # Container-Ausgabe verdoppeln.
        if not from_stream and (to_console or level in ("ERROR", "WARN", "WARNING")):
            try:
                target_stream = st.orig_stdout or sys.__stdout__
                target_stream.write(entry + "\n")
                target_stream.flush()
            except Exception:
                pass
        if not st.active:
            return
        _ensure_dir()
        path = LOG_DIR / target
        _rotate(path)
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(entry + "\n")
    except Exception:
        pass


# ----------------------------------------------------------------------
# Umleitung von stdout/stderr
# ----------------------------------------------------------------------

class _Tee(io.TextIOBase):
    """
    Schreibt in das Original UND ins Log.

    Bewusst ein Tee und keine Umleitung: Die Container-Ausgabe
    ('docker logs') soll unverändert weiterlaufen. Ein Diagnosewerkzeug,
    das die gewohnte Ausgabe wegnimmt, macht die Fehlersuche schwerer statt
    leichter.
    """

    def __init__(self, original, level: str):
        self.original = original
        self.level = level
        self._buffer = ""

    def write(self, text: str) -> int:
        try:
            self.original.write(text)
        except Exception:
            pass
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                write(line, level=self._level_for(line), from_stream=True)
        return len(text)

    def _level_for(self, line: str) -> str:
        """
        Stufe anhand des Inhalts bestimmen.

        Frueher galt ALLES auf stderr pauschal als Fehler. uvicorn schreibt
        dorthin aber auch gewoehnliche Zugriffszeilen - im letzten Log stand
        deshalb eine harmlose JWT-Warnung als [ERROR], und der Fehlerzaehler
        im Dashboard war entsprechend nutzlos. Jetzt entscheidet der Text.
        """
        if self.level != "ERROR":
            return "INFO"
        low = line.lower()
        if "traceback" in low or "error" in low or "exception" in low:
            return "ERROR"
        if "warn" in low:
            return "WARN"
        return "INFO"

    def flush(self) -> None:
        try:
            self.original.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return self.original.isatty()
        except Exception:
            return False


class _LogHandler(logging.Handler):
    """Bringt auch das logging-Modul (uvicorn, FastAPI) in dieselbe Datei."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            write(f"{record.name}: {record.getMessage()}",
                  level=record.levelname)
            if record.exc_info:
                write("".join(traceback.format_exception(*record.exc_info)),
                      level="ERROR")
        except Exception:
            pass


# ----------------------------------------------------------------------
# Ausnahmen einsammeln, die sonst niemand sieht
# ----------------------------------------------------------------------

def _thread_hook(args) -> None:
    write(f"Unbehandelte Ausnahme im Thread {args.thread.name if args.thread else '?'}:\n"
          + "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                               args.exc_traceback)),
          level="ERROR")


def _sys_hook(exc_type, exc_value, tb) -> None:
    write("Unbehandelte Ausnahme im Hauptthread:\n"
          + "".join(traceback.format_exception(exc_type, exc_value, tb)),
          level="ERROR")
    try:
        sys.__excepthook__(exc_type, exc_value, tb)
    except Exception:
        pass


def _loop_handler(loop, context: dict) -> None:
    """
    Ausnahmen aus asyncio-Aufgaben.

    Ohne diesen Haken meldet Python nur "Task exception was never
    retrieved" - und zwar irgendwann später, wenn der Müllsammler
    vorbeikommt. Der Zeitpunkt hat dann nichts mehr mit der Ursache zu tun.
    """
    exc = context.get("exception")
    message = context.get("message") or "asyncio-Fehler"
    detail = ("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
              if exc else json.dumps({k: str(v) for k, v in context.items()},
                                     ensure_ascii=False))
    write(f"{message}\n{detail}", level="ERROR")


def install() -> None:
    """
    Hängt sich überall ein. Läuft EINMAL beim Start, unabhängig davon, ob
    der Wartungsmodus gerade an ist - der Ring puffert dann trotzdem, und
    beim Einschalten sind die letzten Minuten sofort mit dabei.
    """
    if st.installed:
        return
    st.installed = True
    st.orig_stdout, st.orig_stderr = sys.stdout, sys.stderr
    sys.stdout = _Tee(sys.stdout, "INFO")
    sys.stderr = _Tee(sys.stderr, "ERROR")

    root = logging.getLogger()
    root.addHandler(_LogHandler())
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)

    threading.excepthook = _thread_hook
    sys.excepthook = _sys_hook
    try:
        asyncio.get_event_loop().set_exception_handler(_loop_handler)
    except RuntimeError:
        pass
    write("Diagnose eingehängt (stdout, stderr, logging, Threads, asyncio)", to_console=True)


# ----------------------------------------------------------------------
# Wächter für Blockaden des Event-Loops
# ----------------------------------------------------------------------
# Das ist das Werkzeug für den Fehler vom 22.08.: Der Prozess lebte, hatte
# stabilen Speicher und keine Ausnahme - antwortete aber nicht mehr. Alle
# Agenten flogen gleichzeitig raus, der Docker-Healthcheck lief in sein
# 5-Sekunden-Limit, Cloudflare meldete 524.
#
# So etwas erzeugt KEINEN Stacktrace, weil nichts abstürzt. Es ist eine
# blockierende Operation im Event-Loop-Thread - typischerweise ein fsync
# auf ein langsames Dateisystem, ein Netzaufruf ohne Zeitlimit oder eine
# lange Schleife.
#
# Der Wächter läuft in einem EIGENEN Thread (nicht im Loop - der ist ja
# gerade blockiert) und misst, wie pünktlich der Loop einen Zeitstempel
# aktualisiert. Bleibt der Stempel stehen, schreibt er die Stacktraces
# ALLER Threads weg. Damit steht beim nächsten Mal schwarz auf weiß, in
# welcher Zeile das Backend hängt.

# Ab welcher Verzögerung protokolliert wird.
LOOP_LAG_WARN = 2.0
# Ab wann zusätzlich alle Stacktraces gesichert werden.
LOOP_LAG_DUMP = 5.0
# NOTBREMSE: Ab dieser Verzögerung gilt das Backend als hängend und beendet
# sich selbst, damit die Neustartkette greift. 0 schaltet das ab.
#
# Warum Selbstbeendigung die richtige Antwort ist: Ein Prozess, dessen
# Ereignisschleife minutenlang steht, ist für jeden Benutzer nicht von einem
# abgestürzten zu unterscheiden - nur dass niemand ihn neu startet. Docker
# reagiert auf einen fehlgeschlagenen Healthcheck NICHT mit einem Neustart,
# sondern markiert den Container lediglich als krank. Ein hängendes Backend
# bleibt so beliebig lange hängen. Beendet es sich dagegen selbst, fangen
# run.py bzw. die Neustart-Regel von Docker es auf und es ist nach Sekunden
# wieder da. Kontrolliertes Beenden ist besser als ein Untoter.
LOOP_LAG_KILL = float(os.getenv("RMM_LOOP_KILL_S", "90") or 90)

_loop_beat = {"t": 0.0, "worst": 0.0, "dumps": 0}


async def loop_heartbeat() -> None:
    """Setzt zweimal pro Sekunde einen Zeitstempel. Mehr nicht - genau das
    macht sie zum verlässlichen Massstab: Kommt sie nicht durch, steht der
    Loop."""
    while True:
        _loop_beat["t"] = time.monotonic()
        await asyncio.sleep(0.5)


def _watchdog_thread() -> None:
    """
    Misst von aussen, wie lange der Herzschlag ausbleibt.

    Zwei Zeitsperren, und sie sind ABSICHTLICH getrennt:

      'reported' bremst die Textmeldung, damit eine lange Blockade nicht
      im Sekundentakt dieselbe Zeile schreibt.

      'dumped' bremst den Stack-Abzug - aber pro BLOCKADE, nicht pro Zeit.

    Warum getrennt: Mit einer gemeinsamen Sperre passierte Folgendes. Nach
    zwei Sekunden Verzoegerung wurde gewarnt und die Sperre gesetzt. Als
    die Verzoegerung dann auf sechs Sekunden anwuchs - also genau in dem
    Moment, in dem der Stack-Abzug den Verursacher gezeigt haette -, griff
    die Sperre und der Abzug unterblieb. Gemeldet wurde die Blockade,
    aufgeklaert nie. Genau dieser Fall ist im Test aufgefallen.
    """
    reported = 0.0
    dumped_this_stall = False
    while True:
        time.sleep(1.0)
        last = _loop_beat["t"]
        if not last:
            continue
        lag = time.monotonic() - last
        if lag > _loop_beat["worst"]:
            _loop_beat["worst"] = lag

        if lag < LOOP_LAG_WARN:
            # Die Blockade ist vorbei - fuer die naechste wieder scharf.
            dumped_this_stall = False
            continue

        if time.monotonic() - reported >= 20:
            reported = time.monotonic()
            write(f"EVENT-LOOP BLOCKIERT seit {lag:.1f}s - das Backend "
                  f"antwortet gerade nicht (Healthcheck und Agenten fliegen "
                  f"dabei raus)", level="ERROR")

        # Der Abzug haengt NUR an der Schwere, nicht an der Meldesperre.
        if lag >= LOOP_LAG_DUMP and not dumped_this_stall:
            dumped_this_stall = True
            _dump_all_stacks(lag)

        # Notbremse: So lange steht keine gesunde Schleife jemals still.
        if LOOP_LAG_KILL > 0 and lag >= LOOP_LAG_KILL:
            _emergency_exit(lag)


def _dump_all_stacks(lag: float) -> None:
    """Stacktraces aller Threads - das zeigt die blockierende Zeile."""
    _loop_beat["dumps"] += 1
    try:
        lines = [f"===== STACK-ABZUG (Loop steht {lag:.1f}s) ====="]
        # Zuerst: Welche Anfragen sind GERADE in Bearbeitung? Das ist meist
        # schon die Antwort - blockiert der Server, steht hier die Aktion,
        # die ihn blockiert. Ohne diese Liste muss man sie aus den
        # Stacktraces heraussuchen.
        try:
            from app.main import INFLIGHT
            if INFLIGHT:
                lines.append("--- laufende Anfragen ---")
                now = time.monotonic()
                for path, started in sorted(INFLIGHT.values(),
                                            key=lambda x: x[1]):
                    lines.append(f"  seit {now - started:5.1f}s  {path}")
            else:
                lines.append("--- keine Anfrage in Bearbeitung "
                             "(die Blockade kommt aus einer Hintergrundaufgabe) ---")
        except Exception:
            pass
        frames = sys._current_frames()
        names = {t.ident: t.name for t in threading.enumerate()}
        for tid, frame in frames.items():
            lines.append(f"--- Thread {names.get(tid, '?')} ({tid}) ---")
            lines.extend(l.rstrip() for l in traceback.format_stack(frame))
        text = "\n".join(lines)
        write(text, level="ERROR")
        # Zusätzlich in eine eigene Datei: Diese Abzüge sind das Wertvollste
        # im ganzen Paket und sollen nicht im übrigen Log untergehen.
        # Bewusst UNABHÄNGIG vom Wartungsmodus: Eine Blockade ist so selten
        # und so aussagekräftig, dass sie auch dann festgehalten gehört,
        # wenn gerade niemand mitschreiben lässt. Sonst steht man nach dem
        # Vorfall wieder mit leeren Händen da.
        if True:
            _ensure_dir()
            with open(LOG_DIR / BLOCK_LOG, "a", encoding="utf-8",
                      errors="replace") as f:
                f.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
    except Exception as e:
        write(f"Stack-Abzug fehlgeschlagen: {e}", level="WARN")


def start_watchdog() -> None:
    """Startet den Wächter-Thread. Läuft dauerhaft, nicht nur im Wartungsmodus."""
    t = threading.Thread(target=_watchdog_thread, name="loop-watchdog",
                         daemon=True)
    t.start()
    write(f"Loop-Wächter aktiv (Warnung ab {LOOP_LAG_WARN}s, "
          f"Stack-Abzug ab {LOOP_LAG_DUMP}s)", to_console=True)


# ----------------------------------------------------------------------
# Ein- und Ausschalten
# ----------------------------------------------------------------------

def enable(minutes: int = 0, reason: str = "") -> dict:
    """Schaltet den Wartungsmodus ein. minutes=0 bedeutet ohne Ende."""
    _ensure_dir()
    st.active = True
    st.started = time.time()
    st.until = time.time() + minutes * 60 if minutes and minutes > 0 else 0.0

    # faulthandler schreibt bei einem harten Abbruch die Stacktraces aller
    # Threads. Die Datei bleibt offen - genau deshalb funktioniert es auch
    # dann noch, wenn nichts mehr geordnet abläuft.
    try:
        st.fault_file = open(LOG_DIR / FAULT_LOG, "a", buffering=1,
                             encoding="utf-8", errors="replace")
        faulthandler.enable(file=st.fault_file, all_threads=True)
    except Exception as e:
        write(f"faulthandler nicht aktivierbar: {e}", level="WARN")

    write("=" * 70)
    write(f"WARTUNGSMODUS EIN - Grund: {reason or 'nicht angegeben'}",
          to_console=True)
    write(f"Python {sys.version.split()[0]}, PID {os.getpid()}, "
          f"Verzeichnis {LOG_DIR}")
    write("=" * 70)
    _dump_environment()

    from app import db
    db.set_setting("maintenance_mode", "1")
    db.set_setting("maintenance_until", str(int(st.until * 1000)))
    return status()


def disable() -> dict:
    write("WARTUNGSMODUS AUS", to_console=True)
    st.active = False
    st.until = 0.0
    try:
        faulthandler.disable()
        if st.fault_file:
            st.fault_file.close()
    except Exception:
        pass
    st.fault_file = None
    from app import db
    db.set_setting("maintenance_mode", "0")
    return status()


def status() -> dict:
    files = []
    try:
        for p in sorted(LOG_DIR.glob("*")):
            if p.is_file():
                files.append({"name": p.name, "size": p.stat().st_size,
                              "modified": int(p.stat().st_mtime * 1000)})
    except OSError:
        pass
    return {
        "active": st.active,
        "since": int(st.started * 1000) if st.started else 0,
        "until": int(st.until * 1000) if st.until else 0,
        "dir": str(LOG_DIR),
        "files": files,
        "counters": dict(st.counters),
        # Wie oft die Ereignisschleife stand und wie lange sie im
        # schlimmsten Fall blockiert war. Steht hier etwas > 0, ist das die
        # Spur, der man nachgehen muss.
        "loop_worst_s": round(_loop_beat["worst"], 2),
        "loop_dumps": _loop_beat["dumps"],
        "samples": list(st.samples)[-240:],
        "ring_size": len(st.ring),
    }


def tail(lines: int = 300) -> list[str]:
    with st.lock:
        return list(st.ring)[-max(1, min(lines, RING_SIZE)):]


# ----------------------------------------------------------------------
# Messwerte
# ----------------------------------------------------------------------

def _dump_environment() -> None:
    """Einmalige Momentaufnahme beim Einschalten - Kontext für später."""
    try:
        import platform
        write(f"System: {platform.platform()}")
        write(f"CPUs: {os.cpu_count()}")
        try:
            import resource
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            write(f"Dateideskriptor-Grenze: weich {soft}, hart {hard}")
        except Exception:
            pass
        # Speichergrenze des Containers - der häufigste Grund für ein
        # plötzliches Verschwinden ohne jede Fehlermeldung.
        for path in ("/sys/fs/cgroup/memory.max",
                     "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            try:
                with open(path) as f:
                    write(f"Speichergrenze ({path}): {f.read().strip()}")
                break
            except OSError:
                continue
    except Exception as e:
        write(f"Umgebungsangaben unvollständig: {e}", level="WARN")


def sample() -> dict:
    """Ein Messpunkt. Bewusst billig - er läuft alle paar Sekunden."""
    data = {"t": int(time.time() * 1000)}
    # Verzögerung des Event-Loops - der wichtigste Wert überhaupt. Steht er
    # über einer Sekunde, antwortet das Backend gerade nicht.
    try:
        if _loop_beat["t"]:
            data["lag"] = round(time.monotonic() - _loop_beat["t"], 2)
            data["lag_max"] = round(_loop_beat["worst"], 2)
    except Exception:
        pass
    try:
        import psutil
        # WICHTIG: dieselbe Process-Instanz wiederverwenden. cpu_percent()
        # misst den Verbrauch SEIT DEM LETZTEN AUFRUF auf demselben Objekt -
        # bei einer frisch erzeugten Instanz kommt darum immer 0.0 heraus.
        # Genau deshalb stand im Log vom 22.08. durchgehend "cpu": 0.0, und
        # eine hohe CPU-Last wäre unsichtbar geblieben.
        global _proc
        if _proc is None:
            _proc = psutil.Process()
            _proc.cpu_percent(interval=None)   # Nullpunkt setzen
        proc = _proc
        with proc.oneshot():
            data["rss_mb"] = round(proc.memory_info().rss / 1048576, 1)
            data["cpu"] = round(proc.cpu_percent(interval=None), 1)
            data["threads"] = proc.num_threads()
            try:
                data["fds"] = proc.num_fds()
            except Exception:
                data["fds"] = len(proc.open_files())
            data["conns"] = len(proc.net_connections(kind="inet"))
        data["sys_mem"] = round(psutil.virtual_memory().percent, 1)
    except Exception:
        pass
    try:
        data["tasks"] = len(asyncio.all_tasks())
    except Exception:
        pass
    # Wie viele Anfragen sind gerade in Bearbeitung? Seit die meisten
    # Endpunkte in Arbeits-Threads laufen, ist das die aussagekräftigste
    # Auslastungszahl: Steht sie dauerhaft am Anschlag des Threadpools
    # (Vorgabe 40), warten Anfragen auf einen freien Thread.
    try:
        from app.main import INFLIGHT
        data["inflight"] = len(INFLIGHT)
        if INFLIGHT:
            oldest = min(v[1] for v in INFLIGHT.values())
            data["oldest_req_s"] = round(time.monotonic() - oldest, 1)
    except Exception:
        pass
    try:
        from app.sockets import state as sock_state
        data["agents"] = len(sock_state.client_id_to_sid)
        data["pending"] = len(sock_state.pending_requests)
    except Exception:
        pass
    try:
        from app import vpn
        data["tunnels"] = len(vpn.rt.stacks)
    except Exception:
        pass
    return data


async def sampler_loop() -> None:
    """
    Schreibt regelmässig einen Messpunkt.

    Läuft IMMER, nicht nur im Wartungsmodus - allerdings nur in den
    Ringpuffer. Dadurch liegen nach einem Absturz auch die Minuten davor
    vor, in denen noch niemand an Diagnose gedacht hatte.
    """
    last_warn = 0.0
    while True:
        try:
            point = sample()
            st.samples.append(point)
            if st.active:
                write("MESSWERT " + json.dumps(point, ensure_ascii=False),
                      level="DEBUG")
            # Frühwarnung: Diese beiden Werte sind die üblichen Verdächtigen,
            # wenn ein Prozess "einfach weg" ist.
            now = time.time()
            if now - last_warn > 120:
                if point.get("fds", 0) > 900:
                    write(f"Viele offene Dateideskriptoren: {point['fds']}",
                          level="WARN")
                    last_warn = now
                if point.get("lag", 0) > 1.0:
                    write(f"Event-Loop hinkt {point['lag']}s hinterher - "
                          f"etwas blockiert den Hauptthread", level="WARN")
                    last_warn = now
                if point.get("inflight", 0) >= 35:
                    write(f"Fast alle Arbeits-Threads belegt "
                          f"({point['inflight']} Anfragen gleichzeitig) - "
                          f"weitere Anfragen warten.", level="WARN")
                    last_warn = now
                if point.get("sys_mem", 0) > 92:
                    write(f"Arbeitsspeicher fast voll: {point['sys_mem']} %",
                          level="WARN")
                    last_warn = now
            # Zeitablauf des Wartungsmodus
            if st.active and st.until and time.time() > st.until:
                write("Wartungsmodus abgelaufen - schalte ab")
                disable()
        except Exception as e:
            try:
                write(f"Messschleife: {e}", level="WARN")
            except Exception:
                pass
        await asyncio.sleep(SAMPLE_SECONDS)


# ----------------------------------------------------------------------
# Agenten-Berichte
# ----------------------------------------------------------------------

def agent_log(client_id: str, hostname: str, lines: list) -> None:
    """Nimmt Logzeilen eines Agenten entgegen und legt sie dazu."""
    if not lines:
        return
    for line in lines:
        if not isinstance(line, str):
            line = str(line)
        if "[ERROR]" in line or "Traceback" in line:
            st.counters["agent_errors"] += 1
        write(f"<{hostname or client_id}> {line}", target=AGENT_LOG)


def bundle() -> bytes:
    """
    Packt alle Logdateien in ein ZIP - das ist die Datei, die man weiterreicht.

    Der Ringpuffer kommt mit hinein, auch wenn der Wartungsmodus erst
    gerade eingeschaltet wurde: Dann steht wenigstens die Vorgeschichte
    drin.
    """
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        try:
            for p in sorted(LOG_DIR.glob("*")):
                if p.is_file():
                    z.write(p, p.name)
        except OSError:
            pass
        z.writestr("ringpuffer.txt", "\n".join(tail(RING_SIZE)))
        z.writestr("messwerte.json",
                   json.dumps(list(st.samples), indent=1, ensure_ascii=False))
        z.writestr("status.json", json.dumps(status(), indent=1,
                                             ensure_ascii=False, default=str))
    return buf.getvalue()


def clear() -> None:
    try:
        for p in LOG_DIR.glob("*"):
            if p.is_file():
                p.unlink()
    except OSError:
        pass
    with st.lock:
        st.ring.clear()
    st.samples.clear()
    st.counters = {"errors": 0, "warnings": 0, "agent_errors": 0}
    write("Logs geleert")


def report_previous_shutdown() -> None:
    """
    Sagt beim Start, WARUM der letzte Lauf endete.

    Ohne diese Meldung sieht jeder Neustart gleich aus - ein geplanter wie
    ein Absturz. Genau daran scheiterte bisher die Einordnung: Im Protokoll
    stand nur "Backend startet", nie warum es vorher aufgehört hatte.
    """
    try:
        from app import db
        mark = db.get_setting("last_watchdog_kill", "") or ""
        if mark:
            ts, _, lag = mark.partition("|")
            when = time.strftime("%Y-%m-%d %H:%M:%S",
                                 time.localtime(int(ts) / 1000))
            write(f"VORHERIGER LAUF: Der Wächter hat das Backend am {when} "
                  f"beendet, weil die Ereignisschleife {lag}s stand. Die "
                  f"Ursache steht in backend-blockaden.log.",
                  level="WARN", to_console=True)
            db.set_setting("last_watchdog_kill", "")
        if db.get_setting("backend_crash_pending", "0") == "1":
            write("VORHERIGER LAUF: Das Backend ist abgestürzt. Der Bericht "
                  "steht im Audit-Log unter 'backend.crash'.",
                  level="WARN", to_console=True)
            db.set_setting("backend_crash_pending", "0")
    except Exception as e:
        print(f"[diag] Grund des letzten Endes nicht lesbar: {e}")


def restore_from_settings() -> None:
    """Wartungsmodus nach einem Neustart wieder aufnehmen."""
    try:
        from app import db
        if db.get_setting("maintenance_mode", "0") != "1":
            return
        until = int(db.get_setting("maintenance_until", "0") or 0)
        if until and until / 1000 < time.time():
            db.set_setting("maintenance_mode", "0")
            return
        minutes = int((until / 1000 - time.time()) / 60) if until else 0
        enable(max(minutes, 0), reason="nach Neustart fortgesetzt")
        write("Der Prozess wurde neu gestartet - das kann ein Absturz "
              "gewesen sein. Bitte backend-fault.log prüfen.", level="WARN")
    except Exception as e:
        print(f"[diag] Wartungsmodus nicht fortgesetzt: {e}")


def _emergency_exit(lag: float) -> None:
    """
    Beendet den Prozess hart, damit er neu gestartet wird.

    Ablauf, bewusst in dieser Reihenfolge:
      1. Stacktraces sichern - sonst ist die Ursache nach dem Neustart weg.
      2. Vermerk in die Einstellungen, damit nach dem Neustart im Dashboard
         steht, WARUM neu gestartet wurde.
      3. os._exit() statt sys.exit(): Ein sauberes Beenden müsste durch
         dieselbe Ereignisschleife, die ja gerade steht - es käme nie an.
    """
    try:
        write(f"NOTBREMSE: Die Ereignisschleife steht seit {lag:.0f}s. "
              f"Das Backend beendet sich jetzt selbst, damit es neu "
              f"gestartet wird.", level="ERROR", to_console=True)
        _dump_all_stacks(lag)
    except Exception:
        pass
    try:
        from app import db
        db.set_setting("last_watchdog_kill",
                       f"{int(time.time() * 1000)}|{lag:.0f}")
    except Exception:
        pass
    try:
        if st.fault_file:
            st.fault_file.flush()
        sys.__stdout__.flush()
        sys.__stderr__.flush()
    except Exception:
        pass
    os._exit(75)     # 75 = EX_TEMPFAIL: "nochmal versuchen"

"""
errors.py
=========
Ein Fehler darf nie spurlos verschwinden - und nie den Server mitnehmen.

Warum dieses Modul
------------------
Die bisherige Fehlerbehandlung war ueber das ganze Backend verstreut: mal
ein try/except mit print(), mal gar keines, mal ein stiller `except:
pass`. Nach einem Vorfall stand deshalb im Protokoll entweder nichts oder
etwas, mit dem man nichts anfangen konnte.

Hier gibt es genau eine Stelle, die Fehler aufnimmt, und sie garantiert
drei Dinge:

  1. JEDER Fehler bekommt einen KENNCODE der Form RMM-BEREICH-NNN. Danach
     laesst sich im Protokoll suchen, und man weiss sofort, welcher Teil des
     Systems betroffen ist - ohne den Stacktrace lesen zu muessen.

  2. JEDER Fehler landet SOFORT in der Container-Ausgabe ('docker logs').
     Das ist der entscheidende Punkt: Die Diagnose in den Einstellungen
     nuetzt nichts, wenn das Backend im Moment des Fehlers schon nicht mehr
     antwortet. Die Container-Ausgabe ueberlebt das - sie geht direkt an
     Docker, nicht durch unseren eigenen Prozess.

  3. Zu jedem Fehler steht KONTEXT dabei: was gerade versucht wurde, mit
     welchen Werten, und ein Hinweis, was zu tun ist. "KeyError: 'id'" ist
     keine Fehlermeldung, sondern ein Raetsel.

Benutzung
---------
    from app.errors import guard, report, Codes

    # a) Als Kontext: der Block darf scheitern, ohne alles mitzureissen
    with guard(Codes.DB_QUERY, "Metrikverlauf lesen", client=client_id):
        rows = db.get_metrics_history(client_id)

    # b) Nur melden
    report(Codes.VPN_TUNNEL, exc, "Tunnel anlegen", client=client_id)

    # c) Als Dekorator fuer Hintergrundarbeit
    @guarded(Codes.TASK_LOOP, "Patch-Engine")
    async def engine(): ...
"""

from __future__ import annotations

import functools
import sys
import time
import traceback
from contextlib import contextmanager


class Codes:
    """
    Kenncodes nach Bereichen.

    Die Nummern sind bewusst fest vergeben und werden NICHT neu sortiert -
    ein Code in einem alten Protokoll soll auch in einem Jahr noch dasselbe
    bedeuten.
    """

    # --- Start und Grundbetrieb ---
    BOOT = "RMM-BOOT-001"            # Fehler beim Hochfahren
    BOOT_ROUTER = "RMM-BOOT-002"     # Ein Router liess sich nicht laden
    SHUTDOWN = "RMM-BOOT-003"        # Fehler beim Herunterfahren

    # --- Datenbank ---
    DB_QUERY = "RMM-DB-001"          # Abfrage fehlgeschlagen
    DB_SLOW = "RMM-DB-002"           # Abfrage dauert zu lange
    DB_ABORT = "RMM-DB-003"          # Abfrage wurde abgebrochen (Zeitlimit)
    DB_LOCKED = "RMM-DB-004"         # Datenbank gesperrt
    DB_MIGRATE = "RMM-DB-005"        # Migration fehlgeschlagen
    DB_HUGE = "RMM-DB-006"           # Tabelle ist gefaehrlich gross

    # --- HTTP ---
    API_UNHANDLED = "RMM-API-001"    # Unbehandelte Ausnahme in einer Anfrage
    API_SLOW = "RMM-API-002"         # Anfrage dauert zu lange
    API_VALIDATION = "RMM-API-003"   # Eingabe unbrauchbar

    # --- Socket.IO / Agenten ---
    SOCK_HANDLER = "RMM-SOCK-001"    # Ereignis-Behandlung fehlgeschlagen
    SOCK_REGISTER = "RMM-SOCK-002"   # Anmeldung eines Agenten
    SOCK_EMIT = "RMM-SOCK-003"       # Senden an einen Agenten
    SOCK_TIMEOUT = "RMM-SOCK-004"    # Agent hat nicht geantwortet

    # --- Hintergrundaufgaben ---
    TASK_LOOP = "RMM-TASK-001"       # Dauerschleife abgestuerzt
    TASK_ONESHOT = "RMM-TASK-002"    # Einmalige Aufgabe fehlgeschlagen

    # --- VPN / Node ---
    VPN_TUNNEL = "RMM-VPN-001"
    VPN_PACKET = "RMM-VPN-002"
    VPN_ENDPOINT = "RMM-VPN-003"
    NODE_MODULE = "RMM-NODE-001"
    NODE_PROXY = "RMM-NODE-002"
    NODE_L2 = "RMM-NODE-003"

    # --- Ereignisschleife ---
    LOOP_BLOCKED = "RMM-LOOP-001"
    LOOP_KILL = "RMM-LOOP-002"

    # --- Dateien / Externes ---
    FILE_IO = "RMM-IO-001"
    EXTERNAL = "RMM-EXT-001"


# Was ein Code bedeutet und was man dagegen tut. Steht mit im Protokoll -
# damit muss niemand erst in der Dokumentation nachschlagen.
HINTS = {
    Codes.DB_SLOW: "Eine Abfrage blockiert die Datenbank und damit alle "
                   "anderen. Meist eine unbegrenzte Abfrage auf eine grosse "
                   "Tabelle. Aufrufer im Stacktrace prüfen.",
    Codes.DB_ABORT: "Die Abfrage wurde nach dem Zeitlimit abgebrochen, damit "
                    "der Server weiterläuft. Das ist die Notbremse, nicht die "
                    "Ursache - die Abfrage selbst gehört korrigiert.",
    Codes.DB_LOCKED: "Ein anderer Vorgang hält die Datenbank. Läuft eine "
                     "Sicherung oder ein externer Zugriff auf die Datei?",
    Codes.DB_HUGE: "Eine Tabelle ist so gross, dass Abfragen darauf den "
                   "Server aufhalten. Aufbewahrungsdauer in den "
                   "Datenschutz-Einstellungen setzen.",
    Codes.API_UNHANDLED: "Der Endpunkt hat eine Ausnahme geworfen, die er "
                         "nicht behandelt. Der Aufrufer bekommt eine 500.",
    Codes.SOCK_TIMEOUT: "Der Agent war online, hat aber nicht rechtzeitig "
                        "geantwortet. Netz, Last oder ein hängender Agent.",
    Codes.TASK_LOOP: "Eine Hintergrundschleife ist abgestürzt. Der Aufseher "
                     "startet sie neu; ohne ihn wäre die Funktion still weg.",
    Codes.LOOP_BLOCKED: "Etwas Synchrones läuft in der Ereignisschleife. "
                        "Solange antwortet das Backend gar nicht.",
    Codes.NODE_L2: "Die L2-Brücke ist nicht verfügbar. Die Tunnel laufen im "
                   "NAT-Betrieb weiter - kein Ausfall, nur eine andere "
                   "Absenderadresse.",
}

# Zaehler je Code, fuer die Diagnose-Ansicht.
counters: dict[str, int] = {}
# Letzte Meldung je Code - gegen Wiederholungsfluten.
_last_seen: dict[str, float] = {}
# Kurze Historie der letzten Fehler, damit die Oberflaeche sie zeigen kann.
recent: list[dict] = []
MAX_RECENT = 200
# Derselbe Code wird hoechstens alle so vielen Sekunden voll ausgegeben.
REPEAT_WINDOW = 10.0


def _console(text: str) -> None:
    """
    Direkt in die Container-Ausgabe.

    Bewusst sys.__stderr__ und nicht print(): print() geht durch unseren
    eigenen Mitschnitt, und der koennte im Fehlerfall selbst betroffen sein.
    Der Originalstrom geht unmittelbar an Docker.
    """
    try:
        stream = sys.__stderr__ or sys.stderr
        stream.write(text if text.endswith("\n") else text + "\n")
        stream.flush()
    except Exception:
        pass


def report(code: str, exc: BaseException | None = None, doing: str = "",
           **context) -> str:
    """
    Meldet einen Fehler. Gibt den Kenncode zurueck, damit der Aufrufer ihn
    auch dem Benutzer zeigen kann.

    Wirft NIE. Ein Fehler in der Fehlerbehandlung waere das Schlimmste, was
    passieren kann - dann verliert man die Spur genau dann, wenn man sie
    braucht.
    """
    try:
        counters[code] = counters.get(code, 0) + 1
        now = time.time()
        seen = _last_seen.get(code, 0.0)
        _last_seen[code] = now
        repeated = (now - seen) < REPEAT_WINDOW

        where = f" | {doing}" if doing else ""
        details = ", ".join(f"{k}={v!r}" for k, v in context.items() if v is not None)
        head = f"[FEHLER {code}]{where}"
        if details:
            head += f" | {details}"
        if exc is not None:
            head += f" | {type(exc).__name__}: {exc}"

        entry = {"code": code, "at": int(now * 1000), "doing": doing,
                 "context": {k: str(v) for k, v in context.items()},
                 "error": f"{type(exc).__name__}: {exc}" if exc else "",
                 "count": counters[code]}
        recent.append(entry)
        if len(recent) > MAX_RECENT:
            del recent[:len(recent) - MAX_RECENT]

        if repeated:
            # Wiederholung. Ein Dauerfehler darf das Protokoll NICHT fluten -
            # sonst verdeckt er genau die eine andere Meldung, auf die es
            # ankommt. Gemeldet wird deshalb nur bei bestimmten Staenden:
            # 2, 3, 5, 10, 25, 50, 100 - danach nur noch alle 100.
            n = counters[code]
            milestones = (2, 3, 5, 10, 25, 50, 100)
            if n in milestones or (n > 100 and n % 100 == 0):
                _console(f"{head} (Wiederholung, {n}x insgesamt)")
            return code

        lines = [head]
        hint = HINTS.get(code)
        if hint:
            lines.append(f"    -> {hint}")
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc,
                                                    exc.__traceback__))
            lines.append("    " + tb.rstrip().replace("\n", "\n    "))
        _console("\n".join(lines))

        # Zusaetzlich in die Diagnose-Datei, falls der Wartungsmodus laeuft.
        try:
            from app import diagnostics
            diagnostics.write("\n".join(lines), level="ERROR", from_stream=True)
        except Exception:
            pass
        return code
    except Exception:
        # Allerletzte Rueckfallebene.
        try:
            _console(f"[FEHLER {code}] (Meldung selbst fehlgeschlagen)")
        except Exception:
            pass
        return code


@contextmanager
def guard(code: str, doing: str = "", reraise: bool = False, **context):
    """
    Faengt alles ab, was in diesem Block schiefgeht.

    'reraise=True' meldet und wirft trotzdem weiter - fuer Stellen, an denen
    der Aufrufer den Fehler wirklich sehen muss (z.B. ein HTTP-Endpunkt, der
    eine 500 liefern soll), man aber trotzdem den Kontext im Protokoll haben
    will.
    """
    try:
        yield
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:      # noqa: BLE001 - hier ist es Absicht
        report(code, exc, doing, **context)
        if reraise:
            raise


def guarded(code: str, doing: str = "", **context):
    """Dekorator - funktioniert fuer gewoehnliche und fuer async-Funktionen."""

    def wrap(fn):
        if _is_async(fn):
            @functools.wraps(fn)
            async def inner_async(*a, **kw):
                try:
                    return await fn(*a, **kw)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:      # noqa: BLE001
                    report(code, exc, doing or fn.__name__, **context)
                    return None
            return inner_async

        @functools.wraps(fn)
        def inner(*a, **kw):
            try:
                return fn(*a, **kw)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:          # noqa: BLE001
                report(code, exc, doing or fn.__name__, **context)
                return None
        return inner

    return wrap


def _is_async(fn) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn)


def summary() -> dict:
    """Fuer die Diagnose-Ansicht: welche Fehler wie oft, und die letzten."""
    return {
        "counters": dict(sorted(counters.items(), key=lambda x: -x[1])),
        "recent": recent[-50:],
        "total": sum(counters.values()),
    }


def reset() -> None:
    counters.clear()
    _last_seen.clear()
    recent.clear()

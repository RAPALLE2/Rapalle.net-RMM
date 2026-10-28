"""
sockets.py
----------
Verwaltet alle Echtzeit-Verbindungen (WebSockets über die Socket.IO-Bibliothek).

Es gibt ZWEI getrennte "Namespaces" (=unabhängige Kommunikationskanäle):

  /agent      -> hier verbinden sich die Agenten (die Programme, die auf den
                 verwalteten PCs/Servern laufen). Nur wer das richtige
                 AGENT_TOKEN mitschickt, darf sich hier verbinden.

  /dashboard  -> hier verbindet sich das Browser-Frontend, um Live-Updates
                 zu bekommen (z.B. "Client X ist jetzt online", "neue
                 CPU-Auslastung für Client Y").

Der Ablauf für einen Befehl wie "führe im Terminal 'dir' aus" ist:
  1. Frontend ruft die REST-Route POST /api/clients/{id}/exec auf
  2. Diese Route ruft request_exec() aus DIESER Datei auf
  3. request_exec() schickt über den Agent-Namespace ein "exec"-Event an
     genau den richtigen Agenten und WARTET (mit einem asyncio.Future) auf
     dessen Antwort
  4. Der Agent führt den Befehl aus und schickt ein "exec-result"-Event zurück
  5. Der exec-result Handler hier findet das wartende Future wieder und
     "erfüllt" es mit dem Ergebnis - dadurch kann request_exec() in Schritt 2
     seine Antwort zurückgeben, und die REST-Route kann sie ans Frontend
     zurückschicken.

Dieses "schicke Anfrage raus, warte auf Antwort mit derselben Anfrage-ID"
Muster nennt man "Request/Response über Events" und ist der Standard-Weg,
um über WebSockets etwas zu erreichen, das sich wie ein normaler
Funktionsaufruf anfühlt.
"""

import asyncio
import time
import uuid
import json as _json


def _json_dumps_safe(obj) -> "str | None":
    """Metrik-Snapshot als kompaktes JSON (für die Historie). Bei Problemen None,
    damit das Speichern des Messpunkts nie an einem exotischen Wert scheitert."""
    try:
        return _json.dumps(obj, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None

import socketio

from app.config import AGENT_TOKEN
from app import db


def _int_env_early(name: str, default: int) -> int:
    """Ganzzahl aus einer Umgebungsvariablen - schon vor dem Anlegen des
    Servers nutzbar (das spätere _env_int steht erst weiter unten und wird
    hier noch nicht gebraucht). Unsinnige Werte fallen auf den Standard
    zurück, statt den Start zu verhindern."""
    import os
    try:
        v = int(os.getenv(name, "") or default)
        return v if v > 0 else default
    except ValueError:
        return default


# Der eigentliche Socket.IO-Server. async_mode="asgi" bedeutet: er läuft
# zusammen mit FastAPI im selben modernen Python-Webserver-Standard (ASGI).
# ----------------------------------------------------------------------
# Server-Grenzwerte: MÜSSEN gesetzt sein, sonst gelten die Defaults von
# python-socketio - und die passen nicht zu dem, was dieses Projekt sendet.
#
# max_http_buffer_size (Default: 1 MB)
#   Ein Screen-Frame ist ein JPEG, das der Agent auf bis zu 1920 px
#   skaliert, base64-kodiert (+37 %) und in EINEM Paket schickt. Bei
#   Qualität 70-90 liegt so ein Frame regelmässig zwischen 400 KB und
#   1,1 MB. Überschreitet EIN Paket das Limit, verwirft Engine.IO nicht
#   etwa nur das Paket - es trennt die Verbindung. Genau deshalb läuft
#   der Screen "halbwegs" (einfache Bilder gehen durch, detailreiche
#   sprengen das Limit) und genau deshalb fällt der Agent dabei offline.
#   50 MB lassen jedem Frame Luft, ohne dass ein einzelner Client
#   nennenswert Speicher binden kann.
#
# ping_interval / ping_timeout (Default: 25 s / 20 s)
#   Hinter Cloudflare werden ungenutzte WebSockets nach etwa 100 s
#   Stille geschlossen. Mit 20 s Takt liegen immer mehrere Pings
#   innerhalb dieses Fensters; die 40 s Timeout verhindern, dass ein
#   kurzer Aussetzer (Proxy-Umschaltung, Mobilfunk) sofort als
#   Verbindungsabbruch gewertet wird.
_SOCKET_MAX_PACKET = _int_env_early("RMM_SOCKET_MAX_PACKET", 50_000_000)


from app import modules as _modules

# Optionale Modul-Funktionen (alle None-sicher, siehe _Optional)
recording = _modules.optional("recordings", "recording")      # Modul 'recordings'
patching = _modules.optional("patching", "patching")          # Modul 'patching'
portforward = _modules.optional("network", "portforward")     # Modul 'network' (Port-Weiterleitung)
rdp_gateway = _modules.optional("guacamole", "rdp_gateway")   # Modul 'guacamole'

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    max_http_buffer_size=_SOCKET_MAX_PACKET,
    ping_interval=20,
    ping_timeout=40,
)


# ======================================================================
# ALLE Socket-Handler zentral absichern
# ======================================================================
# In dieser Datei haengen rund 50 Ereignis-Behandlungen. Wirft eine davon
# eine Ausnahme, faengt python-socketio sie zwar ab - aber die Meldung
# landet in dessen eigenem Logger, der standardmaessig stumm ist. Der
# Agent wartet dann auf eine Antwort, die nie kommt, und im Protokoll steht
# nichts. Das ist die unangenehmste Sorte Fehler: Es sieht aus, als haette
# jemand nichts gemacht.
#
# 50 Handler einzeln mit try/except zu versehen waere fehleranfaellig und
# beim naechsten neuen Handler schon wieder unvollstaendig. Stattdessen
# wird HIER, an einer einzigen Stelle, der Registrierungsmechanismus
# umhuellt: Jeder Handler, der ueber @sio.on oder @sio.event angemeldet
# wird, bekommt die Absicherung automatisch - auch jeder kuenftige.
#
# Was die Huelle tut:
#   * faengt jede Ausnahme ab und meldet sie MIT Kenncode und Ereignisnamen
#   * schickt, wenn die Anfrage eine requestId trug, eine Fehlerantwort
#     zurueck - sonst wartet der Aufrufer bis zu seinem Zeitlimit
#   * misst die Dauer und meldet auffaellig langsame Behandlungen

def _wrap_handler(handler, event_name: str):
    """
    Legt die Absicherung um einen einzelnen Ereignis-Handler.

    GRUNDREGEL: Diese Huelle darf einen Handler niemals unbrauchbar machen.
    Sie ist eine Verbesserung, kein Kernbestandteil - schlaegt hier
    irgendetwas fehl, wird der ORIGINAL-Handler registriert. Ein
    ungesicherter Handler ist immer noch besser als ein kaputter.
    Genau das ist schon einmal schiefgegangen: Die Huelle verlor die
    Signatur, und dadurch konnte sich kein Dashboard mehr anmelden.
    """
    import functools
    import inspect

    if getattr(handler, "_rmm_guarded", False):
        return handler
    if not callable(handler):
        return handler

    async def _fail(sid, args, exc):
        from app.errors import report, Codes
        client_id = state.sid_to_client_id.get(sid) if isinstance(sid, str) else None
        code = report(Codes.SOCK_HANDLER, exc, f"Ereignis '{event_name}'",
                      sid=sid, client=client_id)
        # Trug die Anfrage eine Kennung, MUSS eine Antwort zurueck - sonst
        # haengt die Gegenseite bis zu ihrem Zeitlimit.
        try:
            payload = next((a for a in args if isinstance(a, dict)), None)
            request_id = (payload or {}).get("requestId")
            if request_id:
                future = state.pending_requests.get(request_id)
                if future and not future.done():
                    future.set_result({"ok": False, "error_code": code,
                                       "error": str(exc)})
        except Exception:
            pass

    # ---------------------------------------------------------------
    # WICHTIG: Die SIGNATUR muss erhalten bleiben.
    #
    # python-socketio sieht sich die Parameterliste eines Handlers an, um
    # zu entscheiden, wie es ihn aufruft. Der 'connect'-Handler bekommt
    # (sid, environ, auth), wenn er drei Parameter hat, und nur
    # (sid, environ), wenn er zwei hat.
    #
    # Eine Huelle mit (*args, **kwargs) hat aus dieser Sicht KEINE
    # benannten Parameter - socketio ruft dann mit zwei Argumenten auf, und
    # der echte Handler bekommt sein 'auth' nicht. Genau das ist passiert:
    #   TypeError: dashboard_connect() missing 1 required positional
    #   argument: 'auth'
    # Damit konnte sich kein Dashboard mehr anmelden.
    #
    # functools.wraps kopiert __name__ und __doc__, aber NICHT die
    # Signatur. Deshalb wird sie hier ausdruecklich uebernommen: __wrapped__
    # setzt wraps() bereits, und inspect.signature() folgt dem - solange
    # man __signature__ nicht ueberschreibt. Sicherheitshalber wird sie
    # explizit gesetzt, damit es nicht von Implementierungsdetails abhaengt.
    try:
        _sig = inspect.signature(handler)
    except (TypeError, ValueError):
        _sig = None

    # Wie viele Positionsargumente nimmt der Handler wirklich? python-socketio ruft
    # z.B. 'disconnect' mit (sid, reason) auf und verlaesst sich darauf, bei einem
    # TypeError ohne 'reason' erneut zu probieren. Diese Huelle nimmt aber alles an
    # und verschluckt den TypeError - deshalb kuerzt sie die Argumente selbst auf
    # die Anzahl, die der Handler erwartet (sonst liefen disconnect-Handler nie).
    _max_pos = None
    if _sig is not None:
        _params = list(_sig.parameters.values())
        if not any(p.kind == p.VAR_POSITIONAL for p in _params):
            _max_pos = sum(1 for p in _params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))

    if inspect.iscoroutinefunction(handler):
        @functools.wraps(handler)
        async def guarded(*args, **kwargs):
            started = time.monotonic()
            if _max_pos is not None and len(args) > _max_pos:
                args = args[:_max_pos]
            try:
                return await handler(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except socketio.exceptions.ConnectionRefusedError:
                # Gewollte Ablehnung eines Verbindungsversuchs (falsches Token): MUSS bei
                # python-socketio ankommen. Frueher wurde sie hier verschluckt - dann galt
                # die Verbindung als erlaubt, auch mit falschem Agent-Token.
                raise
            except Exception as exc:
                await _fail(args[0] if args else None, args, exc)
                return None
            finally:
                took = time.monotonic() - started
                if took > 2.0:
                    try:
                        from app.errors import report, Codes
                        report(Codes.SOCK_HANDLER, None,
                               f"Ereignis '{event_name}' war langsam",
                               dauer=f"{took:.1f}s")
                    except Exception:
                        pass
    else:
        @functools.wraps(handler)
        def guarded(*args, **kwargs):
            if _max_pos is not None and len(args) > _max_pos:
                args = args[:_max_pos]
            try:
                return handler(*args, **kwargs)
            except socketio.exceptions.ConnectionRefusedError:
                raise                                   # siehe oben: Ablehnung nicht verschlucken
            except Exception as exc:
                from app.errors import report, Codes
                report(Codes.SOCK_HANDLER, exc, f"Ereignis '{event_name}'")
                return None

    guarded._rmm_guarded = True
    if _sig is not None:
        guarded.__signature__ = _sig
    return guarded


_orig_on = sio.on
_orig_event = sio.event


def _safe_wrap(fn, event):
    """Umhuellt - und faellt bei Problemen auf das Original zurueck."""
    try:
        wrapped = _wrap_handler(fn, event)
        # Gegenprobe: Die Signatur MUSS erhalten bleiben, sonst ruft
        # python-socketio den Handler mit der falschen Zahl Argumente auf.
        import inspect
        if inspect.signature(wrapped) != inspect.signature(fn):
            print(f"[sockets] Absicherung fuer '{event}' uebersprungen - "
                  f"Signatur haette sich geaendert.")
            return fn
        return wrapped
    except Exception as e:
        print(f"[sockets] Absicherung fuer '{event}' nicht moeglich ({e}) - "
              f"Handler laeuft ungesichert weiter.")
        return fn


def _guarded_on(event, handler=None, namespace=None):
    """Ersatz fuer sio.on - haengt die Absicherung dazwischen."""
    if handler is not None:
        return _orig_on(event, _safe_wrap(handler, event), namespace)

    def decorator(fn):
        _orig_on(event, _safe_wrap(fn, event), namespace)
        return fn
    return decorator


def _guarded_event(*args, **kwargs):
    """Ersatz fuer sio.event (mit und ohne Klammern verwendbar)."""
    if len(args) == 1 and callable(args[0]) and not kwargs:
        fn = args[0]
        return _orig_event(_safe_wrap(fn, fn.__name__))

    # FEHLER BEHOBEN: Frueher stand hier _orig_event(handler, namespace=...). Mit
    # Argumenten liefert python-socketio dort aber nur einen DEKORATOR zurueck und
    # registriert NICHTS. Betroffen waren 'connect' und 'disconnect' im Namespace
    # /agent: Die Agent-Token-Pruefung und die Annahme-Schleuse liefen nie (jede
    # Verbindung wurde angenommen, auch mit falschem Token), und getrennte Agenten
    # wurden nicht als offline gemeldet. Jetzt wird direkt ueber sio.on registriert.
    def decorator(fn):
        _orig_on(fn.__name__, _safe_wrap(fn, fn.__name__), kwargs.get("namespace"))
        return fn
    return decorator


sio.on = _guarded_on
sio.event = _guarded_event


def verify_handlers() -> dict:
    """
    Prueft beim Start, dass jeder Ereignis-Handler noch die Signatur hat,
    die python-socketio erwartet.

    Warum: Ein Handler mit falscher Signatur faellt NICHT beim Start auf.
    Er scheitert erst, wenn jemand das Ereignis ausloest - also wenn ein
    Dashboard sich anmeldet, eine Shell geoeffnet wird oder ein Agent sich
    meldet. Und dann steht im Protokoll nur ein TypeError irgendwo in einer
    Bibliothek. Diese Pruefung zieht den Fehler an den Start vor, wo er
    hingehoert.
    """
    import inspect
    problems = []
    checked = 0
    handlers = getattr(sio, "handlers", {})
    for namespace, events in handlers.items():
        if not isinstance(events, dict):
            continue
        for event, fn in events.items():
            if not callable(fn):
                continue
            checked += 1
            try:
                params = list(inspect.signature(fn).parameters)
            except (TypeError, ValueError):
                problems.append(f"{namespace}:{event} (Signatur nicht lesbar)")
                continue
            # 'connect' bekommt (sid, environ) oder (sid, environ, auth),
            # 'disconnect' (sid) oder (sid, reason), alles andere (sid, data).
            expected = {"connect": (2, 3), "disconnect": (1, 2)}.get(event)
            if expected and len(params) not in expected:
                problems.append(
                    f"{namespace or '/'}:{event} hat {len(params)} Parameter "
                    f"({', '.join(params)}), erwartet {' oder '.join(map(str, expected))}")
    if problems:
        for p in problems:
            print(f"[sockets] SIGNATUR-PROBLEM: {p}")
        try:
            from app.errors import report, Codes
            report(Codes.BOOT, None, "Socket-Handler mit falscher Signatur",
                   anzahl=len(problems), betroffen="; ".join(problems[:5]))
        except Exception:
            pass
    else:
        print(f"[sockets] {checked} Ereignis-Handler geprueft - Signaturen in Ordnung.")
    return {"checked": checked, "problems": problems}


class ConnectionState:
    """Hält den aktuellen Live-Zustand aller verbundenen Agenten im Speicher."""

    def __init__(self) -> None:
        self.client_id_to_sid: dict[str, str] = {}   # Client-ID -> Socket.IO Session-ID
        self.sid_to_client_id: dict[str, str] = {}   # umgekehrte Zuordnung (für Disconnect)
        self.live_metrics: dict[str, dict] = {}      # Client-ID -> letzte gemeldete Metriken
        self.last_metric_save: dict[str, int] = {}   # Client-ID -> Zeitstempel des letzten gespeicherten Punkts
        self.pending_requests: dict[str, asyncio.Future] = {}  # request_id -> wartendes Future
        # Client-ID -> Zeitpunkt (monotonic), zu dem der Agent nach einem Update
        # frisch registriert hat ("updated: true"). Wird vom Update-Endpunkt
        # abgefragt, um ein Update als erfolgreich zu bestätigen.
        self.update_confirmed: dict[str, float] = {}

    def is_online(self, client_id: str) -> bool:
        return client_id in self.client_id_to_sid


# Ein einziges, globales State-Objekt für die ganze App
state = ConnectionState()


# ======================================================================
# AUFNAHME-SCHLEUSE FÜR AGENTEN  ("einer nach dem anderen")
# ----------------------------------------------------------------------
# Problem, das hier gelöst wird: Nach einem Neustart des Containers (oder
# einem kurzen Netzausfall) wollen ALLE Agenten gleichzeitig verbinden.
# Jede Anmeldung zieht mehrere Datenbank-Schreibvorgänge, Notifier-Regeln
# und zwei Broadcasts an alle Dashboards nach sich. Bei 50+ Agenten
# gleichzeitig entstehen daraus tausende parallele Coroutinen und
# DB-Zugriffe in derselben Sekunde - der Container wird vom OOM-Killer
# bzw. vom blockierten Event-Loop erledigt.
#
# Lösung in drei Stufen:
#   1. TÜRSTEHER (Semaphore): Nur eine begrenzte Zahl Agenten darf sich
#      gleichzeitig im Anmelde-Vorgang befinden. Standard: 1 - also
#      wirklich streng einer nach dem anderen.
#   2. WARTESCHLANGE: Wer keinen Platz bekommt, wartet. Wird die Wartezeit
#      zu lang oder ist die Schlange voll, wird die Verbindung höflich
#      abgelehnt; der Agent versucht es mit Backoff später erneut.
#   3. SERIALISIERUNG: Die eigentliche 'register'-Verarbeitung läuft unter
#      einem Lock, damit sie nie überlappt - egal wie viele Sockets offen
#      sind.
#
# Alle Werte sind über Umgebungsvariablen einstellbar, damit man auf
# grösserer Hardware wieder aufdrehen kann, ohne Code zu ändern.
# ======================================================================

import os as _os


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(_os.getenv(name, "") or default))
    except ValueError:
        return default


# Wie viele Agenten dürfen sich GLEICHZEITIG anmelden?
#
# War 1 - das war zu streng und hat mehr kaputt gemacht als geholfen. Eine
# Anmeldung dauert Millisekunden; das Problem waren nie zwei gleichzeitige
# Anmeldungen, sondern hundert. Bei 4 Plätzen bleibt die Last gedeckelt,
# aber eine Handvoll Agenten kommt nach einem Netzausfall sofort wieder
# rein, statt sich gegenseitig in immer längere Wartezeiten zu drängen.
AGENT_ADMISSION_SLOTS = _env_int("AGENT_ADMISSION_SLOTS", 4)
# Wie lange darf ein Agent auf seinen Platz warten, bevor er abgewiesen wird?
AGENT_ADMISSION_WAIT_S = _env_int("AGENT_ADMISSION_WAIT_S", 45)
# Wie viele Agenten dürfen höchstens in der Schlange stehen? Darüber hinaus
# wird sofort abgelehnt, statt immer mehr Speicher mit Wartenden zu füllen.
AGENT_ADMISSION_QUEUE = _env_int("AGENT_ADMISSION_QUEUE", 100)
# Notbremse: Meldet sich ein Agent nach dem Verbinden nicht binnen dieser
# Zeit an, wird sein Platz wieder freigegeben.
#
# War 30 s. Das war viel zu lang: Eine Anmeldung folgt dem Verbinden
# innerhalb von Millisekunden. Bricht die Verbindung genau dazwischen ab,
# blockierte der tote Platz eine halbe Minute lang die Schleuse - bei
# wenigen Plätzen legt das den Wiedereinstieg aller Agenten lahm.
AGENT_ADMISSION_HOLD_S = _env_int("AGENT_ADMISSION_HOLD_S", 10)
# Wie viele Heartbeats werden gleichzeitig verarbeitet? Heartbeats schreiben
# in die DB - unbegrenzt parallel ist genau die Last, die den Container killt.
AGENT_HEARTBEAT_SLOTS = _env_int("AGENT_HEARTBEAT_SLOTS", 4)

_admission_sem: "asyncio.Semaphore | None" = None
_register_lock: "asyncio.Lock | None" = None
_heartbeat_sem: "asyncio.Semaphore | None" = None
_admission_waiting = 0                 # aktuell in der Schlange
_admission_held: dict[str, object] = {}  # sid -> Watchdog-Task (hält einen Platz)
_admission_stats = {"granted": 0, "refused": 0, "healed": 0}


def _ensure_gate() -> None:
    """Legt die Schleusen-Objekte beim ersten Gebrauch an.

    Bewusst spät: asyncio-Primitive dürfen erst existieren, wenn der
    Event-Loop läuft - beim Import des Moduls tut er das noch nicht.
    """
    global _admission_sem, _register_lock, _heartbeat_sem
    if _admission_sem is None:
        _admission_sem = asyncio.Semaphore(AGENT_ADMISSION_SLOTS)
    if _register_lock is None:
        _register_lock = asyncio.Lock()
    if _heartbeat_sem is None:
        _heartbeat_sem = asyncio.Semaphore(AGENT_HEARTBEAT_SLOTS)


async def _admission_acquire(sid: str) -> bool:
    """Wartet auf einen freien Platz in der Schleuse. False = abgelehnt."""
    global _admission_waiting
    _ensure_gate()
    if _admission_waiting >= AGENT_ADMISSION_QUEUE:
        return False
    _admission_waiting += 1
    try:
        await asyncio.wait_for(_admission_sem.acquire(),
                               timeout=AGENT_ADMISSION_WAIT_S)
    except asyncio.TimeoutError:
        _admission_stats["refused"] += 1
        print(f"[gate] Agent {sid} abgewiesen - alle {AGENT_ADMISSION_SLOTS} "
              f"Plätze belegt, {_admission_waiting - 1} weitere warten")
        return False
    finally:
        _admission_waiting -= 1

    # Notbremse scharf machen: Platz automatisch freigeben, falls kein
    # 'register' kommt.
    async def _watchdog():
        try:
            await asyncio.sleep(AGENT_ADMISSION_HOLD_S)
        except asyncio.CancelledError:
            return
        print(f"[gate] Agent {sid} hat sich nicht angemeldet - Platz freigegeben")
        _admission_release(sid)

    _admission_held[sid] = asyncio.ensure_future(_watchdog())
    _admission_stats["granted"] += 1
    return True


def _admission_release(sid: str) -> None:
    """Gibt den Platz eines Agenten wieder frei (mehrfacher Aufruf ist harmlos)."""
    task = _admission_held.pop(sid, None)
    if task is None:
        return
    try:
        task.cancel()
    except Exception:
        pass
    if _admission_sem is not None:
        try:
            _admission_sem.release()
        except ValueError:
            pass


# ----------------------------------------------------------------------
# Gebündelte Dashboard-Broadcasts
# ----------------------------------------------------------------------
# 'clients:changed' hat keinen Inhalt - es sagt dem Frontend nur "lade die
# Liste neu". 80 Agenten, die binnen zwei Sekunden verbinden, lösten bisher
# 160 solcher Broadcasts aus (und damit 160 Neuladungen pro Dashboard).
# Jetzt wird höchstens einer pro Sekunde verschickt.
_clients_changed_task: "object | None" = None


def _emit_clients_changed_soon(delay: float = 1.0) -> None:
    """Meldet den Dashboards gesammelt, dass sich die Client-Liste geändert hat."""
    global _clients_changed_task
    task = _clients_changed_task
    if task is not None and not task.done():
        return   # es ist bereits eine Meldung unterwegs

    async def _fire():
        await asyncio.sleep(delay)
        try:
            await sio.emit("clients:changed", namespace="/dashboard")
        except Exception:
            pass

    try:
        _clients_changed_task = asyncio.ensure_future(_fire())
    except RuntimeError:
        _clients_changed_task = None


def _new_request_id() -> str:
    return uuid.uuid4().hex


async def request_exec(client_id: str, command: str, timeout_seconds: float = 20.0,
                       session: str | None = None, shell: str = "auto",
                       elevated: bool = False) -> dict:
    """
    Schickt einen Shell-Befehl an einen Agenten und wartet auf das Ergebnis.
    Gibt zurück: {"stdout": ..., "stderr": ..., "code": ...}
    Wirft eine Exception, wenn der Client offline ist oder nicht rechtzeitig antwortet.

    'session' identifiziert ein Terminal-Fenster: der Agent hält pro Session ein
    eigenes Arbeitsverzeichnis, damit 'cd' über mehrere Befehle hinweg wirkt.
    'shell' = 'cmd'|'powershell'|'auto', 'elevated' = als Administrator (Windows).
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit(
        "exec",
        {"requestId": request_id, "command": command, "session": session,
         "shell": shell, "elevated": elevated},
        to=sid, namespace="/agent",
    )

    try:
        result = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)
    return result


async def request_fs_list(client_id: str, path: str, timeout_seconds: float = 15.0) -> list[dict]:
    """
    Fragt bei einem Agenten den Inhalt eines Ordners ab.
    Gibt eine Liste von Datei-/Ordner-Einträgen zurück.
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit("fs-list", {"requestId": request_id, "path": path}, to=sid, namespace="/agent")

    try:
        payload = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)

    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload.get("entries", [])


async def request_proc_list(client_id: str, timeout_seconds: float = 15.0) -> list[dict]:
    """Fragt die laufende Prozessliste eines Clients ab (für den Task-Manager)."""
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit("proc-list", {"requestId": request_id}, to=sid, namespace="/agent")

    try:
        payload = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)

    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload.get("processes", [])


async def request_proc_kill(client_id: str, pid: int, timeout_seconds: float = 10.0) -> dict:
    """Fordert einen Agenten auf, einen Prozess zu beenden."""
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit("proc-kill", {"requestId": request_id, "pid": pid}, to=sid, namespace="/agent")

    try:
        payload = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)
    return payload


async def request_fs_read(client_id: str, path: str, timeout_seconds: float = 30.0) -> dict:
    """Fordert eine Datei von einem Agenten an (für den Download im Dashboard)."""
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit("fs-read", {"requestId": request_id, "path": path}, to=sid, namespace="/agent")

    try:
        payload = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)

    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


async def request_patch_ping(client_id: str, timeout_seconds: float = 8.0) -> dict:
    """
    Fragt den Agenten, ob er Patching überhaupt beherrscht. Antwortet er
    nicht innerhalb weniger Sekunden, läuft dort ein Agent ohne die
    Patch-Handler - dann hat es keinen Zweck, einen Scan zu schicken.
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future
    await sio.emit("patch-ping", {"requestId": request_id}, to=sid, namespace="/agent")
    try:
        return await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)


async def request_patch_selftest(client_id: str, timeout_seconds: float = 90.0) -> dict:
    """
    Kurzer Funktionstest auf dem Client: sind die Update-Quellen ansprechbar,
    läuft der Agent erhöht? Dauert Sekunden statt Minuten und beantwortet
    die Frage "liegt es am Agenten oder am System?".
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future
    await sio.emit("patch-selftest", {"requestId": request_id},
                   to=sid, namespace="/agent")
    try:
        payload = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


async def request_patch_scan(client_id: str, timeout_seconds: float = 900.0) -> dict:
    """
    Lässt einen Agenten nach verfügbaren Aktualisierungen suchen.

    Grosszügiger Timeout: die Windows-Update-Abfrage braucht auf trägen
    Systemen regelmässig mehrere Minuten. Ein Agent ab Patch-Protokoll 2
    antwortet in jedem Fall - notfalls mit einer Fehlermeldung; bleibt er
    trotzdem stumm, steht auf dem Client etwas.

    Anders als früher wird eine Fehlerantwort hier NICHT in eine Ausnahme
    verwandelt: der Aufrufer will auch bei einem Fehler die mitgelieferten
    Zusatzangaben (Quellen, Fähigkeiten) auswerten.
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit("patch-scan", {"requestId": request_id}, to=sid, namespace="/agent")

    try:
        return await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)


async def request_patch_apply(client_id: str, items: list[dict],
                              timeout_seconds: float = 7200.0) -> dict:
    """
    Lässt einen Agenten die benannten Aktualisierungen installieren.
    Sehr langer Timeout - Feature-Updates laufen durchaus zwei Stunden.
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit("patch-apply", {"requestId": request_id, "items": items},
                   to=sid, namespace="/agent")

    try:
        return await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)


async def request_fs_op(client_id: str, event: str, data: dict,
                        timeout_seconds: float = 60.0) -> dict:
    """
    Generischer Helfer für schreibende FS-Operationen auf einem Client:
    fs-write (Upload/Editieren), fs-mkdir, fs-delete, fs-rename.
    Alle antworten mit dem Event 'fs-op-result'.
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit(event, {"requestId": request_id, **data}, to=sid, namespace="/agent")

    try:
        payload = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)

    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


# ====================================================================
# Handler für den /agent Namespace (Verbindungen von den Agent-Programmen)
# ====================================================================

# ------------------------------------------------------------------
# Automatisches Agent-Update
# ------------------------------------------------------------------
# Veraltete Agenten werden beim Verbinden automatisch aktualisiert, wenn das
# Feature greift. Effektiver Modus pro Client:
#   auto_update = 'on'     -> immer aktualisieren
#   auto_update = 'off'    -> nie automatisch aktualisieren
#   auto_update = 'global' -> Settings-Schalter 'agent_auto_update' entscheidet
# Ein Cooldown pro Client verhindert Update-Schleifen, falls ein Update
# fehlschlägt und der Agent sich immer wieder mit alter Version meldet.

_AUTO_UPDATE_COOLDOWN_S = 1800   # 30 min zwischen zwei Auto-Update-Versuchen
_auto_update_last: dict[str, float] = {}
# Letzter Heartbeat-basierter Versions-Check pro Client (max. 1x/60s).
_auto_update_hb_check: dict[str, float] = {}
# Drossel für die Metrik-Schwellwert-Prüfung (Benachrichtigungs-Regeln).
_threshold_check: dict[str, float] = {}

# Pfad zur ausgelieferten Agent-Version (…/backend/app/sockets.py -> …/agent/version.txt)
from pathlib import Path as _Path
from app.paths import AGENT_VERSION_FILE as _AGENT_VERSION_FILE, AGENT_MAIN_FILE as _AGENT_PY_FILE

# Was jeder Client beim Anmelden über sich gesagt hat: Code-Prüfsumme und
# Patch-Fähigkeit. Nur im Arbeitsspeicher - die Werte kommen bei jedem
# 'register' frisch herein, und ohne Verbindung sind sie bedeutungslos.
_agent_code_hashes: dict[str, str] = {}
_agent_caps: dict[str, dict] = {}


def agent_capabilities(client_id: str) -> dict:
    """
    Selbstauskunft eines verbundenen Agenten.

    Ersetzt das frühere Raten per Ping: Wer patchen kann, sagt es beim
    Anmelden. Ein leeres Ergebnis heisst "hat nichts gemeldet" - also
    Agent-Code ohne Patch-Modul.
    """
    return dict(_agent_caps.get(client_id) or {})

# Prüfsumme der ausgelieferten agent.py, zwischengespeichert nach mtime.
_latest_hash_cache: tuple[float, str] = (0.0, "")


def _latest_agent_hash() -> str:
    """
    Prüfsumme der agent.py, die dieses Backend ausliefert.

    Grund für dieses ganze Verfahren: version.txt ist die einzige Quelle, aus
    der bisher hervorging, ob ein Agent veraltet ist. Wird sie beim Ändern von
    agent.py nicht mit hochgezählt - und das passiert regelmäßig -, hält das
    Backend jeden Client für aktuell und stößt NIE ein Update an. Auf den
    Clients lief dadurch monatealter Code; neue Fähigkeiten wie 'patch-ping'
    fehlten dort schlicht, obwohl sie im Projekt vorhanden waren.
    """
    global _latest_hash_cache
    try:
        mtime = _AGENT_PY_FILE.stat().st_mtime
    except OSError:
        return ""
    if _latest_hash_cache[0] == mtime and _latest_hash_cache[1]:
        return _latest_hash_cache[1]
    try:
        import hashlib
        digest = hashlib.sha256(_AGENT_PY_FILE.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""
    _latest_hash_cache = (mtime, digest)
    return digest


def _latest_agent_version() -> str:
    """Aktuell ausgelieferte Agent-Version (Single Source: agent/version.txt)."""
    try:
        return _AGENT_VERSION_FILE.read_text(encoding="utf-8").strip() or ""
    except OSError:
        return ""


def _version_tuple(v: str):
    """'1.2.10' -> (1, 2, 10). Nicht-numerische Teile werden als 0 gewertet."""
    parts = []
    for p in str(v).strip().split("."):
        try:
            parts.append(int("".join(ch for ch in p if ch.isdigit()) or 0))
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


def _agent_outdated(agent_v: str, latest_v: str, client_id: str | None = None) -> bool:
    """
    Ist der Agent auf dem Client älter als der ausgelieferte?

    Zwei Kriterien, in dieser Reihenfolge:
      1. Kleinere Versionsnummer -> veraltet (wie bisher).
      2. Gleiche Versionsnummer, aber ANDERER Code -> ebenfalls veraltet.
         Erkannt an der Prüfsumme, die der Agent beim Anmelden meldet.
         Meldet er gar keine, läuft dort eine Fassung von vor dieser
         Änderung - also ebenfalls alter Code.

    Kriterium 2 greift nur, wenn dieses Backend eine eigene Prüfsumme bilden
    kann; ohne agent.py auf der Platte bleibt es beim reinen Versionsvergleich.
    """
    if agent_v and latest_v:
        try:
            if _version_tuple(agent_v) < _version_tuple(latest_v):
                return True
        except Exception:
            if agent_v.strip() != latest_v.strip():
                return True

    if client_id is None:
        return False
    latest_hash = _latest_agent_hash()
    if not latest_hash:
        return False
    return _agent_code_hashes.get(client_id, "") != latest_hash


_migration_last: dict[str, float] = {}


async def _migration_redirect(client_id: str, hostname: str = "") -> bool:
    """Umzug aktiv? -> Agent per Update zum neuen Server schicken (hoechstens alle 15 min je Agent,
    damit ein fehlschlagendes Update keine Schleife erzeugt). True = ausgeloest."""
    target = (await db.call(db.get_setting, "migration_target_url", "") or "").strip()
    if not target:
        return False
    now = time.time()
    if now - _migration_last.get(client_id, 0.0) < 900:
        return False
    _migration_last[client_id] = now
    await asyncio.sleep(3)                       # Anmeldung erst sauber abschliessen lassen
    if await send_to_agent(client_id, "update-agent", {}):
        await db.call(db.add_audit_entry, None, "migration.agent_redirected", target=client_id,
                      details=f"{hostname or client_id} -> {target}")
        return True
    return False


async def _maybe_auto_update(client_id: str, agent_version: str | None) -> None:
    """Stößt bei veralteter Version das Selbst-Update an, falls Auto-Update greift."""
    latest = _latest_agent_version()
    if not _agent_outdated(agent_version or "", latest, client_id):
        _auto_update_last.pop(client_id, None)   # aktuell -> Cooldown zurücksetzen
        return

    client = await db.call(db.get_client, client_id)
    if not client or not client.get("active", 1):
        return
    mode = (client.get("auto_update") or "global").lower()
    if mode == "off":
        return
    if mode != "on" and await db.call(db.get_setting, "agent_auto_update", "0") != "1":
        return

    now = time.monotonic()
    last = _auto_update_last.get(client_id)
    if last is not None and now - last < _AUTO_UPDATE_COOLDOWN_S:
        return   # kürzlich schon versucht -> keine Update-Schleife bauen
    _auto_update_last[client_id] = now

    ok = await send_to_agent(client_id, "update-agent", {})
    if ok:
        await db.call(db.add_audit_entry, None, "agent.auto_update_triggered",
                      target=client_id,
                      details=f"{client.get('hostname')}: {agent_version} -> {latest}")
        print(f"[auto-update] {client.get('hostname')} ({client_id}): "
              f"Agent {agent_version} -> {latest} wird aktualisiert")
        # Dashboard informieren (gleiches Event wie beim manuellen Update-Start).
        await sio.emit("client:update-started", {"id": client_id, "auto": True},
                       namespace="/dashboard")


@sio.event(namespace="/agent")
async def connect(sid, environ, auth):
    """
    Wird aufgerufen, sobald sich JEMAND mit /agent verbinden will.

    Zwei Prüfungen, in dieser Reihenfolge:
      1. AGENT_TOKEN - ohne das richtige Token wird sofort abgelehnt.
      2. Aufnahme-Schleuse - der Agent wartet hier, bis er an der Reihe ist.
         Das Warten passiert BEVOR die Verbindung als hergestellt gilt, also
         bevor irgendwelche Daten fliessen. Dadurch baut immer nur eine
         Handvoll Agenten gleichzeitig wirklich auf.

    Wer nicht rechtzeitig drankommt, wird mit einem klaren Grund abgewiesen
    ("busy") - der Agent wartet dann selbst und probiert es erneut. Das ist
    deutlich billiger, als hunderte halboffene Verbindungen zu halten.
    """
    token = (auth or {}).get("token")
    if token != AGENT_TOKEN:
        raise socketio.exceptions.ConnectionRefusedError("Ungültiges Agent-Token")

    if not await _admission_acquire(sid):
        raise socketio.exceptions.ConnectionRefusedError(
            "busy: Server nimmt gerade andere Agenten auf - bitte später erneut")


@sio.on("register", namespace="/agent")
async def on_register(sid, payload):
    """
    Aussenhuelle der Anmeldung.

    UMGEBAUT (2026-09-20). Die alte Fassung hatte zwei Fehler, die zusammen
    genau das Bild erzeugten, das in der Praxis auftrat: "Der Agent
    installiert sich sauber, meldet sich verbunden - und taucht im Dashboard
    nie auf."

      1. Sie fing JEDE Ausnahme aus _do_register() ab, schrieb sie nur auf
         die Konsole und schickte trotzdem 'register-ack' mit ok=True. Der
         Agent hielt sich fuer angemeldet und begann mit Heartbeats, obwohl
         in der Datenbank nie ein Client-Datensatz entstanden war. Der
         Fehlschlag war damit vollstaendig unsichtbar.

      2. Sie hielt waehrend der GESAMTEN Verarbeitung ein globales Lock -
         und die Verarbeitung machte rund fuenfzehn SYNCHRONE
         Datenbankaufrufe direkt im Event-Loop. Bei einer grossen Datenbank
         reichte das, um die Anmeldungen so zu stauen, dass die Schleuse
         nach 45 s mit "busy" abwies. Auch dann sah der Agent nur eine
         Verbindung, die immer wieder neu aufgebaut wurde.

    Jetzt gilt: Das Lock ist PRO CLIENT (zwei verschiedene Geraete stoeren
    sich nicht mehr gegenseitig), saemtliche Datenbankarbeit laeuft in
    Arbeits-Threads, und das 'register-ack' sagt die Wahrheit - inklusive
    Grund, wenn es schiefging.
    """
    _ensure_gate()
    client_id = (payload or {}).get("id") or ""
    ok, reason = False, "unbekannt"
    try:
        async with _register_lock_for(client_id):
            ok, reason = await _do_register(sid, payload)
    except Exception as e:
        from app.errors import report as _report, Codes as _Codes
        code = _report(_Codes.API_UNHANDLED, e, "Agenten-Anmeldung",
                       client=client_id or sid)
        ok, reason = False, f"Serverfehler ({code})"
    finally:
        try:
            ack = {"ok": ok}
            if not ok:
                ack["reason"] = reason
            await sio.emit("register-ack", ack, to=sid, namespace="/agent")
        except Exception:
            pass
        _admission_release(sid)

    if not ok:
        # Bewusst laut: Eine fehlgeschlagene Anmeldung ist der Unterschied
        # zwischen "Geraet ist im Dashboard" und "Geraet existiert nicht".
        print(f"[register] FEHLGESCHLAGEN fuer {client_id or sid}: {reason}")


# Ein Lock je Client statt eines globalen. Die Schleuse (_admission_sem)
# begrenzt weiterhin, WIE VIELE Anmeldungen gleichzeitig laufen duerfen; das
# Lock verhindert nur noch, dass DERSELBE Agent sich zweimal parallel
# anmeldet (das passiert, wenn er kurz hintereinander neu verbindet).
_register_locks: dict = {}


def _register_lock_for(client_id: str):
    lock = _register_locks.get(client_id)
    if lock is None:
        lock = asyncio.Lock()
        _register_locks[client_id] = lock
        # Aufraeumen, damit das Verzeichnis nicht unbegrenzt waechst.
        if len(_register_locks) > 5000:
            for key in [k for k, v in list(_register_locks.items())
                        if not v.locked()][:2500]:
                _register_locks.pop(key, None)
    return lock


async def _do_register(sid, payload) -> tuple[bool, str]:
    """
    Ein Agent meldet sich an. Rueckgabe: (erfolgreich, Grund).

    Der Ablauf ist bewusst in zwei Haelften geteilt:

      PFLICHT   Alles, ohne das das Geraet nicht im Dashboard erscheint:
                Datensatz anlegen, einem Mandanten zuordnen, Dashboards
                benachrichtigen. Scheitert hier etwas, meldet die Funktion
                einen Fehlschlag - der Agent erfaehrt das und versucht es
                erneut, statt in einen stillen Leerlauf zu gehen.

      KUER      Alles Weitere: Auto-Update, Benachrichtigungsregeln,
                Patch-Angaben, Node-Konfiguration, Absturzmeldung. Jeder
                dieser Punkte steht in seinem eigenen try - ein Fehler dort
                darf die Anmeldung nicht mehr kippen. Frueher konnte genau
                das passieren: eine kaputte Benachrichtigungsregel verhinderte
                die Aufnahme eines Geraets.

    Saemtliche Datenbankaufrufe laufen ueber db.call() in einem
    Arbeits-Thread. Im Event-Loop darf nichts mehr auf eine Dateisperre
    warten - dort haengen alle Agenten, die Shell und der Remote-Screen dran.
    """
    payload = payload or {}
    client_id = payload.get("id")
    hostname = payload.get("hostname")
    if not client_id or not hostname:
        return False, "Anmeldung ohne id/hostname"

    # ---------------- PFLICHT ----------------------------------------
    state.client_id_to_sid[client_id] = sid
    state.sid_to_client_id[sid] = client_id

    # Gemeldete Code-Pruefsumme merken - MUSS vor jeder Veraltet-Pruefung
    # passieren, sonst hielte die erste Pruefung jeden Agenten fuer veraltet.
    _agent_code_hashes[client_id] = str(payload.get("agent_code_hash") or "")
    _agent_caps[client_id] = {
        "protocol": int(payload.get("patch_protocol") or 0),
        "sources": list(payload.get("patch_sources") or []),
        "agent_version": payload.get("agent_version") or "",
        "agent_code_hash": payload.get("agent_code_hash") or "",
    }

    try:
        was_known = await db.call(db.get_client, client_id) is not None
        await db.call(
            db.upsert_client,
            client_id,
            hostname,
            payload.get("platform"),
            payload.get("arch"),
            payload.get("release"),
            payload.get("ip"),
        )
    except Exception as e:
        from app.errors import report as _report, Codes as _Codes
        code = _report(_Codes.API_UNHANDLED, e,
                       "Client-Datensatz beim Anmelden schreiben",
                       client=client_id)
        return False, f"Datenbank schreibt nicht ({code})"

    # Zuordnung zu Mandant/Standort. Ohne tenant_id blendet das Dashboard
    # den Client in den meisten Ansichten AUS - deshalb gehoert das hier in
    # die Pflicht und nicht in die Kuer.
    try:
        client = await db.call(db.get_client, client_id)
        if client and not client["tenant_id"]:
            token = payload.get("enrollment_token")
            token_row = (await db.call(db.get_enrollment_token, token)) if token else None

            if token_row and token_row["tenant_id"]:
                fields = {"tenant_id": token_row["tenant_id"],
                          "location_id": token_row["location_id"]}
                if token_row["client_name"]:
                    fields["hostname"] = token_row["client_name"]
            else:
                tenant, location = await db.call(db.ensure_uncategorized)
                fields = {"tenant_id": tenant, "location_id": location}
                if token_row and token_row["client_name"]:
                    fields["hostname"] = token_row["client_name"]

            await db.call(db.update_client, client_id, fields)
            if token:
                await db.call(db.mark_enrollment_token_used, token)
    except Exception as e:
        from app.errors import report as _report, Codes as _Codes
        code = _report(_Codes.API_UNHANDLED, e,
                       "Client einem Mandanten zuordnen", client=client_id)
        return False, f"Zuordnung fehlgeschlagen ({code})"

    # Ab hier gilt die Anmeldung als geglueckt: Der Datensatz steht, und die
    # Dashboards erfahren es. Das geschieht VOR der Kuer, damit kein
    # Nebenschauplatz das Erscheinen im Dashboard verzoegern kann.
    try:
        await sio.emit("client:online", {"id": client_id}, namespace="/dashboard")
        _emit_clients_changed_soon()
    except Exception as e:
        print(f"[register] Dashboards nicht benachrichtigt: {e!r}")

    # ---------------- KUER -------------------------------------------
    async def _side(name, coro_factory):
        """Ein Nebenschauplatz - darf scheitern, ohne die Anmeldung zu kippen."""
        try:
            await coro_factory()
        except Exception as e:
            print(f"[register] '{name}' fuer {client_id} fehlgeschlagen: {e!r}")

    async def _version():
        await db.call(db.set_agent_version, client_id, payload.get("agent_version"))

    async def _updated_flag():
        if not payload.get("updated"):
            return
        state.update_confirmed[client_id] = time.monotonic()
        await sio.emit("client:updated", {"id": client_id}, namespace="/dashboard")
        from app import notifier
        from router.admin_routes import build_notification
        host = hostname or client_id
        await notifier.fire_event("agent_update", client_id=client_id,
            notification=build_notification(
                f"Agent auf {host} wurde auf Version "
                f"{payload.get('agent_version') or '?'} aktualisiert.",
                head=f"\u2b06\ufe0f Agent aktualisiert \u2013 {host}",
                client=host, service="Agent-Update", level="success"))

    async def _auto_update():
        # Server-Umzug aktiv (Source -> Migration -> "Agenten umziehen"): jeder Agent,
        # der sich HIER noch meldet, bekommt das Update-Kommando. Das Update-Skript
        # traegt die neue Server-Adresse ein -> der Agent verbindet sich danach mit dem
        # neuen Backend. Gilt unabhaengig von der Agent-Version.
        if await _migration_redirect(client_id, hostname):
            return
        pending = await db.call(db.get_client, client_id)
        if pending and pending.get("pending_agent_update"):
            await db.call(db.set_pending_agent_update, client_id, False)
            if _agent_outdated(payload.get("agent_version") or "",
                               _latest_agent_version(), client_id):
                if await send_to_agent(client_id, "update-agent", {}):
                    await db.call(db.add_audit_entry, None,
                                  "agent.pending_update_triggered",
                                  target=client_id,
                                  details=(pending.get("hostname") or client_id))
                    await sio.emit("client:update-started",
                                   {"id": client_id, "auto": True},
                                   namespace="/dashboard")
        elif await db.call(db.get_setting, "agent_auto_update_offline", "1") == "1":
            await _maybe_auto_update(client_id, payload.get("agent_version"))

    async def _device_type():
        if await db.call(db.apply_detected_device_type, client_id,
                         payload.get("device_type")):
            _emit_clients_changed_soon()

    async def _crash_report():
        crash = payload.get("last_crash")
        if not crash:
            return
        row = await db.call(db.get_client, client_id)
        host = (row or {}).get("hostname") or hostname or client_id
        await db.call(db.add_audit_entry, None, "agent.crashed",
                      target=client_id, details=str(crash)[:1000])
        print(f"[agent-crash] {host}: Agent ist abgestuerzt und wurde neu "
              f"gestartet:\n{crash}")
        await sio.emit("client:agent-crashed", {
            "id": client_id, "hostname": host, "error": str(crash)[-2000:],
        }, namespace="/dashboard")

    async def _subnets():
        subnets = payload.get("subnets")
        if subnets:
            await db.call(db.set_client_subnets, client_id, subnets)

    async def _diag_mode():
        if await db.call(db.get_setting, "maintenance_agents", "0") == "1":
            await sio.emit("diag-mode", {"enabled": True, "minutes": 0},
                           to=sid, namespace="/agent")

    async def _notify_online():
        from app import notifier
        from router.admin_routes import build_notification
        host = hostname or client_id
        await notifier.fire_event("client_online", client_id=client_id,
            notification=build_notification(
                f"{host} ist jetzt online.",
                head=f"\U0001f7e2 Client online \u2013 {host}",
                client=host, service="Status", level="success"))
        if not was_known:
            await notifier.fire_event("client_new", client_id=client_id,
                notification=build_notification(
                    f"Neuer Client {host} wurde registriert.",
                    head=f"\u2728 Neuer Client \u2013 {host}",
                    client=host, service="Onboarding", level="info"))

    async def _node_check():
        from app import node_manager
        await node_manager.on_agent_online(client_id)

    await _side("node-pruefung", _node_check)
    await _side("agent-version", _version)
    await _side("update-bestaetigung", _updated_flag)
    await _side("auto-update", _auto_update)
    await _side("geraetetyp", _device_type)
    await _side("absturzmeldung", _crash_report)
    await _side("netze", _subnets)
    await _side("wartungsmodus", _diag_mode)
    await _side("benachrichtigungen", _notify_online)

    return True, "ok"


@sio.on("heartbeat", namespace="/agent")
async def on_heartbeat(sid, payload):
    """Ein Agent meldet alle paar Sekunden seine aktuellen Metriken (CPU/RAM/Disk/...)."""
    client_id = payload.get("id")
    if not client_id:
        return

    # Heartbeats sind der zweite Lastfaktor: Sie schreiben in die Datenbank
    # und senden an alle Dashboards. Ohne Begrenzung laufen bei vielen
    # Clients beliebig viele davon gleichzeitig. Wir lassen nur eine feste
    # Zahl gleichzeitig durch - und wenn gerade Anmeldungen laufen, haben
    # DIESE Vorrang: ein ausgelassener Heartbeat ist folgenlos, eine
    # abgebrochene Anmeldung nicht.
    _ensure_gate()
    # Anmeldungen haben Vorrang: ein ausgelassener Messpunkt ist folgenlos,
    # eine verzoegerte Anmeldung nicht. Geprueft wird jetzt die Schleuse
    # selbst (wartende + belegte Plaetze) - das alte globale Register-Lock
    # gibt es nicht mehr, es war der Flaschenhals bei den Anmeldungen.
    if _admission_waiting or _admission_held:
        return
    if _heartbeat_sem.locked():
        return   # Rückstau -> diesen Messpunkt überspringen statt aufstauen
    async with _heartbeat_sem:
        await _handle_heartbeat(client_id, payload)


# Zeitpunkt des letzten Aufraeumens der Messwert-Historie (siehe unten).
_last_prune = [0]


async def _handle_heartbeat(client_id, payload):
    """
    Eigentliche Heartbeat-Verarbeitung (siehe on_heartbeat).

    Die Datenbankzugriffe laufen in einem Thread, NICHT im Event-Loop.
    Grund: Die Datenbank liegt im Bind-Mount, auf einem NAS also auf einem
    langsamen Dateisystem. Ein einzelnes hängendes Schreiben blockierte
    bisher das gesamte Backend - keine HTTP-Antwort, keine Socket.IO-Pings,
    alle Agenten fliegen gleichzeitig raus. Genau dieses Muster steht im
    Log vom 22.08. um 23:38:29.

    Der Heartbeat ist der mit Abstand häufigste Schreibpfad (jeder Agent
    alle fünf Sekunden), deshalb wird er zuerst ausgelagert.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, db.touch_client, client_id)

    # Auto-Update auch für BEREITS verbundene Clients: Wird eine neue
    # Agent-Version ausgerollt, während der Client online ist, greift der
    # Register-Check nicht mehr. Deshalb hier ein sparsamer (max. 1x/60s pro
    # Client) Versions-Check, der ggf. das Auto-Update anstößt.
    now_check = time.monotonic()
    if now_check - _auto_update_hb_check.get(client_id, 0.0) >= 60.0:
        _auto_update_hb_check[client_id] = now_check
        try:
            _c = await db.call(db.get_client, client_id)
            if _c:
                await _maybe_auto_update(client_id, _c.get("agent_version"))
        except Exception:
            pass

    metrics = payload.get("metrics", {})
    now_ms = int(time.time() * 1000)
    metrics["ts"] = now_ms
    state.live_metrics[client_id] = metrics

    # --- Persistenz: höchstens alle metrics_interval_seconds einen Punkt speichern ---
    # (Heartbeats kommen alle paar Sekunden, gespeichert wird aber nur im
    #  eingestellten Takt, damit die DB nicht vollläuft.)
    # Der Messpunkt wird geschrieben UND aufgeraeumt - beides sind Schreib-
    # zugriffe auf eine Tabelle, die schnell sehr gross wird. Sie liefen
    # bisher synchron im Event-Loop; ein einziges langsames fsync legte damit
    # das ganze Backend still. Jetzt: Arbeits-Thread.
    try:
        interval_ms = (await db.call(db.get_int_setting,
                                     "metrics_interval_seconds")) * 1000
        last_save = state.last_metric_save.get(client_id, 0)
        if now_ms - last_save >= interval_ms:
            state.last_metric_save[client_id] = now_ms
            mem_total = metrics.get("memTotal") or 0
            cpu = metrics.get("cpuLoad") or 0
            ram = (metrics.get("memUsed", 0) / mem_total * 100) if mem_total else 0
            await db.call(
                db.record_metric_point,
                client_id, cpu, ram,
                metrics.get("netIn") or 0, metrics.get("netOut") or 0,
                now_ms,
                extra=_json_dumps_safe(metrics),
            )
            # Alles ausserhalb der Aufbewahrungsdauer wegraeumen.
            # 0 (oder negativ) = unbegrenzt aufbewahren -> NIE prunen.
            # Das Aufraeumen laeuft hoechstens alle fuenf Minuten, nicht bei
            # jedem Messpunkt: Es war bei vielen Agenten der teuerste
            # Einzelvorgang im Heartbeat-Pfad und trotzdem hundertfach
            # redundant - geloescht ist geloescht.
            retention_hours = await db.call(db.get_int_setting,
                                            "metrics_retention_hours")
            if retention_hours > 0 and now_ms - _last_prune[0] >= 300_000:
                _last_prune[0] = now_ms
                retention_ms = retention_hours * 3600 * 1000
                await db.call(db.prune_metrics_history, now_ms - retention_ms)
    except Exception as e:
        print(f"[metrics] Konnte Messpunkt nicht speichern: {e}")

    await sio.emit("client:metrics", {"id": client_id, "metrics": metrics}, namespace="/dashboard")

    # Benachrichtigungs-Regeln: Metrik-Schwellwerte (CPU/RAM/Disk/Temp).
    # Höchstens alle 30 s pro Client prüfen - den eigentlichen Alarm-Cooldown
    # (kein Spam) übernimmt die Regel selbst (notifier._cooled_down).
    now_check2 = time.monotonic()
    if now_check2 - _threshold_check.get(client_id, 0.0) >= 30.0:
        _threshold_check[client_id] = now_check2
        try:
            from app import notifier
            _c = await db.call(db.get_client, client_id)
            if _c:
                await notifier.check_metric_thresholds(_c, metrics)
        except Exception as e:
            print(f"[notify] Schwellwert-Prüfung fehlgeschlagen: {e}")


# ------------------------------------------------------------------
# VPN: Meldungen der Nodes
# ------------------------------------------------------------------
# Seit die Tunnel auf den Nodes enden (echtes WireGuard) trägt das Backend
# keine Nutzdaten mehr. Was hier ankommt, sind nur noch Zustandsmeldungen
# und - falls die Node hinter symmetrischem NAT sitzt - die Pakete des
# Relays. Die Handler sind bewusst dünn; die Logik liegt in der Port-Weiterleitung.

# VPN AUSGEBAUT (2026-09-20)
# ---------------------------
# Hier standen 'node-wg-state', 'wg-relay' und 'node-net-packet'. Der letzte
# war der gefaehrliche: JEDES einzelne IP-Paket eines Tunnels lief als
# Socket.IO-Ereignis durch diesen Event-Loop und wurde von dort per
# ensure_future weiterverteilt. Ein aktiver Tunnel hat damit dieselbe
# Schleife geflutet, die auch Anmeldungen, Shell und Screen bedient.
#
# Die Ereignisse werden jetzt bewusst NICHT mehr entgegengenommen. Ein alter
# Agent, der sie noch sendet, bekommt von python-socketio schlicht keine
# Reaktion - das ist folgenlos und kostet nichts.


# --- Port-Weiterleitung -------------------------------------------
# Sie benutzt dieselben Ereignisse wie frueher das VPN (vpn-open/-data/
# -close). Der Agent kennt sie bereits; nur die Gegenstelle im Backend ist
# jetzt eine andere - nicht mehr der (entfernte) Userspace-Stack, sondern
# unmittelbar die Weiterleitung.

@sio.on("vpn-open-result", namespace="/agent")
async def on_forward_open_result(sid, payload):
    portforward.on_open_result(payload or {})


@sio.on("vpn-data", namespace="/agent")
async def on_forward_data(sid, payload):
    portforward.on_data(payload or {})


@sio.on("vpn-close", namespace="/agent")
async def on_forward_close(sid, payload):
    portforward.on_close(payload or {})


# 'node-wg-result' und request_node_wg() sind mit dem VPN entfallen: Sie
# dienten ausschliesslich dazu, WireGuard auf einer Node einzurichten.


@sio.on("exec-result", namespace="/agent")
async def on_exec_result(sid, payload):
    """Antwort eines Agenten auf einen zuvor gesendeten 'exec'-Befehl."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("agent-action-log", namespace="/agent")
async def on_agent_action_log(sid, payload):
    """
    Fortschritts-/Fehlermeldung des Agenten zu Update/Uninstall. Wird direkt an
    die Dashboards weitergereicht, damit man in Echtzeit sieht, was auf dem
    Client passiert (statt nur eines generischen 60s-Timeouts).
    """
    await sio.emit("client:action-log", {
        "id": payload.get("id"),
        "kind": payload.get("kind"),
        "stage": payload.get("stage"),
        "detail": payload.get("detail"),
        "agent_version": payload.get("agent_version"),
    }, namespace="/dashboard")


@sio.on("fs-result", namespace="/agent")
async def on_fs_result(sid, payload):
    """Antwort eines Agenten auf eine zuvor gesendete 'fs-list'-Anfrage."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("patch-ping-result", namespace="/agent")
async def on_patch_ping_result(sid, payload):
    """
    Antwort auf die Fähigkeitsabfrage. Die gemeldeten Fähigkeiten werden
    gleich mit übernommen - so ist der Stand auch dann aktuell, wenn sich
    auf dem Client nach dem Anmelden etwas geändert hat (winget nachträglich
    installiert, Rechte geändert).
    """
    client_id = state.sid_to_client_id.get(sid)
    if client_id and payload.get("protocol"):
        _agent_caps[client_id] = {
            "protocol": int(payload.get("protocol") or 0),
            "sources": list(payload.get("sources") or []),
            "agent_version": payload.get("agent_version") or "",
            "agent_code_hash": payload.get("agent_code_hash") or "",
            "elevated": bool(payload.get("elevated")),
            "winget": payload.get("winget") or "",
        }
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("patch-selftest-result", namespace="/agent")
async def on_patch_selftest_result(sid, payload):
    """Antwort auf den Funktionstest."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("patch-progress", namespace="/agent")
async def on_patch_progress(sid, payload):
    """
    Zwischenstand eines laufenden Scans oder einer Installation.

    Ohne diese Meldungen war ein Patch-Auftrag eine Blackbox: minutenlang
    Stille, danach entweder ein Ergebnis oder eine Zeitüberschreitung - ohne
    jeden Hinweis, wo es geklemmt hat.
    """
    client_id = state.sid_to_client_id.get(sid) or payload.get("id")
    if not client_id:
        return
    try:
        patching.on_progress(client_id, payload)
    except Exception:
        pass
    await sio.emit("patch:progress", {"id": client_id, **payload},
                   namespace="/dashboard")


@sio.on("patch-scan-result", namespace="/agent")
async def on_patch_scan_result(sid, payload):
    """Antwort eines Agenten mit der Liste verfügbarer Aktualisierungen."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("patch-apply-result", namespace="/agent")
async def on_patch_apply_result(sid, payload):
    """Antwort eines Agenten nach dem Einspielen von Aktualisierungen."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("proc-result", namespace="/agent")
async def on_proc_result(sid, payload):
    """Antwort eines Agenten mit der Prozessliste."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("proc-kill-result", namespace="/agent")
async def on_proc_kill_result(sid, payload):
    """Antwort eines Agenten auf eine Prozess-Beenden-Anfrage."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("fs-read-result", namespace="/agent")
async def on_fs_read_result(sid, payload):
    """Antwort eines Agenten mit dem Inhalt einer Datei (Download)."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("fs-op-result", namespace="/agent")
async def on_fs_op_result(sid, payload):
    """Antwort eines Agenten auf eine schreibende FS-Operation
    (Upload/Ordner anlegen/Löschen/Umbenennen)."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("screen-frame", namespace="/agent")
async def on_screen_frame(sid, payload):
    """
    Ein Agent schickt ein Bildschirm-Frame. Wir leiten es an alle
    Dashboard-Clients weiter UND schneiden es mit, falls für diesen Client
    gerade eine Aufzeichnung läuft.
    """
    recording.record_frame(
        payload.get("id"),
        payload.get("image", ""),
        payload.get("width", 0),
        payload.get("height", 0),
    )
    await sio.emit("screen-frame", payload, namespace="/dashboard")


@sio.on("screen-error", namespace="/agent")
async def on_screen_error(sid, payload):
    """Ein Agent meldet, dass Screen-Streaming nicht möglich ist."""
    # Beim Start wurde bereits eine Aufzeichnung angelegt - die wäre jetzt ein
    # leeres 0-Frame-Replay. Sofort wieder löschen (Datei + DB). Der Audit-
    # Eintrag bleibt bewusst bestehen (Zugriffsversuch bleibt nachvollziehbar).
    recording.abort_recording(payload.get("id"))
    await sio.emit("screen-error", payload, namespace="/dashboard")


@sio.on("screen-mode", namespace="/agent")
async def on_screen_mode(sid, payload):
    """
    Ein Agent meldet, WIE Remote-Zugriff möglich ist (z.B. mode='shell', wenn
    kein grafischer Bildschirm vorhanden ist). Das Dashboard öffnet dann direkt
    eine Shell statt einen Fehler anzuzeigen.
    """
    await sio.emit("screen-mode", payload, namespace="/dashboard")


# --- Interaktives Terminal: Agent -> Dashboard weiterleiten ---
# Laufende Terminal-Sitzungen: session_id -> Verlaufspuffer (für EIN Audit-Log
# pro Sitzung mit der gesamten History, statt pro Befehl).
_term_sessions: dict = {}


# Terminal-Sitzungen, deren Ausgabe NICHT an die Dashboards geht, sondern gezielt an
# einen Empfaenger (z.B. die RMM-Konsole per SSH/Web): session -> {"output": async fn(str), "exit": async fn()}
term_listeners: dict[str, dict] = {}


@sio.on("term-output", namespace="/agent")
async def on_term_output(sid, payload):
    """Shell-Ausgabe an alle Dashboards weiterreichen + in die laufende
    Terminal-Aufzeichnung schreiben (Replay statt Einzel-Befehls-Log)."""
    session = payload.get("session")
    listener = term_listeners.get(session)
    if listener:                                  # gezielte Sitzung (Konsole) - nicht an alle Dashboards
        try:
            await listener["output"](payload.get("data", ""))
        except Exception:
            pass
        return
    if session in _term_sessions:
        recording.record_term_data(session, payload.get("data", ""))
    await sio.emit("term-output", payload, namespace="/dashboard")


@sio.on("term-exit", namespace="/agent")
async def on_term_exit(sid, payload):
    listener = term_listeners.pop(payload.get("session"), None)
    if listener:
        try:
            await listener["exit"]()
        except Exception:
            pass
        return
    await sio.emit("term-exit", payload, namespace="/dashboard")
    await _finalize_term_session(payload.get("session"))


# --- Agent-Konsole: Dashboard liest den Log des Agenten mit ---
# Dashboard -> Agent: Stream öffnen/schließen; Agent -> Dashboard: Historie
# und Live-Zeilen weiterreichen (gefiltert wird im Frontend über payload.id).

@sio.on("agent-console-open", namespace="/dashboard")
async def dashboard_agent_console_open(sid, data):
    client_id = data.get("clientId")
    if not await _ws_user_may(data.get("username"), "use_terminal", client_id):
        await sio.emit("agent-console", {
            "id": client_id,
            "data": "\r\n\x1b[31mKeine Berechtigung für die Agent-Konsole dieses Clients.\x1b[0m\r\n",
        }, namespace="/dashboard")
        return
    ok = await send_to_agent(client_id, "agent-console-open", {})
    await sio.emit("agent-console-ack", {"id": client_id, "agent_online": bool(ok)},
                   namespace="/dashboard")


@sio.on("agent-console-close", namespace="/dashboard")
async def dashboard_agent_console_close(sid, data):
    await send_to_agent(data.get("clientId"), "agent-console-close", {})


@sio.on("agent-console", namespace="/agent")
async def on_agent_console(sid, payload):
    await sio.emit("agent-console", payload, namespace="/dashboard")


@sio.on("agent-console-history", namespace="/agent")
async def on_agent_console_history(sid, payload):
    await sio.emit("agent-console-history", payload, namespace="/dashboard")


@sio.event(namespace="/agent")
async def disconnect(sid):
    """Ein Agent hat die Verbindung verloren/beendet -> als offline markieren."""
    # Zuerst den Schleusen-Platz freigeben: Bricht die Verbindung ab, BEVOR
    # sich der Agent angemeldet hat, dürfen die Wartenden nicht bis zum
    # Watchdog-Timeout blockiert bleiben.
    _admission_release(sid)
    client_id = state.sid_to_client_id.pop(sid, None)
    if client_id:
        state.client_id_to_sid.pop(client_id, None)
        state.live_metrics.pop(client_id, None)
        await sio.emit("client:offline", {"id": client_id}, namespace="/dashboard")
        _emit_clients_changed_soon()
        # Benachrichtigungs-Regeln: "Client geht offline".
        try:
            from app import notifier
            from router.admin_routes import build_notification
            _c = await db.call(db.get_client, client_id)
            _host = (_c or {}).get("hostname") or client_id
            await notifier.fire_event("client_offline", client_id=client_id,
                notification=build_notification(
                    f"{_host} ist offline gegangen.",
                    head=f"🔴 Client offline – {_host}",
                    client=_host, service="Status", level="warn"))
        except Exception as _e:
            print(f"[notify] offline-Event fehlgeschlagen: {_e}")


async def send_to_agent(client_id: str, event: str, data: dict) -> bool:
    """
    Schickt ein beliebiges Event an den Agenten eines Clients (ohne auf eine
    Antwort zu warten). Wird für Screen-Start/Stop/Input benutzt.
    Gibt True zurück, wenn der Client online war.
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        return False
    await sio.emit(event, data, to=sid, namespace="/agent")
    return True


# ====================================================================
# Handler für den /dashboard Namespace (Verbindungen vom Browser-Frontend)
# ====================================================================
# Hinweis: Hier bewusst (noch) keine Prüfung des Login-Tokens beim reinen
# Verbindungsaufbau - die REST-Routen sind bereits per JWT abgesichert.
# Für eine noch strengere Absicherung könnte man hier zusätzlich das JWT
# im "auth"-Objekt prüfen, analog zum Agent-Token oben.

@sio.on("connect", namespace="/dashboard")
async def dashboard_connect(sid, environ, auth):
    # Beim Verbinden die BOOT_ID des laufenden Backend-Prozesses schicken. Das
    # Frontend erkennt daran einen Neustart (BOOT_ID ändert sich) und lädt neu -
    # so entfällt das sekündliche Polling von /api/boot-id.
    try:
        from app.main import BOOT_ID
        await sio.emit("boot:id", {"boot_id": BOOT_ID}, to=sid, namespace="/dashboard")
    except Exception:
        pass


@sio.on("disconnect", namespace="/dashboard")
async def dashboard_disconnect(sid):
    # Falls dieser Client die Backend-Ausgabe abonniert hatte, austragen.
    try:
        from app.loghub import hub as _h
        _h.subscribers.discard(sid)
    except Exception:
        pass


# --- Rechteprüfung im (unauthentifizierten) Dashboard-Namespace ------------
# Der Dashboard-Namespace hat keinen JWT-Handshake; wir vertrauen dem vom
# Frontend mitgeschickten Benutzernamen (best effort, analog zum Audit-Log).
# Die REST-Routen bleiben die harte Sicherheitsgrenze; hier verhindern wir
# zusätzlich versehentliche/naive Zugriffe ohne Recht.
async def _ws_user_may(username: str | None, perm: str,
                       client_id: str | None) -> bool:
    # Jetzt async: Die Rechtepruefung liest den Benutzer aus der Datenbank.
    # Vorher lief dieser Lesezugriff synchron im Event-Loop - bei jedem
    # Start eines Remote-Bildschirms und jeder Terminal-Sitzung.
    from app import db as _db
    from app.auth import user_has_permission
    if not username:
        return False
    u = await _db.call(_db.get_user_by_username, username)
    if not u:
        return False
    return user_has_permission(u, perm, client_id)


@sio.on("screen-start", namespace="/dashboard")
async def dashboard_screen_start(sid, data):
    """Dashboard möchte den Bildschirm eines Clients sehen -> Agent anweisen + Aufnahme starten."""
    from app import db as _db
    client_id = data.get("clientId")
    if not await _ws_user_may(data.get("username"), "use_screen", client_id):
        await sio.emit("screen-error",
                       {"id": client_id, "error": "Keine Berechtigung für den Remote-Bildschirm dieses Clients."},
                       namespace="/dashboard")
        return

    # Aufnahme-Qualität/FPS aus den Einstellungen an den Agent mitgeben, damit
    # er die Frames passend erzeugt (bessere/schlechtere Qualität, mehr/weniger
    # Bilder). Der Agent nutzt diese Werte fürs JPEG-Encoding & die Sende-Rate.
    quality = await _db.call(_db.get_int_setting, "screen_record_quality") or 40
    fps = await _db.call(_db.get_int_setting, "screen_record_fps") or 5

    # Zustimmung am Gerät: Physische Geräte UND VMs bekommen eine Abfrage
    # angezeigt (z.B. wenn jemand per RDP in der VM arbeitet) - der Bildschirm
    # startet erst nach Bestätigung. Ist niemand angemeldet, verbindet der
    # Agent direkt. Nur LXC-Container sind ausgenommen (keine grafische
    # Anmeldung möglich).
    client = await _db.call(_db.get_client, client_id)
    needs_consent = bool(client) and (client.get("device_type") or "physical") != "lxc"

    # Silent-Modus (einmalig): Hat der Benutzer den Modus im Profil aktiviert
    # UND das Recht 'screen_silent' (global) bzw. 'c_screen_silent' auf diesem
    # Client, wird die Zustimmungs-Abfrage am Gerät übersprungen. Direkt beim
    # Verbindungsaufbau schaltet sich der Modus automatisch wieder AUS, damit
    # er wirklich nur für DIESE eine Sitzung gilt.
    _u = await _db.call(_db.get_user_by_username, data.get("username") or "")
    if needs_consent and _u and _u.get("silent_screen"):
        from app.auth import is_super_admin as _is_sa
        if (_is_sa(_u) or user_has_permission(_u, "screen_silent")
                or user_has_permission(_u, "c_screen_silent", client_id)):
            needs_consent = False
            await _db.call(_db.set_user_silent_screen, _u["id"], False)  # einmalig
            await _db.call(_db.add_audit_entry, _u["username"],
                           "screen.silent_used", target=client_id,
                           details=client.get("hostname") if client else client_id)
            print(f"[screen] Silent-Modus von {_u['username']} genutzt -> keine "
                  f"Abfrage, Modus wieder deaktiviert")
            # Alle Dashboards informieren: Das Profil (falls offen) schaltet den
            # Toggle dann sofort sichtbar wieder AUS.
            await sio.emit("silent-screen:consumed",
                           {"username": _u["username"],
                            "client": client.get("hostname") if client else client_id},
                           namespace="/dashboard")
    print(f"[screen] Start für {client.get('hostname') if client else client_id}: "
          f"device_type={client.get('device_type') if client else '?'} -> require_consent={needs_consent}")

    await send_to_agent(client_id, "screen-start", {
        "quality": quality, "fps": fps,
        "require_consent": needs_consent,
        "requested_by": data.get("username", ""),
        # Mehrbenutzer (Windows): vom Dashboard gewaehlte Sitzung des Benutzers,
        # dessen Bildschirm gesehen/gesteuert werden soll.
        "session": data.get("session"),
    })

    # Aufzeichnung nur starten, wenn global aktiviert.
    recording_on = await _db.call(_db.get_setting, "recording_enabled", "1") == "1"

    # Aufzeichnung starten (falls Client bekannt und aktiviert) + Audit-Log.
    if client:
        rec_id = recording.start_recording(
            client_id, client["hostname"], data.get("username", "unbekannt"),
        ) if recording_on else None
        # Audit-Eintrag IMMER (Zugriff protokollieren) - mit Replay-Verknüpfung,
        # falls aufgezeichnet wurde.
        await _db.call(
            _db.add_audit_entry,
            data.get("username", "unbekannt"),
            "screen.session_started",
            target=client_id,
            details=f"rec:{rec_id}" if rec_id else client["hostname"],
        )


@sio.on("screen-stop", namespace="/dashboard")
async def dashboard_screen_stop(sid, data):
    """Dashboard hat das VNC-Fenster geschlossen -> Streaming + Aufnahme stoppen."""
    client_id = data.get("clientId")
    await send_to_agent(client_id, "screen-stop", {})
    recording.stop_recording(client_id)


@sio.on("screen-input", namespace="/dashboard")
async def dashboard_screen_input(sid, data):
    """Maus-/Tastatureingabe vom Dashboard an den Agenten weiterreichen."""
    client_id = data.pop("clientId", None)
    if client_id:
        await send_to_agent(client_id, "screen-input", data)


@sio.on("screen-clipboard-get", namespace="/dashboard")
async def dashboard_screen_clipboard_get(sid, data):
    """Dashboard möchte die Zwischenablage des Remote-PCs holen (Clipboard-Sync)."""
    client_id = data.get("clientId")
    if client_id:
        ok = await send_to_agent(client_id, "screen-clipboard-get", {})
        if not ok:
            await sio.emit("screen-clipboard",
                           {"id": client_id, "error": "Client ist offline."},
                           namespace="/dashboard")


# --- Chat-/Steuerfenster der laufenden Fernwartung ---
# --- Registry (nur Windows, Recht c_registry je Client) ---
@sio.on("registry", namespace="/dashboard")
async def dashboard_registry(sid, data):
    client_id = data.get("clientId")
    op = data.get("op") or "list"
    # Schreiben/Loeschen braucht c_registry; Lesen (list/get) ebenso (Registry kann sensibel sein).
    if not await _ws_user_may(data.get("username"), "c_registry", client_id):
        await sio.emit("registry-result", {"id": client_id, "rid": data.get("rid"), "op": op,
                       "ok": False, "error": "Keine Berechtigung (Registry)."}, namespace="/dashboard")
        return
    ok = await send_to_agent(client_id, "registry", {k: data.get(k) for k in ("op", "path", "name", "type", "data", "rid")})
    if not ok:
        await sio.emit("registry-result", {"id": client_id, "rid": data.get("rid"), "op": op,
                       "ok": False, "error": "Client ist offline."}, namespace="/dashboard")


@sio.on("registry-result", namespace="/agent")
async def on_registry_result(sid, payload):
    await sio.emit("registry-result", payload, namespace="/dashboard")


# --- Windows-Automatik-Updates aus/ein (Recht c_win_update) ---
@sio.on("wu-auto", namespace="/dashboard")
async def dashboard_wu_auto(sid, data):
    from app import db as _db
    client_id = data.get("clientId")
    setting = "disabled" in data
    perm = "c_win_update" if setting else "c_patch_view"
    if not await _ws_user_may(data.get("username"), perm, client_id):
        await sio.emit("wu-auto", {"id": client_id, "supported": True,
                       "error": "Keine Berechtigung."}, namespace="/dashboard")
        return
    payload = {"disabled": bool(data.get("disabled"))} if setting else {}
    ok = await send_to_agent(client_id, "wu-auto", payload)
    if setting and ok:
        await _db.call(_db.add_audit_entry, data.get("username", "?"),
                       "winupdate.auto_" + ("disabled" if data.get("disabled") else "enabled"), target=client_id)
    if not ok:
        await sio.emit("wu-auto", {"id": client_id, "supported": True, "error": "Client ist offline."},
                       namespace="/dashboard")


@sio.on("wu-auto", namespace="/agent")
async def on_wu_auto(sid, payload):
    await sio.emit("wu-auto", payload, namespace="/dashboard")


@sio.on("screen-session-chat", namespace="/dashboard")
async def dashboard_session_chat(sid, data):
    """Admin tippt im VNC-Fenster -> an den Agenten (zeigt es der Person vor dem PC)."""
    client_id = data.get("clientId")
    if not await _ws_user_may(data.get("username"), "use_screen", client_id):
        return
    await send_to_agent(client_id, "screen-session-chat", {"text": (data.get("text") or "")[:2000]})


@sio.on("screen-session-chat", namespace="/agent")
async def on_session_chat(sid, payload):
    await sio.emit("screen-session-chat", payload, namespace="/dashboard")


@sio.on("screen-session-grant", namespace="/agent")
async def on_session_grant(sid, payload):
    # Benutzer hat Admin-Rechte erlaubt/entzogen -> ans Dashboard + Audit.
    from app import db as _db
    cid = (payload or {}).get("id")
    action = "granted" if payload.get("granted") else "revoked"
    detail = "silent (ohne Benutzerfreigabe)" if payload.get("silent") else "durch Benutzer"
    await _db.call(_db.add_audit_entry, "system",
                   "screen.admin_" + action, target=cid, details=detail)
    await sio.emit("screen-session-grant", payload, namespace="/dashboard")


@sio.on("screen-session-disconnect", namespace="/agent")
async def on_session_disconnect(sid, payload):
    # Benutzer hat die Fernwartung getrennt -> Aufnahme stoppen + Dashboard informieren.
    cid = (payload or {}).get("id")
    recording.stop_recording(cid)
    await sio.emit("screen-session-disconnect", payload, namespace="/dashboard")


@sio.on("screen-clipboard", namespace="/agent")
async def on_screen_clipboard(sid, payload):
    """Agent liefert den Inhalt der Remote-Zwischenablage -> ans Dashboard."""
    await sio.emit("screen-clipboard", payload, namespace="/dashboard")


@sio.on("screen-set-monitor", namespace="/dashboard")
async def dashboard_screen_set_monitor(sid, data):
    """Bildschirm-Wechsel (Multi-Monitor) an den Agenten weiterreichen."""
    client_id = data.get("clientId")
    monitor = data.get("monitor")
    if client_id and monitor is not None:
        await send_to_agent(client_id, "screen-set-monitor", {"monitor": int(monitor)})


# --- Mehrbenutzer-Auswahl: "erst einen Benutzer waehlen" ---
@sio.on("screen-users", namespace="/dashboard")
async def dashboard_screen_users(sid, data):
    """Dashboard will wissen, welche Benutzer am Geraet angemeldet sind."""
    from app import db as _db
    client_id = data.get("clientId")
    if not await _ws_user_may(data.get("username"), "use_screen", client_id):
        return
    ok = await send_to_agent(client_id, "screen-users", {})
    if not ok:
        await sio.emit("screen-users", {"id": client_id, "users": [], "multi": False,
                                        "error": "Client ist offline."}, namespace="/dashboard")


@sio.on("screen-users", namespace="/agent")
async def on_screen_users(sid, payload):
    await sio.emit("screen-users", payload, namespace="/dashboard")


# --- Virtueller Bildschirmtreiber (headless, Modul "headless-screen") ---
def _vdisplay_pkg():
    """URL + SHA256 des Treiberpakets, wenn das Modul es bereitgestellt hat."""
    try:
        from app import modules as _m
        if _m.is_enabled("headless-screen"):
            return _m.use("headless-screen", "vdriver").package_info()
    except Exception as e:
        print(f"[vdisplay] Paketinfo nicht verfuegbar: {e}")
    return {"url": "", "sha256": ""}


@sio.on("vdisplay-status", namespace="/dashboard")
async def dashboard_vdisplay_status(sid, data):
    client_id = data.get("clientId")
    if not await _ws_user_may(data.get("username"), "use_screen", client_id):
        return
    await send_to_agent(client_id, "vdisplay-status", {})


@sio.on("vdisplay-install", namespace="/dashboard")
async def dashboard_vdisplay_install(sid, data):
    from app import db as _db
    client_id = data.get("clientId")
    if not await _ws_user_may(data.get("username"), "manage_headless_screen", client_id):
        await sio.emit("vdisplay-result", {"id": client_id, "action": "install", "ok": False,
                       "error": "Keine Berechtigung (manage_headless_screen)."}, namespace="/dashboard")
        return
    pkg = _vdisplay_pkg()
    if not pkg.get("url"):
        await sio.emit("vdisplay-result", {"id": client_id, "action": "install", "ok": False,
                       "error": "Kein Treiberpaket auf dem Server. Modul „Headless-Bildschirm“ aktivieren."},
                       namespace="/dashboard")
        return
    await send_to_agent(client_id, "vdisplay-install", pkg)
    await _db.call(_db.add_audit_entry, data.get("username", "?"), "headless.vdisplay_install",
                   target=client_id)


@sio.on("vdisplay-remove", namespace="/dashboard")
async def dashboard_vdisplay_remove(sid, data):
    from app import db as _db
    client_id = data.get("clientId")
    if not await _ws_user_may(data.get("username"), "manage_headless_screen", client_id):
        await sio.emit("vdisplay-result", {"id": client_id, "action": "remove", "ok": False,
                       "error": "Keine Berechtigung (manage_headless_screen)."}, namespace="/dashboard")
        return
    await send_to_agent(client_id, "vdisplay-remove", {})
    await _db.call(_db.add_audit_entry, data.get("username", "?"), "headless.vdisplay_remove",
                   target=client_id)


@sio.on("vdisplay-status", namespace="/agent")
async def on_vdisplay_status(sid, payload):
    await sio.emit("vdisplay-status", payload, namespace="/dashboard")


@sio.on("vdisplay-result", namespace="/agent")
async def on_vdisplay_result(sid, payload):
    await sio.emit("vdisplay-result", payload, namespace="/dashboard")


# ------------------------------------------------------------------
# EXPERIMENTELLES RDP-GATEWAY (für headless VMs)
# Das Dashboard bittet das Backend, sich selbst per RDP zur VM zu verbinden
# und die Frames über denselben "screen-frame"-Kanal zu streamen.
# ------------------------------------------------------------------

async def _rdp_emit_frame(client_id, jpeg_b64, width, height):
    await sio.emit("screen-frame", {
        "id": client_id, "image": jpeg_b64, "width": width, "height": height,
    }, namespace="/dashboard")


async def _rdp_emit_error(client_id, message):
    await sio.emit("screen-error", {"id": client_id, "error": message}, namespace="/dashboard")


@sio.on("rdp-start", namespace="/dashboard")
async def dashboard_rdp_start(sid, data):
    """
    Startet einen RDP-Verbindungsversuch des Backends zur VM. Nur wenn pyrdp
    verfügbar ist - sonst bekommt das Dashboard einen klaren Hinweis auf den
    nativen .rdp-Weg.
    """
    from app import db as _db
    client_id = data.get("clientId")
    client = (await _db.call(_db.get_client, client_id)) if client_id else None
    if not client:
        await _rdp_emit_error(client_id, "Client nicht gefunden.")
        return

    if not rdp_gateway.is_available():
        await _rdp_emit_error(
            client_id,
            "Eingebettetes RDP (pyrdp) ist auf dem Server nicht installiert. "
            "Bitte den Button 'Per RDP verbinden (nativ)' nutzen - das öffnet den "
            "Windows-RDP-Client und funktioniert zuverlässig auch bei headless VMs."
        )
        return

    host = client.get("ip")
    if not host:
        await _rdp_emit_error(client_id, "Keine IP-Adresse für diesen Client bekannt.")
        return

    loop = asyncio.get_event_loop()
    started = rdp_gateway.start_session(
        client_id, host, data.get("port", 3389),
        data.get("username", ""), data.get("password", ""),
        loop, _rdp_emit_frame, _rdp_emit_error,
    )
    if not started:
        await _rdp_emit_error(client_id, "RDP-Session konnte nicht gestartet werden.")


@sio.on("rdp-stop", namespace="/dashboard")
async def dashboard_rdp_stop(sid, data):
    client_id = data.get("clientId")
    if client_id:
        rdp_gateway.stop_session(client_id)


_term_sessions_UNUSED = None  # (Puffer oben in der Agent-Sektion definiert)


# --- Interaktives Terminal: Dashboard -> Agent weiterleiten ---
@sio.on("term-open", namespace="/dashboard")
async def dashboard_term_open(sid, data):
    """Startet eine interaktive Shell-Session auf dem Ziel-Client."""
    client_id = data.get("clientId")
    shell = data.get("shell", "auto")
    session = data.get("session")
    if not await _ws_user_may(data.get("username"), "use_terminal", client_id):
        await sio.emit("term-output", {
            "id": client_id, "session": session,
            "data": "\r\n\x1b[31mKeine Berechtigung für das Terminal dieses Clients.\x1b[0m\r\n",
        }, namespace="/dashboard")
        await sio.emit("term-exit", {"id": client_id, "session": session}, namespace="/dashboard")
        return
    ok = await send_to_agent(client_id, "term-open", {
        "session": session,
        "shell": shell,
        "cols": data.get("cols", 80),
        "rows": data.get("rows", 24),
    })
    # Bestätigung ans Dashboard: beweist, dass DIESES (aktuelle) Backend das
    # Event verarbeitet hat, und meldet, ob der Ziel-Agent online ist. Damit
    # kann das Frontend genau unterscheiden: kein ack = Backend veraltet/nicht
    # neu gestartet; ack mit agent_online=false = Client offline; ack aber keine
    # Ausgabe = Agent veraltet (kein PTY).
    await sio.emit("term-ack", {"session": session, "agent_online": bool(ok)},
                   namespace="/dashboard")
    if ok:
        # Sitzung anlegen + Aufzeichnung starten: Die gesamte Sitzung wird als
        # Terminal-Replay mitgeschnitten (wie der Screen-Recorder), statt
        # Befehle und Ausgaben einzeln zu loggen. Audit-Eintrag mit Replay-Link
        # folgt beim Schließen (term-close/exit).
        import time as _t
        from app import db as _db
        rec_id = None
        if await _db.call(_db.get_setting, "recording_enabled", "1") == "1":
            client = await _db.call(_db.get_client, client_id)
            rec_id = recording.start_term_recording(
                session, client_id,
                (client or {}).get("hostname") or client_id,
                data.get("username", "unbekannt"),
            )
        _term_sessions[session] = {
            "client_id": client_id,
            "username": data.get("username", "unbekannt"),
            "shell": shell,
            "started": _t.time(),
            "rec_id": rec_id,
        }
    else:
        await sio.emit("term-output", {
            "id": client_id, "session": session,
            "data": "\r\n\x1b[31mClient ist offline.\x1b[0m\r\n",
        }, namespace="/dashboard")
        await sio.emit("term-exit", {"id": client_id, "session": session},
                       namespace="/dashboard")


@sio.on("term-input", namespace="/dashboard")
async def dashboard_term_input(sid, data):
    await send_to_agent(data.get("clientId"), "term-input", {
        "session": data.get("session"), "data": data.get("data", ""),
    })


@sio.on("term-resize", namespace="/dashboard")
async def dashboard_term_resize(sid, data):
    await send_to_agent(data.get("clientId"), "term-resize", {
        "session": data.get("session"),
        "cols": data.get("cols", 80), "rows": data.get("rows", 24),
    })


@sio.on("term-close", namespace="/dashboard")
async def dashboard_term_close(sid, data):
    await send_to_agent(data.get("clientId"), "term-close", {
        "session": data.get("session"),
    })
    await _finalize_term_session(data.get("session"))


async def _finalize_term_session(session: str) -> None:
    """Beendet die Terminal-Aufzeichnung (Replay) und schreibt EINEN kompakten
    Audit-Eintrag mit Verweis auf das Replay - statt Befehle/Ausgaben einzeln
    ins Log zu schreiben."""
    from app import db as _db
    meta = _term_sessions.pop(session, None)
    if not meta:
        return
    import time as _t
    rec_id = recording.stop_term_recording(session)
    dur = int(_t.time() - meta["started"])
    details = f"shell:{meta['shell']} dauer:{dur}s"
    if rec_id:
        # Gleiche Verknüpfung wie beim Screen-Recording: das Audit-Log kann
        # daraus einen "Aufzeichnung ansehen"-Link machen.
        details = f"rec:{rec_id} " + details
    await _db.call(_db.add_audit_entry, meta["username"], "terminal.session",
                   target=meta["client_id"], details=details)


# ==========================================================================
# BACKEND-AUSGABE (Live-Log) - "Source"-Tab
# --------------------------------------------------------------------------
# Read-only: streamt die Konsolenausgabe (stdout/stderr) des laufenden
# Backend-Prozesses (run.py) an eingeschriebene Admin-Dashboards. Es gibt KEINE
# Eingabe/Shell mehr - nur Anzeige. Auth über das JWT des Admins.
# ==========================================================================

from app.loghub import hub as _loghub_hub


def _admin_from_token(token: str):
    """Gibt den Admin-User zurück, wenn das Token gültig und role=='admin' ist."""
    try:
        from app.auth import decode_access_token
        payload = decode_access_token(token)
        u = db.get_user_by_id(payload.get("sub"))
        if u and u.get("role") == "admin":
            return u
    except Exception:
        pass
    return None


@sio.on("backend-log-open", namespace="/dashboard")
async def backend_log_open(sid, data):
    """Admin abonniert die Backend-Ausgabe; bekommt zuerst den Verlauf."""
    if not _admin_from_token(data.get("token")):
        await sio.emit("backend-log-history",
                       {"data": "[Nicht autorisiert - nur Super-Admins]\n"},
                       to=sid, namespace="/dashboard")
        return
    # Event-Loop merken (für Broadcasts aus Fremd-Threads) + Zuhörer registrieren.
    _loghub_hub.set_loop(asyncio.get_event_loop())
    _loghub_hub.subscribers.add(sid)
    await sio.emit("backend-log-history", {"data": _loghub_hub.history()},
                   to=sid, namespace="/dashboard")


@sio.on("backend-log-close", namespace="/dashboard")
async def backend_log_close(sid, data):
    _loghub_hub.subscribers.discard(sid)

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

# Der eigentliche Socket.IO-Server. async_mode="asgi" bedeutet: er läuft
# zusammen mit FastAPI im selben modernen Python-Webserver-Standard (ASGI).
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")


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


async def request_patch_scan(client_id: str, timeout_seconds: float = 600.0) -> dict:
    """
    Lässt einen Agenten nach verfügbaren Aktualisierungen suchen.
    Großzügiger Timeout: die Windows-Update-Abfrage braucht auf trägen
    Systemen regelmäßig mehrere Minuten.
    """
    sid = state.client_id_to_sid.get(client_id)
    if not sid:
        raise RuntimeError("Client ist offline")

    request_id = _new_request_id()
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    state.pending_requests[request_id] = future

    await sio.emit("patch-scan", {"requestId": request_id}, to=sid, namespace="/agent")

    try:
        payload = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)

    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


async def request_patch_apply(client_id: str, items: list[dict],
                              timeout_seconds: float = 3600.0) -> dict:
    """
    Lässt einen Agenten die benannten Aktualisierungen installieren.
    Sehr langer Timeout - Feature-Updates laufen durchaus eine Stunde.
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
        payload = await asyncio.wait_for(future, timeout=timeout_seconds)
    finally:
        state.pending_requests.pop(request_id, None)

    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload


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
_AGENT_VERSION_FILE = _Path(__file__).resolve().parents[2] / "agent" / "version.txt"


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


def _agent_outdated(agent_v: str, latest_v: str) -> bool:
    if not agent_v or not latest_v:
        return False
    try:
        return _version_tuple(agent_v) < _version_tuple(latest_v)
    except Exception:
        return agent_v.strip() != latest_v.strip()


async def _maybe_auto_update(client_id: str, agent_version: str | None) -> None:
    """Stößt bei veralteter Version das Selbst-Update an, falls Auto-Update greift."""
    latest = _latest_agent_version()
    if not _agent_outdated(agent_version or "", latest):
        _auto_update_last.pop(client_id, None)   # aktuell -> Cooldown zurücksetzen
        return

    client = db.get_client(client_id)
    if not client or not client.get("active", 1):
        return
    mode = (client.get("auto_update") or "global").lower()
    if mode == "off":
        return
    if mode != "on" and db.get_setting("agent_auto_update", "0") != "1":
        return

    now = time.monotonic()
    last = _auto_update_last.get(client_id)
    if last is not None and now - last < _AUTO_UPDATE_COOLDOWN_S:
        return   # kürzlich schon versucht -> keine Update-Schleife bauen
    _auto_update_last[client_id] = now

    ok = await send_to_agent(client_id, "update-agent", {})
    if ok:
        db.add_audit_entry(None, "agent.auto_update_triggered", target=client_id,
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
    Wir prüfen hier das AGENT_TOKEN - ohne das richtige Token wird die
    Verbindung sofort wieder abgelehnt (ConnectionRefusedError).
    """
    token = (auth or {}).get("token")
    if token != AGENT_TOKEN:
        raise socketio.exceptions.ConnectionRefusedError("Ungültiges Agent-Token")


@sio.on("register", namespace="/agent")
async def on_register(sid, payload):
    """
    Ein Agent meldet sich frisch an (direkt nach dem Verbindungsaufbau).
    payload enthält: id, hostname, platform, arch, release, ip
    und optional "enrollment_token" (falls der Agent über die
    "Client hinzufügen"-Funktion installiert wurde).
    """
    client_id = payload.get("id")
    hostname = payload.get("hostname")
    if not client_id or not hostname:
        return  # unvollständige Daten -> ignorieren

    state.client_id_to_sid[client_id] = sid
    state.sid_to_client_id[sid] = client_id

    # Kommt der Agent frisch aus einem Update? Dann Zeitpunkt merken, damit der
    # Update-Endpunkt das Update als erfolgreich bestätigen kann, und das
    # Dashboard informieren.
    if payload.get("updated"):
        import time as _time
        state.update_confirmed[client_id] = _time.monotonic()
        await sio.emit("client:updated", {"id": client_id}, namespace="/dashboard")
        # Benachrichtigungs-Regel: Agent wurde aktualisiert
        try:
            from app import notifier
            from app.routers.admin_routes import build_notification
            _host = payload.get("hostname") or client_id
            await notifier.fire_event("agent_update", client_id=client_id,
                notification=build_notification(
                    f"Agent auf {_host} wurde auf Version "
                    f"{payload.get('agent_version') or '?'} aktualisiert.",
                    head=f"⬆️ Agent aktualisiert – {_host}",
                    client=_host, service="Agent-Update", level="success"))
        except Exception:
            pass

    # Gemeldete Agent-Version speichern (für den "veraltet"-Hinweis im Dashboard).
    db.set_agent_version(client_id, payload.get("agent_version"))

    # "Alle Agenten aktualisieren" mit Offline-Option: War der Client beim
    # Massen-Update offline und wurde VORGEMERKT (pending_agent_update), wird
    # das Update jetzt - direkt beim Wiederverbinden - nachgeholt.
    _pending_client = db.get_client(client_id)
    if _pending_client and _pending_client.get("pending_agent_update"):
        db.set_pending_agent_update(client_id, False)
        if _agent_outdated(payload.get("agent_version") or "", _latest_agent_version()):
            ok = await send_to_agent(client_id, "update-agent", {})
            if ok:
                db.add_audit_entry(None, "agent.pending_update_triggered", target=client_id,
                                   details=(_pending_client.get("hostname") or client_id))
                await sio.emit("client:update-started", {"id": client_id, "auto": True},
                               namespace="/dashboard")
    else:
        # Automatisches Agent-Update: Ist der Agent veraltet und Auto-Update für
        # diesen Client aktiv (pro Client 'on' oder 'global' + Settings-Schalter),
        # wird das Update direkt beim Verbinden angestoßen. Über das Setting
        # 'agent_auto_update_offline' ("1" = Standard) lässt sich steuern, ob
        # Clients, die offline waren, beim Wiederverbinden aktualisiert werden.
        if db.get_setting("agent_auto_update_offline", "1") == "1":
            await _maybe_auto_update(client_id, payload.get("agent_version"))

    # Merken, ob der Client vor dem Upsert schon existierte (für den
    # "Neuer Client registriert"-Trigger der Benachrichtigungs-Regeln).
    _was_known = db.get_client(client_id) is not None

    # Basis-Infos speichern (legt den Client an, falls neu, sonst aktualisieren)
    db.upsert_client(
        client_id,
        hostname,
        payload.get("platform"),
        payload.get("arch"),
        payload.get("release"),
        payload.get("ip"),
    )

    # Automatisch erkannter Gerätetyp (VM/LXC): wird nur GESPEICHERT
    # (detected_device_type) - übernommen wird er erst nach Bestätigung durch
    # den Nutzer im Client-Panel (device_type_ack).
    if db.apply_detected_device_type(client_id, payload.get("device_type")):
        await sio.emit("clients:changed", namespace="/dashboard")

    # Absturz-Meldung des Agenten (Crash-Schutz: Agent startet sich selbst neu
    # und meldet den Traceback beim nächsten Registrieren) -> Audit + dem
    # Nutzer im Dashboard anzeigen.
    _crash = payload.get("last_crash")
    if _crash:
        _c = db.get_client(client_id)
        _host = (_c or {}).get("hostname") or payload.get("hostname") or client_id
        db.add_audit_entry(None, "agent.crashed", target=client_id, details=str(_crash)[:1000])
        print(f"[agent-crash] {_host}: Agent ist abgestürzt und wurde neu gestartet:\n{_crash}")
        await sio.emit("client:agent-crashed", {
            "id": client_id, "hostname": _host, "error": str(_crash)[-2000:],
        }, namespace="/dashboard")

    # Nach dem Speichern den aktuellen Stand aus der DB holen
    client = db.get_client(client_id)

    # Client einem Tenant/Standort zuordnen, FALLS er noch keinem zugeordnet ist.
    # (Ein bereits manuell zugeordneter Client wird NICHT überschrieben.)
    if client and not client["tenant_id"]:
        enrollment_token = payload.get("enrollment_token")
        token_row = db.get_enrollment_token(enrollment_token) if enrollment_token else None

        if token_row and token_row["tenant_id"]:
            # Fall 1: Gültiger Token mit Tenant/Location -> dorthin einsortieren
            update_fields = {
                "tenant_id": token_row["tenant_id"],
                "location_id": token_row["location_id"],
            }
            # Optionaler Wunschname aus dem Onboarding-Dialog
            if token_row["client_name"]:
                update_fields["hostname"] = token_row["client_name"]
            db.update_client(client_id, update_fields)
            db.mark_enrollment_token_used(enrollment_token)
        else:
            # Fall 2: Kein (brauchbarer) Token-Tenant -> ins "Uncategorized"-Tenant
            uncategorized_tenant, default_location = db.ensure_uncategorized()
            update_fields = {
                "tenant_id": uncategorized_tenant,
                "location_id": default_location,
            }
            # Falls der Token trotzdem einen Wunschnamen trug, diesen übernehmen
            if token_row and token_row["client_name"]:
                update_fields["hostname"] = token_row["client_name"]
            db.update_client(client_id, update_fields)
            if enrollment_token:
                db.mark_enrollment_token_used(enrollment_token)

    # Dashboard(s) informieren, dass sich die Client-Liste geändert hat
    await sio.emit("client:online", {"id": client_id}, namespace="/dashboard")
    await sio.emit("clients:changed", namespace="/dashboard")

    # Benachrichtigungs-Regeln: "Client kommt online" / "Neuer Client".
    try:
        from app import notifier
        from app.routers.admin_routes import build_notification
        _host = hostname or client_id
        await notifier.fire_event("client_online", client_id=client_id,
            notification=build_notification(
                f"{_host} ist jetzt online.",
                head=f"🟢 Client online – {_host}",
                client=_host, service="Status", level="success"))
        if not _was_known:
            await notifier.fire_event("client_new", client_id=client_id,
                notification=build_notification(
                    f"Neuer Client {_host} wurde registriert.",
                    head=f"✨ Neuer Client – {_host}",
                    client=_host, service="Onboarding", level="info"))
    except Exception as _e:
        print(f"[notify] online-Event fehlgeschlagen: {_e}")


@sio.on("heartbeat", namespace="/agent")
async def on_heartbeat(sid, payload):
    """Ein Agent meldet alle paar Sekunden seine aktuellen Metriken (CPU/RAM/Disk/...)."""
    client_id = payload.get("id")
    if not client_id:
        return
    db.touch_client(client_id)

    # Auto-Update auch für BEREITS verbundene Clients: Wird eine neue
    # Agent-Version ausgerollt, während der Client online ist, greift der
    # Register-Check nicht mehr. Deshalb hier ein sparsamer (max. 1x/60s pro
    # Client) Versions-Check, der ggf. das Auto-Update anstößt.
    now_check = time.monotonic()
    if now_check - _auto_update_hb_check.get(client_id, 0.0) >= 60.0:
        _auto_update_hb_check[client_id] = now_check
        try:
            _c = db.get_client(client_id)
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
    try:
        interval_ms = db.get_int_setting("metrics_interval_seconds") * 1000
        last_save = state.last_metric_save.get(client_id, 0)
        if now_ms - last_save >= interval_ms:
            state.last_metric_save[client_id] = now_ms
            mem_total = metrics.get("memTotal") or 0
            cpu = metrics.get("cpuLoad") or 0
            ram = (metrics.get("memUsed", 0) / mem_total * 100) if mem_total else 0
            db.record_metric_point(
                client_id, cpu, ram,
                metrics.get("netIn") or 0, metrics.get("netOut") or 0,
                now_ms,
                extra=_json_dumps_safe(metrics),
            )
            # Alles außerhalb der Aufbewahrungsdauer wegräumen.
            # 0 (oder negativ) = unbegrenzt aufbewahren -> NIE prunen.
            retention_hours = db.get_int_setting("metrics_retention_hours")
            if retention_hours > 0:
                retention_ms = retention_hours * 3600 * 1000
                db.prune_metrics_history(now_ms - retention_ms)
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
            _c = db.get_client(client_id)
            if _c:
                await notifier.check_metric_thresholds(_c, metrics)
        except Exception as e:
            print(f"[notify] Schwellwert-Prüfung fehlgeschlagen: {e}")


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
    from app import recording
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
    from app import recording
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


@sio.on("term-output", namespace="/agent")
async def on_term_output(sid, payload):
    """Shell-Ausgabe an alle Dashboards weiterreichen + in die laufende
    Terminal-Aufzeichnung schreiben (Replay statt Einzel-Befehls-Log)."""
    from app import recording
    session = payload.get("session")
    if session in _term_sessions:
        recording.record_term_data(session, payload.get("data", ""))
    await sio.emit("term-output", payload, namespace="/dashboard")


@sio.on("term-exit", namespace="/agent")
async def on_term_exit(sid, payload):
    await sio.emit("term-exit", payload, namespace="/dashboard")
    _finalize_term_session(payload.get("session"))


# --- Agent-Konsole: Dashboard liest den Log des Agenten mit ---
# Dashboard -> Agent: Stream öffnen/schließen; Agent -> Dashboard: Historie
# und Live-Zeilen weiterreichen (gefiltert wird im Frontend über payload.id).

@sio.on("agent-console-open", namespace="/dashboard")
async def dashboard_agent_console_open(sid, data):
    client_id = data.get("clientId")
    if not _ws_user_may(data.get("username"), "use_terminal", client_id):
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
    client_id = state.sid_to_client_id.pop(sid, None)
    if client_id:
        state.client_id_to_sid.pop(client_id, None)
        state.live_metrics.pop(client_id, None)
        await sio.emit("client:offline", {"id": client_id}, namespace="/dashboard")
        await sio.emit("clients:changed", namespace="/dashboard")
        # Benachrichtigungs-Regeln: "Client geht offline".
        try:
            from app import notifier
            from app.routers.admin_routes import build_notification
            _c = db.get_client(client_id)
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
def _ws_user_may(username: str | None, perm: str, client_id: str | None) -> bool:
    from app import db as _db
    from app.auth import user_has_permission
    if not username:
        return False
    u = _db.get_user_by_username(username)
    if not u:
        return False
    return user_has_permission(u, perm, client_id)


@sio.on("screen-start", namespace="/dashboard")
async def dashboard_screen_start(sid, data):
    """Dashboard möchte den Bildschirm eines Clients sehen -> Agent anweisen + Aufnahme starten."""
    from app import recording, db as _db
    client_id = data.get("clientId")
    if not _ws_user_may(data.get("username"), "use_screen", client_id):
        await sio.emit("screen-error",
                       {"id": client_id, "error": "Keine Berechtigung für den Remote-Bildschirm dieses Clients."},
                       namespace="/dashboard")
        return

    # Aufnahme-Qualität/FPS aus den Einstellungen an den Agent mitgeben, damit
    # er die Frames passend erzeugt (bessere/schlechtere Qualität, mehr/weniger
    # Bilder). Der Agent nutzt diese Werte fürs JPEG-Encoding & die Sende-Rate.
    quality = _db.get_int_setting("screen_record_quality") or 40
    fps = _db.get_int_setting("screen_record_fps") or 5

    # Zustimmung am Gerät: Physische Geräte UND VMs bekommen eine Abfrage
    # angezeigt (z.B. wenn jemand per RDP in der VM arbeitet) - der Bildschirm
    # startet erst nach Bestätigung. Ist niemand angemeldet, verbindet der
    # Agent direkt. Nur LXC-Container sind ausgenommen (keine grafische
    # Anmeldung möglich).
    client = _db.get_client(client_id)
    needs_consent = bool(client) and (client.get("device_type") or "physical") != "lxc"

    # Silent-Modus (einmalig): Hat der Benutzer den Modus im Profil aktiviert
    # UND das Recht 'screen_silent' (global) bzw. 'c_screen_silent' auf diesem
    # Client, wird die Zustimmungs-Abfrage am Gerät übersprungen. Direkt beim
    # Verbindungsaufbau schaltet sich der Modus automatisch wieder AUS, damit
    # er wirklich nur für DIESE eine Sitzung gilt.
    _u = _db.get_user_by_username(data.get("username") or "")
    if needs_consent and _u and _u.get("silent_screen"):
        from app.auth import is_super_admin as _is_sa
        if (_is_sa(_u) or user_has_permission(_u, "screen_silent")
                or user_has_permission(_u, "c_screen_silent", client_id)):
            needs_consent = False
            _db.set_user_silent_screen(_u["id"], False)   # einmalig -> wieder aus
            _db.add_audit_entry(_u["username"], "screen.silent_used", target=client_id,
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
    })

    # Aufzeichnung nur starten, wenn global aktiviert.
    recording_on = _db.get_setting("recording_enabled", "1") == "1"

    # Aufzeichnung starten (falls Client bekannt und aktiviert) + Audit-Log.
    if client:
        rec_id = recording.start_recording(
            client_id, client["hostname"], data.get("username", "unbekannt"),
        ) if recording_on else None
        # Audit-Eintrag IMMER (Zugriff protokollieren) - mit Replay-Verknüpfung,
        # falls aufgezeichnet wurde.
        _db.add_audit_entry(
            data.get("username", "unbekannt"),
            "screen.session_started",
            target=client_id,
            details=f"rec:{rec_id}" if rec_id else client["hostname"],
        )


@sio.on("screen-stop", namespace="/dashboard")
async def dashboard_screen_stop(sid, data):
    """Dashboard hat das VNC-Fenster geschlossen -> Streaming + Aufnahme stoppen."""
    from app import recording
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
    from app import rdp_gateway, db as _db
    client_id = data.get("clientId")
    client = _db.get_client(client_id) if client_id else None
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
    from app import rdp_gateway
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
    if not _ws_user_may(data.get("username"), "use_terminal", client_id):
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
        from app import recording, db as _db
        rec_id = None
        if _db.get_setting("recording_enabled", "1") == "1":
            client = _db.get_client(client_id)
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
    _finalize_term_session(data.get("session"))


def _finalize_term_session(session: str) -> None:
    """Beendet die Terminal-Aufzeichnung (Replay) und schreibt EINEN kompakten
    Audit-Eintrag mit Verweis auf das Replay - statt Befehle/Ausgaben einzeln
    ins Log zu schreiben."""
    from app import db as _db, recording
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
    _db.add_audit_entry(meta["username"], "terminal.session",
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

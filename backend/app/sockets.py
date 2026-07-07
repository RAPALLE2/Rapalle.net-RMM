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


# ====================================================================
# Handler für den /agent Namespace (Verbindungen von den Agent-Programmen)
# ====================================================================

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

    # Basis-Infos speichern (legt den Client an, falls neu, sonst aktualisieren)
    db.upsert_client(
        client_id,
        hostname,
        payload.get("platform"),
        payload.get("arch"),
        payload.get("release"),
        payload.get("ip"),
    )

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


@sio.on("heartbeat", namespace="/agent")
async def on_heartbeat(sid, payload):
    """Ein Agent meldet alle paar Sekunden seine aktuellen Metriken (CPU/RAM/Disk/...)."""
    client_id = payload.get("id")
    if not client_id:
        return
    db.touch_client(client_id)

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


@sio.on("exec-result", namespace="/agent")
async def on_exec_result(sid, payload):
    """Antwort eines Agenten auf einen zuvor gesendeten 'exec'-Befehl."""
    future = state.pending_requests.get(payload.get("requestId"))
    if future and not future.done():
        future.set_result(payload)


@sio.on("fs-result", namespace="/agent")
async def on_fs_result(sid, payload):
    """Antwort eines Agenten auf eine zuvor gesendete 'fs-list'-Anfrage."""
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


@sio.event(namespace="/agent")
async def disconnect(sid):
    """Ein Agent hat die Verbindung verloren/beendet -> als offline markieren."""
    client_id = state.sid_to_client_id.pop(sid, None)
    if client_id:
        state.client_id_to_sid.pop(client_id, None)
        state.live_metrics.pop(client_id, None)
        await sio.emit("client:offline", {"id": client_id}, namespace="/dashboard")
        await sio.emit("clients:changed", namespace="/dashboard")


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
    pass  # Verbindung wird einfach angenommen


@sio.on("disconnect", namespace="/dashboard")
async def dashboard_disconnect(sid):
    pass  # nichts Besonderes zu tun


@sio.on("screen-start", namespace="/dashboard")
async def dashboard_screen_start(sid, data):
    """Dashboard möchte den Bildschirm eines Clients sehen -> Agent anweisen + Aufnahme starten."""
    from app import recording, db as _db
    client_id = data.get("clientId")
    await send_to_agent(client_id, "screen-start", {})

    # Aufzeichnung starten (falls Client bekannt) + im Audit-Log vermerken
    client = _db.get_client(client_id)
    if client:
        rec_id = recording.start_recording(
            client_id,
            client["hostname"],
            data.get("username", "unbekannt"),
        )
        # Audit-Eintrag mit Verknüpfung zur Aufzeichnung (rec:<id> im Detail-Feld,
        # damit das Frontend einen "Aufzeichnung ansehen"-Button anzeigen kann)
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

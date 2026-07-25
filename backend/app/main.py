"""
main.py
-------
Der zentrale Einstiegspunkt des Backends. Verbindet drei Dinge zu EINER
Anwendung, die mit einem einzigen Befehl gestartet wird:

  1. FastAPI          -> die REST-API (/api/...)
  2. Socket.IO         -> die Echtzeit-Verbindungen (/socket.io/...)
  3. Statische Dateien -> das komplette Frontend (HTML/CSS/JS) aus dem
                          "frontend"-Ordner, unter der Wurzel "/"

Das bedeutet: du brauchst NUR diesen einen Python-Prozess zu starten
(siehe run.py) - kein separater Webserver, kein Node/npm nötig, um das
Dashboard im Browser zu sehen. Öffne einfach http://localhost:4000
"""

from pathlib import Path

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import db
from app import loghub as _loghub
_loghub.install()  # idempotent: fängt Backend-Ausgabe ab (falls nicht via run.py)
from app.config import CORS_ORIGIN
from app.sockets import sio
from app.routers import (
    auth_routes,
    users_routes,
    hierarchy_routes,
    clients_routes,
    network_routes,
    files_routes,
    enrollment_routes,
    audit_routes,
    recordings_routes,
    scripts_routes,
    admin_routes,
    agent_update_routes,
    update_routes,
    database_routes,
    guac_routes,
    source_routes,
    relay_routes,
    speedtest_routes,
    ai_routes,
    tickets_routes,
    games_routes,
    chat_routes,
    notify_routes,
    notes_routes,
    media_routes,
    calendar_routes,
    todos_routes,
    privacy_routes,
    patch_routes,
)

# 1) Datenbank initialisieren (legt Tabellen an, erzeugt admin/admin falls nötig)
# Vorher: Ist der externe Datenbank-Modus aktiv, den Stand von extern laden
# (danach laufen init_db-Migrationen ganz normal über den geladenen Stand).
from app import dbsync as _dbsync
_dbsync.startup_restore_if_external()
db.init_db()

# Beim Start alle Aufbewahrungsfristen anwenden (DSGVO Art. 5 Abs. 1 lit. e).
# Die Fristen stehen in den Einstellungen; privacy.purge() ist die einzige
# Stelle, die sie durchsetzt - danach läuft sie täglich als Hintergrund-Job.
try:
    from app import privacy as _privacy
    _report = _privacy.purge()
    _removed = sum(v for v in _report.values() if isinstance(v, int))
    if _removed:
        print(f"[privacy] Aufbewahrungsfristen angewendet: {_report}")
except Exception as e:
    print(f"[privacy] Fristen konnten beim Start nicht angewendet werden: {e}")

# 2) FastAPI-App erstellen und alle Routen-Module einhängen
api = FastAPI(title="RAPALLE.net RMM Backend")

# Host-Sperre: Zugriff nur über die in den Einstellungen hinterlegten
# Adressen. Steht ganz vorne, damit unerlaubte Hosts nicht einmal
# CORS-Header zu sehen bekommen. Standardmäßig ausgeschaltet.
from app.hostlock import HostLockMiddleware as _HostLock
api.add_middleware(_HostLock)

api.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN] if CORS_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

api.include_router(auth_routes.router)
api.include_router(users_routes.router)
api.include_router(hierarchy_routes.router)
api.include_router(clients_routes.router)
api.include_router(network_routes.router)
api.include_router(files_routes.router)
api.include_router(enrollment_routes.router)
api.include_router(audit_routes.router)
api.include_router(recordings_routes.router)
api.include_router(scripts_routes.router)
api.include_router(admin_routes.router)
api.include_router(agent_update_routes.router)
api.include_router(update_routes.router)
api.include_router(database_routes.router)
api.include_router(guac_routes.router)
api.include_router(source_routes.router)
api.include_router(relay_routes.router)
api.include_router(speedtest_routes.router)
api.include_router(ai_routes.router)          # AI-Chat (Verbindungen + Proxy)
api.include_router(tickets_routes.router)     # Ticket-System
api.include_router(games_routes.router)       # Gaming-Hub-Scoreboard
api.include_router(chat_routes.router)        # Chat (DMs + Gruppen)
api.include_router(notify_routes.router)      # Benachrichtigungs-Regeln + SMTP
api.include_router(notes_routes.router)       # Client-Notizen (Sichtbarkeit + Protokoll)
api.include_router(calendar_routes.router)    # Organigramm + Kalender
api.include_router(media_routes.router)       # Medien-Bibliothek des Audio-Players
api.include_router(todos_routes.router)       # Persönliche Todo-Liste (privat)
api.include_router(privacy_routes.router)     # DSGVO: Auskunft, Löschung, Fristen
api.include_router(patch_routes.router)       # Software-Patching

# Guacamole-WebSocket-Tunnel (Browser <-> guacd). Muss VOR dem statischen
# Frontend-Mount registriert werden, damit die Route greift.
api.add_api_websocket_route("/guac/tunnel", guac_routes.tunnel_endpoint)


# ------------------------------------------------------------------
# Versionen: Single Source of Truth sind die version.txt-Dateien
# (backend/version.txt und agent/version.txt). Grundlage für das
# spätere Auto-Update: Agents/Frontends vergleichen ihre Version
# gegen /api/version und aktualisieren sich bei Abweichung.
# ------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Eindeutige ID DIESES Backend-Prozesses. Ändert sich bei jedem (Neu-)Start.
# Das Frontend pollt sie und lädt sich neu, sobald sie sich ändert -> nach
# einem Backend-Restart aktualisiert sich die Webconsole von selbst.
import uuid as _uuid
BOOT_ID = _uuid.uuid4().hex


def _read_version(path: Path, fallback: str = "0.0.0") -> str:
    try:
        v = path.read_text(encoding="utf-8").strip()
        return v or fallback
    except OSError:
        return fallback


def get_backend_version() -> str:
    return _read_version(_PROJECT_ROOT / "backend" / "version.txt")


def get_agent_version() -> str:
    return _read_version(_PROJECT_ROOT / "agent" / "version.txt")


@api.get("/api/health")
async def health_check():
    """Einfacher Endpunkt zum Prüfen, ob das Backend läuft."""
    return {"ok": True, "name": "RAPALLE.net RMM", "version": get_backend_version()}


@api.get("/api/version")
async def versions():
    """
    Zentrale Versionsauskunft (kein Auth - enthält keine Geheimnisse):
    Backend- und Agent-Version aus den jeweiligen version.txt-Dateien.
    'boot_id' identifiziert den laufenden Prozess (Auto-Reload nach Restart).
    """
    return {"backend": get_backend_version(), "agent": get_agent_version(), "boot_id": BOOT_ID}


@api.get("/api/boot-id")
async def boot_id():
    """Leichter Endpunkt, den das Frontend pollt, um Backend-Neustarts zu erkennen."""
    return {"boot_id": BOOT_ID}


from fastapi import Request as _Request


@api.get("/api/server-address")
async def server_address(request: _Request):
    """
    Liefert die "beste" öffentlich erreichbare Basis-URL des Backends - für
    Dinge wie den Explorer-Relay (Netzlaufwerk-URL) und Install-Befehle.

    Priorität:
      1. server_url  (vollständige, manuell gesetzte URL, z.B. https://rmm.firma.de)
      2. server_domain bzw. server_host + server_backend_port
      3. Fallback: Host aus der aufgerufenen Anfrage (request), ersetzt aber
         'localhost'/'127.0.0.1' NICHT durch sich selbst - stattdessen wird der
         Host-Header genutzt, damit die Adresse für ENTFERNTE Rechner stimmt.

    Das Frontend soll diese URL statt 'localhost' anzeigen, da ein
    Netzlaufwerk auf einem ANDEREN PC 'localhost' nie erreichen kann.
    """
    # 1) Komplette URL gesetzt?
    server_url = (db.get_setting("server_url") or "").strip()
    if server_url:
        # Sicherstellen, dass ein Schema vorhanden ist - sonst wird die Adresse
        # im Browser als RELATIVER Pfad interpretiert (führte zu /<ip>/dav 404).
        if not server_url.lower().startswith(("http://", "https://")):
            server_url = f"{request.url.scheme}://{server_url}"
        return {"base_url": server_url.rstrip("/"), "source": "server_url",
                "backend_port": (db.get_setting("server_backend_port") or "4000").strip()}

    # 2) Domain/Host + Port aus den Einstellungen
    domain = (db.get_setting("server_domain") or "").strip()
    host = (db.get_setting("server_host") or "").strip()
    backend_port = (db.get_setting("server_backend_port") or "4000").strip()

    chosen = domain or host
    # Falls versehentlich mit Schema eingetragen (z.B. "http://10.0.0.1"),
    # das Schema entfernen - wir setzen es selbst.
    chosen = chosen.replace("https://", "").replace("http://", "").strip("/")
    scheme = request.url.scheme or "http"
    if chosen:
        # Bei Standard-Ports keinen Port anhängen
        if (scheme == "http" and backend_port == "80") or (scheme == "https" and backend_port == "443"):
            base = f"{scheme}://{chosen}"
        else:
            base = f"{scheme}://{chosen}:{backend_port}"
        return {"base_url": base, "source": "settings", "backend_port": backend_port}

    # 3) Fallback: Host-Header der Anfrage (funktioniert für den aufrufenden
    #    Browser). Ist das 'localhost'/'127.0.0.1' (Dashboard lokal geöffnet),
    #    wird stattdessen die automatisch erkannte LAN-IP des Backends
    #    eingesetzt (config.HOST) - so ist die Adresse IMMER dynamisch aus
    #    einer Variablen befüllt und für ENTFERNTE Rechner gültig, nie ein
    #    hartes 'localhost'.
    host_header = request.headers.get("host") or request.url.netloc
    host_only = host_header.split(":")[0]
    is_localhost = host_only in ("localhost", "127.0.0.1", "::1")
    if is_localhost:
        from app.config import HOST as _detected_host
        if _detected_host and _detected_host != "0.0.0.0":
            port = host_header.split(":")[1] if ":" in host_header else backend_port
            host_header = f"{_detected_host}:{port}"
            is_localhost = False
    base = f"{scheme}://{host_header}"
    return {"base_url": base.rstrip("/"), "source": "request",
            "backend_port": backend_port,
            "is_localhost": is_localhost}


# ------------------------------------------------------------------
# Automation-Engine: prüft jede Minute, ob geplante Automationen fällig
# sind, und führt deren Befehl auf den Ziel-Clients aus. Läuft als
# Hintergrund-Task im selben Prozess.
# ------------------------------------------------------------------
import asyncio as _asyncio
from app.sockets import request_exec as _request_exec


async def _automation_engine():
    while True:
        try:
            for auto in db.get_due_automations():
                client_ids = [c for c in (auto["client_ids"] or "").split(",") if c]
                run_id = _uuid.uuid4().hex   # gruppiert diesen Durchlauf
                for cid in client_ids:
                    hostname = None
                    try:
                        c = db.get_client(cid)
                        hostname = c["hostname"] if c else None
                    except Exception:
                        pass
                    try:
                        res = await _request_exec(cid, auto["command"], timeout_seconds=60)
                        db.record_automation_result(
                            run_id, auto["id"], cid, hostname,
                            res.get("stdout", ""), res.get("stderr", ""),
                            res.get("code"), ok=True,
                        )
                    except Exception as e:
                        print(f"[automation] '{auto['name']}' auf {cid} fehlgeschlagen: {e}")
                        db.record_automation_result(
                            run_id, auto["id"], cid, hostname,
                            "", str(e), None, ok=False,
                        )
                db.mark_automation_run(auto["id"])
                db.add_audit_entry("system", "automation.executed", target=auto["id"], details=auto["name"])
        except Exception as e:
            print(f"[automation] Engine-Fehler: {e}")
        await _asyncio.sleep(30)  # alle 30 Sekunden prüfen


# ------------------------------------------------------------------
# Uptime-Monitor: prüft die an Clients gebundenen Websites (client_websites
# mit monitor_enabled=1) im jeweils konfigurierten Intervall per HTTP-Request.
# Je nach monitor_notify wird benachrichtigt:
#   'up'     -> nur wenn ein Scan erfolgreich war  (beim Statuswechsel down->up
#               bzw. beim allerersten erfolgreichen Scan)
#   'down'   -> nur wenn ein Scan fehlgeschlagen ist (beim Statuswechsel up->down
#               bzw. beim allerersten fehlgeschlagenen Scan)
#   'always' -> nach JEDEM Scan (Erfolg und Fehler)
# Benachrichtigt wird über ALLE konfigurierten Webhooks UND als normale
# In-App-Notification (Socket-Event "notify" an alle Dashboards).
# ------------------------------------------------------------------
import urllib.request as _urlreq
import urllib.error as _urlerr

from app.routers.admin_routes import build_notification as _build_notification
from app.routers.admin_routes import send_webhook as _send_webhook


def _check_website(url: str) -> tuple[bool, str | None]:
    """Ruft die URL auf (synchron, läuft via to_thread). 2xx/3xx = up.
    Rückgabe: (ok, fehlermeldung)."""
    req = _urlreq.Request(url, headers={"User-Agent": "RAPALLE-RMM-UptimeMonitor/1.0"})
    try:
        with _urlreq.urlopen(req, timeout=15) as resp:
            code = getattr(resp, "status", 200)
            if 200 <= code < 400:
                return True, None
            return False, f"HTTP {code}"
    except _urlerr.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


async def _notify_uptime(site: dict, ok: bool, error: str | None) -> None:
    """Baut die Notification und verschickt sie an Webhooks + Dashboards."""
    client = db.get_client(site["client_id"]) or {}
    tenant_name = location_name = None
    try:
        if client.get("tenant_id"):
            tenant_name = next((t["name"] for t in db.list_tenants()
                                if t["id"] == client["tenant_id"]), None)
        if client.get("location_id"):
            location_name = next((l["name"] for l in db.list_locations()
                                  if l["id"] == client["location_id"]), None)
    except Exception:
        pass

    if ok:
        head = f"✅ Website erreichbar – {site['name']}"
        body_txt = f"{site['url']} hat auf den Uptime-Scan geantwortet (UP)."
        level = "success"
    else:
        head = f"🚨 Website nicht erreichbar – {site['name']}"
        body_txt = f"{site['url']} hat auf den Uptime-Scan NICHT geantwortet (DOWN)." \
                   + (f" Fehler: {error}" if error else "")
        level = "error"

    notification = _build_notification(
        body_txt, head=head, body=body_txt,
        tenant=tenant_name, location=location_name,
        client=client.get("hostname"), service="Uptime-Monitor", level=level,
    )

    # 1) Alle Webhooks (synchroner HTTP-Call -> in Thread auslagern)
    for hook in db.list_webhooks():
        try:
            await _asyncio.to_thread(_send_webhook, hook, notification)
        except Exception as e:
            print(f"[uptime] Webhook '{hook.get('name')}' fehlgeschlagen: {e}")

    # 2) Normale In-App-Notification an alle verbundenen Dashboards
    try:
        await sio.emit("notify", {
            "message": f"{head}\n{body_txt}",
            "level": level,
        }, namespace="/dashboard")
    except Exception as e:
        print(f"[uptime] Dashboard-Notify fehlgeschlagen: {e}")

    db.add_audit_entry("system", "website.uptime", target=site["client_id"],
                       details=f"{site['name']} -> {'UP' if ok else 'DOWN'}")

    # 3) Benachrichtigungs-Regeln (website_up / website_down)
    try:
        from app import notifier as _notifier
        await _notifier.fire_event("website_up" if ok else "website_down",
                                   client_id=site["client_id"],
                                   notification=notification,
                                   dedupe_key=f"{site['id']}:{'up' if ok else 'down'}")
    except Exception as e:
        print(f"[uptime] Regel-Dispatch fehlgeschlagen: {e}")


async def _uptime_monitor_engine():
    while True:
        try:
            import time as _time
            now_ms = int(_time.time() * 1000)
            for site in db.list_monitored_websites():
                interval_ms = max(10, int(site["monitor_interval_seconds"] or 300)) * 1000
                last = site["last_checked"] or 0
                if now_ms - last < interval_ms:
                    continue  # noch nicht fällig

                ok, error = await _asyncio.to_thread(_check_website, site["url"])
                new_status = "up" if ok else "down"
                prev_status = site["last_status"]   # None = noch nie geprüft
                db.set_website_check_result(site["id"], new_status, error)

                mode = site["monitor_notify"] or "down"
                changed = prev_status != new_status  # inkl. allererstem Scan
                should_notify = (
                    mode == "always"
                    or (mode == "up" and ok and changed)
                    or (mode == "down" and not ok and changed)
                )
                if should_notify:
                    await _notify_uptime(site, ok, error)

                # Dashboards über neuen Status informieren (Panel aktualisiert Ampel)
                try:
                    await sio.emit("website:status", {
                        "id": site["id"], "client_id": site["client_id"],
                        "status": new_status,
                    }, namespace="/dashboard")
                except Exception:
                    pass
        except Exception as e:
            print(f"[uptime] Engine-Fehler: {e}")
        await _asyncio.sleep(5)  # alle 5 Sekunden prüfen, welche Scans fällig sind


@api.on_event("startup")
async def _start_background_tasks():
    # Wurde der Backend-Prozess zuvor durch einen Absturz beendet und von run.py
    # automatisch neu gestartet, wird das hier – nach erfolgreichem Hochfahren –
    # im Audit-Log vermerkt (Gegenstück zu 'backend.crash').
    try:
        if db.get_setting("backend_crash_pending", "0") == "1":
            db.add_audit_entry("system", "backend.restarted",
                               details="Automatischer Neustart nach Absturz")
            db.set_setting("backend_crash_pending", "0")
    except Exception as e:
        print(f"[startup] Crash-Recovery-Audit fehlgeschlagen: {e}")
    _asyncio.create_task(_automation_engine())
    _asyncio.create_task(_uptime_monitor_engine())
    # Garantie-Prüfschleife (Benachrichtigungs-Regeln warranty_expiring/expired)
    from app import notifier as _notifier
    _asyncio.create_task(_notifier.warranty_loop())
    _asyncio.create_task(_server_auto_update_engine())
    _asyncio.create_task(_db_sync_engine())
    _asyncio.create_task(_relay_expiry_engine())
    _asyncio.create_task(_privacy_purge_engine())
    from app import patching as _patching
    _asyncio.create_task(_patching.engine())


async def _relay_expiry_engine():
    """Schließt freigegebene Explorer-Relays automatisch, sobald ihr
    eingestellter Zeitpunkt (relay_expires_at) erreicht ist. Prüft jede Minute.
    Jede automatische Schließung wird – wie bei den Agents – im Audit-Log
    festgehalten (Aktion 'relay.auto_closed')."""
    await _asyncio.sleep(15)   # dem Start-Ansturm erst Ruhe gönnen
    while True:
        try:
            import time as _time
            now_ms = int(_time.time() * 1000)
            for c in db.list_expired_relay_clients(now_ms):
                db.set_client_relay_enabled(c["id"], False)
                db.add_audit_entry("system", "relay.auto_closed", target=c["id"],
                                   details=c.get("hostname"))
                # Offene Dashboards informieren, damit die Relay-Ansichten
                # (Explorer-Relay-App / Relay-Tab) sich aktualisieren.
                try:
                    await sio.emit("relay-changed",
                                   {"client_id": c["id"], "auto": True},
                                   namespace="/dashboard")
                except Exception:
                    pass
        except Exception as e:
            print(f"[relay] Auto-Close-Engine-Fehler: {e}")
        await _asyncio.sleep(60)


async def _db_sync_engine():
    """Spiegelt die lokale DB alle 60 s in die externe DB (nur bei Änderungen)."""
    while True:
        await _asyncio.sleep(60)
        try:
            loop = _asyncio.get_event_loop()
            await loop.run_in_executor(None, _dbsync.periodic_sync)
        except Exception as e:
            print(f"[dbsync] Engine-Fehler: {e}")


@api.on_event("shutdown")
async def _final_db_sync():
    """Beim Herunterfahren letzten Stand in die externe DB schreiben."""
    try:
        _dbsync.periodic_sync()
    except Exception as e:
        print(f"[dbsync] Finaler Sync fehlgeschlagen: {e}")


async def _server_auto_update_engine():
    """Prüft periodisch (alle 30 min), ob ein Server-Auto-Update ansteht."""
    await _asyncio.sleep(60)   # dem Backend erst Zeit zum Hochfahren geben
    while True:
        try:
            await update_routes.auto_update_tick()
        except Exception as e:
            print(f"[auto-update] Engine-Fehler: {e}")
        await _asyncio.sleep(1800)


# 3) Frontend-Ordner als statische Dateien einhängen.
#    Wichtig: das muss NACH den API-Routen passieren, sonst würden die
#    statischen Dateien alle Anfragen "wegschnappen" bevor die API sie sieht.
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

# Bilder (inkl. hochgeladenes Branding) VOR dem Catch-all-Mount ausliefern:
# zuerst aus dem schreibbaren Upload-Store, sonst aus dem gebündelten
# frontend/images. So sind ersetzte Logos sofort sichtbar, auch wenn der
# frontend-Ordner read-only ist.
from fastapi.responses import FileResponse as _FileResponse
from fastapi import HTTPException as _HTTPException
from app.routers.admin_routes import branding_path as _branding_path


@api.get("/images/{name}")
async def _serve_image(name: str):
    p = _branding_path(name)
    if p is None:
        raise _HTTPException(status_code=404, detail="Bild nicht gefunden")
    # no-cache: Browser MUSS revalidieren (Last-Modified/ETag kommen von
    # FileResponse). Ohne diesen Header cachen Browser Bilder heuristisch
    # stundenlang - ersetzte Branding-Logos erschienen dann nicht ("Bild
    # lädt nicht"), obwohl der Upload längst gespeichert war.
    return _FileResponse(str(p), headers={"Cache-Control": "no-cache"})


api.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


# 4) Socket.IO und FastAPI zu einer einzigen ASGI-Anwendung zusammenfügen.
#    "socket_app" ist das, was uvicorn am Ende tatsächlich startet (siehe run.py).
socket_app = socketio.ASGIApp(sio, other_asgi_app=api, socketio_path="socket.io")


async def _privacy_purge_engine():
    """
    Wendet die Aufbewahrungsfristen einmal täglich an (DSGVO Art. 5 Abs. 1
    lit. e - Speicherbegrenzung). Ohne diesen Job würden Fristen nur beim
    Neustart greifen; auf einem Server, der monatelang durchläuft, wären
    sie damit praktisch wirkungslos.
    """
    await _asyncio.sleep(120)          # Start-Ansturm abwarten
    while True:
        try:
            from app import privacy as _p
            result = _p.purge()
            total = sum(v for v in result.values() if isinstance(v, int))
            if total:
                print(f"[privacy] Täglicher Durchlauf: {result}")
        except Exception as e:
            print(f"[privacy] Täglicher Durchlauf fehlgeschlagen: {e}")
        await _asyncio.sleep(86400)

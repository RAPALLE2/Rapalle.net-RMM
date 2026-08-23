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
# Der Import selbst ist die erste Gefahrenstelle: Eine einzige Datei mit
# einem Syntaxfehler laesst hier ALLES scheitern, noch bevor _mount()
# ueberhaupt zum Zuge kaeme. Deshalb zuerst der gewoehnliche Sammelimport -
# und wenn der stolpert, werden die Module einzeln nachgeholt.
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
    docker_routes,
    guac_routes,
    source_routes,
    relay_routes,
    storage_routes,
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
    vpn_routes,
    node_routes,
    diag_routes,
)  # noqa: E501 - Sammelimport, Absicherung siehe _import_router() unten


def _import_router(name):
    """
    Holt einen Router einzeln nach.

    Wird nur gebraucht, wenn der Sammelimport oben scheitert - dann steht in
    der Container-Ausgabe genau, WELCHE Datei das Problem hat, statt eines
    Stacktraces, in dem der Name untergeht.
    """
    import importlib
    try:
        return importlib.import_module(f"app.routers.{name}")
    except Exception as exc:
        from app.errors import report, Codes
        report(Codes.BOOT_ROUTER, exc, f"Router-Datei '{name}.py' ist defekt",
               folge="Dieser Bereich fehlt, das Backend startet trotzdem")
        return None

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

# ---------------------------------------------------------------------------
# Beobachtung jeder einzelnen Anfrage
# ---------------------------------------------------------------------------
# Zweck: Nach einem Absturz soll im Protokoll stehen, WELCHE Anfrage zuletzt
# lief. Genau diese Angabe fehlte bisher - im Container-Log standen nur
# Zugriffszeilen von uvicorn, die erst NACH der Antwort geschrieben werden.
# Stirbt der Prozess mitten in einer Anfrage, taucht sie dort also nie auf.
#
# Diese Middleware schreibt deshalb:
#   * jede Anfrage, die ungewöhnlich lange dauert (mit Dauer und Pfad),
#   * jede Ausnahme mit vollständigem Stacktrace,
#   * und sie hält fest, welche Anfragen GERADE laufen. Der Loop-Wächter
#     nimmt diese Liste in seinen Stack-Abzug auf.
import time as _t
import traceback as _traceback
from starlette.middleware.base import BaseHTTPMiddleware as _BaseMW
from starlette.responses import JSONResponse as _JSONResp

# Ab dieser Dauer gilt eine Anfrage als auffällig langsam.
import os as _os_env

try:
    SLOW_REQUEST_S = float(_os_env.getenv("RMM_SLOW_REQUEST_S", "2.0") or 2.0)
except ValueError:
    SLOW_REQUEST_S = 2.0

# Was gerade in Bearbeitung ist: Kennung -> (Pfad, Startzeit)
INFLIGHT: dict[int, tuple[str, float]] = {}
_inflight_seq = 0


class _RequestWatch(_BaseMW):
    async def dispatch(self, request, call_next):
        global _inflight_seq
        _inflight_seq += 1
        key = _inflight_seq
        path = f"{request.method} {request.url.path}"
        started = _t.monotonic()
        INFLIGHT[key] = (path, started)
        try:
            response = await call_next(request)
        except Exception as exc:
            # Eine Ausnahme darf NIE die Verbindung einfach abreissen lassen -
            # der Browser wartet sonst bis in sein eigenes Zeitlimit. Lieber
            # eine ehrliche 500 MIT KENNCODE: Damit kann der Benutzer die
            # Meldung weitergeben und man findet die Stelle im Protokoll
            # sofort wieder, ohne nach Uhrzeiten zu suchen.
            duration = _t.monotonic() - started
            from app.errors import report as _report, Codes as _Codes
            code = _report(_Codes.API_UNHANDLED, exc, path,
                           dauer=f"{duration:.1f}s",
                           client=getattr(request.client, "host", "?"))
            return _JSONResp(
                status_code=500,
                content={"detail": f"Interner Fehler ({code}) in "
                                   f"{request.url.path}. Der Kenncode steht im "
                                   f"Server-Protokoll – dort ist auch die "
                                   f"Ursache vermerkt.",
                         "error_code": code})
        finally:
            duration = _t.monotonic() - started
            INFLIGHT.pop(key, None)
            if duration >= SLOW_REQUEST_S:
                try:
                    from app.errors import report as _report, Codes as _Codes
                    _report(_Codes.API_SLOW, None, path,
                            dauer=f"{duration:.1f}s")
                except Exception:
                    pass
        return response


api.add_middleware(_RequestWatch)

# Host-Sperre: Zugriff nur über die in den Einstellungen hinterlegten
# Adressen. Steht ganz vorne, damit unerlaubte Hosts nicht einmal
# CORS-Header zu sehen bekommen. Standardmäßig ausgeschaltet.
from app.hostlock import HostLockMiddleware as _HostLock
api.add_middleware(_HostLock)

# --- Fehlertexte in der Sprache des Benutzers ------------------------------
# Das Backend wirft seine HTTPExceptions weiterhin mit deutschem Text. Dieser
# Handler uebersetzt den Text kurz vor dem Senden - EINE Stelle statt ueber 200
# verstreuter Aufrufe. Die Sprache kommt aus dem Profil des angemeldeten
# Benutzers (users.language); ist niemand angemeldet, gilt die Server-Sprache.
# Unbekannte Texte bleiben unveraendert, es kann also nichts verlorengehen.
from fastapi import HTTPException as _HTTPExc
from fastapi.responses import JSONResponse as _JSONResponse
from app.i18n import translate_detail as _tr_detail, server_lang as _srv_lang


def _lang_of_request(request) -> str:
    """Sprache des anfragenden Benutzers, sonst Server-Sprache."""
    try:
        from app.auth import get_current_user
        user = get_current_user(request.headers.get("authorization"))
        if user and user.get("language"):
            return str(user["language"])
    except Exception:
        # Kein/ungueltiges Token: Das ist normal (Login, oeffentliche Seiten).
        pass
    return _srv_lang()


@api.exception_handler(_HTTPExc)
async def _translated_http_exception(request, exc: _HTTPExc):
    return _JSONResponse(
        status_code=exc.status_code,
        content={"detail": _tr_detail(exc.detail, _lang_of_request(request))},
        headers=getattr(exc, "headers", None),
    )


api.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN] if CORS_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _mount(name, module) -> None:
    """Bindet einen Router ein und ueberlebt es, wenn er kaputt ist."""
    try:
        api.include_router(module.router)
    except Exception as exc:
        from app.errors import report, Codes
        report(Codes.BOOT_ROUTER, exc,
               f"Router '{name}' konnte nicht eingebunden werden",
               folge=f"Die Endpunkte von {name} fehlen, der Rest laeuft")


# Jeder Router einzeln eingebunden.
#
# Frueher standen hier 34 nackte include_router()-Zeilen. Scheitert eine
# davon - eine kaputte Datei nach einem Update, ein fehlendes Modul -, wirft
# der Import beim Start, und das GESAMTE Backend startet nicht. Der Container
# landet dann in der Notfall-Fehlerseite, und alle 34 Bereiche sind weg, weil
# einer defekt war.
#
# _mount() bindet jeden Router einzeln ein und meldet einen Fehlschlag mit
# Kenncode. Ein defekter Bereich fehlt dann - alle anderen laufen.
_mount("auth_routes", auth_routes)
_mount("users_routes", users_routes)
_mount("hierarchy_routes", hierarchy_routes)
_mount("clients_routes", clients_routes)
_mount("network_routes", network_routes)
_mount("files_routes", files_routes)
_mount("enrollment_routes", enrollment_routes)
_mount("audit_routes", audit_routes)
_mount("recordings_routes", recordings_routes)
_mount("scripts_routes", scripts_routes)
_mount("admin_routes", admin_routes)
_mount("agent_update_routes", agent_update_routes)
_mount("update_routes", update_routes)
_mount("database_routes", database_routes)
_mount("docker_routes", docker_routes)   # Zusatzdienste im Container-Betrieb
_mount("guac_routes", guac_routes)
_mount("source_routes", source_routes)
_mount("relay_routes", relay_routes)
_mount("storage_routes", storage_routes)  # Storage/Deployment + /deployment
_mount("speedtest_routes", speedtest_routes)
_mount("ai_routes", ai_routes)          # AI-Chat (Verbindungen + Proxy)
_mount("tickets_routes", tickets_routes)     # Ticket-System
_mount("games_routes", games_routes)       # Gaming-Hub-Scoreboard
_mount("chat_routes", chat_routes)        # Chat (DMs + Gruppen)
_mount("notify_routes", notify_routes)      # Benachrichtigungs-Regeln + SMTP
_mount("notes_routes", notes_routes)       # Client-Notizen (Sichtbarkeit + Protokoll)
_mount("calendar_routes", calendar_routes)    # Organigramm + Kalender
_mount("media_routes", media_routes)       # Medien-Bibliothek des Audio-Players
_mount("todos_routes", todos_routes)       # Persönliche Todo-Liste (privat)
_mount("privacy_routes", privacy_routes)     # DSGVO: Auskunft, Löschung, Fristen
_mount("patch_routes", patch_routes)       # Software-Patching
_mount("vpn_routes", vpn_routes)         # WireGuard-kompatibles VPN
_mount("node_routes", node_routes)        # Node-Stufe + Reverse Proxy
_mount("diag_routes", diag_routes)        # Wartungsmodus / Diagnose

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
    """
    Gesundheitsprüfung - bewusst das Billigste, was geht.

    Hier wird NICHTS angefasst: keine Datenbank, keine Datei, kein
    Netzwerk. Der Endpunkt beantwortet genau eine Frage - "läuft die
    Ereignisschleife noch?" -, und die soll er auch dann beantworten
    können, wenn der Server gerade unter Volllast steht.

    Frühere Fassung las bei jedem Aufruf version.txt von der Platte. Das
    ist harmlos, solange nichts los ist, und genau der Tropfen zu viel,
    wenn Docker mitten in einem Massen-Update alle fünf Sekunden anklopft.
    Die Version steht weiterhin unter /api/version.
    """
    return {"ok": True, "name": "RAPALLE.net RMM"}


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
import time as _time
from app.sockets import request_exec as _request_exec


async def _automation_engine():
    while True:
        try:
            for auto in await db.call(db.get_due_automations, ):
                client_ids = [c for c in (auto["client_ids"] or "").split(",") if c]
                run_id = _uuid.uuid4().hex   # gruppiert diesen Durchlauf
                for cid in client_ids:
                    hostname = None
                    try:
                        c = await db.call(db.get_client, cid)
                        hostname = c["hostname"] if c else None
                    except Exception:
                        pass
                    try:
                        res = await _request_exec(cid, auto["command"], timeout_seconds=60)
                        await db.call(db.record_automation_result, 
                            run_id, auto["id"], cid, hostname,
                            res.get("stdout", ""), res.get("stderr", ""),
                            res.get("code"), ok=True,
                        )
                    except Exception as e:
                        print(f"[automation] '{auto['name']}' auf {cid} fehlgeschlagen: {e}")
                        await db.call(db.record_automation_result, 
                            run_id, auto["id"], cid, hostname,
                            "", str(e), None, ok=False,
                        )
                await db.call(db.mark_automation_run, auto["id"])
                await db.call(db.add_audit_entry, "system", "automation.executed", target=auto["id"], details=auto["name"])
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
            for site in await db.call(db.list_monitored_websites):
                interval_ms = max(10, int(site["monitor_interval_seconds"] or 300)) * 1000
                last = site["last_checked"] or 0
                if now_ms - last < interval_ms:
                    continue  # noch nicht fällig

                ok, error = await _asyncio.to_thread(_check_website, site["url"])
                new_status = "up" if ok else "down"
                prev_status = site["last_status"]   # None = noch nie geprüft
                await db.call(db.set_website_check_result, site["id"],
                              new_status, error)

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
    # Diagnose einhängen und den Wartungsmodus fortsetzen, falls er vor
    # einem Neustart aktiv war. Das steht bewusst GANZ VORN: Alles, was
    # danach schiefgeht, soll bereits im Log landen.
    from app import diagnostics as _diag
    _diag.install()
    _diag.report_previous_shutdown()
    _diag.restore_from_settings()
    # Der Loop-Waechter laeuft in einem EIGENEN Thread. Das ist Absicht:
    # Steht die Ereignisschleife still, kaeme eine async-Ueberwachung selbst
    # nicht mehr zum Zug und wuerde ausgerechnet den Fall verschweigen, den
    # sie melden soll. Ein Thread misst von aussen.
    _supervise("loop-herzschlag", _diag.loop_heartbeat)
    _diag.start_watchdog()
    _supervise("diagnose-messwerte", _diag.sampler_loop)
    _supervise("tabellen-groesse", _diag.table_size_watch)

    from app import notifier as _notifier
    from app import patching as _patching

    _supervise("automation", _automation_engine)
    _supervise("uptime-monitor", _uptime_monitor_engine)
    # Garantie-Prüfschleife (Benachrichtigungs-Regeln warranty_expiring/expired)
    _supervise("garantie", _notifier.warranty_loop)
    _supervise("server-auto-update", _server_auto_update_engine)
    _supervise("db-sync", _db_sync_engine)
    _supervise("relay-ablauf", _relay_expiry_engine)
    _supervise("privacy-purge", _privacy_purge_engine)
    _supervise("patching", _patching.engine)

    # WireGuard-Endpunkt (reines Python, siehe app/wireguard.py). Startet den
    # UDP-Listener und laedt die noch gueltigen Tunnel wieder ein. Faellt der
    # Port aus, laeuft alles andere trotzdem weiter.
    try:
        from app import vpn as _vpn
        _asyncio.create_task(_vpn.start())
    except Exception as _e:
        print(f"[vpn] Start uebersprungen: {_e}")


# ----------------------------------------------------------------------
# Aufseher für Hintergrundaufgaben
# ----------------------------------------------------------------------
# Bisher wurde jede Schleife mit create_task() gestartet. Wirft so eine
# Schleife eine Ausnahme, ist sie WEG - lautlos. Das Backend läuft dann
# scheinbar weiter, aber Automatisierung, Uptime-Prüfung oder Patching
# passieren nicht mehr. Genau das sieht von aussen aus wie "das Backend
# spinnt" oder "es stürzt ab".
#
# Der Aufseher fängt die Ausnahme, schreibt sie ins Log und startet die
# Schleife neu - mit wachsendem Abstand, damit eine dauerhaft kaputte
# Schleife nicht in einer Endlosschleife die Maschine auslastet.

_TASKS: dict = {}


def _supervise(name: str, factory) -> None:
    """Startet eine Hintergrundschleife, die sich nach einem Fehler erholt."""

    async def runner():
        delay = 2.0
        while True:
            started = _time.time()
            try:
                await factory()
                # Normal beendet: die Schleife war endlich gemeint.
                print(f"[aufseher] '{name}' regulär beendet")
                return
            except _asyncio.CancelledError:
                raise
            except Exception as exc:
                from app.errors import report as _report, Codes as _Codes
                _report(_Codes.TASK_LOOP, exc, f"Hintergrundschleife '{name}'",
                        lief=f"{_time.time() - started:.0f}s")
                # Lief die Aufgabe lange, war es vermutlich ein Einzelfall -
                # dann sofort wieder mit kurzem Abstand starten. Stirbt sie
                # dagegen immer gleich neu, wird der Abstand grösser.
                delay = 2.0 if _time.time() - started > 120 else min(delay * 2, 300.0)
                print(f"[aufseher] '{name}' startet in {delay:.0f}s neu")
                await _asyncio.sleep(delay)

    _TASKS[name] = _asyncio.create_task(runner(), name=f"supervised:{name}")


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
            # db.call() statt eines direkten Aufrufs: GENAU HIER stand am
            # 23.08. das Backend. Ein commit() aus einem anderen Thread
            # wartete wegen eines parallelen Docker-Downloads 91 Sekunden
            # auf sein fsync und hielt dabei die Datenbanksperre. Diese
            # Zeile lief im Hauptthread, wartete auf dieselbe Sperre - und
            # damit stand die gesamte Ereignisschleife, bis der Waechter
            # den Prozess beendete.
            #
            # Im Arbeits-Thread darf dieselbe Wartezeit auftreten, ohne dass
            # irgendjemand sonst davon etwas merkt.
            for c in await db.call(db.list_expired_relay_clients, now_ms):
                await db.call(db.set_client_relay_enabled, c["id"], False)
                await db.call(db.add_audit_entry, "system", "relay.auto_closed",
                              target=c["id"], details=c.get("hostname"))
                # Offene Dashboards informieren, damit die Relay-Ansichten
                # (Explorer-Relay-App / Relay-Tab) sich aktualisieren.
                try:
                    await sio.emit("relay-changed",
                                   {"client_id": c["id"], "auto": True},
                                   namespace="/dashboard")
                except Exception:
                    pass
        except Exception as e:
            from app.errors import report as _report, Codes as _Codes
            _report(_Codes.TASK_LOOP, e, "Relay-Ablaufschleife")
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
from fastapi import Depends as _Depends
from app.auth import get_current_user as _current_user
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


@api.get("/api/frontend-version")
async def _frontend_version(user: dict = _Depends(_current_user)):
    """
    Was liegt WIRKLICH im frontend-Ordner dieses Servers?

    Trennt zwei Fälle, die im Browser identisch aussehen und schon einmal
    eine ganze Fehlersuche gekostet haben:

      * Die Datei auf der Platte ist noch die alte -> das Deployment ist
        nicht angekommen. Die hier gemeldete Prüfsumme ändert sich dann
        nicht, egal wie oft man neu lädt.
      * Die Datei ist neu, der Browser bekommt sie aber nicht -> es cacht
        etwas dazwischen (Browser selbst, nginx, Cloudflare).

    Vergleich: diesen Endpunkt aufrufen und die gemeldete Prüfsumme mit der
    vergleichen, die der Browser tatsächlich geladen hat (Entwicklertools ->
    Netzwerk -> die .js-Datei ansehen). Stimmen sie nicht überein, cacht
    etwas dazwischen.
    """
    import hashlib
    watched = ["js/app.js", "js/api.js", "js/i18n.js", "js/apps/webbrowser.js"]
    out = {}
    for rel in watched:
        p = FRONTEND_DIR / rel
        try:
            data = p.read_bytes()
            out[rel] = {
                "sha256": hashlib.sha256(data).hexdigest()[:16],
                "bytes": len(data),
                "mtime": int(p.stat().st_mtime),
                # Handfestes Merkmal statt blosser Prüfsumme: enthält die
                # Datei noch Code des ausgebauten Seitenproxys?
                "has_webproxy": b"webproxy" in data,
            }
        except OSError as e:
            out[rel] = {"error": str(e)}
    return {"frontend_dir": str(FRONTEND_DIR), "files": out}


class _RevalidatingStatic(StaticFiles):
    """
    StaticFiles mit passenden Cache-Headern fuer die Frontend-Dateien.

    Ausgangslage: StaticFiles schickt von Haus aus NUR Last-Modified und ETag,
    aber kein Cache-Control. Ohne Cache-Control cachen Browser heuristisch -
    je nach Alter der Datei durchaus stundenlang, ohne auch nur nachzufragen.
    Bei ES-Modulen ist das tueckisch: Nach einem Update lief im Browser weiter
    der alte Code und rief Endpunkte auf, die es serverseitig nicht mehr gibt.

    Deshalb stand hier ueberall 'no-cache' ("vor Benutzung rueckfragen").
    Das hat aber einen Preis, der erst im Betrieb hinter einem Proxy auffiel:
    Das Dashboard besteht aus mehreren Dutzend ES-Modulen. Bei JEDEM Laden
    ergab das mehrere Dutzend Rueckfragen innerhalb weniger Sekunden - ein
    Muster, das Rate-Limiter (Cloudflare & Co.) als Burst einstufen und mit
    429 abweisen. Dann fehlen einzelne Module, und der Browser arbeitet mit
    alten Resten weiter ("does not provide an export named ...").

    Kompromiss:
      *.html                 -> no-cache. Das ist EINE Datei; sie muss immer
                               frisch sein, denn sie zieht alles andere nach.
      *.js/.mjs/.css/.json   -> kurzes max-age. Innerhalb dieser Zeitspanne
                               kommt ein Reload ganz ohne Anfragen aus, was
                               den Burst beseitigt. Danach wird wieder
                               revalidiert, Updates kommen also weiter an.
      alles andere           -> unveraendert (Bilder, Schriften).

    Die Dauer laesst sich ueber die Einstellung 'static_cache_seconds' aendern;
    0 stellt das alte Verhalten (immer rueckfragen) wieder her.
    """

    REVALIDATE = (".js", ".mjs", ".css", ".json", ".map")
    ALWAYS_FRESH = (".html",)
    DEFAULT_MAX_AGE = 60

    def _max_age(self) -> int:
        try:
            raw = db.get_setting("static_cache_seconds")
            if raw is None or str(raw).strip() == "":
                return self.DEFAULT_MAX_AGE
            return max(0, min(86400, int(str(raw).strip())))
        except Exception:
            return self.DEFAULT_MAX_AGE

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        low = path.lower()
        if low.endswith(self.ALWAYS_FRESH):
            response.headers["Cache-Control"] = "no-cache"
        elif low.endswith(self.REVALIDATE):
            secs = self._max_age()
            response.headers["Cache-Control"] = (
                f"private, max-age={secs}, must-revalidate" if secs else "no-cache")
        return response


api.mount("/", _RevalidatingStatic(directory=FRONTEND_DIR, html=True), name="frontend")


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

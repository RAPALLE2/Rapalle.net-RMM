"""
notifier.py (Core)
------------------
Ereignis-Ausloeser fuer Benachrichtigungen. Die eigentliche Arbeit (Regeln,
SMTP, Webhooks, Dashboard-Toasts, Garantie-Pruefung) macht das MODUL "notify"
(src/modules/backend/notify). Ist es aus, laufen alle Aufrufe hier still ins Leere.

Der Core meldet Ereignisse an den bekannten Stellen:
  sockets.py   client_online/offline/new, agent_update, Metrik-Schwellwerte
  main.py      website_up/down (Uptime-Monitor)
  auth_routes  user_login, login_failed

fire_event() reicht das Ereignis an ALLE aktiven Module weiter, die dafuer
einen Hook haben (module.json -> "hooks") - "notify" ist eins davon.
"""

import asyncio

# Trigger-Katalog: key -> (Label, Standard-Cooldown Sekunden, params-Hinweis).
# Bleibt im Core, weil die Ereignisse vom Core kommen; das Modul liefert ihn
# an seinen Regel-Editor aus.
TRIGGERS = {
    "client_offline":    {"label": "Client geht offline",              "cooldown": 60,    "params": []},
    "client_online":     {"label": "Client kommt online",              "cooldown": 60,    "params": []},
    "client_new":        {"label": "Neuer Client registriert",         "cooldown": 0,     "params": []},
    "warranty_expiring": {"label": "Garantie läuft bald ab",           "cooldown": 86400, "params": ["days_before"]},
    "warranty_expired":  {"label": "Garantie abgelaufen",              "cooldown": 86400, "params": []},
    "cpu_high":          {"label": "CPU-Auslastung über Schwellwert",  "cooldown": 1800,  "params": ["threshold"]},
    "ram_high":          {"label": "RAM-Auslastung über Schwellwert",  "cooldown": 1800,  "params": ["threshold"]},
    "disk_high":         {"label": "Disk-Belegung über Schwellwert",   "cooldown": 21600, "params": ["threshold"]},
    "temp_high":         {"label": "CPU-Temperatur über Schwellwert",  "cooldown": 1800,  "params": ["threshold"]},
    "website_down":      {"label": "Überwachte Website down",          "cooldown": 300,   "params": []},
    "website_up":        {"label": "Überwachte Website wieder up",     "cooldown": 300,   "params": []},
    "agent_update":      {"label": "Agent wurde aktualisiert",         "cooldown": 0,     "params": []},
    "user_login":        {"label": "Benutzer-Anmeldung am Dashboard",  "cooldown": 0,     "params": []},
    "login_failed":      {"label": "Fehlgeschlagene Anmeldung",        "cooldown": 60,    "params": []},
}

CHANNELS = ("email", "webhook", "dashboard")


def _engine():
    """Regel-Motor des Moduls "notify" - None, wenn es aus ist."""
    try:
        from app import modules
        return modules.use("notify", "engine") if modules.is_enabled("notify") else None
    except Exception:
        return None


async def fire_event(trigger: str, client_id: str | None = None,
                     notification: dict | None = None,
                     dedupe_key: str | None = None) -> None:
    """
    Meldet ein Ereignis an die Module.
      trigger      -> Schluessel aus TRIGGERS
      client_id    -> betroffener Client (None bei clientlosen Events)
      notification -> Objekt aus build_notification() (head/body/Kontext)
      dedupe_key   -> Cooldown-Schluessel (default: client_id bzw. trigger)
    """
    if trigger not in TRIGGERS:
        return
    if notification is None:
        from router.admin_routes import build_notification
        notification = build_notification(trigger, level="info")
    try:
        from app import modules as _modules
        await _modules.emit(trigger, event=trigger, client_id=client_id,
                            notification=notification, dedupe_key=dedupe_key)
    except Exception as exc:     # Module duerfen den Core nie stoeren - aber sichtbar bleiben
        print(f"[notify] Ereignis '{trigger}' konnte nicht verteilt werden: {exc}")


def fire_event_threadsafe(loop, trigger: str, **kwargs) -> None:
    """Für synchrone Aufrufer (z.B. Auth-Routen ohne await)."""
    try:
        asyncio.run_coroutine_threadsafe(fire_event(trigger, **kwargs), loop)
    except Exception:
        pass


async def check_metric_thresholds(client: dict, metrics: dict) -> None:
    """Pro Heartbeat aus sockets.py: cpu/ram/disk/temp-Regeln (Modul "notify")."""
    eng = _engine()
    if eng is not None:
        await eng.check_metric_thresholds(client, metrics)


async def send_all_webhooks(notification: dict) -> None:
    """Uptime-Monitor: Meldung an alle Webhooks (Modul "notify")."""
    eng = _engine()
    if eng is not None:
        await eng.send_all_webhooks(notification)

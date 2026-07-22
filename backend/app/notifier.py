"""
notifier.py
-----------
Zentrale Benachrichtigungs-Engine (Notification-Rework).

Konzept: Der Admin legt REGELN an ("Wenn <Trigger> auf <Clients>, dann
<Kanal> an <Ziel>"). Trigger werden an den passenden Stellen im Backend
über fire_event() ausgelöst (sockets.py: online/offline/neu/Metriken,
main.py: Website-Monitor, hier: Garantie-Prüfschleife).

Kanäle:
  email     -> SMTP (Einstellungen: smtp_host/port/user/password/from/security),
               target = eine oder mehrere Adressen (Komma-getrennt)
  webhook   -> bestehender Webhook (Discord oder Custom mit eigenen Headern
               und Body-Template), target = webhook_id
  dashboard -> Live-Toast an alle verbundenen Dashboards (Socket-Event
               "notify:push") + Eintrag in der Benachrichtigungs-Zentrale

Dedupe/Cooldown: pro Regel wird in last_fired (JSON {key: ts}) gemerkt,
wann ein Ereignis-Schlüssel zuletzt gefeuert hat, damit z.B. eine hohe
CPU-Last nicht bei jedem Heartbeat erneut alarmiert.
"""

import asyncio
import json
import smtplib
import ssl
import time
from email.mime.text import MIMEText
from email.utils import formatdate

from app import db

# Trigger-Katalog: key -> (Label, Standard-Cooldown Sekunden, params-Hinweis).
# Der Katalog wird auch ans Frontend geliefert (Regel-Editor).
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


# ------------------------------------------------------------------
# Regeln (CRUD-Hilfen auf der notification_rules-Tabelle)
# ------------------------------------------------------------------

def list_rules() -> list[dict]:
    rows = db._conn.execute(
        "SELECT * FROM notification_rules ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def get_rule(rule_id: str) -> dict | None:
    row = db._conn.execute(
        "SELECT * FROM notification_rules WHERE id = ?", (rule_id,)).fetchone()
    return dict(row) if row else None


def create_rule(fields: dict) -> dict:
    import uuid
    rid = str(uuid.uuid4())
    db._conn.execute(
        "INSERT INTO notification_rules"
        " (id, name, enabled, trigger, client_ids, channel, target, params, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, fields.get("name") or "Regel", int(fields.get("enabled", 1)),
         fields["trigger"], fields.get("client_ids", ""),
         fields["channel"], fields.get("target", ""),
         json.dumps(fields.get("params") or {}), int(time.time() * 1000)))
    db._conn.commit()
    return get_rule(rid)


def update_rule(rule_id: str, fields: dict) -> dict | None:
    allowed = {"name", "enabled", "trigger", "client_ids", "channel", "target", "params"}
    updates = {}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "params":
            v = json.dumps(v or {})
        if k == "enabled":
            v = int(bool(v))
        updates[k] = v
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db._conn.execute(f"UPDATE notification_rules SET {set_clause} WHERE id = ?",
                         (*updates.values(), rule_id))
        db._conn.commit()
    return get_rule(rule_id)


def delete_rule(rule_id: str) -> None:
    db._conn.execute("DELETE FROM notification_rules WHERE id = ?", (rule_id,))
    db._conn.commit()


def _mark_fired(rule_id: str, key: str) -> None:
    row = db._conn.execute(
        "SELECT last_fired FROM notification_rules WHERE id = ?", (rule_id,)).fetchone()
    try:
        data = json.loads(row["last_fired"] or "{}") if row else {}
    except Exception:
        data = {}
    data[key] = int(time.time())
    # Alte Einträge begrenzen, damit das JSON nicht unbegrenzt wächst.
    if len(data) > 200:
        for k in sorted(data, key=data.get)[: len(data) - 200]:
            data.pop(k, None)
    db._conn.execute("UPDATE notification_rules SET last_fired = ? WHERE id = ?",
                     (json.dumps(data), rule_id))
    db._conn.commit()


def _cooled_down(rule: dict, key: str, cooldown: int) -> bool:
    if cooldown <= 0:
        return True
    try:
        data = json.loads(rule.get("last_fired") or "{}")
    except Exception:
        data = {}
    return time.time() - float(data.get(key, 0)) >= cooldown


# ------------------------------------------------------------------
# E-Mail (SMTP)
# ------------------------------------------------------------------

def smtp_settings() -> dict:
    return {
        "host": db.get_setting("smtp_host") or "",
        "port": db.get_int_setting("smtp_port") or 587,
        "user": db.get_setting("smtp_user") or "",
        "password": db.get_setting("smtp_password") or "",
        "from": db.get_setting("smtp_from") or (db.get_setting("smtp_user") or ""),
        "security": (db.get_setting("smtp_security") or "starttls").lower(),
    }


def send_email(to_addrs: str, subject: str, body: str) -> None:
    """Verschickt eine Text-Mail über den konfigurierten SMTP-Server.
    to_addrs darf mehrere Adressen enthalten (Komma-getrennt). Blockierend -
    aus async-Kontext immer über asyncio.to_thread aufrufen."""
    cfg = smtp_settings()
    if not cfg["host"]:
        raise RuntimeError("Kein SMTP-Server konfiguriert (Einstellungen -> Benachrichtigungen).")
    recipients = [a.strip() for a in (to_addrs or "").split(",") if a.strip()]
    if not recipients:
        raise RuntimeError("Keine Empfänger-Adresse angegeben.")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["from"] or cfg["user"] or "rmm@localhost"
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)

    if cfg["security"] == "ssl":
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
    try:
        server.ehlo()
        if cfg["security"] == "starttls":
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.sendmail(msg["From"], recipients, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


# ------------------------------------------------------------------
# Dispatch: eine Notification über den Kanal einer Regel rausschicken
# ------------------------------------------------------------------

def _dispatch_sync(rule: dict, notification: dict) -> None:
    """Blockierender Versand (E-Mail/Webhook) - im Thread ausführen."""
    channel = rule["channel"]
    if channel == "email":
        subject = notification.get("head") or "RMM Benachrichtigung"
        body = (f"{notification.get('body') or notification.get('message') or ''}\n\n"
                f"Client:   {notification.get('client')}\n"
                f"Tenant:   {notification.get('tenant')}\n"
                f"Standort: {notification.get('location')}\n"
                f"Service:  {notification.get('service')}\n"
                f"Level:    {notification.get('level')}\n"
                f"Zeit:     {time.strftime('%d.%m.%Y %H:%M:%S')}\n\n"
                f"— RAPALLE.net RMM")
        send_email(rule.get("target") or "", subject, body)
    elif channel == "webhook":
        from app.routers.admin_routes import send_webhook
        hook = db.get_webhook(rule.get("target") or "")
        if not hook:
            raise RuntimeError("Webhook der Regel existiert nicht mehr.")
        send_webhook(hook, notification)
    # 'dashboard' wird async direkt in fire_event() erledigt.


async def _dispatch(rule: dict, notification: dict) -> None:
    if rule["channel"] == "dashboard":
        try:
            from app.sockets import sio
            await sio.emit("notify:push", notification, namespace="/dashboard")
        except Exception as e:
            print(f"[notify] Dashboard-Push fehlgeschlagen: {e}")
        return
    try:
        await asyncio.to_thread(_dispatch_sync, rule, notification)
    except Exception as e:
        print(f"[notify] Regel '{rule.get('name')}' ({rule['channel']}) fehlgeschlagen: {e}")


def _rule_matches_client(rule: dict, client_id: str | None) -> bool:
    ids = [c for c in (rule.get("client_ids") or "").split(",") if c]
    if not ids:
        return True          # '' = alle Clients (auch client-lose Events)
    return client_id in ids


async def fire_event(trigger: str, client_id: str | None = None,
                     notification: dict | None = None,
                     dedupe_key: str | None = None) -> None:
    """
    Löst alle passenden Regeln für ein Ereignis aus.
      trigger      -> Schlüssel aus TRIGGERS
      client_id    -> betroffener Client (None bei clientlosen Events)
      notification -> Objekt aus build_notification() (head/body/Kontext)
      dedupe_key   -> Cooldown-Schlüssel (default: client_id bzw. trigger)
    """
    if trigger not in TRIGGERS:
        return
    key = dedupe_key or client_id or trigger
    cooldown = TRIGGERS[trigger]["cooldown"]
    if notification is None:
        from app.routers.admin_routes import build_notification
        notification = build_notification(trigger, level="info")
    for rule in list_rules():
        if not rule.get("enabled"):
            continue
        if rule["trigger"] != trigger:
            continue
        if not _rule_matches_client(rule, client_id):
            continue
        if not _cooled_down(rule, key, cooldown):
            continue
        _mark_fired(rule["id"], key)
        await _dispatch(rule, notification)


def fire_event_threadsafe(loop, trigger: str, **kwargs) -> None:
    """Für synchrone Aufrufer (z.B. Auth-Routen ohne await)."""
    try:
        asyncio.run_coroutine_threadsafe(fire_event(trigger, **kwargs), loop)
    except Exception:
        pass


# ------------------------------------------------------------------
# Metrik-Schwellwerte (wird pro Heartbeat aus sockets.py aufgerufen)
# ------------------------------------------------------------------

async def check_metric_thresholds(client: dict, metrics: dict) -> None:
    """Prüft cpu_high / ram_high / disk_high / temp_high für einen Client.
    Der Cooldown pro Regel verhindert Alarm-Stürme bei jedem Heartbeat."""
    from app.routers.admin_routes import build_notification
    cid = client["id"]
    hostname = client.get("hostname") or cid

    def _n(head, msg, level="warn"):
        return build_notification(msg, head=head, client=hostname,
                                  service="Monitoring", level=level)

    checks = []
    cpu = metrics.get("cpuLoad")
    if cpu is not None:
        checks.append(("cpu_high", float(cpu), "%",
                       f"CPU-Auslastung bei {round(float(cpu))}%"))
    mem_t, mem_u = metrics.get("memTotal"), metrics.get("memUsed")
    if mem_t:
        ram = (mem_u or 0) / mem_t * 100
        checks.append(("ram_high", ram, "%", f"RAM-Auslastung bei {round(ram)}%"))
    disk_t, disk_u = metrics.get("diskTotal"), metrics.get("diskUsed")
    if disk_t:
        dsk = (disk_u or 0) / disk_t * 100
        checks.append(("disk_high", dsk, "%", f"Disk-Belegung bei {round(dsk)}%"))
    temp = metrics.get("cpuTemp")
    if temp is not None:
        checks.append(("temp_high", float(temp), "°C",
                       f"CPU-Temperatur bei {round(float(temp))}°C"))

    for trigger, value, unit, msg in checks:
        # Nur weitermachen, wenn es überhaupt eine passende aktive Regel gibt
        # (spart die build_notification-Arbeit im Normalfall).
        rules = [r for r in list_rules()
                 if r.get("enabled") and r["trigger"] == trigger
                 and _rule_matches_client(r, cid)]
        if not rules:
            continue
        for rule in rules:
            try:
                params = json.loads(rule.get("params") or "{}")
            except Exception:
                params = {}
            threshold = float(params.get("threshold") or (90 if unit == "%" else 85))
            if value < threshold:
                continue
            if not _cooled_down(rule, cid, TRIGGERS[trigger]["cooldown"]):
                continue
            _mark_fired(rule["id"], cid)
            head = f"⚠️ {TRIGGERS[trigger]['label']} – {hostname}"
            await _dispatch(rule, _n(head, f"{msg} (Schwellwert: {round(threshold)}{unit})."))


# ------------------------------------------------------------------
# Garantie-Prüfschleife (läuft periodisch im Hintergrund)
# ------------------------------------------------------------------

async def warranty_loop() -> None:
    """Prüft stündlich alle Clients mit hinterlegtem Garantie-Datum:
      warranty_expiring -> innerhalb von params.days_before (default 30 Tage)
      warranty_expired  -> Datum überschritten
    Der 24h-Cooldown pro Regel+Client sorgt für max. 1 Mail/Tag."""
    from app.routers.admin_routes import build_notification
    await asyncio.sleep(20)   # Backend erst in Ruhe hochfahren lassen
    while True:
        try:
            now = int(time.time() * 1000)
            rows = db._conn.execute(
                "SELECT id, hostname, warranty_until FROM clients"
                " WHERE warranty_until IS NOT NULL AND warranty_until > 0").fetchall()
            for row in rows:
                cid, hostname, until = row["id"], row["hostname"] or row["id"], row["warranty_until"]
                days_left = (until - now) / 86400000
                until_txt = time.strftime("%d.%m.%Y", time.localtime(until / 1000))
                if days_left < 0:
                    n = build_notification(
                        f"Die Garantie von {hostname} ist am {until_txt} abgelaufen "
                        f"(vor {abs(int(days_left))} Tagen).",
                        head=f"🛡️ Garantie abgelaufen – {hostname}",
                        client=hostname, service="Garantie", level="error")
                    await fire_event("warranty_expired", client_id=cid, notification=n)
                else:
                    # expiring: pro Regel eigene Vorlaufzeit prüfen
                    for rule in list_rules():
                        if not rule.get("enabled") or rule["trigger"] != "warranty_expiring":
                            continue
                        if not _rule_matches_client(rule, cid):
                            continue
                        try:
                            params = json.loads(rule.get("params") or "{}")
                        except Exception:
                            params = {}
                        days_before = float(params.get("days_before") or 30)
                        if days_left > days_before:
                            continue
                        if not _cooled_down(rule, cid, TRIGGERS["warranty_expiring"]["cooldown"]):
                            continue
                        _mark_fired(rule["id"], cid)
                        n = build_notification(
                            f"Die Garantie von {hostname} läuft am {until_txt} ab "
                            f"(noch {int(days_left)} Tage).",
                            head=f"🛡️ Garantie läuft bald ab – {hostname}",
                            client=hostname, service="Garantie", level="warn")
                        await _dispatch(rule, n)
        except Exception as e:
            print(f"[notify] Garantie-Prüfung fehlgeschlagen: {e}")
        await asyncio.sleep(3600)

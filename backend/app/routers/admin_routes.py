"""
routers/admin_routes.py
------------------------
Sammel-Router für die Verwaltungs-Features:
  - Gruppen & Rollen (Rechte-System)
  - Realms (Verzeichnis-Anbindung / Active Directory - Konfiguration)
  - Webhooks (Benachrichtigungen, Discord/Custom)
  - Automationen (geplante Befehle)

Diese Endpunkte sind Admin-geschützt.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ==================================================================
# Gruppen & Rollen
# ==================================================================

class GroupBody(BaseModel):
    name: str
    permissions: list[str] = []


@router.get("/permissions")
async def list_permissions(user: dict = Depends(get_current_user)):
    """Liste aller verfügbaren Rechte-Schlüssel (für die Checkbox-UI)."""
    return {"permissions": db.ALL_PERMISSIONS}


@router.get("/groups")
async def list_groups(user: dict = Depends(get_current_user)):
    require_admin(user)
    groups = db.list_groups()
    for g in groups:
        g["permissions"] = [p for p in (g["permissions"] or "").split(",") if p]
    return groups


@router.post("/groups")
async def create_group(body: GroupBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    g = db.create_group(body.name, body.permissions)
    db.add_audit_entry(user["username"], "group.created", target=g["id"], details=body.name)
    return g


@router.put("/groups/{group_id}")
async def update_group(group_id: str, body: GroupBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    g = db.update_group(group_id, body.name, body.permissions)
    db.add_audit_entry(user["username"], "group.updated", target=group_id, details=body.name)
    return g


@router.delete("/groups/{group_id}")
async def delete_group(group_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    db.delete_group(group_id)
    db.add_audit_entry(user["username"], "group.deleted", target=group_id)
    return {"ok": True}


class UserGroupsBody(BaseModel):
    group_ids: list[str] = []


@router.put("/users/{user_id}/groups")
async def set_user_groups(user_id: str, body: UserGroupsBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    db.set_user_groups(user_id, body.group_ids)
    return {"ok": True}


@router.get("/users/{user_id}/groups")
async def get_user_groups(user_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    return {"group_ids": db.get_user_group_ids(user_id)}


# ==================================================================
# Realms (Verzeichnis-Anbindung / Active Directory)
# ==================================================================

class RealmBody(BaseModel):
    name: str
    server: str
    base_dn: str | None = None
    bind_user: str | None = None
    bind_password: str | None = None
    port: int | None = None
    use_ssl: bool = False
    user_filter: str | None = None
    enabled: bool = True


@router.get("/realms")
async def list_realms(user: dict = Depends(get_current_user)):
    require_admin(user)
    realms = db.list_realms()
    # Bind-Passwort niemals ans Frontend zurückgeben
    for r in realms:
        r.pop("bind_password", None)
    return realms


@router.post("/realms")
async def create_realm(body: RealmBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    r = db.create_realm(
        body.name, body.server, body.base_dn, body.bind_user, body.bind_password,
        port=body.port, use_ssl=body.use_ssl, user_filter=body.user_filter,
    )
    r.pop("bind_password", None)
    db.add_audit_entry(user["username"], "realm.created", target=r["id"], details=body.name)
    return r


@router.put("/realms/{realm_id}")
async def update_realm(realm_id: str, body: RealmBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    if not db.get_realm(realm_id):
        raise HTTPException(404, "Realm nicht gefunden")
    # exclude_unset: nur die tatsächlich mitgeschickten Felder ändern, damit ein
    # simpler Aktiv/Inaktiv-Umschalter nicht versehentlich SSL/Port/DN überschreibt.
    r = db.update_realm(realm_id, body.model_dump(exclude_unset=True))
    if r:
        r.pop("bind_password", None)
    db.add_audit_entry(user["username"], "realm.updated", target=realm_id, details=body.name)
    return r


@router.post("/realms/{realm_id}/test")
async def test_realm(realm_id: str, user: dict = Depends(get_current_user)):
    """
    Prüft die Realm-Konfiguration: verbindet sich mit dem Bind-Account und
    durchsucht das Base DN. Meldet Erfolg oder eine aussagekräftige Fehlermeldung.
    """
    require_admin(user)
    realm = db.get_realm(realm_id)
    if not realm:
        raise HTTPException(404, "Realm nicht gefunden")
    from app.auth import test_realm_connection
    result = test_realm_connection(realm)
    db.add_audit_entry(user["username"], "realm.tested", target=realm_id)
    return result


@router.delete("/realms/{realm_id}")
async def delete_realm(realm_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    db.delete_realm(realm_id)
    db.add_audit_entry(user["username"], "realm.deleted", target=realm_id)
    return {"ok": True}


# ==================================================================
# Webhooks / Benachrichtigungen
# ==================================================================

class WebhookBody(BaseModel):
    name: str
    url: str
    type: str = "custom"   # "discord" | "custom"


# ------------------------------------------------------------------
# Notification-Aufbau
# ------------------------------------------------------------------
# Jede Benachrichtigung besteht IMMER aus einem "head" (Titel/Kurzfassung)
# und einem "body" (eigentliche Nachricht), dazu ein einheitlicher Kontext
# (tenant / location / client / service / timestamp) und ein Level, das
# die Embed-Farbe bestimmt. Die Farben entsprechen denen im Frontend
# (siehe notify.js: info/success/warn/error).

# Level -> Discord-Embed-Farbe (dezimal). Hex aus dem Frontend übernommen.
LEVEL_COLORS = {
    "info":    0x4DA6FF,
    "success": 0x3ECF8E,
    "warn":    0xF5A524,
    "warning": 0xF5A524,
    "error":   0xFF4D6D,
    "danger":  0xFF4D6D,
}

# Level -> Emoji für den head (rein kosmetisch).
LEVEL_EMOJI = {
    "info":    "ℹ️",
    "success": "✅",
    "warn":    "⚠️",
    "warning": "⚠️",
    "error":   "🚨",
    "danger":  "🚨",
}


def _normalize_level(level: str | None) -> str:
    level = (level or "info").lower()
    return level if level in LEVEL_COLORS else "info"


def build_notification(
    message: str,
    *,
    head: str | None = None,
    body: str | None = None,
    tenant: str | None = None,
    location: str | None = None,
    client: str | None = None,
    service: str | None = None,
    level: str = "info",
    timestamp: int | None = None,
) -> dict:
    """
    Baut ein einheitliches Notification-Objekt. 'head' und 'body' werden
    - falls nicht explizit übergeben - automatisch aus den Kontextdaten
    zusammengesetzt, sodass eine Notification IMMER head + body hat.

    timestamp: Millisekunden seit Epoch (default: jetzt).
    """
    level = _normalize_level(level)
    service = service or "System"
    client = client or "—"
    tenant = tenant or "—"
    location = location or "—"
    ts = timestamp if timestamp is not None else int(time.time() * 1000)

    # head: kurze Zusammenfassung, z.B. "🚨 Disk Space – SRV-01"
    if not head:
        head = f"{LEVEL_EMOJI.get(level, 'ℹ️')} {service} – {client}"
    # body: die eigentliche Nachricht
    if not body:
        body = message

    return {
        "head": head,
        "body": body,
        "tenant": tenant,
        "location": location,
        "client": client,
        "message": message,
        "service": service,
        "level": level,
        "timestamp": ts,
    }


def _discord_payload(n: dict) -> dict:
    """
    Fertiges Discord-Template: ein Embed mit head als Titel, body als
    Beschreibung, den Kontextfeldern (Tenant / Location / Client / Service),
    einem ISO-Timestamp und der Farbe passend zum Level.
    """
    ts_iso = datetime.fromtimestamp(
        n["timestamp"] / 1000, tz=timezone.utc
    ).isoformat()

    embed = {
        "title": n["head"],
        "description": n["body"],
        "color": LEVEL_COLORS.get(n["level"], LEVEL_COLORS["info"]),
        "timestamp": ts_iso,
        "fields": [
            {"name": "Tenant",   "value": str(n["tenant"]),   "inline": True},
            {"name": "Location", "value": str(n["location"]), "inline": True},
            {"name": "Client",   "value": str(n["client"]),   "inline": True},
            {"name": "Service",  "value": str(n["service"]),  "inline": True},
        ],
        "footer": {"text": "RAPALLE.net RMM"},
    }
    return {"embeds": [embed]}


def send_webhook(webhook: dict, notification: dict | str) -> None:
    """
    Schickt eine Benachrichtigung an einen Webhook.

    'notification' ist normalerweise ein Objekt aus build_notification()
    mit head/body/Kontext. Aus Bequemlichkeit wird auch ein einfacher String
    akzeptiert (wird dann automatisch zu einer Notification verpackt).

    - Discord: fertiges Embed-Template (head=Titel, body=Beschreibung,
      Kontextfelder, Timestamp, Farbe je nach Level).
    - Custom : generisches JSON, das IMMER 'head' und 'body' enthält, plus
      den kompletten Kontext.

    Läuft synchron in einem kurzen HTTP-Request (best effort).
    """
    if isinstance(notification, str):
        notification = build_notification(notification)

    if webhook["type"] == "discord":
        payload = _discord_payload(notification)
    else:
        # Custom-Ziel: head + body sind garantiert vorhanden, dazu der Kontext.
        payload = {
            "head": notification["head"],
            "body": notification["body"],
            "tenant": notification["tenant"],
            "location": notification["location"],
            "client": notification["client"],
            "message": notification["message"],
            "service": notification["service"],
            "level": notification["level"],
            "timestamp": notification["timestamp"],
        }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook["url"],
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


@router.get("/webhooks")
async def list_webhooks(user: dict = Depends(get_current_user)):
    require_admin(user)
    return db.list_webhooks()


@router.post("/webhooks")
async def create_webhook(body: WebhookBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    w = db.create_webhook(body.name, body.url, body.type)
    db.add_audit_entry(user["username"], "webhook.created", target=w["id"], details=body.name)
    return w


@router.post("/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    webhook = db.get_webhook(webhook_id)
    if not webhook:
        raise HTTPException(404, "Webhook nicht gefunden")
    try:
        notification = build_notification(
            "This is a Test-Notification from RAPALLE.net RMM.",
            head="🔔 Test-Notification",
            tenant="Test-Tenant",
            location="Test-Location",
            client="Test-Client",
            service="Webhook-Test",
            level="info",
        )
        send_webhook(webhook, notification)
    except Exception as e:
        
        import traceback
        traceback.print_exc()

        raise HTTPException(502, f"Senden fehlgeschlagen: {e}")
    return {"ok": True}


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    db.delete_webhook(webhook_id)
    db.add_audit_entry(user["username"], "webhook.deleted", target=webhook_id)
    return {"ok": True}


# ==================================================================
# Globale Einstellungen (Allgemein-Tab)
# ==================================================================

class SettingsBody(BaseModel):
    # Alle optional: es werden nur die mitgeschickten Felder gespeichert.
    server_url: str | None = None
    server_host: str | None = None
    server_domain: str | None = None
    server_backend_port: int | None = None
    server_url: str | None = None
    metrics_interval_seconds: int | None = None
    metrics_retention_hours: int | None = None
    replay_retention_days: int | None = None


@router.get("/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    require_admin(user)
    return db.get_all_settings()


@router.put("/settings")
async def update_settings(body: SettingsBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    changed = body.model_dump(exclude_none=True)
    # server_url darf bewusst auf "" gesetzt werden (= wieder automatisch).
    for key, value in changed.items():
        db.set_setting(key, value)
    db.add_audit_entry(user["username"], "settings.updated", details=",".join(changed.keys()))
    return db.get_all_settings()


# ==================================================================
# Automationen
# ==================================================================

class AutomationBody(BaseModel):
    name: str
    command: str
    client_ids: list[str] = []
    interval_seconds: int


@router.get("/automations")
async def list_automations(user: dict = Depends(get_current_user)):
    require_admin(user)
    autos = db.list_automations()
    for a in autos:
        a["client_ids"] = [c for c in (a["client_ids"] or "").split(",") if c]
    return autos


@router.post("/automations")
async def create_automation(body: AutomationBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    a = db.create_automation(body.name, body.command, body.client_ids, body.interval_seconds)
    db.add_audit_entry(user["username"], "automation.created", target=a["id"], details=body.name)
    return a


@router.post("/automations/{auto_id}/toggle")
async def toggle_automation(auto_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    auto = db.get_automation(auto_id)
    if not auto:
        raise HTTPException(404, "Automation nicht gefunden")
    db.set_automation_enabled(auto_id, not auto["enabled"])
    return {"ok": True, "enabled": not auto["enabled"]}


@router.delete("/automations/{auto_id}")
async def delete_automation(auto_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    db.delete_automation(auto_id)
    db.add_audit_entry(user["username"], "automation.deleted", target=auto_id)
    return {"ok": True}

# webhook_routes.py (Modul "notify")
# ----------------------------------
# Webhook-Verwaltung (frueher router/admin_routes.py, URLs unveraendert):
#   GET/POST /api/admin/webhooks, PATCH/DELETE /api/admin/webhooks/{id},
#   POST /api/admin/webhooks/{id}/test
# Recht: Administrator (wie bisher).

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_admin
from router.admin_routes import build_notification

from .webhooks import send_webhook

router = APIRouter(prefix="/api/admin", tags=["notify"])


class WebhookBody(BaseModel):
    name: str
    url: str
    type: str = "custom"   # "discord" | "custom"
    # Custom-Webhooks: eigene HTTP-Header (JSON-Objekt als String) und ein
    # Body-Template mit Platzhaltern {head} {body} {message} {client} {tenant}
    # {location} {service} {level} {timestamp}. Leer = Standard-JSON.
    headers: str | None = None
    body_template: str | None = None


class WebhookUpdateBody(BaseModel):
    name: str | None = None
    url: str | None = None
    type: str | None = None
    enabled: bool | None = None
    headers: str | None = None
    body_template: str | None = None


@router.get("/webhooks")
def list_webhooks(user: dict = Depends(get_current_user)):
    require_admin(user)
    return db.list_webhooks()


@router.post("/webhooks")
def create_webhook(body: WebhookBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    w = db.create_webhook(body.name, body.url, body.type,
                          headers=body.headers, body_template=body.body_template)
    db.add_audit_entry(user["username"], "webhook.created", target=w["id"], details=body.name)
    return w


@router.patch("/webhooks/{webhook_id}")
def update_webhook(webhook_id: str, body: WebhookUpdateBody,
                         user: dict = Depends(get_current_user)):
    require_admin(user)
    if not db.get_webhook(webhook_id):
        raise HTTPException(404, "Webhook nicht gefunden")
    fields = body.model_dump(exclude_unset=True)
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    if fields.get("headers"):
        try:
            parsed = json.loads(fields["headers"])
            if not isinstance(parsed, dict):
                raise ValueError()
        except Exception:
            raise HTTPException(400, 'Header müssen ein JSON-Objekt sein, z.B. {"X-Api-Key": "..."}')
    updated = db.update_webhook(webhook_id, fields)
    db.add_audit_entry(user["username"], "webhook.updated", target=webhook_id)
    return updated


@router.post("/webhooks/{webhook_id}/test")
def test_webhook(webhook_id: str, user: dict = Depends(get_current_user)):
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
def delete_webhook(webhook_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    db.delete_webhook(webhook_id)
    db.add_audit_entry(user["username"], "webhook.deleted", target=webhook_id)
    return {"ok": True}

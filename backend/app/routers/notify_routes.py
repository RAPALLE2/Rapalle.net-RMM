"""
routers/notify_routes.py
------------------------
Benachrichtigungs-Rework: Regel-Verwaltung + SMTP-Einstellungen.

  GET  /api/notify/catalog        -> Trigger-/Kanal-Katalog (für den Editor)
  GET  /api/notify/rules          -> alle Regeln
  POST /api/notify/rules          -> Regel anlegen
  PATCH /api/notify/rules/{id}    -> Regel ändern
  DELETE /api/notify/rules/{id}   -> Regel löschen
  POST /api/notify/rules/{id}/test-> Test-Notification über den Regel-Kanal
  GET  /api/notify/smtp           -> SMTP-Einstellungen (Passwort maskiert)
  POST /api/notify/smtp           -> SMTP-Einstellungen speichern
  POST /api/notify/smtp/test      -> Test-Mail verschicken

Berechtigung: Admin ODER 'manage_settings' (gleiche Grenze wie die übrigen
Benachrichtigungs-Einstellungen im Settings-Fenster).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, notifier
from app.auth import get_current_user, is_super_admin, user_has_permission
from app.routers.admin_routes import build_notification

router = APIRouter(prefix="/api/notify", tags=["notify"])


def _require_manage(user: dict) -> None:
    if is_super_admin(user) or user_has_permission(user, "manage_settings"):
        return
    raise HTTPException(403, "Fehlendes Recht: manage_settings")


@router.get("/catalog")
async def catalog(user: dict = Depends(get_current_user)):
    _require_manage(user)
    return {
        "triggers": [{"key": k, "label": v["label"], "params": v["params"],
                      "cooldown": v["cooldown"]}
                     for k, v in notifier.TRIGGERS.items()],
        "channels": list(notifier.CHANNELS),
    }


# ------------------------------------------------------------------
# Regeln
# ------------------------------------------------------------------

class RuleBody(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    trigger: str | None = None
    client_ids: str | None = None      # kommagetrennt, '' = alle
    channel: str | None = None
    target: str | None = None
    params: dict | None = None


def _validate(fields: dict) -> None:
    if "trigger" in fields and fields["trigger"] not in notifier.TRIGGERS:
        raise HTTPException(400, "Unbekannter Trigger")
    if "channel" in fields and fields["channel"] not in notifier.CHANNELS:
        raise HTTPException(400, "Unbekannter Kanal (email|webhook|dashboard)")


@router.get("/rules")
async def list_rules(user: dict = Depends(get_current_user)):
    _require_manage(user)
    return notifier.list_rules()


@router.post("/rules")
async def create_rule(body: RuleBody, user: dict = Depends(get_current_user)):
    _require_manage(user)
    fields = body.model_dump(exclude_unset=True)
    if not fields.get("trigger") or not fields.get("channel"):
        raise HTTPException(400, "trigger und channel sind erforderlich")
    _validate(fields)
    if fields["channel"] in ("email", "webhook") and not (fields.get("target") or "").strip():
        raise HTTPException(400, "Ziel (E-Mail-Adresse bzw. Webhook) erforderlich")
    rule = notifier.create_rule(fields)
    db.add_audit_entry(user["username"], "notify.rule_created", target=rule["id"],
                       details=fields.get("name") or fields["trigger"])
    return rule


@router.patch("/rules/{rule_id}")
async def update_rule(rule_id: str, body: RuleBody,
                      user: dict = Depends(get_current_user)):
    _require_manage(user)
    if not notifier.get_rule(rule_id):
        raise HTTPException(404, "Regel nicht gefunden")
    fields = body.model_dump(exclude_unset=True)
    _validate(fields)
    return notifier.update_rule(rule_id, fields)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, user: dict = Depends(get_current_user)):
    _require_manage(user)
    notifier.delete_rule(rule_id)
    db.add_audit_entry(user["username"], "notify.rule_deleted", target=rule_id)
    return {"ok": True}


@router.post("/rules/{rule_id}/test")
async def test_rule(rule_id: str, user: dict = Depends(get_current_user)):
    _require_manage(user)
    rule = notifier.get_rule(rule_id)
    if not rule:
        raise HTTPException(404, "Regel nicht gefunden")
    n = build_notification(
        f"Test der Regel „{rule['name']}“ (Trigger: "
        f"{notifier.TRIGGERS.get(rule['trigger'], {}).get('label', rule['trigger'])}).",
        head="🔔 Test-Notification", client="Test-Client",
        service="Regel-Test", level="info")
    try:
        await notifier._dispatch(rule, n)   # bewusst ohne Cooldown
    except Exception as e:
        raise HTTPException(502, f"Versand fehlgeschlagen: {e}")
    return {"ok": True}


# ------------------------------------------------------------------
# SMTP
# ------------------------------------------------------------------

class SmtpBody(BaseModel):
    host: str
    port: int = 587
    user: str = ""
    password: str | None = None    # None = unverändert lassen
    from_addr: str = ""
    security: str = "starttls"     # starttls | ssl | none


@router.get("/smtp")
async def get_smtp(user: dict = Depends(get_current_user)):
    _require_manage(user)
    cfg = notifier.smtp_settings()
    cfg["password"] = "•••" if cfg["password"] else ""
    cfg["from_addr"] = cfg.pop("from")
    return cfg


@router.post("/smtp")
async def set_smtp(body: SmtpBody, user: dict = Depends(get_current_user)):
    _require_manage(user)
    if body.security not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "Ungültige Sicherheit (starttls|ssl|none)")
    db.set_setting("smtp_host", body.host.strip())
    db.set_setting("smtp_port", str(int(body.port)))
    db.set_setting("smtp_user", body.user.strip())
    if body.password is not None:      # leerer String = Passwort löschen
        db.set_setting("smtp_password", body.password)
    db.set_setting("smtp_from", body.from_addr.strip())
    db.set_setting("smtp_security", body.security)
    db.add_audit_entry(user["username"], "notify.smtp_updated", details=body.host)
    return {"ok": True}


class SmtpTestBody(BaseModel):
    to: str


@router.post("/smtp/test")
async def test_smtp(body: SmtpTestBody, user: dict = Depends(get_current_user)):
    _require_manage(user)
    import asyncio
    try:
        await asyncio.to_thread(
            notifier.send_email, body.to,
            "🔔 RMM Test-Mail",
            "Diese Test-Mail bestätigt, dass die SMTP-Verbindung funktioniert.\n\n"
            "— RAPALLE.net RMM")
    except Exception as e:
        raise HTTPException(502, f"Test-Mail fehlgeschlagen: {e}")
    return {"ok": True}

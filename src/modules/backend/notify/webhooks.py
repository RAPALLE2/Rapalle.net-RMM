# webhooks.py (Modul "notify")
# ----------------------------
# Versand an Webhooks (frueher router/admin_routes.py).
#   Discord -> fertiges Embed (head=Titel, body=Beschreibung, Kontextfelder, Farbe je Level)
#   Custom  -> JSON mit head/body/Kontext ODER eigenes Body-Template mit Platzhaltern

import json
import urllib.request
from datetime import datetime, timezone

from router.admin_routes import LEVEL_COLORS, build_notification


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

    # Eigene HTTP-Header des Webhooks (JSON-Objekt als Text in der DB).
    extra_headers = {}
    try:
        parsed = json.loads(webhook.get("headers") or "{}")
        if isinstance(parsed, dict):
            extra_headers = {str(k): str(v) for k, v in parsed.items()}
    except Exception:
        pass

    # Eigenes Body-Template: Platzhalter {head} {body} {message} {client}
    # {tenant} {location} {service} {level} {timestamp} werden ersetzt.
    template = (webhook.get("body_template") or "").strip()
    if webhook["type"] != "discord" and template:
        out = template
        for key in ("head", "body", "message", "client", "tenant",
                    "location", "service", "level", "timestamp"):
            val = str(notification.get(key, ""))
            # In JSON-Templates müssen Sonderzeichen escaped werden - wir
            # escapen wie ein JSON-String OHNE die umschließenden Quotes,
            # damit "text": "{body}" gültig bleibt.
            out = out.replace("{" + key + "}", json.dumps(val)[1:-1])
        data = out.encode("utf-8")
        content_type = extra_headers.pop("Content-Type", None) \
            or extra_headers.pop("content-type", None) or "application/json"
        req = urllib.request.Request(
            webhook["url"], data=data,
            headers={"Content-Type": content_type, "User-Agent": "Mozilla/5.0",
                     "Accept": "application/json", **extra_headers},
            method="POST")
        urllib.request.urlopen(req, timeout=10)
        return

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
            **extra_headers,
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)

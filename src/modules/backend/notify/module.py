# module.py (Modul "notify")
# --------------------------
# Benachrichtigungen: Regeln (Trigger -> Kanal), SMTP, Webhooks, Dashboard-Toasts.
#
# Der Core meldet Ereignisse ueber app.notifier.fire_event(); jedes davon kommt
# hier als Hook an (module.json -> "hooks") und geht an den Regel-Motor.
# Metrik-Schwellwerte und der Uptime-Monitor rufen engine.py direkt (ueber
# app.notifier). Die Garantie-Pruefung laeuft als Hintergrundaufgabe.

from . import engine


def on_startup(ctx):
    ctx.spawn("garantie", engine.warranty_loop)


async def on_event(ctx, data: dict):
    await engine.handle_event(
        data.get("event") or "",
        client_id=data.get("client_id"),
        notification=data.get("notification"),
        dedupe_key=data.get("dedupe_key"),
    )

# module.py (Modul "client")
# --------------------------
# Client-App fuer Windows, Linux und Android (Quellcode: src/client).
# Wird ein Benutzer geloescht, verschwinden auch seine Geraete.

from . import devices


def on_user_deleted(ctx, data: dict):
    uid = (data or {}).get("user_id")
    if uid:
        n = devices.remove_for_user(uid)
        if n:
            ctx.log(f"{n} Gerät(e) des gelöschten Benutzers abgemeldet")

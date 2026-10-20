# module.py (Modul "client")
# --------------------------
# Client-App fuer Windows, Linux und Android (Quellcode: src/client).
# - Beim Aktivieren/Start: Bau-Umgebung fuer die Client-Pakete vorbereiten (buildenv.py,
#   im Docker-Betrieb das Bau-Image mit allen Werkzeugen) - im Hintergrund.
# - Beim Deaktivieren: Bau-Image wieder entfernen (enthaelt keine Daten).
# - Wird ein Benutzer geloescht, verschwinden auch seine Geraete.

from . import buildenv, devices


def on_startup(ctx):
    # startup laeuft beim Aktivieren UND bei jedem Backend-Start: fehlt das Bau-Image
    # (z.B. nach "docker image prune"), wird es neu angelegt. Blockiert nichts.
    try:
        if buildenv.prepare_async():
            ctx.log("Bau-Umgebung für die Client-Pakete wird vorbereitet (Hintergrund)")
    except Exception as e:      # noqa: BLE001 - Bauen ist Zusatz, darf das Modul nicht stoppen
        ctx.log(f"Bau-Umgebung nicht vorbereitet: {e}")


def on_disable(ctx):
    # Im Hintergrund: das Loeschen eines grossen Images dauert ein paar Sekunden
    buildenv.remove_image_async()
    ctx.log("Bau-Image der Client-Pakete wird entfernt")


def on_user_deleted(ctx, data: dict):
    uid = (data or {}).get("user_id")
    if uid:
        n = devices.remove_for_user(uid)
        if n:
            ctx.log(f"{n} Gerät(e) des gelöschten Benutzers abgemeldet")

# module.py (Modul "headless-screen")
# Beim Aktivieren das Treiberpaket fuer den virtuellen Bildschirm bereitstellen
# (falls keine eigene URL gesetzt ist). Die eigentliche Installation laeuft pro
# Agent auf Wunsch (Dashboard -> Agent, siehe hs_routes/sockets).
from . import vdriver


def on_startup(ctx):
    try:
        vdriver.ensure_async(ctx)
    except Exception as e:      # noqa: BLE001
        ctx.log(f"Treiber-Bereitstellung übersprungen: {e}")

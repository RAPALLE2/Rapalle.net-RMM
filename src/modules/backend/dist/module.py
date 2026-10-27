# module.py (Modul "dist") - einheitliche Verteilung unter /dist
# Legt die Kern-Ordner an und spiegelt beim Start die vorhandenen Agent-/Client-
# Pakete nach backend/dist/agent bzw. /client, damit alles an EINER Stelle liegt.
from . import store, sync


def on_startup(ctx):
    store.ensure_dirs()
    try:
        n = sync.sync_packages()
        if n:
            ctx.log(f"{n} Paket(e) nach /dist gespiegelt")
    except Exception as e:      # noqa: BLE001
        ctx.log(f"Paket-Spiegelung übersprungen: {e}")
    ctx.log("Verteilung bereit unter /dist")

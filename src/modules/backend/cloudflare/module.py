# module.py (Modul "cloudflare") - Cloudflare Tunnel
# --------------------------------------------------
# startup: cloudflared (feste Version) laden; gibt es schon einen Tunnel, Connector starten.
# shutdown/disable: Connector stoppen. (Die Tunnel/Routen in Cloudflare bleiben bestehen -
# "Alles entfernen" in der Modul-App raeumt sie ab.)

import asyncio

from . import connector, remote, store


async def _remote_loop():
    """Fernzugriffe der Client-App nach Ablauf / Nutzungsende wieder schliessen."""
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(remote.cleanup)
        except Exception as e:      # noqa: BLE001
            print(f"[cloudflare] Aufräumen: {e}")


def on_startup(ctx):
    ctx.spawn("remote-cleanup", _remote_loop)
    connector.ensure_binary_async()
    t = store.load()["tunnels"].get("server") or {}
    if t.get("token_enc") and store.load()["routes"]:
        connector.start(store.dec(t["token_enc"]))
        ctx.log("Cloudflare-Tunnel wird verbunden")


def on_shutdown(ctx):
    connector.stop()


def on_disable(ctx):
    connector.stop()

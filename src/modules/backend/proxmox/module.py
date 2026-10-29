# module.py (Modul "proxmox") - automatischer Abgleich Agenten <-> Proxmox-Gaeste
# (Intervall aus den Modul-Einstellungen; neuer/umgezogener Agent -> Abgleich kurz danach)
from . import match


async def on_startup(ctx):
    match.begin()


async def on_shutdown(ctx):
    match.end()


async def on_disable(ctx):
    match.end()


async def on_client(ctx, data: dict):
    """Neuer Agent bzw. Agent wieder online (z.B. nach Migration/Umzug)."""
    match.scan_soon()

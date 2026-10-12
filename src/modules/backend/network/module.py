"""Netzwerk-Werkzeuge - Einstiegspunkt."""
from . import portforward


async def on_startup(ctx):
    """Offene Port-Weiterleitungen wiederherstellen und die Ablaufschleife starten."""
    if ctx.feature("portforward"):
        ctx.spawn("weiterleitung-restore", portforward.restore)
        ctx.spawn("weiterleitung-ablauf", portforward.expiry_loop)

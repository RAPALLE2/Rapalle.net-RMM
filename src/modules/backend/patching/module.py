"""Patching - Einstiegspunkt."""
from . import patching


async def on_startup(ctx):
    """Startet die Patch-Engine (geplante Scans/Installationen)."""
    ctx.spawn("patching-engine", patching.engine)

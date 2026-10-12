"""Skripte & Automation - Einstiegspunkt."""
from . import automation_engine


async def on_startup(ctx):
    """Die Automation-Engine prueft jede Minute, welche geplanten Befehle faellig sind."""
    if ctx.feature("automation"):
        ctx.spawn("automation", automation_engine.run)

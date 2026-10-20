# module.py (Modul "proxmox") - automatischer Abgleich Agenten <-> Proxmox-Gaeste (alle 10 Minuten)
from . import match


async def on_startup(ctx):
    match.begin()


async def on_shutdown(ctx):
    match.end()


async def on_disable(ctx):
    match.end()

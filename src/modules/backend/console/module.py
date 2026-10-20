# module.py (Modul "console") - SSH-Server je nach Einstellung starten/stoppen
from . import sshd


async def on_startup(ctx):
    sshd.begin()


async def on_shutdown(ctx):
    await sshd.end()


async def on_disable(ctx):
    await sshd.end()

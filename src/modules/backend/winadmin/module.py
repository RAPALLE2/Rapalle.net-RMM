# module.py (Modul "winadmin") - Windows: Registry + Automatik-Updates
# Die Logik liegt im Agenten (registry/wu-auto) + Backend-Relays (sockets.py).
def on_startup(ctx):
    ctx.log("Windows-Verwaltung (Registry, Updates) bereit")

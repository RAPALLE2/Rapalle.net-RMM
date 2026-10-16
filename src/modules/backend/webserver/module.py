# module.py (Modul "webserver")
# -----------------------------
# Haengt die oeffentlichen Adressen aller Websites ein (rmm.url.de/<name>) und
# beim Abschalten wieder aus. Der Hosting-Kern (webhost.py) wird auch vom Modul
# "deployment" benutzt (requires: [webserver]).

from . import sites


def on_startup(ctx):
    sites.ROOT.mkdir(parents=True, exist_ok=True)
    sites.sync_routes()
    n = len(sites.all_sites())
    if n:
        ctx.log(f"{n} Website(s) eingehängt")


def on_enable(ctx):
    sites.sync_routes()


def on_disable(ctx):
    sites.unmount_routes()


def on_shutdown(ctx):
    sites.unmount_routes()

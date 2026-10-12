"""
Beispiel-Modul - Einstiegspunkt (module.py)
===========================================

Dieses File wird beim Aktivieren des Moduls importiert. Alle Funktionen, die in
der module.json unter "hooks" stehen, werden vom Core zum passenden Zeitpunkt
aufgerufen. Jede Funktion ist optional.

Lebenszyklus-Hooks bekommen nur den Kontext:      def on_startup(ctx): ...
Ereignis-Hooks bekommen Kontext + Daten (dict):   def on_client_online(ctx, data): ...

Alles hier ist abgesichert: Wirft eine Funktion eine Ausnahme, wird ein
Crashbericht geschrieben, das Modul deaktiviert - und das Backend laeuft weiter.
"""

import asyncio


async def on_startup(ctx):
    """Bei jedem Laden: Backend-Start UND Aktivieren. Hier Hintergrundaufgaben starten."""
    ctx.log(f"startet (Version {ctx.version}), Datenordner: {ctx.data_dir}")
    if ctx.feature("ticker"):
        ctx.spawn("ticker", lambda: _ticker(ctx))      # laeuft bis zum Deaktivieren


async def on_shutdown(ctx):
    """Beim Deaktivieren und beim Beenden des Backends. Aufgaben werden automatisch gestoppt."""
    ctx.log("faehrt herunter")


def on_enable(ctx):
    """NUR wenn der Benutzer das Modul einschaltet (nicht bei jedem Backend-Start)."""
    ctx.log("wurde eingeschaltet - hier kaeme z.B. die Ersteinrichtung hin")


def on_disable(ctx):
    """NUR wenn der Benutzer das Modul ausschaltet."""
    ctx.log("wurde ausgeschaltet")


async def on_client_online(ctx, data):
    """
    Trigger: ein Client ist online gegangen - dieselben Trigger wie bei den
    Webhook-/Mail-Benachrichtigungen. data = {"event", "client_id", "notification"}.
    """
    if ctx.setting("mode") == "verbose":
        ctx.log(f"Client online: {data.get('client_id')}")


async def on_client_offline(ctx, data):
    if ctx.setting("mode") == "verbose":
        ctx.log(f"Client offline: {data.get('client_id')}")


async def on_node_online(ctx, data):
    """
    Trigger: eine Node ist online gegangen. data = {"client_id"}.
    ctx.nodes bietet: list(), get(id), online(), deploy(id, params), remove(id), action(id, "up"/"down"/...).
    Hier: Den Node-Teil dieses Moduls (src/modules/node/example/) automatisch bereitstellen.
    """
    cid = data.get("client_id")
    ctx.log(f"Node online: {cid}")
    if ctx.setting("auto_deploy"):
        try:
            await ctx.nodes.deploy(cid, {"port": ctx.setting("node_port", 18080)})
            ctx.log(f"Node-Teil auf {cid} bereitgestellt")
        except Exception as exc:                       # z.B. Docker fehlt - darf das Modul nicht abstuerzen lassen
            ctx.log(f"Bereitstellung auf {cid} nicht moeglich: {exc}")


async def on_node_offline(ctx, data):
    ctx.log(f"Node offline: {data.get('client_id')}")


async def _ticker(ctx):
    n = 0
    while True:
        await asyncio.sleep(int(ctx.setting("interval", 60)))
        n += 1
        ctx.log(f"Ticker #{n}")

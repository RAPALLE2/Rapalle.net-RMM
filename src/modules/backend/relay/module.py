"""Relay - Einstiegspunkt."""
import asyncio as _asyncio

from app import db
from app.sockets import sio


async def on_startup(ctx):
    """Schliesst freigegebene Relays automatisch, sobald ihr Zeitpunkt erreicht ist."""
    ctx.spawn("relay-ablauf", run)


async def run():
    """Schließt freigegebene Explorer-Relays automatisch, sobald ihr
    eingestellter Zeitpunkt (relay_expires_at) erreicht ist. Prüft jede Minute.
    Jede automatische Schließung wird – wie bei den Agents – im Audit-Log
    festgehalten (Aktion 'relay.auto_closed')."""
    await _asyncio.sleep(15)   # dem Start-Ansturm erst Ruhe gönnen
    while True:
        try:
            import time as _time
            now_ms = int(_time.time() * 1000)
            # db.call() statt eines direkten Aufrufs: GENAU HIER stand am
            # 23.08. das Backend. Ein commit() aus einem anderen Thread
            # wartete wegen eines parallelen Docker-Downloads 91 Sekunden
            # auf sein fsync und hielt dabei die Datenbanksperre. Diese
            # Zeile lief im Hauptthread, wartete auf dieselbe Sperre - und
            # damit stand die gesamte Ereignisschleife, bis der Waechter
            # den Prozess beendete.
            #
            # Im Arbeits-Thread darf dieselbe Wartezeit auftreten, ohne dass
            # irgendjemand sonst davon etwas merkt.
            for c in await db.call(db.list_expired_relay_clients, now_ms):
                await db.call(db.set_client_relay_enabled, c["id"], False)
                await db.call(db.add_audit_entry, "system", "relay.auto_closed",
                              target=c["id"], details=c.get("hostname"))
                # Offene Dashboards informieren, damit die Relay-Ansichten
                # (Explorer-Relay-App / Relay-Tab) sich aktualisieren.
                try:
                    await sio.emit("relay-changed",
                                   {"client_id": c["id"], "auto": True},
                                   namespace="/dashboard")
                except Exception:
                    pass
        except Exception as e:
            from app.errors import report as _report, Codes as _Codes
            _report(_Codes.TASK_LOOP, e, "Relay-Ablaufschleife")
        await _asyncio.sleep(60)

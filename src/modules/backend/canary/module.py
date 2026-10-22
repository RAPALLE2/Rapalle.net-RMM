# module.py (Modul "canary") - IP-Tracker & Canary-Tokens
# Beim Aktivieren optional den DNS-Canary-Server starten (Einstellung).
from app import modules

from . import dns_server


def on_startup(ctx):
    s = {}
    try:
        s = modules.get_settings("canary") or {}
    except Exception:
        pass
    if str(s.get("dns_enabled", False)).lower() in ("1", "true", "yes") or s.get("dns_enabled") is True:
        try:
            dns_server.start(int(s.get("dns_port") or 53), str(s.get("dns_answer_ip") or "0.0.0.0"))
            ctx.log("DNS-Canary-Server gestartet")
        except Exception as e:      # noqa: BLE001
            ctx.log(f"DNS-Canary-Server nicht gestartet: {e}")


def on_shutdown(ctx):
    dns_server.stop()


def on_disable(ctx):
    dns_server.stop()

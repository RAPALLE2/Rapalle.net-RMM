# module.py (Modul "sql")
# -----------------------
# Beim Deaktivieren darf keine externe Datenbank aktiv bleiben, denn ohne das
# Modul gibt es keine Oberflaeche mehr, um sie zu verwalten. Deshalb wird die
# komplette externe Datenbank in die lokale SQLite-Datei kopiert, der Modus auf
# "lokal" gestellt und das Backend neu gestartet. Lief die Datenbank im
# Docker-Container, wird dieser angehalten (Container + Volume bleiben).
#
# Geht der Rueckkopie etwas schief, bleibt der externe Modus bestehen - der
# Core (app/dbsync.py) gleicht weiter ab, es gehen also keine Daten verloren.
# Das Modul kann dann wieder aktiviert werden, um es erneut zu versuchen.

import asyncio

from app import dbsync

from . import sqlcore


async def on_disable(ctx):
    cfg = dbsync.load_config()
    if cfg.get("mode", "local") != "external":
        ctx.log("Datenbank ist lokal - nichts zu tun.")
        return
    if dbsync.JOB.get("running"):
        ctx.log("Es läuft bereits ein Datenbank-Wechsel - Rückkopie übersprungen.")
        raise RuntimeError("Datenbank-Wechsel läuft bereits; externe DB bleibt aktiv.")

    ctx.log(f"Externe Datenbank ({cfg.get('type')}) wird in die lokale Datei kopiert …")
    use_docker = sqlcore.docker_db_in_use(cfg)
    dbsync.job_reset("backup")
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None, lambda: sqlcore.run_switch(dict(cfg), "local", "system:module-sql", restart=False))
    if not ok:
        raise RuntimeError("Rückkopie in die lokale Datei fehlgeschlagen: "
                           + str(dbsync.JOB.get("detail") or "unbekannter Fehler")
                           + " - externe Datenbank bleibt aktiv.")
    ctx.log("Rückkopie erledigt: " + str(dbsync.JOB.get("detail") or ""))
    if use_docker:
        try:
            await loop.run_in_executor(None, sqlcore.stop_docker_db)
            ctx.log("Docker-Datenbank-Container angehalten (Daten bleiben erhalten).")
        except Exception as exc:
            ctx.log(f"Docker-Container konnte nicht angehalten werden: {exc}")
    ctx.log("Backend startet neu …")
    sqlcore.restart_soon(5.0)

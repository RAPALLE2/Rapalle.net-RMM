# sqlcore.py (Modul "sql")
# ------------------------
# Gemeinsame Logik fuer Routen UND Modul-Hooks: Datenbank-Wechsel, Neustart,
# Docker-Datenbank-Container.
#
# Die eigentliche Technik (lokale SQLite als Arbeitskopie, Abgleich mit der
# externen DB, Sicherungen) bleibt im Core: app/dbsync.py. Grund: Das Backend
# muss auch dann sauber starten, wenn dieses Modul aus ist oder abgestuerzt ist.

import os
import sys
import threading

from app import db, dbsync


def restart_soon(delay: float = 4.0) -> None:
    """Backend neu starten - aus jedem Thread aufrufbar.

    Die Verzoegerung laesst der Oberflaeche Zeit, das Ergebnis noch abzuholen.
    """
    def _go():
        try:
            sys.stdout.flush(); sys.stderr.flush()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Timer(delay, _go).start()


def run_switch(cfg: dict, mode: str, username: str, *, restart: bool = True) -> bool:
    """
    Wechselt den Datenbank-Modus (blockierend - im Executor aufrufen!).

      mode="external": lokale Daten -> externe DB, ab dann externer Modus
      mode="local":    externe DB -> lokale Datei, ab dann lokaler Modus

    Fortschritt und Fehler landen in dbsync.JOB. Rueckgabe: True bei Erfolg.
    Schlaegt etwas fehl, bleibt der alte Modus bestehen; eine bereits
    beschriebene lokale DB wird aus der Sicherung wiederhergestellt.
    """
    old_cfg = dbsync.load_config()
    old_mode = old_cfg.get("mode", "local")
    backup = ""
    try:
        # 1) IMMER zuerst die lokale Datenbank sichern (Rueckfallebene).
        backup = dbsync.backup_local("switch-" + mode)
        dbsync.JOB["backup"] = backup
        dbsync.job_log(f"Sicherung angelegt: {backup}")

        # 2) Daten kopieren.
        if mode == "external":
            res = dbsync.dump_local_to_external(cfg, track=True)
            detail = (f"lokal -> extern ({cfg['type']}), "
                      f"{sum(res['tables'].values())} Zeilen kopiert")
        else:
            if old_mode == "external":
                try:
                    dbsync.dump_local_to_external(old_cfg, track=False)
                    dbsync.job_log("Letzter Abgleich in die externe DB erledigt.")
                except Exception as e:
                    dbsync.job_error("(finaler Abgleich)", e)
            res = dbsync.restore_external_to_local(cfg, track=True)
            detail = (f"extern ({cfg['type']}) -> lokal, "
                      f"{sum(res['tables'].values())} Zeilen kopiert")

        # 3) Tabellen-Fehler = Wechsel gescheitert.
        if dbsync.JOB["errors"]:
            if mode == "local" and backup:
                dbsync.restore_local_backup(backup)
                dbsync.JOB["restored"] = True
                dbsync.job_log("Lokale Datenbank aus der Sicherung wiederhergestellt.")
            dbsync.job_finish(
                False,
                f"{len(dbsync.JOB['errors'])} Tabelle(n) fehlgeschlagen - "
                f"Modus unverändert ({old_mode}).")
            return False

        # 4) Erst jetzt den Modus festschreiben.
        cfg["mode"] = mode
        dbsync.save_config(cfg)
        db.add_audit_entry(username, "database.mode_switched", details=detail)
        dbsync.job_finish(True, detail)
        if restart:
            dbsync.job_log("Backend startet neu…")
            restart_soon()
        return True
    except Exception as e:
        dbsync.job_error("(Wechsel)", e)
        try:
            if mode == "local" and backup:
                dbsync.restore_local_backup(backup)
                dbsync.JOB["restored"] = True
                dbsync.job_log("Lokale Datenbank aus der Sicherung wiederhergestellt.")
        except Exception as e2:
            dbsync.job_error("(Rücksicherung)", e2)
        dbsync.job_finish(False, f"Abgebrochen: {e}")
        return False


def docker_db_in_use(cfg: dict) -> bool:
    """Zeigt die (externe) Konfiguration auf den Docker-Datenbank-Container?"""
    try:
        from app import docker_services
        st = docker_services.status()
        if not (st.get("is_docker") and st.get("socket")):
            return False
        creds = docker_services.db_credentials() or {}
        return bool(creds.get("host")) and creds.get("host") == cfg.get("host")
    except Exception:
        return False


def stop_docker_db() -> None:
    """Datenbank-Container anhalten. Container und Volume bleiben erhalten."""
    from app import docker_services
    docker_services.disable("db", remove=False)

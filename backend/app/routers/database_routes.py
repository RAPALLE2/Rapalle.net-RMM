"""
routers/database_routes.py
---------------------------
Umschalten zwischen lokaler SQLite und externer SQL-Datenbank
(Settings -> Datenbank). Siehe app/dbsync.py für die Architektur.

Endpunkte (nur Admin):
  GET  /api/admin/database/info    -> Modus + Konfiguration (ohne Passwort)
  POST /api/admin/database/test    -> Verbindung mit übergebener Config testen
  POST /api/admin/database/switch  -> Modus wechseln (kopiert die Daten,
                                      Backend startet danach neu)
"""

import asyncio
import os
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, dbsync
from app.auth import get_current_user
from app.routers.admin_routes import require_admin

router = APIRouter(prefix="/api/admin/database", tags=["database"])


class DbConfigBody(BaseModel):
    type: str = "mysql"            # mysql | postgres | sqlite
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = ""
    password: str = ""             # leer = gespeichertes Passwort behalten
    database: str = ""


class SwitchBody(DbConfigBody):
    mode: str                      # "local" | "external"


def _merge_config(body: DbConfigBody) -> dict:
    """Übergebene Felder mit der gespeicherten Config zusammenführen
    (leeres Passwort = altes Passwort behalten)."""
    cfg = dbsync.load_config()
    cfg.update({
        "type": body.type, "host": body.host, "port": body.port,
        "user": body.user, "database": body.database,
    })
    if body.password:
        cfg["password"] = body.password
    return cfg


def _schedule_restart(delay: float = 1.5) -> None:
    def _restart():
        print("[dbsync] Neustart nach Datenbank-Umschaltung...")
        try:
            sys.stdout.flush(); sys.stderr.flush()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
    asyncio.get_event_loop().call_later(delay, _restart)


def _restart_soon(delay: float = 4.0) -> None:
    """
    Wie _schedule_restart, aber aus JEDEM Thread aufrufbar.

    Der Wechsel laeuft im Executor - dort gibt es keine laufende
    Ereignisschleife, `asyncio.get_event_loop()` wuerde scheitern. Deshalb ein
    einfacher Timer-Thread. Die Verzoegerung ist bewusst etwas groesser, damit
    die Oberflaeche den Fortschritt noch einmal abholen und das Ergebnis
    anzeigen kann, bevor das Backend weg ist.
    """
    import threading
    threading.Timer(delay, lambda: (
        sys.stdout.flush(),
        os.execv(sys.executable, [sys.executable] + sys.argv),
    )).start()


@router.get("/info")
def database_info(user: dict = Depends(get_current_user)):
    require_admin(user)
    return {"config": dbsync.public_config()}


@router.post("/test")
async def database_test(body: DbConfigBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    cfg = _merge_config(body)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, dbsync.test_connection, cfg)
    except Exception as e:
        raise HTTPException(400, f"Verbindung fehlgeschlagen: {e}")
    return {"ok": True}


@router.post("/switch")
async def database_switch(body: SwitchBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    if body.mode not in ("local", "external"):
        raise HTTPException(400, "Ungültiger Modus (local|external)")
    if dbsync.JOB.get("running"):
        raise HTTPException(409, "Es läuft bereits ein Datenbank-Wechsel")

    cfg = _merge_config(body)

    # Vorab pruefen, solange noch nichts angefasst wurde. Ein Tippfehler im
    # Host soll nicht erst nach dem halben Kopiervorgang auffallen.
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, dbsync.test_connection, cfg)
    except Exception as e:
        raise HTTPException(400, f"Verbindung fehlgeschlagen: {e}")

    dbsync.job_reset("backup")
    username = user["username"]

    def _work():
        old_cfg = dbsync.load_config()
        old_mode = old_cfg.get("mode", "local")
        backup = ""
        try:
            # 1) IMMER zuerst eine Sicherung der lokalen Datenbank. Sie ist
            #    die Rueckfallebene, falls beim Kopieren etwas schiefgeht.
            backup = dbsync.backup_local("switch-" + body.mode)
            dbsync.JOB["backup"] = backup
            dbsync.job_log(f"Sicherung angelegt: {backup}")

            # 2) Daten kopieren.
            if body.mode == "external":
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

            # 3) Gab es Tabellen-Fehler, gilt der Wechsel als gescheitert.
            #    Bei "extern -> lokal" wurde dabei in die LOKALE Datenbank
            #    geschrieben - die wird aus der Sicherung wiederhergestellt.
            if dbsync.JOB["errors"]:
                if body.mode == "local" and backup:
                    dbsync.restore_local_backup(backup)
                    dbsync.JOB["restored"] = True
                    dbsync.job_log("Lokale Datenbank aus der Sicherung wiederhergestellt.")
                dbsync.job_finish(
                    False,
                    f"{len(dbsync.JOB['errors'])} Tabelle(n) fehlgeschlagen - "
                    f"Modus unverändert ({old_mode}).")
                return

            # 4) Erst jetzt den Modus festschreiben und neu starten.
            cfg["mode"] = body.mode
            dbsync.save_config(cfg)
            db.add_audit_entry(username, "database.mode_switched", details=detail)
            dbsync.job_finish(True, detail)
            dbsync.job_log("Backend startet neu…")
            _restart_soon()
        except Exception as e:
            # Harter Fehler (Verbindung weg, Schema unlesbar, ...).
            dbsync.job_error("(Wechsel)", e)
            try:
                if body.mode == "local" and backup:
                    dbsync.restore_local_backup(backup)
                    dbsync.JOB["restored"] = True
                    dbsync.job_log("Lokale Datenbank aus der Sicherung wiederhergestellt.")
            except Exception as e2:
                dbsync.job_error("(Rücksicherung)", e2)
            dbsync.job_finish(False, f"Abgebrochen: {e}")

    loop.run_in_executor(None, _work)
    return {"ok": True, "started": True,
            "detail": "Wechsel läuft - Fortschritt siehe /progress"}


@router.get("/progress")
def database_progress(user: dict = Depends(get_current_user)):
    require_admin(user)
    return dict(dbsync.JOB)


@router.get("/backups")
def database_backups(user: dict = Depends(get_current_user)):
    require_admin(user)
    return {"backups": dbsync.list_backups()}


class BackupBody(BaseModel):
    path: str


@router.post("/backup")
async def database_backup(user: dict = Depends(get_current_user)):
    """Sicherung der lokalen Datenbank von Hand anstossen."""
    require_admin(user)
    loop = asyncio.get_event_loop()
    try:
        path = await loop.run_in_executor(None, dbsync.backup_local, "manuell")
    except Exception as e:
        raise HTTPException(500, f"Sicherung fehlgeschlagen: {e}")
    db.add_audit_entry(user["username"], "database.backup", details=path)
    return {"ok": True, "path": path}


@router.post("/restore-backup")
async def database_restore_backup(body: BackupBody,
                                  user: dict = Depends(get_current_user)):
    """Eine Sicherung zurueckspielen. Das Backend startet danach neu."""
    require_admin(user)
    known = {b["path"] for b in dbsync.list_backups()}
    if body.path not in known:
        raise HTTPException(400, "Unbekannte Sicherung")
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, dbsync.restore_local_backup, body.path)
    except Exception as e:
        raise HTTPException(500, f"Wiederherstellung fehlgeschlagen: {e}")
    db.add_audit_entry(user["username"], "database.restored", details=body.path)
    _restart_soon()
    return {"ok": True, "restart": "Backend startet in wenigen Sekunden neu"}

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


@router.get("/info")
async def database_info(user: dict = Depends(get_current_user)):
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

    cfg = _merge_config(body)
    old_mode = dbsync.load_config().get("mode", "local")
    loop = asyncio.get_event_loop()

    try:
        if body.mode == "external":
            # lokal -> extern: ALLES in die externe DB kopieren.
            await loop.run_in_executor(None, dbsync.test_connection, cfg)
            res = await loop.run_in_executor(None, dbsync.dump_local_to_external, cfg)
            detail = f"lokal -> extern ({cfg['type']}), {sum(res['tables'].values())} Zeilen kopiert"
        else:
            # extern -> lokal: final synchronisieren und den externen Stand
            # zurück in die lokale Datenbank kopieren.
            if old_mode == "external":
                try:
                    await loop.run_in_executor(None, dbsync.dump_local_to_external, cfg)
                except Exception as e:
                    print(f"[dbsync] Finaler Sync vor dem Wechsel fehlgeschlagen: {e}")
            res = await loop.run_in_executor(None, dbsync.restore_external_to_local, cfg)
            detail = f"extern ({cfg['type']}) -> lokal, {sum(res['tables'].values())} Zeilen kopiert"
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Datenbank-Wechsel fehlgeschlagen: {e}")

    cfg["mode"] = body.mode
    dbsync.save_config(cfg)
    db.add_audit_entry(user["username"], "database.mode_switched", details=detail)
    _schedule_restart()
    return {"ok": True, "detail": detail,
            "restart": "Backend startet in wenigen Sekunden neu"}

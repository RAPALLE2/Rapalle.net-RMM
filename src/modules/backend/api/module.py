# module.py (Modul "api")
# -----------------------
# REST-API (/api/v1). Der eigentliche Code liegt in src/backend/api.
# Beim Start/Aktivieren:
#   * Tabellen api_tokens / api_log anlegen
#   * Standard-Gruppe "API-Benutzer" (Vorlage "api" beim Benutzer-Anlegen) mit dem
#     Recht "use_api" anlegen - fehlt sie, wird sie neu erzeugt
#   * stuendlich: Protokoll nach Aufbewahrungsdauer kuerzen, alte Tokens entfernen

import asyncio

from app import db

API_GROUP = "API-Benutzer"


def _ensure_group(ctx) -> None:
    grp = db.get_group_by_name(API_GROUP)
    if not grp:
        grp = db.create_group(API_GROUP, [])
        ctx.log(f"Gruppe „{API_GROUP}“ angelegt")
    db._conn.execute("UPDATE groups SET auto_assign = ? WHERE id = ?", ("api", grp["id"]))
    db._conn.commit()
    grants = db.get_grants("group", grp["id"])
    if not any(g.get("perm") == "use_api" and g.get("scope") == "global" for g in grants):
        db.set_grants("group", grp["id"], grants + [{"scope": "global", "perm": "use_api", "effect": "allow"}])


async def _housekeeping():
    from api import store
    while True:
        try:
            store.prune_log()
            store.purge_tokens()
        except Exception as exc:
            print(f"[api] Aufräumen fehlgeschlagen: {exc}")
        await asyncio.sleep(3600)


def on_startup(ctx):
    from api import keys, store
    store.ensure_tables()
    keys.ensure_table()
    _ensure_group(ctx)
    ctx.spawn("aufraeumen", _housekeeping)


def on_enable(ctx):
    from api import keys, store
    store.ensure_tables()
    keys.ensure_table()
    _ensure_group(ctx)

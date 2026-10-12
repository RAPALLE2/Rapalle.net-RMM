"""Automation-Engine: fuehrt faellige geplante Befehle auf den Ziel-Clients aus."""
import asyncio as _asyncio
import time as _time
import uuid as _uuid

from app import db
from app.sockets import request_exec as _request_exec


async def run():
    while True:
        try:
            for auto in await db.call(db.get_due_automations, ):
                client_ids = [c for c in (auto["client_ids"] or "").split(",") if c]
                run_id = _uuid.uuid4().hex   # gruppiert diesen Durchlauf
                for cid in client_ids:
                    hostname = None
                    try:
                        c = await db.call(db.get_client, cid)
                        hostname = c["hostname"] if c else None
                    except Exception:
                        pass
                    try:
                        res = await _request_exec(cid, auto["command"], timeout_seconds=60)
                        await db.call(db.record_automation_result, 
                            run_id, auto["id"], cid, hostname,
                            res.get("stdout", ""), res.get("stderr", ""),
                            res.get("code"), ok=True,
                        )
                    except Exception as e:
                        print(f"[automation] '{auto['name']}' auf {cid} fehlgeschlagen: {e}")
                        await db.call(db.record_automation_result, 
                            run_id, auto["id"], cid, hostname,
                            "", str(e), None, ok=False,
                        )
                await db.call(db.mark_automation_run, auto["id"])
                await db.call(db.add_audit_entry, "system", "automation.executed", target=auto["id"], details=auto["name"])
        except Exception as e:
            print(f"[automation] Engine-Fehler: {e}")
        await _asyncio.sleep(30)  # alle 30 Sekunden prüfen

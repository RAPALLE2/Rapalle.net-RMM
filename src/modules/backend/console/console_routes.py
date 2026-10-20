# console_routes.py (Modul "console") - RMM-Konsole im Browser / in der Client-App
# -------------------------------------------------------------------------------
#   WS  /api/console/ws        Dashboard-Sitzung als Unterprotokoll "rmmtoken.<JWT>" (nicht in der Adresse!)
#                              Browser -> Server: {"t":"i","d":"<Eingabe>"} | {"t":"r","c":cols,"r":rows}
#                              Server -> Browser: Text (Terminal-Ausgabe)
#   GET /api/console/info      SSH-Stand (fuer die Anzeige: Port, Fingerabdruck), darf ich die Konsole nutzen?

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, WebSocket

from app import db
from app.auth import decode_access_token, get_current_user

from . import sshd
from .shell import Console

router = APIRouter(prefix="/api/console", tags=["console"])


@router.get("/info")
def info(user: dict = Depends(get_current_user)):
    return {"allowed": sshd.may_use(user), "ssh": sshd.status()}


@router.websocket("/ws")
async def console_ws(websocket: WebSocket):
    offered = websocket.scope.get("subprotocols") or []
    token = next((p[len("rmmtoken."):] for p in offered if p.startswith("rmmtoken.")), "")
    try:
        user = db.get_user_by_id(decode_access_token(token).get("sub"))
    except Exception:
        user = None
    if not sshd.may_use(user):
        await websocket.close(code=4403)
        return
    await websocket.accept(subprotocol="rmm-console" if "rmm-console" in offered else None)
    ip = websocket.client.host if websocket.client else ""
    via = "Client-App" if websocket.query_params.get("via") == "client" else "Dashboard"
    queue: asyncio.Queue = asyncio.Queue()
    done = asyncio.Event()

    def out(s: str):
        queue.put_nowait(s)

    con = Console(user, out, done.set, ip=ip, via=via)
    db.add_audit_entry(user["username"], "console.web_open", details=f"{via} von {ip}")

    async def sender():
        while True:
            s = await queue.get()
            parts = [s]
            while not queue.empty():                 # Ausgaben buendeln
                parts.append(queue.get_nowait())
            await websocket.send_text("".join(parts))

    async def receiver():
        while not done.is_set():
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            try:
                m = json.loads(msg.get("text") or "{}")
            except ValueError:
                continue
            if m.get("t") == "i":
                await con.feed(str(m.get("d", "")))
            elif m.get("t") == "r":
                con.resize(int(m.get("c", 80)), int(m.get("r", 24)))

    send_task = asyncio.create_task(sender())
    recv_task = asyncio.create_task(receiver())
    await con.start()
    try:
        await asyncio.wait([recv_task, asyncio.create_task(done.wait())], return_when=asyncio.FIRST_COMPLETED)
        await asyncio.sleep(0.2)                      # letzte Ausgabe noch senden
    finally:
        con.close()
        send_task.cancel()
        recv_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass

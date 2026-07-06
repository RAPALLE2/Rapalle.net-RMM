"""
app/routers/guac_routes.py
--------------------------
REST- und WebSocket-Endpunkte für den Apache-Guacamole-Support.

  POST /api/guac/token   -> erzeugt ein Einmal-Token für eine Verbindung
                            (Protokoll + Parameter werden serverseitig gehalten)
  GET  /api/guac/status  -> ist guacd erreichbar?
  WS   /guac/tunnel      -> der eigentliche Tunnel Browser <-> guacd
                            (die WS-Route wird in main.py eingehängt)
"""

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from app import db, guacamole
from app.auth import get_current_user

router = APIRouter(prefix="/api/guac", tags=["guacamole"])

# Erlaubte Protokolle (das kann guacd standardmäßig)
_ALLOWED_PROTOCOLS = {"rdp", "vnc", "ssh", "telnet", "kubernetes"}


class GuacTokenBody(BaseModel):
    protocol: str
    params: dict = {}
    client_id: str | None = None   # nur fürs Audit-Log


@router.get("/status")
async def guac_status(user: dict = Depends(get_current_user)):
    """Meldet, ob der (extern gehostete) guacd erreichbar ist."""
    return {"available": await guacamole.guacd_available()}


@router.post("/token")
async def guac_token(body: GuacTokenBody, user: dict = Depends(get_current_user)):
    """
    Erzeugt ein kurzlebiges Einmal-Token, mit dem der Browser den WebSocket-
    Tunnel öffnen kann. Die (evtl. sensiblen) Verbindungsparameter bleiben
    serverseitig und tauchen nicht in der URL auf.
    """
    protocol = (body.protocol or "").lower()
    if protocol not in _ALLOWED_PROTOCOLS:
        from fastapi import HTTPException
        raise HTTPException(400, f"Protokoll nicht unterstützt: {protocol}")

    token = guacamole.create_token(protocol, dict(body.params or {}), user.get("username", ""))
    db.add_audit_entry(
        user["username"], "guac.connect",
        target=body.client_id or "", details=protocol,
    )
    return {"token": token}


# ------------------------------------------------------------------
# WebSocket-Tunnel. Wird von main.py als /guac/tunnel registriert.
# Authentifizierung läuft über das Einmal-Token (WS kann keine Header setzen).
# ------------------------------------------------------------------
async def tunnel_endpoint(ws: WebSocket):
    # guacamole-common-js verlangt das Subprotokoll "guacamole".
    await ws.accept(subprotocol="guacamole")
    print("[guac] WebSocket-Tunnel geöffnet (Browser verbunden).")

    token = ws.query_params.get("token", "")
    entry = guacamole.consume_token(token)
    if not entry:
        print(f"[guac] FEHLER: ungültiges/abgelaufenes Token (len={len(token)}).")
        try:
            await ws.send_text(
                guacamole.encode_instruction("error", "Ungültiges oder abgelaufenes Token", "0").decode()
            )
            await ws.close()
        except Exception:
            pass
        return

    # Optimale Größe vom Browser (guacamole-common-js hängt sie an die URL an)
    def _int(name, default):
        try:
            return int(ws.query_params.get(name, default))
        except (TypeError, ValueError):
            return default

    width = _int("GUAC_WIDTH", _int("width", 1024))
    height = _int("GUAC_HEIGHT", _int("height", 768))
    dpi = _int("GUAC_DPI", _int("dpi", 96))

    try:
        await guacamole.run_tunnel(
            ws, entry["protocol"], entry["params"],
            width=width, height=height, dpi=dpi,
        )
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass

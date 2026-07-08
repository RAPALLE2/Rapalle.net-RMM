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
from app.auth import get_current_user, require_perm, can_access_client

router = APIRouter(prefix="/api/guac", tags=["guacamole"])

# Erlaubte Protokolle (das kann guacd standardmäßig)
_ALLOWED_PROTOCOLS = {"rdp", "vnc", "ssh", "telnet", "kubernetes"}


class GuacTokenBody(BaseModel):
    protocol: str
    params: dict = {}
    client_id: str | None = None   # fürs Audit-Log UND fürs Replay
    hostname: str | None = None    # Anzeigename im Replay
    record: bool = True            # Session als Replay aufzeichnen (Standard: ja)


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
    # Rechteprüfung: Guacamole-Nutzung (ggf. client-bezogen).
    from fastapi import HTTPException as _HTTPException
    if body.client_id and not can_access_client(user, body.client_id):
        raise _HTTPException(404, "Client nicht gefunden")
    require_perm(user, "use_guacamole", body.client_id or None)

    token = guacamole.create_token(
        protocol, dict(body.params or {}), user.get("username", ""),
        client_id=body.client_id or "", hostname=body.hostname or "",
        record=bool(body.record),
    )
    db.add_audit_entry(
        user["username"], "guac.connect",
        target=body.client_id or "", details=protocol,
    )
    return {"token": token}


# ------------------------------------------------------------------
# Gespeicherte Guacamole-Logins PRO CLIENT (alles außer Passwort).
# Wird als JSON im settings-Key/Value-Speicher abgelegt (Key
# "guacprofile:<client_id>"). Das Passwort wird bewusst NIE gespeichert.
# ------------------------------------------------------------------
import json as _json
from pydantic import BaseModel as _BaseModel


class GuacProfileBody(_BaseModel):
    protocol: str | None = None
    host: str | None = None
    port: str | None = None
    username: str | None = None
    domain: str | None = None
    resolution: str | None = None
    quality: str | None = None


def _guac_profile_key(client_id: str) -> str:
    return f"guacprofile:{client_id}"


@router.get("/profile/{client_id}")
async def get_guac_profile(client_id: str, user: dict = Depends(get_current_user)):
    """Gespeichertes Guacamole-Login eines Clients (ohne Passwort) lesen."""
    if not can_access_client(user, client_id):
        from fastapi import HTTPException
        raise HTTPException(404, "Client nicht gefunden")
    require_perm(user, "use_guacamole", client_id)
    raw = db.get_setting(_guac_profile_key(client_id), "")
    try:
        return {"profile": _json.loads(raw) if raw else None}
    except Exception:
        return {"profile": None}


@router.put("/profile/{client_id}")
async def save_guac_profile(client_id: str, body: GuacProfileBody, user: dict = Depends(get_current_user)):
    """Guacamole-Login eines Clients speichern. Passwort wird NIE gespeichert."""
    if not can_access_client(user, client_id):
        from fastapi import HTTPException
        raise HTTPException(404, "Client nicht gefunden")
    require_perm(user, "use_guacamole", client_id)
    profile = {k: v for k, v in body.model_dump().items() if v not in (None, "")}
    db.set_setting(_guac_profile_key(client_id), _json.dumps(profile))
    db.add_audit_entry(user["username"], "guac.profile_saved", target=client_id)
    return {"ok": True, "profile": profile}


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

    # Optionales Replay: die guacd-Session serverseitig als .jsonl mitschneiden.
    # Qualität/FPS/Skalierung kommen aus den Einstellungen; der globale
    # Master-Schalter "recording_enabled" kann alles abschalten. Funktioniert
    # für ALLE Protokolle mit Bildstrom (RDP, VNC und auch SSH/Telnet, die
    # guacd als Terminal-Bild rendert).
    recorder = None
    _rec_on = db.get_setting("recording_enabled", "1") == "1"
    if entry.get("record") and _rec_on:
        try:
            from app.guac_recording import GuacSessionRecorder
            recorder = GuacSessionRecorder(
                entry.get("client_id") or "guac",
                entry.get("hostname") or entry.get("params", {}).get("hostname") or "Guacamole",
                entry.get("by") or "",
            )
            if recorder.start() is None:
                recorder = None  # Pillow fehlt / Aufnahme aus -> ohne fortfahren
        except Exception as e:
            print(f"[guac] Replay-Aufnahme nicht möglich: {e}")
            recorder = None

    try:
        await guacamole.run_tunnel(
            ws, entry["protocol"], entry["params"],
            width=width, height=height, dpi=dpi, recorder=recorder,
        )
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if recorder is not None:
            try:
                recorder.stop()
                if recorder.rec_id:
                    # Audit-Eintrag mit direktem Link zum Replay.
                    db.add_audit_entry(
                        entry.get("by") or "", "guac.recording",
                        target=entry.get("client_id") or "",
                        details=f"Replay: /api/recordings/{recorder.rec_id}",
                    )
            except Exception:
                pass
        try:
            await ws.close()
        except Exception:
            pass

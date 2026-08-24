"""
portforward_routes.py
---------------------
Port-Weiterleitungen im Dashboard.

  GET    /api/portforward            alle offenen Weiterleitungen
  POST   /api/portforward            neue anlegen
  DELETE /api/portforward/{id}       schliessen

Gedacht fuer den haeufigsten Fall: "Ich moechte auf den VNC-/RDP-/
Web-Dienst dieses Rechners." Das geht damit ohne VPN, ohne Treiber und
ohne offene Ports beim Kunden.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, portforward
from app.auth import can_access_client, get_current_user, require_perm

router = APIRouter()

# Gaengige Dienste - die Oberflaeche bietet sie zur Auswahl an, damit
# niemand Portnummern nachschlagen muss.
PRESETS = [
    {"name": "VNC", "port": 5900, "hint": "VNC-Betrachter auf diese Adresse richten"},
    {"name": "RDP", "port": 3389, "hint": "Remotedesktop: Adresse eintragen"},
    {"name": "SSH", "port": 22, "hint": "ssh -p <Port> benutzer@<Adresse>"},
    {"name": "HTTP", "port": 80, "hint": "Im Browser oeffnen"},
    {"name": "HTTPS", "port": 443, "hint": "Im Browser oeffnen"},
    {"name": "SMB / Dateifreigabe", "port": 445, "hint": "\\\\\\\\<Adresse>\\\\Freigabe"},
]


class ForwardBody(BaseModel):
    client_id: str
    target_port: int
    target_host: str = "127.0.0.1"
    minutes: int = 240
    label: str = ""
    # Herkunft einschraenken, z.B. "192.168.178.0/24". Leer = jeder, der das
    # Backend erreicht.
    allow_from: str = ""


@router.get("/api/portforward")
def list_forwards(user: dict = Depends(get_current_user)):
    items = [f for f in portforward.overview()
             if can_access_client(user, f.get("client_id"))]
    return {"forwards": items, "presets": PRESETS,
            "port_range": [portforward.PORT_RANGE_START,
                           portforward.PORT_RANGE_END]}


@router.post("/api/portforward")
async def create_forward(body: ForwardBody,
                         user: dict = Depends(get_current_user)):
    require_perm(user, "c_portforward", body.client_id)
    if not 1 <= body.target_port <= 65535:
        raise HTTPException(400, "Ungültiger Ziel-Port")
    try:
        rec = await portforward.create(
            client_id=body.client_id, target_port=body.target_port,
            username=user["username"], minutes=body.minutes,
            target_host=body.target_host or "127.0.0.1",
            allow_from=body.allow_from, label=body.label)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    db.add_audit_entry(user["username"], "portforward.created",
                       target=body.client_id,
                       details=f"{rec['listen_port']} -> "
                               f"{rec['target_host']}:{rec['target_port']}")
    return rec


@router.delete("/api/portforward/{forward_id}")
async def delete_forward(forward_id: str,
                         user: dict = Depends(get_current_user)):
    rec = db.get_port_forward(forward_id)
    if not rec:
        raise HTTPException(404, "Weiterleitung unbekannt")
    require_perm(user, "c_portforward", rec["client_id"])
    await portforward.stop(forward_id)
    db.add_audit_entry(user["username"], "portforward.closed",
                       target=rec["client_id"],
                       details=str(rec.get("listen_port")))
    return {"ok": True}

"""
routers/network_routes.py
----------------------------
REST-Schnittstelle für den LAN-Scan (Network Center / Toolbox-Netzwerkscanner).

Endpunkte:
  GET /api/network/scan              -> neuen Scan starten (optional ?subnet=192.168.5.)
  GET /api/network/scan/last         -> letztes Scan-Ergebnis
  GET /api/network/portscan?ip=...   -> Portscan einer einzelnen IP
"""

import time

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.network_scan import scan_local_network, scan_ports

router = APIRouter(prefix="/api/network", tags=["network"])

# Einfacher In-Memory-Zwischenspeicher für das letzte Scan-Ergebnis.
_last_scan: dict = {"at": None, "devices": []}
_scan_in_progress = False


@router.get("/scan")
async def run_scan(subnet: str | None = None, user: dict = Depends(get_current_user)):
    """Startet einen Netzwerk-Scan. Optional ein bestimmtes Subnetz (z.B. '192.168.5.')."""
    global _scan_in_progress, _last_scan

    if _scan_in_progress:
        raise HTTPException(409, "Es läuft bereits ein Scan")

    _scan_in_progress = True
    try:
        devices = await scan_local_network(subnet)
        _last_scan = {"at": int(time.time() * 1000), "devices": devices, "subnet": subnet}
        return _last_scan
    finally:
        _scan_in_progress = False


@router.get("/scan/last")
async def get_last_scan(user: dict = Depends(get_current_user)):
    return _last_scan


@router.get("/portscan")
async def run_portscan(ip: str, user: dict = Depends(get_current_user)):
    """Prüft die gängigen Ports einer IP-Adresse auf offen/geschlossen."""
    open_ports = await scan_ports(ip)
    return {"ip": ip, "ports": open_ports}

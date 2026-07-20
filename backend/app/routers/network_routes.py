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

from app.auth import get_current_user, require_perm
from app.network_scan import scan_local_network, scan_ports, parse_port_spec

router = APIRouter(prefix="/api/network", tags=["network"])

# Einfacher In-Memory-Zwischenspeicher für das letzte Scan-Ergebnis.
_last_scan: dict = {"at": None, "devices": []}
_scan_in_progress = False


@router.get("/scan")
async def run_scan(subnet: str | None = None, user: dict = Depends(get_current_user)):
    """Startet einen Netzwerk-Scan. Optional ein bestimmtes Subnetz (z.B. '192.168.5.')."""
    require_perm(user, "network_scan")
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
    require_perm(user, "network_scan")
    return _last_scan


@router.get("/portscan")
async def run_portscan(
    ip: str,
    mode: str = "standard",       # "standard" | "all" | "custom"
    ports: str | None = None,      # bei mode=custom: z.B. "22,80,8000-8100"
    user: dict = Depends(get_current_user),
):
    """
    Portscan einer IP.
      - standard: gängige Ports (schnell)
      - all:      alle Ports 1-65535 (dauert länger, höhere Nebenläufigkeit)
      - custom:   frei angegebene Ports/Bereiche (Parameter 'ports')
    """
    require_perm(user, "port_scan")
    mode = (mode or "standard").lower()
    if mode == "all":
        port_list = list(range(1, 65536))
        open_ports = await scan_ports(ip, port_list, concurrency=500, timeout=0.5)
    elif mode == "custom":
        port_list = parse_port_spec(ports or "")
        if not port_list:
            raise HTTPException(400, "Keine gültigen Ports angegeben.")
        # Konservativer skalieren, je nach Anzahl der Ports.
        conc = 500 if len(port_list) > 2000 else 200
        open_ports = await scan_ports(ip, port_list, concurrency=conc, timeout=0.6)
    else:
        open_ports = await scan_ports(ip)  # gängige Ports
    return {"ip": ip, "mode": mode, "scanned": len(port_list) if mode != "standard" else None, "ports": open_ports}

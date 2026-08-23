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
from app.network_scan import (
    scan_local_network, scan_ports, parse_port_spec,
    start_scan_job, get_scan_job, cancel_scan_job, parse_scan_target, SCAN_SPEEDS,
)

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


# ------------------------------------------------------------------
# Job-basierter Scan (große Netze wie 10.10 = /16, mit Fortschritt)
# ------------------------------------------------------------------

@router.get("/scan/preview")
def scan_preview(target: str | None = None, user: dict = Depends(get_current_user)):
    """Prüft die Ziel-Angabe und meldet, wie groß der Scan würde."""
    require_perm(user, "network_scan")
    try:
        prefixes, label = parse_scan_target(target)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"label": label, "subnets": len(prefixes), "hosts": len(prefixes) * 254,
            "speeds": {k: {"workers": v[0], "per_subnet": v[1]} for k, v in SCAN_SPEEDS.items()}}


@router.post("/scan/start")
def scan_start(target: str | None = None, speed: str = "normal",
                     user: dict = Depends(get_current_user)):
    """
    Startet einen Scan im Hintergrund und liefert sofort die Job-ID.
      target: "192.168.1.", "10.10" (= /16), "10.10.0.0/16", leer = eigenes Netz
      speed:  normal | fast | turbo  (Speed-Up verteilt die /24-Blöcke auf
              mehrere parallele Worker)
    """
    require_perm(user, "network_scan")
    if speed not in SCAN_SPEEDS:
        raise HTTPException(400, f"Unbekannte Geschwindigkeit (erlaubt: {', '.join(SCAN_SPEEDS)})")
    try:
        job_id = start_scan_job(target, speed)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"job_id": job_id}


@router.get("/scan/job/{job_id}")
def scan_job_status(job_id: str, user: dict = Depends(get_current_user)):
    """Fortschritt + (bei Abschluss) gefundene Geräte."""
    require_perm(user, "network_scan")
    job = get_scan_job(job_id)
    if not job:
        raise HTTPException(404, "Scan-Job nicht gefunden (evtl. abgelaufen)")
    return {k: v for k, v in job.items() if k != "cancel"}


@router.post("/scan/job/{job_id}/cancel")
def scan_job_cancel(job_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "network_scan")
    if not cancel_scan_job(job_id):
        raise HTTPException(404, "Kein laufender Scan mit dieser ID")
    return {"ok": True}


@router.get("/scan/last")
def get_last_scan(user: dict = Depends(get_current_user)):
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

# routers/speedtest_routes.py
# ---------------------------
# Endpunkte fuer die Speedtest-App im Frontend (Ookla-artig):
#   GET  /api/speedtest/ping      -> Mini-Antwort fuer Latenz/Jitter-Messung
#   GET  /api/speedtest/download  -> streamt Zufallsdaten (?mb=..., max 64)
#   POST /api/speedtest/upload    -> nimmt Body entgegen, zaehlt nur Bytes
#
# Gemessen wird Browser <-> RMM-Server. Alle Endpunkte erfordern Login
# (Bearer-Token wie ueberall) und senden no-store, damit weder Browser noch
# Proxies die Testdaten cachen und das Ergebnis verfaelschen.

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse

from app.auth import get_current_user

router = APIRouter()

_NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
}

_CHUNK = 1024 * 1024          # 1 MiB Bloecke
_MAX_MB = 64                  # Obergrenze pro Download-Request (Client loopt)


@router.get("/api/speedtest/ping")
async def speedtest_ping(user: dict = Depends(get_current_user)):
    # Bewusst winzig: die Antwortzeit soll fast nur Netz-Latenz sein.
    return Response(content="pong", media_type="text/plain", headers=_NO_STORE)


@router.get("/api/speedtest/download")
async def speedtest_download(mb: int = 16, user: dict = Depends(get_current_user)):
    mb = max(1, min(int(mb), _MAX_MB))

    def gen():
        # os.urandom statt Nullen: unkomprimierbar, damit transparente
        # Kompression (Proxy/VPN) die Messung nicht schoenfaerbt.
        block = os.urandom(_CHUNK)
        for _ in range(mb):
            yield block

    headers = dict(_NO_STORE)
    headers["Content-Length"] = str(mb * _CHUNK)
    headers["Content-Type"] = "application/octet-stream"
    return StreamingResponse(gen(), headers=headers)


@router.post("/api/speedtest/upload")
async def speedtest_upload(request: Request, user: dict = Depends(get_current_user)):
    # Body nur zaehlen, nie speichern.
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
    return Response(content=str(received), media_type="text/plain", headers=_NO_STORE)

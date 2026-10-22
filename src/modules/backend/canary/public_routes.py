# public_routes.py (Modul "canary") - OEFFENTLICHE Endpunkte (ohne Anmeldung!)
# ---------------------------------------------------------------------------
# Diese Routen loesen den Treffer aus. Sie muessen offen erreichbar sein.
#   GET /t/{slug}            IP-Tracker: Infos sammeln -> sofortige Weiterleitung (302)
#   GET /c/{slug}            Canary web/qr: 1x1-Pixel (oder Weiterleitung), Treffer
#   GET /c/{slug}/pixel.png  Canary e-mail: transparentes Pixel (Mail-Oeffnung)
#   GET /cf/{slug}           Datei-Token: liefert die erzeugte Datei zum Download
# Die DNS-Canary laeuft ueber einen eigenen Mini-DNS-Server (dns_server.py), nicht hier.

from __future__ import annotations

import asyncio
import base64

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from . import alert, collect, store

router = APIRouter(tags=["canary-public"])

# 1x1 transparentes PNG
_PIXEL = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


async def _hit(kind: str, obj: dict, request: Request) -> dict:
    info = await asyncio.to_thread(collect.collect, request)
    hit = store.record_hit(kind, obj["id"], obj.get("name") or obj.get("slug") or "", info)
    await alert.fire(kind, obj.get("name") or obj.get("slug") or "", hit)
    return hit


@router.get("/t/{slug}")
async def tracker(slug: str, request: Request):
    t = store.find_tracker_by_slug(slug)
    if not t:
        return Response("Not found", status_code=404)
    await _hit("tracker", t, request)
    target = (t.get("redirect") or "").strip()
    if target:
        return RedirectResponse(target, status_code=302)
    # Keine Weiterleitung hinterlegt -> unauffaellige leere Seite
    return Response("", media_type="text/html")


@router.get("/c/{slug}")
async def canary_web(slug: str, request: Request):
    t = store.find_token_by_slug(slug)
    if not t or t.get("kind") not in ("web", "qr"):
        return Response(_PIXEL, media_type="image/png")     # nicht verraten, ob es existiert
    await _hit("token", t, request)
    target = (t.get("redirect") or "").strip()
    if target:
        return RedirectResponse(target, status_code=302)
    return Response(_PIXEL, media_type="image/png")


@router.get("/c/{slug}/pixel.png")
async def canary_pixel(slug: str, request: Request):
    t = store.find_token_by_slug(slug)
    if t and t.get("kind") == "email":
        await _hit("token", t, request)
    return Response(_PIXEL, media_type="image/png",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@router.get("/cf/{slug}")
async def canary_file(slug: str, request: Request):
    """Download des Datei-Tokens. Das Oeffnen der Datei selbst meldet spaeter ueber
    das eingebettete Pixel/den Link; der Download hier wird nicht als Treffer gezaehlt."""
    t = store.find_token_by_slug(slug)
    if not t or t.get("kind") != "file":
        return Response("Not found", status_code=404)
    path = store.FILES_DIR / (t.get("file_name") or "")
    if not path.is_file():
        return Response("Not found", status_code=404)
    return FileResponse(str(path), filename=t.get("file_name"),
                        media_type="application/octet-stream")

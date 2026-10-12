"""Datenschutz - Einstiegspunkt: wendet die Aufbewahrungsfristen beim Start und danach taeglich an."""
import asyncio

from app import db
from . import privacy


async def on_startup(ctx):
    await _purge_once(ctx, "Start")
    ctx.spawn("privacy-purge", lambda: _daily(ctx))


async def _purge_once(ctx, label):
    report = await db.call(privacy.purge)
    removed = sum(v for v in report.values() if isinstance(v, int))
    if removed:
        ctx.log(f"Aufbewahrungsfristen angewendet ({label}): {report}")


async def _daily(ctx):
    await asyncio.sleep(120)           # Start-Ansturm abwarten
    while True:
        await _purge_once(ctx, "taeglich")
        await asyncio.sleep(86400)

# alert.py (Modul "canary") - bei einem Treffer alarmieren
# --------------------------------------------------------
# Immer: Dashboard-Push (notify:push) + Audit-Eintrag.
# Zusaetzlich, wenn das Modul "notify" aktiv ist: an alle aktiven Webhooks und -
# falls in den Modul-Einstellungen eine E-Mail hinterlegt ist - per SMTP.
from __future__ import annotations

import time

from app import db, modules


def _settings() -> dict:
    try:
        return modules.get_settings("canary") or {}
    except Exception:
        return {}


def _summary(hit: dict) -> str:
    g = hit.get("geo") or {}
    loc = ", ".join(x for x in (g.get("city"), g.get("country")) if x)
    parts = [hit.get("ip") or "?"]
    if loc:
        parts.append(loc)
    if hit.get("os") or hit.get("browser"):
        parts.append(f"{hit.get('os','')}/{hit.get('browser','')}".strip("/"))
    if hit.get("bot"):
        parts.append("Bot/Script")
    return " · ".join(parts)


def build(kind: str, name: str, hit: dict) -> dict:
    label = "IP-Tracker" if kind == "tracker" else "Canary-Token"
    head = f"🕵️ {label} ausgelöst: {name}"
    body = _summary(hit)
    return {"head": head, "body": body, "message": f"{head} — {body}",
            "service": "Canary", "level": "warn", "client": None, "tenant": None,
            "location": hit.get("geo", {}).get("country"), "timestamp": hit.get("at") or int(time.time() * 1000),
            "canary": {"kind": kind, "name": name, "hit": hit}}


async def fire(kind: str, name: str, hit: dict) -> None:
    note = build(kind, name, hit)
    # 1) Dashboard-Push
    try:
        from app.sockets import sio
        await sio.emit("notify:push", note, namespace="/dashboard")
    except Exception as e:      # noqa: BLE001
        print(f"[canary] Dashboard-Push: {e}")
    # 2) Audit
    try:
        db.add_audit_entry("canary", f"canary.{kind}_hit", details=f"{name}: {note['body']}")
    except Exception:
        pass
    # 3) notify-Modul: Webhooks + optional E-Mail
    if modules.is_enabled("notify"):
        try:
            eng = modules.use("notify", "engine")
            await eng.send_all_webhooks(note)
            to = (_settings().get("alert_email") or "").strip()
            if to:
                import asyncio
                await asyncio.to_thread(eng.send_email, to, note["head"], note["message"])
        except Exception as e:      # noqa: BLE001
            print(f"[canary] notify: {e}")


def fire_threadsafe(kind: str, name: str, hit: dict) -> None:
    """Aus Sync-Code (z. B. DNS-Server-Thread) aufrufen."""
    import asyncio
    loop = modules._loop
    if loop is None:
        return
    asyncio.run_coroutine_threadsafe(fire(kind, name, hit), loop)

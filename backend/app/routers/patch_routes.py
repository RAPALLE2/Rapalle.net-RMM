"""
routers/patch_routes.py
-----------------------
Software-Patching als API. Dünn gehalten: alles Fachliche steckt in
app/patching.py, damit Automatik und Handbetrieb denselben Code benutzen.

Rechte:
  'patching'        - Bestand sehen, scannen, installieren
  'manage_patching' - zusätzlich Regeln der Automatik ändern
                      (impliziert 'patching', siehe _PERM_IMPLIES in db.py)

Sichtbarkeit: über auth.can_access_client bzw. auth.visible_client_ids -
wer einen Client nicht sehen darf, sieht auch dessen Updates nicht.

Langläufer (Suche, Installation) laufen NIE am offenen HTTP-Request: ein
vorgelagerter Reverse Proxy bricht lange vorher ab (Cloudflare nach rund
100 s mit Fehler 524, nginx nach 60 s). Beide Endpunkte nehmen den Auftrag
an und antworten sofort; den Fortschritt holt sich die Oberfläche über
/job.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, patching, sockets
from app.auth import (get_current_user, require_perm, can_access_client,
                      visible_client_ids)

router = APIRouter(prefix="/api/patches", tags=["patches"])


def _visible_client(user: dict, client_id: str) -> dict:
    """Client holen und Sichtbarkeit prüfen."""
    client = db.get_client(client_id)
    if not client:
        raise HTTPException(404, "Client nicht gefunden")
    if not can_access_client(user, client_id):
        raise HTTPException(403, "Kein Zugriff auf diesen Client")
    return client


# ------------------------------------------------------------------
# Bestand
# ------------------------------------------------------------------

@router.get("/overview")
def overview(user: dict = Depends(get_current_user)):
    """
    Flottenweite Übersicht: Anzahl je Schweregrad, je Quelle und je Client.
    Speist die Patch-App und die Dashboard-Widgets.
    """
    require_perm(user, "patching")
    data = patching.overview()
    visible = visible_client_ids(user, [c["id"] for c in data["clients"]])
    data["clients"] = [c for c in data["clients"] if c["id"] in visible]
    # Nach der Sichtbarkeitsfilterung neu zählen - sonst zeigt die Übersicht
    # Zahlen aus Clients, die der Benutzer gar nicht sehen darf.
    data["affected_clients"] = sum(
        1 for c in data["clients"] if (c.get("patch_count") or 0) > 0)
    data["never_scanned"] = sum(
        1 for c in data["clients"] if not c.get("patch_last_scan"))
    data["unsupported_clients"] = sum(
        1 for c in data["clients"]
        if c.get("online") and c.get("patch_protocol", 0) < patching.REQUIRED_PROTOCOL)
    data["levels_meta"] = patching.LEVELS
    return data


@router.get("/client/{client_id}")
async def client_patches(client_id: str, status: str = "pending",
                         user: dict = Depends(get_current_user)):
    """Alle Updates eines Clients, plus Zustand, Fähigkeiten und Regel."""
    require_perm(user, "patching")
    client = _visible_client(user, client_id)
    rule = patching.effective_rule(client_id)
    return {
        "client": {
            "id": client["id"],
            "hostname": client.get("hostname"),
            # ACHTUNG: die Tabelle 'clients' hat KEINE online-Spalte. Der
            # Status kommt ausschliesslich aus der Socket-Verbindungsliste.
            "online": sockets.state.is_online(client_id),
            "patch_policy": client.get("patch_policy") or "global",
            "patch_last_scan": client.get("patch_last_scan"),
            "patch_reboot": bool(client.get("patch_reboot")),
            "agent_version": client.get("agent_version"),
        },
        "patches": patching.list_patches(client_id=client_id, status=status),
        # Sagt der Oberfläche VOR dem Klick, ob der Auftrag Sinn ergibt.
        "readiness": patching.readiness(client_id),
        "job": patching.job(client_id),
        # None heisst: für diesen Client patcht die Automatik nicht.
        "auto_rule": rule,
        "auto_preview": patching.selectable(client_id, rule) if rule else [],
    }


@router.get("/client/{client_id}/job")
def client_job(client_id: str, user: dict = Depends(get_current_user)):
    """Fortschritt des laufenden (oder letzten) Auftrags."""
    require_perm(user, "patching")
    _visible_client(user, client_id)
    return patching.job(client_id)


@router.get("/client/{client_id}/readiness")
def client_readiness(client_id: str, user: dict = Depends(get_current_user)):
    """Kann dieser Client patchen - und wenn nein, warum nicht?"""
    require_perm(user, "patching")
    _visible_client(user, client_id)
    return patching.readiness(client_id)


@router.post("/client/{client_id}/selftest")
async def client_selftest(client_id: str, user: dict = Depends(get_current_user)):
    """
    Kurzer Funktionstest auf dem Client (Sekunden, kein voller Scan).

    Beantwortet die Frage, die sich aus der Ferne sonst nicht beantworten
    lässt: liegt es am Agenten, an den Rechten oder an den Update-Quellen?
    """
    require_perm(user, "patching")
    _visible_client(user, client_id)
    if not sockets.state.is_online(client_id):
        raise HTTPException(400, "Client ist offline")
    try:
        result = await sockets.request_patch_selftest(client_id, timeout_seconds=90.0)
    except asyncio.TimeoutError:
        raise HTTPException(504, (
            "Der Agent hat den Selbsttest nicht beantwortet. Entweder kennt er "
            "das Kommando nicht (alter Agent-Code), oder sein Event-Loop steht."))
    except Exception as e:
        raise HTTPException(502, f"{e.__class__.__name__}: {e}")
    result["readiness"] = patching.readiness(client_id)
    return result


# ------------------------------------------------------------------
# Suche
# ------------------------------------------------------------------

@router.post("/client/{client_id}/scan")
async def scan_client(client_id: str, user: dict = Depends(get_current_user)):
    """
    Stösst die Suche an und kehrt SOFORT zurück. Fortschritt über /job.
    """
    require_perm(user, "patching")
    _visible_client(user, client_id)

    ready = patching.readiness(client_id)
    if ready["reason"] in ("offline", "no_protocol", "no_sources"):
        # Klar benennen statt in eine Zeitüberschreitung laufen zu lassen.
        raise HTTPException(409, ready["message"])
    if patching.job_running(client_id):
        return {"started": False, "already_running": True,
                "job": patching.job(client_id)}

    # Reihenfolge ist wichtig: ERST den Zustand setzen, DANN die Aufgabe
    # starten. Die Oberfläche fragt den Stand unmittelbar nach dieser Antwort
    # ab - täte das erst die Aufgabe selbst, läse die erste Abfrage
    # "läuft nicht" und die Suche wirkte wirkungslos.
    patching.job_start(client_id, "scan", user.get("username", ""))
    patching.track(asyncio.create_task(
        patching.run_scan(client_id, user.get("username", ""))))
    return {"started": True, "job": patching.job(client_id), "hint": ready["message"]}


# ------------------------------------------------------------------
# Installation
# ------------------------------------------------------------------

class ApplyBody(BaseModel):
    # Liste von {uid, source, name}. Leer = alle offenen des Clients.
    items: list[dict] = []


@router.post("/client/{client_id}/apply")
async def apply_patches(client_id: str, body: ApplyBody,
                        user: dict = Depends(get_current_user)):
    """
    Benannte Updates installieren. Ohne Auswahl werden ALLE offenen
    installiert - das ist eine bewusste Aktion des Benutzers, keine
    Automatik. Läuft im Hintergrund; Fortschritt über /job.
    """
    require_perm(user, "patching")
    client = _visible_client(user, client_id)

    ready = patching.readiness(client_id)
    if not ready["ready"]:
        raise HTTPException(409, ready["message"])
    if patching.job_running(client_id):
        raise HTTPException(409, "Für diesen Client läuft bereits ein Auftrag.")

    items = body.items
    if not items:
        items = [{"uid": p["uid"], "source": p["source"], "name": p["name"]}
                 for p in patching.list_patches(client_id=client_id, status="pending")]
    if not items:
        raise HTTPException(400, "Keine offenen Aktualisierungen")

    patching.job_start(client_id, "apply", user.get("username", ""), total=len(items))
    patching.track(asyncio.create_task(patching.run_apply(
        client_id, items, user.get("username", ""),
        trigger="manual", hostname=client.get("hostname") or "")))
    return {"started": True, "count": len(items), "job": patching.job(client_id)}


class ExcludeBody(BaseModel):
    excluded: bool = True


@router.post("/{patch_id}/exclude")
def exclude_patch(patch_id: str, body: ExcludeBody,
                        user: dict = Depends(get_current_user)):
    """
    Ein Update dauerhaft übergehen. Ausgeschlossene überleben den nächsten
    Scan - sonst wäre der Ausschluss beim Folgetag wieder weg.
    """
    require_perm(user, "patching")
    row = db._conn.execute("SELECT * FROM patches WHERE id = ?", (patch_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Aktualisierung nicht gefunden")
    _visible_client(user, row["client_id"])
    db._conn.execute("UPDATE patches SET status = ?, updated_at = ? WHERE id = ?",
                     ("excluded" if body.excluded else "pending",
                      patching._now(), patch_id))
    db._conn.commit()
    patching.refresh_summary(row["client_id"])
    return {"ok": True}


# ------------------------------------------------------------------
# Regeln der Automatik
# ------------------------------------------------------------------

@router.get("/rules")
def get_rules(user: dict = Depends(get_current_user)):
    """Globale Regel plus alle Client-Ausnahmen."""
    require_perm(user, "patching")
    rows = [dict(r) for r in db._conn.execute(
        "SELECT r.*, c.hostname FROM patch_rules r"
        " LEFT JOIN clients c ON c.id = r.client_id"
        " WHERE r.scope = 'client'").fetchall()]
    return {
        "global": patching.get_rule(None),
        "global_enabled": db.get_setting("patch_auto_enabled", "0") == "1",
        "client_rules": [r for r in rows if can_access_client(user, r["client_id"])],
        "levels": [{"key": l, "label": patching.LEVEL_LABELS.get(l, l)}
                   for l in patching.LEVELS],
    }


class RuleBody(BaseModel):
    enabled: bool | None = None
    levels: str | None = None
    sources: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    weekdays: str | None = None
    auto_reboot: bool | None = None
    exclusions: str | None = None
    scan_interval_hours: int | None = None


def _values(body: RuleBody) -> dict:
    return {k: v for k, v in body.model_dump().items() if v is not None}


class GlobalSwitchBody(BaseModel):
    enabled: bool


@router.put("/rules/global/switch")
def set_global_switch(body: GlobalSwitchBody,
                            user: dict = Depends(get_current_user)):
    """
    Hauptschalter der Automatik. Genau wie beim Agent-Auto-Update: Clients
    mit patch_policy='global' folgen ihm, 'on'/'off' pro Client sticht.
    """
    require_perm(user, "manage_patching")
    db.set_setting("patch_auto_enabled", "1" if body.enabled else "0")
    db.add_audit_entry(user.get("username"), "patch.auto_switch",
                       details="eingeschaltet" if body.enabled else "ausgeschaltet")
    return {"ok": True}


@router.put("/rules/global")
def save_global_rule(body: RuleBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_patching")
    rule = patching.save_rule(None, _values(body))
    db.add_audit_entry(user.get("username"), "patch.rule_changed", details="global")
    return rule


@router.put("/rules/client/{client_id}")
def save_client_rule(client_id: str, body: RuleBody,
                           user: dict = Depends(get_current_user)):
    """Eigene Regel für einen Client. Sticht die globale."""
    require_perm(user, "manage_patching")
    client = _visible_client(user, client_id)
    rule = patching.save_rule(client_id, _values(body))
    db.add_audit_entry(user.get("username"), "patch.rule_changed",
                       target=client.get("hostname"), details="Client-Regel")
    return rule


@router.delete("/rules/client/{client_id}")
def drop_client_rule(client_id: str, user: dict = Depends(get_current_user)):
    """Ausnahme entfernen - der Client folgt danach wieder der globalen Regel."""
    require_perm(user, "manage_patching")
    client = _visible_client(user, client_id)
    patching.delete_rule(client_id)
    db.add_audit_entry(user.get("username"), "patch.rule_changed",
                       target=client.get("hostname"), details="Client-Regel entfernt")
    return {"ok": True}


class PolicyBody(BaseModel):
    policy: str      # 'global' | 'on' | 'off'


@router.put("/policy/{client_id}")
def set_policy(client_id: str, body: PolicyBody,
                     user: dict = Depends(get_current_user)):
    """Auto-Patch-Modus eines Clients setzen (wie beim Agent-Auto-Update)."""
    require_perm(user, "manage_patching")
    if body.policy not in ("global", "on", "off"):
        raise HTTPException(400, "Ungültiger Modus")
    client = _visible_client(user, client_id)
    db._conn.execute("UPDATE clients SET patch_policy = ? WHERE id = ?",
                     (body.policy, client_id))
    db._conn.commit()
    db.add_audit_entry(user.get("username"), "patch.policy_changed",
                       target=client.get("hostname"), details=body.policy)
    return {"ok": True, "policy": body.policy}


# ------------------------------------------------------------------
# Verlauf
# ------------------------------------------------------------------

@router.get("/runs")
def runs(client_id: str | None = None, limit: int = 50,
               user: dict = Depends(get_current_user)):
    require_perm(user, "patching")
    if client_id:
        _visible_client(user, client_id)
    rows = patching.list_runs(client_id, min(200, max(1, limit)))
    visible = visible_client_ids(user, [r["client_id"] for r in rows])
    return [r for r in rows if r["client_id"] in visible]


@router.post("/auto/run")
async def trigger_auto(user: dict = Depends(get_current_user)):
    """Automatik sofort durchlaufen lassen, statt auf den Takt zu warten."""
    require_perm(user, "manage_patching")
    result = await patching.run_auto_cycle()
    db.add_audit_entry(user.get("username"), "patch.auto_manual", details=str(result))
    return result

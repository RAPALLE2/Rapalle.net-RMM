"""
routers/privacy_routes.py
-------------------------
Betroffenenrechte und Aufbewahrungsfristen als API.

Zwei Ebenen:
  * Selbstbedienung (jeder eingeloggte Benutzer): eigene Daten einsehen,
    exportieren, Löschung beantragen. Art. 15, 20 und 17 verlangen, dass
    das ohne Umweg möglich ist.
  * Verwaltung (Recht 'manage_privacy'): Fristen setzen, Bestand einsehen,
    fremde Daten exportieren, Löschanträge bearbeiten, Löschung ausführen.

Jeder Zugriff auf FREMDE Daten landet im Audit-Log - wer Auskunft über
andere zieht, muss nachvollziehbar sein.
"""

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app import db, privacy
from app.auth import get_current_user, require_perm

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


# ==================================================================
# SELBSTBEDIENUNG - eigene Daten
# ==================================================================

@router.get("/me")
def my_data_overview(user: dict = Depends(get_current_user)):
    """Kurzüberblick: welche Datenarten liegen zu mir vor."""
    data = privacy.export_user_data(user["id"])
    return {
        "person": data.get("person", {}),
        "kategorien": [{"label": k, "count": len(v)}
                       for k, v in (data.get("daten") or {}).items()],
    }


@router.get("/me/export")
def export_my_data(user: dict = Depends(get_current_user)):
    """Vollständige Auskunft als JSON-Download (Art. 15 / Art. 20)."""
    data = privacy.export_user_data(user["id"])
    db.add_audit_entry(user.get("username"), "privacy.self_export",
                       details="Eigene Daten exportiert")
    name = f"auskunft-{user.get('username', 'benutzer')}-{time.strftime('%Y%m%d')}.json"
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


class ErasureBody(BaseModel):
    # 'account' = gesamtes Konto, 'content' = nur eigene Inhalte
    kind: str = "account"
    reason: str = ""


@router.post("/me/erasure-request")
def request_erasure(body: ErasureBody, user: dict = Depends(get_current_user)):
    """
    Löschung beantragen (Art. 17). Wird von einem Admin geprüft, weil
    Art. 17 Abs. 3 Ausnahmen kennt (z.B. Nachweispflichten) und die
    Löschung eines aktiven Kontos betriebliche Folgen hat.
    """
    if body.kind not in ("account", "content"):
        raise HTTPException(400, "Unbekannte Art des Antrags")
    open_already = [r for r in privacy.list_erasure_requests("open")
                    if r["user_id"] == user["id"]]
    if open_already:
        raise HTTPException(400, "Es liegt bereits ein offener Antrag vor")
    rid = privacy.create_erasure_request(user, body.kind, body.reason or "")
    return {"id": rid, "status": "open",
            "hinweis": "Der Antrag wird innerhalb eines Monats bearbeitet (Art. 12 Abs. 3 DSGVO)."}


@router.get("/me/erasure-request")
def my_erasure_requests(user: dict = Depends(get_current_user)):
    return [r for r in privacy.list_erasure_requests() if r["user_id"] == user["id"]]


# ==================================================================
# VERWALTUNG
# ==================================================================

@router.get("/report")
def data_report(user: dict = Depends(get_current_user)):
    """Bestandsübersicht - Grundlage fürs Verzeichnis nach Art. 30."""
    require_perm(user, "manage_privacy")
    return privacy.report()


class RetentionBody(BaseModel):
    values: dict = {}


@router.put("/retention")
def set_retention(body: RetentionBody, user: dict = Depends(get_current_user)):
    """Aufbewahrungsfristen setzen. 0 = unbegrenzt (bewusste Entscheidung)."""
    require_perm(user, "manage_privacy")
    known = {r["key"] for r in privacy.RETENTION}
    changed = []
    for key, value in (body.values or {}).items():
        if key not in known:
            continue
        try:
            v = max(0, int(value))
        except (TypeError, ValueError):
            raise HTTPException(400, f"Ungültiger Wert für {key}")
        if str(db.get_setting(key, "")) != str(v):
            db.set_setting(key, v)
            changed.append(f"{key}={v}")
    if changed:
        db.add_audit_entry(user.get("username"), "privacy.retention_changed",
                           details=", ".join(changed))
    return {"ok": True, "changed": changed}


@router.post("/purge")
def run_purge(user: dict = Depends(get_current_user)):
    """Fristen sofort anwenden, statt auf den Nacht-Job zu warten."""
    require_perm(user, "manage_privacy")
    result = privacy.purge()
    db.add_audit_entry(user.get("username"), "privacy.purge_manual",
                       details=str(result))
    return result


@router.get("/users/{user_id}/export")
def export_user(user_id: str, user: dict = Depends(get_current_user)):
    """Auskunft für eine andere Person erteilen (z.B. auf schriftliches Verlangen)."""
    require_perm(user, "manage_privacy")
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
    data = privacy.export_user_data(user_id)
    db.add_audit_entry(user.get("username"), "privacy.export_other",
                       target=target.get("username"),
                       details="Auskunft über fremde Daten erstellt")
    name = f"auskunft-{target.get('username')}-{time.strftime('%Y%m%d')}.json"
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


class EraseBody(BaseModel):
    # 'anonymize' = Nachweise bleiben ohne Personenbezug, 'hard' = alles weg
    mode: str = "anonymize"
    confirm_username: str = ""


@router.post("/users/{user_id}/erase")
def erase_user(user_id: str, body: EraseBody,
                     user: dict = Depends(get_current_user)):
    """
    Löschung ausführen (Art. 17). Nicht umkehrbar, deshalb muss der
    Benutzername zur Bestätigung mitgeschickt werden.
    """
    require_perm(user, "manage_privacy")
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if target["id"] == user["id"]:
        raise HTTPException(400, "Das eigene Konto kann hier nicht gelöscht werden")
    if body.confirm_username != target.get("username"):
        raise HTTPException(400, "Benutzername zur Bestätigung stimmt nicht")
    if body.mode not in ("anonymize", "hard"):
        raise HTTPException(400, "Unbekannter Modus")

    result = privacy.erase_user(user_id, body.mode)
    # Offene Anträge dieser Person als erledigt markieren.
    db._conn.execute(
        "UPDATE erasure_requests SET status = 'done', handled_at = ?, handled_by = ?"
        " WHERE user_id = ? AND status = 'open'",
        (int(time.time() * 1000), user.get("username", ""), user_id))
    db._conn.commit()
    db.add_audit_entry(user.get("username"), "privacy.erase_executed",
                       details=f"Modus {body.mode}, {len(result)} Bereiche")
    return result


@router.get("/erasure-requests")
def erasure_requests(status: str | None = None,
                           user: dict = Depends(get_current_user)):
    require_perm(user, "manage_privacy")
    return privacy.list_erasure_requests(status)


class ResolveBody(BaseModel):
    status: str = "rejected"     # 'rejected' | 'done'
    note: str = ""


@router.post("/erasure-requests/{request_id}/resolve")
def resolve_request(request_id: str, body: ResolveBody,
                          user: dict = Depends(get_current_user)):
    """
    Antrag abschließen. Eine Ablehnung muss begründet werden - Art. 12
    Abs. 4 verlangt, dass Betroffene die Gründe erfahren.
    """
    require_perm(user, "manage_privacy")
    if body.status not in ("rejected", "done"):
        raise HTTPException(400, "Ungültiger Status")
    if body.status == "rejected" and not body.note.strip():
        raise HTTPException(400, "Eine Ablehnung muss begründet werden (Art. 12 Abs. 4 DSGVO)")
    row = db._conn.execute("SELECT * FROM erasure_requests WHERE id = ?",
                           (request_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Antrag nicht gefunden")
    db._conn.execute(
        "UPDATE erasure_requests SET status = ?, handled_at = ?, handled_by = ?,"
        " note = ? WHERE id = ?",
        (body.status, int(time.time() * 1000), user.get("username", ""),
         body.note.strip()[:2000], request_id))
    db._conn.commit()
    db.add_audit_entry(user.get("username"), "privacy.erasure_resolved",
                       target=row["username"], details=body.status)
    return {"ok": True}

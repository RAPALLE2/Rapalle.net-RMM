"""
routers/calendar_routes.py
--------------------------
Kalender + Organisations-Hierarchie.

SICHTBARKEIT eines Termins - man sieht ihn, wenn mindestens eines zutrifft:
  * man hat ihn selbst angelegt
  * man ist als Benutzer eingetragen
  * man ist in einer eingetragenen Gruppe
  * man ist (transitiv) VORGESETZTER einer eingetragenen Person
  * man ist Super-Admin oder hat 'manage_calendar'

TERMINE ANLEGEN für andere darf man,
  * für sich selbst: immer (mit 'use_calendar')
  * für Untergebene: als Vorgesetzter laut Organigramm
  * für beliebige Ziele: mit 'manage_calendar' bzw. als Super-Admin
"""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, org
from app.auth import get_current_user, is_super_admin, require_perm, user_has_permission

router = APIRouter(prefix="/api", tags=["calendar"])

IMPORTANCE = ("low", "normal", "high", "critical")


def _now() -> int:
    return int(time.time() * 1000)


def _conn():
    return db._conn


# ==================================================================
# ORGANIGRAMM
# ==================================================================

@router.get("/org/tree")
def org_tree(user: dict = Depends(get_current_user)):
    require_perm(user, "see_org")
    tree = org.build_tree()
    tree["me"] = {"type": "user", "id": user["id"]}
    tree["can_manage"] = is_super_admin(user) or user_has_permission(user, "manage_org")
    return tree


class OrgParentBody(BaseModel):
    child_type: str
    child_id: str
    parent_type: str | None = None
    parent_id: str | None = None


@router.put("/org/parent")
def org_set_parent(body: OrgParentBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_org")
    try:
        org.set_parent(body.child_type, body.child_id, body.parent_type, body.parent_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.add_audit_entry(user["username"], "org.parent_changed",
                       target=f"{body.child_type}:{body.child_id}",
                       details=f"-> {body.parent_type or 'oberste Ebene'}:{body.parent_id or ''}")
    return {"ok": True}


class WorkspaceBody(BaseModel):
    workspace: str = ""


@router.put("/org/users/{user_id}/workspace")
def set_workspace(user_id: str, body: WorkspaceBody,
                        user: dict = Depends(get_current_user)):
    """Arbeitsbereich/Abteilung eines Benutzers setzen."""
    require_perm(user, "manage_org")
    if not db.get_user_by_id(user_id):
        raise HTTPException(404, "Benutzer nicht gefunden")
    _conn().execute("UPDATE users SET workspace = ? WHERE id = ?",
                    (body.workspace.strip(), user_id))
    _conn().commit()
    return {"ok": True}


@router.get("/org/workspaces")
def list_workspaces(user: dict = Depends(get_current_user)):
    """Bereits vergebene Arbeitsbereiche - als Vorschlagsliste."""
    require_perm(user, "see_org")
    rows = _conn().execute(
        "SELECT DISTINCT workspace FROM users"
        " WHERE workspace IS NOT NULL AND workspace != '' ORDER BY workspace").fetchall()
    return [r["workspace"] for r in rows]


# ==================================================================
# KALENDER
# ==================================================================

def _targets_of(event_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT target_type, target_id FROM calendar_targets WHERE event_id = ?",
        (event_id,)).fetchall()
    return [{"type": r["target_type"], "id": r["target_id"]} for r in rows]


def _targets_map(event_ids: list[str]) -> dict[str, list[dict]]:
    if not event_ids:
        return {}
    marks = ",".join("?" for _ in event_ids)
    rows = _conn().execute(
        f"SELECT event_id, target_type, target_id FROM calendar_targets"
        f" WHERE event_id IN ({marks})", tuple(event_ids)).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["event_id"], []).append(
            {"type": r["target_type"], "id": r["target_id"]})
    return out


def _visibility_context(user: dict) -> dict:
    """Einmal je Anfrage berechnen - sonst würde die Hierarchie pro Termin
    neu ausgewertet."""
    return {
        "all": is_super_admin(user) or user_has_permission(user, "manage_calendar"),
        "groups": set(db.get_user_group_ids(user["id"])),
        "subordinates": org.subordinate_user_ids(user),
    }


def _group_members(group_id: str) -> set[str]:
    rows = _conn().execute(
        "SELECT user_id FROM user_groups WHERE group_id = ?", (group_id,)).fetchall()
    return {r["user_id"] for r in rows}


def _may_see(user: dict, ev: dict, targets: list[dict], ctx: dict) -> bool:
    if ctx["all"] or ev.get("created_by") == user["id"]:
        return True
    for t in targets:
        if t["type"] == "user":
            if t["id"] == user["id"] or t["id"] in ctx["subordinates"]:
                return True
        elif t["type"] == "group":
            # Eigene Gruppe -> sichtbar. Außerdem: Ist auch nur EIN Mitglied
            # der Gruppe mir unterstellt, betrifft mich der Termin als
            # Vorgesetzter ebenfalls.
            if t["id"] in ctx["groups"]:
                return True
            if _group_members(t["id"]) & ctx["subordinates"]:
                return True
    return False


def _may_target(user: dict, targets: list[dict], ctx: dict) -> tuple[bool, str]:
    """Darf der Benutzer einen Termin für DIESE Ziele anlegen?"""
    if ctx["all"]:
        return True, ""
    for t in targets:
        if t["type"] == "user":
            if t["id"] == user["id"] or t["id"] in ctx["subordinates"]:
                continue
            u = db.get_user_by_id(t["id"])
            return False, f"Kein Vorgesetzter von {(u or {}).get('username', t['id'])}"
        if t["type"] == "group":
            # Erlaubt, wenn man selbst Mitglied ist ODER die Gruppe (bzw. alle
            # ihre Mitglieder) einem unterstellt ist.
            if t["id"] in ctx["groups"]:
                continue
            members = _group_members(t["id"])
            if members and all(m in ctx["subordinates"] or m == user["id"] for m in members):
                continue
            g = db.get_group(t["id"])
            return False, f"Keine Berechtigung für die Gruppe {(g or {}).get('name', t['id'])}"
        if t["type"] == "client":
            if not user_has_permission(user, "access_clients", t["id"]):
                return False, "Kein Zugriff auf einen der gewählten Clients"
    return True, ""


def _serialize(ev: dict, targets: list[dict]) -> dict:
    out = dict(ev)
    out["targets"] = targets
    out["all_day"] = bool(ev["all_day"])
    return out


@router.get("/calendar/events")
def list_events(start: int | None = None, end: int | None = None,
                      user: dict = Depends(get_current_user)):
    """Termine in einem Zeitraum (Unix-ms). Ohne Angabe: laufender Monat ±1."""
    require_perm(user, "use_calendar")
    if start is None or end is None:
        now = _now()
        start = start if start is not None else now - 45 * 86400000
        end = end if end is not None else now + 90 * 86400000
    rows = [dict(r) for r in _conn().execute(
        "SELECT * FROM calendar_events WHERE end_at >= ? AND start_at <= ?"
        " ORDER BY start_at", (int(start), int(end))).fetchall()]
    tmap = _targets_map([r["id"] for r in rows])
    ctx = _visibility_context(user)
    out = []
    for ev in rows:
        targets = tmap.get(ev["id"], [])
        if not _may_see(user, ev, targets, ctx):
            continue
        item = _serialize(ev, targets)
        item["can_edit"] = ctx["all"] or ev.get("created_by") == user["id"]
        out.append(item)
    return out


@router.get("/calendar/targets")
def target_options(user: dict = Depends(get_current_user)):
    """Auswahl-Listen für den Termin-Dialog: Benutzer, Gruppen, Clients.
    'assignable' sagt, für wen der Benutzer Termine eintragen darf."""
    require_perm(user, "use_calendar")
    ctx = _visibility_context(user)
    users = []
    for u in db.list_users():
        assignable = ctx["all"] or u["id"] == user["id"] or u["id"] in ctx["subordinates"]
        users.append({"id": u["id"], "username": u["username"],
                      "name": u.get("display_name") or u["username"],
                      "workspace": u.get("workspace") or "",
                      "assignable": assignable})
    groups = [{"id": g["id"], "name": g["name"],
               "is_ad_group": bool(g.get("is_ad_group")),
               "unmanaged": bool(g.get("unmanaged"))} for g in db.list_groups()]
    clients = [{"id": c["id"], "name": c.get("hostname") or c["id"]}
               for c in db.list_clients()
               if ctx["all"] or user_has_permission(user, "access_clients", c["id"])]
    return {"users": users, "groups": groups, "clients": clients,
            "can_manage_all": ctx["all"]}


class EventBody(BaseModel):
    title: str
    description: str = ""
    location: str = ""
    start_at: int
    duration_minutes: int = 60
    end_at: int | None = None      # hat Vorrang vor duration_minutes
    all_day: bool = False
    importance: str = "normal"
    targets: list = []             # [{"type":"user"|"group"|"client","id":"…"}]


def _day_bounds(ts: int) -> tuple[int, int]:
    """Beginn (00:00:00.000) und Ende (23:59:59.999) des Tages, in dem ts liegt.
    Gerechnet wird in der LOKALEN Zeit des Servers, damit ein ganztägiger
    Termin genau auf dem Kalendertag liegt, den der Benutzer gewählt hat."""
    lt = time.localtime(ts / 1000)
    day_start = int(time.mktime(
        (lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000)
    # +1 Tag über mktime (behandelt Sommer-/Winterzeit korrekt), dann 1 ms zurück
    next_day = int(time.mktime(
        (lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1)) * 1000)
    return day_start, next_day - 1


def _clean(body: EventBody) -> tuple[str, int, int, str, list]:
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "Titel fehlt")
    start = int(body.start_at)
    if body.all_day:
        # Ganztägig heißt: 00:00 bis 23:59:59 DESSELBEN Tages - nicht 24 Stunden
        # ab dem Startzeitpunkt (das reichte bis in den Folgetag hinein).
        start, end = _day_bounds(start)
    else:
        end = int(body.end_at) if body.end_at else start + max(5, int(body.duration_minutes)) * 60000
    if end <= start:
        raise HTTPException(400, "Ende muss nach dem Beginn liegen")
    imp = body.importance if body.importance in IMPORTANCE else "normal"
    targets = []
    seen = set()
    for t in (body.targets or []):
        ttype = (t.get("type") if isinstance(t, dict) else None) or "user"
        tid = (t.get("id") if isinstance(t, dict) else t) or ""
        if ttype not in ("user", "group", "client") or not tid:
            continue
        if (ttype, tid) in seen:
            continue
        seen.add((ttype, tid))
        targets.append({"type": ttype, "id": tid})
    return title, start, end, imp, targets


@router.post("/calendar/events")
def create_event(body: EventBody, user: dict = Depends(get_current_user)):
    require_perm(user, "use_calendar")
    title, start, end, imp, targets = _clean(body)
    if not targets:                       # ohne Ziel = eigener Termin
        targets = [{"type": "user", "id": user["id"]}]
    ctx = _visibility_context(user)
    ok, why = _may_target(user, targets, ctx)
    if not ok:
        raise HTTPException(403, why)

    eid = uuid.uuid4().hex
    now = _now()
    _conn().execute(
        "INSERT INTO calendar_events (id, title, description, location, start_at, end_at,"
        " all_day, importance, created_by, created_by_name, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (eid, title, body.description or "", body.location or "", start, end,
         int(bool(body.all_day)), imp, user["id"], user["username"], now, now))
    for t in targets:
        _conn().execute(
            "INSERT OR IGNORE INTO calendar_targets (event_id, target_type, target_id)"
            " VALUES (?, ?, ?)", (eid, t["type"], t["id"]))
    _conn().commit()
    db.add_audit_entry(user["username"], "calendar.created", target=eid, details=title)
    return _serialize(dict(_conn().execute(
        "SELECT * FROM calendar_events WHERE id = ?", (eid,)).fetchone()), targets)


@router.put("/calendar/events/{event_id}")
def update_event(event_id: str, body: EventBody,
                       user: dict = Depends(get_current_user)):
    require_perm(user, "use_calendar")
    row = _conn().execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Termin nicht gefunden")
    ev = dict(row)
    ctx = _visibility_context(user)
    if not (ctx["all"] or ev.get("created_by") == user["id"]):
        raise HTTPException(403, "Nur der Ersteller darf diesen Termin ändern")
    title, start, end, imp, targets = _clean(body)
    if targets:
        ok, why = _may_target(user, targets, ctx)
        if not ok:
            raise HTTPException(403, why)
    _conn().execute(
        "UPDATE calendar_events SET title = ?, description = ?, location = ?, start_at = ?,"
        " end_at = ?, all_day = ?, importance = ?, updated_at = ? WHERE id = ?",
        (title, body.description or "", body.location or "", start, end,
         int(bool(body.all_day)), imp, _now(), event_id))
    if targets:
        _conn().execute("DELETE FROM calendar_targets WHERE event_id = ?", (event_id,))
        for t in targets:
            _conn().execute(
                "INSERT OR IGNORE INTO calendar_targets (event_id, target_type, target_id)"
                " VALUES (?, ?, ?)", (event_id, t["type"], t["id"]))
    _conn().commit()
    return _serialize(dict(_conn().execute(
        "SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()),
        _targets_of(event_id))


@router.delete("/calendar/events/{event_id}")
def delete_event(event_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "use_calendar")
    row = _conn().execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Termin nicht gefunden")
    ev = dict(row)
    ctx = _visibility_context(user)
    if not (ctx["all"] or ev.get("created_by") == user["id"]):
        raise HTTPException(403, "Nur der Ersteller darf diesen Termin löschen")
    _conn().execute("DELETE FROM calendar_targets WHERE event_id = ?", (event_id,))
    _conn().execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
    _conn().commit()
    db.add_audit_entry(user["username"], "calendar.deleted", target=event_id, details=ev["title"])
    return {"ok": True}

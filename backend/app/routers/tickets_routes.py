"""
routers/tickets_routes.py
-------------------------
Ticket-System mit Zuweisungen, Status, Fälligkeit, Client-Verknüpfung,
Kommentaren und Datei-Anhängen (Screenshots etc.).

Sichtbarkeit einzelner Tickets: NUR Admins, der Ersteller, direkt zugewiesene
Benutzer und Mitglieder zugewiesener Gruppen sehen ein Ticket. Zusätzlich
gelten die globalen Rechte:
  ticket_read     -> Ticket-App/Liste sehen (Basis für alle anderen)
  ticket_create   -> Tickets erstellen
  ticket_edit     -> Titel/Beschreibung/Priorität/Fälligkeit/Clients ändern
  ticket_comment  -> kommentieren
  ticket_assign   -> Benutzer/Gruppen zuweisen
  ticket_resolve  -> als gelöst markieren / wieder öffnen / Status ändern
  ticket_delete   -> Tickets löschen

Datei-Anhänge werden als ROHER Request-Body hochgeladen (kein multipart,
gleiches Muster wie Branding/Recordings) und unter backend/ticket_files/
abgelegt.
"""

import pathlib
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db, visibility as vis
from app.auth import get_current_user, is_super_admin, require_perm, user_has_permission

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

# Ablage der Anhänge: backend/ticket_files/<ticket_id>/<file_id>
FILES_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "ticket_files"
MAX_FILE_BYTES = 25 * 1024 * 1024   # 25 MB pro Anhang

VALID_STATUS = ("open", "in_progress", "resolved", "closed")
VALID_PRIO = ("low", "normal", "high", "critical")


def _conn():
    return db._conn


def _now() -> int:
    return int(time.time() * 1000)


# ------------------------------------------------------------------
# Sichtbarkeit
# ------------------------------------------------------------------

def _assignees_of(ticket_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT subject_type, subject_id FROM ticket_assignees WHERE ticket_id = ?",
        (ticket_id,)).fetchall()
    return [dict(r) for r in rows]


def _clients_of(ticket_id: str) -> list[str]:
    rows = _conn().execute(
        "SELECT client_id FROM ticket_clients WHERE ticket_id = ?", (ticket_id,)).fetchall()
    return [r["client_id"] for r in rows]


def _user_may_see(user: dict, ticket: dict) -> bool:
    """Admin, Ersteller, zugewiesener Benutzer oder Mitglied zugewiesener Gruppe."""
    if is_super_admin(user):
        return True
    if ticket["created_by"] == user["username"]:
        return True
    group_ids = set(db.get_user_group_ids(user["id"]))
    for a in _assignees_of(ticket["id"]):
        if a["subject_type"] == "user" and a["subject_id"] == user["id"]:
            return True
        if a["subject_type"] == "group" and a["subject_id"] in group_ids:
            return True
    return False


def _get_visible_ticket(user: dict, ticket_id: str) -> dict:
    row = _conn().execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Ticket nicht gefunden")
    t = dict(row)
    if not _user_may_see(user, t):
        raise HTTPException(403, "Kein Zugriff auf dieses Ticket")
    return t


def _visible_comments(ticket_id: str, user: dict | None) -> list[dict]:
    """Kommentare nach Sichtbarkeit filtern:
       'all' -> alle, 'private' -> nur Verfasser, 'custom' -> Verfasser +
       freigegebene Benutzer. Admins sehen fremde private Kommentare nur als
       Platzhalter (löschbar, aber ohne Inhalt)."""
    rows = [dict(r) for r in _conn().execute(
        "SELECT * FROM ticket_comments WHERE ticket_id = ? ORDER BY created_at",
        (ticket_id,)).fetchall()]
    if user is None:
        return rows
    shares = vis.shares_map("ticket_comment_shares", "comment_id", [r["id"] for r in rows])
    out = []
    for c in rows:
        c["shared_with"] = shares.get(c["id"], [])
        c["can_edit"] = vis.may_modify(user, c)
        if vis.may_see(user, c, c["shared_with"]):
            out.append(c)
        elif is_super_admin(user):
            out.append(vis.redact(c))
    return out


def _full(t: dict, user: dict | None = None) -> dict:
    """Ticket inkl. Zuweisungen, Clients, Kommentaren und Datei-Liste."""
    out = dict(t)
    out["assignees"] = _assignees_of(t["id"])
    out["clients"] = _clients_of(t["id"])
    out["comments"] = _visible_comments(t["id"], user)
    out["files"] = [dict(r) for r in _conn().execute(
        "SELECT id, filename, size, mime, uploaded_by, created_at FROM ticket_files "
        "WHERE ticket_id = ? ORDER BY created_at", (t["id"],)).fetchall()]
    return out


def _touch(ticket_id: str):
    _conn().execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (_now(), ticket_id))
    _conn().commit()


# ------------------------------------------------------------------
# Liste + Detail
# ------------------------------------------------------------------

@router.get("")
def list_tickets(user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_read")
    rows = [dict(r) for r in _conn().execute(
        "SELECT * FROM tickets ORDER BY created_at DESC").fetchall()]
    out = []
    for t in rows:
        if _user_may_see(user, t):
            t["assignees"] = _assignees_of(t["id"])
            t["clients"] = _clients_of(t["id"])
            out.append(t)
    return out


@router.get("/{ticket_id}")
def get_ticket(ticket_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_read")
    return _full(_get_visible_ticket(user, ticket_id), user)


# ------------------------------------------------------------------
# Erstellen / Bearbeiten / Löschen
# ------------------------------------------------------------------

class AssigneeEntry(BaseModel):
    subject_type: str   # 'user' | 'group'
    subject_id: str


class TicketBody(BaseModel):
    title: str
    description: str = ""
    priority: str = "normal"
    due_date: int | None = None            # ms oder None
    clients: list[str] = []
    assignees: list[AssigneeEntry] = []
    # Rechte-Anfrage: gesetzt, wenn das Ticket aus dem Dialog "Dir fehlt eine
    # Berechtigung" entsteht. Dann wird die Support-Gruppe zugewiesen, AUCH
    # wenn der Ersteller kein 'ticket_assign' hat - sonst landet die Meldung
    # bei niemandem. Titel und Ziel setzt in diesem Fall das Frontend fest.
    perm_request: bool = False


def _save_assignees(ticket_id: str, assignees: list[AssigneeEntry]):
    c = _conn()
    c.execute("DELETE FROM ticket_assignees WHERE ticket_id = ?", (ticket_id,))
    for a in assignees:
        if a.subject_type in ("user", "group") and a.subject_id:
            c.execute("INSERT OR IGNORE INTO ticket_assignees (ticket_id, subject_type, subject_id) "
                      "VALUES (?, ?, ?)", (ticket_id, a.subject_type, a.subject_id))
    c.commit()


def _save_clients(ticket_id: str, clients: list[str]):
    c = _conn()
    c.execute("DELETE FROM ticket_clients WHERE ticket_id = ?", (ticket_id,))
    for cid in clients:
        if cid:
            c.execute("INSERT OR IGNORE INTO ticket_clients (ticket_id, client_id) VALUES (?, ?)",
                      (ticket_id, cid))
    c.commit()


@router.post("")
def create_ticket(body: TicketBody, user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_create")
    if not body.title.strip():
        raise HTTPException(400, "Titel fehlt")
    if body.priority not in VALID_PRIO:
        raise HTTPException(400, "Ungültige Priorität")
    tid = uuid.uuid4().hex
    now = _now()
    _conn().execute(
        "INSERT INTO tickets (id, title, description, status, priority, created_by, "
        "created_at, updated_at, due_date) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?)",
        (tid, body.title.strip(), body.description, body.priority,
         user["username"], now, now, body.due_date))
    _conn().commit()
    _save_clients(tid, body.clients)
    # Zuweisen direkt beim Erstellen nur mit ticket_assign - sonst still leer.
    if body.assignees and user_has_permission(user, "ticket_assign"):
        _save_assignees(tid, body.assignees)
    elif body.perm_request:
        # Rechte-Anfrage: immer an die Support-Gruppe, unabhaengig von
        # 'ticket_assign'. Gibt es die Gruppe nicht, bleibt das Ticket
        # unzugewiesen - es ist trotzdem angelegt und sichtbar.
        grp = db.get_group_by_name(db.DEFAULT_GROUP_SUPPORT)
        if grp:
            _save_assignees(tid, [AssigneeEntry(subject_type="group",
                                                subject_id=grp["id"])])
    db.add_audit_entry(user["username"], "ticket.created", target=tid,
                       details=body.title.strip())
    vis.log("ticket", tid, user, "ticket.created", body.title.strip())
    return _full(dict(_conn().execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()), user)


@router.put("/{ticket_id}")
def update_ticket(ticket_id: str, body: TicketBody,
                        user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_edit")
    t = _get_visible_ticket(user, ticket_id)
    if not body.title.strip():
        raise HTTPException(400, "Titel fehlt")
    if body.priority not in VALID_PRIO:
        raise HTTPException(400, "Ungültige Priorität")
    _conn().execute(
        "UPDATE tickets SET title=?, description=?, priority=?, due_date=?, updated_at=? WHERE id=?",
        (body.title.strip(), body.description, body.priority, body.due_date, _now(), ticket_id))
    _conn().commit()
    _save_clients(ticket_id, body.clients)
    if user_has_permission(user, "ticket_assign"):
        _save_assignees(ticket_id, body.assignees)
    db.add_audit_entry(user["username"], "ticket.updated", target=ticket_id,
                       details=t["title"])
    # Was genau sich geändert hat, für das Protokoll festhalten.
    changes = []
    if body.title.strip() != t["title"]:
        changes.append(f"Titel: „{t['title']}“ → „{body.title.strip()}“")
    if body.priority != t["priority"]:
        changes.append(f"Priorität: {t['priority']} → {body.priority}")
    if (body.description or "") != (t["description"] or ""):
        changes.append("Beschreibung geändert")
    if body.due_date != t["due_date"]:
        changes.append("Fälligkeit geändert")
    vis.log("ticket", ticket_id, user, "ticket.updated",
            "; ".join(changes) or "keine inhaltlichen Änderungen")
    return _full(_get_visible_ticket(user, ticket_id), user)


@router.delete("/{ticket_id}")
def delete_ticket(ticket_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_delete")
    t = _get_visible_ticket(user, ticket_id)
    c = _conn()
    # Freigaben der Kommentare mit aufräumen
    c.execute("DELETE FROM ticket_comment_shares WHERE comment_id IN"
              " (SELECT id FROM ticket_comments WHERE ticket_id = ?)", (ticket_id,))
    for table in ("ticket_assignees", "ticket_clients", "ticket_comments", "ticket_files"):
        c.execute(f"DELETE FROM {table} WHERE ticket_id = ?", (ticket_id,))
    c.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    c.commit()
    # Anhänge von der Platte löschen (best effort).
    import shutil
    shutil.rmtree(FILES_DIR / ticket_id, ignore_errors=True)
    db.add_audit_entry(user["username"], "ticket.deleted", target=ticket_id,
                       details=t["title"])
    vis.clear_log("ticket", ticket_id)
    return {"ok": True}


# ------------------------------------------------------------------
# Zuweisen / Status / Kommentare
# ------------------------------------------------------------------

class AssignBody(BaseModel):
    assignees: list[AssigneeEntry]


@router.put("/{ticket_id}/assignees")
def set_assignees(ticket_id: str, body: AssignBody,
                        user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_assign")
    t = _get_visible_ticket(user, ticket_id)
    _save_assignees(ticket_id, body.assignees)
    _touch(ticket_id)
    db.add_audit_entry(user["username"], "ticket.assigned", target=ticket_id,
                       details=f"{t['title']}: {len(body.assignees)} Zuweisung(en)")
    vis.log("ticket", ticket_id, user, "ticket.assignees",
            f"{len(body.assignees)} Zuweisung(en)")
    return _full(_get_visible_ticket(user, ticket_id), user)


class StatusBody(BaseModel):
    status: str


@router.put("/{ticket_id}/status")
def set_status(ticket_id: str, body: StatusBody,
                     user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_resolve")
    t = _get_visible_ticket(user, ticket_id)
    if body.status not in VALID_STATUS:
        raise HTTPException(400, "Ungültiger Status")
    resolved_at, resolved_by = t["resolved_at"], t["resolved_by"]
    if body.status == "resolved" and t["status"] != "resolved":
        resolved_at, resolved_by = _now(), user["username"]
    if body.status in ("open", "in_progress"):
        resolved_at, resolved_by = None, None
    _conn().execute(
        "UPDATE tickets SET status=?, resolved_at=?, resolved_by=?, updated_at=? WHERE id=?",
        (body.status, resolved_at, resolved_by, _now(), ticket_id))
    _conn().commit()
    db.add_audit_entry(user["username"], f"ticket.status_{body.status}", target=ticket_id,
                       details=t["title"])
    vis.log("ticket", ticket_id, user, "ticket.status",
            f"{t['status']} → {body.status}")
    return _full(_get_visible_ticket(user, ticket_id), user)


class CommentBody(BaseModel):
    text: str
    visibility: str = "all"            # all | private | custom
    # Einträge: {"type": "user"|"group", "id": "..."} - reine IDs = Benutzer.
    shared_with: list = []


def _vis_label(v: str) -> str:
    return {"all": "für alle", "private": "nur für mich",
            "custom": "für bestimmte Benutzer"}.get(v, v)


@router.post("/{ticket_id}/comments")
def add_comment(ticket_id: str, body: CommentBody,
                      user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_comment")
    _get_visible_ticket(user, ticket_id)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "Kommentar ist leer")
    v = vis.normalize_visibility(body.visibility)
    cid = uuid.uuid4().hex
    _conn().execute(
        "INSERT INTO ticket_comments (id, ticket_id, author, author_id, text,"
        " visibility, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, ticket_id, user["username"], user["id"], text, v, _now()))
    _conn().commit()
    if v == "custom":
        vis.set_shares("ticket_comment_shares", "comment_id", cid, body.shared_with)
    _touch(ticket_id)
    vis.log("ticket", ticket_id, user, "comment.created",
            f"{_vis_label(v)}: {text[:60]}")
    return _full(_get_visible_ticket(user, ticket_id), user)


@router.delete("/{ticket_id}/comments/{comment_id}")
def delete_comment(ticket_id: str, comment_id: str,
                         user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_comment")
    _get_visible_ticket(user, ticket_id)
    row = _conn().execute(
        "SELECT * FROM ticket_comments WHERE id = ? AND ticket_id = ?",
        (comment_id, ticket_id)).fetchone()
    if not row:
        raise HTTPException(404, "Kommentar nicht gefunden")
    c = dict(row)
    if not vis.may_modify(user, c):
        raise HTTPException(403, "Nur der Verfasser darf diesen Kommentar löschen")
    _conn().execute("DELETE FROM ticket_comment_shares WHERE comment_id = ?", (comment_id,))
    _conn().execute("DELETE FROM ticket_comments WHERE id = ?", (comment_id,))
    _conn().commit()
    _touch(ticket_id)
    vis.log("ticket", ticket_id, user, "comment.deleted",
            c["text"][:60] if vis.may_see(user, c) else "(privater Kommentar)")
    return _full(_get_visible_ticket(user, ticket_id), user)


# ------------------------------------------------------------------
# Aktivitätsprotokoll des Tickets
# ------------------------------------------------------------------

@router.get("/{ticket_id}/activity")
def ticket_activity(ticket_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_read")
    _get_visible_ticket(user, ticket_id)
    entries = vis.get_log("ticket", ticket_id)
    for e in entries:
        e["label"] = vis.ACTION_LABELS.get(e["action"], e["action"])
    return entries


@router.get("/meta/users")
def ticket_users(user: dict = Depends(get_current_user)):
    """Auswahlliste für die Kommentar-Sichtbarkeit 'für bestimmte':
    Benutzer UND Gruppen (unverwaltete AD-Gruppen sind markiert)."""
    require_perm(user, "ticket_read")
    return {
        "users": [{"id": u["id"], "username": u["username"]}
                  for u in db.list_users() if u["id"] != user["id"]],
        "groups": [{"id": g["id"], "name": g["name"],
                    "is_ad_group": bool(g.get("is_ad_group")),
                    "unmanaged": bool(g.get("unmanaged"))}
                   for g in db.list_groups()],
    }


# ------------------------------------------------------------------
# Datei-Anhänge (Screenshots etc.) - Upload als roher Body (kein multipart).
# ------------------------------------------------------------------

@router.post("/{ticket_id}/files")
async def upload_file(ticket_id: str, request: Request, filename: str = "datei",
                      user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_comment")
    _get_visible_ticket(user, ticket_id)
    data = await request.body()
    if not data:
        raise HTTPException(400, "Leerer Upload")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "Datei zu groß (max. 25 MB)")
    # Dateinamen entschärfen (nur Basename, keine Pfade).
    safe_name = pathlib.Path(filename).name or "datei"
    fid = uuid.uuid4().hex
    target_dir = FILES_DIR / ticket_id
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / fid).write_bytes(data)
    _conn().execute(
        "INSERT INTO ticket_files (id, ticket_id, filename, size, mime, uploaded_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fid, ticket_id, safe_name, len(data),
         request.headers.get("content-type") or "application/octet-stream",
         user["username"], _now()))
    _conn().commit()
    _touch(ticket_id)
    vis.log("ticket", ticket_id, user, "file.uploaded", safe_name)
    return _full(_get_visible_ticket(user, ticket_id), user)


@router.get("/{ticket_id}/files/{file_id}")
def download_file(ticket_id: str, file_id: str,
                        user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_read")
    _get_visible_ticket(user, ticket_id)
    row = _conn().execute(
        "SELECT * FROM ticket_files WHERE id = ? AND ticket_id = ?",
        (file_id, ticket_id)).fetchone()
    if not row:
        raise HTTPException(404, "Datei nicht gefunden")
    path = FILES_DIR / ticket_id / file_id
    if not path.exists():
        raise HTTPException(404, "Datei fehlt auf dem Server")
    return FileResponse(path, media_type=row["mime"] or "application/octet-stream",
                        filename=row["filename"])


@router.delete("/{ticket_id}/files/{file_id}")
def delete_file(ticket_id: str, file_id: str,
                      user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_edit")
    _get_visible_ticket(user, ticket_id)
    frow = _conn().execute(
        "SELECT filename FROM ticket_files WHERE id = ? AND ticket_id = ?",
        (file_id, ticket_id)).fetchone()
    _conn().execute("DELETE FROM ticket_files WHERE id = ? AND ticket_id = ?",
                    (file_id, ticket_id))
    _conn().commit()
    try:
        (FILES_DIR / ticket_id / file_id).unlink()
    except OSError:
        pass
    _touch(ticket_id)
    vis.log("ticket", ticket_id, user, "file.deleted",
            frow["filename"] if frow else file_id)
    return _full(_get_visible_ticket(user, ticket_id), user)


# Auswahl-Listen für den Zuweisungs-Dialog.
@router.get("/meta/support-group")
def support_group(user: dict = Depends(get_current_user)):
    """Die Support-Standardgruppe - fuer den Dialog bei fehlenden Rechten.
    Braucht absichtlich nur einen Login: Wer ein Ticket aufmachen darf, muss
    auch wissen duerfen, wohin es geht."""
    grp = db.get_group_by_name(db.DEFAULT_GROUP_SUPPORT)
    return {"id": grp["id"] if grp else None,
            "name": db.DEFAULT_GROUP_SUPPORT,
            "exists": bool(grp)}


@router.get("/meta/subjects")
def assign_subjects(user: dict = Depends(get_current_user)):
    require_perm(user, "ticket_read")
    users = [{"id": u["id"], "label": u.get("display_name") or u["username"]}
             for u in db.list_users()]
    # Flags mitliefern, damit das Frontend unverwaltete AD-Gruppen in einen
    # eigenen Ordner einsortieren kann.
    groups = [{"id": g["id"], "label": g["name"], "name": g["name"],
               "is_ad_group": bool(g.get("is_ad_group")),
               "unmanaged": bool(g.get("unmanaged"))}
              for g in db.list_groups()]
    return {"users": users, "groups": groups}

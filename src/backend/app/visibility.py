"""
visibility.py
-------------
Gemeinsame Bausteine für Client-Notizen und Ticket-Kommentare:

1. SICHTBARKEIT einzelner Einträge
     'all'     -> für jeden sichtbar, der den übergeordneten Datensatz
                  (Client bzw. Ticket) überhaupt sehen darf
     'private' -> nur für den Verfasser
     'custom'  -> Verfasser + ausdrücklich freigegebene Benutzer
   Super-Admins sehen ausdrücklich NICHT automatisch private Einträge -
   "nur für mich" soll verlässlich bedeuten, was es sagt. Löschen dürfen
   Admins sie trotzdem (Aufräumen), sie bekommen aber den Text nicht zu sehen.

2. AKTIVITÄTSPROTOKOLL (activity_log)
   Wer hat wann was getan - für Notizen (entity_type "client_notes",
   entity_id = client_id) und Tickets (entity_type "ticket").
"""

import time
import uuid

from app import db

VISIBILITIES = ("all", "private", "custom")


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


def normalize_visibility(value: str | None) -> str:
    v = (value or "all").lower()
    return v if v in VISIBILITIES else "all"


# ------------------------------------------------------------------
# Freigaben (custom)
# ------------------------------------------------------------------

def _norm_share(item) -> tuple[str, str] | None:
    """Nimmt "u123" (= Benutzer), {"type":"group","id":"g1"} oder
    "group:g1" entgegen und liefert (subject_type, id)."""
    if isinstance(item, dict):
        t = (item.get("type") or "user").lower()
        i = item.get("id") or ""
    elif isinstance(item, str) and ":" in item and item.split(":", 1)[0] in ("user", "group"):
        t, i = item.split(":", 1)
    else:
        t, i = "user", str(item or "")
    if not i:
        return None
    if t == "group":
        return ("group", i) if db.get_group(i) else None
    return ("user", i) if db.get_user_by_id(i) else None


def set_shares(table: str, id_column: str, entry_id: str, subjects: list) -> None:
    """Ersetzt die Freigabe-Liste eines Eintrags (Benutzer UND Gruppen)."""
    c = db._conn
    c.execute(f"DELETE FROM {table} WHERE {id_column} = ?", (entry_id,))
    seen = set()
    for item in (subjects or []):
        norm = _norm_share(item)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        c.execute(
            f"INSERT OR IGNORE INTO {table} ({id_column}, user_id, subject_type)"
            f" VALUES (?, ?, ?)", (entry_id, norm[1], norm[0]))
    c.commit()


def get_shares(table: str, id_column: str, entry_id: str) -> list[dict]:
    rows = db._conn.execute(
        f"SELECT user_id, subject_type FROM {table} WHERE {id_column} = ?",
        (entry_id,)).fetchall()
    return [{"type": r["subject_type"] or "user", "id": r["user_id"]} for r in rows]


def shares_map(table: str, id_column: str, entry_ids: list[str]) -> dict[str, list[dict]]:
    """Freigaben für viele Einträge auf einmal (spart N Abfragen)."""
    if not entry_ids:
        return {}
    marks = ",".join("?" for _ in entry_ids)
    rows = db._conn.execute(
        f"SELECT {id_column} AS eid, user_id, subject_type FROM {table}"
        f" WHERE {id_column} IN ({marks})", tuple(entry_ids)).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["eid"], []).append(
            {"type": r["subject_type"] or "user", "id": r["user_id"]})
    return out


# ------------------------------------------------------------------
# Sichtbarkeits-Prüfung
# ------------------------------------------------------------------

def may_see(user: dict, entry: dict, shared_with: list | None = None) -> bool:
    """entry braucht die Felder 'visibility' und 'author_id'.
    shared_with ist eine Liste aus {"type","id"} (oder reine Benutzer-IDs
    aus Altbeständen). Freigegeben ist, wer selbst genannt ist ODER in einer
    freigegebenen Gruppe steckt."""
    vis = normalize_visibility(entry.get("visibility"))
    if vis == "all":
        return True
    if entry.get("author_id") and entry["author_id"] == user["id"]:
        return True
    if vis != "custom":
        return False
    my_groups = None
    for item in (shared_with or []):
        t = item.get("type", "user") if isinstance(item, dict) else "user"
        i = item.get("id") if isinstance(item, dict) else item
        if t == "user" and i == user["id"]:
            return True
        if t == "group":
            if my_groups is None:
                my_groups = set(db.get_user_group_ids(user["id"]))
            if i in my_groups:
                return True
    return False


def may_modify(user: dict, entry: dict) -> bool:
    """Ändern/Löschen darf der Verfasser - und ein Admin (Aufräumen)."""
    from app.auth import is_super_admin
    return is_super_admin(user) or (entry.get("author_id") == user["id"])


def redact(entry: dict) -> dict:
    """Für Admins: Eintrag ohne Inhalt, damit "nur für mich" dicht bleibt,
    der Eintrag aber verwaltbar (löschbar) ist."""
    out = dict(entry)
    out["text"] = ""
    out["hidden"] = True
    return out


# ------------------------------------------------------------------
# Aktivitätsprotokoll
# ------------------------------------------------------------------

def log(entity_type: str, entity_id: str, user: dict | None,
        action: str, details: str = "") -> None:
    db._conn.execute(
        "INSERT INTO activity_log (id, entity_type, entity_id, actor_id, actor_name,"
        " action, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (new_id(), entity_type, entity_id,
         (user or {}).get("id"), (user or {}).get("username") or "System",
         action, details or "", now_ms()))
    db._conn.commit()


def get_log(entity_type: str, entity_id: str, limit: int = 200) -> list[dict]:
    rows = db._conn.execute(
        "SELECT * FROM activity_log WHERE entity_type = ? AND entity_id = ?"
        " ORDER BY created_at DESC LIMIT ?",
        (entity_type, entity_id, max(1, min(int(limit or 200), 1000)))).fetchall()
    return [dict(r) for r in rows]


def clear_log(entity_type: str, entity_id: str) -> None:
    """Beim Löschen des übergeordneten Datensatzes aufräumen."""
    db._conn.execute("DELETE FROM activity_log WHERE entity_type = ? AND entity_id = ?",
                     (entity_type, entity_id))
    db._conn.commit()


# Klartext-Beschreibungen für die Anzeige im Frontend.
ACTION_LABELS = {
    "note.created": "Notiz erstellt",
    "note.updated": "Notiz bearbeitet",
    "note.deleted": "Notiz gelöscht",
    "note.visibility": "Sichtbarkeit geändert",
    "note.pinned": "Notiz angeheftet",
    "note.unpinned": "Notiz losgelöst",
    "ticket.created": "Ticket erstellt",
    "ticket.updated": "Ticket bearbeitet",
    "ticket.status": "Status geändert",
    "ticket.assignees": "Zuweisungen geändert",
    "ticket.deleted": "Ticket gelöscht",
    "comment.created": "Kommentar geschrieben",
    "comment.deleted": "Kommentar gelöscht",
    "file.uploaded": "Datei angehängt",
    "file.deleted": "Datei entfernt",
}

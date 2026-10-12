"""
routers/todos_routes.py
-----------------------
Persönliche Todo-Liste ("Mini-Notion"). ALLES ist privat: jeder Datensatz
hängt an user_id, es gibt keine Freigaben und keine Admin-Einsicht.

Aufbau
  * Kategorien = Wichtigkeits-Spalten. Vier feste (builtin) werden pro
    Benutzer einmalig angelegt: hoch / mittel / niedrig / Archiv.
    Zusätzlich darf jeder eigene Kategorien anlegen (rank = Sortierung).
  * Todos hängen in genau einer Kategorie. Abgehakte bleiben in ihrer
    Kategorie stehen und rutschen dort ans Ende (Sortierung im SELECT).
  * Wiederkehrende Todos (recurring=1) werden beim ersten Laden an einem
    neuen Tag automatisch wieder auf offen gesetzt. Maßgeblich ist das
    Datum des BROWSERS (Query-Parameter ?today=YYYY-MM-DD), sonst die
    Server-Zeit. Dabei wird eine Streak mitgeführt.
  * Archivieren = Verschieben in die builtin-Kategorie 'archive'. Die
    ursprüngliche Kategorie wird in prev_category_id gemerkt, damit
    "Wiederherstellen" zurück an die richtige Stelle legt.

Berechtigung: alle Routen erfordern das globale Recht 'use_todos'.
"""

import time
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

router = APIRouter(prefix="/api/todos", tags=["todos"])

MAX_TITLE = 300
MAX_NOTES = 5000

# Feste Kategorien, die jeder Benutzer beim ersten Öffnen bekommt.
# (key, Anzeigename, Farbe, rank)  - rank klein = wichtig = weiter vorne.
BUILTIN_CATEGORIES = [
    ("high", "Wichtig", "#f75c5c", 10),
    ("mid", "Normal", "#38bdf8", 20),
    ("low", "Irgendwann", "#7f93ad", 30),
    ("archive", "Archiv", "#4b5a72", 9000),
]


def _conn():
    return db._conn


def _now() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return str(uuid.uuid4())


def _parse_day(value: str | None) -> date:
    """'YYYY-MM-DD' vom Browser übernehmen, sonst Server-Datum."""
    if value:
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    return date.today()


# ------------------------------------------------------------------
# Kategorien
# ------------------------------------------------------------------

def _ensure_builtins(user_id: str) -> None:
    """Fehlende Standard-Kategorien anlegen (idempotent)."""
    have = {r["builtin"] for r in _conn().execute(
        "SELECT builtin FROM todo_categories WHERE user_id = ? AND builtin != ''",
        (user_id,)).fetchall()}
    missing = [c for c in BUILTIN_CATEGORIES if c[0] not in have]
    if not missing:
        return
    now = _now()
    _conn().executemany(
        "INSERT INTO todo_categories (id, user_id, name, color, rank, builtin, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(_new_id(), user_id, name, color, rank, key, now)
         for key, name, color, rank in missing])
    _conn().commit()


def _categories(user_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM todo_categories WHERE user_id = ?"
        " ORDER BY rank ASC, created_at ASC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def _archive_id(user_id: str) -> str:
    row = _conn().execute(
        "SELECT id FROM todo_categories WHERE user_id = ? AND builtin = 'archive'",
        (user_id,)).fetchone()
    if not row:
        _ensure_builtins(user_id)
        row = _conn().execute(
            "SELECT id FROM todo_categories WHERE user_id = ? AND builtin = 'archive'",
            (user_id,)).fetchone()
    return row["id"]


def _own_category(user_id: str, category_id: str) -> dict:
    row = _conn().execute(
        "SELECT * FROM todo_categories WHERE id = ? AND user_id = ?",
        (category_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Kategorie nicht gefunden")
    return dict(row)


def _own_todo(user_id: str, todo_id: str) -> dict:
    row = _conn().execute(
        "SELECT * FROM todos WHERE id = ? AND user_id = ?",
        (todo_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Todo nicht gefunden")
    return dict(row)


# ------------------------------------------------------------------
# Tages-Reset für wiederkehrende Todos
# ------------------------------------------------------------------

def _daily_reset(user_id: str, today: date) -> int:
    """
    Alle wiederkehrenden Todos, die zuletzt an einem FRÜHEREN Tag
    zurückgesetzt wurden, wieder auf offen stellen.

    Streak-Regel: wurde das Todo gestern erledigt, läuft die Serie weiter
    (der Zähler wurde beim Abhaken bereits erhöht). Sonst reißt sie ab.
    Rückgabe: Anzahl zurückgesetzter Einträge.
    """
    iso = today.isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    rows = _conn().execute(
        "SELECT id, done, last_done_day, streak FROM todos"
        " WHERE user_id = ? AND recurring = 1 AND last_reset != ?",
        (user_id, iso)).fetchall()
    if not rows:
        return 0
    now = _now()
    for r in rows:
        keep = (r["last_done_day"] or "") in (iso, yesterday)
        _conn().execute(
            "UPDATE todos SET done = 0, done_at = NULL, last_reset = ?,"
            " streak = ?, updated_at = ? WHERE id = ?",
            (iso, r["streak"] if keep else 0, now, r["id"]))
    _conn().commit()
    return len(rows)


# ------------------------------------------------------------------
# Lesen
# ------------------------------------------------------------------

@router.get("")
def list_todos(today: str | None = None,
                     user: dict = Depends(get_current_user)):
    """
    Kompletter Zustand der Liste. Sortierung der Todos:
      1. Kategorie (rank)
      2. offen vor erledigt   -> Abgehaktes bleibt in der Kategorie, ganz unten
      3. manuelle Reihenfolge (rank), dann Erstellzeit
    """
    require_perm(user, "use_todos")
    uid = user["id"]
    _ensure_builtins(uid)
    reset_count = _daily_reset(uid, _parse_day(today))

    cats = _categories(uid)
    order = {c["id"]: i for i, c in enumerate(cats)}
    rows = [dict(r) for r in _conn().execute(
        "SELECT * FROM todos WHERE user_id = ?"
        " ORDER BY done ASC, rank ASC, created_at ASC", (uid,)).fetchall()]
    rows.sort(key=lambda t: (order.get(t["category_id"], 10**6),
                             t["done"], t["rank"], t["created_at"]))
    return {"categories": cats, "todos": rows, "reset_count": reset_count,
            "today": _parse_day(today).isoformat()}


# ------------------------------------------------------------------
# Kategorien anlegen / ändern / löschen
# ------------------------------------------------------------------

class CategoryBody(BaseModel):
    name: str
    color: str = "#38bdf8"
    rank: int | None = None


@router.post("/categories")
def create_category(body: CategoryBody, user: dict = Depends(get_current_user)):
    require_perm(user, "use_todos")
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name fehlt")
    if len(name) > 60:
        raise HTTPException(400, "Name zu lang (max. 60 Zeichen)")
    rank = body.rank
    if rank is None:
        # Standardmäßig ans Ende der normalen Kategorien (vor das Archiv).
        row = _conn().execute(
            "SELECT MAX(rank) AS m FROM todo_categories"
            " WHERE user_id = ? AND builtin != 'archive'", (user["id"],)).fetchone()
        rank = int(row["m"] or 0) + 10
    cid = _new_id()
    _conn().execute(
        "INSERT INTO todo_categories (id, user_id, name, color, rank, builtin, created_at)"
        " VALUES (?, ?, ?, ?, ?, '', ?)",
        (cid, user["id"], name, body.color, rank, _now()))
    _conn().commit()
    return {"id": cid}


class CategoryOrderBody(BaseModel):
    ids: list[str] = []


@router.put("/categories/order")
def reorder_categories(body: CategoryOrderBody,
                             user: dict = Depends(get_current_user)):
    """Reihenfolge der Wichtigkeits-Spalten setzen (Archiv bleibt hinten)."""
    require_perm(user, "use_todos")
    rank = 10
    for cid in body.ids:
        cat = _own_category(user["id"], cid)
        if cat["builtin"] == "archive":
            continue
        _conn().execute("UPDATE todo_categories SET rank = ? WHERE id = ?", (rank, cid))
        rank += 10
    _conn().commit()
    return {"ok": True}


@router.put("/categories/{category_id}")
def update_category(category_id: str, body: CategoryBody,
                          user: dict = Depends(get_current_user)):
    require_perm(user, "use_todos")
    cat = _own_category(user["id"], category_id)
    name = (body.name or "").strip() or cat["name"]
    rank = cat["rank"] if body.rank is None else int(body.rank)
    if cat["builtin"] == "archive":
        rank = cat["rank"]          # Archiv bleibt immer ganz hinten
    _conn().execute(
        "UPDATE todo_categories SET name = ?, color = ?, rank = ? WHERE id = ?",
        (name[:60], body.color, rank, category_id))
    _conn().commit()
    return {"ok": True}


@router.delete("/categories/{category_id}")
def delete_category(category_id: str, user: dict = Depends(get_current_user)):
    """Eigene Kategorie löschen. Enthaltene Todos wandern ins Archiv."""
    require_perm(user, "use_todos")
    cat = _own_category(user["id"], category_id)
    if cat["builtin"]:
        raise HTTPException(400, "Standard-Kategorien können nicht gelöscht werden")
    _conn().execute(
        "UPDATE todos SET category_id = ?, updated_at = ? WHERE category_id = ? AND user_id = ?",
        (_archive_id(user["id"]), _now(), category_id, user["id"]))
    _conn().execute("DELETE FROM todo_categories WHERE id = ?", (category_id,))
    _conn().commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Todos anlegen / ändern / löschen
# ------------------------------------------------------------------

class TodoBody(BaseModel):
    title: str
    notes: str = ""
    category_id: str | None = None
    recurring: bool = False
    due_at: int | None = None


def _clean(body: TodoBody) -> tuple[str, str]:
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "Titel fehlt")
    if len(title) > MAX_TITLE:
        raise HTTPException(400, f"Titel zu lang (max. {MAX_TITLE} Zeichen)")
    return title, (body.notes or "").strip()[:MAX_NOTES]


@router.post("")
def create_todo(body: TodoBody, today: str | None = None,
                      user: dict = Depends(get_current_user)):
    require_perm(user, "use_todos")
    uid = user["id"]
    _ensure_builtins(uid)
    title, notes = _clean(body)

    if body.category_id:
        cat = _own_category(uid, body.category_id)
    else:
        cat = next(c for c in _categories(uid) if c["builtin"] == "mid")

    row = _conn().execute(
        "SELECT MAX(rank) AS m FROM todos WHERE user_id = ? AND category_id = ?",
        (uid, cat["id"])).fetchone()
    rank = int(row["m"] or 0) + 10
    tid, now = _new_id(), _now()
    _conn().execute(
        "INSERT INTO todos (id, user_id, category_id, prev_category_id, title, notes,"
        " done, done_at, recurring, last_reset, last_done_day, streak, due_at, rank,"
        " created_at, updated_at)"
        " VALUES (?, ?, ?, '', ?, ?, 0, NULL, ?, ?, '', 0, ?, ?, ?, ?)",
        (tid, uid, cat["id"], title, notes, int(bool(body.recurring)),
         _parse_day(today).isoformat(), body.due_at, rank, now, now))
    _conn().commit()
    return {"id": tid}


@router.put("/{todo_id}")
def update_todo(todo_id: str, body: TodoBody,
                      user: dict = Depends(get_current_user)):
    require_perm(user, "use_todos")
    todo = _own_todo(user["id"], todo_id)
    title, notes = _clean(body)
    category_id = body.category_id or todo["category_id"]
    _own_category(user["id"], category_id)
    _conn().execute(
        "UPDATE todos SET title = ?, notes = ?, category_id = ?, recurring = ?,"
        " due_at = ?, updated_at = ? WHERE id = ?",
        (title, notes, category_id, int(bool(body.recurring)), body.due_at,
         _now(), todo_id))
    _conn().commit()
    return {"ok": True}


class ToggleBody(BaseModel):
    done: bool | None = None
    today: str | None = None


@router.post("/{todo_id}/toggle")
def toggle_todo(todo_id: str, body: ToggleBody,
                      user: dict = Depends(get_current_user)):
    """
    Abhaken bzw. wieder öffnen. Bei wiederkehrenden Todos wird die Streak
    hochgezählt (bzw. beim Zurücknehmen wieder abgezogen).
    """
    require_perm(user, "use_todos")
    todo = _own_todo(user["id"], todo_id)
    done = (not todo["done"]) if body.done is None else bool(body.done)
    day = _parse_day(body.today)
    iso = day.isoformat()
    streak = todo["streak"]

    if todo["recurring"]:
        if done and todo["last_done_day"] != iso:
            yesterday = (day - timedelta(days=1)).isoformat()
            streak = (streak + 1) if todo["last_done_day"] == yesterday else 1
        elif not done and todo["last_done_day"] == iso:
            streak = max(0, streak - 1)

    _conn().execute(
        "UPDATE todos SET done = ?, done_at = ?, last_done_day = ?, streak = ?,"
        " updated_at = ? WHERE id = ?",
        (int(done), _now() if done else None,
         iso if done else (todo["last_done_day"] or ""), streak, _now(), todo_id))
    _conn().commit()
    return {"ok": True, "done": done, "streak": streak}


class MoveBody(BaseModel):
    category_id: str
    # Neue Reihenfolge der Ziel-Kategorie (IDs von oben nach unten).
    order: list[str] = []


@router.post("/{todo_id}/move")
def move_todo(todo_id: str, body: MoveBody,
                    user: dict = Depends(get_current_user)):
    """Todo in eine andere Kategorie schieben (Drag & Drop)."""
    require_perm(user, "use_todos")
    _own_todo(user["id"], todo_id)
    _own_category(user["id"], body.category_id)
    _conn().execute(
        "UPDATE todos SET category_id = ?, prev_category_id = '', updated_at = ?"
        " WHERE id = ?", (body.category_id, _now(), todo_id))
    rank = 10
    for tid in (body.order or [todo_id]):
        _conn().execute(
            "UPDATE todos SET rank = ? WHERE id = ? AND user_id = ?",
            (rank, tid, user["id"]))
        rank += 10
    _conn().commit()
    return {"ok": True}


@router.post("/{todo_id}/archive")
def archive_todo(todo_id: str, user: dict = Depends(get_current_user)):
    """Ins Archiv verschieben. Die alte Kategorie wird gemerkt."""
    require_perm(user, "use_todos")
    todo = _own_todo(user["id"], todo_id)
    arch = _archive_id(user["id"])
    if todo["category_id"] == arch:
        return {"ok": True}
    _conn().execute(
        "UPDATE todos SET prev_category_id = ?, category_id = ?, recurring = 0,"
        " updated_at = ? WHERE id = ?",
        (todo["category_id"], arch, _now(), todo_id))
    _conn().commit()
    return {"ok": True}


@router.post("/{todo_id}/unarchive")
def unarchive_todo(todo_id: str, user: dict = Depends(get_current_user)):
    """Aus dem Archiv zurück in die ursprüngliche Kategorie."""
    require_perm(user, "use_todos")
    todo = _own_todo(user["id"], todo_id)
    target = todo["prev_category_id"] or ""
    exists = target and _conn().execute(
        "SELECT 1 FROM todo_categories WHERE id = ? AND user_id = ?",
        (target, user["id"])).fetchone()
    if not exists:
        target = next(c["id"] for c in _categories(user["id"]) if c["builtin"] == "mid")
    _conn().execute(
        "UPDATE todos SET category_id = ?, prev_category_id = '', updated_at = ?"
        " WHERE id = ?", (target, _now(), todo_id))
    _conn().commit()
    return {"ok": True, "category_id": target}


@router.post("/archive-done")
def archive_all_done(user: dict = Depends(get_current_user)):
    """Alle erledigten, NICHT wiederkehrenden Todos auf einen Rutsch archivieren."""
    require_perm(user, "use_todos")
    arch = _archive_id(user["id"])
    cur = _conn().execute(
        "UPDATE todos SET prev_category_id = category_id, category_id = ?, updated_at = ?"
        " WHERE user_id = ? AND done = 1 AND recurring = 0 AND category_id != ?",
        (arch, _now(), user["id"], arch))
    _conn().commit()
    return {"ok": True, "moved": cur.rowcount}


@router.delete("/{todo_id}")
def delete_todo(todo_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "use_todos")
    _own_todo(user["id"], todo_id)
    _conn().execute("DELETE FROM todos WHERE id = ? AND user_id = ?",
                    (todo_id, user["id"]))
    _conn().commit()
    return {"ok": True}

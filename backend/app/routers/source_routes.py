"""
routers/source_routes.py
------------------------
Admin-"Source"-Werkzeuge (nur für Super-Admins):
  - Datei-Explorer über das Projekt (backend / frontend / agent / …) inkl.
    Lesen und Speichern von Dateien.
  - Direkter Datenbank-Zugriff: Tabellen auflisten, Inhalte ansehen, beliebiges
    SQL ausführen (inkl. der Key/Value-"settings" mit JSON-Arrays).

Die zugehörige Live-Shell zum Backend-Host läuft über Socket.IO (siehe
sockets.py, host-term-*), da sie interaktiv/streamend ist.

ACHTUNG: Diese Endpunkte geben einem Admin vollen Zugriff auf Dateien und
Datenbank des Backend-Hosts. Sie sind bewusst NUR für Super-Admins freigegeben.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/source", tags=["source"])

# Projekt-Wurzel = Ordner, der backend/ frontend/ agent/ enthält.
# source_routes.py liegt in backend/app/routers/ -> drei Ebenen hoch.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Maximale Dateigröße, die im Editor geladen wird (größere nur als Hinweis).
_MAX_READ_BYTES = 2 * 1024 * 1024


def _safe_path(rel: str) -> Path:
    """
    Löst einen (relativen) Pfad gegen die Projekt-Wurzel auf und stellt sicher,
    dass er die Wurzel NICHT verlässt (kein '..'-Ausbruch).
    """
    rel = (rel or "").lstrip("/")
    p = (PROJECT_ROOT / rel).resolve()
    if p != PROJECT_ROOT and PROJECT_ROOT not in p.parents:
        raise HTTPException(400, "Pfad außerhalb des Projekts ist nicht erlaubt")
    return p


# ------------------------------------------------------------------
# Datei-Explorer
# ------------------------------------------------------------------

@router.get("/roots")
async def get_roots(user: dict = Depends(get_current_user)):
    """Schnellzugriff-Ordner + Projekt-Wurzel."""
    require_admin(user)
    roots = []
    for name in ["backend", "frontend", "agent"]:
        if (PROJECT_ROOT / name).is_dir():
            roots.append({"name": name, "path": name})
    roots.append({"name": "(Projekt-Wurzel)", "path": ""})
    return {"project_root": str(PROJECT_ROOT), "roots": roots}


@router.get("/list")
async def list_dir(path: str = "", user: dict = Depends(get_current_user)):
    """Listet den Inhalt eines Ordners (Ordner zuerst, dann Dateien, alphabetisch)."""
    require_admin(user)
    p = _safe_path(path)
    if not p.exists():
        raise HTTPException(404, "Pfad nicht gefunden")
    if not p.is_dir():
        raise HTTPException(400, "Kein Ordner")
    entries = []
    for child in p.iterdir():
        try:
            st = child.stat()
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": st.st_size,
                "mtime": int(st.st_mtime * 1000),
            })
        except OSError:
            continue
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    rel = str(p.relative_to(PROJECT_ROOT)) if p != PROJECT_ROOT else ""
    parent = "" if rel in ("", ".") else str(Path(rel).parent).replace(".", "")
    return {"path": rel, "parent": parent, "entries": entries}


@router.get("/read")
async def read_file(path: str, user: dict = Depends(get_current_user)):
    """Liest eine Textdatei (bis 2 MB). Binärdateien werden als solche markiert."""
    require_admin(user)
    p = _safe_path(path)
    if not p.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    size = p.stat().st_size
    if size > _MAX_READ_BYTES:
        return {"path": path, "too_large": True, "size": size, "content": ""}
    raw = p.read_bytes()
    try:
        content = raw.decode("utf-8")
        return {"path": path, "content": content, "size": size, "binary": False}
    except UnicodeDecodeError:
        return {"path": path, "content": "", "size": size, "binary": True}


class WriteBody(BaseModel):
    path: str
    content: str


@router.put("/write")
async def write_file(body: WriteBody, user: dict = Depends(get_current_user)):
    """Speichert Textinhalt in eine Datei (überschreibt)."""
    require_admin(user)
    p = _safe_path(body.path)
    if p.is_dir():
        raise HTTPException(400, "Ist ein Ordner")
    p.write_text(body.content, encoding="utf-8")
    db.add_audit_entry(user["username"], "source.file_saved", target=body.path)
    return {"ok": True, "size": len(body.content.encode("utf-8"))}


# ------------------------------------------------------------------
# Datenbank
# ------------------------------------------------------------------

@router.get("/db/tables")
async def db_tables(user: dict = Depends(get_current_user)):
    """Alle Tabellen mit Zeilenanzahl."""
    require_admin(user)
    rows = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    out = []
    for r in rows:
        name = r["name"]
        try:
            cnt = db._conn.execute(f'SELECT COUNT(*) AS c FROM "{name}"').fetchone()["c"]
        except Exception:
            cnt = None
        out.append({"name": name, "count": cnt})
    return {"tables": out}


@router.get("/db/table")
async def db_table(name: str, limit: int = 200, offset: int = 0,
                   user: dict = Depends(get_current_user)):
    """Inhalt einer Tabelle (Spalten + Zeilen, paginiert)."""
    require_admin(user)
    # Tabellenname validieren (nur bekannte Tabellen).
    valid = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if name not in valid:
        raise HTTPException(404, "Tabelle nicht gefunden")
    limit = max(1, min(1000, limit))
    cur = db._conn.execute(f'SELECT * FROM "{name}" LIMIT ? OFFSET ?', (limit, offset))
    cols = [d[0] for d in cur.description]
    rows = [list(r) for r in cur.fetchall()]
    total = db._conn.execute(f'SELECT COUNT(*) AS c FROM "{name}"').fetchone()["c"]
    return {"name": name, "columns": cols, "rows": rows,
            "total": total, "limit": limit, "offset": offset}


class SqlBody(BaseModel):
    sql: str


@router.post("/db/query")
async def db_query(body: SqlBody, user: dict = Depends(get_current_user)):
    """
    Führt beliebiges SQL aus. Bei SELECT werden Spalten + Zeilen zurückgegeben,
    sonst die betroffene Zeilenanzahl. (Nur Super-Admin.)
    """
    require_admin(user)
    sql = (body.sql or "").strip()
    if not sql:
        raise HTTPException(400, "Leeres SQL")
    try:
        cur = db._conn.execute(sql)
        if cur.description:  # SELECT / PRAGMA mit Ergebnismenge
            cols = [d[0] for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]
            db._conn.commit()
            return {"kind": "rows", "columns": cols, "rows": rows, "count": len(rows)}
        else:
            db._conn.commit()
            db.add_audit_entry(user["username"], "source.sql_exec",
                               details=sql[:200])
            return {"kind": "exec", "rowcount": cur.rowcount}
    except Exception as e:
        raise HTTPException(400, f"SQL-Fehler: {e}")

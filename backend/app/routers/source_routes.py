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

import io
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_admin, require_perm

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
    require_perm(user, "see_source")
    roots = []
    for name in ["backend", "frontend", "agent"]:
        if (PROJECT_ROOT / name).is_dir():
            roots.append({"name": name, "path": name})
    roots.append({"name": "(Projekt-Wurzel)", "path": ""})
    return {"project_root": str(PROJECT_ROOT), "roots": roots}


@router.get("/list")
async def list_dir(path: str = "", user: dict = Depends(get_current_user)):
    """Listet den Inhalt eines Ordners (Ordner zuerst, dann Dateien, alphabetisch)."""
    require_perm(user, "see_source")
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
    require_perm(user, "see_source")
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
    require_perm(user, "edit_source")
    p = _safe_path(body.path)
    if p.is_dir():
        raise HTTPException(400, "Ist ein Ordner")
    p.write_text(body.content, encoding="utf-8")
    db.add_audit_entry(user["username"], "source.file_saved", target=body.path)
    return {"ok": True, "size": len(body.content.encode("utf-8"))}


# ------------------------------------------------------------------
# Explorer: Ordner anlegen, Datei anlegen, löschen, umbenennen
# ------------------------------------------------------------------

class PathBody(BaseModel):
    path: str


class RenameBody(BaseModel):
    src: str
    dst: str


@router.post("/mkdir")
async def make_dir(body: PathBody, user: dict = Depends(get_current_user)):
    """Legt einen (verschachtelten) Ordner an."""
    require_perm(user, "edit_source")
    p = _safe_path(body.path)
    if p.exists():
        raise HTTPException(400, "Pfad existiert bereits")
    p.mkdir(parents=True, exist_ok=False)
    db.add_audit_entry(user["username"], "source.mkdir", target=body.path)
    return {"ok": True}


@router.post("/newfile")
async def new_file(body: PathBody, user: dict = Depends(get_current_user)):
    """Legt eine leere Datei an."""
    require_perm(user, "edit_source")
    p = _safe_path(body.path)
    if p.exists():
        raise HTTPException(400, "Datei existiert bereits")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")
    db.add_audit_entry(user["username"], "source.newfile", target=body.path)
    return {"ok": True}


@router.post("/delete")
async def delete_path(body: PathBody, user: dict = Depends(get_current_user)):
    """Löscht eine Datei oder einen Ordner (rekursiv). Projekt-Wurzel ist tabu."""
    require_perm(user, "delete_source")
    p = _safe_path(body.path)
    if p == PROJECT_ROOT:
        raise HTTPException(400, "Projekt-Wurzel kann nicht gelöscht werden")
    if not p.exists():
        raise HTTPException(404, "Pfad nicht gefunden")
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    db.add_audit_entry(user["username"], "source.deleted", target=body.path)
    return {"ok": True}


@router.post("/rename")
async def rename_path(body: RenameBody, user: dict = Depends(get_current_user)):
    """Benennt eine Datei/einen Ordner um bzw. verschiebt sie/ihn."""
    require_perm(user, "edit_source")
    src = _safe_path(body.src)
    dst = _safe_path(body.dst)
    if not src.exists():
        raise HTTPException(404, "Quelle nicht gefunden")
    if dst.exists():
        raise HTTPException(400, "Ziel existiert bereits")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    db.add_audit_entry(user["username"], "source.renamed",
                       target=body.src, details="-> " + body.dst)
    return {"ok": True}


# ------------------------------------------------------------------
# Datenbank
# ------------------------------------------------------------------

def _cursor_columns(cur, rows: list | None = None) -> list[str]:
    """
    Liefert die Spaltennamen eines Abfrage-Ergebnisses.

    Die App nutzt einen thread-sicheren Wrapper um SQLite (db._SafeConn), der
    ein _CursorResult zurückgibt. Je nach Backend/Version hat das Objekt
    entweder .description (wie sqlite3), .keys() (wie SQLAlchemy) - oder gar
    nichts. Dann holen wir die Namen aus der ersten Zeile (sqlite3.Row.keys()).
    Ohne diesen Fallback endete die Tabellenansicht in einem 500er
    ("'_CursorResult' object has no attribute 'description'").
    """
    desc = getattr(cur, "description", None)
    if desc:
        return [d[0] for d in desc]
    keys = getattr(cur, "keys", None)
    if callable(keys):
        try:
            names = list(keys())
            if names:
                return [str(n) for n in names]
        except Exception:
            pass
    for r in (rows or []):
        rk = getattr(r, "keys", None)
        if callable(rk):
            try:
                return [str(n) for n in rk()]
            except Exception:
                pass
        break
    return []


def _has_result_set(cur, rows: list | None = None) -> bool:
    """True, wenn die Abfrage eine Ergebnismenge geliefert hat (SELECT/PRAGMA)."""
    return bool(_cursor_columns(cur, rows)) or bool(rows)


@router.get("/db/tables")
async def db_tables(user: dict = Depends(get_current_user)):
    """Alle Tabellen mit Zeilenanzahl."""
    require_perm(user, "see_source")
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
    require_perm(user, "see_source")
    # Tabellenname validieren (nur bekannte Tabellen).
    valid = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if name not in valid:
        raise HTTPException(404, "Tabelle nicht gefunden")
    limit = max(1, min(1000, limit))
    # rowid mitliefern, damit Zellen/Zeilen im Frontend eindeutig editier-/löschbar
    # sind. Tabellen ohne rowid (WITHOUT ROWID) fallen auf die reine Ansicht zurück.
    try:
        cur = db._conn.execute(f'SELECT rowid AS __rowid__, * FROM "{name}" LIMIT ? OFFSET ?',
                               (limit, offset))
        raw = cur.fetchall()
        cols = _cursor_columns(cur, raw)[1:]
        rowids = [r[0] for r in raw]
        rows = [list(r)[1:] for r in raw]
        if not cols:
            raise ValueError("keine Spalten ermittelbar")
    except Exception:
        cur = db._conn.execute(f'SELECT * FROM "{name}" LIMIT ? OFFSET ?', (limit, offset))
        raw = cur.fetchall()
        cols = _cursor_columns(cur, raw)
        rows = [list(r) for r in raw]
        rowids = [None] * len(rows)
    if not cols:
        # Letzte Rettung: Spalten direkt aus dem Tabellen-Schema lesen.
        cols = [r[1] for r in db._conn.execute(f'PRAGMA table_info("{name}")').fetchall()]
    total = db._conn.execute(f'SELECT COUNT(*) AS c FROM "{name}"').fetchone()["c"]
    return {"name": name, "columns": cols, "rows": rows, "rowids": rowids,
            "total": total, "limit": limit, "offset": offset}


# ------------------------------------------------------------------
# ZIP-Upload + intelligente Extraktion
# ------------------------------------------------------------------
PROJECT_MARKERS = {"frontend", "backend", "agent"}


def _extract_target_and_root(names: list[str], current_path: str):
    """
    Bestimmt (Ziel-Ordner, Content-Root im ZIP) anhand des ZIP-Inhalts:

    - frontend/backend/agent direkt im ZIP-Root -> Ziel = PROJECT_ROOT, Root = "".
    - EIN Wrapper-Ordner (z.B. "Rapalle.net-RMM/"), darin frontend/backend/agent
      -> Ziel = PROJECT_ROOT, Root = "<wrapper>/" (Wrapper wird abgestreift).
    - sonst -> Ziel = aktuell geöffneter Explorer-Ordner, Root = "".
    """
    def top(n: str) -> str:
        return n.split("/", 1)[0]

    tops = {top(n) for n in names if n.strip() and top(n)}

    if tops & PROJECT_MARKERS:
        return PROJECT_ROOT, ""

    if len(tops) == 1:
        wrapper = next(iter(tops))
        second = set()
        for n in names:
            parts = n.split("/")
            if len(parts) >= 2 and parts[0] == wrapper and parts[1]:
                second.add(parts[1])
        if second & PROJECT_MARKERS:
            return PROJECT_ROOT, wrapper + "/"

    return _safe_path(current_path), ""


@router.post("/upload-zip")
async def upload_zip(path: str = Form(""), file: UploadFile = File(...),
                     user: dict = Depends(get_current_user)):
    """
    Lädt ein ZIP hoch und extrahiert es (bestehende Dateien werden ÜBERSCHRIEBEN).
    Projekt-ZIPs (mit frontend/backend/agent - auch in einem Wrapper-Ordner)
    landen im Projekt-Root; sonstige ZIPs im aktuell geöffneten Explorer-Ordner.
    """
    require_perm(user, "edit_source")
    raw = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Keine gültige ZIP-Datei")

    names = zf.namelist()
    dest, content_root = _extract_target_and_root(names, path)

    extracted, skipped = [], []
    for info in zf.infolist():
        name = info.filename
        if content_root:
            if not name.startswith(content_root):
                continue
            rel = name[len(content_root):]
        else:
            rel = name
        if not rel or rel.endswith("/"):
            continue  # Ordner-Einträge werden implizit angelegt

        target = (dest / rel).resolve()
        # Zip-Slip-Schutz: Ziel muss innerhalb des Projekts liegen.
        if target != PROJECT_ROOT and PROJECT_ROOT not in target.parents:
            skipped.append(rel)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:  # wb = überschreiben
            shutil.copyfileobj(src, out)
        try:
            extracted.append(str(target.relative_to(PROJECT_ROOT)))
        except ValueError:
            extracted.append(str(target))

    db.add_audit_entry(user["username"], "source.zip_extracted",
                       target=("(root)" if dest == PROJECT_ROOT else str(dest.relative_to(PROJECT_ROOT))),
                       details=f"{len(extracted)} Dateien aus {file.filename}")
    return {
        "ok": True,
        "dest": "" if dest == PROJECT_ROOT else str(dest.relative_to(PROJECT_ROOT)),
        "extracted": extracted,
        "count": len(extracted),
        "skipped": skipped,
        "project_update": dest == PROJECT_ROOT,
    }


class SqlBody(BaseModel):
    sql: str


@router.post("/db/query")
async def db_query(body: SqlBody, user: dict = Depends(get_current_user)):
    """
    Führt beliebiges SQL aus. Bei SELECT werden Spalten + Zeilen zurückgegeben,
    sonst die betroffene Zeilenanzahl. (Nur Super-Admin.)
    """
    require_perm(user, "edit_source")
    sql = (body.sql or "").strip()
    if not sql:
        raise HTTPException(400, "Leeres SQL")
    try:
        cur = db._conn.execute(sql)
        raw = cur.fetchall()
        if _has_result_set(cur, raw):  # SELECT / PRAGMA mit Ergebnismenge
            cols = _cursor_columns(cur, raw)
            rows = [list(r) for r in raw]
            db._conn.commit()
            return {"kind": "rows", "columns": cols, "rows": rows, "count": len(rows)}
        else:
            db._conn.commit()
            db.add_audit_entry(user["username"], "source.sql_exec",
                               details=sql[:200])
            return {"kind": "exec", "rowcount": cur.rowcount}
    except Exception as e:
        raise HTTPException(400, f"SQL-Fehler: {e}")


# ------------------------------------------------------------------
# Datenbank: Editieren / Löschen / Anlegen / Backup (nur Super-Admin)
# ------------------------------------------------------------------

def _valid_table(name: str) -> str:
    valid = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if name not in valid:
        raise HTTPException(404, "Tabelle nicht gefunden")
    return name


def _valid_column(table: str, col: str) -> str:
    cols = {r["name"] for r in db._conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    if col not in cols:
        raise HTTPException(404, "Spalte nicht gefunden")
    return col


class CellBody(BaseModel):
    table: str
    rowid: int
    column: str
    value: str | None = None   # None = Zelle leeren (NULL)


@router.put("/db/cell")
async def db_update_cell(body: CellBody, user: dict = Depends(get_current_user)):
    """Setzt den Wert einer einzelnen Zelle (value=None -> NULL)."""
    require_perm(user, "edit_source")
    t = _valid_table(body.table)
    c = _valid_column(t, body.column)
    cur = db._conn.execute(f'UPDATE "{t}" SET "{c}" = ? WHERE rowid = ?',
                           (body.value, body.rowid))
    db._conn.commit()
    db.add_audit_entry(user["username"], "source.db_cell_updated",
                       target=f"{t}.{c}", details=f"rowid={body.rowid}")
    return {"ok": True, "rowcount": cur.rowcount}


class RowBody(BaseModel):
    table: str
    rowid: int


@router.post("/db/delete-row")
async def db_delete_row(body: RowBody, user: dict = Depends(get_current_user)):
    """Löscht eine Zeile anhand ihrer rowid."""
    require_perm(user, "delete_source")
    t = _valid_table(body.table)
    cur = db._conn.execute(f'DELETE FROM "{t}" WHERE rowid = ?', (body.rowid,))
    db._conn.commit()
    db.add_audit_entry(user["username"], "source.db_row_deleted",
                       target=t, details=f"rowid={body.rowid}")
    return {"ok": True, "rowcount": cur.rowcount}


class InsertRowBody(BaseModel):
    table: str
    values: dict = {}   # {spalte: wert} - fehlende Spalten = Default/NULL


@router.post("/db/insert-row")
async def db_insert_row(body: InsertRowBody, user: dict = Depends(get_current_user)):
    """Fügt eine neue Zeile ein (nur angegebene Spalten, Rest = Default)."""
    require_perm(user, "edit_source")
    t = _valid_table(body.table)
    if body.values:
        cols = [_valid_column(t, c) for c in body.values.keys()]
        placeholders = ", ".join("?" for _ in cols)
        collist = ", ".join(f'"{c}"' for c in cols)
        db._conn.execute(f'INSERT INTO "{t}" ({collist}) VALUES ({placeholders})',
                         tuple(body.values[c] for c in cols))
    else:
        db._conn.execute(f'INSERT INTO "{t}" DEFAULT VALUES')
    db._conn.commit()
    db.add_audit_entry(user["username"], "source.db_row_inserted", target=t)
    return {"ok": True}


class TableBody(BaseModel):
    table: str


@router.post("/db/drop-table")
async def db_drop_table(body: TableBody, user: dict = Depends(get_current_user)):
    """Löscht eine komplette Tabelle. (Die doppelte Nachfrage macht das Frontend.)"""
    require_perm(user, "delete_source")
    t = _valid_table(body.table)
    db._conn.execute(f'DROP TABLE "{t}"')
    db._conn.commit()
    db.add_audit_entry(user["username"], "source.db_table_dropped", target=t)
    return {"ok": True}


class CreateTableBody(BaseModel):
    name: str
    columns: str   # z.B. "id INTEGER PRIMARY KEY, name TEXT"


@router.post("/db/create-table")
async def db_create_table(body: CreateTableBody, user: dict = Depends(get_current_user)):
    """Legt eine neue Tabelle mit der angegebenen Spalten-Definition an."""
    require_perm(user, "edit_source")
    name = (body.name or "").strip()
    if not name or not name.replace("_", "").isalnum():
        raise HTTPException(400, "Ungültiger Tabellenname (nur Buchstaben/Zahlen/_)")
    cols = (body.columns or "").strip()
    if not cols:
        raise HTTPException(400, "Spalten-Definition fehlt")
    try:
        db._conn.execute(f'CREATE TABLE "{name}" ({cols})')
        db._conn.commit()
    except Exception as e:
        raise HTTPException(400, f"SQL-Fehler: {e}")
    db.add_audit_entry(user["username"], "source.db_table_created", target=name)
    return {"ok": True}


@router.post("/db/backup")
async def db_backup(user: dict = Depends(get_current_user)):
    """Erstellt eine konsistente Kopie der Datenbank als data.sqlite.bak."""
    require_perm(user, "see_source")
    import sqlite3
    src_path = PROJECT_ROOT / "backend" / "data.sqlite"
    bak_path = PROJECT_ROOT / "backend" / "data.sqlite.bak"
    try:
        dest = sqlite3.connect(str(bak_path))
        with dest:
            db._conn.backup(dest)   # konsistentes Online-Backup
        dest.close()
    except Exception as e:
        raise HTTPException(500, f"Backup fehlgeschlagen: {e}")
    size = bak_path.stat().st_size
    db.add_audit_entry(user["username"], "source.db_backup",
                       target=str(bak_path.name), details=f"{size} bytes")
    return {"ok": True, "path": "backend/data.sqlite.bak", "size": size}

# ------------------------------------------------------------------
# Installationsart (Docker-Container oder natives Programm)
# ------------------------------------------------------------------

@router.get("/runtime")
async def get_runtime(user: dict = Depends(get_current_user)):
    """
    Sagt der Oberfläche, WIE dieses Backend installiert ist:

      install_kind = "docker"  -> läuft als Container. Port/Host hängen am
                                  Container und sind zur Laufzeit gesperrt.
      install_kind = "native"  -> normal per "python run.py" installiert,
                                  alles frei über backend/.env änderbar.

    Wird im Source-Tab als Hinweisleiste angezeigt.
    """
    require_perm(user, "see_source")
    from app import runtime_env
    return runtime_env.runtime_info()

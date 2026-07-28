"""
dbsync.py
---------
Externe SQL-Datenbank als persistenter Speicher (Settings -> Datenbank).

Architektur ("Working-Copy"-Modell):
  - Die Anwendung arbeitet IMMER mit der lokalen SQLite (schnell, kein
    Dialekt-Umbau der gesamten Codebasis nötig).
  - Modus "external": Die externe SQL-DB (MySQL/MariaDB, PostgreSQL oder eine
    SQLite-Datei z.B. auf einer Netzwerkfreigabe) ist der persistente Speicher.
      * Beim UMSCHALTEN lokal -> extern wird ALLES kopiert.
      * Im Betrieb spiegelt ein Hintergrund-Sync Änderungen (alle 60 s,
        nur wenn sich etwas geändert hat) und beim Herunterfahren.
      * Beim START im externen Modus wird der Stand extern -> lokal geladen.
      * Beim UMSCHALTEN extern -> lokal wird final synchronisiert und der
        externe Stand nach lokal kopiert.

Die Konfiguration liegt BEWUSST außerhalb der Datenbank in
backend/dbconfig.json (Henne-Ei-Problem):
  { "mode": "local" | "external",
    "type": "mysql" | "postgres" | "sqlite",
    "host": "...", "port": 3306, "user": "...", "password": "...",
    "database": "...",          # bei type=sqlite: Dateipfad
  }

Beim Dump wird zusätzlich eine Meta-Tabelle __rmm_meta geschrieben, die die
originalen SQLite-CREATE-Statements aller Tabellen enthält - damit kann die
lokale DB beim Restore exakt (inkl. Indizes/Defaults) wiederhergestellt werden.
"""

import json
import sqlite3
import time
from pathlib import Path

from app import db

_BACKEND_DIR = Path(__file__).resolve().parents[1]          # …/backend
_CONFIG_FILE = _BACKEND_DIR / "dbconfig.json"

_META_TABLE = "__rmm_meta"

# Für den Änderungs-Check des periodischen Syncs
_last_total_changes: int | None = None


# ------------------------------------------------------------------
# Konfiguration
# ------------------------------------------------------------------

def load_config() -> dict:
    try:
        cfg = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("kein Objekt")
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("mode", "local")
    cfg.setdefault("type", "mysql")
    cfg.setdefault("host", "127.0.0.1")
    cfg.setdefault("port", 3306)
    cfg.setdefault("user", "")
    cfg.setdefault("password", "")
    cfg.setdefault("database", "")
    return cfg


def save_config(cfg: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")


def public_config() -> dict:
    """Konfiguration ohne Passwort (für die API/UI)."""
    cfg = dict(load_config())
    cfg["password"] = "•••" if cfg.get("password") else ""
    return cfg


# ------------------------------------------------------------------
# Externe Verbindung (kleine Dialekt-Schicht)
# ------------------------------------------------------------------


def _ensure_driver(module: str, pypi: str):
    """
    Treiber importieren - und wenn er fehlt, selbst nachinstallieren.

    Beide Treiber stehen fest in requirements.txt, bestehende Installationen
    haben sie aber oft noch nicht (das Image wurde vor der Aenderung gebaut).
    Ein einzelner pip-Aufruf reicht dafuer nicht immer: Je nach Umgebung
    scheitert er an einer "externally managed environment" (PEP 668) oder an
    fehlenden Schreibrechten im System-Verzeichnis. Deshalb werden mehrere
    Wege der Reihe nach probiert - und im Fehlerfall steht die ECHTE
    pip-Ausgabe in der Meldung, statt nur "Treiber fehlt".
    """
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError:
        pass

    import subprocess
    import sys

    attempts = [
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", pypi],
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "--break-system-packages", pypi],
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "--user", pypi],
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "--user", "--break-system-packages", pypi],
    ]
    errors = []
    for cmd in attempts:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if res.returncode == 0:
                importlib.invalidate_caches()
                # Ein frisch nach --user installiertes Paket liegt evtl. in
                # einem Pfad, der noch nicht in sys.path steht.
                try:
                    import site
                    for p in (site.getusersitepackages(),):
                        if p and p not in sys.path:
                            sys.path.insert(0, p)
                except Exception:
                    pass
                try:
                    return importlib.import_module(module)
                except ImportError as e:
                    errors.append(f"installiert, aber nicht importierbar: {e}")
                    continue
            tail = (res.stderr or res.stdout or "").strip().splitlines()
            errors.append(tail[-1] if tail else f"Exit-Code {res.returncode}")
        except Exception as e:
            errors.append(str(e))

    raise RuntimeError(
        f"Der Datenbank-Treiber '{pypi}' fehlt und liess sich nicht automatisch "
        f"nachinstallieren. Letzter Fehler: {errors[-1] if errors else 'unbekannt'}. "
        f"Abhilfe: Container-Image neu bauen (docker compose build --no-cache) "
        f"oder im Container ausfuehren: pip install {pypi}") from None


class ExternalDB:
    """
    Minimale Abstraktion über MySQL/PostgreSQL/SQLite für genau die
    Operationen, die der Sync braucht: Tabellen anlegen/löschen,
    Massen-Insert, alles lesen.
    """

    def __init__(self, cfg: dict):
        self.type = (cfg.get("type") or "mysql").lower()
        self.cfg = cfg
        if self.type == "mysql":
            pymysql = _ensure_driver("pymysql", "PyMySQL")
            self.conn = pymysql.connect(
                host=cfg.get("host") or "127.0.0.1",
                port=int(cfg.get("port") or 3306),
                user=cfg.get("user") or "",
                password=cfg.get("password") or "",
                database=cfg.get("database") or "",
                charset="utf8mb4", autocommit=False,
            )
            self.ph = "%s"
        elif self.type == "postgres":
            psycopg2 = _ensure_driver("psycopg2", "psycopg2-binary")
            self.conn = psycopg2.connect(
                host=cfg.get("host") or "127.0.0.1",
                port=int(cfg.get("port") or 5432),
                user=cfg.get("user") or "",
                password=cfg.get("password") or "",
                dbname=cfg.get("database") or "",
            )
            self.ph = "%s"
        elif self.type == "sqlite":
            path = cfg.get("database") or ""
            if not path:
                raise RuntimeError("Bei type=sqlite muss 'database' der Dateipfad sein")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path)
            self.ph = "?"
        else:
            raise RuntimeError(f"Unbekannter Datenbank-Typ: {self.type}")

    # --- Bezeichner/Typen je Dialekt ---

    def q(self, ident: str) -> str:
        return f"`{ident}`" if self.type == "mysql" else f'"{ident}"'

    def coltype(self, sqlite_decl: str) -> str:
        d = (sqlite_decl or "").upper()
        if "INT" in d:
            return "BIGINT"
        if "REAL" in d or "FLOA" in d or "DOUB" in d:
            return "DOUBLE" if self.type == "mysql" else ("DOUBLE PRECISION" if self.type == "postgres" else "REAL")
        if "BLOB" in d:
            return "LONGBLOB" if self.type == "mysql" else ("BYTEA" if self.type == "postgres" else "BLOB")
        return "LONGTEXT" if self.type == "mysql" else "TEXT"

    # --- Grundoperationen ---

    def execute(self, sql: str, params: tuple = ()):  # noqa: ANN001
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, rows: list):
        if not rows:
            return
        cur = self.conn.cursor()
        cur.executemany(sql, rows)

    def commit(self):
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def recreate_table(self, name: str, columns: list[tuple[str, str]]) -> None:
        """columns: Liste (name, sqlite_typdeklaration)."""
        self.execute(f"DROP TABLE IF EXISTS {self.q(name)}")
        cols = ", ".join(f"{self.q(c)} {self.coltype(t)}" for c, t in columns)
        self.execute(f"CREATE TABLE {self.q(name)} ({cols})")

    def insert_rows(self, name: str, colnames: list[str], rows: list) -> None:
        cols = ", ".join(self.q(c) for c in colnames)
        ph = ", ".join([self.ph] * len(colnames))
        self.executemany(f"INSERT INTO {self.q(name)} ({cols}) VALUES ({ph})", rows)

    def read_all(self, name: str, colnames: list[str]) -> list:
        cols = ", ".join(self.q(c) for c in colnames)
        cur = self.execute(f"SELECT {cols} FROM {self.q(name)}")
        return cur.fetchall()

    def table_exists(self, name: str) -> bool:
        try:
            self.execute(f"SELECT 1 FROM {self.q(name)} LIMIT 1" if self.type != "postgres"
                         else f'SELECT 1 FROM {self.q(name)} LIMIT 1')
            return True
        except Exception:
            try:
                self.conn.rollback()   # PostgreSQL: fehlgeschlagene Query beendet die TX
            except Exception:
                pass
            return False


def test_connection(cfg: dict) -> dict:
    """Verbindung testen; wirft bei Problemen einen verständlichen Fehler."""
    ext = ExternalDB(cfg)
    try:
        ext.execute("SELECT 1")
        return {"ok": True}
    finally:
        ext.close()


# ------------------------------------------------------------------
# Dump / Restore
# ------------------------------------------------------------------

def _local_tables() -> list[dict]:
    """Alle lokalen Tabellen mit CREATE-SQL und Spalten (Name + Typ)."""
    tables = []
    rows = db._conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for r in rows:
        cols = db._conn.execute(f'PRAGMA table_info("{r["name"]}")').fetchall()
        tables.append({
            "name": r["name"],
            "sql": r["sql"],
            "columns": [(c["name"], c["type"] or "TEXT") for c in cols],
        })
    return tables


def dump_local_to_external(cfg: dict | None = None) -> dict:
    """Kopiert die KOMPLETTE lokale Datenbank in die externe (ersetzt dort alles)."""
    cfg = cfg or load_config()
    ext = ExternalDB(cfg)
    try:
        tables = _local_tables()
        counts = {}
        for t in tables:
            colnames = [c[0] for c in t["columns"]]
            data = db._conn.execute(
                f'SELECT {", ".join(chr(34)+c+chr(34) for c in colnames)} FROM "{t["name"]}"'
            ).fetchall()
            rows = [tuple(r[c] for c in colnames) for r in data]
            ext.recreate_table(t["name"], t["columns"])
            ext.insert_rows(t["name"], colnames, rows)
            counts[t["name"]] = len(rows)
        # Meta: originale SQLite-Schemas + Spaltenlisten + Zeitstempel
        ext.recreate_table(_META_TABLE, [("k", "TEXT"), ("v", "TEXT")])
        meta = {
            "schema": {t["name"]: t["sql"] for t in tables},
            "columns": {t["name"]: [c[0] for c in t["columns"]] for t in tables},
            "synced_at": int(time.time() * 1000),
        }
        ext.insert_rows(_META_TABLE, ["k", "v"], [("state", json.dumps(meta))])
        ext.commit()
        return {"ok": True, "tables": counts, "synced_at": meta["synced_at"]}
    finally:
        ext.close()


def _read_meta(ext: ExternalDB) -> dict | None:
    if not ext.table_exists(_META_TABLE):
        return None
    rows = ext.read_all(_META_TABLE, ["k", "v"])
    for k, v in rows:
        if k == "state":
            return json.loads(v)
    return None


def restore_external_to_local(cfg: dict | None = None) -> dict:
    """
    Ersetzt die lokale Datenbank durch den Stand aus der externen DB.
    Die Tabellen werden mit den ORIGINALEN SQLite-CREATE-Statements neu
    angelegt (aus __rmm_meta), danach werden alle Zeilen eingespielt.
    """
    cfg = cfg or load_config()
    ext = ExternalDB(cfg)
    try:
        meta = _read_meta(ext)
        if not meta:
            raise RuntimeError("Externe Datenbank enthält keinen RMM-Stand (__rmm_meta fehlt)")
        counts = {}
        cur = db._conn
        cur.execute("PRAGMA foreign_keys = OFF")
        for name, create_sql in meta["schema"].items():
            colnames = meta["columns"][name]
            rows = ext.read_all(name, colnames) if ext.table_exists(name) else []
            cur.execute(f'DROP TABLE IF EXISTS "{name}"')
            cur.execute(create_sql)
            if rows:
                ph = ", ".join(["?"] * len(colnames))
                cols = ", ".join(f'"{c}"' for c in colnames)
                cur.executemany(
                    f'INSERT INTO "{name}" ({cols}) VALUES ({ph})',
                    [tuple(r) for r in rows],
                )
            counts[name] = len(rows)
        cur.commit()
        return {"ok": True, "tables": counts, "synced_at": meta.get("synced_at")}
    finally:
        ext.close()


# ------------------------------------------------------------------
# Laufender Betrieb (Startup-Restore, periodischer Sync)
# ------------------------------------------------------------------

def startup_restore_if_external() -> None:
    """
    Wird beim Backend-Start VOR init_db() aufgerufen: Ist der externe Modus
    aktiv, wird der Stand aus der externen DB in die lokale Working-Copy
    geladen. Schlägt das fehl, läuft das Backend mit dem lokalen Stand weiter
    (besser als gar nicht zu starten) und meldet den Fehler im Log.
    """
    cfg = load_config()
    if cfg.get("mode") != "external":
        return
    try:
        res = restore_external_to_local(cfg)
        total = sum(res["tables"].values())
        print(f"[dbsync] Externer Modus: {total} Zeilen aus {cfg.get('type')} geladen")
    except Exception as e:
        print(f"[dbsync] WARNUNG: Externe DB nicht ladbar ({e}) - arbeite mit lokalem Stand weiter")
    global _last_total_changes
    _last_total_changes = db._conn.total_changes


def periodic_sync() -> None:
    """
    Spiegelt die lokale Working-Copy in die externe DB, wenn sich seit dem
    letzten Sync etwas geändert hat. Wird von main.py periodisch und beim
    Herunterfahren aufgerufen. (Synchron; von main im Executor ausgeführt.)
    """
    global _last_total_changes
    cfg = load_config()
    if cfg.get("mode") != "external":
        return
    changes = db._conn.total_changes
    if _last_total_changes is not None and changes == _last_total_changes:
        return   # nichts geändert -> nichts zu tun
    try:
        dump_local_to_external(cfg)
        _last_total_changes = changes
    except Exception as e:
        print(f"[dbsync] Sync in externe DB fehlgeschlagen: {e}")

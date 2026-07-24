"""
privacy.py
----------
Zentrale Stelle für alles, was die DSGVO technisch verlangt. Bewusst als
EIN Modul, damit es genau eine Wahrheit darüber gibt, wo im System
personenbezogene Daten liegen - verstreute Einzellösungen veralten.

Enthält:
  * PERSONAL_DATA_MAP  - Verzeichnis aller Tabellen mit Personenbezug.
                         Grundlage für Auskunft, Löschung UND für den
                         Bericht, der beim Verzeichnis der Verarbeitungs-
                         tätigkeiten (Art. 30) hilft.
  * RETENTION          - welche Aufbewahrungsfrist auf welche Daten wirkt
                         (Art. 5 Abs. 1 lit. e - Speicherbegrenzung).
  * export_user_data() - Auskunft & Datenübertragbarkeit (Art. 15 / 20)
  * erase_user()       - Löschung (Art. 17), wahlweise anonymisieren
  * purge()            - Fristen durchsetzen, läuft täglich als Job
  * report()           - Bestandsübersicht für Admins

WICHTIG zur Abgrenzung: Dieses Modul erledigt die TECHNISCHE Seite.
Rechtsgrundlage, Verzeichnis, DSFA, Betriebsvereinbarung und AV-Verträge
sind organisatorisch und liegen außerhalb der Software.
"""

import json
import pathlib
import time
import uuid

from app import db

# ------------------------------------------------------------------
# Verzeichnis der personenbezogenen Daten
# ------------------------------------------------------------------
# strategy:
#   'delete'    -> Datensatz wird bei Löschung entfernt
#   'anonymize' -> Datensatz bleibt (fachlich nötig / Nachweispflicht),
#                  der Personenbezug wird aber gekappt
#
# Handelsrechtliche oder sicherheitsbezogene Nachweispflichten können
# gegen die sofortige Löschung sprechen (Art. 17 Abs. 3 lit. b/e). Deshalb
# wird das Audit-Log anonymisiert statt gelöscht: die Aktion bleibt
# nachvollziehbar, die Person ist es nicht mehr.

PERSONAL_DATA_MAP = [
    # (Tabelle, Spalte mit der Benutzer-ID, Bezeichnung, Strategie, Namensspalte)
    {"table": "users", "col": "id", "label": "Benutzerkonto",
     "strategy": "delete", "name_col": None},
    {"table": "user_groups", "col": "user_id", "label": "Gruppenzugehörigkeit",
     "strategy": "delete", "name_col": None},
    {"table": "todos", "col": "user_id", "label": "Todos",
     "strategy": "delete", "name_col": None},
    {"table": "todo_categories", "col": "user_id", "label": "Todo-Kategorien",
     "strategy": "delete", "name_col": None},
    {"table": "chat_members", "col": "user_id", "label": "Chat-Teilnahmen",
     "strategy": "delete", "name_col": None},
    {"table": "chat_messages", "col": "sender_id", "label": "Chat-Nachrichten",
     "strategy": "delete", "name_col": None},
    {"table": "chat_conversations", "col": "created_by", "label": "Angelegte Chats",
     "strategy": "anonymize", "name_col": None},
    {"table": "calendar_events", "col": "created_by", "label": "Angelegte Termine",
     "strategy": "anonymize", "name_col": "created_by_name"},
    {"table": "calendar_targets", "col": "target_id", "label": "Termin-Zuordnungen",
     "strategy": "delete", "name_col": None, "where": "target_type = 'user'"},
    {"table": "client_notes", "col": "author_id", "label": "Client-Notizen",
     "strategy": "anonymize", "name_col": "author_name"},
    {"table": "note_shares", "col": "user_id", "label": "Notiz-Freigaben",
     "strategy": "delete", "name_col": None},
    {"table": "tickets", "col": "created_by", "label": "Angelegte Tickets",
     "strategy": "anonymize", "name_col": None},
    {"table": "ticket_comments", "col": "author_id", "label": "Ticket-Kommentare",
     "strategy": "anonymize", "name_col": "author"},
    {"table": "ticket_comment_shares", "col": "user_id", "label": "Kommentar-Freigaben",
     "strategy": "delete", "name_col": None},
    {"table": "ticket_assignees", "col": "subject_id", "label": "Ticket-Zuweisungen",
     "strategy": "delete", "name_col": None, "where": "subject_type = 'user'"},
    {"table": "media_items", "col": "owner_id", "label": "Medien-Bibliothek",
     "strategy": "delete", "name_col": "owner_name"},
    {"table": "ai_connections", "col": "owner_user_id", "label": "AI-Verbindungen",
     "strategy": "delete", "name_col": None},
    {"table": "ai_connection_shares", "col": "subject_id", "label": "AI-Freigaben",
     "strategy": "delete", "name_col": None, "where": "subject_type = 'user'"},
    {"table": "permission_grants", "col": "subject_id", "label": "Rechte-Zuweisungen",
     "strategy": "delete", "name_col": None, "where": "subject_type = 'user'"},
    {"table": "org_hierarchy", "col": "child_id", "label": "Organigramm-Einordnung",
     "strategy": "delete", "name_col": None, "where": "child_type = 'user'"},
    {"table": "activity_log", "col": "actor_id", "label": "Aktivitätsprotokoll",
     "strategy": "anonymize", "name_col": "actor_name"},
    {"table": "erasure_requests", "col": "user_id", "label": "Löschanträge",
     "strategy": "delete", "name_col": "username"},
]

# Tabellen, die die Person NICHT über die ID, sondern über den Benutzernamen
# führen. Die werden gesondert behandelt (siehe _by_username).
USERNAME_TABLES = [
    {"table": "audit_log", "col": "username", "label": "Audit-Log",
     "strategy": "anonymize"},
    {"table": "screen_recordings", "col": "username", "label": "Bildschirmaufzeichnungen",
     "strategy": "delete"},
]

ANON_ID = "geloescht"
ANON_NAME = "Gelöschter Benutzer"


# ------------------------------------------------------------------
# Aufbewahrungsfristen
# ------------------------------------------------------------------
# key = Einstellungs-Schlüssel, unit = 'days' | 'hours', 0 = unbegrenzt.

RETENTION = [
    {"key": "replay_retention_days", "unit": "days", "default": 30,
     "label": "Bildschirm- & Session-Aufzeichnungen",
     "note": "Der heikelste Datenbestand: zeigt konkretes Verhalten am Arbeitsplatz. "
             "Kurze Frist wählen."},
    {"key": "retention_audit_days", "unit": "days", "default": 90,
     "label": "Audit-Log",
     "note": "Wird nach Ablauf anonymisiert, nicht gelöscht - die Aktion bleibt "
             "als Sicherheitsnachweis erhalten, die Person nicht."},
    {"key": "retention_activity_days", "unit": "days", "default": 180,
     "label": "Aktivitätsprotokoll (Notizen, Tickets)"},
    {"key": "retention_chat_days", "unit": "days", "default": 365,
     "label": "Chat-Nachrichten"},
    {"key": "metrics_retention_hours", "unit": "hours", "default": 0,
     "label": "Metrik-Historie der Clients",
     "note": "Kein direkter Personenbezug, kann aber Arbeitszeiten sichtbar machen."},
    {"key": "retention_todo_archive_days", "unit": "days", "default": 365,
     "label": "Archivierte Todos"},
    {"key": "retention_enrollment_days", "unit": "days", "default": 30,
     "label": "Verbrauchte Enrollment-Token"},
]


def _conn():
    return db._conn


def _now() -> int:
    return int(time.time() * 1000)


def _table_exists(name: str) -> bool:
    return _conn().execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,)).fetchone() is not None


def _cols(table: str) -> list[str]:
    return [r["name"] for r in _conn().execute(f"PRAGMA table_info({table})").fetchall()]


def retention_days(key: str, default: int) -> int:
    try:
        return int(db.get_setting(key, str(default)) or default)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------
# Auskunft / Datenübertragbarkeit (Art. 15, Art. 20)
# ------------------------------------------------------------------

def export_user_data(user_id: str) -> dict:
    """
    Alle zu einer Person gespeicherten Daten als strukturiertes Dict.
    Bewusst maschinenlesbar (JSON), das verlangt Art. 20 Abs. 1.

    Passwort-Hashes werden NICHT ausgeliefert - sie sind kein Auskunfts-
    gegenstand und ihre Herausgabe wäre ein Sicherheitsrisiko.
    """
    user = db.get_user_by_id(user_id)
    if not user:
        return {}
    username = user.get("username") or ""

    out: dict = {
        "auskunft_erstellt_am": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "hinweis": (
            "Auskunft nach Art. 15 DSGVO / Datenübertragbarkeit nach Art. 20 DSGVO. "
            "Enthält alle im RMM-System zu dieser Person gespeicherten Daten."
        ),
        "person": {k: v for k, v in user.items() if k != "password_hash"},
        "daten": {},
    }

    for entry in PERSONAL_DATA_MAP:
        table = entry["table"]
        if table == "users" or not _table_exists(table):
            continue
        where = f"{entry['col']} = ?"
        if entry.get("where"):
            where += f" AND {entry['where']}"
        try:
            rows = _conn().execute(
                f"SELECT * FROM {table} WHERE {where}", (user_id,)).fetchall()
        except Exception:
            continue
        if rows:
            out["daten"][entry["label"]] = [dict(r) for r in rows]

    for entry in USERNAME_TABLES:
        table = entry["table"]
        if not _table_exists(table):
            continue
        try:
            rows = _conn().execute(
                f"SELECT * FROM {table} WHERE {entry['col']} = ?", (username,)).fetchall()
        except Exception:
            continue
        if rows:
            out["daten"][entry["label"]] = [dict(r) for r in rows]

    return out


# ------------------------------------------------------------------
# Löschung (Art. 17)
# ------------------------------------------------------------------

def erase_user(user_id: str, mode: str = "anonymize") -> dict:
    """
    Personenbezug einer Person entfernen.

    mode='anonymize' (empfohlen): Datensätze mit Nachweisfunktion bleiben
        bestehen, der Bezug zur Person wird gekappt. Fachlich saubere
        Umsetzung von Art. 17 unter Beachtung von Abs. 3.
    mode='hard': alles wird gelöscht, auch das Audit-Log dieser Person.
        Nur nutzen, wenn keine Nachweispflicht entgegensteht.

    Gibt einen Bericht zurück (Tabelle -> Anzahl betroffener Zeilen),
    der beim Nachweis der Löschung hilft.
    """
    user = db.get_user_by_id(user_id)
    if not user:
        return {"error": "Benutzer nicht gefunden"}
    username = user.get("username") or ""
    report: dict[str, int] = {}
    c = _conn()

    for entry in PERSONAL_DATA_MAP:
        table, col = entry["table"], entry["col"]
        if table == "users" or not _table_exists(table):
            continue
        where = f"{col} = ?"
        if entry.get("where"):
            where += f" AND {entry['where']}"
        strategy = entry["strategy"]
        if mode == "hard":
            strategy = "delete"
        try:
            if strategy == "delete":
                cur = c.execute(f"DELETE FROM {table} WHERE {where}", (user_id,))
            else:
                sets = [f"{col} = ?"]
                params: list = [ANON_ID]
                if entry.get("name_col") and entry["name_col"] in _cols(table):
                    sets.append(f"{entry['name_col']} = ?")
                    params.append(ANON_NAME)
                params.append(user_id)
                cur = c.execute(
                    f"UPDATE {table} SET {', '.join(sets)} WHERE {where}", params)
            if cur.rowcount:
                report[entry["label"]] = cur.rowcount
        except Exception as e:
            report[f"{entry['label']} (Fehler)"] = str(e)

    # Über den Benutzernamen geführte Tabellen
    for entry in USERNAME_TABLES:
        table, col = entry["table"], entry["col"]
        if not _table_exists(table):
            continue
        strategy = "delete" if mode == "hard" else entry["strategy"]
        try:
            if entry["table"] == "screen_recordings":
                # Erst die Dateien von der Platte, dann die Datenbank-Zeilen.
                rows = c.execute(
                    "SELECT file_path FROM screen_recordings WHERE username = ?",
                    (username,)).fetchall()
                for r in rows:
                    _delete_recording_file(r["file_path"])
            if strategy == "delete":
                cur = c.execute(f"DELETE FROM {table} WHERE {col} = ?", (username,))
            else:
                cur = c.execute(
                    f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (ANON_NAME, username))
            if cur.rowcount:
                report[entry["label"]] = cur.rowcount
        except Exception as e:
            report[f"{entry['label']} (Fehler)"] = str(e)

    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    c.commit()
    report["Benutzerkonto"] = 1

    # Die Löschung selbst wird protokolliert - ohne den Namen der gelöschten
    # Person, sonst stünde sie über den Umweg des Protokolls wieder drin.
    db.add_audit_entry("system", "privacy.user_erased",
                       details=f"Modus: {mode}, betroffene Bereiche: {len(report)}")
    return report


# ------------------------------------------------------------------
# Fristen durchsetzen (Art. 5 Abs. 1 lit. e)
# ------------------------------------------------------------------

def purge() -> dict:
    """
    Alle konfigurierten Aufbewahrungsfristen anwenden. Läuft täglich als
    Hintergrund-Job und kann von Admins manuell ausgelöst werden.
    Gibt zurück, was tatsächlich entfernt wurde.
    """
    c = _conn()
    now = _now()
    result: dict[str, int] = {}

    def cutoff(key: str, default: int, unit: str = "days") -> int | None:
        v = retention_days(key, default)
        if v <= 0:
            return None                       # 0 = unbegrenzt aufbewahren
        factor = 3600_000 if unit == "hours" else 86400_000
        return now - v * factor

    # --- Aufzeichnungen (Datei + DB-Zeile) ---
    cut = cutoff("replay_retention_days", 30)
    if cut is not None and _table_exists("screen_recordings"):
        rows = c.execute(
            "SELECT id, file_path FROM screen_recordings WHERE started_at < ?",
            (cut,)).fetchall()
        for r in rows:
            _delete_recording_file(r["file_path"])
        c.execute("DELETE FROM screen_recordings WHERE started_at < ?", (cut,))
        result["Aufzeichnungen"] = len(rows)

    # --- Audit-Log: anonymisieren statt löschen ---
    cut = cutoff("retention_audit_days", 90)
    if cut is not None and _table_exists("audit_log"):
        cur = c.execute(
            "UPDATE audit_log SET username = ? WHERE ts < ? AND username != ?",
            (ANON_NAME, cut, ANON_NAME))
        result["Audit-Log anonymisiert"] = cur.rowcount

    # --- Aktivitätsprotokoll ---
    cut = cutoff("retention_activity_days", 180)
    if cut is not None and _table_exists("activity_log"):
        cur = c.execute("DELETE FROM activity_log WHERE created_at < ?", (cut,))
        result["Aktivitätsprotokoll"] = cur.rowcount

    # --- Chat-Nachrichten ---
    cut = cutoff("retention_chat_days", 365)
    if cut is not None and _table_exists("chat_messages"):
        cur = c.execute("DELETE FROM chat_messages WHERE created_at < ?", (cut,))
        result["Chat-Nachrichten"] = cur.rowcount

    # --- Metrik-Historie ---
    cut = cutoff("metrics_retention_hours", 0, unit="hours")
    if cut is not None and _table_exists("metrics_history"):
        cur = c.execute("DELETE FROM metrics_history WHERE ts < ?", (cut,))
        result["Metrik-Historie"] = cur.rowcount

    # --- Archivierte Todos ---
    cut = cutoff("retention_todo_archive_days", 365)
    if cut is not None and _table_exists("todos"):
        cur = c.execute(
            "DELETE FROM todos WHERE updated_at < ? AND category_id IN"
            " (SELECT id FROM todo_categories WHERE builtin = 'archive')", (cut,))
        result["Archivierte Todos"] = cur.rowcount

    # --- Verbrauchte Enrollment-Token ---
    cut = cutoff("retention_enrollment_days", 30)
    if cut is not None and _table_exists("enrollment_tokens"):
        cur = c.execute("DELETE FROM enrollment_tokens WHERE created_at < ?", (cut,))
        result["Enrollment-Token"] = cur.rowcount

    c.commit()

    # --- Verwaiste Aufzeichnungsdateien ohne DB-Eintrag ---
    result["Verwaiste Dateien"] = _purge_orphan_files()

    total = sum(v for v in result.values() if isinstance(v, int))
    if total:
        db.add_audit_entry("system", "privacy.purge",
                           details=f"{total} Datensätze nach Ablauf der Frist entfernt")
    db.set_setting("privacy_last_purge", str(now))
    return result


def _recordings_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "recordings"


def _local_file(stored_path: str) -> pathlib.Path | None:
    """
    Zu einem in der DB gespeicherten Pfad die TATSÄCHLICHE lokale Datei finden.

    In der DB stehen absolute Pfade des Rechners, auf dem aufgezeichnet wurde
    (z.B. Windows-Pfade). Wird die Installation verschoben, umgezogen oder die
    Datenbank per dbsync woanders eingespielt, zeigen die ins Leere. Der
    Dateiname ist dagegen stabil (UUID + Zeitstempel), deshalb wird primär
    darüber aufgelöst.
    """
    if not stored_path:
        return None
    name = stored_path.replace("\\", "/").rsplit("/", 1)[-1]
    if not name:
        return None
    local = _recordings_dir() / name
    if local.is_file():
        return local
    direct = pathlib.Path(stored_path)
    return direct if direct.is_file() else None


def _delete_recording_file(stored_path: str) -> bool:
    f = _local_file(stored_path)
    if not f:
        return False
    try:
        f.unlink()
        return True
    except Exception:
        return False


def _purge_orphan_files() -> int:
    """
    Dateien im recordings-Ordner, zu denen es keinen DB-Eintrag mehr gibt.
    Ohne das bleiben nach Löschungen Videos auf der Platte liegen - und die
    sind weiterhin personenbezogene Daten.

    SICHERHEITSBREMSE: Verglichen wird nur über den Dateinamen. Passt KEINE
    einzige Datei zu einem DB-Eintrag, obwohl beide Seiten gefüllt sind, ist
    das mit hoher Wahrscheinlichkeit ein Pfad-/Mount-Problem und nicht ein
    Ordner voller Karteileichen - dann wird nichts angefasst. Lieber ein
    Rest zu viel als ein versehentlich gelöschter Bestand.
    """
    folder = _recordings_dir()
    if not folder.is_dir():
        return 0
    files = [f for f in folder.iterdir() if f.is_file()]
    if not files:
        return 0

    known = set()
    if _table_exists("screen_recordings"):
        for r in _conn().execute("SELECT file_path FROM screen_recordings").fetchall():
            p = (r["file_path"] or "").replace("\\", "/").rsplit("/", 1)[-1]
            if p:
                known.add(p)

    if known and not any(f.name in known for f in files):
        print("[privacy] Verwaisten-Aufräumen übersprungen: kein Dateiname passt zu"
              " einem Datenbank-Eintrag (vermutlich verschobener Pfad).")
        return 0

    removed = 0
    for f in files:
        if f.name in known:
            continue
        try:
            f.unlink()
            removed += 1
        except Exception:
            pass
    return removed


# ------------------------------------------------------------------
# Bestandsübersicht (hilft beim Verzeichnis nach Art. 30)
# ------------------------------------------------------------------

def report() -> dict:
    """Welche Datenbestände existieren, wie groß, wie alt ist der älteste Eintrag."""
    c = _conn()
    items = []
    ts_cols = {
        "audit_log": "ts", "activity_log": "created_at", "chat_messages": "created_at",
        "screen_recordings": "started_at", "metrics_history": "ts",
        "todos": "created_at", "enrollment_tokens": "created_at",
        "calendar_events": "created_at", "client_notes": "created_at",
    }
    for entry in PERSONAL_DATA_MAP + USERNAME_TABLES:
        table = entry["table"]
        if not _table_exists(table):
            continue
        try:
            count = c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        except Exception:
            continue
        oldest = None
        col = ts_cols.get(table)
        if col and col in _cols(table) and count:
            row = c.execute(f"SELECT MIN({col}) AS m FROM {table}").fetchone()
            oldest = row["m"]
        items.append({
            "table": table, "label": entry["label"], "count": count,
            "oldest": oldest, "strategy": entry["strategy"],
        })

    folder = _recordings_dir()
    files, size = 0, 0
    if folder.is_dir():
        for f in folder.iterdir():
            if f.is_file():
                files += 1
                try:
                    size += f.stat().st_size
                except Exception:
                    pass

    last = db.get_setting("privacy_last_purge", "0")
    return {
        "items": sorted(items, key=lambda x: -x["count"]),
        "recording_files": files,
        "recording_bytes": size,
        "last_purge": int(last or 0),
        "retention": [
            {**r, "value": retention_days(r["key"], r["default"])} for r in RETENTION
        ],
    }


# ------------------------------------------------------------------
# Löschanträge (Selbstbedienung für Betroffene)
# ------------------------------------------------------------------

def create_erasure_request(user: dict, kind: str, reason: str) -> str:
    """
    Betroffene können Löschung selbst beantragen. Der Antrag wird nicht
    sofort ausgeführt - ein Admin muss ihn prüfen, weil Löschung eines
    aktiven Kontos betriebliche Folgen hat und Art. 17 Abs. 3 Ausnahmen
    kennt. Art. 12 Abs. 3 gibt dafür einen Monat Zeit.
    """
    rid = str(uuid.uuid4())
    _conn().execute(
        "INSERT INTO erasure_requests (id, user_id, username, kind, reason,"
        " status, created_at, handled_at, handled_by)"
        " VALUES (?, ?, ?, ?, ?, 'open', ?, NULL, '')",
        (rid, user["id"], user.get("username", ""), kind, reason[:2000], _now()))
    _conn().commit()
    db.add_audit_entry(user.get("username"), "privacy.erasure_requested",
                       details=f"Art: {kind}")
    return rid


def list_erasure_requests(status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM erasure_requests"
    params: tuple = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY created_at DESC"
    return [dict(r) for r in _conn().execute(sql, params).fetchall()]

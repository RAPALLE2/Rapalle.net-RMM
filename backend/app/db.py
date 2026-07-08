"""
db.py
-----
Kompletter Datenbank-Layer für RAPALLE.net RMM.

Wir benutzen SQLite (in Python fest eingebaut, "import sqlite3" - keine
Installation nötig, kein Server nötig). Die Datenbank ist einfach eine
Datei "data.sqlite" im backend-Ordner.

Tabellen-Übersicht (siehe auch die CREATE TABLE Blöcke weiter unten):
- users:      Login-Benutzer des Dashboards (Admins/Techniker)
- tenants:    Kunden/Firmen (oberste Ebene der Hierarchie)
- locations:  Standorte eines Tenants (z.B. "Büro Regensburg")
- folders:    Frei anlegbare, verschachtelbare Ordner INNERHALB einer Location
              (z.B. "Rack 1" -> "Server" -> ...). parent_folder_id verweist
              auf einen anderen Ordner, oder ist NULL wenn es ein Ordner
              direkt unter der Location ist.
- clients:    Die eigentlichen verwalteten Geräte (PCs, Server, VMs/CTs).
              parent_client_id wird gesetzt, wenn ein Client eine VM/CT
              eines anderen (physischen) Clients ist - dadurch muss das
              Backend VMs/CTs nicht als eigenes Konzept behandeln, sie
              sind einfach "Clients mit einem Eltern-Client".

Alle Funktionen hier sind bewusst einfach gehalten (SQL direkt, kein ORM),
damit man als Python-Einsteiger genau sieht, was passiert.
"""

import sqlite3
import time
import pathlib
import uuid

# Pfad zur Datenbank-Datei: liegt im backend-Ordner, eine Ebene über "app/"
DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data.sqlite"

# Eine einzige, geteilte Verbindung für die ganze App.
# check_same_thread=False, weil FastAPI/Uvicorn mehrere Threads nutzen kann.
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)

# row_factory sorgt dafür, dass wir Zeilen wie Dictionaries ansprechen können
# (z.B. row["hostname"] statt row[3]), das ist deutlich lesbarer.
_conn.row_factory = sqlite3.Row


def _new_id() -> str:
    """Erzeugt eine neue, eindeutige ID (UUID) für neue Datensätze."""
    return str(uuid.uuid4())


def _now_ms() -> int:
    """Aktuelle Zeit in Millisekunden seit 1970 (praktisch für Zeitstempel)."""
    return int(time.time() * 1000)


def _migrate_add_column(table: str, column: str, definition: str) -> None:
    """
    Fügt eine Spalte hinzu, falls sie noch nicht existiert (einfache Migration).
    So können bestehende Datenbanken aus älteren Versionen aktualisiert werden,
    ohne dass man data.sqlite löschen muss.
    """
    existing = [row["name"] for row in _conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        _conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        _conn.commit()


def init_db() -> None:
    """
    Legt alle Tabellen an, falls sie noch nicht existieren.
    Wird beim Start des Backends einmal aufgerufen (siehe main.py).
    """
    _conn.executescript(
        """
        -- Login-Benutzer des Dashboards
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',   -- 'admin' oder 'viewer' (Vorbereitung für spätere feinere Rechte)
            must_change_pw INTEGER NOT NULL DEFAULT 0,  -- 1 = muss beim nächsten Login neues Passwort setzen
            language TEXT NOT NULL DEFAULT 'de',   -- Sprach-Präferenz: 'de' oder 'en'
            theme TEXT NOT NULL DEFAULT 'dark',    -- 'dark' oder 'light'
            created_at INTEGER NOT NULL
        );

        -- Kunden/Firmen (oberste Hierarchie-Ebene)
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#2dd4bf'
        );

        -- Standorte eines Tenants
        CREATE TABLE IF NOT EXISTS locations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name TEXT NOT NULL
        );

        -- Frei anlegbare, verschachtelbare Ordner innerhalb einer Location
        CREATE TABLE IF NOT EXISTS folders (
            id TEXT PRIMARY KEY,
            location_id TEXT NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
            parent_folder_id TEXT REFERENCES folders(id) ON DELETE CASCADE,
            name TEXT NOT NULL
        );

        -- Die eigentlichen verwalteten Geräte
        CREATE TABLE IF NOT EXISTS clients (
            id TEXT PRIMARY KEY,
            hostname TEXT NOT NULL,
            tenant_id TEXT REFERENCES tenants(id) ON DELETE SET NULL,
            location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
            folder_id TEXT REFERENCES folders(id) ON DELETE SET NULL,
            parent_client_id TEXT REFERENCES clients(id) ON DELETE CASCADE, -- gesetzt bei VM/CT
            platform TEXT,
            arch TEXT,
            release TEXT,
            ip TEXT,
            color TEXT NOT NULL DEFAULT '#38bdf8',
            notes TEXT NOT NULL DEFAULT '',
            status_override TEXT,        -- z.B. 'maintenance', manuell vom Admin gesetzt
            active INTEGER NOT NULL DEFAULT 1,  -- 0 = deaktiviert (bleibt aber sichtbar/historisch)
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL
        );

        -- Audit-Log: hält fest, wer wann was gemacht hat (Basis, wird noch erweitert)
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            ts INTEGER NOT NULL,
            username TEXT,
            action TEXT NOT NULL,     -- z.B. "login", "exec", "client.update"
            target TEXT,              -- z.B. Client-ID oder Username eines betroffenen Datensatzes
            details TEXT              -- freier Text/JSON mit weiteren Infos
        );

        -- Einmal-Tokens fürs Client-Onboarding ("Client hinzufügen" in der Sidebar).
        -- Ein Token wird beim Klick auf "Client hinzufügen" erzeugt und enthält
        -- schon Tenant/Location, damit der neue Agent gleich richtig einsortiert ist.
        CREATE TABLE IF NOT EXISTS enrollment_tokens (
            token TEXT PRIMARY KEY,
            tenant_id TEXT REFERENCES tenants(id) ON DELETE SET NULL,
            location_id TEXT REFERENCES locations(id) ON DELETE SET NULL,
            created_at INTEGER NOT NULL,
            used_at INTEGER
        );

        -- Aufzeichnungen von Remote-Screen-Sessions.
        -- Die eigentlichen Frames liegen als Datei auf der Platte (Pfad in file_path),
        -- hier stehen nur die Metadaten. Auto-Löschung nach X Tagen (siehe cleanup).
        CREATE TABLE IF NOT EXISTS screen_recordings (
            id TEXT PRIMARY KEY,
            client_id TEXT,
            client_hostname TEXT,
            username TEXT,             -- wer die Session gestartet hat
            started_at INTEGER NOT NULL,
            ended_at INTEGER,
            frame_count INTEGER NOT NULL DEFAULT 0,
            file_path TEXT NOT NULL
        );

        -- Gespeicherte Skripte (Scripts-App): benannte Befehle mit Plattform-Tag.
        CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            command TEXT NOT NULL,
            os TEXT NOT NULL DEFAULT 'any',   -- 'windows', 'linux', 'any'
            created_at INTEGER NOT NULL
        );

        -- Gruppen/Rollen mit Rechten. permissions ist eine kommagetrennte Liste
        -- von Rechte-Schlüsseln (z.B. "login,screen,terminal"). is_ad_group=1
        -- markiert Gruppen, die aus einem Verzeichnis (AD) stammen.
        CREATE TABLE IF NOT EXISTS groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            permissions TEXT NOT NULL DEFAULT '',
            is_ad_group INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );

        -- Zuordnung Benutzer <-> Gruppe (ein Benutzer kann in mehreren Gruppen sein).
        CREATE TABLE IF NOT EXISTS user_groups (
            user_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            PRIMARY KEY (user_id, group_id)
        );

        -- Feingranulare Rechte-Vergabe (tri-state: allow/deny, sonst = keine Angabe).
        -- subject_type: 'user' oder 'group'; scope: 'global' oder eine client_id.
        -- Deny gewinnt immer, 'admin' ist ein Wildcard-Recht (siehe auth.py Resolver).
        CREATE TABLE IF NOT EXISTS permission_grants (
            id TEXT PRIMARY KEY,
            subject_type TEXT NOT NULL,   -- 'user' | 'group'
            subject_id TEXT NOT NULL,
            scope TEXT NOT NULL,          -- 'global' | <client_id>
            perm TEXT NOT NULL,
            effect TEXT NOT NULL,         -- 'allow' | 'deny'
            UNIQUE(subject_type, subject_id, scope, perm)
        );
        CREATE INDEX IF NOT EXISTS idx_perm_grants_subject
            ON permission_grants (subject_type, subject_id);

        -- Verzeichnis-Anbindungen (Realms), z.B. Active Directory via LDAP/LDAPS.
        -- Voll funktionsfähig: Benutzer können sich gegen diese Realms anmelden.
        CREATE TABLE IF NOT EXISTS realms (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            server TEXT NOT NULL,
            base_dn TEXT,
            bind_user TEXT,
            bind_password TEXT,
            port INTEGER,                         -- NULL = Standard (389, bzw. 636 bei SSL)
            use_ssl INTEGER NOT NULL DEFAULT 0,   -- 1 = LDAPS
            user_filter TEXT,                     -- optionaler zusätzlicher LDAP-Filter
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        -- Webhook-Ziele für Benachrichtigungen (Discord oder custom).
        CREATE TABLE IF NOT EXISTS webhooks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'custom',   -- 'discord' | 'custom'
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        -- Automationen: Befehl/Skript, das auf Clients zu festen Intervallen läuft.
        CREATE TABLE IF NOT EXISTS automations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            command TEXT NOT NULL,
            client_ids TEXT NOT NULL DEFAULT '',   -- kommagetrennte Client-IDs
            interval_seconds INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run INTEGER,
            created_at INTEGER NOT NULL
        );

        -- Automation-Läufe: EIN Eintrag pro Client pro Durchlauf. Damit lässt
        -- sich im Dashboard eine Ergebnisliste je Durchlauf einsehen (Ausgabe,
        -- Fehler, Exit-Code, Zeit). run_id gruppiert alle Clients EINES Laufs.
        CREATE TABLE IF NOT EXISTS automation_runs (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,               -- gruppiert einen Durchlauf
            automation_id TEXT NOT NULL,
            client_id TEXT NOT NULL,
            client_hostname TEXT,
            started_at INTEGER NOT NULL,
            stdout TEXT,
            stderr TEXT,
            exit_code INTEGER,
            ok INTEGER NOT NULL DEFAULT 0       -- 1 = erfolgreich ausgeführt
        );

        -- Globale Einstellungen als einfacher Schlüssel/Wert-Speicher.
        -- Wird u.a. für die Install-Server-Adresse und die Metrik-/Replay-
        -- Aufbewahrung genutzt (siehe DEFAULT_SETTINGS weiter unten).
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- Langzeit-Historie der Client-Metriken (persistiert, damit die
        -- Graphen nach einem Seiten-Neuladen nicht bei 0 anfangen).
        CREATE TABLE IF NOT EXISTS metrics_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            ts INTEGER NOT NULL,            -- Zeitstempel in ms
            cpu REAL NOT NULL DEFAULT 0,    -- Prozent
            ram REAL NOT NULL DEFAULT 0,    -- Prozent
            net_in REAL NOT NULL DEFAULT 0, -- Bytes/s
            net_out REAL NOT NULL DEFAULT 0 -- Bytes/s
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_history_client_ts
            ON metrics_history (client_id, ts);
        """
    )
    _conn.commit()

    # Migration: Spalten für VM/LXC-Kennzeichnung nachrüsten, falls die
    # clients-Tabelle noch aus einer älteren Version stammt.
    _migrate_add_column("clients", "device_type", "TEXT DEFAULT 'physical'")  # 'physical' | 'vm' | 'lxc'
    _migrate_add_column("enrollment_tokens", "client_name", "TEXT")  # optionaler Wunschname beim Onboarding
    _migrate_add_column("users", "accent", "TEXT DEFAULT 'teal'")  # persönliche Farbpalette
    _migrate_add_column("users", "auth_realm", "TEXT")  # NULL = lokaler User, sonst Realm-ID (AD)

    # Migration: Realms um Port, SSL (LDAPS) und einen optionalen zusätzlichen
    # Benutzer-Filter erweitern (für produktive AD-Anbindungen).
    _migrate_add_column("realms", "port", "INTEGER")                 # NULL = Standard (389 / 636 bei SSL)
    _migrate_add_column("realms", "use_ssl", "INTEGER NOT NULL DEFAULT 0")  # 1 = LDAPS
    _migrate_add_column("realms", "user_filter", "TEXT")            # optionaler zusätzlicher LDAP-Filter

    # Migration Metrik-Einstellungen: Wer die ALTEN Defaults (60 s Intervall /
    # 1 h Aufbewahrung) unverändert gespeichert hat, wird auf die neuen Werte
    # (10 s / unbegrenzt) gehoben. Bewusst abweichend gesetzte Werte bleiben.
    _conn.execute("DELETE FROM settings WHERE key = 'metrics_interval_seconds' AND value = '60'")
    _conn.execute("DELETE FROM settings WHERE key = 'metrics_retention_hours' AND value = '1'")

    # Falls noch kein Benutzer existiert: Standard-Login admin/admin anlegen,
    # mit must_change_pw=1, damit beim ersten Login ein neues Passwort gesetzt werden MUSS.
    if _conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
        from app.auth import hash_password  # lokal importiert, um Zirkel-Import zu vermeiden

        _conn.execute(
            """
            INSERT INTO users (id, username, password_hash, display_name, role, must_change_pw, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_new_id(), "admin", hash_password("admin"), "Administrator", "admin", 1, _now_ms()),
        )
        _conn.commit()
        print("[db] Standard-Login angelegt: Benutzername 'admin', Passwort 'admin' (Änderung beim ersten Login erforderlich)")


# ------------------------------------------------------------------
# USERS
# ------------------------------------------------------------------

def get_user_by_username(username: str) -> dict | None:
    row = _conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    rows = _conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    return [dict(r) for r in rows]


def create_user(username: str, password_hash: str, display_name: str, role: str, must_change_pw: bool) -> dict:
    user_id = _new_id()
    _conn.execute(
        """
        INSERT INTO users (id, username, password_hash, display_name, role, must_change_pw, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, username, password_hash, display_name, role, int(must_change_pw), _now_ms()),
    )
    _conn.commit()
    return get_user_by_id(user_id)


def update_user_password(user_id: str, password_hash: str, must_change_pw: bool = False) -> None:
    _conn.execute(
        "UPDATE users SET password_hash = ?, must_change_pw = ? WHERE id = ?",
        (password_hash, int(must_change_pw), user_id),
    )
    _conn.commit()


def update_user_profile(user_id: str, display_name: str | None = None, language: str | None = None, theme: str | None = None, accent: str | None = None) -> None:
    """Aktualisiert nur die übergebenen Felder (die anderen bleiben unverändert)."""
    user = get_user_by_id(user_id)
    if not user:
        return
    _conn.execute(
        "UPDATE users SET display_name = ?, language = ?, theme = ?, accent = ? WHERE id = ?",
        (
            display_name if display_name is not None else user["display_name"],
            language if language is not None else user["language"],
            theme if theme is not None else user["theme"],
            accent if accent is not None else (user["accent"] if "accent" in user.keys() else "teal"),
            user_id,
        ),
    )
    _conn.commit()


def delete_user(user_id: str) -> None:
    _conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    _conn.commit()


# ------------------------------------------------------------------
# TENANTS / LOCATIONS / FOLDERS  (Hierarchie)
# ------------------------------------------------------------------

def list_tenants() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM tenants ORDER BY name").fetchall()]


def create_tenant(name: str, color: str = "#2dd4bf") -> dict:
    tid = _new_id()
    _conn.execute("INSERT INTO tenants (id, name, color) VALUES (?, ?, ?)", (tid, name, color))
    _conn.commit()
    return dict(_conn.execute("SELECT * FROM tenants WHERE id = ?", (tid,)).fetchone())


def ensure_uncategorized() -> tuple[str, str]:
    """
    Stellt sicher, dass es einen Tenant "Uncategorized" mit einer Location
    "Default" gibt, und gibt (tenant_id, location_id) zurück.

    Wird benutzt, wenn ein Client OHNE Tenant/Location hinzugefügt wird -
    er landet dann automatisch hier statt "heimatlos" zu sein.
    """
    # Tenant "Uncategorized" suchen oder anlegen
    row = _conn.execute("SELECT id FROM tenants WHERE name = ?", ("Uncategorized",)).fetchone()
    if row:
        tenant_id = row["id"]
    else:
        tenant_id = _new_id()
        _conn.execute(
            "INSERT INTO tenants (id, name, color) VALUES (?, ?, ?)",
            (tenant_id, "Uncategorized", "#7f93ad"),
        )

    # Location "Default" innerhalb dieses Tenants suchen oder anlegen
    row = _conn.execute(
        "SELECT id FROM locations WHERE tenant_id = ? AND name = ?", (tenant_id, "Default")
    ).fetchone()
    if row:
        location_id = row["id"]
    else:
        location_id = _new_id()
        _conn.execute(
            "INSERT INTO locations (id, tenant_id, name) VALUES (?, ?, ?)",
            (location_id, tenant_id, "Default"),
        )

    _conn.commit()
    return tenant_id, location_id


def list_locations(tenant_id: str | None = None) -> list[dict]:
    if tenant_id:
        rows = _conn.execute("SELECT * FROM locations WHERE tenant_id = ? ORDER BY name", (tenant_id,)).fetchall()
    else:
        rows = _conn.execute("SELECT * FROM locations ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def create_location(tenant_id: str, name: str) -> dict:
    lid = _new_id()
    _conn.execute("INSERT INTO locations (id, tenant_id, name) VALUES (?, ?, ?)", (lid, tenant_id, name))
    _conn.commit()
    return dict(_conn.execute("SELECT * FROM locations WHERE id = ?", (lid,)).fetchone())


def _move_clients_to_uncategorized(where_sql: str, params: tuple) -> int:
    """
    Verschiebt alle Clients, auf die where_sql zutrifft, nach
    Uncategorized/Default (folder_id wird geleert, da Ordner an der alten
    Location hängen). Gibt die Anzahl verschobener Clients zurück.
    """
    unc_tenant, unc_location = ensure_uncategorized()
    cur = _conn.execute(
        f"UPDATE clients SET tenant_id = ?, location_id = ?, folder_id = NULL WHERE {where_sql}",
        (unc_tenant, unc_location, *params),
    )
    return cur.rowcount


def delete_location(location_id: str) -> dict:
    """
    Löscht eine Location. Alle Clients darin wandern nach Uncategorized/Default.
    Die "Default"-Location des Uncategorized-Tenants ist geschützt (Auffangbecken).
    Ordner der Location werden mitgelöscht (Clients wurden vorher verschoben).
    """
    unc_tenant, unc_location = ensure_uncategorized()
    if location_id == unc_location:
        raise ValueError("Die Standard-Location 'Uncategorized/Default' kann nicht gelöscht werden")
    if not _conn.execute("SELECT 1 FROM locations WHERE id = ?", (location_id,)).fetchone():
        raise KeyError("Location nicht gefunden")

    moved = _move_clients_to_uncategorized("location_id = ?", (location_id,))
    # Ordner explizit löschen (nicht auf FK-CASCADE verlassen - PRAGMA
    # foreign_keys ist in SQLite standardmäßig aus).
    _conn.execute("DELETE FROM folders WHERE location_id = ?", (location_id,))
    _conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    _conn.commit()
    return {"moved_clients": moved}


def delete_tenant(tenant_id: str) -> dict:
    """
    Löscht einen Tenant samt aller seiner Locations und Ordner. Alle Clients
    des Tenants (auch solche ohne Location) wandern nach Uncategorized/Default.
    Der Uncategorized-Tenant selbst ist geschützt (Auffangbecken).
    """
    unc_tenant, _ = ensure_uncategorized()
    if tenant_id == unc_tenant:
        raise ValueError("Der Tenant 'Uncategorized' kann nicht gelöscht werden")
    if not _conn.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant_id,)).fetchone():
        raise KeyError("Tenant nicht gefunden")

    # Clients über tenant_id ODER über eine Location des Tenants erwischen
    # (deckt auch inkonsistente Datensätze ab, bei denen nur eins gesetzt ist).
    moved = _move_clients_to_uncategorized(
        "tenant_id = ? OR location_id IN (SELECT id FROM locations WHERE tenant_id = ?)",
        (tenant_id, tenant_id),
    )
    _conn.execute(
        "DELETE FROM folders WHERE location_id IN (SELECT id FROM locations WHERE tenant_id = ?)",
        (tenant_id,),
    )
    _conn.execute("DELETE FROM locations WHERE tenant_id = ?", (tenant_id,))
    _conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
    _conn.commit()
    return {"moved_clients": moved}


def list_folders(location_id: str | None = None) -> list[dict]:
    if location_id:
        rows = _conn.execute("SELECT * FROM folders WHERE location_id = ?", (location_id,)).fetchall()
    else:
        rows = _conn.execute("SELECT * FROM folders").fetchall()
    return [dict(r) for r in rows]


def create_folder(location_id: str, name: str, parent_folder_id: str | None = None) -> dict:
    fid = _new_id()
    _conn.execute(
        "INSERT INTO folders (id, location_id, parent_folder_id, name) VALUES (?, ?, ?, ?)",
        (fid, location_id, parent_folder_id, name),
    )
    _conn.commit()
    return dict(_conn.execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone())


# ------------------------------------------------------------------
# CLIENTS
# ------------------------------------------------------------------

def upsert_client(
    client_id: str,
    hostname: str,
    platform: str | None,
    arch: str | None,
    release: str | None,
    ip: str | None,
) -> None:
    """
    Wird aufgerufen, wenn sich ein Agent registriert.
    Legt den Client neu an (dann landet er zunächst OHNE Tenant/Location,
    "nicht zugeordnet" - der Admin ordnet ihn im Dashboard per Edit-Dialog
    einem Tenant/einer Location zu) oder aktualisiert die Basis-Infos,
    falls er schon existiert.
    """
    now = _now_ms()
    existing = _conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,)).fetchone()
    if existing:
        _conn.execute(
            """
            UPDATE clients
            SET hostname=?, platform=?, arch=?, release=?, ip=?, last_seen=?
            WHERE id=?
            """,
            (hostname, platform, arch, release, ip, now, client_id),
        )
    else:
        _conn.execute(
            """
            INSERT INTO clients (id, hostname, platform, arch, release, ip, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (client_id, hostname, platform, arch, release, ip, now, now),
        )
    _conn.commit()


def touch_client(client_id: str) -> None:
    """Aktualisiert nur den 'zuletzt gesehen'-Zeitstempel (bei jedem Heartbeat)."""
    _conn.execute("UPDATE clients SET last_seen = ? WHERE id = ?", (_now_ms(), client_id))
    _conn.commit()


def list_clients() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM clients ORDER BY hostname").fetchall()]


def get_client(client_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return dict(row) if row else None


def update_client(client_id: str, fields: dict) -> dict | None:
    """
    Generisches Update für den Edit-Dialog im Frontend.
    'fields' ist ein Dict mit den Spalten, die geändert werden sollen,
    z.B. {"hostname": "Neuer Name", "tenant_id": "...", "color": "#ff0000"}.
    """
    allowed = {
        "hostname", "tenant_id", "location_id", "folder_id", "parent_client_id",
        "color", "notes", "status_override", "active", "device_type",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_client(client_id)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [client_id]
    _conn.execute(f"UPDATE clients SET {set_clause} WHERE id = ?", values)
    _conn.commit()
    return get_client(client_id)


def delete_client(client_id: str) -> None:
    _conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    # Client-scoped Rechte-Grants mit aufräumen (verwaiste Einträge vermeiden).
    _conn.execute("DELETE FROM permission_grants WHERE scope = ?", (client_id,))
    _conn.commit()


# ------------------------------------------------------------------
# AUDIT LOG
# ------------------------------------------------------------------

def add_audit_entry(username: str | None, action: str, target: str | None = None, details: str | None = None) -> None:
    _conn.execute(
        "INSERT INTO audit_log (id, ts, username, action, target, details) VALUES (?, ?, ?, ?, ?, ?)",
        (_new_id(), _now_ms(), username, action, target, details),
    )
    _conn.commit()


def list_audit_log(limit: int = 200) -> list[dict]:
    rows = _conn.execute(
        "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def cleanup_old_audit_entries(max_age_days: int = 30) -> None:
    """Löscht Audit-Einträge, die älter als max_age_days sind (Aufbewahrungsfrist)."""
    cutoff = _now_ms() - max_age_days * 86400 * 1000
    _conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
    _conn.commit()


# ------------------------------------------------------------------
# ENROLLMENT TOKENS (Client-Onboarding)
# ------------------------------------------------------------------

def create_enrollment_token(tenant_id: str | None, location_id: str | None, client_name: str | None = None) -> str:
    """Erzeugt einen neuen, zufälligen Onboarding-Token."""
    token = uuid.uuid4().hex  # z.B. "3f9a2b1c..." - lang genug, um nicht erratbar zu sein
    _conn.execute(
        "INSERT INTO enrollment_tokens (token, tenant_id, location_id, client_name, created_at) VALUES (?, ?, ?, ?, ?)",
        (token, tenant_id, location_id, client_name, _now_ms()),
    )
    _conn.commit()
    return token


def get_enrollment_token(token: str) -> dict | None:
    row = _conn.execute("SELECT * FROM enrollment_tokens WHERE token = ?", (token,)).fetchone()
    return dict(row) if row else None


def mark_enrollment_token_used(token: str) -> None:
    _conn.execute("UPDATE enrollment_tokens SET used_at = ? WHERE token = ?", (_now_ms(), token))
    _conn.commit()


# ------------------------------------------------------------------
# SCREEN RECORDINGS (Aufzeichnung von Remote-Sessions)
# ------------------------------------------------------------------

def create_recording(client_id: str, client_hostname: str, username: str, file_path: str) -> str:
    """Legt einen neuen Recording-Eintrag an und gibt dessen ID zurück."""
    rec_id = _new_id()
    _conn.execute(
        """INSERT INTO screen_recordings
           (id, client_id, client_hostname, username, started_at, file_path)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (rec_id, client_id, client_hostname, username, _now_ms(), file_path),
    )
    _conn.commit()
    return rec_id


def finish_recording(rec_id: str, frame_count: int) -> None:
    """Markiert eine Aufzeichnung als beendet und speichert die Frame-Anzahl."""
    _conn.execute(
        "UPDATE screen_recordings SET ended_at = ?, frame_count = ? WHERE id = ?",
        (_now_ms(), frame_count, rec_id),
    )
    _conn.commit()


def list_recordings(limit: int = 200) -> list[dict]:
    rows = _conn.execute(
        "SELECT * FROM screen_recordings ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_recording(rec_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM screen_recordings WHERE id = ?", (rec_id,)).fetchone()
    return dict(row) if row else None


def delete_recording(rec_id: str) -> dict | None:
    """Entfernt einen Recording-DB-Eintrag und gibt ihn zurück (zum Datei-Löschen)."""
    rec = get_recording(rec_id)
    if rec:
        _conn.execute("DELETE FROM screen_recordings WHERE id = ?", (rec_id,))
        _conn.commit()
    return rec


def list_old_recordings(max_age_days: int) -> list[dict]:
    """Liefert Aufzeichnungen, die älter als max_age_days sind (für Auto-Löschung)."""
    cutoff = _now_ms() - max_age_days * 86400 * 1000
    rows = _conn.execute(
        "SELECT * FROM screen_recordings WHERE started_at < ?", (cutoff,)
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# SCRIPTS (gespeicherte Befehle)
# ------------------------------------------------------------------

def list_scripts() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM scripts ORDER BY name").fetchall()]


def get_script(script_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
    return dict(row) if row else None


def create_script(name: str, command: str, os_target: str) -> dict:
    sid = _new_id()
    _conn.execute(
        "INSERT INTO scripts (id, name, command, os, created_at) VALUES (?, ?, ?, ?, ?)",
        (sid, name, command, os_target, _now_ms()),
    )
    _conn.commit()
    return get_script(sid)


def update_script(script_id: str, name: str, command: str, os_target: str) -> dict | None:
    _conn.execute(
        "UPDATE scripts SET name = ?, command = ?, os = ? WHERE id = ?",
        (name, command, os_target, script_id),
    )
    _conn.commit()
    return get_script(script_id)


def delete_script(script_id: str) -> None:
    _conn.execute("DELETE FROM scripts WHERE id = ?", (script_id,))
    _conn.commit()


# ------------------------------------------------------------------
# GRUPPEN / ROLLEN & RECHTE
# ------------------------------------------------------------------
# Verfügbare Rechte-Schlüssel (Legacy-Gruppen-Checkboxen, global allow):
ALL_PERMISSIONS = [
    "login", "screen", "terminal", "explorer", "quick_actions",
    "audit", "manage_users", "manage_clients", "automation",
]

# --- Neues, feingranulares Rechte-Vokabular (tri-state Grants) -------------
# 'admin' ist ein Wildcard (deckt alle anderen Rechte im selben Scope ab).
# 'access_clients' steuert Sichtbarkeit + Basiszugriff auf Clients.
# Manche Rechte sind nur global sinnvoll, andere auch pro Client (siehe Tabs im
# Frontend). Der Resolver in auth.py erlaubt aber jeden Key in jedem Scope.
PERM_KEYS = [
    "admin", "login", "use_guacamole", "use_terminal", "use_screen",
    "use_explorer", "use_taskmanager", "see_audit", "see_replay",
    "delete_replay", "access_clients", "manage_users", "manage_clients",
    "manage_agent", "automation",
]

# Welche Rechte im General-Tab (global) bzw. im Client-Tab angeboten werden.
GENERAL_PERM_KEYS = [
    "admin", "login", "access_clients", "use_guacamole", "use_terminal",
    "use_screen", "use_explorer", "use_taskmanager", "see_audit",
    "see_replay", "delete_replay", "manage_users", "manage_clients",
    "manage_agent", "automation",
]
CLIENT_PERM_KEYS = [
    "access_clients", "admin", "use_terminal", "use_screen", "use_explorer",
    "use_taskmanager", "use_guacamole", "manage_clients", "manage_agent",
]

# Legacy-Gruppen-Recht -> neuer Perm-Key (für Rückwärtskompatibilität).
_LEGACY_PERM_MAP = {
    "login": "login", "screen": "use_screen", "terminal": "use_terminal",
    "explorer": "use_explorer", "audit": "see_audit",
    "manage_users": "manage_users", "manage_clients": "manage_clients",
    "automation": "automation",
}


# ------------------------------------------------------------------
# PERMISSION GRANTS (tri-state, user/group, global/client)
# ------------------------------------------------------------------

def get_grants(subject_type: str, subject_id: str) -> list[dict]:
    """Alle Grants EINES Subjekts (Benutzer oder Gruppe)."""
    rows = _conn.execute(
        "SELECT scope, perm, effect FROM permission_grants "
        "WHERE subject_type = ? AND subject_id = ?",
        (subject_type, subject_id),
    ).fetchall()
    return [dict(r) for r in rows]


def set_grants(subject_type: str, subject_id: str, grants: list[dict]) -> None:
    """
    Ersetzt ALLE Grants eines Subjekts. 'grants' ist eine Liste von
    {scope, perm, effect}. effect muss 'allow' oder 'deny' sein; alles andere
    (bzw. weggelassene Kombinationen) bedeutet 'keine Angabe' und wird nicht
    gespeichert.
    """
    _conn.execute(
        "DELETE FROM permission_grants WHERE subject_type = ? AND subject_id = ?",
        (subject_type, subject_id),
    )
    for g in grants:
        scope = g.get("scope") or "global"
        perm = g.get("perm")
        effect = g.get("effect")
        if perm not in PERM_KEYS or effect not in ("allow", "deny"):
            continue
        _conn.execute(
            "INSERT OR REPLACE INTO permission_grants "
            "(id, subject_type, subject_id, scope, perm, effect) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_new_id(), subject_type, subject_id, scope, perm, effect),
        )
    _conn.commit()


def delete_grants_for_client(client_id: str) -> None:
    """Räumt Client-scoped Grants auf, wenn ein Client gelöscht wird."""
    _conn.execute("DELETE FROM permission_grants WHERE scope = ?", (client_id,))
    _conn.commit()


def get_effective_grants(user_id: str) -> list[dict]:
    """
    Sammelt ALLE relevanten Grants eines Benutzers: seine eigenen (subject
    'user') plus die aller Gruppen, in denen er Mitglied ist ('group'), plus
    die Legacy-Gruppenrechte (groups.permissions) als globale allow-Grants.
    Rückgabe: Liste {scope, perm, effect}.
    """
    grants: list[dict] = []
    grants.extend(get_grants("user", user_id))

    group_ids = get_user_group_ids(user_id)
    for gid in group_ids:
        grants.extend(get_grants("group", gid))
        # Legacy: alte Checkbox-Rechte der Gruppe als globale allow-Grants.
        g = get_group(gid)
        if g:
            for p in (g["permissions"] or "").split(","):
                key = _LEGACY_PERM_MAP.get(p.strip())
                if key:
                    grants.append({"scope": "global", "perm": key, "effect": "allow"})
    return grants


def list_groups() -> list[dict]:
    rows = _conn.execute("SELECT * FROM groups ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def get_group(group_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    return dict(row) if row else None


def create_group(name: str, permissions: list[str], is_ad_group: bool = False) -> dict:
    gid = _new_id()
    _conn.execute(
        "INSERT INTO groups (id, name, permissions, is_ad_group, created_at) VALUES (?, ?, ?, ?, ?)",
        (gid, name, ",".join(permissions), 1 if is_ad_group else 0, _now_ms()),
    )
    _conn.commit()
    return get_group(gid)


def update_group(group_id: str, name: str, permissions: list[str]) -> dict | None:
    _conn.execute(
        "UPDATE groups SET name = ?, permissions = ? WHERE id = ?",
        (name, ",".join(permissions), group_id),
    )
    _conn.commit()
    return get_group(group_id)


def delete_group(group_id: str) -> None:
    _conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    _conn.execute("DELETE FROM user_groups WHERE group_id = ?", (group_id,))
    _conn.commit()


def set_user_groups(user_id: str, group_ids: list[str]) -> None:
    _conn.execute("DELETE FROM user_groups WHERE user_id = ?", (user_id,))
    for gid in group_ids:
        _conn.execute("INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)", (user_id, gid))
    _conn.commit()


def get_user_group_ids(user_id: str) -> list[str]:
    rows = _conn.execute("SELECT group_id FROM user_groups WHERE user_id = ?", (user_id,)).fetchall()
    return [r["group_id"] for r in rows]


def get_user_permissions(user_id: str, role: str) -> set[str]:
    """
    Ermittelt die effektiven Rechte eines Benutzers: Admins haben ALLE Rechte,
    ansonsten die Vereinigung der Rechte aller Gruppen des Benutzers.
    """
    if role == "admin":
        return set(ALL_PERMISSIONS)
    perms: set[str] = set()
    rows = _conn.execute(
        """SELECT g.permissions FROM groups g
           JOIN user_groups ug ON ug.group_id = g.id
           WHERE ug.user_id = ?""",
        (user_id,),
    ).fetchall()
    for r in rows:
        perms.update([p for p in (r["permissions"] or "").split(",") if p])
    return perms


# ------------------------------------------------------------------
# REALMS (Verzeichnis-Anbindung, z.B. Active Directory)
# ------------------------------------------------------------------

def list_realms() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM realms ORDER BY name").fetchall()]


def list_realms_full() -> list[dict]:
    """Wie list_realms(), aber INKLUSIVE bind_password - nur für interne Auth-Nutzung!"""
    return [dict(r) for r in _conn.execute("SELECT * FROM realms ORDER BY name").fetchall()]


def get_public_realms() -> list[dict]:
    """Nur die für das Login-Dropdown nötigen Felder (id + name), ohne Geheimnisse."""
    rows = _conn.execute("SELECT id, name FROM realms WHERE enabled = 1 ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def upsert_ad_user(username: str, display_name: str, realm_id: str) -> dict:
    """
    Legt einen AD-Benutzer lokal an (falls neu) oder aktualisiert seinen
    Anzeigenamen. AD-Benutzer haben keinen nutzbaren Passwort-Hash (sie melden
    sich immer über das Verzeichnis an) und die Rolle 'viewer' - ihre echten
    Rechte kommen über die zugeordneten Gruppen.
    """
    existing = get_user_by_username(username)
    if existing:
        _conn.execute(
            "UPDATE users SET display_name = ?, auth_realm = ? WHERE id = ?",
            (display_name, realm_id, existing["id"]),
        )
        _conn.commit()
        return get_user_by_id(existing["id"])

    user_id = _new_id()
    _conn.execute(
        """INSERT INTO users (id, username, password_hash, display_name, role, must_change_pw, auth_realm, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, username, "!ad-login-no-password!", display_name, "viewer", 0, realm_id, _now_ms()),
    )
    _conn.commit()
    return get_user_by_id(user_id)


def sync_ad_user_groups(user_id: str, ad_group_names: list[str]) -> None:
    """
    Ordnet einen AD-Benutzer den RMM-Gruppen zu, deren Name mit einer seiner
    AD-Gruppen übereinstimmt. So kann ein Admin z.B. eine RMM-Gruppe "RMM-Admins"
    mit bestimmten Rechten anlegen, und jeder AD-Benutzer in der AD-Gruppe
    "RMM-Admins" bekommt diese Rechte automatisch.
    """
    matched_group_ids = []
    all_groups = list_groups()
    for g in all_groups:
        if g["name"] in ad_group_names:
            matched_group_ids.append(g["id"])
    set_user_groups(user_id, matched_group_ids)


def get_realm(realm_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM realms WHERE id = ?", (realm_id,)).fetchone()
    return dict(row) if row else None


def create_realm(name, server, base_dn, bind_user, bind_password,
                 port=None, use_ssl=False, user_filter=None) -> dict:
    rid = _new_id()
    _conn.execute(
        """INSERT INTO realms
           (id, name, server, base_dn, bind_user, bind_password, port, use_ssl, user_filter, enabled, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (rid, name, server, base_dn, bind_user, bind_password,
         port, 1 if use_ssl else 0, user_filter, _now_ms()),
    )
    _conn.commit()
    return dict(_conn.execute("SELECT * FROM realms WHERE id = ?", (rid,)).fetchone())


def update_realm(realm_id: str, fields: dict) -> dict | None:
    """
    Aktualisiert ein Realm. Nur bekannte Felder werden übernommen. Ein leeres
    bind_password wird ignoriert (damit man beim Bearbeiten das gespeicherte
    Passwort nicht versehentlich löscht, wenn man das Feld leer lässt).
    """
    allowed = {
        "name", "server", "base_dn", "bind_user", "bind_password",
        "port", "use_ssl", "user_filter", "enabled",
    }
    updates = {}
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "bind_password" and (v is None or v == ""):
            continue  # leeres Passwort = unverändert lassen
        if k in ("use_ssl", "enabled"):
            v = 1 if v else 0
        updates[k] = v
    if not updates:
        return get_realm(realm_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [realm_id]
    _conn.execute(f"UPDATE realms SET {set_clause} WHERE id = ?", values)
    _conn.commit()
    return get_realm(realm_id)


def delete_realm(realm_id: str) -> None:
    _conn.execute("DELETE FROM realms WHERE id = ?", (realm_id,))
    _conn.commit()


# ------------------------------------------------------------------
# SETTINGS (globaler Schlüssel/Wert-Speicher)
# ------------------------------------------------------------------

# Standardwerte für alle bekannten Einstellungen. get_setting() greift hierauf
# zurück, wenn in der DB noch nichts gespeichert wurde.
DEFAULT_SETTINGS = {
    # Leer = automatisch aus der aufgerufenen URL ableiten (request.base_url).
    # Setzt man hier z.B. "https://rmm.firma.de", tauchen die Install-Befehle
    # mit dieser Adresse statt "localhost" auf. Vollständige URL überschreibt
    # Host/Domain/Port unten.
    "server_url": "",
    # Server-IP/Host bzw. optionale Domain und Ports (für die Install-Befehle).
    "server_host": "",              # IP oder Hostname
    "server_domain": "",            # optionale Domain (wird bevorzugt vor der IP)
    "server_backend_port": "4000",  # Port des Backends/der API
    "server_frontend_port": "4000", # Port des Dashboards (Frontend)
    # In welchem Abstand ein Metrik-Punkt gespeichert wird (Sekunden).
    "metrics_interval_seconds": "10",
    # Wie lange die Metrik-Historie aufbewahrt wird (Stunden).
    # 0 = UNBEGRENZT: es wird nie automatisch gelöscht - die Graphen zeigen
    # nach einem Browser-Reload immer die komplette bisherige Historie.
    "metrics_retention_hours": "0",
    # Wie lange Screen-Replays aufbewahrt werden (Tage).
    "replay_retention_days": "10",
    # --- Aufnahme-Einstellungen (Screen-Agent UND Guacamole-Sessions) ---
    # Master-Schalter: "1" = Sessions werden als Replay aufgezeichnet, "0" = aus.
    "recording_enabled": "1",
    # Screen-Agent-Replay: JPEG-Qualität (1-100) und max. Bilder/Sekunde.
    "screen_record_quality": "40",
    "screen_record_fps": "5",
    # Guacamole-Replay (RDP/VNC/SSH/Telnet): Qualität, FPS, Auflösungs-Skalierung.
    # scale 1.0 = volle Auflösung; 0.5 = halbe (kleinere Dateien).
    "guac_record_quality": "50",
    "guac_record_fps": "8",
    "guac_record_scale": "0.75",
    # Extern gehostetes Apache guacd (für Remote-Desktop im Browser).
    "guacd_host": "127.0.0.1",
    "guacd_port": "4822",
}


def get_setting(key: str, default: str | None = None) -> str | None:
    row = _conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    if key in DEFAULT_SETTINGS:
        return DEFAULT_SETTINGS[key]
    return default


def get_int_setting(key: str) -> int:
    """Einstellung als Ganzzahl lesen; fällt bei Unsinn auf den Default zurück."""
    try:
        return int(float(get_setting(key)))
    except (TypeError, ValueError):
        try:
            return int(float(DEFAULT_SETTINGS.get(key, 0)))
        except (TypeError, ValueError):
            return 0


def get_float_setting(key: str) -> float:
    """Einstellung als Kommazahl lesen; fällt bei Unsinn auf den Default zurück."""
    try:
        return float(get_setting(key))
    except (TypeError, ValueError):
        try:
            return float(DEFAULT_SETTINGS.get(key, 0))
        except (TypeError, ValueError):
            return 0.0


def get_all_settings() -> dict:
    """Alle Einstellungen (Defaults + gespeicherte Overrides) als Dict."""
    result = dict(DEFAULT_SETTINGS)
    for r in _conn.execute("SELECT key, value FROM settings").fetchall():
        result[r["key"]] = r["value"]
    return result


def set_setting(key: str, value) -> None:
    _conn.execute(
        """INSERT INTO settings (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (key, str(value)),
    )
    _conn.commit()


# ------------------------------------------------------------------
# METRICS HISTORY (persistierte Graphen-Daten)
# ------------------------------------------------------------------

def record_metric_point(
    client_id: str, cpu: float, ram: float, net_in: float, net_out: float,
    ts: int | None = None,
) -> None:
    """Speichert einen einzelnen Metrik-Messpunkt für einen Client."""
    ts = ts if ts is not None else _now_ms()
    _conn.execute(
        """INSERT INTO metrics_history (client_id, ts, cpu, ram, net_in, net_out)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (client_id, ts, cpu, ram, net_in, net_out),
    )
    _conn.commit()


def get_metrics_history(client_id: str, since_ts: int | None = None) -> list[dict]:
    """Gibt die gespeicherten Messpunkte eines Clients aufsteigend nach Zeit zurück."""
    if since_ts is not None:
        rows = _conn.execute(
            """SELECT ts, cpu, ram, net_in, net_out FROM metrics_history
               WHERE client_id = ? AND ts >= ? ORDER BY ts ASC""",
            (client_id, since_ts),
        ).fetchall()
    else:
        rows = _conn.execute(
            """SELECT ts, cpu, ram, net_in, net_out FROM metrics_history
               WHERE client_id = ? ORDER BY ts ASC""",
            (client_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def prune_metrics_history(older_than_ts: int) -> None:
    """Löscht Messpunkte, die älter als der Zeitstempel sind."""
    _conn.execute("DELETE FROM metrics_history WHERE ts < ?", (older_than_ts,))
    _conn.commit()


# ------------------------------------------------------------------
# WEBHOOKS (Benachrichtigungen)
# ------------------------------------------------------------------

def list_webhooks() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM webhooks ORDER BY name").fetchall()]


def get_webhook(webhook_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
    return dict(row) if row else None


def create_webhook(name, url, wtype) -> dict:
    wid = _new_id()
    _conn.execute(
        "INSERT INTO webhooks (id, name, url, type, created_at) VALUES (?, ?, ?, ?, ?)",
        (wid, name, url, wtype, _now_ms()),
    )
    _conn.commit()
    return get_webhook(wid)


def delete_webhook(webhook_id: str) -> None:
    _conn.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
    _conn.commit()


# ------------------------------------------------------------------
# AUTOMATIONEN (geplante Befehle/Skripte)
# ------------------------------------------------------------------

def list_automations() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM automations ORDER BY name").fetchall()]


def get_automation(auto_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM automations WHERE id = ?", (auto_id,)).fetchone()
    return dict(row) if row else None


def create_automation(name, command, client_ids, interval_seconds) -> dict:
    aid = _new_id()
    _conn.execute(
        """INSERT INTO automations (id, name, command, client_ids, interval_seconds, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (aid, name, command, ",".join(client_ids), interval_seconds, _now_ms()),
    )
    _conn.commit()
    return get_automation(aid)


def set_automation_enabled(auto_id: str, enabled: bool) -> None:
    _conn.execute("UPDATE automations SET enabled = ? WHERE id = ?", (1 if enabled else 0, auto_id))
    _conn.commit()


def mark_automation_run(auto_id: str) -> None:
    _conn.execute("UPDATE automations SET last_run = ? WHERE id = ?", (_now_ms(), auto_id))
    _conn.commit()


def record_automation_result(run_id: str, automation_id: str, client_id: str,
                             client_hostname: str | None, stdout: str, stderr: str,
                             exit_code: int | None, ok: bool) -> None:
    """Speichert das Ergebnis EINES Clients innerhalb eines Automation-Durchlaufs."""
    _conn.execute(
        """INSERT INTO automation_runs
           (id, run_id, automation_id, client_id, client_hostname, started_at,
            stdout, stderr, exit_code, ok)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (_new_id(), run_id, automation_id, client_id, client_hostname, _now_ms(),
         (stdout or "")[:20000], (stderr or "")[:20000], exit_code, 1 if ok else 0),
    )
    _conn.commit()


def list_automation_runs(automation_id: str, limit_runs: int = 20) -> list[dict]:
    """
    Liefert die letzten Durchläufe einer Automation, gruppiert nach run_id.
    Jeder Durchlauf enthält die Ergebnisliste aller Clients.
    """
    rows = [dict(r) for r in _conn.execute(
        """SELECT * FROM automation_runs WHERE automation_id = ?
           ORDER BY started_at DESC""", (automation_id,)).fetchall()]
    runs: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        rid = r["run_id"]
        if rid not in runs:
            runs[rid] = {"run_id": rid, "started_at": r["started_at"], "results": []}
            order.append(rid)
        runs[rid]["results"].append({
            "client_id": r["client_id"],
            "client_hostname": r["client_hostname"],
            "stdout": r["stdout"],
            "stderr": r["stderr"],
            "exit_code": r["exit_code"],
            "ok": bool(r["ok"]),
            "started_at": r["started_at"],
        })
        runs[rid]["started_at"] = max(runs[rid]["started_at"], r["started_at"])
    return [runs[rid] for rid in order[:limit_runs]]


def prune_automation_runs(older_than_ts: int) -> None:
    _conn.execute("DELETE FROM automation_runs WHERE started_at < ?", (older_than_ts,))
    _conn.commit()


def delete_automation(auto_id: str) -> None:
    _conn.execute("DELETE FROM automations WHERE id = ?", (auto_id,))
    _conn.commit()


def get_due_automations() -> list[dict]:
    """Liefert aktivierte Automationen, deren Intervall seit last_run abgelaufen ist."""
    now = _now_ms()
    due = []
    for a in list_automations():
        if not a["enabled"]:
            continue
        last = a["last_run"] or 0
        if now - last >= a["interval_seconds"] * 1000:
            due.append(a)
    return due

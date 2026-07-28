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
import json
import time
import pathlib
import threading
import uuid

# Pfad zur Datenbank-Datei: liegt im backend-Ordner, eine Ebene über "app/"
DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data.sqlite"


# ------------------------------------------------------------------
# Thread-sichere SQLite-Verbindung
# ------------------------------------------------------------------
# Es gibt EINE geteilte Verbindung für die ganze App. FastAPI/Uvicorn führt
# Request-Handler aber in mehreren Worker-Threads aus, und zusätzlich greifen
# Hintergrund-Tasks (Automation, Uptime, Relay-Auto-Close, DB-Sync, …) aus dem
# Event-Loop-Thread zu. Eine sqlite3.Connection darf NICHT gleichzeitig aus
# mehreren Threads benutzt werden – sonst kommt es zu sporadischen Abstürzen
# wie „sqlite3.InterfaceError: bad parameter or other API misuse".
#
# Deshalb kapseln wir die echte Verbindung in einen dünnen Wrapper, der ALLE
# Zugriffe mit EINEM (reentranten) Lock serialisiert und Abfrage-Ergebnisse
# sofort (noch unter dem Lock) materialisiert. So bleibt jede Operation atomar
# und die eine Verbindung ist gefahrlos aus beliebig vielen Threads nutzbar.

class _CursorResult:
    """Ergebnis eines execute(): Zeilen sind bereits (unter Lock) geladen und
    daher thread-sicher weiterverwendbar (fetchone/fetchall/Iteration)."""

    def __init__(self, rows, rowcount, lastrowid, description=None):
        self._rows = rows
        self._i = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        # Spalten-Beschreibung des echten Cursors (7-Tupel je Spalte, wie bei
        # sqlite3). Wird gebraucht, um Spaltennamen zu ermitteln - z.B. im
        # Source-DB-Werkzeug. Ohne das schlug "cur.description" fehl.
        self.description = description

    def keys(self):
        """Spaltennamen als Liste (wie bei sqlite3.Row / SQLAlchemy-Result)."""
        return [d[0] for d in (self.description or [])]

    def fetchone(self):
        if self._i < len(self._rows):
            r = self._rows[self._i]
            self._i += 1
            return r
        return None

    def fetchall(self):
        r = self._rows[self._i:]
        self._i = len(self._rows)
        return list(r)

    def fetchmany(self, size=1):
        r = self._rows[self._i:self._i + size]
        self._i += len(r)
        return list(r)

    def __iter__(self):
        while self._i < len(self._rows):
            yield self._rows[self._i]
            self._i += 1


class _SafeConn:
    """Dünner, thread-sicherer Wrapper um die geteilte SQLite-Verbindung."""

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_lock", threading.RLock())

    @property
    def lock(self):
        return object.__getattribute__(self, "_lock")

    def _run(self, method, sql, arg):
        with self.lock:
            real = object.__getattribute__(self, "_conn")
            cur = method(real, sql, arg) if arg is not None else method(real, sql)
            try:
                rows = cur.fetchall()
            except Exception:
                rows = []
            # description MUSS hier (noch unter dem Lock) gelesen werden -
            # nach dem nächsten execute() auf derselben Verbindung ist sie weg.
            try:
                desc = cur.description
            except Exception:
                desc = None
            return _CursorResult(rows, cur.rowcount, cur.lastrowid, desc)

    def execute(self, sql, params=None):
        import sqlite3 as _sq
        return self._run(_sq.Connection.execute, sql, params if params is not None else ())

    def executemany(self, sql, seq):
        import sqlite3 as _sq
        return self._run(_sq.Connection.executemany, sql, list(seq))

    def executescript(self, sql):
        with self.lock:
            return object.__getattribute__(self, "_conn").executescript(sql)

    def commit(self):
        with self.lock:
            return object.__getattribute__(self, "_conn").commit()

    def rollback(self):
        with self.lock:
            return object.__getattribute__(self, "_conn").rollback()

    def backup(self, target, *a, **k):
        with self.lock:
            return object.__getattribute__(self, "_conn").backup(target, *a, **k)

    def cursor(self):
        # Direkter Cursor umgeht die Serialisierung; nur unter .lock verwenden.
        with self.lock:
            return object.__getattribute__(self, "_conn").cursor()

    @property
    def total_changes(self):
        with self.lock:
            return object.__getattribute__(self, "_conn").total_changes

    # Sonstige Attribute (z.B. row_factory) an die echte Verbindung durchreichen.
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)


# Eine einzige, geteilte Verbindung für die ganze App.
# check_same_thread=False, weil FastAPI/Uvicorn mehrere Threads nutzen kann;
# die Serialisierung übernimmt _SafeConn (siehe oben).
_raw_conn = sqlite3.connect(DB_PATH, check_same_thread=False)

# row_factory sorgt dafür, dass wir Zeilen wie Dictionaries ansprechen können
# (z.B. row["hostname"] statt row[3]), das ist deutlich lesbarer.
_raw_conn.row_factory = sqlite3.Row

_conn = _SafeConn(_raw_conn)


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

        -- Websites, die an einen Client "gebunden" sind (Quick Access).
        -- Optional mit Uptime-Monitoring: das Backend ruft die URL im
        -- gewählten Intervall auf und benachrichtigt je nach notify-Modus.
        CREATE TABLE IF NOT EXISTS client_websites (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            favorite INTEGER NOT NULL DEFAULT 0,          -- 1 = als Favorit angeheftet
            monitor_enabled INTEGER NOT NULL DEFAULT 0,   -- 1 = Uptime-Monitoring aktiv
            monitor_notify TEXT NOT NULL DEFAULT 'down',  -- 'up' | 'down' | 'always'
            monitor_interval_seconds INTEGER NOT NULL DEFAULT 300, -- Delay zwischen Scans
            last_status TEXT,                             -- 'up' | 'down' | NULL (noch nie geprüft)
            last_checked INTEGER,                         -- Zeitstempel (ms) des letzten Scans
            last_status_change INTEGER,                   -- wann der Status zuletzt wechselte (ms)
            last_error TEXT,                              -- letzte Fehlermeldung (bei down)
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
            net_out REAL NOT NULL DEFAULT 0,-- Bytes/s
            extra TEXT                      -- JSON: komplette Metrik-Momentaufnahme
                                            -- (damit AUCH Ping/Temp/Power/... eine
                                            --  Historie haben, nicht nur cpu/ram/net)
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_history_client_ts
            ON metrics_history (client_id, ts);

        -- ----------------------------------------------------------
        -- AI-Chat: gespeicherte API-Verbindungen (URL/Key/Modell).
        -- visibility: 'private' (nur Ersteller), 'all' (alle Benutzer),
        -- 'shared' (nur die in ai_connection_shares gelisteten User/Gruppen).
        -- Der API-Key wird NIE an Nicht-Eigentümer ausgeliefert - der Chat
        -- läuft immer als Proxy über das Backend.
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS ai_connections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            api_url TEXT NOT NULL,
            api_key TEXT NOT NULL,
            model TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'private',  -- 'private' | 'all' | 'shared'
            owner_user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_connection_shares (
            connection_id TEXT NOT NULL,
            subject_type TEXT NOT NULL,   -- 'user' | 'group'
            subject_id TEXT NOT NULL,
            PRIMARY KEY (connection_id, subject_type, subject_id)
        );

        -- ----------------------------------------------------------
        -- Ticket-System. Sichtbar sind Tickets nur für Admins, den Ersteller,
        -- direkt zugewiesene Benutzer und Mitglieder zugewiesener Gruppen.
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',      -- 'open' | 'in_progress' | 'resolved' | 'closed'
            priority TEXT NOT NULL DEFAULT 'normal',  -- 'low' | 'normal' | 'high' | 'critical'
            created_by TEXT NOT NULL,                 -- username
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            due_date INTEGER,                         -- Fälligkeit (ms) oder NULL
            resolved_at INTEGER,
            resolved_by TEXT
        );
        CREATE TABLE IF NOT EXISTS ticket_assignees (
            ticket_id TEXT NOT NULL,
            subject_type TEXT NOT NULL,   -- 'user' | 'group'
            subject_id TEXT NOT NULL,
            PRIMARY KEY (ticket_id, subject_type, subject_id)
        );
        CREATE TABLE IF NOT EXISTS ticket_clients (
            ticket_id TEXT NOT NULL,
            client_id TEXT NOT NULL,
            PRIMARY KEY (ticket_id, client_id)
        );
        CREATE TABLE IF NOT EXISTS ticket_comments (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ticket_files (
            id TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            mime TEXT,
            uploaded_by TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    _conn.commit()

    # Migration: Spalten für VM/LXC-Kennzeichnung nachrüsten, falls die
    # clients-Tabelle noch aus einer älteren Version stammt.
    _migrate_add_column("clients", "device_type", "TEXT DEFAULT 'physical'")  # 'physical' | 'vm' | 'lxc'
    # "Alle Agents aktualisieren" mit Offline-Option: Flag merkt sich, dass der
    # Client ein Agent-Update bekommen soll, sobald er wieder online ist.
    _migrate_add_column("clients", "pending_agent_update", "INTEGER DEFAULT 0")
    # Silent-Modus für den Remote-Bildschirm (einmalig, siehe sockets.py) und
    # serverseitig gespeicherte UI-Einstellungen (Layouts, Favoriten, ...),
    # damit sie in JEDEM Browser gleich sind (nicht pro Browser/localStorage).
    _migrate_add_column("users", "silent_screen", "INTEGER DEFAULT 0")
    _migrate_add_column("users", "ui_prefs", "TEXT")
    # Client-Websites: Öffnen im internen Browser-Fenster oder externen Tab.
    _migrate_add_column("client_websites", "open_mode", "TEXT DEFAULT 'external'")
    _migrate_add_column("clients", "agent_version", "TEXT")  # gemeldete Agent-Version (für "veraltet"-Hinweis)
    # Automatisches Agent-Update pro Client: 'global' = folgt der Einstellung
    # in den Settings, 'on' = immer, 'off' = nie.
    _migrate_add_column("clients", "auto_update", "TEXT NOT NULL DEFAULT 'global'")
    # Auto-Erkennung des Gerätetyps: Der Agent meldet vm/lxc - übernommen wird
    # das aber erst nach BESTÄTIGUNG durch den Nutzer im Client-Panel.
    _migrate_add_column("clients", "detected_device_type", "TEXT")
    _migrate_add_column("clients", "device_type_ack", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column("enrollment_tokens", "client_name", "TEXT")  # optionaler Wunschname beim Onboarding
    _migrate_add_column("users", "accent", "TEXT DEFAULT 'teal'")  # persönliche Farbpalette
    _migrate_add_column("users", "auth_realm", "TEXT")  # NULL = lokaler User, sonst Realm-ID (AD)
    _migrate_add_column("users", "relay_password", "TEXT")  # (alt, ungenutzt) - früheres Einmalpasswort
    _migrate_add_column("users", "relay_ha1", "TEXT")  # Digest-HA1 = MD5(user:realm:konto-passwort), bei Login gesetzt
    _migrate_add_column("clients", "relay_enabled", "INTEGER NOT NULL DEFAULT 0")  # 1 = im Explorer-Relay freigegeben
    # Automatisches Schließen des Relays: Unix-Zeit in ms, zu der die Freigabe
    # automatisch aufgehoben wird. 0 = nie (bleibt dauerhaft offen).
    _migrate_add_column("clients", "relay_expires_at", "INTEGER NOT NULL DEFAULT 0")
    # Favorisierter Arbeitsordner pro Client (erscheint im Web-Explorer in der
    # Laufwerks-/Wurzel-Ansicht, z.B. /var/www bei einem Webhost). '' = keiner.
    _migrate_add_column("clients", "fav_dir", "TEXT NOT NULL DEFAULT ''")
    # AD-Gruppen, die nichts mit dem RMM zu tun haben, können als "unmanaged"
    # markiert werden: sie landen in einem standardmäßig eingeklappten Ordner
    # in der Rechte-/Gruppenauswahl und werden AD-Benutzern nicht zugewiesen.
    _migrate_add_column("groups", "unmanaged", "INTEGER NOT NULL DEFAULT 0")
    # Standard-Gruppen: '' = normale Gruppe, 'user' = jeder neue Benutzer kommt
    # automatisch hinein, 'admin' = jeder neue Administrator.
    _migrate_add_column("groups", "auto_assign", "TEXT NOT NULL DEFAULT ''")
    _migrate_add_column("screen_recordings", "format", "TEXT NOT NULL DEFAULT 'frames'")  # 'frames' (alt) | 'video' (Client-Aufnahme)
    _migrate_add_column("scripts", "folder", "TEXT NOT NULL DEFAULT ''")  # Ordner-Name für die Scripts-App ('' = kein Ordner)
    _migrate_add_column("metrics_history", "extra", "TEXT")  # JSON-Snapshot aller Metriken (Historie für Ping/Temp/Power/...)
    # Garantie-Ende pro Client (Unix-ms, NULL = keine Garantie hinterlegt).
    _migrate_add_column("clients", "warranty_until", "INTEGER")
    # Ticket-Kommentare: Sichtbarkeit wie bei den Notizen ('all' | 'private' |
    # 'custom') und die Benutzer-ID des Verfassers (bisher nur der Anzeigename).
    _migrate_add_column("ticket_comments", "visibility", "TEXT DEFAULT 'all'")
    _migrate_add_column("ticket_comments", "author_id", "TEXT")

    # Custom-Webhooks: eigene HTTP-Header (JSON-Objekt als Text) und ein
    # frei definierbares Body-Template mit Platzhaltern ({head}, {body}, ...).
    _migrate_add_column("webhooks", "headers", "TEXT")
    _migrate_add_column("webhooks", "body_template", "TEXT")

    # ------------------------------------------------------------------
    # Chat (WhatsApp-artig): Direktnachrichten + Gruppen mit Gruppen-Admins.
    # Benachrichtigungs-Regeln: welcher Trigger löst welchen Kanal aus.
    # ------------------------------------------------------------------
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL DEFAULT 'dm',        -- 'dm' | 'group'
            name TEXT,                              -- Gruppenname (bei dm NULL)
            created_by TEXT,                        -- user_id des Erstellers
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chat_members (
            conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,    -- Gruppen-Admin (bei dm irrelevant)
            joined_at INTEGER NOT NULL,
            last_read_at INTEGER NOT NULL DEFAULT 0,-- für Ungelesen-Zähler
            PRIMARY KEY (conversation_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            sender_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chat_messages_conv
            ON chat_messages(conversation_id, created_at);

        -- ----------------------------------------------------------
        -- Client-Notizen als EINZELNE Einträge mit Sichtbarkeit:
        --   'all'     -> für alle sichtbar, die den Client sehen dürfen
        --   'private' -> nur für den Verfasser
        --   'custom'  -> Verfasser + ausgewählte Benutzer (note_shares)
        -- Das alte Freitextfeld clients.notes bleibt bestehen und wird
        -- einmalig als Eintrag mit Sichtbarkeit 'all' übernommen.
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS client_notes (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            author_id TEXT,
            author_name TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'all',
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_client_notes_client
            ON client_notes(client_id, created_at);
        CREATE TABLE IF NOT EXISTS note_shares (
            note_id TEXT NOT NULL REFERENCES client_notes(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            PRIMARY KEY (note_id, user_id)
        );

        -- Gleiche Sichtbarkeitslogik für Ticket-Kommentare.
        CREATE TABLE IF NOT EXISTS ticket_comment_shares (
            comment_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            PRIMARY KEY (comment_id, user_id)
        );

        -- ----------------------------------------------------------
        -- Aktivitätsprotokoll für Notizen und Tickets.
        -- entity_type: 'client_notes' | 'ticket'
        -- entity_id:   client_id      | ticket_id
        -- action:      z.B. 'note.created', 'ticket.status', 'comment.created'
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS activity_log (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            actor_id TEXT,
            actor_name TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activity_entity
            ON activity_log(entity_type, entity_id, created_at);

        -- ----------------------------------------------------------
        -- Medien-Bibliothek des Audio-Players (Media-Hub):
        --   kind 'local'   -> hochgeladene MP3/MP4 (Datei unter media_files/)
        --   kind 'youtube' -> gemerkter YouTube-Link (Video oder Playlist)
        --   kind 'spotify' -> gemerkter Spotify-Link
        --   kind 'radio'   -> eigener Stream/Sender
        -- shared = 1: für alle Benutzer sichtbar, sonst nur für den Besitzer.
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS media_items (
            id TEXT PRIMARY KEY,
            owner_id TEXT,
            owner_name TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            filename TEXT,
            size INTEGER NOT NULL DEFAULT 0,
            mime TEXT,
            shared INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_media_owner
            ON media_items(owner_id, kind, created_at);

        -- Wiedergabelisten: buendeln beliebige Eintraege der Bibliothek -
        -- Radiosender, hochgeladene Dateien, YouTube- und Spotify-Links.
        CREATE TABLE IF NOT EXISTS media_playlists (
            id TEXT PRIMARY KEY,
            owner_id TEXT,
            owner_name TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            shared INTEGER NOT NULL DEFAULT 0,
            favorite INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        -- Zuordnung Liste -> Eintrag. "position" haelt die Reihenfolge fest.
        CREATE TABLE IF NOT EXISTS media_playlist_items (
            playlist_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (playlist_id, item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_media_pl_items
            ON media_playlist_items(playlist_id, position);

        -- ----------------------------------------------------------
        -- ORGANISATIONS-HIERARCHIE
        -- Jeder Knoten (Benutzer ODER Gruppe) kann GENAU EINEN Vorgesetzten
        -- haben - ebenfalls ein Benutzer oder eine Gruppe. Daraus ergibt sich
        -- ein Baum: "Wer ist wem unterstellt?".
        -- Vorgesetzte dürfen die Kalender ihrer Untergebenen sehen und dort
        -- Termine eintragen (siehe calendar_routes).
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS org_hierarchy (
            child_type TEXT NOT NULL,      -- 'user' | 'group'
            child_id TEXT NOT NULL,
            parent_type TEXT,              -- NULL = oberste Ebene
            parent_id TEXT,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (child_type, child_id)
        );
        CREATE INDEX IF NOT EXISTS idx_org_parent
            ON org_hierarchy(parent_type, parent_id);

        -- ----------------------------------------------------------
        -- KALENDER
        -- Ein Termin kann sich an Benutzer, Gruppen UND Clients richten
        -- (calendar_targets). importance: low | normal | high | critical
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS calendar_events (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            start_at INTEGER NOT NULL,     -- Unix-ms
            end_at INTEGER NOT NULL,       -- Unix-ms
            all_day INTEGER NOT NULL DEFAULT 0,
            importance TEXT NOT NULL DEFAULT 'normal',
            created_by TEXT,
            created_by_name TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cal_start ON calendar_events(start_at);
        CREATE TABLE IF NOT EXISTS calendar_targets (
            event_id TEXT NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL,     -- 'user' | 'group' | 'client'
            target_id TEXT NOT NULL,
            PRIMARY KEY (event_id, target_type, target_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cal_target
            ON calendar_targets(target_type, target_id);

        -- Benachrichtigungs-Regeln: "Wenn <trigger> (auf Clients X/Y/Z),
        -- dann <channel> an <target>". params = JSON (z.B. days_before,
        -- threshold). last_fired = JSON {key: ts} fuer Cooldown/Dedupe.
        CREATE TABLE IF NOT EXISTS notification_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            trigger TEXT NOT NULL,
            client_ids TEXT NOT NULL DEFAULT '',    -- kommagetrennt, '' = alle Clients
            channel TEXT NOT NULL,                  -- 'email' | 'webhook' | 'dashboard'
            target TEXT NOT NULL DEFAULT '',        -- E-Mail-Adresse(n) bzw. webhook_id
            params TEXT NOT NULL DEFAULT '{}',
            last_fired TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        );

        -- ----------------------------------------------------------
        -- TODOS (persönliche Aufgabenliste, "Mini-Notion")
        -- Streng privat: jeder Datensatz hängt an user_id, es gibt keine
        -- Freigaben. Kategorien sind die Wichtigkeits-Spalten; vier davon
        -- werden pro Benutzer automatisch angelegt (builtin: high | mid |
        -- low | archive), weitere kann sich jeder selbst anlegen.
        -- rank = manuelle Sortierung, kleiner = weiter oben/vorne.
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS todo_categories (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#38bdf8',
            rank INTEGER NOT NULL DEFAULT 0,
            builtin TEXT NOT NULL DEFAULT '',   -- '' = selbst angelegt
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_todo_cat_user
            ON todo_categories(user_id, rank);

        CREATE TABLE IF NOT EXISTS todos (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            category_id TEXT NOT NULL,
            prev_category_id TEXT NOT NULL DEFAULT '',  -- Kategorie vor dem Archivieren
            title TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            done INTEGER NOT NULL DEFAULT 0,
            done_at INTEGER,
            recurring INTEGER NOT NULL DEFAULT 0,       -- 1 = täglich neu abhaken
            last_reset TEXT NOT NULL DEFAULT '',        -- 'YYYY-MM-DD' des letzten Tages-Resets
            last_done_day TEXT NOT NULL DEFAULT '',     -- 'YYYY-MM-DD' des letzten Abhakens
            streak INTEGER NOT NULL DEFAULT 0,          -- Serie erledigter Tage
            due_at INTEGER,
            rank INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_todos_user
            ON todos(user_id, category_id, done, rank);

        -- ----------------------------------------------------------
        -- DSGVO: Löschanträge Betroffener (Art. 17).
        -- Wird nicht sofort ausgeführt - Art. 17 Abs. 3 kennt Ausnahmen,
        -- deshalb prüft ein Admin. status: open | done | rejected
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS erasure_requests (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'account',   -- 'account' | 'content'
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            note TEXT NOT NULL DEFAULT '',          -- Begründung der Entscheidung
            created_at INTEGER NOT NULL,
            handled_at INTEGER,
            handled_by TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_erasure_status
            ON erasure_requests(status, created_at);

        -- ----------------------------------------------------------
        -- SOFTWARE-PATCHING
        -- patches = Bestand je Client aus dem letzten Scan. Wird bei jedem
        -- Scan für den Client ersetzt, nicht ergänzt - sonst stehen längst
        -- installierte Updates ewig in der Liste.
        -- level: security | critical | important | moderate | low | feature | other
        -- source: windows-update | winget | apt | dnf
        -- ----------------------------------------------------------
        CREATE TABLE IF NOT EXISTS patches (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            uid TEXT NOT NULL,              -- Kennung beim Paketmanager
            name TEXT NOT NULL,
            current_version TEXT NOT NULL DEFAULT '',
            available_version TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT 'other',
            size INTEGER NOT NULL DEFAULT 0,
            needs_reboot INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | installing | installed | failed | excluded
            error TEXT NOT NULL DEFAULT '',
            found_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_patches_client
            ON patches(client_id, status, level);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_patches_unique
            ON patches(client_id, source, uid);

        -- Auto-Patch-Regeln. scope='global' gilt für alle Clients, die auf
        -- 'global' stehen; scope='client' mit client_id sticht die globale
        -- Regel. Gleiche Logik wie beim Agent-Auto-Update.
        CREATE TABLE IF NOT EXISTS patch_rules (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT 'global',   -- 'global' | 'client'
            client_id TEXT,
            enabled INTEGER NOT NULL DEFAULT 0,
            levels TEXT NOT NULL DEFAULT 'security,critical',  -- kommagetrennt
            sources TEXT NOT NULL DEFAULT '',       -- leer = alle Quellen
            window_start TEXT NOT NULL DEFAULT '02:00',
            window_end TEXT NOT NULL DEFAULT '05:00',
            weekdays TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',    -- 1=Mo
            auto_reboot INTEGER NOT NULL DEFAULT 0,
            exclusions TEXT NOT NULL DEFAULT '',    -- kommagetrennte uids
            scan_interval_hours INTEGER NOT NULL DEFAULT 24,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_patch_rules_client
            ON patch_rules(scope, COALESCE(client_id, ''));

        -- Verlauf: was wurde wann von wem (oder automatisch) installiert.
        CREATE TABLE IF NOT EXISTS patch_runs (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL,
            trigger TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'auto'
            actor TEXT NOT NULL DEFAULT '',
            requested INTEGER NOT NULL DEFAULT 0,
            installed INTEGER NOT NULL DEFAULT 0,
            failed INTEGER NOT NULL DEFAULT 0,
            needs_reboot INTEGER NOT NULL DEFAULT 0,
            detail TEXT NOT NULL DEFAULT '',
            started_at INTEGER NOT NULL,
            finished_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_patch_runs_client
            ON patch_runs(client_id, started_at);

        -- Hosts, deren Zertifikat ein Benutzer im internen Browser
        -- ausdrücklich akzeptiert hat. Bewusst PRO BENUTZER: eine
        -- Ausnahme, die einer setzt, darf nicht stillschweigend für alle
        -- anderen gelten. 'reason' hält fest, WAS am Zertifikat nicht
        -- stimmte, damit später nachvollziehbar bleibt, wofür die
        -- Ausnahme einmal gedacht war.
        CREATE TABLE IF NOT EXISTS webproxy_trusted_hosts (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            host TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_webproxy_trust
            ON webproxy_trusted_hosts(username, host);
        """
    )
    _conn.commit()

    # Freigaben ("für bestimmte") gelten auch für GRUPPEN. Die Spalte user_id
    # trägt weiterhin die ID - subject_type sagt, ob Benutzer oder Gruppe.
    # MUSS NACH dem CREATE-TABLE-Skript stehen - weiter oben gibt es die
    # Tabellen note_shares/ticket_comment_shares noch nicht.
    # Arbeitsbereich / Abteilung eines Benutzers (frei wählbarer Text).
    _migrate_add_column("users", "workspace", "TEXT")
    _migrate_add_column("erasure_requests", "note", "TEXT NOT NULL DEFAULT ''")
    # Patch-Zusammenfassung direkt am Client: list_clients() macht SELECT *,
    # damit stehen die Werte ohne Zusatzabfrage in jedem Dashboard-Widget.
    _migrate_add_column("clients", "patch_policy", "TEXT NOT NULL DEFAULT 'global'")
    _migrate_add_column("clients", "patch_count", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column("clients", "patch_security", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column("clients", "patch_critical", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column("clients", "patch_reboot", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column("clients", "patch_last_scan", "INTEGER")
    _migrate_add_column("note_shares", "subject_type", "TEXT NOT NULL DEFAULT 'user'")
    _migrate_add_column("ticket_comment_shares", "subject_type", "TEXT NOT NULL DEFAULT 'user'")
    _conn.commit()

    # Migration: Realms um Port, SSL (LDAPS) und einen optionalen zusätzlichen
    # Benutzer-Filter erweitern (für produktive AD-Anbindungen).
    _migrate_add_column("realms", "port", "INTEGER")                 # NULL = Standard (389 / 636 bei SSL)
    _migrate_add_column("realms", "use_ssl", "INTEGER NOT NULL DEFAULT 0")  # 1 = LDAPS
    _migrate_add_column("realms", "user_filter", "TEXT")            # optionaler zusätzlicher LDAP-Filter

    # Audio-Player: Favoriten-Markierung an Bibliothekseinträgen.
    _migrate_add_column("media_items", "favorite", "INTEGER NOT NULL DEFAULT 0")

    # Zwei-Faktor-Anmeldung (TOTP). Das Geheimnis wird beim Einrichten erzeugt
    # und erst nach erfolgreicher Prüfung scharf geschaltet (totp_enabled=1) -
    # sonst könnte man sich aussperren, wenn die App den Code nicht liefert.
    _migrate_add_column("users", "totp_secret", "TEXT")
    _migrate_add_column("users", "totp_enabled", "INTEGER NOT NULL DEFAULT 0")
    _migrate_add_column("users", "totp_backup", "TEXT")   # nur Hashes

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

    # Standard-Gruppen (Alle Benutzer / Alle Administratoren) einmalig anlegen
    # und bestehende Konten nachtragen.
    _seed_default_groups()

    # Standard-Skripte (Agent-Update, App-Updates) einmalig einspielen.
    _seed_default_scripts()


# ------------------------------------------------------------------
# STANDARD-GRUPPEN
# ------------------------------------------------------------------
# Zwei Gruppen, in die neue Konten automatisch aufgenommen werden:
#   "Alle Benutzer"          -> jeder Account
#   "Alle Administratoren"   -> jeder Account mit der Rolle 'admin'
#
# Sie sind ganz normale Gruppen: Rechte lassen sich in der Rechte-Verwaltung
# frei anpassen, und sie duerfen geloescht werden - der Seed laeuft nur EINMAL
# (Settings-Flag 'default_groups_seeded'), sie tauchen also nicht wieder auf.
#
# Die Startrechte sind bewusst zurueckhaltend gewaehlt: anmelden, eigenes
# Dashboard, Profilname, persoenliche Werkzeuge. NICHTS, was fremde Geraete
# oder Daten sichtbar macht ('access_clients' fehlt absichtlich).
DEFAULT_GROUP_USER = "Alle Benutzer"
DEFAULT_GROUP_ADMIN = "Alle Administratoren"
DEFAULT_GROUP_SUPPORT = "Alle Support"

_DEFAULT_USER_PERMS = [
    "login", "see_dashboard", "restore_session", "customize_dashboard",
    "edit_profile_name", "use_todos", "use_calendar",
    # Tickets: JEDER muss ein Ticket aufmachen und mitlesen koennen. Sonst
    # laeuft der "Rechte fehlen -> Ticket erstellen"-Weg ins Leere: Genau die
    # Leute, denen ein Recht fehlt, koennten sich dann nicht einmal melden.
    "ticket_read", "ticket_create", "ticket_comment",
]
# Der Admin-Gruppe reicht das Wildcard-Recht: es deckt alle anderen ab.
_DEFAULT_ADMIN_PERMS = ["admin"]

# Support: bearbeitet die Tickets, die aus fehlenden Rechten entstehen.
# Bewusst OHNE 'access_clients' - Support soll Tickets bearbeiten koennen,
# nicht automatisch alle Geraete sehen. Das vergibt man bei Bedarf dazu.
_DEFAULT_SUPPORT_PERMS = _DEFAULT_USER_PERMS + [
    "ticket_edit", "ticket_assign", "ticket_resolve", "see_org",
]

# Seed-Version: bei Erhoehung werden fehlende Gruppen angelegt und GENAU DIE
# in dieser Version neu dazugekommenen Rechte nachgetragen. Absichtlich nicht
# die komplette Liste - sonst bekaeme ein Administrator Rechte zurueck, die er
# bewusst aus der Gruppe entfernt hat.
_DEFAULT_GROUPS_SEED = "2"
_SEED_ADD_V2 = {
    DEFAULT_GROUP_USER: ["ticket_read", "ticket_create", "ticket_comment"],
    DEFAULT_GROUP_ADMIN: [],
    # Die Support-Gruppe ist in dieser Version NEU - sie wird komplett angelegt.
    DEFAULT_GROUP_SUPPORT: _DEFAULT_SUPPORT_PERMS,
}


def _seed_default_groups() -> None:
    # Selbstheilend statt "einmal und nie wieder": Wurde der Seed frueher schon
    # gesetzt, die Gruppen aber sind nicht (mehr) da - etwa weil sie in einer
    # aelteren Version gar nicht erst angelegt wurden oder beim Datenbank-
    # Wechsel verloren gingen -, werden sie erneut erzeugt. Nur wenn beide
    # Gruppen existieren, bleibt alles unangetastet (dann darf man sie auch
    # loeschen, ohne dass sie zurueckkommen... siehe unten).
    version = get_setting("default_groups_seeded") or ""
    wanted = (
        (DEFAULT_GROUP_USER, _DEFAULT_USER_PERMS, "user"),
        (DEFAULT_GROUP_ADMIN, _DEFAULT_ADMIN_PERMS, "admin"),
        (DEFAULT_GROUP_SUPPORT, _DEFAULT_SUPPORT_PERMS, "support"),
    )
    if version == _DEFAULT_GROUPS_SEED:
        _backfill_default_groups()
        return
    if version:
        # Aeltere Version: nur ERGAENZEN. Vorhandene Gruppen behalten ihre
        # Rechte; es werden lediglich fehlende Erlaubnisse dazugelegt und
        # fehlende Gruppen neu erzeugt. Wer eine Gruppe bewusst geloescht hat,
        # bekommt sie nicht zurueck - ausser die Support-Gruppe, die es vorher
        # ueberhaupt nicht gab.
        for name, perms, kind in wanted:
            grp = get_group_by_name(name)
            fresh = False
            if not grp:
                if name != DEFAULT_GROUP_SUPPORT:
                    continue
                grp = create_group(name, [p for p in perms if p in ALL_PERMISSIONS])
                fresh = True
            _conn.execute("UPDATE groups SET auto_assign = ? WHERE id = ?", (kind, grp["id"]))
            wish = perms if fresh else _SEED_ADD_V2.get(name, [])
            have = {g["perm"] for g in get_grants("group", grp["id"])
                    if g.get("scope") == "global"}
            add = [{"scope": "global", "perm": p, "effect": "allow"}
                   for p in wish if p not in have]
            if add:
                set_grants("group", grp["id"],
                           get_grants("group", grp["id"]) + add)
        _conn.commit()
        set_setting("default_groups_seeded", _DEFAULT_GROUPS_SEED)
        print("[db] Standard-Gruppen ergaenzt (inkl. '%s')" % DEFAULT_GROUP_SUPPORT)
        _backfill_default_groups()
        return
    for name, perms, kind in wanted:
        grp = get_group_by_name(name)
        # Die alte Rechte-Spalte mitfuellen: einige Pruefungen laufen noch
        # ueber get_user_permissions() statt ueber die Grants.
        legacy = [p for p in perms if p in ALL_PERMISSIONS]
        if not grp:
            grp = create_group(name, legacy)
        else:
            update_group(grp["id"], name, legacy)
        _conn.execute("UPDATE groups SET auto_assign = ? WHERE id = ?", (kind, grp["id"]))
        set_grants("group", grp["id"],
                   [{"scope": "global", "perm": p, "effect": "allow"} for p in perms])
    _conn.commit()
    set_setting("default_groups_seeded", _DEFAULT_GROUPS_SEED)
    print(f"[db] Standard-Gruppen angelegt: '{DEFAULT_GROUP_USER}', "
          f"'{DEFAULT_GROUP_ADMIN}', '{DEFAULT_GROUP_SUPPORT}'")
    _backfill_default_groups()


def auto_assign_groups(user_id: str, role: str) -> None:
    """Traegt einen Benutzer in alle passenden Standard-Gruppen ein."""
    try:
        rows = _conn.execute(
            "SELECT id, auto_assign FROM groups WHERE auto_assign IS NOT NULL "
            "AND auto_assign != ''").fetchall()
    except Exception:
        return
    for r in rows:
        kind = (r["auto_assign"] or "").strip()
        # "user" gilt fuer ALLE Rollen, "admin"/"support" nur fuer die jeweilige.
        if kind == "user" or (kind == role and role in ("admin", "support")):
            _conn.execute(
                "INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)",
                (user_id, r["id"]))
    _conn.commit()


def _backfill_default_groups() -> None:
    """Bestehende Konten nachtragen - idempotent (INSERT OR IGNORE)."""
    try:
        users = _conn.execute("SELECT id, role FROM users").fetchall()
    except Exception:
        return
    for u in users:
        auto_assign_groups(u["id"], u["role"])


def _seed_default_scripts() -> None:
    """
    Legt die mitgelieferten Standard-Skripte EINMALIG an (Settings-Flag
    'default_scripts_seeded'). Dadurch bleiben sie löschbar/bearbeitbar,
    ohne bei jedem Backend-Start wieder aufzutauchen.

    Enthalten:
      - Agent Update (Linux)   -> offizielles /agent-dist/update.sh, losgelöst
      - Agent Update (Windows) -> SYSTEM-Wartungstask (Event 812), Fallback update.ps1
      - Windows-Apps aktualisieren (winget upgrade --all)
      - Linux-Pakete aktualisieren (apt/dnf/yum/pacman/zypper)
    """
    # Versionierter Seed: "2" = aktueller Stand (mit Ordnern + Ping-Skript).
    # Bestehende Installationen mit Wert "1" bekommen die Ordner nachgetragen
    # und fehlende Skripte ergänzt; gelöschte Skripte bleiben gelöscht.
    row = _conn.execute(
        "SELECT value FROM settings WHERE key = 'default_scripts_seeded'"
    ).fetchone()
    seed_version = row["value"] if row else None
    if seed_version == "2":
        return

    # (name, os, folder, command)
    defaults = [
        (
            "Agent Update (Linux)",
            "linux",
            "Linux",
            "# Startet das offizielle Agent-Update. WICHTIG: losgelöst vom\n"
            "# Agent-Prozess (systemd-run), denn das Update stoppt den\n"
            "# Agent-Dienst - sonst würde das Update sich selbst mit abschießen.\n"
            "BACKEND_URL=$(sed -n 's/^BACKEND_URL=//p' /opt/rapalle-rmm-agent/.env | tr -d '\\r')\n"
            "if [ -z \"$BACKEND_URL\" ]; then echo 'BACKEND_URL nicht gefunden (/opt/rapalle-rmm-agent/.env)'; exit 1; fi\n"
            "if command -v systemd-run >/dev/null 2>&1; then\n"
            "  systemd-run --collect --quiet bash -c \"curl -fsSL \\\"$BACKEND_URL/agent-dist/update.sh\\\" | bash >/tmp/rapalle-agent-update.log 2>&1\"\n"
            "else\n"
            "  nohup bash -c \"curl -fsSL \\\"$BACKEND_URL/agent-dist/update.sh\\\" | bash >/tmp/rapalle-agent-update.log 2>&1\" >/dev/null 2>&1 &\n"
            "fi\n"
            "echo \"Agent-Update gestartet (Log: /tmp/rapalle-agent-update.log)\"",
        ),
        (
            "Agent Update (Windows)",
            "windows",
            "Windows",
            "REM Loest das offizielle Agent-Update aus. Bevorzugt ueber den vor-\n"
            "REM installierten SYSTEM-Wartungstask (Event 812, elevated). Fallback:\n"
            "REM update.ps1 losgeloest starten (Log: %TEMP%\\rapalle-agent-update.log).\n"
            "powershell -NoProfile -ExecutionPolicy Bypass -Command \"$ok=$false; try { Write-EventLog -LogName Application -Source 'RapalleRMM' -EventId 812 -EntryType Information -Message 'rmm update'; $ok=$true; Write-Host 'Agent-Update per SYSTEM-Task (Event 812) ausgeloest.' } catch {}; if (-not $ok) { $b=(Select-String -Path 'C:\\Program Files\\RapalleRmmAgent\\.env' -Pattern '^BACKEND_URL=(.+)$').Matches[0].Groups[1].Value.Trim(); $cmd='iwr ' + $b + '/agent-dist/update.ps1 -UseBasicParsing | iex > $env:TEMP\\rapalle-agent-update.log 2>&1'; Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-Command',$cmd); Write-Host 'Agent-Update gestartet (update.ps1).' }\"",
        ),
        (
            "Windows-Apps aktualisieren (winget)",
            "windows",
            "Windows",
            "REM Aktualisiert ALLE per winget verwalteten Programme unbeaufsichtigt.\n"
            "winget upgrade --all --silent --disable-interactivity --accept-source-agreements --accept-package-agreements --include-unknown",
        ),
        (
            "Linux-Pakete aktualisieren",
            "linux",
            "Linux",
            "# Aktualisiert alle Systempakete - erkennt den Paketmanager automatisch.\n"
            "export DEBIAN_FRONTEND=noninteractive\n"
            "if command -v apt-get >/dev/null 2>&1; then\n"
            "  apt-get update && apt-get upgrade -y\n"
            "elif command -v dnf >/dev/null 2>&1; then\n"
            "  dnf upgrade -y\n"
            "elif command -v yum >/dev/null 2>&1; then\n"
            "  yum update -y\n"
            "elif command -v pacman >/dev/null 2>&1; then\n"
            "  pacman -Syu --noconfirm\n"
            "elif command -v zypper >/dev/null 2>&1; then\n"
            "  zypper --non-interactive update\n"
            "else\n"
            "  echo 'Kein bekannter Paketmanager gefunden'; exit 1\n"
            "fi",
        ),
    ]

    defaults.append((
        "Ping-Test (1.1.1.1)",
        "any",
        "Test",
        # Läuft auf BEIDEN Plattformen: Linux nimmt -c 4; unter Windows schlägt
        # -c fehl und der zweite Aufruf mit -n 4 greift.
        "ping -c 4 1.1.1.1 || ping -n 4 1.1.1.1",
    ))

    existing = {r["name"]: r for r in _conn.execute("SELECT * FROM scripts").fetchall()}
    created = updated = 0
    for name, os_target, folder, command in defaults:
        if name in existing:
            # Upgrade von Seed-Version 1: Ordner nachtragen, falls der Nutzer
            # das Skript noch nicht selbst einsortiert hat.
            if seed_version == "1" and not (existing[name]["folder"] or "").strip():
                _conn.execute("UPDATE scripts SET folder = ? WHERE id = ?",
                              (folder, existing[name]["id"]))
                updated += 1
            continue
        if seed_version == "1" and name != "Ping-Test (1.1.1.1)":
            continue   # bei Upgrade nur das NEUE Skript ergänzen (gelöschte bleiben weg)
        _conn.execute(
            "INSERT INTO scripts (id, name, command, os, folder, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (_new_id(), name, command, os_target, folder, _now_ms()),
        )
        created += 1
    _conn.execute(
        "INSERT INTO settings (key, value) VALUES ('default_scripts_seeded', '2') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    _conn.commit()
    if created or updated:
        print(f"[db] Standard-Skripte: {created} angelegt, {updated} in Ordner einsortiert")


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


# ------------------------------------------------------------------
# Explorer-Relay: Anmeldung + Freigabe pro Client
# ------------------------------------------------------------------
# Für die Digest-Authentifizierung (damit Windows sich OHNE Registry-Änderung
# über HTTP anmelden kann) braucht der Server das HA1 = MD5(user:realm:passwort).
# Da wir Passwörter nur als bcrypt speichern, berechnen wir das HA1 EINMAL bei
# der normalen Dashboard-Anmeldung (dort liegt das Klartext-Passwort vor) und
# legen es ab. So kann sich der Nutzer am Netzlaufwerk mit seinem GANZ NORMALEN
# Konto-Passwort anmelden.
RELAY_REALM = "RAPALLE.net RMM Relay"


def _relay_fernet():
    """Fernet-Instanz, deren Schlüssel aus dem JWT_SECRET abgeleitet wird.
    Damit wird das Konto-Passwort für die Netzlaufwerk-Anmeldung (Digest)
    verschlüsselt abgelegt - nötig, weil Digest das Klartext-Passwort braucht,
    um HA1 für den vom Client gesendeten Benutzernamen zu berechnen."""
    import base64, hashlib
    from cryptography.fernet import Fernet
    from app.config import JWT_SECRET
    key = base64.urlsafe_b64encode(hashlib.sha256(JWT_SECRET.encode()).digest())
    return Fernet(key)


def store_relay_secret(user_id: str, plain_password: str) -> None:
    """Verschlüsseltes Konto-Passwort für den Relay speichern (im Feld relay_password)."""
    try:
        token = _relay_fernet().encrypt(plain_password.encode()).decode("ascii")
        _conn.execute("UPDATE users SET relay_password = ? WHERE id = ?", (token, user_id))
        _conn.commit()
    except Exception:
        pass


def get_relay_secret(user_id: str) -> str | None:
    """Entschlüsseltes Konto-Passwort für die Digest-Berechnung (oder None)."""
    row = _conn.execute("SELECT relay_password FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row["relay_password"]:
        return None
    try:
        return _relay_fernet().decrypt(row["relay_password"].encode()).decode()
    except Exception:
        return None


# Rückwärtskompatible Namen (falls anderswo referenziert)
def store_relay_ha1(user_id: str, username: str, plain_password: str) -> None:
    store_relay_secret(user_id, plain_password)


def get_relay_ha1(user_id: str) -> str | None:
    return None


def get_user_by_username_any(username: str) -> dict | None:
    """Benutzer nur anhand des Benutzernamens (ohne Realm-Suffix) suchen."""
    return get_user_by_username(username)


def set_client_relay_enabled(client_id: str, enabled: bool, expires_at: int = 0) -> None:
    """Relay-Freigabe setzen. expires_at = Unix-ms für automatisches Schließen
    (0 = nie). Beim Sperren wird der Ablaufzeitpunkt immer zurückgesetzt."""
    if enabled:
        _conn.execute(
            "UPDATE clients SET relay_enabled = 1, relay_expires_at = ? WHERE id = ?",
            (int(expires_at or 0), client_id))
    else:
        _conn.execute(
            "UPDATE clients SET relay_enabled = 0, relay_expires_at = 0 WHERE id = ?",
            (client_id,))
    _conn.commit()


def set_client_relay_expiry(client_id: str, expires_at: int) -> None:
    """Nur den Auto-Schließen-Zeitpunkt eines (bereits freigegebenen) Clients setzen."""
    _conn.execute("UPDATE clients SET relay_expires_at = ? WHERE id = ?",
                  (int(expires_at or 0), client_id))
    _conn.commit()


def is_client_relay_enabled(client_id: str) -> bool:
    row = _conn.execute("SELECT relay_enabled FROM clients WHERE id = ?", (client_id,)).fetchone()
    return bool(row and row["relay_enabled"])


def list_relay_enabled_clients() -> list[dict]:
    rows = _conn.execute(
        "SELECT * FROM clients WHERE relay_enabled = 1 ORDER BY hostname").fetchall()
    return [dict(r) for r in rows]


def list_expired_relay_clients(now_ms: int) -> list[dict]:
    """Freigegebene Clients, deren Auto-Schließen-Zeitpunkt erreicht ist (>0)."""
    rows = _conn.execute(
        "SELECT * FROM clients WHERE relay_enabled = 1 AND relay_expires_at > 0 "
        "AND relay_expires_at <= ?", (int(now_ms),)).fetchall()
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
    # Standard-Gruppen sofort zuweisen, damit ein frisches Konto nicht ohne
    # jedes Recht dasteht (nicht einmal 'login').
    auto_assign_groups(user_id, role)
    return get_user_by_id(user_id)


def update_user_password(user_id: str, password_hash: str, must_change_pw: bool = False) -> None:
    _conn.execute(
        "UPDATE users SET password_hash = ?, must_change_pw = ? WHERE id = ?",
        (password_hash, int(must_change_pw), user_id),
    )
    _conn.commit()


def set_user_totp(user_id: str, secret: str | None = None,
                  enabled: bool | None = None, backup: str | None = None) -> None:
    """
    Zwei-Faktor-Felder eines Benutzers setzen. Nur die übergebenen Werte werden
    geändert - so lässt sich z.B. ein verbrauchter Wiederherstellungscode
    entfernen, ohne das Geheimnis anzufassen.
    """
    sets, args = [], []
    if secret is not None:
        sets.append("totp_secret = ?"); args.append(secret or None)
    if enabled is not None:
        sets.append("totp_enabled = ?"); args.append(int(bool(enabled)))
    if backup is not None:
        sets.append("totp_backup = ?"); args.append(backup or None)
    if not sets:
        return
    args.append(user_id)
    _conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(args))
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


def delete_folder(folder_id: str) -> int:
    """
    Löscht einen Ordner samt aller Unterordner (rekursiv). Clients, die in einem
    dieser Ordner lagen, verlieren nur ihre Ordner-Zuordnung (folder_id = NULL) -
    sie bleiben in ihrer Location/ihrem Tenant. Gibt die Anzahl gelöschter Ordner
    zurück.
    """
    # Alle betroffenen Ordner-IDs sammeln (der Ordner + alle Nachkommen).
    to_delete = [folder_id]
    frontier = [folder_id]
    while frontier:
        children = _conn.execute(
            "SELECT id FROM folders WHERE parent_folder_id = ?", (frontier.pop(),)
        ).fetchall()
        for c in children:
            to_delete.append(c["id"])
            frontier.append(c["id"])
    placeholders = ",".join("?" for _ in to_delete)
    # Clients aus diesen Ordnern lösen (bleiben in Location/Tenant).
    _conn.execute(
        f"UPDATE clients SET folder_id = NULL WHERE folder_id IN ({placeholders})",
        to_delete,
    )
    _conn.execute(f"DELETE FROM folders WHERE id IN ({placeholders})", to_delete)
    _conn.commit()
    return len(to_delete)


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


def apply_detected_device_type(client_id: str, detected: str | None) -> bool:
    """
    Merkt sich den vom Agenten automatisch erkannten Gerätetyp ("vm"/"lxc")
    in detected_device_type. Der eigentliche device_type wird NICHT mehr
    automatisch geändert - stattdessen fragt das Client-Panel den Nutzer
    einmalig, ob der Client als VM/LXC (mit Host-Auswahl) oder als physisch
    geführt werden soll (Bestätigung -> device_type_ack = 1).
    Gibt True zurück, wenn eine (neue) Erkennung gespeichert wurde.
    """
    if detected not in ("vm", "lxc"):
        return False
    row = _conn.execute("SELECT detected_device_type FROM clients WHERE id = ?", (client_id,)).fetchone()
    if not row or row["detected_device_type"] == detected:
        return False
    _conn.execute("UPDATE clients SET detected_device_type = ? WHERE id = ?", (detected, client_id))
    _conn.commit()
    return True


def list_clients() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM clients ORDER BY hostname").fetchall()]


def get_client(client_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,)).fetchone()
    return dict(row) if row else None


def set_agent_version(client_id: str, version: str | None) -> None:
    """Speichert die vom Agenten gemeldete Version (für den 'veraltet'-Hinweis)."""
    if not version:
        return
    _conn.execute("UPDATE clients SET agent_version = ? WHERE id = ?", (version, client_id))
    _conn.commit()


def set_pending_agent_update(client_id: str, on: bool) -> None:
    """
    Merkt vor (bzw. löscht), dass der Client ein Agent-Update bekommen soll,
    sobald er sich das nächste Mal verbindet (Offline-Option von
    "Alle Agenten jetzt aktualisieren").
    """
    _conn.execute("UPDATE clients SET pending_agent_update = ? WHERE id = ?",
                  (1 if on else 0, client_id))
    _conn.commit()


def set_user_silent_screen(user_id: int, on: bool) -> None:
    """Einmaliger Silent-Modus: Die NÄCHSTE Remote-Bildschirm-Sitzung dieses
    Benutzers startet ohne Zustimmungs-Dialog am Gerät; danach wird das Flag
    automatisch wieder gelöscht (siehe sockets.dashboard_screen_start)."""
    _conn.execute("UPDATE users SET silent_screen = ? WHERE id = ?",
                  (1 if on else 0, user_id))
    _conn.commit()


_UI_PREFS_MAX_BYTES = 512 * 1024   # Altbestand: EIN Blob in users.ui_prefs

# Neue Ablage: eine Zeile JE SCHLUESSEL statt eines einzigen grossen Blobs.
#
# Warum der Umbau: Vorher lagen alle UI-Einstellungen zusammen in
# users.ui_prefs und wurden gegen ein Limit von 512 KB geprueft. Sobald ein
# einzelner Brocken (Flotten-Verlauf, Benachrichtigungen, grosse Layouts) das
# Limit sprengte, schlug das GESAMTE Speichern fehl - und mit ihm auch das
# Startmenue-Layout mit seinen Ordnern. Nach aussen sah das so aus, als wuerde
# der Browser-Cache "vergessen": In einem anderen Browser/unter einer anderen
# URL war schlicht nichts da, weil nie etwas ankam.
#
# Jetzt scheitert im schlimmsten Fall EIN Schluessel, nie mehr alle.
_UI_PREF_KEY_MAX_BYTES = 2 * 1024 * 1024      # 2 MB je Schluessel
_UI_PREF_TOTAL_MAX_BYTES = 32 * 1024 * 1024   # 32 MB je Benutzer insgesamt


def _ensure_user_prefs_table() -> None:
    _conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_prefs (
            user_id TEXT NOT NULL,
            key     TEXT NOT NULL,
            value   TEXT NOT NULL,
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, key)
        );
    """)
    _conn.commit()


def get_user_prefs(user_id) -> dict:
    """Alle UI-Schluessel eines Benutzers als {key: value}."""
    _ensure_user_prefs_table()
    out: dict[str, str] = {}
    try:
        rows = _conn.execute(
            "SELECT key, value FROM user_prefs WHERE user_id = ?",
            (str(user_id),)).fetchall()
        for r in rows:
            out[r["key"]] = r["value"]
    except Exception:
        pass
    # Einmalige Uebernahme aus der alten Blob-Spalte, damit bestehende
    # Installationen ihre Einstellungen behalten.
    if not out:
        raw = get_user_ui_prefs(user_id)
        if raw:
            import json as _json
            try:
                data = _json.loads(raw)
                keys = data.get("keys") if isinstance(data, dict) else None
                if isinstance(keys, dict) and keys:
                    merge_user_prefs(user_id, keys)
                    out = {str(k): str(v) for k, v in keys.items()}
            except Exception:
                pass
    return out


def merge_user_prefs(user_id, keys: dict) -> None:
    """
    Einzelne Schluessel schreiben/loeschen (Wert None loescht).
    Zu grosse Einzelwerte werden uebersprungen - der Rest wird trotzdem
    gespeichert, damit ein Ausreisser nicht alles blockiert.
    """
    _ensure_user_prefs_table()
    uid = str(user_id)
    skipped: list[str] = []
    for k, v in (keys or {}).items():
        key = str(k)
        if v is None:
            _conn.execute("DELETE FROM user_prefs WHERE user_id = ? AND key = ?", (uid, key))
            continue
        val = str(v)
        if len(val.encode("utf-8")) > _UI_PREF_KEY_MAX_BYTES:
            skipped.append(key)
            continue
        _conn.execute(
            "INSERT INTO user_prefs (user_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (uid, key, val, _now_ms()))
    _conn.commit()
    if skipped:
        print(f"[db] UI-Einstellungen zu gross, uebersprungen: {', '.join(skipped)}")


def user_prefs_size(user_id) -> int:
    _ensure_user_prefs_table()
    try:
        row = _conn.execute(
            "SELECT COALESCE(SUM(LENGTH(value)), 0) AS n FROM user_prefs WHERE user_id = ?",
            (str(user_id),)).fetchone()
        return int(row["n"] or 0)
    except Exception:
        return 0


def get_user_ui_prefs(user_id: int) -> str | None:
    """Serverseitig gespeicherte UI-Einstellungen (JSON-String) des Benutzers."""
    row = _conn.execute("SELECT ui_prefs FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["ui_prefs"] if row and row["ui_prefs"] else None


def set_user_ui_prefs(user_id: int, prefs_json: str) -> None:
    """Speichert die UI-Einstellungen (JSON-String, größenbegrenzt)."""
    if prefs_json and len(prefs_json.encode("utf-8")) > _UI_PREFS_MAX_BYTES:
        raise ValueError("UI-Einstellungen zu groß (max. 512 KB)")
    _conn.execute("UPDATE users SET ui_prefs = ? WHERE id = ?", (prefs_json, user_id))
    _conn.commit()


def merge_user_ui_prefs(user_id: int, keys: dict) -> dict:
    """
    Führt einzelne UI-Schlüssel in den gespeicherten Stand ein, OHNE die
    übrigen Schlüssel zu verlieren.

    Warum das wichtig ist: Früher hat das Frontend immer den KOMPLETTEN Satz
    per PUT geschrieben. Öffnete man das RMM über eine andere URL/einen anderen
    Browser (leerer localStorage) und lief ein Speichervorgang, bevor der
    Server-Stand geladen war, wurden serverseitig gespeicherte Dinge (offene
    Ordner im Startmenü, Favoriten, Layouts) überschrieben bzw. gelöscht.
    Mit dem Merge kann ein Client immer nur das ändern, was er wirklich kennt.

    Wert None löscht den Schlüssel gezielt.
    """
    import json as _json
    raw = get_user_ui_prefs(user_id)
    cur = {}
    if raw:
        try:
            data = _json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("keys"), dict):
                cur = data["keys"]
        except Exception:
            cur = {}
    for k, v in (keys or {}).items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[str(k)] = str(v)
    set_user_ui_prefs(user_id, _json.dumps({"keys": cur}))
    return cur


def update_client(client_id: str, fields: dict) -> dict | None:
    """
    Generisches Update für den Edit-Dialog im Frontend.
    'fields' ist ein Dict mit den Spalten, die geändert werden sollen,
    z.B. {"hostname": "Neuer Name", "tenant_id": "...", "color": "#ff0000"}.
    """
    allowed = {
        "hostname", "tenant_id", "location_id", "folder_id", "parent_client_id",
        "color", "notes", "status_override", "active", "device_type", "auto_update",
        "device_type_ack", "fav_dir", "warranty_until", "patch_policy",
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

def create_recording(client_id: str, client_hostname: str, username: str, file_path: str,
                     fmt: str = "frames") -> str:
    """Legt einen neuen Recording-Eintrag an und gibt dessen ID zurück.
    fmt: 'frames' (JPEG-Frames, Standard) oder 'term' (Terminal-Sitzung als
    Text-Replay - siehe recording.start_term_recording)."""
    rec_id = _new_id()
    _conn.execute(
        """INSERT INTO screen_recordings
           (id, client_id, client_hostname, username, started_at, file_path, format)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (rec_id, client_id, client_hostname, username, _now_ms(), file_path, fmt),
    )
    _conn.commit()
    return rec_id


def create_video_recording(client_id: str, client_hostname: str, username: str,
                           file_path: str, started_at: int, ended_at: int) -> str:
    """Legt einen Eintrag für eine im Browser aufgenommene 1:1-Video-Aufzeichnung an."""
    rec_id = _new_id()
    _conn.execute(
        """INSERT INTO screen_recordings
           (id, client_id, client_hostname, username, started_at, ended_at, frame_count, file_path, format)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'video')""",
        (rec_id, client_id, client_hostname, username, started_at, ended_at, file_path),
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


def prune_missing_recordings() -> int:
    """
    Entfernt Replay-Einträge, deren Datei auf der Platte nicht mehr existiert
    (z.B. manuell gelöscht oder Volume gewechselt). WICHTIG: Es wird NUR der
    screen_recordings-Eintrag entfernt - das Audit-Log bleibt vollständig
    erhalten. Gibt die Anzahl der entfernten Einträge zurück.
    """
    from pathlib import Path as _P
    removed = 0
    rows = _conn.execute("SELECT id, file_path FROM screen_recordings").fetchall()
    for r in rows:
        fp = r["file_path"]
        if not fp:
            continue
        try:
            if not _P(fp).exists():
                _conn.execute("DELETE FROM screen_recordings WHERE id = ?", (r["id"],))
                removed += 1
        except OSError:
            # Pfad nicht prüfbar (z.B. Netzlaufwerk offline) -> Eintrag behalten.
            continue
    if removed:
        _conn.commit()
    return removed


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


def create_script(name: str, command: str, os_target: str, folder: str = "") -> dict:
    sid = _new_id()
    _conn.execute(
        "INSERT INTO scripts (id, name, command, os, folder, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (sid, name, command, os_target, (folder or "").strip(), _now_ms()),
    )
    _conn.commit()
    return get_script(sid)


def update_script(script_id: str, name: str, command: str, os_target: str, folder: str = "") -> dict | None:
    _conn.execute(
        "UPDATE scripts SET name = ?, command = ?, os = ?, folder = ? WHERE id = ?",
        (name, command, os_target, (folder or "").strip(), script_id),
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
# Neben den NEUEN Keys behalten wir die ALTEN Keys gültig, damit bereits
# gespeicherte Grants weiter auflösen (keine DB-Migration!). Die Zuordnung
# alt->neu passiert zur Laufzeit über _PERM_IMPLIES (siehe unten).

# Globale (universelle) Rechte:
GLOBAL_PERM_KEYS = [
    "admin",
    "login",
    "see_dashboard",
    "customize_dashboard",
    "restore_session",
    "edit_profile_name",
    "access_clients",
    "see_replay", "delete_replay",
    "see_audit",
    "see_source", "edit_source", "delete_source",
    "manage_branding",
    "manage_sso",
    # Einstellungen: sehen / Standard-Einstellungen ändern / Admin-Einstellungen
    # (IP/Port, Server-Update von GitHub, Datenbank, Neustart/Stop, guacd).
    "see_settings", "manage_settings", "admin_settings",
    "see_permissions", "manage_permissions",
    "create_users",
    "network_scan", "port_scan",
    "bulk_shell",
    "use_scripts", "create_scripts",
    "automation",
    "manage_hierarchy",
    "play_games",
    # Audio-Player / Media-Hub (eigene Bibliothek, Uploads)
    "use_media",
    # Medien fuer ALLE bereitstellen: Uploads, Links und Wiedergabelisten, die
    # jeder Benutzer sieht. Bewusst ein eigenes Recht - wer den Player benutzen
    # darf ("use_media"), soll nicht automatisch die gemeinsame Bibliothek
    # veraendern koennen.
    "share_media",
    "manage_favorites",
    "use_relay", "relay_unlimited",
    # Chat-App (Direktnachrichten & Gruppen)
    "use_chat",
    # Persönliche Todo-Liste (streng privat, keine Freigaben)
    "use_todos",
    # Software-Patching: Bestand sehen und Updates anstoßen bzw. Regeln setzen.
    "patching", "manage_patching",
    # Datenschutz: Fristen setzen, Auskunft über Dritte, Löschung ausführen.
    # Bewusst NICHT in 'admin_settings' enthalten - wer Server-Einstellungen
    # ändern darf, muss nicht automatisch fremde Personendaten einsehen.
    "manage_privacy",
    # (Frueher stand hier zusaetzlich "super_admin" - "Voll-Administrator".
    #  Das war dasselbe wie das 'admin'-Wildcard und nur verwirrend: zwei
    #  Haekchen mit identischer Wirkung. Beide sind jetzt zu 'admin'
    #  zusammengefasst; 'super_admin' bleibt als Alt-Schluessel gueltig, damit
    #  bereits vergebene Rechte weiter greifen.)
    # Neue Clients aufnehmen (Enrollment-Token erzeugen, Agent-Installer holen)
    "add_client",
    # Organigramm (Über-/Unterstellung von Benutzern und Gruppen)
    "see_org", "manage_org",
    # Kalender
    "use_calendar",      # eigenen Kalender + eigene Termine sehen
    "manage_calendar",   # Termine für andere anlegen/ändern (über die eigene
                         # Vorgesetzten-Rolle hinaus)
    # Remote-Bildschirm ohne Anfrage: erlaubt den einmaligen "Silent-Modus"
    # im Profil (nächste Remote-Sitzung ohne Zustimmungs-Dialog am Gerät).
    "screen_silent",
    # Ticket-System (Sichtbarkeit einzelner Tickets zusätzlich auf
    # Admins/Ersteller/Zugewiesene begrenzt, siehe tickets_routes.py).
    "ticket_read", "ticket_create", "ticket_edit", "ticket_comment",
    "ticket_assign", "ticket_resolve", "ticket_delete",
]

# Pro-Client-Rechte:
CLIENT_ONLY_PERM_KEYS = [
    "access_clients",
    "manage_clients",       # bearbeiten
    "manage_agent",         # aktualisieren (Agent-Update)
    "c_delete",             # löschen
    "c_screen", "c_screen_view",
    "c_terminal", "c_terminal_console",
    "c_guacamole",
    "c_power",              # runterfahren/neustarten
    "c_explorer_view", "c_explorer_edit",
    "c_taskmanager_view", "c_taskmanager_kill",
    "c_relay", "c_relay_unlimited",
    "c_notes_view", "c_notes_edit",
    "c_patch_view", "c_patch_apply",   # Patches dieses Clients sehen / einspielen
    "c_websites_view", "c_websites_edit",
    "c_screen_silent",      # Remote-Bildschirm ohne Anfrage (pro Client)
    "set_warranty",         # Garantie-Datum dieses Clients setzen/ändern
]

# Alt-Keys (nur noch für Rückwärts-Auflösung gespeicherter Grants).
LEGACY_PERM_KEYS = [
    "use_guacamole", "use_terminal", "use_screen", "use_explorer",
    "use_taskmanager", "manage_users",
    # Zusammengefuehrt mit 'admin' (siehe oben) - wird nicht mehr angeboten,
    # loest aber weiterhin auf.
    "super_admin",
]

# Master-Liste gültiger Keys (für set_grants-Validierung + Resolver).
PERM_KEYS = GLOBAL_PERM_KEYS + [
    p for p in CLIENT_ONLY_PERM_KEYS if p not in GLOBAL_PERM_KEYS
] + LEGACY_PERM_KEYS

# ------------------------------------------------------------------
# STANDARD-CLIENT ("Default-Client")
# Ein Pseudo-Client-Scope, dessen Client-Rechte für JEDEN Client gelten.
# Damit lassen sich Rechte einmal zentral vergeben, statt sie pro Client
# einzeln zu setzen. Auflösung (siehe auth._resolve): 'deny' schlägt immer
# 'allow' - ein Verbot auf einem KONKRETEN Client sticht also die Erlaubnis
# aus dem Standard-Client.
# ------------------------------------------------------------------
DEFAULT_CLIENT_SCOPE = "*"

# Welche Rechte im General-Tab (global) bzw. im Client-Tab angeboten werden.
GENERAL_PERM_KEYS = list(GLOBAL_PERM_KEYS)
CLIENT_PERM_KEYS = list(CLIENT_ONLY_PERM_KEYS)

# ------------------------------------------------------------------
# Rechte-Implikationen (zur Laufzeit, ohne die DB zu ändern):
# Ein 'allow' auf dem Quell-Key gilt automatisch auch für alle Ziel-Keys.
# Deckt (a) alte Keys -> neue Keys und (b) "stärkeres Recht deckt schwächeres"
# ab (z.B. Vollzugriff impliziert Nur-Ansehen).
# ------------------------------------------------------------------
_PERM_IMPLIES = {
    # --- Alt -> Neu (gespeicherte Grants bleiben unangetastet) ---
    "use_screen": ["c_screen", "c_screen_view"],
    "use_terminal": ["c_terminal", "c_terminal_console"],
    "use_explorer": ["c_explorer_view", "c_explorer_edit"],
    "use_taskmanager": ["c_taskmanager_view", "c_taskmanager_kill"],
    "use_guacamole": ["c_guacamole"],
    "manage_users": ["create_users", "see_permissions", "manage_permissions"],
    # altes manage_clients deckte auch Notizen/Websites/Löschen/Power ab
    "manage_clients": ["c_delete", "c_power", "c_notes_view", "c_notes_edit",
                       "c_websites_view", "c_websites_edit", "set_warranty"],
    # --- Stärkeres Recht deckt schwächeres ab ---
    "c_screen": ["c_screen_view"],
    "c_terminal": ["c_terminal_console"],
    "c_explorer_edit": ["c_explorer_view"],
    "c_taskmanager_kill": ["c_taskmanager_view"],
    "c_notes_edit": ["c_notes_view"],
    "c_websites_edit": ["c_websites_view"],
    "c_relay_unlimited": ["c_relay"],
    "manage_permissions": ["see_permissions"],
    "manage_org": ["see_org"],
    "manage_calendar": ["use_calendar"],
    "manage_patching": ["patching"],
    # Wer Medien fuer alle bereitstellen darf, darf den Player selbst nutzen.
    "share_media": ["use_media"],
    "c_patch_apply": ["c_patch_view"],
    "edit_source": ["see_source"],
    "delete_source": ["see_source", "edit_source"],
    "relay_unlimited": ["use_relay"],
    "create_scripts": ["use_scripts"],
    # Einstellungen: Bearbeiten setzt Ansehen voraus, Admin-Einstellungen
    # decken auch die Standard-Einstellungen ab.
    "manage_settings": ["see_settings"],
    "admin_settings": ["manage_settings", "see_settings"],
    # Dashboard anpassen setzt Dashboard sehen voraus.
    "customize_dashboard": ["see_dashboard"],
    # Aufzeichnungen löschen setzt Sehen voraus.
    "delete_replay": ["see_replay"],
    # Bildschirm ohne Anfrage setzt das Bildschirm-Recht voraus.
    "c_screen_silent": ["c_screen"],
    # Ticket-Rechte setzen das Lese-Recht voraus.
    "ticket_create": ["ticket_read"],
    "ticket_edit": ["ticket_read"],
    "ticket_comment": ["ticket_read"],
    "ticket_assign": ["ticket_read"],
    "ticket_resolve": ["ticket_read"],
    "ticket_delete": ["ticket_read"],
}


def perm_implies_map() -> dict:
    """Implikations-Map (Quelle -> abgedeckte Rechte) fürs Frontend."""
    return {k: list(v) for k, v in _PERM_IMPLIES.items()}


def perms_implied_by(perm: str) -> list[str]:
    """Quell-Keys, deren 'allow' das gefragte Recht 'perm' automatisch erfüllt."""
    return [src for src, dsts in _PERM_IMPLIES.items() if perm in dsts]


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
    """Räumt Client-scoped Grants auf, wenn ein Client gelöscht wird.
    Der Standard-Client-Scope ('*') bleibt dabei immer erhalten."""
    if not client_id or client_id == DEFAULT_CLIENT_SCOPE:
        return
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


def get_group_by_name(name: str) -> dict | None:
    row = _conn.execute("SELECT * FROM groups WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def upsert_ad_group(name: str) -> dict:
    """
    Legt eine AD-Gruppe (is_ad_group=1) an, falls sie noch nicht existiert, und
    gibt sie zurück. Existiert bereits eine gleichnamige Gruppe (egal ob lokal
    oder AD), wird diese unverändert zurückgegeben (Namen sind der Matching-Key
    zwischen AD-Gruppen und RMM-Gruppen).
    """
    existing = get_group_by_name(name)
    if existing:
        return existing
    return create_group(name, [], is_ad_group=True)


def create_group(name: str, permissions: list[str], is_ad_group: bool = False) -> dict:
    gid = _new_id()
    _conn.execute(
        "INSERT INTO groups (id, name, permissions, is_ad_group, created_at) VALUES (?, ?, ?, ?, ?)",
        (gid, name, ",".join(permissions), 1 if is_ad_group else 0, _now_ms()),
    )
    _conn.commit()
    return get_group(gid)


def set_group_unmanaged(group_id: str, unmanaged: bool) -> dict | None:
    """Markiert eine (AD-)Gruppe als unverwaltet (1) bzw. verwaltet (0). Unverwaltete
    Gruppen erscheinen in einem eingeklappten Ordner und werden bei der
    AD-Anmeldung NICHT automatisch zugewiesen."""
    _conn.execute("UPDATE groups SET unmanaged = ? WHERE id = ?",
                  (1 if unmanaged else 0, group_id))
    _conn.commit()
    return get_group(group_id)


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
    Weist einem AD-Benutzer automatisch seine AD-Gruppen zu:
      - Für jede AD-Gruppe wird bei Bedarf eine RMM-Gruppe angelegt (is_ad_group=1).
      - Als "unmanaged" markierte Gruppen werden übersprungen (sie sollen nichts
        mit dem RMM zu tun haben).
      - Manuell zugewiesene NICHT-AD-Gruppen (lokale Gruppen) bleiben erhalten.
    """
    # Bestehende lokale (Nicht-AD-)Mitgliedschaften behalten.
    keep = []
    for gid in get_user_group_ids(user_id):
        g = get_group(gid)
        if g and not g.get("is_ad_group"):
            keep.append(gid)

    ad_ids = []
    for name in ad_group_names:
        g = upsert_ad_group(name)          # legt AD-Gruppe an, falls neu
        if g.get("unmanaged"):
            continue                        # unverwaltete AD-Gruppen nicht zuweisen
        ad_ids.append(g["id"])

    # Reihenfolge/Duplikate bereinigen und speichern.
    set_user_groups(user_id, list(dict.fromkeys(keep + ad_ids)))


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
    # --- Host-Sperre: Zugriff nur über die hinterlegten Adressen ---
    # "0" = aus (Standard). Bewusst aus, weil eine falsch gesetzte Sperre
    # den Zugang kostet; siehe hostlock.py.
    "host_lock_enabled": "0",
    # 'ui'  = nur Oberfläche/API sperren, Agenten kommen weiter durch
    # 'all' = auch Agenten-Verbindungen (Socket.IO) prüfen
    "host_lock_scope": "ui",
    # Zusätzlich erlaubte Adressen, kommagetrennt (z.B. interner DNS-Name).
    "host_lock_extra": "",
    # X-Forwarded-Host vertrauen? Nur einschalten, wenn wirklich ein
    # Reverse Proxy davorsteht - sonst hebelt jeder die Sperre selbst aus.
    "host_lock_trust_proxy": "0",
    # --- Aufbewahrungsfristen (DSGVO Art. 5 Abs. 1 lit. e) ---
    # Überall gilt: 0 = unbegrenzt. Das ist eine bewusste Entscheidung und
    # sollte begründet sein - "aus Versehen für immer" ist kein Rechtsgrund.
    # Durchgesetzt werden die Fristen von privacy.purge() (täglicher Job).
    # Wie lange Screen-Replays aufbewahrt werden (Tage). Der heikelste
    # Bestand des Systems, daher knapper Standard.
    "replay_retention_days": "30",
    "retention_audit_days": "90",        # danach anonymisiert, nicht gelöscht
    "retention_activity_days": "180",
    "retention_chat_days": "365",
    "retention_todo_archive_days": "365",
    "retention_enrollment_days": "30",
    # Zeitpunkt des letzten Durchlaufs (nur Anzeige).
    "privacy_last_purge": "0",
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
    # Server-Selbst-Update aus GitHub: "1" = automatisch, Kanal siehe unten.
    "server_auto_update": "0",
    # "commit" = neuester Commit, "full" = neuestes Full-Release,
    # "any" = neuestes Release (Alpha und Full)
    "server_auto_update_channel": "full",
    # Automatisches Agent-Update: "1" = veraltete Agenten aktualisieren sich
    # beim Verbinden selbst (Clients mit auto_update='global' folgen dieser
    # Einstellung; 'on'/'off' pro Client hat Vorrang). Default aus.
    "agent_auto_update": "0",
    # --- Software-Patching ---
    # Wie oft die Agenten nach Aktualisierungen suchen (Stunden).
    "patch_scan_interval_hours": "24",
    # Master-Schalter fürs automatische Einspielen. Clients mit
    # patch_policy='global' folgen ihm; 'on'/'off' pro Client sticht.
    "patch_auto_enabled": "0",
    # --- SMTP (E-Mail-Benachrichtigungen) ---
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_security": "starttls",   # 'starttls' | 'ssl' | 'none'
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
    ts: int | None = None, extra: str | None = None,
) -> None:
    """Speichert einen einzelnen Metrik-Messpunkt für einen Client.
    extra = JSON-String der kompletten Metrik-Momentaufnahme (optional), damit
    auch Metriken ohne eigene Spalte (Ping, Temperatur, Leistung, ...) eine
    Historie bekommen."""
    ts = ts if ts is not None else _now_ms()
    _conn.execute(
        """INSERT INTO metrics_history (client_id, ts, cpu, ram, net_in, net_out, extra)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (client_id, ts, cpu, ram, net_in, net_out, extra),
    )
    _conn.commit()


def get_metrics_history(client_id: str, since_ts: int | None = None) -> list[dict]:
    """Gibt die gespeicherten Messpunkte eines Clients aufsteigend nach Zeit zurück."""
    if since_ts is not None:
        rows = _conn.execute(
            """SELECT ts, cpu, ram, net_in, net_out, extra FROM metrics_history
               WHERE client_id = ? AND ts >= ? ORDER BY ts ASC""",
            (client_id, since_ts),
        ).fetchall()
    else:
        rows = _conn.execute(
            """SELECT ts, cpu, ram, net_in, net_out, extra FROM metrics_history
               WHERE client_id = ? ORDER BY ts ASC""",
            (client_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # extra (JSON-Snapshot) direkt in den Punkt mergen, damit das Frontend
        # jede Metrik (Ping/Temp/Power/...) als Verlauf abgreifen kann.
        raw = d.pop("extra", None)
        if raw:
            try:
                snap = json.loads(raw)
                if isinstance(snap, dict):
                    d["m"] = snap
            except (ValueError, TypeError):
                pass
        out.append(d)
    return out


def prune_metrics_history(older_than_ts: int) -> None:
    """Löscht Messpunkte, die älter als der Zeitstempel sind."""
    _conn.execute("DELETE FROM metrics_history WHERE ts < ?", (older_than_ts,))
    _conn.commit()


# ------------------------------------------------------------------
# WEBHOOKS (Benachrichtigungen)
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# Client-Websites (Quick Access + Uptime-Monitoring)
# ------------------------------------------------------------------

def list_client_websites(client_id: str) -> list[dict]:
    """Alle an einen Client gebundenen Websites (für den Quick Access)."""
    rows = _conn.execute(
        "SELECT * FROM client_websites WHERE client_id = ? ORDER BY favorite DESC, name",
        (client_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_favorite_websites() -> list[dict]:
    """Alle als Favorit angehefteten Websites (über alle Clients hinweg),
    angereichert um den Hostnamen des zugehörigen Clients."""
    rows = _conn.execute(
        """SELECT w.*, c.hostname AS client_hostname
           FROM client_websites w
           JOIN clients c ON c.id = w.client_id
           WHERE w.favorite = 1
           ORDER BY w.name""",
    ).fetchall()
    return [dict(r) for r in rows]


def list_monitored_websites() -> list[dict]:
    """Alle Websites mit aktivem Uptime-Monitoring (für die Monitor-Engine)."""
    rows = _conn.execute(
        """SELECT w.*, c.hostname AS client_hostname, c.tenant_id, c.location_id
           FROM client_websites w
           JOIN clients c ON c.id = w.client_id
           WHERE w.monitor_enabled = 1""",
    ).fetchall()
    return [dict(r) for r in rows]


def get_client_website(website_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM client_websites WHERE id = ?", (website_id,)).fetchone()
    return dict(row) if row else None


def create_client_website(client_id: str, name: str, url: str, favorite: bool = False,
                          monitor_enabled: bool = False, monitor_notify: str = "down",
                          monitor_interval_seconds: int = 300,
                          open_mode: str = "external") -> dict:
    wid = _new_id()
    _conn.execute(
        """INSERT INTO client_websites
           (id, client_id, name, url, favorite, monitor_enabled, monitor_notify,
            monitor_interval_seconds, open_mode, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (wid, client_id, name, url, int(favorite), int(monitor_enabled),
         monitor_notify, int(monitor_interval_seconds), open_mode, _now_ms()),
    )
    _conn.commit()
    return get_client_website(wid)


def update_client_website(website_id: str, fields: dict) -> dict | None:
    """Aktualisiert nur die übergebenen Felder (analog update_client)."""
    allowed = {"name", "url", "favorite", "monitor_enabled", "monitor_notify",
               "monitor_interval_seconds", "open_mode"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        _conn.execute(f"UPDATE client_websites SET {sets} WHERE id = ?",
                      (*fields.values(), website_id))
        _conn.commit()
    return get_client_website(website_id)


def delete_client_website(website_id: str) -> None:
    _conn.execute("DELETE FROM client_websites WHERE id = ?", (website_id,))
    _conn.commit()


def set_website_check_result(website_id: str, status: str, error: str | None = None) -> None:
    """Ergebnis eines Uptime-Scans speichern ('up'/'down'). Aktualisiert
    last_checked immer, last_status_change nur bei tatsächlichem Wechsel."""
    now = _now_ms()
    prev = _conn.execute("SELECT last_status FROM client_websites WHERE id = ?",
                         (website_id,)).fetchone()
    changed = (prev is None) or (prev["last_status"] != status)
    if changed:
        _conn.execute(
            """UPDATE client_websites SET last_status = ?, last_checked = ?,
               last_status_change = ?, last_error = ? WHERE id = ?""",
            (status, now, now, error, website_id))
    else:
        _conn.execute(
            "UPDATE client_websites SET last_checked = ?, last_error = ? WHERE id = ?",
            (now, error, website_id))
    _conn.commit()


def list_webhooks() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM webhooks ORDER BY name").fetchall()]


def get_webhook(webhook_id: str) -> dict | None:
    row = _conn.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()
    return dict(row) if row else None


def update_webhook(webhook_id: str, fields: dict) -> dict | None:
    """Aktualisiert nur die übergebenen Felder eines Webhooks."""
    allowed = {"name", "url", "type", "enabled", "headers", "body_template"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        _conn.execute(f"UPDATE webhooks SET {set_clause} WHERE id = ?",
                      (*updates.values(), webhook_id))
        _conn.commit()
    return get_webhook(webhook_id)


def create_webhook(name, url, wtype, headers: str | None = None,
                   body_template: str | None = None) -> dict:
    wid = _new_id()
    _conn.execute(
        "INSERT INTO webhooks (id, name, url, type, headers, body_template, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (wid, name, url, wtype, headers, body_template, _now_ms()),
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

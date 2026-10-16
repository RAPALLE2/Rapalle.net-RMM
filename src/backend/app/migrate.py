"""
migrate.py
----------
Umzug einer kompletten RMM-Instanz auf einen anderen Server.

Was mitgenommen wird:
  * die gesamte Datenbank (data.sqlite) - also Clients, Benutzer, Gruppen und
    Rechte, Einstellungen, Dashboard-Widgets, Tickets, Notizen, Skripte,
    Zeitpläne, Audit-Log … einfach alles, was das RMM speichert
  * die Aufzeichnungen (recordings/) - die Replays der Fernsitzungen
  * die Medien-Bibliothek (media_files/) des Audio-Players
  * das Branding (branding/) - eigene Logos und Bilder
  * die MODULE: welche aktiv sind + ihre Einstellungen (data/modules.json) und
    ihre Daten (data/modules/: Spaces-Verzeichnis, Client-Geraete, Proxmox-
    Verbindungen, Notizen, Freigaben ...)
  * den SPEICHER (storage/): Spaces-Dateien, Deployment-Ordner, gebaute
    Client-Pakete
  * Geheimnisse NUR auf ausdrücklichen Wunsch: .env-Dateien (AGENT_TOKEN,
    JWT_SECRET) und die Verbindung des SQL-Moduls (dbconfig.json). Ohne sie
    muessen sich Agenten neu anmelden, und mit JWT_SECRET verschluesselte
    Modul-Daten (z.B. Proxmox-Zugaenge, API-Codes) sind auf dem Ziel nicht lesbar.
    Mit ihnen bleibt alles unveraendert erreichbar.

Warum eine eigene Datei und nicht einfach "Ordner kopieren":
Die Datenbank wird im laufenden Betrieb geschrieben. Ein simples cp kann
mitten in einer Transaktion erwischen und liefert eine beschädigte Datei.
Deshalb nutzen wir die eingebaute Backup-Schnittstelle von SQLite
(`Connection.backup`), die einen in sich stimmigen Stand zieht - ohne den
Betrieb anzuhalten.

Der Export ist ein ZIP mit einer manifest.json obenauf. Beim Einspielen wird
diese zuerst geprüft, damit niemand versehentlich ein beliebiges Archiv über
seine Installation kippt.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

from app.paths import (
    BACKEND_DIR, PROJECT_ROOT, DB_FILE, DATA_DIR, ENV_FILE, AGENT_DIR,
    RECORDINGS_DIR, AUDIO_DIR, BRANDING_DIR, BACKEND_VERSION_FILE,
    MODULES_STATE_FILE, MODULES_DATA_DIR, STORAGE_DIR, DBCONFIG_FILE,
)
MANIFEST_NAME = "manifest.json"
ARCHIVE_MARK = "rapalle-rmm-migration"

# Ordner, die mitwandern können. Schlüssel = Name im Archiv.
DATA_DIRS = {
    "recordings": RECORDINGS_DIR,
    "media_files": AUDIO_DIR,
    "branding": BRANDING_DIR,
    "module_data": MODULES_DATA_DIR,    # Daten der Module (Option "modules")
    "storage": STORAGE_DIR,             # Spaces, Deployment, Client-Pakete (Option "storage")
}
# Einzelne Dateien (Option "modules"): aktivierte Module + Modul-Einstellungen
DATA_FILES = {
    "modules_state.json": MODULES_STATE_FILE,
}
# Welche Option schaltet welchen Ordner/welche Datei?
OPTION_OF = {"recordings": "recordings", "media_files": "media_files", "branding": "branding",
             "module_data": "modules", "storage": "storage", "modules_state.json": "modules"}

# Geheimnisse - nur auf Wunsch, siehe Modul-Beschreibung.
SECRET_FILES = {
    "backend.env": ENV_FILE,
    "agent.env": AGENT_DIR / ".env",
    "dbconfig.json": DBCONFIG_FILE,      # Verbindung des SQL-Moduls (enthaelt Zugangsdaten)
}


def _modules_state() -> dict:
    try:
        d = json.loads(MODULES_STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _sql_external() -> bool:
    """Nutzt das SQL-Modul eine EXTERNE Datenbank? Dann liegen die Daten dort."""
    try:
        return json.loads(DBCONFIG_FILE.read_text(encoding="utf-8")).get("mode") == "external"
    except Exception:
        return False


# ==========================================================================
# Hilfsfunktionen
# ==========================================================================

def _dir_size(path: Path) -> tuple[int, int]:
    """(Anzahl Dateien, Gesamtgröße) eines Ordners - für die Vorschau."""
    count = size = 0
    if not path.is_dir():
        return 0, 0
    for f in path.rglob("*"):
        if f.is_file():
            count += 1
            try:
                size += f.stat().st_size
            except OSError:
                pass
    return count, size


def _version() -> str:
    try:
        return (BACKEND_VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return "unbekannt"


def _table_counts() -> dict:
    """Zeilenzahlen der wichtigsten Tabellen - Kontrolle vor und nach dem Umzug."""
    out: dict[str, int] = {}
    try:
        con = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
        try:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()]
            for n in names:
                try:
                    out[n] = con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
                except sqlite3.Error:
                    pass
        finally:
            con.close()
    except sqlite3.Error:
        pass
    return out


def preview() -> dict:
    """Was würde ein Export enthalten? Wird im Dashboard angezeigt."""
    counts = _table_counts()
    dirs = {}
    total = DB_FILE.stat().st_size if DB_FILE.is_file() else 0
    for name, path in DATA_DIRS.items():
        n, size = _dir_size(path)
        dirs[name] = {"files": n, "size": size}
        total += size
    ms = _modules_state()
    enabled = sorted(k for k, v in (ms.get("enabled") or {}).items() if v)
    return {
        "modules": {"enabled": enabled, "settings": len(ms.get("settings") or {}),
                    "state_file": MODULES_STATE_FILE.is_file()},
        "sql_external": _sql_external(),
        "version": _version(),
        "database": {
            "size": DB_FILE.stat().st_size if DB_FILE.is_file() else 0,
            "tables": len(counts),
            "rows": sum(counts.values()),
            "counts": {k: v for k, v in sorted(
                counts.items(), key=lambda kv: -kv[1])[:12]},
        },
        "dirs": dirs,
        "total_size": total,
        "has_backend_env": SECRET_FILES["backend.env"].is_file(),
    }


def _copy_database(dest: Path) -> None:
    """
    Stimmigen Abzug der Datenbank ziehen - im laufenden Betrieb.

    `Connection.backup` von SQLite kopiert seitenweise und beruecksichtigt
    laufende Transaktionen. Ein einfaches Dateikopieren waere hier riskant.
    """
    src = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


# ==========================================================================
# Export
# ==========================================================================

def build_export(target: Path, include_recordings: bool = True,
                 include_media: bool = True, include_branding: bool = True,
                 include_secrets: bool = False, include_modules: bool = True,
                 include_storage: bool = True) -> dict:
    """
    Schreibt das Umzugs-Archiv nach `target` und liefert das Manifest zurück.
    """
    wanted = {
        "recordings": include_recordings,
        "media_files": include_media,
        "branding": include_branding,
        "modules": include_modules,
        "storage": include_storage,
    }

    manifest = {
        "kind": ARCHIVE_MARK,
        "format": 2,                       # 2 = mit Modulen + Speicher
        "created_at": int(time.time()),
        "created_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": _version(),
        "hostname": os.getenv("HOSTNAME", ""),
        "includes": {k: bool(v) for k, v in wanted.items()},
        "includes_secrets": bool(include_secrets),
        "counts": _table_counts(),
        "modules_enabled": sorted(k for k, v in (_modules_state().get("enabled") or {}).items() if v)
                           if include_modules else [],
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = target.parent / f"data-export-{int(time.time())}.sqlite"
    _copy_database(tmp_db)

    try:
        # ZIP_DEFLATED: Datenbank und Logs schrumpfen deutlich; Videos in den
        # Aufzeichnungen sind bereits komprimiert, das kostet dort kaum Zeit.
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED,
                             allowZip64=True) as zf:
            zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
            zf.write(tmp_db, "data.sqlite")
            for name, path in DATA_DIRS.items():
                if not wanted.get(OPTION_OF[name]) or not path.is_dir():
                    continue
                for f in sorted(path.rglob("*")):
                    if f.is_file() and not f.name.endswith((".tmp", ".part")):
                        zf.write(f, f"{name}/{f.relative_to(path)}")
            for arc, path in DATA_FILES.items():
                if wanted.get(OPTION_OF[arc]) and path.is_file():
                    zf.write(path, f"files/{arc}")
            if include_secrets:
                for arc, path in SECRET_FILES.items():
                    if path.is_file():
                        zf.write(path, f"secrets/{arc}")
    finally:
        try:
            tmp_db.unlink()
        except OSError:
            pass

    manifest["archive_size"] = target.stat().st_size
    return manifest


# ==========================================================================
# Import
# ==========================================================================

def read_manifest(archive: Path) -> dict:
    """Manifest lesen und pruefen - VOR jeder Aenderung am Zielsystem."""
    if not zipfile.is_zipfile(archive):
        raise ValueError("Das ist kein ZIP-Archiv.")
    with zipfile.ZipFile(archive) as zf:
        if MANIFEST_NAME not in zf.namelist():
            raise ValueError("Im Archiv fehlt die manifest.json - das ist kein "
                             "Umzugs-Archiv dieser Anwendung.")
        data = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        if data.get("kind") != ARCHIVE_MARK:
            raise ValueError("Das Archiv gehoert nicht zu RAPALLE.net RMM.")
        if "data.sqlite" not in zf.namelist():
            raise ValueError("Im Archiv fehlt die Datenbank.")
    return data


def apply_import(archive: Path, restore_secrets: bool = False,
                 keep_backup: bool = True) -> dict:
    """
    Spielt ein Umzugs-Archiv ein.

    Reihenfolge mit Bedacht gewaehlt:
      1. Manifest pruefen (bricht ab, BEVOR etwas angefasst wird)
      2. Alles nach nebenan entpacken - schlaegt das fehl, ist der alte Stand
         noch unberuehrt
      3. Bestehenden Stand zur Seite legen (Sicherung mit Zeitstempel)
      4. Neuen Stand an seinen Platz schieben

    Das Backend muss danach neu starten: die Datenbankverbindung zeigt sonst
    weiter auf die alte, inzwischen ersetzte Datei.
    """
    manifest = read_manifest(archive)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = BACKEND_DIR / f".migrate-{stamp}"
    backup_dir = BACKEND_DIR / f"backup-vor-migration-{stamp}"
    report = {"manifest": manifest, "backup": None, "restored": []}

    # --- 2. Entpacken -----------------------------------------------------
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                # Kein Ausbrechen aus dem Zielordner (Zip-Slip).
                dest = (staging / info.filename).resolve()
                if staging.resolve() not in dest.parents and dest != staging.resolve():
                    raise ValueError(f"Unsicherer Pfad im Archiv: {info.filename}")
            zf.extractall(staging)

        new_db = staging / "data.sqlite"
        if not new_db.is_file():
            raise ValueError("Datenbank im Archiv nicht gefunden.")
        # Kurzer Funktionstest, bevor der alte Stand angefasst wird.
        con = sqlite3.connect(f"file:{new_db}?mode=ro", uri=True)
        try:
            con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        finally:
            con.close()

        # --- 3. Sicherung des aktuellen Standes ---------------------------
        if keep_backup:
            backup_dir.mkdir(parents=True, exist_ok=True)
            if DB_FILE.is_file():
                shutil.copy2(DB_FILE, backup_dir / "data.sqlite")
            for arc, path in DATA_FILES.items():
                if path.is_file():
                    shutil.copy2(path, backup_dir / arc)
            for name, path in DATA_DIRS.items():
                if path.is_dir() and any(path.iterdir()):
                    shutil.copytree(path, backup_dir / name, dirs_exist_ok=True)
            report["backup"] = str(backup_dir)

        # --- 4. Uebernehmen ------------------------------------------------
        shutil.copy2(new_db, DB_FILE)
        # WAL-/Journal-Reste der alten Datenbank entfernen, sonst mischt SQLite
        # den alten Schreibpuffer in die neue Datei.
        for suffix in ("-wal", "-shm", "-journal"):
            leftover = Path(str(DB_FILE) + suffix)
            if leftover.exists():
                leftover.unlink()
        report["restored"].append("data.sqlite")

        for name, path in DATA_DIRS.items():
            src = staging / name
            if not src.is_dir():
                continue
            # Zielordner ZUERST leeren. Sonst blieben die Dateien der alten
            # Instanz daneben liegen - die neue Datenbank kennt sie aber nicht
            # mehr. Ergebnis waeren verwaiste Aufzeichnungen und Medien, die
            # nur Platz kosten und beim Aufraeumen nie erfasst werden.
            # Gefahrlos, weil der alte Stand oben gesichert wurde (keep_backup);
            # ohne Sicherung wird bewusst NICHT geloescht.
            if keep_backup and path.is_dir():
                for old_entry in list(path.iterdir()):
                    try:
                        if old_entry.is_dir() and not old_entry.is_symlink():
                            shutil.rmtree(old_entry)
                        else:
                            old_entry.unlink()
                    except OSError:
                        pass
            path.mkdir(parents=True, exist_ok=True)
            for f in src.rglob("*"):
                rel = f.relative_to(src)
                if f.is_dir():
                    (path / rel).mkdir(parents=True, exist_ok=True)
                else:
                    (path / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, path / rel)
            report["restored"].append(name)

        for arc, path in DATA_FILES.items():
            src = staging / "files" / arc
            if src.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, path)
                report["restored"].append(arc)

        if restore_secrets:
            for arc, path in SECRET_FILES.items():
                src = staging / "secrets" / arc
                if src.is_file():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, path)
                    report["restored"].append(arc)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return report

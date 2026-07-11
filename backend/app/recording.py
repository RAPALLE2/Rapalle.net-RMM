"""
recording.py
------------
Nimmt Remote-Screen-Sessions auf. Statt eines echten Videos (das aufwendige
Kodierung bräuchte) speichern wir die einzelnen JPEG-Frames als "JSONL"-Datei:
eine Zeile pro Frame mit Zeitstempel + Base64-Bild. Das ist einfach zu
schreiben und beim Abspielen kann das Frontend die Frames einfach der Reihe
nach mit den passenden Zeitabständen anzeigen (wie ein Daumenkino).

Vorteile dieses Ansatzes:
- Kein Video-Codec/ffmpeg nötig (läuft überall)
- Sehr einfach abzuspielen und zu spulen
- Frames sind ohnehin schon als JPEG-Base64 vorhanden (vom Agent)

Aufbewahrung: siehe cleanup_old_recordings() - Standard 10 Tage.
"""

import json
import pathlib
import time

from app import db

# Ordner, in dem die Aufzeichnungs-Dateien liegen (neben der Datenbank)
RECORDINGS_DIR = pathlib.Path(__file__).resolve().parent.parent / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

# Aufbewahrungsdauer in Tagen
RETENTION_DAYS = 10


class ActiveRecording:
    """Hält eine gerade laufende Aufzeichnung (offene Datei + Zähler)."""

    def __init__(self, rec_id: str, file_handle, file_path: str):
        self.rec_id = rec_id
        self.file = file_handle
        self.file_path = file_path
        self.frame_count = 0
        self.start_time = time.time()


# client_id -> ActiveRecording (pro Client kann eine Aufzeichnung laufen)
_active: dict[str, ActiveRecording] = {}


def start_recording(client_id: str, client_hostname: str, username: str) -> str | None:
    """Beginnt eine Aufzeichnung für einen Client. Gibt die Recording-ID zurück."""
    if client_id in _active:
        return _active[client_id].rec_id  # läuft schon

    filename = f"{client_id}_{int(time.time())}.jsonl"
    file_path = RECORDINGS_DIR / filename
    fh = open(file_path, "w", encoding="utf-8")

    rec_id = db.create_recording(client_id, client_hostname, username, str(file_path))
    _active[client_id] = ActiveRecording(rec_id, fh, str(file_path))
    return rec_id


def record_frame(client_id: str, image_b64: str, width: int, height: int) -> None:
    """Schreibt einen Frame in die laufende Aufzeichnung (falls eine läuft)."""
    rec = _active.get(client_id)
    if not rec:
        return
    # Zeit-Offset seit Aufnahmebeginn (für korrektes Abspiel-Timing)
    offset_ms = int((time.time() - rec.start_time) * 1000)
    line = json.dumps({"t": offset_ms, "w": width, "h": height, "img": image_b64})
    rec.file.write(line + "\n")
    rec.frame_count += 1


def stop_recording(client_id: str) -> None:
    """Beendet die Aufzeichnung eines Clients und speichert die Metadaten."""
    rec = _active.pop(client_id, None)
    if not rec:
        return
    try:
        rec.file.close()
    except Exception:
        pass
    db.finish_recording(rec.rec_id, rec.frame_count)


def abort_recording(client_id: str, only_if_empty: bool = True) -> bool:
    """
    Bricht eine laufende Aufzeichnung ab und löscht sie SOFORT wieder
    (Datei + DB-Eintrag). Wird genutzt, wenn das Screen-Streaming mit einem
    Fehler startet (z.B. headless VM) - sonst bleibt ein leeres 0-Frame-Replay
    in der Liste liegen.

    only_if_empty=True (Standard): Nur löschen, wenn noch KEIN Frame
    aufgezeichnet wurde - eine Session mit echtem Bildmaterial wird bei einem
    späten Fehler nicht weggeworfen, sondern normal abgeschlossen.

    WICHTIG: Der Audit-Log-Eintrag (screen.session_started) wird bewusst
    NICHT angetastet - der Zugriffsversuch bleibt nachvollziehbar.
    """
    rec = _active.get(client_id)
    if not rec:
        return False
    if only_if_empty and rec.frame_count > 0:
        # Es gibt schon Bildmaterial -> regulär beenden statt löschen.
        stop_recording(client_id)
        return False
    _active.pop(client_id, None)
    try:
        rec.file.close()
    except Exception:
        pass
    try:
        pathlib.Path(rec.file_path).unlink(missing_ok=True)
    except Exception:
        pass
    db.delete_recording(rec.rec_id)
    return True


# ------------------------------------------------------------------
# TERMINAL-SITZUNGEN als Replay ("Screen-Recorder" fürs Terminal)
# ------------------------------------------------------------------
# Statt jeden Befehl/jede Ausgabe einzeln zu loggen, wird die GESAMTE
# PTY-Ausgabe einer Terminal-Sitzung mit Zeitstempeln mitgeschnitten
# (Format 'term': eine JSONL-Zeile pro Ausgabe-Stück {"t": ms, "d": text}).
# Der Recordings-Player spielt das wie ein Video über den Terminal-Emulator ab.
# Gekeyt wird pro SESSION (nicht pro Client), da mehrere Terminal-Fenster
# gleichzeitig auf demselben Client offen sein können.

_active_term: dict[str, ActiveRecording] = {}


def start_term_recording(session: str, client_id: str, client_hostname: str,
                         username: str) -> str | None:
    """Beginnt die Aufzeichnung einer Terminal-Sitzung. Gibt die Recording-ID zurück."""
    if session in _active_term:
        return _active_term[session].rec_id
    filename = f"{client_id}_{int(time.time())}.term.jsonl"
    file_path = RECORDINGS_DIR / filename
    fh = open(file_path, "w", encoding="utf-8")
    rec_id = db.create_recording(client_id, client_hostname, username, str(file_path),
                                 fmt="term")
    _active_term[session] = ActiveRecording(rec_id, fh, str(file_path))
    return rec_id


def record_term_data(session: str, data: str) -> None:
    """Schreibt ein Ausgabe-Stück der Shell in die laufende Terminal-Aufzeichnung."""
    rec = _active_term.get(session)
    if not rec or not data:
        return
    offset_ms = int((time.time() - rec.start_time) * 1000)
    rec.file.write(json.dumps({"t": offset_ms, "d": data}) + "\n")
    rec.frame_count += 1


def stop_term_recording(session: str) -> str | None:
    """Beendet die Terminal-Aufzeichnung. Leere Aufnahmen (0 Ausgaben) werden
    sofort wieder gelöscht. Gibt die Recording-ID zurück (oder None)."""
    rec = _active_term.pop(session, None)
    if not rec:
        return None
    try:
        rec.file.close()
    except Exception:
        pass
    if rec.frame_count == 0:
        try:
            pathlib.Path(rec.file_path).unlink(missing_ok=True)
        except Exception:
            pass
        db.delete_recording(rec.rec_id)
        return None
    db.finish_recording(rec.rec_id, rec.frame_count)
    return rec.rec_id


def cleanup_old_recordings() -> int:
    """
    Löscht Aufzeichnungen, die älter als RETENTION_DAYS sind (Datei + DB-Eintrag).
    Gibt die Anzahl gelöschter Aufzeichnungen zurück.
    """
    days = db.get_int_setting("replay_retention_days") or RETENTION_DAYS
    old = db.list_old_recordings(days)
    for rec in old:
        try:
            pathlib.Path(rec["file_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        db.delete_recording(rec["id"])
    return len(old)

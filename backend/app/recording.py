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

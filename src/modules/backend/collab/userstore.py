# userstore.py (Modul "collab")
# -----------------------------
# Kleiner JSON-Speicher pro Benutzer fuer Notizheft und Wecker:
#   backend/data/modules/collab/<bereich>/<user_id>.json
# Atomar geschrieben (tmp + replace), damit ein Absturz nichts zerstoert.
# Liegt im Daten-Ordner -> bleibt bei Neustart und Update erhalten.

import json
import threading
from pathlib import Path

from app.paths import MODULES_DATA_DIR

_lock = threading.RLock()


def _file(area: str, user_id: str) -> Path:
    safe = "".join(c for c in str(user_id) if c.isalnum() or c in "-_") or "unknown"
    d = MODULES_DATA_DIR / "collab" / area
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.json"


def load(area: str, user_id: str, default: dict) -> dict:
    with _lock:
        f = _file(area, user_id)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in default.items():
                    data.setdefault(k, v)
                return data
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"[collab] {f.name} unlesbar ({exc}) - starte leer")
        return json.loads(json.dumps(default))


def save(area: str, user_id: str, data: dict) -> None:
    with _lock:
        f = _file(area, user_id)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

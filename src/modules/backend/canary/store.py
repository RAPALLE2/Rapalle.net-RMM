# store.py (Modul "canary") - Ablage fuer IP-Tracker-Links und Canary-Tokens
# --------------------------------------------------------------------------
# data/modules/canary/data.json:
#   {"trackers": {id: {...}}, "tokens": {id: {...}}, "hits": [ {...}, ... ]}
# Treffer (hits) werden zusammen gehalten (einfaches, abfragbares Protokoll);
# die Liste ist gedeckelt (MAX_HITS, aelteste fliegen raus).
#
# Tracker (IP-Tracker): oeffentlicher Link -> sammelt Server-Infos -> leitet weiter.
#   {id, name, slug, redirect (Ziel-URL), created, created_by, enabled, count, last}
# Token (Canary): id, name, kind (web|dns|qr|email|file), slug/host/address,
#   note, created, created_by, enabled, count, last, (file_kind/file_name bei Dateien)

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path

from app.paths import MODULES_DATA_DIR

DIR = MODULES_DATA_DIR / "canary"
FILE = DIR / "data.json"
FILES_DIR = DIR / "files"           # erzeugte Datei-Tokens (zum Download)
MAX_HITS = 2000
_lock = threading.RLock()


def _load() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    for k in ("trackers", "tokens"):
        d.setdefault(k, {})
    d.setdefault("hits", [])
    return d


def _save(d: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def data() -> dict:
    return _load()


def new_id() -> str:
    return secrets.token_hex(6)


def new_slug() -> str:
    # URL-freundlich, schwer zu raten
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]


def new_token_hex() -> str:
    return secrets.token_hex(16)


def update(fn):
    with _lock:
        d = _load()
        res = fn(d)
        _save(d)
        return res


# ------------------------------------------------------------------ Tracker
def find_tracker_by_slug(slug: str) -> dict | None:
    for t in _load()["trackers"].values():
        if t.get("slug") == slug and t.get("enabled", True):
            return t
    return None


def get_tracker(tid: str) -> dict | None:
    return _load()["trackers"].get(tid)


# ------------------------------------------------------------------ Token
def find_token_by_slug(slug: str) -> dict | None:
    for t in _load()["tokens"].values():
        if t.get("slug") == slug and t.get("enabled", True):
            return t
    return None


def find_token_by_host(host: str) -> dict | None:
    host = (host or "").strip(".").lower()
    for t in _load()["tokens"].values():
        h = (t.get("host") or "").strip(".").lower()
        if h and t.get("enabled", True) and (host == h or host.endswith("." + h)):
            return t
    return None


def get_token(tid: str) -> dict | None:
    return _load()["tokens"].get(tid)


# ------------------------------------------------------------------ Treffer
def record_hit(kind: str, ref_id: str, name: str, info: dict) -> dict:
    """Treffer anfuegen + Zaehler/last am Objekt aktualisieren. -> hit."""
    hit = {"id": secrets.token_hex(8), "kind": kind, "ref": ref_id, "name": name,
           "at": int(time.time() * 1000), **info}
    with _lock:
        d = _load()
        bucket = d["trackers"] if kind == "tracker" else d["tokens"]
        obj = bucket.get(ref_id)
        if obj is not None:
            obj["count"] = int(obj.get("count", 0)) + 1
            obj["last"] = hit["at"]
        d["hits"].insert(0, hit)
        del d["hits"][MAX_HITS:]
        _save(d)
    return hit


def hits(ref_id: str | None = None, limit: int = 200) -> list:
    hs = _load()["hits"]
    if ref_id:
        hs = [h for h in hs if h.get("ref") == ref_id]
    return hs[:limit]


def clear_hits(ref_id: str | None = None) -> None:
    with _lock:
        d = _load()
        d["hits"] = [] if ref_id is None else [h for h in d["hits"] if h.get("ref") != ref_id]
        _save(d)

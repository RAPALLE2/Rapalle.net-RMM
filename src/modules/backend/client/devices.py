# devices.py (Modul "client")
# ---------------------------
# Geraete, auf denen die Client-App mit einem Benutzerkonto angemeldet ist.
#
# Ablauf ("Login speichern"):
#   1. Die App meldet sich EINMAL normal an (/api/auth/login, inkl. 2FA/SSO)
#      und registriert das Geraet -> bekommt einen GERAETETOKEN "<id>.<geheim>".
#   2. Den Token legt sie im sicheren Speicher des Systems ab (Windows-
#      Anmeldeinformationen, Linux-Schluesselbund, Android Keystore).
#   3. Bei jedem Start tauscht sie ihn gegen eine Sitzung (JWT mit "dev"-Claim).
#   4. "Entfernen" (Profil -> Meine Geraete) loescht das Geraet: Token und alle
#      Sitzungen dieses Geraets sind SOFORT ungueltig (app/auth.decode_access_token
#      fragt bei jedem Token mit "dev" hier nach).
#
# Gespeichert wird nur ein SHA-256 des Geheimnisses: data/modules/client/devices.json

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time

from app.paths import MODULES_DATA_DIR

FILE = MODULES_DATA_DIR / "client" / "devices.json"
_lock = threading.RLock()
_touch_cache: dict[str, float] = {}


def _now() -> int:
    return int(time.time() * 1000)


def _load() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("devices"), dict) else {"devices": {}}
    except FileNotFoundError:
        return {"devices": {}}
    except Exception as exc:
        print(f"[client] devices.json unlesbar ({exc}) - starte leer")
        return {"devices": {}}


def _save(d: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(FILE)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def public(dev: dict) -> dict:
    return {k: v for k, v in dev.items() if k != "secret_hash"}


def register(user: dict, info: dict, ip: str = "") -> tuple[dict, str]:
    dev_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    dev = {
        "id": dev_id, "user_id": user["id"], "username": user["username"],
        "name": (info.get("name") or "Gerät")[:80],
        "platform": (info.get("platform") or "unbekannt")[:20],
        "os": (info.get("os") or "")[:120],
        "app_version": (info.get("app_version") or "")[:30],
        "created": _now(), "last_seen": _now(), "last_ip": ip[:64],
        "secret_hash": _hash(secret),
    }
    with _lock:
        d = _load()
        d["devices"][dev_id] = dev
        _save(d)
    return dev, f"{dev_id}.{secret}"


def check_token(device_token: str) -> dict | None:
    """Geraetetoken pruefen -> Geraet oder None."""
    dev_id, _, secret = (device_token or "").partition(".")
    if not dev_id or not secret:
        return None
    with _lock:
        dev = _load()["devices"].get(dev_id)
    if not dev or not hmac.compare_digest(dev.get("secret_hash", ""), _hash(secret)):
        return None
    return dev


def is_active(dev_id: str, user_id: str) -> bool:
    """Wird bei JEDEM Token mit "dev"-Claim gefragt - deshalb schlank halten."""
    with _lock:
        dev = _load()["devices"].get(dev_id)
    return bool(dev and dev.get("user_id") == user_id)


def touch(dev_id: str, ip: str = "", app_version: str | None = None) -> None:
    """Zuletzt gesehen aktualisieren (hoechstens einmal pro Minute schreiben)."""
    now = time.time()
    if now - _touch_cache.get(dev_id, 0) < 60 and app_version is None:
        return
    _touch_cache[dev_id] = now
    with _lock:
        d = _load()
        dev = d["devices"].get(dev_id)
        if not dev:
            return
        dev["last_seen"] = _now()
        if ip:
            dev["last_ip"] = ip[:64]
        if app_version:
            dev["app_version"] = app_version[:30]
        _save(d)


def list_for(user_id: str | None = None) -> list[dict]:
    with _lock:
        devs = list(_load()["devices"].values())
    if user_id is not None:
        devs = [x for x in devs if x.get("user_id") == user_id]
    return sorted((public(x) for x in devs), key=lambda x: x.get("last_seen", 0), reverse=True)


def get(dev_id: str) -> dict | None:
    with _lock:
        dev = _load()["devices"].get(dev_id)
    return public(dev) if dev else None


def rename(dev_id: str, name: str) -> dict | None:
    with _lock:
        d = _load()
        dev = d["devices"].get(dev_id)
        if not dev:
            return None
        dev["name"] = name.strip()[:80] or dev["name"]
        _save(d)
        return public(dev)


def remove(dev_id: str) -> bool:
    with _lock:
        d = _load()
        if d["devices"].pop(dev_id, None) is None:
            return False
        _save(d)
    _touch_cache.pop(dev_id, None)
    return True


def remove_for_user(user_id: str) -> int:
    with _lock:
        d = _load()
        before = len(d["devices"])
        d["devices"] = {k: v for k, v in d["devices"].items() if v.get("user_id") != user_id}
        _save(d)
        return before - len(d["devices"])

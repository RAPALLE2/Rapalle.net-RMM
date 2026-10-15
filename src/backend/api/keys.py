# api/keys.py - API-Schluessel fuer API-Konten (Vorlage "API-Benutzer")
# ---------------------------------------------------------------------
# API-Konten haben KEIN Passwort und KEINE Erstanmeldung. Beim Anlegen erzeugt der
# Server einen API-Schluessel und zeigt ihn GENAU EINMAL an:
#
#   rmmak_<JWT>      JWT = {typ: "rmm-apikey", sub: <user_id>, jti: <schluessel-id>, rnd: <zufall>, iat}
#                    signiert (HS256) mit einem eigenen, aus dem Server-Geheimnis abgeleiteten Schluessel
#
# In der Datenbank (Tabelle api_keys) liegt nur SHA-256(Schluessel) + Schluessel-ID.
# Pruefung: Signatur -> Typ -> ID nachschlagen -> Hash vergleichen -> nicht gesperrt ->
#           Benutzer existiert, ist ein API-Konto und der Name passt.
# Pro Konto ist immer nur EIN Schluessel gueltig; "Neuer Schluessel" sperrt den alten sofort.
#
# ROTATION (Modul-Einstellungen "key_rotation" + "key_grace_minutes"):
#   Ist der Schluessel aelter als das Intervall (oder per "Alle Schluessel rotieren"
#   faellig gestellt), liefert die Anmeldung zusaetzlich einen NEUEN Schluessel
#   ("new_api_key"). Der alte bleibt noch die Uebergangszeit gueltig, damit das
#   Skript den neuen speichern kann - danach ist er ungueltig.
#
# Anmeldung an der API: POST /api/v1/auth/login {"username": "...", "api_key": "rmmak_..."}
# Danach gilt die eingestellte Betriebsart (Sitzung bzw. secure-Code) wie bei jedem Konto.

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import jwt
from fastapi import HTTPException

from app import db
from app.config import JWT_SECRET

PREFIX = "rmmak_"
API_ROLE = "api"


def _key() -> bytes:
    return hashlib.sha256(("rmm-api-key:" + JWT_SECRET).encode()).digest()


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def ensure_table() -> None:
    db._conn.execute("""CREATE TABLE IF NOT EXISTS api_keys (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, key_hash TEXT NOT NULL, created INTEGER,
        last_used INTEGER, last_ip TEXT, revoked INTEGER DEFAULT 0)""")
    db._conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id)")
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(api_keys)").fetchall()}
    if "rotate_due" not in cols:          # Rotation faellig gestellt (Knopf "Alle Schluessel rotieren")
        db._conn.execute("ALTER TABLE api_keys ADD COLUMN rotate_due INTEGER DEFAULT 0")
    if "valid_until" not in cols:         # abgeloester Schluessel: gilt nur noch bis hierhin (Uebergangszeit)
        db._conn.execute("ALTER TABLE api_keys ADD COLUMN valid_until INTEGER")
    db._conn.commit()


def _setting(key: str, default):
    try:
        from app import modules
        v = modules.setting("api", key, default)
        return default if v in (None, "") else v
    except Exception:
        return default


def is_api_account(user: dict | None) -> bool:
    return bool(user) and user.get("role") == API_ROLE


def generate(user: dict, keep_old_until: int | None = None) -> str:
    """Neuen Schluessel erzeugen. Klartext nur als Rueckgabe.
    keep_old_until=None -> alte Schluessel sofort gesperrt; sonst gelten sie noch bis dahin (Rotation)."""
    ensure_table()
    kid = secrets.token_hex(12)
    token = jwt.encode({"typ": "rmm-apikey", "sub": user["id"], "jti": kid, "rnd": secrets.token_urlsafe(32),
                        "iat": int(time.time())}, _key(), algorithm="HS256")
    key = PREFIX + token
    if keep_old_until:
        db._conn.execute("UPDATE api_keys SET valid_until = ?, rotate_due = 0 WHERE user_id = ? AND revoked = 0 "
                         "AND valid_until IS NULL", (keep_old_until, user["id"]))
    else:
        db._conn.execute("UPDATE api_keys SET revoked = 1 WHERE user_id = ? AND revoked = 0", (user["id"],))
    db._conn.execute("INSERT INTO api_keys (id,user_id,key_hash,created,last_used,last_ip,revoked) VALUES (?,?,?,?,?,?,0)",
                     (kid, user["id"], _sha(key), int(time.time()), None, ""))
    db._conn.commit()
    return key


def revoke_user(user_id: str) -> int:
    ensure_table()
    cur = db._conn.execute("UPDATE api_keys SET revoked = 1 WHERE user_id = ? AND revoked = 0", (user_id,))
    db._conn.commit()
    return cur.rowcount


def verify(username: str, key: str, ip: str = "") -> dict:
    """-> Benutzer. Jeder Fehler ergibt dieselbe allgemeine Meldung (kein Ausforschen)."""
    return verify_with_row(username, key, ip)[0]


def verify_with_row(username: str, key: str, ip: str = "") -> tuple[dict, dict]:
    """-> (Benutzer, Schluessel-Datensatz)."""
    fail = HTTPException(401, "Anmeldung fehlgeschlagen")
    key = (key or "").strip()
    if not key.startswith(PREFIX):
        raise fail
    try:
        payload = jwt.decode(key[len(PREFIX):], _key(), algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise fail
    if payload.get("typ") != "rmm-apikey":
        raise fail
    ensure_table()
    row = db._conn.execute("SELECT * FROM api_keys WHERE id = ?", (payload.get("jti", ""),)).fetchone()
    if not row or row["revoked"] or not hmac.compare_digest(row["key_hash"], _sha(key)):
        raise fail
    if row["valid_until"] and row["valid_until"] <= int(time.time()):    # abgeloest, Uebergangszeit vorbei
        raise fail
    user = db.get_user_by_id(row["user_id"])
    if (not user or user["id"] != payload.get("sub") or not is_api_account(user)
            or user["username"].lower() != (username or "").strip().lower()):
        raise fail
    db._conn.execute("UPDATE api_keys SET last_used = ?, last_ip = ? WHERE id = ?", (int(time.time()), ip, row["id"]))
    db._conn.commit()
    return user, dict(row)


# ------------------------------------------------------------------ Rotation
def rotation_interval() -> int | None:
    from .store import LIFETIMES
    return LIFETIMES.get(str(_setting("key_rotation", "never")), None)


def grace_seconds() -> int:
    try:
        return max(0, int(_setting("key_grace_minutes", 60))) * 60
    except (TypeError, ValueError):
        return 3600


def rotation_due(row: dict) -> bool:
    """Muss dieser (gueltige, noch nicht abgeloeste) Schluessel jetzt rotiert werden?"""
    if row.get("valid_until"):                 # wurde schon abgeloest -> neuer Schluessel existiert
        return False
    if row.get("rotate_due"):
        return True
    iv = rotation_interval()
    return bool(iv) and int(time.time()) - int(row.get("created") or 0) >= iv


def may_rotate(user: dict) -> bool:
    return is_api_account(user)


def rotate_on_login(user: dict) -> tuple[str, int]:
    """Neuen Schluessel ausgeben; der alte gilt noch die Uebergangszeit. -> (neuer Schluessel, alter gilt bis)"""
    grace = grace_seconds()
    if grace <= 0:                       # keine Uebergangszeit: alter Schluessel sofort ungueltig
        return generate(user), int(time.time())
    until = int(time.time()) + grace
    return generate(user, keep_old_until=until), until


def force_rotation_all() -> int:
    """Alle aktiven Schluessel faellig stellen: neuer Schluessel bei der naechsten Anmeldung."""
    ensure_table()
    cur = db._conn.execute("UPDATE api_keys SET rotate_due = 1 WHERE revoked = 0 AND valid_until IS NULL")
    db._conn.commit()
    return cur.rowcount


def accounts() -> list[dict]:
    """Alle API-Konten mit Stand ihres Schluessels."""
    ensure_table()
    out = []
    for u in db.list_users():
        if not is_api_account(u):
            continue
        k = db._conn.execute("SELECT * FROM api_keys WHERE user_id = ? AND revoked = 0 AND valid_until IS NULL "
                             "ORDER BY created DESC LIMIT 1", (u["id"],)).fetchone()
        k = dict(k) if k else None
        iv = rotation_interval()
        nxt = None
        if k:
            nxt = "beim nächsten Login" if rotation_due(k) else (k["created"] + iv if iv else None)
        out.append({"id": u["id"], "username": u["username"], "display_name": u.get("display_name") or "",
                    "key_active": bool(k), "key_created": k["created"] if k else None,
                    "key_last_used": k["last_used"] if k else None, "key_last_ip": k["last_ip"] if k else "",
                    "next_rotation": nxt})
    return sorted(out, key=lambda x: x["username"].lower())

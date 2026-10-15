# api/store.py - Datenbank-Teil der REST-API
# -------------------------------------------
# Tabellen (in der normalen RMM-Datenbank, werden beim Modulstart angelegt):
#   api_tokens  Anmeldungen an der API: "secure"-Tokens und "session"-Sitzungen
#               (gespeichert wird NUR ein SHA-256 des Codes, nie der Code selbst)
#   api_log     Protokoll aller API-Aufrufe (Wer, Was, Ergebnis, Dauer, IP)
#
# Einstellungen kommen aus dem Modul "api" (Modul-App -> Einstellungen):
#   mode            open | user | secure
#   token_lifetime  1h | 8h | 24h | 7d | 35d | 1y | never     (nur secure)
#   session_minutes Leerlauf-Zeit einer Sitzung im Modus "user"
#   service_user    Benutzer fuer den Modus "open" (ohne Anmeldung)
#   allow_ips       erlaubte Adressen/Netze (leer = alle), z.B. "10.0.0.0/8, 192.168.1.5"
#   log_days        Aufbewahrung des Protokolls in Tagen

from __future__ import annotations

import hashlib
import ipaddress
import time

from app import db

LIFETIMES = {"1h": 3600, "8h": 8 * 3600, "24h": 86400, "7d": 7 * 86400, "35d": 35 * 86400,
             "1y": 365 * 86400, "never": None}
DEFAULTS = {"mode": "secure", "token_lifetime": "8h", "session_minutes": 60, "service_user": "",
            "allow_ips": "", "log_days": 30}
MAX_LOG_ROWS = 200_000


def now() -> int:
    return int(time.time())


def setting(key: str):
    try:
        from app import modules
        v = modules.setting("api", key, DEFAULTS.get(key))
    except Exception:
        v = DEFAULTS.get(key)
    return DEFAULTS.get(key) if v in (None, "") and key in ("mode", "token_lifetime") else v


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ------------------------------------------------------------------ Tabellen
def ensure_tables() -> None:
    db._conn.execute("""CREATE TABLE IF NOT EXISTS api_tokens (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, username TEXT, kind TEXT NOT NULL,
        token_hash TEXT NOT NULL, created INTEGER, expires INTEGER, last_used INTEGER,
        last_ip TEXT, label TEXT, revoked INTEGER DEFAULT 0, idle INTEGER)""")
    db._conn.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id)")
    db._conn.execute("""CREATE TABLE IF NOT EXISTS api_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, mode TEXT, username TEXT, method TEXT,
        path TEXT, status INTEGER, ms INTEGER, ip TEXT, token_id TEXT, detail TEXT)""")
    db._conn.execute("CREATE INDEX IF NOT EXISTS idx_api_log_ts ON api_log(ts)")
    db._conn.commit()


# ------------------------------------------------------------------ Tokens / Sitzungen
def add_token(tid: str, user: dict, kind: str, token_hash: str, expires: int | None,
              ip: str, label: str = "", idle: int | None = None) -> None:
    db._conn.execute(
        "INSERT INTO api_tokens (id,user_id,username,kind,token_hash,created,expires,last_used,last_ip,label,revoked,idle)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,0,?)",
        (tid, user["id"], user["username"], kind, token_hash, now(), expires, now(), ip, label[:80], idle))
    db._conn.commit()


def get_token(tid: str) -> dict | None:
    r = db._conn.execute("SELECT * FROM api_tokens WHERE id = ?", (tid,)).fetchone()
    return dict(r) if r else None


def find_by_hash(token_hash: str) -> dict | None:
    r = db._conn.execute("SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash,)).fetchone()
    return dict(r) if r else None


def touch_token(tid: str, ip: str, expires: int | None = None) -> None:
    if expires is None:
        db._conn.execute("UPDATE api_tokens SET last_used = ?, last_ip = ? WHERE id = ?", (now(), ip, tid))
    else:
        db._conn.execute("UPDATE api_tokens SET last_used = ?, last_ip = ?, expires = ? WHERE id = ?",
                         (now(), ip, expires, tid))
    db._conn.commit()


def revoke(tid: str) -> bool:
    cur = db._conn.execute("UPDATE api_tokens SET revoked = 1 WHERE id = ? AND revoked = 0", (tid,))
    db._conn.commit()
    return cur.rowcount > 0


def revoke_all(user_id: str | None = None) -> int:
    if user_id:
        cur = db._conn.execute("UPDATE api_tokens SET revoked = 1 WHERE revoked = 0 AND user_id = ?", (user_id,))
    else:
        cur = db._conn.execute("UPDATE api_tokens SET revoked = 1 WHERE revoked = 0")
    db._conn.commit()
    return cur.rowcount


def list_tokens(include_old: bool = False) -> list[dict]:
    q = "SELECT id,user_id,username,kind,created,expires,last_used,last_ip,label,revoked FROM api_tokens"
    if not include_old:
        q += " WHERE revoked = 0 AND (expires IS NULL OR expires > ?)"
        rows = db._conn.execute(q + " ORDER BY last_used DESC LIMIT 500", (now(),)).fetchall()
    else:
        rows = db._conn.execute(q + " ORDER BY created DESC LIMIT 500").fetchall()
    return [dict(r) for r in rows]


def purge_tokens() -> None:
    """Abgelaufene/widerrufene Eintraege nach 30 Tagen endgueltig entfernen."""
    old = now() - 30 * 86400
    db._conn.execute("DELETE FROM api_tokens WHERE (revoked = 1 AND last_used < ?) OR (expires IS NOT NULL AND expires < ?)",
                     (old, old))
    db._conn.commit()


# ------------------------------------------------------------------ Protokoll
def log_call(*, mode: str, username: str, method: str, path: str, status: int, ms: int, ip: str,
             token_id: str = "", detail: str = "") -> None:
    try:
        db._conn.execute(
            "INSERT INTO api_log (ts,mode,username,method,path,status,ms,ip,token_id,detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (now(), mode, username or "", method, path[:500], status, ms, ip, token_id or "", (detail or "")[:300]))
        db._conn.commit()
    except Exception as exc:          # Protokoll darf die API nie stoeren
        print(f"[api] Protokoll fehlgeschlagen: {exc}")


def query_log(*, limit: int = 200, before: int | None = None, username: str = "", status: str = "",
              path: str = "") -> list[dict]:
    q, args = "SELECT * FROM api_log WHERE 1=1", []
    if before:
        q += " AND id < ?"; args.append(before)
    if username:
        q += " AND username = ?"; args.append(username)
    if status == "error":
        q += " AND status >= 400"
    elif status == "ok":
        q += " AND status < 400"
    if path:
        q += " AND path LIKE ?"; args.append(f"%{path}%")
    q += " ORDER BY id DESC LIMIT ?"; args.append(max(1, min(limit, 1000)))
    return [dict(r) for r in db._conn.execute(q, args).fetchall()]


def prune_log() -> int:
    days = int(setting("log_days") or 30)
    cur = db._conn.execute("DELETE FROM api_log WHERE ts < ?", (now() - days * 86400,))
    n = cur.rowcount
    row = db._conn.execute("SELECT COUNT(*) AS c FROM api_log").fetchone()
    if row and row["c"] > MAX_LOG_ROWS:
        db._conn.execute("DELETE FROM api_log WHERE id IN (SELECT id FROM api_log ORDER BY id ASC LIMIT ?)",
                         (row["c"] - MAX_LOG_ROWS,))
    db._conn.commit()
    return n


# ------------------------------------------------------------------ IP-Freigabe
def ip_allowed(ip: str) -> bool:
    raw = (setting("allow_ips") or "").strip()
    if not raw:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if addr in ipaddress.ip_network(part, strict=False):
                return True
        except ValueError:
            continue
    return False

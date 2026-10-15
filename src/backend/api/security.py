# api/security.py - Anmeldung an der REST-API
# --------------------------------------------
# Drei Betriebsarten (Modul-Einstellung "mode"):
#
#   open    "ohne Benutzer"  - keine Anmeldung. Jeder Aufruf laeuft als der in den
#                              Einstellungen gewaehlte API-Benutzer (mit dessen Rechten).
#                              Nur sinnvoll zusammen mit einer IP-Freigabeliste!
#   user    "mit Benutzer"   - POST /api/v1/auth/login (Benutzer + Passwort [+ 2FA]) oeffnet
#                              eine Sitzung (Cookie "rmm_api_session" oder Header X-API-Session).
#                              Sie laeuft nach "session_minutes" ohne Aufruf ab.
#   secure  "mit Benutzer (secure)" - Login liefert einen langen Code. Er muss bei JEDER
#                              Anfrage mitgeschickt werden (Authorization: Bearer <code> oder
#                              X-API-Token). Laufzeit: Einstellung "token_lifetime".
#
# So entsteht der secure-Code:
#   1. zufaelliger Geheimwert (48 Byte)
#   2. JWT mit Token-ID, Benutzer, Geheimwert und Ablauf - signiert mit eigenem API-Schluessel
#   3. das JWT wird verschluesselt (Fernet/AES) -> das ist der Code fuer den Aufrufer
#   4. in der Datenbank liegt nur SHA-256(Code) + Token-ID
# Pruefen: entschluesseln -> Signatur + Ablauf pruefen -> Token-ID nachschlagen ->
#          Hash vergleichen -> nicht widerrufen -> Benutzer existiert + hat "use_api".
# "Alle Tokens ablaufen lassen" (Modul-Einstellungen) widerruft sofort alles.

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

import jwt
from fastapi import HTTPException, Request

from app import db
from app.auth import user_has_permission
from app.config import JWT_SECRET

from . import store

SESSION_COOKIE = "rmm_api_session"


def _api_key() -> bytes:
    return hashlib.sha256(("rmm-api-jwt:" + JWT_SECRET).encode()).digest()


def _fernet():
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(("rmm-api-enc:" + JWT_SECRET).encode()).digest())
    return Fernet(key)


def client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def may_use_api(user: dict) -> bool:
    from app.auth import is_super_admin
    return is_super_admin(user) or user_has_permission(user, "use_api")


# ------------------------------------------------------------------ secure
def issue_secure_token(user: dict, ip: str, label: str = "") -> dict:
    life = store.LIFETIMES.get(store.setting("token_lifetime") or "8h", 8 * 3600)
    tid = secrets.token_hex(12)
    payload = {"jti": tid, "sub": user["id"], "rnd": secrets.token_urlsafe(48), "typ": "rmm-api"}
    exp = None
    if life:
        exp = store.now() + life
        payload["exp"] = exp
    signed = jwt.encode(payload, _api_key(), algorithm="HS256")
    code = _fernet().encrypt(signed.encode()).decode()
    store.add_token(tid, user, "secure", store.sha(code), exp, ip, label)
    return {"token": code, "token_id": tid, "expires_at": exp, "lifetime": store.setting("token_lifetime") or "8h"}


def verify_secure_token(code: str) -> tuple[dict, dict]:
    """-> (user, token_row). Wirft HTTPException(401) bei jedem Fehler."""
    from cryptography.fernet import InvalidToken
    try:
        signed = _fernet().decrypt(code.encode()).decode()
    except (InvalidToken, ValueError):
        raise HTTPException(401, "API-Code ungültig")
    try:
        payload = jwt.decode(signed, _api_key(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "API-Code abgelaufen - bitte neu anmelden")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "API-Code ungültig")
    row = store.get_token(payload.get("jti", ""))
    if not row or row["kind"] != "secure" or not hmac.compare_digest(row["token_hash"], store.sha(code)):
        raise HTTPException(401, "API-Code unbekannt")
    if row["revoked"]:
        raise HTTPException(401, "API-Code wurde widerrufen")
    if row["expires"] and row["expires"] < store.now():
        raise HTTPException(401, "API-Code abgelaufen - bitte neu anmelden")
    user = db.get_user_by_id(row["user_id"])
    if not user or user["id"] != payload.get("sub"):
        raise HTTPException(401, "Benutzer existiert nicht mehr")
    return user, row


# ------------------------------------------------------------------ user (Sitzung)
def open_session(user: dict, ip: str) -> dict:
    minutes = int(store.setting("session_minutes") or 60)
    sid = secrets.token_urlsafe(32)
    tid = secrets.token_hex(12)
    store.add_token(tid, user, "session", store.sha(sid), store.now() + minutes * 60, ip, idle=minutes * 60)
    return {"session": sid, "token_id": tid, "idle_minutes": minutes}


def verify_session(sid: str, ip: str) -> tuple[dict, dict]:
    row = store.find_by_hash(store.sha(sid))
    if not row or row["kind"] != "session" or row["revoked"]:
        raise HTTPException(401, "Keine gültige API-Sitzung - bitte anmelden (POST /api/v1/auth/login)")
    if row["expires"] and row["expires"] < store.now():
        raise HTTPException(401, "API-Sitzung abgelaufen - bitte neu anmelden")
    user = db.get_user_by_id(row["user_id"])
    if not user:
        raise HTTPException(401, "Benutzer existiert nicht mehr")
    store.touch_token(row["id"], ip, store.now() + int(row.get("idle") or 3600))   # Leerlauf verlaengern
    return user, row


# ------------------------------------------------------------------ pro Anfrage
def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def resolve(request: Request) -> dict:
    """Ermittelt den API-Benutzer fuer diese Anfrage (je nach Betriebsart).
    Legt request.state.api_user / api_token_id / api_mode ab."""
    mode = store.setting("mode") or "secure"
    ip = client_ip(request)
    request.state.api_mode = mode
    if not store.ip_allowed(ip):
        raise HTTPException(403, f"Zugriff von {ip} ist nicht freigegeben")
    token_id = ""
    if mode == "open":
        name = (store.setting("service_user") or "").strip()
        user = db.get_user_by_username(name) if name else None
        if not user:
            raise HTTPException(503, "Modus „ohne Benutzer“: In den API-Einstellungen ist kein API-Benutzer gewählt")
    elif mode == "user":
        sid = request.headers.get("x-api-session") or request.cookies.get(SESSION_COOKIE) or ""
        if not sid:
            raise HTTPException(401, "Bitte zuerst anmelden: POST /api/v1/auth/login")
        user, row = verify_session(sid, ip)
        token_id = row["id"]
    else:
        code = request.headers.get("x-api-token") or _bearer(request)
        if not code:
            raise HTTPException(401, "API-Code fehlt (Authorization: Bearer <code>) - Login: POST /api/v1/auth/login")
        user, row = verify_secure_token(code)
        store.touch_token(row["id"], ip)
        token_id = row["id"]
    request.state.api_user = user            # auch bei Ablehnung im Protokoll sichtbar
    request.state.api_token_id = token_id
    if not may_use_api(user):
        raise HTTPException(403, "Fehlendes Recht: use_api (REST-API benutzen)")
    return user

"""
auth.py
-------
Alles rund um Login-Sicherheit:

1. Passwörter werden NIE im Klartext gespeichert, sondern als "Hash"
   (eine Einweg-Verschlüsselung) über die Bibliothek "passlib".
2. Nach dem Login bekommt das Frontend ein JWT ("JSON Web Token") - das ist
   ein signiertes Text-Token, das Benutzername + Ablaufzeit enthält. Das
   Frontend schickt dieses Token bei jeder weiteren Anfrage mit
   (Header "Authorization: Bearer <token>"), damit das Backend weiß, wer
   die Anfrage stellt, ohne bei jeder Anfrage erneut das Passwort zu prüfen.

SSO / Verzeichnis-Anmeldung (Active Directory via LDAP/LDAPS):
   Neben der lokalen Anmeldung (authenticate_local) gibt es die Verzeichnis-
   Anmeldung (authenticate_realm) im MODUL "sso". Ist es aus, koennen sich
   AD-Benutzer nicht anmelden und ihre Sitzungen werden abgewiesen. Beide liefern
   am Ende dasselbe: ein fertiges User-Dict. Ab da läuft alles (JWT erzeugen,
   Berechtigungen prüfen) über denselben Code - egal ob lokal oder per AD
   angemeldet. AD-Benutzer bekommen ihre Rechte über RMM-Gruppen, die genauso
   heißen wie ihre AD-Gruppen (siehe sync_ad_user_groups in db.py).
"""

import time
import bcrypt
import jwt
from fastapi import Header, HTTPException

from app.config import JWT_SECRET, JWT_EXPIRE_HOURS
from app import db

# Hinweis: Wir nutzen die "bcrypt"-Bibliothek direkt (statt passlib).
# bcrypt hat ein technisches Limit von 72 Bytes pro Passwort - längere
# Passwörter werden von uns vorher sauber auf 72 Bytes gekürzt, damit es
# keine Abstürze gibt (das ist bei bcrypt üblich und völlig unbedenklich,
# da 72 Zeichen für ein Passwort mehr als genug Sicherheit bieten).


def _to_bytes(password: str) -> bytes:
    """Wandelt das Passwort in Bytes um und kürzt auf max. 72 Bytes (bcrypt-Limit)."""
    return password.encode("utf-8")[:72]


def hash_password(plain_password: str) -> str:
    """Erzeugt einen sicheren Hash aus einem Klartext-Passwort."""
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(_to_bytes(plain_password), salt)
    return hashed.decode("utf-8")  # als Text speichern, damit es in die DB passt


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Prüft, ob ein Klartext-Passwort zu einem gespeicherten Hash passt."""
    try:
        return bcrypt.checkpw(_to_bytes(plain_password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def authenticate_local(username: str, password: str) -> dict | None:
    """
    Prüft Benutzername/Passwort gegen die lokale Datenbank.
    Gibt das User-Dict zurück bei Erfolg, sonst None.
    """
    user = db.get_user_by_username(username)
    if not user:
        return None
    # API-Konten (Modul "api") melden sich NUR mit API-Schluessel an der REST-API an -
    # nie mit Passwort, nie im Dashboard. Allgemeine Meldung (kein Ausforschen).
    if user.get("role") == "api":
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


# Verzeichnis-Anmeldung (LDAP/AD): Modul "sso" (src/modules/backend/sso/ldap_auth.py).

def sso_enabled() -> bool:
    """Ist das Modul "sso" aktiv? (lazy import - modules.py importiert selbst app.*)"""
    try:
        from app import modules
        return modules.is_enabled("sso")
    except Exception:
        return False


def authenticate_realm(username: str, password: str, realm_id: str) -> dict | None:
    """Anmeldung gegen ein Verzeichnis - nur mit aktivem Modul "sso"."""
    from app import modules
    ldap = modules.use("sso", "ldap_auth") if sso_enabled() else None
    if ldap is None:
        raise HTTPException(403, "Die Verzeichnis-Anmeldung (SSO) ist deaktiviert. "
                                 "Bitte mit einem lokalen Konto anmelden.")
    return ldap.authenticate_realm(username, password, realm_id)


def create_access_token(user: dict) -> str:
    """Erstellt ein signiertes JWT für einen eingeloggten Benutzer."""
    payload = {
        "sub": user["id"],              # "subject" = Benutzer-ID
        "username": user["username"],
        "role": user["role"],
        "exp": int(time.time()) + JWT_EXPIRE_HOURS * 3600,  # Ablaufzeit
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Prüft ein JWT auf Gültigkeit und gibt seinen Inhalt zurück."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Sitzung abgelaufen, bitte erneut anmelden")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Ungültiges Token")
    # Sitzung einer Client-App (Modul "client"): Das Geraet muss noch registriert
    # sein. "Entfernen" unter Profil -> Meine Geraete wirkt dadurch sofort -
    # fuer REST UND die Live-Verbindung (beide pruefen hier).
    dev = payload.get("dev")
    if dev:
        try:
            from app import modules
            devices = modules.use("client", "devices") if modules.is_enabled("client") else None
        except Exception:
            devices = None
        if devices is None or not devices.is_active(dev, payload.get("sub")):
            raise HTTPException(401, "Dieses Gerät wurde abgemeldet")
    return payload


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """
    FastAPI-"Dependency": in jede geschützte Route einfach als Parameter
    einbauen, z.B.:

        @router.get("/etwas-geschütztes")
        async def route(user: dict = Depends(get_current_user)):
            ...

    FastAPI ruft diese Funktion automatisch auf, liest den Authorization-
    Header aus, prüft das Token, und übergibt den passenden User an die Route.
    Ist kein gültiges Token vorhanden, wird automatisch ein 401-Fehler
    zurückgegeben, BEVOR der eigentliche Routen-Code überhaupt läuft.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Nicht angemeldet")
    token = authorization.removeprefix("Bearer ").strip()
    payload = decode_access_token(token)
    user = db.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(401, "Benutzer existiert nicht mehr")
    # Verzeichnis-Konten (AD) brauchen das Modul "sso". Ist es aus, gelten auch
    # bereits ausgestellte Sitzungen nicht mehr - das Konto selbst bleibt bestehen.
    try:
        realm = user["auth_realm"] if "auth_realm" in user.keys() else None
    except Exception:
        realm = None
    if realm and not sso_enabled():
        raise HTTPException(401, "Die Verzeichnis-Anmeldung (SSO) ist deaktiviert.")
    return user


def require_admin(user: dict) -> None:
    """Kleine Hilfsfunktion: wirft einen Fehler, wenn der User kein Admin ist."""
    if user["role"] != "admin":
        raise HTTPException(403, "Nur für Administratoren")


def require_permission(user: dict, permission: str) -> None:
    """
    Wirft einen 403-Fehler, wenn der Benutzer das angegebene Recht nicht hat.
    Admins haben immer alle Rechte. Ansonsten wird über die Gruppen des
    Benutzers geprüft (siehe db.get_user_permissions).
    """
    perms = db.get_user_permissions(user["id"], user["role"])
    if permission not in perms:
        raise HTTPException(403, f"Fehlendes Recht: {permission}")


def get_user_with_permissions(user: dict) -> dict:
    """Ergänzt ein User-Dict um die Liste seiner effektiven Rechte (für das Frontend)."""
    perms = db.get_user_permissions(user["id"], user["role"])
    return {**user, "permissions": sorted(perms)}


# ==================================================================
# NEUES, feingranulares Rechte-System (tri-state Grants)
# ==================================================================
# Auflösung für ein (perm, client_id):
#   1. spezifisches Recht 'deny' (global ODER client)  -> verboten
#   2. 'admin'-Wildcard 'deny' (global/client)          -> verboten
#   3. spezifisches Recht 'allow'                       -> erlaubt
#   4. 'admin'-Wildcard 'allow'                         -> erlaubt
#   5. sonst                                            -> verboten (default-deny)
# Der lokale Super-Admin (role == 'admin') hat IMMER alle Rechte.


def is_super_admin(user: dict) -> bool:
    """
    Voll-Administrator - darf alles (Bypass des Resolvers). Wege:
      1. Rolle 'admin' (wie bisher)
      2. das globale Recht 'admin' (Wildcard, über die Rechte-Verwaltung
         vergeben - auch an Gruppen)
      3. der Alt-Schlüssel 'super_admin' (früher ein zweites, gleichwertiges
         Häkchen; beide sind zu 'admin' zusammengefasst, bereits vergebene
         Grants gelten weiter)
    Ein ausdrückliches 'deny' entzieht das Recht wieder - die Rolle 'admin'
    bleibt davon aber unberührt.
    """
    try:
        role = user.get("role")
    except AttributeError:
        role = user["role"]
    if role == "admin":
        return True
    # Rekursionsschutz: _has_perm() fragt is_super_admin() NICHT für diese Keys.
    return (_has_perm_raw(user, "admin", None)
            or _has_perm_raw(user, "super_admin", None))


def _aggregate_effect(grants: list[dict], perm: str, scopes: list[str]) -> str | None:
    """
    Fasst alle Grants für 'perm' in den angegebenen Scopes zusammen.
    'deny' schlägt 'allow'; ohne Treffer -> None.
    """
    found_allow = False
    for g in grants:
        if g["perm"] == perm and g["scope"] in scopes:
            if g["effect"] == "deny":
                return "deny"
            if g["effect"] == "allow":
                found_allow = True
    return "allow" if found_allow else None


def _resolve(grants: list[dict], perm: str, client_id: str | None) -> bool:
    # Scopes einer Client-Prüfung:
    #   global                    -> gilt überall
    #   DEFAULT_CLIENT_SCOPE ("*") -> "Standard-Client": gilt für JEDEN Client
    #   client_id                 -> Rechte genau dieses Clients
    # 'deny' schlägt in JEDEM dieser Scopes ein 'allow'. Ein Verbot auf einem
    # konkreten Client sticht also die Erlaubnis aus dem Standard-Client.
    scopes = ["global"]
    if client_id:
        scopes += [db.DEFAULT_CLIENT_SCOPE, client_id]
    # 1./3. spezifisches Recht. 'deny' auf 'perm' selbst schlägt alles.
    specific = _aggregate_effect(grants, perm, scopes)
    if specific == "deny":
        return False
    # Implikationen: ein 'allow' auf einem stärkeren/älteren Key erfüllt 'perm'
    # automatisch (z.B. alt use_screen -> c_screen/c_screen_view).
    implied_allow = False
    for src in db.perms_implied_by(perm):
        if _aggregate_effect(grants, src, scopes) == "allow":
            implied_allow = True
            break
    # 2./4. admin-Wildcard
    admin = _aggregate_effect(grants, "admin", scopes)
    if admin == "deny":
        return False
    if specific == "allow":
        return True
    if implied_allow:
        return True
    if admin == "allow":
        return True
    return False


def _has_perm_raw(user: dict, perm: str, client_id: str | None = None) -> bool:
    """Reine Grant-Auflösung OHNE Super-Admin-Bypass.
    Nur für is_super_admin() selbst - sonst gäbe es eine Endlosschleife
    (is_super_admin -> user_has_permission -> is_super_admin -> ...)."""
    try:
        return _resolve(db.get_effective_grants(user["id"]), perm, client_id)
    except Exception:
        return False


def user_has_permission(user: dict, perm: str, client_id: str | None = None) -> bool:
    """
    Effektive Rechteprüfung. Bei client_id werden client- UND globale Grants
    berücksichtigt. Client-Aktionen sind zusätzlich durch 'access_clients'
    gegated (ohne Zugriff auf den Client -> alles verboten).
    """
    if is_super_admin(user):
        return True
    grants = db.get_effective_grants(user["id"])
    # Gate: für client-bezogene Aktionen muss der Client überhaupt zugänglich
    # sein (access_clients auf global ODER auf diesem Client).
    if client_id and perm != "access_clients":
        if not _resolve(grants, "access_clients", client_id):
            return False
    return _resolve(grants, perm, client_id)


def can_access_client(user: dict, client_id: str) -> bool:
    """Ist dieser Client für den Benutzer sichtbar/zugänglich?"""
    if is_super_admin(user):
        return True
    grants = db.get_effective_grants(user["id"])
    return _resolve(grants, "access_clients", client_id)


def visible_client_ids(user: dict, all_client_ids: list[str]) -> set[str]:
    """Filtert eine Client-ID-Liste auf die für den Benutzer sichtbaren."""
    if is_super_admin(user):
        return set(all_client_ids)
    grants = db.get_effective_grants(user["id"])
    return {cid for cid in all_client_ids if _resolve(grants, "access_clients", cid)}


def require_perm(user: dict, perm: str, client_id: str | None = None) -> None:
    """Wirft 403, wenn das Recht fehlt."""
    if not user_has_permission(user, perm, client_id):
        raise HTTPException(403, f"Fehlendes Recht: {perm}")


def effective_permissions(user: dict, client_ids: list[str]) -> dict:
    """
    Baut die Rechte-Übersicht fürs Frontend:
      { admin, global: {perm: bool}, clients: { client_id: {perm: bool} } }
    Enthält nur SICHTBARE Clients (access_clients).
    """
    if is_super_admin(user):
        g = {p: True for p in db.PERM_KEYS}
        clients = {cid: {p: True for p in db.CLIENT_PERM_KEYS} for cid in client_ids}
        return {"admin": True, "global": g, "clients": clients}

    global_map = {p: user_has_permission(user, p) for p in db.PERM_KEYS}
    clients_map: dict[str, dict] = {}
    for cid in client_ids:
        if not can_access_client(user, cid):
            continue  # unsichtbare Clients gar nicht erst aufnehmen
        clients_map[cid] = {p: user_has_permission(user, p, cid) for p in db.CLIENT_PERM_KEYS}
    return {"admin": False, "global": global_map, "clients": clients_map}

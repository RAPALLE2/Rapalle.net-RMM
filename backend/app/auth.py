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
   Neben der lokalen Anmeldung (authenticate_local) gibt es die voll
   funktionsfähige Verzeichnis-Anmeldung (authenticate_realm). Beide liefern
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
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def _import_ldap3():
    """Importiert ldap3 oder liefert eine klare Fehlermeldung, falls es fehlt."""
    try:
        import ldap3
        from ldap3.utils.conv import escape_filter_chars
        return ldap3, escape_filter_chars
    except ImportError:
        raise HTTPException(
            501,
            "AD-Anmeldung nicht verfügbar: das Paket 'ldap3' ist nicht installiert. "
            "Bitte auf dem Server 'pip install ldap3' ausführen.",
        )


def _build_ldap_server(realm: dict):
    """Baut ein ldap3.Server-Objekt aus der Realm-Konfiguration (inkl. Port/SSL)."""
    ldap3, _ = _import_ldap3()
    use_ssl = bool(realm.get("use_ssl"))
    port = realm.get("port")
    try:
        port = int(port) if port not in (None, "") else None
    except (TypeError, ValueError):
        port = None

    host = realm["server"]
    # Falls jemand eine komplette URL einträgt (ldaps://host), respektieren wir das.
    if host.startswith("ldaps://"):
        use_ssl = True
        host = host[len("ldaps://"):]
    elif host.startswith("ldap://"):
        host = host[len("ldap://"):]

    return ldap3.Server(
        host,
        port=port,
        use_ssl=use_ssl,
        get_info=ldap3.ALL,
        connect_timeout=8,
    )


def _extract_group_names(member_of: list[str]) -> list[str]:
    """Wandelt AD-memberOf-DNs in reine Gruppennamen um (CN=Gruppe,... -> Gruppe)."""
    names = []
    for dn in member_of:
        first = dn.split(",")[0].strip()
        if first.upper().startswith("CN="):
            names.append(first[3:])
    return names


def test_realm_connection(realm: dict) -> dict:
    """
    Prüft die Realm-Konfiguration, ohne einen Benutzer anzumelden: verbindet
    sich mit dem Bind-/Service-Account und macht eine kleine Suche im Base DN.
    Gibt ein Ergebnis-Dict zurück (wird vom Test-Button im Frontend genutzt).
    """
    ldap3, _ = _import_ldap3()
    server = _build_ldap_server(realm)
    try:
        conn = ldap3.Connection(
            server,
            user=realm.get("bind_user") or None,
            password=realm.get("bind_password") or None,
            auto_bind=True,
        )
    except Exception as e:
        raise HTTPException(502, f"Verbindung/Anmeldung fehlgeschlagen: {e}")

    base_dn = realm.get("base_dn") or ""
    try:
        conn.search(base_dn, "(objectClass=*)", search_scope=ldap3.BASE, attributes=["distinguishedName"])
        info = {
            "ok": True,
            "server": str(server.host),
            "ssl": bool(realm.get("use_ssl")),
            "base_dn_ok": bool(base_dn) and len(conn.entries) >= 0,
        }
    except Exception as e:
        conn.unbind()
        raise HTTPException(502, f"Base DN nicht durchsuchbar: {e}")
    conn.unbind()
    return info


def list_realm_groups(realm: dict) -> list[str]:
    """
    Listet die Gruppennamen (CN) eines AD/LDAP-Verzeichnisses auf. Wird benutzt,
    um AD-Gruppen ins RMM zu importieren, damit man ihnen VORAB Rechte geben kann
    (ohne dass sich erst ein Mitglied anmelden muss).
    """
    ldap3, _ = _import_ldap3()
    server = _build_ldap_server(realm)
    base_dn = realm.get("base_dn") or ""
    try:
        conn = ldap3.Connection(
            server,
            user=realm.get("bind_user") or None,
            password=realm.get("bind_password") or None,
            auto_bind=True,
        )
    except Exception as e:
        raise HTTPException(502, f"AD-Verbindung fehlgeschlagen: {e}")
    try:
        names: set[str] = set()
        # Paginiert suchen, damit auch große Verzeichnisse vollständig geladen werden.
        conn.search(
            base_dn, "(objectClass=group)", search_scope=ldap3.SUBTREE,
            attributes=["cn"], paged_size=500,
        )
        for e in conn.entries:
            if "cn" in e and str(e.cn):
                names.add(str(e.cn))
        # Weitere Seiten holen, falls vorhanden.
        while True:
            cookie = (conn.result.get("controls", {})
                      .get("1.2.840.113556.1.4.319", {})
                      .get("value", {}).get("cookie"))
            if not cookie:
                break
            conn.search(
                base_dn, "(objectClass=group)", search_scope=ldap3.SUBTREE,
                attributes=["cn"], paged_size=500, paged_cookie=cookie,
            )
            for e in conn.entries:
                if "cn" in e and str(e.cn):
                    names.add(str(e.cn))
    except Exception as e:
        conn.unbind()
        raise HTTPException(502, f"Gruppensuche fehlgeschlagen: {e}")
    conn.unbind()
    return sorted(names)


def authenticate_realm(username: str, password: str, realm_id: str) -> dict | None:
    """
    Authentifiziert einen Benutzer gegen ein konfiguriertes Verzeichnis (LDAP/AD).

    Ablauf:
      1. Realm-Konfiguration laden (Server, Port, SSL, Base DN, Bind-User).
      2. Mit dem Bind-User am LDAP anmelden und den Benutzer suchen
         (sAMAccountName ODER userPrincipalName, Filter-Zeichen werden escaped).
      3. Ein zweites Mal mit dem gefundenen DN + eingegebenem Passwort binden
         - klappt das, ist das Passwort korrekt.
      4. Die AD-Gruppen des Benutzers auslesen (inkl. verschachtelter Gruppen,
         soweit das Verzeichnis das unterstützt).
      5. Benutzer lokal anlegen/aktualisieren (ohne Passwort-Hash) und den
         gleichnamigen RMM-Gruppen zuordnen.

    Gibt das lokale User-Dict zurück oder None bei Fehlschlag.
    """
    ldap3, escape_filter_chars = _import_ldap3()

    realm = db.get_realm(realm_id)
    if not realm:
        return None
    if not realm.get("enabled", 1):
        raise HTTPException(403, "Dieses Verzeichnis ist deaktiviert.")

    base_dn = realm.get("base_dn") or ""
    bind_user = realm.get("bind_user")
    bind_password = realm.get("bind_password")
    extra_filter = (realm.get("user_filter") or "").strip()

    safe_username = escape_filter_chars(username)
    user_filter = f"(|(sAMAccountName={safe_username})(userPrincipalName={safe_username}))"
    search_filter = f"(&(objectClass=user){user_filter}{extra_filter})"

    server = _build_ldap_server(realm)
    try:
        # 1. Mit dem Service-/Bind-Account anmelden, um den Benutzer zu finden
        conn = ldap3.Connection(
            server, user=bind_user or None, password=bind_password or None, auto_bind=True
        )

        conn.search(
            base_dn, search_filter, search_scope=ldap3.SUBTREE,
            attributes=["memberOf", "displayName", "sAMAccountName"],
        )
        if not conn.entries:
            conn.unbind()
            return None

        entry = conn.entries[0]
        user_dn = entry.entry_dn
        display_name = str(entry.displayName) if "displayName" in entry else username
        member_of = [str(g) for g in entry.memberOf] if "memberOf" in entry else []

        # 4b. Verschachtelte Gruppen (AD): Gruppen, in denen der User indirekt
        #     Mitglied ist. Schlägt das fehl (z.B. nicht-AD-LDAP), ignorieren wir es.
        try:
            nested_filter = f"(member:1.2.840.113556.1.4.1943:={escape_filter_chars(user_dn)})"
            conn.search(base_dn, nested_filter, search_scope=ldap3.SUBTREE, attributes=["cn"])
            for g in conn.entries:
                member_of.append(g.entry_dn)
        except Exception:
            pass
        conn.unbind()

        # 2. Mit dem gefundenen DN + eingegebenem Passwort binden (= Passwortprüfung)
        if not password:
            return None  # leeres Passwort niemals als "anonym gültig" durchwinken
        user_conn = ldap3.Connection(server, user=user_dn, password=password)
        if not user_conn.bind():
            return None  # falsches Passwort
        user_conn.unbind()

    except HTTPException:
        raise
    except Exception as e:
        # Netzwerkfehler, falscher Bind-User etc.
        raise HTTPException(502, f"AD-Anmeldung fehlgeschlagen: {e}")

    # 3. AD-Gruppennamen aus den memberOf-DNs extrahieren (dedupliziert)
    ad_group_names = sorted(set(_extract_group_names(member_of)))

    # 5. Benutzer lokal anlegen/aktualisieren und AD-Gruppen zuordnen
    user = db.upsert_ad_user(username, display_name, realm_id)
    db.sync_ad_user_groups(user["id"], ad_group_names)
    return db.get_user_by_id(user["id"])


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
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Sitzung abgelaufen, bitte erneut anmelden")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Ungültiges Token")


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
    """Der klassische Voll-Admin (Rolle 'admin') darf alles - Bypass des Resolvers."""
    try:
        return user.get("role") == "admin"
    except AttributeError:
        return user["role"] == "admin"


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
    scopes = ["global"] + ([client_id] if client_id else [])
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

"""
routers/auth_routes.py
-----------------------
Alles rund um Login und das eigene Benutzerprofil.

Endpunkte:
  POST /api/auth/login            -> Anmelden, liefert JWT-Token zurück
  POST /api/auth/change-password  -> eigenes Passwort ändern (auch für den
                                      Pflicht-Wechsel beim ersten Login)
  GET  /api/auth/me               -> Infos zum aktuell eingeloggten User
  PUT  /api/auth/profile          -> eigenen Anzeigenamen/Sprache/Theme ändern
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import (
    authenticate_local,
    authenticate_realm,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
    is_super_admin,
    user_has_permission,
    effective_permissions,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str
    realm: str = "local"   # "local" oder eine Realm-ID (AD)


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


class ProfileBody(BaseModel):
    display_name: str | None = None
    language: str | None = None   # "de" oder "en"
    theme: str | None = None      # "dark" oder "light"
    accent: str | None = None     # Farbpalette, z.B. "teal", "violet", ...


def _auth_realm_of(user) -> str | None:
    """Realm-ID des Users (AD/LDAP/SSO) oder None bei lokalem Konto.
    Funktioniert für dict UND sqlite3.Row."""
    try:
        return user["auth_realm"] if "auth_realm" in user.keys() else None
    except Exception:
        return None


def _public_user(user: dict) -> dict:
    """Gibt nur die Felder zurück, die das Frontend sehen darf (kein Passwort-Hash!)."""
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "must_change_pw": bool(user["must_change_pw"]),
        "is_admin": is_super_admin(user),
        "language": user["language"],
        "theme": user["theme"],
        "accent": user["accent"] if "accent" in user.keys() else "teal",
        # Realm-ID (AD/LDAP/SSO) oder null bei lokalem Konto - das Frontend
        # blendet damit u.a. die Passwort-ändern-Funktion aus.
        "auth_realm": _auth_realm_of(user),
    }


@router.get("/realms")
async def public_realms():
    """
    Öffentliche Liste der Anmelde-Realms für das Dropdown im Login-Fenster.
    Enthält nur id + name (keine Geheimnisse). "local" wird im Frontend ergänzt.
    """
    return db.get_public_realms()


@router.post("/login")
async def login(body: LoginBody):
    # Lokale Anmeldung oder Anmeldung gegen ein Verzeichnis (AD)?
    if body.realm and body.realm != "local":
        user = authenticate_realm(body.username, body.password, body.realm)
    else:
        user = authenticate_local(body.username, body.password)

    if not user:
        db.add_audit_entry(body.username, "login.failed", details=f"realm:{body.realm}")
        raise HTTPException(401, "Benutzername oder Passwort falsch")

    # Rechteprüfung: wer nicht das Recht 'login' hat (und kein Super-Admin ist),
    # darf sich trotz korrektem Passwort NICHT anmelden.
    if not user_has_permission(user, "login"):
        db.add_audit_entry(user["username"], "login.denied", details="fehlendes Recht 'login'")
        raise HTTPException(403, "Keine Berechtigung zur Anmeldung an diesem System")

    db.add_audit_entry(user["username"], "login.success", details=f"realm:{body.realm}")
    token = create_access_token(user)
    return {"token": token, "user": _public_user(user)}


@router.post("/change-password")
async def change_password(body: ChangePasswordBody, user: dict = Depends(get_current_user)):
    # Verzeichnis-Benutzer (AD/LDAP/SSO, erkennbar an auth_realm) haben KEIN
    # lokales Passwort - der Hash ist nur ein Platzhalter. Eine "Änderung"
    # würde ein lokales Schatten-Passwort erzeugen, das am Verzeichnis vorbei
    # zum Login taugt. Deshalb hart blocken; geändert wird im AD/LDAP selbst.
    if _auth_realm_of(user):
        raise HTTPException(403, "Passwort wird zentral im Verzeichnis (AD/LDAP/SSO) verwaltet und kann hier nicht geändert werden")
    # Beim allerersten Pflicht-Passwortwechsel ist current_password="admin" (o.ä.) zu prüfen
    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(400, "Aktuelles Passwort ist falsch")
    if len(body.new_password) < 8:
        raise HTTPException(400, "Neues Passwort muss mindestens 8 Zeichen haben")

    db.update_user_password(user["id"], hash_password(body.new_password), must_change_pw=False)
    db.add_audit_entry(user["username"], "password.changed", target=user["username"])
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return _public_user(user)


@router.get("/effective")
async def my_effective_permissions(user: dict = Depends(get_current_user)):
    """
    Effektive Rechte des eingeloggten Benutzers für das Frontend-Gating:
    { admin, global: {perm: bool}, clients: { client_id: {perm: bool} } }.
    Enthält nur sichtbare Clients.
    """
    all_ids = [c["id"] for c in db.list_clients()]
    return effective_permissions(user, all_ids)


@router.put("/profile")
async def update_profile(body: ProfileBody, user: dict = Depends(get_current_user)):
    db.update_user_profile(user["id"], body.display_name, body.language, body.theme, body.accent)
    return _public_user(db.get_user_by_id(user["id"]))

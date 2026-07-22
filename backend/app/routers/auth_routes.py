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
        # Benachrichtigungs-Regel: fehlgeschlagene Anmeldung
        try:
            from app import notifier
            from app.routers.admin_routes import build_notification
            await notifier.fire_event("login_failed",
                notification=build_notification(
                    f"Fehlgeschlagene Anmeldung für '{body.username}'.",
                    head="🚫 Fehlgeschlagene Anmeldung",
                    service="Auth", level="warn"),
                dedupe_key=f"login_failed:{body.username}")
        except Exception:
            pass
        raise HTTPException(401, "Benutzername oder Passwort falsch")

    # Rechteprüfung: wer nicht das Recht 'login' hat (und kein Super-Admin ist),
    # darf sich trotz korrektem Passwort NICHT anmelden.
    if not user_has_permission(user, "login"):
        db.add_audit_entry(user["username"], "login.denied", details="fehlendes Recht 'login'")
        raise HTTPException(403, "Keine Berechtigung zur Anmeldung an diesem System")

    db.add_audit_entry(user["username"], "login.success", details=f"realm:{body.realm}")
    # Benachrichtigungs-Regel: Benutzer-Anmeldung
    try:
        from app import notifier
        from app.routers.admin_routes import build_notification
        await notifier.fire_event("user_login",
            notification=build_notification(
                f"{user['username']} hat sich am Dashboard angemeldet.",
                head=f"🔑 Anmeldung – {user['username']}",
                service="Auth", level="info"),
            dedupe_key=f"user_login:{user['username']}")
    except Exception:
        pass
    # HA1 fürs Netzlaufwerk (Digest) aus dem Klartext-Passwort ableiten und ablegen,
    # damit sich der Nutzer am Relay mit seinem normalen Passwort anmelden kann.
    try:
        db.store_relay_secret(user["id"], body.password)
    except Exception:
        pass
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
    # HA1 fürs Netzlaufwerk mit dem neuen Passwort aktualisieren.
    try:
        db.store_relay_secret(user["id"], body.new_password)
    except Exception:
        pass
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


# ------------------------------------------------------------------
# Serverseitig gespeicherte UI-Einstellungen (Layouts, Favoriten, Sprache,
# Icon-Modus, ...). Damit sehen ALLE Browser desselben Benutzers gleich aus -
# statt dass jede Browser-Installation (Firefox/Edge/...) ihren eigenen
# localStorage-Stand hat.
# ------------------------------------------------------------------

class UiPrefsBody(BaseModel):
    # { "keys": { "<localStorage-Key>": "<Wert als String>" } }
    keys: dict[str, str] = {}


@router.get("/ui-prefs")
async def get_ui_prefs(user: dict = Depends(get_current_user)):
    import json as _json
    raw = db.get_user_ui_prefs(user["id"])
    if not raw:
        return {"keys": {}}
    try:
        data = _json.loads(raw)
        return {"keys": data.get("keys", {}) if isinstance(data, dict) else {}}
    except Exception:
        return {"keys": {}}


@router.put("/ui-prefs")
async def put_ui_prefs(body: UiPrefsBody, user: dict = Depends(get_current_user)):
    import json as _json
    try:
        db.set_user_ui_prefs(user["id"], _json.dumps({"keys": body.keys}))
    except ValueError as e:
        raise HTTPException(413, str(e))
    return {"ok": True}


# ------------------------------------------------------------------
# Silent-Modus für den Remote-Bildschirm (einmalig): Die NÄCHSTE Sitzung
# startet ohne Zustimmungs-Dialog am Gerät, danach schaltet sich der Modus
# automatisch wieder aus. Benötigt das Recht 'screen_silent' (global) oder
# 'c_screen_silent' auf mindestens einem sichtbaren Client.
# ------------------------------------------------------------------

def _may_use_silent_screen(user: dict) -> bool:
    if is_super_admin(user) or user_has_permission(user, "screen_silent"):
        return True
    from app.auth import visible_client_ids
    all_ids = [c["id"] for c in db.list_clients()]
    for cid in visible_client_ids(user, all_ids):
        if user_has_permission(user, "c_screen_silent", cid):
            return True
    return False


class SilentScreenBody(BaseModel):
    enabled: bool


@router.get("/silent-screen")
async def get_silent_screen(user: dict = Depends(get_current_user)):
    return {"enabled": bool(user.get("silent_screen")),
            "allowed": _may_use_silent_screen(user)}


# ------------------------------------------------------------------
# Spotify-Integration: Jeder eingeloggte Benutzer darf die Client-ID der
# (vom Admin in den Einstellungen hinterlegten) Spotify-App lesen - sie ist
# kein Geheimnis (steht in jeder OAuth-URL). Der Login läuft komplett im
# Browser per Authorization Code + PKCE, es gibt KEIN Client-Secret.
# ------------------------------------------------------------------

@router.get("/spotify-config")
async def spotify_config(user: dict = Depends(get_current_user)):
    return {"client_id": db.get_setting("spotify_client_id", "") or ""}


@router.put("/silent-screen")
async def put_silent_screen(body: SilentScreenBody, user: dict = Depends(get_current_user)):
    if body.enabled and not _may_use_silent_screen(user):
        raise HTTPException(403, "Keine Berechtigung für den Remote-Bildschirm ohne Anfrage")
    db.set_user_silent_screen(user["id"], body.enabled)
    db.add_audit_entry(user["username"],
                       "screen.silent_" + ("armed" if body.enabled else "disarmed"))
    return {"ok": True, "enabled": body.enabled}


@router.put("/profile")
async def update_profile(body: ProfileBody, user: dict = Depends(get_current_user)):
    # Anzeigename nur ändern, wenn das Recht 'edit_profile_name' vorliegt.
    # Sprache/Theme/Akzent sind persönliche Darstellung und bleiben immer erlaubt.
    new_name = body.display_name
    if (new_name is not None and new_name != user.get("display_name")
            and not (is_super_admin(user) or user_has_permission(user, "edit_profile_name"))):
        new_name = user.get("display_name")
    db.update_user_profile(user["id"], new_name, body.language, body.theme, body.accent)
    return _public_user(db.get_user_by_id(user["id"]))

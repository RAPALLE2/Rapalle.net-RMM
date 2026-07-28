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

from fastapi import APIRouter, Depends, HTTPException, Request
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
    code: str = ""         # Einmalcode der Authenticator-App (falls aktiviert)


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
        # Woher kommt der Vollzugriff? "role" = Rolle admin (wie beim Anlegen),
        # "permission" = über das Recht 'super_admin' vergeben, sonst None.
        "admin_via": ("role" if user["role"] == "admin"
                      else ("permission" if is_super_admin(user) else None)),
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
async def login(body: LoginBody, request: Request = None):
    # --- Schutz gegen Erraten von Passwoertern (BSI ORP.4.A9) -------------
    # Die Sperre wird VOR der Passwortpruefung ausgewertet: Sonst koennte man
    # trotz Sperre unbegrenzt weiterprobieren und aus den Antwortzeiten
    # Rueckschluesse ziehen.
    client_ip = ""
    try:
        client_ip = (request.client.host if request and request.client else "") or ""
    except Exception:
        pass

    from app import security_policy
    blocked, remaining = security_policy.lock_status(body.username, client_ip)
    if blocked:
        db.add_audit_entry(body.username, "login.blocked",
                           details=f"gesperrt, noch {remaining}s, ip:{client_ip}")
        raise HTTPException(
            429,
            f"Zu viele Fehlversuche. Bitte in {max(1, remaining // 60)} Minuten "
            f"erneut versuchen.")

    # Lokale Anmeldung oder Anmeldung gegen ein Verzeichnis (AD)?
    if body.realm and body.realm != "local":
        user = authenticate_realm(body.username, body.password, body.realm)
    else:
        user = authenticate_local(body.username, body.password)

    if not user:
        locked_now, lock_secs = security_policy.note_failure(body.username, client_ip)
        db.add_audit_entry(
            body.username, "login.failed",
            details=f"realm:{body.realm}, ip:{client_ip}"
                    + (f", KONTO GESPERRT fuer {lock_secs // 60} Minuten" if locked_now else ""))
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
        if locked_now:
            raise HTTPException(
                429,
                f"Zu viele Fehlversuche - der Zugang ist fuer "
                f"{lock_secs // 60} Minuten gesperrt.")
        raise HTTPException(401, "Benutzername oder Passwort falsch")

    # --- Zweiter Faktor (TOTP) -------------------------------------------
    # Erst NACH korrektem Passwort: Sonst liesse sich ueber die Rueckfrage
    # herausfinden, welche Benutzernamen existieren.
    if user.get("totp_enabled"):
        from app import totp as _totp
        code = (body.code or "").strip()
        if not code:
            # Kein Fehler, sondern eine Rueckfrage: Die Oberflaeche blendet
            # daraufhin das Code-Feld ein und schickt die Anmeldung erneut.
            raise HTTPException(401, "2fa_required")

        ok = _totp.verify(user.get("totp_secret") or "", code,
                          user_key=str(user["id"]))
        if not ok:
            # Wiederherstellungscode? Der wird dabei verbraucht.
            used, rest = _totp.use_backup_code(user.get("totp_backup") or "", code)
            if used:
                db.set_user_totp(user["id"], backup=rest)
                db.add_audit_entry(user["username"], "login.2fa_backup_used",
                                   details=f"verbleibend: {len([x for x in rest.split(',') if x])}")
                ok = True

        if not ok:
            security_policy.note_failure(body.username, client_ip)
            db.add_audit_entry(user["username"], "login.2fa_failed",
                               details=f"ip:{client_ip}")
            raise HTTPException(401, "Der Einmalcode ist falsch oder abgelaufen")

    # Rechteprüfung: wer nicht das Recht 'login' hat (und kein Super-Admin ist),
    # darf sich trotz korrektem Passwort NICHT anmelden.
    if not user_has_permission(user, "login"):
        db.add_audit_entry(user["username"], "login.denied", details="fehlendes Recht 'login'")
        raise HTTPException(403, "Keine Berechtigung zur Anmeldung an diesem System")

    security_policy.note_success(body.username, client_ip)
    db.add_audit_entry(user["username"], "login.success",
                       details=f"realm:{body.realm}, ip:{client_ip}")
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
    # Passwort-Richtlinie serverseitig durchsetzen (BSI ORP.4.A8/A22).
    # Die feste Grenze von 8 Zeichen reicht dafuer nicht aus; die geltenden
    # Werte stehen in den Einstellungen und sind dokumentiert.
    from app import security_policy
    problems = security_policy.check_password(body.new_password, user["username"])
    if problems:
        raise HTTPException(
            400, "Das Passwort erfüllt die Richtlinie nicht: " + "; ".join(problems))
    if body.new_password == body.current_password:
        raise HTTPException(400, "Das neue Passwort muss sich vom alten unterscheiden")

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
    # Die Redirect-URI wird NICHT separat gepflegt, sondern aus der
    # "Vollständigen URL" (Einstellungen → Allgemein → server_url) abgeleitet.
    # Damit gibt es genau EINE Stelle, an der die öffentliche Adresse steht.
    # Ist dort nichts hinterlegt, nimmt das Frontend die Adresse, unter der
    # das Dashboard gerade geöffnet ist.
    # WICHTIG: Spotify vergleicht ZEICHENGENAU. Deshalb wird die eingetragene
    # URL exakt so weitergegeben, wie sie hinterlegt ist - lediglich mehrfache
    # Schrägstriche am Ende werden auf einen reduziert. Würden wir hier einen
    # Schrägstrich erzwingen, passte es nicht mehr zu einem Spotify-Eintrag
    # ohne Schrägstrich (und umgekehrt) -> "Not matching configuration".
    base = (db.get_setting("server_url", "") or "").strip()
    if base.endswith("/"):
        base = base.rstrip("/") + "/"
    return {
        "client_id": db.get_setting("spotify_client_id", "") or "",
        "redirect_uri": base,
    }


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


@router.get("/password-policy")
async def password_policy():
    """
    Die geltende Passwort-Richtlinie - ohne Anmeldung abrufbar, weil sie schon
    beim erzwungenen ersten Passwortwechsel gebraucht wird. Sie verrät nichts
    Schützenswertes, sondern nur die Regeln, die ohnehin beim Speichern
    greifen.
    """
    from app import security_policy
    return {"policy": security_policy.policy(),
            "text": security_policy.describe_policy()}


# ==================================================================
# ZWEI-FAKTOR-ANMELDUNG (TOTP)
# ------------------------------------------------------------------
# Jeder Benutzer richtet sie selbst im Profil ein:
#   1. POST /api/auth/2fa/setup    -> Geheimnis + QR-Code (noch NICHT aktiv)
#   2. Authenticator-App scannt den QR-Code
#   3. POST /api/auth/2fa/activate -> Code aus der App bestaetigt die Einrichtung
#      und liefert einmalig die Wiederherstellungscodes
#   4. POST /api/auth/2fa/disable  -> abschalten (Passwort erforderlich)
#
# Der zweite Schritt ist wichtig: Erst wenn ein Code aus der App wirklich
# passt, wird die Anmeldung darauf umgestellt. Sonst koennte sich jemand
# aussperren, dessen App das Geheimnis gar nicht aufgenommen hat.
# ==================================================================

class TotpCodeBody(BaseModel):
    code: str = ""


class TotpDisableBody(BaseModel):
    password: str = ""
    code: str = ""       # Alternative: aktueller Code aus der App


def _confirm_identity(user: dict, password: str = "", code: str = "") -> None:
    """
    Bestaetigt, dass wirklich der Kontoinhaber am Geraet sitzt - nicht nur ein
    offener Browser.

    Wichtig: Konten aus einem Verzeichnis (AD/LDAP/SSO) haben LOKAL gar kein
    Passwort. Frueher wurde hier stur gegen den lokalen Hash geprueft, der bei
    diesen Konten leer ist - die Antwort war deshalb immer "Passwort falsch",
    egal was man eingab. Jetzt gilt:

      * lokales Konto   -> lokales Passwort ODER aktueller Code aus der App
      * Verzeichnis-Konto -> Passwort wird gegen das Verzeichnis geprueft,
                             ersatzweise reicht der Code aus der App
    """
    from app import totp as _totp

    # 1) Code aus der Authenticator-App - funktioniert fuer beide Kontoarten
    #    und ist der stärkere Nachweis, weil er das Geraet voraussetzt.
    if code and user.get("totp_secret"):
        if _totp.verify(user["totp_secret"], code, user_key=str(user["id"])):
            return
        used, rest = _totp.use_backup_code(user.get("totp_backup") or "", code)
        if used:
            db.set_user_totp(user["id"], backup=rest)
            # Auch im uebergebenen Objekt nachziehen: Es stammt aus der
            # Anmeldung und wuerde sonst innerhalb desselben Aufrufs noch den
            # alten Stand zeigen - der Code liesse sich dann ein zweites Mal
            # verwenden.
            user["totp_backup"] = rest
            return

    realm = user.get("auth_realm")
    if realm:
        # 2a) Verzeichnis-Konto: Passwort dort pruefen lassen.
        if password:
            try:
                if authenticate_realm(user["username"], password, realm):
                    return
            except Exception:
                pass          # Verzeichnis nicht erreichbar -> unten Fehler
        raise HTTPException(
            400,
            "Bestätigung fehlgeschlagen. Dieses Konto wird über ein Verzeichnis "
            "(AD/LDAP/SSO) verwaltet - bitte das Verzeichnis-Passwort eingeben "
            "oder stattdessen einen aktuellen Code aus der Authenticator-App.")

    # 2b) Lokales Konto
    if password and user.get("password_hash") and verify_password(password, user["password_hash"]):
        return
    raise HTTPException(400, "Passwort oder Code stimmt nicht.")


@router.get("/2fa/status")
async def totp_status(user: dict = Depends(get_current_user)):
    backup = (user.get("totp_backup") or "")
    return {
        "enabled": bool(user.get("totp_enabled")),
        "backup_left": len([h for h in backup.split(",") if h]),
        # Sagt der Oberflaeche, wonach sie fragen soll: Verzeichnis-Konten
        # haben lokal kein Passwort.
        "realm": user.get("auth_realm") or "",
    }


@router.post("/2fa/setup")
async def totp_setup(user: dict = Depends(get_current_user)):
    """
    Neues Geheimnis erzeugen und als QR-Code liefern. Noch nicht aktiv - die
    Anmeldung bleibt unveraendert, bis der naechste Schritt bestaetigt ist.
    """
    from app import totp as _totp
    secret = _totp.new_secret()
    db.set_user_totp(user["id"], secret=secret, enabled=False)
    uri = _totp.provisioning_uri(secret, user["username"])
    qr = _totp.qr_data_uri(uri)
    db.add_audit_entry(user["username"], "2fa.setup_started")
    return {
        "secret": secret,
        "uri": uri,
        "qr": qr,          # None -> Oberflaeche zeigt das Geheimnis zum Abtippen
        "digits": _totp.DIGITS,
        "period": _totp.PERIOD,
    }


@router.post("/2fa/activate")
async def totp_activate(body: TotpCodeBody, user: dict = Depends(get_current_user)):
    from app import totp as _totp
    secret = user.get("totp_secret")
    if not secret:
        raise HTTPException(400, "Bitte zuerst die Einrichtung starten.")
    if not _totp.verify(secret, body.code, user_key=str(user["id"])):
        raise HTTPException(400, "Der Code stimmt nicht. Uhrzeit des Geräts prüfen "
                                 "und den aktuellen Code aus der App eingeben.")
    codes = _totp.new_backup_codes()
    db.set_user_totp(user["id"], enabled=True, backup=_totp.hash_backup_codes(codes))
    db.add_audit_entry(user["username"], "2fa.enabled")
    # Die Klartextcodes gibt es GENAU EINMAL - danach liegen nur noch Hashes vor.
    return {"ok": True, "backup_codes": codes}


@router.post("/2fa/disable")
async def totp_disable(body: TotpDisableBody, user: dict = Depends(get_current_user)):
    """Abschalten nur mit eigenem Passwort ODER Code - ein offener Browser genuegt nicht."""
    _confirm_identity(user, body.password, body.code)
    db.set_user_totp(user["id"], secret="", enabled=False, backup="")
    db.add_audit_entry(user["username"], "2fa.disabled")
    return {"ok": True}


@router.post("/2fa/backup-codes")
async def totp_new_backup_codes(body: TotpDisableBody,
                                user: dict = Depends(get_current_user)):
    """Neue Wiederherstellungscodes erzeugen; die alten verfallen dabei."""
    if not user.get("totp_enabled"):
        raise HTTPException(400, "Zwei-Faktor-Anmeldung ist nicht aktiv")
    _confirm_identity(user, body.password, body.code)
    from app import totp as _totp
    codes = _totp.new_backup_codes()
    db.set_user_totp(user["id"], backup=_totp.hash_backup_codes(codes))
    db.add_audit_entry(user["username"], "2fa.backup_codes_renewed")
    return {"ok": True, "backup_codes": codes}

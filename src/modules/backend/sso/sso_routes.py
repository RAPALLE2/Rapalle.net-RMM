# sso_routes.py (Modul "sso")
# ---------------------------
# Verzeichnis-Anbindungen (Realms, LDAP/Active Directory) verwalten - frueher in
# router/admin_routes.py. URLs unveraendert. Recht: "manage_sso".
#
#   GET    /api/admin/realms                         Liste (ohne Bind-Passwort)
#   POST   /api/admin/realms                         anlegen
#   PUT    /api/admin/realms/{id}                    aendern
#   POST   /api/admin/realms/{id}/test               Verbindung testen
#   DELETE /api/admin/realms/{id}                    loeschen
#   GET    /api/admin/realms/{id}/ad-groups          AD-Gruppen auflisten
#   POST   /api/admin/realms/{id}/ad-groups/import   AD-Gruppen als RMM-Gruppen importieren
#   GET    /api/admin/sso/local-admins               lokale Admin-Konten (Bedingung fuers Deaktivieren)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

from . import ldap_auth, guard

router = APIRouter(prefix="/api/admin", tags=["sso"])


class RealmBody(BaseModel):
    name: str
    server: str
    base_dn: str | None = None
    bind_user: str | None = None
    bind_password: str | None = None
    port: int | None = None
    use_ssl: bool = False
    user_filter: str | None = None
    enabled: bool = True


@router.get("/realms")
def list_realms(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_sso")
    realms = db.list_realms()
    # Bind-Passwort niemals ans Frontend zurückgeben
    for r in realms:
        r.pop("bind_password", None)
    return realms


@router.post("/realms")
def create_realm(body: RealmBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_sso")
    r = db.create_realm(
        body.name, body.server, body.base_dn, body.bind_user, body.bind_password,
        port=body.port, use_ssl=body.use_ssl, user_filter=body.user_filter,
    )
    r.pop("bind_password", None)
    db.add_audit_entry(user["username"], "realm.created", target=r["id"], details=body.name)
    return r


@router.put("/realms/{realm_id}")
def update_realm(realm_id: str, body: RealmBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_sso")
    if not db.get_realm(realm_id):
        raise HTTPException(404, "Realm nicht gefunden")
    # exclude_unset: nur die tatsächlich mitgeschickten Felder ändern, damit ein
    # simpler Aktiv/Inaktiv-Umschalter nicht versehentlich SSL/Port/DN überschreibt.
    r = db.update_realm(realm_id, body.model_dump(exclude_unset=True))
    if r:
        r.pop("bind_password", None)
    db.add_audit_entry(user["username"], "realm.updated", target=realm_id, details=body.name)
    return r


@router.post("/realms/{realm_id}/test")
def test_realm(realm_id: str, user: dict = Depends(get_current_user)):
    """
    Prüft die Realm-Konfiguration: verbindet sich mit dem Bind-Account und
    durchsucht das Base DN. Meldet Erfolg oder eine aussagekräftige Fehlermeldung.
    """
    require_perm(user, "manage_sso")
    realm = db.get_realm(realm_id)
    if not realm:
        raise HTTPException(404, "Realm nicht gefunden")
    result = ldap_auth.test_realm_connection(realm)
    db.add_audit_entry(user["username"], "realm.tested", target=realm_id)
    return result


@router.delete("/realms/{realm_id}")
def delete_realm(realm_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_sso")
    db.delete_realm(realm_id)
    db.add_audit_entry(user["username"], "realm.deleted", target=realm_id)
    return {"ok": True}


@router.get("/realms/{realm_id}/ad-groups")
def get_realm_ad_groups(realm_id: str, user: dict = Depends(get_current_user)):
    """
    Listet die Gruppen des AD/LDAP-Verzeichnisses auf und markiert, welche davon
    bereits als RMM-Gruppe importiert sind (damit man ihnen Rechte geben kann).
    """
    require_perm(user, "manage_sso")
    realm = db.get_realm(realm_id)
    if not realm:
        raise HTTPException(404, "Realm nicht gefunden")
    names = ldap_auth.list_realm_groups(realm)
    existing = {g["name"] for g in db.list_groups()}
    return {
        "groups": [{"name": n, "imported": n in existing} for n in names],
        "count": len(names),
    }


class ImportAdGroupsBody(BaseModel):
    names: list[str] = []          # leer = alle gefundenen importieren


@router.post("/realms/{realm_id}/ad-groups/import")
def import_realm_ad_groups(realm_id: str, body: ImportAdGroupsBody,
                                 user: dict = Depends(get_current_user)):
    """
    Importiert AD-Gruppen als RMM-Gruppen (is_ad_group=1). Anschließend können in
    der Berechtigungen-App Rechte an diese Gruppen vergeben werden. Mitglieder
    der AD-Gruppe erben diese Rechte beim Login automatisch (Match über Namen).
    """
    require_perm(user, "manage_sso")
    realm = db.get_realm(realm_id)
    if not realm:
        raise HTTPException(404, "Realm nicht gefunden")

    names = body.names
    if not names:
        names = ldap_auth.list_realm_groups(realm)   # nichts angegeben -> alle importieren

    imported = []
    for name in names:
        g = db.upsert_ad_group(name)
        imported.append(g["name"])
    db.add_audit_entry(user["username"], "ad_groups.imported", target=realm_id,
                       details=f"{len(imported)} Gruppen")
    return {"ok": True, "imported": imported, "count": len(imported)}


@router.get("/sso/local-admins")
def local_admins(user: dict = Depends(get_current_user)):
    """Lokale, aktive Admin-Konten. Mindestens eins ist Pflicht, um SSO abzuschalten."""
    require_perm(user, "manage_sso")
    admins = guard.local_admins()
    return {"count": len(admins),
            "admins": [{"username": a["username"], "display_name": a.get("display_name") or ""} for a in admins]}

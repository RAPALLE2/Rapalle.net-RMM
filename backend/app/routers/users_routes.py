"""
routers/users_routes.py
-------------------------
Benutzerverwaltung für die Settings-App im Dashboard. Nur Admins dürfen das.

Endpunkte:
  GET    /api/users        -> alle Benutzer auflisten
  POST   /api/users        -> neuen Benutzer anlegen
                               (mit Wahlmöglichkeit "Einmalpasswort" vs.
                               "Passwort direkt final setzen")
  DELETE /api/users/{id}   -> Benutzer löschen
"""

import secrets
import string

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, hash_password, require_admin, require_perm

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserBody(BaseModel):
    username: str
    display_name: str
    role: str = "admin"            # "admin" oder "viewer"
    password: str | None = None    # wenn None -> automatisch generiertes Einmalpasswort
    one_time_password: bool = True  # True = User MUSS beim ersten Login ein eigenes PW setzen


def _generate_one_time_password(length: int = 12) -> str:
    """Erzeugt ein zufälliges, aber gut lesbares Einmalpasswort."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.get("")
async def get_users(user: dict = Depends(get_current_user)):
    require_perm(user, "see_permissions")
    users = db.list_users()
    # Passwort-Hashes NIE ans Frontend schicken
    return [{k: v for k, v in u.items() if k != "password_hash"} for u in users]


@router.post("")
async def create_user(body: CreateUserBody, user: dict = Depends(get_current_user)):
    require_perm(user, "create_users")

    if db.get_user_by_username(body.username):
        raise HTTPException(400, "Benutzername existiert bereits")

    # Entweder das vom Admin vorgegebene Passwort nehmen, oder eins generieren
    plain_password = body.password or _generate_one_time_password()

    new_user = db.create_user(
        username=body.username,
        password_hash=hash_password(plain_password),
        display_name=body.display_name,
        role=body.role,
        must_change_pw=body.one_time_password,
    )
    db.add_audit_entry(user["username"], "user.created", target=body.username)

    # Das Klartext-Passwort geben wir GENAU EINMAL zurück (direkt nach dem
    # Anlegen), damit der Admin es dem neuen Benutzer mitteilen kann.
    # Es wird sonst nirgendwo gespeichert oder erneut angezeigt.
    return {
        "id": new_user["id"],
        "username": new_user["username"],
        "generated_password": plain_password if body.one_time_password or not body.password else None,
    }


@router.delete("/{user_id}")
async def remove_user(user_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "create_users")
    if user_id == user["id"]:
        raise HTTPException(400, "Du kannst dich nicht selbst löschen")
    db.delete_user(user_id)
    db.add_audit_entry(user["username"], "user.deleted", target=user_id)
    return {"ok": True}

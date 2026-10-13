# access.py (Modul "spaces")
# --------------------------
# Wer darf was?
#
# Rechte (global, Rechteverwaltung -> Allgemein):
#   get_user_space    eigenen persoenlichen Space benutzen (auch Notizheft, Bilder in Chat/Tickets, Musik)
#   use_public_space  Public Spaces lesen und darin arbeiten
#   see_all_spaces    alle Spaces sehen (nur lesen)
#   edit_all_spaces   alle Spaces bearbeiten und verwalten; Public Spaces anlegen
#
# Teamspaces brauchen kein eigenes Recht: Mitglieder (Benutzer oder Gruppe)
# arbeiten darin, Team-Admins verwalten Mitglieder. Anlegen darf jeder mit get_user_space.
#
# Stufen: NONE < READ < WRITE < ADMIN  (ADMIN = verwalten, freigeben, loeschen)

from app import db
from app.auth import is_super_admin, user_has_permission

NONE, READ, WRITE, ADMIN = 0, 1, 2, 3

PERMS = ("get_user_space", "use_public_space", "see_all_spaces", "edit_all_spaces")


def has(user, perm: str) -> bool:
    return is_super_admin(user) or user_has_permission(user, perm)


def group_ids(user) -> set[str]:
    try:
        return set(db.get_user_group_ids(user["id"]))
    except Exception:
        return set()


def member_role(user, sp: dict, gids: set[str] | None = None) -> str | None:
    """Rolle im Teamspace ("admin"/"member") oder None. Admin gewinnt."""
    gids = group_ids(user) if gids is None else gids
    role = None
    for m in sp.get("members", []):
        if (m.get("type") == "user" and m.get("id") == user["id"]) or (m.get("type") == "group" and m.get("id") in gids):
            if m.get("role") == "admin":
                return "admin"
            role = "member"
    return role


def level(user, sp: dict | None, gids: set[str] | None = None) -> int:
    if not sp or not user:
        return NONE
    if has(user, "edit_all_spaces"):
        return ADMIN
    lv = READ if has(user, "see_all_spaces") else NONE
    kind = sp.get("kind")
    if kind == "personal":
        if sp.get("owner") == user["id"] and has(user, "get_user_space"):
            return ADMIN
        return lv
    if kind == "team":
        role = member_role(user, sp, gids)
        if role == "admin" or sp.get("owner") == user["id"]:
            return ADMIN
        if role == "member":
            return max(lv, WRITE)
        return lv
    if kind == "public":
        if sp.get("owner") and sp.get("owner") == user["id"] and has(user, "use_public_space"):
            return ADMIN
        if has(user, "use_public_space"):
            return max(lv, WRITE)
        return lv
    return lv


def may_create(user, kind: str) -> bool:
    if kind == "team":
        return has(user, "get_user_space") or has(user, "edit_all_spaces")
    if kind == "public":
        return has(user, "edit_all_spaces")
    return False


def share_applies(user, share: dict, gids: set[str] | None = None) -> bool:
    """Gilt eine Benutzer/Gruppen-Freigabe fuer diesen Benutzer?"""
    if share.get("kind") != "users":
        return False
    if share.get("expires") and share["expires"] < __import__("time").time() * 1000:
        return False
    gids = group_ids(user) if gids is None else gids
    return user["id"] in share.get("users", []) or bool(gids & set(share.get("groups", [])))

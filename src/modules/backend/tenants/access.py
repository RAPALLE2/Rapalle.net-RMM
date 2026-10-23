# access.py (Modul "tenants") - wer darf welchen Mandanten sehen/oeffnen?
# ---------------------------------------------------------------------
# Die FEINEN Rechte liegen auf dem Mandanten-Backend selbst. Hier wird nur
# gesteuert, wer ueberhaupt einen Mandanten im Haupt-RMM sieht und oeffnen darf:
# leere allow-Liste = fuer alle sichtbar; sonst nur die genannten Benutzer/Gruppen.
# Super-Admins sehen immer alles.
from __future__ import annotations

from app import db
from app.auth import is_super_admin


def may_open(user: dict, tenant: dict) -> bool:
    if is_super_admin(user):
        return True
    allow = tenant.get("allow") or []
    if not allow:
        return True
    gids = set(db.get_user_group_ids(user["id"]))
    for a in allow:
        if a.get("type") == "user" and a.get("id") == user["id"]:
            return True
        if a.get("type") == "group" and a.get("id") in gids:
            return True
    return False


def visible_tenants(user: dict, tenants: list[dict]) -> list[dict]:
    return [t for t in tenants if may_open(user, t)]

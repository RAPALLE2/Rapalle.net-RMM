# guard.py (Modul "sso")
# ----------------------
# Bedingung fuers Abschalten: Es muss mindestens EIN lokaler, aktiver Admin
# existieren - sonst koennte sich nach dem Abschalten niemand mehr anmelden.
#
#   lokal  = kein Verzeichnis-Konto (auth_realm leer)
#   aktiv  = hat ein Passwort (AD-Konten haben keins) -> kann sich lokal anmelden
#   Admin  = Rolle "admin" ODER globales Recht "Full Admin" (auth.is_super_admin;
#            ein ausdrueckliches "deny" auf "admin" entzieht es wieder)

from app import db
from app.auth import is_super_admin


def local_admins() -> list[dict]:
    out = []
    for u in db.list_users():
        if (u.get("auth_realm") or "").strip():
            continue
        if not (u.get("password_hash") or "").strip():
            continue
        if is_super_admin(u):
            out.append(u)
    return out

# module.py (Modul "sso")
# -----------------------
# Verzeichnis-Anmeldung (LDAP/Active Directory).
#
# Abschalten ist nur erlaubt, wenn mindestens ein lokaler, aktiver Admin
# existiert (guard.local_admins). Das gilt auch fuer die automatische
# Abschaltung nach einem Absturz (Hook "before_disable" = Veto).
#
# Nach dem Abschalten bleiben AD-Benutzer, AD-Gruppen und Realms in der
# Datenbank bestehen; AD-Benutzer koennen sich nur nicht mehr anmelden und
# laufende Sitzungen werden abgewiesen (app/auth.py -> get_current_user).

from . import guard


def can_disable(ctx):
    if not guard.local_admins():
        return ("Es gibt keinen lokalen, aktiven Admin-Account. Ohne SSO könnte sich danach "
                "niemand mehr anmelden. Bitte zuerst einen lokalen Benutzer mit Passwort und "
                "Rolle 'admin' oder dem Recht 'Full Admin' anlegen.")
    return None


def on_disable(ctx):
    ctx.log("SSO deaktiviert - AD-Benutzer können sich nicht mehr anmelden (Konten bleiben bestehen).")

# module.py (Modul "addir") - Active Directory verwalten (LDAP-Schreibzugriff)
# Keine Hintergrundaufgabe noetig; die Verbindungen werden je Anfrage geoeffnet
# und wieder geschlossen.
def on_startup(ctx):
    ctx.log("Active-Directory-Verwaltung bereit")

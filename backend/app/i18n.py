# backend/app/i18n.py
# -------------------
# Sprachumschaltung fuer das Backend (Deutsch/Englisch).
#
# WARUM ein eigenes Modul und nicht einfach englische Texte?
# Das Backend erzeugt Texte, die an DREI verschiedenen Stellen landen, und die
# haben unterschiedliche "Zielsprachen":
#
#   1. Fehlermeldungen an das Dashboard (HTTPException-detail)
#      -> Sprache des ANFRAGENDEN BENUTZERS (users.language in der Datenbank).
#         Zwei Personen mit verschiedenen Spracheinstellungen koennen dieselbe
#         Aktion ausloesen und muessen jeweils ihre Sprache sehen.
#
#   2. Server-Logs ([run], [screen], [relay] ...)
#      -> SERVER-Sprache. Ein Log hat keinen Benutzer; es liest der Betreiber.
#         Steuerbar ueber die Einstellung `server_language` bzw. die
#         Umgebungsvariable RMM_LANG. Voreinstellung: de.
#
#   3. Installations-Skripte (install.ps1 / install.sh / update.*)
#      -> Sprache aus dem ?lang=-Parameter des Abrufs. Das Skript laeuft
#         spaeter auf einem fremden Rechner, wo es weder Benutzer noch
#         Servereinstellung gibt - die Sprache muss also beim Erzeugen
#         feststehen.
#
# Der Aufbau ist bewusst derselbe wie im Frontend (frontend/js/i18n.js):
# flache Schluessel, {platzhalter} in geschweiften Klammern, DE als
# Rueckfallebene. Fehlt ein Schluessel, wird der Schluesselname zurueckgegeben -
# das faellt beim Testen sofort auf, statt still einen leeren Text zu zeigen.

from __future__ import annotations

import os

# Unterstuetzte Sprachen. Bei Erweiterung: LANGS ergaenzen und in TEXTS einen
# weiteren Block anlegen - fehlende Schluessel fallen automatisch auf DE zurueck.
LANGS = ("de", "en")
DEFAULT_LANG = "de"


def _norm(lang: str | None) -> str:
    """Beliebige Eingabe ('EN', 'de-DE', None) auf 'de'/'en' bringen."""
    if not lang:
        return DEFAULT_LANG
    code = str(lang).strip().lower().replace("_", "-").split("-")[0][:2]
    return code if code in LANGS else DEFAULT_LANG


def server_lang() -> str:
    """
    Sprache fuer Server-Logs und alles ohne Benutzerbezug.

    Reihenfolge: Einstellung `server_language` aus der Datenbank, sonst die
    Umgebungsvariable RMM_LANG, sonst Deutsch. Der Import von db erfolgt
    absichtlich INNERHALB der Funktion: i18n wird sehr frueh importiert
    (auch von db selbst), ein Import auf Modulebene wuerde einen Zirkel
    erzeugen.
    """
    try:
        from .db import get_setting
        val = get_setting("server_language")
        if val:
            return _norm(val)
    except Exception:
        pass
    return _norm(os.getenv("RMM_LANG"))


def user_lang(user: dict | None) -> str:
    """
    Sprache eines Dashboard-Benutzers (Spalte users.language).
    Faellt auf die Server-Sprache zurueck, wenn nichts gesetzt ist.
    """
    if isinstance(user, dict):
        val = user.get("language")
        if val:
            return _norm(val)
    return server_lang()


def t(key: str, lang: str | None = None, **params) -> str:
    """
    Text in der gewuenschten Sprache. Ohne `lang` gilt die Server-Sprache.

    Platzhalter werden wie im Frontend als {name} geschrieben und ueber
    **params gefuellt. Fehlt ein Platzhalter im Aufruf, bleibt er stehen,
    statt eine Ausnahme zu werfen - eine unvollstaendige Meldung ist immer
    noch besser als ein Absturz mitten in der Fehlerbehandlung.
    """
    code = _norm(lang) if lang else server_lang()
    text = TEXTS.get(code, {}).get(key) or TEXTS[DEFAULT_LANG].get(key) or key
    if params:
        for name, value in params.items():
            text = text.replace("{" + name + "}", str(value))
    return text


TEXTS: dict[str, dict[str, str]] = {
    "de": {
        # ---- Rechte / Zugriff ----
        "err_forbidden": "Keine Berechtigung für diese Aktion.",
        "err_no_client_access": "Kein Zugriff auf diesen Client.",
        "err_admin_only": "Nur Administratoren dürfen das.",
        "err_not_found": "Nicht gefunden.",
        "err_client_not_found": "Client nicht gefunden.",
        "err_user_not_found": "Benutzer nicht gefunden.",
        "err_client_offline": "Der Client ist offline.",
        "err_bad_request": "Ungültige Anfrage.",
        "err_login_failed": "Benutzername oder Passwort falsch.",
        "err_locked": "Zu viele Fehlversuche. Bitte später erneut versuchen.",
        # ---- Remote-Bildschirm ----
        "screen_no_permission": "Dir fehlt die Berechtigung für den Remote-Bildschirm.",
        "screen_silent_used": "Silent-Modus genutzt – keine Abfrage am Gerät.",
        # ---- Relay / Dateien ----
        "relay_not_shared": "Für diesen Client ist das Relay nicht freigegeben.",
        "relay_off": "Der Datei-Zugang ist am Server abgeschaltet.",
        # ---- Agent-Update ----
        "upd_triggered": "Update für {n} Client(s) ausgelöst.",
        "upd_none_online": "Kein passender Client online.",
        # ---- Server-Logs ----
        "log_relay_mode": "Datei-Zugang am Relay: {mode}",
        "log_backend_start": "Backend gestartet auf {url}",
        "log_db_mode": "Datenbank: {mode}",
        "log_agent_connected": "Agent verbunden: {name} ({id})",
        "log_agent_disconnected": "Agent getrennt: {name}",
        "log_screen_start": "Bildschirm-Start für {name}: {mode}",
        "log_update_all": "Agent-Update für {n} Client(s) ausgelöst.",
        # ---- Installations-Skripte (Ausgaben auf dem Zielrechner) ----
        "ins_title": "RAPALLE.net RMM – Agent installieren",
        "ins_upd_title": "RAPALLE.net RMM – Agent NEU AUSROLLEN",
        "ins_downloading": "Lade neueste Agent-Version...",
        "ins_stopping": "Stoppe Agent und Bildschirm-Helfer...",
        "ins_cleaning": "Räume alten Programmordner auf...",
        "ins_deploying": "Rolle neue Agent-Dateien aus...",
        "ins_venv": "Baue virtuelle Umgebung neu mit {py} ...",
        "ins_deps": "Installiere Abhängigkeiten...",
        "ins_task": "Registriere Autostart-Task neu (SYSTEM, beim Systemstart)...",
        "ins_done": "Neuausrollung fertig – Agent neu gestartet.",
        "ins_warn_venv": "WARNUNG: Neue venv konnte nicht gebaut werden – nutze die vorhandene.",
        "ins_warn_nopy": "WARNUNG: Kein System-Python gefunden – venv bleibt unverändert.",
        "ins_err_novenv": "FEHLER: Keine lauffähige venv vorhanden. Bitte install erneut ausführen.",
        "ins_err_deps": "FEHLER bei der Paket-Installation – Agent kann so nicht starten.",
        "ins_service_note": ("Der Agent läuft jetzt als Dienst unter SYSTEM und startet beim "
                             "Booten – auch wenn sich NIEMAND anmeldet (Server)."),
        "ins_close_note": "Du kannst dieses Fenster schließen – der Agent läuft weiter.",
        "ins_appear_note": "Der Client sollte in wenigen Sekunden im Dashboard erscheinen.",
        "ins_logfile": "Log-Datei bei Problemen: {path}",
    },
    "en": {
        # ---- Permissions / access ----
        "err_forbidden": "You are not allowed to do that.",
        "err_no_client_access": "No access to this client.",
        "err_admin_only": "Only administrators may do that.",
        "err_not_found": "Not found.",
        "err_client_not_found": "Client not found.",
        "err_user_not_found": "User not found.",
        "err_client_offline": "The client is offline.",
        "err_bad_request": "Invalid request.",
        "err_login_failed": "Wrong username or password.",
        "err_locked": "Too many failed attempts. Please try again later.",
        # ---- Remote screen ----
        "screen_no_permission": "You lack the permission for the remote screen.",
        "screen_silent_used": "Silent mode used – no prompt on the device.",
        # ---- Relay / files ----
        "relay_not_shared": "The relay is not shared for this client.",
        "relay_off": "File access is switched off on the server.",
        # ---- Agent update ----
        "upd_triggered": "Update triggered for {n} client(s).",
        "upd_none_online": "No matching client online.",
        # ---- Server logs ----
        "log_relay_mode": "File access at the relay: {mode}",
        "log_backend_start": "Backend started on {url}",
        "log_db_mode": "Database: {mode}",
        "log_agent_connected": "Agent connected: {name} ({id})",
        "log_agent_disconnected": "Agent disconnected: {name}",
        "log_screen_start": "Screen start for {name}: {mode}",
        "log_update_all": "Agent update triggered for {n} client(s).",
        # ---- Installation scripts (output on the target machine) ----
        "ins_title": "RAPALLE.net RMM – install agent",
        "ins_upd_title": "RAPALLE.net RMM – REDEPLOY agent",
        "ins_downloading": "Downloading the latest agent version...",
        "ins_stopping": "Stopping the agent and screen helper...",
        "ins_cleaning": "Cleaning up the old program folder...",
        "ins_deploying": "Deploying the new agent files...",
        "ins_venv": "Rebuilding the virtual environment with {py} ...",
        "ins_deps": "Installing dependencies...",
        "ins_task": "Re-registering the autostart task (SYSTEM, at boot)...",
        "ins_done": "Redeploy finished – agent restarted.",
        "ins_warn_venv": "WARNING: could not build a new venv – using the existing one.",
        "ins_warn_nopy": "WARNING: no system Python found – venv left unchanged.",
        "ins_err_novenv": "ERROR: no working venv present. Please run the installer again.",
        "ins_err_deps": "ERROR while installing packages – the agent cannot start like this.",
        "ins_service_note": ("The agent now runs as a service under SYSTEM and starts at boot – "
                             "even when NOBODY logs in (server)."),
        "ins_close_note": "You can close this window – the agent keeps running.",
        "ins_appear_note": "The client should appear in the dashboard within a few seconds.",
        "ins_logfile": "Log file if there are problems: {path}",
    },
}

# ---------------------------------------------------------------------------
# Uebersetzung der Fehlertexte BEIM AUSGANG
# ---------------------------------------------------------------------------
# Im Backend stehen ueber 200 HTTPException-Texte verstreut in ~30 Dateien.
# Jede einzelne Stelle umzubauen haette bedeutet, ueberall den anfragenden
# Benutzer durchzureichen - viel Aenderung an Code, der heute funktioniert,
# und entsprechend viel Gelegenheit, etwas kaputtzumachen.
#
# Stattdessen bleibt der deutsche Text im Code stehen und dient als
# SCHLUESSEL. Ein zentraler Ausnahme-Handler (siehe main.py) uebersetzt ihn
# kurz vor dem Senden in die Sprache des Benutzers. Vorteile:
#   - eine Stelle statt 200,
#   - unbekannte Texte werden unveraendert durchgereicht (nichts geht kaputt),
#   - neue Meldungen funktionieren sofort auf Deutsch und koennen spaeter
#     nachgetragen werden.
#
# Der Abgleich ist bewusst exakt (nach Normalisierung von Leerraum). Eine
# unscharfe Suche wuerde frueher oder spaeter den falschen Satz ersetzen.

_ERROR_EN = {
    "Client nicht gefunden": "Client not found",
    "Benutzer nicht gefunden": "User not found",
    "Kein Zugriff auf diesen Client": "No access to this client",
    "Client ist offline": "The client is offline",
    "Datei nicht gefunden": "File not found",
    "Pfad nicht gefunden": "Path not found",
    "Ordner nicht gefunden": "Folder not found",
    "Website nicht gefunden": "Website not found",
    "Webhook nicht gefunden": "Webhook not found",
    "Verbindung nicht gefunden": "Connection not found",
    "Regel nicht gefunden": "Rule not found",
    "Termin nicht gefunden": "Event not found",
    "Tabelle nicht gefunden": "Table not found",
    "Subjekt nicht gefunden": "Subject not found",
    "Realm nicht gefunden": "Realm not found",
    "Ticket nicht gefunden": "Ticket not found",
    "Gruppe nicht gefunden": "Group not found",
    "Bild nicht gefunden": "Image not found",
    "Skript nicht gefunden": "Script not found",
    "Aufzeichnung nicht gefunden": "Recording not found",
    "Titel fehlt": "Title is missing",
    "Name fehlt": "Name is missing",
    "Ungültiger Status": "Invalid status",
    "Ungültige Priorität": "Invalid priority",
    "Ungültige Layout-Art (dash|fleet)": "Invalid layout type (dash|fleet)",
    "Fehlendes Recht: use_relay": "Missing permission: use_relay",
    "Der interne Seitenproxy ist abgeschaltet": "The internal page proxy is switched off",
    "Die URL muss mit http:// oder https:// beginnen":
        "The URL has to start with http:// or https://",
    "monitor_notify muss 'up', 'down' oder 'always' sein":
        "monitor_notify has to be 'up', 'down' or 'always'",
    "Das Scan-Intervall muss mindestens 10 Sekunden betragen":
        "The scan interval has to be at least 10 seconds",
    "open_mode muss 'external' oder 'internal' sein":
        "open_mode has to be 'external' or 'internal'",
    "Keine Berechtigung": "Not allowed",
    "Nicht angemeldet": "Not signed in",
    "Nur Administratoren": "Administrators only",
    "Falsches Passwort": "Wrong password",
    "Benutzername oder Passwort falsch": "Wrong username or password",
    # ---- Nachtrag: die restlichen Fehlertexte aus den Routern ----
    # Ermittelt mit einem Suchlauf ueber alle HTTPException-Aufrufe im Backend,
    # damit nichts vergessen wird. Teiltexte (Meldungen, die zur Laufzeit noch
    # etwas anhaengen) sind hier NICHT eingetragen - der Abgleich ist exakt,
    # ein halber Treffer wuerde den Rest des Satzes deutsch stehen lassen.
    "AI-API lieferte keine verwertbare Antwort": "The AI API returned no usable answer",
    "API-Key fehlt": "API key is missing",
    "API-URL muss mit http(s):// beginnen": "The API URL has to start with http(s)://",
    "Aktualisierung nicht gefunden": "Update not found",
    "Antrag nicht gefunden": "Request not found",
    "Antwort zu gross für den internen Browser": "Response too large for the internal browser",
    "Aufzeichnungs-Datei fehlt": "Recording file is missing",
    "Aufzeichnungs-Datei fehlt (evtl. bereits gelöscht)":
        "Recording file is missing (possibly already deleted)",
    "Automation nicht gefunden": "Automation not found",
    "Benutzer existiert nicht mehr": "The user no longer exists",
    "Benutzername zur Bestätigung stimmt nicht": "The username for confirmation does not match",
    "Build-Zeitlimit (30 Minuten) überschritten": "Build time limit (30 minutes) exceeded",
    "Das eigene Konto kann hier nicht gelöscht werden":
        "You cannot delete your own account here",
    "Das neue Passwort muss sich vom alten unterscheiden":
        "The new password has to differ from the old one",
    "Datei fehlt auf dem Server": "The file is missing on the server",
    "Datei fehlt auf der Platte": "The file is missing on disk",
    "Datei zu groß (max. 25 MB)": "File too large (max. 25 MB)",
    "Datei zu groß (max. 8 MB)": "File too large (max. 8 MB)",
    "Der Agent ist nicht verbunden.": "The agent is not connected.",
    "Der Code stimmt nicht. Uhrzeit des Geräts prüfen":
        "The code is wrong. Check the clock of the device",
    "Der Einmalcode ist falsch oder abgelaufen": "The one-time code is wrong or has expired",
    "Dieser Eintrag ist keine Datei": "This entry is not a file",
    "Du kannst dich nicht selbst löschen": "You cannot delete yourself",
    "Eine Ablehnung muss begründet werden (Art. 12 Abs. 4 DSGVO)":
        "A rejection has to be justified (Art. 12(4) GDPR)",
    "Eintrag nicht gefunden": "Entry not found",
    "Ende muss nach dem Beginn liegen": "The end has to be after the start",
    "Es läuft bereits ein Datenbank-Wechsel": "A database switch is already running",
    "Es läuft bereits ein Scan": "A scan is already running",
    "Fehlendes Recht: admin_settings/manage_settings":
        "Missing permission: admin_settings/manage_settings",
    "Fehlendes Recht: manage_settings": "Missing permission: manage_settings",
    "Fehlendes Recht: share_media": "Missing permission: share_media",
    "Fehlendes Recht: use_media": "Missing permission: use_media",
    "Für diesen Client läuft bereits ein Auftrag.": "A job is already running for this client.",
    "Gesprächspartner nicht gefunden": "Conversation partner not found",
    "Kategorie nicht gefunden": "Category not found",
    "Kein Zugriff auf diese Wiedergabeliste": "No access to this playlist",
    "Kein Zugriff auf diesen Eintrag": "No access to this entry",
    "Kein Zugriff auf dieses Ticket": "No access to this ticket",
    "Keine Berechtigung für den Remote-Bildschirm ohne Anfrage":
        "No permission for the remote screen without asking",
    "Keine Berechtigung für diese Verbindung": "No permission for this connection",
    "Keine IP-Adresse für diesen Client bekannt": "No IP address known for this client",
    "Keine gültige ZIP-Datei": "Not a valid ZIP file",
    "Keine gültigen Ports angegeben.": "No valid ports given.",
    "Kommentar nicht gefunden": "Comment not found",
    "Konnte neuesten Commit nicht ermitteln": "Could not determine the latest commit",
    "Location nicht gefunden": "Location not found",
    "Modell fehlt": "Model is missing",
    "Nicht unterstütztes Format": "Unsupported format",
    "Notiz nicht gefunden": "Note not found",
    "Nur Gruppen können umbenannt werden": "Only groups can be renamed",
    "Nur Gruppen-Admins dürfen das": "Only group admins may do that",
    "Nur der Besitzer darf diese Liste löschen": "Only the owner may delete this list",
    "Nur der Besitzer darf diese Liste ändern": "Only the owner may change this list",
    "Nur der Besitzer darf diesen Eintrag löschen": "Only the owner may delete this entry",
    "Nur der Besitzer darf diesen Eintrag ändern": "Only the owner may change this entry",
    "Nur der Ersteller darf diesen Termin löschen": "Only the creator may delete this event",
    "Nur der Ersteller darf diesen Termin ändern": "Only the creator may change this event",
    "Nur der Ersteller kann die Verbindung löschen":
        "Only the creator can delete the connection",
    "Nur der Verfasser darf diese Notiz löschen": "Only the author may delete this note",
    "Nur der Verfasser darf diese Notiz ändern": "Only the author may change this note",
    "Nur der Verfasser darf diesen Kommentar löschen":
        "Only the author may delete this comment",
    "Nur für Administratoren": "Administrators only",
    "Nur http und https werden weitergereicht": "Only http and https are forwarded",
    "Paket nicht gefunden": "Package not found",
    "Passwort oder Code stimmt nicht.": "Password or code is wrong.",
    "Passwort wird zentral im Verzeichnis (AD/LDAP/SSO) verwaltet und kann hier nicht geändert werden":
        "The password is managed centrally in the directory (AD/LDAP/SSO) and cannot be changed here",
    "Pfad außerhalb des Projekts ist nicht erlaubt":
        "A path outside the project is not allowed",
    "Projekt-Wurzel kann nicht gelöscht werden": "The project root cannot be deleted",
    "Quelle nicht gefunden": "Source not found",
    "Scan-Job nicht gefunden (evtl. abgelaufen)": "Scan job not found (it may have expired)",
    "Selbstgespräche sind nicht vorgesehen 🙂": "Talking to yourself is not supported 🙂",
    "Spalte nicht gefunden": "Column not found",
    "Spalten-Definition fehlt": "Column definition is missing",
    "Standard-Kategorien können nicht gelöscht werden": "Default categories cannot be deleted",
    "Tenant nicht gefunden": "Tenant not found",
    "Titel und URL erforderlich": "Title and URL are required",
    "Todo nicht gefunden": "To-do not found",
    "Unbekannter oder abgelaufener Onboarding-Link":
        "Unknown or expired onboarding link",
    "Ungültige Art (youtube|spotify|radio)": "Invalid kind (youtube|spotify|radio)",
    "Ungültige Sicherheit (starttls|ssl|none)": "Invalid security (starttls|ssl|none)",
    "Ungültige Sichtbarkeit": "Invalid visibility",
    "Ungültige Ziel-Angabe": "Invalid target",
    "Ungültiger Auto-Update-Modus (global|on|off)": "Invalid auto-update mode (global|on|off)",
    "Ungültiger Gerätetyp (physical|vm|lxc)": "Invalid device type (physical|vm|lxc)",
    "Ungültiger Modus": "Invalid mode",
    "Ungültiger Modus (erlaubt: off, ftp, sftp)": "Invalid mode (allowed: off, ftp, sftp)",
    "Ungültiger Modus (local|external)": "Invalid mode (local|external)",
    "Ungültiger Range-Header": "Invalid range header",
    "Ungültiger Tabellenname (nur Buchstaben/Zahlen/_)":
        "Invalid table name (letters/digits/_ only)",
    "Ungültiger Typ (dm|group)": "Invalid type (dm|group)",
    "Ungültiges Token": "Invalid token",
    "Ungültiges Ziel (commit|full|any|custom)": "Invalid target (commit|full|any|custom)",
    "Unterhaltung nicht gefunden": "Conversation not found",
    "Wiedergabeliste nicht gefunden": "Playlist not found",
    "Ziel fehlt": "Target is missing",
    "Zwei-Faktor-Anmeldung ist nicht aktiv": "Two-factor authentication is not active",
    "tools/build_installers.py nicht gefunden": "tools/build_installers.py not found",
    "trigger und channel sind erforderlich": "trigger and channel are required",
    # ---- Mehrzeilig im Quelltext zusammengesetzte Meldungen ----
    # Der Abgleich normalisiert Leerraum ("  ".join(split())), deshalb passt
    # hier der VOLLSTAENDIGE Satz, obwohl er im Code auf mehrere Zeilen
    # verteilt ist.
    "monitor_notify muss 'up', 'down' oder 'always' sein":
        "monitor_notify has to be 'up', 'down' or 'always'",
    "open_mode muss 'external' oder 'internal' sein":
        "open_mode has to be 'external' or 'internal'",
    "Der Agent ist nicht innerhalb von 60 Sekunden offline gegangen. "
    "Die Deinstallation ist möglicherweise fehlgeschlagen - der Client wurde "
    "NICHT aus dem Dashboard entfernt. Bitte den Client prüfen und ggf. erneut versuchen.":
        "The agent did not go offline within 60 seconds. The uninstall may have "
        "failed - the client was NOT removed from the dashboard. Please check the "
        "client and try again if needed.",
    "Zwei-Faktor-Anmeldung ist nicht aktiv": "Two-factor authentication is not active",
}


def translate_detail(detail, lang: str | None = None):
    """
    Uebersetzt einen Fehlertext, wenn er bekannt ist. Alles andere bleibt
    unveraendert - lieber ein deutscher Satz als ein verstuemmelter.
    Nicht-Zeichenketten (FastAPI erlaubt auch Listen/Dicts als detail)
    werden unangetastet durchgereicht.
    """
    if not isinstance(detail, str):
        return detail
    if _norm(lang) != "en":
        return detail
    return _ERROR_EN.get(" ".join(detail.split()), detail)

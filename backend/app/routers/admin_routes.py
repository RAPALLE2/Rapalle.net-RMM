"""
routers/admin_routes.py
------------------------
Sammel-Router für die Verwaltungs-Features:
  - Gruppen & Rollen (Rechte-System)
  - Realms (Verzeichnis-Anbindung / Active Directory - Konfiguration)
  - Webhooks (Benachrichtigungen, Discord/Custom)
  - Automationen (geplante Befehle)

Diese Endpunkte sind Admin-geschützt.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone

import os
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import db
from app.auth import (get_current_user, require_admin, require_perm,
                      user_has_permission, list_realm_groups)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ==================================================================
# Gruppen & Rollen
# ==================================================================

class GroupBody(BaseModel):
    name: str
    permissions: list[str] = []


@router.get("/permissions")
def list_permissions(user: dict = Depends(get_current_user)):
    """Liste aller verfügbaren Rechte-Schlüssel (für die Checkbox-UI)."""
    return {"permissions": db.ALL_PERMISSIONS}


@router.get("/groups")
def list_groups(user: dict = Depends(get_current_user)):
    require_perm(user, "see_permissions")
    groups = db.list_groups()
    for g in groups:
        g["permissions"] = [p for p in (g["permissions"] or "").split(",") if p]
    return groups


@router.post("/groups")
def create_group(body: GroupBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_permissions")
    g = db.create_group(body.name, body.permissions)
    db.add_audit_entry(user["username"], "group.created", target=g["id"], details=body.name)
    return g


@router.put("/groups/{group_id}")
def update_group(group_id: str, body: GroupBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_permissions")
    g = db.update_group(group_id, body.name, body.permissions)
    db.add_audit_entry(user["username"], "group.updated", target=group_id, details=body.name)
    return g


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_permissions")
    db.delete_group(group_id)
    db.add_audit_entry(user["username"], "group.deleted", target=group_id)
    return {"ok": True}


class GroupUnmanagedBody(BaseModel):
    unmanaged: bool = True


@router.put("/groups/{group_id}/unmanaged")
def set_group_unmanaged(group_id: str, body: GroupUnmanagedBody,
                              user: dict = Depends(get_current_user)):
    """(AD-)Gruppe als unverwaltet markieren – landet im eingeklappten Ordner
    und wird AD-Benutzern nicht mehr automatisch zugewiesen."""
    require_perm(user, "manage_permissions")
    g = db.set_group_unmanaged(group_id, body.unmanaged)
    db.add_audit_entry(user["username"], "group.updated", target=group_id,
                       details=f"unmanaged={'1' if body.unmanaged else '0'}")
    return g or {"ok": True}


class UserGroupsBody(BaseModel):
    group_ids: list[str] = []


@router.put("/users/{user_id}/groups")
def set_user_groups(user_id: str, body: UserGroupsBody, user: dict = Depends(get_current_user)):
    require_perm(user, "manage_permissions")
    db.set_user_groups(user_id, body.group_ids)
    return {"ok": True}


@router.get("/users/{user_id}/groups")
def get_user_groups(user_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "see_permissions")
    return {"group_ids": db.get_user_group_ids(user_id)}


# ==================================================================
# Feingranulare Rechte (tri-state Grants) für die Permissions-App
# ==================================================================
# Labels für die Frontend-Anzeige. Der Resolver kennt die Semantik.
PERM_LABELS = {
    # --- Global / universell ---
    "admin": "Full Admin (Vollzugriff)",
    "login": "Anmelden",
    "see_dashboard": "Dashboard sehen",
    "customize_dashboard": "Dashboard anpassen",
    "restore_session": "Zuletzt geöffnete Sachen wiederherstellen",
    "edit_profile_name": "Profilname ändern",
    "access_clients": "Clients sehen/zugreifen",
    "see_replay": "Aufzeichnungen sehen",
    "delete_replay": "Aufzeichnungen löschen",
    "see_audit": "Audit-Log sehen",
    "see_source": "Source ansehen",
    "edit_source": "Source bearbeiten",
    "delete_source": "Source löschen (Datenbank/Explorer)",
    "manage_branding": "Branding ändern",
    "manage_sso": "SSO ändern",
    "see_settings": "Einstellungen ansehen",
    "manage_settings": "Standard-Einstellungen ändern",
    "admin_settings": "Admin-Einstellungen (IP/Port, Server-Update, DB, Neustart)",
    "screen_silent": "Remote-Bildschirm ohne Anfrage (Silent-Modus)",
    "c_screen_silent": "Remote-Bildschirm ohne Anfrage",
    "set_warranty": "Garantie-Datum setzen",
    "ticket_read": "Tickets lesen",
    "ticket_create": "Tickets erstellen",
    "ticket_edit": "Tickets bearbeiten",
    "ticket_comment": "Tickets kommentieren",
    "ticket_assign": "Tickets zuweisen",
    "ticket_resolve": "Tickets als gelöst markieren",
    "ticket_delete": "Tickets löschen",
    "see_permissions": "Rechte & Gruppen sehen",
    "manage_permissions": "Rechte & Gruppen bearbeiten",
    "create_users": "Benutzer erstellen",
    "network_scan": "Netzwerk-Scan",
    "port_scan": "Port-Scan",
    "bulk_shell": "Bulk Remote-Shell",
    "use_scripts": "Skripte benutzen",
    "create_scripts": "Skripte erstellen",
    "automation": "Automationen",
    "manage_hierarchy": "Tenants/Locations/Ordner verwalten",
    "play_games": "Spiele spielen",
    "use_media": "Audio-Player & Medien-Bibliothek",
    "share_media": "Medien für alle bereitstellen (Uploads, Links, Listen)",
    "manage_favorites": "Favoritenliste bearbeiten",
    "use_relay": "Relay benutzen",
    "use_chat": "Chat benutzen",
    "use_todos": "Todo-Liste benutzen (privat)",
    "manage_privacy": "Datenschutz: Fristen, Auskunft, Löschung (DSGVO)",
    "patching": "Updates sehen und installieren",
    "manage_patching": "Auto-Patch-Regeln festlegen",
    "super_admin": "⭐ Super-Admin (alle Rechte)",
    "add_client": "Clients hinzufügen (Enrollment)",
    "see_org": "Organigramm ansehen",
    "manage_org": "Organigramm bearbeiten (Über-/Unterstellung, Arbeitsbereich)",
    "use_calendar": "Kalender benutzen",
    "manage_calendar": "Kalender: Termine für alle anlegen",
    "relay_unlimited": "Relay unbegrenzt (keine Auto-Schließzeit)",
    "use_vpn": "VPN benutzen (Tunnel ausstellen)",
    "vpn_unlimited": "VPN unbegrenzt (Tunnel ohne Ablaufzeit)",
    # --- Pro Client ---
    "manage_clients": "Bearbeiten",
    "manage_agent": "Aktualisieren (Agent-Update)",
    "c_delete": "Löschen",
    "c_screen": "Remote-Bildschirm (Steuern)",
    "c_screen_view": "Remote-Bildschirm (nur ansehen)",
    "c_terminal": "Terminal",
    "c_terminal_console": "Terminal (nur Agent-Konsole)",
    "c_guacamole": "Guacamole",
    "c_power": "Herunterfahren/Neustarten",
    "c_explorer_view": "Datei-Explorer sehen",
    "c_explorer_edit": "Datei-Explorer bearbeiten",
    "c_taskmanager_view": "Task-Manager sehen",
    "c_taskmanager_kill": "Prozess beenden",
    "c_relay": "Relay starten",
    "c_relay_unlimited": "Relay unbegrenzt",
    "c_vpn": "VPN-Tunnel ausstellen",
    "c_vpn_unlimited": "VPN-Tunnel ohne Ablaufzeit",
    "c_nodeproxy": "Reverse Proxy dieser Node benutzen",
    "c_notes_view": "Notizen sehen",
    "c_notes_edit": "Notizen bearbeiten",
    "c_websites_view": "Websites sehen",
    "c_websites_edit": "Websites bearbeiten",
    # --- Alt-Keys (nur Anzeige bei bereits gespeicherten Grants) ---
    "use_guacamole": "Guacamole (alt)",
    "use_terminal": "Terminal (alt)",
    "use_screen": "Remote-Bildschirm (alt)",
    "use_explorer": "Datei-Explorer (alt)",
    "use_taskmanager": "Task-Manager (alt)",
    "manage_users": "Benutzer verwalten (alt)",
}


@router.get("/permission-catalog")
def permission_catalog(user: dict = Depends(get_current_user)):
    """Liefert die Rechte-Schlüssel (global + client) inkl. Labels fürs Frontend."""
    require_perm(user, "see_permissions")
    return {
        "labels": PERM_LABELS,
        "general": db.GENERAL_PERM_KEYS,
        "client": db.CLIENT_PERM_KEYS,
        # Implikationen (Quelle -> abgedeckte Rechte): Das Frontend nutzt sie,
        # um beim Erlauben eines Rechts die Basis-Rechte automatisch mitzusetzen.
        "implies": db.perm_implies_map(),
        # Pseudo-Client-Scope, dessen Client-Rechte für JEDEN Client gelten.
        "default_client_scope": db.DEFAULT_CLIENT_SCOPE,
    }


class GrantItem(BaseModel):
    scope: str = "global"        # 'global' | <client_id>
    perm: str
    effect: str                  # 'allow' | 'deny'


class GrantsBody(BaseModel):
    grants: list[GrantItem] = []


def _valid_subject(subject_type: str, subject_id: str) -> bool:
    if subject_type == "user":
        return db.get_user_by_id(subject_id) is not None
    if subject_type == "group":
        return db.get_group(subject_id) is not None
    return False


@router.get("/grants/{subject_type}/{subject_id}")
def get_grants(subject_type: str, subject_id: str, user: dict = Depends(get_current_user)):
    """Alle Grants eines Subjekts (Benutzer oder Gruppe)."""
    require_perm(user, "see_permissions")
    if subject_type not in ("user", "group"):
        raise HTTPException(400, "subject_type muss 'user' oder 'group' sein")
    if not _valid_subject(subject_type, subject_id):
        raise HTTPException(404, "Subjekt nicht gefunden")
    return {"grants": db.get_grants(subject_type, subject_id)}


@router.put("/grants/{subject_type}/{subject_id}")
def put_grants(subject_type: str, subject_id: str, body: GrantsBody,
                     user: dict = Depends(get_current_user)):
    """Ersetzt ALLE Grants eines Subjekts (tri-state; nur allow/deny werden gespeichert)."""
    require_perm(user, "manage_permissions")
    if subject_type not in ("user", "group"):
        raise HTTPException(400, "subject_type muss 'user' oder 'group' sein")
    if not _valid_subject(subject_type, subject_id):
        raise HTTPException(404, "Subjekt nicht gefunden")
    db.set_grants(subject_type, subject_id, [g.model_dump() for g in body.grants])
    db.add_audit_entry(user["username"], "grants.updated",
                       target=f"{subject_type}:{subject_id}",
                       details=f"{len(body.grants)} Grants")
    return {"ok": True, "grants": db.get_grants(subject_type, subject_id)}


# ==================================================================
# Realms (Verzeichnis-Anbindung / Active Directory)
# ==================================================================

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
    from app.auth import test_realm_connection
    result = test_realm_connection(realm)
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
    names = list_realm_groups(realm)
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
        names = list_realm_groups(realm)   # nichts angegeben -> alle importieren

    imported = []
    for name in names:
        g = db.upsert_ad_group(name)
        imported.append(g["name"])
    db.add_audit_entry(user["username"], "ad_groups.imported", target=realm_id,
                       details=f"{len(imported)} Gruppen")
    return {"ok": True, "imported": imported, "count": len(imported)}


@router.post("/restart")
async def restart_backend(user: dict = Depends(get_current_user)):
    """
    Startet den Backend-Prozess neu (re-exec des laufenden Python-Prozesses).
    Läuft das Backend unter einem Prozess-Manager (systemd o.ä.) mit Auto-Restart,
    genügt alternativ ein sauberer Exit - re-exec funktioniert aber in beiden
    Fällen. Antwortet SOFORT und startet ~1 s später neu, damit die Antwort noch
    beim Client ankommt.
    """
    # Neu starten darf, wer Einstellungen ändern darf: 'admin_settings'
    # (Server-/Systemeinstellungen) ODER 'manage_settings' (Standard-
    # Einstellungen). Grund: Änderungen an FTP/SFTP wirken erst nach einem
    # Neustart - wer sie setzen darf, muss sie auch anwenden können.
    # Vollwertige Administratoren haben ohnehin alle Rechte.
    if not (user_has_permission(user, "admin_settings")
            or user_has_permission(user, "manage_settings")):
        raise HTTPException(403, "Fehlendes Recht: admin_settings/manage_settings")
    db.add_audit_entry(user["username"], "backend.restart")

    import sys as _sys
    import asyncio as _asyncio

    async def _do_restart():
        await _asyncio.sleep(1.0)
        try:
            os.execv(_sys.executable, [_sys.executable] + _sys.argv)
        except Exception:
            # Falls re-exec scheitert: hart beenden, damit ein Prozess-Manager
            # (systemd Restart=always) uns neu startet.
            os._exit(0)

    _asyncio.create_task(_do_restart())
    return {"ok": True, "message": "Backend startet in ca. 1 Sekunde neu…"}


@router.post("/stop")
async def stop_backend(user: dict = Depends(get_current_user)):
    """
    STOPPT den Backend-Prozess (sauberer Exit, Code 0). Achtung: Läuft das
    Backend unter einem Prozess-Manager mit Auto-Restart (systemd
    Restart=always), startet der Manager es sofort wieder - dann muss der
    Dienst dort gestoppt werden. Vor dem Beenden wird noch der finale
    Datenbank-Sync ausgeführt (externer DB-Modus). Antwortet SOFORT und
    beendet sich ~1 s später.
    """
    require_perm(user, "admin_settings")
    db.add_audit_entry(user["username"], "backend.stop")

    import asyncio as _asyncio

    async def _do_stop():
        await _asyncio.sleep(1.0)
        try:
            from app import dbsync as _dbsync
            _dbsync.periodic_sync()   # letzten Stand in die externe DB schreiben
        except Exception:
            pass
        print("[admin] Backend wird auf Nutzerwunsch GESTOPPT.")
        os._exit(0)

    _asyncio.create_task(_do_stop())
    return {"ok": True, "message": "Backend wird in ca. 1 Sekunde gestoppt."}


# ==================================================================
# Webhooks / Benachrichtigungen
# ==================================================================

class WebhookBody(BaseModel):
    name: str
    url: str
    type: str = "custom"   # "discord" | "custom"
    # Custom-Webhooks: eigene HTTP-Header (JSON-Objekt als String) und ein
    # Body-Template mit Platzhaltern {head} {body} {message} {client} {tenant}
    # {location} {service} {level} {timestamp}. Leer = Standard-JSON.
    headers: str | None = None
    body_template: str | None = None


class WebhookUpdateBody(BaseModel):
    name: str | None = None
    url: str | None = None
    type: str | None = None
    enabled: bool | None = None
    headers: str | None = None
    body_template: str | None = None


# ------------------------------------------------------------------
# Notification-Aufbau
# ------------------------------------------------------------------
# Jede Benachrichtigung besteht IMMER aus einem "head" (Titel/Kurzfassung)
# und einem "body" (eigentliche Nachricht), dazu ein einheitlicher Kontext
# (tenant / location / client / service / timestamp) und ein Level, das
# die Embed-Farbe bestimmt. Die Farben entsprechen denen im Frontend
# (siehe notify.js: info/success/warn/error).

# Level -> Discord-Embed-Farbe (dezimal). Hex aus dem Frontend übernommen.
LEVEL_COLORS = {
    "info":    0x4DA6FF,
    "success": 0x3ECF8E,
    "warn":    0xF5A524,
    "warning": 0xF5A524,
    "error":   0xFF4D6D,
    "danger":  0xFF4D6D,
}

# Level -> Emoji für den head (rein kosmetisch).
LEVEL_EMOJI = {
    "info":    "ℹ️",
    "success": "✅",
    "warn":    "⚠️",
    "warning": "⚠️",
    "error":   "🚨",
    "danger":  "🚨",
}


def _normalize_level(level: str | None) -> str:
    level = (level or "info").lower()
    return level if level in LEVEL_COLORS else "info"


def build_notification(
    message: str,
    *,
    head: str | None = None,
    body: str | None = None,
    tenant: str | None = None,
    location: str | None = None,
    client: str | None = None,
    service: str | None = None,
    level: str = "info",
    timestamp: int | None = None,
) -> dict:
    """
    Baut ein einheitliches Notification-Objekt. 'head' und 'body' werden
    - falls nicht explizit übergeben - automatisch aus den Kontextdaten
    zusammengesetzt, sodass eine Notification IMMER head + body hat.

    timestamp: Millisekunden seit Epoch (default: jetzt).
    """
    level = _normalize_level(level)
    service = service or "System"
    client = client or "—"
    tenant = tenant or "—"
    location = location or "—"
    ts = timestamp if timestamp is not None else int(time.time() * 1000)

    # head: kurze Zusammenfassung, z.B. "🚨 Disk Space – SRV-01"
    if not head:
        head = f"{LEVEL_EMOJI.get(level, 'ℹ️')} {service} – {client}"
    # body: die eigentliche Nachricht
    if not body:
        body = message

    return {
        "head": head,
        "body": body,
        "tenant": tenant,
        "location": location,
        "client": client,
        "message": message,
        "service": service,
        "level": level,
        "timestamp": ts,
    }


def _discord_payload(n: dict) -> dict:
    """
    Fertiges Discord-Template: ein Embed mit head als Titel, body als
    Beschreibung, den Kontextfeldern (Tenant / Location / Client / Service),
    einem ISO-Timestamp und der Farbe passend zum Level.
    """
    ts_iso = datetime.fromtimestamp(
        n["timestamp"] / 1000, tz=timezone.utc
    ).isoformat()

    embed = {
        "title": n["head"],
        "description": n["body"],
        "color": LEVEL_COLORS.get(n["level"], LEVEL_COLORS["info"]),
        "timestamp": ts_iso,
        "fields": [
            {"name": "Tenant",   "value": str(n["tenant"]),   "inline": True},
            {"name": "Location", "value": str(n["location"]), "inline": True},
            {"name": "Client",   "value": str(n["client"]),   "inline": True},
            {"name": "Service",  "value": str(n["service"]),  "inline": True},
        ],
        "footer": {"text": "RAPALLE.net RMM"},
    }
    return {"embeds": [embed]}


def send_webhook(webhook: dict, notification: dict | str) -> None:
    """
    Schickt eine Benachrichtigung an einen Webhook.

    'notification' ist normalerweise ein Objekt aus build_notification()
    mit head/body/Kontext. Aus Bequemlichkeit wird auch ein einfacher String
    akzeptiert (wird dann automatisch zu einer Notification verpackt).

    - Discord: fertiges Embed-Template (head=Titel, body=Beschreibung,
      Kontextfelder, Timestamp, Farbe je nach Level).
    - Custom : generisches JSON, das IMMER 'head' und 'body' enthält, plus
      den kompletten Kontext.

    Läuft synchron in einem kurzen HTTP-Request (best effort).
    """
    if isinstance(notification, str):
        notification = build_notification(notification)

    # Eigene HTTP-Header des Webhooks (JSON-Objekt als Text in der DB).
    extra_headers = {}
    try:
        parsed = json.loads(webhook.get("headers") or "{}")
        if isinstance(parsed, dict):
            extra_headers = {str(k): str(v) for k, v in parsed.items()}
    except Exception:
        pass

    # Eigenes Body-Template: Platzhalter {head} {body} {message} {client}
    # {tenant} {location} {service} {level} {timestamp} werden ersetzt.
    template = (webhook.get("body_template") or "").strip()
    if webhook["type"] != "discord" and template:
        out = template
        for key in ("head", "body", "message", "client", "tenant",
                    "location", "service", "level", "timestamp"):
            val = str(notification.get(key, ""))
            # In JSON-Templates müssen Sonderzeichen escaped werden - wir
            # escapen wie ein JSON-String OHNE die umschließenden Quotes,
            # damit "text": "{body}" gültig bleibt.
            out = out.replace("{" + key + "}", json.dumps(val)[1:-1])
        data = out.encode("utf-8")
        content_type = extra_headers.pop("Content-Type", None) \
            or extra_headers.pop("content-type", None) or "application/json"
        req = urllib.request.Request(
            webhook["url"], data=data,
            headers={"Content-Type": content_type, "User-Agent": "Mozilla/5.0",
                     "Accept": "application/json", **extra_headers},
            method="POST")
        urllib.request.urlopen(req, timeout=10)
        return

    if webhook["type"] == "discord":
        payload = _discord_payload(notification)
    else:
        # Custom-Ziel: head + body sind garantiert vorhanden, dazu der Kontext.
        payload = {
            "head": notification["head"],
            "body": notification["body"],
            "tenant": notification["tenant"],
            "location": notification["location"],
            "client": notification["client"],
            "message": notification["message"],
            "service": notification["service"],
            "level": notification["level"],
            "timestamp": notification["timestamp"],
        }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook["url"],
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            **extra_headers,
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


@router.get("/webhooks")
def list_webhooks(user: dict = Depends(get_current_user)):
    require_admin(user)
    return db.list_webhooks()


@router.post("/webhooks")
def create_webhook(body: WebhookBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    w = db.create_webhook(body.name, body.url, body.type,
                          headers=body.headers, body_template=body.body_template)
    db.add_audit_entry(user["username"], "webhook.created", target=w["id"], details=body.name)
    return w


@router.patch("/webhooks/{webhook_id}")
def update_webhook(webhook_id: str, body: WebhookUpdateBody,
                         user: dict = Depends(get_current_user)):
    require_admin(user)
    if not db.get_webhook(webhook_id):
        raise HTTPException(404, "Webhook nicht gefunden")
    fields = body.model_dump(exclude_unset=True)
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    if fields.get("headers"):
        try:
            parsed = json.loads(fields["headers"])
            if not isinstance(parsed, dict):
                raise ValueError()
        except Exception:
            raise HTTPException(400, 'Header müssen ein JSON-Objekt sein, z.B. {"X-Api-Key": "..."}')
    updated = db.update_webhook(webhook_id, fields)
    db.add_audit_entry(user["username"], "webhook.updated", target=webhook_id)
    return updated


@router.post("/webhooks/{webhook_id}/test")
def test_webhook(webhook_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    webhook = db.get_webhook(webhook_id)
    if not webhook:
        raise HTTPException(404, "Webhook nicht gefunden")
    try:
        notification = build_notification(
            "This is a Test-Notification from RAPALLE.net RMM.",
            head="🔔 Test-Notification",
            tenant="Test-Tenant",
            location="Test-Location",
            client="Test-Client",
            service="Webhook-Test",
            level="info",
        )
        send_webhook(webhook, notification)
    except Exception as e:
        
        import traceback
        traceback.print_exc()

        raise HTTPException(502, f"Senden fehlgeschlagen: {e}")
    return {"ok": True}


@router.delete("/webhooks/{webhook_id}")
def delete_webhook(webhook_id: str, user: dict = Depends(get_current_user)):
    require_admin(user)
    db.delete_webhook(webhook_id)
    db.add_audit_entry(user["username"], "webhook.deleted", target=webhook_id)
    return {"ok": True}


# ==================================================================
# Globale Einstellungen (Allgemein-Tab)
# ==================================================================

class SettingsBody(BaseModel):
    # Alle optional: es werden nur die mitgeschickten Felder gespeichert.
    server_url: str | None = None
    server_host: str | None = None
    server_domain: str | None = None
    server_backend_port: int | None = None
    server_frontend_port: int | None = None
    host_lock_enabled: str | None = None
    host_lock_scope: str | None = None
    host_lock_extra: str | None = None
    host_lock_trust_proxy: str | None = None
    metrics_interval_seconds: int | None = None
    metrics_retention_hours: int | None = None
    replay_retention_days: int | None = None
    # Aufnahme-Einstellungen (Screen-Agent + Guacamole)
    recording_enabled: str | None = None
    screen_record_quality: int | None = None
    screen_record_fps: int | None = None
    guac_record_quality: int | None = None
    guac_record_fps: int | None = None
    guac_record_scale: float | None = None
    guacd_host: str | None = None
    guacd_port: int | None = None
    # Automatisches Agent-Update ("1" = an, "0" = aus; pro Client übersteuerbar)
    agent_auto_update: str | None = None
    # Auto-Update auch für Clients, die offline waren, sobald sie sich wieder
    # verbinden ("1" = an, Standard).
    agent_auto_update_offline: str | None = None
    # Spotify-Integration: Client-ID einer (vom Admin registrierten) Spotify-App.
    # Damit können sich Benutzer im Audio Player per OAuth (PKCE, ohne Secret)
    # anmelden und mit Premium volle Titel abspielen.
    spotify_client_id: str | None = None
    # Server-Selbst-Update (Settings -> Update)
    server_auto_update: str | None = None            # "1" | "0"
    server_auto_update_channel: str | None = None    # "commit" | "full" | "any"
    # --- VPN (WireGuard-kompatibel) ---
    # Port und Netz sind bewusst STRINGS: leer bedeutet "Standard verwenden",
    # und das liesse sich mit int nicht ausdrücken.
    vpn_enabled: str | None = None
    vpn_port: str | None = None
    vpn_subnet: str | None = None
    vpn_endpoint_host: str | None = None
    vpn_dns: str | None = None
    vpn_mtu: str | None = None


# Diese Schlüssel gelten als ADMIN-Einstellungen (IP/Port, Server-Update,
# guacd-Adresse). Alles andere sind "Standard-Einstellungen" (manage_settings).
ADMIN_SETTING_KEYS = {
    "server_host", "server_domain", "server_backend_port", "server_frontend_port",
    "server_url", "server_auto_update", "server_auto_update_channel",
    "guacd_host", "guacd_port",
    # Host-Sperre steuert, wer den Dienst überhaupt erreicht - klar
    # Admin-Sache, nicht 'manage_settings'.
    "host_lock_enabled", "host_lock_scope", "host_lock_extra",
    "host_lock_trust_proxy",
    # Der VPN-Endpunkt oeffnet einen Port nach aussen und bestimmt, wer ins
    # Netz der Clients kommt - das ist Admin-Sache, nicht 'manage_settings'.
    "vpn_enabled", "vpn_port", "vpn_subnet", "vpn_endpoint_host",
    "vpn_dns", "vpn_mtu",
}


@router.get("/hostlock/status")
def hostlock_status(request: Request, user: dict = Depends(get_current_user)):
    """
    Zeigt, welchen Host der Server bei DIESER Anfrage sieht und welche
    Adressen erlaubt sind. Erste Anlaufstelle, wenn die Sperre nicht greift.
    """
    require_perm(user, "see_settings")
    from app import hostlock
    return hostlock.diagnostics(request)


@router.get("/settings")
def get_settings(user: dict = Depends(get_current_user)):
    require_perm(user, "see_settings")
    return db.get_all_settings()


@router.put("/settings")
def update_settings(body: SettingsBody, user: dict = Depends(get_current_user)):
    changed = body.model_dump(exclude_none=True)
    # Pro Schlüssel das passende Recht verlangen: Admin-Keys brauchen
    # 'admin_settings', alle übrigen 'manage_settings'.
    if any(k in ADMIN_SETTING_KEYS for k in changed):
        require_perm(user, "admin_settings")
    if any(k not in ADMIN_SETTING_KEYS for k in changed):
        require_perm(user, "manage_settings")
    # server_url darf bewusst auf "" gesetzt werden (= wieder automatisch).
    for key, value in changed.items():
        db.set_setting(key, value)
    db.add_audit_entry(user["username"], "settings.updated", details=",".join(changed.keys()))
    return db.get_all_settings()


# ==================================================================
# Automationen
# ==================================================================

class AutomationBody(BaseModel):
    name: str
    command: str
    client_ids: list[str] = []
    interval_seconds: int


@router.get("/automations")
def list_automations(user: dict = Depends(get_current_user)):
    require_perm(user, "automation")
    autos = db.list_automations()
    for a in autos:
        a["client_ids"] = [c for c in (a["client_ids"] or "").split(",") if c]
    return autos


@router.post("/automations")
def create_automation(body: AutomationBody, user: dict = Depends(get_current_user)):
    require_perm(user, "automation")
    a = db.create_automation(body.name, body.command, body.client_ids, body.interval_seconds)
    db.add_audit_entry(user["username"], "automation.created", target=a["id"], details=body.name)
    return a


@router.post("/automations/{auto_id}/toggle")
def toggle_automation(auto_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "automation")
    auto = db.get_automation(auto_id)
    if not auto:
        raise HTTPException(404, "Automation nicht gefunden")
    db.set_automation_enabled(auto_id, not auto["enabled"])
    return {"ok": True, "enabled": not auto["enabled"]}


@router.delete("/automations/{auto_id}")
def delete_automation(auto_id: str, user: dict = Depends(get_current_user)):
    require_perm(user, "automation")
    db.delete_automation(auto_id)
    db.add_audit_entry(user["username"], "automation.deleted", target=auto_id)
    return {"ok": True}


@router.get("/automations/{auto_id}/runs")
def get_automation_runs(auto_id: str, user: dict = Depends(get_current_user)):
    """Liefert die letzten Durchläufe einer Automation mit Ergebnis je Client."""
    require_perm(user, "automation")
    return {"runs": db.list_automation_runs(auto_id)}


# ---------------------------------------------------------------------------
# Branding: Logos/Bilder per Upload ersetzen
# ---------------------------------------------------------------------------
# Die Dateien liegen im Frontend-Ordner (werden von StaticFiles ausgeliefert).
# Upload als ROHER Request-Body (kein multipart) - so brauchen wir keine
# zusätzliche Abhängigkeit (python-multipart) und der Fetch im Frontend
# bleibt ein Einzeiler (body: file).

from pathlib import Path as _Path

_IMAGES_DIR = _Path(__file__).resolve().parents[3] / "frontend" / "images"
# Hochgeladene Branding-Dateien landen in einem SCHREIBBAREN Datenordner neben
# der Datenbank (das gebündelte frontend/images ist in vielen Deployments
# read-only, z.B. per Container-Image oder nginx). Beim Ausliefern wird zuerst
# hier gesucht, dann im gebündelten Default.
_BRANDING_STORE = _Path(__file__).resolve().parents[2] / "branding"


def branding_path(name: str):
    """Pfad zu einem Bild: bevorzugt der Upload-Store, sonst das gebündelte
    Default aus frontend/images. Gibt None zurück, wenn nichts existiert."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    up = _BRANDING_STORE / name
    if up.is_file():
        return up
    d = _IMAGES_DIR / name
    return d if d.is_file() else None

# Whitelist: Slot-Name -> (Dateiname, erlaubte Magic-Bytes, Beschreibung)
_PNG = (b"\x89PNG\r\n\x1a\n",)
_JPG = (b"\xff\xd8\xff",)
_ICO = (b"\x00\x00\x01\x00", b"\x89PNG\r\n\x1a\n")  # .ico oder PNG-als-Favicon
BRANDING_SLOTS: dict[str, dict] = {
    "logo_r.png":   {"magic": _PNG, "label": "Logo klein (Topbar + Browser-Icon)"},
    "logo.png":     {"magic": _PNG, "label": "Logo Standard"},
    "logo_big.png": {"magic": _PNG, "label": "Logo groß"},
    "login-bg.jpg": {"magic": _JPG, "label": "Login-Hintergrundbild"},
    "favicon.ico":  {"magic": _ICO, "label": "Favicon (.ico)"},
}
_MAX_BRANDING_BYTES = 8 * 1024 * 1024  # 8 MB reichen für jedes Logo/Hintergrundbild


@router.get("/branding")
def list_branding(user: dict = Depends(get_current_user)):
    """Listet alle austauschbaren Branding-Dateien inkl. Änderungszeit (Cache-Busting)."""
    require_perm(user, "manage_branding")
    slots = []
    for name, meta in BRANDING_SLOTS.items():
        f = branding_path(name)
        slots.append({
            "name": name,
            "label": meta["label"],
            "url": f"/images/{name}",
            "exists": f is not None,
            "mtime": int(f.stat().st_mtime) if f is not None else 0,
        })
    return {"slots": slots}


@router.post("/branding/{name}")
async def upload_branding(name: str, request: Request, user: dict = Depends(get_current_user)):
    """
    Ersetzt eine Branding-Datei. Body = rohe Bilddaten (kein multipart).
    Akzeptiert ALLE gängigen Bildformate und konvertiert serverseitig (Pillow)
    ins Ziel-Format des Slots (PNG/JPEG/ICO). Nur Whitelist-Dateinamen,
    Größenlimit, atomares Ersetzen - die alte Datei bleibt bei Fehlern intakt.
    """
    require_perm(user, "manage_branding")
    slot = BRANDING_SLOTS.get(name)
    if not slot:
        raise HTTPException(404, f"Unbekannter Branding-Slot. Erlaubt: {', '.join(BRANDING_SLOTS)}")

    data = await request.body()
    if not data:
        raise HTTPException(400, "Leerer Upload")
    if len(data) > _MAX_BRANDING_BYTES:
        raise HTTPException(413, "Datei zu groß (max. 8 MB)")

    # ALLE Bildformate akzeptieren (PNG, JPEG, WebP, GIF, BMP, ICO, TIFF, ...)
    # und serverseitig ins Ziel-Format des Slots konvertieren. Die frühere
    # Magic-Byte-Prüfung lehnte Dateien teils fälschlich ab - jetzt entscheidet
    # Pillow, ob es ein lesbares Bild ist, und die Konvertierung garantiert
    # das korrekte Format auf der Platte.
    try:
        from PIL import Image
    except ImportError:
        raise HTTPException(500, "Pillow ist nicht installiert - bitte Backend-"
                                 "Abhängigkeiten aktualisieren (requirements.txt).")
    import io as _io
    try:
        img = Image.open(_io.BytesIO(data))
        img.load()
    except Exception as e:
        raise HTTPException(400, f"Die Datei ist kein lesbares Bild "
                                 f"({e.__class__.__name__}: {e}).")

    target_ext = name.rsplit(".", 1)[-1].lower()
    out = _io.BytesIO()
    try:
        if target_ext == "png":
            img.convert("RGBA").save(out, format="PNG", optimize=True)
        elif target_ext in ("jpg", "jpeg"):
            # JPEG kann kein Alpha: transparente Bereiche auf Weiß legen.
            if "A" in img.getbands():
                rgba = img.convert("RGBA")
                base = Image.new("RGB", rgba.size, (255, 255, 255))
                base.paste(rgba, mask=rgba.split()[-1])
                base.save(out, format="JPEG", quality=90)
            else:
                img.convert("RGB").save(out, format="JPEG", quality=90)
        elif target_ext == "ico":
            ic = img.convert("RGBA")
            sizes = [(s, s) for s in (16, 32, 48, 64) if s <= max(ic.size)] or [(16, 16)]
            ic.save(out, format="ICO", sizes=sizes)
        else:
            # Unbekannter Slot-Typ: Original unverändert speichern.
            out.write(data)
    except Exception as e:
        raise HTTPException(400, f"Konvertierung nach {target_ext.upper()} "
                                 f"fehlgeschlagen ({e.__class__.__name__}: {e}).")
    data = out.getvalue()
    src_fmt = (img.format or "unbekannt")

    try:
        _BRANDING_STORE.mkdir(parents=True, exist_ok=True)
        tmp = _BRANDING_STORE / (name + ".tmp-upload")
        tmp.write_bytes(data)
        os.replace(tmp, _BRANDING_STORE / name)  # atomar - nie halb geschriebene Logos
    except OSError as e:
        # Klartext-Fehler statt generischem 500 - z.B. wenn backend/ read-only
        # gemountet ist oder der Dienst-Benutzer keine Schreibrechte hat.
        print(f"[branding] Upload von '{name}' fehlgeschlagen: {e}")
        raise HTTPException(500, f"Speichern fehlgeschlagen ({e.__class__.__name__}: {e}). "
                                 f"Hat der Backend-Prozess Schreibrechte auf {_BRANDING_STORE}?")
    print(f"[branding] '{name}' ersetzt ({src_fmt} -> {name.rsplit('.', 1)[-1].upper()}, "
          f"{len(data)} bytes) -> {_BRANDING_STORE / name}")

    db.add_audit_entry(user["username"], "branding.updated", target=name,
                       details=f"{src_fmt} -> {name.rsplit('.', 1)[-1].upper()}, {len(data)} bytes")
    return {"ok": True, "name": name, "size": len(data),
            "mtime": int((_BRANDING_STORE / name).stat().st_mtime)}


# ------------------------------------------------------------------
# Standard-Layouts (Dashboard-Widgets & Client-Panels) für ALLE Nutzer
# ------------------------------------------------------------------
# Ein Admin kann sein aktuelles Layout als organisationsweiten Standard
# speichern. Jeder Nutzer bekommt diesen Standard, sobald er noch kein eigenes
# Layout hat ODER wenn er in der Oberfläche bewusst "auf Standard zurücksetzen"
# klickt. Bestehende eigene Layouts bleiben unangetastet - man kann also
# weiterarbeiten. Gespeichert wird als JSON im settings-KV-Store.

class DefaultLayoutBody(BaseModel):
    kind: str            # "dash" (Client-Panels) | "fleet" (Dashboard-Widgets)
    layout: dict | list  # das komplette Layout-Objekt bzw. die Widget-Liste


_LAYOUT_SETTING_KEYS = {
    "dash": "default_layout_dash",
    "fleet": "default_layout_fleet",
    # Organisationsweite Layout-Profil-PRESETS für die Client-Ansicht
    # (Settings -> Profil "Physisch"/"VMs"/"LXCs"; vom Admin überschreibbar).
    "dash_profile_physical": "default_layout_dash_profile_physical",
    "dash_profile_vm": "default_layout_dash_profile_vm",
    "dash_profile_lxc": "default_layout_dash_profile_lxc",
}


@router.get("/default-layouts")
def get_default_layouts(user: dict = Depends(get_current_user)):
    """
    Liefert die organisationsweiten Standard-Layouts (oder null, falls keiner
    gesetzt ist). Für JEDEN eingeloggten Nutzer lesbar - das Frontend braucht
    sie beim ersten Start und beim Zurücksetzen.
    """
    out = {}
    for kind, key in _LAYOUT_SETTING_KEYS.items():
        raw = db.get_setting(key)
        if raw:
            try:
                out[kind] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                out[kind] = None
        else:
            out[kind] = None
    return out


@router.post("/default-layouts")
def set_default_layout(body: DefaultLayoutBody, user: dict = Depends(get_current_user)):
    """Speichert ein Layout als neuen organisationsweiten Standard (nur Admin)."""
    require_admin(user)
    key = _LAYOUT_SETTING_KEYS.get(body.kind)
    if not key:
        raise HTTPException(400, "Ungültige Layout-Art (dash|fleet)")
    db.set_setting(key, json.dumps(body.layout))
    db.add_audit_entry(user["username"], "settings.default_layout_set", target=body.kind)
    return {"ok": True, "kind": body.kind}


@router.delete("/default-layouts/{kind}")
def clear_default_layout(kind: str, user: dict = Depends(get_current_user)):
    """Entfernt den organisationsweiten Standard wieder (nur Admin)."""
    require_admin(user)
    key = _LAYOUT_SETTING_KEYS.get(kind)
    if not key:
        raise HTTPException(400, "Ungültige Layout-Art (dash|fleet)")
    db.set_setting(key, "")
    db.add_audit_entry(user["username"], "settings.default_layout_cleared", target=kind)
    return {"ok": True, "kind": kind}

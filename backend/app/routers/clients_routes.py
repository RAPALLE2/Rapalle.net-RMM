"""
routers/clients_routes.py
----------------------------
Alles rund um die verwalteten Geräte (Clients) selbst.

Endpunkte:
  GET    /api/clients                 -> alle Clients + Online-Status + Live-Metriken
  GET    /api/clients/{id}            -> ein einzelner Client
  PUT    /api/clients/{id}            -> Client bearbeiten (Edit-Dialog:
                                          Tenant/Location/Name/Farbe/aktiv/...)
  DELETE /api/clients/{id}            -> Client löschen
  POST   /api/clients/{id}/exec       -> Shell-Befehl auf dem Client ausführen (Terminal)
  POST   /api/clients/bulk-exec       -> Shell-Befehl auf MEHREREN Clients gleichzeitig
  GET    /api/clients/{id}/fs         -> Ordnerinhalt auf dem Client auflisten (File Station)
"""

import asyncio

import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app import db
from app.auth import (
    get_current_user, require_perm, can_access_client,
    visible_client_ids, user_has_permission,
)
from app.sockets import state, request_exec, request_fs_list, request_proc_list, request_proc_kill, request_fs_read, request_fs_op, send_to_agent


def _require_client_perm(user: dict, client_id: str, perm: str) -> None:
    """Prüft ein client-bezogenes Recht; wirft 404 statt 403, wenn der Client
    für den Benutzer gar nicht sichtbar ist (versteckte Clients bleiben verborgen)."""
    if not can_access_client(user, client_id):
        raise HTTPException(404, "Client nicht gefunden")
    require_perm(user, perm, client_id)

router = APIRouter(prefix="/api/clients", tags=["clients"])


# ==================================================================
# Client-Websites (Quick Access + Favoriten + Uptime-Monitoring)
# ==================================================================

# Erlaubte Benachrichtigungs-Modi für das Uptime-Monitoring:
#   'up'     -> Benachrichtigung, wenn ein Scan ERFOLGREICH war (bei Statuswechsel)
#   'down'   -> Benachrichtigung, wenn ein Scan FEHLGESCHLAGEN ist (bei Statuswechsel)
#   'always' -> Benachrichtigung nach JEDEM Scan (Erfolg und Fehler)
_NOTIFY_MODES = {"up", "down", "always"}


class WebsiteBody(BaseModel):
    name: str
    url: str
    favorite: bool = False
    monitor_enabled: bool = False
    monitor_notify: str = "down"              # 'up' | 'down' | 'always'
    monitor_interval_seconds: int = 300       # Delay zwischen den Scans


class WebsiteUpdateBody(BaseModel):
    name: str | None = None
    url: str | None = None
    favorite: bool | None = None
    monitor_enabled: bool | None = None
    monitor_notify: str | None = None
    monitor_interval_seconds: int | None = None


def _validate_website(url: str | None, notify: str | None, interval: int | None) -> None:
    if url is not None and not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "Die URL muss mit http:// oder https:// beginnen")
    if notify is not None and notify not in _NOTIFY_MODES:
        raise HTTPException(400, "monitor_notify muss 'up', 'down' oder 'always' sein")
    if interval is not None and interval < 10:
        raise HTTPException(400, "Das Scan-Intervall muss mindestens 10 Sekunden betragen")


# Wichtig: Literal-Route VOR den /{client_id}/...-Routen definieren.
@router.get("/websites/favorites")
async def get_favorite_websites(user: dict = Depends(get_current_user)):
    """Alle als Favorit angehefteten Websites - gefiltert auf Clients,
    die der Benutzer überhaupt sehen darf."""
    favs = db.list_favorite_websites()
    visible = visible_client_ids(user, [w["client_id"] for w in favs])
    return [w for w in favs if w["client_id"] in visible]


@router.get("/{client_id}/websites")
async def get_client_websites(client_id: str, user: dict = Depends(get_current_user)):
    if not db.get_client(client_id) or not can_access_client(user, client_id):
        raise HTTPException(404, "Client nicht gefunden")
    return db.list_client_websites(client_id)


@router.post("/{client_id}/websites")
async def create_client_website(client_id: str, body: WebsiteBody,
                                user: dict = Depends(get_current_user)):
    if not db.get_client(client_id):
        raise HTTPException(404, "Client nicht gefunden")
    _require_client_perm(user, client_id, "manage_clients")
    _validate_website(body.url, body.monitor_notify, body.monitor_interval_seconds)

    w = db.create_client_website(
        client_id, body.name.strip(), body.url.strip(),
        favorite=body.favorite, monitor_enabled=body.monitor_enabled,
        monitor_notify=body.monitor_notify,
        monitor_interval_seconds=body.monitor_interval_seconds,
    )
    db.add_audit_entry(user["username"], "website.created", target=client_id,
                       details=f"{body.name} ({body.url})")
    return w


@router.put("/{client_id}/websites/{website_id}")
async def update_client_website(client_id: str, website_id: str, body: WebsiteUpdateBody,
                                user: dict = Depends(get_current_user)):
    _require_client_perm(user, client_id, "manage_clients")
    site = db.get_client_website(website_id)
    if not site or site["client_id"] != client_id:
        raise HTTPException(404, "Website nicht gefunden")

    fields = body.model_dump(exclude_unset=True)
    _validate_website(fields.get("url"), fields.get("monitor_notify"),
                      fields.get("monitor_interval_seconds"))
    for key in ("favorite", "monitor_enabled"):
        if key in fields:
            fields[key] = int(fields[key])

    updated = db.update_client_website(website_id, fields)
    db.add_audit_entry(user["username"], "website.updated", target=client_id, details=str(fields))
    return updated


@router.delete("/{client_id}/websites/{website_id}")
async def delete_client_website(client_id: str, website_id: str,
                                user: dict = Depends(get_current_user)):
    _require_client_perm(user, client_id, "manage_clients")
    site = db.get_client_website(website_id)
    if not site or site["client_id"] != client_id:
        raise HTTPException(404, "Website nicht gefunden")
    db.delete_client_website(website_id)
    db.add_audit_entry(user["username"], "website.deleted", target=client_id,
                       details=site.get("name"))
    return {"ok": True}


@router.get("/{client_id}/metrics/history")
async def get_client_metrics_history(client_id: str, user: dict = Depends(get_current_user)):
    """
    Liefert die gespeicherte Metrik-Historie eines Clients. Das Frontend nutzt
    das, um die Graphen nach einem Seiten-Neuladen sofort mit Verlaufsdaten zu
    füllen, statt bei 0 anzufangen.

    metrics_retention_hours = 0 (Standard) -> KOMPLETTE Historie zurückgeben.
    Sonst nur die letzten N Stunden.
    """
    retention_hours = db.get_int_setting("metrics_retention_hours")
    since_ts = None
    if retention_hours > 0:
        since_ts = int(time.time() * 1000) - retention_hours * 3600 * 1000
    return {"points": db.get_metrics_history(client_id, since_ts)}


class ExecBody(BaseModel):
    command: str
    session: str | None = None   # Terminal-Session (für persistentes Arbeitsverzeichnis)
    shell: str = "auto"          # 'cmd' | 'powershell' | 'auto'
    elevated: bool = False       # als Administrator (nur Windows)


class BulkExecBody(BaseModel):
    client_ids: list[str]
    command: str


class UpdateClientBody(BaseModel):
    hostname: str | None = None
    tenant_id: str | None = None
    location_id: str | None = None
    folder_id: str | None = None
    parent_client_id: str | None = None
    color: str | None = None
    notes: str | None = None
    status_override: str | None = None   # z.B. "maintenance" oder null zum Zurücksetzen
    active: bool | None = None
    # WICHTIG: fehlte bisher! Pydantic verwirft unbekannte Felder - dadurch
    # wurde eine im Edit-Dialog gewählte VM/LXC-Einstufung NIE gespeichert und
    # der Dialog zeigte nach jedem Neuladen wieder "Physisches Gerät".
    device_type: str | None = None       # physical | vm | lxc


def _with_live_state(c: dict) -> dict:
    """Reichert einen Client-Datensatz aus der DB um Online-Status + Live-Metriken an."""
    return {
        **c,
        "online": state.is_online(c["id"]),
        "metrics": state.live_metrics.get(c["id"]),
    }


@router.get("")
async def get_clients(user: dict = Depends(get_current_user)):
    clients = db.list_clients()
    visible = visible_client_ids(user, [c["id"] for c in clients])
    return [_with_live_state(c) for c in clients if c["id"] in visible]


@router.get("/{client_id}")
async def get_client(client_id: str, user: dict = Depends(get_current_user)):
    c = db.get_client(client_id)
    if not c or not can_access_client(user, client_id):
        raise HTTPException(404, "Client nicht gefunden")
    return _with_live_state(c)


@router.put("/{client_id}")
async def update_client(client_id: str, body: UpdateClientBody, user: dict = Depends(get_current_user)):
    if not db.get_client(client_id):
        raise HTTPException(404, "Client nicht gefunden")
    _require_client_perm(user, client_id, "manage_clients")

    # Nur die tatsächlich mitgeschickten Felder übernehmen
    fields = body.model_dump(exclude_unset=True)
    if "active" in fields:
        fields["active"] = int(fields["active"])  # SQLite kennt kein echtes Bool
    if "device_type" in fields and fields["device_type"] not in ("physical", "vm", "lxc", None):
        raise HTTPException(400, "Ungültiger Gerätetyp (physical|vm|lxc)")

    updated = db.update_client(client_id, fields)
    db.add_audit_entry(user["username"], "client.updated", target=client_id, details=str(fields))
    return _with_live_state(updated)


@router.delete("/{client_id}")
async def remove_client(client_id: str, user: dict = Depends(get_current_user)):
    _require_client_perm(user, client_id, "manage_clients")
    db.delete_client(client_id)
    db.add_audit_entry(user["username"], "client.deleted", target=client_id)
    return {"ok": True}


@router.post("/{client_id}/exec")
async def exec_on_client(client_id: str, body: ExecBody, user: dict = Depends(get_current_user)):
    _require_client_perm(user, client_id, "use_terminal")
    try:
        result = await request_exec(client_id, body.command, session=body.session,
                                    shell=body.shell, elevated=body.elevated)
    except Exception as e:
        raise HTTPException(504, str(e))

    # Jeder ausgeführte Befehl landet im Audit-Log (wie gewünscht: "wer hat wann was gemacht")
    _mode = f"[{body.shell}{'/admin' if body.elevated else ''}] " if body.shell != "auto" or body.elevated else ""
    db.add_audit_entry(user["username"], "terminal.exec", target=client_id, details=_mode + body.command)
    return result


@router.post("/bulk-exec")
async def bulk_exec(body: BulkExecBody, user: dict = Depends(get_current_user)):
    """
    Führt denselben Befehl auf mehreren Clients gleichzeitig aus (Bulk Terminal).
    Alle Anfragen laufen parallel (asyncio.gather), das Ergebnis ist eine
    Zuordnung Client-ID -> Ergebnis (oder Fehlermeldung, falls einer offline ist).
    """
    # Nur Clients, auf die der Benutzer Terminal-Rechte hat (Rest wird verworfen).
    allowed_ids = [cid for cid in body.client_ids
                   if user_has_permission(user, "use_terminal", cid)]

    async def run_one(client_id: str) -> tuple[str, dict]:
        try:
            result = await request_exec(client_id, body.command)
            return client_id, {"ok": True, **result}
        except Exception as e:
            return client_id, {"ok": False, "error": str(e)}

    results = await asyncio.gather(*(run_one(cid) for cid in allowed_ids))
    db.add_audit_entry(
        user["username"], "terminal.bulk_exec",
        target=",".join(body.client_ids), details=body.command,
    )
    return dict(results)


@router.get("/{client_id}/fs")
async def list_client_fs(client_id: str, path: str = "", user: dict = Depends(get_current_user)):
    _require_client_perm(user, client_id, "use_explorer")
    try:
        entries = await request_fs_list(client_id, path)
    except Exception as e:
        raise HTTPException(504, str(e))
    return {"path": path, "entries": entries}


@router.get("/{client_id}/processes")
async def list_client_processes(client_id: str, user: dict = Depends(get_current_user)):
    """Liefert die laufende Prozessliste eines Clients (Task-Manager)."""
    _require_client_perm(user, client_id, "use_taskmanager")
    try:
        processes = await request_proc_list(client_id)
    except Exception as e:
        raise HTTPException(504, str(e))
    return {"processes": processes}


class KillBody(BaseModel):
    pid: int


@router.post("/{client_id}/processes/kill")
async def kill_client_process(client_id: str, body: KillBody, user: dict = Depends(get_current_user)):
    """Beendet einen Prozess auf einem Client."""
    _require_client_perm(user, client_id, "use_taskmanager")
    try:
        result = await request_proc_kill(client_id, body.pid)
    except Exception as e:
        raise HTTPException(504, str(e))
    db.add_audit_entry(user["username"], "process.kill", target=client_id, details=f"PID {body.pid}")
    return result


@router.get("/{client_id}/fs/read")
async def read_client_file(client_id: str, path: str, user: dict = Depends(get_current_user)):
    """
    Liest eine Datei vom Client und gibt sie base64-kodiert zurück.
    Das Frontend baut daraus einen Download. (Für kleinere Dateien gedacht;
    der Agent begrenzt die Größe auf 25 MB.)
    """
    _require_client_perm(user, client_id, "use_explorer")
    try:
        result = await request_fs_read(client_id, path)
    except Exception as e:
        raise HTTPException(504, str(e))
    db.add_audit_entry(user["username"], "file.download", target=client_id, details=path)
    return result


# ------------------------------------------------------------------
# Schreibende Datei-Operationen auf einem Client (Upload/Ordner/Löschen/…)
# Alle brauchen das Recht "use_explorer".
# ------------------------------------------------------------------

class FsWriteBody(BaseModel):
    path: str          # Zielpfad inkl. Dateiname
    data: str          # base64-kodierter Inhalt


class FsPathBody(BaseModel):
    path: str


class FsRenameBody(BaseModel):
    src: str
    dst: str


@router.post("/{client_id}/fs/write")
async def write_client_file(client_id: str, body: FsWriteBody, user: dict = Depends(get_current_user)):
    """Lädt eine Datei hoch oder schreibt eine editierte Datei zurück."""
    _require_client_perm(user, client_id, "use_explorer")
    try:
        result = await request_fs_op(client_id, "fs-write", {"path": body.path, "data": body.data})
    except Exception as e:
        raise HTTPException(504, str(e))
    db.add_audit_entry(user["username"], "file.upload", target=client_id, details=body.path)
    return result


@router.post("/{client_id}/fs/mkdir")
async def mkdir_client(client_id: str, body: FsPathBody, user: dict = Depends(get_current_user)):
    _require_client_perm(user, client_id, "use_explorer")
    try:
        result = await request_fs_op(client_id, "fs-mkdir", {"path": body.path})
    except Exception as e:
        raise HTTPException(504, str(e))
    db.add_audit_entry(user["username"], "file.mkdir", target=client_id, details=body.path)
    return result


@router.post("/{client_id}/fs/delete")
async def delete_client_path(client_id: str, body: FsPathBody, user: dict = Depends(get_current_user)):
    _require_client_perm(user, client_id, "use_explorer")
    try:
        result = await request_fs_op(client_id, "fs-delete", {"path": body.path})
    except Exception as e:
        raise HTTPException(504, str(e))
    db.add_audit_entry(user["username"], "file.delete", target=client_id, details=body.path)
    return result


@router.post("/{client_id}/fs/rename")
async def rename_client_path(client_id: str, body: FsRenameBody, user: dict = Depends(get_current_user)):
    _require_client_perm(user, client_id, "use_explorer")
    try:
        result = await request_fs_op(client_id, "fs-rename", {"src": body.src, "dst": body.dst})
    except Exception as e:
        raise HTTPException(504, str(e))
    db.add_audit_entry(user["username"], "file.rename", target=client_id,
                       details=f"{body.src} -> {body.dst}")
    return result


@router.post("/{client_id}/update-agent")
async def update_client_agent(client_id: str, user: dict = Depends(get_current_user)):
    """
    Weist den Agenten an, sich selbst zu aktualisieren: Er startet den
    Client-Update-Befehl in einer eigenen Shell-Session (lädt das aktuelle
    Agent-Paket, ersetzt agent.py und startet den Dienst neu). Nach dem Neustart
    meldet der Agent "updated" zurück.

    Der Endpunkt wartet bis zu 60 Sekunden auf diese Bestätigung:
      - kommt sie -> Update erfolgreich.
      - kommt sie nicht -> Fehler (das Update ist vermutlich fehlgeschlagen).
    """
    _require_client_perm(user, client_id, "manage_agent")
    if not state.is_online(client_id):
        raise HTTPException(409, "Der Agent ist nicht verbunden.")

    client = db.get_client(client_id)
    # Eventuelle alte Bestätigung verwerfen, damit wir nur auf die neue reagieren.
    state.update_confirmed.pop(client_id, None)

    ok = await send_to_agent(client_id, "update-agent", {})
    if not ok:
        raise HTTPException(503, "Client ist offline")
    db.add_audit_entry(user["username"], "agent.update_triggered", target=client_id,
                       details=(client or {}).get("hostname"))

    # Auf "updated"-Bestätigung warten (max. 60 s, alle 1 s prüfen).
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        if client_id in state.update_confirmed:
            state.update_confirmed.pop(client_id, None)
            db.add_audit_entry(user["username"], "agent.updated", target=client_id,
                               details=(client or {}).get("hostname"))
            return {"ok": True, "updated": True}

    db.add_audit_entry(user["username"], "agent.update_timeout", target=client_id,
                       details=(client or {}).get("hostname"))
    raise HTTPException(
        504,
        "Der Agent hat innerhalb von 60 Sekunden kein erfolgreiches Update "
        "bestätigt. Das Update ist möglicherweise fehlgeschlagen - bitte den "
        "Client bzw. das Log /tmp/rapalle-agent-update.log prüfen.",
    )


@router.post("/{client_id}/uninstall-agent")
async def uninstall_client_agent(client_id: str, user: dict = Depends(get_current_user)):
    """
    Deinstalliert den Agenten vollständig und entfernt den Client aus dem
    Dashboard - aber NUR, wenn er wirklich verschwindet. Ablauf:

      1. Uninstall-Befehl an den Agenten schicken (stoppt den Dienst und löscht
         ALLE Agent-Daten auf dem Client, siehe agent.py / uninstall.sh|ps1).
      2. Bis zu 60 Sekunden warten, bis der Client wirklich offline geht
         (= der Agent-Prozess ist beendet, die Socket-Verbindung getrennt).
      3a. Geht er in dieser Zeit offline -> Client aus der Datenbank entfernen
          (verschwindet aus dem Dashboard) und Erfolg melden.
      3b. Ist er nach 60 s IMMER NOCH online -> Fehlermeldung; der Client wird
          NICHT entfernt (die Deinstallation ist offenbar fehlgeschlagen).
    """
    _require_client_perm(user, client_id, "manage_agent")

    client = db.get_client(client_id)
    if not client:
        raise HTTPException(404, "Client nicht gefunden")

    # Der Agent muss verbunden sein, damit er den Uninstall-Befehl empfangen und
    # ausführen kann. Einen bereits offline/toten Client entfernt man über den
    # normalen "Löschen"-Button im Bearbeiten-Dialog.
    if not state.is_online(client_id):
        raise HTTPException(
            409,
            "Der Agent ist nicht verbunden. Eine Fern-Deinstallation ist nur bei "
            "verbundenem Agent möglich. Einen bereits offline Client kannst du über "
            "'Bearbeiten → Löschen' aus dem Dashboard entfernen.",
        )

    # 1. Uninstall anstoßen
    ok = await send_to_agent(client_id, "uninstall-agent", {})
    if not ok:
        raise HTTPException(503, "Client ist offline")
    db.add_audit_entry(user["username"], "agent.uninstall_triggered", target=client_id,
                       details=client.get("hostname"))

    # 2. Auf "offline" warten (max. 60 s, alle 2 s prüfen).
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        await asyncio.sleep(2.0)
        if not state.is_online(client_id):
            # 3a. Wirklich weg -> aus dem Dashboard entfernen.
            db.delete_client(client_id)
            db.add_audit_entry(user["username"], "agent.uninstalled", target=client_id,
                               details=client.get("hostname"))
            # Alle Dashboards über die geänderte Client-Liste informieren.
            from app.sockets import sio
            await sio.emit("clients:changed", namespace="/dashboard")
            return {"ok": True, "removed": True, "hostname": client.get("hostname")}

    # 3b. Timeout: Agent ist nicht offline gegangen -> Fehler, Client bleibt.
    db.add_audit_entry(user["username"], "agent.uninstall_timeout", target=client_id,
                       details=client.get("hostname"))
    raise HTTPException(
        504,
        "Der Agent ist nicht innerhalb von 60 Sekunden offline gegangen. "
        "Die Deinstallation ist möglicherweise fehlgeschlagen - der Client wurde "
        "NICHT aus dem Dashboard entfernt. Bitte den Client prüfen und ggf. erneut versuchen.",
    )


@router.get("/{client_id}/rdp-file")
async def get_rdp_file(client_id: str, user: dict = Depends(get_current_user)):
    """
    Erzeugt eine .rdp-Datei mit der IP-Adresse des Clients. Der Benutzer öffnet
    sie, woraufhin sich der native Windows-RDP-Client (mstsc) verbindet.

    Das ist der zuverlässige Weg für headless VMs: RDP erzeugt selbst eine
    Grafiksitzung, die man dann fernsteuern kann - im Gegensatz zu unserem
    Bildschirm-Streaming, das eine bereits vorhandene Sitzung voraussetzt.
    """
    client = db.get_client(client_id)
    if not client or not can_access_client(user, client_id):
        raise HTTPException(404, "Client nicht gefunden")
    require_perm(user, "use_screen", client_id)

    host = client.get("ip")
    if not host:
        raise HTTPException(400, "Keine IP-Adresse für diesen Client bekannt")

    # Minimale, gängige .rdp-Konfiguration. Der Benutzer gibt beim Verbinden
    # selbst Anmeldedaten ein (wir speichern keine Passwörter in der Datei).
    rdp_content = "\r\n".join([
        "screen mode id:i:2",              # Vollbild
        "use multimon:i:0",
        "desktopwidth:i:1280",
        "desktopheight:i:720",
        "session bpp:i:32",
        f"full address:s:{host}",
        "audiomode:i:0",
        "redirectclipboard:i:1",           # Zwischenablage teilen
        "autoreconnection enabled:i:1",
        "authentication level:i:0",        # keine strikte Server-Auth (Lab-freundlich)
        "prompt for credentials:i:1",      # Anmeldedaten abfragen
        "negotiate security layer:i:1",
        "",
    ])

    db.add_audit_entry(user["username"], "rdp.file_generated", target=client_id, details=host)
    filename = f"{client.get('hostname', 'client')}.rdp"
    return PlainTextResponse(
        rdp_content,
        media_type="application/x-rdp",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

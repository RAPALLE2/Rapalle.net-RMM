# client_routes.py (Modul "client")
# ---------------------------------
# Schnittstelle fuer die Client-App (src/client) und die Profil-Seite.
#
# Anmeldung / Geraete
#   POST   /api/client/register          (normales Login-Token) {name, platform, os, app_version}
#                                        -> {device, device_token, access_token}
#   POST   /api/client/session           {device_token, app_version?} -> {access_token, user, device}
#   GET    /api/client/devices           meine Geraete (current = dieses Geraet)
#   PATCH  /api/client/devices/{id}      {name}
#   DELETE /api/client/devices/{id}      Geraet abmelden (sofort wirksam)
#   GET    /api/client/admin/devices     alle Geraete (Recht manage_users)
# App
#   GET    /api/client/config            Server-Funktionen fuer die App (Relay/WebDAV, Spaces, Fernbild ...)
#   POST   /api/client/screen            {client_id, protocol: rdp|vnc, port?} -> Weiterleitung nur fuer meine IP
#   DELETE /api/client/screen/{id}
# Installer
#   GET    /api/client/downloads         verfuegbare Pakete (je Plattform)
#   GET    /api/client/download/{name}
#   POST   /api/client/installers        hochladen (manage_settings)   DELETE /api/client/installers/{name}
# Oeffentliche Installationswege (Einzeiler, Pakete): dist_routes.py (/client-dist/...)

from __future__ import annotations

import ipaddress
import re
import time
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db, modules
from app.auth import get_current_user, require_perm, user_has_permission
from app.config import JWT_EXPIRE_HOURS, JWT_SECRET
from app.paths import STORAGE_DIR

from . import devices

router = APIRouter(prefix="/api/client", tags=["client"])

INSTALLER_DIR = STORAGE_DIR / "client"
SCREEN_PORTS = {"rdp": 3389, "vnc": 5900}


def _ip(request: Request) -> str:
    return (request.client.host if request.client else "") or ""


def _payload(authorization: str | None) -> dict:
    tok = (authorization or "").removeprefix("Bearer ").strip()
    try:
        return jwt.decode(tok, JWT_SECRET, algorithms=["HS256"]) if tok else {}
    except Exception:
        return {}


def _device_session(user: dict, dev: dict) -> str:
    """Sitzung fuer ein Geraet: normales JWT plus "dev"-Claim (sofort widerrufbar)."""
    return jwt.encode({"sub": user["id"], "username": user["username"], "role": user["role"], "dev": dev["id"],
                       "exp": int(time.time()) + JWT_EXPIRE_HOURS * 3600}, JWT_SECRET, algorithm="HS256")


def _user_public(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "username", "display_name", "role", "language", "theme", "accent")}


# ================================================================== Anmeldung
class RegisterBody(BaseModel):
    name: str = ""
    platform: str = ""
    os: str = ""
    app_version: str = ""


class SessionBody(BaseModel):
    device_token: str
    app_version: str | None = None


@router.post("/register")
def register(body: RegisterBody, request: Request, user: dict = Depends(get_current_user)):
    dev, token = devices.register(user, body.model_dump(), _ip(request))
    db.add_audit_entry(user["username"], "client.device_registered", details=f"{dev['name']} ({dev['platform']})")
    return {"device": devices.public(dev), "device_token": token, "access_token": _device_session(user, dev)}


@router.post("/session")
def session(body: SessionBody, request: Request):
    """Gespeicherten Geraetetoken gegen eine Sitzung tauschen (ohne Passwort)."""
    dev = devices.check_token(body.device_token)
    if not dev:
        time.sleep(0.5)          # Durchprobieren unattraktiv machen
        raise HTTPException(401, "Gerät nicht (mehr) angemeldet - bitte neu anmelden")
    user = db.get_user_by_id(dev["user_id"])
    if not user:
        devices.remove(dev["id"])
        raise HTTPException(401, "Benutzer existiert nicht mehr")
    if user.get("auth_realm"):
        from app.auth import sso_enabled
        if not sso_enabled():
            raise HTTPException(401, "Die Verzeichnis-Anmeldung (SSO) ist deaktiviert")
    devices.touch(dev["id"], _ip(request), body.app_version)
    return {"access_token": _device_session(user, dev), "user": _user_public(user), "device": devices.get(dev["id"])}


# ================================================================== Geraete
class RenameBody(BaseModel):
    name: str


@router.get("/devices")
def my_devices(request: Request, user: dict = Depends(get_current_user),
               authorization: str | None = Header(default=None)):
    cur = _payload(authorization).get("dev")
    if cur:
        devices.touch(cur, _ip(request))
    return {"devices": [{**d, "current": d["id"] == cur} for d in devices.list_for(user["id"])]}


def _own(user, dev_id: str) -> dict:
    dev = devices.get(dev_id)
    if not dev:
        raise HTTPException(404, "Gerät nicht gefunden")
    if dev["user_id"] != user["id"] and not user_has_permission(user, "manage_users"):
        raise HTTPException(403, "Nicht dein Gerät")
    return dev


@router.patch("/devices/{dev_id}")
def rename_device(dev_id: str, body: RenameBody, user: dict = Depends(get_current_user)):
    _own(user, dev_id)
    return devices.rename(dev_id, body.name)


@router.delete("/devices/{dev_id}")
def remove_device(dev_id: str, user: dict = Depends(get_current_user)):
    dev = _own(user, dev_id)
    devices.remove(dev_id)
    db.add_audit_entry(user["username"], "client.device_removed", details=f"{dev['name']} ({dev['username']})")
    return {"ok": True}


@router.get("/admin/devices")
def all_devices(user: dict = Depends(get_current_user)):
    require_perm(user, "manage_users")
    return {"devices": devices.list_for(None)}


# ================================================================== App-Konfiguration
def _relay_info() -> dict:
    if not modules.is_enabled("relay"):
        return {"enabled": False}
    out = {"enabled": True, "webdav_path": "/dav/", "webdav": False, "mode": "off"}
    try:
        rr = modules.use("relay", "relay_routes")
        ftp = modules.use("relay", "ftp_relay")
        from app.config import PORT
        out.update({"webdav": bool(rr.webdav_enabled()), "mode": ftp.mode(), "port": PORT,
                    "host": (db.get_setting("server_host") or "").strip()})
    except Exception:
        pass
    return out


@router.get("/config")
def config(request: Request, user: dict = Depends(get_current_user)):
    portforward = modules.is_enabled("network") and modules.feature("network", "portforward")
    return {
        "server": str(request.base_url).rstrip("/"),
        "user": _user_public(user),
        "relay": _relay_info(),
        "may_use_relay": user_has_permission(user, "use_relay"),
        "spaces": modules.is_enabled("spaces"),
        "remote_screen": bool(portforward),
        "remote_screen_protocols": list(SCREEN_PORTS),
        "downloads": _downloads(),
        "vpn": {"available": False, "note": "WireGuard folgt in einer späteren Version"},
    }


# ================================================================== Fernbild (RDP/VNC)
class ScreenBody(BaseModel):
    client_id: str
    protocol: str = "rdp"
    port: int | None = None
    minutes: int = 120


@router.post("/screen")
async def screen(body: ScreenBody, request: Request, user: dict = Depends(get_current_user)):
    """Weiterleitung zu RDP/VNC des Zielrechners - erreichbar NUR von der IP dieses Geraets."""
    if body.protocol not in SCREEN_PORTS:
        raise HTTPException(400, "Protokoll muss rdp oder vnc sein")
    require_perm(user, "c_portforward", body.client_id)
    pf = modules.use("network", "portforward") if modules.is_enabled("network") else None
    if pf is None or not modules.feature("network", "portforward"):
        raise HTTPException(409, "Fernbild braucht das Modul „Netzwerk“ mit Port-Weiterleitung")
    ip = _ip(request)
    allow = ""
    try:
        addr = ipaddress.ip_address(ip)
        allow = f"{addr}/{32 if addr.version == 4 else 128}"
    except ValueError:
        pass
    port = body.port or SCREEN_PORTS[body.protocol]
    try:
        rec = await pf.create(client_id=body.client_id, target_port=port, username=user["username"],
                              minutes=max(5, min(body.minutes, 720)), target_host="127.0.0.1",
                              allow_from=allow, label=f"Client-App {body.protocol.upper()}")
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    db.add_audit_entry(user["username"], "client.screen", target=body.client_id,
                       details=f"{body.protocol} {rec['listen_port']} -> {port} (nur {allow or 'alle'})")
    host = request.url.hostname or ""
    return {"forward_id": rec.get("id"), "host": host, "port": rec["listen_port"], "protocol": body.protocol,
            "allow_from": allow, "expires": rec.get("expires_at")}


@router.delete("/screen/{forward_id}")
async def screen_stop(forward_id: str, user: dict = Depends(get_current_user)):
    pf = modules.use("network", "portforward") if modules.is_enabled("network") else None
    if pf is None:
        raise HTTPException(409, "Modul „Netzwerk“ ist aus")
    rec = next((f for f in pf.overview() if f.get("id") == forward_id), None)
    if not rec:
        return {"ok": True}
    if rec.get("username") != user["username"] and not user_has_permission(user, "manage_settings"):
        raise HTTPException(403, "Nicht deine Weiterleitung")
    await pf.stop(forward_id)
    return {"ok": True}


# ================================================================== Installer
# Paketliste/-pfade kommen aus dist_routes.py (mitgelieferte in src/client/release
# + hochgeladene in storage/client; Einzeiler-Installer ebenfalls dort).
from . import dist_routes as _dist


def _downloads() -> list[dict]:
    return _dist.files()


def _installer(name: str) -> Path:
    if "/" in name or "\\" in name or name.startswith(".") or not _dist.platform_of(name):
        raise HTTPException(400, "Ungültiger Dateiname")
    return INSTALLER_DIR / name


@router.get("/downloads")
def downloads(user: dict = Depends(get_current_user)):
    return {"downloads": _downloads(), "may_upload": user_has_permission(user, "manage_settings")}


@router.get("/download/{name}")
def download(name: str, user: dict = Depends(get_current_user)):
    p = _dist.path_of(name)
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


@router.post("/installers")
async def upload_installer(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    require_perm(user, "manage_settings")
    name = Path(file.filename or "").name
    p = _installer(name)
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".part")
    with tmp.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            fh.write(chunk)
    tmp.replace(p)
    db.add_audit_entry(user["username"], "client.installer_upload", details=name)
    return {"ok": True, "name": name}


@router.delete("/installers/{name}")
def delete_installer(name: str, user: dict = Depends(get_current_user)):
    """Loescht nur HOCHGELADENE Pakete; mitgelieferte (src/client/release) bleiben."""
    require_perm(user, "manage_settings")
    p = _installer(name)
    if p.is_file():
        p.unlink()
    return {"ok": True}

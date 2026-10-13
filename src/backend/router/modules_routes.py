# router/modules_routes.py
# ------------------------
# API der Modul-App (Core). Verwaltung mit dem globalen Recht "manage_modules".
#
#   GET    /api/modules                       Liste aller Module (Status, Funktionen, Einstellungen)
#   GET    /api/modules/frontend              aktive Module mit Frontend-Teil (alle angemeldeten Benutzer)
#   POST   /api/modules/{id}/enable           aktivieren (?with_deps=1 aktiviert Abhaengigkeiten mit)
#   POST   /api/modules/{id}/disable          deaktivieren (?cascade=1 schaltet abhaengige Module mit ab)
#   GET    /api/modules/{id}/settings         Einstellungen
#   PUT    /api/modules/{id}/settings         Einstellungen speichern
#   PUT    /api/modules/{id}/features         Teilfunktionen schalten
#   GET    /api/modules/{id}/crashes          Crashberichte
#   DELETE /api/modules/{id}/crashes          Crashberichte loeschen
#   POST   /api/modules/{id}/report-crash     Fehler aus dem Frontend melden
#   PUT    /api/modules/crash-policy          Wie viele Abstuerze fuehren zur Abschaltung
#   POST   /api/modules/rescan                module.json neu einlesen
#   GET    /modules/frontend/{id}/{pfad}      Frontend-Dateien eines AKTIVEN Moduls

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import db, modules
from app.auth import get_current_user, require_perm

router = APIRouter()


def _admin(user: dict) -> None:
    # Globales Recht "manage_modules" (Rechte-Verwaltung -> Allgemein).
    # Administratoren haben es automatisch.
    require_perm(user, "manage_modules")


def _wrap(exc: modules.ModuleError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/modules")
def list_modules(user: dict = Depends(get_current_user)):
    _admin(user)
    return modules.list_all()


@router.get("/api/modules/frontend")
def frontend_modules(user: dict = Depends(get_current_user)):
    return {"modules": modules.frontend_manifest()}


@router.post("/api/modules/rescan")
def rescan(user: dict = Depends(get_current_user)):
    _admin(user)
    modules.discover()
    return modules.list_all()


class CrashPolicy(BaseModel):
    crash_limit: int = Field(ge=1, le=50)


@router.put("/api/modules/crash-policy")
def crash_policy(body: CrashPolicy, user: dict = Depends(get_current_user)):
    _admin(user)
    return modules.set_crash_policy(body.crash_limit)


@router.post("/api/modules/{mid}/enable")
async def enable_module(mid: str, with_deps: bool = False, user: dict = Depends(get_current_user)):
    _admin(user)
    try:
        res = await modules.enable(mid, with_deps=with_deps)
    except modules.ModuleError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "module.enable", target=mid, details=",".join(res["enabled"]))
    return {**res, "module": modules.describe(mid)}


@router.post("/api/modules/{mid}/disable")
async def disable_module(mid: str, cascade: bool = False, user: dict = Depends(get_current_user)):
    _admin(user)
    try:
        res = await modules.disable(mid, cascade=cascade)
    except modules.ModuleError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "module.disable", target=mid, details=",".join(res["disabled"]))
    return {**res, "module": modules.describe(mid)}


@router.get("/api/modules/{mid}/settings")
def get_settings(mid: str, user: dict = Depends(get_current_user)):
    _admin(user)
    try:
        return {"settings": modules.get_settings(mid)}
    except modules.ModuleError as exc:
        raise _wrap(exc)


@router.put("/api/modules/{mid}/settings")
def put_settings(mid: str, body: dict, user: dict = Depends(get_current_user)):
    _admin(user)
    try:
        res = modules.set_settings(mid, body.get("settings") or {})
    except modules.ModuleError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "module.settings", target=mid)
    return {"settings": res}


@router.put("/api/modules/{mid}/features")
async def put_features(mid: str, body: dict, user: dict = Depends(get_current_user)):
    _admin(user)
    try:
        res = await modules.set_features(mid, body.get("features") or {})
    except modules.ModuleError as exc:
        raise _wrap(exc)
    db.add_audit_entry(user["username"], "module.features", target=mid, details=str(res))
    return {"features": res, "module": modules.describe(mid)}


@router.get("/api/modules/{mid}/crashes")
def get_crashes(mid: str, user: dict = Depends(get_current_user)):
    _admin(user)
    try:
        return {"crashes": modules.read_crashes(mid)}
    except modules.ModuleError as exc:
        raise _wrap(exc)


@router.delete("/api/modules/{mid}/crashes")
def delete_crashes(mid: str, user: dict = Depends(get_current_user)):
    _admin(user)
    try:
        modules.clear_crashes(mid)
    except modules.ModuleError as exc:
        raise _wrap(exc)
    return {"ok": True}


class FrontendCrash(BaseModel):
    where: str = Field(default="frontend", max_length=200)
    error: str = Field(default="", max_length=1000)
    stack: str = Field(default="", max_length=6000)
    fatal: bool = False        # True: Modul liess sich nicht laden -> abschalten


@router.post("/api/modules/{mid}/report-crash")
def report_frontend_crash(mid: str, body: FrontendCrash, user: dict = Depends(get_current_user)):
    try:
        modules.get(mid)
    except modules.ModuleError as exc:
        raise _wrap(exc)
    if not modules.is_enabled(mid):
        return {"ok": True, "ignored": True}
    # Fatal (Modul liess sich nicht laden/zeichnen): zaehlt gegen das Crash-Limit und
    # schaltet das Modul ab. Nicht-fatale Laufzeitfehler einzelner Browser werden
    # nur als Bericht abgelegt (sichtbar in der Modul-App), schalten aber nichts ab.
    modules.report_crash(mid, None, body.where, source="frontend",
                         extra=f"{body.error}\n{body.stack}\nbenutzer={user.get('username')}".strip(),
                         disable=bool(body.fatal))
    return {"ok": True}


@router.get("/modules/frontend/{mid}/{rel:path}")
def module_frontend_file(mid: str, rel: str):
    f = modules.frontend_file(mid, rel)
    if f is None:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return FileResponse(f, headers={"Cache-Control": "no-cache"})

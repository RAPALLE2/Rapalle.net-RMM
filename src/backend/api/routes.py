# api/routes.py - REST-API des RMM (/api/v1)
# -------------------------------------------
# Wird NICHT vom Core gestartet, sondern vom Modul "api" (src/modules/backend/api)
# eingehaengt. Ist das Modul aus, gibt es /api/v1 nicht.
#
# Ablauf fuer Aufrufer:
#   1. GET  /api/v1/status                       Betriebsart + was zu tun ist (ohne Anmeldung)
#   2. POST /api/v1/auth/login                   {username, password, realm?, code?, label?}
#        secure -> {token: "<langer Code>", expires_at}  ... dann bei JEDER Anfrage
#                   "Authorization: Bearer <Code>" (oder "X-API-Token: <Code>")
#        user   -> Sitzung (Cookie rmm_api_session bzw. Header "X-API-Session: <session>")
#        open   -> keine Anmeldung noetig (laeuft als API-Benutzer aus den Einstellungen)
#   3. eigentliche Aufrufe, z.B.  GET /api/v1/clients,  DELETE /api/v1/clients/<id>
#      oder jede Dashboard-Funktion ueber die Bruecke:  /api/v1/call/<pfad>  (= /api/<pfad>)
#   4. POST /api/v1/auth/logout
#
# Rechte: "use_api" + pro Aufruf dasselbe Recht wie im Dashboard (Admins: alles).
# Katalog aller Endpunkte: GET /api/v1/routes

from __future__ import annotations

import json
import time
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from app import db
from app.auth import get_current_user, require_perm

from . import bridge, keys, security, store

API_VERSION = "1.0"


# ================================================================== Protokoll je Aufruf
class LoggedRoute(APIRoute):
    """Protokolliert JEDEN /api/v1-Aufruf (auch abgewiesene) in api_log."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            t0 = time.perf_counter()
            status, detail = 500, ""
            try:
                resp = await original(request)
                status = resp.status_code
                return resp
            except HTTPException as e:
                status, detail = e.status_code, str(e.detail)[:300]
                raise
            except Exception as e:          # noqa: BLE001
                detail = f"{type(e).__name__}: {e}"[:300]
                raise
            finally:
                user = getattr(request.state, "api_user", None) or {}
                store.log_call(mode=getattr(request.state, "api_mode", store.setting("mode") or ""),
                               username=user.get("username") or getattr(request.state, "api_login_name", ""),
                               method=request.method, path=request.url.path + (f"?{request.url.query}" if request.url.query else ""),
                               status=status, ms=int((time.perf_counter() - t0) * 1000), ip=security.client_ip(request),
                               token_id=getattr(request.state, "api_token_id", ""), detail=detail)
        return handler


router = APIRouter(prefix="/api/v1", tags=["REST-API v1"], route_class=LoggedRoute)


def api_user(request: Request) -> dict:
    """Abhaengigkeit fuer alle geschuetzten Endpunkte."""
    return security.resolve(request)


# ================================================================== Status & Anmeldung
@router.get("/status")
def status(request: Request):
    mode = store.setting("mode") or "secure"
    request.state.api_mode = mode
    how = {"open": "Keine Anmeldung nötig - Aufrufe laufen als der eingestellte API-Benutzer.",
           "user": "POST /api/v1/auth/login, danach Cookie rmm_api_session oder Header X-API-Session mitschicken.",
           "secure": "POST /api/v1/auth/login, danach bei jeder Anfrage 'Authorization: Bearer <token>' mitschicken."}
    return {"api": "RAPALLE.net RMM REST-API", "version": API_VERSION, "mode": mode, "how_to": how.get(mode),
            "token_lifetime": store.setting("token_lifetime") if mode == "secure" else None,
            "catalog": "/api/v1/routes"}


@router.post("/auth/login")
async def login(request: Request):
    """Anmelden. Prueft Passwort, Sperren, 2FA und SSO wie die Dashboard-Anmeldung."""
    mode = store.setting("mode") or "secure"
    request.state.api_mode = mode
    ip = security.client_ip(request)
    if not store.ip_allowed(ip):
        raise HTTPException(403, f"Zugriff von {ip} ist nicht freigegeben")
    try:
        body = json.loads((await request.body()) or b"{}")
    except ValueError:
        raise HTTPException(400, "JSON erwartet: {username, password}")
    request.state.api_login_name = str(body.get("username") or "")[:80]
    if mode == "open":
        return {"mode": "open", "detail": "Im Modus „ohne Benutzer“ ist keine Anmeldung nötig."}
    candidate = db.get_user_by_username(str(body.get("username") or "").strip())
    key_info: dict = {}
    if keys.is_api_account(candidate) or body.get("api_key"):
        # API-Konto: Anmeldung mit API-Schluessel (kein Passwort, keine Erstanmeldung)
        user, krow = keys.verify_with_row(str(body.get("username") or ""),
                                          str(body.get("api_key") or body.get("password") or ""), ip)
        if keys.may_rotate(user) and keys.rotation_due(krow):
            new_key, old_until = keys.rotate_on_login(user)
            key_info = {"api_key_rotated": True, "new_api_key": new_key, "old_api_key_valid_until": old_until,
                        "key_notice": "Der API-Schlüssel wurde rotiert. Bitte den neuen Schlüssel (new_api_key) speichern – "
                                      "der bisherige gilt nur noch bis old_api_key_valid_until."}
            db.add_audit_entry(user["username"], "api.key_auto_rotated", details=f"alter Schlüssel bis {old_until}")
        elif krow.get("valid_until"):
            key_info = {"api_key_replaced": True, "old_api_key_valid_until": krow["valid_until"],
                        "key_notice": "Dieser API-Schlüssel wurde bereits ersetzt und läuft bald ab – bitte den neuen Schlüssel verwenden."}
    else:
        inner = {k: body.get(k) for k in ("username", "password", "realm", "code") if body.get(k) is not None}
        inner.setdefault("realm", "local")
        # Die ECHTE Dashboard-Anmeldung aufrufen (Sperren, 2FA, AD/LDAP inklusive) - ohne Benutzer.
        resp = await bridge.forward(request, None, "POST", "/api/auth/login", body=json.dumps(inner).encode(),
                                    content_type="application/json")
        data = json.loads(resp.body or b"{}")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, data.get("detail") or "Anmeldung fehlgeschlagen")
        user = db.get_user_by_id((data.get("user") or {}).get("id", ""))
        if not user:
            raise HTTPException(401, "Anmeldung fehlgeschlagen")
    request.state.api_user = user
    if not security.may_use_api(user):
        raise HTTPException(403, "Fehlendes Recht: use_api (REST-API benutzen)")
    info = {"mode": mode, "user": {"id": user["id"], "username": user["username"], "role": user.get("role")}}
    if mode == "user":
        s = security.open_session(user, ip)
        request.state.api_token_id = s["token_id"]
        out = JSONResponse({**info, **key_info, "session": s["session"], "idle_minutes": s["idle_minutes"],
                            "how_to": "Header 'X-API-Session: <session>' oder Cookie rmm_api_session mitschicken."})
        out.set_cookie(security.SESSION_COOKIE, s["session"], httponly=True, samesite="strict",
                       secure=request.url.scheme == "https", max_age=s["idle_minutes"] * 60)
        return out
    t = security.issue_secure_token(user, ip, str(body.get("label") or "")[:80])
    request.state.api_token_id = t["token_id"]
    return {**info, **key_info, "token": t["token"], "token_type": "Bearer", "expires_at": t["expires_at"],
            "lifetime": t["lifetime"], "how_to": "Bei jeder Anfrage 'Authorization: Bearer <token>' mitschicken."}


@router.post("/auth/logout")
def logout(request: Request, user: dict = Depends(api_user)):
    tid = getattr(request.state, "api_token_id", "")
    if tid:
        store.revoke(tid)
    out = JSONResponse({"ok": True, "revoked": bool(tid)})
    out.delete_cookie(security.SESSION_COOKIE)
    return out


@router.get("/auth/me")
async def me(request: Request, user: dict = Depends(api_user)):
    resp = await bridge.forward(request, user, "GET", "/api/auth/effective")
    perms = json.loads(resp.body or b"{}") if resp.status_code == 200 else {}
    tok = store.get_token(getattr(request.state, "api_token_id", "")) or {}
    return {"user": {"id": user["id"], "username": user["username"], "display_name": user.get("display_name"),
                     "role": user.get("role")},
            "mode": request.state.api_mode, "token_expires_at": tok.get("expires"), "permissions": perms}


# ================================================================== Katalog
SHORTCUTS: list[tuple[str, tuple[str, ...], str, str]] = [
    # (v1-Pfad, Methoden, Dashboard-Pfad, Beschreibung)
    ("/users", ("GET", "POST"), "/api/users", "Benutzer auflisten / anlegen"),
    ("/users/{user_id}", ("DELETE",), "/api/users/{user_id}", "Benutzer löschen"),
    ("/users/{user_id}/groups", ("GET", "PUT"), "/api/admin/users/{user_id}/groups", "Gruppen eines Benutzers"),
    ("/groups", ("GET", "POST"), "/api/admin/groups", "Gruppen auflisten / anlegen"),
    ("/groups/{group_id}", ("PUT", "DELETE"), "/api/admin/groups/{group_id}", "Gruppe ändern / löschen"),
    ("/permissions", ("GET",), "/api/admin/permission-catalog", "Rechte-Katalog"),
    ("/permissions/{subject_type}/{subject_id}", ("GET", "PUT"), "/api/admin/grants/{subject_type}/{subject_id}",
     "Rechte (Grants) eines Benutzers/einer Gruppe lesen / setzen"),
    ("/clients", ("GET",), "/api/clients", "Clients (Agenten) auflisten"),
    ("/clients/{client_id}", ("GET", "PUT", "DELETE"), "/api/clients/{client_id}", "Client lesen / ändern / löschen"),
    ("/clients/{client_id}/exec", ("POST",), "/api/clients/{client_id}/exec", "Befehl auf dem Client ausführen"),
    ("/clients/{client_id}/processes", ("GET",), "/api/clients/{client_id}/processes", "Prozesse"),
    ("/clients/{client_id}/processes/kill", ("POST",), "/api/clients/{client_id}/processes/kill", "Prozess beenden"),
    ("/clients/{client_id}/files", ("GET",), "/api/clients/{client_id}/fs", "Dateien auflisten (?path=)"),
    ("/clients/{client_id}/metrics", ("GET",), "/api/clients/{client_id}/metrics/history", "Messwerte"),
    ("/clients/{client_id}/notes", ("GET", "POST"), "/api/clients/{client_id}/notes", "Notizen"),
    ("/clients/bulk-exec", ("POST",), "/api/clients/bulk-exec", "Befehl auf mehreren Clients"),
    ("/agents/{client_id}/update", ("POST",), "/api/clients/{client_id}/update-agent", "Agent aktualisieren"),
    ("/agents/{client_id}/uninstall", ("POST",), "/api/clients/{client_id}/uninstall-agent", "Agent deinstallieren"),
    ("/agents/update-all", ("POST",), "/api/clients/update-all-agents", "Alle Agenten aktualisieren"),
    ("/hierarchy", ("GET",), "/api/hierarchy", "Mandanten / Standorte / Ordner"),
    ("/modules", ("GET",), "/api/modules", "Module"),
    ("/modules/{module_id}/enable", ("POST",), "/api/modules/{module_id}/enable", "Modul aktivieren"),
    ("/modules/{module_id}/disable", ("POST",), "/api/modules/{module_id}/disable", "Modul deaktivieren"),
    ("/modules/{module_id}/settings", ("GET", "PUT"), "/api/modules/{module_id}/settings", "Modul-Einstellungen"),
    ("/modules/{module_id}/features", ("PUT",), "/api/modules/{module_id}/features", "Teilfunktionen schalten"),
    ("/apps", ("GET",), "/api/modules/frontend", "Apps der aktiven Module"),
    ("/settings", ("GET", "PUT"), "/api/admin/settings", "Allgemeine Einstellungen"),
    ("/nodes", ("GET",), "/api/nodes", "Nodes"),
    ("/nodes/modules", ("GET",), "/api/nodes/modules", "Node-Module"),
    ("/nodes/{client_id}", ("GET",), "/api/nodes/{client_id}", "Node eines Clients"),
    ("/nodes/{client_id}/enable", ("POST",), "/api/nodes/{client_id}/enable", "Node installieren"),
    ("/nodes/{client_id}/disable", ("POST",), "/api/nodes/{client_id}/disable", "Node entfernen"),
    ("/nodes/{client_id}/update", ("POST",), "/api/nodes/{client_id}/update", "Node aktualisieren"),
    ("/nodes/{client_id}/modules/{module_id}", ("POST", "DELETE"), "/api/nodes/{client_id}/modules/{module_id}",
     "Node-Modul bereitstellen / entfernen"),
    ("/audit", ("GET",), "/api/audit", "Audit-Protokoll"),
    ("/system/update", ("GET",), "/api/admin/update/info", "Server-Update: Stand"),
    ("/system/update", ("POST",), "/api/admin/update/run", "Server-Update ausführen"),
    ("/system/restart", ("POST",), "/api/admin/restart", "Backend neu starten"),
]


@router.get("/routes")
def routes(request: Request, user: dict = Depends(api_user)):
    """Alle Endpunkte: Kurzwege + jede Dashboard-Route ueber /api/v1/call/..."""
    return {"shortcuts": [{"methods": list(m), "path": "/api/v1" + p, "dashboard": d, "summary": s}
                          for p, m, d, s in SHORTCUTS],
            "bridge": bridge.internal_routes(request.scope.get("app")),
            "note": "Jede Route unter 'bridge' ist als /api/v1/call/<rest> erreichbar (z.B. /api/clients -> /api/v1/call/clients)."}


def _make_shortcut(target: str):
    async def endpoint(request: Request, user: dict = Depends(api_user)):
        path = target.format(**{k: str(v) for k, v in request.path_params.items()})
        return await bridge.forward(request, user, request.method, path, request.url.query)
    return endpoint


# Kurzwege zuerst eintragen (vor der allgemeinen Bruecke), gleiche Pfade zusammenfassen.
_seen: dict[tuple[str, str], bool] = {}
for _p, _m, _d, _s in SHORTCUTS:
    for _meth in _m:
        if (_p, _meth) in _seen:
            continue
        _seen[(_p, _meth)] = True
        router.add_api_route(_p, _make_shortcut(_d), methods=[_meth], summary=_s, name=f"v1 {_meth} {_p}")


@router.api_route("/call/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def call(rest: str, request: Request, user: dict = Depends(api_user)):
    """Allgemeine Bruecke: /api/v1/call/<rest>  ==  Dashboard-Route /api/<rest>."""
    return await bridge.forward(request, user, request.method, "/api/" + rest.lstrip("/"), request.url.query)


# ================================================================== Verwaltung (Modul-Einstellungen)
admin_router = APIRouter(prefix="/api/apimod", tags=["REST-API Verwaltung"])


def _manage(user: dict) -> None:
    require_perm(user, "manage_modules")


@admin_router.get("/overview")
def overview(request: Request, user: dict = Depends(get_current_user)):
    _manage(user)
    day = store.now() - 86400
    c = db._conn.execute("SELECT COUNT(*) AS n, SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS e "
                         "FROM api_log WHERE ts >= ?", (day,)).fetchone()
    return {"mode": store.setting("mode") or "secure", "token_lifetime": store.setting("token_lifetime") or "8h",
            "active_tokens": len(store.list_tokens()), "calls_24h": c["n"] or 0, "errors_24h": c["e"] or 0,
            "base": str(request.base_url).rstrip("/") + "/api/v1", "shortcuts": len(SHORTCUTS),
            "bridge_routes": len(bridge.internal_routes(request.scope.get("app")))}


@admin_router.get("/settings")
def settings_view(user: dict = Depends(get_current_user)):
    """Formular-Schema + aktuelle Werte (gespeichert wird ueber PUT /api/modules/api/settings)."""
    _manage(user)
    from app import modules
    return {"schema": modules.get("api").settings_schema, "values": modules.get_settings("api")}


# --- API-Konten (Vorlage "API-Benutzer"): Schluessel verwalten -------------------
def _manage_accounts(user: dict) -> None:
    from app.auth import user_has_permission
    if not (user_has_permission(user, "create_users") or user_has_permission(user, "manage_modules")):
        require_perm(user, "create_users")


@admin_router.get("/accounts")
def api_accounts(user: dict = Depends(get_current_user)):
    _manage_accounts(user)
    return {"accounts": keys.accounts()}


@admin_router.post("/accounts/rotate-all")
def rotate_all_keys(user: dict = Depends(get_current_user)):
    """Alle API-Schluessel faellig stellen: jedes Konto bekommt bei der naechsten Anmeldung einen neuen
    (der alte gilt danach noch die Uebergangszeit)."""
    _manage_accounts(user)
    n = keys.force_rotation_all()
    db.add_audit_entry(user["username"], "api.keys_rotate_all", details=f"{n} Schlüssel fällig gestellt")
    return {"ok": True, "due": n}


@admin_router.post("/accounts/{user_id}/rotate")
def rotate_key(user_id: str, user: dict = Depends(get_current_user)):
    """Neuen Schluessel erzeugen - der alte und alle Anmeldungen des Kontos werden sofort ungueltig."""
    _manage_accounts(user)
    target = db.get_user_by_id(user_id)
    if not keys.is_api_account(target):
        raise HTTPException(404, "Kein API-Konto")
    key = keys.generate(target)
    store.revoke_all(user_id)
    db.add_audit_entry(user["username"], "api.key_rotated", target=target["username"])
    return {"username": target["username"], "api_key": key}


@admin_router.post("/accounts/{user_id}/revoke")
def revoke_key(user_id: str, user: dict = Depends(get_current_user)):
    _manage_accounts(user)
    target = db.get_user_by_id(user_id)
    if not keys.is_api_account(target):
        raise HTTPException(404, "Kein API-Konto")
    n = keys.revoke_user(user_id)
    store.revoke_all(user_id)
    db.add_audit_entry(user["username"], "api.key_revoked", target=target["username"])
    return {"ok": True, "revoked": n}


@admin_router.get("/tokens")
def tokens(all: int = 0, user: dict = Depends(get_current_user)):
    _manage(user)
    return {"tokens": store.list_tokens(include_old=bool(all))}


@admin_router.delete("/tokens/{token_id}")
def revoke_token(token_id: str, user: dict = Depends(get_current_user)):
    _manage(user)
    ok = store.revoke(token_id)
    db.add_audit_entry(user["username"], "api.token_revoked", details=token_id)
    return {"ok": ok}


@admin_router.post("/tokens/revoke-all")
def revoke_all(user: dict = Depends(get_current_user)):
    _manage(user)
    n = store.revoke_all()
    db.add_audit_entry(user["username"], "api.tokens_revoked_all", details=f"{n} Anmeldungen")
    return {"ok": True, "revoked": n}


@admin_router.get("/log")
def log(limit: int = 200, before: int = 0, username: str = "", status: str = "", path: str = "",
        user: dict = Depends(get_current_user)):
    _manage(user)
    return {"entries": store.query_log(limit=limit, before=before or None, username=username, status=status, path=path)}


@admin_router.delete("/log")
def clear_log(user: dict = Depends(get_current_user)):
    _manage(user)
    db._conn.execute("DELETE FROM api_log")
    db._conn.commit()
    db.add_audit_entry(user["username"], "api.log_cleared")
    return {"ok": True}

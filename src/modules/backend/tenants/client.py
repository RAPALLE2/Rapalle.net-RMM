# client.py (Modul "tenants") - Aufrufe an ein Mandanten-Backend
# -------------------------------------------------------------
# Meldet den Benutzer mit SEINEN hinterlegten Zugangsdaten am Mandanten-Backend an
# (normale Login-API des Mandanten) und holt einen Token. Darueber:
#   - autologin(): Token fuer die Einbettung (Dashboard mit #client-token=)
#   - api(): beliebige Mandanten-API im Namen des Benutzers (z. B. Spaces/Relay)
# httpx (async). TLS-Pruefung je Mandant abschaltbar (selbstsignierte Zertifikate).
from __future__ import annotations

import httpx

from . import store


class TenantError(RuntimeError):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


def _base(tenant: dict) -> str:
    return (tenant.get("url") or "").rstrip("/")


async def _login(tenant: dict, username: str, password: str, realm: str, code: str = "") -> str:
    base = _base(tenant)
    if not base:
        raise TenantError("Keine Mandanten-URL hinterlegt")
    body = {"username": username, "password": password, "realm": realm or "local"}
    if code:
        body["code"] = code
    verify = bool(tenant.get("verify_tls", True))
    try:
        async with httpx.AsyncClient(verify=verify, timeout=20, follow_redirects=True) as c:
            r = await c.post(base + "/api/auth/login", json=body)
    except httpx.HTTPError as e:
        raise TenantError(f"Mandant nicht erreichbar: {e}") from None
    if r.status_code != 200:
        detail = ""
        try:
            detail = r.json().get("detail") or ""
        except Exception:
            detail = r.text[:200]
        if detail == "2fa_required":
            raise TenantError("2fa_required", 401)
        raise TenantError(f"Anmeldung am Mandanten fehlgeschlagen: {detail or r.status_code}", r.status_code)
    data = r.json()
    tok = data.get("access_token") or data.get("token")
    if not tok:
        raise TenantError("Mandant lieferte keinen Token")
    return tok


async def token_for(tenant: dict, user: dict, code: str = "") -> str:
    """Token am Mandanten holen - mit den fuer diesen Benutzer gespeicherten Zugangsdaten."""
    cred = store.get_cred(tenant["id"], user["id"])
    if not cred or not cred.get("pw_enc"):
        raise TenantError("no_credentials", 412)
    return await _login(tenant, cred["username"], store.dec(cred["pw_enc"]), cred.get("realm", "local"), code)


async def verify_credentials(tenant: dict, username: str, password: str, realm: str, code: str = "") -> dict:
    """Beim Hinterlegen pruefen. -> {ok} oder wirft TenantError (inkl. 2fa_required)."""
    await _login(tenant, username, password, realm, code)
    return {"ok": True}


async def api(tenant: dict, token: str, method: str, path: str, **kw) -> httpx.Response:
    base = _base(tenant)
    verify = bool(tenant.get("verify_tls", True))
    headers = {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(verify=verify, timeout=25, follow_redirects=True) as c:
        return await c.request(method, base + path, headers=headers, **kw)

# oidc_routes.py (Modul "cloudflare") - RMM als Login fuer Cloudflare Access (OpenID Connect)
# -------------------------------------------------------------------------------------------
# Jede Route mit Schutz "RMM-Login" bekommt in Cloudflare einen EIGENEN OIDC-Login
# (client_id = rmm-<route>), der hierher zeigt. Dadurch weiss das RMM bei der Anmeldung,
# fuer WELCHE Route sie gilt, und prueft die erlaubten Benutzer/Gruppen der Route.
# Angemeldet wird mit der echten Dashboard-Anmeldung (lokal oder AD/LDAP, Sperren, 2FA).
#
#   GET  /cf-oidc/{rid}/.well-known/openid-configuration
#   GET  /cf-oidc/{rid}/authorize    Login-Seite (Benutzer, Passwort, Anmeldung über, 2FA)
#   POST /cf-oidc/{rid}/authorize    Login pruefen -> Code an Cloudflare zurueck
#   POST /cf-oidc/{rid}/token        Code -> id_token (RS256) + access_token
#   GET  /cf-oidc/{rid}/jwks         oeffentlicher Schluessel
#   GET  /cf-oidc/{rid}/userinfo
# Diese Adressen sind oeffentlich (Cloudflare ruft /token und /jwks direkt ab).

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import secrets
import time
import urllib.parse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import db, modules

from . import store

router = APIRouter(tags=["cloudflare-oidc"])
_codes: dict[str, dict] = {}          # code -> {rid, user_id, nonce, challenge, method, redirect, exp}
_pending: dict[str, dict] = {}        # 2FA-Zwischenstand -> {username, password, realm, exp, params}
_tokens: dict[str, dict] = {}         # access_token -> {user_id, rid, exp}


# ------------------------------------------------------------------ Schluessel
def _key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    def make(data):
        o = data.get("oidc") or {}
        if not o.get("key_enc"):
            k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()
            data["oidc"] = {"key_enc": store.enc(pem), "kid": secrets.token_hex(8)}
        return data["oidc"]
    o = store.load().get("oidc") or {}
    if not o.get("key_enc"):
        o = store.update(make)
    pem = store.dec(o["key_enc"]).encode()
    return serialization.load_pem_private_key(pem, None), o["kid"]


def _b64(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _route(rid: str) -> tuple[dict, dict]:
    data = store.load()
    r = store.route(data, rid)
    if not r or r.get("access") != "rmm" or not (r.get("oidc") or {}).get("client_id"):
        raise LookupError("Unbekannte Anmeldung")
    return data, r


def _issuer(data: dict, rid: str) -> str:
    return f"{(data.get('public_base') or '').rstrip('/')}/cf-oidc/{rid}"


def _redirect_ok(data: dict, uri: str) -> bool:
    team = (data.get("team_domain") or "").strip().lower()
    p = urllib.parse.urlparse(uri or "")
    if p.scheme != "https" or p.path != "/cdn-cgi/access/callback":
        return False
    return (p.hostname or "").lower() == team if team else (p.hostname or "").endswith(".cloudflareaccess.com")


def _allowed(r: dict, user: dict) -> bool:
    allow = r.get("allow") or []
    if not allow:
        return True
    groups = set(db.get_user_group_ids(user["id"]))
    return any((a["type"] == "user" and a["id"] == user["id"]) or (a["type"] == "group" and a["id"] in groups)
               for a in allow)


def _email(user: dict, data: dict) -> str:
    if user.get("email"):
        return user["email"]
    host = (data.get("public_base") or "").split("://", 1)[-1].split("/", 1)[0].split(":")[0] or "rmm.local"
    return f"{user['username']}@{host}"


# ------------------------------------------------------------------ Anmeldung (echte Dashboard-Anmeldung)
async def _login(username: str, password: str, realm: str, code: str, ip: str) -> tuple[int, dict | None, str]:
    app = modules._api
    body = json.dumps({"username": username, "password": password, "realm": realm or "local",
                       **({"code": code} if code else {})}).encode()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/api/auth/login", "raw_path": b"/api/auth/login", "root_path": "", "query_string": b"",
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                         (b"user-agent", b"rmm-cloudflare-access"), (b"host", b"localhost")],
             "client": (ip, 0), "server": ("localhost", 0)}
    sent, status, chunks = False, 500, []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))
    await app(scope, receive, send)
    try:
        data = json.loads(b"".join(chunks) or b"{}")
    except ValueError:
        data = {}
    if status != 200:
        return status, None, str(data.get("detail") or "Anmeldung fehlgeschlagen")
    user = db.get_user_by_id((data.get("user") or {}).get("id", ""))
    return (200, user, "") if user else (401, None, "Anmeldung fehlgeschlagen")


def _realms() -> list[dict]:
    try:
        from app.auth import sso_enabled
        return db.get_public_realms() if sso_enabled() else []
    except Exception:
        return []


def _page(title: str, params: dict, error: str = "", step: str = "login", pending: str = "", username: str = "") -> HTMLResponse:
    e = html.escape
    hidden = "".join(f'<input type="hidden" name="{e(k)}" value="{e(str(v))}">' for k, v in params.items())
    realms = _realms()
    realm_sel = (f'<label>Anmeldung über<select name="realm"><option value="local">Lokales Konto</option>'
                 + "".join(f'<option value="{e(r["id"])}">{e(r["name"])}</option>' for r in realms)
                 + "</select></label>") if realms else ""
    fields = (f'''<label>Benutzername<input name="username" autocomplete="username" required autofocus value="{e(username)}"></label>
      <label>Passwort<input name="password" type="password" autocomplete="current-password" required></label>{realm_sel}'''
              if step == "login" else
              f'''<input type="hidden" name="pending" value="{e(pending)}">
      <p class="sub">Bestätigungscode aus der Authenticator-App (oder ein Backup-Code)</p>
      <label>Code<input name="code" inputmode="numeric" autocomplete="one-time-code" required autofocus></label>''')
    body = f'''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Anmelden – RAPALLE.net RMM</title><style>
:root{{color-scheme:dark}}body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0b1220;font:14px system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#e6edf6}}
form{{width:min(360px,calc(100vw - 32px));background:#121c2e;border:1px solid #24324a;border-radius:14px;padding:24px;box-shadow:0 20px 50px rgba(0,0,0,.45)}}
h1{{font-size:18px;margin:0 0 4px}}.sub{{color:#8fa1ba;font-size:12.5px;margin:0 0 14px}}
label{{display:block;font-size:12px;color:#8fa1ba;margin-bottom:10px}}input,select{{display:block;width:100%;box-sizing:border-box;margin-top:4px;
height:38px;padding:0 10px;border-radius:8px;border:1px solid #2b3b57;background:#0d1626;color:#e6edf6;font-size:15px}}
button{{width:100%;height:40px;border:0;border-radius:8px;background:#2dd4bf;color:#05211d;font-weight:700;font-size:14px;cursor:pointer;margin-top:6px}}
.err{{background:#3b1520;border:1px solid #7f1d2d;color:#fecdd3;padding:8px 10px;border-radius:8px;font-size:12.5px;margin-bottom:12px}}
.acc{{color:#2dd4bf}}</style></head><body><form method="post">
<h1>RAPALLE<span class="acc">.net</span> RMM</h1><p class="sub">Anmelden für <b>{e(title)}</b></p>
{f'<div class="err">{e(error)}</div>' if error else ""}{fields}{hidden}<button type="submit">Anmelden</button></form></body></html>'''
    return HTMLResponse(body, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                       "Referrer-Policy": "no-referrer"})


_PARAMS = ("client_id", "redirect_uri", "state", "nonce", "code_challenge", "code_challenge_method", "response_type", "scope")


def _clean():
    now = time.time()
    for d in (_codes, _pending, _tokens):
        for k in [k for k, v in d.items() if v["exp"] < now]:
            d.pop(k, None)


# ------------------------------------------------------------------ Endpunkte
@router.get("/cf-oidc/{rid}/.well-known/openid-configuration")
def discovery(rid: str):
    try:
        data, _ = _route(rid)
    except LookupError:
        return JSONResponse({"error": "not_found"}, 404)
    iss = _issuer(data, rid)
    return {"issuer": iss, "authorization_endpoint": f"{iss}/authorize", "token_endpoint": f"{iss}/token",
            "jwks_uri": f"{iss}/jwks", "userinfo_endpoint": f"{iss}/userinfo", "response_types_supported": ["code"],
            "subject_types_supported": ["public"], "id_token_signing_alg_values_supported": ["RS256"],
            "scopes_supported": ["openid", "email", "profile"], "code_challenge_methods_supported": ["S256", "plain"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "claims_supported": ["sub", "email", "name", "preferred_username", "groups"]}


@router.get("/cf-oidc/{rid}/jwks")
def jwks(rid: str):
    k, kid = _key()
    n = k.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": _b64(n.n), "e": _b64(n.e)}]}


@router.get("/cf-oidc/{rid}/authorize")
def authorize_page(rid: str, request: Request):
    q = dict(request.query_params)
    try:
        data, r = _route(rid)
    except LookupError:
        return HTMLResponse("Unbekannte Anmeldung", 404)
    if q.get("client_id") != r["oidc"]["client_id"] or not _redirect_ok(data, q.get("redirect_uri", "")):
        return HTMLResponse("Ungültige Anfrage (client_id / redirect_uri)", 400)
    return _page(r.get("hostname") or r["name"], {k: q[k] for k in _PARAMS if k in q})


@router.post("/cf-oidc/{rid}/authorize")
async def authorize_post(rid: str, request: Request):
    _clean()
    form = dict(await request.form())
    params = {k: form[k] for k in _PARAMS if k in form}
    try:
        data, r = _route(rid)
    except LookupError:
        return HTMLResponse("Unbekannte Anmeldung", 404)
    if params.get("client_id") != r["oidc"]["client_id"] or not _redirect_ok(data, params.get("redirect_uri", "")):
        return HTMLResponse("Ungültige Anfrage (client_id / redirect_uri)", 400)
    title = r.get("hostname") or r["name"]
    ip = (request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")).strip()
    if form.get("pending"):
        p = _pending.pop(form["pending"], None)
        if not p:
            return _page(title, params, "Die Anmeldung ist abgelaufen – bitte erneut anmelden.")
        username, password, realm, code = p["username"], p["password"], p["realm"], (form.get("code") or "").strip()
    else:
        username, password = (form.get("username") or "").strip(), form.get("password") or ""
        realm, code = form.get("realm") or "local", ""
    status, user, detail = await _login(username, password, realm, code, ip)
    if status != 200:
        if detail == "2fa_required" or (code and "2fa" in detail.lower()) or "code" in detail.lower():
            tok = secrets.token_urlsafe(24)
            _pending[tok] = {"username": username, "password": password, "realm": realm, "exp": time.time() + 300}
            return _page(title, params, "" if not code else "Code ungültig – bitte erneut eingeben.", "code", tok)
        return _page(title, params, detail if status in (401, 403, 423, 429) else "Anmeldung fehlgeschlagen",
                     username=username)
    if not _allowed(r, user):
        db.add_audit_entry(user["username"], "cloudflare.access_denied", details=title)
        return _page(title, params, "Dein Konto ist für diese Adresse nicht freigegeben.")
    c = secrets.token_urlsafe(32)
    _codes[c] = {"rid": rid, "user_id": user["id"], "nonce": params.get("nonce", ""),
                 "challenge": params.get("code_challenge", ""), "method": params.get("code_challenge_method", "plain"),
                 "redirect": params["redirect_uri"], "exp": time.time() + 120}
    db.add_audit_entry(user["username"], "cloudflare.access_login", details=title)
    qs = urllib.parse.urlencode({"code": c, **({"state": params["state"]} if params.get("state") else {})})
    return RedirectResponse(f"{params['redirect_uri']}?{qs}", 302)


def _client_auth(request: Request, form: dict) -> tuple[str, str]:
    h = request.headers.get("authorization") or ""
    if h.lower().startswith("basic "):
        try:
            cid, _, sec = base64.b64decode(h[6:]).decode().partition(":")
            return urllib.parse.unquote(cid), urllib.parse.unquote(sec)
        except Exception:
            return "", ""
    return form.get("client_id", ""), form.get("client_secret", "")


@router.post("/cf-oidc/{rid}/token")
async def token(rid: str, request: Request):
    _clean()
    form = dict(await request.form())
    err = lambda e, s=400: JSONResponse({"error": e}, s, headers={"Cache-Control": "no-store"})   # noqa: E731
    try:
        data, r = _route(rid)
    except LookupError:
        return err("invalid_client", 401)
    cid, sec = _client_auth(request, form)
    o = r["oidc"]
    if cid != o["client_id"] or not secrets.compare_digest(sec.encode(), store.dec(o["secret_enc"]).encode()):
        return err("invalid_client", 401)
    if form.get("grant_type") != "authorization_code":
        return err("unsupported_grant_type")
    c = _codes.pop(form.get("code", ""), None)
    if not c or c["rid"] != rid or c["exp"] < time.time() or form.get("redirect_uri", c["redirect"]) != c["redirect"]:
        return err("invalid_grant")
    if c["challenge"]:
        ver = form.get("code_verifier", "")
        calc = (base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
                if c["method"] == "S256" else ver)
        if not secrets.compare_digest(calc, c["challenge"]):
            return err("invalid_grant")
    user = db.get_user_by_id(c["user_id"])
    if not user:
        return err("invalid_grant")
    import jwt
    k, kid = _key()
    now = int(time.time())
    groups = {g["id"]: g["name"] for g in db.list_groups()}
    claims = {"iss": _issuer(data, rid), "sub": user["id"], "aud": o["client_id"], "iat": now, "exp": now + 600,
              "email": _email(user, data), "email_verified": True, "name": user.get("display_name") or user["username"],
              "preferred_username": user["username"],
              "groups": [groups[g] for g in db.get_user_group_ids(user["id"]) if g in groups]}
    if c["nonce"]:
        claims["nonce"] = c["nonce"]
    id_token = jwt.encode(claims, k, algorithm="RS256", headers={"kid": kid})
    at = secrets.token_urlsafe(32)
    _tokens[at] = {"user_id": user["id"], "rid": rid, "exp": time.time() + 600}
    return JSONResponse({"access_token": at, "token_type": "Bearer", "expires_in": 600, "id_token": id_token},
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/cf-oidc/{rid}/userinfo")
def userinfo(rid: str, request: Request):
    h = request.headers.get("authorization") or ""
    t = _tokens.get(h[7:] if h.lower().startswith("bearer ") else "")
    if not t or t["rid"] != rid or t["exp"] < time.time():
        return JSONResponse({"error": "invalid_token"}, 401)
    user = db.get_user_by_id(t["user_id"])
    data = store.load()
    groups = {g["id"]: g["name"] for g in db.list_groups()}
    return {"sub": user["id"], "email": _email(user, data), "name": user.get("display_name") or user["username"],
            "preferred_username": user["username"],
            "groups": [groups[g] for g in db.get_user_group_ids(user["id"]) if g in groups]}

# cf_routes.py (Modul "cloudflare") - Verwaltung (Recht manage_cloudflare)
# ------------------------------------------------------------------------
#   GET    /api/cloudflare/state            Einstellungen, Routen, Connector, Dienste, Nodes
#   POST   /api/cloudflare/auth             {type: token|key, token|key, email} -> pruefen + Konten
#   POST   /api/cloudflare/account          {account_id, public_base} -> Zonen + Team-Domain
#   POST   /api/cloudflare/refresh          Zonen/Team-Domain neu holen
#   POST   /api/cloudflare/routes           Route anlegen/aendern (+ sofort abgleichen)
#   DELETE /api/cloudflare/routes/{rid}     Route + alles in Cloudflare entfernen
#   POST   /api/cloudflare/sync             alles neu abgleichen
#   POST   /api/cloudflare/connector/{act}  start | stop | restart
#   DELETE /api/cloudflare/nodes/{cid}      Node-Tunnel entfernen
#   DELETE /api/cloudflare/all              ALLES entfernen (Routen, Tunnel, Zugang)
#   GET    /api/cloudflare/directory        Benutzer + Gruppen (Freigabe RMM-Login)

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm

from . import connector, services, store, sync
from .cfapi import Cf, CfError

router = APIRouter(prefix="/api/cloudflare", tags=["cloudflare"])


def _perm(user):
    require_perm(user, "manage_cloudflare")


def _err(e: Exception):
    if isinstance(e, CfError):
        raise HTTPException(502, str(e))
    raise HTTPException(400, str(e))


def _route_view(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k != "oidc"}
    out["proto"] = services.proto_of(r)
    out["url"] = (f"https://{r['hostname']}" + (f"/{r['ref'].get('site')}/" if r["kind"] == "website" else "")
                  if r.get("hostname") else "")
    return out


def _nodes() -> list[dict]:
    try:
        from app.node_manager import list_states
        return [{"client_id": n["client_id"], "online": n.get("online"), "hostname": n.get("hostname"),
                 "docker": bool((n.get("docker") or {}).get("available") or (n.get("docker") or {}).get("version"))}
                for n in sync._run(list_states(), 20)]
    except Exception:
        return []


@router.get("/state")
def state(user: dict = Depends(get_current_user)):
    _perm(user)
    data = store.load()
    return {**store.public_view(data), "routes": [_route_view(r) for r in data["routes"]],
            "connector": connector.status(), "catalog": services.catalog(), "nodes": _nodes(),
            "modes": services.MODES, "proto_ports": services.PROTO_PORT}


class AuthBody(BaseModel):
    type: str = "token"
    token: str = ""
    key: str = ""
    email: str = ""


@router.post("/auth")
def set_auth(body: AuthBody, user: dict = Depends(get_current_user)):
    _perm(user)
    if body.type not in ("token", "key"):
        raise HTTPException(400, "type: token oder key")
    secret = (body.token if body.type == "token" else body.key).strip()
    cur = store.load().get("auth") or {}
    if not secret:                          # leer = gespeichertes Geheimnis behalten
        secret = store.dec(cur.get("token_enc") if body.type == "token" else cur.get("key_enc"))
    if not secret:
        raise HTTPException(400, "API-Token bzw. API-Key angeben")
    if body.type == "key" and "@" not in body.email:
        raise HTTPException(400, "Für den Global API Key wird die E-Mail des Cloudflare-Kontos gebraucht")
    auth = {"type": body.type, "email": body.email.strip(),
            ("token_enc" if body.type == "token" else "key_enc"): store.enc(secret)}
    try:
        cf = Cf(auth)
        info = cf.verify()
        accounts = cf.accounts()
    except Exception as e:      # noqa: BLE001
        _err(e)

    def upd(d):
        d["auth"] = auth
    store.update(upd)
    db.add_audit_entry(user["username"], "cloudflare.auth", details=info.get("kind", ""))
    return {"ok": True, **info, "accounts": accounts}


class AccountBody(BaseModel):
    account_id: str
    public_base: str = ""


def _load_account(acc_id: str, public_base: str | None) -> dict:
    data = store.load()
    cf = Cf.from_store(data)
    accs = {a["id"]: a for a in cf.accounts()}
    if acc_id not in accs:
        raise ValueError("Konto nicht gefunden")
    zones = cf.zones(acc_id)
    team = cf.team_domain(acc_id)

    def upd(d):
        if (d.get("account") or {}).get("id") not in (None, acc_id) and d["routes"]:
            raise ValueError("Es gibt noch Routen im bisherigen Konto – erst entfernen")
        d["account"], d["zones"], d["team_domain"] = accs[acc_id], zones, team
        if public_base is not None:
            pb = public_base.strip().rstrip("/")
            if pb and not pb.startswith("https://"):
                raise ValueError("Öffentliche RMM-Adresse bitte mit https:// angeben")
            d["public_base"] = pb
        return {"account": accs[acc_id], "zones": zones, "team_domain": team}
    return store.update(upd)


@router.post("/account")
def set_account(body: AccountBody, user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        return _load_account(body.account_id, body.public_base)
    except Exception as e:      # noqa: BLE001
        _err(e)


@router.post("/refresh")
def refresh(user: dict = Depends(get_current_user)):
    _perm(user)
    acc = (store.load().get("account") or {}).get("id")
    if not acc:
        raise HTTPException(409, "Noch kein Konto gewählt")
    try:
        return _load_account(acc, None)
    except Exception as e:      # noqa: BLE001
        _err(e)


class RouteBody(BaseModel):
    id: str = ""
    name: str
    kind: str
    ref: dict = {}
    endpoint: str = "server"
    node_id: str = ""
    mode: str = ""
    hostname: str = ""
    access: str = "none"
    allow: list = []
    emails: list = []


KINDS = ("dashboard", "webdav", "sftp", "ftp", "console_ssh", "website", "docker", "agent", "custom")


@router.post("/routes")
def save_route(body: RouteBody, user: dict = Depends(get_current_user)):
    _perm(user)
    if body.kind not in KINDS:
        raise HTTPException(400, "Unbekannte Art")
    if not body.name.strip():
        raise HTTPException(400, "Name fehlt")
    if body.kind == "agent" and not db.get_client(body.ref.get("client_id", "")):
        raise HTTPException(400, "Agent unbekannt")

    def upd(d):
        old = store.route(d, body.id) if body.id else None
        r = old or {"id": secrets.token_hex(6), "created": int(time.time()), "created_by": user["username"], "state": {}}
        prev_host = (old or {}).get("hostname")
        r.update({"name": body.name.strip()[:60], "kind": body.kind, "ref": body.ref or {},
                  "endpoint": body.endpoint if body.endpoint in ("server", "node") else "server",
                  "node_id": body.node_id, "mode": body.mode, "hostname": body.hostname, "access": body.access,
                  "allow": [a for a in body.allow if isinstance(a, dict) and a.get("type") in ("user", "group")],
                  "emails": [str(e).strip() for e in body.emails if str(e).strip()]})
        sync.validate(r, d)
        if old and prev_host and prev_host != r.get("hostname"):
            r["state"]["old_dns"] = {"zone": old.get("zone_id"), "id": r["state"].pop("dns_id", None)}
        if not old:
            d["routes"].append(r)
        return r
    try:
        r = store.update(upd)
    except Exception as e:      # noqa: BLE001
        _err(e)
    # Alte Adresse aufraeumen (Adresse geaendert)
    od = (r.get("state") or {}).get("old_dns")
    if od and od.get("id"):
        try:
            Cf.from_store().dns_delete(od["zone"], od["id"])
        except CfError:
            pass
    db.add_audit_entry(user["username"], "cloudflare.route_saved", details=f"{r['name']} {r.get('hostname') or 'WARP'}")
    try:
        rep = sync.apply(user["username"])
    except Exception as e:      # noqa: BLE001
        _err(e)
    return {"ok": True, "route": _route_view(store.route(store.load(), r["id"]) or r), "report": rep}


@router.delete("/routes/{rid}")
def delete_route(rid: str, user: dict = Depends(get_current_user)):
    _perm(user)
    r = store.route(store.load(), rid)
    if not r:
        raise HTTPException(404, "Route nicht gefunden")
    sync.remove_route(rid)
    db.add_audit_entry(user["username"], "cloudflare.route_deleted", details=r["name"])
    try:
        rep = sync.apply(user["username"])
    except Exception as e:      # noqa: BLE001
        rep = {"errors": [str(e)]}
    return {"ok": True, "report": rep}


@router.post("/sync")
def do_sync(user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        return sync.apply(user["username"])
    except Exception as e:      # noqa: BLE001
        _err(e)


@router.post("/connector/{act}")
def connector_act(act: str, user: dict = Depends(get_current_user)):
    _perm(user)
    t = store.load()["tunnels"].get("server") or {}
    if act == "stop":
        connector.stop()
    elif act in ("start", "restart"):
        if not t.get("token_enc"):
            raise HTTPException(409, "Es gibt noch keinen Tunnel – zuerst abgleichen bzw. eine Route anlegen")
        (connector.restart if act == "restart" else connector.start)(store.dec(t["token_enc"]))
    else:
        raise HTTPException(400, "start | stop | restart")
    db.add_audit_entry(user["username"], f"cloudflare.connector_{act}")
    return connector.status()


@router.delete("/nodes/{cid}")
def delete_node(cid: str, user: dict = Depends(get_current_user)):
    _perm(user)
    try:
        sync.remove_node_tunnel(cid)
    except Exception as e:      # noqa: BLE001
        _err(e)
    return {"ok": True}


@router.delete("/all")
def delete_all(user: dict = Depends(get_current_user)):
    _perm(user)
    for r in list(store.load()["routes"]):
        sync.remove_route(r["id"])
    data = store.load()
    connector.stop()
    errs = []
    try:
        cf = Cf.from_store(data)
        acc = data["account"]["id"]
        for t in [data["tunnels"].get("server") or {}] + list(data["tunnels"]["nodes"].values()):
            if t.get("id"):
                try:
                    cf.tunnel_delete(acc, t["id"])
                except CfError as e:
                    errs.append(str(e))
        if data.get("oidc_bypass_app"):
            try:
                cf.app_delete(acc, data["oidc_bypass_app"])
            except CfError:
                pass
    except (CfError, KeyError):
        pass
    for cid in list(data["tunnels"]["nodes"]):
        try:
            sync._run(__import__("app.node_manager", fromlist=["NodesAPI"]).NodesAPI("cloudflare").remove(cid))
        except Exception:
            pass
    store.save(dict(store.DEFAULT, oidc=data.get("oidc") or {}))
    db.add_audit_entry(user["username"], "cloudflare.reset")
    return {"ok": True, "errors": errs}


@router.get("/directory")
def directory(user: dict = Depends(get_current_user)):
    _perm(user)
    return {"users": [{"id": u["id"], "username": u["username"]} for u in db.list_users()],
            "groups": [{"id": g["id"], "name": g["name"], "is_ad_group": g.get("is_ad_group")} for g in db.list_groups()]}

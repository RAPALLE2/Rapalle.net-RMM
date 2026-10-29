# sync.py (Modul "cloudflare") - Routen nach Cloudflare uebertragen
# -----------------------------------------------------------------
# apply() bringt Cloudflare auf den Stand der Routen (idempotent, jederzeit wiederholbar):
#   Tunnel anlegen (RMM-Server + je Node) -> Weiterleitungen zu Agents (Modul network,
#   dauerhaft) -> Ingress je Tunnel (aus den ECHTEN Ports, services.origin) -> DNS-CNAMEs
#   -> private Netze (WARP) -> Access: Login (RMM-Konto per OIDC, Einmal-PIN per E-Mail)
#   -> Node-Connector verteilen -> eigenen Connector starten.
# remove_route() raeumt alles einer Route ab (DNS, Access, Login, Netz, Weiterleitung).

from __future__ import annotations

import asyncio
import re
import secrets
import socket
import threading
import time

from app import modules

from . import connector, services, store
from .cfapi import Cf, CfError

_sync_lock = threading.Lock()
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _run(coro, timeout=120):
    """Async-Funktion des Backends aus diesem Thread aufrufen."""
    loop = modules._loop
    if loop is None:
        raise RuntimeError("Backend-Ereignisschleife nicht verfügbar")
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)


def tunnel_name(kind: str, extra: str = "") -> str:
    host = re.sub(r"[^a-z0-9-]", "-", socket.gethostname().lower())[:30] or "rmm"
    return f"rmm-{host}" if kind == "server" else f"rmm-node-{extra}"[:60]


# ===================================================================== Pruefung
def validate(r: dict, data: dict) -> None:
    proto = services.proto_of(r)
    mode = r.get("mode") or ("http" if proto == "http" else services.MODES[proto][0])
    if mode not in services.MODES[proto]:
        raise ValueError(f"Zugangsart „{mode}“ passt nicht zu {proto.upper()} "
                         f"(möglich: {', '.join(services.MODES[proto])})")
    r["mode"] = mode
    if mode != "warp":
        h = (r.get("hostname") or "").strip().lower().rstrip(".")
        if not HOST_RE.match(h):
            raise ValueError("Bitte eine gültige Adresse (z. B. rmm.firma.de) angeben")
        zone = next((z for z in data.get("zones") or [] if h == z["name"] or h.endswith("." + z["name"])), None)
        if not zone:
            raise ValueError("Die Adresse gehört zu keiner Zone dieses Cloudflare-Kontos")
        r["hostname"], r["zone_id"], r["zone"] = h, zone["id"], zone["name"]
        for o in data["routes"]:
            if o["id"] != r["id"] and o.get("mode") != "warp" and o.get("hostname") == h:
                raise ValueError(f"{h} wird schon von „{o['name']}“ benutzt")
    else:
        r["hostname"] = ""
    acc = r.get("access") or "none"
    if acc not in ("none", "rmm", "otp", "service"):
        raise ValueError("Schutz: none, rmm oder otp")
    if mode == "browser" and acc == "none":
        raise ValueError("SSH/VNC im Browser geht nur mit Cloudflare Access – bitte einen Login wählen")
    if mode == "warp" and acc != "none":
        acc = "none"            # Zugriff regeln dort die WARP-/Zero-Trust-Geraeteregeln
    if acc == "otp" and not [e for e in r.get("emails") or [] if "@" in e]:
        raise ValueError("Für den Einmal-PIN bitte mindestens eine E-Mail-Adresse angeben")
    r["access"] = acc
    if r["kind"] == "agent":
        if not (r.get("ref") or {}).get("client_id"):
            raise ValueError("Agent fehlt")
        if r.get("endpoint") == "node" and not r.get("node_id"):
            raise ValueError("Bitte die Node wählen, auf der der Tunnel enden soll")


# ===================================================================== Tunnel
def ensure_server_tunnel(cf: Cf, data: dict) -> dict:
    acc = data["account"]["id"]
    t = data["tunnels"].get("server") or {}
    if t.get("id"):
        try:
            if not cf.tunnel_get(acc, t["id"]).get("deleted_at"):
                return t
        except CfError as e:
            if e.status != 404:
                raise
    name = tunnel_name("server")
    found = cf.tunnel_find(acc, name)
    if found:
        t = {"id": found["id"], "name": found["name"], "token_enc": store.enc(cf.tunnel_token(acc, found["id"]))}
    else:
        n = cf.tunnel_create(acc, name)
        t = {"id": n["id"], "name": n["name"], "token_enc": store.enc(n["token"])}
    t["created"] = int(time.time())
    data["tunnels"]["server"] = t
    return t


def ensure_node_tunnel(cf: Cf, data: dict, cid: str) -> dict:
    acc = data["account"]["id"]
    t = data["tunnels"]["nodes"].get(cid) or {}
    if t.get("id"):
        try:
            if not cf.tunnel_get(acc, t["id"]).get("deleted_at"):
                return t
        except CfError as e:            # in Cloudflare geloescht -> neu anlegen
            if e.status != 404:
                raise
    name = tunnel_name("node", cid[:12])
    found = cf.tunnel_find(acc, name)
    if found:
        t = {"id": found["id"], "name": found["name"], "token_enc": store.enc(cf.tunnel_token(acc, found["id"]))}
    else:
        n = cf.tunnel_create(acc, name)
        t = {"id": n["id"], "name": n["name"], "token_enc": store.enc(n["token"])}
    t["deployed"] = False
    data["tunnels"]["nodes"][cid] = t
    return t


def _ensure_forward(r: dict, username: str) -> int:
    """endpoint server: dauerhafte Weiterleitung RMM -> Agent (Modul network)."""
    if not modules.is_enabled("network"):
        raise ValueError("Für „Tunnel endet am RMM-Server“ wird das Modul „Netzwerk“ gebraucht")
    pf = modules.use("network", "portforward")
    st = r.setdefault("state", {})
    host, port = services.agent_target(r)
    f = pf.forwards.get(st.get("forward_id") or "")
    if f and f.host == host and f.port == port and f.client_id == r["ref"]["client_id"]:
        return f.listen_port
    if f:
        _run(pf.stop(f.id))
    rec = _run(pf.create(r["ref"]["client_id"], port, username or "cloudflare", minutes=0, target_host=host,
                         allow_from=f"127.0.0.1/32,{services.own_ip()}/32", label=f"Cloudflare: {r['name']}"))
    st["forward_id"], st["forward_port"] = rec["id"], rec["listen_port"]
    return rec["listen_port"]


# ===================================================================== Access
def _base(data: dict) -> str:
    return (data.get("public_base") or "").rstrip("/")


def _app_type(r: dict) -> str:
    if r.get("mode") == "browser":
        return "ssh" if services.proto_of(r) == "ssh" else "vnc"
    return "self_hosted"


def _access(cf: Cf, data: dict, r: dict) -> None:
    acc, st = data["account"]["id"], r.setdefault("state", {})
    if r["access"] == "none":
        for key, fn in (("app_id", cf.app_delete), ("idp_id", cf.idp_delete)):
            if st.get(key):
                try:
                    fn(acc, st[key])
                except CfError:
                    pass
                st.pop(key, None)
        return
    if r["access"] == "service":
        # Fernzugriff der Client-App: eigener Service-Token je Route (nur wer im RMM das Recht auf
        # diesen Agenten hat, bekommt ihn) - keine Browser-Anmeldung noetig
        for key in ("idp_id",):
            if st.get(key):
                try:
                    cf.idp_delete(acc, st.pop(key))
                except CfError:
                    pass
        stk = r.get("service_token") or {}
        if not stk.get("id"):
            t = cf.service_token_create(acc, f"RMM-Client: {r['name']}")
            stk = {"id": t["id"], "client_id": t["client_id"], "secret_enc": store.enc(t["client_secret"])}
            r["service_token"] = stk
        st["app_id"] = cf.app_upsert(acc, st.get("app_id", ""), {
            "name": f"RMM: {r['name']}"[:60], "domain": r["hostname"], "type": "self_hosted", "session_duration": "24h",
            "app_launcher_visible": False})
        cf.app_policy_set(acc, st["app_id"], {"name": "RMM", "decision": "non_identity",
                                              "include": [{"service_token": {"token_id": stk["id"]}}], "precedence": 1})
        return
    if r["access"] == "rmm":
        base = _base(data)
        if not base.startswith("https://"):
            raise ValueError("Für den RMM-Login wird die öffentliche RMM-Adresse (https://…) gebraucht – "
                             "Tab „Verbindung“ (am besten eine Route „RMM-Dashboard“ ohne Schutz oder mit Schutz anlegen)")
        o = r.setdefault("oidc", {})
        if not o.get("client_id"):
            o["client_id"] = f"rmm-{r['id']}"
            o["secret_enc"] = store.enc(secrets.token_urlsafe(32))
        b = f"{base}/cf-oidc/{r['id']}"
        idp = cf.idp_upsert(acc, st.get("idp_id", ""), {
            "name": f"RMM-Login: {r['name']}"[:60], "type": "oidc",
            "config": {"client_id": o["client_id"], "client_secret": store.dec(o["secret_enc"]),
                       "auth_url": f"{b}/authorize", "token_url": f"{b}/token", "certs_url": f"{b}/jwks",
                       "scopes": ["openid", "email", "profile"], "claims": ["groups", "preferred_username"],
                       "pkce_enabled": True, "email_claim_name": "email"}})
        st["idp_id"] = idp
        include = [{"login_method": {"id": idp}}]
    else:
        if st.get("idp_id"):
            try:
                cf.idp_delete(acc, st.pop("idp_id"))
            except CfError:
                pass
        if not data.get("_otp_ok"):              # je Abgleich einmal pruefen (kann in Cloudflare geloescht sein)
            data["otp_idp"], data["_otp_ok"] = cf.otp_idp(acc), True
        idp = data["otp_idp"]
        include = [{"email": {"email": e.strip()}} for e in r.get("emails") or [] if "@" in e]
    domain = r["hostname"]
    st["app_id"] = cf.app_upsert(acc, st.get("app_id", ""), {
        "name": f"RMM: {r['name']}"[:60], "domain": domain, "type": _app_type(r), "session_duration": "24h",
        "allowed_idps": [idp], "auto_redirect_to_identity": True, "app_launcher_visible": True})
    cf.app_policy_set(acc, st["app_id"], {"name": "RMM", "decision": "allow", "include": include, "precedence": 1})


def _oidc_bypass(cf: Cf, data: dict) -> None:
    """Die Login-Adressen /cf-oidc/... duerfen nicht selbst hinter Access liegen (Cloudflare holt
    dort das Token) - liegt die RMM-Adresse hinter einem Schutz, bekommt /cf-oidc eine Ausnahme."""
    acc = data["account"]["id"]
    host = _base(data).split("://", 1)[-1].split("/", 1)[0]
    need = any(r["access"] == "rmm" for r in data["routes"]) and any(
        r.get("hostname") == host and r["access"] != "none" for r in data["routes"])
    cur = data.get("oidc_bypass_app", "")
    if need:
        data["oidc_bypass_app"] = cf.app_upsert(acc, cur, {
            "name": "RMM: Login-Ausnahme (/cf-oidc)", "domain": f"{host}/cf-oidc", "type": "self_hosted",
            "session_duration": "24h", "app_launcher_visible": False})
        cf.app_policy_set(acc, data["oidc_bypass_app"],
                          {"name": "RMM", "decision": "bypass", "include": [{"everyone": {}}], "precedence": 1})
    elif cur:
        try:
            cf.app_delete(acc, cur)
        except CfError:
            pass
        data["oidc_bypass_app"] = ""


# ===================================================================== Gesamtabgleich
def apply(username: str = "") -> dict:
    with _sync_lock:
        data = store.load()
        if not (data.get("account") or {}).get("id"):
            raise ValueError("Zuerst den Cloudflare-Zugang einrichten und ein Konto wählen")
        cf = Cf.from_store(data)
        acc = data["account"]["id"]
        report = {"routes": {}, "errors": []}
        server = ensure_server_tunnel(cf, data)
        tunnels = {"server": server["id"]}
        ingress: dict[str, list] = {"server": []}
        warp: dict[str, bool] = {"server": False}
        for r in data["routes"]:
            st = r.setdefault("state", {})
            try:
                validate(r, data)
                key = services.tunnel_key(r)
                if key.startswith("node:"):
                    nt = ensure_node_tunnel(cf, data, key[5:])
                    tunnels[key] = nt["id"]
                    ingress.setdefault(key, [])
                    warp.setdefault(key, False)
                fport = _ensure_forward(r, username) if r["kind"] == "agent" and key == "server" else None
                o = services.origin(r, fport)
                tid = tunnels[key]
                if r["mode"] == "warp":
                    warp[key] = True
                    net = f"{o['host']}/32"
                    st["teamnet_id"] = cf.teamnet_add(acc, net, tid, f"RMM: {r['name']}")
                    st["warp_address"] = f"{o['host']}:{o['port']}"
                else:
                    rule = {"hostname": r["hostname"], "service": o["service"]}
                    if o.get("path"):
                        rule["path"] = "^" + re.escape(o["path"])
                    if o["service"].startswith("https://"):
                        rule["originRequest"] = {"noTLSVerify": True}
                    ingress[key].append(rule)
                    if o.get("path"):                 # Website-Adresse: Rest der Adresse -> 404 statt Dashboard
                        ingress[key].append({"hostname": r["hostname"], "service": "http_status:404"})
                    st["dns_id"] = cf.dns_upsert(r["zone_id"], r["hostname"], tid)
                    if st.get("teamnet_id"):
                        try:
                            cf.teamnet_delete(acc, st.pop("teamnet_id"))
                        except CfError:
                            pass
                    _access(cf, data, r)
                st["error"], st["synced"] = "", int(time.time())
                st["service"] = o["service"] + (o.get("path") or "")
            except (ValueError, CfError, RuntimeError) as e:
                st["error"] = str(e)
                report["errors"].append(f"{r['name']}: {e}")
            report["routes"][r["id"]] = st.get("error") or "ok"
        try:
            _oidc_bypass(cf, data)
        except (ValueError, CfError) as e:
            report["errors"].append(f"Login-Ausnahme: {e}")
        # Ingress je Tunnel (Pfad-Regeln vor allgemeinen Regeln derselben Adresse) + Auffangregel
        for key, rules in ingress.items():
            rules.sort(key=lambda x: (x.get("hostname", ""), 0 if x.get("path") else 1))
            try:
                cf.tunnel_config(acc, tunnels[key], rules + [{"service": "http_status:404"}], warp[key])
            except CfError as e:
                report["errors"].append(f"Tunnel {key}: {e}")
        # Node-Connectoren verteilen (Node-Teil des Moduls mit Tunnel-Token)
        for key in [k for k in tunnels if k.startswith("node:")]:
            cid = key[5:]
            t = data["tunnels"]["nodes"][cid]
            try:
                _run(_deploy_node(cid, store.dec(t["token_enc"])))
                t["deployed"], t["error"] = True, ""
            except Exception as e:      # noqa: BLE001
                t["deployed"], t["error"] = False, str(e)
                report["errors"].append(f"Node {cid[:8]}: {e}")
        data.pop("_otp_ok", None)
        data["last_sync"] = int(time.time())
        data["last_error"] = "; ".join(report["errors"])[:2000]
        store.save(data)
    connector.start(store.dec(server["token_enc"]))
    return report


async def _deploy_node(cid: str, token: str):
    from app.node_manager import NodesAPI
    # Diese Node wurde im Cloudflare-Modul ausdruecklich fuer den Tunnel gewaehlt -> aktivieren
    return await NodesAPI("cloudflare").deploy(cid, {"tunnel_token": token}, activate=True)


def remove_route(rid: str) -> None:
    with _sync_lock:
        data = store.load()
        r = store.route(data, rid)
        if not r:
            return
        st = dict(r.get("state") or {})
        # Ein privates Netz (IP/32) teilen sich ggf. mehrere WARP-Routen derselben Adresse
        if st.get("teamnet_id") and any((o.get("state") or {}).get("teamnet_id") == st["teamnet_id"]
                                        for o in data["routes"] if o["id"] != rid):
            st.pop("teamnet_id")
        try:
            cf = Cf.from_store(data)
            acc = data["account"]["id"]
            for key, fn in (("app_id", lambda i: cf.app_delete(acc, i)), ("idp_id", lambda i: cf.idp_delete(acc, i)),
                            ("teamnet_id", lambda i: cf.teamnet_delete(acc, i))):
                if st.get(key):
                    try:
                        fn(st[key])
                    except CfError:
                        pass
            if (r.get("service_token") or {}).get("id"):
                try:
                    cf.service_token_delete(acc, r["service_token"]["id"])
                except CfError:
                    pass
            if st.get("dns_id") and r.get("zone_id"):
                try:
                    cf.dns_delete(r["zone_id"], st["dns_id"])
                except CfError:
                    pass
        except CfError:
            pass
        if st.get("forward_id") and modules.is_enabled("network"):
            try:
                _run(modules.use("network", "portforward").stop(st["forward_id"]))
            except Exception:
                pass
        data["routes"] = [x for x in data["routes"] if x["id"] != rid]
        store.save(data)


def remove_node_tunnel(cid: str) -> None:
    with _sync_lock:
        data = store.load()
        if any(services.tunnel_key(r) == f"node:{cid}" for r in data["routes"]):
            raise ValueError("Es gibt noch Routen über diese Node")
        t = data["tunnels"]["nodes"].pop(cid, None)
        store.save(data)
    if t:
        try:
            _run(__import__("app.node_manager", fromlist=["NodesAPI"]).NodesAPI("cloudflare").remove(cid))
        except Exception:
            pass
        try:
            Cf.from_store().tunnel_delete(store.load()["account"]["id"], t["id"])
        except CfError:
            pass

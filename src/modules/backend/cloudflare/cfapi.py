# cfapi.py (Modul "cloudflare") - kleiner Cloudflare-API-Client (nur Standardbibliothek)
# -------------------------------------------------------------------------------------
# Anmeldung: API-Token (empfohlen) ODER Global API Key + E-Mail.
# Benoetigte Token-Rechte: Account > Cloudflare Tunnel: Edit, Account > Access: Apps and
# Policies: Edit, Account > Access: Organizations, Identity Providers, and Groups: Edit,
# Account > Zero Trust: Edit (private Netze/WARP), Zone > DNS: Edit, Zone > Zone: Read.
# RMM_CF_API (Umgebung) ersetzt die API-Adresse - nur fuer Tests.

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from . import store

API = os.environ.get("RMM_CF_API", "https://api.cloudflare.com/client/v4").rstrip("/")


class CfError(RuntimeError):
    def __init__(self, msg: str, status: int = 0, codes: list | None = None):
        super().__init__(msg)
        self.status = status
        self.codes = codes or []


class Cf:
    def __init__(self, auth: dict):
        self.type = auth.get("type") or "token"
        self.email = auth.get("email") or ""
        self.secret = store.dec(auth.get("token_enc") if self.type == "token" else auth.get("key_enc"))
        if not self.secret:
            raise CfError("Cloudflare-Zugang ist nicht eingerichtet")

    @classmethod
    def from_store(cls, data: dict | None = None) -> "Cf":
        return cls((data or store.load()).get("auth") or {})

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "User-Agent": "RAPALLE.net-RMM"}
        if self.type == "token":
            h["Authorization"] = f"Bearer {self.secret}"
        else:
            h["X-Auth-Email"], h["X-Auth-Key"] = self.email, self.secret
        return h

    def call(self, method: str, path: str, body=None, query: dict | None = None, raw: bool = False):
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read() or b"{}")
            except ValueError:
                payload = {}
            errs = payload.get("errors") or []
            msg = "; ".join(f"{x.get('message')} ({x.get('code')})" for x in errs) or f"HTTP {e.code}"
            raise CfError(f"Cloudflare: {msg}", e.code, [x.get("code") for x in errs]) from None
        except urllib.error.URLError as e:
            raise CfError(f"Cloudflare nicht erreichbar: {e.reason}") from None
        if raw:
            return payload
        if not payload.get("success", True):
            errs = payload.get("errors") or []
            raise CfError("Cloudflare: " + ("; ".join(str(x.get("message")) for x in errs) or "Fehler"))
        return payload.get("result")

    def all(self, path: str, query: dict | None = None) -> list:
        """Alle Seiten einer Liste holen."""
        out, page = [], 1
        while True:
            q = {"per_page": 50, "page": page, **(query or {})}
            res = self.call("GET", path, query=q, raw=True)
            items = res.get("result") or []
            out += items
            info = res.get("result_info") or {}
            if not items or page >= int(info.get("total_pages") or 1):
                return out
            page += 1

    # ----------------------------------------------------------------- Konto
    def verify(self) -> dict:
        if self.type == "token":
            res = self.call("GET", "/user/tokens/verify")
            if (res or {}).get("status") not in (None, "active"):
                raise CfError(f"API-Token ist nicht aktiv ({res.get('status')})")
            return {"ok": True, "kind": "API-Token"}
        u = self.call("GET", "/user")
        return {"ok": True, "kind": "Global API Key", "email": (u or {}).get("email")}

    def accounts(self) -> list:
        return [{"id": a["id"], "name": a.get("name", "")} for a in self.all("/accounts")]

    def zones(self, account_id: str) -> list:
        return [{"id": z["id"], "name": z["name"], "status": z.get("status")}
                for z in self.all("/zones", {"account.id": account_id})]

    def team_domain(self, acc: str) -> str:
        try:
            org = self.call("GET", f"/accounts/{acc}/access/organizations") or {}
            return org.get("auth_domain") or ""
        except CfError:
            return ""

    # ----------------------------------------------------------------- Tunnel
    def tunnel_create(self, acc: str, name: str) -> dict:
        t = self.call("POST", f"/accounts/{acc}/cfd_tunnel", {"name": name, "config_src": "cloudflare"})
        tok = t.get("token") or self.call("GET", f"/accounts/{acc}/cfd_tunnel/{t['id']}/token")
        return {"id": t["id"], "name": t.get("name", name), "token": tok}

    def tunnel_get(self, acc: str, tid: str) -> dict:
        return self.call("GET", f"/accounts/{acc}/cfd_tunnel/{tid}") or {}

    def tunnel_find(self, acc: str, name: str) -> dict | None:
        found = self.all(f"/accounts/{acc}/cfd_tunnel", {"name": name, "is_deleted": "false"})
        return found[0] if found else None

    def tunnel_token(self, acc: str, tid: str) -> str:
        return self.call("GET", f"/accounts/{acc}/cfd_tunnel/{tid}/token")

    def tunnel_delete(self, acc: str, tid: str) -> None:
        try:
            self.call("DELETE", f"/accounts/{acc}/cfd_tunnel/{tid}/connections")
        except CfError:
            pass
        self.call("DELETE", f"/accounts/{acc}/cfd_tunnel/{tid}")

    def tunnel_config(self, acc: str, tid: str, ingress: list, warp: bool) -> None:
        self.call("PUT", f"/accounts/{acc}/cfd_tunnel/{tid}/configurations",
                  {"config": {"ingress": ingress, "warp-routing": {"enabled": warp}}})

    # ----------------------------------------------------------------- DNS
    def dns_upsert(self, zone: str, name: str, tid: str) -> str:
        target = f"{tid}.cfargotunnel.com"
        rec = {"type": "CNAME", "name": name, "content": target, "proxied": True,
               "comment": "RAPALLE.net RMM Tunnel"}
        found = self.all(f"/zones/{zone}/dns_records", {"name": name})
        for r in found:
            if r.get("type") == "CNAME":
                if r.get("content") != target or not r.get("proxied"):
                    self.call("PUT", f"/zones/{zone}/dns_records/{r['id']}", rec)
                return r["id"]
            raise CfError(f"Für {name} gibt es schon einen {r.get('type')}-Eintrag – bitte in Cloudflare entfernen")
        return self.call("POST", f"/zones/{zone}/dns_records", rec)["id"]

    def dns_delete(self, zone: str, rid: str) -> None:
        self.call("DELETE", f"/zones/{zone}/dns_records/{rid}")

    # ----------------------------------------------------------------- Access
    def idp_upsert(self, acc: str, idp_id: str, body: dict) -> str:
        if idp_id:
            try:
                self.call("PUT", f"/accounts/{acc}/access/identity_providers/{idp_id}", body)
                return idp_id
            except CfError as e:
                if e.status != 404:
                    raise
        return self.call("POST", f"/accounts/{acc}/access/identity_providers", body)["id"]

    def idp_delete(self, acc: str, idp_id: str) -> None:
        self.call("DELETE", f"/accounts/{acc}/access/identity_providers/{idp_id}")

    def otp_idp(self, acc: str) -> str:
        for i in self.all(f"/accounts/{acc}/access/identity_providers"):
            if i.get("type") == "onetimepin":
                return i["id"]
        return self.call("POST", f"/accounts/{acc}/access/identity_providers",
                         {"name": "One-time PIN", "type": "onetimepin", "config": {}})["id"]

    def app_upsert(self, acc: str, app_id: str, body: dict) -> str:
        if app_id:
            try:
                self.call("PUT", f"/accounts/{acc}/access/apps/{app_id}", body)
                return app_id
            except CfError as e:
                if e.status != 404:
                    raise
        return self.call("POST", f"/accounts/{acc}/access/apps", body)["id"]

    def app_delete(self, acc: str, app_id: str) -> None:
        self.call("DELETE", f"/accounts/{acc}/access/apps/{app_id}")

    def app_policy_set(self, acc: str, app_id: str, policy: dict) -> None:
        """Genau EINE Richtlinie (\"RMM\") je Anwendung halten."""
        pols = self.call("GET", f"/accounts/{acc}/access/apps/{app_id}/policies") or []
        mine = [p for p in pols if p.get("name") == policy["name"]]
        if mine:
            self.call("PUT", f"/accounts/{acc}/access/apps/{app_id}/policies/{mine[0]['id']}", policy)
        else:
            self.call("POST", f"/accounts/{acc}/access/apps/{app_id}/policies", policy)

    # ----------------------------------------------------------------- private Netze (WARP)
    def teamnet_add(self, acc: str, network: str, tid: str, comment: str) -> str:
        for r in self.all(f"/accounts/{acc}/teamnet/routes", {"network_subset": network, "is_deleted": "false"}):
            if r.get("network") == network:
                if r.get("tunnel_id") != tid:
                    raise CfError(f"Das Netz {network} ist bereits einem anderen Tunnel zugeordnet")
                return r["id"]
        return self.call("POST", f"/accounts/{acc}/teamnet/routes",
                         {"network": network, "tunnel_id": tid, "comment": comment})["id"]

    def teamnet_delete(self, acc: str, rid: str) -> None:
        self.call("DELETE", f"/accounts/{acc}/teamnet/routes/{rid}")

    # ----------------------------------------------------------------- Service-Token (Client-App)
    def service_token_create(self, acc: str, name: str) -> dict:
        t = self.call("POST", f"/accounts/{acc}/access/service_tokens", {"name": name[:60], "duration": "8760h"})
        return {"id": t["id"], "client_id": t["client_id"], "client_secret": t["client_secret"]}

    def service_token_delete(self, acc: str, tid: str) -> None:
        self.call("DELETE", f"/accounts/{acc}/access/service_tokens/{tid}")

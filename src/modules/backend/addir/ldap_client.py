# ldap_client.py (Modul "addir") - alle AD-Operationen ueber ldap3
# ----------------------------------------------------------------
# Eine Verbindung (conn-Dict aus store) wird zu einem gebundenen ldap3-Client.
# Bietet: suchen (User/Gruppen/OUs/Computer), User anlegen/bearbeiten/verschieben/
# loeschen, aktivieren/sperren/entsperren, Ablaufdatum, Passwort setzen/zuruecksetzen,
# Gruppen anlegen/verschieben/loeschen, Mitglieder hinzufuegen/entfernen, OUs
# anlegen/verschieben/loeschen. Passwort-Schreibvorgaenge verlangen - je Verbindung
# einstellbar (pw_requires_tls) - eine verschluesselte Verbindung (LDAPS/StartTLS),
# weil AD unicodePwd nur verschluesselt annimmt.
#
# RMM_LDAP_MOCK (nur Tests): Pfad -> ldap3-MOCK-Server statt echtem AD.
from __future__ import annotations

import os

from . import store


class AdError(RuntimeError):
    pass


def _ldap():
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars
        from ldap3.utils.dn import escape_rdn, safe_dn
        return ldap3, LDAPException, escape_filter_chars, escape_rdn, safe_dn
    except ImportError:
        raise AdError("Das Paket 'ldap3' fehlt. Modul neu aktivieren oder 'pip install ldap3' ausführen.")


# AD userAccountControl-Flags
UAC_DISABLED = 0x0002
UAC_NORMAL = 0x0200
UAC_DONT_EXPIRE_PASSWD = 0x10000
UAC_PASSWORD_EXPIRED = 0x800000


# --------------------------------------------------------- Verbindung
class Ad:
    def __init__(self, conn: dict):
        self.c = conn
        self.base_dn = (conn.get("base_dn") or "").strip()
        self._conn = None
        self.encrypted = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *a):
        self.close()

    def _server(self):
        ldap3, *_ = _ldap()
        mock = os.environ.get("RMM_LDAP_MOCK")
        if mock:
            srv = ldap3.Server("mock_ad", get_info=ldap3.OFFLINE_AD_2012_R2)
            return srv, True
        host = (self.c.get("server") or "").strip()
        use_ssl = bool(self.c.get("use_ssl"))
        if host.startswith("ldaps://"):
            use_ssl, host = True, host[len("ldaps://"):]
        elif host.startswith("ldap://"):
            host = host[len("ldap://"):]
        port = self.c.get("port")
        try:
            port = int(port) if port not in (None, "") else None
        except (TypeError, ValueError):
            port = None
        tls = None
        if use_ssl or self.c.get("start_tls"):
            import ssl
            validate = ssl.CERT_REQUIRED if self.c.get("verify_cert") else ssl.CERT_NONE
            tls = ldap3.Tls(validate=validate)
        srv = ldap3.Server(host, port=port, use_ssl=use_ssl, tls=tls,
                           get_info=ldap3.ALL, connect_timeout=8)
        return srv, use_ssl

    def connect(self):
        ldap3, LDAPException, *_ = _ldap()
        mock = os.environ.get("RMM_LDAP_MOCK")
        srv, use_ssl = self._server()
        bind_user = (self.c.get("bind_user") or "").strip()
        bind_pw = store.dec(self.c.get("bind_pw_enc", ""))
        try:
            if mock:
                self._conn = ldap3.Connection(srv, client_strategy=ldap3.MOCK_SYNC)
                self._conn.strategy.entries_from_json(mock)
                self._conn.bind()
                self.encrypted = True
                return self
            self._conn = ldap3.Connection(srv, user=bind_user or None, password=bind_pw or None,
                                          authentication=ldap3.SIMPLE if bind_user else ldap3.ANONYMOUS,
                                          auto_bind=False, raise_exceptions=True)
            if self.c.get("start_tls") and not use_ssl:
                self._conn.open()
                self._conn.start_tls()
            if not self._conn.bind():
                raise AdError("Anmeldung am AD fehlgeschlagen (Bind-Benutzer/Passwort prüfen).")
            self.encrypted = bool(use_ssl or self.c.get("start_tls"))
        except LDAPException as e:
            raise AdError(f"Verbindung zum AD fehlgeschlagen: {e}") from None
        return self

    def close(self):
        try:
            if self._conn:
                self._conn.unbind()
        except Exception:
            pass

    # --------------------------------------------------------- Helfer
    def _require_pw_channel(self):
        if self.c.get("pw_requires_tls", True) and not self.encrypted:
            raise AdError("Passwort-Änderungen brauchen eine verschlüsselte Verbindung (LDAPS/StartTLS). "
                          "In den Verbindungs-Einstellungen aktivieren oder (nur Test) die Pflicht abschalten.")

    def _search(self, base, flt, attrs=("*",), scope=None):
        ldap3, LDAPException, *_ = _ldap()
        scope = scope or ldap3.SUBTREE
        try:
            self._conn.search(base or self.base_dn, flt, search_scope=scope, attributes=list(attrs))
        except LDAPException as e:
            raise AdError(f"Suche fehlgeschlagen: {e}") from None
        return list(self._conn.entries)

    def _do(self, what, fn):
        ldap3, LDAPException, *_ = _ldap()
        try:
            ok = fn()
        except LDAPException as e:
            raise AdError(f"{what} fehlgeschlagen: {e}") from None
        if not ok:
            raise AdError(f"{what} fehlgeschlagen: {self._conn.result.get('description')} "
                          f"- {self._conn.result.get('message') or ''}".strip(" -"))
        return True

    @staticmethod
    def _entry(e) -> dict:
        d = {"dn": e.entry_dn}
        for k in e.entry_attributes:
            v = e[k].values
            d[k] = v[0] if len(v) == 1 else v
        return d

    def _parent_ou(self, dn: str) -> str:
        return dn.split(",", 1)[1] if "," in dn else self.base_dn

    # --------------------------------------------------------- Lesen
    def tree(self) -> list[dict]:
        """Alle OUs + Container als flache Liste (fuer den Baum/Verschieben)."""
        _, _, esc, *_ = _ldap()
        ous = self._search(self.base_dn, "(|(objectClass=organizationalUnit)(objectClass=container))",
                           ("distinguishedName", "name", "ou"))
        out = [{"dn": self.base_dn, "name": self.base_dn.split(",")[0].split("=")[-1], "type": "base"}]
        for e in ous:
            d = self._entry(e)
            out.append({"dn": d["dn"], "name": d.get("ou") or d.get("name") or d["dn"].split(",")[0],
                        "type": "ou" if "organizationalUnit" in str(e.entry_dn).lower() or d.get("ou") else "container"})
        return out

    def search(self, kind: str, query: str = "", base: str | None = None, limit: int = 300) -> list[dict]:
        _, _, esc, *_ = _ldap()
        q = esc(query.strip()) if query else ""
        cls = {"user": "user", "group": "group", "ou": "organizationalUnit", "computer": "computer"}.get(kind, "user")
        if kind == "user":
            # objectClass=user trifft auch Computer -> Computer ausschliessen.
            # (objectCategory=person braeuchte je AD die kanonische Form, daher so robuster.)
            base_flt = "(&(objectClass=user)(!(objectClass=computer)))"
        else:
            base_flt = f"(objectClass={cls})"
        if q:
            if kind == "group" or kind == "ou":
                flt = f"(&{base_flt}(|(cn=*{q}*)(name=*{q}*)(sAMAccountName=*{q}*)))"
            else:
                flt = f"(&{base_flt}(|(cn=*{q}*)(sAMAccountName=*{q}*)(displayName=*{q}*)(mail=*{q}*)(sn=*{q}*)(givenName=*{q}*)))"
        else:
            flt = base_flt
        attrs = {
            "user": ("sAMAccountName", "displayName", "givenName", "sn", "mail", "userAccountControl",
                     "distinguishedName", "memberOf", "telephoneNumber", "department", "title", "manager",
                     "accountExpires", "lockoutTime", "whenCreated", "description", "userPrincipalName"),
            "group": ("sAMAccountName", "cn", "name", "distinguishedName", "member", "description", "groupType"),
            "ou": ("ou", "name", "distinguishedName", "description"),
            "computer": ("sAMAccountName", "cn", "dNSHostName", "operatingSystem", "distinguishedName",
                         "userAccountControl", "whenCreated"),
        }[kind]
        res = self._search(base or self.base_dn, flt, attrs)[:limit]
        out = [self._entry(e) for e in res]
        if kind == "user" or kind == "computer":
            for d in out:
                uac = int(d.get("userAccountControl") or 0)
                d["enabled"] = not (uac & UAC_DISABLED)
                d["locked"] = bool(int(d.get("lockoutTime") or 0))
        return out

    def get_by_dn(self, dn: str, attrs=("*",)) -> dict | None:
        res = self._search(dn, "(objectClass=*)", attrs, scope=_ldap()[0].BASE)
        return self._entry(res[0]) if res else None

    # --------------------------------------------------------- Benutzer
    def create_user(self, ou_dn: str, sam: str, fields: dict, password: str = "",
                     enabled: bool = True) -> str:
        ldap3, _, esc, erdn, safe_dn = _ldap()
        sam = sam.strip()
        cn = (fields.get("displayName") or fields.get("cn") or sam).strip()
        dn = f"CN={erdn(cn)},{ou_dn}"
        upn = fields.get("userPrincipalName") or self._default_upn(sam)
        attrs = {"sAMAccountName": sam, "userPrincipalName": upn, "userAccountControl": UAC_NORMAL | UAC_DISABLED}
        for k in ("displayName", "givenName", "sn", "mail", "telephoneNumber", "department", "title", "description"):
            if fields.get(k):
                attrs[k] = fields[k]
        self._do("Benutzer anlegen", lambda: self._conn.add(dn, ["top", "person", "organizationalPerson", "user"], attrs))
        if password:
            self.set_password(dn, password, must_change=fields.get("must_change", False))
        if enabled and password:
            self.set_enabled(dn, True)
        return dn

    def _default_upn(self, sam: str) -> str:
        # aus base_dn eine Domain bauen: DC=firma,DC=de -> firma.de
        parts = [p.split("=", 1)[1] for p in self.base_dn.split(",") if p.strip().upper().startswith("DC=")]
        dom = ".".join(parts) if parts else "local"
        return f"{sam}@{dom}"

    def modify_attrs(self, dn: str, changes: dict) -> None:
        ldap3, *_ = _ldap()
        mod = {}
        for k, v in changes.items():
            if v is None or v == "":
                mod[k] = [(ldap3.MODIFY_REPLACE, [])]
            else:
                mod[k] = [(ldap3.MODIFY_REPLACE, [v])]
        if mod:
            self._do("Attribute ändern", lambda: self._conn.modify(dn, mod))

    def rename_move(self, dn: str, new_parent: str | None = None, new_cn: str | None = None) -> str:
        ldap3, _, esc, erdn, _ = _ldap()
        cur_rdn = dn.split(",", 1)[0]
        rdn = f"CN={erdn(new_cn)}" if new_cn else cur_rdn
        self._do("Verschieben/Umbenennen", lambda: self._conn.modify_dn(dn, rdn, new_superior=new_parent or None))
        parent = new_parent or self._parent_ou(dn)
        return f"{rdn},{parent}"

    def delete(self, dn: str) -> None:
        self._do("Löschen", lambda: self._conn.delete(dn))

    def set_enabled(self, dn: str, enabled: bool) -> None:
        cur = self.get_by_dn(dn, ("userAccountControl",)) or {}
        uac = int(cur.get("userAccountControl") or UAC_NORMAL)
        uac = (uac & ~UAC_DISABLED) if enabled else (uac | UAC_DISABLED)
        self.modify_attrs(dn, {"userAccountControl": uac})

    def unlock(self, dn: str) -> None:
        self.modify_attrs(dn, {"lockoutTime": 0})

    def set_expiry(self, dn: str, when: str | None) -> None:
        # when = "never" / "" -> nie; sonst "YYYY-MM-DD"
        if not when or when == "never":
            val = 0            # 0 bzw. 9223372036854775807 = nie; 0 ist bei AD "nie"
        else:
            import datetime
            dt = datetime.datetime.strptime(when, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
            epoch = datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc)
            val = int((dt - epoch).total_seconds() * 10_000_000)
        self.modify_attrs(dn, {"accountExpires": val})

    # --------------------------------------------------------- Passwort
    @staticmethod
    def _pw_value(password: str) -> bytes:
        return ('"%s"' % password).encode("utf-16-le")

    def set_password(self, dn: str, password: str, must_change: bool = False) -> None:
        ldap3, *_ = _ldap()
        self._require_pw_channel()
        self._do("Passwort setzen",
                 lambda: self._conn.modify(dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [self._pw_value(password)])]}))
        if must_change:
            self.modify_attrs(dn, {"pwdLastSet": 0})
        else:
            self.modify_attrs(dn, {"pwdLastSet": -1})

    # --------------------------------------------------------- Gruppen
    def create_group(self, ou_dn: str, name: str, scope: str = "global", security: bool = True,
                     description: str = "") -> str:
        ldap3, _, esc, erdn, _ = _ldap()
        GROUP_TYPE = {"global": 0x00000002, "domainlocal": 0x00000004, "universal": 0x00000008}
        gt = GROUP_TYPE.get(scope, 0x00000002)
        if security:
            gt |= 0x80000000
        gt = gt - 0x100000000 if gt > 0x7FFFFFFF else gt      # signed 32-bit fuer AD
        dn = f"CN={erdn(name)},{ou_dn}"
        attrs = {"sAMAccountName": name, "groupType": gt}
        if description:
            attrs["description"] = description
        self._do("Gruppe anlegen", lambda: self._conn.add(dn, ["top", "group"], attrs))
        return dn

    def add_member(self, group_dn: str, member_dn: str) -> None:
        ldap3, *_ = _ldap()
        self._do("Mitglied hinzufügen",
                 lambda: self._conn.modify(group_dn, {"member": [(ldap3.MODIFY_ADD, [member_dn])]}))

    def remove_member(self, group_dn: str, member_dn: str) -> None:
        ldap3, *_ = _ldap()
        self._do("Mitglied entfernen",
                 lambda: self._conn.modify(group_dn, {"member": [(ldap3.MODIFY_DELETE, [member_dn])]}))

    def members(self, group_dn: str) -> list[dict]:
        g = self.get_by_dn(group_dn, ("member",)) or {}
        mem = g.get("member") or []
        if isinstance(mem, str):
            mem = [mem]
        out = []
        for dn in mem:
            e = self.get_by_dn(dn, ("sAMAccountName", "displayName", "cn", "objectClass"))
            if e:
                out.append({"dn": dn, "name": e.get("displayName") or e.get("cn") or e.get("sAMAccountName") or dn,
                            "sam": e.get("sAMAccountName")})
        return out

    # --------------------------------------------------------- OU
    def create_ou(self, parent_dn: str, name: str, description: str = "") -> str:
        ldap3, _, esc, erdn, _ = _ldap()
        dn = f"OU={erdn(name)},{parent_dn}"
        attrs = {}
        if description:
            attrs["description"] = description
        self._do("OU anlegen", lambda: self._conn.add(dn, ["top", "organizationalUnit"], attrs or None))
        return dn


def open_conn(cid: str) -> Ad:
    c = store.get(cid)
    if not c:
        raise AdError("AD-Verbindung nicht gefunden")
    return Ad(c)


def test(conn: dict) -> dict:
    """Verbindung pruefen -> {ok, encrypted, base_dn, info}."""
    try:
        with Ad(conn) as ad:
            whoami = ""
            try:
                whoami = ad._conn.extend.standard.who_am_i() or ""
            except Exception:
                pass
            return {"ok": True, "encrypted": ad.encrypted, "base_dn": ad.base_dn, "whoami": whoami}
    except AdError as e:
        return {"ok": False, "error": str(e)}

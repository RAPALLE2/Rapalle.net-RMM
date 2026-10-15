#!/usr/bin/env python3
"""
rmm_api_client.py - Beispiel-Client fuer die REST-API des RAPALLE.net RMM
==========================================================================
Nur Python-Standardbibliothek (Python 3.8+), keine Installation noetig.
Ausfuehrliche Beschreibung: docs/REST-API.md

Als Bibliothek:

    from rmm_api_client import RmmApi

    with RmmApi("https://rmm.firma.de", "apibot", api_key="rmmak_...") as rmm:
        for c in rmm.get("/clients"):
            print(c["hostname"], "online" if c.get("online") else "offline")

Als Kommandozeilenwerkzeug:

    export RMM_SERVER=https://rmm.firma.de RMM_USER=apibot RMM_API_KEY=rmmak_...
    python3 rmm_api_client.py status
    python3 rmm_api_client.py clients
    python3 rmm_api_client.py offline
    python3 rmm_api_client.py exec <client-id> "hostname"
    python3 rmm_api_client.py get /modules
    python3 rmm_api_client.py call GET /spaces/fs/list path=Mein%20Space
    python3 rmm_api_client.py routes

Anmeldung:
  * API-Konto (Vorlage "API-Benutzer"): Benutzername + API-Schluessel (rmmak_...)
  * normales Konto mit dem Recht "REST-API benutzen": Benutzername + Passwort (+ 2FA-Code)
Die Betriebsart des Servers (secure / mit Benutzer / ohne Benutzer) erkennt der
Client selbst ueber GET /api/v1/status.

Schluessel-Rotation: Ist auf dem Server "API-Schluessel automatisch rotieren" aktiv,
liefert die Anmeldung irgendwann einen neuen Schluessel (new_api_key). Der Client
benutzt ihn sofort und ruft on_key_rotated(neuer_schluessel) auf - dort speichern!
Kommandozeile: mit RMM_API_KEY_FILE=<datei> wird der Schluessel aus der Datei gelesen
und ein neuer automatisch hineingeschrieben.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar


class RmmApiError(Exception):
    """Fehler der API (status = HTTP-Status, detail = Meldung des Servers)."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class RmmApi:
    """Kleiner Client fuer /api/v1 mit automatischer Anmeldung.

    server     Basis-Adresse des RMM, z.B. "https://rmm.firma.de"
    username   Kontoname
    api_key    API-Schluessel eines API-Kontos (rmmak_...)           ODER
    password   Passwort eines normalen Kontos mit Recht "use_api" (+ code fuer 2FA)
    verify_tls False nur bei selbst signiertem Zertifikat (unsicher!)
    label      erscheint in der Modul-App unter "Anmeldungen" (z.B. Name des Skripts)
    on_key_rotated  Funktion(neuer_schluessel) - wird aufgerufen, wenn der Server den
               API-Schluessel rotiert hat. Den neuen Schluessel dort dauerhaft speichern,
               der alte gilt nur noch die eingestellte Uebergangszeit.
    """

    def __init__(self, server: str, username: str = "", api_key: str = "", password: str = "",
                 code: str = "", realm: str = "local", verify_tls: bool = True, label: str = "rmm_api_client.py",
                 timeout: int = 60, on_key_rotated=None):
        self.base = server.rstrip("/") + "/api/v1"
        self.username, self.api_key, self.password = username, api_key, password
        self.code, self.realm, self.label, self.timeout = code, realm, label, timeout
        self.on_key_rotated = on_key_rotated
        self.token = ""                       # secure-Modus: langer Code
        self.session = ""                     # Modus "mit Benutzer": Sitzungs-ID
        self.mode = ""
        ctx = None
        if not verify_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()),
                                                   urllib.request.HTTPSHandler(context=ctx))

    # ------------------------------------------------------------ Grundlage
    def _raw(self, method: str, path: str, body=None, params: dict | None = None, auth: bool = True):
        url = self.base + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if auth and self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if auth and self.session:
            req.add_header("X-API-Session", self.session)
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                raw = r.read()
                ctype = r.headers.get("Content-Type", "")
                return json.loads(raw) if raw and "json" in ctype else raw
        except urllib.error.HTTPError as e:
            detail = e.reason
            try:
                detail = json.loads(e.read() or b"{}").get("detail") or detail
            except ValueError:
                pass
            raise RmmApiError(e.code, str(detail)) from None

    def request(self, method: str, path: str, body=None, params: dict | None = None):
        """Aufruf mit automatischer (Neu-)Anmeldung, falls der Code abgelaufen ist."""
        if not self.mode:
            self.login()
        try:
            return self._raw(method, path, body, params)
        except RmmApiError as e:
            if e.status == 401 and self.mode in ("secure", "user"):
                self.login()                       # einmal neu anmelden und wiederholen
                return self._raw(method, path, body, params)
            raise

    def get(self, path, **params):
        return self.request("GET", path, params=params or None)

    def post(self, path, body=None, **params):
        return self.request("POST", path, body if body is not None else {}, params or None)

    def put(self, path, body=None):
        return self.request("PUT", path, body if body is not None else {})

    def patch(self, path, body=None):
        return self.request("PATCH", path, body if body is not None else {})

    def delete(self, path, **params):
        return self.request("DELETE", path, params=params or None)

    def call(self, method: str, dashboard_path: str, body=None, **params):
        """Jede Dashboard-Funktion ueber die Bruecke: call("GET", "/clients") == /api/clients."""
        return self.request(method, "/call/" + dashboard_path.lstrip("/"), body, params or None)

    # ------------------------------------------------------------ Anmeldung
    def status(self) -> dict:
        return self._raw("GET", "/status", auth=False)

    def login(self) -> dict:
        st = self.status()
        self.mode = st["mode"]
        if self.mode == "open":
            return st                               # ohne Anmeldung
        body = {"username": self.username, "label": self.label}
        if self.api_key:
            body["api_key"] = self.api_key
        else:
            body.update({"password": self.password, "realm": self.realm})
            if self.code:
                body["code"] = self.code
        res = self._raw("POST", "/auth/login", body, auth=False)
        self.token = res.get("token", "")
        self.session = res.get("session", "")
        if res.get("new_api_key"):
            # Server hat rotiert: neuen Schluessel sofort benutzen und speichern lassen.
            self.api_key = res["new_api_key"]
            if self.on_key_rotated:
                self.on_key_rotated(self.api_key)
            else:
                print("WARNUNG: API-Schlüssel wurde rotiert - neuen Schlüssel speichern "
                      "(on_key_rotated nutzen):\n" + self.api_key, file=sys.stderr)
        elif res.get("api_key_replaced"):
            print("WARNUNG: Dieser API-Schlüssel wurde ersetzt und läuft bald ab - bitte den neuen verwenden.",
                  file=sys.stderr)
        return res

    def logout(self) -> None:
        if self.mode in ("secure", "user") and (self.token or self.session):
            try:
                self._raw("POST", "/auth/logout", {})
            finally:
                self.token = self.session = ""

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc):
        self.logout()

    # ------------------------------------------------------------ Bequeme Helfer
    def clients(self) -> list:
        return self.get("/clients")

    def offline_clients(self) -> list:
        return [c for c in self.clients() if not c.get("online")]

    def exec(self, client_id: str, command: str, shell: str = "auto", elevated: bool = False):
        """Befehl auf einem Client ausfuehren (Recht "Terminal" = c_terminal auf diesem Client).
        shell: "auto" | "cmd" | "powershell" (Windows); elevated=True = als Administrator (Windows)."""
        body = {"command": command, "shell": shell, "elevated": elevated}
        return self.post(f"/clients/{urllib.parse.quote(client_id)}/exec", body)

    def enable_module(self, module_id: str):
        return self.post(f"/modules/{urllib.parse.quote(module_id)}/enable")


# ================================================================ Kommandozeile
def _main(argv: list[str]) -> int:
    server = os.environ.get("RMM_SERVER", "")
    user = os.environ.get("RMM_USER", "")
    key = os.environ.get("RMM_API_KEY", "")
    key_file = os.environ.get("RMM_API_KEY_FILE", "")
    if key_file and os.path.isfile(key_file):
        with open(key_file, encoding="utf-8") as fh:
            key = fh.read().strip()

    def save_key(new_key: str) -> None:
        if key_file:
            tmp = key_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(new_key + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, key_file)
            print(f"Hinweis: API-Schlüssel wurde rotiert und in {key_file} gespeichert.", file=sys.stderr)
        else:
            print("WARNUNG: API-Schlüssel wurde rotiert - neuer Schlüssel (RMM_API_KEY aktualisieren!):\n"
                  + new_key, file=sys.stderr)
    pw = os.environ.get("RMM_PASSWORD", "")
    insecure = os.environ.get("RMM_INSECURE", "") == "1"
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if not server:
        print("Bitte RMM_SERVER (und RMM_USER + RMM_API_KEY bzw. RMM_PASSWORD) setzen.", file=sys.stderr)
        return 2
    rmm = RmmApi(server, user, api_key=key, password=pw, verify_tls=not insecure, on_key_rotated=save_key)
    cmd, args = argv[0], argv[1:]
    out = None
    try:
        if cmd == "status":
            out = rmm.status()
        else:
            with rmm:
                if cmd == "clients":
                    for c in rmm.clients():
                        print(f"{'●' if c.get('online') else '○'} {c.get('hostname', '?'):<28} {c.get('platform', ''):<10} {c['id']}")
                elif cmd == "offline":
                    for c in rmm.offline_clients():
                        print(c.get("hostname"), c["id"])
                elif cmd == "exec" and len(args) >= 2:
                    out = rmm.exec(args[0], " ".join(args[1:]))
                elif cmd == "routes":
                    r = rmm.get("/routes")
                    for s in r["shortcuts"]:
                        print(f"{','.join(s['methods']):<12} {s['path']:<52} {s['summary']}")
                    print(f"\n+ {len(r['bridge'])} Dashboard-Routen über /api/v1/call/...")
                elif cmd in ("get", "delete") and args:
                    params = dict(a.split("=", 1) for a in args[1:] if "=" in a)
                    out = getattr(rmm, cmd)(args[0], **params)
                elif cmd in ("post", "put", "patch") and args:
                    body = json.loads(args[1]) if len(args) > 1 else {}
                    out = getattr(rmm, cmd)(args[0], body)
                elif cmd == "call" and len(args) >= 2:
                    params = dict(a.split("=", 1) for a in args[2:] if "=" in a)
                    out = rmm.call(args[0], args[1], **params)
                elif cmd == "me":
                    out = rmm.get("/auth/me")
                else:
                    print(f"Unbekannter Befehl: {cmd} (siehe --help)", file=sys.stderr)
                    return 2
    except RmmApiError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    if out is not None:
        print(json.dumps(out, indent=2, ensure_ascii=False) if not isinstance(out, bytes) else out.decode(errors="replace"))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

# store.py (Modul "cloudflare") - Einstellungen, Tunnel und Routen
# ----------------------------------------------------------------
# data/modules/cloudflare/config.json:
#   auth      {type: "token"|"key", email, token_enc | key_enc}
#   account   {id, name}, team_domain ("<team>.cloudflareaccess.com"), public_base ("https://rmm.firma.de")
#   tunnels   {server: {id, name, token_enc}, nodes: {<client_id>: {id, name, token_enc, deployed}}}
#   routes    [Route, ...]  (siehe services.py / sync.py)
#   oidc      {key_enc (RSA-PEM), kid}
#   otp_idp   ID des Cloudflare-"One-time PIN"-Logins
# Geheimnisse (API-Token/-Key, Tunnel-Token, OIDC-Schluessel, Client-Secrets) liegen
# verschluesselt (Fernet, Schluessel aus JWT_SECRET) - wie beim Modul Proxmox.

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import threading
from pathlib import Path

from app.config import JWT_SECRET
from app.paths import MODULES_DATA_DIR

DIR = MODULES_DATA_DIR / "cloudflare"
FILE = DIR / "config.json"
BIN_DIR = DIR / "bin"
_lock = threading.RLock()


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("rmm-cloudflare:" + JWT_SECRET).encode()).digest()))


def enc(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode() if text else ""


def dec(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""


DEFAULT = {"auth": {}, "account": {}, "team_domain": "", "public_base": "", "zones": [],
           "tunnels": {"server": {}, "nodes": {}}, "routes": [], "oidc": {}, "otp_idp": "",
           "last_sync": None, "last_error": ""}


def load() -> dict:
    with _lock:
        try:
            data = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        out = copy.deepcopy(DEFAULT)
        out.update(data)
        out["tunnels"] = {"server": {}, "nodes": {}, **(data.get("tunnels") or {})}
        return out


def save(data: dict) -> None:
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, FILE)


def update(fn):
    """data = fn(data) unter Sperre speichern."""
    with _lock:
        data = load()
        res = fn(data)
        save(data)
        return res


def route(data: dict, rid: str) -> dict | None:
    return next((r for r in data["routes"] if r["id"] == rid), None)


def public_view(data: dict) -> dict:
    """Fuer die Oberflaeche: ohne Geheimnisse."""
    a = data.get("auth") or {}
    return {"auth": {"type": a.get("type", ""), "email": a.get("email", ""),
                     "configured": bool(a.get("token_enc") or a.get("key_enc"))},
            "account": data.get("account") or {}, "team_domain": data.get("team_domain", ""),
            "public_base": data.get("public_base", ""), "zones": data.get("zones") or [],
            "tunnels": {"server": {k: v for k, v in (data["tunnels"].get("server") or {}).items() if k != "token_enc"},
                        "nodes": {cid: {k: v for k, v in t.items() if k != "token_enc"}
                                  for cid, t in (data["tunnels"].get("nodes") or {}).items()}},
            "last_sync": data.get("last_sync"), "last_error": data.get("last_error", "")}

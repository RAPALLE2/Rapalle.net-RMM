# store.py (Modul "addir") - mehrere Active-Directory-Verbindungen verwalten
# -------------------------------------------------------------------------
# data/modules/addir/connections.json:
#   {"conns": {id: {id, name, server, port, use_ssl, start_tls, base_dn,
#                   bind_user, bind_pw_enc, pw_requires_tls, verify_cert, created, created_by}}}
# Das Bind-Passwort liegt verschluesselt (Fernet, Schluessel aus JWT_SECRET - wie Proxmox/Cloudflare).
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading

from app.config import JWT_SECRET
from app.paths import MODULES_DATA_DIR

DIR = MODULES_DATA_DIR / "addir"
FILE = DIR / "connections.json"
_lock = threading.RLock()


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("rmm-addir:" + JWT_SECRET).encode()).digest()))


def enc(text: str) -> str:
    return _fernet().encrypt((text or "").encode()).decode() if text else ""


def dec(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""


def _load() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("conns", {})
    return d


def _save(d: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        import os
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(FILE)


def update(fn):
    with _lock:
        d = _load()
        res = fn(d)
        _save(d)
        return res


def new_id() -> str:
    return secrets.token_hex(6)


def list_conns() -> list[dict]:
    return list(_load()["conns"].values())


def get(cid: str) -> dict | None:
    return _load()["conns"].get(cid)


def public(c: dict) -> dict:
    """Ohne Passwort - fuer die Oberflaeche / fuer SSO-Import."""
    return {k: v for k, v in c.items() if k != "bind_pw_enc"} | {"has_password": bool(c.get("bind_pw_enc"))}

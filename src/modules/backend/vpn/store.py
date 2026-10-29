# store.py (Modul "vpn") - Tunnel + Teilnehmer, Schluessel verschluesselt
# ----------------------------------------------------------------------
# data/modules/vpn/data.json:
#   {"tunnels": {tid: {
#       id, name, host ("backend" | Node-Client-ID), endpoint ("host:port"), port,
#       mode ("virtual" | "real"), ip_mode ("own" | "nat"; nur bei real),
#       subnet (virtuelles Netz bzw. internes Netz bei NAT, z.B. 10.77.3.0/24),
#       lan {iface, cidr, pool_start, pool_end}   (nur real),
#       dns, keepalive, host_ip, host_pub, host_priv_enc,
#       created, created_by, expires (0 = unbegrenzt), state ("active"|"expired"|"error"), error,
#       peers: {pid: {id, kind ("user"|"agent"|"backend"), ref, name, ip, pub, priv_enc,
#                     added, added_by, expires (0 = wie Tunnel), auto (Client-App richtet ein)}}
#   }}}
# Private Schluessel liegen NUR verschluesselt (Fernet aus JWT_SECRET) vor.
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading

from app.config import JWT_SECRET
from app.paths import MODULES_DATA_DIR

DIR = MODULES_DATA_DIR / "vpn"
FILE = DIR / "data.json"
_lock = threading.RLock()


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("rmm-vpn:" + JWT_SECRET).encode()).digest()))


def enc(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode() if text else ""


def dec(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode() if token else ""


def new_id() -> str:
    return secrets.token_hex(4)


def load() -> dict:
    with _lock:
        try:
            d = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d.setdefault("tunnels", {})
        return d


def save(d: dict) -> None:
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(FILE)


def update(fn):
    """fn(data) -> Ergebnis; speichert danach. Thread-sicher."""
    with _lock:
        d = load()
        res = fn(d)
        save(d)
        return res


def get(tid: str) -> dict | None:
    return load()["tunnels"].get(tid)

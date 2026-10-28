# store.py (Modul "wol") - Wake-on-LAN-Angaben je Client
# ------------------------------------------------------
# data/modules/wol/data.json: { "clients": { <client_id>: {
#   macs: [..],          manuell gepflegte MACs (zusaetzlich zu den automatisch erkannten)
#   broadcast: "",       optionale Broadcast-Adresse (sonst 255.255.255.255 bzw. aus der IP)
#   port: 9,             Ziel-Port (9 Standard)
#   via: "auto"|"backend"|"<node_client_id>",   bevorzugter Absender
#   enabled: true        WOL fuer diesen PC erlauben (Geraet muss WOL im BIOS/NIC koennen)
# }}}
# Automatisch erkannte MACs/IPs kommen aus den Live-Messwerten des Agenten.
from __future__ import annotations

import json
import re
import threading

from app.paths import MODULES_DATA_DIR

DIR = MODULES_DATA_DIR / "wol"
FILE = DIR / "data.json"
_lock = threading.RLock()
_MAC_RE = re.compile(r"^[0-9a-f]{2}([:-]?[0-9a-f]{2}){5}$", re.I)


def norm_mac(mac: str) -> str:
    h = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    if len(h) != 12:
        return ""
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).lower()


def valid_mac(mac: str) -> bool:
    return bool(norm_mac(mac)) and norm_mac(mac) != "00:00:00:00:00:00"


def _load() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault("clients", {})
    return d


def _save(d: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def update(fn):
    with _lock:
        d = _load()
        res = fn(d)
        _save(d)
        return res


def get(cid: str) -> dict:
    c = _load()["clients"].get(cid) or {}
    return {"macs": [m for m in (c.get("macs") or []) if valid_mac(m)],
            "broadcast": c.get("broadcast", ""), "port": int(c.get("port") or 9),
            "via": c.get("via", "auto"), "enabled": bool(c.get("enabled", True))}


def set_config(cid: str, **fields) -> dict:
    def upd(d):
        c = d["clients"].setdefault(cid, {})
        if "macs" in fields:
            c["macs"] = sorted({norm_mac(m) for m in (fields["macs"] or []) if valid_mac(m)})
        for k in ("broadcast", "port", "via", "enabled"):
            if k in fields and fields[k] is not None:
                c[k] = fields[k]
        return c
    update(upd)
    return get(cid)


def add_mac(cid: str, mac: str) -> bool:
    if not valid_mac(mac):
        return False
    def upd(d):
        c = d["clients"].setdefault(cid, {})
        macs = set(c.get("macs") or [])
        macs.add(norm_mac(mac))
        c["macs"] = sorted(macs)
    update(upd)
    return True

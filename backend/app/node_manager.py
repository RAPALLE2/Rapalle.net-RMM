"""
node_manager.py
---------------
Die Node-Stufe: welcher Client ist eine Node, welche Zusatzmodule bekommt
er, und wie erfaehrt das Backend, ob eine Node direkt erreichbar ist.

Warum es diese Stufe gibt
-------------------------
Ein gewoehnlicher Client soll so klein und harmlos bleiben, wie er ist:
Metriken melden, Befehle ausfuehren, fertig. Ein Brueckenkopf ins Netz ist
etwas grundlegend anderes - er oeffnet einen UDP-Port, holt fremde
Webseiten und fasst im Extremfall den Netzwerkadapter an. Diese Faehigkeiten
gehoeren nicht ungefragt auf jedes Geraet. Deshalb sind sie an eine
ausdrueckliche Aufwertung gebunden, und nur aufgewertete Geraete bekommen
die zugehoerigen Dateien ueberhaupt zu sehen.

Modulauslieferung
-----------------
Die Node-Module liegen unter backend/agent_modules/. Das Backend nennt der
Node beim Aktivieren Namen und Pruefsumme; die Node laedt fehlende oder
veraltete Dateien nach. Damit gilt fuer Module dasselbe wie fuer den
Agenten selbst: Die Pruefsumme entscheidet, nicht eine Versionsnummer, die
jemand zu erhoehen vergessen koennte.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from app import db

MODULE_DIR = Path(__file__).resolve().parents[1] / "agent_modules"

# Welche Module eine Node bekommt. Reihenfolge egal - die Node laedt alle.
# VPN AUSGEBAUT (2026-09-20): "node_wg.py" ist aus dieser Liste entfernt.
# Eine Node bekommt damit kein WireGuard-Modul mehr ausgeliefert. Reverse
# Proxy und NAT-Probe bleiben - beide haben mit dem Tunnel nichts zu tun.
NODE_MODULES = [
    "node_relay.py",   # Erreichbarkeit hinter NAT: Probe + Relay
    "node_proxy.py",   # Reverse Proxy fuer Webseiten im Netz der Node
]

_hash_cache: dict[str, tuple[float, str]] = {}


def module_path(name: str) -> Path | None:
    """Pfad eines Moduls - nur Dateien aus NODE_MODULES, nie beliebige."""
    if name not in NODE_MODULES:
        return None
    path = MODULE_DIR / name
    return path if path.is_file() else None


def module_hash(name: str) -> str:
    """SHA256-Praefix einer Moduldatei, zwischengespeichert nach mtime."""
    path = module_path(name)
    if not path:
        return ""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    cached = _hash_cache.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    _hash_cache[name] = (mtime, digest)
    return digest


def module_manifest() -> list[dict]:
    """Was die Node haben soll: Name + Pruefsumme."""
    return [{"name": n, "hash": module_hash(n)} for n in NODE_MODULES
            if module_path(n)]


# ----------------------------------------------------------------------
# Aufwertung / Rueckstufung
# ----------------------------------------------------------------------

async def promote(client_id: str, username: str) -> dict:
    """
    Wertet einen Client zur Node auf und schickt ihm die Module.

    Der Client muss online sein - eine Aufwertung, die erst irgendwann
    spaeter wirksam wird, waere im Dashboard nicht von einer stillen
    Fehlfunktion zu unterscheiden.
    """
    client = db.get_client(client_id)
    if not client:
        raise ValueError("Client unbekannt")
    db.set_client_node(client_id, True)
    db.add_audit_entry(username, "node.promoted", target=client_id,
                       details=client.get("hostname"))
    await push_config(client_id)
    return {"ok": True, "is_node": True}


async def demote(client_id: str, username: str) -> dict:
    """Stuft eine Node zurueck und laesst sie ihre Zusatzmodule abraeumen."""
    client = db.get_client(client_id)
    if not client:
        raise ValueError("Client unbekannt")
    db.set_client_node(client_id, False)
    db.add_audit_entry(username, "node.demoted", target=client_id,
                       details=client.get("hostname"))
    from app.sockets import send_to_agent
    await send_to_agent(client_id, "node-disable", {})
    return {"ok": True, "is_node": False}


async def push_config(client_id: str) -> bool:
    """
    Schickt einer Node ihre Betriebsdaten: Modulliste, UDP-Port, Schluessel
    und die Adresse, an die sie ihr Probe-Paket senden soll.
    """
    if not db.is_client_node(client_id):
        return False
    from app.sockets import send_to_agent

    # Ohne VPN braucht die Node weder Schluessel noch UDP-Port noch eine
    # Probe-Adresse. Uebrig bleibt die Modulliste - mehr war der Rest nie.
    return await send_to_agent(client_id, "node-enable", {
        "modules": module_manifest(),
    })


# ----------------------------------------------------------------------
# Adressen der Node
# ----------------------------------------------------------------------
# VPN AUSGEBAUT (2026-09-20): node_keys() erzeugte je Node ein
# WireGuard-Schluesselpaar. Ohne Tunnel gibt es nichts mehr zu verschluesseln,
# die Funktion ist ersatzlos entfallen (samt Abhaengigkeit auf app.wg_keys).

def node_udp_port() -> int:
    """UDP-Port, auf dem Nodes ihren Endpunkt aufmachen."""
    try:
        return int(db.get_setting("node_vpn_port", "51821") or 51821)
    except ValueError:
        return 51821


def probe_token(client_id: str) -> str:
    """Kennung, an der das Backend ein Probe-Paket seiner Node erkennt."""
    secret = db.get_setting("vpn_server_public", "") or "rmm"
    return hashlib.sha256(f"{secret}:{client_id}".encode()).hexdigest()[:32]


def client_for_probe_token(token: str) -> str | None:
    """Umkehrung: welches Geraet gehoert zu diesem Probe-Token?"""
    for node in db.list_nodes():
        if probe_token(node["id"]) == token:
            return node["id"]
    return None


def _caps(client_id: str) -> dict:
    client = db.get_client(client_id) or {}
    try:
        return json.loads(client.get("node_caps") or "{}")
    except (ValueError, TypeError):
        return {}


def update_caps(client_id: str, reported: dict) -> None:
    """Uebernimmt die Selbstauskunft einer Node (Module, L2-Zustand)."""
    caps = _caps(client_id)
    # Schluessel bleiben unangetastet - die Node darf sie nicht ueberschreiben.
    for key in ("modules", "l2", "l2_reason", "vpn_running", "proxy",
                "agent_version", "local_ips"):
        if key in reported:
            caps[key] = reported[key]
    caps["updated_at"] = int(time.time() * 1000)
    db.set_node_caps(client_id, json.dumps(caps))


def node_info(client_id: str) -> dict:
    """Aufbereitete Node-Angaben fuer das Dashboard (ohne Schluessel)."""
    client = db.get_client(client_id) or {}
    caps = _caps(client_id)
    caps.pop("private_key", None)
    endpoint = client.get("node_endpoint") or ""
    return {
        "is_node": bool(client.get("is_node")),
        # Die Oberflaeche braucht das, um vor der L2-Einrichtung den
        # richtigen Warntext zu zeigen: Unter Windows wird ein Treiber
        # installiert, unter Linux nicht.
        "platform": client.get("platform") or "",
        # Die vom Agenten gemeldeten Netze - die Oberflaeche zeigt damit
        # bei Site-to-Site an, was tatsaechlich geroutet wird.
        "subnets": db.get_client_subnets(client_id),
        "endpoint": endpoint,
        "endpoint_checked": client.get("node_endpoint_checked") or 0,
        "direct_possible": bool(endpoint),
        "caps": caps,
        "udp_port": node_udp_port(),
    }

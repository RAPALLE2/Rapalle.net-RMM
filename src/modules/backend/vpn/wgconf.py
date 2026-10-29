# wgconf.py (Modul "vpn") - Schluessel, Adressen und WireGuard-Konfigurationen
# --------------------------------------------------------------------------
# Hier entsteht ALLES, was Host und Teilnehmer brauchen - Nodes, Agents und die
# Client-App wenden nur noch an. Netzarten:
#   virtual       eigenes Netz (subnet), Host = .1, Teilnehmer erreichen sich gegenseitig
#   real + own    Teilnehmer bekommen je eine IP aus dem reellen Netz des Hosts (Pool);
#                 der Host beantwortet ARP fuer sie (Proxy-ARP) -> im LAN sichtbar
#   real + nat    Teilnehmer im internen Netz (subnet); ins reelle Netz gehen alle mit
#                 der IP des Hosts (Masquerade)
from __future__ import annotations

import base64
import ipaddress

from . import store


def keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    k = X25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(priv).decode(), base64.b64encode(pub).decode()


def iface(t: dict) -> str:
    """Schnittstelle des HOSTS."""
    return f"rmm{t['id']}"[:15]


def peer_iface(t: dict) -> str:
    """Schnittstelle eines TEILNEHMERS - anderer Name als beim Host, damit Host und
    Teilnehmer auf derselben Maschine (z.B. Agent auf dem Node-Host) nicht kollidieren."""
    return f"rmv{t['id']}"[:15]


def pick_subnet(pool: str, used: set[str]) -> str:
    """Freies /24 aus dem Pool (Standard 10.77.0.0/16)."""
    net = ipaddress.ip_network(pool, strict=False)
    for sub in net.subnets(new_prefix=24 if net.prefixlen <= 24 else net.prefixlen):
        if str(sub) not in used:
            return str(sub)
    raise ValueError("Kein freies Teilnetz mehr im VPN-Pool")


def _pool_ips(t: dict):
    if t["mode"] == "real" and t.get("ip_mode") == "own":
        lan = t["lan"]
        a, b = ipaddress.ip_address(lan["pool_start"]), ipaddress.ip_address(lan["pool_end"])
        for i in range(int(a), int(b) + 1):
            yield str(ipaddress.ip_address(i))
    else:
        for h in ipaddress.ip_network(t["subnet"]).hosts():
            yield str(h)


def host_ip(t: dict) -> str:
    return next(iter(_pool_ips(t)))


def next_ip(t: dict) -> str:
    used = {t.get("host_ip")} | {p["ip"] for p in t["peers"].values()}
    for ip in _pool_ips(t):
        if ip not in used:
            return ip
    raise ValueError("Keine freie Adresse mehr in diesem Tunnel")


def peer_allowed(t: dict) -> str:
    """Was ein Teilnehmer durch den Tunnel schickt."""
    nets = []
    if t["mode"] == "real":
        nets.append(t["lan"]["cidr"])
        if t.get("ip_mode") != "own":
            nets.append(t["subnet"])
    else:
        nets.append(t["subnet"])
    for extra in t.get("routes") or []:
        if extra not in nets:
            nets.append(extra)
    return ", ".join(nets)


def _prefix(t: dict) -> str:
    if t["mode"] == "real" and t.get("ip_mode") == "own":
        return "32"
    return str(ipaddress.ip_network(t["subnet"]).prefixlen)


def host_conf(t: dict) -> str:
    """[Interface] des Hosts + alle aktiven Teilnehmer."""
    i = "%i"
    up, down = ["sysctl -w net.ipv4.ip_forward=1"], []

    def rule(r):
        up.append(f"iptables -A {r}")
        down.append(f"iptables -D {r}")

    if t["mode"] == "virtual":
        rule(f"FORWARD -i {i} -o {i} -j ACCEPT")
    else:
        lif = t["lan"]["iface"]
        rule(f"FORWARD -i {i} -o {lif} -j ACCEPT")
        rule(f"FORWARD -i {lif} -o {i} -m state --state RELATED,ESTABLISHED -j ACCEPT")
        if t.get("ip_mode") == "own":
            rule(f"FORWARD -i {lif} -o {i} -j ACCEPT")
            up.append(f"sysctl -w net.ipv4.conf.{lif}.proxy_arp=1")
        else:
            up.append(f"iptables -t nat -A POSTROUTING -s {t['subnet']} -o {lif} -j MASQUERADE")
            down.append(f"iptables -t nat -D POSTROUTING -s {t['subnet']} -o {lif} -j MASQUERADE")
    lines = ["# RMM-VPN Host – vom Backend erzeugt, nicht von Hand ändern",
             "[Interface]", f"Address = {t['host_ip']}/{_prefix(t)}", f"ListenPort = {t['port']}",
             f"PrivateKey = {store.dec(t['host_priv_enc'])}",
             f"PostUp = {'; '.join(up)}"]
    if down:
        lines.append(f"PostDown = {'; '.join(down)}")
    for p in t["peers"].values():
        lines += ["", f"# {p['kind']}: {p['name']}", "[Peer]", f"PublicKey = {p['pub']}", f"AllowedIPs = {p['ip']}/32"]
    return "\n".join(lines) + "\n"


def peer_conf(t: dict, p: dict) -> str:
    """Komplette Konfiguration eines Teilnehmers (zum Herunterladen oder Anwenden)."""
    lines = [f"# RMM-VPN „{t['name']}“ – {p['name']}", "[Interface]",
             f"PrivateKey = {store.dec(p['priv_enc'])}", f"Address = {p['ip']}/32"]
    if t.get("dns"):
        lines.append(f"DNS = {t['dns']}")
    lines += ["", "[Peer]", f"PublicKey = {t['host_pub']}", f"Endpoint = {t['endpoint']}",
              f"AllowedIPs = {peer_allowed(t)}", f"PersistentKeepalive = {int(t.get('keepalive') or 25)}"]
    return "\n".join(lines) + "\n"

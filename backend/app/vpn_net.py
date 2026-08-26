"""
vpn_net.py
==========
Das virtuelle Netz: Das Backend ist der Router.

Die Idee
--------
Bisher war jeder Tunnel eine Einbahnstrasse zu genau einem Geraet. Wer drei
Rechner erreichen wollte, brauchte drei Tunnel-Dateien und musste zwischen
ihnen umschalten.

Hier gibt es stattdessen EIN Netz, in das sich alles einwaehlt:

  * Jeder CLIENT bekommt eine feste virtuelle Adresse (10.77.1.x).
    Er selbst merkt davon nichts - er braucht kein WireGuard. Das Backend
    kennt seine Adresse und leitet alles, was dorthin geht, ueber die
    bestehende Agenten-Verbindung weiter.
  * Jeder BENUTZER bekommt beim Ausstellen eines Tunnels eine Adresse aus
    dem Benutzerbereich (10.77.0.x) und damit Zugang zum ganzen Netz.
  * Das BACKEND ist der Router in der Mitte (10.77.0.1) und beantwortet
    ausserdem DNS.

Damit gilt: Ein Tunnel, alle Geraete. `ping buchhaltung-pc.rmm` funktioniert,
ohne dass jemand eine Adresse nachschlagen muss.

Warum die Clients KEIN WireGuard brauchen
-----------------------------------------
Das ist die entscheidende Eigenschaft dieses Aufbaus und der Grund, warum er
ueberhaupt funktioniert: Auf den verwalteten Geraeten darf nichts
installiert werden. Sie haengen ohnehin schon per Socket.IO am Backend. Ihre
virtuelle Adresse ist deshalb reine Buchfuehrung im Backend - ankommende
Pakete werden dort auf die vorhandene Verbindung umgelegt.

Rechte gelten weiterhin
-----------------------
Eine virtuelle Adresse ist KEIN Freifahrtschein. Vor jeder Weiterleitung
wird geprueft, ob der Benutzer, auf den der Tunnel ausgestellt ist, diesen
Client ueberhaupt sehen darf. Ein Netz, in dem jeder alles erreicht, sobald
er einmal drin ist, waere in einem RMM nicht vertretbar.
"""

from __future__ import annotations

import ipaddress
import struct
import time

from app import db

# Aufteilung des Tunnel-Netzes (standardmaessig 10.77.0.0/16):
#
#   10.77.0.1          das Backend selbst - Router, DNS und zugleich die
#                      Ersatzadresse fuer "das Geraet am anderen Ende"
#   10.77.0.2 - .254   Benutzer (ein Tunnel = eine Adresse)
#   10.77.1.0/16-Rest  Clients (feste Adresse je Geraet)
#
# Die Trennung ist Absicht: An der Adresse allein erkennt man, ob ein Paket
# von einem Benutzer oder zu einem Geraet gehoert.
USER_BLOCK_LAST_OCTET_MAX = 254
CLIENT_BLOCK_START = 256          # ab 10.77.1.0

# Namenszone des virtuellen Netzes. `<hostname>.rmm` zeigt auf die virtuelle
# Adresse des Geraets.
DEFAULT_ZONE = "rmm"


def zone() -> str:
    return (db.get_setting("vpn_zone", DEFAULT_ZONE) or DEFAULT_ZONE).strip(".")


def _network() -> ipaddress.IPv4Network:
    from app import vpn
    return ipaddress.ip_network(vpn.vpn_subnet(), strict=False)


def router_address() -> str:
    """Die Adresse des Backends im virtuellen Netz."""
    return str(next(_network().hosts()))


# ----------------------------------------------------------------------
# Mitglieder
# ----------------------------------------------------------------------

def client_address(client_id: str, create: bool = True) -> str:
    """
    Die feste virtuelle Adresse eines Clients.

    Fest heisst: Sie aendert sich nicht, auch nicht wenn das Geraet offline
    war oder das Backend neu gestartet wurde. Nur so kann man sie sich
    merken, in Lesezeichen ablegen oder in einer Anleitung nennen.
    """
    existing = db.get_vpn_member_address("client", client_id)
    if existing or not create:
        return existing or ""

    net = _network()
    used = set(db.list_vpn_member_addresses())
    base = int(net.network_address)
    for offset in range(CLIENT_BLOCK_START, min(net.num_addresses - 1, 65534)):
        candidate = str(ipaddress.ip_address(base + offset))
        if candidate not in used:
            client = db.get_client(client_id) or {}
            db.set_vpn_member("client", client_id, candidate,
                              client.get("hostname") or client_id)
            return candidate
    raise RuntimeError("Keine freie Adresse im virtuellen Netz mehr")


def user_address(tunnel_id: str, username: str) -> str:
    """Adresse fuer einen Benutzer-Tunnel (aus dem unteren Bereich)."""
    existing = db.get_vpn_member_address("user", tunnel_id)
    if existing:
        return existing
    net = _network()
    used = set(db.list_vpn_member_addresses())
    base = int(net.network_address)
    for offset in range(2, USER_BLOCK_LAST_OCTET_MAX + 1):
        candidate = str(ipaddress.ip_address(base + offset))
        if candidate not in used:
            db.set_vpn_member("user", tunnel_id, candidate, username)
            return candidate
    raise RuntimeError("Keine freie Benutzer-Adresse mehr - "
                       "es sind zu viele Tunnel gleichzeitig offen")


def members(include_offline: bool = True) -> list[dict]:
    """Alle Mitglieder mit Adresse, Name und Zustand - fuer die Oberflaeche."""
    out = []
    for row in db.list_vpn_members():
        item = dict(row)
        if row["kind"] == "client":
            client = db.get_client(row["ref"]) or {}
            item["online"] = bool(client.get("online"))
            item["hostname"] = client.get("hostname") or row["label"]
            item["fqdn"] = f"{_dns_label(item['hostname'])}.{zone()}"
            item["ip"] = client.get("ip") or ""
            if not include_offline and not item["online"]:
                continue
        else:
            item["online"] = True
            item["fqdn"] = ""
        out.append(item)
    return sorted(out, key=lambda x: (x["kind"] != "client", x["address"]))


def client_for_address(address: str) -> str | None:
    """Welcher Client verbirgt sich hinter dieser virtuellen Adresse?"""
    return db.get_vpn_member_ref("client", address)


def _dns_label(name: str) -> str:
    """Macht aus einem Hostnamen einen gueltigen DNS-Namen."""
    safe = "".join(ch if (ch.isalnum() or ch == "-") else "-"
                   for ch in (name or "").lower())
    return safe.strip("-")[:63] or "unbenannt"


# ----------------------------------------------------------------------
# DNS
# ----------------------------------------------------------------------
# Ein sehr kleiner DNS-Server: Er beantwortet A-Anfragen fuer die eigene
# Zone und sagt bei allem anderen ehrlich "kenne ich nicht" (NXDOMAIN).
#
# Warum nicht weiterleiten? Ein DNS, der fremde Namen weiterreicht, wird
# schnell zum offenen Resolver und damit zum Werkzeug fuer Angriffe. Der
# WireGuard-Client behaelt seinen gewohnten DNS-Server fuer alles ausserhalb
# des Tunnels; hier geht es nur um die Namen des virtuellen Netzes.

QTYPE_A = 1
QTYPE_AAAA = 28
QCLASS_IN = 1


def handle_dns(query: bytes) -> bytes | None:
    """
    Beantwortet eine DNS-Anfrage. None = keine gueltige Anfrage.

    Bewusst von Hand statt mit einer Bibliothek: Es geht um genau einen
    Anfragetyp, und eine zusaetzliche Abhaengigkeit fuer 60 Zeilen waere
    unverhaeltnismaessig.
    """
    if len(query) < 12:
        return None
    try:
        txn = query[0:2]
        flags = struct.unpack(">H", query[2:4])[0]
        qdcount = struct.unpack(">H", query[4:6])[0]
        if flags & 0x8000 or qdcount < 1:
            return None      # das ist bereits eine Antwort

        # Namen lesen
        labels, pos = [], 12
        while pos < len(query):
            length = query[pos]
            if length == 0:
                pos += 1
                break
            if length & 0xC0:
                return None   # Komprimierung in der Frage - kommt nicht vor
            labels.append(query[pos + 1:pos + 1 + length].decode("ascii", "replace"))
            pos += 1 + length
        if pos + 4 > len(query):
            return None
        qtype, qclass = struct.unpack(">HH", query[pos:pos + 4])
        question = query[12:pos + 4]
        name = ".".join(labels).lower()

        answer_ip = _resolve(name) if qclass == QCLASS_IN else None

        header_flags = 0x8580          # Antwort, autoritativ, Rekursion ok
        if answer_ip and qtype == QTYPE_A:
            body = (b"\xc0\x0c"                       # Zeiger auf den Namen
                    + struct.pack(">HHIH", QTYPE_A, QCLASS_IN, 60, 4)
                    + bytes(int(x) for x in answer_ip.split(".")))
            counts = struct.pack(">HHHH", 1, 1, 0, 0)
        elif answer_ip and qtype == QTYPE_AAAA:
            # Name existiert, aber nicht als IPv6 - das ist NICHT NXDOMAIN.
            # Ein falsches NXDOMAIN hier laesst manche Programme den Namen
            # ganz aufgeben, auch fuer IPv4.
            body = b""
            counts = struct.pack(">HHHH", 1, 0, 0, 0)
        else:
            header_flags = 0x8583      # NXDOMAIN
            body = b""
            counts = struct.pack(">HHHH", 1, 0, 0, 0)

        return txn + struct.pack(">H", header_flags) + counts + question + body
    except Exception:
        return None


def _resolve(name: str) -> str | None:
    """Loest einen Namen im virtuellen Netz auf."""
    suffix = "." + zone()
    if name in (zone(), "router" + suffix, "backend" + suffix, "rmm" + suffix):
        return router_address()
    if not name.endswith(suffix):
        return None
    host = name[:-len(suffix)]
    for member in db.list_vpn_members():
        if member["kind"] != "client":
            continue
        client = db.get_client(member["ref"]) or {}
        label = _dns_label(client.get("hostname") or member["label"])
        if label == host:
            return member["address"]
    return None


def dns_reply_packet(src_ip: str, src_port: int, dst_ip: str, dst_port: int,
                     payload: bytes) -> bytes:
    """Baut das fertige IP/UDP-Paket fuer eine DNS-Antwort."""
    # IP- und UDP-Kopf von Hand. Frueher kam das aus vpn_stack.py; seit die
    # Krypto und der Userspace-Stack draussen sind, sind es genau diese
    # zwanzig Zeilen, die noch gebraucht werden.
    def checksum(data: bytes) -> int:
        if len(data) % 2:
            data += b"\x00"
        total = 0
        for i in range(0, len(data), 2):
            total += (data[i] << 8) | data[i + 1]
        while total >> 16:
            total = (total & 0xFFFF) + (total >> 16)
        return (~total) & 0xFFFF

    def raw(text: str) -> bytes:
        return bytes(int(p) for p in text.split("."))

    s, d = raw(src_ip), raw(dst_ip)
    udp = struct.pack(">HHHH", src_port, dst_port, 8 + len(payload), 0) + payload
    pseudo = s + d + struct.pack(">BBH", 0, 17, len(udp))
    udp = udp[:6] + struct.pack(">H", checksum(pseudo + udp) or 0xFFFF) + udp[8:]

    total = 20 + len(udp)
    head = struct.pack(">BBHHHBBH4s4s", 0x45, 0, total, 0, 0x4000, 64, 17, 0, s, d)
    head = head[:10] + struct.pack(">H", checksum(head)) + head[12:]
    return head + udp

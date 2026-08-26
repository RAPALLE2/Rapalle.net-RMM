"""
wg_relay.py
===========
Bringt Benutzer und Node zusammen, obwohl beide hinter NAT sitzen.

Das Problem
-----------
Opas PC hat keinen offenen Port. Damit dein Rechner ihn direkt erreicht,
gibt es genau zwei Wege - und beide brauchen das Backend, wenn auch auf
sehr unterschiedliche Weise:

  1. DIREKT (Hole-Punching). Beide Seiten senden gleichzeitig nach aussen.
     Ihre NAT-Geraete legen dabei je eine Zuordnung an, und wenn beide
     die vom anderen beobachtete Adresse kennen, treffen sich die Pakete
     in der Mitte. Das Backend tauscht nur diese Adressen aus - es sieht
     KEINE Nutzdaten. Das ist der schnelle Weg.

  2. RELAY. Klappt Weg 1 nicht - bei symmetrischem NAT ist er
     grundsaetzlich aussichtslos, typisch fuer Mobilfunk und CGNAT -,
     laeuft der Verkehr durch das Backend. Langsamer, aber es funktioniert
     immer. Der Inhalt bleibt dabei verschluesselt: Das Backend reicht
     WireGuard-Pakete weiter, ohne sie oeffnen zu koennen.

Es wird IMMER zuerst der direkte Weg versucht. Die Oberflaeche zeigt
ehrlich an, welcher Weg gerade traegt.

Zwei Relay-Arten
----------------
  UDP-RELAY      Ein eigener UDP-Port am Backend. Schnell und schlank -
                 ein Paket rein, ein Paket raus, kein Umkodieren.
                 Braucht einen erreichbaren UDP-Port.
  AGENT-RELAY    Ueber die bestehende Socket.IO-Verbindung der Node.
                 Braucht keinen zusaetzlichen Port, kostet aber Tempo:
                 Die Pakete werden Base64-kodiert durch eine
                 TCP-Verbindung geschoben.

Welcher Weg genommen wird, entscheidet sich von selbst: Ist der UDP-Port
gebunden, wird er benutzt; sonst die Agent-Verbindung.
"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import time

from app.errors import Codes, report

# Kennung am Anfang jedes Relay-Pakets. Sie unterscheidet unsere Pakete von
# WireGuard-Paketen (die mit 1-4 beginnen) und von Datenmuell.
MAGIC = b"RMMR1"
# Nachrichtenarten
MSG_BIND = 1        # "ich bin Sitzung X" - legt die Zuordnung an
MSG_DATA = 2        # Nutzlast (ein WireGuard-Paket)
MSG_PING = 3        # haelt die NAT-Zuordnung offen

# Wie lange eine Zuordnung ohne Verkehr bestehen bleibt.
SESSION_IDLE = 180.0
# So oft haelt jede Seite ihre NAT-Zuordnung offen.
KEEPALIVE = 20.0


def relay_port() -> int:
    from app import db
    try:
        return int(db.get_setting("vpn_relay_port", "")
                   or os.getenv("VPN_RELAY_PORT", "51821") or 51821)
    except ValueError:
        return 51821


class Session:
    """
    Eine Relay-Sitzung: zwei Seiten, die einander Pakete schicken.

    'a' ist ueblicherweise der Benutzer, 'b' die Node. Wer zuerst kommt,
    ist egal - die Sitzung merkt sich einfach beide Adressen.
    """

    def __init__(self, token: str, tunnel_id: str, client_id: str):
        self.token = token
        self.tunnel_id = tunnel_id
        self.client_id = client_id
        self.side_a: tuple | None = None      # Adresse des Benutzers
        self.side_b: tuple | None = None      # Adresse der Node
        self.b_via_agent = False              # Node haengt am Agent-Relay
        self.created = time.time()
        self.last = time.time()
        self.bytes_a = 0
        self.bytes_b = 0

    def touch(self) -> None:
        self.last = time.time()

    def expired(self) -> bool:
        return (time.time() - self.last) > SESSION_IDLE

    def other(self, addr: tuple):
        """Die jeweils andere Seite - dorthin geht das Paket."""
        if self.side_a == addr:
            return self.side_b
        if self.side_b == addr:
            return self.side_a
        return None

    def stats(self) -> dict:
        return {"token": self.token[:12], "tunnel": self.tunnel_id,
                "client": self.client_id,
                "benutzer": f"{self.side_a[0]}:{self.side_a[1]}" if self.side_a else "",
                "node": ("Agent-Verbindung" if self.b_via_agent
                         else (f"{self.side_b[0]}:{self.side_b[1]}"
                               if self.side_b else "")),
                "bytes": self.bytes_a + self.bytes_b,
                "alter_s": round(time.time() - self.created)}


class RelayServer(asyncio.DatagramProtocol):
    """Der UDP-Relay. Reicht Pakete zwischen zwei Adressen weiter."""

    def __init__(self):
        self.transport = None
        self.sessions: dict[str, Session] = {}      # Token -> Sitzung
        self.by_addr: dict[tuple, Session] = {}     # Adresse -> Sitzung
        self.packets = 0
        self.relayed = 0
        self.unknown = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            self.packets += 1
            if not data.startswith(MAGIC):
                # Kein Relay-Paket. Kommt von Scannern - stillschweigend weg.
                self.unknown += 1
                return
            kind = data[len(MAGIC)]
            body = data[len(MAGIC) + 1:]

            if kind == MSG_BIND:
                self._bind(body, addr)
            elif kind == MSG_PING:
                session = self.by_addr.get(addr)
                if session:
                    session.touch()
                self.transport.sendto(MAGIC + bytes([MSG_PING]), addr)
            elif kind == MSG_DATA:
                self._forward(body, addr)
        except Exception as e:
            report(Codes.VPN_PACKET, e, "Relay-Paket", von=str(addr))

    def _bind(self, body: bytes, addr) -> None:
        """Eine Seite meldet sich mit ihrem Token an."""
        token = body[:64].decode("ascii", "replace").strip("\x00")
        session = self.sessions.get(token)
        if not session:
            self.unknown += 1
            return
        # Wer sich als erster meldet, ist Seite A - ausser die Node hat sich
        # bereits ueber die Agent-Verbindung als Seite B eingetragen.
        if session.side_a is None or session.side_a == addr:
            session.side_a = addr
        elif session.side_b is None or session.side_b == addr:
            session.side_b = addr
        else:
            # Dritte Seite - abweisen. Ein Relay, bei dem sich beliebig
            # viele einklinken koennen, waere ein offenes Scheunentor.
            self.unknown += 1
            return
        self.by_addr[addr] = session
        session.touch()
        self.transport.sendto(MAGIC + bytes([MSG_BIND]) + b"OK", addr)

    def _forward(self, payload: bytes, addr) -> None:
        session = self.by_addr.get(addr)
        if not session:
            self.unknown += 1
            return
        session.touch()
        target = session.other(addr)

        if target is None and session.b_via_agent and session.side_a == addr:
            # Die Node haengt nicht am UDP-Relay, sondern an ihrer
            # Agent-Verbindung. Dann geht es dort weiter.
            session.bytes_a += len(payload)
            _to_agent(session, payload)
            self.relayed += 1
            return
        if target is None:
            return          # die Gegenseite ist noch nicht da
        if session.side_a == addr:
            session.bytes_a += len(payload)
        else:
            session.bytes_b += len(payload)
        self.transport.sendto(MAGIC + bytes([MSG_DATA]) + payload, target)
        self.relayed += 1

    # -- Von der Agent-Verbindung ---------------------------------------

    def from_agent(self, token: str, payload: bytes) -> None:
        """Ein Paket kam ueber die Socket.IO-Verbindung der Node herein."""
        session = self.sessions.get(token)
        if not session or not session.side_a or not self.transport:
            return
        session.touch()
        session.bytes_b += len(payload)
        self.transport.sendto(MAGIC + bytes([MSG_DATA]) + payload,
                              session.side_a)
        self.relayed += 1

    # -- Verwaltung ------------------------------------------------------

    def open_session(self, token: str, tunnel_id: str, client_id: str,
                     via_agent: bool) -> Session:
        session = Session(token, tunnel_id, client_id)
        session.b_via_agent = via_agent
        self.sessions[token] = session
        return session

    def close_session(self, token: str) -> None:
        session = self.sessions.pop(token, None)
        if not session:
            return
        for addr in (session.side_a, session.side_b):
            if addr:
                self.by_addr.pop(addr, None)

    def cleanup(self) -> None:
        for token, session in list(self.sessions.items()):
            if session.expired():
                self.close_session(token)

    def stats(self) -> dict:
        return {"port": relay_port(), "sitzungen": len(self.sessions),
                "pakete": self.packets, "weitergereicht": self.relayed,
                "fremd": self.unknown,
                "liste": [s.stats() for s in self.sessions.values()]}


def _to_agent(session: Session, payload: bytes) -> None:
    """Ein Paket ueber die Socket.IO-Verbindung an die Node schicken."""
    try:
        from app.sockets import send_to_agent
        asyncio.ensure_future(send_to_agent(session.client_id, "wg-relay", {
            "token": session.token,
            "data": base64.b64encode(payload).decode(),
        }))
    except Exception as e:
        report(Codes.SOCK_EMIT, e, "Relay-Paket an die Node",
               client=session.client_id)


# ----------------------------------------------------------------------
# Laufzeit
# ----------------------------------------------------------------------

class _Runtime:
    def __init__(self):
        self.server: RelayServer | None = None
        self.transport = None
        self.started = False
        self.udp_available = False


rt = _Runtime()


async def start() -> bool:
    """Startet den UDP-Relay. Scheitert das, bleibt die Agent-Verbindung."""
    if rt.started:
        return rt.udp_available
    server = RelayServer()
    port = relay_port()
    try:
        transport, _ = await asyncio.get_event_loop().create_datagram_endpoint(
            lambda: server, local_addr=("0.0.0.0", port))
        rt.transport = transport
        rt.udp_available = True
        print(f"[relay] UDP-Relay lauscht auf {port}")
    except OSError as e:
        rt.udp_available = False
        print(f"[relay] UDP-Port {port} nicht verfügbar ({e}) – es wird "
              f"ausschliesslich über die Agent-Verbindung weitergereicht. "
              f"Das ist langsamer, funktioniert aber ohne offenen Port.")
    rt.server = server
    rt.started = True
    asyncio.ensure_future(_cleanup_loop())
    return rt.udp_available


async def _cleanup_loop() -> None:
    while rt.started:
        await asyncio.sleep(30)
        try:
            if rt.server:
                rt.server.cleanup()
        except Exception as e:
            report(Codes.TASK_LOOP, e, "Relay-Aufräumen")


def new_token() -> str:
    import secrets
    return secrets.token_hex(24)


def open_session(tunnel_id: str, client_id: str) -> str:
    """Legt eine Sitzung an und gibt das Token zurück."""
    if not rt.server:
        raise RuntimeError("Relay läuft nicht")
    token = new_token()
    rt.server.open_session(token, tunnel_id, client_id,
                           via_agent=not rt.udp_available)
    return token


def close_session(token: str) -> None:
    if rt.server:
        rt.server.close_session(token)


def on_agent_packet(payload: dict) -> None:
    """Ein Relay-Paket kam von einer Node über Socket.IO."""
    if not rt.server:
        return
    try:
        raw = base64.b64decode(payload.get("data") or "")
    except Exception:
        return
    rt.server.from_agent(payload.get("token") or "", raw)


def stats() -> dict:
    base = {"udp": rt.udp_available, "port": relay_port(),
            "läuft": rt.started}
    if rt.server:
        base.update(rt.server.stats())
    return base

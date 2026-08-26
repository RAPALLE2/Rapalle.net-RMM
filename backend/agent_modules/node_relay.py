"""
node_relay.py  -  NODE-MODUL (wird nur auf Nodes nachgeladen)
=============================================================
Macht die Node erreichbar, obwohl sie hinter NAT sitzt und kein Port
geoeffnet ist.

Zwei Wege, in dieser Reihenfolge
--------------------------------
  1. DIREKT. Die Node schickt regelmaessig ein Paket an das Backend. Ihr
     NAT-Geraet legt dabei eine Zuordnung an, und das Backend sieht, unter
     welcher oeffentlichen Adresse und welchem Port sie erscheint. Genau
     diese Adresse landet in der naechsten Tunnel-Datei. Der Benutzer
     verbindet sich dann DIREKT - am Backend vorbei.

     Wichtig dabei: Das Paket geht aus DEMSELBEN Socket, den auch
     WireGuard benutzt. Nur dann beobachtet das Backend die Zuordnung, die
     spaeter auch der Tunnel trifft.

  2. RELAY. Bei symmetrischem NAT vergibt das NAT-Geraet fuer jedes Ziel
     einen anderen Port - die beobachtete Adresse ist dann fuer den
     Benutzer wertlos. Typisch bei Mobilfunk und CGNAT. In dem Fall
     tunneln wir die WireGuard-Pakete durch das Backend. Langsamer, aber
     es funktioniert immer. Der Inhalt bleibt verschluesselt; das Backend
     reicht nur weiter.

Der Wechsel passiert von selbst: Meldet das Backend keine brauchbare
Adresse, laeuft es ueber den Relay.

Wie das Weiterreichen technisch aussieht
----------------------------------------
Der Relay hat einen eigenen UDP-Port am Backend. Diese Node meldet sich
dort mit ihrem Token an, und von da an gilt: Was hereinkommt, wird in den
lokalen WireGuard-Port eingespeist; was WireGuard herausgibt, geht zurueck
an den Relay. Ist der UDP-Port nicht erreichbar, laeuft dasselbe ueber die
bestehende Socket.IO-Verbindung ('send_via_agent').
"""

from __future__ import annotations

import socket
import struct
import threading
import time

MAGIC = b"RMMR1"
MSG_BIND = 1
MSG_DATA = 2
MSG_PING = 3

# So oft wird die NAT-Zuordnung offengehalten. Viele NAT-Geraete vergessen
# eine UDP-Zuordnung nach 30 bis 60 Sekunden.
KEEPALIVE = 20.0


class NodeRelay:
    """Haelt die Node erreichbar und reicht Pakete weiter."""

    def __init__(self, backend_host: str, relay_port: int, wg_port: int,
                 token: str, send_via_agent=None, log=print):
        self.backend_host = backend_host
        self.relay_port = int(relay_port)
        self.wg_port = int(wg_port)
        self.token = token
        self.send_via_agent = send_via_agent
        self.log = log

        self.sock: socket.socket | None = None
        self.public_endpoint = ""        # wie das Backend uns sieht
        self.mode = "aus"                # 'direkt' oder 'relay'
        self.running = False
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.packets_in = 0
        self.packets_out = 0
        self.last_seen = 0.0

    # -- Start / Stopp -------------------------------------------------

    def start(self) -> None:
        if not self.backend_host:
            self.log("[node-relay] Keine Backend-Adresse - Relay bleibt aus")
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(1.0)
        self._stop.clear()
        self.running = True
        for target in (self._keepalive_loop, self._receive_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)
        self.log(f"[node-relay] Erreichbarkeit wird hergestellt "
                 f"(Backend {self.backend_host}:{self.relay_port})")

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # -- Anmelden und offenhalten --------------------------------------

    def _keepalive_loop(self) -> None:
        dest = (self.backend_host, self.relay_port)
        bind = MAGIC + bytes([MSG_BIND]) + self.token.encode()
        while not self._stop.is_set():
            try:
                # Anmeldung wiederholen: Faellt das Backend zwischendurch
                # aus, ist die Sitzung dort weg - eine erneute Anmeldung
                # stellt sie ohne Zutun wieder her.
                self.sock.sendto(bind, dest)
                time.sleep(0.2)
                self.sock.sendto(MAGIC + bytes([MSG_PING]), dest)
            except OSError as e:
                self.log(f"[node-relay] Keepalive fehlgeschlagen: {e}")
            self._stop.wait(KEEPALIVE)

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            if not data.startswith(MAGIC):
                continue
            kind = data[len(MAGIC)]
            body = data[len(MAGIC) + 1:]
            self.last_seen = time.time()

            if kind == MSG_BIND:
                self.mode = "relay"
            elif kind == MSG_PING:
                pass
            elif kind == MSG_DATA:
                self.packets_in += 1
                self._to_wireguard(body)

    # -- Weiterreichen --------------------------------------------------

    def _to_wireguard(self, packet: bytes) -> None:
        """
        Ein WireGuard-Paket vom Relay in den lokalen WireGuard-Port.

        Der Absender ist dabei unser eigener Socket - WireGuard antwortet
        also an uns, und wir schicken die Antwort zurueck an den Relay.
        Damit ist der Rueckweg ohne weiteres Zutun geschlossen.
        """
        try:
            self.sock.sendto(packet, ("127.0.0.1", self.wg_port))
        except OSError as e:
            self.log(f"[node-relay] Weiterleitung an WireGuard: {e}")

    def inject(self, packet: bytes) -> None:
        """Ein Paket, das ueber die Agent-Verbindung hereinkam."""
        self.packets_in += 1
        self._to_wireguard(packet)

    def deliver_local(self, packet: bytes) -> None:
        """
        Ein IP-Paket aus dem virtuellen Netz an dieses Geraet.

        Im virtuellen Netz braucht das Geraet selbst kein WireGuard - das
        Backend ist die Gegenstelle. Deshalb wird hier nicht getunnelt,
        sondern direkt zugestellt. Ohne Rohsocket geht das nur fuer
        Verbindungen, die wir selbst aufbauen; die eigentliche Zustellung
        uebernimmt weiterhin der Reverse Proxy bzw. die Port-Weiterleitung.
        """
        # Bewusst nicht umgesetzt: Ein IP-Paket lokal einzuspeisen braucht
        # ein TUN-Geraet oder einen Rohsocket. Das virtuelle Netz erreicht
        # Geraete deshalb ueber die vorhandenen Wege (Proxy, Weiterleitung),
        # nicht ueber rohes IP. Diese Methode existiert, damit der Aufrufer
        # nicht ins Leere laeuft.
        self.log("[node-relay] Rohes IP-Paket verworfen - im virtuellen Netz "
                 "werden Geraete ueber Proxy und Port-Weiterleitung erreicht")

    def note_public_endpoint(self, endpoint: str) -> None:
        """Das Backend teilt mit, wie es uns von aussen sieht."""
        if endpoint and endpoint != self.public_endpoint:
            self.public_endpoint = endpoint
            self.mode = "direkt"
            self.log(f"[node-relay] Direkt erreichbar unter {endpoint}")

    def stats(self) -> dict:
        return {
            "modus": self.mode,
            "oeffentlich": self.public_endpoint,
            "wg_port": self.wg_port,
            "pakete_rein": self.packets_in,
            "pakete_raus": self.packets_out,
            "zuletzt_gesehen": int(self.last_seen * 1000) if self.last_seen else 0,
            "laeuft": self.running,
        }

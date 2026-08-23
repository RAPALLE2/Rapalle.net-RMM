"""
node_ipstack.py  -  NODE-MODUL (wird nur auf Nodes nachgeladen)
---------------------------------------------------------------
Ein kleiner IPv4-Stack im Arbeitsspeicher.

Wortgleich mit backend/app/vpn_stack.py. Endet der Tunnel direkt auf der
Node, laeuft dieser Stack DORT - und die Nutzdaten gehen von der Node
unmittelbar per socket ins Netz, ohne Umweg ueber das Backend.

Wozu? Aus dem WireGuard-Tunnel kommen rohe IP-Pakete. Normalerweise würde
man sie in ein TUN-Gerät schreiben und den Kernel weiterleiten lassen - das
setzt aber NET_ADMIN, ein TUN-Gerät und Root voraus, und vor allem müsste
dann auf dem verwalteten Gerät etwas installiert werden. Genau das soll
nicht sein.

Stattdessen endet der Tunnel hier: Dieser Stack liest die IP-Pakete, führt
den TCP-Handschlag selbst, und reicht den reinen BYTE-STROM an den Agenten
weiter. Der Agent macht daraufhin das, was jedes Python-Programm kann - er
öffnet eine ganz gewöhnliche Socket-Verbindung zum Ziel. Aus Sicht des
Zielrechners kommt die Verbindung damit vom verwalteten Gerät selbst.

Unterstützt:
  * TCP  - eigener, bewusst einfacher Zustandsautomat (siehe TcpConn)
  * UDP  - zustandslose Weiterleitung mit kurzlebiger Zuordnung
  * ICMP - Echo-Anfragen ("ping") werden über den Agenten beantwortet

Bewusst NICHT unterstützt: IPv6, IP-Fragmentierung, TCP-Optionen ausser MSS,
selektive Bestätigungen (SACK), Fenster-Skalierung. Das kostet etwas
Durchsatz, hält den Code aber überschaubar und robust.
"""

from __future__ import annotations

import asyncio
import struct
import time

# --- Protokollnummern ---
PROTO_ICMP = 1
PROTO_TCP = 6
PROTO_UDP = 17

# TCP-Flags
FIN, SYN, RST, PSH, ACK, URG = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20

# Maximale Nutzlast je TCP-Segment. 1240 lässt bequem Platz für IP- und
# TCP-Kopf sowie den WireGuard-Rahmen innerhalb einer 1420er-Tunnel-MTU.
MSS = 1240
# Fenstergrösse, die wir dem Gegenüber anbieten.
RECV_WINDOW = 65535
# Wie oft unbestätigte Daten erneut geschickt werden.
RETRANSMIT_INTERVAL = 0.4
# Nach so langer Untätigkeit wird eine Verbindung aufgeräumt.
TCP_IDLE_TIMEOUT = 900.0
UDP_IDLE_TIMEOUT = 120.0


# ----------------------------------------------------------------------
# Prüfsummen und Kopf-Bau
# ----------------------------------------------------------------------

def _checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _ip4(src: bytes, dst: bytes, proto: int, payload: bytes,
         ident: int = 0) -> bytes:
    total = 20 + len(payload)
    header = struct.pack(">BBHHHBBH4s4s", 0x45, 0, total, ident & 0xFFFF,
                         0x4000, 64, proto, 0, src, dst)
    chk = _checksum(header)
    header = header[:10] + struct.pack(">H", chk) + header[12:]
    return header + payload


def _pseudo(src: bytes, dst: bytes, proto: int, length: int) -> bytes:
    return src + dst + struct.pack(">BBH", 0, proto, length)


def ip_str(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def ip_raw(text: str) -> bytes:
    return bytes(int(p) for p in text.split("."))


# ----------------------------------------------------------------------
# Eine TCP-Verbindung durch den Tunnel
# ----------------------------------------------------------------------

class TcpConn:
    """
    Ein bewusst einfacher TCP-Zustandsautomat.

    Vereinfachungen und warum sie vertretbar sind:
      * Wir bestätigen JEDES ankommende Segment sofort. Etwas mehr
        Bestätigungs-Pakete, dafür kein Timer-Gefrickel.
      * Segmente ausserhalb der Reihenfolge werden verworfen. Der Absender
        schickt sie ohnehin erneut; im Tunnel kommt das selten vor.
      * Unbestätigte eigene Daten liegen in einem Puffer und werden
        periodisch erneut geschickt, bis sie bestätigt sind.
    """

    def __init__(self, stack: "IPStack", key, src, sport, dst, dport, seq):
        self.stack = stack
        self.key = key
        self.src, self.sport = src, sport      # die Seite im Tunnel (Benutzer)
        self.dst, self.dport = dst, dport      # das Ziel im Netz des Clients
        self.rcv_nxt = (seq + 1) & 0xFFFFFFFF  # nächstes erwartetes Byte
        self.snd_una = 0                        # ältestes unbestätigtes Byte
        self.snd_nxt = 0
        self.iss = int(time.time() * 1000) & 0x7FFFFFFF
        self.snd_una = self.snd_nxt = self.iss
        self.state = "SYN_RECEIVED"
        self.out_buffer = bytearray()           # zum Benutzer, noch unbestätigt
        self.pending_from_peer = bytearray()    # zum Ziel, bis der Agent bereit ist
        self.opened = False                     # Agent hat die Verbindung bestätigt
        self.last_activity = time.monotonic()
        self.peer_window = RECV_WINDOW
        self.closing = False
        self.fin_sent = False
        self.stream_id = ""
        self._rto_una = -1          # Stand, seit dem wir auf eine Bestätigung warten
        self._rto_since: float | None = None

    # -- Hilfen -------------------------------------------------------

    def _emit(self, flags: int, payload: bytes = b"", seq: int | None = None):
        seq = self.snd_nxt if seq is None else seq
        tcp = struct.pack(">HHIIBBHHH", self.dport, self.sport,
                          seq & 0xFFFFFFFF, self.rcv_nxt & 0xFFFFFFFF,
                          5 << 4, flags, RECV_WINDOW, 0, 0)
        chk = _checksum(_pseudo(self.dst, self.src, PROTO_TCP,
                                len(tcp) + len(payload)) + tcp + payload)
        tcp = tcp[:16] + struct.pack(">H", chk) + tcp[18:]
        self.stack.send_ip(_ip4(self.dst, self.src, PROTO_TCP, tcp + payload))

    def send_syn_ack(self):
        # MSS-Option mitgeben, damit der Gegenüber nicht zu grosse Segmente
        # baut, die im Tunnel zerbrechen würden.
        opts = struct.pack(">BBH", 2, 4, MSS)
        tcp = struct.pack(">HHIIBBHHH", self.dport, self.sport,
                          self.iss, self.rcv_nxt, 6 << 4, SYN | ACK,
                          RECV_WINDOW, 0, 0) + opts
        chk = _checksum(_pseudo(self.dst, self.src, PROTO_TCP, len(tcp)) + tcp)
        tcp = tcp[:16] + struct.pack(">H", chk) + tcp[18:]
        self.stack.send_ip(_ip4(self.dst, self.src, PROTO_TCP, tcp))
        self.snd_nxt = (self.iss + 1) & 0xFFFFFFFF
        self.state = "ESTABLISHED"

    def send_rst(self):
        self._emit(RST | ACK)
        self.state = "CLOSED"

    # -- Vom Benutzer (Tunnel) ---------------------------------------

    def on_segment(self, flags: int, seq: int, ack: int, window: int,
                   payload: bytes):
        self.last_activity = time.monotonic()
        self.peer_window = window

        if flags & RST:
            self.state = "CLOSED"
            self.stack.close_tcp(self, notify_agent=True)
            return

        # Bestätigungen abarbeiten: alles bis 'ack' ist angekommen.
        if flags & ACK:
            acked = (ack - self.snd_una) & 0xFFFFFFFF
            if 0 < acked <= len(self.out_buffer) + 1:
                consume = min(acked, len(self.out_buffer))
                del self.out_buffer[:consume]
                self.snd_una = ack

        if payload:
            if seq == self.rcv_nxt:
                self.rcv_nxt = (self.rcv_nxt + len(payload)) & 0xFFFFFFFF
                if self.opened:
                    self.stack.to_agent_data(self, bytes(payload))
                else:
                    self.pending_from_peer += payload
            # Immer bestätigen - auch bei Segmenten ausserhalb der Reihe.
            # So erfährt der Absender sofort, wo wir wirklich stehen.
            self._emit(ACK)

        if flags & FIN:
            self.rcv_nxt = (self.rcv_nxt + 1) & 0xFFFFFFFF
            self._emit(ACK)
            self.closing = True
            self.stack.to_agent_close(self)

    # -- Vom Agenten (Zielrechner) -----------------------------------

    def on_agent_open(self, ok: bool):
        if not ok:
            self.send_rst()
            self.stack.close_tcp(self, notify_agent=False)
            return
        self.opened = True
        if self.pending_from_peer:
            self.stack.to_agent_data(self, bytes(self.pending_from_peer))
            self.pending_from_peer.clear()

    def on_agent_data(self, data: bytes):
        self.last_activity = time.monotonic()
        self.out_buffer += data
        self._flush()

    def on_agent_close(self):
        if self.fin_sent:
            return
        self._flush()
        self._emit(FIN | ACK, seq=(self.snd_una + len(self.out_buffer)) & 0xFFFFFFFF)
        self.fin_sent = True
        self.state = "FIN_WAIT"

    def _flush(self):
        """Schickt so viel aus dem Puffer, wie das Fenster hergibt."""
        offset = (self.snd_nxt - self.snd_una) & 0xFFFFFFFF
        while offset < len(self.out_buffer):
            chunk = self.out_buffer[offset:offset + MSS]
            if not chunk:
                break
            self._emit(PSH | ACK, bytes(chunk), seq=self.snd_nxt)
            self.snd_nxt = (self.snd_nxt + len(chunk)) & 0xFFFFFFFF
            offset += len(chunk)

    def retransmit(self):
        """Unbestätigte Daten erneut schicken (einfache Wiederholung).

        Erst NACH Ablauf des Wartefensters - sonst würde jeder Tick die
        gerade erst verschickten Daten sinnlos verdoppeln.
        """
        if not self.out_buffer:
            self._rto_since = None
            return
        now = time.monotonic()
        if self._rto_una != self.snd_una:
            self._rto_una = self.snd_una
            self._rto_since = now
            return
        if self._rto_since is None or now - self._rto_since < RETRANSMIT_INTERVAL:
            return
        self._rto_since = now
        chunk = bytes(self.out_buffer[:MSS])
        self._emit(PSH | ACK, chunk, seq=self.snd_una)


# ----------------------------------------------------------------------
# Der Stack selbst
# ----------------------------------------------------------------------

class IPStack:
    """
    Ein Stack pro Tunnel. 'send_ip' schiebt fertige IP-Pakete zurück in den
    WireGuard-Tunnel; die 'agent_*'-Rückrufe reichen Nutzdaten an den Agenten.
    """

    def __init__(self, tunnel_id: str, send_ip, agent_send):
        self.tunnel_id = tunnel_id
        self.send_ip = send_ip          # callable(bytes)
        self.agent_send = agent_send    # callable(event:str, payload:dict)
        self.tcp: dict[tuple, TcpConn] = {}
        self.udp: dict[tuple, float] = {}
        self._stream_seq = 0
        self.streams: dict[str, TcpConn] = {}   # Strom-ID -> Verbindung
        self.bytes_in = 0
        self.bytes_out = 0

    def _next_stream_id(self) -> str:
        self._stream_seq += 1
        return f"{self.tunnel_id[:8]}-{self._stream_seq}"

    # -- Eingang aus dem Tunnel ---------------------------------------

    def on_tunnel_packet(self, packet: bytes) -> None:
        if len(packet) < 20 or (packet[0] >> 4) != 4:
            return   # nur IPv4
        ihl = (packet[0] & 0x0F) * 4
        proto = packet[9]
        src, dst = packet[12:16], packet[16:20]
        body = packet[ihl:]
        self.bytes_in += len(packet)

        if proto == PROTO_TCP:
            self._on_tcp(src, dst, body)
        elif proto == PROTO_UDP:
            self._on_udp(src, dst, body)
        elif proto == PROTO_ICMP:
            self._on_icmp(src, dst, body)

    def _on_tcp(self, src, dst, body: bytes) -> None:
        if len(body) < 20:
            return
        sport, dport, seq, ack = struct.unpack(">HHII", body[:12])
        offset = (body[12] >> 4) * 4
        flags = body[13]
        window = struct.unpack(">H", body[14:16])[0]
        payload = body[offset:]
        key = (src, sport, dst, dport)

        conn = self.tcp.get(key)
        if conn is None:
            if not (flags & SYN) or (flags & ACK):
                return   # kein neuer Verbindungswunsch -> ignorieren
            conn = TcpConn(self, key, src, sport, dst, dport, seq)
            self.tcp[key] = conn
            sid = self._next_stream_id()
            conn.stream_id = sid
            self.streams[sid] = conn
            conn.send_syn_ack()
            # Der Agent baut jetzt die echte Verbindung zum Ziel auf.
            self.agent_send("vpn-open", {
                "tunnel": self.tunnel_id, "stream": sid,
                "host": ip_str(dst), "port": dport, "proto": "tcp",
            })
            return
        conn.on_segment(flags, seq, ack, window, payload)

    def _on_udp(self, src, dst, body: bytes) -> None:
        if len(body) < 8:
            return
        sport, dport, length = struct.unpack(">HHH", body[:6])
        payload = body[8:length] if length >= 8 else body[8:]
        key = (src, sport, dst, dport)
        self.udp[key] = time.monotonic()
        import base64
        self.agent_send("vpn-udp", {
            "tunnel": self.tunnel_id,
            "host": ip_str(dst), "port": dport,
            "sport": sport, "src": ip_str(src),
            "data": base64.b64encode(payload).decode(),
        })

    def _on_icmp(self, src, dst, body: bytes) -> None:
        if len(body) < 8 or body[0] != 8:
            return   # nur Echo-Anfragen
        ident, seq = struct.unpack(">HH", body[4:8])
        self.agent_send("vpn-ping", {
            "tunnel": self.tunnel_id, "host": ip_str(dst),
            "src": ip_str(src), "ident": ident, "seq": seq,
            "payload_len": len(body) - 8,
        })

    # -- Rückweg: Antworten des Agenten -------------------------------

    def on_agent_open_result(self, stream_id: str, ok: bool) -> None:
        conn = self.streams.get(stream_id)
        if conn:
            conn.on_agent_open(ok)

    def on_agent_stream_data(self, stream_id: str, data: bytes) -> None:
        conn = self.streams.get(stream_id)
        if conn:
            self.bytes_out += len(data)
            conn.on_agent_data(data)

    def on_agent_stream_close(self, stream_id: str) -> None:
        conn = self.streams.get(stream_id)
        if conn:
            conn.on_agent_close()

    def on_agent_udp(self, src_ip: str, sport: int, dst_ip: str, dport: int,
                     data: bytes) -> None:
        """Antwort-Datagramm: 'src' ist hier das Ziel im Netz des Clients."""
        udp = struct.pack(">HHHH", sport, dport, 8 + len(data), 0) + data
        s, d = ip_raw(src_ip), ip_raw(dst_ip)
        chk = _checksum(_pseudo(s, d, PROTO_UDP, len(udp)) + udp)
        udp = udp[:6] + struct.pack(">H", chk or 0xFFFF) + udp[8:]
        self.bytes_out += len(data)
        self.send_ip(_ip4(s, d, PROTO_UDP, udp))

    def on_agent_ping_result(self, host: str, dst: str, ident: int, seq: int,
                             ok: bool, payload_len: int = 32) -> None:
        if not ok:
            return
        body = struct.pack(">BBHHH", 0, 0, 0, ident, seq) + b"\x00" * payload_len
        chk = _checksum(body)
        body = body[:2] + struct.pack(">H", chk) + body[4:]
        self.send_ip(_ip4(ip_raw(host), ip_raw(dst), PROTO_ICMP, body))

    # -- Aufräumen -----------------------------------------------------

    def close_tcp(self, conn: TcpConn, notify_agent: bool = True) -> None:
        self.tcp.pop(conn.key, None)
        sid = getattr(conn, "stream_id", None)
        if sid:
            self.streams.pop(sid, None)
            if notify_agent:
                self.agent_send("vpn-close", {"tunnel": self.tunnel_id,
                                              "stream": sid})

    def to_agent_data(self, conn: TcpConn, data: bytes) -> None:
        import base64
        self.bytes_out += len(data)
        self.agent_send("vpn-data", {
            "tunnel": self.tunnel_id, "stream": conn.stream_id,
            "data": base64.b64encode(data).decode(),
        })

    def to_agent_close(self, conn: TcpConn) -> None:
        self.agent_send("vpn-close", {"tunnel": self.tunnel_id,
                                      "stream": getattr(conn, "stream_id", "")})

    def tick(self) -> None:
        """Wird ein paar Mal pro Sekunde aufgerufen: Wiederholungen + Aufräumen."""
        now = time.monotonic()
        for conn in list(self.tcp.values()):
            if now - conn.last_activity > TCP_IDLE_TIMEOUT:
                conn.send_rst()
                self.close_tcp(conn)
                continue
            conn.retransmit()
        for key, ts in list(self.udp.items()):
            if now - ts > UDP_IDLE_TIMEOUT:
                self.udp.pop(key, None)

    def shutdown(self) -> None:
        for conn in list(self.tcp.values()):
            try:
                conn.send_rst()
            except Exception:
                pass
            self.close_tcp(conn)

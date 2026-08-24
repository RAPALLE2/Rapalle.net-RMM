"""
node_wireguard.py  -  NODE-MODUL (wird nur auf Nodes nachgeladen)
-----------------------------------------------------------------
WireGuard-Protokoll in reinem Python - die SERVER-Seite (Responder).

Dieses Modul ist WORTGLEICH mit backend/app/wireguard.py. Es wird vom
Backend an Nodes ausgeliefert, damit der Tunnel DIREKT auf der Node enden
kann statt im Backend. Beide Seiten sprechen damit garantiert dasselbe
Protokoll - eine zweite, abweichende Umsetzung waere die sicherste Art,
sich subtile Handshake-Fehler einzuhandeln.

Auf einem gewoehnlichen Client liegt diese Datei NICHT. Sie kommt erst mit
der Aufwertung zur Node dazu.

Warum eigener Code statt des Kernel-Moduls oder 'wg-quick'?
  * Auf den verwalteten Geräten darf NICHTS installiert werden. Der Agent
    ist reines Python und bleibt es auch - er sieht von WireGuard gar nichts.
  * Der Tunnel endet deshalb HIER im Backend. Der Benutzer verbindet sich mit
    einem ganz normalen, offiziellen WireGuard-Client (Windows/macOS/iOS/
    Android/Linux) gegen diesen Python-Endpunkt. Auf der anderen Seite
    übersetzt vpn_stack.py die Datenpakete in gewöhnliche TCP-/UDP-
    Verbindungen, die der Agent mit Bordmitteln aufbaut.
  * Dadurch braucht der Container weder das Kernel-Modul, noch NET_ADMIN,
    noch ein TUN-Gerät.

Was hier umgesetzt ist (nach dem WireGuard-Whitepaper, Protokollversion 1):
  * Nachrichtentyp 1  - Handshake-Initiation  (empfangen)
  * Nachrichtentyp 2  - Handshake-Response    (gesendet)
  * Nachrichtentyp 4  - Transportdaten        (beide Richtungen)
  * Nachrichtentyp 3  - Cookie-Reply: wird NICHT gesendet. Cookies sind ein
    Schutz gegen Überlast-Angriffe; ohne sie funktioniert das Protokoll
    normal weiter, nur der DoS-Schutz fehlt. mac1 wird trotzdem geprüft,
    damit fremder Datenmüll früh und billig aussortiert wird.

Das Rauschen-Handshake-Muster ist Noise_IKpsk2. Die Zeilen unten folgen
absichtlich eng der Notation des Whitepapers (Ci = Chaining Key, Hi = Hash),
damit man beides nebeneinander lesen kann.

Kryptografie kommt aus 'cryptography' (X25519, ChaCha20-Poly1305) und
hashlib (BLAKE2s) - beides ist bereits eine Abhängigkeit des Backends.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import struct
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

# ----------------------------------------------------------------------
# Protokoll-Konstanten (wortwörtlich aus dem WireGuard-Whitepaper)
# ----------------------------------------------------------------------
CONSTRUCTION = b"Noise_IKpsk2_25519_ChaCha20Poly1305_BLAKE2s"
IDENTIFIER = b"WireGuard v1 zx2c4 Jason@zx2c4.com"
LABEL_MAC1 = b"mac1----"
LABEL_COOKIE = b"cookie--"

MSG_INITIATION = 1
MSG_RESPONSE = 2
MSG_COOKIE = 3
MSG_TRANSPORT = 4

LEN_INITIATION = 148
LEN_RESPONSE = 92

ZERO_KEY = b"\x00" * 32

# Nach dieser Zeit ohne gültiges Paket gilt eine Sitzung als tot. WireGuard-
# Clients erneuern den Handshake von sich aus alle ~2 Minuten, deshalb ist
# das grosszügig bemessen.
SESSION_IDLE_TIMEOUT = 360.0


# ----------------------------------------------------------------------
# Kleine Krypto-Bausteine
# ----------------------------------------------------------------------

def _hash(data: bytes) -> bytes:
    return hashlib.blake2s(data, digest_size=32).digest()


def _mac(key: bytes, data: bytes) -> bytes:
    """Gekürzter, geschlüsselter BLAKE2s - für mac1/mac2 (16 Byte)."""
    return hashlib.blake2s(data, digest_size=16, key=key).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.blake2s).digest()


def _kdf(key: bytes, data: bytes, count: int) -> list[bytes]:
    """HKDF auf BLAKE2s-Basis, wie im Whitepaper (KDF1/KDF2/KDF3)."""
    tau0 = _hmac(key, data)
    out = []
    prev = b""
    for i in range(1, count + 1):
        prev = _hmac(tau0, prev + bytes([i]))
        out.append(prev)
    return out


def _dh(private: X25519PrivateKey, public_raw: bytes) -> bytes:
    return private.exchange(X25519PublicKey.from_public_bytes(public_raw))


def _pub_raw(private: X25519PrivateKey) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _priv_raw(private: X25519PrivateKey) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _aead_encrypt(key: bytes, counter: int, plain: bytes, ad: bytes) -> bytes:
    nonce = b"\x00" * 4 + struct.pack("<Q", counter)
    return ChaCha20Poly1305(key).encrypt(nonce, plain, ad)


def _aead_decrypt(key: bytes, counter: int, cipher: bytes, ad: bytes) -> bytes:
    nonce = b"\x00" * 4 + struct.pack("<Q", counter)
    return ChaCha20Poly1305(key).decrypt(nonce, cipher, ad)


def _tai64n(ts: float | None = None) -> bytes:
    """Zeitstempel im TAI64N-Format (12 Byte) - das erwartet WireGuard."""
    ts = time.time() if ts is None else ts
    secs = int(ts)
    nanos = int((ts - secs) * 1_000_000_000)
    return struct.pack(">QI", 0x400000000000000A + secs, nanos)


# ----------------------------------------------------------------------
# Schlüsselerzeugung (öffentlich nutzbar)
# ----------------------------------------------------------------------

def generate_keypair() -> tuple[str, str]:
    """Erzeugt (privater Schlüssel, öffentlicher Schlüssel) als Base64 -
    genau das Format, das in einer .conf-Datei steht."""
    import base64
    priv = X25519PrivateKey.generate()
    return (base64.b64encode(_priv_raw(priv)).decode(),
            base64.b64encode(_pub_raw(priv)).decode())


def public_from_private(private_b64: str) -> str:
    import base64
    priv = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(_pub_raw(priv)).decode()


def generate_preshared_key() -> str:
    """Zusätzlicher symmetrischer Schlüssel (PresharedKey in der .conf).
    Er ist optional, kostet nichts und härtet den Tunnel gegen künftige
    Angriffe mit Quantenrechnern."""
    import base64
    return base64.b64encode(os.urandom(32)).decode()


# ----------------------------------------------------------------------
# Eine laufende Transport-Sitzung (ein erfolgreicher Handshake)
# ----------------------------------------------------------------------

class Session:
    """Schlüsselmaterial und Zähler für EINE Handshake-Generation."""

    def __init__(self, local_index: int, remote_index: int,
                 send_key: bytes, recv_key: bytes, peer: "Peer"):
        self.local_index = local_index
        self.remote_index = remote_index
        self.send_key = ChaCha20Poly1305(send_key)
        self.recv_key = ChaCha20Poly1305(recv_key)
        self.send_counter = 0
        self.peer = peer
        self.created = time.monotonic()
        self.last_recv = time.monotonic()
        # Wiedereinspiel-Schutz: höchster gesehener Zähler + kleines Fenster
        # für Pakete, die die Reihenfolge verlassen haben.
        self._recv_max = -1
        self._recv_seen: set[int] = set()

    def expired(self) -> bool:
        return (time.monotonic() - self.last_recv) > SESSION_IDLE_TIMEOUT

    def accept_counter(self, counter: int) -> bool:
        """Wiedereinspiel-Schutz: jeder Zähler darf nur EINMAL vorkommen."""
        if counter <= self._recv_max - 2048:
            return False
        if counter in self._recv_seen:
            return False
        self._recv_seen.add(counter)
        if counter > self._recv_max:
            self._recv_max = counter
        if len(self._recv_seen) > 8192:
            cutoff = self._recv_max - 2048
            self._recv_seen = {c for c in self._recv_seen if c > cutoff}
        return True

    def encrypt(self, plain: bytes) -> bytes:
        """Baut eine fertige Transport-Nachricht (Typ 4)."""
        counter = self.send_counter
        self.send_counter += 1
        nonce = b"\x00" * 4 + struct.pack("<Q", counter)
        # WireGuard füllt Nutzdaten auf ein Vielfaches von 16 auf - die
        # Länge steckt ohnehin im IP-Kopf, das Padding verschleiert sie.
        pad = (-len(plain)) % 16
        cipher = self.send_key.encrypt(nonce, plain + b"\x00" * pad, b"")
        return (struct.pack("<IIQ", MSG_TRANSPORT, self.remote_index, counter)
                + cipher)

    def decrypt(self, counter: int, cipher: bytes) -> bytes:
        nonce = b"\x00" * 4 + struct.pack("<Q", counter)
        return self.recv_key.decrypt(nonce, cipher, b"")


class Peer:
    """Eine Gegenstelle: ein Benutzer mit seinem WireGuard-Schlüssel."""

    def __init__(self, public_key: bytes, preshared: bytes, tunnel_id: str):
        self.public_key = public_key
        self.preshared = preshared or ZERO_KEY
        self.tunnel_id = tunnel_id
        self.endpoint: tuple[str, int] | None = None   # letzte Absenderadresse
        self.session: Session | None = None
        self.last_timestamp: bytes = b""               # gegen Wiedereinspielen
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.last_handshake: float = 0.0
        self.last_seen: float = 0.0


# ----------------------------------------------------------------------
# Der UDP-Server
# ----------------------------------------------------------------------

class WireGuardServer(asyncio.DatagramProtocol):
    """
    Nimmt WireGuard-Verbindungen entgegen und reicht die entschlüsselten
    IP-Pakete nach oben weiter.

    Das Weiterreichen geschieht über zwei Rückrufe, die von aussen gesetzt
    werden (siehe vpn.py):
      on_packet(peer, ip_packet)   - ein entschlüsseltes IP-Paket kam an
      on_handshake(peer)           - eine Gegenstelle hat sich neu verbunden
    """

    def __init__(self, private_key_b64: str):
        import base64
        self.private = X25519PrivateKey.from_private_bytes(
            base64.b64decode(private_key_b64))
        self.public_raw = _pub_raw(self.private)
        self.mac1_key = _hash(LABEL_MAC1 + self.public_raw)

        self.transport: asyncio.DatagramTransport | None = None
        self.peers: dict[bytes, Peer] = {}        # öffentlicher Schlüssel -> Peer
        self.sessions: dict[int, Session] = {}    # eigener Index -> Session
        self.on_packet = None
        self.on_handshake = None
        # Wird gesetzt, wenn eine Node ihre Erreichbarkeit prueft.
        self.on_probe = None
        # Erstes gescheitertes Handschlag-Paket (fuer die Fehlersuche).
        self.captured: dict | None = None

        # ZAEHLER. Der Grund fuer sie: Wenn ein Tunnel "nicht geht", gibt es
        # genau drei Moeglichkeiten, und ohne diese Zahlen kann man sie
        # nicht auseinanderhalten:
        #   1. Es kommt gar nichts an -> der UDP-Port ist nicht durchgereicht
        #      (Reverse-Proxys wie Cloudflare oder nginx leiten KEIN UDP
        #      weiter, und Firewalls oft auch nicht).
        #   2. Es kommt etwas an, aber der Handschlag scheitert -> falsche
        #      Schluessel oder eine veraltete Tunnel-Datei.
        #   3. Der Handschlag steht, aber es fliessen keine Daten -> das
        #      Problem liegt hinter dem Tunnel.
        # Die Zahlen beantworten das in Sekunden statt in Stunden.
        self.stats = {
            "packets": 0, "handshakes": 0, "transport": 0, "probes": 0,
            "unknown_peer": 0, "bad_mac": 0, "undecryptable": 0,
            "junk": 0, "last_from": "", "last_at": 0.0,
            "first_at": 0.0,
            # Wie viele Handschlag-Versuche ueberhaupt eintrafen. Diese Zahl
            # fehlte - und ohne sie war nicht zu unterscheiden, ob die
            # Pakete vom WireGuard-Client kamen oder von den Probe-Paketen
            # der eigenen Nodes. Genau daran ist eine Fehlersuche
            # gescheitert: 10 Pakete, 0 Handschlaege, keine Fehler - in
            # Wahrheit waren es zehn Probes und kein einziger Handschlag.
            "initiations": 0,
            "errors": 0,        # Ausnahme bei der Verarbeitung
            "last_error": "",
            "last_client_from": "",   # letzter echter WireGuard-Absender
        }

    # -- Verwaltung der Gegenstellen ---------------------------------

    def add_peer(self, public_key_b64: str, preshared_b64: str | None,
                 tunnel_id: str) -> Peer:
        import base64
        pub = base64.b64decode(public_key_b64)
        psk = base64.b64decode(preshared_b64) if preshared_b64 else ZERO_KEY
        peer = Peer(pub, psk, tunnel_id)
        self.peers[pub] = peer
        return peer

    def remove_peer(self, public_key_b64: str) -> None:
        import base64
        try:
            pub = base64.b64decode(public_key_b64)
        except Exception:
            return
        peer = self.peers.pop(pub, None)
        if peer and peer.session:
            self.sessions.pop(peer.session.local_index, None)

    def peer_for_tunnel(self, tunnel_id: str) -> Peer | None:
        for p in self.peers.values():
            if p.tunnel_id == tunnel_id:
                return p
        return None

    # -- asyncio-Anbindung -------------------------------------------

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        st = self.stats
        st["packets"] += 1
        st["last_from"] = f"{addr[0]}:{addr[1]}"
        st["last_at"] = time.time()
        if not st["first_at"]:
            st["first_at"] = time.time()
            # Das allererste Paket ausdruecklich melden. Damit steht in
            # 'docker logs' schwarz auf weiss, DASS der Port erreichbar ist -
            # die Frage, an der die Fehlersuche sonst als Erstes haengt.
            print(f"[wg] Erstes Paket auf dem VPN-Port empfangen, von "
                  f"{addr[0]}:{addr[1]} - der UDP-Port ist also erreichbar.")
        try:
            self._handle(data, addr)
        except Exception as e:
            # Ein einzelnes kaputtes Paket darf den Server nie beenden.
            # Fremde Scanner schicken hier ständig Unsinn her.
            self.stats["errors"] += 1
            self.stats["last_error"] = f"{type(e).__name__}: {e}"
            print(f"[wg] Paket von {addr} verworfen: {e!r}")

    def _handle(self, data: bytes, addr) -> None:
        if len(data) < 4:
            return
        # Erreichbarkeitsprobe einer Node. Sie kommt aus DEMSELBEN Socket,
        # den die Node spaeter fuer den Tunnel benutzt - nur deshalb ist die
        # hier beobachtete Adresse die, unter der auch der Tunnel ankommt.
        # WireGuard-Nachrichten fangen nie mit diesem Praefix an (ihr erstes
        # Byte ist 1-4), eine Verwechslung ist also ausgeschlossen.
        if data[:9] == b"RMMPROBE1":
            self.stats["probes"] += 1
            if self.on_probe:
                try:
                    self.on_probe(data[9:41].decode("ascii", "ignore"), addr)
                except Exception as e:
                    print(f"[wg] Probe-Rückruf fehlgeschlagen: {e!r}")
            return
        msg_type = data[0]
        if msg_type == MSG_INITIATION and len(data) == LEN_INITIATION:
            self.stats["initiations"] += 1
            self.stats["last_client_from"] = f"{addr[0]}:{addr[1]}"
            self._handle_initiation(data, addr)
        elif msg_type == MSG_TRANSPORT and len(data) >= 32:
            self.stats["transport"] += 1
            self.stats["last_client_from"] = f"{addr[0]}:{addr[1]}"
            self._handle_transport(data, addr)
        else:
            # Weder WireGuard noch unsere Probe - meist ein Portscanner.
            self.stats["junk"] += 1
        # Typ 2 (Response) und 3 (Cookie) erwarten wir als Server nicht.

    # -- Handshake ----------------------------------------------------

    def _handle_initiation(self, data: bytes, addr) -> None:
        # mac1 zuerst prüfen: billig, und sortiert alles aus, was nicht
        # wenigstens unseren öffentlichen Schlüssel kennt.
        expected_mac1 = _mac(self.mac1_key, data[:116])
        if not hmac.compare_digest(expected_mac1, data[116:132]):
            # mac1 wird aus UNSEREM oeffentlichen Schluessel gebildet. Passt
            # er nicht, gehoert die Gegenstelle zu einem anderen Server -
            # oder die Tunnel-Datei stammt noch von vor einem
            # Schluesselwechsel.
            self.stats["bad_mac"] += 1
            print(f"[wg] Handschlag von {addr[0]} abgelehnt: falscher "
                  f"Server-Schluessel (mac1). Stammt die Tunnel-Datei von "
                  f"diesem Server?")
            return

        sender_index = struct.unpack("<I", data[4:8])[0]
        ephemeral = data[8:40]
        enc_static = data[40:88]
        # Der Zeitstempel ist TAI64N (12 Byte) + Poly1305-Siegel (16) = 28
        # Byte. Damit endet der signierte Teil bei 116 - genau dort beginnt
        # mac1.
        enc_timestamp = data[88:116]

        c = _hash(CONSTRUCTION)
        h = _hash(c + IDENTIFIER)
        h = _hash(h + self.public_raw)
        c = _kdf(c, ephemeral, 1)[0]
        h = _hash(h + ephemeral)

        c, k = _kdf(c, _dh(self.private, ephemeral), 2)
        try:
            peer_static = _aead_decrypt(k, 0, enc_static, h)
        except Exception:
            # Die Entschluesselung des statischen Feldes ist fehlgeschlagen,
            # OBWOHL mac1 gestimmt hat. mac1 beweist, dass die Gegenstelle
            # unseren richtigen oeffentlichen Schluessel kennt - der
            # Diffie-Hellman-Austausch MUSS dann passen. Bleibt nur: Unsere
            # Hash-Kette weicht irgendwo von der Spezifikation ab.
            #
            # Genau diese Abweichung findet der Selbsttest unten, indem er
            # die naheliegenden Varianten durchprobiert. Er laeuft nur beim
            # ERSTEN Fehlschlag - danach waere er nur noch Last.
            self.stats["static_failed"] = self.stats.get("static_failed", 0) + 1
            if self.stats["static_failed"] == 1:
                # Das gescheiterte Paket aufheben. Es enthaelt KEINE
                # Geheimnisse - alles darin ist entweder oeffentlich
                # (Ephemeral-Schluessel) oder verschluesselt. Damit laesst
                # sich der Fall spaeter Schritt fuer Schritt nachvollziehen,
                # ohne dass jemand danebenstehen und mitlesen muss.
                self.captured = {
                    "hex": data.hex(), "from": f"{addr[0]}:{addr[1]}",
                    "at": time.time(), "step": "static",
                }
                self._diagnose_initiation(data, ephemeral, enc_static)
            raise
        h = _hash(h + enc_static)

        peer = self.peers.get(peer_static)
        if peer is None:
            self.stats["unknown_peer"] += 1
            import base64 as _b64
            print(f"[wg] Handschlag von {addr[0]} abgelehnt: unbekannter "
                  f"Schluessel {_b64.b64encode(peer_static).decode()[:12]}… - "
                  f"der Tunnel wurde vermutlich geschlossen oder stammt von "
                  f"einer frueheren Installation.")
            return

        c, k = _kdf(c, _dh(self.private, peer_static), 2)
        try:
            timestamp = _aead_decrypt(k, 0, enc_timestamp, h)
        except Exception:
            # Hier ist die Ursache fast immer eine falsche Feldlaenge: Der
            # Zeitstempel ist TAI64N (12) + Siegel (16) = 28 Byte. Nimmt man
            # 32, sieht alles davor richtig aus und nur dieser Schritt
            # scheitert.
            self.stats["timestamp_failed"] = \
                self.stats.get("timestamp_failed", 0) + 1
            if not self.captured:
                self.captured = {"hex": data.hex(), "from": f"{addr[0]}:{addr[1]}",
                                 "at": time.time(), "step": "timestamp"}
            print(f"[wg] Handschlag von {addr[0]}: statisches Feld ok, aber "
                  f"der Zeitstempel liess sich nicht entschluesseln "
                  f"({len(enc_timestamp)} Byte, 28 erwartet).")
            raise
        h = _hash(h + enc_timestamp)

        # Wiedereinspiel-Schutz des Handshakes: der Zeitstempel muss echt
        # neuer sein als der letzte, den wir von dieser Gegenstelle sahen.
        if peer.last_timestamp and timestamp <= peer.last_timestamp:
            return
        peer.last_timestamp = timestamp

        self._send_response(peer, sender_index, c, h, ephemeral, addr)

    def _diagnose_initiation(self, data: bytes, ephemeral: bytes,
                            enc_static: bytes) -> None:
        """
        Sucht die Abweichung, wenn der Handschlag trotz korrektem mac1
        scheitert.

        Probiert die naheliegenden Varianten der Hash-Kette durch und meldet,
        welche funktioniert haette. Das ersetzt stundenlanges Vergleichen mit
        der Spezifikation durch eine Zeile im Protokoll.
        """
        import base64 as _b64
        shared = _dh(self.private, ephemeral)

        def try_variant(name: str, h_value: bytes, key_index: int = 1,
                        chain: bytes | None = None) -> bool:
            try:
                keys = _kdf(chain if chain is not None else _hash(CONSTRUCTION),
                            shared, 2)
                _aead_decrypt(keys[key_index], 0, enc_static, h_value)
                print(f"[wg] DIAGNOSE: Variante '{name}' haette funktioniert - "
                      f"HIER weicht die Umsetzung von WireGuard ab.")
                return True
            except Exception:
                return False

        base_c = _hash(CONSTRUCTION)
        h0 = _hash(base_c + IDENTIFIER)
        h_with_pub = _hash(h0 + self.public_raw)
        c1 = _kdf(base_c, ephemeral, 1)[0]

        variants = [
            ("Standard (unsere)", _hash(h_with_pub + ephemeral), 1, c1),
            ("ohne Server-Schluessel im Hash", _hash(h0 + ephemeral), 1, c1),
            ("Schluessel/Kette vertauscht", _hash(h_with_pub + ephemeral), 0, c1),
            ("ohne Ephemeral im Hash", h_with_pub, 1, c1),
            ("Kette ohne Ephemeral", _hash(h_with_pub + ephemeral), 1, base_c),
        ]
        found = any(try_variant(n, h, i, c) for n, h, i, c in variants)
        if not found:
            print(f"[wg] DIAGNOSE: Keine der geprueften Varianten passt. "
                  f"Damit stimmt der Diffie-Hellman nicht - die Gegenstelle "
                  f"rechnet mit einem anderen Server-Schluessel, obwohl mac1 "
                  f"passte. Unser oeffentlicher Schluessel ist "
                  f"{_b64.b64encode(self.public_raw).decode()}. Bitte mit dem "
                  f"'PublicKey' in der Tunnel-Datei vergleichen.")

    def replay(self, packet_hex: str) -> list[dict]:
        """
        Spielt ein Handschlag-Paket Schritt fuer Schritt durch und meldet
        nach JEDEM Schritt, ob er geklappt hat.

        Das ist die Antwort auf einen Widerspruch, der sich durch Nachlesen
        nicht aufloesen liess: mac1 besteht - die Gegenstelle kennt also
        unseren richtigen oeffentlichen Schluessel - und trotzdem scheitert
        die Entschluesselung. Beides zugleich ist rechnerisch unmoeglich.
        Also stimmt eine der beiden Beobachtungen nicht, und dieser
        Nachvollzug zeigt welche.
        """
        import base64 as _b64
        steps: list[dict] = []

        def add(name, ok, detail=""):
            steps.append({"schritt": name, "ok": bool(ok), "detail": detail})

        try:
            data = bytes.fromhex(packet_hex)
        except ValueError:
            add("Paket lesbar", False, "kein gueltiges Hex")
            return steps

        add("Laenge 148 Byte", len(data) == LEN_INITIATION, f"{len(data)} Byte")
        add("Typ 1 (Handschlag)", data[0] == MSG_INITIATION, f"Typ {data[0]}")
        if len(data) != LEN_INITIATION or data[0] != MSG_INITIATION:
            return steps

        expected = _mac(self.mac1_key, data[:116])
        got = data[116:132]
        add("mac1 stimmt", hmac.compare_digest(expected, got),
            f"erwartet {expected.hex()[:16]}…, erhalten {got.hex()[:16]}…")

        ephemeral = data[8:40]
        enc_static = data[40:88]
        enc_timestamp = data[88:116]
        add("Server-Schluessel", True,
            _b64.b64encode(self.public_raw).decode())

        try:
            shared = _dh(self.private, ephemeral)
            add("Diffie-Hellman", True, f"Ergebnis {shared.hex()[:16]}…")
        except Exception as e:
            add("Diffie-Hellman", False, str(e))
            return steps

        c = _hash(CONSTRUCTION)
        h = _hash(c + IDENTIFIER)
        h = _hash(h + self.public_raw)
        c = _kdf(c, ephemeral, 1)[0]
        h = _hash(h + ephemeral)
        c2, k = _kdf(c, shared, 2)
        try:
            peer_static = _aead_decrypt(k, 0, enc_static, h)
            add("Statisches Feld entschluesselt", True,
                _b64.b64encode(peer_static).decode())
        except Exception as e:
            add("Statisches Feld entschluesselt", False, f"{type(e).__name__}")
            add("Bekannte Gegenstellen", bool(self.peers),
                ", ".join(_b64.b64encode(p).decode()[:16] + "…"
                          for p in self.peers) or "keine")
            return steps

        known = peer_static in self.peers
        add("Gegenstelle bekannt", known,
            "" if known else "Dieser Schluessel ist hier nicht eingetragen - "
                             "Tunnel abgelaufen oder von einem anderen Server")
        if not known:
            return steps

        h2 = _hash(h + enc_static)
        c3, k2 = _kdf(c2, _dh(self.private, peer_static), 2)
        try:
            _aead_decrypt(k2, 0, enc_timestamp, h2)
            add("Zeitstempel entschluesselt", True)
        except Exception as e:
            add("Zeitstempel entschluesselt", False, f"{type(e).__name__}")
        return steps

    def selftest(self) -> dict:
        """
        Baut einen vollstaendigen Handschlag gegen sich selbst.

        Klappt der, arbeiten Krypto-Bibliothek und Protokoll-Umsetzung in
        DIESER Umgebung korrekt zusammen - dann liegt ein Problem woanders.
        Klappt er nicht, ist die Ursache gefunden, ohne dass ueberhaupt ein
        Client noetig waere.
        """
        import base64 as _b64
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        try:
            cp, cb = generate_keypair()
            probe_server = WireGuardServer(_b64.b64encode(_priv_raw(self.private)).decode())
            probe_server.peers = {}
            probe_server.add_peer(cb, None, "selftest")
            sent = []

            class _T:
                def sendto(self, d, a):
                    sent.append(d)

            probe_server.transport = _T()

            S = X25519PrivateKey.from_private_bytes(_b64.b64decode(cp))
            Sp, Rp = _pub_raw(S), self.public_raw
            e = X25519PrivateKey.generate()
            Ep = _pub_raw(e)
            c = _hash(CONSTRUCTION)
            h = _hash(c + IDENTIFIER)
            h = _hash(h + Rp)
            c = _kdf(c, Ep, 1)[0]
            h = _hash(h + Ep)
            c, k = _kdf(c, _dh(e, Rp), 2)
            es = _aead_encrypt(k, 0, Sp, h)
            h = _hash(h + es)
            c, k = _kdf(c, _dh(S, Rp), 2)
            et = _aead_encrypt(k, 0, _tai64n(), h)
            msg = bytes([1, 0, 0, 0]) + struct.pack("<I", 7) + Ep + es + et
            msg = msg + _mac(_hash(LABEL_MAC1 + Rp), msg) + b"\x00" * 16

            probe_server.datagram_received(msg, ("127.0.0.1", 1))
            ok = bool(sent) and sent[0][0] == MSG_RESPONSE
            return {
                "ok": ok,
                "meldung": ("Handschlag gegen die eigene Umsetzung erfolgreich - "
                            "Krypto und Protokoll arbeiten in dieser Umgebung "
                            "korrekt." if ok else
                            "Der Handschlag scheitert schon gegen die eigene "
                            "Umsetzung. Damit ist die Ursache hier im Server, "
                            "nicht beim Client."),
                "schritte": probe_server.replay(msg.hex()),
            }
        except Exception as e:
            return {"ok": False, "meldung": f"Selbsttest abgebrochen: {e}",
                    "schritte": []}

    def _send_response(self, peer: Peer, their_index: int, c: bytes, h: bytes,
                       their_ephemeral: bytes, addr) -> None:
        our_index = self._new_index()
        eph = X25519PrivateKey.generate()
        eph_pub = _pub_raw(eph)

        c = _kdf(c, eph_pub, 1)[0]
        h = _hash(h + eph_pub)
        c = _kdf(c, _dh(eph, their_ephemeral), 1)[0]
        c = _kdf(c, _dh(eph, peer.public_key), 1)[0]
        c, tau, k = _kdf(c, peer.preshared, 3)
        h = _hash(h + tau)
        enc_empty = _aead_encrypt(k, 0, b"", h)
        h = _hash(h + enc_empty)

        # Kopf ist: Typ(1) + reserviert(3) + sender(4) + receiver(4) = 12 Byte
        msg = (bytes([MSG_RESPONSE, 0, 0, 0])
               + struct.pack("<II", our_index, their_index)
               + eph_pub + enc_empty)
        mac1 = _mac(_hash(LABEL_MAC1 + peer.public_key), msg)
        msg = msg + mac1 + b"\x00" * 16

        # Transportschlüssel ableiten. Reihenfolge laut Whitepaper: für den
        # Responder ist der ERSTE Schlüssel der Empfangs-, der zweite der
        # Sendeschlüssel (beim Initiator genau andersherum).
        recv_key, send_key = _kdf(c, b"", 2)

        old = peer.session
        if old:
            self.sessions.pop(old.local_index, None)
        session = Session(our_index, their_index, send_key, recv_key, peer)
        peer.session = session
        peer.endpoint = addr
        peer.last_handshake = time.time()
        peer.last_seen = time.time()
        self.sessions[our_index] = session

        self.stats["handshakes"] += 1
        print(f"[wg] Handschlag erfolgreich mit {addr[0]}:{addr[1]} "
              f"(Tunnel {peer.tunnel_id})")
        if self.transport:
            self.transport.sendto(msg, addr)
        if self.on_handshake:
            try:
                self.on_handshake(peer)
            except Exception as e:
                print(f"[wg] Handshake-Rückruf fehlgeschlagen: {e!r}")

    def _new_index(self) -> int:
        while True:
            idx = struct.unpack("<I", os.urandom(4))[0]
            if idx and idx not in self.sessions:
                return idx

    # -- Transportdaten ------------------------------------------------

    def _handle_transport(self, data: bytes, addr) -> None:
        receiver, counter = struct.unpack("<IQ", data[4:16])
        session = self.sessions.get(receiver)
        if session is None:
            return
        if not session.accept_counter(counter):
            return
        try:
            plain = session.decrypt(counter, data[16:])
        except Exception:
            self.stats["undecryptable"] += 1
            return   # gefälscht oder beschädigt

        session.last_recv = time.monotonic()
        peer = session.peer
        peer.endpoint = addr          # Roaming: Gegenstelle darf die IP wechseln
        peer.last_seen = time.time()
        peer.rx_bytes += len(data)

        if not plain:
            return   # Keepalive (leeres Paket) - nichts zu tun

        # Auffüll-Bytes abschneiden: die echte Länge steht im IP-Kopf.
        real = _ip_total_length(plain)
        if real and real <= len(plain):
            plain = plain[:real]

        if self.on_packet:
            try:
                self.on_packet(peer, plain)
            except Exception as e:
                print(f"[wg] Paket-Rückruf fehlgeschlagen: {e!r}")

    # -- Senden --------------------------------------------------------

    def send_to_peer(self, peer: Peer, ip_packet: bytes) -> bool:
        """Verschlüsselt ein IP-Paket und schickt es an die Gegenstelle."""
        session = peer.session
        if not session or not peer.endpoint or not self.transport:
            return False
        try:
            self.transport.sendto(session.encrypt(ip_packet), peer.endpoint)
        except Exception:
            return False
        peer.tx_bytes += len(ip_packet)
        return True

    def cleanup(self) -> None:
        """Räumt tote Sitzungen weg (wird periodisch aufgerufen)."""
        for idx, s in list(self.sessions.items()):
            if s.expired():
                self.sessions.pop(idx, None)
                if s.peer.session is s:
                    s.peer.session = None


def _ip_total_length(packet: bytes) -> int:
    """Gesamtlänge aus dem IP-Kopf (v4 und v6), 0 wenn unklar."""
    if len(packet) < 20:
        return 0
    version = packet[0] >> 4
    if version == 4:
        return struct.unpack(">H", packet[2:4])[0]
    if version == 6 and len(packet) >= 40:
        return 40 + struct.unpack(">H", packet[4:6])[0]
    return 0

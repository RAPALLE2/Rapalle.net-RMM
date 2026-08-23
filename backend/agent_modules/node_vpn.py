"""
node_vpn.py  -  NODE-MODUL (wird nur auf Nodes nachgeladen)
-----------------------------------------------------------
Betreibt einen WireGuard-Endpunkt DIREKT auf der Node.

Der Unterschied zum Relay-Betrieb: Beim Relay endet der Tunnel im Backend,
und die Nutzdaten laufen als Socket.IO-Ereignisse zur Node weiter - das
Backend sieht dabei alles im Klartext. Hier endet der Tunnel auf der Node
selbst. Das Backend vermittelt nur die Schluessel und die Adressen; die
eigentlichen Daten gehen unmittelbar vom Benutzer zur Node.

Zwei Betriebsarten, die der Benutzer beim Ausstellen waehlt:

  mode = "client"  Nur das Geraet selbst ist erreichbar (seine eigene
                   Adresse und 127.0.0.1). Damit laesst sich z.B. ein
                   Dienst auf localhost:80 der Node oeffnen, ohne dass der
                   Benutzer irgendwo sonst hinkommt.

  mode = "site"    Das ganze Netz hinter der Node ist erreichbar.

WICHTIG: Die Betriebsart wird HIER durchgesetzt, nicht ueber AllowedIPs in
der .conf. AllowedIPs steht auf dem Rechner des Benutzers und laesst sich
dort in zehn Sekunden aendern - eine Zugriffsbeschraenkung, die man selbst
bearbeiten kann, ist keine.

Weiterleitung ins Netz: standardmaessig per NAT (der Verkehr traegt die
Adresse der Node). Ist die L2-Bruecke verfuegbar (siehe node_l2.py), kann
der Benutzer stattdessen eine echte Adresse aus dem LAN bekommen.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
import time

# Diese beiden Module liegen im selben Node-Modulordner.
from . import node_wireguard as wg
from .node_ipstack import IPStack


class NodeTunnel:
    """Ein einzelner Tunnel, der auf dieser Node endet."""

    def __init__(self, spec: dict, node):
        self.id = spec["id"]
        self.mode = spec.get("mode") or "client"
        self.address = spec.get("address") or ""
        self.allowed_targets = spec.get("targets") or []
        self.l2 = bool(spec.get("l2"))
        # Adresse, die auf der Gegenseite fuer "das Geraet selbst" steht.
        # 'localhost' kann der Benutzer nicht verwenden - das zeigt auf
        # SEINEN Rechner und geht nie durch den Tunnel.
        self.loopback_alias = spec.get("loopback_alias") or ""
        self.node = node
        self.peer = None
        self.stack = IPStack(self.id,
                             send_ip=self._to_peer,
                             agent_send=self._from_stack)
        self.streams: dict[str, socket.socket] = {}
        self.created = time.time()
        self.bytes_in = 0
        self.bytes_out = 0

    # -- Richtlinie ----------------------------------------------------

    def resolve(self, host: str) -> str:
        """Ersatzadresse -> echtes Ziel (127.0.0.1)."""
        return "127.0.0.1" if host and host == self.loopback_alias else host

    def target_allowed(self, host: str) -> bool:
        """
        Darf dieser Tunnel das angegebene Ziel ansprechen?

        Im Modus 'client' ist das genau das Geraet selbst. Im Modus 'site'
        alles, was NICHT ausdruecklich gesperrt ist - gesperrt sind die
        Adressen des Backends und das Tunnel-Netz selbst, damit der Tunnel
        nicht auf sich zurueckzeigt.
        """
        host = self.resolve(host)
        if self.mode == "client":
            return host in self.node.local_addresses
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        if ip.is_loopback and host not in self.node.local_addresses:
            return False
        if ip.is_multicast or ip.is_reserved:
            return False
        for blocked in self.node.blocked_networks:
            if ip in blocked:
                return False
        return True

    # -- Datenwege -----------------------------------------------------

    # -- Eingang aus dem Tunnel ---------------------------------------

    def on_tunnel_packet(self, packet: bytes) -> None:
        """
        Ein IP-Paket kam aus dem Tunnel. Zwei voellig verschiedene Wege:

        L2-BRUECKE: Das Paket geht UNVERAENDERT aufs Kabel, mit der echten
        Absenderadresse des Benutzers. Der userspace-TCP-Stack wird dabei
        komplett umgangen - er wuerde die Verbindung ja gerade beenden und
        neu aufbauen, also genau das NAT erzeugen, das hier vermieden
        werden soll.

        NAT: Der Stack fuehrt TCP selbst und baut die Verbindung mit der
        Adresse der Node neu auf.
        """
        if self.l2 and self.node.l2 and self.node.l2.available:
            if self.node.l2.inject(packet, self.id):
                self.bytes_in += len(packet)
                return
            # Einspeisen abgelehnt (z.B. fremde Absenderadresse) - dann
            # NICHT still auf NAT ausweichen: Das waere ein anderes
            # Verhalten als angekuendigt.
            return
        self.stack.on_tunnel_packet(packet)

    def from_bridge(self, packet: bytes) -> None:
        """Ein Paket aus dem LAN, das an die uebernommene Adresse ging."""
        self._to_peer(packet)

    def _to_peer(self, packet: bytes) -> None:
        if self.peer:
            self.node.server.send_to_peer(self.peer, packet)
            self.bytes_out += len(packet)

    def _from_stack(self, event: str, data: dict) -> None:
        """
        Der IP-Stack will etwas ins Netz schicken.

        Beim Relay-Betrieb ginge das ueber das Backend; hier machen wir es
        selbst - das ist der ganze Sinn des direkten Tunnels.
        """
        if event == "vpn-open":
            self._open(data)
        elif event == "vpn-data":
            self._send(data)
        elif event == "vpn-close":
            self._close(data.get("stream", ""))
        elif event == "vpn-udp":
            self._udp(data)
        elif event == "vpn-ping":
            self._ping(data)

    def _open(self, data: dict) -> None:
        stream = data.get("stream", "")
        host, port = data.get("host", ""), int(data.get("port") or 0)
        if not self.target_allowed(host):
            self.node.log(f"[node-vpn] {self.id}: {host}:{port} durch "
                          f"Betriebsart '{self.mode}' gesperrt")
            self.stack.on_agent_open_result(stream, False)
            return

        target = self.resolve(host)

        def run():
            try:
                sock = socket.create_connection((target, port), 8.0)
            except Exception:
                self.stack.on_agent_open_result(stream, False)
                return
            sock.settimeout(None)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            self.streams[stream] = sock
            self.stack.on_agent_open_result(stream, True)
            while True:
                try:
                    chunk = sock.recv(32768)
                except OSError:
                    break
                if not chunk:
                    break
                self.bytes_in += len(chunk)
                self.stack.on_agent_stream_data(stream, chunk)
            self._close(stream)
            self.stack.on_agent_stream_close(stream)

        threading.Thread(target=run, daemon=True).start()

    def _send(self, data: dict) -> None:
        import base64
        sock = self.streams.get(data.get("stream", ""))
        if not sock:
            return
        try:
            sock.sendall(base64.b64decode(data.get("data") or ""))
        except Exception:
            self._close(data.get("stream", ""))

    def _close(self, stream: str) -> None:
        sock = self.streams.pop(stream, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    def _udp(self, data: dict) -> None:
        import base64
        host, port = data.get("host", ""), int(data.get("port") or 0)
        if not self.target_allowed(host):
            return
        try:
            payload = base64.b64decode(data.get("data") or "")
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(6.0)
            sock.sendto(payload, (self.resolve(host), port))
            reply, _ = sock.recvfrom(65535)
            self.stack.on_agent_udp(host, port, data.get("src", ""),
                                    int(data.get("sport") or 0), reply)
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _ping(self, data: dict) -> None:
        host = self.resolve(data.get("host", ""))
        if not self.target_allowed(host):
            return
        import subprocess
        import sys
        try:
            cmd = (["ping", "-n", "1", "-w", "1500", host] if sys.platform == "win32"
                   else ["ping", "-c", "1", "-W", "2", host])
            ok = subprocess.run(cmd, capture_output=True, timeout=6).returncode == 0
        except Exception:
            ok = False
        self.stack.on_agent_ping_result(host, data.get("src", ""),
                                        int(data.get("ident") or 0),
                                        int(data.get("seq") or 0), ok,
                                        int(data.get("payload_len") or 32))

    def shutdown(self) -> None:
        for stream in list(self.streams):
            self._close(stream)
        try:
            self.stack.shutdown()
        except Exception:
            pass


class NodeVpn:
    """Der WireGuard-Endpunkt dieser Node."""

    def __init__(self, private_key: str, port: int, log=print):
        self.private_key = private_key
        self.port = port
        self.log = log
        self.server: wg.WireGuardServer | None = None
        self.transport = None
        self.tunnels: dict[str, NodeTunnel] = {}
        self.local_addresses: set[str] = {"127.0.0.1"}
        self.blocked_networks: list = []
        self.l2 = None            # node_l2.L2Bridge, falls verfuegbar
        self._probe_target = None  # (host, port) des Backends fuer die Probe

    # -- Start ---------------------------------------------------------

    async def start(self, local_ips: list[str], blocked: list[str]) -> bool:
        self.local_addresses = {"127.0.0.1", *[i for i in local_ips if i]}
        self.blocked_networks = []
        for net in blocked or []:
            try:
                self.blocked_networks.append(ipaddress.ip_network(net, strict=False))
            except ValueError:
                continue

        server = wg.WireGuardServer(self.private_key)
        loop = asyncio.get_event_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: server, local_addr=("0.0.0.0", self.port))
        except OSError as e:
            self.log(f"[node-vpn] UDP-Port {self.port} nicht verfuegbar: {e}")
            return False
        self.server, self.transport = server, transport
        server.on_packet = self._on_packet
        server.on_handshake = self._on_handshake
        asyncio.ensure_future(self._tick())
        self.log(f"[node-vpn] Endpunkt laeuft auf UDP {self.port}")
        return True

    def _on_packet(self, peer, packet: bytes) -> None:
        tun = self.tunnels.get(peer.tunnel_id)
        if tun:
            tun.peer = peer
            tun.on_tunnel_packet(packet)

    def _on_handshake(self, peer) -> None:
        tun = self.tunnels.get(peer.tunnel_id)
        if tun:
            tun.peer = peer
        self.log(f"[node-vpn] Handshake fuer Tunnel {peer.tunnel_id}")

    async def _tick(self) -> None:
        while self.server:
            try:
                for tun in list(self.tunnels.values()):
                    tun.stack.tick()
                self.server.cleanup()
            except Exception as e:
                self.log(f"[node-vpn] Tick: {e}")
            await asyncio.sleep(0.2)

    # -- Tunnel verwalten ---------------------------------------------

    def add_tunnel(self, spec: dict) -> None:
        """Das Backend teilt einen neuen Tunnel mit (Schluessel + Richtlinie)."""
        self.remove_tunnel(spec["id"])
        tun = NodeTunnel(spec, self)
        self.tunnels[spec["id"]] = tun
        if self.server:
            self.server.add_peer(spec["public_key"], spec.get("preshared_key"),
                                 spec["id"])
        # Echte LAN-Adresse: nur wenn die Bruecke wirklich laeuft. Sonst
        # bleibt es beim NAT-Betrieb - das ist der vereinbarte Rueckfall.
        if tun.l2 and self.l2 and self.l2.available:
            try:
                self.l2.claim(spec.get("lan_address") or "", tun.id)
            except Exception as e:
                self.log(f"[node-vpn] L2 fuer {tun.id} nicht moeglich: {e} "
                         f"- weiter im NAT-Betrieb")
                tun.l2 = False
        elif tun.l2:
            # Angefordert, aber keine Bruecke da: ehrlich vermerken, damit
            # das Dashboard nicht "L2" anzeigt, wo NAT laeuft.
            self.log(f"[node-vpn] L2 fuer {tun.id} angefordert, aber keine "
                     f"Bruecke aktiv - NAT-Betrieb")
            tun.l2 = False
        self.log(f"[node-vpn] Tunnel {spec['id']} aufgenommen "
                 f"(Modus {tun.mode}, {'L2' if tun.l2 else 'NAT'})")

    def remove_tunnel(self, tunnel_id: str) -> None:
        tun = self.tunnels.pop(tunnel_id, None)
        if not tun:
            return
        tun.shutdown()
        if self.server:
            peer = self.server.peer_for_tunnel(tunnel_id)
            if peer:
                import base64
                self.server.remove_peer(base64.b64encode(peer.public_key).decode())
        if tun.l2 and self.l2:
            try:
                self.l2.release(tunnel_id)
            except Exception:
                pass

    def attach_bridge(self, bridge) -> None:
        """
        Verbindet die L2-Bruecke mit diesem Endpunkt.

        Der Rueckruf ist der fehlende Draht: Ohne ihn faengt die Bruecke
        zwar Rahmen ein, weiss aber nicht, wohin damit. Genau daran fehlte
        es der ersten Fassung.
        """
        self.l2 = bridge
        bridge.send_to_tunnel = self._from_bridge

    def _from_bridge(self, tunnel_id: str, packet: bytes) -> None:
        tun = self.tunnels.get(tunnel_id)
        if tun:
            tun.from_bridge(packet)

    def stats(self) -> list[dict]:
        out = []
        for tun in self.tunnels.values():
            out.append({
                "id": tun.id, "mode": tun.mode, "l2": tun.l2,
                "connected": bool(tun.peer and tun.peer.session),
                "streams": len(tun.streams),
                "bytes_in": tun.bytes_in, "bytes_out": tun.bytes_out,
                "path": "l2" if tun.l2 else "nat",
            })
        return out

    # -- Erreichbarkeitsprobe -----------------------------------------

    def send_probe(self, host: str, port: int, token: str) -> None:
        """
        Schickt ein Probe-Paket an das Backend.

        Damit erfaehrt das Backend, unter welcher oeffentlichen Adresse und
        welchem Port diese Node von aussen erscheint - das ist die Adresse,
        die spaeter als 'Endpoint' in der .conf des Benutzers landet. Das
        Paket geht bewusst aus DEMSELBEN Socket wie der spaetere Tunnel;
        nur dann stimmt die NAT-Zuordnung, die das Backend beobachtet.
        """
        if not self.transport:
            return
        try:
            self.transport.sendto(b"RMMPROBE1" + token.encode()[:32],
                                  (host, port))
        except Exception as e:
            self.log(f"[node-vpn] Probe fehlgeschlagen: {e}")

    def shutdown(self) -> None:
        for tid in list(self.tunnels):
            self.remove_tunnel(tid)
        if self.transport:
            try:
                self.transport.close()
            except Exception:
                pass
        self.server = None

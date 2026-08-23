"""
vpn.py
------
Die Tunnel-Verwaltung: was ein "Tunnel" ist, wie lange er lebt, und wie die
Pakete zwischen WireGuard-Endpunkt (wireguard.py), IP-Stack (vpn_stack.py)
und dem Agenten hin und her wandern.

Der Weg eines Pakets, einmal komplett:

  WireGuard-App des Benutzers
      | verschlüsseltes UDP an SERVER:51820
      v
  wireguard.py  - entschlüsselt, liefert ein rohes IP-Paket
      v
  vpn_stack.py  - führt TCP/UDP/ICMP selbst und macht daraus einen Bytestrom
      v
  DIESE DATEI   - schickt den Strom als Socket.IO-Ereignis an den Agenten
      v
  agent.py      - öffnet eine ganz normale Python-Socket-Verbindung zum Ziel

Und zurück genauso, nur andersherum. Auf dem verwalteten Gerät läuft damit
KEIN WireGuard - dort ist es eine gewöhnliche ausgehende Verbindung.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import time
import uuid

from app import db, wireguard
from app.vpn_stack import IPStack

# ----------------------------------------------------------------------
# Einstellungen (alle über die Settings-Tabelle bzw. Umgebung änderbar)
# ----------------------------------------------------------------------
DEFAULT_SUBNET = "10.77.0.0/16"     # Adressbereich der Tunnel-Endpunkte
DEFAULT_PORT = 51820                # UDP-Port, auf dem WireGuard lauscht
DEFAULT_MTU = 1380                  # sichere MTU auch hinter DSL/PPPoE
DEFAULT_DNS = ""                    # optional: DNS-Server für den Tunnel


def _setting(key: str, default: str) -> str:
    try:
        return db.get_setting(key, default) or default
    except Exception:
        return default


def vpn_port() -> int:
    """UDP-Port des Endpunkts.

    Reihenfolge: Einstellung in der Oberfläche, sonst Umgebungsvariable
    VPN_PORT (die auch docker-compose für die Portfreigabe benutzt), sonst
    der WireGuard-Standard.
    """
    import os
    env_default = os.getenv("VPN_PORT") or str(DEFAULT_PORT)
    try:
        return int(_setting("vpn_port", env_default))
    except ValueError:
        return DEFAULT_PORT


def vpn_subnet() -> str:
    return _setting("vpn_subnet", DEFAULT_SUBNET)


def vpn_enabled() -> bool:
    return _setting("vpn_enabled", "1") == "1"


# ----------------------------------------------------------------------
# Zustand zur Laufzeit
# ----------------------------------------------------------------------

class _Runtime:
    def __init__(self):
        self.server: wireguard.WireGuardServer | None = None
        self.transport = None
        self.stacks: dict[str, IPStack] = {}      # tunnel_id -> Stack
        self.modes: dict[str, str] = {}           # tunnel_id -> 'client'|'site'
        self.stream_owner: dict[str, str] = {}    # stream_id -> tunnel_id
        self.started = False
        # Zahlen zu direkt endenden Tunneln, gemeldet von den Nodes.
        self.node_stats: dict[str, dict] = {}


rt = _Runtime()


# ----------------------------------------------------------------------
# Server-Schlüsselpaar (einmalig erzeugt, danach aus der DB)
# ----------------------------------------------------------------------

def server_keys() -> tuple[str, str]:
    """(privat, öffentlich) des Servers - beim ersten Aufruf erzeugt."""
    priv = _setting("vpn_server_private", "")
    if not priv:
        priv, pub = wireguard.generate_keypair()
        db.set_setting("vpn_server_private", priv)
        db.set_setting("vpn_server_public", pub)
        return priv, pub
    pub = _setting("vpn_server_public", "")
    if not pub:
        pub = wireguard.public_from_private(priv)
        db.set_setting("vpn_server_public", pub)
    return priv, pub


# ----------------------------------------------------------------------
# Adressvergabe
# ----------------------------------------------------------------------

def _allocate_ip() -> str:
    """
    Nächste freie Adresse aus dem Tunnel-Netz.

    .1 gehört dem Server (das ist das Gateway im Tunnel), ab .2 bekommen die
    Benutzer ihre Adresse. Belegt sind die Adressen aller noch nicht
    abgelaufenen Tunnel.
    """
    net = ipaddress.ip_network(vpn_subnet(), strict=False)
    used = {t["address"] for t in db.list_vpn_tunnels(active_only=True)}
    server_ip = str(next(net.hosts()))
    used.add(server_ip)
    for host in net.hosts():
        s = str(host)
        if s not in used:
            return s
    raise RuntimeError("Keine freie Tunnel-Adresse mehr im eingestellten Netz")


def server_tunnel_ip() -> str:
    net = ipaddress.ip_network(vpn_subnet(), strict=False)
    return str(next(net.hosts()))


# ----------------------------------------------------------------------
# Tunnel anlegen / beenden
# ----------------------------------------------------------------------

def create_tunnel(client_id: str, username: str, minutes: int,
                  name: str = "", routes: str = "",
                  host_override: str = "", mode: str = "client",
                  prefer_direct: bool = True, want_l2: bool = False,
                  lan_address: str = "") -> dict:
    """
    Legt einen Tunnel an und gibt den Datensatz samt fertiger
    Konfigurationsdatei zurück.

    'minutes' = 0 bedeutet unbegrenzt (das Recht dafür prüft die Route).
    'mode'    = 'client' (nur das Gerät selbst) oder 'site' (ganzes Netz).
    'routes'  = zusätzliche Netze, kommagetrennt.

    Transportweg: Ist der Client eine Node UND von aussen direkt erreichbar,
    endet der Tunnel auf der Node ('direct') - das Backend sieht dann keine
    Nutzdaten. Sonst endet er im Backend ('relay'). Das ist die vereinbarte
    Reihenfolge: direkt wenn möglich, sonst über das Backend.
    """
    client = db.get_client(client_id)
    if not client:
        raise ValueError("Client unbekannt")
    mode = "site" if mode == "site" else "client"

    priv_user, pub_user = wireguard.generate_keypair()
    psk = wireguard.generate_preshared_key()
    address = _allocate_ip()
    tunnel_id = uuid.uuid4().hex
    now_ms = int(time.time() * 1000)
    expires = now_ms + int(minutes) * 60_000 if minutes and minutes > 0 else 0

    transport, ep_host, ep_port, node_pub = _pick_transport(client, prefer_direct)
    l2_active = bool(want_l2 and transport == "direct" and _l2_ready(client_id))
    allowed = _allowed_ips(client, routes, mode)

    db.create_vpn_tunnel({
        "id": tunnel_id,
        "client_id": client_id,
        "name": name or f"{client.get('hostname') or client_id}",
        "username": username,
        "address": address,
        "public_key": pub_user,
        "preshared_key": psk,
        "allowed_ips": allowed,
        "created_at": now_ms,
        "expires_at": expires,
        "mode": mode,
        "transport": transport,
        "l2": 1 if l2_active else 0,
    })

    if transport == "direct":
        # Die Node bekommt Schlüssel und Richtlinie; die Nutzdaten laufen
        # danach an diesem Backend vorbei.
        _push_tunnel_to_node(client_id, {
            "id": tunnel_id, "mode": mode, "address": address,
            "public_key": pub_user, "preshared_key": psk,
            "l2": l2_active, "lan_address": lan_address,
            "targets": [client.get("ip") or ""],
        })
    else:
        _activate(tunnel_id, pub_user, psk, client_id, mode)

    config = build_config(
        private_key=priv_user, address=address, psk=psk,
        allowed_ips=allowed, host_override=host_override or ep_host,
        peer_public_key=node_pub, port_override=ep_port,
    )
    record = db.get_vpn_tunnel(tunnel_id)
    record["config"] = config
    record["transport"] = transport
    record["l2_active"] = l2_active
    # Der private Schlüssel wird BEWUSST nicht gespeichert: er existiert nur
    # in dieser einen Antwort. Wer die Datei verliert, stellt einen neuen
    # Tunnel aus - das ist sicherer, als den Schlüssel vorzuhalten.
    record["private_key_once"] = priv_user
    return record


def _pick_transport(client: dict, prefer_direct: bool) -> tuple[str, str, int, str]:
    """
    Entscheidet, WO der Tunnel endet.

    Rückgabe: (transport, host, port, öffentlicher Schlüssel der Gegenstelle)

    'direct' setzt drei Dinge voraus: der Client ist eine Node, sie hat ein
    Node-Modul mit eigenem Schlüssel, und das Backend hat sie schon einmal
    von aussen gesehen (node_endpoint aus dem Probe-Paket). Fehlt eines
    davon, ist 'relay' die richtige Antwort - nicht ein Fehler.
    """
    if prefer_direct and client.get("is_node") and client.get("node_endpoint"):
        try:
            from app import node_manager
            host, _, port = str(client["node_endpoint"]).rpartition(":")
            _priv, pub = node_manager.node_keys(client["id"])
            if host and port.isdigit() and pub:
                return "direct", host, int(port), pub
        except Exception as e:
            print(f"[vpn] Direktbetrieb nicht möglich, weiche auf das "
                  f"Backend aus: {e}")
    return "relay", endpoint_host(), vpn_port(), server_keys()[1]


def _l2_ready(client_id: str) -> bool:
    """Läuft auf dieser Node wirklich eine L2-Brücke?"""
    try:
        from app import node_manager
        return bool((node_manager.node_info(client_id).get("caps") or {}).get("l2"))
    except Exception:
        return False


def _push_tunnel_to_node(client_id: str, spec: dict) -> None:
    """Übergibt Schlüssel und Richtlinie eines Tunnels an die Node."""
    try:
        from app.sockets import send_to_agent
        asyncio.ensure_future(send_to_agent(client_id, "node-tunnel-add", spec))
    except Exception as e:
        print(f"[vpn] Tunnel konnte nicht an {client_id} übergeben werden: {e}")


def _allowed_ips(client: dict, routes: str, mode: str = "client") -> str:
    """
    Welche Ziele soll der WireGuard-Client durch den Tunnel schicken?

    Das ist eine ROUTING-Angabe, keine Zugriffsbeschränkung: Sie steht in
    der Datei auf dem Rechner des Benutzers und lässt sich dort ändern. Die
    eigentliche Beschränkung auf 'nur dieses Gerät' setzt die Gegenstelle
    durch (node_vpn.NodeTunnel.target_allowed bzw. _target_allowed hier).
    """
    parts = []
    ip = (client.get("ip") or "").strip()
    if ip:
        parts.append(f"{ip}/32")
    if mode == "site":
        # Ganzes Netz: aus der Adresse des Geräts das umgebende /24 ableiten.
        # Das trifft die übliche Heimat-/Büro-Aufteilung; abweichende Netze
        # trägt man über 'routes' nach.
        try:
            parts.append(str(ipaddress.ip_network(f"{ip}/24", strict=False)))
        except ValueError:
            pass
    for r in (routes or "").replace(";", ",").split(","):
        r = r.strip()
        if not r:
            continue
        try:
            parts.append(str(ipaddress.ip_network(r, strict=False)))
        except ValueError:
            continue
    if not parts:
        # Ohne bekannte Adresse bleibt nur das Tunnel-Netz selbst - der
        # Benutzer kann die Route später von Hand in der .conf ergänzen.
        parts.append(vpn_subnet())
    # Doppelte entfernen, Reihenfolge beibehalten.
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return ", ".join(out)


def endpoint_host() -> str:
    """
    Der Hostname, den der WireGuard-Client in der .conf ansteuert.

    Reihenfolge - bewusst dieselbe Kette wie bei den Install-Befehlen und
    beim Explorer-Relay, damit es nur EINE Stelle gibt, an der die Adresse
    der Installation gepflegt wird:

      1. vpn_endpoint_host  - ausdrücklich für das VPN gesetzt. Das ist der
         Regelfall hinter einem Reverse-Proxy: der Proxy reicht kein UDP
         durch, der Tunnel-Port zeigt also direkt auf den Server und damit
         oft auf eine andere Adresse als das Dashboard.
      2. server_url         - vollständige URL, davon nur der Host.
      3. server_domain, sonst server_host.

    Nur der HOST wird übernommen, nie Schema oder Port: WireGuard spricht
    UDP auf einem eigenen Port, der HTTP-Port des Dashboards ist hier
    bedeutungslos.
    """
    explicit = _setting("vpn_endpoint_host", "").strip()
    if explicit:
        return explicit.replace("https://", "").replace("http://", "").strip("/").split("/")[0]

    url = _setting("server_url", "").strip()
    if url:
        host = url.replace("https://", "").replace("http://", "").strip("/").split("/")[0]
        # Port abtrennen (IPv6 in eckigen Klammern beachten).
        if host.startswith("["):
            return host.split("]")[0] + "]"
        return host.split(":")[0]

    return (_setting("server_domain", "").strip()
            or _setting("server_host", "").strip())


def build_config(private_key: str, address: str, psk: str, allowed_ips: str,
                 host_override: str = "", peer_public_key: str = "",
                 port_override: int = 0) -> str:
    """
    Erzeugt den Inhalt der .conf-Datei für den WireGuard-Client.

    'peer_public_key' ist der Schlüssel der Gegenstelle: beim Direktbetrieb
    der der Node, sonst der des Backends. Ein falscher Schlüssel hier
    scheitert später stumm im Handshake - deshalb kommt er aus derselben
    Entscheidung wie die Endpunkt-Adresse.
    """
    server_pub = peer_public_key or server_keys()[1]
    host = host_override or endpoint_host()
    port = port_override or vpn_port()
    dns = _setting("vpn_dns", DEFAULT_DNS)
    mtu = _setting("vpn_mtu", str(DEFAULT_MTU))

    lines = [
        "# RAPALLE.net RMM - VPN-Tunnel",
        "# Diese Datei in einen beliebigen WireGuard-Client importieren.",
        "# Auf dem Zielgeraet selbst ist KEINE Installation noetig.",
        "",
        "[Interface]",
        f"PrivateKey = {private_key}",
        f"Address = {address}/32",
        f"MTU = {mtu}",
    ]
    if dns:
        lines.append(f"DNS = {dns}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {server_pub}",
        f"PresharedKey = {psk}",
        f"AllowedIPs = {allowed_ips}",
        f"Endpoint = {host or 'SERVER-ADRESSE-EINTRAGEN'}:{port}",
        "PersistentKeepalive = 25",
        "",
    ]
    return "\n".join(lines)


def revoke_tunnel(tunnel_id: str) -> bool:
    row = db.get_vpn_tunnel(tunnel_id)
    if not row:
        return False
    db.close_vpn_tunnel(tunnel_id)
    if (row.get("transport") or "relay") == "direct":
        # Beim Direktbetrieb liegt der Schlüssel auf der Node - nur sie kann
        # den Tunnel wirklich schliessen.
        try:
            from app.sockets import send_to_agent
            asyncio.ensure_future(send_to_agent(
                row["client_id"], "node-tunnel-remove", {"id": tunnel_id}))
        except Exception as e:
            print(f"[vpn] Node konnte nicht benachrichtigt werden: {e}")
    else:
        _deactivate(tunnel_id, row.get("public_key"))
    return True


# ----------------------------------------------------------------------
# Aktivieren/Deaktivieren im laufenden Server
# ----------------------------------------------------------------------

def _activate(tunnel_id: str, public_key: str, psk: str, client_id: str,
              mode: str = "client") -> None:
    if not rt.server:
        return
    rt.server.add_peer(public_key, psk, tunnel_id)
    rt.modes[tunnel_id] = mode
    rt.stacks[tunnel_id] = IPStack(
        tunnel_id,
        send_ip=lambda pkt, tid=tunnel_id: _send_into_tunnel(tid, pkt),
        agent_send=lambda ev, data, cid=client_id, tid=tunnel_id:
            _to_agent_checked(cid, tid, ev, data),
    )


def _target_allowed(tunnel_id: str, client_id: str, host: str) -> bool:
    """
    Betriebsart auch im Relay-Fall durchsetzen.

    Im Modus 'client' darf ein Tunnel ausschliesslich das Gerät selbst
    ansprechen. Ohne diese Prüfung wäre die Beschränkung reine Kosmetik:
    AllowedIPs steht in der Datei des Benutzers und ist dort änderbar.
    """
    if rt.modes.get(tunnel_id, "client") != "client":
        return True
    client = db.get_client(client_id) or {}
    return host in {client.get("ip") or "", "127.0.0.1"}


def _to_agent_checked(client_id: str, tunnel_id: str, event: str,
                      data: dict) -> None:
    host = data.get("host")
    if host and not _target_allowed(tunnel_id, client_id, host):
        # Das Zurückweisen muss der Stack erfahren, sonst wartet der Browser
        # des Benutzers bis zum Zeitlimit auf eine Antwort, die nie kommt.
        if event == "vpn-open":
            stack = rt.stacks.get(tunnel_id)
            if stack:
                stack.on_agent_open_result(data.get("stream", ""), False)
        return
    _to_agent(client_id, event, data)


def _deactivate(tunnel_id: str, public_key: str | None) -> None:
    rt.modes.pop(tunnel_id, None)
    stack = rt.stacks.pop(tunnel_id, None)
    if stack:
        try:
            stack.shutdown()
        except Exception:
            pass
        for sid in list(rt.stream_owner):
            if rt.stream_owner[sid] == tunnel_id:
                rt.stream_owner.pop(sid, None)
    if rt.server and public_key:
        rt.server.remove_peer(public_key)


def _send_into_tunnel(tunnel_id: str, packet: bytes) -> None:
    if not rt.server:
        return
    peer = rt.server.peer_for_tunnel(tunnel_id)
    if peer:
        rt.server.send_to_peer(peer, packet)


def _to_agent(client_id: str, event: str, data: dict) -> None:
    """Ereignis an den Agenten schicken (aus synchronem Code heraus)."""
    stream = data.get("stream")
    if stream:
        rt.stream_owner[stream] = data.get("tunnel", "")
    try:
        from app.sockets import send_to_agent
        asyncio.ensure_future(send_to_agent(client_id, event, data))
    except Exception as e:
        print(f"[vpn] Konnte '{event}' nicht an {client_id} senden: {e}")


# ----------------------------------------------------------------------
# Rückweg: Meldungen des Agenten (aufgerufen aus sockets.py)
# ----------------------------------------------------------------------

def _stack_for(payload: dict) -> IPStack | None:
    tid = payload.get("tunnel") or rt.stream_owner.get(payload.get("stream", ""))
    return rt.stacks.get(tid or "")


def on_agent_open_result(payload: dict) -> None:
    stack = _stack_for(payload)
    if stack:
        stack.on_agent_open_result(payload.get("stream", ""),
                                   bool(payload.get("ok")))


def on_agent_data(payload: dict) -> None:
    stack = _stack_for(payload)
    if not stack:
        return
    try:
        raw = base64.b64decode(payload.get("data") or "")
    except Exception:
        return
    stack.on_agent_stream_data(payload.get("stream", ""), raw)


def on_agent_close(payload: dict) -> None:
    stack = _stack_for(payload)
    if stack:
        stack.on_agent_stream_close(payload.get("stream", ""))


def on_agent_udp(payload: dict) -> None:
    stack = _stack_for(payload)
    if not stack:
        return
    try:
        raw = base64.b64decode(payload.get("data") or "")
    except Exception:
        return
    stack.on_agent_udp(payload.get("host", ""), int(payload.get("port") or 0),
                       payload.get("dst", ""), int(payload.get("dport") or 0),
                       raw)


def on_agent_ping(payload: dict) -> None:
    stack = _stack_for(payload)
    if stack:
        stack.on_agent_ping_result(
            payload.get("host", ""), payload.get("src", ""),
            int(payload.get("ident") or 0), int(payload.get("seq") or 0),
            bool(payload.get("ok")), int(payload.get("payload_len") or 32))


# ----------------------------------------------------------------------
# Start / Hintergrundschleifen
# ----------------------------------------------------------------------

async def start() -> None:
    """Startet den UDP-Endpunkt und lädt die noch gültigen Tunnel."""
    if rt.started or not vpn_enabled():
        return
    priv, _pub = server_keys()
    server = wireguard.WireGuardServer(priv)
    port = vpn_port()
    loop = asyncio.get_event_loop()
    try:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: server, local_addr=("0.0.0.0", port))
    except OSError as e:
        print(f"[vpn] UDP-Port {port} nicht verfügbar: {e} - VPN bleibt aus")
        return
    rt.server, rt.transport, rt.started = server, transport, True

    server.on_packet = _on_wg_packet
    server.on_handshake = _on_wg_handshake
    server.on_probe = _on_node_probe

    for row in db.list_vpn_tunnels(active_only=True):
        # Direkt endende Tunnel gehören der Node - das Backend hält für sie
        # keinen Endpunkt vor.
        if (row.get("transport") or "relay") == "direct":
            continue
        _activate(row["id"], row["public_key"], row["preshared_key"],
                  row["client_id"], row.get("mode") or "client")
    print(f"[vpn] WireGuard-Endpunkt lauscht auf UDP {port} "
          f"({len(rt.stacks)} aktive Tunnel geladen)")

    # Beide Schleifen unter Aufsicht: Stirbt eine an einer Ausnahme, läuft
    # das VPN sonst still weiter, ohne Wiederholungen und ohne Ablauf - und
    # niemand merkt es, bis ein Tunnel nicht mehr funktioniert.
    try:
        from app.main import _supervise
        _supervise("vpn-tick", _tick_loop)
        _supervise("vpn-ablauf", _expiry_loop)
    except Exception:
        # Fällt der Aufseher aus (z.B. anderer Startweg), lieber ungesichert
        # laufen als gar nicht.
        asyncio.ensure_future(_tick_loop())
        asyncio.ensure_future(_expiry_loop())


def _on_wg_packet(peer, packet: bytes) -> None:
    stack = rt.stacks.get(peer.tunnel_id)
    if stack:
        stack.on_tunnel_packet(packet)


def _on_node_probe(token: str, addr) -> None:
    """
    Eine Node hat ihre Erreichbarkeit geprüft.

    Die hier beobachtete Absenderadresse ist genau die, unter der die Node
    von aussen erscheint - inklusive des Ports, den ihr NAT vergeben hat.
    Sie wandert als 'Endpoint' in die .conf der nächsten Tunnel. Ändert das
    NAT die Zuordnung, meldet die Node beim nächsten Probe-Lauf eine neue
    Adresse; bereits ausgestellte Tunnel greifen dann nicht mehr und müssen
    neu ausgestellt werden. Das ist die Einschränkung, die Hole-Punching
    unvermeidlich mitbringt.
    """
    client_id = None
    try:
        from app import node_manager
        client_id = node_manager.client_for_probe_token(token)
    except Exception:
        pass
    if not client_id:
        return
    endpoint = f"{addr[0]}:{addr[1]}"
    previous = (db.get_client(client_id) or {}).get("node_endpoint")
    db.set_node_endpoint(client_id, endpoint)
    if previous != endpoint:
        print(f"[vpn] Node {client_id} ist direkt erreichbar unter {endpoint}")


def _on_wg_handshake(peer) -> None:
    try:
        db.touch_vpn_tunnel(peer.tunnel_id, int(time.time() * 1000))
    except Exception:
        pass


async def _tick_loop() -> None:
    """
    Wiederholungen und Aufräumen im TCP-Zustandsautomaten.

    Ohne offene Tunnel wird der Takt bewusst grob: Fünfmal pro Sekunde
    aufzuwachen, um festzustellen, dass nichts zu tun ist, ist auf einem
    Server, der monatelang läuft, reine Verschwendung - und einer von vielen
    kleinen Beiträgen zur Grundlast.
    """
    idle_ticks = 0
    while rt.started:
        try:
            if rt.stacks:
                idle_ticks = 0
                for stack in list(rt.stacks.values()):
                    stack.tick()
                if rt.server:
                    rt.server.cleanup()
                await asyncio.sleep(0.2)
                continue
            # Nichts zu tun: nur noch jede Sekunde nachsehen, und das
            # Aufräumen alter Sitzungen alle fünf Sekunden.
            idle_ticks += 1
            if rt.server and idle_ticks % 5 == 0:
                rt.server.cleanup()
            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"[vpn] Tick-Fehler: {e}")
            await asyncio.sleep(1.0)


async def _expiry_loop() -> None:
    """Schliesst abgelaufene Tunnel - wie beim Explorer-Relay, minütlich."""
    await asyncio.sleep(20)
    while rt.started:
        try:
            now_ms = int(time.time() * 1000)
            for row in db.list_expired_vpn_tunnels(now_ms):
                db.close_vpn_tunnel(row["id"])
                _deactivate(row["id"], row.get("public_key"))
                db.add_audit_entry("system", "vpn.auto_closed",
                                   target=row.get("client_id"),
                                   details=f"Tunnel '{row.get('name')}' abgelaufen")
                try:
                    from app.sockets import sio
                    await sio.emit("vpn-changed",
                                   {"tunnel_id": row["id"], "auto": True},
                                   namespace="/dashboard")
                except Exception:
                    pass
        except Exception as e:
            print(f"[vpn] Ablauf-Schleife: {e}")
        await asyncio.sleep(60)


# ----------------------------------------------------------------------
# Übersicht für das Dashboard
# ----------------------------------------------------------------------

def apply_node_stats(client_id: str, stats: list) -> None:
    """Übernimmt die Tunnel-Zahlen, die eine Node über sich meldet."""
    for entry in stats or []:
        tid = entry.get("id")
        if tid:
            rt.node_stats[tid] = entry


def tunnel_overview() -> list[dict]:
    """Alle offenen Tunnel mit Live-Zahlen aus dem laufenden Server."""
    out = []
    for row in db.list_vpn_tunnels(active_only=True):
        item = dict(row)
        item.pop("preshared_key", None)
        if (row.get("transport") or "relay") == "direct":
            # Bei direkten Tunneln laufen die Daten am Backend vorbei - die
            # Zahlen können deshalb nur von der Node kommen. Sie meldet sie
            # mit ihrem Node-Bericht; bis dahin bleiben sie leer. Das ist
            # der Preis dafür, dass hier nichts mitgelesen wird.
            live = rt.node_stats.get(row["id"]) or {}
            item.update({
                "connected": bool(live.get("connected")),
                "endpoint": (db.get_client(row["client_id"]) or {}).get("node_endpoint") or "",
                "rx_bytes": live.get("bytes_in") or 0,
                "tx_bytes": live.get("bytes_out") or 0,
                "streams": live.get("streams") or 0,
                "last_handshake": row.get("last_handshake") or 0,
            })
            out.append(item)
            continue
        stack = rt.stacks.get(row["id"])
        peer = rt.server.peer_for_tunnel(row["id"]) if rt.server else None
        item["connected"] = bool(peer and peer.session)
        item["last_handshake"] = int(peer.last_handshake * 1000) if peer and peer.last_handshake else 0
        item["endpoint"] = f"{peer.endpoint[0]}:{peer.endpoint[1]}" if peer and peer.endpoint else ""
        item["rx_bytes"] = peer.rx_bytes if peer else 0
        item["tx_bytes"] = peer.tx_bytes if peer else 0
        item["streams"] = len(stack.tcp) if stack else 0
        out.append(item)
    return out

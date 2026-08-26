"""
vpn.py
======
Die Tunnel-Verwaltung. Drei Betriebsarten, ein gemeinsamer Unterbau.

    PEER-TO-PEER    Der Tunnel endet auf der NODE. Der Benutzer verbindet
                    sich direkt dorthin und erreicht genau dieses Gerät.
                    Das Backend vermittelt nur Schlüssel und Adressen -
                    die Nutzdaten laufen daran vorbei.

    SITE-TO-SITE    Wie Peer-to-Peer, aber die Node reicht zusätzlich ihr
                    ganzes Netz durch. Der Verkehr bekommt dabei die
                    Adresse der Node als Absender (NAT).

    VIRTUELLES NETZ Stern um das Backend. Jedes Gerät und jeder Benutzer
                    hat eine feste Adresse und ist nur mit dem Backend
                    verbunden; das Backend routet dazwischen und macht DNS.

Warum die Krypto nicht mehr selbst geschrieben ist
--------------------------------------------------
Eine frühere Fassung setzte das WireGuard-Protokoll in Python um. Sie
bestand jeden Selbsttest und scheiterte trotzdem reproduzierbar am
Handschlag echter Clients - der Fehler liess sich über viele Durchgänge
nicht finden. Krypto selbst zu schreiben ist genau die Aufgabe, bei der
sich ein Fehler nicht als Fehlermeldung zeigt, sondern als "geht nicht".

Jetzt macht das die Referenzumsetzung: auf der Node echtes WireGuard
(Kernelmodul oder wireguard-go), im Backend wireguard-go. Was hier bleibt,
ist die Verwaltung - und die ist überprüfbar.

Erreichbarkeit ohne offene Ports
--------------------------------
Nodes hängen hinter NAT. Sieht das Backend eine Node von aussen, wird die
beobachtete Adresse direkt in die .conf geschrieben. Geht das nicht -
etwa bei symmetrischem NAT -, läuft die Verbindung über den Relay
(wg_relay.py). Welcher Weg trägt, steht in der Oberfläche.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import time
import uuid

from app import db, vpn_net, wg_relay
from app.errors import Codes, report

DEFAULT_SUBNET = "10.77.0.0/16"
DEFAULT_PORT = 51820
DEFAULT_MTU = 1380
DEFAULT_NODE_PORT = 51822

MODE_PEER = "peer"
MODE_SITE = "site"
MODE_NET = "net"

MODES = {
    MODE_PEER: "Peer-to-Peer – direkt zu diesem Gerät",
    MODE_SITE: "Site-to-Site – das ganze Netz dahinter",
    MODE_NET: "Virtuelles Netz – alle Geräte über das Backend",
}


def _setting(key: str, default: str) -> str:
    try:
        return db.get_setting(key, default) or default
    except Exception:
        return default


def vpn_port() -> int:
    import os
    try:
        return int(_setting("vpn_port", os.getenv("VPN_PORT") or str(DEFAULT_PORT)))
    except ValueError:
        return DEFAULT_PORT


def node_port() -> int:
    try:
        return int(_setting("vpn_node_port", str(DEFAULT_NODE_PORT)))
    except ValueError:
        return DEFAULT_NODE_PORT


def vpn_subnet() -> str:
    return _setting("vpn_subnet", DEFAULT_SUBNET)


def vpn_enabled() -> bool:
    return _setting("vpn_enabled", "1") == "1"


def endpoint_host() -> str:
    """
    Adresse, unter der das Backend von aussen erreichbar ist.

    Immer dieselbe Reihenfolge wie im übrigen Projekt, damit es nur EINE
    Stelle gibt, an der die Adresse gepflegt wird. Nur der Host, nie ein
    Port - WireGuard und der Relay haben eigene.
    """
    explicit = _setting("vpn_endpoint_host", "").strip()
    if explicit:
        return _host_only(explicit)
    url = _setting("server_url", "").strip()
    if url:
        return _host_only(url)
    return (_setting("server_domain", "").strip()
            or _setting("server_host", "").strip())


def _host_only(value: str) -> str:
    host = value.replace("https://", "").replace("http://", "").strip("/")
    host = host.split("/")[0]
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.split(":")[0]


class _Runtime:
    def __init__(self):
        self.wg = None                       # wireguard-go im Backend
        self.engine = "aus"
        self.started = False
        self.relay_tokens: dict[str, str] = {}
        self.node_state: dict[str, dict] = {}


rt = _Runtime()


def server_keys() -> tuple[str, str]:
    """Schlüsselpaar des Backends. Einmalig erzeugt, danach aus der DB."""
    from app import wg_keys
    priv = _setting("vpn_server_private", "")
    if not priv:
        priv, pub = wg_keys.generate()
        db.set_setting("vpn_server_private", priv)
        db.set_setting("vpn_server_public", pub)
        return priv, pub
    pub = wg_keys.public_from_private(priv)
    if pub != _setting("vpn_server_public", ""):
        # Beide MÜSSEN zusammenpassen: Der öffentliche landet in jeder
        # ausgestellten Datei. Passen sie nicht, ist jede davon unbrauchbar,
        # und im Protokoll steht nur ein nichtssagender Handshake-Fehler.
        db.set_setting("vpn_server_public", pub)
    return priv, pub


def node_keys(client_id: str) -> tuple[str, str]:
    """Eigenes Schlüsselpaar je Node - nie ein gemeinsames für alle."""
    from app import node_manager
    return node_manager.node_keys(client_id)


# ----------------------------------------------------------------------
# Tunnel ausstellen
# ----------------------------------------------------------------------

def create_tunnel(client_id: str, username: str, minutes: int,
                  mode: str = MODE_PEER, name: str = "",
                  routes: str = "") -> dict:
    """Stellt einen Tunnel aus und gibt den Datensatz samt .conf zurück."""
    client = db.get_client(client_id)
    if not client:
        raise ValueError("Client unbekannt")
    if mode not in MODES:
        mode = MODE_PEER

    from app import wg_keys
    priv_user, pub_user = wg_keys.generate()
    psk = wg_keys.generate_psk()
    tunnel_id = uuid.uuid4().hex
    now_ms = int(time.time() * 1000)
    expires = now_ms + int(minutes) * 60_000 if minutes and minutes > 0 else 0

    address = vpn_net.user_address(tunnel_id, username)
    vpn_net.client_address(client_id)

    if mode == MODE_NET:
        peer_pub = server_keys()[1]
        endpoint, transport, relay_token = _backend_endpoint()
    else:
        peer_pub = node_keys(client_id)[1]
        endpoint, transport, relay_token = _node_endpoint(client_id, tunnel_id)

    allowed = _allowed_ips(client, mode, routes)

    db.create_vpn_tunnel({
        "id": tunnel_id, "client_id": client_id,
        "name": name or (client.get("hostname") or client_id),
        "username": username, "address": address,
        "public_key": pub_user, "preshared_key": psk,
        "allowed_ips": allowed, "created_at": now_ms, "expires_at": expires,
        "mode": mode, "transport": transport, "l2": 0,
    })
    if relay_token:
        rt.relay_tokens[tunnel_id] = relay_token

    if mode == MODE_NET:
        _activate_backend_peer(tunnel_id, pub_user, psk, address)
    else:
        _push_to_node(client_id, {
            "tunnel": tunnel_id, "mode": mode,
            "public_key": pub_user, "preshared_key": psk,
            "allowed_ips": f"{address}/32",
            "routes": [r.strip() for r in allowed.split(",") if "/" in r],
            "relay_token": relay_token,
        })

    record = db.get_vpn_tunnel(tunnel_id)
    record.update({
        "config": build_config(priv_user, address, psk, allowed, peer_pub,
                               endpoint, now_ms, expires, mode),
        "private_key_once": priv_user,
        "transport": transport,
        "endpoint": endpoint,
        "mode_label": MODES[mode],
    })
    return record


def _backend_endpoint() -> tuple[str, str, str]:
    """Gegenstelle ist das Backend (virtuelles Netz)."""
    return f"{endpoint_host()}:{vpn_port()}", "backend", ""


def _node_endpoint(client_id: str, tunnel_id: str) -> tuple[str, str, str]:
    """
    Gegenstelle ist die Node.

    Direkt, wenn das Backend sie schon einmal von aussen gesehen hat -
    sonst über den Relay. Der Relay ist kein Notbehelf, sondern der
    vorgesehene Weg für alles hinter symmetrischem NAT.
    """
    state = rt.node_state.get(client_id) or {}
    direct = state.get("public_endpoint") or ""
    if direct:
        return direct, "direkt", ""
    try:
        token = wg_relay.open_session(tunnel_id, client_id)
    except Exception as e:
        report(Codes.VPN_ENDPOINT, e, "Relay-Sitzung anlegen", client=client_id)
        return f"{endpoint_host()}:{node_port()}", "unbekannt", ""
    return f"{endpoint_host()}:{wg_relay.relay_port()}", "relay", token


def _allowed_ips(client: dict, mode: str, routes: str) -> str:
    """Was der WireGuard-Client durch den Tunnel schicken soll."""
    parts: list[str] = []
    tunnel_net = ipaddress.ip_network(vpn_subnet(), strict=False)

    if mode == MODE_NET:
        parts.append(str(tunnel_net))
    else:
        parts.append(f"{vpn_net.client_address(client['id'])}/32")

    if mode == MODE_SITE:
        gemeldet = []
        try:
            gemeldet = db.get_client_subnets(client.get("id") or "")
        except Exception:
            pass
        for net in gemeldet:
            try:
                parsed = ipaddress.ip_network(net, strict=False)
            except ValueError:
                continue
            if parsed.overlaps(tunnel_net):
                continue          # keine Route auf das Tunnelnetz selbst
            parts.append(str(parsed))
        if not gemeldet and client.get("ip"):
            # Rückfall, solange die Node ihre Netze noch nicht gemeldet hat.
            try:
                parts.append(str(ipaddress.ip_network(
                    f"{client['ip']}/24", strict=False)))
            except ValueError:
                pass

    for extra in (routes or "").replace(";", ",").split(","):
        extra = extra.strip()
        if not extra:
            continue
        try:
            parts.append(str(ipaddress.ip_network(extra, strict=False)))
        except ValueError:
            continue

    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return ", ".join(out)


def build_config(private_key: str, address: str, psk: str, allowed_ips: str,
                 peer_public_key: str, endpoint: str, created_at: int,
                 expires_at: int, mode: str) -> str:
    """Der Inhalt der .conf-Datei."""
    mtu = _setting("vpn_mtu", str(DEFAULT_MTU))
    dns = _setting("vpn_dns", "")
    if not dns and mode == MODE_NET and _setting("vpn_use_internal_dns", "1") == "1":
        # Namen im virtuellen Netz auflösen. Nur dort sinnvoll - nur dort
        # ist das Backend die Gegenstelle.
        dns = vpn_net.router_address()

    def stamp(ms: int) -> str:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(ms / 1000))

    lines = [
        "# RAPALLE.net RMM - VPN-Tunnel",
        f"# Art:          {MODES.get(mode, mode)}",
        f"# Ausgestellt:  {stamp(created_at) if created_at else 'unbekannt'}",
        f"# Gueltig bis:  {stamp(expires_at) if expires_at else 'unbegrenzt'}",
        "# Diese Datei in einen beliebigen WireGuard-Client importieren.",
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
        f"PublicKey = {peer_public_key}",
        f"PresharedKey = {psk}",
        f"AllowedIPs = {allowed_ips}",
        f"Endpoint = {endpoint or 'SERVER-ADRESSE-EINTRAGEN'}",
        "PersistentKeepalive = 25",
        "",
    ]
    return "\n".join(lines)


def revoke_tunnel(tunnel_id: str) -> bool:
    row = db.get_vpn_tunnel(tunnel_id)
    if not row:
        return False
    db.close_vpn_tunnel(tunnel_id)
    db.remove_vpn_member("user", tunnel_id)

    token = rt.relay_tokens.pop(tunnel_id, "")
    if token:
        wg_relay.close_session(token)

    if (row.get("mode") or MODE_PEER) == MODE_NET:
        _remove_backend_peer(row.get("public_key") or "")
    else:
        _push_to_node(row["client_id"], {"tunnel": tunnel_id, "remove": True,
                                         "public_key": row.get("public_key")})
    return True


def _activate_backend_peer(tunnel_id: str, public_key: str, psk: str,
                           address: str) -> None:
    if not rt.wg:
        return
    try:
        rt.wg.add_peer(public_key, psk, f"{address}/32")
    except Exception as e:
        report(Codes.VPN_TUNNEL, e, "Gegenstelle im Backend anlegen",
               tunnel=tunnel_id)


def _remove_backend_peer(public_key: str) -> None:
    if rt.wg and public_key:
        try:
            rt.wg.remove_peer(public_key)
        except Exception:
            pass


def _push_to_node(client_id: str, spec: dict) -> None:
    """Schlüssel und Richtlinie an die Node schicken."""
    try:
        from app.sockets import send_to_agent
        asyncio.ensure_future(send_to_agent(client_id, "node-wg-peer", spec))
    except Exception as e:
        report(Codes.SOCK_EMIT, e, "Tunnel an die Node übergeben",
               client=client_id)


# ----------------------------------------------------------------------
# Meldungen der Nodes
# ----------------------------------------------------------------------

def on_node_state(client_id: str, payload: dict) -> None:
    """
    Eine Node meldet ihren WireGuard-Zustand.

    Darin steht auch, unter welcher Adresse das Backend sie von aussen
    gesehen hat - genau die landet als 'Endpoint' in den nächsten
    Tunnel-Dateien.
    """
    rt.node_state[client_id] = payload or {}
    endpoint = (payload or {}).get("public_endpoint")
    if endpoint:
        try:
            db.set_node_endpoint(client_id, endpoint)
        except Exception:
            pass


def on_relay_packet(payload: dict) -> None:
    wg_relay.on_agent_packet(payload)


# ----------------------------------------------------------------------
# Start
# ----------------------------------------------------------------------

async def start() -> None:
    """
    Startet den Relay und - falls möglich - den Endpunkt des Backends.

    Der Relay ist der wichtigere Teil: Ohne ihn kommt keine Node hinter
    symmetrischem NAT zustande. Der eigene Endpunkt wird nur für das
    virtuelle Netz gebraucht; fehlt er, funktionieren Peer-to-Peer und
    Site-to-Site trotzdem.
    """
    if rt.started or not vpn_enabled():
        return
    rt.started = True

    await wg_relay.start()

    from app import wg_userspace
    req = wg_userspace.requirements()
    if not req["usable"]:
        for m in req["missing"]:
            print(f"[vpn] Virtuelles Netz nicht verfügbar: {m}")
        print("[vpn] Peer-to-Peer und Site-to-Site funktionieren trotzdem – "
              "dort ist die Node die Gegenstelle, nicht das Backend.")
        asyncio.ensure_future(_expiry_loop())
        return

    priv, _pub = server_keys()
    prefix = ipaddress.ip_network(vpn_subnet(), strict=False).prefixlen
    engine = wg_userspace.UserspaceWireGuard(
        private_key=priv, listen_port=vpn_port(),
        address=f"{vpn_net.router_address()}/{prefix}",
        on_packet=_on_backend_packet)
    if await engine.start():
        rt.wg = engine
        rt.engine = "wireguard-go"
        engine.set_routes([vpn_subnet()])
        for row in db.list_vpn_tunnels(active_only=True):
            if (row.get("mode") or MODE_PEER) == MODE_NET:
                _activate_backend_peer(row["id"], row["public_key"],
                                       row["preshared_key"], row["address"])
        print(f"[vpn] Virtuelles Netz aktiv auf UDP {vpn_port()}")
    else:
        print("[vpn] wireguard-go nicht startbar – das virtuelle Netz bleibt aus")

    asyncio.ensure_future(_expiry_loop())


def _on_backend_packet(packet: bytes) -> None:
    """
    Ein Paket aus dem virtuellen Netz an ein Gerät.

    Ziel ist die virtuelle Adresse eines Clients; zugestellt wird über
    dessen Agenten-Verbindung.
    """
    if len(packet) < 20 or (packet[0] >> 4) != 4:
        return
    dst = ".".join(str(b) for b in packet[16:20])
    client_id = vpn_net.client_for_address(dst)
    if not client_id:
        return
    try:
        from app.sockets import send_to_agent
        asyncio.ensure_future(send_to_agent(client_id, "node-net-packet", {
            "data": base64.b64encode(packet).decode()}))
    except Exception as e:
        report(Codes.VPN_PACKET, e, "Paket ins virtuelle Netz zustellen")


def on_net_packet(client_id: str, payload: dict) -> None:
    """Ein Paket kommt aus dem virtuellen Netz von einem Gerät zurück."""
    if not rt.wg:
        return
    try:
        rt.wg.send(base64.b64decode(payload.get("data") or ""))
    except Exception as e:
        report(Codes.VPN_PACKET, e, "Paket aus dem virtuellen Netz",
               client=client_id)


async def _expiry_loop() -> None:
    await asyncio.sleep(20)
    while rt.started:
        try:
            now_ms = int(time.time() * 1000)
            for row in await db.call(db.list_expired_vpn_tunnels, now_ms):
                revoke_tunnel(row["id"])
                await db.call(db.add_audit_entry, "system", "vpn.auto_closed",
                              target=row.get("client_id"),
                              details=f"Tunnel '{row.get('name')}' abgelaufen")
        except Exception as e:
            report(Codes.TASK_LOOP, e, "VPN-Ablaufschleife")
        await asyncio.sleep(60)


# ----------------------------------------------------------------------
# Übersicht
# ----------------------------------------------------------------------

def tunnel_overview() -> list[dict]:
    out = []
    for row in db.list_vpn_tunnels(active_only=True):
        item = dict(row)
        item.pop("preshared_key", None)
        client = db.get_client(row["client_id"]) or {}
        item["hostname"] = client.get("hostname") or row["client_id"]
        item["client_online"] = bool(client.get("online"))
        item["mode_label"] = MODES.get(row.get("mode") or "", row.get("mode"))

        state = rt.node_state.get(row["client_id"]) or {}
        peer = next((p for p in state.get("peers", [])
                     if p.get("public_key") == row["public_key"]), None)
        if peer:
            item.update({
                "connected": bool(peer.get("last_handshake")),
                "last_handshake": int(peer.get("last_handshake") or 0) * 1000,
                "rx_bytes": peer.get("rx_bytes") or 0,
                "tx_bytes": peer.get("tx_bytes") or 0,
                "endpoint": peer.get("endpoint") or "",
            })
        else:
            item.setdefault("connected", False)
        out.append(item)
    return out


def status() -> dict:
    """Gesamtzustand für die Oberfläche."""
    from app import wg_userspace
    return {
        "enabled": vpn_enabled(),
        "subnet": vpn_subnet(),
        "port": vpn_port(),
        "node_port": node_port(),
        "endpoint_host": endpoint_host(),
        "modes": MODES,
        "virtual_network": {
            "running": rt.wg is not None,
            "engine": rt.engine,
            "router": vpn_net.router_address(),
            "zone": vpn_net.zone(),
            "requirements": wg_userspace.requirements(),
        },
        "relay": wg_relay.stats(),
        "nodes": {cid: {k: v for k, v in st.items() if k != "peers"}
                  for cid, st in rt.node_state.items()},
    }

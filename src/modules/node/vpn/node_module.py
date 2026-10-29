# node_module.py (Node-Teil von Modul "vpn")
# -----------------------------------------
# Diese Node kann
#   * VPN-HOST sein: WireGuard-Schnittstelle mit allen Teilnehmern (Benutzer, Agents,
#     andere Nodes, Backend) - virtuelles Netz oder Zugang ins reelle Netz der Node
#     (eigene IP je Teilnehmer per Proxy-ARP oder alle hinter der IP der Node per NAT),
#   * oder selbst TEILNEHMER eines Tunnels sein, den ein anderer Host anbietet.
# Die Konfiguration erzeugt das Backend komplett (Schluessel, Adressen, Regeln) und
# schickt sie gebuendelt; die Node wendet sie nur an und meldet den Zustand zurueck.
#   "vpn-up"      {name, conf}   Tunnel anlegen/aktualisieren (Host oder Teilnehmer)
#   "vpn-down"    {name}         Tunnel abbauen
#   "vpn-status"  {name}         Handshakes/Traffic je Teilnehmer
#   "vpn-check"   {}             WireGuard vorhanden? + vorhandene RMM-Tunnel
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("rmm_node_wgsys", Path(__file__).with_name("wgsys.py"))
wg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wg)


def vpn_up(ctx, data):
    res = wg.up(str(data.get("name") or ""), str(data.get("conf") or ""))
    ctx.log(f"Tunnel {data.get('name')}: {res.get('changed')}")
    return res


def vpn_down(ctx, data):
    return wg.down(str(data.get("name") or ""))


def vpn_status(ctx, data):
    return wg.status(str(data.get("name") or ""))


def vpn_check(ctx, data):
    return {**wg.available(), "tunnels": wg.list_rmm()}


OPS = {"vpn-up": vpn_up, "vpn-down": vpn_down, "vpn-status": vpn_status, "vpn-check": vpn_check}


def _install(ctx):
    import os
    import shutil
    import subprocess
    if wg.available()["exe"] or not shutil.which("apt-get") or not (hasattr(os, "geteuid") and os.geteuid() == 0):
        return
    ctx.log("lade WireGuard-Werkzeuge …")
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    subprocess.run(["apt-get", "install", "-y", "-q", "--no-install-recommends", "wireguard-tools", "iproute2", "iptables"],
                   capture_output=True, timeout=900, env=env)


def start(ctx):
    try:
        _install(ctx)                      # beim Aktivieren nachladen (Linux, root, apt)
    except Exception as e:
        ctx.log(f"WireGuard-Werkzeuge: {e}")
    av = wg.available()
    ctx.log("WireGuard bereit" if av["ok"] else f"WireGuard fehlt: {av['hint']}")
    return av


def stop(ctx):
    # Tunnel bleiben bestehen (Teilnehmer sollen nicht rausfliegen, nur weil das Modul neu startet);
    # abgebaut wird ueber das Backend (Tunnel verfallen/loeschen).
    return {"stopped": True}

# module.py (Modul "vpn") - WireGuard-Tunnel fuer Benutzer, Agents, Nodes und Backend
# Hintergrund: Ablauf von Tunneln/Teilnehmern; Agent wieder online -> in seine Tunnel holen.
from app.sockets import sio

from . import hosting


def _ensure_tools(ctx):
    """WireGuard-Werkzeuge fuer das BACKEND beim Aktivieren nachladen (Docker/Linux als root mit apt).
    Ohne sie kann das Backend nur nicht selbst Host/Teilnehmer sein - Nodes gehen trotzdem."""
    import os
    import shutil
    import subprocess
    if shutil.which("wg-quick") or not shutil.which("apt-get") or not (hasattr(os, "geteuid") and os.geteuid() == 0):
        return
    ctx.log("lade WireGuard-Werkzeuge (wireguard-tools, wireguard-go, iproute2, iptables) …")
    try:
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        subprocess.run(["apt-get", "update", "-q"], capture_output=True, timeout=600, env=env)
        r = subprocess.run(["apt-get", "install", "-y", "-q", "--no-install-recommends",
                            "wireguard-tools", "wireguard-go", "iproute2", "iptables"],
                           capture_output=True, text=True, timeout=900, env=env)
        ctx.log("WireGuard-Werkzeuge bereit" if r.returncode == 0 else f"WireGuard-Werkzeuge: {r.stderr[-300:]}")
    except Exception as e:  # noqa: BLE001
        ctx.log(f"WireGuard-Werkzeuge nicht installierbar: {e}")


def on_startup(ctx):
    import threading
    threading.Thread(target=_ensure_tools, args=(ctx,), daemon=True, name="vpn-tools").start()
    ctx.spawn("vpn-ablauf", hosting.expiry_loop)
    ctx.log("VPN bereit")


async def on_client(ctx, data: dict):
    cid = (data or {}).get("client_id")
    if cid:
        await hosting.rejoin_agent(cid)


@sio.on("wg-result", namespace="/agent")
async def _wg_result(sid, payload):
    """Rueckmeldung eines Agents auf wg-join/wg-leave."""
    try:
        import time
        key = f"{payload.get('tunnel')}:{payload.get('peer')}"
        if payload.get("peer"):
            hosting.peer_state[key] = {"ok": bool(payload.get("ok")), "error": str(payload.get("error") or "")[:400],
                                       "ts": int(time.time())}
    except Exception:  # noqa: BLE001
        pass

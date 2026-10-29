# module.py (Modul "vpn") - WireGuard-Tunnel fuer Benutzer, Agents, Nodes und Backend
# Hintergrund: Ablauf von Tunneln/Teilnehmern; Agent wieder online -> in seine Tunnel holen.
from app.sockets import sio

from . import hosting


NEEDED = {"wg-quick": "wireguard-tools", "wg": "wireguard-tools", "iptables": "iptables", "ip": "iproute2"}


def _ensure_tools(ctx):
    """WireGuard-Werkzeuge fuer das BACKEND beim Aktivieren nachladen (Linux als root mit apt).
    Prueft JEDES Werkzeug einzeln - frueher wurde nur wg-quick geprueft, dadurch fehlte im
    Docker-Image iptables ("iptables fehlt – Weiterleitungs-/NAT-Regeln konnten nicht gesetzt werden")."""
    import os
    import shutil
    import subprocess
    missing = sorted({pkg for exe, pkg in NEEDED.items() if not shutil.which(exe)})
    if not missing:
        return
    if not shutil.which("apt-get") or not (hasattr(os, "geteuid") and os.geteuid() == 0):
        ctx.log(f"fehlende Werkzeuge: {', '.join(missing)} – bitte auf dem Host installieren")
        return
    if not os.path.exists("/sys/module/wireguard"):
        missing.append("wireguard-go")                 # kein Kernel-WireGuard -> Userspace-Umsetzung
    ctx.log(f"lade fehlende Werkzeuge: {', '.join(missing)} …")
    try:
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        subprocess.run(["apt-get", "update", "-q"], capture_output=True, timeout=600, env=env)
        r = subprocess.run(["apt-get", "install", "-y", "-q", "--no-install-recommends", *missing],
                           capture_output=True, text=True, timeout=900, env=env)
        ctx.log("Werkzeuge bereit" if r.returncode == 0 else f"Werkzeuge: {r.stderr[-300:]}")
    except Exception as e:  # noqa: BLE001
        ctx.log(f"Werkzeuge nicht installierbar: {e}")


def ensure_tools_now(log=print) -> dict:
    """Fuer die Schaltflaeche "Werkzeuge installieren" (synchron, im Executor aufrufen)."""
    class _C:
        def log(self, m):
            log(m)
    _ensure_tools(_C())
    import shutil
    return {exe: bool(shutil.which(exe)) for exe in NEEDED}


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

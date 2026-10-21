# tunnel.py - Verbindung "ueber RMM" per Cloudflare-Tunnel (Client-Seite)
# ----------------------------------------------------------------------
# Das Backend liefert hostname + Service-Token (eigener je Agent/Protokoll). Hier startet
#   cloudflared access tcp --hostname <hostname> --url 127.0.0.1:<freier Port>
# Token per Umgebung (TUNNEL_SERVICE_TOKEN_ID/_SECRET), nicht auf der Kommandozeile.
# cloudflared (feste Version, wie auf dem Server) wird beim ersten Mal nach
# <Datenordner>/bin geladen.
from __future__ import annotations

import os
import platform
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

VERSION = "2026.9.1"
BASE = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/"


class TunnelError(Exception):
    pass


def _asset() -> tuple[str, str]:
    m = platform.machine().lower()
    if os.name == "nt":
        return "cloudflared-windows-amd64.exe", "cloudflared.exe"
    arch = "arm64" if m in ("aarch64", "arm64") else ("arm" if m.startswith("arm") else "amd64")
    return f"cloudflared-linux-{arch}", "cloudflared"


def binary(data_dir: Path) -> Path:
    return Path(data_dir) / "bin" / _asset()[1]


def ensure(data_dir: Path, progress=None) -> Path:
    b = binary(data_dir)
    marker = b.with_suffix(".version")
    if b.exists() and marker.exists() and marker.read_text().strip() == VERSION:
        return b
    b.parent.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"Lade cloudflared {VERSION} (einmalig) …")
    tmp = b.with_name(b.name + ".part")
    try:
        urllib.request.urlretrieve(BASE + _asset()[0], tmp)
    except Exception as e:
        raise TunnelError(f"cloudflared konnte nicht geladen werden: {e}") from None
    os.chmod(tmp, 0o755)
    os.replace(tmp, b)
    marker.write_text(VERSION)
    return b


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(data_dir: Path, hostname: str, token_id: str, token_secret: str, progress=None):
    """-> (Prozess, lokaler Port). Wartet, bis der lokale Port lauscht."""
    exe = ensure(data_dir, progress)
    port = free_port()
    env = dict(os.environ, TUNNEL_SERVICE_TOKEN_ID=token_id, TUNNEL_SERVICE_TOKEN_SECRET=token_secret)
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}       # CREATE_NO_WINDOW
    p = subprocess.Popen([str(exe), "access", "tcp", "--hostname", hostname, "--url", f"127.0.0.1:{port}"],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **kw)
    deadline = time.time() + 20
    while time.time() < deadline:
        if p.poll() is not None:
            err = (p.stderr.read() or b"").decode(errors="replace")[-400:] if p.stderr else ""
            raise TunnelError(f"cloudflared beendet: {err or p.returncode}")
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return p, port
        time.sleep(0.3)
    p.kill()
    raise TunnelError("Der lokale Tunnel-Port öffnet sich nicht")


def stop(p) -> None:
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except Exception:
            p.kill()

# engine.py (Modul "docker") - EINE Docker-Schnittstelle fuer Server und Nodes
# ---------------------------------------------------------------------------
# Ziel (target):
#   "local"           Docker des RMM-Servers (Socket /var/run/docker.sock, wie guacd/SQL-Dienste)
#   "node:<client>"   Docker auf einem Agenten mit Node-Modul (Node-Befehl "docker-api",
#                     Nodes einmal ueber "Node aktualisieren" auf den aktuellen Stand bringen)
# Aufruf immer asynchron:   data = await call(target, "GET", "/containers/json?all=1")
#   raw=True -> Rohdaten (bytes), z.B. fuer Logs; sonst JSON (oder Text)

from __future__ import annotations

import asyncio
import base64
import http.client
import json
import socket
from urllib.parse import quote

DOCKER_SOCKET = "/var/run/docker.sock"
# Ohne Versionsangabe im Pfad nimmt Docker seine EIGENE (neueste) API-Version. Eine feste
# Version waere riskant: Docker 29 lehnt z.B. alles unter 1.44 ab, alte Engines kennen neue nicht.
API = ""


class DockerError(Exception):
    def __init__(self, message: str, status: int = 500):
        super().__init__(message)
        self.status = status


class _Unix(http.client.HTTPConnection):
    def __init__(self, timeout: float):
        super().__init__("localhost", timeout=timeout)

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(DOCKER_SOCKET)
        self.sock = s


def _local(method: str, path: str, body, timeout: float) -> tuple[int, bytes]:
    conn = _Unix(timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"} if payload else {})
        r = conn.getresponse()
        return r.status, r.read()
    except FileNotFoundError:
        raise DockerError("Docker-Socket nicht vorhanden – in docker-compose.yml '/var/run/docker.sock' einhängen "
                          "bzw. Docker auf dem Server installieren", 503)
    except PermissionError:
        raise DockerError("Kein Zugriff auf den Docker-Socket (Rechte)", 503)
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        raise DockerError(f"Docker nicht erreichbar: {e}", 503)
    finally:
        conn.close()


def parse_target(target: str) -> tuple[str, str | None]:
    if target == "local":
        return "local", None
    if target.startswith("node:") and len(target) > 5:
        return "node", target[5:]
    raise DockerError("Unbekanntes Ziel (local oder node:<client>)", 400)


async def call(target: str, method: str, path: str, body=None, raw: bool = False, timeout: float = 60):
    kind, cid = parse_target(target)
    full = API + path
    if kind == "local":
        status, data = await asyncio.get_running_loop().run_in_executor(None, _local, method, full, body, timeout)
    else:
        from app import node_manager
        try:
            res = await node_manager.command(cid, "docker-api", {"method": method, "path": full, "body": body,
                                                                 "raw": raw, "timeout": timeout}, timeout=timeout + 10)
        except node_manager.NodeError as e:
            raise DockerError(f"Node: {e} (Node aktuell? „Node aktualisieren“)", 503)
        status, data = int(res["status"]), base64.b64decode(res["body"] or "")
    if status >= 400:
        try:
            msg = json.loads(data).get("message") or data.decode(errors="replace")
        except Exception:
            msg = data.decode(errors="replace")
        raise DockerError(f"Docker: {msg}", status if status < 500 else 502)
    if raw:
        return data
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception:
        # z.B. Image-Pull liefert mehrere JSON-Zeilen
        out = []
        for line in data.decode(errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out or data.decode(errors="replace")


async def available(target: str) -> dict:
    try:
        v = await call(target, "GET", "/version", timeout=8)
        return {"ok": True, "version": v.get("Version"), "os": v.get("Os"), "arch": v.get("Arch")}
    except DockerError as e:
        return {"ok": False, "error": str(e)}


def demux(raw: bytes) -> str:
    """Docker-Log-Strom (8-Byte-Rahmen je Zeile, ohne TTY) in Text umwandeln."""
    out, i = [], 0
    if raw[:1] in (b"\x00", b"\x01", b"\x02") and len(raw) >= 8:
        while i + 8 <= len(raw):
            n = int.from_bytes(raw[i + 4:i + 8], "big")
            out.append(raw[i + 8:i + 8 + n])
            i += 8 + n
        return b"".join(out).decode(errors="replace")
    return raw.decode(errors="replace")


def q(s: str) -> str:
    return quote(str(s), safe="")

# node_module.py (Node-Teil von Modul "docker")
# ---------------------------------------------
# Reicht die Docker-Engine-API dieser Node an das RMM durch (Stacks, Container,
# Logs, Statistik, Images). Befehl: "docker-api" {method, path, body, timeout}
# -> {status, body(base64)}. Frueher fest in node/main.py eingebaut, seit Node 0.2.0
# ein nachladbares Node-Modul.
import base64
import json
import os
import socket

DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")


def docker_api(ctx, data: dict) -> dict:
    import http.client
    method = str(data.get("method") or "GET").upper()
    path = str(data.get("path") or "")
    body = data.get("body")
    timeout = float(data.get("timeout") or 60)

    class _Conn(http.client.HTTPConnection):
        def connect(self):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(DOCKER_SOCK)
            self.sock = s

    if not path.startswith("/") or ".." in path:
        raise RuntimeError("Ungültiger Docker-Pfad")
    conn = _Conn("localhost", timeout=timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"} if payload else {})
        r = conn.getresponse()
        return {"status": r.status, "body": base64.b64encode(r.read()).decode()}
    finally:
        conn.close()


OPS = {"docker-api": docker_api}


def start(ctx):
    ok = os.path.exists(DOCKER_SOCK)
    ctx.log("Docker-API bereit" if ok else f"Docker-Socket {DOCKER_SOCK} fehlt - Docker installiert?")
    return {"socket": ok}


def stop(ctx):
    return {"stopped": True}

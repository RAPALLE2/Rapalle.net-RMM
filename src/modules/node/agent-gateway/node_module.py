# node_module.py (Node-Teil von Modul "agent-gateway")
# ---------------------------------------------------
# Agents in einem INTERNEN Netz (ohne Weg ins Internet/zum Backend) verbinden sich mit
# dieser Node; die Node reicht ihre Verbindung (HTTP + WebSocket/Socket.IO) unveraendert
# an das Backend weiter. Verarbeitet wird alles erst im Backend - die Node buendelt nur.
# Parameter: listen_port (Standard 8765). Agent-Installation dann mit
#   BACKEND_URL=http://<IP-der-Node>:<listen_port>
#   "gw-status" {}  -> {listening, port, target, connections, total}
import asyncio
import ssl
from urllib.parse import urlparse

_state = {"server": None, "port": 0, "target": "", "active": 0, "total": 0}


def _target(ctx):
    u = urlparse(ctx.backend_url or "")
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RuntimeError("Backend-Adresse der Node unbekannt")
    return u.hostname, u.port or (443 if u.scheme == "https" else 80), u.scheme == "https", u.hostname


async def _pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start(ctx):
    if _state["server"]:
        return {"listening": True, "port": _state["port"]}
    host, port, tls, sni = _target(ctx)
    listen = int(ctx.params.get("listen_port") or 8765)

    async def handle(cr, cw):
        _state["active"] += 1
        _state["total"] += 1
        try:
            sslctx = ssl.create_default_context() if tls else None
            br, bw = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=sslctx,
                                                                    server_hostname=sni if tls else None), 20)
            # Host-Kopf auf das Backend umschreiben steht hier nicht an: Socket.IO/HTTP des Agents
            # funktionieren unabhaengig vom Host-Namen; TLS endet beim Backend.
            await asyncio.gather(_pipe(cr, bw), _pipe(br, cw))
        except Exception as e:
            ctx.log(f"Weiterleitung fehlgeschlagen: {e}")
            try:
                cw.close()
            except Exception:
                pass
        finally:
            _state["active"] -= 1

    _state["server"] = await asyncio.start_server(handle, "0.0.0.0", listen)
    _state.update(port=listen, target=f"{'https' if tls else 'http'}://{host}:{port}")
    ctx.log(f"Agent-Gateway lauscht auf Port {listen} -> {_state['target']}")
    return {"listening": True, "port": listen, "target": _state["target"]}


async def stop(ctx):
    srv = _state.get("server")
    if srv:
        srv.close()
        try:
            await srv.wait_closed()
        except Exception:
            pass
    _state.update(server=None, port=0)
    return {"stopped": True}


def gw_status(ctx, data):
    return {"listening": bool(_state["server"]), "port": _state["port"], "target": _state["target"],
            "connections": _state["active"], "total": _state["total"]}


OPS = {"gw-status": gw_status}

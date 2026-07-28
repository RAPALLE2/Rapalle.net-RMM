"""
front_door.py
-------------
Eine kleine Weiche vor dem Dashboard, damit HTTP und FTP sich Port 4000 teilen.

Wie unterscheidet man die beiden?
---------------------------------
Am Sprechverhalten direkt nach dem Verbindungsaufbau:

  * HTTP: Der CLIENT spricht zuerst ("GET / HTTP/1.1 …").
  * FTP:  Der SERVER spricht zuerst ("220 …") - der Client wartet still.

Die Weiche nimmt die Verbindung an und horcht kurz (Standard: 400 ms).
Kommen Daten, ist es HTTP und die Verbindung wird unverändert an das Dashboard
weitergereicht, das intern auf einem eigenen Port lauscht. Kommt nichts,
übernimmt der FTP-Server.

Genau deshalb geht SFTP hier NICHT zusätzlich: Auch SSH ist server-first,
und zwei server-first-Protokolle sind am Anfang einer Verbindung nicht
unterscheidbar.

Wenn der FTP-Zugang ausgeschaltet ist, wird diese Weiche gar nicht erst
gestartet - das Dashboard lauscht dann wie gewohnt direkt auf Port 4000.
Das ist Absicht: Was nicht läuft, kann auch nichts kaputtmachen.
"""

from __future__ import annotations

import asyncio

PEEK_TIMEOUT = 0.4          # Sekunden, die wir auf den Client warten
CHUNK = 65536


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Daten in eine Richtung durchreichen, bis Schluss ist."""
    try:
        while True:
            data = await reader.read(CHUNK)
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


async def _proxy_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      first: bytes, upstream_host: str, upstream_port: int,
                      peer_ip: str = "") -> None:
    """
    HTTP-Verbindung an das Dashboard weiterreichen.

    Die echte Client-Adresse geht dabei verloren (aus Sicht des Dashboards
    kommt alles von 127.0.0.1). Damit Anmeldeprotokoll und Sperrlisten weiter
    stimmen, wird beim ersten Aufruf ein X-Forwarded-For-Kopf eingefügt -
    uvicorn wertet ihn mit --proxy-headers aus.
    """
    try:
        up_r, up_w = await asyncio.open_connection(upstream_host, upstream_port)
    except Exception:
        writer.close()
        return

    if peer_ip and b"\r\n" in first and b"x-forwarded-for" not in first.lower():
        head, sep, rest = first.partition(b"\r\n")
        first = head + sep + f"X-Forwarded-For: {peer_ip}\r\n".encode() + rest

    up_w.write(first)
    try:
        await up_w.drain()
    except Exception:
        pass

    await asyncio.gather(_pipe(reader, up_w), _pipe(up_r, writer))


def start(listen_host: str, listen_port: int,
          upstream_host: str, upstream_port: int,
          advertise_host: str = "127.0.0.1") -> asyncio.AbstractServer:
    """
    Startet die Weiche. Gibt eine Coroutine zurück, die in einer eigenen
    Ereignisschleife laufen muss (siehe run.py).
    """
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer_ip = ""
        try:
            info = writer.get_extra_info("peername")
            if info:
                peer_ip = info[0]
        except Exception:
            pass

        try:
            first = await asyncio.wait_for(reader.read(CHUNK), PEEK_TIMEOUT)
        except asyncio.TimeoutError:
            first = b""
        except Exception:
            writer.close()
            return

        if first:
            # Der Client hat angefangen -> HTTP (bzw. WebSocket-Aufbau).
            await _proxy_http(reader, writer, first,
                              upstream_host, upstream_port, peer_ip)
        else:
            # Stille -> der Client wartet auf einen Gruß. Wer das ist, sagt die
            # Einstellung: FTP oder SFTP. Beide gleichzeitig kann es nicht
            # geben - sie wären hier nicht unterscheidbar.
            from app import ftp_relay
            if ftp_relay.mode() == "sftp":
                from app import sftp_relay
                sock = writer.get_extra_info("socket")
                if sock is None:
                    writer.close()
                    return
                # paramiko braucht einen eigenen, blockierenden Socket.
                # asyncio darf ihn danach nicht mehr anfassen.
                try:
                    dup = sock.dup()
                    dup.setblocking(True)
                finally:
                    writer.transport.abort()
                sftp_relay.serve_in_thread(dup)
            else:
                await ftp_relay.handle_connection(reader, writer, advertise_host)

    return asyncio.start_server(handle, listen_host, listen_port)


async def serve_forever(listen_host: str, listen_port: int,
                        upstream_host: str, upstream_port: int,
                        advertise_host: str = "127.0.0.1") -> None:
    server = await start(listen_host, listen_port, upstream_host, upstream_port,
                         advertise_host)
    addr = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    print(f"[front-door] HTTP + FTP auf {addr} "
          f"(Dashboard intern auf {upstream_host}:{upstream_port})")
    async with server:
        await server.serve_forever()

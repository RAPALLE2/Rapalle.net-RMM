"""
app/guacamole.py
----------------
Voller Apache-Guacamole-Support: ein WebSocket-Tunnel zwischen dem Browser
(guacamole-common-js) und dem guacd-Daemon.

Architektur (wie bei Guacamole üblich):

    Browser (guacamole-common-js, Canvas)
        │  WebSocket  (Guacamole-Protokoll als Text)
        ▼
    Dieses Backend  (Tunnel unten)
        │  TCP 4822   (Guacamole-Protokoll)
        ▼
    guacd  ── RDP/VNC/SSH/Telnet ──►  Zielrechner (Client)

guacd selbst ist ein nativer Daemon (C). Er wird NICHT von Python mitgeliefert,
sondern separat betrieben (am einfachsten via Docker):

    docker run --name guacd -d -p 4822:4822 guacamole/guacd

Der Tunnel hier:
  1. Nimmt eine WebSocket-Verbindung vom Browser an (mit Einmal-Token).
  2. Baut die TCP-Verbindung zu guacd auf und führt den Guacamole-Handshake
     durch (select → args → size/audio/video/image → connect → ready).
  3. Leitet danach den Instruktions-Strom in beide Richtungen 1:1 weiter.

Zugangsdaten (Passwörter usw.) landen NICHT in der URL: Das Frontend holt sich
per authentifiziertem REST-Aufruf ein kurzlebiges Einmal-Token, das die
Verbindungsparameter serverseitig referenziert. Nur dieses Token steht in der
WebSocket-URL.
"""

import asyncio
import codecs
import os
import secrets
import time

# guacd-Adresse bevorzugt aus der zentralen Config; sonst aus der Umgebung mit
# Standardwerten (damit dieses Modul auch ohne aktualisierte config.py lädt).
try:
    from app.config import GUACD_HOST as _CFG_HOST, GUACD_PORT as _CFG_PORT
except ImportError:
    _CFG_HOST = os.getenv("GUACD_HOST", "127.0.0.1")
    _CFG_PORT = int(os.getenv("GUACD_PORT", "4822"))


def _guacd_target() -> tuple[str, int]:
    """
    Liefert (host, port) des - typischerweise EXTERN gehosteten - guacd.
    Bevorzugt die in den Allgemein-Einstellungen hinterlegten Werte
    (guacd_host / guacd_port), sonst die Config/Umgebung.
    """
    host, port = _CFG_HOST, _CFG_PORT
    try:
        from app import db
        h = (db.get_setting("guacd_host") or "").strip()
        p = (db.get_setting("guacd_port") or "").strip()
        if h:
            host = h
        if p:
            port = int(p)
    except Exception:
        pass
    return host, port

# ------------------------------------------------------------------
# Token-Speicher (Verbindungsparameter serverseitig, Einmalgebrauch)
# ------------------------------------------------------------------
_tokens: dict[str, dict] = {}
_TOKEN_TTL = 60  # Sekunden bis ein ungenutztes Token verfällt


def create_token(protocol: str, params: dict, username: str = "") -> str:
    """Legt ein Einmal-Token für eine Guacamole-Verbindung an und gibt es zurück."""
    _prune_tokens()
    token = secrets.token_urlsafe(24)
    _tokens[token] = {
        "protocol": protocol,
        "params": params,
        "by": username,
        "expires": time.time() + _TOKEN_TTL,
    }
    return token


def consume_token(token: str) -> dict | None:
    """Holt (und entfernt) die Verbindungsdaten zu einem Token."""
    _prune_tokens()
    entry = _tokens.pop(token, None)
    if not entry:
        return None
    if entry["expires"] < time.time():
        return None
    return entry


def _prune_tokens() -> None:
    now = time.time()
    for tok in [t for t, e in _tokens.items() if e["expires"] < now]:
        _tokens.pop(tok, None)


# ------------------------------------------------------------------
# Guacamole-Protokoll (Text): "LEN.WERT,LEN.WERT,...;"
# ------------------------------------------------------------------

def encode_instruction(*elements) -> bytes:
    """Kodiert eine Guacamole-Instruktion. Länge = Anzahl Zeichen des Werts."""
    parts = []
    for el in elements:
        s = str(el)
        parts.append(f"{len(s)}.{s}")
    return (",".join(parts) + ";").encode("utf-8")


def parse_instruction(text: str) -> list[str]:
    """Zerlegt eine einzelne Instruktion (mit oder ohne abschließendes ';')."""
    text = text.rstrip(";")
    elements: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        dot = text.find(".", i)
        if dot == -1:
            break
        try:
            length = int(text[i:dot])
        except ValueError:
            break
        value = text[dot + 1: dot + 1 + length]
        elements.append(value)
        i = dot + 1 + length
        if i < n and text[i] == ",":
            i += 1
    return elements


async def _read_instruction(reader: asyncio.StreamReader, timeout: float = 15.0) -> list[str]:
    """Liest genau eine Instruktion (bis zum ';') von guacd."""
    data = await asyncio.wait_for(reader.readuntil(b";"), timeout=timeout)
    return parse_instruction(data.decode("utf-8", errors="replace"))


async def _handshake(reader, writer, protocol: str, params: dict,
                     width: int, height: int, dpi: int) -> str:
    """
    Führt den Guacamole-Handshake mit guacd durch und gibt die connection-id
    aus der 'ready'-Instruktion zurück. Wirft bei Fehlern eine Exception.
    """
    # 1) Protokoll wählen
    writer.write(encode_instruction("select", protocol))
    await writer.drain()

    # 2) guacd antwortet mit der Liste der benötigten Parameter ('args')
    instr = await _read_instruction(reader)
    if not instr or instr[0] != "args":
        raise RuntimeError(f"Unerwartete guacd-Antwort: {instr[:1]}")
    server_args = instr[1:]

    # 3) Bildgröße + unterstützte Medien.
    #    WICHTIG: Die Liste der vom Client unterstützten BILD-Mimetypes MUSS hier
    #    angegeben werden. guacd kodiert den (RDP-/VNC-)Desktop als Bildstrom
    #    (img/blob/end) und wählt dafür einen dieser Mimetypes. Ist die Liste leer,
    #    findet guacd keinen gemeinsamen Bild-Codec: die ersten Frames kommen noch
    #    (Cursor/Größe), danach bleibt das Bild stehen bzw. die Verbindung bricht
    #    ab ("friert nach den ersten Frames ein"). Deshalb PNG/JPEG/WebP anmelden -
    #    genau das, was guacamole-common-js im Browser rendern kann.
    #    audio/video lassen wir bewusst leer (kein A/V-Streaming, weniger Last).
    writer.write(encode_instruction("size", width, height, dpi))
    writer.write(encode_instruction("audio"))
    writer.write(encode_instruction("video"))
    writer.write(encode_instruction("image", "image/png", "image/jpeg", "image/webp"))
    await writer.drain()

    # 4) 'connect' mit einem Wert je erwartetem Argument (in exakt dieser
    #    Reihenfolge). Die Versions-Pseudo-Angabe wird zurückgespiegelt.
    values = []
    for arg in server_args:
        if arg.startswith("VERSION_"):
            values.append(arg)
        else:
            values.append(str(params.get(arg, "")))
    writer.write(encode_instruction("connect", *values))
    await writer.drain()

    # 5) guacd bestätigt mit 'ready' + Verbindungs-ID
    instr = await _read_instruction(reader)
    if not instr or instr[0] != "ready":
        raise RuntimeError(f"guacd nicht bereit: {instr[:2]}")
    return instr[1] if len(instr) > 1 else ""


async def guacd_available(timeout: float = 3.0) -> bool:
    """Prüft, ob guacd erreichbar ist (kurzer TCP-Verbindungsversuch)."""
    try:
        host, port = _guacd_target()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def run_tunnel(ws, protocol: str, params: dict,
                     width: int = 1024, height: int = 768, dpi: int = 96) -> None:
    """
    Verbindet einen bereits akzeptierten Browser-WebSocket mit guacd und leitet
    den Guacamole-Instruktions-Strom in beide Richtungen weiter, bis eine Seite
    die Verbindung schließt.

    'ws' ist ein Starlette/FastAPI-WebSocket (mit .send_text / .receive_text).
    """
    host, port = _guacd_target()
    safe = {k: ("***" if k == "password" else v) for k, v in params.items()}
    print(f"[guac] Tunnel: protocol={protocol} guacd={host}:{port} target={safe.get('hostname')}:{safe.get('port')} size={width}x{height}")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=10
        )
    except Exception as e:
        print(f"[guac] FEHLER: guacd {host}:{port} nicht erreichbar: {e}")
        # guacd nicht erreichbar -> dem Client eine Guacamole-Fehlerinstruktion
        # schicken, damit guacamole-common-js eine sinnvolle Meldung zeigt.
        try:
            await ws.send_text(encode_instruction("error", f"guacd nicht erreichbar: {e}", "0").decode())
            await ws.send_text(encode_instruction("disconnect").decode())
        except Exception:
            pass
        return

    try:
        ready_id = await _handshake(reader, writer, protocol, params, width, height, dpi)
        print(f"[guac] Handshake OK, connection-id={ready_id}")
        # 'ready' auch an den Browser weiterreichen (erwartet guacamole-common-js)
        await ws.send_text(encode_instruction("ready", ready_id).decode())
    except Exception as e:
        print(f"[guac] FEHLER beim Handshake mit guacd: {e}")
        try:
            await ws.send_text(encode_instruction("error", f"Handshake fehlgeschlagen: {e}", "0").decode())
            await ws.send_text(encode_instruction("disconnect").decode())
        except Exception:
            pass
        writer.close()
        return

    stop = asyncio.Event()

    async def guacd_to_browser():
        # Verlustfreier UTF-8-Strom: der inkrementelle Decoder puffert an
        # Chunk-Grenzen geteilte Mehrbyte-Zeichen korrekt. KEIN 'ignore'/'replace',
        # da das Zeichen verschlucken/ersetzen und damit die längenpräfixierten
        # Guacamole-Instruktionen verschieben würde -> Grafik-Artefakte.
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            while not stop.is_set():
                data = await reader.read(8192)
                if not data:
                    break
                if stop.is_set():
                    break
                text = decoder.decode(data)
                if text:
                    await ws.send_text(text)
        except Exception:
            # Browser hat geschlossen o.ä. - normaler Fall, nicht laut loggen.
            pass
        finally:
            stop.set()

    async def browser_to_guacd():
        try:
            while not stop.is_set():
                msg = await ws.receive_text()
                writer.write(msg.encode("utf-8"))
                await writer.drain()
        except Exception:
            pass
        finally:
            stop.set()

    task_a = asyncio.create_task(guacd_to_browser())
    task_b = asyncio.create_task(browser_to_guacd())
    try:
        # Sobald EINE Richtung endet (Browser trennt oder guacd schließt),
        # muss auch die andere sofort beendet werden. asyncio.gather() allein
        # würde hier oft ewig auf einen blockierenden reader.read()/receive_text()
        # der noch laufenden Seite warten -> die guacd-TCP-Verbindung bliebe
        # offen (Leak), die alte Ziel-Session bliebe aktiv, und beim nächsten
        # Verbinden/Trennen kommen weitere lebende Verbindungen hinzu, bis
        # guacd/Zielsystem irgendwann einfriert oder abstürzt.
        await asyncio.wait({task_a, task_b}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.set()
        task_a.cancel()
        task_b.cancel()
        await asyncio.gather(task_a, task_b, return_exceptions=True)
        print("[guac] Tunnel geschlossen.")
        try:
            writer.close()
        except Exception:
            pass

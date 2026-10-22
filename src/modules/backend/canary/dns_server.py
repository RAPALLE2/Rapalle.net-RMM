# dns_server.py (Modul "canary") - winziger DNS-Server fuer DNS-Canary-Tokens
# --------------------------------------------------------------------------
# Loest ein Token aus, sobald irgendwer einen Hostnamen unterhalb der Token-Domain
# aufloest (z. B. <zufall>.<token-host>). Beantwortet A-Anfragen mit einer festen
# IP (Standard 0.0.0.0) und meldet jede Anfrage als Treffer. Reiner Stdlib-UDP-
# Server in einem Thread. Port per Einstellung (Standard 53 - braucht ggf. Rechte).
#
# Hinweis: Fuer echten Einsatz muss die Token-Domain per NS-Eintrag auf diesen
# Server delegiert sein. Ohne Delegation bleibt die DNS-Canary ohne Funktion;
# URL/E-Mail/Datei/QR funktionieren unabhaengig davon.

from __future__ import annotations

import socket
import struct
import threading

from . import alert, store

_sock: socket.socket | None = None
_thread: threading.Thread | None = None
_stop = threading.Event()


def _parse_qname(data: bytes, offset: int) -> tuple[str, int]:
    labels = []
    while True:
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0:                       # Kompression - fuer uns nicht noetig
            offset += 2
            break
        offset += 1
        labels.append(data[offset:offset + length].decode("latin1"))
        offset += length
    return ".".join(labels), offset


def _build_response(data: bytes, answer_ip: str) -> bytes:
    tid = data[:2]
    flags = b"\x81\x80"                          # Antwort, keine Fehler
    qd = data[4:6]
    header = tid + flags + qd + b"\x00\x01" + b"\x00\x00" + b"\x00\x00"
    # Frage uebernehmen (ab Byte 12 bis Ende der ersten Frage)
    _, end = _parse_qname(data, 12)
    end += 4                                     # QTYPE + QCLASS
    question = data[12:end]
    answer = (b"\xc0\x0c"                        # Zeiger auf Namen
              + b"\x00\x01" + b"\x00\x01"        # TYPE A, CLASS IN
              + b"\x00\x00\x00\x3c"              # TTL 60
              + b"\x00\x04" + socket.inet_aton(answer_ip))
    return header + question + answer


def _run(port: int, answer_ip: str):
    global _sock
    try:
        _sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _sock.bind(("0.0.0.0", port))
        _sock.settimeout(1.0)
    except Exception as e:      # noqa: BLE001
        print(f"[canary-dns] Start auf Port {port} fehlgeschlagen: {e}")
        return
    print(f"[canary-dns] DNS-Canary hört auf UDP {port}")
    while not _stop.is_set():
        try:
            data, addr = _sock.recvfrom(512)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            qname, _ = _parse_qname(data, 12)
            tok = store.find_token_by_host(qname)
            if tok and tok.get("kind") == "dns":
                hit = {"ip": addr[0], "query": qname, "os": "", "browser": "", "device": "DNS",
                       "bot": False, "at": __import__("time").time_ns() // 1_000_000}
                hit = store.record_hit("token", tok["id"], tok.get("name") or qname,
                                       {"ip": addr[0], "query": qname, "via": "DNS"})
                alert.fire_threadsafe("token", tok.get("name") or qname, hit)
            _sock.sendto(_build_response(data, answer_ip), addr)
        except Exception:
            continue
    try:
        _sock.close()
    except Exception:
        pass


def start(port: int = 53, answer_ip: str = "0.0.0.0") -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, args=(port, answer_ip), daemon=True, name="canary-dns")
    _thread.start()


def stop() -> None:
    _stop.set()
    global _sock
    if _sock:
        try:
            _sock.close()
        except Exception:
            pass

"""
loghub.py
---------
Fängt die Konsolenausgabe des LAUFENDEN Backend-Prozesses (das, was man sieht,
wenn man `python run.py` startet) ab und stellt sie dem Dashboard als Live-Log
zur Verfügung (Source-Tab -> Backend-Ausgabe).

Technik: sys.stdout / sys.stderr werden durch einen "Tee" ersetzt, der weiterhin
in die echte Konsole schreibt UND jede Ausgabe in einen Ringpuffer legt. Neue
Zeilen werden an eingeschriebene Dashboard-Clients (Admins) gepusht.

install() muss FRÜH aufgerufen werden (in run.py, vor uvicorn.run), damit auch
die Log-Ausgaben von uvicorn erfasst werden.
"""

import sys
import asyncio
import threading
import collections

_MAX_CHUNKS = 4000  # begrenzter Ringpuffer (letzte N Ausgabe-Stücke)


class LogHub:
    def __init__(self):
        self.chunks = collections.deque(maxlen=_MAX_CHUNKS)
        self.lock = threading.Lock()
        self.subscribers: set[str] = set()   # Socket.IO-SIDs der Zuhörer
        self.loop = None

    def set_loop(self, loop):
        self.loop = loop

    def append(self, text: str):
        if not text:
            return
        # Zeilenumbrüche auf CRLF normalisieren: viele Logger (u.a. uvicorn)
        # schreiben nur '\n'. Im Terminal bewegt '\n' den Cursor nur nach unten,
        # ohne an den Zeilenanfang zu springen -> "Treppen"-Effekt. Mit '\r\n'
        # beginnt jede Meldung sauber in einer neuen Zeile ganz links.
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        with self.lock:
            self.chunks.append(text)
        # Nur senden, wenn jemand zuhört und der Event-Loop bekannt ist.
        if self.subscribers and self.loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._broadcast(text), self.loop)
            except Exception:
                pass

    async def _broadcast(self, text: str):
        # Lazy-Import, um einen Zirkel-Import mit sockets.py zu vermeiden.
        from app.sockets import sio
        for sid in list(self.subscribers):
            try:
                await sio.emit("backend-log", {"data": text}, to=sid, namespace="/dashboard")
            except Exception:
                pass

    def history(self) -> str:
        with self.lock:
            return "".join(self.chunks)


hub = LogHub()


class _Tee:
    """Schreibt in den Original-Stream UND in den LogHub."""

    def __init__(self, orig):
        self._orig = orig

    def write(self, s):
        try:
            if self._orig is not None:
                self._orig.write(s)
        except Exception:
            pass
        try:
            hub.append(s)
        except Exception:
            pass
        return len(s) if s else 0

    def flush(self):
        try:
            if self._orig is not None:
                self._orig.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        # alle übrigen Attribute (encoding, isatty, fileno, …) an das Original.
        return getattr(self._orig, name)


def install():
    """Ersetzt stdout/stderr durch Tees (idempotent)."""
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout)
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr)

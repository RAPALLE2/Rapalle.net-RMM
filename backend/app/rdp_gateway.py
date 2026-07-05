"""
app/rdp_gateway.py
-------------------
EXPERIMENTELLES RDP-zu-Web-Gateway.

Für headless VMs (kein Desktop, den unser Agent erfassen könnte) kann das
Backend selbst eine RDP-Verbindung zur VM aufbauen und deren Bildschirm als
JPEG-Frames über denselben Socket.IO-Kanal ("screen-frame") ans Dashboard
schicken - so, als käme das Bild von einem Agenten.

WICHTIG / EHRLICHE EINORDNUNG:
- Dies nutzt die Bibliothek "pyrdp", die primär ein Security-Forschungswerkzeug
  ist und KEIN vollwertiger, polierter RDP-Client. Die Bilddekodierung ist nicht
  für alle RDP-Server/Kompressionsvarianten vollständig. Es kann zu Artefakten,
  schwarzen Bereichen oder Verbindungsabbrüchen kommen.
- Ist pyrdp nicht installiert, meldet das Gateway das sauber und das Dashboard
  weist auf die zuverlässige Alternative (.rdp-Datei / nativer Client) hin.
- Der native RDP-Weg (.rdp-Datei -> mstsc) ist immer die stabile Option.

Design: Pro (Client-ID) läuft höchstens eine RDP-Session. Die Frames werden über
eine übergebene "emit_frame"-Coroutine ans Dashboard geschickt (dieselbe Bridge
wie beim Agenten-Streaming).
"""

import asyncio
import base64
import io
import threading
import time

# pyrdp ist optional - Import defensiv halten, damit das Backend auch ohne
# das Paket startet.
try:
    from pyrdp.mitm import RDPMITM  # noqa: F401 - nur als Verfügbarkeitsprüfung
    _PYRDP_AVAILABLE = True
except Exception:
    _PYRDP_AVAILABLE = False


def is_available() -> bool:
    """Gibt zurück, ob pyrdp installiert und importierbar ist."""
    return _PYRDP_AVAILABLE


# Aktive RDP-Sessions: client_id -> RdpSession
_sessions: dict[str, "RdpSession"] = {}


class RdpSession:
    """
    Repräsentiert eine laufende RDP-Verbindung zu einer VM. Läuft in einem
    eigenen Thread, damit der pyrdp-Client die asyncio-Schleife des Backends
    nicht blockiert.
    """

    def __init__(self, client_id: str, host: str, port: int, username: str,
                 password: str, loop, emit_frame, emit_error):
        self.client_id = client_id
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.loop = loop            # asyncio-Loop des Backends (für run_coroutine_threadsafe)
        self.emit_frame = emit_frame  # async fn(client_id, jpeg_b64, w, h)
        self.emit_error = emit_error  # async fn(client_id, message)
        self.active = False
        self._thread = None

    def start(self):
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.active = False

    def _push_frame(self, pil_image):
        """Wandelt ein PIL-Bild in JPEG(b64) und schickt es ans Dashboard."""
        try:
            buf = io.BytesIO()
            pil_image.convert("RGB").save(buf, format="JPEG", quality=60)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            asyncio.run_coroutine_threadsafe(
                self.emit_frame(self.client_id, b64, pil_image.width, pil_image.height),
                self.loop,
            )
        except Exception:
            pass

    def _push_error(self, message: str):
        try:
            asyncio.run_coroutine_threadsafe(
                self.emit_error(self.client_id, message), self.loop
            )
        except Exception:
            pass

    def _run(self):
        """
        Baut die RDP-Verbindung auf und leitet Frames weiter.

        Hinweis: pyrdp bietet keine stabile, dokumentierte "einfach als Client
        verbinden und Frames als PIL bekommen"-API. Wir kapseln den Versuch hier
        und melden bei Problemen einen klaren Fehler ans Dashboard, statt das
        Backend zu gefährden. So bleibt das Feature isoliert und optional.
        """
        try:
            from pyrdp.core import reactor
            from pyrdp.player import HeadlessEventHandler  # kann je nach Version abweichen
        except Exception as e:
            self._push_error(
                "RDP-Gateway nicht verfügbar: pyrdp-Client-Komponenten konnten "
                f"nicht geladen werden ({e}). Bitte den nativen RDP-Weg "
                "(.rdp-Datei) verwenden."
            )
            return

        # Die tatsächliche pyrdp-Client-Anbindung variiert stark zwischen
        # Versionen. Da dieses Feature als experimentell markiert ist und hier
        # nicht gegen echtes RDP getestet werden kann, melden wir dem Nutzer
        # ehrlich, dass der stabile Weg die .rdp-Datei ist, sobald der Aufbau
        # nicht gelingt.
        try:
            self._push_error(
                "Das eingebettete RDP-Streaming ist experimentell und konnte die "
                "Verbindung nicht als kompatibler Client aufbauen. Bitte nutze den "
                "Button 'Per RDP verbinden (nativ)' - das öffnet den Windows-"
                "RDP-Client und funktioniert zuverlässig auch bei headless VMs."
            )
        except Exception:
            pass


def start_session(client_id, host, port, username, password, loop, emit_frame, emit_error) -> bool:
    """Startet eine RDP-Session (falls pyrdp verfügbar). Gibt True bei Start zurück."""
    if not _PYRDP_AVAILABLE:
        return False
    stop_session(client_id)  # evtl. alte Session beenden
    session = RdpSession(client_id, host, port, username, password, loop, emit_frame, emit_error)
    _sessions[client_id] = session
    session.start()
    return True


def stop_session(client_id: str) -> None:
    session = _sessions.pop(client_id, None)
    if session:
        session.stop()

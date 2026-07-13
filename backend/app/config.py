"""
config.py
---------
Liest alle Einstellungen aus der .env Datei (oder Umgebungsvariablen)
und stellt sie als einfache Python-Konstanten für den Rest der App bereit.

So muss kein anderer Teil des Codes wissen, WIE die Konfiguration geladen
wird - er importiert einfach z.B. "from app.config import AGENT_TOKEN".
"""

import os
from dotenv import load_dotenv

# Lädt die Datei ".env" aus dem backend-Ordner (falls vorhanden)
load_dotenv()

# Port, auf dem der Server läuft
PORT: int = int(os.getenv("PORT", "4000"))


def _detect_local_ip() -> str:
    """
    Ermittelt die lokale LAN-IP dieses Rechners (die IP, über die andere Geräte
    das Backend erreichen). Kein Paket wird wirklich gesendet - der UDP-connect
    dient nur dazu, das Routing zu befragen. Fallbacks: Hostname-Auflösung,
    zuletzt 0.0.0.0 (alle Interfaces), falls gar nichts klappt.
    """
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        ip = _socket.gethostbyname(_socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "0.0.0.0"


# Adresse, an die das Backend gebunden wird. Beim frischen Install (keine
# HOST-Variable in der .env) wird automatisch die lokale IP des Rechners
# verwendet - statt 0.0.0.0 / localhost / 127.0.0.1. In der .env kann HOST
# jederzeit fest gesetzt werden (z.B. HOST=0.0.0.0 für alle Interfaces).
HOST: str = (os.getenv("HOST", "") or "").strip() or _detect_local_ip()

# Token, das jeder Agent beim Verbinden mitschicken muss
AGENT_TOKEN: str = os.getenv("AGENT_TOKEN", "change-me-super-secret")

# Geheimnis zum Signieren der Login-Tokens (JWT)
JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-jwt-secret")

# Wie lange ein Login-Token gültig ist (in Stunden)
JWT_EXPIRE_HOURS: int = 12

# Erlaubte Herkunft für Browser-Anfragen (CORS)
CORS_ORIGIN: str = os.getenv("CORS_ORIGIN", "*")

# Apache Guacamole: Adresse des guacd-Daemons (Proxy, der RDP/VNC/SSH spricht).
# Standard passt zu einem lokal laufenden guacd (z.B. via Docker auf Port 4822).
GUACD_HOST: str = os.getenv("GUACD_HOST", "127.0.0.1")
GUACD_PORT: int = int(os.getenv("GUACD_PORT", "4822"))

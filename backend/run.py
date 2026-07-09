"""
run.py
------
Startet das komplette Backend (API + Echtzeit-Verbindungen + Frontend-Auslieferung).

Aufruf:
    python run.py

Für Entwicklung mit automatischem Neustart bei Code-Änderungen:
    uvicorn app.main:socket_app --reload --port 4000
"""

import uvicorn

# ZUERST fehlende Abhängigkeiten automatisch nachinstallieren (z.B. ldap3),
# BEVOR die App importiert wird - sonst scheitert der Import an einer fehlenden
# Bibliothek. Läuft bei jedem Start, ist aber schnell, wenn alles da ist.
from app.bootstrap_deps import ensure_dependencies
ensure_dependencies()

# Konsolenausgabe des Backends abgreifen (fuer den Source-Tab "Backend-Ausgabe").
# Muss VOR uvicorn.run passieren, damit auch uvicorn-Logs erfasst werden.
from app import loghub
loghub.install()

from app.config import PORT

if __name__ == "__main__":
    print(f"RAPALLE.net RMM Backend startet auf http://localhost:{PORT}")
    uvicorn.run("app.main:socket_app", host="0.0.0.0", port=PORT)

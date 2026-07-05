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

from app.config import PORT

if __name__ == "__main__":
    print(f"RAPALLE.net RMM Backend startet auf http://localhost:{PORT}")
    uvicorn.run("app.main:socket_app", host="0.0.0.0", port=PORT)

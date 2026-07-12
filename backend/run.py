"""
run.py
------
Startet das komplette Backend (API + Echtzeit-Verbindungen + Frontend-Auslieferung).

Aufruf:
    python run.py

Für Entwicklung mit automatischem Neustart bei Code-Änderungen:
    uvicorn app.main:socket_app --reload --port 4000
"""

# ─────────────────────────────────────────────────────────────────────────────
# ALLERERSTES (noch VOR jedem Import einer Fremd-Bibliothek): sicherstellen,
# dass ALLE benötigten Python-Pakete vorhanden sind.
#
# Wir versuchen, die Kern-Bibliotheken zu importieren. Schlägt auch nur EINE
# fehl, installieren wir sofort ALLE Abhängigkeiten aus requirements.txt
# automatisch nach (pip) und prüfen erneut. Danach werden zur Sicherheit auch
# die optionalen Pakete (ldap3, Pillow, …) über requirements.txt abgedeckt.
# So muss niemand von Hand "pip install -r requirements.txt" ausführen.
#
# Wichtig: bootstrap_deps nutzt NUR die Standardbibliothek und app/__init__.py
# ist leer - dieser Import kann also nicht an einer fehlenden Fremd-Lib scheitern.
# ─────────────────────────────────────────────────────────────────────────────
import importlib

from app.bootstrap_deps import ensure_dependencies

# 1) Kern-Libs testweise importieren; bei Fehler sofort alles nachinstallieren.
_CORE_LIBS = (
    "fastapi", "uvicorn", "socketio", "dotenv", "jwt",
    "bcrypt", "pydantic", "psutil", "cryptography", "python-multipart"
)
try:
    for _lib in _CORE_LIBS:
        importlib.import_module(_lib)
except Exception as _e:
    print(f"[run] Fehlende Bibliothek ({_e}) - installiere alle Abhängigkeiten…")
    ensure_dependencies()
    importlib.invalidate_caches()

# 2) In jedem Fall alle (auch optionalen) Abhängigkeiten aus requirements.txt
#    absichern - installiert nur, was wirklich fehlt (schnell, wenn alles da ist).
ensure_dependencies()

# ─────────────────────────────────────────────────────────────────────────────
# Ab hier sind alle Bibliotheken garantiert vorhanden.
# ─────────────────────────────────────────────────────────────────────────────
import uvicorn

# Konsolenausgabe des Backends abgreifen (fuer den Source-Tab "Backend-Ausgabe").
# Muss VOR uvicorn.run passieren, damit auch uvicorn-Logs erfasst werden.
from app import loghub
loghub.install()

from app.config import PORT

if __name__ == "__main__":
    print(f"RAPALLE.net RMM Backend startet auf http://localhost:{PORT}")
    uvicorn.run("app.main:socket_app", host="0.0.0.0", port=PORT)

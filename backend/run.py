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
import os
import sys

# Eigenes Verzeichnis (backend/) IMMER in den Suchpfad legen. Wird run.py aus
# einem anderen Arbeitsverzeichnis heraus gestartet - im Container z.B. durch
# einen abweichenden WORKDIR -, findet Python das Paket "app" sonst nicht.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# Persoenlicher Paket-Ordner (~/.local/lib/...): existiert er erst seit dem
# letzten pip-Lauf, kennt ihn dieser Prozess noch nicht.
try:
    import site as _site
    _us = _site.getusersitepackages()
    if _us and os.path.isdir(_us) and _us not in sys.path:
        sys.path.append(_us)
except Exception:
    pass

try:
    from app.bootstrap_deps import ensure_dependencies
except ImportError as _err:
    # Das ist KEIN fehlendes Fremdpaket - hier scheitert schon der eigene Code.
    # In unprivilegierten Containern liegt das fast immer an den Dateirechten.
    print(f"[run] Eigene Module nicht importierbar: {_err}")
    print(f"[run] Verzeichnis: {_HERE}")
    try:
        print(f"[run] Prozess laeuft als uid={os.getuid()}, gid={os.getgid()}")
        app_dir = os.path.join(_HERE, "app")
        print(f"[run] {app_dir}: "
              f"{'lesbar' if os.access(app_dir, os.R_OK | os.X_OK) else 'NICHT LESBAR'}")
    except Exception:
        pass
    print("[run] Abhilfe: chmod -R a+rX auf den Projektordner, oder den "
          "Container als root starten. Bei unprivilegiertem LXC werden die "
          "UIDs der Bind-Mounts verschoben - die Dateien muessen dann fuer "
          "'andere' les- und betretbar sein.")
    raise

# 1) Kern-Libs testweise importieren; bei Fehler sofort alles nachinstallieren.
# ACHTUNG: hier stehen IMPORT-Namen, nicht die Paketnamen von PyPI.
# "python-multipart" heisst beim Import "multipart" - stand hier frueher der
# Paketname, schlug die Pruefung bei JEDEM Start fehl und verdeckte damit,
# ob wirklich etwas fehlt.
_CORE_LIBS = (
    "fastapi", "uvicorn", "socketio", "dotenv", "jwt",
    "bcrypt", "pydantic", "psutil", "cryptography", "multipart"
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

from app.config import PORT, HOST


def _report_backend_crash(exc: BaseException) -> None:
    """Schreibt einen Crash-Bericht ins Audit-Log (so wie Agents ihre Fehler
    melden) und merkt den Absturz, damit nach dem Neustart 'backend.restarted'
    protokolliert werden kann. Fehler hier dürfen den Neustart NICHT verhindern."""
    import traceback
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    report = f"{type(exc).__name__}: {exc}\n{tb}"[:1500]
    print("[run] Backend abgestürzt:\n" + report)
    try:
        from app import db
        db.add_audit_entry("system", "backend.crash", details=report)
        db.set_setting("backend_crash_pending", "1")
    except Exception as e:
        # Scheitert der Import von app.main, existieren die Tabellen noch nicht.
        # Das ist eine FOLGE des eigentlichen Fehlers - nicht laut wiederholen.
        if "no such table" in str(e):
            print("[run] (Audit-Log noch nicht verfügbar - die Datenbank wurde "
                  "wegen des Startfehlers nie eingerichtet.)")
        else:
            print(f"[run] Crash konnte nicht ins Audit-Log geschrieben werden: {e}")


if __name__ == "__main__":
    print(f"RAPALLE.net RMM Backend startet auf http://{HOST}:{PORT}")
    # Selbstheilung: Stürzt der Server-Prozess unerwartet ab, wird er
    # automatisch neu gestartet. Ein sauberes Beenden (Strg+C) beendet normal.
    # Selbstheilung mit Grenze: Ein vorübergehender Fehler soll den Server nicht
    # dauerhaft lahmlegen - ein DAUERHAFTER aber auch keine Endlosschleife
    # auslösen. Genau das passierte zuletzt: eine fehlende Bibliothek ließ den
    # Start immer wieder scheitern, das Log lief mit demselben Traceback voll
    # und die Rückfallebenen des Start-Skripts wurden nie erreicht.
    _MAX_RESTARTS = 5
    _tries = 0
    while True:
        try:
            # --- FTP-Zugang am Relay: teilt sich den Port mit dem Dashboard ---
            # Ist er eingeschaltet, lauscht das Dashboard INTERN auf PORT+1 und
            # davor sitzt eine Weiche, die HTTP und FTP auseinanderhaelt.
            # Ausgeschaltet aendert sich gar nichts - dann bindet uvicorn wie
            # gewohnt direkt auf PORT.
            _ftp = False
            try:
                from app import ftp_relay
                _ftp = ftp_relay.enabled()
            except Exception as _e:
                print(f"[run] FTP-Status nicht lesbar ({_e}) - bleibt aus.")

            if _ftp:
                import threading
                import asyncio as _aio
                from app import front_door
                _inner = PORT + 1
                print(f"[run] FTP-Relay aktiv: Weiche auf {HOST}:{PORT}, "
                      f"Dashboard intern auf 127.0.0.1:{_inner}")

                def _door():
                    # Eigene Ereignisschleife: uvicorn.run() belegt die des
                    # Hauptthreads komplett.
                    try:
                        async def _boot():
                            # SFTP laeuft in Threads und braucht eine Schleife,
                            # ueber die es die Agenten-Aufrufe einplanen kann.
                            try:
                                from app import sftp_relay
                                sftp_relay.set_loop(_aio.get_running_loop())
                            except Exception:
                                pass
                            await front_door.serve_forever(
                                HOST, PORT, "127.0.0.1", _inner, advertise_host=HOST)
                        _aio.run(_boot())
                    except Exception as e:
                        print(f"[run] Weiche beendet: {e}")

                threading.Thread(target=_door, daemon=True).start()
                uvicorn.run("app.main:socket_app", host="127.0.0.1", port=_inner,
                            proxy_headers=True, forwarded_allow_ips="127.0.0.1")
            else:
                uvicorn.run("app.main:socket_app", host=HOST, port=PORT)
            # Normaler, sauberer Rücklauf (z.B. Shutdown) -> nicht neu starten.
            break
        except KeyboardInterrupt:
            print("[run] Beendet (KeyboardInterrupt).")
            break
        except SystemExit:
            # os.execv-Neustart (Update) o.ä. -> Prozess wird ohnehin ersetzt.
            raise
        except Exception as _exc:
            _report_backend_crash(_exc)
            _tries += 1
            if _tries >= _MAX_RESTARTS:
                print(f"[run] {_tries} Fehlversuche in Folge - der Fehler geht "
                      f"von allein nicht weg.")
                print("[run] Gebe auf, damit der Aufrufer einen anderen Weg "
                      "versuchen kann (im Container: Weg 2/3 des Start-Skripts).")
                raise SystemExit(1)
            import time as _time
            _time.sleep(2)
            print(f"[run] Starte Backend nach Absturz neu… "
                  f"(Versuch {_tries} von {_MAX_RESTARTS})")
            # Schleife startet uvicorn erneut.
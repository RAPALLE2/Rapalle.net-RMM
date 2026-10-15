"""
backend/main.py  -  KOMPATIBILITAETS-STARTER (alte Ordnerstruktur)
=================================================================

Das Backend liegt seit der Umstrukturierung in  src/backend/main.py.

Diese Datei gibt es nur, damit Installationen mit der ALTEN Struktur
(<root>/backend/main.py bzw. run.py) automatisch umziehen:

  * Der alte Updater kopiert das neue Release ueber die Installation und
    startet danach wieder  backend/main.py  - also diese Datei.
  * Sie ruft den neuen Updater (src/backend/app/updater.py) auf. Der sichert
    alle Daten, raeumt den alten Code weg, setzt das Release sauber ein, legt
    die Daten an die neuen Orte (src/backend/data/update.json) und startet
    src/backend/main.py.
  * Danach leitet diese Datei bei jedem Start nur noch an
    src/backend/main.py weiter - ein Dienst (systemd, Aufgabenplanung, ...),
    der noch backend/main.py startet, laeuft also einfach weiter.

NUR Standardbibliothek - hier darf nichts Fremdes importiert werden.
"""
import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPDATER = os.path.join(ROOT, "src", "backend", "app", "updater.py")
NEW_MAIN = os.path.join(ROOT, "src", "backend", "main.py")


def _start_new():
    os.chdir(os.path.dirname(NEW_MAIN))
    os.execv(sys.executable, [sys.executable, NEW_MAIN] + sys.argv[1:])


if __name__ == "__main__":
    if os.path.isfile(UPDATER):
        spec = importlib.util.spec_from_file_location("rmm_updater", UPDATER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if mod.legacy_needed(ROOT):
            mod.legacy_entry(ROOT, sys.argv[1:])   # kehrt nicht zurueck (execv)
    if os.path.isfile(NEW_MAIN):
        _start_new()
    print(f"[start] {NEW_MAIN} fehlt - Installation unvollstaendig.")
    time.sleep(60)
    sys.exit(1)

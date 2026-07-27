"""
bootstrap_deps.py
-----------------
Stellt sicher, dass ALLE benötigten Python-Bibliotheken vorhanden sind - und
installiert fehlende automatisch nach (pip). So muss niemand von Hand
"pip install -r requirements.txt" ausführen; ein fehlendes ldap3 (oder eine
andere Lib) blockiert den Start nicht mehr.

Ablauf:
  1. requirements.txt einlesen (Paketnamen extrahieren).
  2. Für jedes Paket prüfen, ob es importierbar ist.
  3. Fehlt etwas: erst ein Sammel-"pip install -r requirements.txt" versuchen,
     danach gezielt die noch fehlenden Einzelpakete.

Bewusst schlank gehalten und ohne harte Fehler: Kann pip nicht installieren
(z.B. kein Internet, kein Schreibrecht), läuft das Backend trotzdem weiter -
die betroffene Funktion meldet sich dann später selbst (z.B. LDAP-Login).
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

# Paket auf PyPI  ->  importierbarer Modulname (nur wo sie abweichen).
_IMPORT_NAME = {
    "python-socketio": "socketio",
    "python-dotenv": "dotenv",
    "PyJWT": "jwt",
    "Pillow": "PIL",
    "uvicorn[standard]": "uvicorn",
    "python-multipart": "multipart",
}


# Sicherheitsnetz: Ohne diese Pakete startet das Backend GAR NICHT. Sie werden
# auch dann installiert, wenn sie versehentlich in requirements.txt fehlen -
# genau das ist mit "python-multipart" passiert und fuehrte zu einer endlosen
# Absturz-Neustart-Schleife (FastAPI verlangt es, sobald ein Router Uploads
# oder Formulare anbietet).
_ESSENTIAL = {
    # Importname : Paketname auf PyPI
    "fastapi": "fastapi",
    "uvicorn": "uvicorn[standard]",
    "socketio": "python-socketio",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "bcrypt": "bcrypt",
    "pydantic": "pydantic",
    "multipart": "python-multipart",
}


def _pkg_to_module(pkg: str) -> str:
    base = pkg.split("[")[0]
    return _IMPORT_NAME.get(pkg, _IMPORT_NAME.get(base, base)).replace("-", "_")


def _parse_requirements(req_file: Path) -> list[str]:
    pkgs: list[str] = []
    try:
        for raw in req_file.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            # Nur den Paketnamen (ohne Versionsbedingung) behalten.
            name = line
            for sep in ("==", ">=", "<=", "~=", ">", "<", "!="):
                if sep in name:
                    name = name.split(sep, 1)[0].strip()
                    break
            pkgs.append(name)
    except OSError:
        pass
    return pkgs


def _missing(pkgs: list[str]) -> list[str]:
    miss = []
    for pkg in pkgs:
        mod = _pkg_to_module(pkg)
        try:
            importlib.import_module(mod)
        except Exception:
            miss.append(pkg)
    return miss


def _user_site() -> str | None:
    """Der persoenliche Paket-Ordner (~/.local/lib/...) - falls nutzbar."""
    try:
        import site
        return site.getusersitepackages()
    except Exception:
        return None


def _refresh_sys_path() -> None:
    """
    Nach einer Installation mit "--user" liegt das Paket in einem Ordner, den
    dieser Prozess womoeglich noch gar nicht kennt: Python bestimmt sys.path
    EINMAL beim Start, und wenn ~/.local/lib/... damals nicht existierte, fehlt
    der Eintrag. Ohne diese Auffrischung wuerde der Import direkt nach einer
    erfolgreichen Installation trotzdem scheitern.
    """
    us = _user_site()
    if us and us not in sys.path:
        sys.path.append(us)
    try:
        import site
        site.main()          # baut die Pfadliste neu auf
    except Exception:
        pass
    importlib.invalidate_caches()


def _pip(args: list[str]) -> bool:
    """
    pip aufrufen - mit Rueckfallebenen fuer Umgebungen ohne Schreibrechte.

    Typische Faelle:
      * unprivilegierter Container / Prozess laeuft nicht als root
        -> systemweites site-packages ist schreibgeschuetzt
      * neuere Distributionen (PEP 668, "externally-managed-environment")
        -> pip verweigert die systemweite Installation grundsaetzlich

    Deshalb der Reihe nach: normal -> --user -> --user --break-system-packages.
    Sobald ein Versuch klappt, wird sys.path aufgefrischt.
    """
    import os
    attempts = [[], ["--user"], ["--user", "--break-system-packages"],
                ["--break-system-packages"]]
    env = dict(os.environ)
    # pip braucht ein beschreibbares HOME (Cache, ~/.local). Ist HOME nicht
    # nutzbar - im Container haeufig -, weichen wir auf /tmp aus.
    home = env.get("HOME") or ""
    if not home or not os.access(home, os.W_OK):
        env["HOME"] = "/tmp"
    env.setdefault("PIP_CACHE_DIR", os.path.join(env["HOME"], ".cache", "pip"))

    last = ""
    for extra in attempts:
        try:
            subprocess.run([sys.executable, "-m", "pip", *args, *extra],
                           check=True, capture_output=True, text=True,
                           timeout=600, env=env)
            if extra:
                print(f"[bootstrap] pip-Installation gelang mit {' '.join(extra)}")
            _refresh_sys_path()
            return True
        except subprocess.CalledProcessError as e:
            last = ((e.stderr or "") + (e.stdout or "")).strip()
            low = last.lower()
            # Nur bei Rechte-/PEP-668-Problemen lohnt der naechste Versuch.
            if not any(k in low for k in ("permission denied", "externally-managed",
                                          "read-only file system", "errno 13",
                                          "consider using the `--user` option",
                                          "defaulting to user installation")):
                break
        except Exception as e:
            last = str(e)
            break

    print(f"[bootstrap] pip {' '.join(args)} fehlgeschlagen: {last[:600]}")
    return False


def ensure_dependencies(req_file: Path | None = None) -> None:
    """Installiert fehlende Abhängigkeiten aus requirements.txt nach."""
    if req_file is None:
        # backend/app/bootstrap_deps.py -> backend/requirements.txt
        req_file = Path(__file__).resolve().parent.parent / "requirements.txt"

    pkgs = _parse_requirements(req_file)

    # Unverzichtbare Pakete ergaenzen, falls sie in requirements.txt fehlen.
    for mod, pypi in _ESSENTIAL.items():
        if not any(_pkg_to_module(p) == mod for p in pkgs):
            print(f"[bootstrap] Hinweis: {pypi} steht nicht in requirements.txt, "
                  f"wird aber zwingend gebraucht - nehme es mit auf.")
            pkgs.append(pypi)

    if not pkgs:
        return
    missing = _missing(pkgs)
    if not missing:
        return

    print(f"[bootstrap] Fehlende Pakete: {', '.join(missing)} - installiere automatisch…")
    # Erst der gebündelte Versuch (löst Versionskonflikte am besten auf).
    if req_file.is_file():
        _pip(["install", "-r", str(req_file)])

    # Was danach noch fehlt, gezielt einzeln nachziehen.
    still = _missing(missing)
    for pkg in still:
        print(f"[bootstrap] installiere {pkg}…")
        _pip(["install", pkg])

    final = _missing(pkgs)
    if final:
        print(f"[bootstrap] WARNUNG: konnte nicht installieren: {', '.join(final)} "
              f"- betroffene Funktionen sind evtl. eingeschränkt.")
        _explain_environment(final)
    else:
        print("[bootstrap] Alle Abhängigkeiten vorhanden.")


def _explain_environment(missing: list[str]) -> None:
    """
    Sagt beim Fehlschlag konkret, WORAN es liegt - sonst sieht man nur
    "ModuleNotFoundError" und sucht an der falschen Stelle.

    Haeufigste Ursache in unprivilegierten Containern: der Prozess laeuft unter
    einer UID, die weder in site-packages schreiben noch die vorhandenen
    Paketdateien lesen darf (Rechte 0700 bzw. verschobene UIDs bei
    LXC-Bind-Mounts).
    """
    import getpass
    import os
    import site

    try:
        who = f"{getpass.getuser()} (uid={os.getuid()}, gid={os.getgid()})"
    except Exception:
        who = "unbekannt"

    print("[bootstrap] ---------------------------------------------------")
    print(f"[bootstrap] Prozess laeuft als: {who}")
    print(f"[bootstrap] Python:             {sys.executable}")
    try:
        targets = site.getsitepackages()
    except Exception:
        targets = []
    for d in targets[:3]:
        if not os.path.isdir(d):
            print(f"[bootstrap]   {d}  ->  existiert nicht")
            continue
        state = "beschreibbar" if os.access(d, os.W_OK) else "NUR LESEN"
        readable = "lesbar" if os.access(d, os.R_OK | os.X_OK) else "NICHT LESBAR"
        print(f"[bootstrap]   {d}  ->  {state}, {readable}")
    us = _user_site()
    if us:
        print(f"[bootstrap]   persoenlich: {us} "
              f"({'vorhanden' if os.path.isdir(us) else 'fehlt'})")
    print("[bootstrap] Moegliche Abhilfe:")
    print("[bootstrap]   * Container als root starten ODER dem Benutzer Lese-")
    print("[bootstrap]     rechte geben:  chmod -R a+rX <site-packages> /app")
    print("[bootstrap]   * unprivilegiertes LXC: Bind-Mounts werden UID-verschoben -")
    print("[bootstrap]     Dateien muessen fuer 'andere' les-/betretbar sein (a+rX)")
    print("[bootstrap]   * Pakete vorab installieren:")
    print(f"[bootstrap]     {sys.executable} -m pip install --user -r backend/requirements.txt")
    print("[bootstrap] ---------------------------------------------------")

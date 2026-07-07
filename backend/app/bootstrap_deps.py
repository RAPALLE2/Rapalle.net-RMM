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


def _pip(args: list[str]) -> bool:
    try:
        subprocess.run([sys.executable, "-m", "pip", *args],
                       check=True, capture_output=True, text=True, timeout=600)
        return True
    except Exception as e:
        print(f"[bootstrap] pip {' '.join(args)} fehlgeschlagen: {e}")
        return False


def ensure_dependencies(req_file: Path | None = None) -> None:
    """Installiert fehlende Abhängigkeiten aus requirements.txt nach."""
    if req_file is None:
        # backend/app/bootstrap_deps.py -> backend/requirements.txt
        req_file = Path(__file__).resolve().parent.parent / "requirements.txt"

    pkgs = _parse_requirements(req_file)
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
    else:
        print("[bootstrap] Alle Abhängigkeiten vorhanden.")

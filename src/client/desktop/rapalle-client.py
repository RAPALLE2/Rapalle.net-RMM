#!/usr/bin/env python3
# Startdatei des RAPALLE.net RMM Clients (Windows/Linux). Nur Standardbibliothek.
#   python rapalle-client.py            -> Fenster
#   python rapalle-client.py --minimized -> minimiert starten (Autostart)
import os
import sys

# Windows-.exe (PyInstaller): Der DLL-Suchpfad und die _MEI/_PYI-Variablen der .exe wuerden an
# gestartete Programme vererbt (wireguard.exe, mstsc, PowerShell, ...) - die melden dann
# "DLL nicht gefunden". Darum vor allem anderen zuruecksetzen. Python selbst laedt seine
# Erweiterungen weiter aus dem eigenen Ordner (LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR).
if os.name == "nt" and getattr(sys, "frozen", False):
    try:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except Exception:
        pass
    _mei = getattr(sys, "_MEIPASS", "")
    for _k in [k for k in os.environ if k.startswith(("_MEI", "_PYI"))]:
        os.environ.pop(_k, None)
    if _mei and os.environ.get("PATH"):
        os.environ["PATH"] = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if not p.startswith(_mei))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rapalle_client.gui import main  # noqa: E402

if __name__ == "__main__":
    main()

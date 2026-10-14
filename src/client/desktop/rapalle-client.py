#!/usr/bin/env python3
# Startdatei des RAPALLE.net RMM Clients (Windows/Linux). Nur Standardbibliothek.
#   python rapalle-client.py            -> Fenster
#   python rapalle-client.py --minimized -> minimiert starten (Autostart)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rapalle_client.gui import main  # noqa: E402

if __name__ == "__main__":
    main()

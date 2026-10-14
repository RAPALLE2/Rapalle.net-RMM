# RAPALLE.net RMM Client

Das RMM-Dashboard als eigene App für **Windows 10/11, Linux und Android** – ohne Abhängigkeiten für den Benutzer.
Die fertigen Pakete liegen in `release/` und werden vom Modul **„Client-App“** angeboten
(Profil → *Client-App & Geräte*), genau wie die Agent-Installation:

| Weg | Windows 10/11 | Linux | Android 6+ |
|---|---|---|---|
| **Befehl** | `irm <server>/client-dist/install.ps1 \| iex` (PowerShell) oder `install.cmd` (CMD) | `curl -fsSL <server>/client-dist/install.sh \| sh` | – |
| **Paket** | `.zip` (exe zum Entpacken) | `.tar.gz`, `.tar` (mit `install.sh`) | – |
| **Datei** | `.exe` (startet direkt), `.msi` (Installation, GPO/Intune) | `.deb` | `.apk` |

Die Befehle laden das passende Paket, legen Startmenü-/Desktop-Verknüpfungen an und tragen die
Server-Adresse gleich ein – man muss sich nur noch anmelden.

## Funktionen

| | Windows | Linux | Android |
|---|---|---|---|
| Dashboard als App-Fenster („kleiner Browser“) | Edge im App-Modus (immer vorhanden) | Chromium/Chrome/Edge/Brave im App-Modus, sonst Firefox | eingebaute WebView |
| Anmeldung speichern (Gerätetoken, kein Passwort) | Windows-Anmeldeinformationen | Schlüsselbund (`secret-tool`), sonst Datei (0600) | privater App-Speicher |
| 2FA / SSO (AD) | ✅ | ✅ | ✅ |
| Relay als Laufwerk | Netzlaufwerk (`net use`, WebDAV) | Dateimanager (`gio mount`) | – (Android erlaubt das nicht; Explorer im Dashboard) |
| Remote-Shell im eigenen Fenster | ✅ | ✅ | im App-Fenster |
| Fernbild per Remotedesktop / VNC | mstsc / VNC-Viewer | FreeRDP/Remmina / vncviewer | „Remote Desktop“ / bVNC |
| Autostart | ✅ | ✅ | – |
| Gerät abmelden (Profil → Meine Geräte) | sofort | sofort | sofort |
| WireGuard-VPN | später | später | später |

## Aufbau

```
src/client/
├── build_installers.py   baut ALLE Pakete nach release/ (wie src/backend/app/build_installers.py beim Agenten)
├── desktop/              Windows + Linux: reines Python (nur Standardbibliothek, Oberfläche tkinter)
│   ├── rapalle-client.py         Startdatei
│   └── rapalle_client/           api.py, store.py, secrets_store.py, browser.py, features.py, autostart.py, gui.py
├── android/              native Android-App (Java, WebView) - AndroidManifest.xml, src/, assets/login.html, res/
│   └── signing/          dauerhafter Signier-Schlüssel (Updates müssen gleich signiert sein!)
├── resources/            Programm-Symbole (aus dem RMM-Logo)
├── release/              FERTIGE PAKETE (werden ausgeliefert)
└── tests/test_logic.py   Tests (+ optional gegen ein laufendes Backend)
```

## Neu bauen

Auf einem Linux-Bausystem (z. B. Ubuntu 24.04) – baut alles, was mit den vorhandenen Werkzeugen geht:

```bash
sudo apt install wine64 wixl openjdk-17-jdk-headless android-sdk-platform-23 aapt apksigner zipalign dalvik-exchange
# Windows-Python mit PyInstaller für Wine (einmalig), z.B. nach /opt/winpy:
#   https://github.com/astral-sh/python-build-standalone  (…-x86_64-pc-windows-msvc-install_only.tar.gz)
#   wine64 /opt/winpy/python/python.exe -m pip install pyinstaller
python3 src/client/build_installers.py                 # alles
python3 src/client/build_installers.py --targets deb,tar,apk   # nur einzelne
```

Auf **Windows** lassen sich `.exe`, `.zip` (und mit wixl/WiX `.msi`) direkt bauen:
`py -m pip install pyinstaller && py src\client\build_installers.py --targets exe,zip`.
Alternativ baut der GitHub-Workflow **„Client-App bauen“** (Actions) alle Pakete automatisch.

Version ändern: `desktop/rapalle_client/__init__.py` (`__version__`). Android übernimmt sie beim Bauen.

Tests: `cd src/client && python3 -m pytest tests`
(gegen ein Backend zusätzlich `RMM_TEST_SERVER=… RMM_TEST_USER=… RMM_TEST_PASS=…`).

## Hinweise
- **Windows SmartScreen:** Die `.exe`/`.msi` sind nicht mit einem gekauften Code-Signing-Zertifikat signiert –
  beim ersten Start „Weitere Informationen → Trotzdem ausführen“.
- **Windows + HTTP:** Netzlaufwerke über WebDAV verbindet Windows standardmäßig nur per HTTPS
  (sonst `BasicAuthLevel = 2` unter `HKLM\SYSTEM\CurrentControlSet\Services\WebClient\Parameters`).
- **Android:** Installation aus „unbekannten Quellen“ erlauben. Der Signier-Schlüssel liegt in
  `android/signing/` – nicht verlieren und nicht ändern, sonst lassen sich Updates nicht über alte Versionen installieren.
- **Linux:** braucht `python3` und `python3-tk` (die `.deb` und der Befehl installieren sie mit);
  für das Laufwerk `gio` (bei GNOME/KDE/XFCE vorhanden).

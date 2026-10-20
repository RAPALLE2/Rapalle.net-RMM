# RAPALLE.net RMM Client

Das RMM-Dashboard als eigene App für **Windows 10/11, Linux und Android** – ohne Abhängigkeiten für den Benutzer.
Die fertigen Pakete liegen in `release/` und werden vom Modul **„Client-App“** angeboten
(Profil → *Client-App & Geräte*), genau wie die Agent-Installation:

| Weg | Windows 10/11 | Linux | Android 6+ |
|---|---|---|---|
| **Befehl** | `irm <server>/client-dist/install.ps1 \| iex` (PowerShell) oder `install.cmd` (CMD) | `curl -fsSL <server>/client-dist/install.sh \| sh` | – |
| **Paket** | `.zip` (exe zum Entpacken) | `.tar.gz`, `.tgz`, `.tar` (mit `install.sh`) | – |
| **Datei** | `.exe` (startet direkt), `.msi` (Installation, GPO/Intune) | `.deb` (Debian/Ubuntu), `.rpm` (Fedora/RHEL/openSUSE), `.pkg.tar.xz` (Arch) | `.apk` |

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
| Fernbild-Ziel wählbar (Gateway, lokale IP, letzte, eigene + Port) | ✅ | ✅ | ✅ |
| Autostart | ✅ | ✅ | – |
| Gerät abmelden (Profil → Meine Geräte) | sofort | sofort | sofort |
| WireGuard-VPN | später | später | später |

## Updates
- **Automatisch:** Nach dem Anmelden prüft die App, ob auf dem Server eine neuere Version liegt, und installiert sie
  (Häkchen „Automatisch aktualisieren“, Standard an). Android fragt nach und lädt die neue APK über den Browser.
- **Von Hand:** Knopf „Nach Updates suchen“ (Android: Menü).
- Die App erkennt ihre Installationsart selbst: einzelne `.exe` (Austausch + Neustart), `.msi` unter „Programme“
  (stilles MSI-Update), `.deb`/`.rpm`/`.pkg.tar.xz` (Admin-Abfrage per `pkexec`: apt / dnf·zypper·yum / pacman),
  `.tar.gz` (Dateien ersetzen, ohne Admin).
  Installierte Pakete enthalten dafür die Datei `.rapalle-install`; ein Start aus dem Quellcode bekommt **keine** Auto-Updates.

## Pakete auf dem Server bauen (wie die Agent-Pakete)
*Module → Client-App → Einstellungen → „Client-Pakete bauen“* baut alles direkt aus `src/client` und legt die Pakete
nach `backend/storage/client/` (Vorrang vor den mitgelieferten, übersteht Server-Updates). Vorher wählst du:
**Version beibehalten** (vorhandene Pakete dieser Version werden überschrieben – installierte Apps aktualisieren sich dann nicht)
oder **neue Version** (ältere Pakete werden entfernt, installierte Apps holen sich das Update automatisch). Live-Log und „Stopp“ inklusive.

**Docker-Betrieb (alle Formate):** Beim Aktivieren des Moduls legt das RMM einmalig das Bau-Image
`rapalle/rmm-client-builder:<kennung>` an (Ubuntu 24.04 + Wine + Windows-Python mit PyInstaller + wixl + rpmbuild + JDK 17 +
Android-Werkzeugen, ca. 1,5–2 GB; Fortschritt in den Modul-Einstellungen). Gebaut wird je Knopfdruck in einem Wegwerf-Container
ohne Netzwerk. Das RMM-Image selbst bleibt klein; Modul aus → Bau-Image wird entfernt. Fehlt das Image (z. B. nach
`docker image prune`), legt das Modul es beim nächsten Start neu an; „Neu vorbereiten“ erzwingt es.
Voraussetzung: Docker-Socket im RMM-Container (`/var/run/docker.sock`, steht so in `docker-compose.yml`).

**Ohne Docker (nativ):** Linux baut `.deb/.tar/.tar.gz/.tgz/.pkg.tar.xz` (+ `.rpm`, `.exe/.msi`, `.apk`, wenn die Werkzeuge
installiert sind), Windows zusätzlich `.exe/.zip/.msi` (PyInstaller per pip und WiX als Download holt das Modul beim Aktivieren).

Von Hand: `python3 src/client/build_installers.py [--targets exe,msi,…] [--out DIR] [--set-version 1.2.3]`.

## RMM-Konsole
Knopf **„RMM-Konsole“** (Android: Menü) öffnet die Kommandozeile des RMM (Modul „RMM-Konsole“, Recht „RMM-Konsole benutzen“):
Geräte, Live-Metriken (`top`, btop-ähnlich), Terminal auf Geräten (`shell`), Befehle (`exec`), Prozesse, Neustart/Herunterfahren,
Agent neu starten, Dateien, Docker – mit Tab-Vervollständigung. Dieselbe Konsole gibt es per SSH (`ssh <benutzer>@<server> -p 2222`)
und im Dashboard.

## Kontrollfenster
- Jede länger laufende Aktion zeigt unten eine Ladeleiste mit **„■ Stopp“** (bricht Befehle/Downloads ab).
- **„Fenster schließen, wenn Dashboard aufgeht“:** schließt das Kontrollfenster 4 s nach dem Öffnen des Dashboards –
  mit Knopf „Offen lassen“.

## Fernbild: wohin verbinden?
Nach der Gerätewahl fragt die App, wohin verbunden wird – vorgewählt ist die gespeicherte Voreinstellung,
sonst die **lokale IP des Geräts**:

| Ziel | Port |
|---|---|
| **RMM-Gateway** – der Server leitet weiter, erreichbar nur von deiner IP (braucht Modul „Netzwerk“ + laufenden Agenten) | automatisch |
| **Lokale IP des Geräts** – wie vom Agenten gemeldet (gleiches Netz / VPN ins Firmennetz) | Standard (3389 / 5900) oder eigener |
| **Letzte Verbindung** – genau wie beim letzten Mal | wie beim letzten Mal |
| **Eigene Adresse** – IP oder Name (VPN, Portfreigabe, DNS) | Standard oder eigener |

„Als Standard für dieses Gerät merken“ speichert die Wahl **pro Benutzer und Zielgerät** (und getrennt für RDP/VNC) auf
dem Server – sie gilt damit auf allen Geräten, auf denen du die App nutzt. Für direkte Verbindungen muss der Agent nicht
online sein, deshalb zeigt die Auswahl beim Fernbild auch Offline-Geräte.

## Aufbau

```
src/client/
├── build_installers.py   baut ALLE Pakete (exe, zip, msi, deb, rpm, pkg, tar, tgz, apk) – wie src/backend/app/build_installers.py beim Agenten
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

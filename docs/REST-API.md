# REST-API – Dokumentation

Mit der REST-API lässt sich **alles steuern, was das Web-Dashboard kann**: Benutzer, Rechte, Gruppen,
Clients/Agenten, Module, Apps, Einstellungen, Nodes, Spaces, Tickets … Die API ist ein **Modul**
(*Modul-App → „REST-API“ → aktivieren*). Ist es aus, gibt es `/api/v1` nicht.

- Basis-Adresse: `https://<dein-server>/api/v1`
- Format: JSON (UTF-8)
- Python-Beispiel: [`examples/rmm_api_client.py`](examples/rmm_api_client.py) – nur Standardbibliothek

---

## 1. Zugang einrichten

### API-Konto anlegen (empfohlen)
*Einstellungen → Benutzer → Benutzer anlegen → Vorlage „API-Benutzer“*

- API-Konten haben **kein Passwort** und **keine Erstanmeldung**.
- Stattdessen erzeugt der Server einen **API-Schlüssel** und zeigt ihn **genau einmal** an:
  `rmmak_eyJhbGciOiJIUzI1NiIs…` (zufälliger Wert, als JWT signiert; gespeichert wird nur sein Hash).
- API-Konten können sich **nicht im Dashboard** anmelden – nur an der API.
- Neuer Schlüssel / Sperren: *Modul-App → REST-API → Einstellungen → „API-Konten“*.
  Ein neuer Schlüssel macht den alten **und alle Anmeldungen** des Kontos sofort ungültig.

### Schlüssel-Rotation
Wie beim Code lässt sich auch für API-Schlüssel eine Laufzeit einstellen:
*Einstellungen → „API-Schlüssel automatisch rotieren nach“* (nie · 1 h · 8 h · 24 h · 7 Tage · 35 Tage · 1 Jahr).

- Ist ein Schlüssel älter als das Intervall, liefert die **nächste Anmeldung** zusätzlich einen **neuen Schlüssel**:
  ```json
  {"token": "…", "api_key_rotated": true, "new_api_key": "rmmak_…", "old_api_key_valid_until": 1767229200}
  ```
- Der bisherige Schlüssel gilt danach noch die **Übergangszeit** (*„Alter Schlüssel gilt nach einer Rotation noch … Minuten“*,
  Standard 60) – Zeit genug, den neuen zu speichern. Danach wird er abgewiesen.
- Meldet sich ein Skript in der Übergangszeit noch mit dem alten Schlüssel an, enthält die Antwort
  `"api_key_replaced": true` als Warnung.
- **„Alle Schlüssel rotieren“** (*Register „API-Konten“*): Jedes Konto bekommt bei seiner nächsten Anmeldung einen neuen Schlüssel.
- Sofort ungültig machen (ohne Übergang): „Neuer Schlüssel“ bzw. „Sperren“ beim jeweiligen Konto.

**Wichtig für Skripte:** `new_api_key` aus der Login-Antwort immer auswerten und speichern. Das Python-Beispiel macht das
automatisch (`on_key_rotated` bzw. `RMM_API_KEY_FILE`).

Alternativ kann auch ein **normales Konto** die API nutzen, wenn es das Recht **„REST-API benutzen“** hat;
es meldet sich dann mit Passwort (und ggf. 2FA-Code) an.

### Rechte
Jeder Aufruf braucht
1. das Recht **„REST-API benutzen“** (`use_api`) – bei der Vorlage „API-Benutzer“ automatisch dabei, und
2. **dasselbe Recht wie im Dashboard** für die jeweilige Aktion, z. B.

| Aktion | nötiges Recht |
|---|---|
| Clients sehen | Clients sehen (`access_clients`) |
| Client löschen | Client löschen (`c_delete`) |
| Befehl auf Client ausführen | Terminal (`c_terminal`) |
| Benutzer anlegen | Benutzer anlegen (`create_users`) |
| Module schalten | Module verwalten (`manage_modules`) |

Fehlt ein Recht, antwortet die API genau wie das Dashboard (meist `403`, bei unsichtbaren Clients `404`).
Admins dürfen alles. Rechte vergibst du wie gewohnt unter *Rechte & Gruppen*.

---

## 2. Betriebsarten

Einstellbar unter *Modul-App → REST-API → Einstellungen → Betriebsart*.

| Betriebsart | Anmeldung | bei jeder Anfrage mitschicken |
|---|---|---|
| **Mit Benutzer (secure)** – Standard | `POST /auth/login` → langer Code | `Authorization: Bearer <code>` (oder `X-API-Token: <code>`) |
| **Mit Benutzer** | `POST /auth/login` → Sitzung | Cookie `rmm_api_session` oder `X-API-Session: <session>` |
| **Ohne Benutzer** | keine – alles läuft als eingestellter API-Benutzer | nichts |

- **Gültigkeit des secure-Codes:** 1 h · 8 h · 24 h · 7 Tage · 35 Tage · 1 Jahr · unbegrenzt.
  Der Code ist ein zufälliger Wert, als JWT signiert und zusätzlich verschlüsselt; der Server vergleicht ihn bei
  jeder Anfrage mit dem gespeicherten Hash.
- **Sitzung („mit Benutzer“):** läuft nach der eingestellten Zeit **ohne Aufruf** ab.
- **„Ohne Benutzer“** nur zusammen mit einer **IP-Freigabeliste** verwenden (Einstellung „Erlaubte IP-Adressen“).
- **Alle Codes/Sitzungen sofort ungültig machen:** *Einstellungen → „Anmeldungen“ → „Alle sofort ablaufen lassen“*.
- Jeder Aufruf landet im **Aufrufprotokoll** (*Einstellungen → „Protokoll“*).

---

## 3. Anmelden

```http
GET  /api/v1/status                 → {"mode": "secure", ...}   (ohne Anmeldung)
POST /api/v1/auth/login
Content-Type: application/json

{"username": "apibot", "api_key": "rmmak_…", "label": "Backup-Skript"}
```

Normales Konto statt API-Konto: `{"username": "max", "password": "…", "code": "123456", "realm": "local"}`
(`code` nur bei aktivierter 2FA, `realm` für Verzeichnis-/AD-Anmeldung).

Antwort (secure):
```json
{"mode": "secure", "token": "gAAAAABm…", "token_type": "Bearer", "expires_at": 1767225600, "lifetime": "8h",
 "user": {"id": "…", "username": "apibot", "role": "api"}}
```
Bei fälliger Schlüssel-Rotation zusätzlich `new_api_key`, `api_key_rotated`, `old_api_key_valid_until` (siehe 1.).

Weitere Aufrufe:
```http
GET /api/v1/clients
Authorization: Bearer gAAAAABm…
```

Abmelden: `POST /api/v1/auth/logout`. Eigene Rechte: `GET /api/v1/auth/me`.

### Fehler
| Status | Bedeutung |
|---|---|
| 400 | ungültige Eingabe |
| 401 | nicht angemeldet / Code abgelaufen oder widerrufen → neu anmelden |
| 403 | Recht fehlt (oder IP nicht freigegeben) |
| 404 | nicht gefunden bzw. nicht sichtbar |
| 409 | Voraussetzung fehlt (z. B. Modul aus) |
| 503 | Modus „ohne Benutzer“ ohne eingestellten API-Benutzer |

Fehlertext immer in `{"detail": "…"}`.

---

## 4. Endpunkte

Vollständiger, aktueller Katalog: **`GET /api/v1/routes`** (Kurzwege + alle Dashboard-Routen).

### Kurzwege
| Methode | Pfad | Zweck |
|---|---|---|
| GET, POST | `/users` | Benutzer auflisten / anlegen (`role: "api"` = API-Konto, Antwort enthält `api_key`) |
| DELETE | `/users/{id}` | Benutzer löschen |
| GET, PUT | `/users/{id}/groups` | Gruppen eines Benutzers |
| GET, POST · PUT, DELETE | `/groups` · `/groups/{id}` | Gruppen |
| GET | `/permissions` | Rechte-Katalog |
| GET, PUT | `/permissions/{user\|group}/{id}` | Rechte (Grants) lesen / setzen |
| GET | `/clients` | Clients (Agenten) |
| GET, PUT, DELETE | `/clients/{id}` | Client lesen / ändern / löschen |
| POST | `/clients/{id}/exec` | Befehl ausführen `{"command": "...", "shell": "auto\|cmd\|powershell", "elevated": false}` |
| GET · POST | `/clients/{id}/processes` · `/processes/kill` | Prozesse |
| GET | `/clients/{id}/files?path=` · `/metrics` · `/notes` | Dateien, Messwerte, Notizen |
| POST | `/clients/bulk-exec` | Befehl auf mehreren Clients |
| POST | `/agents/{id}/update` · `/agents/{id}/uninstall` · `/agents/update-all` | Agenten |
| GET | `/hierarchy` | Mandanten / Standorte / Ordner |
| GET · POST | `/modules` · `/modules/{id}/enable` · `/disable` | Module |
| GET, PUT · PUT | `/modules/{id}/settings` · `/features` | Modul-Einstellungen / Teilfunktionen |
| GET | `/apps` | Apps der aktiven Module |
| GET, PUT | `/settings` | Allgemeine Einstellungen |
| GET · POST | `/nodes` · `/nodes/{client}/enable\|disable\|update` | Nodes |
| POST, DELETE | `/nodes/{client}/modules/{modul}` | Node-Module |
| GET | `/audit` | Audit-Protokoll |
| GET · POST | `/system/update` · `/system/restart` | Server-Update / Neustart |

### Brücke – jede Dashboard-Funktion
Jede Route des Dashboards `/api/<pfad>` ist als **`/api/v1/call/<pfad>`** erreichbar – mit denselben Rechten:

```
/api/spaces/fs/list?path=Mein%20Space   →   /api/v1/call/spaces/fs/list?path=Mein%20Space
/api/tickets                            →   /api/v1/call/tickets
/api/admin/groups                       →   /api/v1/call/admin/groups
```

Nicht per REST steuerbar sind nur Live-Funktionen, die im Dashboard über die Live-Verbindung laufen
(Terminal-Sitzung, Live-Bildschirm). Einzelbefehle gehen über `/clients/{id}/exec`.

---

## 5. Beispiele

### curl (secure)
```bash
API=https://rmm.firma.de/api/v1
TOKEN=$(curl -s -X POST $API/auth/login -H "Content-Type: application/json" \
        -d '{"username":"apibot","api_key":"rmmak_…","label":"Shell"}' | jq -r .token)

curl -s -H "Authorization: Bearer $TOKEN" $API/clients | jq '.[] | {hostname, online}'
curl -s -H "Authorization: Bearer $TOKEN" -X POST $API/modules/spaces/enable
curl -s -H "Authorization: Bearer $TOKEN" -X POST $API/auth/logout
```

### PowerShell
```powershell
$api = "https://rmm.firma.de/api/v1"
$login = Invoke-RestMethod -Method Post -Uri "$api/auth/login" -ContentType "application/json" `
         -Body (@{ username = "apibot"; api_key = "rmmak_…" } | ConvertTo-Json)
$h = @{ Authorization = "Bearer $($login.token)" }
Invoke-RestMethod -Uri "$api/clients" -Headers $h | Select-Object hostname, online
```

### Python
[`examples/rmm_api_client.py`](examples/rmm_api_client.py) erkennt die Betriebsart selbst, meldet sich an
und bei abgelaufenem Code automatisch neu an.

```python
from pathlib import Path
from rmm_api_client import RmmApi, RmmApiError

KEY_FILE = Path("/etc/rmm/apibot.key")              # Schlüssel liegt in einer Datei (chmod 600)

def save_key(new_key: str) -> None:                 # wird bei einer Schlüssel-Rotation aufgerufen
    KEY_FILE.write_text(new_key + "\n")

with RmmApi("https://rmm.firma.de", "apibot", api_key=KEY_FILE.read_text().strip(),
            label="Inventar", on_key_rotated=save_key) as rmm:
    # 1) alle Clients mit Status
    for c in rmm.clients():
        print(c["hostname"], "online" if c.get("online") else "offline")

    # 2) Offline-Clients
    print("offline:", [c["hostname"] for c in rmm.offline_clients()])

    # 3) Befehl auf allen Windows-Clients, die online sind (Recht "Terminal")
    for c in rmm.clients():
        if c.get("online") and c.get("platform") == "windows":
            try:
                print(c["hostname"], rmm.exec(c["id"], "Get-Date", shell="powershell"))
            except RmmApiError as e:
                print(c["hostname"], "→", e.detail)

    # 4) Modul einschalten (Recht "Module verwalten")
    rmm.enable_module("spaces")

    # 5) Neues API-Konto anlegen (Recht "Benutzer anlegen") - Schluessel kommt EINMAL zurueck
    neu = rmm.post("/users", {"username": "monitoring", "display_name": "Monitoring", "role": "api"})
    print("Schlüssel für", neu["username"], ":", neu["api_key"])

    # 6) beliebige Dashboard-Funktion über die Brücke
    print(rmm.call("GET", "/spaces/fs/list", path="Mein Space"))
```

Als Kommandozeile:
```bash
export RMM_SERVER=https://rmm.firma.de RMM_USER=apibot RMM_API_KEY_FILE=/etc/rmm/apibot.key
python3 docs/examples/rmm_api_client.py clients     # neuer Schlüssel nach Rotation landet automatisch in der Datei
python3 docs/examples/rmm_api_client.py exec <client-id> "hostname"
python3 docs/examples/rmm_api_client.py routes
python3 docs/examples/rmm_api_client.py call GET /audit
```
(`RMM_API_KEY=rmmak_…` statt Datei geht auch – bei einer Rotation wird der neue Schlüssel dann nur angezeigt.
`RMM_INSECURE=1` nur bei selbst signiertem Zertifikat.)

---

## 6. Sicherheitshinweise
- API-Schlüssel und secure-Codes wie Passwörter behandeln (nicht ins Repository, nicht in Logs).
- Für Automatisierung eigene API-Konten mit **nur den nötigen Rechten** anlegen statt Admin-Konten.
- Server per **HTTPS** betreiben; „Ohne Benutzer“ nur mit IP-Freigabeliste.
- Schlüssel regelmäßig **automatisch rotieren** lassen (z. B. 35 Tage) und Skripte `new_api_key` speichern lassen.
- Bei Verdacht: Schlüssel unter „API-Konten“ neu erzeugen oder sperren, „Alle sofort ablaufen lassen“.

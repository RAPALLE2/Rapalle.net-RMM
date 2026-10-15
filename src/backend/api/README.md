# RAPALLE.net RMM – REST-API (`/api/v1`)

Steuert alles, was das Web-Dashboard kann. Wird vom **Modul „REST-API“** geladen (Modul-App → aktivieren);
der Core startet diesen Ordner nie selbst. Einstellungen, aktive Anmeldungen und das Aufrufprotokoll:
*Modul-App → REST-API → Einstellungen*.

## Zugang
1. Konto anlegen mit der Vorlage **„API-Benutzer“**: kein Passwort, keine Erstanmeldung – der Server erzeugt einen
   JWT-signierten **API-Schlüssel** (`rmmak_…`, nur einmal angezeigt; neu erzeugen/sperren unter Modul-App → REST-API →
   API-Konten). Alternativ einem normalen Konto das Recht **„REST-API benutzen“** geben (Anmeldung mit Passwort).
   **Rotation:** Einstellung „API-Schlüssel automatisch rotieren nach“ – fällige Schlüssel werden bei der Anmeldung
   ersetzt (`new_api_key` in der Antwort), der alte gilt noch die Übergangszeit; Knopf „Alle Schlüssel rotieren“.
2. Dem Konto die Rechte geben, die es per API braucht – **wie im Dashboard** (z. B. „Client löschen“, um Clients
   zu löschen). Ohne das passende Recht antwortet die API mit 403/404, genau wie das Dashboard. Admins dürfen alles.

## Betriebsarten (Einstellung „Betriebsart“)
| Betriebsart | Anmeldung | Bei jeder Anfrage |
|---|---|---|
| Ohne Benutzer | keine – läuft als eingestellter API-Benutzer (nur mit IP-Freigabe sinnvoll!) | nichts |
| Mit Benutzer | `POST /api/v1/auth/login` → Sitzung | Cookie `rmm_api_session` **oder** Header `X-API-Session: <session>` |
| Mit Benutzer (secure) | `POST /api/v1/auth/login` → langer Code | `Authorization: Bearer <code>` **oder** `X-API-Token: <code>` |

Gültigkeit des secure-Codes: 1 h, 8 h, 24 h, 7 Tage, 35 Tage, 1 Jahr oder unbegrenzt. Der Code ist ein
zufälliger Wert, als JWT signiert und verschlüsselt; in der Datenbank liegt nur sein Hash. „Alle sofort ablaufen
lassen“ in den Einstellungen macht alle Codes/Sitzungen sofort ungültig.

## Beispiel (secure)
```bash
API=https://rmm.firma.de/api/v1
TOKEN=$(curl -s -X POST $API/auth/login -H "Content-Type: application/json" \
        -d '{"username":"apibot","api_key":"rmmak_…","label":"Backup-Skript"}' | jq -r .token)
curl -H "Authorization: Bearer $TOKEN" $API/clients                    # Clients
curl -H "Authorization: Bearer $TOKEN" -X DELETE $API/clients/<id>     # braucht "Client löschen"
curl -H "Authorization: Bearer $TOKEN" -X POST $API/modules/spaces/enable
curl -H "Authorization: Bearer $TOKEN" -X POST $API/auth/logout
```
Normales Konto: `{"username", "password"}` (+ `"code"` bei 2FA, `"realm"` bei AD).
Ausführliche Doku mit Python-Beispiel: `docs/REST-API.md`, `docs/examples/rmm_api_client.py`.

## Endpunkte
- `GET /api/v1/status` – Betriebsart (ohne Anmeldung)
- `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me` (inkl. eigener Rechte)
- `GET /api/v1/routes` – **Katalog**: alle Kurzwege + alle Dashboard-Routen
- Kurzwege: `/users`, `/groups`, `/permissions`, `/permissions/{user|group}/{id}`, `/clients`, `/clients/{id}`
  (+ `/exec`, `/processes`, `/files`, `/metrics`, `/notes`), `/agents/{id}/update|uninstall`, `/agents/update-all`,
  `/hierarchy`, `/modules` (+ `/enable`, `/disable`, `/settings`, `/features`), `/apps`, `/settings`,
  `/nodes` (+ `/enable`, `/disable`, `/update`, `/modules/{id}`), `/audit`, `/system/update`, `/system/restart`
- **Brücke**: jede Dashboard-Route `/api/<pfad>` ist als `/api/v1/call/<pfad>` erreichbar
  (z. B. `/api/spaces/fs/list?path=…` → `/api/v1/call/spaces/fs/list?path=…`).

Nicht per REST: Live-Funktionen, die im Dashboard über die Live-Verbindung laufen (Terminal, Live-Bildschirm).

## Aufbau
`security.py` (Betriebsarten, Codes, Sitzungen) · `bridge.py` (Brücke, gleiche Rechteprüfung wie das Dashboard) ·
`store.py` (Tabellen `api_tokens`, `api_log`, Einstellungen, IP-Freigabe) · `routes.py` (`/api/v1`, `/api/apimod`).

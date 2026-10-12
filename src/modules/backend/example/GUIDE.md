# Eigene Module für das RMM programmieren

Diese Anleitung gehört zum **Beispiel-Modul** (`example`). Es ist absichtlich klein, aber es benutzt
*jede* Möglichkeit des Modulsystems. Kopiere es als Vorlage und ändere es ab.

> **Kurzfassung:** Ein Modul ist ein Ordner mit einer `module.json`. Du legst ihn unter
> `src/modules/<bereich>/<name>/` ab, schaltest ihn in der **Modul-App** ein – fertig.
> Ein Fehler in deinem Modul legt nie das RMM lahm: Es wird automatisch deaktiviert und ein
> Crashbericht gespeichert.

---

## 1. Grundidee

| Regel | Bedeutung |
|---|---|
| **Core läuft ohne Module** | Dashboard, Clients, Updates, Settings, Berechtigungen, Terminal, VNC, Explorer … sind Core. Alles andere ist ein Modul. |
| **Standard: aus** | Neue Module sind deaktiviert, bis ein Administrator sie in der Modul-App einschaltet. |
| **Absturzschutz** | Jeder Aufruf in deinen Code ist abgesichert (Start, Stop, Routen, Socket-Events, Hintergrundaufgaben, Hooks). |
| **Pakete erst beim Aktivieren** | Python-Pakete stehen in der `module.json`, nicht in der `requirements.txt` des Core. |

## 2. Ordnerstruktur

```
src/modules/
  backend/<name>/     Python-Teil   (Routen, Logik, Hintergrundaufgaben)
  frontend/<name>/    JavaScript-Teil (Apps/Fenster, Einstellungsmenü)
  agent/<name>/       (reserviert) Teile, die auf dem Client laufen
  client/<name>/      (reserviert)
  node/<name>/        (reserviert)
```

Ein Modul kann aus **mehreren Teilen** bestehen. Die Teile gehören zusammen, wenn in ihrer
`module.json` dieselbe **`id`** steht. Die Modul-App zeigt nur **ein** Modul (z. B. `sso` mit den
Teilen *Backend* und *Frontend*).

```
src/modules/backend/example/
  module.json              <- Beschreibung (Pflicht)
  module.py                <- Einstiegspunkt mit Hooks (optional)
  example_routes.py        <- ein Router (optional, beliebig viele)
  example_crash_routes.py
  GUIDE.md                 <- diese Anleitung
src/modules/frontend/example/
  module.json
  example.js               <- eine App
```

## 3. Mein erstes Modul in 5 Minuten

1. `src/modules/backend/hallo/` anlegen.
2. `module.json`:
   ```json
   { "schema": 1, "id": "hallo", "name": "Hallo", "version": "1.0.0",
     "description": "Mein erstes Modul", "category": "Entwicklung", "icon": "👋",
     "routers": ["hallo_routes"] }
   ```
3. `hallo_routes.py`:
   ```python
   from fastapi import APIRouter, Depends
   from app.auth import get_current_user

   router = APIRouter()

   @router.get("/api/hallo")
   def hallo(user: dict = Depends(get_current_user)):
       return {"text": f"Hallo {user['username']}!"}
   ```
4. In der **Modul-App** auf **↻ Neu einlesen** klicken, das Modul **aktivieren** – `GET /api/hallo` antwortet.
5. Absturz-Test: `raise RuntimeError("test")` einbauen → Modul wird abgeschaltet, Bericht erscheint.

## 4. `module.json` – alle Felder

```json
{
  "schema": 1,
  "id": "example",                  // Pflicht. a-z, 0-9, _ und -, 2-32 Zeichen, mit Buchstabe beginnend
  "name": "Beispiel-Modul",         // Anzeigename
  "version": "1.0.0",               // Anzeige in der Modul-App
  "description": "…",
  "author": "RAPALLE.net",
  "category": "Entwicklung",        // Gruppe in der Modul-App
  "icon": "🧪",
  "available": true,                // false = in der Modul-App nicht aktivierbar
  "unavailable_reason": "…",        // Text dazu

  "requires": ["anderes-modul"],    // andere Module, die vorher aktiv sein müssen
  "pip": ["paramiko>=3.4"],         // Python-Pakete; werden beim Aktivieren installiert

  "entry": "module",                // Datei (ohne .py) mit den Hook-Funktionen. Standard: module.py, falls vorhanden
  "routers": [                      // Dateien mit einer Variable `router` (FastAPI APIRouter)
    "example_routes",
    { "module": "example_crash_routes", "feature": "crash_demo" }   // nur wenn Teilfunktion an
  ],
  "websockets": [                   // WebSocket-Endpunkte
    { "module": "meine_ws", "path": "/mein/ws", "endpoint": "ws_endpoint" }
  ],
  "hooks": {                        // Ereignis -> Funktionsname in der Entry-Datei
    "startup": "on_startup",
    "client_online": "on_client_online"
  },
  "features": [                     // einzeln schaltbare Teilfunktionen
    { "id": "ticker", "label": "Hintergrund-Ticker", "description": "…", "default": true }
  ],
  "settings": [                     // einfache Einstellungen -> Formular wird automatisch erzeugt
    { "key": "greeting", "label": "Begrüßung", "type": "string", "default": "Hallo" }
  ]
}
```

**Einstellungs-Typen:** `string`, `text`, `number` (`min`/`max`), `bool`, `select` (`options`), `password`
(wird in der Oberfläche maskiert). Gespeichert wird in `data/modules.json`.

> Die module.json aller Teile eines Moduls werden zusammengeführt. Wenn nichts anderes gesagt ist,
> gewinnt der **Backend-Teil**. Dateien/Routen/Hooks stehen im Backend-Teil, Apps im Frontend-Teil.

## 5. Der Einstiegspunkt `module.py`

Alle Funktionen sind optional. Sie dürfen normal oder `async` sein.

```python
async def on_startup(ctx):            # bei jedem Laden (Backend-Start UND Aktivieren)
    ctx.spawn("ticker", lambda: _loop(ctx))

async def on_shutdown(ctx): ...       # beim Deaktivieren und beim Beenden
def on_enable(ctx): ...               # NUR wenn der Benutzer das Modul einschaltet
def on_disable(ctx): ...              # NUR wenn der Benutzer es ausschaltet
async def on_client_online(ctx, data): ...   # Ereignis-Hooks: ctx + Daten
```

### Ereignisse (Trigger)

Die Ereignis-Hooks nutzen **dieselben Trigger wie die Webhook-/Mail-Benachrichtigungen**.
`data = {"event": …, "client_id": …, "notification": …}`

`client_online`, `client_offline`, `client_new`, `agent_update`, `cpu_high`, `ram_high`, `disk_high`,
`temp_high`, `warranty_expiring`, `warranty_expired`, `website_up`, `website_down`, `user_login`,
`login_failed`

Außerdem: `module.enabled` / `module.disabled` – ein *anderes* Modul wurde geschaltet
(`data = {"module": "<id>"}`).

## 6. Der Kontext `ctx`

| Name | Zweck |
|---|---|
| `ctx.id`, `ctx.name`, `ctx.version` | Infos zum Modul |
| `ctx.dir` | Ordner des Backend-Teils |
| `ctx.data_dir` | **Eigener** Datenordner (`data/modules/<id>/`) – hier eigene Dateien ablegen |
| `ctx.log("…")` | Zeile mit Modulname ins Log |
| `ctx.setting("key", default)` | Einstellung lesen |
| `ctx.feature("id")` | Ist die Teilfunktion eingeschaltet? |
| `ctx.spawn("name", fn)` | Dauerschleife starten (`fn` ist eine `async def` ohne Argumente). Wird beim Deaktivieren automatisch beendet. |
| `ctx.sio_on("event", handler)` | Socket.IO-Event registrieren (wird beim Deaktivieren entfernt) |
| `ctx.sio` / `ctx.api` | Socket.IO-Server / FastAPI-App (selten nötig) |
| `ctx.is_enabled("id")`, `ctx.use("id", "datei")` | Andere Module ansprechen |
| `await ctx.emit("event", **daten)` | Ereignis an andere Module senden |

### Außerhalb der Hooks (z. B. in Routen)

```python
from app import modules
modules.setting("example", "greeting")      # Einstellung
modules.feature("example", "ticker")        # Teilfunktion
```

## 7. Eigene Dateien im Modul

Alle `.py`-Dateien im Backend-Ordner sind Teil eines Pakets namens `rmm_mod_<id>`. Untereinander importierst du so:

```python
from . import meine_logik                    # relativ (empfohlen)
from rmm_mod_example import meine_logik      # absolut
```

Den **Core** importierst du normal: `from app import db`, `from app.auth import get_current_user`,
`from app.sockets import sio`, `from app.paths import DATA_DIR` …

## 8. Core greift optional auf ein Modul zu

Der Core darf **nie** hart von einem Modul abhängen. Er benutzt `modules.optional()`: Ist das Modul aus,
ist jeder Aufruf ein stilles No-Op.

```python
from app import modules
recording = modules.optional("recordings", "recording")
recording.record_frame(...)      # tut nichts, wenn 'recordings' aus ist
if recording: ...                # True, wenn das Modul aktiv ist
```

## 9. Abhängigkeiten zwischen Modulen

`"requires": ["recordings"]` → Das Modul lässt sich nur aktivieren, wenn `recordings` aktiv ist
(die Modul-App bietet an, es mitzuaktivieren). Wird ein benötigtes Modul abgeschaltet, fragt die App,
ob die abhängigen Module mit abgeschaltet werden sollen.

## 10. Was bei einem Absturz passiert

Unbehandelte Ausnahme in …

* einer **Route** → Antwort `500` mit Hinweistext, das Modul wird deaktiviert.
* einem **Hook / `startup`** → Aktivierung wird abgebrochen bzw. das Modul abgeschaltet.
* einer **Hintergrundaufgabe** (`ctx.spawn`) oder einem **Socket-Event** → Modul wird deaktiviert.
* einer **Frontend-App** (Import/Zeichnen) → nur dieses Fenster zeigt den Fehler, das Modul wird deaktiviert.

Jedes Mal wird ein **Crashbericht** (mit Traceback) unter `logs/modules/<id>/` gespeichert und in der
Modul-App im Reiter *Abstürze* angezeigt. `HTTPException` zählt **nicht** als Absturz – damit gibst du
normale Fehlermeldungen zurück (`raise HTTPException(400, "…")`).

In der Modul-App lässt sich einstellen, nach wie vielen Abstürzen (innerhalb von 10 Minuten) abgeschaltet wird (Standard: 1).

## 11. Regeln & Tipps

* **Kein Eingriff in den Core.** Ein Modul fügt nur hinzu. Brauchst du etwas vom Core, das fehlt, erweitere den Core
  (und lass Module weiterhin optional).
* **Datenbank:** Eigene Tabellen am besten in `on_startup` mit `CREATE TABLE IF NOT EXISTS` anlegen. Daten bleiben beim
  Deaktivieren erhalten.
* **Nicht blockieren:** In `async`-Code keine langen synchronen Aufrufe (`time.sleep`, große Dateien). Nutze
  `await asyncio.sleep()` oder `await db.call(funktion, …)`.
* **Aufräumen:** Alles, was du über `ctx.spawn` / `ctx.sio_on` registrierst, räumt das System selbst weg. Eigene
  Ressourcen (Sockets, Dateien) schließt du in `on_shutdown`.
* **Rechte:** Endpunkte schützt du wie im Core: `Depends(get_current_user)`, `require_admin(user)`, `require_perm(user, "…")`.
* **Neu laden:** Nach Code-Änderungen das Modul in der Modul-App aus- und wieder einschalten.

## 12. Node-Teil (Docker-Dienste auf einem Client)

Eine **Node** ist ein eigener Prozess auf einem Client (`src/node/main.py`), der **unabhängig vom Agenten** läuft.
Sie prüft Docker (oder meldet, dass es installiert werden muss) und führt **Node-Module** aus. Der Node-Teil eines Moduls liegt in
`src/modules/node/<name>/` (gleiche `id` wie Backend-/Frontend-Teil) und wird vom Backend **an die Node geschickt**.

```
src/modules/node/example/
  module.json          <- Abschnitt "node" beschreibt, WIE die Node es ausführt
  docker-compose.yml   <- wird mit `docker compose -p rmm-<id> up -d` gestartet
```

### module.json (Node-Teil)

```json
{
  "schema": 1, "id": "example", "version": "1.0.0",
  "node": {
    "kind": "compose",                 // "compose" (docker-compose.yml) oder "python" (node_module.py)
    "compose": "docker-compose.yml",
    "requires_docker": true,
    "autostart": true,                 // nach der Bereitstellung gleich starten
    "actions": ["up", "down", "restart", "status", "logs"],   // erlaubte Aktionen
    "params": [ { "key": "port", "label": "Port auf der Node", "type": "number", "default": 18080 } ]
  }
}
```

* **Parameter** (`params`) landen als `KEY=wert` in einer `.env` neben der compose-Datei → in der compose-Datei als `${PORT:-18080}`.
* **kind `python`:** Die Node lädt `node_module.py` und ruft `start(ctx)`, `stop(ctx)` und `action(ctx, name, data)` auf
  (`ctx.run([...])`, `ctx.log()`, `ctx.params`, `ctx.data_dir`) – z. B. für Dinge, die kein Container sind.
* Es werden **nur** die in `actions` erlaubten Aktionen ausgeführt. Die Node hat keine freie Shell.

### Vom Backend aus steuern (`ctx.nodes`)

```python
await ctx.nodes.deploy(client_id, {"port": 18080})   # Node-Teil dieses Moduls installieren (+ starten)
await ctx.nodes.action(client_id, "restart")          # up | down | restart | status | logs
await ctx.nodes.remove(client_id)
await ctx.nodes.list()                                # alle Nodes mit Zustand (online, docker, modules)
```

Der Node-Teil wird nur verschickt, wenn das Modul **aktiv** ist. Wird das Modul ausgeschaltet, entfernt das Backend seinen
Node-Teil automatisch von allen Nodes. Zusätzlich gibt es die Hooks `node_online`, `node_offline`, `node_removed`
(`data = {"client_id"}`). Auch ohne Code kann ein Administrator Node-Module in der **Node-Oberfläche**
(Client-Panel → Update-Knopf → „Node verwalten“) bereitstellen.

### So läuft es

```
Backend ──node-install──> Agent ──lädt src/node/ als ZIP, richtet Dienst ein──> Node-Prozess (eigener Dienst)
Backend <──eigene Verbindung /node (eigenes Token)────────────────────────────  Node-Prozess
Backend ──module-install (Dateien aus src/modules/node/<id>/)──> Node ──docker compose up -d──> Container
```

## 13. Frontend

Siehe `src/modules/frontend/example/GUIDE.md`.

## 14. Checkliste vor dem Teilen

- [ ] `module.json` ist gültiges JSON, `id` stimmt in allen Teilen überein
- [ ] Modul lässt sich ein- und ausschalten, ohne dass Fehler im Log stehen
- [ ] Nach dem Ausschalten sind die Routen weg (404)
- [ ] Absichtlicher Fehler → Modul wird abgeschaltet, Bericht erscheint, RMM läuft weiter
- [ ] Python-Pakete stehen in `pip`, nicht im Core
- [ ] Node-Teil: nur erlaubte `actions`, Parameter statt fest verdrahteter Werte, `docker compose config` ist gültig

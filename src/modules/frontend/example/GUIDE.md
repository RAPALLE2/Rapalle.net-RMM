# Frontend-Teil eines Moduls

Der Frontend-Teil liegt in `src/modules/frontend/<name>/` und gehört über die gemeinsame `id` zum
Backend-Teil (siehe `src/modules/backend/example/GUIDE.md`). Er wird **nur geladen, wenn das Modul aktiv ist** – und eine App erst
beim ersten Öffnen.

## module.json (Frontend)

```json
{
  "schema": 1,
  "id": "example",
  "name": "Beispiel-Modul",
  "version": "1.0.0",
  "apps": [
    {
      "id": "example",              // eindeutige App-ID
      "label": "Beispiel",          // Name im Startmenü
      "icon": "🧪",
      "title": "Beispiel-Modul",    // Fenstertitel
      "entry": "example.js",        // Datei des Moduls
      "export": "renderExample",    // exportierte Funktion (body, win) => …
      "w": 560, "h": 460,           // Fenstergröße
      "perm": "use_example",        // optional: benötigtes Recht (Admins immer erlaubt)
      "feature": "ticker",          // optional: nur sichtbar, wenn Teilfunktion an
      "startmenu": false            // optional: nicht im Startmenü zeigen
    }
  ],
  "init": { "entry": "index.js", "export": "init" },                 // optional: läuft einmal beim Laden
  "settings_ui": { "entry": "settings.js", "export": "renderSettings" }  // optional: eigenes Einstellungsmenü
}
```

## Eine App schreiben

```js
import { moduleRequest } from "/js/app/modules.js";   // Backend-Aufruf (JSON, wirft bei Fehlern)
import { esc } from "/js/app/utils.js";

export function renderExample(body, win) {
  body.innerHTML = `<button id="b">Los</button><pre id="o"></pre>`;
  body.querySelector("#b").onclick = async () => {
    const r = await moduleRequest("/api/example/hello");
    body.querySelector("#o").textContent = JSON.stringify(r);
  };
}
```

* **Core importieren** immer mit **absolutem Pfad**: `/js/app/…` oder `/js/feature/…`.
* **Eigene Dateien** relativ: `import { x } from "./helfer.js";`
* **Andere Module** nie direkt importieren. Nutze `getModuleApi("id")` (siehe unten) – es liefert `null`, wenn das Modul aus ist.
* Die Fenster-Optionen `props` kommen aus `openWindow({ …, props })` und stehen in `win.props`.

## Hilfsfunktionen aus `/js/app/modules.js`

| Funktion | Zweck |
|---|---|
| `moduleRequest(pfad, opts)` | Authentifizierter Backend-Aufruf, liefert JSON |
| `isModuleOn("id")` | Ist ein Modul aktiv? |
| `moduleFeature("id", "feature")` | Ist eine Teilfunktion an? |
| `getModules()` | Liste der aktiven Module |
| `registerModuleApi("id", { … })` | Funktionen für andere Teile des Frontends bereitstellen (im `init`-Skript) |
| `getModuleApi("id")` | Funktionen eines aktiven Moduls holen (sonst `null`) |
| `openWebsiteEntry({url})` | Beispiel einer Core-Funktion, die ein Modul optional nutzt |
| `reportModuleCrash(id, wo, fehler, fatal)` | Fehler selbst melden |

## Einstellungsmenü

Mit `settings_ui` bekommt dein Modul ein **eigenes Einstellungsmenü** in der Modul-App (Reiter *Einstellungen*) –
die allgemeine Einstellungen-App bleibt dadurch schlank. Für einfache Werte reicht `settings` in der Backend-`module.json`
(Formular wird automatisch erzeugt). Für Eigenes (z. B. Container-Steuerung):

```js
export async function renderSettings(root, ctx) {   // ctx = { module, req }
  root.innerHTML = `<button id="x">Neu starten</button>`;
  root.querySelector("#x").onclick = () => ctx.req("/api/mein/modul/restart", { method: "POST" });
}
```

Beispiele aus dem echten Code: `src/modules/frontend/guacamole/settings.js` (Docker-Überwachung mit Restart-Knopf, manuelle Verbindung),
`recordings/settings.js`, `relay/settings.js`.

## Startmenü-Einträge & Favoriten

Apps erscheinen automatisch im Startmenü und im Favoriten-Dialog (`startmenu: false` blendet sie aus).
Im `init`-Skript kannst du zusätzliche Einträge anlegen: `addCatalogEntry(id, icon, label)` aus `/js/app/startmenu.js`
(Beispiel: `src/modules/frontend/browser/index.js`).

## Wenn etwas abstürzt

* `import` schlägt fehl oder die `render`-Funktion wirft → das Fenster zeigt den Fehler, das Modul wird deaktiviert,
  ein Crashbericht erscheint in der Modul-App.
* Fehler in Klick-Handlern/Promises aus deinen Dateien werden als **nicht-fatal** protokolliert (sichtbar in *Abstürze*),
  schalten das Modul aber nicht ab.

## Testen

1. Modul in der Modul-App aktivieren → Eintrag erscheint im Startmenü.
2. App öffnen. 3. In der Entwicklerkonsole des Browsers auf Fehler achten.
4. Absichtlich `throw new Error("x")` in `render` einbauen → Crash-Verhalten prüfen.

## Einheitliche Bedienelemente (Pflicht)

Aussehen und Größe kommen ausschließlich aus `/css/ui.css`. Bitte **keine** Inline-Styles
für Farbe, Größe, Rahmen oder Innenabstand an Buttons, Feldern, Dropdowns oder Tabs setzen
(Layout wie `margin`, `flex`, `width` ist erlaubt).

| Element | Klasse |
|---|---|
| Standard-Knopf | `btn` |
| Hauptaktion (Speichern, Anlegen) | `btn-primary` |
| Löschen / Entfernen / Beenden | `btn btn-danger` |
| heikle Aktion (Neustart …) | `btn btn-warn` |
| ohne Rahmen | `btn btn-ghost` |
| kleiner Icon-Knopf im Widget-Kopf | `btn-tool` |
| Größen/Zustände | `btn-sm`, `btn-icon`, `btn-block`, `active` |
| Register/Tabs | `<div class="tab-bar"><button class="tab-btn active">…</button></div>` |
| kompaktes Feld/Dropdown | `input-sm` |

Textfelder, `select`, `textarea`, Checkboxen und Radios sind automatisch gestaltet.

```js
root.innerHTML = `
  <div class="tab-bar"><button class="tab-btn active">Übersicht</button><button class="tab-btn">Einstellungen</button></div>
  <input type="text" placeholder="Name"> <select><option>A</option></select>
  <button class="btn-primary">Speichern</button> <button class="btn btn-danger">Löschen</button>`;
```

// persist.js
// ----------
// Speichert den sichtbaren UI-Zustand im Browser (localStorage), damit nach
// einem Reload / Strg+F5 alles genauso aussieht wie vorher:
//   - welche Tenants/Locations/Ordner/Clients in der Sidebar aufgeklappt sind
//   - welcher Eintrag in der Sidebar ausgewählt ist
//   - welche Fenster offen sind (inkl. Position, Größe, min/max) und ihre
//     Reihenfolge (welches liegt oben)
//
// Der Zustand ist PRO BENUTZER getrennt (Key enthält den Usernamen), damit sich
// verschiedene Anmeldungen am selben Browser nicht überschreiben.
//
// Hinweis: Das ist die echte RMM-Web-App (auf eurem Server) - localStorage ist
// hier völlig in Ordnung und der Standardweg für solche UI-Persistenz.

const KEY_PREFIX = "rapalle-ui:";
let _key = KEY_PREFIX + "anon";
let _enabled = false;
let _saveTimer = null;

// Diese Callbacks liefern/übernehmen den jeweiligen Teilzustand. Sie werden
// von app.js/sidebar.js gesetzt, damit persist.js die Module nicht kennen muss.
let _getExpanded = () => [];
let _setExpanded = () => {};

// Dashboard-Layout (anpassbare Client-Ansicht) + Edit-Modus pro Benutzer.
// Wird komplett clientseitig gehalten (wie der übrige UI-Zustand).
let _dashLayout = null;
let _dashEdit = false;
let _fleetWidgets = null;      // benutzerdefinierte Widgets im Flotten-Dashboard
export function getDashLayout() { return _dashLayout; }
export function setDashLayout(layout) { _dashLayout = layout; }
export function getDashEdit() { return _dashEdit; }
export function setDashEdit(on) { _dashEdit = !!on; }
export function getFleetWidgets() { return _fleetWidgets; }
export function setFleetWidgets(w) { _fleetWidgets = w; }
// Persönliche Einstellung: VMs/LXCs vollwertig in Flotten-Diagrammen mitzählen?
let _fleetIncludeVirtual = true;
export function getFleetIncludeVirtual() { return _fleetIncludeVirtual; }
export function setFleetIncludeVirtual(on) { _fleetIncludeVirtual = !!on; }

// Organisationsweite Standard-Layouts (vom Admin gesetzt). Werden beim Start
// vom Backend geladen; genutzt als Basis für neue Nutzer und beim "Auf
// Standard zurücksetzen". null = kein org-Standard gesetzt.
let _orgDefaultDash = null;
let _orgDefaultFleet = null;
export function getOrgDefaultDash() { return _orgDefaultDash; }
export function getOrgDefaultFleet() { return _orgDefaultFleet; }
export function setOrgDefaults({ dash, fleet }) {
  if (dash !== undefined) _orgDefaultDash = dash;
  if (fleet !== undefined) _orgDefaultFleet = fleet;
}

export function configurePersistence({ username, getExpanded, setExpanded }) {
  _key = KEY_PREFIX + (username || "anon");
  if (getExpanded) _getExpanded = getExpanded;
  if (setExpanded) _setExpanded = setExpanded;
  _enabled = true;
}

// --- Speichern (gedrosselt, damit häufige Änderungen nicht spammen) ---
export function scheduleSave(state) {
  if (!_enabled) return;
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => saveNow(state), 300);
}

export function saveNow(state) {
  if (!_enabled) return;
  try {
    const data = {
      v: 1,
      expanded: _getExpanded(),
      selection: state.selection || null,
      sidebar: state.sidebar || null,
      dashLayout: _dashLayout,
      dashEdit: _dashEdit,
      fleetWidgets: _fleetWidgets,
      fleetIncludeVirtual: _fleetIncludeVirtual,
      focusOrder: state.focusOrder || [],
      windows: (state.windows || []).map((w) => ({
        key: w.key,
        appId: w.appId,
        title: w.title,
        props: w.props || {},
        clientColor: w.clientColor || null,
        x: w.x, y: w.y, w: w.w, h: w.h,
        minimized: !!w.minimized,
        maximized: !!w.maximized,
      })),
    };
    localStorage.setItem(_key, JSON.stringify(data));
  } catch (e) {
    // localStorage kann voll/deaktiviert sein - dann eben keine Persistenz.
    console.warn("[persist] Speichern fehlgeschlagen:", e);
  }
}

// --- Laden ---
export function loadState() {
  if (!_enabled) return null;
  try {
    const raw = localStorage.getItem(_key);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data || data.v !== 1) return null;
    return data;
  } catch (e) {
    console.warn("[persist] Laden fehlgeschlagen:", e);
    return null;
  }
}

// Aufgeklappte Knoten in die Sidebar zurückspielen (vor dem ersten Render).
export function applyExpanded(data) {
  if (data && Array.isArray(data.expanded)) _setExpanded(data.expanded);
  if (data && data.dashLayout) _dashLayout = data.dashLayout;
  if (data && typeof data.dashEdit === "boolean") _dashEdit = data.dashEdit;
  if (data && data.fleetWidgets) _fleetWidgets = data.fleetWidgets;
  if (data && typeof data.fleetIncludeVirtual === "boolean") _fleetIncludeVirtual = data.fleetIncludeVirtual;
}

export function clearPersisted() {
  try { localStorage.removeItem(_key); } catch {}
}

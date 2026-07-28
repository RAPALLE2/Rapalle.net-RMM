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
// Rechte-Helfer fürs harte Gating des Bearbeiten-Modus (kein Import-Zyklus:
// state.js importiert selbst nichts).
import { hasGlobalPerm } from "./state.js";
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

// --- Layout-PROFILE (pro Benutzer): benannte Client-Panel-Layouts, z.B.
// "Physisch", "VMs", "LXCs". Zusätzlich merkt sich _clientDashProfiles pro
// Client, WELCHES Profil dort genutzt wird ('' / fehlend = Standard-Layout).
let _dashProfiles = {};         // name -> layout
let _clientDashProfiles = {};   // clientId -> profilname
export function getDashProfiles() { return _dashProfiles; }
export function setDashProfiles(p) { _dashProfiles = p || {}; }
export function getClientDashProfile(clientId) { return _clientDashProfiles[clientId] || null; }
export function setClientDashProfile(clientId, name) {
  if (name) _clientDashProfiles[clientId] = name;
  else delete _clientDashProfiles[clientId];
}
export function getClientDashProfileMap() { return _clientDashProfiles; }
// Der Layout-Bearbeiten-Modus (Dashboard + Client-Panel + Startmenü) ist hart
// an das Recht 'customize_dashboard' gekoppelt: Ohne das Recht liefert
// getDashEdit() immer false und setDashEdit(true) wird ignoriert - egal, über
// welchen Weg (Profil-Checkbox, gespeicherter Zustand, Konsole) der Modus
// aktiviert werden soll. Harte Grenze bleibt trotzdem das Backend.
export function getDashEdit() { return _dashEdit && hasGlobalPerm("customize_dashboard"); }
export function setDashEdit(on) {
  if (on && !hasGlobalPerm("customize_dashboard")) return;
  _dashEdit = !!on;
}
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
// Org-weite Layout-Profil-Presets ("Physisch"/"VMs"/"LXCs"), vom Admin gesetzt.
let _orgProfilePresets = {};
export function getOrgDefaultDash() { return _orgDefaultDash; }
export function getOrgDefaultFleet() { return _orgDefaultFleet; }
export function getOrgProfilePresets() { return _orgProfilePresets; }
export function setOrgProfilePresets(map) { _orgProfilePresets = map || {}; }
export function setOrgDefaults({ dash, fleet }) {
  if (dash !== undefined) _orgDefaultDash = dash;
  if (fleet !== undefined) _orgDefaultFleet = fleet;
}

// Persönliche Wiederherstellungs-Einstellungen: Was soll beim erneuten Anmelden
// wiederhergestellt werden? (Sonst: sauberer Start vom Dashboard.)
//   client -> zuletzt ausgewählter Client
//   folder -> aufgeklappte/ausgewählte Ordner-Struktur (Sidebar)
//   apps   -> zuletzt offene Fenster/Apps (inkl. offener Ordner im Explorer)
let _restorePrefs = { client: true, folder: true, apps: true };
export function getRestorePrefs() { return { ..._restorePrefs }; }
export function setRestorePrefs(p) {
  _restorePrefs = {
    client: p && "client" in p ? !!p.client : _restorePrefs.client,
    folder: p && "folder" in p ? !!p.folder : _restorePrefs.folder,
    apps: p && "apps" in p ? !!p.apps : _restorePrefs.apps,
  };
}

export function configurePersistence({ username, getExpanded, setExpanded }) {
  _user = username || "anon";
  _key = KEY_PREFIX + _user;
  if (getExpanded) _getExpanded = getExpanded;
  if (setExpanded) _setExpanded = setExpanded;
  _enabled = true;
}

// ------------------------------------------------------------------
// Server-Sync: Die UI-Einstellungen (Layouts, Favoriten, Startmenü, Sprache,
// Icon-Modus) werden zusätzlich PRO BENUTZER auf dem Server gespeichert.
// Dadurch sieht die Oberfläche in JEDEM Browser gleich aus - localStorage
// bleibt nur der schnelle lokale Cache. Beim Login gewinnt der Server-Stand.
// ------------------------------------------------------------------
let _user = "anon";
let _serverTimer = null;
let _hydrated = false;

// Welche Schlüssel gehören zum server-gespeicherten UI-Zustand?
//
// Früher war das eine feste Liste - jeder neue Schlüssel, den irgendein Modul
// einführte, wurde schlicht vergessen und lebte nur im Browser. Deshalb jetzt
// PRÄFIX-BASIERT: alles, was zu diesen Präfixen passt, landet automatisch in
// der Datenbank. Nur echt gerätegebundene Dinge (Login-Token, Spotify-OAuth)
// bleiben ausdrücklich lokal.
const SYNC_PREFIXES = [
  KEY_PREFIX,          // "rapalle-ui:<user>"  Fenster/Sidebar/Layouts/Profile
  "rapalle-favs:",     // Favoriten (Sidebar + Dashboard + angeheftete Einträge)
  "rmm_appmenu:",      // Startmenü-Layout inkl. ORDNER
  "rmm_webapps:",      // interner Browser: gespeicherte Web-Apps
  "rmm_dash",          // Dashboard-Zusatzzustände
  "rmm_fleet",         // Flotten-Dashboard (Raster, Verlauf, Migrationen)
  "rmm_icon_mode",     // Icon-Darstellung (SVG/Emoji)
  "rmm_lang",          // Sprache
  "rmm_term_",         // Terminal-Einstellungen
  "rmm_notifications", // Benachrichtigungs-Center
];
// Diese bleiben bewusst NUR im Browser (Sicherheit / gerätegebunden).
const NEVER_SYNC = new Set(["rmm_token", "rmm_login_hint", "rmm_spotify_tokens", "rmm_spotify_pkce"]);

function _isSyncable(k) {
  if (!k || NEVER_SYNC.has(k)) return false;
  // Benutzergebundene Schlüssel nur für DIESEN Benutzer synchronisieren.
  if (k.includes(":")) {
    const [pfx, rest] = [k.slice(0, k.indexOf(":") + 1), k.slice(k.indexOf(":") + 1)];
    if (SYNC_PREFIXES.includes(pfx) && rest !== _user) return false;
  }
  return SYNC_PREFIXES.some((p) => k === p || k.startsWith(p));
}

// Alle aktuell im Browser liegenden, synchronisierbaren Schlüssel.
function _syncedKeys() {
  const out = new Set([_key, `rmm_appmenu:${_user}`, `rapalle-favs:${_user}`, `rmm_webapps:${_user}`]);
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (_isSyncable(k)) out.add(k);
    }
  } catch {}
  return [...out].filter((k) => !NEVER_SYNC.has(k));
}

// Beim Login: Server-Stand holen und in localStorage übernehmen (Server
// gewinnt IMMER). MUSS vor loadState()/initFavorites()/initStartMenu() laufen.
//
// Wichtig: Es werden ALLE vom Server gelieferten Schlüssel übernommen, nicht
// nur die aus einer festen Liste. Dadurch ist der Browser-Cache nur noch ein
// Spiegel der Datenbank - egal über welche URL man das RMM öffnet.
export async function hydrateFromServer() {
  try {
    const { api } = await import("./api.js");
    const res = await api.getUiPrefs();
    const keys = (res && res.keys) || {};
    // Sonderfall "Erstmigration": Der Server kennt noch gar nichts (z.B. weil
    // dieser Benutzer bisher nur lokal gearbeitet hat). Dann NICHT aufräumen,
    // sondern den vorhandenen lokalen Stand einmalig in die Datenbank heben.
    if (!Object.keys(keys).length) {
      _hydrated = true;
      const local = _syncedKeys().some((k) => localStorage.getItem(k) !== null);
      if (local) { try { await flushToServer(); } catch {} }
      return;
    }
    // 1) Lokale Reste eines fremden/alten Stands entfernen, damit nichts
    //    "durchblutet", was der Server nicht (mehr) kennt.
    const stale = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (_isSyncable(k) && !Object.prototype.hasOwnProperty.call(keys, k)) stale.push(k);
      }
    } catch {}
    for (const k of stale) { try { localStorage.removeItem(k); } catch {} }
    // 2) Server-Stand einspielen.
    for (const [k, v] of Object.entries(keys)) {
      if (NEVER_SYNC.has(k)) continue;
      try { localStorage.setItem(k, v); } catch {}
    }
    _hydrated = true;
  } catch (e) {
    // Server nicht erreichbar o.ä. -> lokaler Stand bleibt bestehen, ABER es
    // wird nichts hochgeladen (siehe _hydrated-Sperre in syncToServerSoon).
    console.warn("[persist] Server-UI-Einstellungen nicht ladbar:", e);
  }
}

export function isHydrated() { return _hydrated; }

// Gedrosseltes Hochladen (fire-and-forget) - immer als MERGE (PATCH).
//
// Sperre: Solange der Server-Stand nicht erfolgreich geladen wurde, wird NICHTS
// hochgeladen. Sonst konnte ein frisch geöffneter Browser (leerer Cache) den in
// der Datenbank gespeicherten Zustand überschreiben - genau der Grund, warum
// Startmenü-Ordner & Co. beim Öffnen über eine andere URL "weg" waren.
export function syncToServerSoon() {
  if (!_enabled || !_hydrated) return;
  clearTimeout(_serverTimer);
  _serverTimer = setTimeout(() => { flushToServer(); }, 1200);
}

// Sofort schreiben (z.B. beim Schließen des Tabs).
export async function flushToServer() {
  if (!_enabled || !_hydrated) return;
  clearTimeout(_serverTimer);
  try {
    const { api } = await import("./api.js");
    const keys = {};
    for (const k of _syncedKeys()) {
      const v = localStorage.getItem(k);
      keys[k] = v === null ? null : v;   // null = Schlüssel serverseitig löschen
    }
    await api.mergeUiPrefs(keys);
  } catch (e) {
    console.warn("[persist] Server-Sync fehlgeschlagen:", e);
  }
}

// Beim Verlassen der Seite den letzten Stand noch mitnehmen (best effort).
try {
  window.addEventListener("pagehide", () => { if (_serverTimer) flushToServer(); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden" && _serverTimer) flushToServer();
  });
} catch {}

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
      dashProfiles: _dashProfiles,
      clientDashProfiles: _clientDashProfiles,
      dashEdit: _dashEdit,
      fleetWidgets: _fleetWidgets,
      fleetIncludeVirtual: _fleetIncludeVirtual,
      restorePrefs: _restorePrefs,
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
        pinned: !!w.pinned,
      })),
    };
    localStorage.setItem(_key, JSON.stringify(data));
    // Zusätzlich (gedrosselt) auf den Server spiegeln, damit andere Browser
    // desselben Benutzers den gleichen Stand sehen.
    syncToServerSoon();
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
  if (data && data.dashProfiles && typeof data.dashProfiles === "object") _dashProfiles = data.dashProfiles;
  if (data && data.clientDashProfiles && typeof data.clientDashProfiles === "object") _clientDashProfiles = data.clientDashProfiles;
  if (data && typeof data.dashEdit === "boolean") _dashEdit = data.dashEdit;
  if (data && data.fleetWidgets) _fleetWidgets = data.fleetWidgets;
  if (data && typeof data.fleetIncludeVirtual === "boolean") _fleetIncludeVirtual = data.fleetIncludeVirtual;
  if (data && data.restorePrefs && typeof data.restorePrefs === "object") setRestorePrefs(data.restorePrefs);
}

export function clearPersisted() {
  try { localStorage.removeItem(_key); } catch {}
}

// app.js
// ------
// Der zentrale Einstiegspunkt des Frontends. Verantwortlich für:
//   1. Login-Ablauf (inkl. erzwungenem Passwortwechsel beim ersten Login)
//   2. Laden der Daten (Profil, Hierarchie, Clients) nach dem Login
//   3. Verkabeln aller Buttons (Startmenü, Benutzermenü, Sidebar, Taskbar)
//   4. Live-Updates über Socket.IO (Client online/offline, Metriken)
//   5. "Content-Router": entscheidet, welche App-Funktion den Inhalt eines
//      Fensters rendert (renderWindowContent)

import { state } from "./state.js";
import { hasGlobalPerm, isAdmin } from "./state.js";
import { api, saveToken, clearToken } from "./api.js";
import { dashboardSocket } from "./socket.js";
import { applyTheme, applyAccent } from "./theme.js";
import { setLanguage, getLanguage, applyStaticTranslations } from "./i18n_apply.js";

import { renderSidebar, setOnSelect, getExpandedIds, setExpandedIds, setOnTreeStateChanged, initFavorites, initSidebarNav } from "./sidebar.js";
import { renderMainContent } from "./panel.js";
import { renderTaskbar, initTaskbar } from "./taskbar.js";
import { setContentRenderer, setOnWindowsChanged, openWindow, minimizeAll, closeWindow } from "./windowmanager.js";
import { recordMetrics } from "./metricshistory.js";
import { notify, notifyError } from "./notify.js";
import { configurePersistence, scheduleSave, saveNow, loadState, applyExpanded, setOrgDefaults, setOrgProfilePresets, hydrateFromServer } from "./persist.js";
import { initCrashGuard } from "./crashguard.js";

// notify global verfügbar machen, damit alle Module (auch Fehlerbehandlung)
// die schönen Slide-Down-Meldungen nutzen können.
window.notify = notify;
window.notifyError = notifyError;

// App-Fenster-Renderer
import { renderTerminal } from "./apps/terminal.js";
import { renderExplorer } from "./apps/explorer.js";
import { renderTaskManager } from "./apps/taskmanager.js";
import { renderVnc } from "./apps/vnc.js";
import { renderEditClient, setEditOnChanged } from "./apps/editclient.js";
import { renderAddClient } from "./apps/addclient.js";
import { renderManage, setManageOnChanged } from "./apps/manage.js";
import { renderRecordings } from "./apps/recordings.js";
import { renderScripts } from "./apps/scripts.js";
import { renderBulk } from "./apps/bulk.js";
import { renderTowerDefense } from "./apps/towerdefense.js";
import { renderGamingHub } from "./apps/gaminghub.js";
import { renderAiChat } from "./apps/aichat.js";
import { renderTickets } from "./apps/tickets.js";
import { renderAudioPlayer } from "./apps/audioplayer.js";
import { handleSpotifyCallback } from "./spotify.js";
import { renderWebBrowser, renderWebApp, getWebApps } from "./apps/webbrowser.js";
import { addCatalogEntry } from "./startmenu.js";
import { renderAutomation } from "./apps/automation.js";
import { renderRelayManager } from "./apps/relaymanager.js";
import { renderSpeedtest } from "./apps/speedtest.js";
import { renderNetwork } from "./apps/network.js";
import { renderPortscan } from "./apps/portscan.js";
import { renderGuacamole } from "./apps/guacamole.js";
import { renderSettings } from "./apps/settings.js";
import { renderPermissions } from "./apps/permissions.js";
import { renderAudit } from "./apps/audit.js";
import { renderProfile } from "./apps/profile.js";
import { renderNotifyCenter, attachUnreadDot } from "./notifycenter.js";
import { updateClientLayouts } from "./dashlayout.js";
import { renderPanelPart } from "./apps/panelpart.js";
import { renderFleetWidget } from "./apps/fleetwidget.js";
import { initStartMenu, refreshStartMenu } from "./startmenu.js";

// -----------------------------------------------------------------
// Content-Router: welcher Renderer gehört zu welchem appId?
// -----------------------------------------------------------------

const APP_RENDERERS = {
  terminal: renderTerminal,
  explorer: renderExplorer,
  taskmanager: renderTaskManager,
  vnc: renderVnc,
  "edit-client": renderEditClient,
  "add-client": renderAddClient,
  manage: renderManage,
  recordings: renderRecordings,
  scripts: renderScripts,
  bulk: renderBulk,
  towerdefense: renderTowerDefense,   // bleibt für wiederhergestellte Fenster
  gaminghub: renderGamingHub,
  aichat: renderAiChat,
  tickets: renderTickets,
  audioplayer: renderAudioPlayer,
  webbrowser: renderWebBrowser,
  webapp: renderWebApp,           // gespeicherte Web-Apps (props: {url,name,icon})
  automation: renderAutomation,
  "relay-manager": renderRelayManager,
  "speedtest": renderSpeedtest,
  network: renderNetwork,
  portscan: renderPortscan,
  guacamole: renderGuacamole,
  settings: renderSettings,
  permissions: renderPermissions,
  audit: renderAudit,
  profile: renderProfile,
  notifycenter: renderNotifyCenter,
  panelpart: renderPanelPart,
  fleetwidget: renderFleetWidget,
};

function renderWindowContent(body, win) {
  const renderer = APP_RENDERERS[win.appId];
  if (renderer) renderer(body, win);
  else body.innerHTML = `<div style="padding:20px;color:var(--subtext)">Unbekannte App: ${win.appId}</div>`;
}

// -----------------------------------------------------------------
// Daten laden & Oberfläche aktualisieren
// -----------------------------------------------------------------

async function reloadHierarchy() {
  state.hierarchy = await api.getHierarchy();
  // Offene Fenster (Add/Edit Client, ...) informieren, damit sie ihre
  // Tenant-/Standort-Auswahlen SOFORT neu befüllen - ohne Fenster-Neuöffnen.
  try { window.dispatchEvent(new CustomEvent("rmm:hierarchy-changed")); } catch {}
}

async function reloadClients() {
  state.clients = await api.getClients();
}

// Effektive Rechte des Benutzers laden (für Frontend-Gating).
async function reloadPerms() {
  try {
    state.perms = await api.getEffectivePermissions();
  } catch {
    // Fallback: keine Rechte-Info -> als "nur Admin sieht alles" behandeln wäre
    // unsicher; stattdessen leere Rechte (Backend bleibt die harte Grenze).
    state.perms = { admin: false, global: {}, clients: {} };
  }
}

// Apps, die ausschließlich dem Super-Admin offenstehen (Backend: require_admin).
// App-Key -> benötigtes globales Recht. Nicht gelistete Apps sind frei
// zugänglich (z.B. Dashboard/Clients, Speedtest). 'settings' ist ein Sonderfall.
const APP_REQUIRED_PERM = {
  audit: "see_audit",
  recordings: "see_replay",
  bulk: "bulk_shell",
  network: "network_scan",
  portscan: "port_scan",
  scripts: "use_scripts",
  automation: "automation",
  permissions: "see_permissions",
  manage: "manage_hierarchy",
  "relay-manager": "use_relay",
  towerdefense: "play_games",
  gaminghub: "play_games",
  tickets: "ticket_read",
};

function _appAllowed(appKey) {
  if (isAdmin()) return true;
  // Einstellungen: sichtbar, wenn der Nutzer wenigstens einen Settings-Bereich
  // sehen/bearbeiten darf (Einstellungen ansehen, SSO, Branding, Source oder
  // Benutzer erstellen). Die einzelnen Tabs werden in der App zusätzlich gegated.
  if (appKey === "settings") {
    return hasGlobalPerm("see_settings") || hasGlobalPerm("manage_sso") ||
           hasGlobalPerm("manage_branding") || hasGlobalPerm("see_source") ||
           hasGlobalPerm("create_users");
  }
  if (appKey === "webbrowser" || appKey.startsWith("webapp:")) return true;
  const perm = APP_REQUIRED_PERM[appKey];
  if (!perm) return true;               // frei zugängliche App
  return hasGlobalPerm(perm);
}

function applyAppVisibility() {
  // Startmenü-Katalog (versteckte Original-Buttons) + Live-Menü aktualisieren.
  document.querySelectorAll("#start-menu [data-app]").forEach((btn) => {
    btn.style.display = _appAllowed(btn.dataset.app) ? "" : "none";
  });
  try { refreshStartMenu(); } catch {}
  // Benutzermenü: Einstellungen nur, wenn erlaubt.
  const settingsBtn = document.getElementById("btn-open-settings");
  if (settingsBtn) settingsBtn.style.display = _appAllowed("settings") ? "" : "none";
  // Sidebar: Client hinzufügen (Enrollment) bleibt Admin-only; Hierarchie
  // verwalten richtet sich nach dem Recht 'manage_hierarchy'.
  const addBtn = document.getElementById("btn-add-client");
  if (addBtn) addBtn.style.display = isAdmin() ? "" : "none";
  const mgmtBtn = document.getElementById("btn-manage-hierarchy");
  if (mgmtBtn) mgmtBtn.style.display = (isAdmin() || hasGlobalPerm("manage_hierarchy")) ? "" : "none";
}

async function refreshAll() {
  await Promise.all([reloadHierarchy(), reloadClients()]);
  renderSidebar();
  renderMainContent();
}

// -----------------------------------------------------------------
// Version: Single Source of Truth ist backend/version.txt (via
// /api/version). Alle Anzeigen im Frontend werden von hier befüllt -
// nirgends mehr hardcoden!
// -----------------------------------------------------------------
async function loadVersion() {
  try {
    const v = await api.getVersions(); // { backend, agent }
    document.querySelectorAll(".topbar-version").forEach((el) => {
      el.textContent = v.backend || "";
      el.title = `Backend ${v.backend || "?"} · Agent ${v.agent || "?"}`;
    });
  } catch { /* Version ist Kosmetik - Fehler nie den Start blockieren lassen */ }
}
loadVersion();

// -----------------------------------------------------------------
// Login-Ablauf
// -----------------------------------------------------------------

const loginScreen = () => document.getElementById("login-screen");
const changePwScreen = () => document.getElementById("change-pw-screen");
const appScreen = () => document.getElementById("app");

function showOnly(el) {
  [loginScreen(), changePwScreen(), appScreen()].forEach((s) => s.classList.add("hidden"));
  el.classList.remove("hidden");
}

async function startSession(user) {
  state.user = user;
  applyTheme(user.theme);
  applyAccent(user.accent || "teal");
  setLanguage(user.language || "de");
  applyStaticTranslations();

  // Muss der User zuerst sein Passwort wechseln? (erster Login / Einmalpasswort)
  if (user.must_change_pw) {
    showOnly(changePwScreen());
    return;
  }

  showOnly(appScreen());
  document.getElementById("user-menu-name").textContent = user.display_name;

  // Persistenz einrichten (pro Benutzer). Aufgeklappte Sidebar-Knoten müssen
  // VOR dem ersten Render gesetzt werden, damit der Baum sofort korrekt aussieht.
  configurePersistence({
    username: user.username,
    getExpanded: getExpandedIds,
    setExpanded: setExpandedIds,
  });
  // Serverseitig gespeicherte UI-Einstellungen holen und in localStorage
  // übernehmen (Server gewinnt) - VOR initFavorites()/loadState(), damit
  // Favoriten, Layouts & Co. in jedem Browser identisch sind.
  await hydrateFromServer();
  // Sprache ggf. auf den Server-Stand umschalten (rmm_lang wurde hydriert).
  try {
    const lang = localStorage.getItem("rmm_lang");
    if (lang && lang !== getLanguage()) setLanguage(lang);
  } catch {}
  initFavorites(user.username);   // Favoriten des Benutzers laden
  initSidebarNav();               // Dashboard-Tab + Favoriten-Header verkabeln

  // Effektive Rechte ZUERST laden – sie werden sowohl für die Restore-Logik
  // (Recht 'restore_session') als auch fürs UI-Gating gebraucht. Früher lief
  // reloadPerms() erst später, wodurch 'restore_session' immer als "fehlt"
  // galt und NICHTS wiederhergestellt wurde. (Rechte hängen nicht an Clients/
  // Hierarchie, können also gefahrlos zuerst geladen werden.)
  await reloadPerms();

  const saved = loadState();
  // Persönliche Wiederherstellungs-Einstellungen (Standard: alles wiederherstellen).
  // Zusätzlich durch das globale Recht 'restore_session' gegated: fehlt es, wird
  // immer sauber vom Dashboard gestartet.
  const mayRestore = hasGlobalPerm("restore_session");
  const restorePrefs = mayRestore
    ? ((saved && saved.restorePrefs) || { client: true, folder: true, apps: true })
    : { client: false, folder: false, apps: false };
  // WICHTIG: Restore/Crash-Guard-Init defensiv kapseln. Ein Fehler hier darf den
  // Login/das Dashboard NIEMALS blockieren (sonst bleibt man im leeren, „blurry"
  // App-Screen hängen, weil das Login schon ausgeblendet ist).
  try {
    if (saved) {
      applyExpanded(saved);           // Aufklapp-Zustand + Layouts/Profile
      // „Offene Ordner" (Sidebar-Baum) nur wiederherstellen, wenn gewünscht.
      if (!restorePrefs.folder) {
        try { setExpandedIds([]); } catch {}
      }
      // Auswahl: Client vs. Ordner getrennt steuerbar.
      if (saved.selection) {
        const selIsClient = saved.selection.type === "client";
        if ((selIsClient && restorePrefs.client) || (!selIsClient && restorePrefs.folder)) {
          state.selection = saved.selection;
        }
      }
      if (restorePrefs.folder && saved.sidebar) { state.sidebar = saved.sidebar; applySidebarState(); }
    }

    // Frontend-Crash-Guard: erkennt Abstürze/Einfrieren, protokolliert sie und
    // startet die Oberfläche im Ernstfall neu (schleifensicher, siehe crashguard.js).
    initCrashGuard({
      username: user.username,
      onPanic: () => {
        try { for (const w of [...(state.windows || [])]) closeWindow(w.key); } catch {}
        try { saveNow(state); } catch {}   // ohne offene Fenster sichern
      },
    });
  } catch (e) {
    try { console.error("[startSession] Restore/CrashGuard-Init fehlgeschlagen:", e); } catch {}
  }

  // Organisationsweite Standard-Layouts laden (vom Admin gesetzt). Werden als
  // Basis für Nutzer OHNE eigenes Layout und beim "Auf Standard zurücksetzen"
  // verwendet. Fehler hier sind unkritisch (dann greift der eingebaute
  // Standard).
  try {
    const defs = await api.getDefaultLayouts();
    setOrgDefaults({ dash: defs.dash || null, fleet: defs.fleet || null });
    // Org-weite Profil-Presets (Physisch/VMs/LXCs) für die Client-Ansicht.
    setOrgProfilePresets({
      "Physisch": defs.dash_profile_physical || null,
      "VMs": defs.dash_profile_vm || null,
      "LXCs": defs.dash_profile_lxc || null,
    });
  } catch { /* kein org-Standard erreichbar -> eingebauter Standard */ }

  // (Effektive Rechte wurden bereits oben – vor der Restore-Logik – geladen.)
  // AB HIER darf kein Fehler mehr aus startSession herausfliegen: sonst würde der
  // Login-Handler den App-Screen wieder ausblenden und man käme nicht mehr rein.
  // Jeder Schritt ist daher einzeln gekapselt – im Zweifel erscheint ein (ggf.
  // leeres) Dashboard statt einer Login-Sackgasse.
  try { await refreshAll(); }
  catch (e) { try { console.error("[startSession] refreshAll fehlgeschlagen:", e); } catch {} }

  try { applyAppVisibility(); } catch (e) { try { console.error(e); } catch {} }

  // Gespeicherte Fenster wiederherstellen (nach refreshAll, damit Clients/
  // Hierarchie geladen sind und die Fenster-Inhalte korrekt rendern) – nur wenn
  // der Benutzer „offene Apps wiederherstellen" aktiviert hat.
  try { if (saved && restorePrefs.apps) restoreWindows(saved); }
  catch (e) { try { console.error("[startSession] restoreWindows fehlgeschlagen:", e); } catch {} }

  // Ab jetzt jede Fenster-/Baum-Änderung speichern.
  try {
    setOnWindowsChanged(() => { renderTaskbar(); scheduleSave(state); });
    setOnTreeStateChanged(() => scheduleSave(state));
    renderTaskbar();
  } catch (e) { try { console.error(e); } catch {} }
}

// Öffnet die zuletzt offenen Fenster in ihrer gespeicherten Reihenfolge,
// Geometrie und mit ihrem Status (minimiert/maximiert) erneut.
function restoreWindows(saved) {
  const wins = saved.windows || [];
  if (!wins.length) return;
  // In Fokus-Reihenfolge öffnen (letztes = oberstes). Fenster, die nicht in
  // focusOrder stehen, hinten anstellen.
  const order = saved.focusOrder || [];
  wins.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
  for (const w of wins) {
    try {
      openWindow({
        singleton: true,   // Restore: exakt diesen Key wiederherstellen
        key: w.key, appId: w.appId, title: w.title, props: w.props || {},
        clientColor: w.clientColor, w: w.w, h: w.h, x: w.x, y: w.y,
        minimized: w.minimized, maximized: w.maximized, pinned: !!w.pinned,
        focus: false,   // Fokus am Ende gesetzt (Reihenfolge)
      });
    } catch (e) {
      console.warn("[persist] Fenster konnte nicht wiederhergestellt werden:", w.key, e);
    }
  }
}

function initLoginForm() {
  // Verfügbare AD-Realms ins Dropdown laden (zusätzlich zu "Lokal")
  const realmSelect = document.getElementById("login-realm");
  const userField = document.getElementById("login-username");

  // Zuletzt genutzten Benutzernamen + Realm wiederherstellen (wie bei Proxmox).
  // Gespeichert wird NUR Benutzername + Realm, NIEMALS das Passwort.
  let savedRealm = null;
  try {
    const raw = localStorage.getItem("rmm_login_hint");
    if (raw) {
      const hint = JSON.parse(raw);
      if (hint.username && userField) userField.value = hint.username;
      savedRealm = hint.realm || null;
    }
  } catch {}

  api.getLoginRealms().then((realms) => {
    for (const r of realms) {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = r.name + " (AD)";
      realmSelect.appendChild(opt);
    }
    // Gespeicherten Realm wählen, sofern noch vorhanden.
    if (savedRealm && [...realmSelect.options].some((o) => o.value === savedRealm)) {
      realmSelect.value = savedRealm;
    }
  }).catch(() => { /* Backend evtl. noch nicht bereit - egal, "Lokal" reicht */ });

  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = document.getElementById("login-error");
    errBox.classList.add("hidden");
    try {
      const username = userField.value;
      const res = await api.login(
        username,
        document.getElementById("login-password").value,
        realmSelect.value
      );
      // Benutzername + Realm merken (Passwort NICHT).
      try {
        localStorage.setItem("rmm_login_hint", JSON.stringify({ username, realm: realmSelect.value }));
      } catch {}
      saveToken(res.token);
      await startSession(res.user);
    } catch (err) {
      // startSession blendet das Login sofort aus und zeigt den App-Screen.
      // Schlägt danach etwas fehl, MUSS das Login wieder eingeblendet werden –
      // sonst hängt man im leeren, „blurry" App-Screen fest und kann sich nicht
      // mehr anmelden. Token verwerfen und Fehler sichtbar anzeigen.
      try { clearToken(); } catch {}
      try { showOnly(loginScreen()); } catch {}
      errBox.textContent = err && err.message ? err.message : "Anmeldung fehlgeschlagen";
      errBox.classList.remove("hidden");
    }
  });
}

function initChangePwForm() {
  document.getElementById("change-pw-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = document.getElementById("cpw-error");
    errBox.classList.add("hidden");

    const current = document.getElementById("cpw-current").value;
    const newPw = document.getElementById("cpw-new").value;
    const repeat = document.getElementById("cpw-repeat").value;

    if (newPw !== repeat) {
      errBox.textContent = "Die neuen Passwörter stimmen nicht überein";
      errBox.classList.remove("hidden");
      return;
    }

    try {
      await api.changePassword(current, newPw);
      // Nach erfolgreicher Änderung: frisches Profil holen und normal starten
      const user = await api.me();
      await startSession(user);
    } catch (err) {
      errBox.textContent = err.message;
      errBox.classList.remove("hidden");
    }
  });
}

// -----------------------------------------------------------------
// Menüs & globale Buttons verkabeln
// -----------------------------------------------------------------

function initMenusAndButtons() {
  // Benutzermenü (oben rechts)
  const userMenuBtn = document.getElementById("user-menu-btn");
  const userMenu = document.getElementById("user-menu");
  userMenuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    userMenu.classList.toggle("hidden");
  });
  // Klick irgendwo anders schließt das Menü wieder
  document.addEventListener("click", (e) => {
    if (userMenu.classList.contains("hidden")) return;
    if (userMenu.contains(e.target) || userMenuBtn.contains(e.target)) return;
    userMenu.classList.add("hidden");
  });
  // Escape schließt ebenfalls
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") userMenu.classList.add("hidden");
  });

  document.getElementById("btn-logout").addEventListener("click", () => {
    clearToken();
    location.reload();
  });
  document.getElementById("btn-open-profile").addEventListener("click", () => {
    userMenu.classList.add("hidden");
    openWindow({ singleton: true, key: "profile", appId: "profile", title: "Profil", w: 460, h: 560 });
  });
  document.getElementById("btn-open-notifications").addEventListener("click", () => {
    userMenu.classList.add("hidden");
    openWindow({ singleton: true, key: "notifycenter", appId: "notifycenter", title: "Benachrichtigungen", w: 520, h: 600 });
  });
  // Punkt am Benutzermenü-Button + am Menüeintrag, solange ungelesene
  // Benachrichtigungen vorhanden sind (Farbe = schwerste ungelesene Stufe).
  attachUnreadDot(userMenuBtn);
  attachUnreadDot(document.getElementById("btn-open-notifications"));
  document.getElementById("btn-open-settings").addEventListener("click", () => {
    userMenu.classList.add("hidden");
    openWindow({ singleton: true, key: "settings", appId: "settings", title: "Einstellungen", w: 560, h: 620 });
  });

  // Startmenü (unten links)
  const startBtn = document.getElementById("taskbar-menu-btn");
  const startMenu = document.getElementById("start-menu");
  startBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    startMenu.classList.toggle("hidden");
  });
  // Klick auf den abgedunkelten Hintergrund (nicht auf eine Kachel) schließt das Menü
  startMenu.addEventListener("click", (e) => {
    if (e.target === startMenu) startMenu.classList.add("hidden");
  });
  // Escape schließt ebenfalls
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") startMenu.classList.add("hidden");
  });

  // App aus dem Anwendungsmenü öffnen (von startmenu.js aufgerufen).
  function openAppFromMenu(app) {
    startMenu.classList.add("hidden");
    if (app === "network") openWindow({ key: "network", appId: "network", title: "Netzwerk-Scanner", w: 620, h: 500 });
    else if (app === "portscan") openWindow({ key: "portscan", appId: "portscan", title: "Portscan", w: 560, h: 480 });
    else if (app === "recordings") openWindow({ key: "recordings", appId: "recordings", title: "Session-Aufzeichnungen", w: 820, h: 560 });
    else if (app === "manage") openWindow({ singleton: true, key: "manage", appId: "manage", title: "Tenants & Standorte verwalten", w: 560, h: 620 });
    else if (app === "settings") openWindow({ singleton: true, key: "settings", appId: "settings", title: "Einstellungen", w: 560, h: 620 });
    else if (app === "permissions") openWindow({ singleton: true, key: "permissions", appId: "permissions", title: "Berechtigungen", w: 820, h: 640 });
    else if (app === "audit") openWindow({ key: "audit", appId: "audit", title: "Audit-Log", w: 720, h: 520 });
    else if (app === "scripts") openWindow({ key: "scripts", appId: "scripts", title: "Scripts", w: 680, h: 560 });
    else if (app === "bulk") openWindow({ key: "bulk", appId: "bulk", title: "Bulk Remote Shell", w: 720, h: 600 });
    else if (app === "towerdefense") openWindow({ key: "towerdefense", appId: "towerdefense", title: "Tower Defense", w: 760, h: 620 });
    else if (app === "gaminghub") openWindow({ singleton: true, key: "gaminghub", appId: "gaminghub", title: "Gaming Hub", w: 820, h: 640 });
    else if (app === "aichat") openWindow({ key: "aichat", appId: "aichat", title: "AI Chat", w: 860, h: 620 });
    else if (app === "audioplayer") openWindow({ singleton: true, key: "audioplayer", appId: "audioplayer", title: "Audio Player", w: 900, h: 680 });
    else if (app === "webbrowser") openWindow({ key: "webbrowser", appId: "webbrowser", title: "Browser", w: 1020, h: 720 });
    else if (app.startsWith("webapp:")) {
      // Gespeicherte Web-App: Seite direkt in eigenem Fenster öffnen.
      const wa = getWebApps().find((a) => `webapp:${a.id}` === app);
      if (wa) openWindow({ key: app, appId: "webapp", title: `${wa.icon} ${wa.name}`,
                           props: { url: wa.url, name: wa.name, icon: wa.icon }, w: 1020, h: 720 });
    }
    else if (app === "tickets") openWindow({ singleton: true, key: "tickets", appId: "tickets", title: "Tickets", w: 960, h: 660 });
    else if (app === "automation") openWindow({ key: "automation", appId: "automation", title: "Automation", w: 620, h: 640 });
    else if (app === "relay-manager") openWindow({ key: "relay-manager", appId: "relay-manager", title: "Explorer-Relay verwalten", w: 760, h: 560 });
    else if (app === "speedtest") openWindow({ key: "speedtest", appId: "speedtest", title: "Speedtest", w: 560, h: 640 });
    else if (app === "clients") { minimizeAll(); state.selection = null; renderMainContent(); }
  }

  // Anpassbares Anwendungsmenü (Drag & Drop, Ordner) initialisieren.
  initStartMenu({
    root: startMenu,
    username: state.user?.username,
    onOpenApp: openAppFromMenu,
    allowed: _appAllowed,
  });
  // Gespeicherte Web-Apps (interner Browser, ⭐ "Als App speichern") als
  // eigene Startmenü-Einträge registrieren.
  for (const wa of getWebApps()) addCatalogEntry(`webapp:${wa.id}`, wa.icon, wa.name);

  // Sidebar: Ein-/Ausklappen, Breite ziehen, Klick-zum-Ausklappen. Details in
  // initSidebarResize() unten (inkl. Persistenz der Breite/Zustand).
  initSidebarResize();

  // Tenants/Standorte verwalten (Zahnrad in der Sidebar-Kopfzeile)
  document.getElementById("btn-manage-hierarchy").addEventListener("click", () => {
    openWindow({ singleton: true, key: "manage", appId: "manage", title: "Tenants & Standorte verwalten", w: 560, h: 620 });
  });

  // "Client hinzufügen"
  document.getElementById("btn-add-client").addEventListener("click", () => {
    openWindow({ singleton: true, key: "add-client", appId: "add-client", title: "Client hinzufügen", w: 560, h: 600 });
  });
}

// -----------------------------------------------------------------
// Sidebar: Breite ziehen, Ein-/Ausklappen, Klick-zum-Ausklappen
// -----------------------------------------------------------------
const SB_COLLAPSED_W = 46;   // Breite im eingeklappten Zustand (siehe CSS)
const SB_MIN_W = 170;        // kleinste "ausgeklappte" Breite
const SB_MAX_W = 560;        // größte Breite
const SB_SNAP = 90;          // zieht man schmaler als das -> gilt als eingeklappt

// Wendet state.sidebar (Breite + collapsed) auf die DOM an.
function applySidebarState() {
  const sb = document.getElementById("sidebar");
  if (!sb) return;
  const s = state.sidebar || (state.sidebar = { width: 260, collapsed: false });
  if (s.collapsed) {
    sb.classList.add("collapsed");
    // Breite bleibt per CSS auf 46px; inline-Breite merkt sich den Ausklapp-Wert.
    sb.style.width = (s.width || 260) + "px";
  } else {
    sb.classList.remove("collapsed");
    sb.style.width = (s.width || 260) + "px";
  }
}

function initSidebarResize() {
  const sb = document.getElementById("sidebar");
  const resizer = document.getElementById("sidebar-resizer");
  const collapseBtn = document.getElementById("sidebar-collapse-btn");
  if (!sb || !resizer) return;

  applySidebarState();

  const persist = () => { try { scheduleSave(state); } catch {} };

  function setCollapsed(collapsed) {
    state.sidebar.collapsed = collapsed;
    applySidebarState();
    persist();
  }

  // « -Button: einklappen (nur relevant, wenn ausgeklappt).
  collapseBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!state.sidebar.collapsed) setCollapsed(true);
    else setCollapsed(false);
  });

  // Klick IRGENDWO auf die eingeklappte Sidebar -> wieder ausklappen.
  sb.addEventListener("click", () => {
    if (state.sidebar.collapsed) setCollapsed(false);
  });

  // --- Breite ziehen ---
  let dragging = false;
  function onMove(clientX) {
    const left = sb.getBoundingClientRect().left;
    let w = clientX - left;
    if (w <= SB_SNAP) {
      // So schmal wie eingeklappt gezogen -> als eingeklappt markieren,
      // aber die zuletzt gewählte Ausklapp-Breite behalten (für's Wieder-Auf).
      if (!state.sidebar.collapsed) { state.sidebar.collapsed = true; sb.classList.add("collapsed"); }
      return;
    }
    // wieder ausgeklappt
    if (state.sidebar.collapsed) { state.sidebar.collapsed = false; sb.classList.remove("collapsed"); }
    w = Math.max(SB_MIN_W, Math.min(SB_MAX_W, w));
    state.sidebar.width = w;
    sb.style.width = w + "px";
  }
  resizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    dragging = true;
    sb.classList.add("resizing");
    resizer.classList.add("active");
    document.body.style.userSelect = "none";
  });
  window.addEventListener("mousemove", (e) => { if (dragging) onMove(e.clientX); });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    sb.classList.remove("resizing");
    resizer.classList.remove("active");
    document.body.style.userSelect = "";
    persist();
  });
}

// -----------------------------------------------------------------
// Live-Updates über Socket.IO
// -----------------------------------------------------------------

function initLiveUpdates() {
  // Client-Liste hat sich geändert (neuer Agent, online/offline) -> neu laden.
  // Wir laden auch die Hierarchie neu, weil ein neu registrierter Client einen
  // neuen Tenant (z.B. "Uncategorized") ausgelöst haben könnte, der sonst nicht
  // in der Sidebar auftauchen würde.
  dashboardSocket.on("clients:changed", async () => {
    await refreshAll();
    await reloadPerms();
    applyAppVisibility();
  });

  // Live-Rückmeldungen des Agenten zu Update/Uninstall (Diagnose): zeigt an,
  // was auf dem Client passiert - statt nur eines 60s-Timeouts.
  dashboardSocket.on("client:action-log", (d) => {
    const c = state.clients.find((x) => x.id === d.id);
    const name = c ? c.hostname : (d.id || "Client");
    const kind = d.kind === "uninstall" ? "Deinstallation" : "Update";
    const level = d.stage === "error" ? "error"
      : (d.stage === "launched-fallback" ? "warn" : "info");
    let msg = `${name}: ${kind} – ${d.stage}`;
    if (d.detail) msg += `\n${d.detail}`;
    if (d.agent_version) msg += `\n(Agent-Version: ${d.agent_version})`;
    window.notify?.(msg, level, 10000, { tag: `agent-${d.kind}:${d.id}` });
  });

  // Fehler bei der Bildschirmübertragung (z.B. headless VM) als Notification
  // + Eintrag im Audit-Log
  dashboardSocket.on("screen-error", (data) => {
    notifyError(data.error || "Fehler bei der Bildschirmübertragung", "error", "remote-screen", 12000);
  });

  // Agent-Absturz: Der Agent hat sich selbst neu gestartet und meldet den
  // Fehler - dem Nutzer deutlich anzeigen (letzte Traceback-Zeile reicht).
  dashboardSocket.on("client:agent-crashed", (d) => {
    const lastLine = String(d.error || "").trim().split("\n").filter(Boolean).slice(-1)[0] || "unbekannter Fehler";
    notifyError(`⚠️ Agent auf ${d.hostname || d.id} ist abgestürzt und wurde automatisch neu gestartet.\nFehler: ${lastLine}\n(Details im Audit-Log)`,
      "error", `agent-crash-${d.id}`, 20000);
  });

  // Neue Metriken für einen Client
  dashboardSocket.on("client:metrics", ({ id, metrics }) => {
    const client = state.clients.find((c) => c.id === id);
    const wasOnline = client ? !!client.online : true;
    if (client) {
      client.metrics = metrics;
      client.online = true;
    }
    recordMetrics(id, metrics);

    // Live-Refresh für Dashboard-Widgets (aggregierte Flotten-Werte).
    try { window.dispatchEvent(new CustomEvent("metrics-updated", { detail: { id } })); } catch {}

    // Gezieltes Update: nur die Werte/SVGs der sichtbaren Panels dieses
    // Clients überschreiben - das Client-Panel wird NICHT neu eingefügt.
    let touched = 0;
    try {
      touched = updateClientLayouts(id);
    } catch { touched = 0; }
    // Fallback nur für Ansichten, die (noch) kein Layout-Host-Update können.
    const sel = state.selection;
    if (!touched && sel && (
      (sel.type === "client" && sel.id === id) ||
      sel.type === "tenant" || sel.type === "location"
    )) {
      renderMainContent();
    }
    // Sidebar NICHT bei jedem Metrik-Tick neu bauen (verursachte Hover-
    // Flackern/Springen der Client-Zeilen) - nur wenn sich der Online-Status
    // sichtbar geändert hat (offline -> online).
    if (!wasOnline) renderSidebar();
  });

  dashboardSocket.on("client:offline", ({ id }) => {
    const client = state.clients.find((c) => c.id === id);
    if (client) { client.online = false; client.metrics = null; }
    renderSidebar();
    renderMainContent();
  });
}

// -----------------------------------------------------------------
// Start
// -----------------------------------------------------------------

// Legt einen Client-Drop auf dem Desktop (Fenster-Layer) als eigenes
// Client-Fenster ab. Der Client bleibt in der Seitenleiste erhalten.
function initDesktopDrop() {
  const layer = document.getElementById("window-layer");
  const dropZone = document.getElementById("main-content") || document.body;
  if (!dropZone) return;
  dropZone.addEventListener("dragover", (e) => {
    if (e.dataTransfer && [...e.dataTransfer.types].includes("text/x-rmm-client")) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      dropZone.classList.add("desktop-drop-hint");
    }
  });
  dropZone.addEventListener("dragleave", (e) => {
    if (e.target === dropZone) dropZone.classList.remove("desktop-drop-hint");
  });
  dropZone.addEventListener("drop", (e) => {
    const clientId = e.dataTransfer?.getData("text/x-rmm-client");
    dropZone.classList.remove("desktop-drop-hint");
    if (!clientId) return;
    e.preventDefault();
    const client = state.clients.find((c) => c.id === clientId);
    if (!client) return;
    const r = (layer || dropZone).getBoundingClientRect();
    openWindow({
      singleton: true,
      key: `panelpart-${clientId}-client-x`, appId: "panelpart",
      title: client.hostname,
      props: { clientId, part: "client" },
      clientColor: client.color,
      x: Math.max(0, e.clientX - r.left - 60),
      y: Math.max(0, e.clientY - r.top - 20),
      w: 900, h: 620,
    });
  });
}

async function main() {
  // Rückkehr vom Spotify-OAuth-Login (?code&state=rmm-spotify:...) verarbeiten
  // und die URL säubern - unabhängig vom RMM-Login-Status.
  try { await handleSpotifyCallback(); } catch {}
  // Renderer für Fenster-Inhalte registrieren
  setContentRenderer(renderWindowContent);
  // (setOnWindowsChanged wird in startSession gesetzt - inkl. Zustand-Speichern)
  // Sidebar-Auswahl -> Haupt-Panel aktualisieren
  setOnSelect(renderMainContent);
  // Bearbeiten-Modus wurde umgeschaltet (z.B. "Bearbeiten beenden"-Button in
  // der Client-/Dashboard-Toolbar) -> Ansicht mit/ohne Werkzeuge neu zeichnen.
  window.addEventListener("dashedit-changed", () => renderMainContent());
  // Edit-Client-Änderungen -> alles neu laden
  setEditOnChanged(refreshAll);
  setManageOnChanged(refreshAll);

  initLoginForm();
  initChangePwForm();
  initMenusAndButtons();
  initTaskbar();
  initLiveUpdates();
  initDesktopDrop();

  // Ist noch ein gültiges Token vorhanden? Dann direkt einloggen.
  const token = localStorage.getItem("rmm_token");
  if (token) {
    try {
      const user = await api.me();
      await startSession(user);
      return;
    } catch {
      clearToken(); // Token abgelaufen/ungültig -> normaler Login
    }
  }
  showOnly(loginScreen());
}

main();

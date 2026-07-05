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
import { api, saveToken, clearToken } from "./api.js";
import { dashboardSocket } from "./socket.js";
import { applyTheme, applyAccent } from "./theme.js";
import { setLanguage, applyStaticTranslations } from "./i18n_apply.js";

import { renderSidebar, setOnSelect } from "./sidebar.js";
import { renderMainContent } from "./panel.js";
import { renderTaskbar, initTaskbar } from "./taskbar.js";
import { setContentRenderer, setOnWindowsChanged, openWindow } from "./windowmanager.js";
import { recordMetrics } from "./metricshistory.js";
import { notify, notifyError } from "./notify.js";

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
import { renderAutomation } from "./apps/automation.js";
import { renderNotifications } from "./apps/notifications.js";
import { renderNetwork } from "./apps/network.js";
import { renderSettings } from "./apps/settings.js";
import { renderAudit } from "./apps/audit.js";
import { renderProfile } from "./apps/profile.js";

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
  towerdefense: renderTowerDefense,
  automation: renderAutomation,
  notifications: renderNotifications,
  network: renderNetwork,
  settings: renderSettings,
  audit: renderAudit,
  profile: renderProfile,
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
}

async function reloadClients() {
  state.clients = await api.getClients();
}

async function refreshAll() {
  await Promise.all([reloadHierarchy(), reloadClients()]);
  renderSidebar();
  renderMainContent();
}

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

  await refreshAll();
  renderTaskbar();
}

function initLoginForm() {
  // Verfügbare AD-Realms ins Dropdown laden (zusätzlich zu "Lokal")
  const realmSelect = document.getElementById("login-realm");
  api.getLoginRealms().then((realms) => {
    for (const r of realms) {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = r.name + " (AD)";
      realmSelect.appendChild(opt);
    }
  }).catch(() => { /* Backend evtl. noch nicht bereit - egal, "Lokal" reicht */ });

  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = document.getElementById("login-error");
    errBox.classList.add("hidden");
    try {
      const res = await api.login(
        document.getElementById("login-username").value,
        document.getElementById("login-password").value,
        realmSelect.value
      );
      saveToken(res.token);
      await startSession(res.user);
    } catch (err) {
      errBox.textContent = err.message;
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
    openWindow({ key: "profile", appId: "profile", title: "Profil", w: 460, h: 560 });
  });
  document.getElementById("btn-open-settings").addEventListener("click", () => {
    userMenu.classList.add("hidden");
    openWindow({ key: "settings", appId: "settings", title: "Einstellungen", w: 560, h: 620 });
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

  startMenu.querySelectorAll("[data-app]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const app = btn.dataset.app;
      startMenu.classList.add("hidden");
      if (app === "network") openWindow({ key: "network", appId: "network", title: "Netzwerk-Scanner", w: 620, h: 500 });
      else if (app === "recordings") openWindow({ key: "recordings", appId: "recordings", title: "Session-Aufzeichnungen", w: 820, h: 560 });
      else if (app === "manage") openWindow({ key: "manage", appId: "manage", title: "Tenants & Standorte verwalten", w: 560, h: 620 });
      else if (app === "settings") openWindow({ key: "settings", appId: "settings", title: "Einstellungen", w: 560, h: 620 });
      else if (app === "audit") openWindow({ key: "audit", appId: "audit", title: "Audit-Log", w: 720, h: 520 });
      else if (app === "scripts") openWindow({ key: "scripts", appId: "scripts", title: "Scripts", w: 680, h: 560 });
      else if (app === "bulk") openWindow({ key: "bulk", appId: "bulk", title: "Bulk Remote Shell", w: 720, h: 600 });
      else if (app === "towerdefense") openWindow({ key: "towerdefense", appId: "towerdefense", title: "Tower Defense", w: 760, h: 620 });
      else if (app === "automation") openWindow({ key: "automation", appId: "automation", title: "Automation", w: 620, h: 640 });
      else if (app === "notifications") openWindow({ key: "notifications", appId: "notifications", title: "Benachrichtigungen", w: 600, h: 560 });
      else if (app === "clients") { state.selection = null; renderMainContent(); }
    })
  );

  // Sidebar einklappen
  document.getElementById("sidebar-collapse-btn").addEventListener("click", () => {
    document.getElementById("sidebar").classList.toggle("collapsed");
  });

  // Tenants/Standorte verwalten (Zahnrad in der Sidebar-Kopfzeile)
  document.getElementById("btn-manage-hierarchy").addEventListener("click", () => {
    openWindow({ key: "manage", appId: "manage", title: "Tenants & Standorte verwalten", w: 560, h: 620 });
  });

  // "Client hinzufügen"
  document.getElementById("btn-add-client").addEventListener("click", () => {
    openWindow({ key: "add-client", appId: "add-client", title: "Client hinzufügen", w: 560, h: 600 });
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
  });

  // Fehler bei der Bildschirmübertragung (z.B. headless VM) als Notification
  // + Eintrag im Audit-Log
  dashboardSocket.on("screen-error", (data) => {
    notifyError(data.error || "Fehler bei der Bildschirmübertragung", "error", "remote-screen", 12000);
  });

  // Neue Metriken für einen Client
  dashboardSocket.on("client:metrics", ({ id, metrics }) => {
    const client = state.clients.find((c) => c.id === id);
    if (client) {
      client.metrics = metrics;
      client.online = true;
    }
    recordMetrics(id, metrics);

    // Nur neu rendern, wenn gerade dieser Client (oder seine Gruppe) sichtbar ist
    const sel = state.selection;
    if (sel && (
      (sel.type === "client" && sel.id === id) ||
      sel.type === "tenant" || sel.type === "location"
    )) {
      renderMainContent();
    }
    renderSidebar();
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

async function main() {
  // Renderer für Fenster-Inhalte registrieren
  setContentRenderer(renderWindowContent);
  // Taskbar aktualisieren, sobald Fenster geöffnet/geschlossen/fokussiert werden
  setOnWindowsChanged(renderTaskbar);
  // Sidebar-Auswahl -> Haupt-Panel aktualisieren
  setOnSelect(renderMainContent);
  // Edit-Client-Änderungen -> alles neu laden
  setEditOnChanged(refreshAll);
  setManageOnChanged(refreshAll);

  initLoginForm();
  initChangePwForm();
  initMenusAndButtons();
  initTaskbar();
  initLiveUpdates();

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

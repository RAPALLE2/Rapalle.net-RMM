// i18n_apply.js
// -------------
// Wendet die aktuelle Sprache auf die FESTEN HTML-Elemente an (die direkt in
// index.html stehen, z.B. Sidebar-Überschrift, "Client hinzufügen"-Button,
// Startmenü-Einträge). Dynamisch gerenderte Bereiche (Panel, Fenster) nutzen
// t() direkt beim Rendern.
//
// Diese Datei re-exportiert setLanguage/getLanguage/t aus i18n.js, damit
// andere Module nur EINEN Import brauchen.

import { t, setLanguage as _setLanguage, getLanguage } from "./i18n.js";

export { getLanguage };
export { t } from "./i18n.js";

export function setLanguage(lang) {
  _setLanguage(lang);
  // Sprache zusätzlich serverseitig sichern (in jedem Browser gleich).
  import("./persist.js").then((m) => m.syncToServerSoon()).catch(() => {});
}

// Übersetzt die statischen Elemente in index.html
export function applyStaticTranslations() {
  // Sidebar-Kopf ("Geräte")
  const sidebarHeader = document.querySelector(".sidebar-header span");
  if (sidebarHeader) sidebarHeader.textContent = t("devices");

  // "Client hinzufügen"-Button
  const addBtn = document.getElementById("btn-add-client");
  if (addBtn) addBtn.textContent = t("add_client");

  // Startmenü-Kacheln (data-app -> Übersetzungsschlüssel). Die Kacheln haben
  // die Struktur <button><span class="app-icon">…</span><span>Text</span></button>,
  // wir ersetzen nur den Text-Span, das Icon bleibt.
  const startMenuKeys = {
    clients: "device_overview", manage: "tenants_locations", scripts: "scripts",
    bulk: "bulk_shell", network: "network_scanner", recordings: "recordings",
    towerdefense: "tower_defense", audit: "audit_log", settings: "settings",
    automation: "automation", notifications: "notifications",
    todos: "todos", privacy: "privacy", patching: "patching",
  };
  document.querySelectorAll("#start-menu [data-app]").forEach((btn) => {
    const key = startMenuKeys[btn.dataset.app];
    if (!key) return;
    // WICHTIG: ":scope >" — nur direkte Kind-Spans! Ohne das traf der Selektor
    // nach der Emoji->SVG-Ersetzung den .svgicon-Span IM Icon und schrieb den
    // uebersetzten Text mit 34px ins Icon (doppelter Riesentext im Startmenue).
    const textSpan = btn.querySelector(":scope > span:not(.app-icon)");
    if (textSpan) textSpan.textContent = t(key);
    else btn.textContent = t(key);
  });

  // Benutzermenü
  const profileBtn = document.getElementById("btn-open-profile");
  // Der Schluessel existiert in BEIDEN Sprachen - die frueher hier fest
  // eingebaute englische Zeichenkette war doppelt gepflegt und wich vom
  // Sprachpaket ab. t() erledigt das allein.
  if (profileBtn) profileBtn.textContent = t("u_profil_passwort_andern");
  const settingsBtn = document.getElementById("btn-open-settings");
  if (settingsBtn) settingsBtn.textContent = t("settings");
  const logoutBtn = document.getElementById("btn-logout");
  if (logoutBtn) logoutBtn.textContent = t("logout");
}

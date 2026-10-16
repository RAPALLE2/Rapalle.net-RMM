// modules.js
// ----------
// Modul-Loader des Frontends (Core). Der Core laeuft ohne jedes Modul.
//
// Ablauf nach dem Login:
//   1. loadModules() holt GET /api/modules/frontend (nur AKTIVE Module).
//   2. Jede App eines Moduls wird registriert und erscheint im Startmenue.
//      Der App-Code wird erst beim ERSTEN Oeffnen geladen (dynamischer Import).
//   3. Optional laeuft pro Modul ein "init"-Skript (Widgets, Hooks ...).
//
// Absturzschutz: Jeder Aufruf in Modul-Code ist abgesichert. Scheitert das Laden
// oder Zeichnen einer App, zeigt NUR das betroffene Fenster einen Fehler, der
// Core laeuft weiter, und der Absturz wird dem Backend gemeldet (-> Crashbericht,
// Modul wird deaktiviert, Fehler erscheint in der Modul-App).
//
// Fuer Modul-Autoren (siehe src/modules/backend/example/GUIDE.md): Ein Modul
// importiert Core-Dateien mit ABSOLUTEN Pfaden, z.B.
//   import { api } from "/js/app/api.js";
//   import { registerModuleApi } from "/js/app/modules.js";

import { getToken } from "./api.js";
import { BACKEND_URL } from "./config.js";
import { openWindow } from "./windowmanager.js";
import { addCatalogEntry, removeCatalogEntry } from "./startmenu.js";

const mods = new Map();          // moduleId -> Manifest aus dem Backend
const apps = new Map();          // appId -> { mid, app, base }
const apis = new Map();          // moduleId -> frei definierbares API-Objekt
const listeners = new Set();
const addedToMenu = new Set();
let loaded = false;
const initDone = new Set();

async function req(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const res = await fetch(`${BACKEND_URL}${path}`, { ...opts, headers });
  let data = null;
  try { data = await res.json(); } catch { /* leere Antwort */ }
  if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
  return data;
}

/** Authentifizierte Backend-Anfrage (JSON) fuer Modul-Code und die Modul-App. */
export const moduleRequest = (...a) => req(...a);

// ---------------------------------------------------------------- Abfragen
export function isModuleOn(mid) { return mods.has(mid); }
export function moduleFeature(mid, fid) { return !!mods.get(mid)?.features?.[fid]; }
export function getModules() { return [...mods.values()]; }
export function moduleAppInfo(appId) { return apps.get(appId)?.app || null; }
export function moduleAppPerm(appId) {
  const a = apps.get(appId);
  return a ? (a.app.perm || "") : undefined;       // undefined = keine Modul-App
}
export function onModulesChanged(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function changed() { for (const fn of listeners) { try { fn(); } catch (e) { console.error(e); } } }

/** Modul-eigene Funktionen fuer andere Teile des Frontends bereitstellen. */
export function registerModuleApi(mid, obj) { apis.set(mid, obj); }
/** Funktionen eines aktiven Moduls holen - null, wenn das Modul aus ist. */
export function getModuleApi(mid) { return mods.has(mid) ? (apis.get(mid) || null) : null; }

// ---------------------------------------------------------------- Knöpfe im Agent-Panel
// Module können eigene Knöpfe im Aktionen-Bereich eines Agenten anbieten:
//   registerClientAction("proxmox", { label(client) -> "🖧 Proxmox" | null, onClick(client) })
// label() gibt null zurück, wenn der Knopf für diesen Agenten nicht erscheinen soll
// (z.B. fehlendes Recht). Ist das Modul aus, erscheinen seine Knöpfe nicht.
const clientActions = new Map();
export function registerClientAction(mid, action) {
  const list = clientActions.get(mid) || [];
  list.push(action);
  clientActions.set(mid, list);
}
export function getClientActions() {
  const out = [];
  for (const [mid, list] of clientActions) if (mods.has(mid)) list.forEach((a) => out.push({ mid, ...a }));
  return out;
}

// ---------------------------------------------------------------- Abstuerze
export async function reportModuleCrash(mid, where, err, fatal = true) {
  try {
    await req(`/api/modules/${encodeURIComponent(mid)}/report-crash`, {
      method: "POST",
      body: JSON.stringify({
        where: String(where || "frontend").slice(0, 200),
        error: String(err?.message || err || "").slice(0, 1000),
        stack: String(err?.stack || "").slice(0, 6000),
        fatal: !!fatal,
      }),
    });
  } catch { /* Melden darf nie selbst scheitern */ }
  if (fatal) {
    window.notify?.(`Modul "${mods.get(mid)?.name || mid}" ist abgestürzt und wurde deaktiviert. Details: Modul-App.`, "error", 12000);
    await loadModules();                 // Liste aktualisieren (Modul ist jetzt weg)
  }
}

// Laufzeitfehler aus Modul-Dateien (z.B. in Klick-Handlern) dem Modul zuordnen.
function _moduleOfUrl(u) { const m = /\/modules\/frontend\/([a-z0-9_-]+)\//.exec(String(u || "")); return m ? m[1] : null; }
window.addEventListener("error", (e) => {
  const mid = _moduleOfUrl(e.filename); if (mid && mods.has(mid)) reportModuleCrash(mid, "Laufzeitfehler", e.error || e.message, false);
});
window.addEventListener("unhandledrejection", (e) => {
  const mid = _moduleOfUrl(e.reason?.stack); if (mid && mods.has(mid)) reportModuleCrash(mid, "Promise-Fehler", e.reason, false);
});

// ---------------------------------------------------------------- Laden
export async function loadModules() {
  let list = [];
  try { list = (await req("/api/modules/frontend")).modules || []; }
  catch (e) { console.warn("Module konnten nicht geladen werden:", e.message); }

  // Alles zuruecksetzen, was aus dem vorigen Stand stammt
  const oldApps = new Set(apps.keys());
  mods.clear(); apps.clear();
  for (const m of list) {
    mods.set(m.id, m);
    for (const a of m.apps || []) apps.set(a.id, { mid: m.id, app: a, base: m.base });
  }
  // Startmenue abgleichen
  for (const id of oldApps) if (!apps.has(id) && addedToMenu.has(id)) { removeCatalogEntry(id); addedToMenu.delete(id); }
  for (const [id, { app }] of apps) {
    if (app.startmenu === false || addedToMenu.has(id)) continue;
    addCatalogEntry(id, app.icon || "🧩", app.label || id);
    addedToMenu.add(id);
  }
  // init-Skripte (einmal je Modul und Seitenaufruf)
  for (const m of list) {
    if (!m.init?.entry || initDone.has(m.id)) continue;
    try {
      const mod = await import(/* @vite-ignore */ m.base + m.init.entry);
      const fn = mod[m.init.export || "init"];
      if (typeof fn === "function") await fn({ module: m, req });
      initDone.add(m.id);
    } catch (e) {
      console.error(`Modul ${m.id}: init fehlgeschlagen`, e);
      await reportModuleCrash(m.id, "init", e, true);
    }
  }
  loaded = true;
  changed();
}

// ---------------------------------------------------------------- Apps
function _box(body, title, text) {
  body.innerHTML = `<div style="padding:22px;color:var(--subtext);max-width:560px">
    <div style="font-size:15px;color:var(--text);margin-bottom:6px">${title}</div>
    <div style="font-size:13px;line-height:1.5">${text}</div></div>`;
}
const _esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/** Zeichnet die App eines Moduls ins Fenster. true = gehoert zu einem aktiven Modul. */
export function renderModuleApp(appId, body, win) {
  const info = apps.get(appId);
  if (!info) return false;
  const { mid, app, base } = info;
  (async () => {
    try {
      const mod = await import(/* @vite-ignore */ base + app.entry);
      const fn = mod[app.export || "render"];
      if (typeof fn !== "function") throw new Error(`Funktion "${app.export || "render"}" fehlt in ${app.entry}`);
      await fn(body, win);
    } catch (e) {
      console.error(`Modul ${mid}: App ${appId} abgestuerzt`, e);
      _box(body, "⚠ Modul abgestürzt",
        `Das Modul <b>${_esc(mods.get(mid)?.name || mid)}</b> ist abgestürzt und wird deaktiviert.<br>
         <code style="font-size:12px">${_esc(e.message)}</code><br>Der Rest des RMM läuft normal weiter.
         Details stehen in der Modul-App.`);
      reportModuleCrash(mid, `App ${appId}`, e, true);
    }
  })();
  return true;
}

/** Oeffnet eine Modul-App aus dem Startmenue. true = behandelt. */
export function openModuleApp(appId) {
  const info = apps.get(appId);
  if (!info) return false;
  const a = info.app;
  openWindow({ key: a.key || a.id, appId: a.id, title: a.title || a.label || a.id, w: a.w || 800, h: a.h || 600 });
  return true;
}

/** Text fuer Fenster, deren App zu einem (inzwischen) inaktiven Modul gehoert. */
export function inactiveModuleBox(body, appId) {
  _box(body, "Modul nicht aktiv",
    `Die App <code>${_esc(appId)}</code> gehört zu einem Modul, das ausgeschaltet ist oder abgestürzt war.
     Ein Administrator kann es in der <b>Modul-App</b> aktivieren.`);
}
export const modulesLoaded = () => loaded;

/**
 * Oeffnet eine Webseite im eingebetteten Browser - das ist eine Funktion des
 * Moduls 'browser'. Ist es aus, oeffnet sich die Seite in einem neuen Tab.
 */
export function openWebsiteEntry(entry) {
  const b = getModuleApi("browser");
  if (b?.openWebsiteEntry) return b.openWebsiteEntry(entry);
  if (entry?.url) window.open(entry.url, "_blank", "noopener");
}


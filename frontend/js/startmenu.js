// startmenu.js
// ------------
// Anpassbares Anwendungsmenü: freie Anordnung per Drag & Drop, Ordner
// (erstellen / umbenennen / öffnen / schließen / löschen), Apps in Ordner
// ziehen und wieder heraus. Das Layout wird pro Benutzer in localStorage
// gespeichert. Der eigentliche App-Katalog (welche Apps existieren, Icon,
// Label, Öffnen-Verhalten) bleibt in index.html/app.js — dieses Modul
// verwaltet nur die ANORDNUNG und ruft beim Klick den vorhandenen Opener auf.
//
// Layout-Modell (localStorage "rmm_appmenu:<user>"):
//   { items: [ {t:"app", id:"scripts"} | {t:"folder", id:"f_ab12",
//               name:"Wartung", open:false, apps:["scripts","bulk"]} ] }
// Apps, die im Katalog neu hinzukommen und noch nirgends stehen, werden
// automatisch hinten angehängt. Nicht mehr existierende Apps fallen raus.

import { uiPrompt, uiConfirm } from "./utils.js";
import { getDashEdit } from "./persist.js";
import { t } from "./i18n.js";

let CATALOG = {};          // id -> { id, icon, label }
let CATALOG_ORDER = [];    // Reihenfolge aus dem DOM (für Neu-Apps)
let layout = { items: [] };
let userKey = "rmm_appmenu:default";
let openApp = () => {};    // Callback: (appId) => void  (aus app.js)
let isAllowed = () => true;// Callback: (appId) => bool  (Rechte-Gate)
let editMode = false;

// -------------------------------------------------------------
// Persistenz
// -------------------------------------------------------------
function load() {
  try {
    const raw = localStorage.getItem(userKey);
    if (raw) layout = JSON.parse(raw);
  } catch { /* defekt -> Default */ }
  if (!layout || !Array.isArray(layout.items)) layout = { items: [] };
}
function save() {
  try {
    localStorage.setItem(userKey, JSON.stringify(layout));
    // Startmenü-Layout zusätzlich serverseitig sichern.
    import("./persist.js").then((m) => m.syncToServerSoon()).catch(() => {});
  } catch {}
}

// Katalog aus den (versteckten) DOM-Buttons in index.html lesen.
function readCatalog(rootEl) {
  CATALOG = {}; CATALOG_ORDER = [];
  rootEl.querySelectorAll("[data-app]").forEach((btn) => {
    const id = btn.dataset.app;
    // ":scope >" = nur DIREKTE Kind-Spans. Ohne das trifft der Selektor nach
    // der Emoji->SVG-Ersetzung (icons.js) den .svgicon-Span IM .app-icon und
    // liefert einen leeren Label-Text -> graue, leere Kacheln.
    const iconEl = btn.querySelector(":scope > .app-icon");
    const labelEl = btn.querySelector(":scope > span:not(.app-icon)");
    CATALOG[id] = {
      id,
      // innerHTML statt textContent: so wird sowohl ein Emoji als auch ein
      // von icons.js erzeugtes <span class="svgicon"> korrekt übernommen.
      icon: iconEl ? iconEl.innerHTML : "",
      iconText: iconEl ? iconEl.textContent.trim() : "",
      label: labelEl ? labelEl.textContent.trim() : id,
    };
    CATALOG_ORDER.push(id);
  });
}

// Layout mit dem Katalog abgleichen: fehlende Apps anhängen, tote entfernen.
function reconcile() {
  const placed = new Set();
  const walk = (arr) => arr.forEach((it) => {
    if (it.t === "app") placed.add(it.id);
    else if (it.t === "folder") (it.apps || []).forEach((a) => placed.add(a));
  });
  walk(layout.items);

  // Tote App-Referenzen entfernen
  layout.items = layout.items.filter((it) => {
    if (it.t === "app") return !!CATALOG[it.id];
    if (it.t === "folder") { it.apps = (it.apps || []).filter((a) => CATALOG[a]); return true; }
    return false;
  });

  // Neue Apps (im Katalog, aber nirgends platziert) hinten anhängen
  for (const id of CATALOG_ORDER) {
    if (!placed.has(id) && CATALOG[id]) layout.items.push({ t: "app", id });
  }
  save();
}

// -------------------------------------------------------------
// Öffentliche Init
// -------------------------------------------------------------
export function initStartMenu({ root, username, onOpenApp, allowed }) {
  userKey = `rmm_appmenu:${username || "default"}`;
  openApp = onOpenApp || openApp;
  isAllowed = allowed || isAllowed;
  readCatalog(root);
  load();
  reconcile();
  buildChrome(root);
  // Bearbeiten-Modus ist an denselben globalen "Layout bearbeiten"-Schalter
  // gekoppelt wie das Dashboard (Profil-Checkbox / "Bearbeiten beenden").
  // Kein separater Menü-Button mehr.
  editMode = !!getDashEdit();
  window.addEventListener("dashedit-changed", () => {
    editMode = !!getDashEdit();
    applyEditChrome();
    render();
  });
  applyEditChrome();
  render();
}

// Wird von app.js nach dem Laden der Rechte aufgerufen (blendet Apps ohne
// Recht aus, ohne das Layout zu verändern).
export function refreshStartMenu() { render(); }

// Benutzer NACHTRÄGLICH setzen.
//
// Warum das nötig ist: initStartMenu() läuft aus initMenusAndButtons() heraus,
// also BEVOR der Login durch ist - state.user ist da noch null. Das Layout
// landete deshalb immer unter "rmm_appmenu:default" statt unter dem Benutzer.
// Dieser Schlüssel wurde vom Server-Abgleich (persist.js) bewusst ignoriert,
// weil er zu keinem Benutzer gehört. Folge: Ordner und Anordnung im Startmenü
// lebten NUR im Browser und waren unter einer anderen URL weg.
//
// Diese Funktion wird nach dem Login (und nach hydrateFromServer) aufgerufen:
// sie schaltet auf den echten Benutzer-Schlüssel um, übernimmt einmalig ein
// evtl. vorhandenes "default"-Layout und zeichnet neu.
export function setStartMenuUser(username) {
  const nextKey = `rmm_appmenu:${username || "default"}`;
  if (nextKey === userKey) return;
  const legacyKey = userKey;                 // in der Regel "rmm_appmenu:default"
  userKey = nextKey;
  load();
  // Noch nichts unter dem Benutzer gespeichert? Dann das alte, browserlokale
  // Layout einmalig übernehmen, damit niemand seine Ordner verliert.
  const empty = !layout || !Array.isArray(layout.items) || !layout.items.length;
  if (empty) {
    try {
      const raw = localStorage.getItem(legacyKey);
      if (raw) {
        const old = JSON.parse(raw);
        if (old && Array.isArray(old.items) && old.items.length) layout = old;
      }
    } catch {}
  }
  // Der default-Schlüssel wird nicht mehr gebraucht und würde beim nächsten
  // Benutzer am selben Browser nur stören.
  try { if (legacyKey.endsWith(":default")) localStorage.removeItem(legacyKey); } catch {}
  reconcile();   // speichert unter dem neuen Schlüssel + stößt den Server-Sync an
  render();
}

// Dynamische Katalog-Einträge (z.B. gespeicherte Web-Apps aus dem internen
// Browser): fügen eine App zur Laufzeit ins Startmenü ein bzw. entfernen sie.
export function addCatalogEntry(id, icon, label) {
  const existed = !!CATALOG[id];
  CATALOG[id] = { id, icon: icon || "🌐", iconText: icon || "🌐", label: label || id };
  if (!existed) CATALOG_ORDER.push(id);
  reconcile();   // hängt neue Apps automatisch ans Layout an
  save();
  render();
}
export function removeCatalogEntry(id) {
  if (!CATALOG[id]) return;
  delete CATALOG[id];
  CATALOG_ORDER = CATALOG_ORDER.filter((x) => x !== id);
  reconcile();   // entfernt tote Referenzen aus dem Layout
  save();
  render();
}

// -------------------------------------------------------------
// Gerüst (Header mit Bearbeiten-Button + Grid-Container)
// -------------------------------------------------------------
let gridEl = null;
let headerEl = null;
function buildChrome(root) {
  const panel = root.querySelector(".start-menu-panel");
  const header = panel.querySelector(".start-menu-header");
  headerEl = header;
  header.innerHTML = `
    <span>Anwendungen</span>
    <span class="sm-tools">
      <button class="sm-newfolder-btn" title="Neuen Ordner erstellen" style="display:none">+ Ordner</button>
      <button class="sm-done-btn" title="Bearbeiten beenden" style="display:none">Bearbeiten beenden</button>
    </span>`;
  header.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:8px";

  // "Bearbeiten beenden" beendet den GLOBALEN Layout-Bearbeiten-Modus (gleicher
  // Schalter wie Dashboard/Profil), damit alles synchron bleibt.
  header.querySelector(".sm-done-btn").addEventListener("click", async () => {
    const { setDashEdit } = await import("./persist.js");
    setDashEdit(false);
    try { window.dispatchEvent(new CustomEvent("dashedit-changed")); } catch {}
  });
  header.querySelector(".sm-newfolder-btn").addEventListener("click", async () => {
    const name = await uiPrompt("Neuen Ordner erstellen", {
      placeholder: "Neuer Ordner", okText: "Erstellen",
    });
    if (name == null) return;
    layout.items.push({ t: "folder", id: `f_${Math.random().toString(36).slice(2, 8)}`,
                        name: name.trim() || "Neuer Ordner", open: false, apps: [] });
    save(); render();
  });

  // Das alte statische Grid durch unseren dynamischen Container ersetzen,
  // die Original-Buttons bleiben als versteckter Katalog erhalten.
  const oldGrid = panel.querySelector(".start-menu-grid");
  oldGrid.style.display = "none";
  oldGrid.classList.add("sm-catalog-hidden");
  gridEl = document.createElement("div");
  gridEl.className = "start-menu-grid sm-live-grid";
  oldGrid.after(gridEl);
}

// Header-Buttons + Panel-Klasse gemäß aktuellem editMode setzen.
function applyEditChrome() {
  if (headerEl) {
    headerEl.querySelector(".sm-newfolder-btn").style.display = editMode ? "" : "none";
    headerEl.querySelector(".sm-done-btn").style.display = editMode ? "" : "none";
  }
  const panel = gridEl && gridEl.closest(".start-menu-panel");
  if (panel) panel.classList.toggle("sm-editing", editMode);
}

// -------------------------------------------------------------
// Rendern
// -------------------------------------------------------------
function appTile(id) {
  const c = CATALOG[id];
  if (!c) return null;
  const el = document.createElement("button");
  el.className = "sm-tile sm-app";
  el.dataset.app = id;
  el.draggable = editMode;
  el.innerHTML = `<span class="app-icon">${c.icon || ""}</span><span class="sm-label">${c.label}</span>`
    + (editMode ? `<span class="sm-remove" title="${t("u_aus_dem_menu_entfernen")}">✕</span>` : "");
  if (!isAllowed(id)) el.style.display = "none";
  return el;
}

function folderTile(folder) {
  const el = document.createElement("div");
  el.className = "sm-tile sm-folder";
  el.dataset.folder = folder.id;
  el.draggable = editMode;
  const count = (folder.apps || []).length;
  const preview = (folder.apps || []).slice(0, 4)
    .map((a) => `<span class="sm-fp">${CATALOG[a] ? CATALOG[a].icon : ""}</span>`).join("");
  el.innerHTML = `
    <span class="app-icon sm-folder-icon">${preview || "📁"}</span>
    <span class="sm-folder-name">${folder.name}</span>
    <span class="sm-folder-count">${count} App${count === 1 ? "" : "s"}</span>
    ${editMode ? `<span class="sm-remove" title=t("src_folder_del")>✕</span>` : ""}`;
  return el;
}

// Anzahl Spalten des Anordnungs-Rasters (frei anordenbar, Luecken erlaubt).
const GRID_COLS = 5;

// Jedem Item einen Slot (lineare Position) geben, falls noch keiner existiert.
function ensureSlots() {
  let next = 0;
  const used = new Set(layout.items.filter((i) => Number.isInteger(i.pos)).map((i) => i.pos));
  layout.items.forEach((it) => {
    if (!Number.isInteger(it.pos)) {
      while (used.has(next)) next++;
      it.pos = next; used.add(next); next++;
    }
  });
}

function render() {
  if (!gridEl) return;
  gridEl.innerHTML = "";
  ensureSlots();

  // Belegte Slots -> Item. Hoechster Slot bestimmt die Rasterhoehe.
  const bySlot = new Map();
  let maxPos = -1;
  layout.items.forEach((it, idx) => {
    bySlot.set(it.pos, { it, idx });
    if (it.pos > maxPos) maxPos = it.pos;
  });

  // Im Bearbeiten-Modus zusaetzlich eine ganze Leerreihe am Ende anbieten,
  // damit man Apps "nach unten" ablegen kann.
  let totalSlots = maxPos + 1;
  if (editMode) totalSlots = Math.ceil((maxPos + 1) / GRID_COLS) * GRID_COLS + GRID_COLS;
  totalSlots = Math.max(totalSlots, GRID_COLS);

  gridEl.style.gridTemplateColumns = `repeat(${GRID_COLS}, minmax(0, 1fr))`;

  for (let slot = 0; slot < totalSlots; slot++) {
    const entry = bySlot.get(slot);
    if (entry) {
      const { it, idx } = entry;
      let tile = it.t === "app" ? appTile(it.id) : folderTile(it);
      if (!tile) { gridEl.appendChild(emptyCell(slot)); continue; }
      tile.dataset.idx = idx;
      tile.dataset.slot = slot;
      wireTile(tile, it, idx);
      gridEl.appendChild(tile);
    } else {
      // Leerer Platz: im Bearbeiten-Modus sichtbar + Drop-Ziel, sonst
      // unsichtbarer Platzhalter (haelt das Raster).
      gridEl.appendChild(emptyCell(slot));
    }
  }

  // Aufgeklappter Ordner als volle Reihe darunter
  layout.items.forEach((it) => {
    if (it.t === "folder" && it.open) gridEl.appendChild(folderDrawer(it));
  });
}

// Leerer Rasterplatz: nimmt per Drop ein Item auf und weist ihm den Slot zu.
function emptyCell(slot) {
  const cell = document.createElement("div");
  cell.className = "sm-cell-empty" + (editMode ? " sm-cell-visible" : "");
  cell.dataset.slot = slot;
  if (editMode) {
    cell.addEventListener("dragover", (e) => {
      if (!dragPayload) return;
      e.preventDefault(); cell.classList.add("sm-drop-into");
    });
    cell.addEventListener("dragleave", () => cell.classList.remove("sm-drop-into"));
    cell.addEventListener("drop", (e) => {
      if (!dragPayload) return;
      e.preventDefault(); e.stopPropagation();
      cell.classList.remove("sm-drop-into");
      moveToSlot(dragPayload, slot);
    });
  }
  return cell;
}

function folderDrawer(folder) {
  const drawer = document.createElement("div");
  drawer.className = "sm-folder-drawer";
  drawer.dataset.folderDrawer = folder.id;
  const head = document.createElement("div");
  head.className = "sm-drawer-head";
  head.innerHTML = `<b>${folder.name}</b>
    <span class="sm-drawer-tools">
      ${editMode ? `<button class="sm-ren" title="Umbenennen">Umbenennen</button>` : ""}
      <button class="sm-close" title="${t("close")}">${t("close")}</button>
    </span>`;
  head.querySelector(".sm-close").addEventListener("click", () => {
    folder.open = false; save(); render();
  });
  const ren = head.querySelector(".sm-ren");
  if (ren) ren.addEventListener("click", () => renameFolder(folder));
  drawer.appendChild(head);

  const inner = document.createElement("div");
  inner.className = "sm-drawer-grid";
  inner.dataset.folderTarget = folder.id;
  (folder.apps || []).forEach((a) => {
    const tile = appTile(a);
    if (!tile) return;
    tile.dataset.inFolder = folder.id;
    wireAppInFolder(tile, folder, a);
    inner.appendChild(tile);
  });
  if (!(folder.apps || []).length) {
    const hint = document.createElement("div");
    hint.className = "sm-drawer-empty";
    hint.textContent = editMode ? "Apps hierher ziehen…" : "Leerer Ordner";
    inner.appendChild(hint);
  }
  // Drop-Ziel: App in diesen Ordner ziehen
  makeDropTarget(inner, (payload) => moveIntoFolder(payload, folder));
  drawer.appendChild(inner);
  return drawer;
}

async function renameFolder(folder) {
  const name = await uiPrompt("Ordner umbenennen", {
    value: folder.name, placeholder: "Ordnername", okText: "Umbenennen",
  });
  if (name == null) return;
  folder.name = name.trim() || folder.name; save(); render();
}

// -------------------------------------------------------------
// Interaktion (Klick + Drag & Drop)
// -------------------------------------------------------------
// Rechtsklick auf eine App-Kachel = neue Instanz (wie Shift+Klick).
// Der Fenstermanager schneidet den Tastenzustand global mit; hier muss nur
// das Browser-Kontextmenue unterdrueckt und die Oeffnung ausgeloest werden.
// Im Bearbeiten-Modus passiert bewusst nichts - dort wird sortiert, nicht
// geoeffnet.
function wireRightOpen(tile, openFn) {
  tile.addEventListener("contextmenu", (e) => {
    if (e.target.closest(".sm-remove")) return;
    if (editMode) return;
    e.preventDefault();
    openFn();
  });
}

function wireTile(tile, item, idx) {
  if (item.t === "app") {
    tile.addEventListener("click", (e) => {
      if (e.target.closest(".sm-remove")) { removeApp(idx); return; }
      if (editMode) return;
      openApp(item.id);
    });
    wireRightOpen(tile, () => openApp(item.id));
  } else if (item.t === "folder") {
    // Ordner lassen sich AUCH im Bearbeiten-Modus oeffnen (dann kann man die
    // Apps darin umsortieren/herausziehen). Beim Oeffnen wird jeder andere
    // offene Ordner geschlossen -> immer nur EINER offen.
    tile.addEventListener("click", (e) => {
      if (e.target.closest(".sm-remove")) { deleteFolder(idx); return; }
      if (tile.classList.contains("sm-dragging")) return;
      const wasOpen = item.open;
      layout.items.forEach((x) => { if (x.t === "folder") x.open = false; });
      item.open = !wasOpen;
      save(); render();
    });
  }
  if (editMode) enableDrag(tile, { from: "grid", idx });
  enableGridDrop(tile, idx);
}

function wireAppInFolder(tile, folder, appId) {
  tile.addEventListener("click", (e) => {
    if (e.target.closest(".sm-remove")) { removeAppFromFolder(folder, appId); return; }
    if (editMode) return;
    openApp(appId);
  });
  wireRightOpen(tile, () => openApp(appId));
  if (editMode) enableDrag(tile, { from: "folder", folderId: folder.id, appId });
}

// ---- Drag-Quelle ----
let dragPayload = null;
function enableDrag(el, payload) {
  el.addEventListener("dragstart", (e) => {
    dragPayload = payload;
    el.classList.add("sm-dragging");
    try { e.dataTransfer.setData("text/plain", JSON.stringify(payload)); } catch {}
    e.dataTransfer.effectAllowed = "move";
  });
  el.addEventListener("dragend", () => {
    el.classList.remove("sm-dragging"); dragPayload = null;
    gridEl.querySelectorAll(".sm-drop-into")
      .forEach((n) => n.classList.remove("sm-drop-into"));
  });
}

// ---- Ziel: eine Grid-Kachel (auf leeren Slot ziehen, tauschen, in Ordner) ----
function enableGridDrop(tile, idx) {
  if (!editMode) return;
  tile.addEventListener("dragover", (e) => {
    if (!dragPayload) return;
    e.preventDefault();
    const item = layout.items[idx];
    tile.classList.remove("sm-drop-into");
    // Über einem Ordner -> "hinein"
    if (item && item.t === "folder" && !(dragPayload.from === "grid" && dragPayload.idx === idx)) {
      tile.classList.add("sm-drop-into");
    }
  });
  tile.addEventListener("dragleave", () => tile.classList.remove("sm-drop-into"));
  tile.addEventListener("drop", (e) => {
    if (!dragPayload) return;
    e.preventDefault(); e.stopPropagation();
    const item = layout.items[idx];
    const into = tile.classList.contains("sm-drop-into");
    tile.classList.remove("sm-drop-into");
    if (into && item && item.t === "folder") { moveIntoFolder(dragPayload, item); return; }
    // Auf eine belegte Kachel gezogen -> deren Slot uebernehmen (Tausch/Verdraengung)
    const targetSlot = Number(tile.dataset.slot);
    moveToSlot(dragPayload, targetSlot);
  });
}

// Verschiebt das gezogene Element auf einen Ziel-Slot. Ist der Slot belegt,
// tauschen die beiden ihre Plaetze (bei App aus Ordner: Ziel-App weicht nicht,
// die gezogene App nimmt den Slot, das dort liegende Item rueckt auf den frei
// gewordenen Quell-Slot bzw. ans Ende).
function moveToSlot(payload, targetSlot) {
  ensureSlots();
  const occupant = layout.items.find((i) => i.pos === targetSlot);

  if (payload.from === "grid") {
    const moving = layout.items[payload.idx];
    if (!moving) { render(); return; }
    const srcSlot = moving.pos;
    if (occupant && occupant !== moving) { occupant.pos = srcSlot; } // Tausch
    moving.pos = targetSlot;
    save(); render(); return;
  }

  // App kommt aus einem Ordner -> als eigene Kachel auf den Slot setzen
  const appId = extractApp(payload);
  if (!appId) { render(); return; }
  if (occupant) {
    // Ziel belegt: den Belegten ans Ende schieben, Slot freimachen
    let maxPos = -1; layout.items.forEach((i) => { if (i.pos > maxPos) maxPos = i.pos; });
    occupant.pos = maxPos + 1;
  }
  layout.items.push({ t: "app", id: appId, pos: targetSlot });
  save(); render();
}

// Allgemeines Drop-Ziel (Ordner-Inneres / leere Bereiche)
function makeDropTarget(el, handler) {
  el.addEventListener("dragover", (e) => { if (dragPayload) { e.preventDefault(); el.classList.add("sm-drop-into"); } });
  el.addEventListener("dragleave", () => el.classList.remove("sm-drop-into"));
  el.addEventListener("drop", (e) => {
    if (!dragPayload) return;
    e.preventDefault(); e.stopPropagation();
    el.classList.remove("sm-drop-into");
    handler(dragPayload);
  });
}

// -------------------------------------------------------------
// Layout-Mutationen
// -------------------------------------------------------------
function extractApp(payload) {
  // Entfernt die App aus ihrer Quelle und liefert ihre id zurück.
  if (payload.from === "grid") {
    const it = layout.items[payload.idx];
    if (it && it.t === "app") { layout.items.splice(payload.idx, 1); return it.id; }
    if (it && it.t === "folder") return null; // Ordner werden separat verschoben
  }
  if (payload.from === "folder") {
    const f = layout.items.find((x) => x.t === "folder" && x.id === payload.folderId);
    if (f) f.apps = (f.apps || []).filter((a) => a !== payload.appId);
    return payload.appId;
  }
  return null;
}

function reorderTo(payload, targetIdx) {
  if (payload.from === "grid") {
    // Ganze Kachel (App ODER Ordner) an neue Position schieben
    const srcIdx = payload.idx;
    if (srcIdx === targetIdx || srcIdx + 1 === targetIdx) { render(); return; }
    const [moved] = layout.items.splice(srcIdx, 1);
    const adj = srcIdx < targetIdx ? targetIdx - 1 : targetIdx;
    layout.items.splice(adj, 0, moved);
    save(); render(); return;
  }
  // App kommt aus einem Ordner -> als eigene Kachel ins Grid einsetzen
  const appId = extractApp(payload);
  if (!appId) { render(); return; }
  const clamped = Math.max(0, Math.min(targetIdx, layout.items.length));
  layout.items.splice(clamped, 0, { t: "app", id: appId });
  save(); render();
}

function moveIntoFolder(payload, folder) {
  if (payload.from === "folder" && payload.folderId === folder.id) { render(); return; }
  const appId = extractApp(payload);
  if (!appId) { render(); return; }
  folder.apps = folder.apps || [];
  if (!folder.apps.includes(appId)) folder.apps.push(appId);
  save(); render();
}

function removeApp(idx) {
  // App aus dem Menü nehmen -> ans Ende (neuer Slot). reconcile() haelt sie
  // im Katalog; komplett ausblenden wuerde eine Extra-Liste erfordern.
  const it = layout.items[idx];
  if (!it || it.t !== "app") return;
  delete it.pos;   // neuen (hinteren) Slot bekommen
  save(); render();
}

function removeAppFromFolder(folder, appId) {
  folder.apps = (folder.apps || []).filter((a) => a !== appId);
  // App zurück ins Grid als eigene Kachel (neuer freier Slot am Ende)
  layout.items.push({ t: "app", id: appId });
  save(); render();
}

async function deleteFolder(idx) {
  const it = layout.items[idx];
  if (!it || it.t !== "folder") return;
  const apps = it.apps || [];
  if (apps.length) {
    const ok = await uiConfirm(t("sm_del_folder_q", { name: it.name }), {
      description: t("sm_del_folder_desc", { n: apps.length }),
      okText: t("delete"), danger: true,
    });
    if (!ok) return;
  }
  layout.items.splice(idx, 1, ...apps.map((a) => ({ t: "app", id: a })));
  save(); render();
}

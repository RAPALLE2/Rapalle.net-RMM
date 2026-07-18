// dashlayout.js
// -------------
// Anpassbare Client-Detailansicht als FESTES RASTER (Grid). Standardraster ist
// 5 Spalten × 4 Reihen; es wächst automatisch nach unten. Jedes Panel belegt
// ganze Rasterzellen (gx,gy = Startzelle, gw,gh = Spanne). Beim Ziehen rastet
// ein Panel an der nächsten Zelle ein, beim Resizen an der nächsten Rasterlinie.
// Zieht man ein Panel auf einen belegten Bereich, werden die dort liegenden
// Panels nach unten gestapelt. Leere Zellen kann man per Klick belegen.
//
// Panel-Modell: { id, type, gx, gy, gw, gh, ...typ-spezifisch }
// Alles pro Benutzer gespeichert (persist.js).

import { t } from "./i18n.js";
import { esc, uiPrompt, uiConfirm } from "./utils.js";
import {
  renderStatusPart, renderActionsPart, renderWebsitesPart, clientHasWebsites,
  renderOverviewSub, OVERVIEW_SUBS,
} from "./panel.js";
import { openWindow } from "./windowmanager.js";
import { clientPresetsByGroup, clientPresetById, renderClientMetric, presetAvailable, availableClientKinds } from "./clientmetrics.js";
import {
  getDashLayout, setDashLayout, getDashEdit, setDashEdit, scheduleSave,
  getOrgDefaultDash,
  getDashProfiles, getClientDashProfile, setClientDashProfile, getClientDashProfileMap,
  getOrgProfilePresets,
} from "./persist.js";

// Zuletzt gerenderter Client (für die Layout-Auflösung in Handlern, die
// keinen Client-Parameter haben - z.B. placeNew/folderUnder).
let _activeClientId = null;
import { state, isAdmin } from "./state.js";
import { api } from "./api.js";
// GEMEINSAME Raster-Engine (identisch mit dem Dashboard/fleetdash.js).
import {
  COLS, BASE_ROWS, ROW_H, GAP, clamp, overlap, compact, neededRows,
  findFreeSpot, cellSize, applyGridPos, growHostIfNeeded,
} from "./gridengine.js";

let _uid = 0;
const nid = () => `p-${Date.now().toString(36)}-${(_uid++).toString(36)}`;

// Raster-Konstanten kommen aus gridengine.js (geteilt mit dem Dashboard).

const LEAF_LABEL = {
  status: () => t("status"),
  actions: () => t("actions"),
  websites: () => "🔗 Websites",
  metrics: () => (OVERVIEW_SUBS.metrics ? OVERVIEW_SUBS.metrics() : "Metrics"),
  notes: () => (OVERVIEW_SUBS.notes ? OVERVIEW_SUBS.notes() : "Notes"),
  disk: () => (OVERVIEW_SUBS.disk ? OVERVIEW_SUBS.disk() : "Disk"),
  text: () => "Text",
};

// Standard-Größe (in Rasterzellen) je Panel-Typ.
const DEFAULT_SIZE = {
  status: [1, 2], actions: [1, 2], websites: [1, 2],
  metrics: [3, 3], notes: [2, 2], disk: [2, 2],
  text: [1, 1], folder: [2, 3], cmetric: [2, 2],
};
function defaultSizeFor(panel) {
  if (panel.type === "cmetric") {
    const k = panel.kind;
    if (k === "number" || k === "gauge" || k === "donut") return [1, 2];
    return [2, 2];
  }
  return DEFAULT_SIZE[panel.type] || [1, 2];
}

const ADDABLE = [
  ["Übersicht", [
    { type: "metrics", label: "Metrics (CPU/RAM/Netz)" },
    { type: "notes", label: "Notizen" },
    { type: "disk", label: "Datenträger" },
  ]],
  ["Client", [
    { type: "status", label: "Status" },
    { type: "actions", label: "Aktionen" },
    { type: "websites", label: "Websites" },
  ]],
  ["Container", [
    { type: "folder", label: "Ordner (Tabs)" },
    { type: "text", label: "Text-Panel" },
  ]],
];

function orgOrBuiltinDefault() {
  // Admin-Standard bevorzugen (falls gesetzt), sonst eingebauter Standard.
  const org = getOrgDefaultDash();
  if (org && Array.isArray(org.panels) && org.panels.length) {
    const clone = JSON.parse(JSON.stringify(org));
    for (const p of clone.panels) p.id = nid();   // frische IDs pro Nutzer
    return clone;
  }
  return builtinDefaultLayout();
}
function builtinDefaultLayout() {
  // Allzweck-Standard: Status/Aktionen, Übersicht, die wichtigsten Live-Werte
  // und je ein Ordner für Leistung und System/Hardware.
  return {
    grid: true, cols: COLS,
    panels: [
      { id: nid(), type: "status", gx: 0, gy: 0, gw: 1, gh: 2 },
      { id: nid(), type: "actions", gx: 1, gy: 0, gw: 1, gh: 2 },
      { id: nid(), type: "folder", title: t("overview"), gx: 2, gy: 0, gw: 3, gh: 3,
        children: [
          { id: nid(), type: "metrics" },
          { id: nid(), type: "disk" },
          { id: nid(), type: "notes" },
        ], activeChild: null },
      { id: nid(), type: "cmetric", metric: "c.cpuLoad", gx: 0, gy: 2, gw: 1, gh: 1 },
      { id: nid(), type: "cmetric", metric: "c.ramPct", gx: 1, gy: 2, gw: 1, gh: 1 },
      { id: nid(), type: "folder", title: "Leistung", gx: 0, gy: 3, gw: 2, gh: 2,
        children: [
          { id: nid(), type: "cmetric", metric: "c.netBoth" },
          { id: nid(), type: "cmetric", metric: "c.cpuPerCore" },
          { id: nid(), type: "cmetric", metric: "c.diskIO" },
          { id: nid(), type: "cmetric", metric: "c.pingCf" },
        ], activeChild: null },
      { id: nid(), type: "folder", title: "System & Hardware", gx: 2, gy: 3, gw: 3, gh: 2,
        children: [
          { id: nid(), type: "cmetric", metric: "c.sysinfo" },
          { id: nid(), type: "cmetric", metric: "c.tempsAll" },
          { id: nid(), type: "cmetric", metric: "c.gpus" },
          { id: nid(), type: "cmetric", metric: "c.uptime" },
        ], activeChild: null },
      { id: nid(), type: "websites", gx: 0, gy: 5, gw: 2, gh: 2 },
    ],
  };
}

// Eingebaute Layouts der drei Profil-Presets - abgestimmt darauf, welche
// Metriken auf dem jeweiligen Gerätetyp überhaupt verfügbar sind (siehe
// clientmetrics.js: VMs/LXCs ohne GPU/Sensoren/Hardware usw.).
function builtinPresetLayout(name) {
  const P = (type, extra = {}) => ({ id: nid(), type, ...extra });
  const CM = (metric, extra = {}) => P("cmetric", { metric, ...extra });
  if (name === "Physisch") {
    return { grid: true, cols: COLS, panels: [
      P("status", { gx: 0, gy: 0, gw: 1, gh: 2 }),
      P("actions", { gx: 1, gy: 0, gw: 1, gh: 2 }),
      P("folder", { title: t("overview"), gx: 2, gy: 0, gw: 3, gh: 3, activeChild: null,
        children: [P("metrics"), P("disk"), P("notes")] }),
      CM("c.cpuLoad", { gx: 0, gy: 2, gw: 1, gh: 1 }),
      CM("c.cpuTemp", { gx: 1, gy: 2, gw: 1, gh: 1 }),
      P("folder", { title: "Sensoren & Strom", gx: 0, gy: 3, gw: 2, gh: 2, activeChild: null,
        children: [CM("c.tempsAll"), CM("c.fansAll"), CM("c.power"), CM("c.battery")] }),
      P("folder", { title: "Leistung", gx: 2, gy: 3, gw: 3, gh: 2, activeChild: null,
        children: [CM("c.cpuPerCore"), CM("c.netBoth"), CM("c.diskIO"), CM("c.pingCf")] }),
      P("folder", { title: "Hardware", gx: 0, gy: 5, gw: 2, gh: 2, activeChild: null,
        children: [CM("c.sysinfo"), CM("c.gpus"), CM("c.gpuModel"), CM("c.ramModules")] }),
      P("websites", { gx: 2, gy: 5, gw: 2, gh: 2 }),
    ] };
  }
  if (name === "VMs") {
    return { grid: true, cols: COLS, panels: [
      P("status", { gx: 0, gy: 0, gw: 1, gh: 2 }),
      P("actions", { gx: 1, gy: 0, gw: 1, gh: 2 }),
      P("folder", { title: t("overview"), gx: 2, gy: 0, gw: 3, gh: 3, activeChild: null,
        children: [P("metrics"), P("disk"), P("notes")] }),
      CM("c.cpuLoad", { gx: 0, gy: 2, gw: 1, gh: 1 }),
      CM("c.ramPct", { gx: 1, gy: 2, gw: 1, gh: 1 }),
      P("folder", { title: "Leistung", gx: 0, gy: 3, gw: 2, gh: 2, activeChild: null,
        children: [CM("c.cpuPerCore"), CM("c.netBoth"), CM("c.diskIO"), CM("c.load")] }),
      P("folder", { title: "System", gx: 2, gy: 3, gw: 3, gh: 2, activeChild: null,
        children: [CM("c.procs"), CM("c.swap"), CM("c.uptime"), CM("c.pingCf")] }),
      P("websites", { gx: 0, gy: 5, gw: 2, gh: 2 }),
    ] };
  }
  if (name === "LXCs") {
    return { grid: true, cols: COLS, panels: [
      P("status", { gx: 0, gy: 0, gw: 1, gh: 2 }),
      P("actions", { gx: 1, gy: 0, gw: 1, gh: 2 }),
      P("folder", { title: t("overview"), gx: 2, gy: 0, gw: 3, gh: 2, activeChild: null,
        children: [P("metrics"), P("notes")] }),
      CM("c.cpuLoad", { gx: 0, gy: 2, gw: 1, gh: 1 }),
      CM("c.ramPct", { gx: 1, gy: 2, gw: 1, gh: 1 }),
      P("folder", { title: "Leistung", gx: 2, gy: 2, gw: 3, gh: 2, activeChild: null,
        children: [CM("c.cpuPerCore"), CM("c.netBoth"), CM("c.load")] }),
      P("folder", { title: "System", gx: 0, gy: 3, gw: 2, gh: 2, activeChild: null,
        children: [CM("c.procs"), CM("c.swap"), CM("c.ramInfo"), CM("c.pingCf")] }),
      P("websites", { gx: 2, gy: 4, gw: 2, gh: 2 }),
    ] };
  }
  return null;
}

function migrateFolder(f) {
  if (!Array.isArray(f.children)) f.children = [];
  if (Array.isArray(f.subs) && f.subs.length) {
    for (const sub of f.subs) if (!f.children.some((c) => c.type === sub)) f.children.push({ id: nid(), type: sub });
    delete f.subs; delete f.activeSub;
  }
  for (const c of f.children) if (!c.id) c.id = nid();
  if (!f.activeChild && f.children.length) f.activeChild = f.children[0].id;
}

// Alte (Flow-)Layouts in Rasterkoordinaten überführen.
function migrateToGrid(layout) {
  if (layout.grid && layout.panels.every((p) => p.gx != null)) return;
  layout.grid = true; layout.cols = COLS;
  let x = 0, y = 0, rowH = 0;
  for (const p of layout.panels) {
    if (p.type === "folder") migrateFolder(p);
    if (p.gx != null && p.gw != null) continue;
    const [dw, dh] = defaultSizeFor(p);
    const gw = clamp(p.gw || Math.round(((p.w || 4) / 12) * COLS) || dw, 1, COLS);
    const gh = p.gh || dh;
    if (x + gw > COLS) { x = 0; y += rowH; rowH = 0; }
    p.gx = x; p.gy = y; p.gw = gw; p.gh = gh;
    delete p.w; delete p.h;
    x += gw; rowH = Math.max(rowH, gh);
  }
}

// Fest eingebaute Profil-Presets (org-weit vom Admin überschreibbar).
// Benutzer-Bearbeitungen werden erst beim EDITIEREN als lokale Kopie angelegt -
// bis dahin sehen alle die Admin-Vorgabe (bzw. den eingebauten Standard).
const PROFILE_PRESETS = ["Physisch", "VMs", "LXCs"];
const PRESET_ORG_KIND = { "Physisch": "dash_profile_physical", "VMs": "dash_profile_vm", "LXCs": "dash_profile_lxc" };
const _presetSession = {};   // Session-Cache der (nicht persistierten) Vorgabe-Layouts

function _freshCopy(layout) {
  const copy = JSON.parse(JSON.stringify(layout));
  for (const p of copy.panels || []) p.id = nid();
  return copy;
}

function presetBaseLayout(name) {
  // Vorgabe eines Presets: Admin-Version (falls gesetzt), sonst das
  // eingebaute Preset-Layout, sonst der allgemeine Standard.
  const org = getOrgProfilePresets()[name];
  if (org && Array.isArray(org.panels) && org.panels.length) return _freshCopy(org);
  const builtin = builtinPresetLayout(name);
  if (builtin) return builtin;
  return orgOrBuiltinDefault();
}

function resolveProfileLayout(name, materialize) {
  // 1) Lokale (vom Benutzer bearbeitete) Fassung gewinnt immer.
  const profiles = getDashProfiles();
  if (profiles[name]) return profiles[name];
  // 2) Preset ohne lokale Kopie: Vorgabe nutzen. materialize=true (Edit-Modus)
  //    legt die lokale Kopie an, damit Änderungen NUR diesen Benutzer betreffen.
  if (!PROFILE_PRESETS.includes(name)) return null;
  if (materialize) {
    profiles[name] = _presetSession[name] || presetBaseLayout(name);
    delete _presetSession[name];
    return profiles[name];
  }
  if (!_presetSession[name]) _presetSession[name] = presetBaseLayout(name);
  return _presetSession[name];
}

function currentLayout(client) {
  // Layout-Auflösung: Hat DIESER Client ein Profil zugewiesen, wird das
  // Profil-Layout genutzt (und dort auch hineineditiert). Sonst das normale
  // Standard-Layout des Benutzers (bzw. Org-/eingebauter Standard).
  const cid = (client && client.id) || _activeClientId;
  _activeClientId = cid || _activeClientId;
  const profName = cid ? getClientDashProfile(cid) : null;
  const profLayout = profName ? resolveProfileLayout(profName, getDashEdit()) : null;
  const usingProfile = !!profLayout;
  let l = usingProfile ? profLayout : getDashLayout();
  if (!l || !Array.isArray(l.panels) || !l.panels.length) {
    l = orgOrBuiltinDefault();
    if (usingProfile) getDashProfiles()[profName] = l;
    else setDashLayout(l);
  }
  for (const p of l.panels) { if (!p.id) p.id = nid(); if (p.type === "folder") migrateFolder(p); }
  migrateToGrid(l);
  // Einmalige Anpassung: bestehende Status-Panels auf 2 Rasterreihen bringen
  // (danach kann der Nutzer sie wieder frei vergrößern).
  if (!l._statusFix2) {
    let changed = false;
    for (const p of l.panels) if (p.type === "status" && p.gh > 2) { p.gh = 2; changed = true; }
    l._statusFix2 = true;
    if (changed) compact(l.panels, null);
  }
  return l;
}

function saveLayout() { scheduleSave(state); }

function panelTitle(p) {
  if (p.type === "folder") return p.title || t("overview");
  if (p.type === "text") return p.title || "Text";
  if (p.type === "cmetric") { const pr = clientPresetById(p.metric); return p.title || (pr ? pr.label : "Metrik"); }
  return (LEAF_LABEL[p.type] || (() => p.type))();
}

// ---- Raster-Geometrie/Kollision ----
// Kollisionen auflösen über die gemeinsame Engine. Ordner bleiben zusätzlich
// fix, damit sie beim Draufziehen nicht "fliehen" (Client-Besonderheit).
function compactPanels(panels, active) {
  compact(panels, active, (p) => p.type === "folder");
}

// =================================================================
// Haupt-Renderer
// =================================================================
export function renderClientLayout(host, toolbarHost, client) {
  if (!host) return;
  _activeClientId = client?.id || null;
  const layout = currentLayout(client);
  const edit = getDashEdit();

  if (toolbarHost) {
    // ---- Profil-Auswahl (immer sichtbar): Presets (Physisch/VMs/LXCs) plus
    // eigene Profile. "(Standard)" = das normale Layout des Benutzers.
    const profiles = getDashProfiles();
    const customNames = Object.keys(profiles).filter((n) => !PROFILE_PRESETS.includes(n)).sort((a, b) => a.localeCompare(b));
    const activeProf = getClientDashProfile(client.id) || "";
    const isPreset = PROFILE_PRESETS.includes(activeProf);
    const profSelect = `<select data-prof-select title="Layout-Profil für diesen Client" style="max-width:150px;padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
        <option value="">(Standard)</option>
        ${PROFILE_PRESETS.map((n) => `<option value="${esc(n)}" ${n === activeProf ? "selected" : ""}>${esc(n)}${profiles[n] ? " (angepasst)" : ""}</option>`).join("")}
        ${customNames.map((n) => `<option value="${esc(n)}" ${n === activeProf ? "selected" : ""}>${esc(n)}</option>`).join("")}
      </select>`;

    toolbarHost.innerHTML = edit ? `<span class="dash-edit-tools">
          ${profSelect}
          <button data-prof-new title="Neues Profil (Kopie des aktuellen Layouts) anlegen">＋ Profil</button>
          ${activeProf && !isPreset ? `<button data-prof-rename title="Aktives Profil umbenennen">✏️</button>` : ""}
          ${activeProf ? `<button data-prof-delete title="${isPreset ? "Eigene Anpassungen dieses Presets verwerfen (zurück zur Vorgabe)" : "Aktives Profil löschen"}">🗑</button>` : ""}
          ${activeProf && isPreset && isAdmin() ? `<button data-prof-org title="Dieses Preset mit dem aktuellen Layout für ALLE Nutzer überschreiben">💾 Profil für alle</button>` : ""}
          <button data-add-panel>+ Panel</button>
          <button data-reset title="Auf Standard zurücksetzen">↺ Standard</button>
          ${isAdmin() ? `<button data-set-default title="Aktuelles Layout als Standard für ALLE Nutzer speichern">💾 Als Standard für alle</button>` : ""}
          <button data-end-edit class="btn-primary" style="width:auto;margin:0" title="Bearbeiten-Modus verlassen">✓ Bearbeiten beenden</button>
        </span>` : `<span class="dash-edit-tools" style="opacity:0.9">${profSelect}</span>`;

    // Profil für diesen Client wechseln
    toolbarHost.querySelector("[data-prof-select]")?.addEventListener("change", (e) => {
      setClientDashProfile(client.id, e.target.value || null);
      saveLayout();
      renderClientLayout(host, toolbarHost, client);
    });
    // Neues Profil = Kopie des aktuell angezeigten Layouts, direkt zugewiesen
    toolbarHost.querySelector("[data-prof-new]")?.addEventListener("click", async () => {
      const name = (await uiPrompt("Neues Layout-Profil", {
        description: "Das aktuelle Layout wird als Profil kopiert und diesem Client zugewiesen. Beispiele: Physisch, VMs, LXCs.",
        placeholder: "Profilname" }))?.trim();
      if (!name) return;
      if (getDashProfiles()[name] && !(await uiConfirm(`Profil "${name}" überschreiben?`, { okText: "Überschreiben", danger: true }))) return;
      const copy = JSON.parse(JSON.stringify(layout));
      for (const p of copy.panels) p.id = nid();
      getDashProfiles()[name] = copy;
      setClientDashProfile(client.id, name);
      saveLayout();
      window.notify?.(`Profil "${name}" angelegt und diesem Client zugewiesen.`, "success");
      renderClientLayout(host, toolbarHost, client);
    });
    // Aktives Profil umbenennen
    toolbarHost.querySelector("[data-prof-rename]")?.addEventListener("click", async () => {
      const oldName = getClientDashProfile(client.id);
      if (!oldName) return;
      const name = (await uiPrompt("Profil umbenennen", { value: oldName }))?.trim();
      if (!name || name === oldName) return;
      const profs = getDashProfiles();
      if (profs[name] && !(await uiConfirm(`Profil "${name}" überschreiben?`, { okText: "Überschreiben", danger: true }))) return;
      profs[name] = profs[oldName]; delete profs[oldName];
      // Alle Client-Zuordnungen auf den neuen Namen umziehen
      const map = getClientDashProfileMap();
      for (const cid of Object.keys(map)) if (map[cid] === oldName) map[cid] = name;
      saveLayout(); renderClientLayout(host, toolbarHost, client);
    });
    // Aktives Profil löschen. Bei PRESETS werden nur die eigenen Anpassungen
    // verworfen - das Preset selbst bleibt (Vorgabe greift wieder).
    toolbarHost.querySelector("[data-prof-delete]")?.addEventListener("click", async () => {
      const name = getClientDashProfile(client.id);
      if (!name) return;
      const preset = PROFILE_PRESETS.includes(name);
      if (!(await uiConfirm(preset ? `Eigene Anpassungen von "${name}" verwerfen?` : `Profil "${name}" löschen?`, {
        description: preset
          ? "Das Preset zeigt danach wieder die Vorgabe (Admin-Version bzw. Standard)."
          : "Alle Clients mit diesem Profil nutzen danach wieder das Standard-Layout.",
        okText: preset ? "Verwerfen" : "Löschen", danger: true }))) return;
      delete getDashProfiles()[name];
      delete _presetSession[name];
      if (!preset) {
        const map = getClientDashProfileMap();
        for (const cid of Object.keys(map)) if (map[cid] === name) delete map[cid];
      }
      saveLayout(); renderClientLayout(host, toolbarHost, client);
    });
    // Admin: aktives Preset mit dem aktuellen Layout für ALLE Nutzer überschreiben
    toolbarHost.querySelector("[data-prof-org]")?.addEventListener("click", async () => {
      const name = getClientDashProfile(client.id);
      const kind = PRESET_ORG_KIND[name];
      if (!kind) return;
      if (!(await uiConfirm(`Preset "${name}" für ALLE Nutzer überschreiben?`, {
        description: "Nutzer ohne eigene Anpassungen dieses Presets sehen ab dem nächsten Laden das neue Layout. Eigene Anpassungen anderer Nutzer bleiben unangetastet.",
        okText: "Für alle speichern" }))) return;
      try {
        await api.setDefaultLayout(kind, layout);
        getOrgProfilePresets()[name] = JSON.parse(JSON.stringify(layout));
        window.notify?.(`Preset "${name}" für alle Nutzer gespeichert.`, "success");
      } catch (e) { window.notify?.("Speichern fehlgeschlagen: " + e.message, "error"); }
    });

    toolbarHost.querySelector("[data-add-panel]")?.addEventListener("click", () => openAddPicker(host, toolbarHost, client, null));
    toolbarHost.querySelector("[data-reset]")?.addEventListener("click", async () => {
      const prof = getClientDashProfile(client.id);
      if (!(await uiConfirm(prof ? `Profil "${prof}" auf Standard zurücksetzen?` : "Layout auf Standard zurücksetzen?", { okText: "Zurücksetzen", danger: true }))) return;
      if (prof) getDashProfiles()[prof] = PROFILE_PRESETS.includes(prof) ? presetBaseLayout(prof) : orgOrBuiltinDefault();
      else setDashLayout(orgOrBuiltinDefault());
      saveLayout(); renderClientLayout(host, toolbarHost, client);
    });
    toolbarHost.querySelector("[data-end-edit]")?.addEventListener("click", () => {
      setDashEdit(false);
      scheduleSave(state);
      // app.js hört auf dieses Event und rendert die Ansicht neu (kein direkter
      // panel.js-Import hier -> vermeidet einen Import-Zyklus).
      try { window.dispatchEvent(new CustomEvent("dashedit-changed")); } catch {}
    });
    toolbarHost.querySelector("[data-set-default]")?.addEventListener("click", async () => {
      if (!(await uiConfirm("Aktuelles Client-Panel-Layout als Standard für ALLE Nutzer setzen?", {
        description: "Neue Nutzer und alle, die auf \"Standard\" zurücksetzen, bekommen dieses Layout. Bestehende eigene Layouts bleiben unangetastet.",
        okText: "Als Standard speichern" }))) return;
      try {
        await api.setDefaultLayout("dash", currentLayout());
        window.notify?.("Als organisationsweiter Standard gespeichert.", "success");
      } catch (e) { window.notify?.("Speichern fehlgeschlagen: " + e.message, "error"); }
    });
  }

  const rows = neededRows(layout.panels);
  host.className = "dash-grid-layout" + (edit ? " editing" : "");
  // Für gezielte Live-Updates (updateClientLayouts): Host markieren und den
  // Render-Kontext merken - so können bei neuen Agent-Metriken NUR die
  // Werte/SVGs überschrieben werden, statt das ganze Panel neu einzufügen.
  host.dataset.clientId = client.id;
  // minmax(0, 1fr) statt 1fr: "1fr" ist intern minmax(auto, 1fr), d.h. breiter
  // Inhalt konnte seine Spalte aufdehnen und das Raster über den Bildschirm
  // hinausschieben. Mit minmax(0, 1fr) sind IMMER alle 5 Spalten sichtbar und
  // wachsen/schrumpfen exakt mit der Bildschirmbreite mit (wie im Dashboard).
  host.style.gridTemplateColumns = `repeat(${COLS}, minmax(0, 1fr))`;
  host.style.gridAutoRows = `${ROW_H}px`;
  host.style.gap = `${GAP}px`;
  host.style.minHeight = `${rows * ROW_H + (rows - 1) * GAP}px`;
  host.innerHTML = "";

  const ctx = { host, toolbarHost, edit, layout, client };
  host._rmmCtx = ctx;

  // Leere Zellen als belegbares Raster (nur im Edit).
  if (edit) renderEmptyCells(host, layout, rows, ctx);

  layout.panels.forEach((panel) => host.appendChild(buildPanel(panel, client, ctx)));

  const wsPanel = layout.panels.find((p) => p.type === "websites");
  if (wsPanel) clientHasWebsites(client.id).then((has) => {
    const c = host.querySelector(`[data-panel="${wsPanel.id}"]`);
    if (c && !has && !edit) c.style.display = "none";
  });
}

// ------------------------------------------------------------------
// Gezieltes Live-Update: Wenn der Agent neue Metriken schickt, werden NUR
// die Werte/SVGs der betroffenen Panels überschrieben - das Client-Panel
// (Grid, Karten, Tabs, Buttons) bleibt im DOM stehen. Kein Flackern, keine
// verlorenen Hover-/Edit-Zustände, keine Scroll-Sprünge.
// Rückgabe: Anzahl der aktualisierten Layout-Hosts (0 = Ansicht nutzt
// dieses Layout nicht -> Aufrufer kann klassisch neu rendern).
// ------------------------------------------------------------------
export function updateClientLayouts(clientId) {
  let touched = 0;
  document.querySelectorAll(`.dash-grid-layout[data-client-id="${CSS.escape(String(clientId))}"]`).forEach((host) => {
    const ctx = host._rmmCtx;
    if (!ctx || !document.body.contains(host)) return;
    touched++;
    // WICHTIG: Client IMMER frisch aus dem State holen. Nach clients:changed
    // (refreshAll) wird state.clients durch NEUE Objekte ersetzt - die alte
    // ctx.client-Referenz bekam dann keine Metriken mehr und die Panels
    // wirkten "eingefroren". ctx.client dient nur noch als Fallback.
    ctx.client = (state.clients || []).find((c) => c.id === ctx.client?.id) || ctx.client;
    // Panel-Typen mit Live-Metriken. notes/text/actions/websites werden NIE
    // neu gerendert (Notizen-Textarea würde sonst beim Tippen zerstört, wenn
    // der Agent neue Metriken schickt).
    const LIVE_TYPES = new Set(["cmetric", "status", "metrics", "disk"]);
    // Enthält ein Bereich gerade den Eingabe-Fokus (Textarea/Input), wird er
    // in diesem Tick übersprungen - nichts unter den Fingern wegrendern.
    const hasFocusIn = (el) => el && el.contains(document.activeElement) &&
      /^(TEXTAREA|INPUT|SELECT)$/.test(document.activeElement.tagName);
    host.querySelectorAll(":scope > .dash-lp").forEach((card) => {
      const panel = ctx.layout.panels.find((p) => String(p.id) === String(card.dataset.panel));
      if (!panel) return;
      const body = card.querySelector(":scope > .dash-lp-body");
      // Fokus ODER Hover: nichts unter Fingern/Cursor ersetzen (Hover-Flackern).
      if (!body || hasFocusIn(body) || body.matches(":hover")) return;
      if (panel.type === "cmetric") {
        // Nur den Diagramm-Inhalt ersetzen (Kind-Umschalter bleibt stehen).
        const holder = body.querySelector(".cmetric-holder");
        if (holder) renderClientMetric(holder, ctx.client, panel);
      } else if (panel.type === "folder") {
        // Nur den Inhalt des aktiven Tabs ersetzen (Tab-Leiste bleibt stehen)
        // - und NUR wenn der aktive Tab überhaupt Live-Daten zeigt.
        const child = panel.children.find((c) => c.id === panel.activeChild) || panel.children[0];
        if (!child || !LIVE_TYPES.has(child.type)) return;
        const activeBody = body.querySelector(":scope > .folder-active");
        if (activeBody && !hasFocusIn(activeBody)) renderActiveChild(activeBody, panel, ctx.client, ctx);
      } else if (LIVE_TYPES.has(panel.type)) {
        // Status/Metrics/Disk: nur den Body dieser einen Karte neu füllen.
        fillPanelBody(body, panel, ctx.client, ctx);
      }
    });
  });
  return touched;
}

// Belegbare Leerzellen rendern (Klick öffnet den Picker an dieser Stelle).
function renderEmptyCells(host, layout, rows, ctx) {
  const occ = Array.from({ length: rows }, () => Array(COLS).fill(false));
  for (const p of layout.panels)
    for (let y = p.gy; y < p.gy + p.gh; y++)
      for (let x = p.gx; x < p.gx + p.gw; x++)
        if (occ[y]) occ[y][x] = true;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < COLS; x++) {
      if (occ[y][x]) continue;
      const cell = document.createElement("button");
      cell.className = "grid-empty-cell";
      cell.style.gridColumn = `${x + 1} / span 1`;
      cell.style.gridRow = `${y + 1} / span 1`;
      cell.title = "Hier ein Panel einfügen";
      cell.innerHTML = "<span>+</span>";
      cell.addEventListener("click", () => openAddPicker(host, ctx.toolbarHost, ctx.client, { gx: x, gy: y }));
      host.appendChild(cell);
    }
  }
}

// =================================================================
// Panel-Karte
// =================================================================
function buildPanel(panel, client, ctx) {
  const { host, toolbarHost, edit, layout } = ctx;
  const card = document.createElement("div");
  card.className = "panel dash-lp";
  card.dataset.panel = panel.id;
  applyGridPos(card, panel);

  const head = document.createElement("div");
  head.className = "dash-lp-head";
  const titleEl = document.createElement("span");
  titleEl.className = "dash-lp-title";
  titleEl.textContent = panelTitle(panel);
  head.appendChild(titleEl);

  const tools = document.createElement("span");
  tools.className = "dash-lp-tools";
  const popBtn = document.createElement("button");
  popBtn.className = "dash-lp-btn"; popBtn.title = "Als eigenes Fenster herauslösen"; popBtn.textContent = "↗️";
  popBtn.addEventListener("click", (e) => { e.stopPropagation(); detachPanel(panel, client); });
  tools.appendChild(popBtn);
  if (edit) {
    {
      // Eigener Name für JEDES Client-Widget (nicht nur Ordner/Text) - wie
      // bei den Flotten-Widgets im Dashboard. Leer = Standard-Titel.
      const ren = document.createElement("button");
      ren.className = "dash-lp-btn"; ren.title = "Eigenen Namen geben"; ren.textContent = "✏️";
      ren.addEventListener("click", async (e) => {
        e.stopPropagation();
        const name = await uiPrompt("Widget-Namen ändern", {
          description: "Leer lassen = Standard-Titel.", value: panel.title || "" });
        if (name !== null) { panel.title = name.trim() || null; saveLayout(); titleEl.textContent = panelTitle(panel); }
      });
      tools.appendChild(ren);
    }
    const del = document.createElement("button");
    del.className = "dash-lp-btn"; del.title = "Entfernen"; del.textContent = "✕";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      layout.panels = layout.panels.filter((p) => p.id !== panel.id);
      saveLayout(); renderClientLayout(host, toolbarHost, client);
    });
    tools.appendChild(del);
  }
  head.appendChild(tools);
  card.appendChild(head);

  const bodyEl = document.createElement("div");
  bodyEl.className = "dash-lp-body";
  card.appendChild(bodyEl);
  fillPanelBody(bodyEl, panel, client, ctx);

  attachGridDrag(card, head, panel, client, ctx);
  if (edit) attachGridResize(card, panel, client, ctx);
  return card;
}

// Alle Karten (außer der aktiv gezogenen) neu positionieren.
function applyAllPositions(host, layout, exceptId) {
  for (const p of layout.panels) {
    if (p.id === exceptId) continue;
    const c = host.querySelector(`.dash-lp[data-panel="${p.id}"]`);
    if (c) applyGridPos(c, p);
  }
}

function fillPanelBody(bodyEl, panel, client, ctx) {
  bodyEl.innerHTML = ""; bodyEl.className = "dash-lp-body";
  if (panel.type === "status") return renderStatusPart(bodyEl, client);
  if (panel.type === "actions") { bodyEl.classList.add("actions-panel"); return renderActionsPart(bodyEl, client); }
  if (panel.type === "websites") { bodyEl.classList.add("actions-panel"); return renderWebsitesPart(bodyEl, client); }
  if (panel.type === "metrics" || panel.type === "notes" || panel.type === "disk") {
    bodyEl.classList.add("overview-content");
    return renderOverviewSub(bodyEl, client, panel.type, () => fillPanelBody(bodyEl, panel, client, ctx));
  }
  if (panel.type === "text") return renderTextPanel(bodyEl, panel, client, ctx);
  if (panel.type === "cmetric") return renderCMetric(bodyEl, panel, client, ctx);
  if (panel.type === "folder") return renderFolder(bodyEl, panel, client, ctx);
}

// ---------------- TEXT ----------------
function renderTextPanel(bodyEl, panel, client, ctx) {
  const edit = !!(ctx && ctx.edit);
  const div = document.createElement("div");
  div.className = "widget-text";
  div.innerHTML = panel.text ? esc(panel.text).replace(/\n/g, "<br>")
    : `<span style="color:var(--subtext)">${edit ? "Doppelklick zum Bearbeiten." : "Leer."}</span>`;
  bodyEl.appendChild(div);
  if (edit) {
    div.addEventListener("dblclick", () => {
      const ta = document.createElement("textarea");
      ta.className = "widget-text-edit"; ta.value = panel.text || "";
      bodyEl.innerHTML = ""; bodyEl.appendChild(ta); ta.focus();
      const finish = () => { panel.text = ta.value; saveLayout(); fillPanelBody(bodyEl, panel, client, ctx); };
      ta.addEventListener("blur", finish);
      ta.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ta.blur(); }
        if (e.key === "Escape") { e.preventDefault(); fillPanelBody(bodyEl, panel, client, ctx); }
      });
    });
  }
}

// ---------------- cmetric ----------------
function renderCMetric(bodyEl, panel, client, ctx) {
  const edit = !!(ctx && ctx.edit);
  const preset = clientPresetById(panel.metric);
  bodyEl.className = "dash-lp-body cmetric-body";
  // WICHTIG: Body immer erst leeren. Vorher wurde beim Umschalten der
  // Diagrammart der neue Inhalt nur ANGEHÄNGT - das Widget "buggte rum",
  // bis der Agent das nächste Mal Metriken schickte und alles frisch
  // aufgebaut wurde. Jetzt schaltet die Ansicht sofort sauber um.
  bodyEl.innerHTML = "";
  if (preset && !presetAvailable(preset, client.device_type)) {
    const card = bodyEl.closest(".dash-lp");
    // WICHTIG: Liegt das Widget in einem ORDNER, ist die umgebende Karte der
    // Ordner selbst - der darf NICHT versteckt werden, sonst verschwindet der
    // ganze Ordner samt Tabs und man kann nichts mehr umstellen. Im Ordner
    // wird stattdessen der Hinweis angezeigt.
    const inFolder = !!bodyEl.closest(".folder-active");
    if (!edit && !inFolder) { if (card) card.style.display = "none"; return; }
    bodyEl.innerHTML = `<div class="cmetric-na">Auf ${client.device_type === "lxc" ? "LXC-Containern" : "VMs"} nicht verfügbar.</div>`;
    return;
  }
  const kindsAvail = preset ? availableClientKinds(preset) : [];
  if (edit && preset && kindsAvail.length > 1) {
    const bar = document.createElement("div"); bar.className = "cmetric-kinds";
    const KIND_ICON = { number: "🔢", gauge: "🎯", donut: "🍩", line: "📈", bars: "📊", info: "🏷️",
      area: "⛰️", spark: "〰️", progress: "▶️", ring: "⭕", stat: "🔺", columns: "📶" };
    bar.innerHTML = kindsAvail.map((k) =>
      `<button class="cmetric-kind ${((panel.kind || preset.charts[0]) === k) ? "active" : ""}" data-kind="${k}" title="${k}">${KIND_ICON[k] || k}</button>`).join("");
    bodyEl.appendChild(bar);
    bar.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation(); panel.kind = b.dataset.kind; saveLayout(); renderCMetric(bodyEl, panel, client, ctx);
    }));
  }
  const holder = document.createElement("div"); holder.className = "cmetric-holder";
  bodyEl.appendChild(holder);
  renderClientMetric(holder, client, panel);
}

// ---------------- ORDNER (Tabs) ----------------
function renderFolder(bodyEl, folder, client, ctx) {
  migrateFolder(folder);
  const edit = !!(ctx && ctx.edit);
  bodyEl.classList.add("folder-panel");
  const tabbar = document.createElement("div"); tabbar.className = "tab-bar dash-folder-tabs";
  const activeBody = document.createElement("div"); activeBody.className = "folder-active overview-content";
  if (!folder.children.length) {
    tabbar.innerHTML = `<span style="color:var(--subtext);font-size:12px">leerer Ordner</span>`;
    if (edit) activeBody.innerHTML = `<div class="folder-drop-hint">Panel per Drag hierher ziehen</div>`;
  }
  folder.children.forEach((child) => {
    if (!folder.activeChild) folder.activeChild = child.id;
    const tab = document.createElement("button");
    tab.className = "tab-btn dash-folder-tab" + (folder.activeChild === child.id ? " active" : "");
    tab.textContent = panelTitle(child); tab.dataset.child = child.id;
    attachTabInteraction(tab, folder, child, client, ctx);
    tabbar.appendChild(tab);
  });
  bodyEl.appendChild(tabbar); bodyEl.appendChild(activeBody);
  renderActiveChild(activeBody, folder, client, ctx);
}
function renderActiveChild(activeBody, folder, client, ctx) {
  activeBody.innerHTML = "";
  const child = folder.children.find((c) => c.id === folder.activeChild) || folder.children[0];
  if (!child) return;
  folder.activeChild = child.id;
  const inner = document.createElement("div"); activeBody.appendChild(inner);
  fillPanelBody(inner, child, client, ctx);
}

function attachTabInteraction(tab, folder, child, client, ctx) {
  const { host, toolbarHost, edit } = ctx;
  tab.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY;
    let dragging = false; const tabbar = tab.parentElement;
    function move(ev) {
      if (!dragging && Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 5) {
        if (!edit) return;
        dragging = true; document.body.classList.add("dash-dragging"); tab.classList.add("tab-dragging");
      }
      if (!dragging) return;
      const f = folderUnder(ev.clientX, ev.clientY, host, null, child);
      host.querySelectorAll(".dash-lp.folder-hover").forEach((c) => c.classList.remove("folder-hover"));
      if (f && f.dataset.panel !== folder.id) f.classList.add("folder-hover");
      else if (tabbar) {
        const sibs = [...tabbar.querySelectorAll(".dash-folder-tab")].filter((x) => x !== tab);
        let placed = false;
        for (const s of sibs) { const r = s.getBoundingClientRect(); if (ev.clientX < r.left + r.width / 2) { tabbar.insertBefore(tab, s); placed = true; break; } }
        if (!placed) tabbar.appendChild(tab);
      }
    }
    function up(ev) {
      document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
      document.body.classList.remove("dash-dragging");
      host.querySelectorAll(".dash-lp.folder-hover").forEach((c) => c.classList.remove("folder-hover"));
      tab.classList.remove("tab-dragging");
      if (!dragging) { folder.activeChild = child.id; saveLayout(); renderClientLayout(host, toolbarHost, client); return; }
      const layout = (host._rmmCtx && host._rmmCtx.layout) || currentLayout(client);
      const targetCard = folderUnder(ev.clientX, ev.clientY, host, null, child);
      if (targetCard && targetCard.dataset.panel !== folder.id) {
        const tf = layout.panels.find((p) => p.id === targetCard.dataset.panel);
        if (tf) {
          folder.children = folder.children.filter((c) => c.id !== child.id);
          if (folder.activeChild === child.id) folder.activeChild = folder.children[0]?.id || null;
          (tf.children ||= []).push(child); tf.activeChild = child.id;
          saveLayout(); renderClientLayout(host, toolbarHost, client); return;
        }
      }
      if (targetCard && targetCard.dataset.panel === folder.id) {
        const order = [...tabbar.querySelectorAll(".dash-folder-tab")].map((x) => x.dataset.child);
        folder.children.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
        folder.activeChild = child.id; saveLayout(); renderClientLayout(host, toolbarHost, client); return;
      }
      // Aus dem Ordner herausziehen -> eigenes Panel an der Rasterzelle.
      folder.children = folder.children.filter((c) => c.id !== child.id);
      if (folder.activeChild === child.id) folder.activeChild = folder.children[0]?.id || null;
      const [dw, dh] = defaultSizeFor(child);
      child.gw = dw; child.gh = dh;
      const cell = cellFromPoint(host, ev.clientX, ev.clientY, dw);
      child.gx = cell.gx; child.gy = cell.gy;
      layout.panels.push(child);
      compactPanels(layout.panels, child);
      saveLayout(); renderClientLayout(host, toolbarHost, client);
    }
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  });
}

// =================================================================
// Picker
// =================================================================
function openAddPicker(host, toolbarHost, client, atCell) {
  _activeClientId = client?.id || _activeClientId;
  const back = document.createElement("div");
  back.className = "widget-picker-back";
  back.innerHTML = `
    <div class="widget-picker">
      <div class="wp-head"><strong>Panel hinzufügen${atCell ? ` (Zelle ${atCell.gx + 1},${atCell.gy + 1})` : ""}</strong><button class="dash-lp-btn" data-close>✕</button></div>
      <div class="wp-body">
        ${ADDABLE.map(([group, items]) => `
          <div class="wp-group-title">${esc(group)}</div>
          <div class="wp-grid">
            ${items.map((it) => `<button class="wp-item" data-type="${esc(it.type)}"><span class="wp-item-label">${esc(it.label)}</span></button>`).join("")}
          </div>`).join("")}
        <div class="wp-group-title" style="margin-top:20px;color:var(--accent)">📡 Telemetrie-Metriken</div>
        ${clientPresetsByGroup(client.device_type).map(([group, presets]) => `
          <div class="wp-group-title">${esc(group)}</div>
          <div class="wp-grid">
            ${presets.map((pr) => `<button class="wp-item" data-metric="${esc(pr.id)}"><span class="wp-item-label">${esc(pr.label)}</span><span class="wp-item-kinds">${pr.charts.join(" · ")}</span></button>`).join("")}
          </div>`).join("")}
      </div>
    </div>`;
  document.body.appendChild(back);
  const close = () => back.remove();
  back.addEventListener("click", (e) => { if (e.target === back) close(); });
  back.querySelector("[data-close]").addEventListener("click", close);
  back.querySelectorAll(".wp-item").forEach((b) => b.addEventListener("click", async () => {
    // Picker SOFORT schließen: Beim Ordner öffnet sich danach ein Eingabe-
    // Dialog - der lag früher HINTER dem "Panel hinzufügen"-Fenster
    // (Picker z-index 8000 > Dialog 6000) und man konnte nichts eingeben.
    close();
    if (b.dataset.metric) addMetricPanel(b.dataset.metric, atCell);
    else await addPanelType(b.dataset.type, atCell);   // wartet ggf. auf den Ordnername-Dialog
    renderClientLayout(host, toolbarHost, client);
  }));
}

function placeNew(panel, atCell) {
  const layout = currentLayout();
  const [dw, dh] = defaultSizeFor(panel);
  panel.gw = clamp(dw, 1, COLS); panel.gh = dh;
  if (atCell) {
    panel.gx = clamp(atCell.gx, 0, COLS - panel.gw); panel.gy = Math.max(0, atCell.gy);
  } else {
    const spot = findFreeSpot(layout.panels, panel.gw, panel.gh);
    panel.gx = spot.gx; panel.gy = spot.gy;
  }
  layout.panels.push(panel);
  compactPanels(layout.panels, panel);   // vorhandene ggf. nach unten stapeln
  saveLayout();
}
async function addPanelType(type, atCell) {
  if (type === "folder") {
    const name = await uiPrompt("Neuen Ordner anlegen", { description: "Name des Ordners:", value: "Neuer Ordner" });
    if (name === null) return;
    placeNew({ id: nid(), type: "folder", title: name.trim() || "Ordner", children: [], activeChild: null }, atCell);
  } else if (type === "text") {
    placeNew({ id: nid(), type: "text", title: "Text", text: "" }, atCell);
  } else {
    placeNew({ id: nid(), type }, atCell);
  }
}
function addMetricPanel(metricId, atCell) {
  const preset = clientPresetById(metricId); if (!preset) return;
  placeNew({ id: nid(), type: "cmetric", metric: metricId, kind: preset.charts[0] }, atCell);
}

// =================================================================
// Herauslösen
// =================================================================
export function detachPanel(panel, client) {
  if (panel.type === "cmetric") {
    openWindow({ singleton: true, key: `panelpart-${client.id}-cmetric-${panel.id}`, appId: "panelpart",
      title: `${panelTitle(panel)} — ${client.hostname}`,
      props: { clientId: client.id, part: "cmetric", metric: panel.metric, kind: panel.kind, partTitle: panelTitle(panel) },
      clientColor: client.color, w: 420, h: 320 });
    return;
  }
  const isFolder = panel.type === "folder";
  // BUGFIX "immer cmetrics statt das eigentliche": Früher wurden für Ordner
  // nur die TYPEN der Kinder übergeben (children.map(c => c.type)). Selbst
  // hinzugefügte Metrik-Widgets (type "cmetric") verloren dadurch metric/kind
  // und wurden im herausgelösten Fenster generisch/falsch gerendert. Jetzt
  // werden die KOMPLETTEN Panel-Definitionen (inkl. metric, kind, title)
  // mitgegeben; "subs" bleibt für alte persistierte Fenster erhalten.
  const childClones = isFolder
    ? panel.children.map((c) => {
        const clone = JSON.parse(JSON.stringify(c));
        clone._label = panelTitle(c);
        return clone;
      })
    : null;
  openWindow({
    singleton: true,
    key: `panelpart-${client.id}-${panel.type}-${panel.id}`, appId: "panelpart",
    title: `${panelTitle(panel)} — ${client.hostname}`,
    props: { clientId: client.id, part: isFolder ? "folder" : panel.type,
      panels: childClones,
      activePanelId: isFolder ? (panel.activeChild || null) : null,
      subs: isFolder ? panel.children.map((c) => c.type) : null,
      activeSub: isFolder ? (panel.children.find((c) => c.id === panel.activeChild)?.type || null) : null,
      partTitle: panelTitle(panel) },
    clientColor: client.color, w: isFolder ? 720 : 380, h: isFolder ? 520 : 420,
  });
}

// =================================================================
// Raster-Drag (Verschieben mit Einrasten + Nach-unten-Stapeln)
// =================================================================
function cellFromPoint(host, cx, cy, gw = 1) {
  const { rect, cw } = cellSize(host);
  let gx = Math.floor((cx - rect.left) / (cw + GAP));
  let gy = Math.floor((cy - rect.top) / (ROW_H + GAP));
  gx = clamp(gx, 0, COLS - gw); gy = Math.max(0, gy);
  return { gx, gy };
}

function attachGridDrag(card, head, panel, client, ctx) {
  const { host, toolbarHost, edit, layout } = ctx;
  head.addEventListener("mousedown", (e) => {
    if (e.target.closest("button")) return;
    if (e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX, startY = e.clientY;
    const rect0 = card.getBoundingClientRect();
    const grabX = e.clientX - rect0.left, grabY = e.clientY - rect0.top;
    // Ausgangspositionen ALLER Panels merken, damit beim Wegbewegen wieder
    // alles an seinen Platz zurückspringt (nicht dauerhaft "runterfällt").
    const snap = layout.panels.map((p) => ({ p, gx: p.gx, gy: p.gy }));
    const restoreSnap = () => { for (const s of snap) { s.p.gx = s.gx; s.p.gy = s.gy; } };
    let mode = null, preview = null, overFolder = null;

    function begin() {
      mode = "drag";
      document.body.classList.add("dash-dragging");
      card.classList.add("dragging");
      card.style.position = "fixed"; card.style.margin = "0";
      // WICHTIG: left/top explizit auf 0 setzen! Ohne das bleibt eine
      // fixed-Karte an ihrer "statischen" Position (= alte Rasterzelle)
      // stehen und translate() addiert obendrauf -> je weiter unten/rechts
      // das Panel lag, desto größer der Versatz beim Ziehen.
      card.style.left = "0"; card.style.top = "0";
      card.style.width = `${rect0.width}px`; card.style.height = `${rect0.height}px`;
      card.style.zIndex = "9999"; card.style.pointerEvents = "none"; card.style.willChange = "transform";
      preview = document.createElement("div"); preview.className = "grid-drop-preview"; host.appendChild(preview);
      moveTo(e.clientX, e.clientY);
    }
    function moveTo(cx, cy) { card.style.transform = `translate(${cx - grabX}px, ${cy - grabY}px) scale(1.01)`; }

    function onMove(ev) {
      const dist = Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY);
      if (!mode && dist > 5) {
        const r = host.getBoundingClientRect();
        const outside = ev.clientX < r.left - 30 || ev.clientX > r.right + 30 || ev.clientY < r.top - 50 || ev.clientY > r.bottom + 120;
        if (edit && !outside) begin(); else { mode = "detach"; card.classList.add("detach-hint"); }
      }
      if (mode !== "drag") return;
      moveTo(ev.clientX, ev.clientY);
      restoreSnap();                             // immer von stabilen Positionen aus
      const cell = cellFromPoint(host, ev.clientX, ev.clientY, panel.gw);
      const cand = { gx: cell.gx, gy: cell.gy, gw: panel.gw, gh: panel.gh };
      // Überlappt die Zielzelle einen Ordner? -> in den Ordner ablegen, NICHT
      // umsortieren (der Ordner bleibt exakt stehen).
      const fp = layout.panels.find((p) => p !== panel && p.type === "folder" && overlap(cand, p));
      host.querySelectorAll(".dash-lp.folder-hover").forEach((c) => c.classList.remove("folder-hover"));
      if (fp) {
        overFolder = host.querySelector(`.dash-lp[data-panel="${fp.id}"]`);
        if (overFolder) overFolder.classList.add("folder-hover");
        if (preview) preview.style.display = "none";
        applyAllPositions(host, layout, panel.id);   // alles auf Snapshot lassen
      } else {
        overFolder = null;
        if (preview) preview.style.display = "";
        panel.gx = cell.gx; panel.gy = cell.gy;
        if (preview) { preview.style.gridColumn = `${panel.gx + 1} / span ${panel.gw}`; preview.style.gridRow = `${panel.gy + 1} / span ${panel.gh}`; }
        compactPanels(layout.panels, panel);
        applyAllPositions(host, layout, panel.id);
        growHostIfNeeded(host, layout.panels);
      }
    }
    function onUp(ev) {
      document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp);
      document.body.classList.remove("dash-dragging");
      if (preview) preview.remove();
      host.querySelectorAll(".dash-lp.folder-hover").forEach((c) => c.classList.remove("folder-hover"));
      if (mode === "drag") {
        for (const p of ["position", "margin", "width", "height", "left", "top", "zIndex", "pointerEvents", "willChange", "transform"]) card.style[p] = "";
        card.classList.remove("dragging");
        if (overFolder && overFolder.dataset.panel !== panel.id) {
          const folder = layout.panels.find((p) => p.id === overFolder.dataset.panel);
          if (folder) {
            layout.panels = layout.panels.filter((p) => p.id !== panel.id);
            (folder.children ||= []).push(panel); folder.activeChild = panel.id;
            saveLayout(); renderClientLayout(host, toolbarHost, client); return;
          }
        }
        compactPanels(layout.panels, panel);
        saveLayout(); renderClientLayout(host, toolbarHost, client);
      } else if (mode === "detach") {
        card.classList.remove("detach-hint"); detachPanel(panel, client);
      }
    }
    document.addEventListener("mousemove", onMove); document.addEventListener("mouseup", onUp);
  });
}


// =================================================================
// Raster-Resize (Breite/Höhe in ganzen Zellen, mit Einrasten)
// =================================================================
function attachGridResize(card, panel, client, ctx) {
  const { host, toolbarHost, layout } = ctx;
  const gx = document.createElement("div"); gx.className = "tile-grip tile-grip-x";
  const gy = document.createElement("div"); gy.className = "tile-grip tile-grip-y";
  const gc = document.createElement("div"); gc.className = "tile-grip tile-grip-xy";
  card.appendChild(gx); card.appendChild(gy); card.appendChild(gc);

  function startResize(axis) {
    return (e) => {
      e.preventDefault(); e.stopPropagation();
      document.body.classList.add("dash-dragging");
      const { cw } = cellSize(host);
      const rect0 = card.getBoundingClientRect();
      const startGW = panel.gw, startGH = panel.gh;
      const snap = layout.panels.map((p) => ({ p, gx: p.gx, gy: p.gy }));
      const restoreSnap = () => { for (const s of snap) { s.p.gx = s.gx; s.p.gy = s.gy; } };
      function move(ev) {
        if (axis !== "y") {
          const px = ev.clientX - rect0.left;
          panel.gw = clamp(Math.round(px / (cw + GAP)) || 1, 1, COLS - panel.gx);
        }
        if (axis !== "x") {
          const py = ev.clientY - rect0.top;
          panel.gh = clamp(Math.round(py / (ROW_H + GAP)) || 1, 1, 20);
        }
        restoreSnap();
        applyGridPos(card, panel);
        compactPanels(layout.panels, panel);
        applyAllPositions(host, layout, panel.id);
        growHostIfNeeded(host, layout.panels);
      }
      function up() {
        document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
        document.body.classList.remove("dash-dragging");
        if (panel.gw !== startGW || panel.gh !== startGH) { compactPanels(layout.panels, panel); saveLayout(); renderClientLayout(host, toolbarHost, client); }
      }
      document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
    };
  }
  gx.addEventListener("mousedown", startResize("x"));
  gy.addEventListener("mousedown", startResize("y"));
  gc.addEventListener("mousedown", startResize("xy"));
}

// =================================================================
// Helfer
// =================================================================
function folderUnder(x, y, host, exclude, draggedPanel) {
  if (draggedPanel && draggedPanel.type === "folder") return null;
  const cards = [...host.querySelectorAll(".dash-lp")].filter((c) => c !== exclude);
  const _lay = (host._rmmCtx && host._rmmCtx.layout) || currentLayout();
  for (const c of cards) {
    const p = _lay.panels.find((pp) => pp.id === c.dataset.panel);
    if (!p || p.type !== "folder") continue;
    const r = c.getBoundingClientRect();
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return c;
  }
  return null;
}

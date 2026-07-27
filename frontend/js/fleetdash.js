// fleetdash.js
// ------------
// Benutzerdefinierter, modularer Bereich des Flotten-Dashboards. Der Nutzer
// kann Widgets (Zahlen, Donuts, Gauges, Linien-Charts, Pies, Balken, Tabellen
// und freie Textblöcke) hinzufügen, per Drag umsortieren, in der Breite ziehen,
// als eigenes Fenster herauslösen und - bei Text - direkt bearbeiten.
//
// Alles pro Benutzer gespeichert (persist.js -> fleetWidgets). Der Edit-Modus
// ist derselbe globale Schalter wie bei der Client-Ansicht (Profil).
//
// RASTER: Der komplette Raster-Code (Host-Setup, Leerzellen, Drag mit
// Einrasten + Drop-Vorschau, Resize in ganzen Zellen mit Einrasten an den
// Rastergrenzen, Kollisionsauflösung) ist eine 1:1-Kopie aus der
// Client-Ansicht (dashlayout.js). Die Rasterzellen selbst sind immer exakt
// gleich groß (minmax(0,1fr)); Widgets können ganze Zellen überspannen.

import { state, isAdmin } from "./state.js";
import { esc, uiConfirm, uiPrompt } from "./utils.js";
import {
  getFleetWidgets, setFleetWidgets, getDashEdit, setDashEdit, scheduleSave,
  getOrgDefaultFleet,
} from "./persist.js";
import { presetsByGroup, presetById, groupLabel } from "./metriccatalog.js";
import { renderWidgetBody, widgetTitle, pushWidgetHistory, availableKinds } from "./dashwidgets.js";
import { openWindow } from "./windowmanager.js";
import { api } from "./api.js";
import { t } from "./i18n.js";
// GEMEINSAME Raster-Engine (identisch mit der Client-Ansicht/dashlayout.js).
import {
  COLS, BASE_ROWS, ROW_H, GAP, clamp, overlap, clampTile, compact, neededRows,
  findFreeSpot, cellSize, applyGridPos, growHostIfNeeded,
} from "./gridengine.js";

let _uid = 0;
const nid = () => `w-${Date.now().toString(36)}-${(_uid++).toString(36)}`;

// Raster-Konstanten kommen aus gridengine.js (geteilt mit dashlayout).

function defaults() {
  // Admin-Standard bevorzugen (falls gesetzt), sonst eingebauter Standard.
  const org = getOrgDefaultFleet();
  if (Array.isArray(org) && org.length) {
    const clone = JSON.parse(JSON.stringify(org));
    for (const wdg of clone) wdg.id = nid();   // frische IDs pro Nutzer
    return clone;
  }
  return builtinDefaults();
}
function builtinDefaults() {
  // Breiten in RASTER-SPALTEN (1..5), Höhen in px auf Höheneinheiten gerastet.
  // Die drei Flotten-Übersicht-Donuts passen jeweils in EINE Höheneinheit.
  return [
    { id: nid(), preset: "fleet.statusDonut", kind: "fleetdonut", gx: 0, gy: 0, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.osDonut", kind: "fleetdonut", gx: 1, gy: 0, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.versionDonut", kind: "fleetdonut", gx: 2, gy: 0, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.online", kind: "donut", gx: 3, gy: 0, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.cpuLoadAvg", kind: "gauge", gx: 4, gy: 0, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.ramLoadAvg", kind: "gauge", gx: 0, gy: 1, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.gpuLoadAvg", kind: "gauge", gx: 1, gy: 1, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.power", kind: "number", gx: 2, gy: 1, gw: 1, gh: 1 },
    { id: nid(), preset: "host.cpuLoad", kind: "bar", gx: 3, gy: 1, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.osDist", kind: "pie", gx: 0, gy: 2, gw: 1, gh: 1 },
  ];
}

// Einmalige Migration: die frühere FESTE "Flotten-Übersicht" (drei Donuts
// unten im Dashboard) wird als normale Widgets angehängt - danach sind sie
// frei verschiebbar, editierbar, löschbar und über "+ Widget" neu einfügbar.
const _OVERVIEW_MIGRATION_KEY = "rmm_fleet_overview_widgets_v1";

function widgets() {
  let w = getFleetWidgets();
  if (!Array.isArray(w)) { w = defaults(); setFleetWidgets(w); }
  let migrated = false;
  try { migrated = !!localStorage.getItem(_OVERVIEW_MIGRATION_KEY); } catch {}
  if (!migrated) {
    try { localStorage.setItem(_OVERVIEW_MIGRATION_KEY, "1"); } catch {}
    if (!w.some((x) => x.preset === "fleet.statusDonut")) {
      w.push(
        { id: nid(), preset: "fleet.statusDonut", kind: "fleetdonut", w: 4 },
        { id: nid(), preset: "fleet.osDonut", kind: "fleetdonut", w: 4 },
        { id: nid(), preset: "fleet.versionDonut", kind: "fleetdonut", w: 4 },
      );
      setFleetWidgets(w);
      save();
    }
  }
  // Einmalige Migration auf das Raster-Modell des Client-Panels
  // (gx/gy/gw/gh, 5 Spalten). Alte Pixel-/12-Spalten-Felder (w/h) werden
  // verworfen: Jedes Widget bekommt EINE Rasterzelle als Standardgröße und
  // wird lückenlos neu platziert. Danach ist das Dashboard-Raster exakt so
  // wie in der Client-Ansicht.
  let gridV2 = false;
  try { gridV2 = !!localStorage.getItem("rmm_fleet_grid_cells_v2"); } catch {}
  if (!gridV2) {
    try { localStorage.setItem("rmm_fleet_grid_cells_v2", "1"); } catch {}
    for (const x of w) {
      delete x.w; delete x.h;                     // Alt-Felder entfernen
      delete x.gx; delete x.gy;                   // Position wird neu vergeben
      x.gw = 1; x.gh = 1;                         // Standard: eine Rasterzelle
    }
    setFleetWidgets(w); save();
  }
  // Das frühere FREI einstellbare Garantie-Widget (eigenes Datum im Widget)
  // gibt es nicht mehr: Im Dashboard zählen die Garantien der CLIENTS.
  // Bereits angelegte Widgets werden deshalb einmalig auf die Garantie-
  // Übersicht umgestellt (Position bleibt erhalten).
  let convertedWarranty = false;
  for (const x of w) {
    if (x.kind === "warranty") {
      x.preset = "fleet.warrantyOverview";
      x.kind = "warrantylist";
      delete x.until; delete x.since; delete x.label;
      if ((x.gw || 1) < 2) x.gw = 2;
      if ((x.gh || 1) < 2) x.gh = 2;
      convertedWarranty = true;
    }
  }
  if (convertedWarranty) { setFleetWidgets(w); save(); }

  for (const x of w) {
    if (!x.id) x.id = nid();
  }
  return w;
}
function save() { scheduleSave(state); }

// ---- Raster-Geometrie/Kollision ----
// Kollisionen auflösen über die gemeinsame Engine - EXAKT wie in der
// Client-Ansicht (dashlayout.js), inkl. Ordner-Sonderfall (im Dashboard gibt
// es keine Ordner, das Prädikat greift dann schlicht nie).
function compactPanels(panels, active) {
  compact(panels, active, (p) => p.type === "folder");
}

// Von dashboard.js aufgerufen. host = Grid-Container, toolbar = Kopf-Aktionen.
export function renderFleetWidgets(host, toolbar) {
  if (!host) return;
  const list = widgets();
  const edit = getDashEdit();

  // Koordinaten (gx/gy/gw/gh) sicherstellen - identisch zum Client-Panel.
  ensureCoords(list);

  if (toolbar) {
    // Gleiche Werkzeuge/Wording wie die Client-Ansicht (dashlayout).
    toolbar.innerHTML = edit ? `<span class="dash-edit-tools">
          <button data-add-panel>+ Panel</button>
          <button data-reset title="${t("dl_reset")}">↺ Standard</button>
          ${isAdmin() ? `<button data-set-default title="${t("u_aktuelle_dashboard_widgets_als_sta")}">💾 Als Standard für alle</button>` : ""}
          <button data-end-edit class="btn-primary" style="width:auto;margin:0" title="Bearbeiten-Modus verlassen">✓ Bearbeiten beenden</button>
        </span>` : "";
    toolbar.querySelector("[data-add-panel]")?.addEventListener("click", () => openAddDialog(host, toolbar, null));
    toolbar.querySelector("[data-reset]")?.addEventListener("click", async () => {
      const ok = await uiConfirm(t("dl_reset_q"), { okText: t("dl_reset_ok"), danger: true });
      if (!ok) return;
      setFleetWidgets(defaults()); save(); renderFleetWidgets(host, toolbar);
    });
    toolbar.querySelector("[data-end-edit]")?.addEventListener("click", () => {
      setDashEdit(false);
      scheduleSave(state);
      try { window.dispatchEvent(new CustomEvent("dashedit-changed")); } catch {}
    });
    toolbar.querySelector("[data-set-default]")?.addEventListener("click", async () => {
      if (!(await uiConfirm(t("u_aktuelle_dashboard_widgets_als_sta_2"), {
        description: t("u_neue_nutzer_und_alle_die_auf_stand"),
        okText: "Als Standard speichern" }))) return;
      try {
        await api.setDefaultLayout("fleet", widgets());
        window.notify?.("Als organisationsweiter Standard gespeichert.", "success");
      } catch (e) { window.notify?.(t("u_speichern_fehlgeschlagen") + e.message, "error"); }
    });
  }

  // Host-Setup: 1:1 aus der Client-Ansicht (dashlayout.js) übernommen.
  const rows = neededRows(list);
  host.className = "dash-grid-layout" + (edit ? " editing" : "");
  // WICHTIG: minmax(0, 1fr) statt 1fr! "1fr" ist intern minmax(auto, 1fr),
  // d.h. breiter Widget-Inhalt (Kopfzeile mit Dropdowns/Buttons) konnte
  // seine Spalte aufdehnen -> belegte Zellen wurden breiter als leere.
  // Mit minmax(0, 1fr) sind ALLE Spalten zu jeder Zeit exakt gleich breit.
  host.style.gridTemplateColumns = `repeat(${COLS}, minmax(0, 1fr))`;
  host.style.gridAutoRows = `${ROW_H}px`;
  host.style.gap = `${GAP}px`;
  host.style.minHeight = `${rows * ROW_H + (rows - 1) * GAP}px`;
  host.innerHTML = "";

  const ctx = { host, toolbar, edit, list };
  host._rmmCtx = ctx;

  // Leere Zellen als belegbares Raster (nur im Edit) - wie beim Client.
  if (edit) renderEmptyCells(host, list, rows, ctx);

  list.forEach((wdg) => host.appendChild(buildWidget(wdg, ctx)));
}

// gx/gy/gw/gh aus Alt-Feldern (w/h) ableiten und lückenlos platzieren.
function ensureCoords(list) {
  let changed = false;
  for (const p of list) {
    // Standardgröße: EINE Rasterzelle; per Resize-Griff in ganzen Zellen
    // vergrößerbar (snappt an die Rastergrenzen, wie im Client-Panel).
    if (typeof p.gw !== "number") { p.gw = 1; changed = true; }
    if (typeof p.gh !== "number") { p.gh = 1; changed = true; }
  }
  // Alle ohne gültige Position lückenlos einfügen (Reihenfolge beibehalten).
  const placed = list.filter((p) => typeof p.gx === "number" && typeof p.gy === "number");
  const missing = list.filter((p) => typeof p.gx !== "number" || typeof p.gy !== "number");
  for (const p of missing) { placeAt(placed, p); placed.push(p); changed = true; }

  // Harte Rasterschranken erzwingen: keine Kachel darf breiter als das Raster
  // sein oder rechts herausragen. Danach Überlappungen auflösen (stapeln).
  for (const p of list) {
    const before = `${p.gx},${p.gy},${p.gw},${p.gh}`;
    clampTile(p);
    if (`${p.gx},${p.gy},${p.gw},${p.gh}` !== before) changed = true;
  }
  const snap = list.map((p) => `${p.gx},${p.gy}`).join("|");
  compactPanels(list, null);
  if (list.map((p) => `${p.gx},${p.gy}`).join("|") !== snap) changed = true;

  if (changed) save();
}

// Erste freie Position finden (gemeinsame Engine).
function placeAt(existing, p) {
  const spot = findFreeSpot(existing, p.gw, p.gh);
  p.gx = spot.gx; p.gy = spot.gy;
}

// Belegbare Leerzellen rendern (Klick öffnet den Picker an dieser Stelle).
// 1:1 aus der Client-Ansicht (dashlayout.js) kopiert.
function renderEmptyCells(host, list, rows, ctx) {
  const occ = Array.from({ length: rows }, () => Array(COLS).fill(false));
  for (const p of list)
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
      cell.title = t("dl_cell_hint");
      cell.innerHTML = "<span>+</span>";
      cell.addEventListener("click", () => openAddDialog(host, ctx.toolbar, { gx: x, gy: y }));
      host.appendChild(cell);
    }
  }
}

// Live-Refresh (bei neuen Metriken): Werte/Charts neu zeichnen, ohne die
// Interaktion zu stören - nur die Körper werden aktualisiert.
export function refreshFleetWidgets(host) {
  if (!host) return;
  for (const wdg of widgets()) {
    if (["line", "area", "spark", "stat"].includes(wdg.kind)) pushWidgetHistory(wdg);
    const card = host.querySelector(`[data-widget="${wdg.id}"] .dash-w-body`);
    // Karten unter dem Cursor überspringen: Neu-Rendern beim Hover erzeugte
    // das schnelle Groß/Klein-Flackern (Element wird ersetzt -> mouseleave/
    // mouseenter-Schleife). Die Live-Werte zeigt der Tooltip trotzdem aktuell.
    if (card && !card._editingText && !card.matches(":hover")) renderWidgetBody(card, wdg);
  }
}

function buildWidget(wdg, ctx) {
  const { host, toolbar, edit } = ctx;
  const card = document.createElement("div");
  card.className = "panel dash-w";
  card.dataset.widget = wdg.id;
  // Rasterposition über die gemeinsame Engine (identisch zum Client).
  applyGridPos(card, wdg);
  // Karte HART auf die Zellgröße klemmen: darf die Rasterzelle nie
  // vergrößern/verkleinern - Inhalt, der nicht passt, wird abgeschnitten.
  card.style.minWidth = "0";
  card.style.maxWidth = "100%";
  card.style.minHeight = "0";
  card.style.maxHeight = "100%";
  card.style.overflow = "hidden";

  // Kopf
  const head = document.createElement("div");
  head.className = "dash-w-head";
  head.innerHTML = `<span class="dash-w-title">${esc(widgetTitle(wdg))}</span>`;
  const tools = document.createElement("span");
  tools.className = "dash-w-tools";

  if (edit && wdg.kind !== "text" && wdg.kind !== "media-fav" && presetById(wdg.preset)) {
    // Chart-Typ umschalten
    const p = presetById(wdg.preset);
    const sel = document.createElement("select");
    sel.className = "dash-w-kind";
    sel.innerHTML = availableKinds(p).map((k) => `<option value="${k}" ${k === wdg.kind ? "selected" : ""}>${k}</option>`).join("");
    sel.addEventListener("change", () => { wdg.kind = sel.value; save(); renderWidgetBody(body, wdg); });
    tools.appendChild(sel);

    // Pro-Widget: alle Geräte, nur physische oder nur VMs & LXCs zählen
    // (ersetzt die frühere globale Profil-Einstellung). Wirkt nur auf
    // Flotten-Aggregate.
    const scopeSel = document.createElement("select");
    scopeSel.className = "dash-w-kind";
    scopeSel.title = t("u_welche_gerate_zahlt_dieses_widget");
    scopeSel.innerHTML = `
      <option value="all" ${(wdg.scope || "all") === "all" ? "selected" : ""}>alle Geräte</option>
      <option value="physical" ${wdg.scope === "physical" ? "selected" : ""}>nur physische</option>
      <option value="virtual" ${wdg.scope === "virtual" ? "selected" : ""}>nur VMs &amp; LXCs</option>`;
    scopeSel.addEventListener("change", () => { wdg.scope = scopeSel.value; save(); renderWidgetBody(body, wdg); });
    tools.appendChild(scopeSel);
  }


  const pop = document.createElement("button");
  pop.className = "dash-w-btn"; pop.title = t("u_als_fenster_herauslosen"); pop.textContent = "↗️";
  pop.addEventListener("click", (e) => { e.stopPropagation(); detachWidget(wdg); });
  tools.appendChild(pop);

  if (edit) {
    const ren = document.createElement("button");
    ren.className = "dash-w-btn"; ren.title = t("u_titel_andern"); ren.textContent = "✏️";
    ren.addEventListener("click", async (e) => {
      e.stopPropagation();
      const name = await uiPrompt(t("u_widget_titel_andern"), {
        description: "Leer lassen = Standard-Titel des Presets.", value: wdg.title || "" });
      if (name !== null) { wdg.title = name.trim() || null; save(); head.querySelector(".dash-w-title").textContent = widgetTitle(wdg); }
    });
    tools.appendChild(ren);
    const del = document.createElement("button");
    del.className = "dash-w-btn"; del.title = "Entfernen"; del.textContent = "✕";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      const arr = widgets().filter((x) => x.id !== wdg.id);
      setFleetWidgets(arr); save(); renderFleetWidgets(host, toolbar);
    });
    tools.appendChild(del);
  }
  head.appendChild(tools);
  card.appendChild(head);

  // Körper
  const body = document.createElement("div");
  body.className = "dash-w-body";
  body.style.minWidth = "0"; body.style.minHeight = "0";
  card.appendChild(body);
  renderWidgetBody(body, wdg);

  // Text-Widget im Edit-Modus direkt editierbar (Klick -> Textarea).
  if (wdg.kind === "text" && edit) {
    body.style.cursor = "text";
    body.addEventListener("dblclick", () => startTextEdit(body, wdg));
    // Hinweis für Erstnutzung
    body.title = "Doppelklick zum Bearbeiten";
  }

  attachGridDrag(card, head, wdg, ctx);
  if (edit) attachGridResize(card, wdg, ctx);
  return card;
}

// Alle Karten (außer der aktiv gezogenen) neu positionieren.
// 1:1 aus der Client-Ansicht (dashlayout.js) kopiert.
function applyAllPositions(host, list, exceptId) {
  for (const p of list) {
    if (p.id === exceptId) continue;
    const c = host.querySelector(`.dash-w[data-widget="${p.id}"]`);
    if (c) applyGridPos(c, p);
  }
}

function startTextEdit(body, wdg) {
  body._editingText = true;
  const ta = document.createElement("textarea");
  ta.className = "widget-text-edit";
  ta.value = wdg.text || "";
  body.innerHTML = "";
  body.appendChild(ta);
  ta.focus();
  const finish = () => {
    wdg.text = ta.value;
    body._editingText = false;
    save();
    renderWidgetBody(body, wdg);
  };
  ta.addEventListener("blur", finish);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ta.blur(); }
    if (e.key === "Escape") { e.preventDefault(); body._editingText = false; renderWidgetBody(body, wdg); }
  });
}

// -------------------- Widget herauslösen --------------------
export function detachWidget(wdg) {
  openWindow({
    singleton: true,
    key: `fleetwidget-${wdg.id}`, appId: "fleetwidget",
    title: widgetTitle(wdg),
    props: { widget: JSON.parse(JSON.stringify(wdg)) },
    w: wdg.kind === "table" || wdg.kind === "bar" ? 520 : 360,
    h: wdg.kind === "table" || wdg.kind === "bar" ? 480 : 320,
  });
}

// -------------------- Auswahl-Dialog --------------------
function openAddDialog(host, toolbar, atCell) {
  const back = document.createElement("div");
  back.className = "widget-picker-back";
  const groups = presetsByGroup();
  back.innerHTML = `
    <div class="widget-picker">
      <div class="wp-head">
        <strong>Widget hinzufügen</strong>
        <button class="dash-w-btn" data-close>✕</button>
      </div>
      <div class="wp-body">
        <div class="wp-group-title">Sonstiges</div>
        <div class="wp-grid">
          <button class="wp-item" data-text-widget="1">
            <span class="wp-item-label">Text / Notiz</span>
            <span class="wp-item-kinds">frei beschreibbar</span>
          </button>
          <button class="wp-item" data-media-widget="1">
            <span class="wp-item-label">Musik-Favoriten</span>
            <span class="wp-item-kinds">Sender · Titel · Listen</span>
          </button>
        </div>
        ${groups.map(([group, presets]) => `
          <div class="wp-group-title">${esc(groupLabel(group))}</div>
          <div class="wp-grid">
            ${presets.map((p) => `
              <button class="wp-item" data-preset="${esc(p.id)}" data-kind="${esc((p.charts || ["number"])[0])}">
                <span class="wp-item-label">${esc(p.label)}</span>
                <span class="wp-item-kinds">${(p.charts || []).join(" · ")}</span>
              </button>`).join("")}
          </div>`).join("")}
      </div>
    </div>`;
  document.body.appendChild(back);
  const close = () => back.remove();
  back.addEventListener("click", (e) => { if (e.target === back) close(); });
  back.querySelector("[data-close]").addEventListener("click", close);
  back.querySelectorAll(".wp-item").forEach((b) =>
    b.addEventListener("click", () => {
      const arr = widgets();
      const w = b.dataset.textWidget
        ? { id: nid(), kind: "text", text: t("u_neuer_text_im_bearbeiten_modus_ank"), gw: 1, gh: 1 }
        // Musik-Favoriten: Liste aus dem Audio-Player, braucht etwas Hoehe.
        : b.dataset.mediaWidget
        ? { id: nid(), kind: "media-fav", gw: 1, gh: 2 }
        : { id: nid(), preset: b.dataset.preset, kind: b.dataset.kind,
            // Listen-/Tabellen-artige Darstellungen brauchen von Anfang an
            // mehr Platz, sonst sieht man nur zwei Zeilen.
            gw: b.dataset.kind === "warrantylist" ? 2 : 1,
            gh: b.dataset.kind === "warrantylist" ? 2 : 1 };
      // Platzieren EXAKT wie im Client-Panel (placeNew in dashlayout.js):
      // an der angeklickten Rasterzelle einfügen (hart ins Raster geklemmt),
      // sonst erste freie Stelle; vorhandene ggf. nach unten stapeln.
      if (atCell) {
        w.gx = clamp(atCell.gx, 0, COLS - w.gw); w.gy = Math.max(0, atCell.gy);
      } else {
        const spot = findFreeSpot(arr, w.gw, w.gh);
        w.gx = spot.gx; w.gy = spot.gy;
      }
      arr.push(w);
      compactPanels(arr, w);
      setFleetWidgets(arr); save(); close(); renderFleetWidgets(host, toolbar);
    }));
}

// =================================================================
// Raster-Drag (Verschieben mit Einrasten + Nach-unten-Stapeln)
// 1:1 aus der Client-Ansicht (dashlayout.js) kopiert.
// =================================================================
function cellFromPoint(host, cx, cy, gw = 1) {
  const { rect, cw } = cellSize(host);
  let gx = Math.floor((cx - rect.left) / (cw + GAP));
  let gy = Math.floor((cy - rect.top) / (ROW_H + GAP));
  gx = clamp(gx, 0, COLS - gw); gy = Math.max(0, gy);
  return { gx, gy };
}

function attachGridDrag(card, head, panel, ctx) {
  const { host, toolbar, edit, list } = ctx;
  head.addEventListener("mousedown", (e) => {
    // (Selects im Widget-Kopf sind eine Dashboard-Besonderheit und dürfen
    // den Drag nicht starten - entspricht dem Button-Guard des Clients.)
    if (e.target.closest("button") || e.target.closest("select")) return;
    if (e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX, startY = e.clientY;
    const rect0 = card.getBoundingClientRect();
    const grabX = e.clientX - rect0.left, grabY = e.clientY - rect0.top;
    // Ausgangspositionen ALLER Panels merken, damit beim Wegbewegen wieder
    // alles an seinen Platz zurückspringt (nicht dauerhaft "runterfällt").
    const snap = list.map((p) => ({ p, gx: p.gx, gy: p.gy }));
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
      // das Widget lag, desto größer der Versatz beim Ziehen.
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
      const fp = list.find((p) => p !== panel && p.type === "folder" && overlap(cand, p));
      host.querySelectorAll(".dash-w.folder-hover").forEach((c) => c.classList.remove("folder-hover"));
      if (fp) {
        overFolder = host.querySelector(`.dash-w[data-widget="${fp.id}"]`);
        if (overFolder) overFolder.classList.add("folder-hover");
        if (preview) preview.style.display = "none";
        applyAllPositions(host, list, panel.id);   // alles auf Snapshot lassen
      } else {
        overFolder = null;
        if (preview) preview.style.display = "";
        panel.gx = cell.gx; panel.gy = cell.gy;
        if (preview) { preview.style.gridColumn = `${panel.gx + 1} / span ${panel.gw}`; preview.style.gridRow = `${panel.gy + 1} / span ${panel.gh}`; }
        compactPanels(list, panel);
        applyAllPositions(host, list, panel.id);
        growHostIfNeeded(host, list);
      }
    }
    function onUp(ev) {
      document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp);
      document.body.classList.remove("dash-dragging");
      if (preview) preview.remove();
      host.querySelectorAll(".dash-w.folder-hover").forEach((c) => c.classList.remove("folder-hover"));
      if (mode === "drag") {
        for (const p of ["position", "margin", "width", "height", "left", "top", "zIndex", "pointerEvents", "willChange", "transform"]) card.style[p] = "";
        card.classList.remove("dragging");
        compactPanels(list, panel);
        setFleetWidgets(list); save(); renderFleetWidgets(host, toolbar);
      } else if (mode === "detach") {
        card.classList.remove("detach-hint"); detachWidget(panel);
      }
    }
    document.addEventListener("mousemove", onMove); document.addEventListener("mouseup", onUp);
  });
}

// =================================================================
// Raster-Resize (Breite/Höhe in ganzen Zellen, mit Einrasten)
// 1:1 aus der Client-Ansicht (dashlayout.js) kopiert.
// =================================================================
function attachGridResize(card, panel, ctx) {
  const { host, toolbar, list } = ctx;
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
      const snap = list.map((p) => ({ p, gx: p.gx, gy: p.gy }));
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
        compactPanels(list, panel);
        applyAllPositions(host, list, panel.id);
        growHostIfNeeded(host, list);
      }
      function up() {
        document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
        document.body.classList.remove("dash-dragging");
        if (panel.gw !== startGW || panel.gh !== startGH) { compactPanels(list, panel); setFleetWidgets(list); save(); renderFleetWidgets(host, toolbar); }
      }
      document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
    };
  }
  gx.addEventListener("mousedown", startResize("x"));
  gy.addEventListener("mousedown", startResize("y"));
  gc.addEventListener("mousedown", startResize("xy"));
}

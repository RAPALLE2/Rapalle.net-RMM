// fleetdash.js
// ------------
// Benutzerdefinierter, modularer Bereich des Flotten-Dashboards. Der Nutzer
// kann Widgets (Zahlen, Donuts, Gauges, Linien-Charts, Pies, Balken, Tabellen
// und freie Textblöcke) hinzufügen, per Drag umsortieren, in der Breite ziehen,
// als eigenes Fenster herauslösen und - bei Text - direkt bearbeiten.
//
// Alles pro Benutzer gespeichert (persist.js -> fleetWidgets). Der Edit-Modus
// ist derselbe globale Schalter wie bei der Client-Ansicht (Profil).

import { state, isAdmin } from "./state.js";
import { esc, uiConfirm, uiPrompt } from "./utils.js";
import {
  getFleetWidgets, setFleetWidgets, getDashEdit, setDashEdit, scheduleSave,
  getOrgDefaultFleet,
} from "./persist.js";
import { presetsByGroup, presetById } from "./metriccatalog.js";
import { renderWidgetBody, widgetTitle, pushWidgetHistory, availableKinds } from "./dashwidgets.js";
import { openWindow } from "./windowmanager.js";
import { api } from "./api.js";

let _uid = 0;
const nid = () => `w-${Date.now().toString(36)}-${(_uid++).toString(36)}`;

// GLEICHES Raster wie die Client-Ansicht (dashlayout.js): 5 Spalten,
// Höheneinheit 150px, 14px Abstand. Höhen rasten auf Vielfache der
// Höheneinheit ein (150, 314, 478, ... = n*164 - 14).
const COLS = 5;
const ROW_H = 150;
const GAP = 14;
const snapH = (px) => Math.max(ROW_H, Math.round((px + GAP) / (ROW_H + GAP)) * (ROW_H + GAP) - GAP);

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
    { id: nid(), preset: "fleet.statusDonut", kind: "fleetdonut", gx: 0, gy: 0, gw: 2, gh: 1 },
    { id: nid(), preset: "fleet.osDonut", kind: "fleetdonut", gx: 2, gy: 0, gw: 2, gh: 1 },
    { id: nid(), preset: "fleet.versionDonut", kind: "fleetdonut", gx: 4, gy: 0, gw: 1, gh: 1 },
    { id: nid(), preset: "fleet.online", kind: "donut", gx: 0, gy: 1, gw: 2, gh: 2 },
    { id: nid(), preset: "fleet.cpuLoadAvg", kind: "gauge", gx: 2, gy: 1, gw: 2, gh: 2 },
    { id: nid(), preset: "fleet.power", kind: "number", gx: 4, gy: 1, gw: 1, gh: 1 },
    { id: nid(), preset: "host.cpuLoad", kind: "bar", gx: 0, gy: 3, gw: 3, gh: 2 },
    { id: nid(), preset: "fleet.osDist", kind: "pie", gx: 3, gy: 3, gw: 2, gh: 2 },
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
  // Einmalige Migration aufs Panel-Raster: alte 12-Spalten-Breiten -> 5
  // Spalten, Höhen auf Höheneinheiten einrasten (Flotten-Donuts = 1 Einheit).
  let grid5 = false;
  try { grid5 = !!localStorage.getItem("rmm_fleet_grid5_v1"); } catch {}
  if (!grid5) {
    try { localStorage.setItem("rmm_fleet_grid5_v1", "1"); } catch {}
    for (const x of w) {
      if (x.w > COLS) x.w = Math.max(1, Math.min(COLS, Math.round(x.w * COLS / 12)));
      if (x.kind === "fleetdonut") x.h = ROW_H;
      else if (x.h) x.h = snapH(x.h);
      else x.h = (x.kind === "number" || x.kind === "text" || x.kind === "spark" || x.kind === "stat" || x.kind === "progress" || x.kind === "ring") ? ROW_H : ROW_H * 2 + GAP;
    }
    setFleetWidgets(w); save();
  }
  for (const x of w) { if (!x.id) x.id = nid(); if (!x.w) x.w = 2; x.w = Math.max(1, Math.min(COLS, x.w)); }
  return w;
}
function save() { scheduleSave(state); }

// Von dashboard.js aufgerufen. host = Grid-Container, toolbar = Kopf-Aktionen.
export function renderFleetWidgets(host, toolbar) {
  const list = widgets();
  const edit = getDashEdit();

  // Koordinaten (gx/gy/gw/gh) sicherstellen - identisch zum Client-Panel.
  ensureCoords(list);

  if (toolbar) {
    // Gleiche Werkzeuge/Wording wie die Client-Ansicht (dashlayout).
    toolbar.innerHTML = edit ? `<span class="dash-edit-tools">
          <button data-add-panel>+ Panel</button>
          <button data-reset title="Auf Standard zurücksetzen">↺ Standard</button>
          ${isAdmin() ? `<button data-set-default title="Aktuelle Dashboard-Widgets als Standard für ALLE Nutzer speichern">💾 Als Standard für alle</button>` : ""}
          <button data-end-edit class="btn-primary" style="width:auto;margin:0" title="Bearbeiten-Modus verlassen">✓ Bearbeiten beenden</button>
        </span>` : "";
    toolbar.querySelector("[data-add-panel]")?.addEventListener("click", () => openAddDialog(host, toolbar, null));
    toolbar.querySelector("[data-reset]")?.addEventListener("click", async () => {
      const ok = await uiConfirm("Layout auf Standard zurücksetzen?", { okText: "Zurücksetzen", danger: true });
      if (!ok) return;
      setFleetWidgets(defaults()); save(); renderFleetWidgets(host, toolbar);
    });
    toolbar.querySelector("[data-end-edit]")?.addEventListener("click", () => {
      setDashEdit(false);
      scheduleSave(state);
      try { window.dispatchEvent(new CustomEvent("dashedit-changed")); } catch {}
    });
    toolbar.querySelector("[data-set-default]")?.addEventListener("click", async () => {
      if (!(await uiConfirm("Aktuelle Dashboard-Widgets als Standard für ALLE Nutzer setzen?", {
        description: "Neue Nutzer und alle, die auf \"Standard\" zurücksetzen, bekommen diese Widgets. Bestehende eigene Anordnungen bleiben unangetastet.",
        okText: "Als Standard speichern" }))) return;
      try {
        await api.setDefaultLayout("fleet", widgets());
        window.notify?.("Als organisationsweiter Standard gespeichert.", "success");
      } catch (e) { window.notify?.("Speichern fehlgeschlagen: " + e.message, "error"); }
    });
  }

  const rows = neededRows(list);
  host.className = "dash-grid-layout" + (edit ? " editing" : "");
  // EXAKT dasselbe Raster-System wie die Client-Ansicht (dashlayout):
  // Koordinaten-Grid mit gezeichneten Zellen, gleiche Spalten/Höhen/Gap.
  host.style.display = "grid";
  host.style.gridTemplateColumns = `repeat(${COLS}, 1fr)`;
  host.style.gridAutoRows = `${ROW_H}px`;
  host.style.gap = `${GAP}px`;
  host.style.minHeight = `${rows * ROW_H + (rows - 1) * GAP}px`;
  host.innerHTML = "";

  // Leeres Raster mit "+"-Zellen (nur im Bearbeiten-Modus) - wie beim Client.
  if (edit) renderEmptyCells(host, list, rows, toolbar);

  list.forEach((wdg) => host.appendChild(buildWidget(wdg, { host, toolbar, edit })));
}

// Belegte-Zellen-Karte -> freie Zellen als "+"-Buttons (öffnen den Picker an
// genau dieser Stelle). 1:1 wie dashlayout.renderEmptyCells.
function renderEmptyCells(host, list, rows, toolbar) {
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
      cell.title = "Hier ein Widget einfügen";
      cell.innerHTML = "<span>+</span>";
      cell.addEventListener("click", () => openAddDialog(host, toolbar, { gx: x, gy: y }));
      host.appendChild(cell);
    }
  }
}

// Anzahl benötigter Rasterreihen (mind. 2 sichtbare Reihen im Edit).
function neededRows(list) {
  let max = 0;
  for (const p of list) max = Math.max(max, (p.gy || 0) + (p.gh || 1));
  return Math.max(max + (getDashEdit() ? 1 : 0), 2);
}

// gx/gy/gw/gh aus Alt-Feldern (w/h) ableiten und lückenlos platzieren.
function ensureCoords(list) {
  let changed = false;
  for (const p of list) {
    if (typeof p.gw !== "number") { p.gw = Math.max(1, Math.min(COLS, p.w || 2)); changed = true; }
    if (typeof p.gh !== "number") {
      p.gh = Math.max(1, Math.round(((p.h || ROW_H) + GAP) / (ROW_H + GAP)));
      changed = true;
    }
  }
  // Alle ohne gültige Position neu einfügen (Reihenfolge beibehalten).
  const placed = list.filter((p) => typeof p.gx === "number" && typeof p.gy === "number");
  const missing = list.filter((p) => typeof p.gx !== "number" || typeof p.gy !== "number");
  for (const p of missing) { placeAt(placed, p); placed.push(p); changed = true; }
  if (changed) save();
}

// Erste freie Position finden, an die (gw x gh) passt.
function placeAt(existing, p) {
  const occ = [];
  const mark = (q) => {
    for (let y = q.gy; y < q.gy + q.gh; y++) {
      occ[y] = occ[y] || Array(COLS).fill(false);
      for (let x = q.gx; x < q.gx + q.gw; x++) occ[y][x] = true;
    }
  };
  existing.forEach(mark);
  const fits = (gx, gy) => {
    if (gx + p.gw > COLS) return false;
    for (let y = gy; y < gy + p.gh; y++)
      for (let x = gx; x < gx + p.gw; x++)
        if (occ[y] && occ[y][x]) return false;
    return true;
  };
  for (let gy = 0; gy < 400; gy++)
    for (let gx = 0; gx <= COLS - p.gw; gx++)
      if (fits(gx, gy)) { p.gx = gx; p.gy = gy; return; }
  p.gx = 0; p.gy = 0;
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
  card.dataset.tile = wdg.id;         // für die Resize-Engine (Nachbarschaft)
  card._panel = wdg;                  // Datensatz {w,h} für die Engine
  // Absolute Rasterposition (wie beim Client-Panel: gx/gy/gw/gh).
  card.style.gridColumn = `${(wdg.gx || 0) + 1} / span ${Math.max(1, Math.min(COLS, wdg.gw || 2))}`;
  card.style.gridRow = `${(wdg.gy || 0) + 1} / span ${Math.max(1, wdg.gh || 1)}`;
  card.style.height = "";

  // Kopf
  const head = document.createElement("div");
  head.className = "dash-w-head";
  head.innerHTML = `<span class="dash-w-title">${esc(widgetTitle(wdg))}</span>`;
  const tools = document.createElement("span");
  tools.className = "dash-w-tools";

  if (edit && wdg.kind !== "text" && presetById(wdg.preset)) {
    // Chart-Typ umschalten
    const p = presetById(wdg.preset);
    const sel = document.createElement("select");
    sel.className = "dash-w-kind";
    sel.innerHTML = availableKinds(p).map((k) => `<option value="${k}" ${k === wdg.kind ? "selected" : ""}>${k}</option>`).join("");
    sel.addEventListener("change", () => { wdg.kind = sel.value; save(); renderWidgetBody(body, wdg); });
    tools.appendChild(sel);

    // Pro-Widget: alle Geräte oder nur physische zählen (ersetzt die frühere
    // globale Profil-Einstellung). Wirkt nur auf Flotten-Aggregate.
    const scopeSel = document.createElement("select");
    scopeSel.className = "dash-w-kind";
    scopeSel.title = "Welche Geräte zählt dieses Widget?";
    scopeSel.innerHTML = `
      <option value="all" ${(wdg.scope || "all") === "all" ? "selected" : ""}>alle Geräte</option>
      <option value="physical" ${wdg.scope === "physical" ? "selected" : ""}>nur physische</option>`;
    scopeSel.addEventListener("change", () => { wdg.scope = scopeSel.value; save(); renderWidgetBody(body, wdg); });
    tools.appendChild(scopeSel);
  }

  const pop = document.createElement("button");
  pop.className = "dash-w-btn"; pop.title = "Als Fenster herauslösen"; pop.textContent = "⧉";
  pop.addEventListener("click", (e) => { e.stopPropagation(); detachWidget(wdg); });
  tools.appendChild(pop);

  if (edit) {
    const ren = document.createElement("button");
    ren.className = "dash-w-btn"; ren.title = "Titel ändern"; ren.textContent = "✎";
    ren.addEventListener("click", async (e) => {
      e.stopPropagation();
      const name = await uiPrompt("Widget-Titel ändern", {
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
  card.appendChild(body);
  renderWidgetBody(body, wdg);

  // Text-Widget im Edit-Modus direkt editierbar (Klick -> Textarea).
  if (wdg.kind === "text" && edit) {
    body.style.cursor = "text";
    body.addEventListener("dblclick", () => startTextEdit(body, wdg));
    // Hinweis für Erstnutzung
    body.title = "Doppelklick zum Bearbeiten";
  }

  attachWidgetDrag(card, head, wdg, ctx);
  if (edit) attachGridResize(card, wdg, ctx);
  return card;
}

// Rasterbasiertes Resizen wie im Client-Panel: Griffe rechts/unten/Ecke
// verändern gw/gh in ganzen Zellen (kein Pixel-Ziehen mehr).
function attachGridResize(card, wdg, ctx) {
  const { host, toolbar } = ctx;
  const gxg = document.createElement("div"); gxg.className = "tile-grip tile-grip-x";
  const gyg = document.createElement("div"); gyg.className = "tile-grip tile-grip-y";
  const gcg = document.createElement("div"); gcg.className = "tile-grip tile-grip-xy";
  card.appendChild(gxg); card.appendChild(gyg); card.appendChild(gcg);

  function startResize(axis) {
    return (e) => {
      e.preventDefault(); e.stopPropagation();
      document.body.classList.add("dash-dragging");
      const rect0 = card.getBoundingClientRect();
      const cw = (host.clientWidth - (COLS - 1) * GAP) / COLS;
      const startGW = wdg.gw, startGH = wdg.gh;
      function move(ev) {
        if (axis !== "y") {
          const px = ev.clientX - rect0.left;
          wdg.gw = Math.max(1, Math.min(COLS - wdg.gx, Math.round(px / (cw + GAP)) || 1));
        }
        if (axis !== "x") {
          const py = ev.clientY - rect0.top;
          wdg.gh = Math.max(1, Math.min(20, Math.round(py / (ROW_H + GAP)) || 1));
        }
        card.style.gridColumn = `${wdg.gx + 1} / span ${wdg.gw}`;
        card.style.gridRow = `${wdg.gy + 1} / span ${wdg.gh}`;
      }
      function up() {
        document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
        document.body.classList.remove("dash-dragging");
        if (wdg.gw !== startGW || wdg.gh !== startGH) { save(); renderFleetWidgets(host, toolbar); }
      }
      document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
    };
  }
  gxg.addEventListener("mousedown", startResize("x"));
  gyg.addEventListener("mousedown", startResize("y"));
  gcg.addEventListener("mousedown", startResize("xy"));
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
        </div>
        ${groups.map(([group, presets]) => `
          <div class="wp-group-title">${esc(group)}</div>
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
        ? { id: nid(), kind: "text", text: "Neuer Text – im Bearbeiten-Modus anklicken zum Ändern.", gw: 2, gh: 1 }
        : { id: nid(), preset: b.dataset.preset, kind: b.dataset.kind, gw: 2, gh: 1 };
      // Am angeklickten Rasterplatz einfügen, sonst erste freie Stelle.
      if (atCell) { w.gx = atCell.gx; w.gy = atCell.gy; }
      else placeAt(arr, w);
      arr.push(w);
      setFleetWidgets(arr); save(); close(); renderFleetWidgets(host, toolbar);
    }));
}

// -------------------- Drag: umsortieren / herauslösen --------------------
function attachWidgetDrag(card, head, wdg, ctx) {
  const { host, toolbar, edit } = ctx;
  head.addEventListener("mousedown", (e) => {
    if (e.target.closest("button") || e.target.closest("select")) return;
    const startX = e.clientX, startY = e.clientY;
    let mode = null;
    function onMove(ev) {
      const dist = Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY);
      if (!mode && dist > 8) {
        const r = host.getBoundingClientRect();
        const outside = ev.clientY < r.top - 40 || ev.clientY > r.bottom + 80 ||
                        ev.clientX < r.left - 30 || ev.clientX > r.right + 30;
        // Innerhalb + Edit = im Raster verschieben (Zellen-Snap, wie Client);
        // außerhalb = als Fenster herauslösen.
        if (edit && !outside) { mode = "move"; card.classList.add("dragging"); }
        else { mode = "detach"; card.classList.add("detach-hint"); }
      }
      if (mode === "move") {
        // Zielzelle unter dem Cursor bestimmen und Widget dorthin setzen.
        const r = host.getBoundingClientRect();
        const cw = (host.clientWidth - (COLS - 1) * GAP) / COLS;
        let gx = Math.floor((ev.clientX - r.left) / (cw + GAP));
        let gy = Math.floor((ev.clientY - r.top) / (ROW_H + GAP));
        gx = Math.max(0, Math.min(COLS - wdg.gw, gx));
        gy = Math.max(0, gy);
        if (gx !== wdg.gx || gy !== wdg.gy) {
          wdg.gx = gx; wdg.gy = gy;
          card.style.gridColumn = `${gx + 1} / span ${wdg.gw}`;
          card.style.gridRow = `${gy + 1} / span ${wdg.gh}`;
        }
      }
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (mode === "move") {
        card.classList.remove("dragging");
        resolveOverlaps(wdg);   // Überlappungen auflösen (nachrücken)
        save(); renderFleetWidgets(host, toolbar);
      } else if (mode === "detach") {
        card.classList.remove("detach-hint");
        detachWidget(wdg);
      }
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

// Nach einem Verschieben Überlappungen auflösen: das bewegte Widget behält
// seinen Platz, alle anderen weichen der Reihe nach auf die nächste freie
// Zelle aus (stabile Reihenfolge = oben-links zuerst).
function resolveOverlaps(moved) {
  const all = widgets();
  const others = all.filter((w) => w.id !== moved.id)
    .sort((a, b) => (a.gy - b.gy) || (a.gx - b.gx));
  const placed = [moved];
  for (const w of others) { placeAt(placed, w); placed.push(w); }
}


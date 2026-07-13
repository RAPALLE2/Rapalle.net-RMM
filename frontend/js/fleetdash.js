// fleetdash.js
// ------------
// Benutzerdefinierter, modularer Bereich des Flotten-Dashboards. Der Nutzer
// kann Widgets (Zahlen, Donuts, Gauges, Linien-Charts, Pies, Balken, Tabellen
// und freie Textblöcke) hinzufügen, per Drag umsortieren, in der Breite ziehen,
// als eigenes Fenster herauslösen und - bei Text - direkt bearbeiten.
//
// Alles pro Benutzer gespeichert (persist.js -> fleetWidgets). Der Edit-Modus
// ist derselbe globale Schalter wie bei der Client-Ansicht (Profil).

import { state } from "./state.js";
import { esc, uiConfirm, uiPrompt } from "./utils.js";
import {
  getFleetWidgets, setFleetWidgets, getDashEdit, scheduleSave,
} from "./persist.js";
import { presetsByGroup, presetById } from "./metriccatalog.js";
import { renderWidgetBody, widgetTitle, pushWidgetHistory, availableKinds } from "./dashwidgets.js";
import { openWindow } from "./windowmanager.js";
import { attachEdgeResizers } from "./paneltiling.js";

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
  // Breiten in RASTER-SPALTEN (1..5), Höhen in px auf Höheneinheiten gerastet.
  // Die drei Flotten-Übersicht-Donuts passen jeweils in EINE Höheneinheit.
  return [
    { id: nid(), preset: "fleet.statusDonut", kind: "fleetdonut", w: 2, h: ROW_H },
    { id: nid(), preset: "fleet.osDonut", kind: "fleetdonut", w: 2, h: ROW_H },
    { id: nid(), preset: "fleet.versionDonut", kind: "fleetdonut", w: 1, h: ROW_H },
    { id: nid(), preset: "fleet.online", kind: "donut", w: 2, h: ROW_H * 2 + GAP },
    { id: nid(), preset: "fleet.cpuLoadAvg", kind: "gauge", w: 2, h: ROW_H * 2 + GAP },
    { id: nid(), preset: "fleet.power", kind: "number", w: 1, h: ROW_H },
    { id: nid(), preset: "host.cpuLoad", kind: "bar", w: 3, h: ROW_H * 2 + GAP },
    { id: nid(), preset: "fleet.osDist", kind: "pie", w: 2, h: ROW_H * 2 + GAP },
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

  if (toolbar) {
    toolbar.innerHTML = edit ? `
        <span class="dash-edit-tools">
          <button data-add-widget>+ Widget</button>
          <button data-add-text>+ Text</button>
          <button data-reset-widgets>↺ Standard</button>
        </span>` : "";
    toolbar.querySelector("[data-add-widget]")?.addEventListener("click", () => openAddDialog(host, toolbar));
    toolbar.querySelector("[data-add-text]")?.addEventListener("click", () => {
      list.push({ id: nid(), kind: "text", text: "Neuer Text – im Bearbeiten-Modus anklicken zum Ändern.", w: 4 });
      save(); renderFleetWidgets(host, toolbar);
    });
    toolbar.querySelector("[data-reset-widgets]")?.addEventListener("click", async () => {
      const ok = await uiConfirm("Widgets auf Standard zurücksetzen?", { okText: "Zurücksetzen", danger: true });
      if (!ok) return;
      setFleetWidgets(defaults()); save(); renderFleetWidgets(host, toolbar);
    });
  }

  host.className = "dash-widgets" + (edit ? " editing" : "");
  host.innerHTML = "";
  list.forEach((wdg) => host.appendChild(buildWidget(wdg, { host, toolbar, edit })));
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
  card.style.gridColumn = `span ${Math.max(1, Math.min(COLS, wdg.w))}`;
  card.style.height = `${wdg.h || ROW_H}px`;

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
  if (edit) {
    // Windows-artige Rand-Griffe: Links = alle angrenzenden, Rechts = nur die
    // beiden an der Grenze; zusätzlich Höhe frei ziehbar.
    attachEdgeResizers(card, host, { cols: COLS, minW: 1,
      panelOf: (c) => c._panel,
      commit: () => {
        // Höhen aufs Panel-Raster einrasten (gleiche Höheneinheiten wie die
        // Client-Ansicht), dann speichern.
        for (const other of widgets()) {
          if (typeof other.h === "number") other.h = snapH(other.h);
          const el = host.querySelector(`[data-widget="${other.id}"]`);
          if (el) el.style.height = `${other.h}px`;
        }
        save();
      } });
  }
  return card;
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
function openAddDialog(host, toolbar) {
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
      arr.push({ id: nid(), preset: b.dataset.preset, kind: b.dataset.kind, w: 4 });
      setFleetWidgets(arr); save(); close(); renderFleetWidgets(host, toolbar);
    }));
}

// -------------------- Drag: umsortieren / herauslösen --------------------
function attachWidgetDrag(card, head, wdg, ctx) {
  const { host, toolbar, edit } = ctx;
  head.addEventListener("mousedown", (e) => {
    if (e.target.closest("button") || e.target.closest("select")) return;
    const startX = e.clientX, startY = e.clientY;
    let mode = null, placeholder = null;
    function onMove(ev) {
      const dist = Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY);
      if (!mode && dist > 8) {
        const r = host.getBoundingClientRect();
        const outside = ev.clientY < r.top - 40 || ev.clientY > r.bottom + 80 ||
                        ev.clientX < r.left - 30 || ev.clientX > r.right + 30;
        if (edit && !outside) {
          mode = "reorder";
          card.classList.add("dragging");
          placeholder = document.createElement("div");
          placeholder.className = "dash-w-placeholder";
          placeholder.style.gridColumn = card.style.gridColumn;
          card.after(placeholder);
          card.style.position = "fixed";
          card.style.width = `${card.offsetWidth}px`;
          card.style.zIndex = "9999";
          card.style.pointerEvents = "none";
        } else { mode = "detach"; card.classList.add("detach-hint"); }
      }
      if (mode === "reorder") {
        card.style.left = `${ev.clientX - 40}px`;
        card.style.top = `${ev.clientY - 14}px`;
        const over = [...host.querySelectorAll(".dash-w")].find((c) => {
          if (c === card) return false;
          const r = c.getBoundingClientRect();
          return ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
        });
        if (over && placeholder) {
          const r = over.getBoundingClientRect();
          if (ev.clientX > r.left + r.width / 2) over.after(placeholder); else over.before(placeholder);
        }
      }
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (mode === "reorder") {
        card.classList.remove("dragging");
        card.style.position = ""; card.style.left = ""; card.style.top = "";
        card.style.zIndex = ""; card.style.pointerEvents = ""; card.style.width = "";
        card.style.gridColumn = `span ${wdg.w}`;
        if (placeholder) placeholder.replaceWith(card);
        const order = [...host.querySelectorAll(".dash-w")].map((c) => c.dataset.widget);
        const arr = widgets();
        arr.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
        setFleetWidgets(arr); save();
      } else if (mode === "detach") {
        card.classList.remove("detach-hint");
        detachWidget(wdg);
      }
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}


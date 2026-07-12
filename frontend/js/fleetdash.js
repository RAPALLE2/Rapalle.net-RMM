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
import { esc } from "./utils.js";
import {
  getFleetWidgets, setFleetWidgets, getDashEdit, setDashEdit, scheduleSave,
} from "./persist.js";
import { presetsByGroup, presetById } from "./metriccatalog.js";
import { renderWidgetBody, widgetTitle, pushWidgetHistory } from "./dashwidgets.js";
import { openWindow } from "./windowmanager.js";
import { attachEdgeResizers } from "./paneltiling.js";

let _uid = 0;
const nid = () => `w-${Date.now().toString(36)}-${(_uid++).toString(36)}`;

function defaults() {
  return [
    { id: nid(), preset: "fleet.online", kind: "donut", w: 4 },
    { id: nid(), preset: "fleet.cpuLoadAvg", kind: "gauge", w: 4 },
    { id: nid(), preset: "fleet.power", kind: "number", w: 4 },
    { id: nid(), preset: "host.cpuLoad", kind: "bar", w: 6 },
    { id: nid(), preset: "fleet.osDist", kind: "pie", w: 6 },
  ];
}

function widgets() {
  let w = getFleetWidgets();
  if (!Array.isArray(w)) { w = defaults(); setFleetWidgets(w); }
  for (const x of w) { if (!x.id) x.id = nid(); if (!x.w) x.w = 4; }
  return w;
}
function save() { scheduleSave(state); }

// Von dashboard.js aufgerufen. host = Grid-Container, toolbar = Kopf-Aktionen.
export function renderFleetWidgets(host, toolbar) {
  const list = widgets();
  const edit = getDashEdit();

  if (toolbar) {
    toolbar.innerHTML = `
      <button class="dash-edit-toggle ${edit ? "on" : ""}" title="Dashboard bearbeiten (an/aus)">
        ${edit ? "✓ Bearbeiten" : "✎ Bearbeiten"}
      </button>
      ${edit ? `
        <span class="dash-edit-tools">
          <button data-add-widget>+ Widget</button>
          <button data-add-text>+ Text</button>
          <button data-reset-widgets>↺ Standard</button>
        </span>` : ""}`;
    toolbar.querySelector(".dash-edit-toggle").addEventListener("click", () => {
      setDashEdit(!edit); save(); renderFleetWidgets(host, toolbar);
    });
    toolbar.querySelector("[data-add-widget]")?.addEventListener("click", () => openAddDialog(host, toolbar));
    toolbar.querySelector("[data-add-text]")?.addEventListener("click", () => {
      list.push({ id: nid(), kind: "text", text: "Neuer Text – im Bearbeiten-Modus anklicken zum Ändern.", w: 4 });
      save(); renderFleetWidgets(host, toolbar);
    });
    toolbar.querySelector("[data-reset-widgets]")?.addEventListener("click", () => {
      if (!confirm("Widgets auf Standard zurücksetzen?")) return;
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
    if (wdg.kind === "line") pushWidgetHistory(wdg);
    const card = host.querySelector(`[data-widget="${wdg.id}"] .dash-w-body`);
    if (card && !card._editingText) renderWidgetBody(card, wdg);
  }
}

function buildWidget(wdg, ctx) {
  const { host, toolbar, edit } = ctx;
  const card = document.createElement("div");
  card.className = "panel dash-w";
  card.dataset.widget = wdg.id;
  card.dataset.tile = wdg.id;         // für die Resize-Engine (Nachbarschaft)
  card._panel = wdg;                  // Datensatz {w,h} für die Engine
  card.style.gridColumn = `span ${Math.max(2, Math.min(12, wdg.w))}`;
  if (wdg.h) card.style.height = `${wdg.h}px`;

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
    sel.innerHTML = (p.charts || ["number"]).map((k) => `<option value="${k}" ${k === wdg.kind ? "selected" : ""}>${k}</option>`).join("");
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
    ren.addEventListener("click", (e) => {
      e.stopPropagation();
      const name = prompt("Titel (leer = Standard):", wdg.title || "");
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
    attachEdgeResizers(card, host, { cols: 12, minW: 2,
      panelOf: (c) => c._panel,
      commit: () => save() });
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


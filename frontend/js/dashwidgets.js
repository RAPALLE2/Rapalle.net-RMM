// dashwidgets.js
// --------------
// Rendert ein einzelnes Dashboard-Widget in verschiedenen Darstellungen.
// Ein Widget = { id, preset, kind, title?, text?, gx, gy, gw, gh }.
//
// DESIGN-PRINZIP (Raster-Version):
//   Jede Darstellung ist auf eine natürliche Größe für EINE 1x1-Rasterzelle
//   optimiert (NAT_W x NAT_H, gut lesbare Schriften). Ein Skalierer
//   (scaleToContainer) lässt den Inhalt dann proportional mit der Zellgröße
//   mitwachsen: ein 2x2-Widget zeigt EXAKT denselben Inhalt in denselben
//   Proportionen, nur ~doppelt so groß.
//
// HOVER-PRINZIP:
//   - Einzelwert-Darstellungen (number/gauge/donut/ring/progress/stat):
//     Tooltip mit Titel + exaktem Live-Wert auf dem ganzen Widget.
//   - Zeilen-/Segment-Darstellungen (pie/bar/column/list/table/fleetdonut):
//     Hover PRO Element - das gehoverte Segment/die Zeile wird hervorgehoben
//     (Rest gedimmt bzw. Zeilen-Hintergrund) und der Tooltip zeigt Label +
//     exakten Wert GENAU DIESES Elements.
//   - Verläufe (line/area/spark): getimestampter Hover wie das Netzwerk-
//     Diagramm im Metrics-Panel (Hover-Linie, Punkt-Marker, Uhrzeit + Wert).

import { state } from "./state.js";
import { esc } from "./utils.js";
import { presetById } from "./metriccatalog.js";
import { setHostScope, clearHostScope } from "./metriccatalog.js";
import {
  buildFleetDonut, showFleetTip, hideFleetTip, attachHoverTip,
  scaleToContainer, timeSeriesChart,
} from "./fleetcharts.js";

const PIE_COLORS = [
  "#4da6ff", "#3ecf8e", "#f5a524", "#ff4d6d", "#a78bfa",
  "#22d3ee", "#fb923c", "#e879f9", "#84cc16", "#f43f5e",
  "#2dd4bf", "#facc15", "#60a5fa", "#c084fc", "#34d399",
];

// Natürliche Inhaltsgröße einer 1x1-Zelle (Body ~240x104 px bei 5 Spalten).
// Alle Darstellungen sind auf GENAU diese Box designt und füllen sie aus;
// der Skalierer bringt sie dann verlustfrei auf die echte Widget-Größe.
const NAT_W = 240;
const NAT_H = 88;   // echte 1x1-Bodyhöhe (~150px-Zelle minus Kopf/Padding)

// Rollierende Historie je Widget-Instanz (id -> {ts:[], v:[]}), max N Punkte.
// Fleet-Widgets zeigen client-uebergreifende Aggregate - dafuer gibt es keinen
// Backend-Verlauf. Damit die Graphen nach einem Reload (Strg+F5) trotzdem nicht
// bei 0 anfangen, wird die Historie LOKAL im Browser (localStorage) gespeichert
// und beim Start wieder geladen.
const MAX_POINTS = 120;
const _HIST_KEY = "rmm_fleet_history";
const _HIST_MAX_AGE = 6 * 3600 * 1000;   // aelter als 6 h -> verwerfen

function _loadHistory() {
  try {
    const raw = localStorage.getItem(_HIST_KEY);
    if (!raw) return new Map();
    const obj = JSON.parse(raw);
    const cutoff = Date.now() - _HIST_MAX_AGE;
    const map = new Map();
    for (const [id, h] of Object.entries(obj)) {
      if (!h || !Array.isArray(h.ts) || !Array.isArray(h.v)) continue;
      const ts = [], v = [];
      for (let i = 0; i < h.ts.length; i++) {
        if (h.ts[i] >= cutoff) { ts.push(h.ts[i]); v.push(h.v[i]); }
      }
      if (ts.length) map.set(id, { ts, v });
    }
    return map;
  } catch { return new Map(); }
}

const _history = _loadHistory();

let _histSaveTimer = null;
function _persistHistory() {
  if (_histSaveTimer) return;
  _histSaveTimer = setTimeout(() => {
    _histSaveTimer = null;
    try {
      const obj = {};
      for (const [id, h] of _history.entries()) obj[id] = h;
      localStorage.setItem(_HIST_KEY, JSON.stringify(obj));
    } catch { /* localStorage voll/deaktiviert -> ignorieren */ }
  }, 1500);
}

export function pushWidgetHistory(widget) {
  // Alle verlaufbasierten Darstellungen sammeln Historie (nicht nur "line").
  if (!["line", "area", "spark", "stat"].includes(widget.kind)) return;
  const p = presetById(widget.preset);
  if (!p || !p.value) return;
  const h = _history.get(widget.id) || { ts: [], v: [] };
  const now = Date.now();
  // Doppelte Punkte < 2 s überspringen (mehrere Renders pro Tick).
  if (h.ts.length && now - h.ts[h.ts.length - 1] < 2000) return;
  setHostScope(widget.scope || "all");
  try { h.ts.push(now); h.v.push(p.value(state) || 0); } finally { clearHostScope(); }
  if (h.ts.length > MAX_POINTS) { h.ts.shift(); h.v.shift(); }
  _history.set(widget.id, h);
  _persistHistory();
}

export function formatValue(preset, v) {
  if (v === null || v === undefined) return "—";
  if (preset.format) return preset.format(v);
  if (preset.unit === "%") return `${Math.round(v)}%`;
  return String(v);
}

// Zusätzliche Ansichten, die automatisch für JEDES passende Preset verfügbar
// sind (ohne jedes Preset einzeln anzufassen):
//   Wert-Presets (value):  area, spark, progress, ring, stat
//   Zeilen-Presets (rows): column, list
export const VALUE_EXTRA_KINDS = ["area", "spark", "progress", "ring", "stat"];
export const ROWS_EXTRA_KINDS = ["column", "list"];
export function availableKinds(preset) {
  if (!preset) return ["number"];
  const kinds = [...(preset.charts || [])];
  const add = (k) => { if (!kinds.includes(k)) kinds.push(k); };
  if (preset.value) for (const k of VALUE_EXTRA_KINDS) add(k);
  if (preset.rows) for (const k of ROWS_EXTRA_KINDS) add(k);
  // Kreis-Darstellungen gegenseitig anbieten:
  if (kinds.includes("pie") || (preset.rows && kinds.includes("donut"))) { add("pie"); add("donut"); }
  if (preset.value && (kinds.includes("gauge") || kinds.includes("donut") || kinds.includes("ring"))) {
    add("gauge"); add("donut"); add("ring");
  }
  return kinds;
}

export function renderWidgetBody(target, widget) {
  target.innerHTML = "";
  if (widget.kind === "text") return renderText(target, widget);

  const preset = presetById(widget.preset);
  if (!preset) { target.innerHTML = `<div style="color:var(--subtext);font-size:12px">Unbekannte Metrik.</div>`; return; }

  // Pro-Widget-Zählweise: "physical" = nur physische Geräte, "all" = alle.
  // WICHTIG: Default ist "all" - exakt das, was das Dropdown anzeigt. Früher
  // wurde bei fehlendem scope null übergeben und damit die alte GLOBALE
  // Profileinstellung benutzt; stand die auf "nur physische", zählten
  // untergeordnete VMs (parent_client_id) trotz Anzeige "alle Geräte" nicht mit.
  setHostScope(widget.scope || "all");
  try {
    renderWidgetInner(target, widget, preset);
  } finally {
    clearHostScope();
  }
}

function renderWidgetInner(target, widget, preset) {
  // Skalier-Holder: Inhalt hat eine feste natürliche 1x1-Größe und wird
  // proportional auf die tatsächliche Widget-Größe skaliert (runter UND rauf).
  const holder = document.createElement("div");
  holder.className = "widget-fit-holder";
  holder.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden;min-width:0;min-height:0";
  target.appendChild(holder);
  const out = holder;

  switch (widget.kind) {
    case "number": renderNumber(out, widget, preset); break;
    case "donut": renderDonut(out, widget, preset); break;
    case "gauge": renderGauge(out, widget, preset); break;
    case "line": renderSeries(out, widget, preset, "line"); break;
    case "area": renderSeries(out, widget, preset, "area"); break;
    case "spark": renderSeries(out, widget, preset, "spark"); break;
    case "pie": renderPie(out, widget, preset); break;
    case "bar": renderBar(out, widget, preset); break;
    case "table": renderTable(out, widget, preset); break;
    case "progress": renderProgress(out, widget, preset); break;
    case "ring": renderRing(out, widget, preset); break;
    case "stat": renderStat(out, widget, preset); break;
    case "column": renderColumn(out, widget, preset); break;
    case "list": renderList(out, widget, preset); break;
    case "overview": renderOverview(out, widget, preset); break;
    case "fleetdonut": renderFleetDonut(out, widget, preset); break;
    default: out.innerHTML = `<div style="color:var(--subtext)">${esc(widget.kind)}?</div>`; return;
  }
  // Proportional skalieren (1x1-optimierter Inhalt wächst mit der Zelle mit).
  scaleToContainer(holder);

  // Widget-weiter Wert-Tooltip NUR für Einzelwert-Darstellungen. Zeilen-/
  // Segment-/Verlaufs-Darstellungen haben feinere Per-Element-Hover und
  // bekommen hier bewusst KEINEN (sonst überschriebe der Widget-Tooltip den
  // Element-Tooltip - das war das "nur der Widget-Name"-Problem).
  // Listener nur EINMAL anhängen; die Tooltip-Funktion wird pro Render neu
  // gesetzt (_tipFn) und kann null sein (= aus).
  if (!target._hoverTipAttached) {
    target._hoverTipAttached = true;
    attachHoverTip(target, () => (typeof target._tipFn === "function" ? target._tipFn() : null));
  }
  const valueTip = ["number", "gauge", "ring", "progress", "stat"].includes(widget.kind)
    || (widget.kind === "donut" && (preset.donut || !preset.rows));
  target._tipFn = valueTip ? () => {
    const p = presetById(widget.preset) || preset;
    setHostScope(widget.scope || "all");
    let v, max;
    try {
      v = p.value ? p.value(state) : null;
      max = typeof p.max === "number" ? ` <span style="color:var(--subtext)">/ ${esc(formatValue(p, p.max))}</span>` : "";
    } finally { clearHostScope(); }
    return `<b>${esc(widget.title || p.label)}</b><br>${esc(formatValue(p, v))}${max}`;
  } : null;
}

// Titel eines Widgets (frei überschreibbar).
export function widgetTitle(widget) {
  if (widget.title) return widget.title;
  if (widget.kind === "text") return "Text";
  const p = presetById(widget.preset);
  return p ? p.label : widget.preset;
}

// -------------------- gemeinsame Hover-Bausteine --------------------

// Zeilen-Hover: Hintergrund der Zeile hervorheben + Tooltip mit dem Wert
// GENAU DIESER Zeile (folgt der Maus). tipFn liefert das Tooltip-HTML.
function bindRowHover(rowEl, tipFn) {
  rowEl.style.borderRadius = rowEl.style.borderRadius || "6px";
  rowEl.style.cursor = "default";
  rowEl.addEventListener("mouseenter", (e) => { rowEl.style.background = "var(--panel-2, #1b2740)"; showFleetTip(tipFn(), e.clientX, e.clientY); });
  rowEl.addEventListener("mousemove", (e) => showFleetTip(tipFn(), e.clientX, e.clientY));
  rowEl.addEventListener("mouseleave", () => { rowEl.style.background = ""; hideFleetTip(); });
}

// "+N weitere"-Hinweiszeile für gekürzte Listen (Tooltip zeigt den Rest).
function moreNote(hidden) {
  if (!hidden.length) return null;
  const el = document.createElement("div");
  el.style.cssText = "font-size:11.5px;color:var(--subtext);padding:2px 4px;cursor:default";
  el.textContent = `+ ${hidden.length} weitere`;
  attachHoverTip(el, () => hidden.slice(0, 15).map((r) => `${esc(r.label)}: <b>${esc(r.raw != null ? r.raw : String(r.value ?? "—"))}</b>`).join("<br>")
    + (hidden.length > 15 ? `<br><span style="color:var(--subtext)">…</span>` : ""));
  return el;
}

// -------------------- Verläufe (line / area / spark) --------------------

function widgetHistory(widget) {
  if (!_history.get(widget.id) || !_history.get(widget.id).v.length) pushWidgetHistory(widget);
  return _history.get(widget.id) || { ts: [Date.now()], v: [0] };
}

// Getimestampter Verlauf mit Kopfzeile (Label + Live-Wert). Der Chart-Hover
// (Hover-Linie + Punkt + Uhrzeit + Wert) kommt aus timeSeriesChart.
function renderSeries(target, widget, preset, mode) {
  const h = widgetHistory(widget);
  const cur = preset.value ? preset.value(state) : 0;
  const spark = mode === "spark";
  const wrap = document.createElement("div");
  wrap.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;flex-direction:column;justify-content:space-between`;
  const head = document.createElement("div");
  head.style.cssText = "display:flex;justify-content:space-between;align-items:baseline;gap:8px";
  head.innerHTML = spark
    ? `<span style="font-size:28px;font-weight:800;line-height:1.1">${esc(formatValue(preset, cur))}</span>
       <span style="font-size:13px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</span>`
    : `<span style="font-size:13.5px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</span>
       <span style="font-size:20px;font-weight:800">${esc(formatValue(preset, cur))}</span>`;
  wrap.appendChild(head);
  wrap.appendChild(timeSeriesChart(
    [{ label: widget.title || preset.label, color: "var(--accent)", values: h.v, timestamps: h.ts }],
    { width: NAT_W, height: spark ? 54 : 62, mode, yMax: typeof preset.max === "number" ? Math.max(preset.max, ...h.v) : null,
      formatValue: (v) => formatValue(preset, v) },
  ));
  target.appendChild(wrap);
}

// -------------------- Einzelwerte --------------------

function renderText(target, widget) {
  const div = document.createElement("div");
  div.className = "widget-text";
  div.innerHTML = esc(widget.text || "Text…").replace(/\n/g, "<br>");
  target.appendChild(div);
}

function renderNumber(target, widget, preset) {
  const v = preset.value ? preset.value(state) : 0;
  const txt = formatValue(preset, v);
  // Wertgröße adaptiv: kurze Werte RIESIG, lange Werte kleiner (passt immer).
  const fs = txt.length > 12 ? 26 : txt.length > 8 ? 34 : 44;
  const box = document.createElement("div");
  box.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center`;
  box.innerHTML = `<div style="font-size:${fs}px;font-weight:800;line-height:1.05;overflow-wrap:anywhere">${esc(txt)}</div>
    <div style="font-size:14px;color:var(--subtext);margin-top:5px;max-width:${NAT_W - 10}px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</div>`;
  target.appendChild(box);
}

// Einzelwert-Donut: Donut SEITLICH (links), rechts großer Wert + Label -
// so wird die komplette 1x1-Box (240x104) ausgenutzt.
function donutSideLayout(pct, insideText, valueText, label, sub, color = "var(--accent)") {
  const size = 88, stroke = 13, r = (size - stroke) / 2 - 1, cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r;
  const len = Math.max(0, Math.min(1, pct / 100)) * C;
  return `
    <div style="width:${NAT_W}px;height:${NAT_H}px;display:flex;align-items:center;gap:14px">
      <div style="position:relative;width:${size}px;height:${size}px;flex:none">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"/>
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
            stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-linecap="round"
            transform="rotate(-90 ${cx} ${cy})"/>
        </svg>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center">
          <div style="font-size:22px;font-weight:800">${esc(insideText)}</div>
        </div>
      </div>
      <div style="flex:1;min-width:0">
        <div style="font-size:${String(valueText).length > 9 ? 23 : 30}px;font-weight:800;line-height:1.1;overflow-wrap:anywhere;word-break:break-word">${esc(valueText)}</div>
        <div style="font-size:14.5px;color:var(--subtext);margin-top:3px;line-height:1.3">${esc(label)}</div>
        ${sub ? `<div style="font-size:13px;color:var(--subtext);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(sub)}</div>` : ""}
      </div>
    </div>`;
}

function renderDonut(target, widget, preset) {
  let pct, valueText, sub;
  if (preset.donut) {
    const d = preset.donut(state);
    pct = d.max ? (d.value / d.max) * 100 : d.value;
    valueText = d.pctText ? `${Math.round(pct)}%` : (preset.format ? preset.format(d.value) : `${Math.round(pct)}%`);
    sub = d.sub;
  } else if (preset.rows) {
    // Kategorische Verteilung: mehrsegmentiger Donut mit Legende + eigenem
    // Per-Segment-Hover (buildFleetDonut), füllt die 1x1-Box.
    const segs = (preset.rows(state) || []).filter((r) => (r.value || 0) > 0)
      .map((r, i) => ({ label: r.label, count: r.value, color: r.color || PIE_COLORS[i % PIE_COLORS.length], items: r.items }));
    const box = document.createElement("div");
    box.style.cssText = `width:${NAT_W}px`;
    box.appendChild(buildFleetDonut(segs, { card: false, size: 88 }));
    target.appendChild(box);
    return;
  } else {
    const v = preset.value(state);
    pct = preset.max ? (v / preset.max) * 100 : v;
    valueText = formatValue(preset, v);
  }
  const wrap = document.createElement("div");
  wrap.innerHTML = donutSideLayout(pct, `${Math.round(pct)}%`, valueText, preset.label, sub);
  target.appendChild(wrap.firstElementChild);
}

// Halbkreis-Gauge SEITLICH: Bogen links auf voller Höhe, rechts GROSSER
// Wert + Label - kein Überlappen von Text und Bogen, volle Boxausnutzung.
function renderGauge(target, widget, preset) {
  const v = preset.value(state);
  const max = preset.max || 100;
  const pct = Math.max(0, Math.min(100, ((v || 0) / max) * 100));
  const gw = 148, gh = NAT_H, cx = gw / 2, cy = gh - 4, r = 66, stroke = 15;
  const ang = Math.PI * (pct / 100);
  const ex = cx - r * Math.cos(ang), ey = cy - r * Math.sin(ang);
  const color = pct > 85 ? "#ff4d6d" : pct > 65 ? "#f5a524" : "#3ecf8e";
  const wrap = document.createElement("div");
  wrap.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;align-items:center;gap:10px`;
  wrap.innerHTML = `
    <div style="position:relative;width:${gw}px;height:${gh}px;flex:none">
      <svg viewBox="0 0 ${gw} ${gh}" width="${gw}" height="${gh}" style="overflow:visible">
        <path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}" fill="none" stroke="var(--border)" stroke-width="${stroke}" stroke-linecap="round"/>
        ${v === null || v === undefined ? "" : `<path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${ex.toFixed(2)} ${ey.toFixed(2)}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"/>`}
      </svg>
      <div style="position:absolute;left:0;right:0;bottom:2px;text-align:center;pointer-events:none">
        <div style="font-size:21px;font-weight:800;line-height:1">${Math.round(pct)}%</div>
      </div>
    </div>
    <div style="flex:1;min-width:0">
      <div style="font-size:${String(formatValue(preset, v)).length > 7 ? 20 : 26}px;font-weight:800;line-height:1.1;overflow-wrap:anywhere;word-break:break-word">${esc(formatValue(preset, v))}</div>
      <div style="font-size:13px;color:var(--subtext);margin-top:4px;line-height:1.3">${esc(preset.label)}</div>
    </div>`;
  target.appendChild(wrap);
}

// Fortschrittsbalken (Wert / Maximum): füllt die 1x1-Box, große Schriften.
function renderProgress(target, widget, preset) {
  const v = preset.value ? preset.value(state) : 0;
  const max = typeof preset.max === "number" ? preset.max : 100;
  const pct = Math.max(0, Math.min(100, ((v || 0) / (max || 1)) * 100));
  const color = pct > 85 ? "#ff4d6d" : pct > 65 ? "#f5a524" : "var(--accent)";
  const wrap = document.createElement("div");
  wrap.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;flex-direction:column;justify-content:center`;
  wrap.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;gap:8px">
      <span style="font-size:14.5px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</span>
      <span style="font-size:25px;font-weight:800">${esc(formatValue(preset, v))}</span>
    </div>
    <div style="height:18px;border-radius:9px;background:var(--panel-2);overflow:hidden">
      <div style="height:100%;width:${pct.toFixed(1)}%;background:${color};border-radius:9px;transition:width .3s"></div>
    </div>
    <div style="font-size:12.5px;color:var(--subtext);margin-top:6px;text-align:right">${Math.round(pct)}% von ${esc(formatValue(preset, max))}</div>`;
  target.appendChild(wrap);
}

// Ring (dünner Donut): SEITLICH, rechts großer Wert + Label (volle Box).
function renderRing(target, widget, preset) {
  const v = preset.value ? preset.value(state) : 0;
  const max = typeof preset.max === "number" ? preset.max : 100;
  const pct = Math.max(0, Math.min(100, ((v || 0) / (max || 1)) * 100));
  const size = 86, stroke = 10, r = (size - stroke) / 2 - 1, cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r, len = (pct / 100) * C;
  const wrap = document.createElement("div");
  wrap.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;align-items:center;gap:14px`;
  wrap.innerHTML = `
    <div style="position:relative;width:${size}px;height:${size}px;flex:none">
      <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"/>
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--accent)" stroke-width="${stroke}"
          stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})"/>
      </svg>
      <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center">
        <div style="font-size:19px;font-weight:800">${Math.round(pct)}%</div>
      </div>
    </div>
    <div style="flex:1;min-width:0">
      <div style="font-size:${String(formatValue(preset, v)).length > 9 ? 21 : 27}px;font-weight:800;line-height:1.1;overflow-wrap:anywhere;word-break:break-word">${esc(formatValue(preset, v))}</div>
      <div style="font-size:12px;color:var(--subtext);margin-top:3px;line-height:1.3">${esc(preset.label)}</div>
    </div>`;
  target.appendChild(wrap);
}

// Stat: großer Wert + Trendpfeil + Mini-Spark (Spark mit Zeit-Hover).
function renderStat(target, widget, preset) {
  const h = widgetHistory(widget);
  const data = h.v;
  const cur = preset.value ? preset.value(state) : 0;
  const ref = data.length > 1 ? data[0] : cur;
  const diff = (cur || 0) - (ref || 0);
  const up = diff > 0, flat = Math.abs(diff) < 1e-9;
  const trendColor = flat ? "var(--subtext)" : (up ? "#f5a524" : "#3ecf8e");
  const wrap = document.createElement("div");
  wrap.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;align-items:center;gap:12px`;
  wrap.innerHTML = `
    <div style="flex:none;max-width:118px">
      <div style="font-size:30px;font-weight:800;line-height:1.1;overflow-wrap:anywhere;word-break:break-word">${esc(formatValue(preset, cur))}</div>
      <div style="font-size:13px;color:var(--subtext);margin-top:2px">${esc(preset.label)}</div>
    </div>
    <div style="text-align:right;flex:1;min-width:0">
      <div style="font-size:15.5px;font-weight:700;color:${trendColor};margin-bottom:2px">${flat ? "→" : (up ? "▲" : "▼")} ${esc(formatValue(preset, Math.abs(diff)))}</div>
      <div class="stat-spark"></div>
    </div>`;
  wrap.querySelector(".stat-spark").appendChild(timeSeriesChart(
    [{ label: widget.title || preset.label, color: trendColor, values: data, timestamps: h.ts }],
    { width: 112, height: 44, mode: "spark", formatValue: (v) => formatValue(preset, v) },
  ));
  target.appendChild(wrap);
}

// -------------------- Zeilen / Segmente --------------------

// Kreisdiagramm mit Legende, 1x1-optimiert (96er-Kreis, Legende rechts,
// max. 6 Zeilen + "+N weitere"). Hover: Segment/Zeile hervorheben, Rest
// dimmen, Tooltip mit Label + Wert + Prozent (+ Client-Liste falls vorhanden).
function renderPie(target, widget, preset) {
  const all = (preset.rows ? preset.rows(state) : []).filter((r) => (r.value || 0) > 0);
  const total = all.reduce((s, r) => s + r.value, 0);
  const rows = all.slice(0, 4), hidden = all.slice(4);
  const size = 88, r = size / 2 - 2, cx = size / 2, cy = size / 2;
  let a0 = -Math.PI / 2;
  let paths = "";
  rows.forEach((row, i) => {
    const frac = row.value / total;
    const a1 = a0 + frac * 2 * Math.PI;
    const large = frac > 0.5 ? 1 : 0;
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    paths += (all.length === 1)
      ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}" class="wpie-seg" data-seg="${i}" style="cursor:pointer;transition:opacity .12s"/>`
      : `<path d="M ${cx} ${cy} L ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z" fill="${color}" class="wpie-seg" data-seg="${i}" style="cursor:pointer;transition:opacity .12s"/>`;
    a0 = a1;
  });
  // Restsegment für die ausgeblendeten Zeilen (grau, mit Sammel-Tooltip).
  if (hidden.length) {
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const frac = hidden.reduce((s, x) => s + x.value, 0) / total;
    const a1 = a0 + frac * 2 * Math.PI;
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    paths += `<path d="M ${cx} ${cy} L ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${frac > 0.5 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z" fill="var(--border)" class="wpie-rest" style="cursor:pointer"/>`;
  }
  const legend = rows.map((row, i) => {
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    const pct = total ? Math.round((row.value / total) * 100) : 0;
    return `<div class="wpie-row" data-seg="${i}" style="display:flex;align-items:center;gap:6px;font-size:${rows.length <= 3 ? 15 : 13.5}px;padding:${rows.length <= 3 ? 3 : 1}px 3px;border-radius:6px;cursor:pointer">
      <span style="width:9px;height:9px;border-radius:3px;background:${color};flex:none"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(row.label)}</span>
      <span style="color:var(--subtext);font-variant-numeric:tabular-nums">${row.value} · ${pct}%</span>
    </div>`;
  }).join("");
  const wrap = document.createElement("div");
  wrap.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;gap:12px;align-items:center`;
  wrap.innerHTML = `
    <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="flex:none">${paths || `<circle cx="${cx}" cy="${cy}" r="${r}" fill="var(--border)"/>`}</svg>
    <div style="flex:1;min-width:0" class="wpie-legend">${legend || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`}</div>`;
  target.appendChild(wrap);
  if (hidden.length) wrap.querySelector(".wpie-legend").appendChild(moreNote(hidden));

  const segEls = wrap.querySelectorAll(".wpie-seg");
  const tipFor = (row) => {
    const pctT = total ? Math.round(((row.value || 0) / total) * 100) : 0;
    const items = row.items ? `<br><span style="color:var(--subtext)">${row.items.slice(0, 12).map((x) => `• ${esc(x)}`).join("<br>")}${row.items.length > 12 ? `<br>… und ${row.items.length - 12} weitere` : ""}</span>` : "";
    return `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(row.value))} (${pctT}%)${items}`;
  };
  const highlight = (i, on) => {
    segEls.forEach((s) => s.setAttribute("opacity", on && +s.dataset.seg !== i ? "0.4" : ""));
    const lr = wrap.querySelector(`.wpie-row[data-seg="${i}"]`);
    if (lr) lr.style.background = on ? "var(--panel-2, #1b2740)" : "";
  };
  const bindHover = (el) => {
    const i = +el.dataset.seg;
    const row = rows[i];
    if (!row) return;
    el.addEventListener("mouseenter", (e) => { highlight(i, true); showFleetTip(tipFor(row), e.clientX, e.clientY); });
    el.addEventListener("mousemove", (e) => showFleetTip(tipFor(row), e.clientX, e.clientY));
    el.addEventListener("mouseleave", () => { highlight(i, false); hideFleetTip(); });
  };
  segEls.forEach(bindHover);
  wrap.querySelectorAll(".wpie-row").forEach(bindHover);
  const rest = wrap.querySelector(".wpie-rest");
  if (rest) attachHoverTip(rest, () => `<b>${hidden.length} weitere</b><br>` + hidden.slice(0, 12).map((x) => `${esc(x.label)}: <b>${x.value}</b>`).join("<br>"));
}

// Säulendiagramm, 1x1-optimiert: max. 6 Säulen, kräftigere Schriften.
// Hover: Säule hervorheben, andere dimmen, Tooltip mit Label + exaktem Wert.
function renderColumn(target, widget, preset) {
  const all = (preset.rows ? preset.rows(state) : []);
  const rows = all.slice(0, 6), hidden = all.slice(6);
  // Feste 1x1-Box: die Säulen teilen sich die VOLLE Breite auf.
  const W = NAT_W, H = NAT_H;
  const padL = 30, padR = 4, padT = 13, padB = 20;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const niceMax = niceCeil(Math.max(1, ...rows.map((r) => r.value || 0)));
  const slot = rows.length ? plotW / rows.length : plotW;
  const bw = Math.max(10, slot - 9);
  const gridY = [0, 0.5, 1].map((f) => {
    const y = padT + plotH - f * plotH;
    return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-width="1" opacity="0.5"/>
      <text x="${padL - 4}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="11" fill="var(--subtext)">${fmtShort(niceMax * f, preset)}</text>`;
  }).join("");
  const bars = rows.map((row, i) => {
    const x = padL + i * slot + (slot - bw) / 2;
    const hh = ((row.value || 0) / niceMax) * plotH;
    const y = padT + plotH - hh;
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    const maxChars = Math.max(4, Math.floor(bw / 6.2));
    const label = String(row.label).length > maxChars ? String(row.label).slice(0, maxChars - 1) + "…" : row.label;
    return `<g class="wcol" data-i="${i}" style="cursor:pointer">
      <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, hh).toFixed(1)}"
        rx="3" fill="${color}" style="transition:opacity .12s"/>
      <text x="${(x + bw / 2).toFixed(1)}" y="${(y - 3).toFixed(1)}" text-anchor="middle" font-size="13" font-weight="700" fill="var(--text)">${esc(String(row.raw != null ? row.raw : Math.round(row.value || 0)))}</text>
      <text x="${(x + bw / 2).toFixed(1)}" y="${(padT + plotH + 12).toFixed(1)}" text-anchor="middle" font-size="12" fill="var(--subtext)">${esc(label)}</text>
    </g>`;
  }).join("");
  const wrap = document.createElement("div");
  wrap.dataset.stretch = "fill";
  wrap.style.cssText = "width:100%;height:100%;min-width:0;min-height:0";
  wrap.innerHTML = rows.length
    ? `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="none" style="display:block">
        ${gridY}
        <line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" stroke="var(--subtext)" stroke-width="1.5"/>
        ${bars}
        ${hidden.length ? `<text x="${W - padR}" y="${padT - 4}" text-anchor="end" font-size="8" fill="var(--subtext)">+${hidden.length} weitere</text>` : ""}
      </svg>`
    : `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`;
  target.appendChild(wrap);
  const cols = wrap.querySelectorAll(".wcol");
  cols.forEach((el) => {
    const row = rows[+el.dataset.i];
    const tip = () => `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(row.value))}`;
    el.addEventListener("mouseenter", (e) => {
      cols.forEach((c) => { if (c !== el) c.setAttribute("opacity", "0.4"); });
      showFleetTip(tip(), e.clientX, e.clientY);
    });
    el.addEventListener("mousemove", (e) => showFleetTip(tip(), e.clientX, e.clientY));
    el.addEventListener("mouseleave", () => { cols.forEach((c) => c.setAttribute("opacity", "")); hideFleetTip(); });
  });
}

// Rundet auf einen "schönen" oberen Achsenwert (1/2/5 * 10^n).
function niceCeil(v) {
  if (v <= 0) return 1;
  const exp = Math.floor(Math.log10(v));
  const base = Math.pow(10, exp);
  const f = v / base;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return nice * base;
}
// Kurzformat für Achsenbeschriftung (nutzt preset.format wenn vorhanden).
function fmtShort(v, preset) {
  if (preset && preset.format) { try { return preset.format(Math.round(v)); } catch {} }
  if (v >= 1e9) return (v / 1e9).toFixed(1) + "G";
  if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "k";
  return String(Math.round(v));
}

// Liste (Top-Zeilen), 1x1: max. 8 Zeilen. Hover: Zeile hervorheben + Tooltip
// mit Rang, Label und exaktem Wert der Zeile.
function renderList(target, widget, preset) {
  const all = (preset.rows ? preset.rows(state) : []);
  const rows = all.slice(0, 10), hidden = all.slice(10);
  const wrap = document.createElement("div");
  wrap.dataset.stretch = "fill";
  wrap.style.cssText = "width:100%;height:100%;display:flex;flex-direction:column;justify-content:center;gap:2px;box-sizing:border-box;padding:2px 8px;min-width:0;min-height:0;overflow:hidden";
  wrap.innerHTML = rows.map((row, i) => `
    <div class="wlist-row" data-i="${i}" style="display:grid;grid-template-columns:2.1em minmax(0,1fr) max-content;gap:8px;align-items:center;border-radius:6px;min-width:0">
      <span style="color:var(--subtext);text-align:right;font-variant-numeric:tabular-nums">${i + 1}.</span>
      <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(row.label)}</span>
      <span style="font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap">${esc(row.raw != null ? row.raw : String(row.value))}</span>
    </div>`).join("") || `<span style="color:var(--subtext);font-size:14px">Keine Daten</span>`;
  target.appendChild(wrap);
  const applySize = () => {
    if (!wrap.isConnected) { ro.disconnect(); return; }
    const n = Math.max(1, rows.length || 1);
    const h = Math.max(32, wrap.clientHeight || NAT_H);
    const w = Math.max(120, wrap.clientWidth || NAT_W);
    const gap = Math.max(1, Math.min(7, Math.floor(h / (n * 10))));
    const fs = Math.max(10, Math.min(20, Math.floor((h - gap * (n - 1) - 4) / n * 0.62), Math.floor(w / 32)));
    wrap.style.gap = gap + "px";
    wrap.querySelectorAll(".wlist-row").forEach((el) => { el.style.fontSize = fs + "px"; });
  };
  const ro = new ResizeObserver(applySize);
  ro.observe(wrap);
  requestAnimationFrame(applySize);
  if (hidden.length) wrap.appendChild(moreNote(hidden));
  wrap.querySelectorAll(".wlist-row").forEach((el) => {
    const row = rows[+el.dataset.i];
    bindRowHover(el, () => `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(row.value))} <span style="color:var(--subtext)">(Platz ${+el.dataset.i + 1})</span>`);
  });
}

// Interaktiver Flotten-Donut (Hover-Highlight + Tooltip mit Client-Liste).
function renderFleetDonut(target, widget, preset) {
  const segments = preset.segments ? preset.segments(state) : [];
  const box = document.createElement("div");
  box.style.cssText = `width:${NAT_W}px`;
  box.appendChild(buildFleetDonut(segments, { card: false, size: 88 }));
  target.appendChild(box);
}

// Ein einzelnes Kreisdiagramm mit Legende (für "overview").
function pieSection(rows, title) {
  rows = (rows || []).filter((r) => (r.value || 0) > 0);
  const total = rows.reduce((s, r) => s + r.value, 0);
  const size = 96, r = size / 2 - 2, cx = size / 2, cy = size / 2;
  let a0 = -Math.PI / 2, paths = "";
  rows.forEach((row, i) => {
    const frac = total ? row.value / total : 0;
    const a1 = a0 + frac * 2 * Math.PI;
    const large = frac > 0.5 ? 1 : 0;
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    paths += rows.length === 1
      ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}" class="wov-seg" data-seg="${i}" style="cursor:pointer;transition:opacity .12s"/>`
      : `<path d="M ${cx} ${cy} L ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z" fill="${color}" class="wov-seg" data-seg="${i}" style="cursor:pointer;transition:opacity .12s"/>`;
    a0 = a1;
  });
  const legend = rows.map((row, i) => {
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    const pct = total ? Math.round((row.value / total) * 100) : 0;
    return `<div class="wov-row" data-seg="${i}" style="display:flex;align-items:center;gap:6px;font-size:11px;padding:1px 3px;border-radius:6px;cursor:pointer">
      <span style="width:9px;height:9px;border-radius:3px;background:${color};flex:none"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(row.label)}</span>
      <span style="color:var(--subtext);font-variant-numeric:tabular-nums">${row.value} · ${pct}%</span>
    </div>`;
  }).join("");
  const el = document.createElement("div");
  el.style.cssText = "display:flex;gap:12px;align-items:center;margin-bottom:6px";
  el.innerHTML = `
    ${title ? `<div style="width:100%;font-size:10.5px;text-transform:uppercase;color:var(--subtext);letter-spacing:.03em">${esc(title)}</div>` : ""}
    <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="flex:none">${paths || `<circle cx="${cx}" cy="${cy}" r="${r}" fill="var(--border)"/>`}</svg>
    <div style="flex:1;min-width:0">${legend || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`}</div>`;
  // Per-Segment-Hover wie beim Pie.
  const segEls = el.querySelectorAll(".wov-seg");
  const bindHover = (node) => {
    const i = +node.dataset.seg;
    const row = rows[i];
    if (!row) return;
    const tip = () => `<b>${esc(row.label)}</b> — ${row.value} (${total ? Math.round((row.value / total) * 100) : 0}%)`;
    node.addEventListener("mouseenter", (e) => {
      segEls.forEach((s) => { if (+s.dataset.seg !== i) s.setAttribute("opacity", "0.4"); });
      const lr = el.querySelector(`.wov-row[data-seg="${i}"]`);
      if (lr) lr.style.background = "var(--panel-2, #1b2740)";
      showFleetTip(tip(), e.clientX, e.clientY);
    });
    node.addEventListener("mousemove", (e) => showFleetTip(tip(), e.clientX, e.clientY));
    node.addEventListener("mouseleave", () => {
      segEls.forEach((s) => s.setAttribute("opacity", ""));
      const lr = el.querySelector(`.wov-row[data-seg="${i}"]`);
      if (lr) lr.style.background = "";
      hideFleetTip();
    });
  };
  segEls.forEach(bindHover);
  el.querySelectorAll(".wov-row").forEach(bindHover);
  return el;
}

// Zusammengesetzte "Flotten-Übersicht": mehrere Kreisdiagramme untereinander.
function renderOverview(target, widget, preset) {
  const sections = preset.sections ? preset.sections(state) : [];
  const wrap = document.createElement("div");
  wrap.style.cssText = `display:flex;flex-direction:column;gap:8px;width:${NAT_W}px`;
  if (!sections.length) wrap.innerHTML = `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`;
  for (const sec of sections) wrap.appendChild(pieSection(sec.rows, sec.title));
  target.appendChild(wrap);
}

// Horizontale Balken, 1x1: max. 8 Zeilen. Hover: Zeile hervorheben + Tooltip
// mit Label + exaktem Wert (+ Anteil am Maximum).
function renderBar(target, widget, preset) {
  const all = (preset.rows ? preset.rows(state) : []);
  const rows = all.slice(0, 8), hidden = all.slice(8);
  const max = Math.max(1, ...rows.map((r) => r.value || 0));
  const wrap = document.createElement("div");
  wrap.dataset.stretch = "fill";
  wrap.className = "widget-bars";
  wrap.style.cssText = "width:100%;height:100%;display:flex;flex-direction:column;justify-content:center;box-sizing:border-box;padding:2px 6px;min-width:0;min-height:0;overflow:hidden";
  wrap.innerHTML = rows.map((row, i) => {
    const pct = ((row.value || 0) / max) * 100;
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    return `<div class="wbar-row" data-i="${i}" style="width:100%;display:grid;grid-template-columns:72px minmax(70px, 1fr) max-content;align-items:center;gap:8px;box-sizing:border-box;min-width:0;min-height:0">
      <span class="wbar-label" style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--subtext)">${esc(row.label)}</span>
      <span class="wbar-track" style="display:block;min-width:0;height:10px;border-radius:999px;background:var(--panel-2);overflow:hidden"><span class="wbar-fill" style="display:block;height:100%;width:${pct.toFixed(1)}%;background:${color};border-radius:999px"></span></span>
      <span class="wbar-val" style="text-align:right;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap">${esc(row.raw != null ? row.raw : String(row.value))}</span>
    </div>`;
  }).join("") || `<span style="color:var(--subtext);font-size:14px">Keine Daten</span>`;
  target.appendChild(wrap);
  const applySize = () => {
    if (!wrap.isConnected) { ro.disconnect(); return; }
    const n = Math.max(1, rows.length || 1);
    const h = Math.max(32, wrap.clientHeight || NAT_H);
    const w = Math.max(120, wrap.clientWidth || NAT_W);
    const gap = Math.max(1, Math.min(7, Math.floor(h / (n * 8))));
    const fs = Math.max(10, Math.min(18, Math.floor((h - gap * (n - 1) - 4) / n * 0.55), Math.floor(w / 34)));
    const th = Math.max(7, Math.min(14, Math.floor((h - gap * (n - 1) - 4) / n * 0.34)));
    const labelW = Math.max(58, Math.min(170, Math.floor(w * 0.24)));
    const valueW = Math.max(34, Math.min(90, Math.floor(w * 0.14)));
    wrap.style.gap = gap + "px";
    wrap.querySelectorAll(".wbar-row").forEach((el) => {
      el.style.gridTemplateColumns = `${labelW}px minmax(70px, 1fr) ${valueW}px`;
      el.style.fontSize = fs + "px";
      const tr = el.querySelector(".wbar-track");
      if (tr) tr.style.height = th + "px";
    });
  };
  const ro = new ResizeObserver(applySize);
  ro.observe(wrap);
  requestAnimationFrame(applySize);
  if (hidden.length) wrap.appendChild(moreNote(hidden));
  wrap.querySelectorAll(".wbar-row").forEach((rowEl) => {
    const row = rows[+rowEl.dataset.i];
    if (!row) return;
    bindRowHover(rowEl, () => `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(row.value))} <span style="color:var(--subtext)">(${Math.round(((row.value || 0) / max) * 100)}% vom Maximum)</span>`);
  });
}

// Tabelle, 1x1: max. 8 Zeilen + Hinweiszeile. Hover: Zeile hervorheben +
// Tooltip mit Client + exaktem Wert.
function renderTable(target, widget, preset) {
  const all = preset.rows ? preset.rows(state) : [];
  const rows = all.slice(0, 7), hidden = all.slice(7);
  const box = document.createElement("div");
  box.dataset.stretch = "fill";
  box.style.cssText = "width:100%;height:100%;display:flex;flex-direction:column;justify-content:center;box-sizing:border-box;padding:2px 8px;min-width:0;min-height:0;overflow:hidden";
  const tbl = document.createElement("table");
  tbl.className = "widget-table";
  tbl.style.cssText = "width:100%;border-collapse:collapse;table-layout:fixed;line-height:1.12";
  tbl.innerHTML = `
    <thead><tr><th style="font-size:.66em;letter-spacing:.04em;text-align:left;padding:.15em .45em;color:var(--subtext);text-transform:uppercase">Client</th><th style="font-size:.66em;letter-spacing:.04em;text-align:right;padding:.15em .45em;color:var(--subtext);text-transform:uppercase">${esc(preset.label.replace(/ je Client$/, ""))}</th></tr></thead>
    <tbody>${rows.map((r, i) => `<tr data-i="${i}"><td style="padding:.18em .45em;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.label)}</td><td style="padding:.18em .45em;text-align:right;font-weight:800;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.raw != null ? r.raw : String(r.value))}</td></tr>`).join("") || `<tr><td colspan="2" style="color:var(--subtext);padding:.35em .45em">Keine Daten</td></tr>`}</tbody>`;
  box.appendChild(tbl);
  target.appendChild(box);
  const applySize = () => {
    if (!box.isConnected) { ro.disconnect(); return; }
    const n = Math.max(1, rows.length + 1);
    const h = Math.max(32, box.clientHeight || NAT_H);
    const w = Math.max(120, box.clientWidth || NAT_W);
    const fs = Math.max(10, Math.min(20, Math.floor(h / n * 0.7), Math.floor(w / 32)));
    tbl.style.fontSize = fs + "px";
  };
  const ro = new ResizeObserver(applySize);
  ro.observe(box);
  requestAnimationFrame(applySize);
  if (hidden.length) box.appendChild(moreNote(hidden));
  tbl.querySelectorAll("tbody tr[data-i]").forEach((tr) => {
    const row = rows[+tr.dataset.i];
    if (!row) return;
    bindRowHover(tr, () => `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(row.value))}`);
  });
}

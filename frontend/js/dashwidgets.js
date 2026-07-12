// dashwidgets.js
// --------------
// Rendert ein einzelnes Dashboard-Widget in verschiedenen Darstellungen.
// Ein Widget = { id, preset, kind, title?, w, text? }.
//   - preset: id aus metriccatalog.js (entfällt bei kind "text")
//   - kind:   "number" | "donut" | "gauge" | "line" | "pie" | "bar" | "table" | "text"
//   - w:      Breite in Grid-Spalten (1..12)
//   - text:   freier Text (nur kind "text"; im Edit-Modus editierbar)
//
// Für Linien-Charts wird pro Widget eine kleine rollierende Historie gehalten
// (im Speicher), die bei jedem Metrik-Tick fortgeschrieben wird.

import { state } from "./state.js";
import { esc } from "./utils.js";
import { presetById } from "./metriccatalog.js";

const PIE_COLORS = [
  "#4da6ff", "#3ecf8e", "#f5a524", "#ff4d6d", "#a78bfa",
  "#22d3ee", "#fb923c", "#e879f9", "#84cc16", "#f43f5e",
  "#2dd4bf", "#facc15", "#60a5fa", "#c084fc", "#34d399",
];

// Rollierende Historie je Widget-Instanz (id -> {ts:[], v:[]}), max N Punkte.
const _history = new Map();
const MAX_POINTS = 120;

export function pushWidgetHistory(widget) {
  if (widget.kind !== "line") return;
  const p = presetById(widget.preset);
  if (!p || !p.value) return;
  const h = _history.get(widget.id) || { ts: [], v: [] };
  h.ts.push(Date.now());
  h.v.push(p.value(state) || 0);
  if (h.ts.length > MAX_POINTS) { h.ts.shift(); h.v.shift(); }
  _history.set(widget.id, h);
}

export function formatValue(preset, v) {
  if (preset.format) return preset.format(v);
  if (preset.unit === "%") return `${Math.round(v)}%`;
  return String(v);
}

// Haupt-Renderer: füllt ein Ziel-Element mit dem Widget-Inhalt.
export function renderWidgetBody(target, widget) {
  target.innerHTML = "";
  if (widget.kind === "text") return renderText(target, widget);

  const preset = presetById(widget.preset);
  if (!preset) { target.innerHTML = `<div style="color:var(--subtext);font-size:12px">Unbekannte Metrik.</div>`; return; }

  switch (widget.kind) {
    case "number": return renderNumber(target, widget, preset);
    case "donut": return renderDonut(target, widget, preset);
    case "gauge": return renderGauge(target, widget, preset);
    case "line": return renderLine(target, widget, preset);
    case "pie": return renderPie(target, widget, preset);
    case "bar": return renderBar(target, widget, preset);
    case "table": return renderTable(target, widget, preset);
    default: target.innerHTML = `<div style="color:var(--subtext)">${esc(widget.kind)}?</div>`;
  }
}

// Titel eines Widgets (frei überschreibbar).
export function widgetTitle(widget) {
  if (widget.title) return widget.title;
  if (widget.kind === "text") return "Text";
  const p = presetById(widget.preset);
  return p ? p.label : widget.preset;
}

// -------------------- Darstellungen --------------------

function renderText(target, widget) {
  const div = document.createElement("div");
  div.className = "widget-text";
  div.innerHTML = esc(widget.text || "Text…").replace(/\n/g, "<br>");
  target.appendChild(div);
}

function renderNumber(target, widget, preset) {
  const v = preset.value ? preset.value(state) : 0;
  const box = document.createElement("div");
  box.className = "widget-number";
  box.innerHTML = `<div class="wn-value">${esc(formatValue(preset, v))}</div>
    <div class="wn-label">${esc(preset.label)}</div>`;
  target.appendChild(box);
}

function donutSvg(pct, valueText, subText, color = "var(--accent)") {
  const size = 150, stroke = 20, r = (size - stroke) / 2 - 2, cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r;
  const len = Math.max(0, Math.min(1, pct / 100)) * C;
  return `
    <div style="position:relative;width:${size}px;height:${size}px">
      <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"/>
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
          stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-linecap="round"
          transform="rotate(-90 ${cx} ${cy})"/>
      </svg>
      <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center">
        <div style="font-size:24px;font-weight:700;line-height:1">${esc(valueText)}</div>
        ${subText ? `<div style="font-size:10px;color:var(--subtext);margin-top:3px;text-align:center;padding:0 8px">${esc(subText)}</div>` : ""}
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
    // Verteilung -> Donut aus den zwei/mehr größten Segmenten (erste Kategorie).
    const rows = preset.rows(state);
    const total = rows.reduce((s, r) => s + (r.value || 0), 0);
    return renderPie(target, widget, preset);  // Pie ist hier die bessere Darstellung
  } else {
    const v = preset.value(state);
    pct = preset.max ? (v / preset.max) * 100 : v;
    valueText = formatValue(preset, v);
  }
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;justify-content:center;padding:6px";
  wrap.innerHTML = donutSvg(pct, valueText, sub);
  target.appendChild(wrap);
}

function renderGauge(target, widget, preset) {
  const v = preset.value(state);
  const max = preset.max || 100;
  const pct = Math.max(0, Math.min(100, (v / max) * 100));
  // Halbkreis-Gauge
  const w = 200, h = 116, cx = w / 2, cy = h - 8, r = 88, stroke = 16;
  const ang = Math.PI * (pct / 100);           // 0..π
  const ex = cx - r * Math.cos(ang), ey = cy - r * Math.sin(ang);
  const color = pct > 85 ? "#ff4d6d" : pct > 65 ? "#f5a524" : "#3ecf8e";
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;flex-direction:column;align-items:center;padding:4px";
  wrap.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">
      <path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}" fill="none" stroke="var(--border)" stroke-width="${stroke}" stroke-linecap="round"/>
      <path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${ex.toFixed(2)} ${ey.toFixed(2)}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"/>
    </svg>
    <div style="font-size:22px;font-weight:700;margin-top:-10px">${esc(formatValue(preset, v))}</div>
    <div style="font-size:11px;color:var(--subtext)">${esc(preset.label)}</div>`;
  target.appendChild(wrap);
}

function renderLine(target, widget, preset) {
  const h = _history.get(widget.id) || { ts: [], v: [] };
  const cur = preset.value ? preset.value(state) : 0;
  if (!h.v.length) { pushWidgetHistory(widget); }
  const data = (_history.get(widget.id) || { v: [cur] }).v;
  const w = 320, hgt = 130, pad = 6;
  const max = Math.max(1, ...data, preset.max || 0);
  const min = Math.min(...data, 0);
  const span = max - min || 1;
  const pts = data.map((val, i) => {
    const x = pad + (data.length <= 1 ? 0 : (i / (data.length - 1)) * (w - 2 * pad));
    const y = hgt - pad - ((val - min) / span) * (hgt - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
      <span style="font-size:11px;color:var(--subtext)">${esc(preset.label)}</span>
      <span style="font-size:16px;font-weight:700">${esc(formatValue(preset, cur))}</span>
    </div>
    <svg viewBox="0 0 ${w} ${hgt}" width="100%" height="${hgt}" preserveAspectRatio="none" style="overflow:visible">
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>`;
  target.appendChild(wrap);
}

function renderPie(target, widget, preset) {
  const rows = (preset.rows ? preset.rows(state) : []).filter((r) => (r.value || 0) > 0);
  const total = rows.reduce((s, r) => s + r.value, 0);
  const size = 150, r = size / 2 - 2, cx = size / 2, cy = size / 2;
  let a0 = -Math.PI / 2;
  let paths = "";
  rows.forEach((row, i) => {
    const frac = row.value / total;
    const a1 = a0 + frac * 2 * Math.PI;
    const large = frac > 0.5 ? 1 : 0;
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    if (rows.length === 1) {
      paths += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}"/>`;
    } else {
      paths += `<path d="M ${cx} ${cy} L ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z" fill="${color}"/>`;
    }
    a0 = a1;
  });
  const legend = rows.map((row, i) => {
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    const pct = total ? Math.round((row.value / total) * 100) : 0;
    return `<div style="display:flex;align-items:center;gap:6px;font-size:12px;padding:2px 0">
      <span style="width:10px;height:10px;border-radius:3px;background:${color};flex:none"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(row.label)}</span>
      <span style="color:var(--subtext);font-variant-numeric:tabular-nums">${row.value} · ${pct}%</span>
    </div>`;
  }).join("");
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;gap:14px;align-items:center;flex-wrap:wrap";
  wrap.innerHTML = `
    <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="flex:none">${paths || `<circle cx="${cx}" cy="${cy}" r="${r}" fill="var(--border)"/>`}</svg>
    <div style="flex:1;min-width:140px">${legend || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`}</div>`;
  target.appendChild(wrap);
}

function renderBar(target, widget, preset) {
  const rows = (preset.rows ? preset.rows(state) : []).slice(0, 20);
  const max = Math.max(1, ...rows.map((r) => r.value || 0));
  const wrap = document.createElement("div");
  wrap.className = "widget-bars";
  wrap.innerHTML = rows.map((row, i) => {
    const pct = ((row.value || 0) / max) * 100;
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    return `<div class="wbar-row">
      <span class="wbar-label" title="${esc(row.label)}">${esc(row.label)}</span>
      <span class="wbar-track"><span class="wbar-fill" style="width:${pct.toFixed(1)}%;background:${color}"></span></span>
      <span class="wbar-val">${esc(row.raw != null ? row.raw : String(row.value))}</span>
    </div>`;
  }).join("") || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`;
  target.appendChild(wrap);
}

function renderTable(target, widget, preset) {
  const rows = preset.rows ? preset.rows(state) : [];
  const tbl = document.createElement("table");
  tbl.className = "widget-table";
  tbl.innerHTML = `
    <thead><tr><th>Client</th><th style="text-align:right">${esc(preset.label.replace(/ je Client$/, ""))}</th></tr></thead>
    <tbody>${rows.map((r) => `<tr><td>${esc(r.label)}</td><td style="text-align:right">${esc(r.raw != null ? r.raw : String(r.value))}</td></tr>`).join("") || `<tr><td colspan="2" style="color:var(--subtext)">Keine Daten</td></tr>`}</tbody>`;
  target.appendChild(tbl);
}

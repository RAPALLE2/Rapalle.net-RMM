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
import { setHostScope, clearHostScope } from "./metriccatalog.js";
import { buildFleetDonut, showFleetTip, hideFleetTip, attachHoverTip, fitToContainer } from "./fleetcharts.js";

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
  // - kategorische Verteilung (rows): pie <-> donut
  // - Einzelwert (value): gauge <-> donut <-> ring
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

  // Pro-Widget-Zählweise: "physical" = nur physische Geräte, sonst alle
  // (VMs/LXCs zählen mit). Gilt für die gesamte Datenberechnung dieses Widgets.
  setHostScope(widget.scope === "physical" ? "physical" : "all");
  try {
    renderWidgetInner(target, widget, preset);
  } finally {
    clearHostScope();
  }
}

function renderWidgetInner(target, widget, preset) {
  // Alle Diagramme in einen Fit-Holder rendern, der den (fixe-Größe-)Inhalt
  // proportional schrumpft, wenn das Widget kleiner ist als der natürliche
  // Diagramminhalt (z.B. 2x1 -> 1x1). Vergrößert wird nie.
  const holder = document.createElement("div");
  holder.className = "widget-fit-holder";
  holder.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:visible";
  target.appendChild(holder);
  const out = holder;

  switch (widget.kind) {
    case "number": renderNumber(out, widget, preset); break;
    case "donut": renderDonut(out, widget, preset); break;
    case "gauge": renderGauge(out, widget, preset); break;
    case "line": renderLine(out, widget, preset); break;
    case "pie": renderPie(out, widget, preset); fitToContainer(holder); break;
    case "bar": renderBar(out, widget, preset); fitToContainer(holder); break;
    case "table": renderTable(out, widget, preset); fitToContainer(holder); break;
    case "area": renderArea(out, widget, preset); break;
    case "spark": renderSpark(out, widget, preset); break;
    case "progress": renderProgress(out, widget, preset); break;
    case "ring": renderRing(out, widget, preset); break;
    case "stat": renderStat(out, widget, preset); break;
    case "column": renderColumn(out, widget, preset); fitToContainer(holder); break;
    case "list": renderList(out, widget, preset); fitToContainer(holder); break;
    case "overview": renderOverview(out, widget, preset); break;
    case "fleetdonut": renderFleetDonut(out, widget, preset); fitToContainer(holder); break;
    default: out.innerHTML = `<div style="color:var(--subtext)">${esc(widget.kind)}?</div>`; return;
  }
  // Wert-/Kreisdiagramme ebenfalls einpassen (Donut/Gauge/Ring haben feste
  // SVG-Größen und würden sonst in 1x1-Zellen überlaufen).
  if (["donut", "gauge", "ring", "fleetdonut", "number", "stat"].includes(widget.kind)) fitToContainer(holder);
  // Hover wie in der Flotten-Übersicht: Tooltip mit exaktem Wert (live) für
  // die einfachen Wert-Widgets. Pie/Bar/FleetDonut haben eigene, feinere
  // Hover-Effekte pro Segment/Zeile (siehe unten). Guard: renderWidgetBody
  // läuft bei jedem Metrik-Tick - Listener nur EINMAL pro Element anhängen.
  if (!target._hoverTipAttached) {
    target._hoverTipAttached = true;
    attachHoverTip(target, () => {
      const p = presetById(target._hoverPreset || widget.preset) || preset;
      setHostScope(target._hoverScope === "physical" ? "physical" : "all");
      let v, max;
      try {
        v = p.value ? p.value(state) : null;
        max = typeof p.max === "number" ? ` <span style="color:var(--subtext)">/ ${esc(formatValue(p, p.max))}</span>` : "";
      } finally { clearHostScope(); }
      const val = v === null || v === undefined ? "—" : formatValue(p, v);
      return `<b>${esc(widget.title || p.label)}</b><br>${esc(val)}${max}`;
    });
  }
  target._hoverPreset = widget.preset;
  target._hoverScope = widget.scope || "all";
}

// Titel eines Widgets (frei überschreibbar).
export function widgetTitle(widget) {
  if (widget.title) return widget.title;
  if (widget.kind === "text") return "Text";
  const p = presetById(widget.preset);
  return p ? p.label : widget.preset;
}

// -------------------- Darstellungen --------------------

// ---- Verlaufspunkte fuer area/spark/stat (gleiche Quelle wie "line") ----
function widgetSeries(widget, preset) {
  if (!_history.get(widget.id) || !_history.get(widget.id).v.length) pushWidgetHistory(widget);
  const cur = preset.value ? preset.value(state) : 0;
  return (_history.get(widget.id) || { v: [cur ?? 0] }).v;
}
function polyPoints(data, w, hgt, pad, max, min) {
  const span = (max - min) || 1;
  return data.map((val, i) => {
    const x = pad + (data.length <= 1 ? 0 : (i / (data.length - 1)) * (w - 2 * pad));
    const y = hgt - pad - ((val - min) / span) * (hgt - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

// Fläche (gefüllter Verlauf)
function renderArea(target, widget, preset) {
  const data = widgetSeries(widget, preset);
  const cur = preset.value ? preset.value(state) : 0;
  const w = 320, hgt = 110, pad = 6;
  const max = Math.max(1, ...data, preset.max || 0);
  const min = Math.min(...data, 0);
  const pts = polyPoints(data, w, hgt, pad, max, min);
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
      <span style="font-size:11px;color:var(--subtext)">${esc(preset.label)}</span>
      <span style="font-size:16px;font-weight:700">${esc(formatValue(preset, cur))}</span>
    </div>
    <svg viewBox="0 0 ${w} ${hgt}" width="100%" height="${hgt}" preserveAspectRatio="none">
      <polygon points="${pad},${hgt - pad} ${pts} ${w - pad},${hgt - pad}" fill="var(--accent)" opacity="0.22"/>
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>`;
  target.appendChild(wrap);
}

// Sparkline (minimal, ohne Achsen)
function renderSpark(target, widget, preset) {
  const data = widgetSeries(widget, preset);
  const cur = preset.value ? preset.value(state) : 0;
  const w = 320, hgt = 56, pad = 4;
  const max = Math.max(1, ...data), min = Math.min(...data, 0);
  const pts = polyPoints(data, w, hgt, pad, max, min);
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div style="font-size:22px;font-weight:700;line-height:1.2">${esc(formatValue(preset, cur))}</div>
    <div style="font-size:11px;color:var(--subtext);margin-bottom:2px">${esc(preset.label)}</div>
    <svg viewBox="0 0 ${w} ${hgt}" width="100%" height="${hgt}" preserveAspectRatio="none">
      <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    </svg>`;
  target.appendChild(wrap);
}

// Fortschrittsbalken (Wert / Maximum)
function renderProgress(target, widget, preset) {
  const v = preset.value ? preset.value(state) : 0;
  const max = typeof preset.max === "number" ? preset.max : 100;
  const pct = Math.max(0, Math.min(100, ((v || 0) / (max || 1)) * 100));
  const color = pct > 85 ? "#ff4d6d" : pct > 65 ? "#f5a524" : "var(--accent)";
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
      <span style="font-size:12px;color:var(--subtext)">${esc(preset.label)}</span>
      <span style="font-size:18px;font-weight:700">${esc(formatValue(preset, v))}</span>
    </div>
    <div style="height:14px;border-radius:7px;background:var(--panel-2);overflow:hidden">
      <div style="height:100%;width:${pct.toFixed(1)}%;background:${color};border-radius:7px;transition:width .3s"></div>
    </div>
    <div style="font-size:10px;color:var(--subtext);margin-top:4px;text-align:right">${Math.round(pct)}% von ${esc(formatValue(preset, max))}</div>`;
  target.appendChild(wrap);
}

// Ring (dünner Donut)
function renderRing(target, widget, preset) {
  const v = preset.value ? preset.value(state) : 0;
  const max = typeof preset.max === "number" ? preset.max : 100;
  const pct = Math.max(0, Math.min(100, ((v || 0) / (max || 1)) * 100));
  const size = 110, stroke = 9, r = (size - stroke) / 2 - 2, cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r, len = (pct / 100) * C;
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;justify-content:center;padding:2px";
  wrap.innerHTML = `
    <div style="position:relative;width:${size}px;height:${size}px">
      <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"/>
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--accent)" stroke-width="${stroke}"
          stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})"/>
      </svg>
      <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center">
        <div style="font-size:17px;font-weight:700;line-height:1">${esc(formatValue(preset, v))}</div>
        <div style="font-size:9px;color:var(--subtext);margin-top:2px">${Math.round(pct)}%</div>
      </div>
    </div>`;
  target.appendChild(wrap);
}

// Stat: großer Wert + Trendpfeil (Vergleich mit Verlaufsanfang) + Mini-Spark
function renderStat(target, widget, preset) {
  const data = widgetSeries(widget, preset);
  const cur = preset.value ? preset.value(state) : 0;
  const ref = data.length > 1 ? data[0] : cur;
  const diff = (cur || 0) - (ref || 0);
  const up = diff > 0, flat = Math.abs(diff) < 1e-9;
  const trendColor = flat ? "var(--subtext)" : (up ? "#f5a524" : "#3ecf8e");
  const w = 140, hgt = 34, pad = 3;
  const max = Math.max(1, ...data), min = Math.min(...data, 0);
  const pts = polyPoints(data, w, hgt, pad, max, min);
  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
      <div>
        <div style="font-size:26px;font-weight:700;line-height:1.1">${esc(formatValue(preset, cur))}</div>
        <div style="font-size:11px;color:var(--subtext)">${esc(preset.label)}</div>
      </div>
      <div style="text-align:right;flex:1;min-width:90px">
        <div style="font-size:13px;font-weight:600;color:${trendColor}">${flat ? "→" : (up ? "▲" : "▼")} ${esc(formatValue(preset, Math.abs(diff)))}</div>
        <svg viewBox="0 0 ${w} ${hgt}" width="${w}" height="${hgt}" preserveAspectRatio="none">
          <polyline points="${pts}" fill="none" stroke="${trendColor}" stroke-width="2"/>
        </svg>
      </div>
    </div>`;
  target.appendChild(wrap);
}

// Säulen (vertikale Balken je Zeile)
function renderColumn(target, widget, preset) {
  const rows = (preset.rows ? preset.rows(state) : []).slice(0, 12);
  // Echtes Säulendiagramm: SVG mit Grundlinie, y-Gitterlinien, Wertbeschriftung
  // über jeder Säule und Kategorie-Label darunter.
  const W = 40 + rows.length * 46, H = 190;
  const padL = 34, padR = 10, padT = 18, padB = 34;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(1, ...rows.map((r) => r.value || 0));
  const bw = rows.length ? Math.min(40, plotW / rows.length - 10) : 20;
  const niceMax = niceCeil(max);
  const gridY = [0, 0.25, 0.5, 0.75, 1].map((f) => {
    const y = padT + plotH - f * plotH;
    return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-width="1" opacity="0.5"/>
      <text x="${padL - 5}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--subtext)">${fmtShort(niceMax * f, preset)}</text>`;
  }).join("");
  const bars = rows.map((row, i) => {
    const x = padL + i * (plotW / rows.length) + (plotW / rows.length - bw) / 2;
    const h = ((row.value || 0) / niceMax) * plotH;
    const y = padT + plotH - h;
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    const label = String(row.label).length > 8 ? String(row.label).slice(0, 7) + "…" : row.label;
    return `<g class="wcol" data-i="${i}" style="cursor:pointer">
      <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, h).toFixed(1)}"
        rx="3" fill="${color}" style="transition:opacity .12s"/>
      <text x="${(x + bw / 2).toFixed(1)}" y="${(y - 4).toFixed(1)}" text-anchor="middle" font-size="10" font-weight="600" fill="var(--text)">${esc(String(row.raw != null ? row.raw : Math.round(row.value || 0)))}</text>
      <text x="${(x + bw / 2).toFixed(1)}" y="${(padT + plotH + 14).toFixed(1)}" text-anchor="middle" font-size="9" fill="var(--subtext)">${esc(label)}</text>
    </g>`;
  }).join("");
  const wrap = document.createElement("div");
  wrap.innerHTML = rows.length
    ? `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="max-width:100%">
        ${gridY}
        <line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" stroke="var(--subtext)" stroke-width="1.5"/>
        ${bars}
      </svg>`
    : `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`;
  target.appendChild(wrap);
  wrap.querySelectorAll(".wcol").forEach((el) => {
    const row = rows[+el.dataset.i];
    const rect = el.querySelector("rect");
    const tip = () => `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(row.value))}`;
    el.addEventListener("mouseenter", (e) => { rect.setAttribute("opacity", "0.8"); showFleetTip(tip(), e.clientX, e.clientY); });
    el.addEventListener("mousemove", (e) => showFleetTip(tip(), e.clientX, e.clientY));
    el.addEventListener("mouseleave", () => { rect.setAttribute("opacity", "1"); hideFleetTip(); });
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

// Liste (Top-Zeilen als Text)
function renderList(target, widget, preset) {
  const rows = (preset.rows ? preset.rows(state) : []).slice(0, 10);
  const wrap = document.createElement("div");
  wrap.innerHTML = rows.map((row, i) => `
    <div style="display:flex;gap:8px;align-items:center;font-size:12px;padding:3px 4px;border-radius:6px">
      <span style="color:var(--subtext);width:16px;text-align:right;font-variant-numeric:tabular-nums">${i + 1}.</span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(row.label)}</span>
      <span style="font-weight:600;font-variant-numeric:tabular-nums">${esc(row.raw != null ? row.raw : String(row.value))}</span>
    </div>`).join("") || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`;
  target.appendChild(wrap);
}

// Interaktiver Flotten-Donut (Hover-Highlight + Tooltip mit Client-Liste) -
// dieselbe Darstellung wie die frühere feste Flotten-Übersicht im Dashboard.
function renderFleetDonut(target, widget, preset) {
  const segments = preset.segments ? preset.segments(state) : [];
  // Kompakt (size 92): passt inkl. Legende in EINE Raster-Höheneinheit (150px).
  target.appendChild(buildFleetDonut(segments, { card: false, size: 92 }));
}

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
    // Kategorische Verteilung als ECHTER mehrsegmentiger Donut (Ring mit
    // Legende + Hover), nicht als Pie-Fallback.
    const segs = (preset.rows(state) || []).filter((r) => (r.value || 0) > 0)
      .map((r, i) => ({ label: r.label, count: r.value, color: r.color || PIE_COLORS[i % PIE_COLORS.length], items: r.items }));
    target.appendChild(buildFleetDonut(segs, { card: false, size: 150 }));
    return;
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
      paths += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}" class="wpie-seg" data-seg="${i}" style="cursor:pointer;transition:opacity .12s"/>`;
    } else {
      paths += `<path d="M ${cx} ${cy} L ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z" fill="${color}" class="wpie-seg" data-seg="${i}" style="cursor:pointer;transition:opacity .12s"/>`;
    }
    a0 = a1;
  });
  const legend = rows.map((row, i) => {
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    const pct = total ? Math.round((row.value / total) * 100) : 0;
    return `<div class="wpie-row" data-seg="${i}" style="display:flex;align-items:center;gap:6px;font-size:12px;padding:2px 0;border-radius:6px;cursor:pointer">
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

  // Hover wie in der Flotten-Übersicht: Segment/Legende hervorheben, Rest
  // abdunkeln, Tooltip mit Wert + Prozent (und Client-Liste, falls vorhanden).
  const segEls = wrap.querySelectorAll(".wpie-seg");
  const rowEls = wrap.querySelectorAll(".wpie-row");
  const tipFor = (row) => {
    const pctT = total ? Math.round(((row.value || 0) / total) * 100) : 0;
    const items = row.items ? `<br><span style="color:var(--subtext)">${row.items.slice(0, 12).map((h) => `• ${esc(h)}`).join("<br>")}${row.items.length > 12 ? `<br>… und ${row.items.length - 12} weitere` : ""}</span>` : "";
    return `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(row.value))} (${pctT}%)${items}`;
  };
  const highlight = (i, on) => {
    segEls.forEach((s) => s.setAttribute("opacity", on && +s.dataset.seg !== i ? "0.45" : ""));
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
  rowEls.forEach(bindHover);
}

// Ein einzelnes Kreisdiagramm mit Legende (wiederverwendet für Pie & Overview).
function pieSection(rows, title) {
  rows = (rows || []).filter((r) => (r.value || 0) > 0);
  const total = rows.reduce((s, r) => s + r.value, 0);
  const size = 120, r = size / 2 - 2, cx = size / 2, cy = size / 2;
  let a0 = -Math.PI / 2, paths = "";
  rows.forEach((row, i) => {
    const frac = total ? row.value / total : 0;
    const a1 = a0 + frac * 2 * Math.PI;
    const large = frac > 0.5 ? 1 : 0;
    const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    paths += rows.length === 1
      ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}"/>`
      : `<path d="M ${cx} ${cy} L ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)} Z" fill="${color}"/>`;
    a0 = a1;
  });
  const legend = rows.map((row, i) => {
    const color = row.color || PIE_COLORS[i % PIE_COLORS.length];
    const pct = total ? Math.round((row.value / total) * 100) : 0;
    return `<div style="display:flex;align-items:center;gap:6px;font-size:11.5px;padding:1px 0">
      <span style="width:9px;height:9px;border-radius:3px;background:${color};flex:none"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(row.label)}</span>
      <span style="color:var(--subtext);font-variant-numeric:tabular-nums">${row.value} \u00b7 ${pct}%</span>
    </div>`;
  }).join("");
  const el = document.createElement("div");
  el.style.cssText = "display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:6px";
  el.innerHTML = `
    ${title ? `<div style="width:100%;font-size:11px;text-transform:uppercase;color:var(--subtext);letter-spacing:.03em">${esc(title)}</div>` : ""}
    <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="flex:none">${paths || `<circle cx="${cx}" cy="${cy}" r="${r}" fill="var(--border)"/>`}</svg>
    <div style="flex:1;min-width:130px">${legend || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`}</div>`;
  return el;
}

// Zusammengesetzte "Flotten-Übersicht": mehrere Kreisdiagramme untereinander.
function renderOverview(target, widget, preset) {
  const sections = preset.sections ? preset.sections(state) : [];
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;flex-direction:column;gap:10px;width:100%";
  if (!sections.length) wrap.innerHTML = `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`;
  for (const sec of sections) wrap.appendChild(pieSection(sec.rows, sec.title));
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

  // Hover wie in der Flotten-Übersicht: Zeile hervorheben + Tooltip mit
  // exaktem Wert (und Anteil am Maximum).
  wrap.querySelectorAll(".wbar-row").forEach((rowEl, i) => {
    const row = rows[i];
    if (!row) return;
    const tip = () => `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(row.value))} <span style="color:var(--subtext)">(${Math.round(((row.value || 0) / max) * 100)}% vom Maximum)</span>`;
    rowEl.style.borderRadius = "6px";
    rowEl.addEventListener("mouseenter", (e) => { rowEl.style.background = "var(--panel-2, #1b2740)"; showFleetTip(tip(), e.clientX, e.clientY); });
    rowEl.addEventListener("mousemove", (e) => showFleetTip(tip(), e.clientX, e.clientY));
    rowEl.addEventListener("mouseleave", () => { rowEl.style.background = ""; hideFleetTip(); });
  });
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

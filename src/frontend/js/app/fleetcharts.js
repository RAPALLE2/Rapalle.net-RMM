// fleetcharts.js
// --------------
// Gemeinsame Bausteine für die Flotten-Übersicht: das interaktive
// Donut-Diagramm (Hover-Highlight + Tooltip mit Client-Liste) und der
// groupBy-Helfer. Wird vom Dashboard (dashboard.js) UND von den
// Flotten-Widgets (dashwidgets.js, kind "fleetdonut") benutzt - so ist die
// Flotten-Übersicht als frei verschieb-/editier-/löschbares Widget verfügbar.

import { esc } from "./utils.js";
import { t } from "./i18n.js";

export const FLEET_PALETTE = [
  "#4da6ff", "#3ecf8e", "#f5a524", "#ff4d6d", "#a78bfa",
  "#22d3ee", "#fb923c", "#e879f9", "#84cc16", "#f43f5e",
  "#2dd4bf", "#facc15", "#60a5fa", "#c084fc", "#34d399",
];

// Ein einzelnes Tooltip-Element für alle Donuts (folgt der Maus).
let _tip = null;
function tipEl() {
  if (_tip) return _tip;
  // Globale Auffang-Regeln gegen "hängende" Tooltips: Wird während des Hovers
  // geklickt + weggezogen (mouseleave feuert dann nie) oder die Ansicht
  // gewechselt, blieb der Tooltip stehen. Jetzt verschwindet er bei JEDEM
  // mousedown, Drag-Start, Scroll und Fenster-Blur - und beim Client-/
  // Ansichtswechsel (hideFleetTip in app.js renderMainContent).
  document.addEventListener("mousedown", hideFleetTip, true);
  document.addEventListener("dragstart", hideFleetTip, true);
  document.addEventListener("scroll", hideFleetTip, true);
  window.addEventListener("blur", hideFleetTip);
  _tip = document.createElement("div");
  _tip.style.cssText = `
    position: fixed; z-index: 6000; pointer-events: none; display: none;
    background: var(--panel, #131c2b); color: var(--text, #e8eef7);
    border: 1px solid var(--border, #2a3550); border-radius: 10px;
    padding: 8px 12px; font-size: 12px; line-height: 1.5; max-width: 320px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.5);`;
  document.body.appendChild(_tip);
  return _tip;
}
export function showFleetTip(html, x, y) {
  const el = tipEl();
  el.innerHTML = html;
  el.style.display = "block";
  const pad = 14;
  let left = x + pad, top = y + pad;
  const r = el.getBoundingClientRect();
  if (left + r.width > window.innerWidth - 8) left = x - r.width - pad;
  if (top + r.height > window.innerHeight - 8) top = y - r.height - pad;
  el.style.left = left + "px";
  el.style.top = top + "px";
}
export function hideFleetTip() { if (_tip) _tip.style.display = "none"; }

// Gruppiert Clients nach einer Schlüsselfunktion -> [{label,count,items,color}]
export function groupBy(clients, keyFn, colorFn) {
  const map = new Map();
  for (const c of clients) {
    const key = keyFn(c) || "unbekannt";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(c.hostname || c.id);
  }
  const entries = [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  return entries.map(([label, items], i) => ({
    label, count: items.length, items,
    color: colorFn ? colorFn(label, i) : FLEET_PALETTE[i % FLEET_PALETTE.length],
  }));
}

// Baut EIN interaktives Donut-Diagramm als DOM-Element.
//   segments: [{label, count, items, color}]
//   opts.title: optionaler Titel (Dashboard-Karte); Widgets lassen ihn weg,
//               weil das Widget selbst schon einen Kopf mit Titel hat.
//   opts.card:  true = mit "dash-card"-Rahmen (Dashboard), false = nackt (Widget)
export function buildFleetDonut(segments, opts = {}) {
  const { title = null, card = false, size = 168 } = opts;
  const total = segments.reduce((s, x) => s + x.count, 0);
  // Kompakt-Modus (kleine Widgets, z.B. eine Höheneinheit): dünnerer Ring,
  // aber Legendentexte bleiben bewusst so groß wie beim gut lesbaren Pie.
  const compact = size <= 110;
  const legendFs = compact ? (segments.length <= 3 ? 15 : 13.5) : 13;
  const legendPad = compact ? (segments.length <= 3 ? "3px 3px" : "1px 3px") : "3px 4px";
  const legendGap = compact ? 6 : 8;
  const totalFs = compact ? (String(total).length > 2 ? 18 : 22) : 30;
  const totalLabelFs = compact ? 8.5 : 11;
  const totalLabelOffset = compact ? 11 : 16;
  const stroke = compact ? 16 : 26, hoverGrow = compact ? 4 : 6;
  // Radius so wählen, dass auch der beim Hover verdickte Ring vollständig in
  // die SVG-Fläche passt und nicht am Rand abgeschnitten wird.
  const r = (size - stroke - hoverGrow) / 2 - 2, cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r;

  const wrap = document.createElement("div");
  if (card) wrap.className = "dash-card";

  let circles = "";
  if (total === 0) {
    circles = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"/>`;
  } else {
    let offset = 0;
    segments.forEach((s, i) => {
      const frac = s.count / total;
      const len = frac * C;
      circles += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
        stroke="${s.color}" stroke-width="${stroke}"
        stroke-dasharray="${len.toFixed(3)} ${(C - len).toFixed(3)}"
        stroke-dashoffset="${(-offset).toFixed(3)}"
        transform="rotate(-90 ${cx} ${cy})"
        class="dash-seg" data-seg="${i}" style="cursor:pointer;transition:stroke-width .12s,opacity .12s"/>`;
      offset += len;
    });
  }

  const legend = segments.map((s, i) => `
    <div class="dash-legend-row" data-seg="${i}" style="display:flex;align-items:center;gap:${legendGap}px;padding:${legendPad};border-radius:6px;cursor:pointer;font-size:${legendFs}px">
      <span style="width:${compact ? 9 : 11}px;height:${compact ? 9 : 11}px;border-radius:3px;background:${s.color};flex-shrink:0"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.label)}</span>
      <span style="color:var(--subtext);font-variant-numeric:tabular-nums">${s.count}</span>
    </div>`).join("") || `<div style="color:var(--subtext);font-size:12px;padding:4px">Keine Daten</div>`;

  wrap.innerHTML = `
    ${title ? `<div class="dash-card-title">${esc(title)}</div>` : ""}
    <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
      <div style="position:relative;width:${size}px;height:${size}px;flex-shrink:0">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="overflow:visible">${circles}</svg>
        <div style="position:absolute;inset:0;pointer-events:none;text-align:center">
          <div style="position:absolute;left:0;right:0;top:50%;transform:translateY(-50%);font-size:${totalFs}px;font-weight:700;line-height:1">${total}</div>
          <div style="position:absolute;left:0;right:0;top:calc(50% + ${totalLabelOffset}px);font-size:${totalLabelFs}px;line-height:1;color:var(--subtext);white-space:nowrap">gesamt</div>
        </div>
      </div>
      <div style="flex:1;min-width:${compact ? 90 : 150}px;${compact ? `max-height:${size}px;overflow:auto` : ""}">${legend}</div>
    </div>
  `;

  // Hover-Logik (Segment + Legende teilen sich dieselbe Hervorhebung/Tooltip).
  const segEls = wrap.querySelectorAll(".dash-seg");
  const rowEls = wrap.querySelectorAll(".dash-legend-row");

  function tipHtml(s) {
    const pct = total ? Math.round((s.count / total) * 100) : 0;
    const list = (s.items || []).slice(0, 12).map((h) => `• ${esc(h)}`).join("<br>");
    const more = (s.items || []).length > 12 ? `<br><span style="color:var(--subtext)">… ${t("app_and_more", { n: s.items.length - 12 })}</span>` : "";
    return `<b>${esc(s.label)}</b> — ${s.count} (${pct}%)<br><span style="color:var(--subtext)">${list}${more}</span>`;
  }
  function highlight(i, on) {
    const seg = wrap.querySelector(`.dash-seg[data-seg="${i}"]`);
    if (seg) { seg.style.strokeWidth = on ? (stroke + hoverGrow) : stroke; seg.setAttribute("opacity", on ? "1" : ""); }
    segEls.forEach((e) => { if (e !== seg) e.setAttribute("opacity", on ? "0.45" : ""); });
    const row = wrap.querySelector(`.dash-legend-row[data-seg="${i}"]`);
    if (row) row.style.background = on ? "var(--panel-2, #1b2740)" : "";
  }
  // Hover-COOLDOWN (0.2 s): Die Vergrößerung/Hervorhebung darf höchstens alle
  // 200 ms den Zustand wechseln. Ohne das flackerte der Ring "groß-klein-
  // groß-klein", wenn der Cursor am Segmentrand stand oder der Inhalt unter
  // dem Cursor aktualisiert wurde.
  let hoverActive = -1;        // aktuell hervorgehobenes Segment (-1 = keins)
  let hoverTimer = null;
  let hoverLastChange = 0;
  function requestHover(i, on) {
    const want = on ? i : -1;
    if (want === hoverActive) { clearTimeout(hoverTimer); hoverTimer = null; return; }
    const apply = () => {
      hoverTimer = null;
      if (want === hoverActive) return;
      if (hoverActive >= 0) highlight(hoverActive, false);
      if (want >= 0) highlight(want, true);
      hoverActive = want;
      hoverLastChange = Date.now();
      if (want < 0) hideFleetTip();
    };
    const wait = 200 - (Date.now() - hoverLastChange);
    clearTimeout(hoverTimer);
    if (wait <= 0) apply();
    else hoverTimer = setTimeout(apply, wait);
  }
  function bind(el) {
    const i = +el.dataset.seg;
    const s = segments[i];
    el.addEventListener("mouseenter", (e) => { requestHover(i, true); showFleetTip(tipHtml(s), e.clientX, e.clientY); });
    el.addEventListener("mousemove", (e) => showFleetTip(tipHtml(s), e.clientX, e.clientY));
    el.addEventListener("mouseleave", () => requestHover(i, false));
  }
  segEls.forEach(bind);
  rowEls.forEach(bind);

  return wrap;
}

// Hängt den Flotten-Übersicht-Hover (Tooltip folgt der Maus) generisch an ein
// Element. htmlFn wird bei jedem Anzeigen frisch ausgewertet (Live-Werte).
// Liefert htmlFn nichts (null/leer), wird KEIN Tooltip gezeigt - so kann ein
// einmal angehängter Hover pro Render an-/abgeschaltet werden (z.B. wenn ein
// Widget die Darstellung wechselt und feinere Per-Element-Hover übernehmen).
export function attachHoverTip(el, htmlFn) {
  if (!el) return;
  el.style.cursor = el.style.cursor || "default";
  const show = (e) => {
    const html = htmlFn();
    if (!html) return;   // deaktiviert: Per-Element-Hover der Kinder nicht stören
    showFleetTip(html, e.clientX, e.clientY);
  };
  el.addEventListener("mouseenter", show);
  el.addEventListener("mousemove", show);
  el.addEventListener("mouseleave", () => hideFleetTip());
}

// ------------------------------------------------------------------
// Responsive-Scale: skaliert einen (fixe-Größe-)Diagramminhalt so, dass er
// immer vollständig in seinen Container passt - z.B. wenn ein 2x1-Widget auf
// 1x1 verkleinert wird. Der innere Inhalt behält sein Seitenverhältnis und
// wird per CSS-transform proportional geschrumpft (nie vergrößert).
// Aufruf NACH dem Einfügen des Inhalts in `holder`.
// ------------------------------------------------------------------
export function fitToContainer(holder) {
  if (!holder || !holder.firstElementChild) return;
  const inner = holder.firstElementChild;
  const apply = () => {
    if (!holder.isConnected) { ro.disconnect(); return; }
    // Transform zurücksetzen, um die natürliche Größe zu messen.
    inner.style.transform = "";
    inner.style.transformOrigin = "center center";
    const availW = holder.clientWidth, availH = holder.clientHeight;
    const needW = inner.scrollWidth, needH = inner.scrollHeight;
    if (!availW || !availH || !needW || !needH) return;
    const scale = Math.min(1, availW / needW, availH / needH);
    if (scale < 0.999) inner.style.transform = `scale(${scale.toFixed(3)})`;
  };
  const ro = new ResizeObserver(apply);
  ro.observe(holder);
  // Initial nach dem Layout messen.
  requestAnimationFrame(apply);
}

// ------------------------------------------------------------------
// Responsive-Scale V2: skaliert einen Inhalt mit FESTER natürlicher Größe
// (für 1x1-Zellen optimiert) proportional in seinen Container - nach UNTEN
// (passt immer) und nach OBEN (2x2-Zelle => Inhalt ~doppelt so groß, exakt
// gleiche Proportionen). Aufruf NACH dem Einfügen des Inhalts in `holder`.
// ------------------------------------------------------------------
export function scaleToContainer(holder) {
  if (!holder || !holder.firstElementChild) return;
  const inner = holder.firstElementChild;
  if (inner.dataset && inner.dataset.stretch === "fill") {
    inner.style.transform = "";
    inner.style.transformOrigin = "";
    inner.style.width = "100%";
    inner.style.height = "100%";
    inner.style.maxWidth = "100%";
    inner.style.maxHeight = "100%";
    inner.style.minWidth = "0";
    inner.style.minHeight = "0";
    inner.style.boxSizing = "border-box";
    return;
  }
  inner.style.flex = "none";
  const apply = () => {
    if (!holder.isConnected) { ro.disconnect(); return; }
    inner.style.transform = "";
    inner.style.transformOrigin = "center center";
    const availW = holder.clientWidth, availH = holder.clientHeight;
    const needW = inner.scrollWidth, needH = inner.scrollHeight;
    if (!availW || !availH || !needW || !needH) return;
    // WICHTIG: Beim Herausloesen in eine App ist der Container VIEL groesser
    // als die native Widget-Groesse (240x88). Frueher wurde dann unbegrenzt
    // hochskaliert -> der Inhalt (grosse Zahl + nowrap-Label) wuchs ueber den
    // Rand hinaus, weil eine ellipsis-Breite mitskaliert nichts mehr begrenzt.
    // Loesung: Hochskalieren auf max. 2.4x deckeln, sodass Text immer im
    // sichtbaren Bereich bleibt; Herunterskalieren (Zelle kleiner als nativ)
    // bleibt unbegrenzt, damit in kleinen Kacheln nichts abgeschnitten wird.
    const raw = Math.min(availW / needW, availH / needH);
    const scale = raw > 1 ? Math.min(raw, 2.4) : raw;
    if (Math.abs(scale - 1) > 0.005) inner.style.transform = `scale(${scale.toFixed(3)})`;
  };
  const ro = new ResizeObserver(apply);
  ro.observe(holder);
  requestAnimationFrame(apply);
}

// ------------------------------------------------------------------
// Getimestampter Verlaufs-Chart (line / area / spark) mit Hover wie beim
// Netzwerk-Diagramm im Metrics-Panel: senkrechte Hover-Linie, Punkt-Marker
// auf der Kurve und Tooltip mit UHRZEIT + exakten Werten aller Serien am
// nächstgelegenen Datenpunkt.
//   series: [{ label, color, values:[..], timestamps:[..] }]
//   opts:   { width, height, mode:"line"|"area"|"spark", axes, yMax,
//             formatValue(v) }
// ------------------------------------------------------------------
export function timeSeriesChart(series, opts = {}) {
  const {
    width = 240, height = 82, mode = "line",
    axes = mode !== "spark", yMax = null, formatValue = null,
  } = opts;
  const padL = axes ? 34 : 4, padR = 6, padT = 6, padB = axes ? 15 : 4;
  const plotW = width - padL - padR, plotH = height - padT - padB;
  const fmt = (v) => (formatValue ? formatValue(v) : String(Math.round(v)));
  const timeFmt = (ts) => new Date(ts).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  let max = yMax;
  if (max == null) { max = 1; for (const s of series) for (const v of (s.values || [])) if (v > max) max = v; }
  const min = Math.min(0, ...series.flatMap((s) => s.values || []));
  const span = (max - min) || 1;

  const ptsFor = (values) => (values || []).map((v, i) => ({
    x: padL + (values.length <= 1 ? 0 : (i / (values.length - 1)) * plotW),
    y: padT + plotH - ((v - min) / span) * plotH, v, i,
  }));

  // y-Achse (0 / Mitte / Max) + dezente Gitterlinien
  const yLabels = !axes ? "" : [0, 0.5, 1].map((f) => {
    const y = padT + plotH - f * plotH;
    return `<text x="${padL - 4}" y="${(y + 3).toFixed(1)}" text-anchor="end" fill="var(--subtext)" font-size="8.5">${esc(fmt(min + f * span))}</text>
      <line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-width="1" opacity="0.45"/>`;
  }).join("");

  // x-Achse: Uhrzeit des ersten/letzten Datenpunkts (falls Timestamps da sind)
  const ts0 = series[0]?.timestamps;
  const xLabels = axes && ts0 && ts0.length > 1
    ? `<text x="${padL}" y="${height - 3}" fill="var(--subtext)" font-size="8.5">${timeFmt(ts0[0])}</text>
       <text x="${width - padR}" y="${height - 3}" text-anchor="end" fill="var(--subtext)" font-size="8.5">${timeFmt(ts0[ts0.length - 1])}</text>`
    : "";

  const paths = series.map((s) => {
    const pts = ptsFor(s.values);
    if (!pts.length) return "";
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    const base = (padT + plotH).toFixed(1);
    const area = mode === "area"
      ? `<path d="${d} L ${pts[pts.length - 1].x.toFixed(1)},${base} L ${pts[0].x.toFixed(1)},${base} Z" fill="${s.color}" opacity="0.2"/>` : "";
    return `${area}<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join("");

  const container = document.createElement("div");
  container.style.cssText = `position:relative;width:${width}px`;
  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" style="display:block;overflow:visible">
      ${yLabels}${xLabels}${paths}
      <line class="tsc-line" x1="0" x2="0" y1="${padT}" y2="${padT + plotH}" stroke="var(--accent)" stroke-width="1" opacity="0.85" style="display:none"/>
      ${series.map((s, si) => `<circle class="tsc-dot" data-s="${si}" r="3" fill="${s.color}" stroke="var(--panel, #131c2b)" stroke-width="1.5" style="display:none"/>`).join("")}
    </svg>`;

  const svg = container.querySelector("svg");
  const hoverLine = container.querySelector(".tsc-line");
  const dots = [...container.querySelectorAll(".tsc-dot")];
  const first = series.find((s) => s.values && s.values.length);

  svg.addEventListener("mousemove", (e) => {
    if (!first) return;
    const rect = svg.getBoundingClientRect();
    const mouseX = ((e.clientX - rect.left) / (rect.width || 1)) * width;
    const pts = ptsFor(first.values);
    let nearest = pts[0];
    for (const p of pts) if (Math.abs(p.x - mouseX) < Math.abs(nearest.x - mouseX)) nearest = p;
    if (!nearest) return;
    hoverLine.style.display = "";
    hoverLine.setAttribute("x1", nearest.x); hoverLine.setAttribute("x2", nearest.x);
    // Punkt-Marker jeder Serie auf ihrer Kurve platzieren.
    series.forEach((s, si) => {
      const p = ptsFor(s.values)[nearest.i];
      const dot = dots[si];
      if (p && dot) { dot.style.display = ""; dot.setAttribute("cx", p.x); dot.setAttribute("cy", p.y); }
      else if (dot) dot.style.display = "none";
    });
    // Tooltip: Uhrzeit des Datenpunkts + Wert(e) aller Serien.
    const ts = first.timestamps && first.timestamps[nearest.i];
    const timeStr = ts ? `<span style="color:var(--subtext)">${timeFmt(ts)}</span><br>` : "";
    const lines = series.map((s) => {
      const v = (s.values || [])[nearest.i];
      return v === undefined ? "" : `<span style="color:${s.color}">●</span> ${esc(s.label)}: <b>${esc(fmt(v))}</b>`;
    }).filter(Boolean).join("<br>");
    showFleetTip(timeStr + lines, e.clientX, e.clientY);
  });
  svg.addEventListener("mouseleave", () => {
    hoverLine.style.display = "none";
    dots.forEach((d) => (d.style.display = "none"));
    hideFleetTip();
  });
  return container;
}

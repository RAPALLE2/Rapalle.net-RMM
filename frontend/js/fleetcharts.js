// fleetcharts.js
// --------------
// Gemeinsame Bausteine für die Flotten-Übersicht: das interaktive
// Donut-Diagramm (Hover-Highlight + Tooltip mit Client-Liste) und der
// groupBy-Helfer. Wird vom Dashboard (dashboard.js) UND von den
// Flotten-Widgets (dashwidgets.js, kind "fleetdonut") benutzt - so ist die
// Flotten-Übersicht als frei verschieb-/editier-/löschbares Widget verfügbar.

import { esc } from "./utils.js";

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
  // kleinere Schriften, engere Legende.
  const compact = size <= 110;
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
    <div class="dash-legend-row" data-seg="${i}" style="display:flex;align-items:center;gap:${compact ? 6 : 8}px;padding:${compact ? "1px 3px" : "3px 4px"};border-radius:6px;cursor:pointer;font-size:${compact ? 11 : 13}px">
      <span style="width:11px;height:11px;border-radius:3px;background:${s.color};flex-shrink:0"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.label)}</span>
      <span style="color:var(--subtext);font-variant-numeric:tabular-nums">${s.count}</span>
    </div>`).join("") || `<div style="color:var(--subtext);font-size:12px;padding:4px">Keine Daten</div>`;

  wrap.innerHTML = `
    ${title ? `<div class="dash-card-title">${esc(title)}</div>` : ""}
    <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
      <div style="position:relative;width:${size}px;height:${size}px;flex-shrink:0">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="overflow:visible">${circles}</svg>
        <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none">
          <div style="font-size:${compact ? 19 : 30}px;font-weight:700;line-height:1">${total}</div>
          <div style="font-size:${compact ? 9 : 11}px;color:var(--subtext)">gesamt</div>
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
    const more = (s.items || []).length > 12 ? `<br><span style="color:var(--subtext)">… und ${s.items.length - 12} weitere</span>` : "";
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
export function attachHoverTip(el, htmlFn) {
  if (!el) return;
  el.style.cursor = el.style.cursor || "default";
  el.addEventListener("mouseenter", (e) => showFleetTip(htmlFn(), e.clientX, e.clientY));
  el.addEventListener("mousemove", (e) => showFleetTip(htmlFn(), e.clientX, e.clientY));
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

// apps/dashboard.js
// -----------------
// Das "echte" Dashboard: drei Donut-Diagramme (hohl in der Mitte) über alle
// verwalteten Clients:
//   1. Agenten online / offline
//   2. Betriebssystem-Verteilung
//   3. Agent-Versionen auf den Clients
// Beim Hovern über ein Segment (oder einen Legendeneintrag) erscheint ein
// Tooltip mit der genauen Anzahl und den betroffenen Clients.

import { state } from "../state.js";
import { esc } from "../utils.js";
import { osLabel } from "../i18n.js";
import { favClientIds, favWebsiteList, favStarHtml, selectClientExternal } from "../sidebar.js";

// Farbpalette für Kategorien (OS/Versionen). Online/Offline haben feste Farben.
const PALETTE = [
  "#4da6ff", "#3ecf8e", "#f5a524", "#ff4d6d", "#a78bfa",
  "#22d3ee", "#fb923c", "#e879f9", "#84cc16", "#f43f5e",
  "#2dd4bf", "#facc15", "#60a5fa", "#c084fc", "#34d399",
];

// Ein einzelnes Tooltip-Element für das ganze Dashboard (folgt der Maus).
let _tip = null;
function tipEl() {
  if (_tip) return _tip;
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
function showTip(html, x, y) {
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
function hideTip() { if (_tip) _tip.style.display = "none"; }

// Gruppiert Clients nach einer Schlüsselfunktion -> [{label,count,items,color}]
function groupBy(clients, keyFn, colorFn) {
  const map = new Map();
  for (const c of clients) {
    const key = keyFn(c) || "unbekannt";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(c.hostname || c.id);
  }
  const entries = [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  return entries.map(([label, items], i) => ({
    label, count: items.length, items,
    color: colorFn ? colorFn(label, i) : PALETTE[i % PALETTE.length],
  }));
}

// Baut EIN Donut-Diagramm als DOM-Element.
function donut(title, segments) {
  const total = segments.reduce((s, x) => s + x.count, 0);
  const size = 168, stroke = 26, hoverGrow = 6;
  // Radius so wählen, dass auch der beim Hover verdickte Ring (stroke+hoverGrow)
  // vollständig in die SVG-Fläche passt und nicht am Rand abgeschnitten wird.
  const r = (size - stroke - hoverGrow) / 2 - 2, cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r;

  const wrap = document.createElement("div");
  wrap.className = "dash-card";

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
    <div class="dash-legend-row" data-seg="${i}" style="display:flex;align-items:center;gap:8px;padding:3px 4px;border-radius:6px;cursor:pointer">
      <span style="width:11px;height:11px;border-radius:3px;background:${s.color};flex-shrink:0"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.label)}</span>
      <span style="color:var(--subtext);font-variant-numeric:tabular-nums">${s.count}</span>
    </div>`).join("") || `<div style="color:var(--subtext);font-size:12px;padding:4px">Keine Daten</div>`;

  wrap.innerHTML = `
    <div class="dash-card-title">${esc(title)}</div>
    <div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
      <div style="position:relative;width:${size}px;height:${size}px;flex-shrink:0">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="overflow:visible">${circles}</svg>
        <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none">
          <div style="font-size:30px;font-weight:700;line-height:1">${total}</div>
          <div style="font-size:11px;color:var(--subtext)">gesamt</div>
        </div>
      </div>
      <div style="flex:1;min-width:150px">${legend}</div>
    </div>
  `;

  // Hover-Logik (Segment + Legende teilen sich dieselbe Hervorhebung/Tooltip).
  const segEls = wrap.querySelectorAll(".dash-seg");
  const rowEls = wrap.querySelectorAll(".dash-legend-row");

  function tipHtml(s) {
    const pct = total ? Math.round((s.count / total) * 100) : 0;
    const list = s.items.slice(0, 12).map((h) => `• ${esc(h)}`).join("<br>");
    const more = s.items.length > 12 ? `<br><span style="color:var(--subtext)">… und ${s.items.length - 12} weitere</span>` : "";
    return `<b>${esc(s.label)}</b> — ${s.count} (${pct}%)<br><span style="color:var(--subtext)">${list}${more}</span>`;
  }
  function highlight(i, on) {
    const seg = wrap.querySelector(`.dash-seg[data-seg="${i}"]`);
    if (seg) { seg.style.strokeWidth = on ? (stroke + hoverGrow) : stroke; seg.setAttribute("opacity", on ? "1" : ""); }
    segEls.forEach((e) => { if (e !== seg) e.setAttribute("opacity", on ? "0.45" : ""); });
    const row = wrap.querySelector(`.dash-legend-row[data-seg="${i}"]`);
    if (row) row.style.background = on ? "var(--panel-2, #1b2740)" : "";
  }
  function bind(el) {
    const i = +el.dataset.seg;
    const s = segments[i];
    el.addEventListener("mouseenter", (e) => { highlight(i, true); showTip(tipHtml(s), e.clientX, e.clientY); });
    el.addEventListener("mousemove", (e) => showTip(tipHtml(s), e.clientX, e.clientY));
    el.addEventListener("mouseleave", () => { highlight(i, false); hideTip(); });
  }
  segEls.forEach(bind);
  rowEls.forEach(bind);

  return wrap;
}

export function renderDashboard(target) {
  const clients = (state.clients || []).filter((c) => !c.parent_client_id);

  target.innerHTML = `
    <div class="dash-wrap">
      <div class="dash-head">
        <h2 style="margin:0">Dashboard</h2>
        <span style="color:var(--subtext);font-size:13px">${clients.length} verwaltete Clients</span>
      </div>
      <div id="dash-favorites" style="display:none;margin-bottom:14px"></div>
      <div class="dash-grid" id="dash-grid"></div>
    </div>
  `;
  const grid = target.querySelector("#dash-grid");

  // Dashboard-Favoriten: Clients UND Websites, die (per Stern) für das Dashboard
  // markiert sind. Reagiert live auf Änderungen am Favoriten-Zustand.
  function renderDashFavorites() {
    const box = target.querySelector("#dash-favorites");
    if (!box) return;
    const favClients = state.clients.filter((c) => favClientIds("d").includes(c.id));
    const favSites = favWebsiteList("d");
    if (!favClients.length && !favSites.length) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "";

    const clientCards = favClients.map((c) => `
      <div class="panel" data-dashfav-client="${esc(c.id)}"
           style="display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer">
        <span style="color:${c.online ? "var(--online, #3ecf8e)" : "var(--subtext)"}">●</span>
        <span style="font-weight:600">${esc(c.hostname)}</span>
        ${favStarHtml("clients", c.id)}
      </div>`).join("");

    const siteCards = favSites.map((w) => {
      const meta = { name: w.name, url: w.url, clientId: w.clientId, clientHostname: w.clientHostname };
      return `
      <div class="panel" style="display:flex;align-items:center;gap:8px;padding:8px 12px">
        <a href="${esc(w.url || "")}" target="_blank" rel="noopener noreferrer"
           title="${esc(w.url || "")}" style="display:flex;align-items:center;gap:8px;text-decoration:none;color:var(--text)">
          <span>🔗</span><span style="font-weight:600">${esc(w.name || w.url || "")}</span>
          <span style="font-size:11px;color:var(--subtext)">${esc(w.clientHostname || "")}</span>
        </a>
        ${favStarHtml("websites", w.id, meta)}
      </div>`;
    }).join("");

    box.innerHTML = `
      <h3 style="margin:0 0 8px;font-size:14px;color:var(--subtext)">★ Angeheftet</h3>
      <div style="display:flex;flex-wrap:wrap;gap:8px">${clientCards}${siteCards}</div>`;

    box.querySelectorAll("[data-dashfav-client]").forEach((el) =>
      el.addEventListener("click", (e) => {
        if (e.target.closest(".fav-star")) return;
        const id = el.dataset.dashfavClient;
        try { selectClientExternal(id); } catch { state.selection = { type: "client", id }; }
      }));
  }
  renderDashFavorites();
  // Bei Favoriten-Änderungen (Stern woanders geklickt) neu aufbauen.
  if (!target._favListener) {
    target._favListener = () => { if (document.body.contains(target)) renderDashFavorites(); };
    window.addEventListener("favorites-changed", target._favListener);
  }

  // 1) Online / Offline (inkl. Wartung)
  const statusSegs = groupBy(
    clients,
    (c) => (c.status_override === "maintenance" ? "Wartung" : (c.online ? "Online" : "Offline")),
    (label) => label === "Online" ? "var(--online, #3ecf8e)" : (label === "Wartung" ? "var(--warn, #f5a524)" : "var(--subtext, #64748b)")
  );
  grid.appendChild(donut("Agenten: Online / Offline", statusSegs));

  // 2) Betriebssystem
  const osSegs = groupBy(clients, (c) => osLabel(c.platform, c.release));
  grid.appendChild(donut("Betriebssysteme", osSegs));

  // 3) Agent-Versionen
  const verSegs = groupBy(clients, (c) => c.agent_version || "unbekannt");
  grid.appendChild(donut("Agent-Versionen", verSegs));
}

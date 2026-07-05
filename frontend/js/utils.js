// utils.js
// --------
// Kleine, wiederverwendbare Helferlein, die an mehreren Stellen gebraucht
// werden: Zahlen hübsch formatieren + zwei SVG-Diagramme (Donut + Linie)
// selbst zeichnen, ohne externe Chart-Bibliothek.

// ---- Zahlen/Zeit formatieren ----

export function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let val = bytes;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i++;
  }
  return `${val.toFixed(val < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export function formatUptime(seconds) {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ---- Farbverlauf berechnen ----
// Gibt je nach Auslastung (0..100) eine Farbe zwischen zwei Enden zurück.
// Wird für die Donut-Charts benutzt (CPU: blau->rot, RAM: grün->orange).

function lerp(a, b, t) {
  return Math.round(a + (b - a) * t);
}

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex(r, g, b) {
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
}

export function gradientColor(percent, fromHex, toHex) {
  const t = Math.max(0, Math.min(1, percent / 100));
  const [r1, g1, b1] = hexToRgb(fromHex);
  const [r2, g2, b2] = hexToRgb(toHex);
  return rgbToHex(lerp(r1, r2, t), lerp(g1, g2, t), lerp(b1, b2, t));
}

// ---- Donut-Chart (3/4-Kreis) mit echtem FARBVERLAUF als SVG ----
// Statt einer einzelnen Farbe wird ein vordefinierter Gradient entlang des
// Bogens gelegt (z.B. CPU: hellblau unten-links -> pink oben). Je höher der
// Prozentwert, desto mehr vom Gradienten wird "enthüllt". Der Anfang bleibt
// also immer gleichfarbig, nur das Ende wandert weiter.
//
// gradientId: eindeutige ID für das SVG-Gradienten-Element
// stops: Array von {offset: 0..1, color: "#..."} - der Farbverlauf

let _gradientCounter = 0;

export function gradientDonutSvg(percent, stops, label, valueText) {
  const size = 120;
  const stroke = 12;
  const radius = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;

  const circumference = 2 * Math.PI * radius;
  const arcLength = circumference * 0.75; // 270°-Bogen
  const pct = Math.max(0, Math.min(100, percent));
  const filled = arcLength * (pct / 100);
  const rotate = `rotate(135 ${cx} ${cy})`;

  const gid = `grad${_gradientCounter++}`;

  // Farbverlauf-Stops als SVG-Elemente
  const stopEls = stops.map((s) =>
    `<stop offset="${(s.offset * 100).toFixed(0)}%" stop-color="${s.color}" />`
  ).join("");

  // Der Gradient läuft entlang des Bogens. Damit die Farbe am Anfang (unten
  // links) immer gleich ist, nutzen wir einen linearen Gradienten diagonal,
  // der grob der Bogenrichtung folgt.
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      <defs>
        <linearGradient id="${gid}" x1="0%" y1="100%" x2="100%" y2="0%">
          ${stopEls}
        </linearGradient>
      </defs>
      <circle cx="${cx}" cy="${cy}" r="${radius}" fill="none"
        stroke="rgba(255,255,255,0.08)" stroke-width="${stroke}"
        stroke-dasharray="${arcLength} ${circumference}"
        stroke-linecap="round" transform="${rotate}" />
      <circle cx="${cx}" cy="${cy}" r="${radius}" fill="none"
        stroke="url(#${gid})" stroke-width="${stroke}"
        stroke-dasharray="${filled} ${circumference}"
        stroke-linecap="round" transform="${rotate}" />
      <text x="${cx}" y="${cy - 2}" text-anchor="middle" fill="var(--text)"
        font-size="20" font-weight="600">${Math.round(pct)}%</text>
      <text x="${cx}" y="${cy + 16}" text-anchor="middle" fill="var(--subtext)"
        font-size="10">${valueText || ""}</text>
    </svg>
    <div class="donut-label">${label}</div>
  `;
}

// Vordefinierte Gradienten-Stops
export const CPU_GRADIENT = [
  { offset: 0, color: "#7cc4ff" },   // hellblau
  { offset: 0.5, color: "#a97cff" }, // violett
  { offset: 1, color: "#ff4d94" },   // pink
];
export const RAM_GRADIENT = [
  { offset: 0, color: "#3ecf8e" },   // grün
  { offset: 0.5, color: "#c7d34a" }, // gelbgrün
  { offset: 1, color: "#f5a524" },   // orange
];
export const DISK_GRADIENT = [
  { offset: 0, color: "#38bdf8" },
  { offset: 1, color: "#f75c5c" },
];

// ---- Interaktives Linien-Diagramm mit Achsen-Beschriftung + Hover-Tooltip ----
// Gibt ein Container-Element zurück (kein String!), das man direkt ins DOM
// hängt. Beim Hovern über die Kurve wird der genaue Wert zu diesem Zeitpunkt
// angezeigt. "timestamps" ist optional (Array gleicher Länge wie values, in ms).
//
// unit: Text hinter dem Wert im Tooltip (z.B. "%" oder "MB/s")
// yMax: fester Maximalwert der Y-Achse (z.B. 100 für Prozent), oder null für Auto

export function interactiveChart(series, { unit = "", yMax = null, height = 90, formatValue = null } = {}) {
  // series: Array von { label, color, values, timestamps? }
  const width = 300;
  const padLeft = 34;   // Platz für Y-Achsen-Beschriftung
  const padBottom = 16; // Platz für X-Achsen-Beschriftung
  const plotW = width - padLeft - 6;
  const plotH = height - padBottom - 6;

  // Maximalwert über alle Serien bestimmen (falls nicht fest vorgegeben)
  let max = yMax;
  if (max == null) {
    max = 1;
    for (const s of series) for (const v of s.values) if (v > max) max = v;
  }

  const container = document.createElement("div");
  container.style.position = "relative";

  function pointsFor(values) {
    if (!values || values.length < 2) return [];
    const stepX = plotW / (values.length - 1);
    return values.map((v, i) => ({
      x: padLeft + i * stepX,
      y: 6 + plotH - (v / max) * plotH,
      v,
      i,
    }));
  }

  // Y-Achsen-Beschriftungen (0, Mitte, Max)
  const yLabels = [0, max / 2, max].map((val, idx) => {
    const y = 6 + plotH - (val / max) * plotH;
    const txt = formatValue ? formatValue(val) : Math.round(val) + unit;
    return `<text x="${padLeft - 4}" y="${y + 3}" text-anchor="end" fill="var(--subtext)" font-size="9">${txt}</text>
            <line x1="${padLeft}" y1="${y}" x2="${width - 6}" y2="${y}" stroke="rgba(255,255,255,0.05)" />`;
  }).join("");

  // Linien-Pfade
  const paths = series.map((s) => {
    const pts = pointsFor(s.values);
    if (!pts.length) return "";
    const d = "M" + pts.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" L");
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" />`;
  }).join("");

  container.innerHTML = `
    <svg width="100%" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" style="display:block">
      ${yLabels}
      ${paths}
      <line class="hover-line" x1="0" y1="6" x2="0" y2="${6 + plotH}" stroke="var(--accent)" stroke-width="1" style="display:none" />
    </svg>
    <div class="chart-tooltip" style="display:none;position:absolute;background:var(--panel-2);
         border:1px solid var(--border);border-radius:6px;padding:5px 8px;font-size:11px;
         pointer-events:none;white-space:nowrap;z-index:10"></div>
  `;

  const svg = container.querySelector("svg");
  const hoverLine = container.querySelector(".hover-line");
  const tooltip = container.querySelector(".chart-tooltip");

  // Hover: nächstgelegenen Datenpunkt finden und Werte anzeigen
  svg.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect();
    // Mausposition in viewBox-Koordinaten umrechnen (SVG skaliert auf 100% Breite)
    const scaleX = width / rect.width;
    const mouseX = (e.clientX - rect.left) * scaleX;

    const first = series.find((s) => s.values && s.values.length >= 2);
    if (!first) return;
    const pts = pointsFor(first.values);
    // nächsten Punkt anhand X finden
    let nearest = pts[0];
    for (const p of pts) if (Math.abs(p.x - mouseX) < Math.abs(nearest.x - mouseX)) nearest = p;
    if (!nearest) return;

    hoverLine.style.display = "";
    hoverLine.setAttribute("x1", nearest.x);
    hoverLine.setAttribute("x2", nearest.x);

    // Zeitangabe (falls timestamps vorhanden)
    let timeStr = "";
    const ts = first.timestamps && first.timestamps[nearest.i];
    if (ts) timeStr = new Date(ts).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) + " · ";

    // Werte aller Serien an diesem Index
    const lines = series.map((s) => {
      const v = s.values[nearest.i];
      const val = formatValue ? formatValue(v) : Math.round(v) + unit;
      return `<span style="color:${s.color}">●</span> ${s.label}: <b>${val}</b>`;
    }).join("<br>");

    tooltip.style.display = "";
    tooltip.innerHTML = timeStr + "<br>" + lines;
    // Tooltip positionieren (in Pixel relativ zum Container)
    const pxX = (nearest.x / width) * rect.width;
    tooltip.style.left = Math.min(pxX + 8, rect.width - 120) + "px";
    tooltip.style.top = "2px";
  });

  svg.addEventListener("mouseleave", () => {
    hoverLine.style.display = "none";
    tooltip.style.display = "none";
  });

  return container;
}

// ---- Doppel-Linien-Diagramm (z.B. Netzwerk In + Out) als SVG ----
// Beide Serien teilen sich dieselbe Skala (Maximum beider zusammen).
// showA/showB steuern, welche Linie sichtbar ist (In/Out ein-/ausblendbar).

export function dualSparklineSvg(valuesA, valuesB, colorA, colorB, showA, showB, width = 260, height = 70) {
  const all = [...(showA ? valuesA : []), ...(showB ? valuesB : [])];
  const max = Math.max(1, ...all); // mindestens 1, um Division durch 0 zu vermeiden

  function pathFor(values) {
    if (!values || values.length < 2) return "";
    const stepX = width / (values.length - 1);
    const points = values.map((v, i) => {
      const x = i * stepX;
      const y = height - (v / max) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return `M${points.join(" L")}`;
  }

  let svg = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">`;
  if (showA) svg += `<path d="${pathFor(valuesA)}" fill="none" stroke="${colorA}" stroke-width="2" />`;
  if (showB) svg += `<path d="${pathFor(valuesB)}" fill="none" stroke="${colorB}" stroke-width="2" />`;
  svg += `</svg>`;
  return svg;
}

// ---- Escaping, um HTML-Injektion in dynamischen Texten zu vermeiden ----
export function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

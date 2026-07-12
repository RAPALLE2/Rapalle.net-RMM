// paneltiling.js
// --------------
// Geteilte, komplett modulare Resize-Engine für Kachel-Layouts (Dashboard-
// Widgets UND anpassbare Client-Ansicht). Sie arbeitet rein über die
// gerenderte Geometrie und ist daher unabhängig vom konkreten Grid.
//
// Verhalten wie ein Windows-Fenstermanager:
//   • LINKSKLICK auf einen Rand-Griff  -> alle Kacheln, die diese Grenze
//     berühren, werden gemeinsam angepasst (die einen wachsen, die anderen
//     schrumpfen).
//   • RECHTSKLICK auf einen Rand-Griff -> NUR die beiden direkt an der Grenze
//     liegenden Kacheln (die dem Cursor am nächsten sind) werden angepasst.
//
// Breite wird in Grid-Spalten gemessen (1..cols), Höhe frei in Pixeln.
//
// Integration: pro Kachel-Element attachEdgeResizers(card, host, opts) aufrufen.
//   opts = {
//     cols,                 Anzahl Grid-Spalten (Standard 12)
//     minW,                 Mindestbreite in Spalten (Standard 2)
//     panelOf(cardEl)       -> Datensatz mit {w, h}; wird direkt mutiert
//     commit()              nach Abschluss speichern
//   }
// Jede Kachel MUSS das Attribut data-tile tragen, damit Nachbarn gefunden werden.

const TOL = 10;   // Pixel-Toleranz, um "an derselben Grenze" zu erkennen
const midY = (r) => r.top + r.height / 2;
const midX = (r) => r.left + r.width / 2;

export function attachEdgeResizers(card, host, opts = {}) {
  const cols = opts.cols || 12;
  const minW = opts.minW || 2;
  const panelOf = opts.panelOf || ((c) => c._panel);
  const commit = opts.commit || (() => {});

  // Gespeicherte Höhe wiederherstellen.
  const p0 = panelOf(card);
  if (p0 && p0.h) card.style.height = `${p0.h}px`;

  const gx = document.createElement("div");
  gx.className = "tile-grip tile-grip-x";
  gx.title = "Ziehen: Breite ändern · Linksklick = alle angrenzenden · Rechtsklick = nur diese beiden";
  gx.addEventListener("contextmenu", (e) => e.preventDefault());
  gx.addEventListener("mousedown", (e) => startWidth(e));
  card.appendChild(gx);

  const gy = document.createElement("div");
  gy.className = "tile-grip tile-grip-y";
  gy.title = "Ziehen: Höhe ändern · Linksklick = alle angrenzenden · Rechtsklick = nur diese beiden";
  gy.addEventListener("contextmenu", (e) => e.preventDefault());
  gy.addEventListener("mousedown", (e) => startHeight(e));
  card.appendChild(gy);

  function siblings() { return [...host.querySelectorAll("[data-tile]")]; }

  // ---------------- Breite (vertikale Grenze) ----------------
  function startWidth(e) {
    e.preventDefault(); e.stopPropagation();
    const rightClick = e.button === 2;
    const all = siblings();
    const rects = new Map(all.map((c) => [c, c.getBoundingClientRect()]));
    const X = rects.get(card).right;

    // Alle, deren RECHTE Kante an dieser Grenze liegt (wachsen/schrumpfen mit +d),
    // und alle, deren LINKE Kante dort liegt (gegenläufig).
    let leftSet = all.filter((c) => Math.abs(rects.get(c).right - X) < TOL);
    let rightSet = all.filter((c) => Math.abs(rects.get(c).left - X) < TOL);

    if (rightClick) {
      // Nur das gegriffene Panel + der vertikal nächste rechte Nachbar.
      leftSet = [card];
      rightSet = rightSet
        .sort((a, b) => Math.abs(midY(rects.get(a)) - e.clientY) - Math.abs(midY(rects.get(b)) - e.clientY))
        .slice(0, 1);
    }

    const colW = host.getBoundingClientRect().width / cols;
    const startCX = e.clientX;
    const startW = new Map(all.map((c) => [c, panelOf(c).w]));
    document.body.style.userSelect = "none";
    document.body.style.cursor = "ew-resize";
    card.classList.add("tile-resizing");

    function move(ev) {
      let d = Math.round((ev.clientX - startCX) / colW);
      // d so begrenzen, dass keine betroffene Kachel unter minW / über cols fällt.
      for (const c of leftSet) {
        d = Math.max(d, minW - startW.get(c));
        d = Math.min(d, cols - startW.get(c));
      }
      for (const c of rightSet) {
        d = Math.min(d, startW.get(c) - minW);
        d = Math.max(d, startW.get(c) - cols);
      }
      for (const c of leftSet) {
        const p = panelOf(c); p.w = clamp(startW.get(c) + d, minW, cols);
        c.style.gridColumn = `span ${p.w}`;
      }
      for (const c of rightSet) {
        const p = panelOf(c); p.w = clamp(startW.get(c) - d, minW, cols);
        c.style.gridColumn = `span ${p.w}`;
      }
    }
    function up() {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      document.body.style.userSelect = ""; document.body.style.cursor = "";
      card.classList.remove("tile-resizing");
      commit();
    }
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }

  // ---------------- Höhe (horizontale Grenze) ----------------
  function startHeight(e) {
    e.preventDefault(); e.stopPropagation();
    const rightClick = e.button === 2;
    const all = siblings();
    const rects = new Map(all.map((c) => [c, c.getBoundingClientRect()]));
    const Y = rects.get(card).bottom;

    let topSet = all.filter((c) => Math.abs(rects.get(c).bottom - Y) < TOL);
    let botSet = all.filter((c) => Math.abs(rects.get(c).top - Y) < TOL);

    if (rightClick) {
      topSet = [card];
      botSet = botSet
        .sort((a, b) => Math.abs(midX(rects.get(a)) - e.clientX) - Math.abs(midX(rects.get(b)) - e.clientX))
        .slice(0, 1);
    }
    // Fällt keine Kachel darunter, wenigstens diese eine anpassen.
    if (!topSet.length) topSet = [card];

    const startCY = e.clientY;
    const startH = new Map(all.map((c) => [c, rects.get(c).height]));
    document.body.style.userSelect = "none";
    document.body.style.cursor = "ns-resize";
    card.classList.add("tile-resizing");

    function move(ev) {
      const d = ev.clientY - startCY;
      for (const c of topSet) {
        const h = Math.max(90, startH.get(c) + d);
        c.style.height = `${h}px`; panelOf(c).h = h;
      }
      for (const c of botSet) {
        const h = Math.max(90, startH.get(c) - d);
        c.style.height = `${h}px`; panelOf(c).h = h;
      }
    }
    function up() {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
      document.body.style.userSelect = ""; document.body.style.cursor = "";
      card.classList.remove("tile-resizing");
      commit();
    }
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

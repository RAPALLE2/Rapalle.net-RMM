// gridengine.js
// -------------
// GEMEINSAME Raster-Engine für die Client-Ansicht (dashlayout.js) UND das
// Dashboard (fleetdash.js). Beide benutzen exakt denselben Code - dadurch
// verhalten sich die Raster garantiert identisch (gleiche Spaltenzahl,
// gleiche Zellhöhe, gleiches Einrasten, gleiche Kollisionsauflösung).
//
// Kachel-Modell (identisch in beiden Ansichten):
//   gx = Spalte (0-basiert), gy = Reihe (0-basiert)
//   gw = Breite in Zellen,   gh = Höhe in Zellen
//
// Wichtig: Kacheln liegen IMMER auf ganzen Zellen und können das Raster nie
// nach rechts verlassen (gx + gw <= COLS wird überall erzwungen).

// ---- Raster-Konstanten (Single Source of Truth für beide Ansichten) ----
export const COLS = 5;         // feste Spaltenzahl
export const BASE_ROWS = 4;    // Grundhöhe (Reihen), wächst nach unten
export const ROW_H = 150;      // Reihenhöhe in px
export const GAP = 14;         // Abstand zwischen Zellen in px

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// Überlappen sich zwei Kacheln?
export function overlap(a, b) {
  return a.gx < b.gx + b.gw && a.gx + a.gw > b.gx && a.gy < b.gy + b.gh && a.gy + a.gh > b.gy;
}

// Kachel hart ins Raster zwingen: Größe begrenzen und Position so schieben,
// dass sie vollständig innerhalb der Spalten liegt.
export function clampTile(t) {
  t.gw = clamp(Math.round(t.gw || 1), 1, COLS);
  t.gh = Math.max(1, Math.round(t.gh || 1));
  t.gx = clamp(Math.round(t.gx || 0), 0, COLS - t.gw);
  t.gy = Math.max(0, Math.round(t.gy || 0));
  return t;
}

// Kollisionen auflösen: die aktive Kachel (und optional weitere "feste")
// bleiben stehen, alle übrigen werden in y-Reihenfolge nach unten geschoben.
// isFixed(p) darf zusätzliche Kacheln als unverschiebbar markieren
// (z.B. Ordner in der Client-Ansicht, damit sie beim Draufziehen nicht fliehen).
export function compact(tiles, active, isFixed = null) {
  const fixed = tiles.filter((p) => p === active || (isFixed ? isFixed(p) : false));
  const placed = [...fixed];
  const rest = tiles.filter((p) => !fixed.includes(p)).sort((a, b) => a.gy - b.gy || a.gx - b.gx);
  for (const p of rest) {
    let guard = 0;
    while (placed.some((q) => overlap(p, q)) && guard++ < 500) p.gy++;
    placed.push(p);
  }
}

// Wie viele Reihen braucht das Raster? (mindestens BASE_ROWS = 4 hoch)
export function neededRows(tiles) {
  return Math.max(BASE_ROWS, ...tiles.map((p) => (p.gy || 0) + (p.gh || 1)), 0);
}

// Erste freie Position für eine gw×gh-Kachel (sonst unten anhängen).
export function findFreeSpot(tiles, gw, gh) {
  const rows = neededRows(tiles) + gh;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x <= COLS - gw; x++) {
      const cand = { gx: x, gy: y, gw, gh };
      if (!tiles.some((p) => overlap(cand, p))) return { gx: x, gy: y };
    }
  }
  return { gx: 0, gy: neededRows(tiles) };
}

// Zellenmaße des Rasters im aktuellen Host ermitteln.
export function cellSize(host) {
  const rect = host.getBoundingClientRect();
  const cw = (rect.width - GAP * (COLS - 1)) / COLS;
  return { rect, cw, ch: ROW_H };
}

// Rasterposition auf eine Karte anwenden.
export function applyGridPos(card, tile) {
  card.style.gridColumn = `${tile.gx + 1} / span ${tile.gw}`;
  card.style.gridRow = `${tile.gy + 1} / span ${tile.gh}`;
  // Handy-Modus (mobile.css stapelt alles in EINE Spalte): "order" sorgt
  // dafür, dass die Stapel-Reihenfolge der visuellen Desktop-Reihenfolge
  // (erst Reihe, dann Spalte) entspricht. Am Desktop wirkungslos, weil dort
  // alle Kacheln explizite gridColumn/gridRow-Positionen haben.
  card.style.order = (tile.gy || 0) * 1000 + (tile.gx || 0);
}

// Host-Höhe nachziehen, wenn Kacheln nach unten wachsen.
export function growHostIfNeeded(host, tiles) {
  const rows = neededRows(tiles);
  host.style.minHeight = `${rows * ROW_H + (rows - 1) * GAP}px`;
}


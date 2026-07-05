// metricshistory.js
// -----------------
// Sammelt die per Socket.IO eingehenden Live-Metriken pro Client in einem
// Ringpuffer, damit die Verlaufs-Diagramme etwas zum Anzeigen haben.
// Jeder Messpunkt bekommt einen Zeitstempel, damit das Zeitspannen-Dropdown
// (z.B. "letzte 5 Minuten" / "letzte Stunde") funktioniert und der Hover-
// Tooltip die genaue Uhrzeit zeigen kann.
//
// Hinweis: Das ist bewusst nur im Browser-Speicher (RAM) und geht beim
// Neuladen der Seite verloren. Für dauerhafte Langzeit-Historie würde man das
// später im Backend in der SQLite-Datenbank speichern.

const MAX_POINTS = 2400; // bei ~5s-Takt etwa 3 Stunden Historie

// clientId -> { cpu: [...], ram: [...], netIn: [...], netOut: [...], ts: [...] }
const history = {};

export function recordMetrics(clientId, metrics) {
  if (!history[clientId]) {
    history[clientId] = { cpu: [], ram: [], netIn: [], netOut: [], ts: [] };
  }
  const h = history[clientId];

  const cpu = metrics.cpuLoad ?? 0;
  const ram = metrics.memTotal ? (metrics.memUsed / metrics.memTotal) * 100 : 0;

  h.cpu.push(cpu);
  h.ram.push(ram);
  h.netIn.push(metrics.netIn ?? 0);
  h.netOut.push(metrics.netOut ?? 0);
  h.ts.push(Date.now());

  for (const key of Object.keys(h)) {
    if (h[key].length > MAX_POINTS) h[key].shift();
  }
}

// Merkt sich, für welche Clients die persistierte Historie schon vom Backend
// geladen wurde, damit das nicht bei jedem Render erneut passiert.
const seeded = new Set();

export function hasSeeded(clientId) {
  return seeded.has(clientId);
}

// Füllt die (leere) In-Memory-Historie mit den vom Backend gespeicherten
// Messpunkten auf. 'points' ist ein Array aus { ts, cpu, ram, net_in, net_out }
// (aufsteigend nach Zeit). Bereits vorhandene, neuere Live-Punkte bleiben
// erhalten - die gespeicherten Punkte werden davorgehängt.
export function seedHistory(clientId, points) {
  seeded.add(clientId);
  if (!points || !points.length) {
    if (!history[clientId]) history[clientId] = { cpu: [], ram: [], netIn: [], netOut: [], ts: [] };
    return;
  }
  const existing = history[clientId] || { cpu: [], ram: [], netIn: [], netOut: [], ts: [] };
  // Nur Live-Punkte behalten, die NEUER sind als der letzte gespeicherte Punkt,
  // um Doppelungen zu vermeiden.
  const lastStoredTs = points[points.length - 1].ts;
  const keep = { cpu: [], ram: [], netIn: [], netOut: [], ts: [] };
  for (let i = 0; i < existing.ts.length; i++) {
    if (existing.ts[i] > lastStoredTs) {
      keep.cpu.push(existing.cpu[i]);
      keep.ram.push(existing.ram[i]);
      keep.netIn.push(existing.netIn[i]);
      keep.netOut.push(existing.netOut[i]);
      keep.ts.push(existing.ts[i]);
    }
  }
  const merged = { cpu: [], ram: [], netIn: [], netOut: [], ts: [] };
  for (const p of points) {
    merged.cpu.push(p.cpu ?? 0);
    merged.ram.push(p.ram ?? 0);
    merged.netIn.push(p.net_in ?? 0);
    merged.netOut.push(p.net_out ?? 0);
    merged.ts.push(p.ts);
  }
  for (let i = 0; i < keep.ts.length; i++) {
    merged.cpu.push(keep.cpu[i]);
    merged.ram.push(keep.ram[i]);
    merged.netIn.push(keep.netIn[i]);
    merged.netOut.push(keep.netOut[i]);
    merged.ts.push(keep.ts[i]);
  }
  for (const key of Object.keys(merged)) {
    while (merged[key].length > MAX_POINTS) merged[key].shift();
  }
  history[clientId] = merged;
}

// Gibt die komplette Historie zurück
export function getHistory(clientId) {
  return history[clientId] || { cpu: [], ram: [], netIn: [], netOut: [], ts: [] };
}

// Gibt nur die Messpunkte der letzten N Millisekunden zurück (für das
// Zeitspannen-Dropdown). rangeMs = null bedeutet "alles".
export function getHistoryRange(clientId, rangeMs) {
  const h = getHistory(clientId);
  if (!rangeMs || !h.ts.length) return h;

  const cutoff = Date.now() - rangeMs;
  // Ersten Index finden, der innerhalb der Zeitspanne liegt
  let startIdx = 0;
  for (let i = 0; i < h.ts.length; i++) {
    if (h.ts[i] >= cutoff) { startIdx = i; break; }
    startIdx = i;
  }
  return {
    cpu: h.cpu.slice(startIdx),
    ram: h.ram.slice(startIdx),
    netIn: h.netIn.slice(startIdx),
    netOut: h.netOut.slice(startIdx),
    ts: h.ts.slice(startIdx),
  };
}

// Verfügbare Zeitspannen fürs Dropdown (Label -> Millisekunden, null = alles)
export const TIME_RANGES = [
  { label: "5 Min", ms: 5 * 60 * 1000 },
  { label: "15 Min", ms: 15 * 60 * 1000 },
  { label: "1 Std", ms: 60 * 60 * 1000 },
  { label: "3 Std", ms: 3 * 60 * 60 * 1000 },
  { label: "Alles", ms: null },
];

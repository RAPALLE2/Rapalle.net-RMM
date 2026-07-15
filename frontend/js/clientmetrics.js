// clientmetrics.js
// ----------------
// Großer, modularer Metrik-Katalog für EINEN Client (Client-Detailansicht).
// Gegenstück zu metriccatalog.js (Flotte): jedes Preset liest aus der bereits
// vorhandenen Telemetrie EINES Clients (client.metrics) - keine Agent-Änderung
// nötig. Presets werden in der Client-Ansicht als eigene Panels eingebunden
// (dashlayout.js, Typ "cmetric") und können in mehreren Darstellungen gezeigt
// werden: number, gauge, donut, line (Verlauf), bars (z.B. je Datenträger/Kern),
// info (Text-Zeilen).
//
// Preset-Felder:
//   id, group, label, charts:[kinds], unit?, format?(v), max?(client)|Zahl,
//   value?(client)   -> Zahl (für number/gauge/donut/line)
//   rows?(client)    -> [{label, value?, raw?}] (für bars/info)
//   text?(client)    -> String (für info, einzeilig)

import { esc, formatBytes, formatUptime } from "./utils.js";
import {
  attachHoverTip, scaleToContainer, buildFleetDonut, showFleetTip, hideFleetTip,
  timeSeriesChart,
} from "./fleetcharts.js";

const m = (c) => (c && c.metrics) || {};
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const pct = (used, total) => (total ? (used / total) * 100 : null);
const fmtPct = (v) => `${Math.round(v)}%`;
const fmtW = (v) => `${Math.round(v * 10) / 10} W`;
const fmtMs = (v) => `${Math.round(v * 10) / 10} ms`;
const fmtMHz = (v) => `${Math.round(v)} MHz`;
const fmtC = (v) => `${Math.round(v * 10) / 10} °C`;
const fmtBps = (v) => `${formatBytes(v)}/s`;
const fmtRpm = (v) => `${Math.round(v)} U/min`;

// RAM-Modul hübsch beschreiben (Agent liefert Strings ODER Objekte).
function ramModuleDesc(mod) {
  if (typeof mod === "string") return mod;
  if (!mod || typeof mod !== "object") return String(mod ?? "—");
  const size = mod.size ? formatBytes(mod.size) : (mod.size_str || "");
  const speed = mod.speed ? `@${mod.speed}MHz` : (mod.speed_str ? `@${mod.speed_str}` : "");
  return [mod.vendor, size, speed].filter(Boolean).join(" ") || "—";
}

export const CLIENT_PRESETS = [
  // =============== CPU ===============
  { id: "c.cpuLoad", group: "CPU", label: "CPU-Auslastung",
    charts: ["gauge", "donut", "line", "number"], unit: "%", format: fmtPct, max: 100,
    value: (c) => num(m(c).cpuLoad) },
  { id: "c.cpuFreq", group: "CPU", label: "CPU-Takt",
    charts: ["gauge", "line", "number"], format: fmtMHz,
    max: (c) => num(m(c).cpuMaxFreq) || 5000,
    value: (c) => num(m(c).cpuFreq) },
  { id: "c.cpuTemp", group: "CPU", label: "CPU-Temperatur",
    charts: ["gauge", "line", "number"], format: fmtC, max: 100,
    value: (c) => num(m(c).cpuTemp) },
  { id: "c.load", group: "CPU", label: "Load Average (1/5/15 min)",
    charts: ["info", "line"],
    value: (c) => num(m(c).load1),
    rows: (c) => [
      { label: "1 min", raw: m(c).load1 != null ? String(m(c).load1) : "—" },
      { label: "5 min", raw: m(c).load5 != null ? String(m(c).load5) : "—" },
      { label: "15 min", raw: m(c).load15 != null ? String(m(c).load15) : "—" },
    ] },
  { id: "c.cores", group: "CPU", label: "Kerne / Threads",
    charts: ["info", "number"], value: (c) => num(m(c).cpuThreads),
    rows: (c) => [
      { label: "Physische Kerne", raw: String(m(c).cpuCores ?? "—") },
      { label: "Threads (logisch)", raw: String(m(c).cpuThreads ?? "—") },
      { label: "Max. Takt", raw: m(c).cpuMaxFreq ? fmtMHz(m(c).cpuMaxFreq) : "—" },
    ] },
  { id: "c.procs", group: "CPU", label: "Prozessanzahl",
    charts: ["number", "line"], value: (c) => num(m(c).procCount) },
  { id: "c.cpuPerCore", group: "CPU", label: "Auslastung je Kern",
    charts: ["bars", "info"],
    rows: (c) => (m(c).cpuPerCore || []).map((v, i) => ({
      label: `Kern ${i + 1}`, value: v, raw: `${Math.round(v)}%`,
    })) },
  { id: "c.cpuFreqPerCore", group: "CPU", label: "Takt je Kern",
    charts: ["bars", "info"],
    rows: (c) => (m(c).cpuFreqPerCore || []).map((v, i) => ({
      label: `Kern ${i + 1}`, value: v, raw: fmtMHz(v),
    })) },

  // =============== GPU ===============
  { id: "c.gpuLoad", group: "GPU", label: "GPU-Auslastung",
    charts: ["gauge", "donut", "line", "number"], unit: "%", format: fmtPct, max: 100,
    value: (c) => num((m(c).gpus || [])[0]?.load) },
  { id: "c.gpuTemp", group: "GPU", label: "GPU-Temperatur",
    charts: ["gauge", "line", "number"], format: fmtC, max: 100,
    value: (c) => num((m(c).gpus || [])[0]?.temp) },
  { id: "c.gpuPower", group: "GPU", label: "GPU-Leistung",
    charts: ["number", "line", "gauge"], format: fmtW, max: 400,
    value: (c) => num((m(c).gpus || [])[0]?.power) },
  { id: "c.gpuMem", group: "GPU", label: "GPU-Speicher",
    charts: ["donut", "number", "line"], format: formatBytes,
    max: (c) => num((m(c).gpus || [])[0]?.memTotal) || 1,
    value: (c) => num((m(c).gpus || [])[0]?.memUsed) },
  { id: "c.gpus", group: "GPU", label: "GPUs (alle, Auslastung)",
    charts: ["bars", "info"],
    rows: (c) => (m(c).gpus || []).map((g, i) => ({
      label: g.name || `GPU ${i + 1}`,
      value: g.load || 0,
      raw: [g.load != null ? `${Math.round(g.load)}%` : null,
            g.temp != null ? fmtC(g.temp) : null,
            g.power != null ? fmtW(g.power) : null,
            g.memTotal ? `${formatBytes(g.memUsed || 0)}/${formatBytes(g.memTotal)}` : null,
           ].filter(Boolean).join(" · ") || "—",
    })) },

  // =============== RAM ===============
  { id: "c.ramPct", group: "RAM", label: "RAM-Auslastung",
    charts: ["gauge", "donut", "line", "number"], unit: "%", format: fmtPct, max: 100,
    value: (c) => num(pct(m(c).memUsed, m(c).memTotal)) },
  { id: "c.ramUsed", group: "RAM", label: "RAM belegt",
    charts: ["number", "line", "donut"], format: formatBytes,
    max: (c) => num(m(c).memTotal) || 1,
    value: (c) => num(m(c).memUsed) },
  { id: "c.ramFree", group: "RAM", label: "RAM frei",
    charts: ["number", "line"], format: formatBytes,
    value: (c) => num(m(c).memAvailable ?? (m(c).memTotal - m(c).memUsed)) },
  { id: "c.swap", group: "RAM", label: "Swap-Auslastung",
    charts: ["gauge", "donut", "number"], unit: "%", format: fmtPct, max: 100,
    value: (c) => num(pct(m(c).swapUsed, m(c).swapTotal)) },
  { id: "c.ramInfo", group: "RAM", label: "RAM-Übersicht",
    charts: ["info"],
    rows: (c) => [
      { label: "Gesamt", raw: m(c).memTotal ? formatBytes(m(c).memTotal) : "—" },
      { label: "Belegt", raw: m(c).memUsed ? formatBytes(m(c).memUsed) : "—" },
      { label: "Frei", raw: m(c).memAvailable != null ? formatBytes(m(c).memAvailable) : "—" },
      { label: "Cache", raw: m(c).memCached ? formatBytes(m(c).memCached) : null },
      { label: "Puffer", raw: m(c).memBuffers ? formatBytes(m(c).memBuffers) : null },
      { label: "Swap", raw: m(c).swapTotal ? `${formatBytes(m(c).swapUsed || 0)} / ${formatBytes(m(c).swapTotal)}` : null },
    ].filter((r) => r.raw != null) },

  // =============== Disk ===============
  { id: "c.diskPct", group: "Disk", label: "Disk-Auslastung (System)",
    charts: ["gauge", "donut", "number"], unit: "%", format: fmtPct, max: 100,
    value: (c) => num(pct(m(c).diskUsed, m(c).diskTotal)) },
  { id: "c.disks", group: "Disk", label: "Datenträger (alle)",
    charts: ["bars", "info"],
    rows: (c) => (m(c).disks || []).map((d) => ({
      label: d.device || d.mountpoint || "Disk",
      value: d.total ? (d.used / d.total) * 100 : 0,
      raw: `${formatBytes(d.used)} / ${formatBytes(d.total)}`,
    })) },
  { id: "c.diskRead", group: "Disk", label: "Disk-Leserate (R-Speed)",
    charts: ["number", "line"], format: fmtBps,
    value: (c) => num(m(c).diskRead) },
  { id: "c.diskWrite", group: "Disk", label: "Disk-Schreibrate (W-Speed)",
    charts: ["number", "line"], format: fmtBps,
    value: (c) => num(m(c).diskWrite) },
  { id: "c.diskIO", group: "Disk", label: "Disk-Durchsatz (R+W)",
    charts: ["number", "line", "info"], format: fmtBps,
    value: (c) => (m(c).diskRead != null ? (m(c).diskRead || 0) + (m(c).diskWrite || 0) : null),
    rows: (c) => [
      { label: "Lesen", raw: m(c).diskRead != null ? fmtBps(m(c).diskRead) : "—" },
      { label: "Schreiben", raw: m(c).diskWrite != null ? fmtBps(m(c).diskWrite) : "—" },
    ] },

  // =============== Netzwerk ===============
  { id: "c.netIn", group: "Netzwerk", label: "Netzwerk ↓ (Empfang)",
    charts: ["number", "line"], format: fmtBps, value: (c) => num(m(c).netIn) },
  { id: "c.netOut", group: "Netzwerk", label: "Netzwerk ↑ (Senden)",
    charts: ["number", "line"], format: fmtBps, value: (c) => num(m(c).netOut) },
  { id: "c.netBoth", group: "Netzwerk", label: "Netzwerk gesamt (↓+↑)",
    charts: ["number", "line", "info"], format: fmtBps,
    value: (c) => (m(c).netIn != null ? (m(c).netIn || 0) + (m(c).netOut || 0) : null),
    rows: (c) => [
      { label: "↓ Empfang", raw: m(c).netIn != null ? fmtBps(m(c).netIn) : "—" },
      { label: "↑ Senden", raw: m(c).netOut != null ? fmtBps(m(c).netOut) : "—" },
    ] },
  { id: "c.pingGoogle", group: "Netzwerk", label: "Ping Google (8.8.8.8)",
    charts: ["number", "line", "gauge"], format: fmtMs, max: 200,
    value: (c) => num(m(c).ping?.google) },
  { id: "c.pingCf", group: "Netzwerk", label: "Ping Cloudflare (1.1.1.1)",
    charts: ["number", "line", "gauge"], format: fmtMs, max: 200,
    value: (c) => num(m(c).ping?.cloudflare) },

  // =============== Strom & Sensoren ===============
  { id: "c.power", group: "Strom & Sensoren", label: "Stromverbrauch",
    charts: ["number", "line", "gauge"], format: fmtW, max: 300,
    value: (c) => num(m(c).powerWatts) },
  { id: "c.fan", group: "Strom & Sensoren", label: "Lüfterdrehzahl",
    charts: ["number", "line", "gauge"], format: fmtRpm, max: 5000,
    value: (c) => num(m(c).fanSpeed) },
  { id: "c.fansAll", group: "Strom & Sensoren", label: "Lüfter (alle)",
    charts: ["bars", "info"],
    rows: (c) => {
      const f = m(c).fans || {};
      const keys = Object.keys(f);
      return keys.length ? keys.map((k) => ({ label: k, value: f[k], raw: fmtRpm(f[k]) }))
                         : [{ label: "Lüfter", raw: "nicht verfügbar" }];
    } },
  { id: "c.tempsAll", group: "Strom & Sensoren", label: "Temperaturen (alle Sensoren)",
    charts: ["bars", "info"],
    rows: (c) => {
      const tps = m(c).temps || {};
      const keys = Object.keys(tps);
      return keys.length ? keys.map((k) => ({ label: k, value: tps[k], raw: fmtC(tps[k]) }))
                         : [{ label: "Sensoren", raw: "nicht verfügbar" }];
    } },
  { id: "c.battery", group: "Strom & Sensoren", label: "Akku",
    charts: ["gauge", "donut", "number", "info"], unit: "%", format: fmtPct, max: 100,
    value: (c) => num(m(c).battery?.percent),
    rows: (c) => {
      const b = m(c).battery;
      if (!b) return [{ label: "Akku", raw: "nicht vorhanden" }];
      return [
        { label: "Ladestand", raw: `${b.percent}%` },
        { label: "Netzteil", raw: b.plugged ? "angeschlossen ⚡" : "getrennt" },
        { label: "Restlaufzeit", raw: b.secsleft ? formatUptime(b.secsleft) : "—" },
      ];
    } },

  // =============== Hardware (statisch) ===============
  { id: "c.cpuModel", group: "Hardware", label: "CPU-Modell",
    charts: ["info"], text: (c) => m(c).cpuModel || "—" },
  { id: "c.gpuModel", group: "Hardware", label: "GPU-Modell(e)",
    charts: ["info"],
    rows: (c) => {
      const g = m(c).gpuModels || [];
      return g.length ? g.map((x, i) => ({ label: `GPU ${i + 1}`, raw: x })) : [{ label: "GPU", raw: "—" }];
    } },
  { id: "c.ramModules", group: "Hardware", label: "RAM-Module",
    charts: ["info"],
    rows: (c) => {
      const mods = m(c).ramModules || [];
      return mods.length ? mods.map((x, i) => ({ label: `Modul ${i + 1}`, raw: ramModuleDesc(x) })) : [{ label: "RAM", raw: "—" }];
    } },
  { id: "c.arch", group: "Hardware", label: "Architektur",
    charts: ["info"], text: (c) => m(c).arch || c.arch || "—" },
  { id: "c.sysinfo", group: "Hardware", label: "Systeminfo (kompakt)",
    charts: ["info"],
    rows: (c) => [
      { label: "CPU", raw: m(c).cpuModel || "—" },
      { label: "GPU", raw: (m(c).gpuModels || []).join(", ") || "—" },
      { label: "RAM", raw: (m(c).ramModules || []).map(ramModuleDesc).join(", ") || (m(c).memTotal ? formatBytes(m(c).memTotal) : "—") },
      { label: "Architektur", raw: m(c).arch || c.arch || "—" },
      { label: "OS", raw: `${c.platform || "?"} ${c.release || ""}`.trim() },
      { label: "IP", raw: c.ip || "—" },
      { label: "Agent", raw: c.agent_version || "—" },
    ] },

  // =============== System ===============
  { id: "c.uptime", group: "System", label: "Uptime",
    charts: ["number"], format: (v) => formatUptime(v),
    value: (c) => num(m(c).uptime) },

  // =============== Netzwerk-Identität (statisch) ===============
  { id: "c.ipAddr", group: "Identität", label: "IP-Adresse",
    charts: ["info"], text: (c) => c.ip || m(c).ip || "—" },
  { id: "c.macAddr", group: "Identität", label: "MAC-Adresse",
    charts: ["info"], text: (c) => m(c).mac || "—" },
  { id: "c.hostnameId", group: "Identität", label: "Hostname",
    charts: ["info"], text: (c) => m(c).hostname || c.hostname || "—" },
  { id: "c.osFull", group: "Identität", label: "Betriebssystem",
    charts: ["info"], text: (c) => `${c.platform || "?"} ${c.release || ""}`.trim() || "—" },
  { id: "c.agentVer", group: "Identität", label: "Agent-Version",
    charts: ["info"], text: (c) => c.agent_version || "—" },
  { id: "c.deviceType", group: "Identität", label: "Gerätetyp",
    charts: ["info"], text: (c) => ({ vm: "Virtuelle Maschine", lxc: "LXC-Container", physical: "Physisches Gerät" }[c.device_type || "physical"]) },
  { id: "c.interfaces", group: "Identität", label: "Netzwerk-Interfaces",
    charts: ["info"],
    rows: (c) => {
      const ifs = m(c).interfaces || [];
      return ifs.length
        ? ifs.map((n) => ({ label: n.name, raw: [n.ipv4, n.mac].filter(Boolean).join("  ·  ") || "—" }))
        : [{ label: "Interfaces", raw: "—" }];
    } },
  { id: "c.netIdentity", group: "Identität", label: "Netzwerk-Übersicht",
    charts: ["info"],
    rows: (c) => [
      { label: "Hostname", raw: m(c).hostname || c.hostname || "—" },
      { label: "IP-Adresse", raw: c.ip || m(c).ip || "—" },
      { label: "MAC-Adresse", raw: m(c).mac || "—" },
      { label: "Interfaces", raw: String((m(c).interfaces || []).length || "—") },
      { label: "OS", raw: `${c.platform || "?"} ${c.release || ""}`.trim() || "—" },
      { label: "Gerätetyp", raw: ({ vm: "VM", lxc: "LXC", physical: "Physisch" }[c.device_type || "physical"]) },
    ] },
];

export function clientPresetById(id) { return CLIENT_PRESETS.find((p) => p.id === id) || null; }

// Zusätzliche Ansichten, die automatisch für JEDES passende Client-Preset
// verfügbar sind: Wert-Presets bekommen area/spark/progress/ring/stat,
// Zeilen-Presets zusätzlich columns.
export const CLIENT_VALUE_EXTRA_KINDS = ["area", "spark", "progress", "ring", "stat"];
export function availableClientKinds(preset) {
  if (!preset) return ["number"];
  const kinds = [...(preset.charts || [])];
  const add = (k) => { if (!kinds.includes(k)) kinds.push(k); };
  if (preset.value) for (const k of CLIENT_VALUE_EXTRA_KINDS) add(k);
  if (preset.rows) add("columns");
  // Kreis-Darstellungen gegenseitig anbieten (wie im Dashboard):
  if (kinds.includes("pie") || (preset.rows && kinds.includes("donut"))) { add("pie"); add("donut"); }
  if (preset.value && (kinds.includes("gauge") || kinds.includes("donut") || kinds.includes("ring"))) {
    add("gauge"); add("donut"); add("ring");
  }
  return kinds;
}

// -----------------------------------------------------------------
// Verfügbarkeit nach Gerätetyp: VMs und vor allem LXC-Container haben keinen
// direkten Hardwarezugriff - Sensoren (Temperatur/Lüfter/Strom), physische
// Hardware-Infos und GPU-Telemetrie sind dort nicht sinnvoll. Solche Presets
// werden für VM/LXC-Clients ausgeblendet (im "+"-Picker und als Panel).
// -----------------------------------------------------------------
// Gruppen, die auf VM/LXC komplett wegfallen (kein echter Hardwarezugriff):
const HIDE_GROUPS_VM = new Set(["Strom & Sensoren", "GPU", "Hardware"]);
const HIDE_GROUPS_LXC = new Set(["Strom & Sensoren", "GPU", "Hardware"]);
// Einzelne Presets, die zusätzlich wegfallen:
const HIDE_IDS_VM = new Set(["c.cpuTemp", "c.cpuFreq", "c.cpuFreqPerCore"]);
const HIDE_IDS_LXC = new Set([
  "c.cpuTemp", "c.cpuFreq", "c.cpuFreqPerCore",
  "c.diskRead", "c.diskWrite", "c.diskIO",     // Container sehen Host-IO
  "c.disks", "c.diskPct",                        // Datenträger = Host
  "c.uptime",                                   // = Host-Uptime, irreführend
]);

// device_type: 'physical' | 'vm' | 'lxc'
export function presetAvailable(preset, deviceType) {
  const dt = deviceType || "physical";
  if (dt === "physical") return true;
  if (dt === "vm") return !HIDE_GROUPS_VM.has(preset.group) && !HIDE_IDS_VM.has(preset.id);
  if (dt === "lxc") return !HIDE_GROUPS_LXC.has(preset.group) && !HIDE_IDS_LXC.has(preset.id);
  return true;
}

export function clientPresetsByGroup(deviceType) {
  const map = new Map();
  for (const p of CLIENT_PRESETS) {
    if (deviceType && !presetAvailable(p, deviceType)) continue;
    if (!map.has(p.group)) map.set(p.group, []);
    map.get(p.group).push(p);
  }
  return [...map.entries()];
}

export function formatClientValue(preset, v) {
  if (v === null || v === undefined) return "—";
  if (preset.format) return preset.format(v);
  if (preset.unit === "%") return fmtPct(v);
  return String(Math.round(v * 100) / 100);
}

// -----------------------------------------------------------------
// Rollierende Verlaufs-Historie je (Client, Panel) für Linien-Charts.
// Wird bei jedem Render fortgeschrieben (Client-Ansicht rendert bei jedem
// Metrik-Tick neu); doppelte Punkte < 2 s werden übersprungen.
// -----------------------------------------------------------------
const _hist = new Map();
const MAX_POINTS = 120;
function pushHistory(key, v) {
  if (v === null || v === undefined) return _hist.get(key) || { ts: [], v: [] };
  const h = _hist.get(key) || { ts: [], v: [] };
  const now = Date.now();
  if (!h.ts.length || now - h.ts[h.ts.length - 1] > 2000) {
    h.ts.push(now); h.v.push(v);
    if (h.ts.length > MAX_POINTS) { h.ts.shift(); h.v.shift(); }
    _hist.set(key, h);
  }
  return h;
}

function resolveMax(preset, client) {
  const mx = preset.max;
  if (typeof mx === "function") { try { return mx(client) || 100; } catch { return 100; } }
  return mx || 100;
}

// -----------------------------------------------------------------
// Renderer: füllt ein Ziel-Element mit der gewählten Darstellung.
//   panel = { id, metric:<preset id>, kind }
//
// DESIGN-PRINZIP (identisch zum Dashboard/dashwidgets.js):
//   Jede Darstellung ist auf eine 1x1-Rasterzelle optimiert (feste natürliche
//   Größe, gut lesbare Schriften) und wird per scaleToContainer proportional
//   auf die tatsächliche Panelgröße skaliert - 2x2 = gleicher Inhalt, gleiche
//   Proportionen, ~doppelt so groß.
//
// HOVER-PRINZIP:
//   - Einzelwerte: Widget-Tooltip mit Titel + exaktem Live-Wert.
//   - Zeilen (info/bars/columns, z.B. "Systeminfo (kompakt)"): Hover PRO
//     ZEILE - die Zeile wird hervorgehoben und der Tooltip zeigt Label +
//     vollständigen Wert GENAU DIESER Zeile (z.B. "CPU" + CPU-Modellname).
//   - Verläufe (line/area/spark): getimestampter Hover wie das Netzwerk-
//     Diagramm im Metrics-Panel (Hover-Linie, Punkt, Uhrzeit + Wert).
// -----------------------------------------------------------------

// Natürliche Inhaltsgröße einer 1x1-Zelle (Body ~240x104). Alle
// Darstellungen sind auf GENAU diese Box designt und füllen sie aus.
const NAT_W = 240;
const NAT_H = 104;

// Zeilen-Hover: Zeile hervorheben + Tooltip mit dem Wert dieser Zeile.
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
  el.style.cssText = "font-size:10px;color:var(--subtext);padding:2px 4px;cursor:default";
  el.textContent = `+ ${hidden.length} weitere`;
  attachHoverTip(el, () => hidden.slice(0, 15).map((r) => `${esc(r.label)}: <b>${esc(r.raw != null ? r.raw : String(r.value ?? "—"))}</b>`).join("<br>")
    + (hidden.length > 15 ? `<br><span style="color:var(--subtext)">…</span>` : ""));
  return el;
}

export function renderClientMetric(target, client, panel) {
  target.innerHTML = "";
  const preset = clientPresetById(panel.metric);
  if (!preset) { target.innerHTML = `<div style="color:var(--subtext);font-size:12px">Unbekannte Metrik.</div>`; return; }
  const kind = panel.kind && availableClientKinds(preset).includes(panel.kind) ? panel.kind : preset.charts[0];
  const v = preset.value ? preset.value(client) : null;

  // Skalier-Holder: 1x1-optimierter Inhalt wächst proportional mit der Zelle.
  const holder = document.createElement("div");
  holder.className = "cmetric-fit-holder";
  holder.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:visible";
  target.appendChild(holder);
  const _t = target;            // Original für Hover-Bindung
  target = holder;              // Diagramme rendern in den Holder

  // Widget-weiter Wert-Tooltip NUR für Einzelwert-Darstellungen; Zeilen-,
  // Segment- und Verlaufs-Darstellungen haben feinere Per-Element-Hover
  // (sonst überdeckte der Widget-Tooltip - z.B. "Systeminfo (kompakt)" -
  // den Wert des gehoverten Elements). Listener nur EINMAL anhängen, die
  // Tooltip-Funktion (_tipFn) wird pro Render neu gesetzt (null = aus).
  if (!_t._hoverTipAttached) {
    _t._hoverTipAttached = true;
    attachHoverTip(_t, () => (typeof _t._tipFn === "function" ? _t._tipFn() : null));
  }
  const valueTipKinds = ["number", "gauge", "ring", "progress", "stat"];
  const isValueDonut = kind === "donut" && !(preset.rows && !preset.value);
  const isSingleText = !preset.rows && !preset.value && (kind === "info" || preset.charts[0] === "info");
  if (valueTipKinds.includes(kind) || isValueDonut) {
    _t._tipFn = () => {
      const val = preset.value ? preset.value(client) : null;
      const txt = val === null || val === undefined ? "—" : formatClientValue(preset, val);
      const mx = resolveMax(preset, client);
      const maxTxt = (kind === "gauge" || kind === "donut") && mx
        ? ` <span style="color:var(--subtext)">/ ${esc(formatClientValue(preset, mx))}</span>` : "";
      return `<b>${esc(panel.title || preset.label)}</b><br>${esc(txt)}${maxTxt}`;
    };
  } else if (isSingleText) {
    // Einzeiliger Text (z.B. CPU-Modell): Tooltip mit dem VOLLEN Wert
    // (falls die Anzeige abgeschnitten ist).
    _t._tipFn = () => `<b>${esc(panel.title || preset.label)}</b><br>${esc(preset.text ? preset.text(client) : formatClientValue(preset, v))}`;
  } else {
    _t._tipFn = null;
  }

  if (kind === "number") {
    const txt = formatClientValue(preset, v);
    const fs = txt.length > 12 ? 22 : txt.length > 8 ? 28 : 38;
    target.innerHTML = `<div style="width:${NAT_W}px;height:${NAT_H}px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center">
      <div style="font-size:${fs}px;font-weight:800;line-height:1.05">${esc(txt)}</div>
      <div style="font-size:12px;color:var(--subtext);margin-top:5px;max-width:${NAT_W - 10}px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</div></div>`;
    scaleToContainer(holder); return;
  }

  if (kind === "gauge") {
    // Halbkreis-Gauge: nutzt die volle 1x1-Box, Wert + Label IM Bogen.
    const max = resolveMax(preset, client);
    const p = v === null ? 0 : Math.max(0, Math.min(100, (v / max) * 100));
    const w = NAT_W, h = NAT_H, cx = w / 2, cy = h - 4, r = 86, stroke = 17;
    const ang = Math.PI * (p / 100);
    const ex = cx - r * Math.cos(ang), ey = cy - r * Math.sin(ang);
    const color = p > 85 ? "#ff4d6d" : p > 65 ? "#f5a524" : "#3ecf8e";
    target.innerHTML = `<div style="position:relative;width:${w}px;height:${h}px">
      <svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" style="overflow:visible">
        <path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}" fill="none" stroke="var(--border)" stroke-width="${stroke}" stroke-linecap="round"/>
        ${v === null ? "" : `<path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${ex.toFixed(2)} ${ey.toFixed(2)}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"/>`}
      </svg>
      <div style="position:absolute;left:0;right:0;bottom:0;display:flex;flex-direction:column;align-items:center;pointer-events:none">
        <div style="font-size:25px;font-weight:800;line-height:1.05">${esc(formatClientValue(preset, v))}</div>
        <div style="font-size:11px;color:var(--subtext);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</div>
      </div></div>`;
    scaleToContainer(holder); return;
  }

  // Kategorische Verteilung (rows) als Pie oder mehrsegmentiger Donut -
  // buildFleetDonut bringt Per-Segment-Hover (Highlight + Tooltip) mit.
  if ((kind === "pie" || kind === "donut") && preset.rows && !preset.value) {
    const segs = (preset.rows(client) || []).filter((r) => (r.value || 0) > 0)
      .map((r, i) => ({ label: r.label, count: r.value, color: r.color, items: r.items }));
    const box = document.createElement("div");
    box.style.cssText = `width:${NAT_W}px`;
    box.appendChild(buildFleetDonut(segs, { card: false, size: 100 }));
    target.appendChild(box);
    scaleToContainer(holder); return;
  }

  if (kind === "donut") {
    // Donut SEITLICH (links), rechts großer Wert + Label - volle 1x1-Box.
    const max = resolveMax(preset, client);
    const frac = v === null ? 0 : Math.max(0, Math.min(1, v / max));
    const size = 96, sw = 14, r = (size - sw) / 2 - 1, cx = size / 2, cy = size / 2;
    const C = 2 * Math.PI * r, len = frac * C;
    const valTxt = formatClientValue(preset, v);
    target.innerHTML = `<div style="width:${NAT_W}px;height:${NAT_H}px;display:flex;align-items:center;gap:14px">
      <div style="position:relative;width:${size}px;height:${size}px;flex:none">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${sw}"/>
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--accent)" stroke-width="${sw}"
            stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-linecap="round"
            transform="rotate(-90 ${cx} ${cy})"/>
        </svg>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center">
          <div style="font-size:17px;font-weight:700">${Math.round(frac * 100)}%</div>
        </div>
      </div>
      <div style="flex:1;min-width:0">
        <div style="font-size:${valTxt.length > 9 ? 18 : 23}px;font-weight:800;line-height:1.1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(valTxt)}</div>
        <div style="font-size:12px;color:var(--subtext);margin-top:3px;line-height:1.3">${esc(preset.label)}</div>
        <div style="font-size:10.5px;color:var(--subtext);margin-top:3px">von ${esc(formatClientValue(preset, max))}</div>
      </div></div>`;
    scaleToContainer(holder); return;
  }

  if (kind === "line" || kind === "area" || kind === "spark") {
    // Getimestampter Verlauf (wie das Netzwerk-Diagramm im Metrics-Panel),
    // füllt die komplette 1x1-Box: Kopfzeile oben, Chart nutzt den Rest.
    const h = pushHistory(`${client.id}:${panel.id}`, v);
    const data = h.v.length ? h.v : [v ?? 0];
    const ts = h.ts.length ? h.ts : [Date.now()];
    const spark = kind === "spark";
    const wrap = document.createElement("div");
    wrap.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;flex-direction:column;justify-content:space-between`;
    const head = document.createElement("div");
    head.style.cssText = "display:flex;justify-content:space-between;align-items:baseline;gap:8px";
    head.innerHTML = spark
      ? `<span style="font-size:24px;font-weight:800;line-height:1.1">${esc(formatClientValue(preset, v))}</span>
         <span style="font-size:11px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</span>`
      : `<span style="font-size:12px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</span>
         <span style="font-size:17px;font-weight:800">${esc(formatClientValue(preset, v))}</span>`;
    wrap.appendChild(head);
    wrap.appendChild(timeSeriesChart(
      [{ label: panel.title || preset.label, color: "var(--accent)", values: data, timestamps: ts }],
      { width: NAT_W, height: spark ? 66 : 80, mode: kind,
        yMax: typeof preset.max === "number" ? Math.max(preset.max, ...data) : null,
        formatValue: (x) => formatClientValue(preset, x) },
    ));
    target.appendChild(wrap);
    scaleToContainer(holder); return;
  }

  if (kind === "progress") {
    const max = resolveMax(preset, client) || 100;
    const pct = Math.max(0, Math.min(100, ((v || 0) / max) * 100));
    const color = pct > 85 ? "#ff4d6d" : pct > 65 ? "#f5a524" : "var(--accent)";
    target.innerHTML = `<div style="width:${NAT_W}px;height:${NAT_H}px;display:flex;flex-direction:column;justify-content:center">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px;gap:8px">
        <span style="font-size:13px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(preset.label)}</span>
        <span style="font-size:22px;font-weight:800">${esc(formatClientValue(preset, v))}</span>
      </div>
      <div style="height:18px;border-radius:9px;background:var(--panel-2);overflow:hidden">
        <div style="height:100%;width:${pct.toFixed(1)}%;background:${color};border-radius:9px;transition:width .3s"></div>
      </div>
      <div style="font-size:11px;color:var(--subtext);margin-top:6px;text-align:right">${Math.round(pct)}% von ${esc(formatClientValue(preset, max))}</div></div>`;
    scaleToContainer(holder); return;
  }

  if (kind === "ring") {
    // Ring SEITLICH (links), rechts großer Wert + Label - volle 1x1-Box.
    const max = resolveMax(preset, client) || 100;
    const pct = Math.max(0, Math.min(100, ((v || 0) / max) * 100));
    const size = 92, stroke = 10, r = (size - stroke) / 2 - 1, cx = size / 2, cy = size / 2;
    const C = 2 * Math.PI * r, len = (pct / 100) * C;
    const valTxt = formatClientValue(preset, v);
    target.innerHTML = `<div style="width:${NAT_W}px;height:${NAT_H}px;display:flex;align-items:center;gap:14px">
      <div style="position:relative;width:${size}px;height:${size}px;flex:none">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"/>
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--accent)" stroke-width="${stroke}"
            stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})"/>
        </svg>
        <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center">
          <div style="font-size:16px;font-weight:700">${Math.round(pct)}%</div>
        </div>
      </div>
      <div style="flex:1;min-width:0">
        <div style="font-size:${valTxt.length > 9 ? 18 : 23}px;font-weight:800;line-height:1.1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(valTxt)}</div>
        <div style="font-size:12px;color:var(--subtext);margin-top:3px;line-height:1.3">${esc(preset.label)}</div>
      </div></div>`;
    scaleToContainer(holder); return;
  }

  if (kind === "stat") {
    const h = pushHistory(`${client.id}:${panel.id}`, v);
    const data = h.v.length ? h.v : [v ?? 0];
    const ts = h.ts.length ? h.ts : [Date.now()];
    const ref = data.length > 1 ? data[0] : (v || 0);
    const diff = (v || 0) - (ref || 0);
    const up = diff > 0, flat = Math.abs(diff) < 1e-9;
    const trendColor = flat ? "var(--subtext)" : (up ? "#f5a524" : "#3ecf8e");
    const wrap = document.createElement("div");
    wrap.style.cssText = `width:${NAT_W}px;height:${NAT_H}px;display:flex;align-items:center;gap:12px`;
    wrap.innerHTML = `
      <div style="flex:none;max-width:118px">
        <div style="font-size:27px;font-weight:800;line-height:1.1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(formatClientValue(preset, v))}</div>
        <div style="font-size:11px;color:var(--subtext);margin-top:2px">${esc(preset.label)}</div>
      </div>
      <div style="text-align:right;flex:1;min-width:0">
        <div style="font-size:14px;font-weight:700;color:${trendColor};margin-bottom:2px">${flat ? "→" : (up ? "▲" : "▼")} ${esc(formatClientValue(preset, Math.abs(diff)))}</div>
        <div class="stat-spark"></div>
      </div>`;
    wrap.querySelector(".stat-spark").appendChild(timeSeriesChart(
      [{ label: panel.title || preset.label, color: trendColor, values: data, timestamps: ts }],
      { width: 112, height: 52, mode: "spark", formatValue: (x) => formatClientValue(preset, x) },
    ));
    target.appendChild(wrap);
    scaleToContainer(holder); return;
  }

  if (kind === "columns") {
    // Säulendiagramm in fester 1x1-Box: die Säulen teilen sich die VOLLE
    // Breite auf. Hover: Säule hervorheben, andere dimmen, Tooltip mit
    // Label + exaktem Wert.
    const all = (preset.rows ? preset.rows(client) : []);
    const rows = all.slice(0, 6), hidden = all.slice(6);
    const W = NAT_W, H = NAT_H;
    const padL = 28, padR = 4, padT = 14, padB = 21;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const rawMax = Math.max(1, ...rows.map((r) => r.value || 0));
    const exp = Math.floor(Math.log10(rawMax)), base = Math.pow(10, exp), f = rawMax / base;
    const niceMax = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * base;
    const slot = rows.length ? plotW / rows.length : plotW;
    const bw = Math.max(10, slot - 9);
    const shortV = (x) => { try { return preset.format ? preset.format(Math.round(x)) : (x >= 1e3 ? (x / 1e3).toFixed(1) + "k" : String(Math.round(x))); } catch { return String(Math.round(x)); } };
    const grid = [0, 0.5, 1].map((fr) => {
      const y = padT + plotH - fr * plotH;
      return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-width="1" opacity="0.5"/>
        <text x="${padL - 4}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="8.5" fill="var(--subtext)">${shortV(niceMax * fr)}</text>`;
    }).join("");
    const bars = rows.map((r, i) => {
      const x = padL + i * slot + (slot - bw) / 2;
      const hh = ((r.value || 0) / niceMax) * plotH, y = padT + plotH - hh;
      const maxChars = Math.max(4, Math.floor(bw / 5.2));
      const label = String(r.label).length > maxChars ? String(r.label).slice(0, maxChars - 1) + "…" : r.label;
      return `<g class="ccol" data-i="${i}" style="cursor:pointer">
        <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, hh).toFixed(1)}" rx="3" fill="var(--accent)" style="transition:opacity .12s"/>
        <text x="${(x + bw / 2).toFixed(1)}" y="${(y - 3).toFixed(1)}" text-anchor="middle" font-size="9.5" font-weight="600" fill="var(--text)">${esc(String(r.raw != null ? r.raw : Math.round(r.value || 0)))}</text>
        <text x="${(x + bw / 2).toFixed(1)}" y="${(padT + plotH + 11).toFixed(1)}" text-anchor="middle" font-size="9" fill="var(--subtext)">${esc(label)}</text>
      </g>`;
    }).join("");
    const wrap = document.createElement("div");
    wrap.innerHTML = rows.length
      ? `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${grid}
          <line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" stroke="var(--subtext)" stroke-width="1.5"/>${bars}
          ${hidden.length ? `<text x="${W - padR}" y="${padT - 4}" text-anchor="end" font-size="8" fill="var(--subtext)">+${hidden.length} weitere</text>` : ""}</svg>`
      : `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`;
    target.appendChild(wrap);
    const cols = wrap.querySelectorAll(".ccol");
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
    scaleToContainer(holder); return;
  }

  if (kind === "bars") {
    // Horizontale Balken: Hover PRO ZEILE (Highlight + Tooltip mit Label,
    // exaktem Wert und Anteil am Maximum) - wie im Dashboard.
    const all = preset.rows ? preset.rows(client) : [];
    const rows = all.slice(0, 8), hidden = all.slice(8);
    const max = Math.max(1, ...rows.map((r) => r.value || 0), 100);
    const wrap = document.createElement("div");
    wrap.className = "widget-bars";
    wrap.style.cssText = `width:${NAT_W}px`;
    // Adaptive Größen: wenige Zeilen => größere Schrift/dickere Balken.
    const fs = rows.length <= 3 ? 13.5 : rows.length <= 5 ? 12.5 : 11.5;
    const th = rows.length <= 3 ? 14 : rows.length <= 5 ? 11 : 9;
    wrap.innerHTML = rows.map((r, i) => `
      <div class="wbar-row" data-i="${i}" style="padding:2px 3px;font-size:${fs}px">
        <span class="wbar-label">${esc(r.label)}</span>
        <span class="wbar-track" style="height:${th}px"><span class="wbar-fill" style="width:${(((r.value || 0) / max) * 100).toFixed(1)}%;background:var(--accent)"></span></span>
        <span class="wbar-val">${esc(r.raw != null ? r.raw : String(Math.round(r.value || 0)))}</span>
      </div>`).join("") || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`;
    target.appendChild(wrap);
    if (hidden.length) wrap.appendChild(moreNote(hidden));
    wrap.querySelectorAll(".wbar-row").forEach((rowEl) => {
      const row = rows[+rowEl.dataset.i];
      if (!row) return;
      bindRowHover(rowEl, () => `<b>${esc(row.label)}</b> — ${esc(row.raw != null ? row.raw : String(Math.round(row.value || 0)))} <span style="color:var(--subtext)">(${Math.round(((row.value || 0) / max) * 100)}% vom Maximum)</span>`);
    });
    scaleToContainer(holder); return;
  }

  // info (Standard-Fallback): Zeilenliste oder einzeiliger Text.
  if (preset.rows) {
    // Zeilenliste (z.B. "Systeminfo (kompakt)"): Hover PRO ZEILE - die Zeile
    // wird hervorgehoben und der Tooltip zeigt Label + VOLLSTÄNDIGEN Wert
    // genau dieser Zeile (z.B. "CPU" + kompletter CPU-Modellname, auch wenn
    // die Anzeige abgeschnitten ist).
    const rows = preset.rows(client);
    // Adaptive Schriftgröße: wenige Zeilen => größer (füllt die 1x1-Box).
    const ifs = rows.length <= 3 ? 14.5 : rows.length <= 5 ? 13 : 12;
    const wrap = document.createElement("div");
    wrap.className = "status-info";
    wrap.style.cssText = `width:${NAT_W}px;margin-top:0`;
    wrap.innerHTML = rows.map((r, i) => `
      <div data-i="${i}" style="padding:${rows.length <= 5 ? 3 : 1}px 4px;font-size:${ifs}px"><span>${esc(r.label)}</span><b>${esc(r.raw != null ? r.raw : String(r.value ?? "—"))}</b></div>`).join("");
    target.appendChild(wrap);
    wrap.querySelectorAll("[data-i]").forEach((rowEl) => {
      const row = rows[+rowEl.dataset.i];
      if (!row) return;
      bindRowHover(rowEl, () => `<b>${esc(row.label)}</b><br>${esc(row.raw != null ? row.raw : String(row.value ?? "—"))}`);
    });
  } else {
    target.innerHTML = `<div class="widget-text" style="width:${NAT_W}px;font-size:14.5px;line-height:1.45;text-align:center">${esc(preset.text ? preset.text(client) : formatClientValue(preset, v))}</div>`;
  }
  scaleToContainer(holder);
}

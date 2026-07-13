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
import { attachHoverTip } from "./fleetcharts.js";

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
];

export function clientPresetById(id) { return CLIENT_PRESETS.find((p) => p.id === id) || null; }

// Zusätzliche Ansichten, die automatisch für JEDES passende Client-Preset
// verfügbar sind: Wert-Presets bekommen area/spark/progress/ring/stat,
// Zeilen-Presets zusätzlich columns.
export const CLIENT_VALUE_EXTRA_KINDS = ["area", "spark", "progress", "ring", "stat"];
export function availableClientKinds(preset) {
  if (!preset) return ["number"];
  const kinds = [...(preset.charts || [])];
  if (preset.value) for (const k of CLIENT_VALUE_EXTRA_KINDS) if (!kinds.includes(k)) kinds.push(k);
  if (preset.rows && !kinds.includes("columns")) kinds.push("columns");
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
// -----------------------------------------------------------------
export function renderClientMetric(target, client, panel) {
  target.innerHTML = "";
  const preset = clientPresetById(panel.metric);
  if (!preset) { target.innerHTML = `<div style="color:var(--subtext);font-size:12px">Unbekannte Metrik.</div>`; return; }
  const kind = panel.kind && availableClientKinds(preset).includes(panel.kind) ? panel.kind : preset.charts[0];
  const v = preset.value ? preset.value(client) : null;

  // Hover wie in der Flotten-Übersicht: Tooltip (folgt der Maus) mit dem
  // exakten, live formatierten Wert - auf ALLEN Client-Metrik-Widgets.
  if (!target._hoverTipAttached) {
    target._hoverTipAttached = true;
    attachHoverTip(target, () => {
      const val = preset.value ? preset.value(client) : null;
      const txt = val === null || val === undefined ? "—" : formatClientValue(preset, val);
      const mx = resolveMax(preset, client);
      const maxTxt = (kind === "gauge" || kind === "donut") && mx
        ? ` <span style="color:var(--subtext)">/ ${esc(formatClientValue(preset, mx))}</span>` : "";
      return `<b>${esc(panel.title || preset.label)}</b><br>${esc(txt)}${maxTxt}`;
    });
  }

  if (kind === "number") {
    target.innerHTML = `<div class="widget-number">
      <div class="wn-value">${esc(formatClientValue(preset, v))}</div>
      <div class="wn-label">${esc(preset.label)}</div></div>`;
    return;
  }

  if (kind === "gauge") {
    const max = resolveMax(preset, client);
    const p = v === null ? 0 : Math.max(0, Math.min(100, (v / max) * 100));
    const w = 200, h = 116, cx = w / 2, cy = h - 8, r = 88, stroke = 16;
    const ang = Math.PI * (p / 100);
    const ex = cx - r * Math.cos(ang), ey = cy - r * Math.sin(ang);
    const color = p > 85 ? "#ff4d6d" : p > 65 ? "#f5a524" : "#3ecf8e";
    target.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;padding:4px">
      <svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}">
        <path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}" fill="none" stroke="var(--border)" stroke-width="${stroke}" stroke-linecap="round"/>
        ${v === null ? "" : `<path d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${ex.toFixed(2)} ${ey.toFixed(2)}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"/>`}
      </svg>
      <div style="font-size:22px;font-weight:700;margin-top:-10px">${esc(formatClientValue(preset, v))}</div>
      <div style="font-size:11px;color:var(--subtext)">${esc(preset.label)}</div></div>`;
    return;
  }

  if (kind === "donut") {
    const max = resolveMax(preset, client);
    const frac = v === null ? 0 : Math.max(0, Math.min(1, v / max));
    const size = 150, sw = 20, r = (size - sw) / 2 - 2, cx = size / 2, cy = size / 2;
    const C = 2 * Math.PI * r, len = frac * C;
    target.innerHTML = `<div style="display:flex;justify-content:center;padding:6px">
      <div style="position:relative;width:${size}px;height:${size}px">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${sw}"/>
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--accent)" stroke-width="${sw}"
            stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-linecap="round"
            transform="rotate(-90 ${cx} ${cy})"/>
        </svg>
        <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center">
          <div style="font-size:22px;font-weight:700;line-height:1">${esc(formatClientValue(preset, v))}</div>
          <div style="font-size:10px;color:var(--subtext);margin-top:3px">${esc(preset.label)}</div>
        </div>
      </div></div>`;
    return;
  }

  if (kind === "line") {
    const h = pushHistory(`${client.id}:${panel.id}`, v);
    const data = h.v.length ? h.v : [v ?? 0];
    const w = 320, hgt = 130, pad = 6;
    const max = Math.max(1, ...data, typeof preset.max === "number" ? preset.max : 0);
    const min = Math.min(...data, 0);
    const span = max - min || 1;
    const pts = data.map((val, i) => {
      const x = pad + (data.length <= 1 ? 0 : (i / (data.length - 1)) * (w - 2 * pad));
      const y = hgt - pad - ((val - min) / span) * (hgt - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    target.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
        <span style="font-size:11px;color:var(--subtext)">${esc(preset.label)}</span>
        <span style="font-size:16px;font-weight:700">${esc(formatClientValue(preset, v))}</span>
      </div>
      <svg viewBox="0 0 ${w} ${hgt}" width="100%" height="${hgt}" preserveAspectRatio="none" style="overflow:visible">
        <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      </svg>`;
    return;
  }

  if (kind === "area" || kind === "spark") {
    // Verlauf (gleiche Quelle wie "line"); area = gefüllt, spark = minimal.
    const h = pushHistory(`${client.id}:${panel.id}`, v);
    const data = h.v.length ? h.v : [v ?? 0];
    const spark = kind === "spark";
    const w = 320, hgt = spark ? 56 : 110, pad = spark ? 4 : 6;
    const max = Math.max(1, ...data, typeof preset.max === "number" ? preset.max : 0);
    const min = Math.min(...data, 0);
    const span = max - min || 1;
    const pts = data.map((val, i) => {
      const x = pad + (data.length <= 1 ? 0 : (i / (data.length - 1)) * (w - 2 * pad));
      const y = hgt - pad - ((val - min) / span) * (hgt - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    target.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px">
        <span style="font-size:11px;color:var(--subtext)">${esc(preset.label)}</span>
        <span style="font-size:16px;font-weight:700">${esc(formatClientValue(preset, v))}</span>
      </div>
      <svg viewBox="0 0 ${w} ${hgt}" width="100%" height="${hgt}" preserveAspectRatio="none">
        ${spark ? "" : `<polygon points="${pad},${hgt - pad} ${pts} ${w - pad},${hgt - pad}" fill="var(--accent)" opacity="0.22"/>`}
        <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      </svg>`;
    return;
  }

  if (kind === "progress") {
    const max = resolveMax(preset, client) || 100;
    const pct = Math.max(0, Math.min(100, ((v || 0) / max) * 100));
    const color = pct > 85 ? "#ff4d6d" : pct > 65 ? "#f5a524" : "var(--accent)";
    target.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
        <span style="font-size:12px;color:var(--subtext)">${esc(preset.label)}</span>
        <span style="font-size:18px;font-weight:700">${esc(formatClientValue(preset, v))}</span>
      </div>
      <div style="height:14px;border-radius:7px;background:var(--panel-2);overflow:hidden">
        <div style="height:100%;width:${pct.toFixed(1)}%;background:${color};border-radius:7px;transition:width .3s"></div>
      </div>
      <div style="font-size:10px;color:var(--subtext);margin-top:4px;text-align:right">${Math.round(pct)}% von ${esc(formatClientValue(preset, max))}</div>`;
    return;
  }

  if (kind === "ring") {
    const max = resolveMax(preset, client) || 100;
    const pct = Math.max(0, Math.min(100, ((v || 0) / max) * 100));
    const size = 104, stroke = 9, r = (size - stroke) / 2 - 2, cx = size / 2, cy = size / 2;
    const C = 2 * Math.PI * r, len = (pct / 100) * C;
    target.innerHTML = `
      <div style="display:flex;justify-content:center;padding:2px">
        <div style="position:relative;width:${size}px;height:${size}px">
          <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">
            <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--border)" stroke-width="${stroke}"/>
            <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--accent)" stroke-width="${stroke}"
              stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})"/>
          </svg>
          <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center">
            <div style="font-size:16px;font-weight:700;line-height:1">${esc(formatClientValue(preset, v))}</div>
            <div style="font-size:9px;color:var(--subtext);margin-top:2px">${Math.round(pct)}%</div>
          </div>
        </div>
      </div>`;
    return;
  }

  if (kind === "stat") {
    const h = pushHistory(`${client.id}:${panel.id}`, v);
    const data = h.v.length ? h.v : [v ?? 0];
    const ref = data.length > 1 ? data[0] : (v || 0);
    const diff = (v || 0) - (ref || 0);
    const up = diff > 0, flat = Math.abs(diff) < 1e-9;
    const trendColor = flat ? "var(--subtext)" : (up ? "#f5a524" : "#3ecf8e");
    const w = 140, hgt = 34, pad = 3;
    const max = Math.max(1, ...data), min = Math.min(...data, 0), span = (max - min) || 1;
    const pts = data.map((val, i) => {
      const x = pad + (data.length <= 1 ? 0 : (i / (data.length - 1)) * (w - 2 * pad));
      const y = hgt - pad - ((val - min) / span) * (hgt - 2 * pad);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    target.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <div>
          <div style="font-size:24px;font-weight:700;line-height:1.1">${esc(formatClientValue(preset, v))}</div>
          <div style="font-size:11px;color:var(--subtext)">${esc(preset.label)}</div>
        </div>
        <div style="text-align:right;flex:1;min-width:90px">
          <div style="font-size:13px;font-weight:600;color:${trendColor}">${flat ? "→" : (up ? "▲" : "▼")} ${esc(formatClientValue(preset, Math.abs(diff)))}</div>
          <svg viewBox="0 0 ${w} ${hgt}" width="${w}" height="${hgt}" preserveAspectRatio="none">
            <polyline points="${pts}" fill="none" stroke="${trendColor}" stroke-width="2"/>
          </svg>
        </div>
      </div>`;
    return;
  }

  if (kind === "columns") {
    const rows = (preset.rows ? preset.rows(client) : []).slice(0, 12);
    const max = Math.max(1, ...rows.map((r) => r.value || 0));
    target.innerHTML = `<div style="display:flex;gap:6px;align-items:flex-end;height:110px;padding:2px 2px 0">${rows.map((r) => `
      <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;min-width:0" title="${esc(r.label)}${r.raw ? ": " + esc(r.raw) : ""}">
        <div style="width:100%;height:${Math.max(3, ((r.value || 0) / max) * 86).toFixed(1)}%;background:var(--accent);border-radius:4px 4px 0 0"></div>
        <span style="font-size:9px;color:var(--subtext);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.label)}</span>
      </div>`).join("") || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`}</div>`;
    return;
  }

  if (kind === "bars") {
    const rows = preset.rows ? preset.rows(client) : [];
    const max = Math.max(1, ...rows.map((r) => r.value || 0), 100);
    target.innerHTML = `<div class="widget-bars">${rows.map((r) => `
      <div class="wbar-row" title="${esc(r.label)}${r.raw ? ": " + esc(r.raw) : ""}">
        <span class="wbar-label">${esc(r.label)}</span>
        <span class="wbar-track"><span class="wbar-fill" style="width:${(((r.value || 0) / max) * 100).toFixed(1)}%;background:var(--accent)"></span></span>
        <span class="wbar-val">${esc(r.raw != null ? r.raw : String(Math.round(r.value || 0)))}</span>
      </div>`).join("") || `<span style="color:var(--subtext);font-size:12px">Keine Daten</span>`}</div>`;
    return;
  }

  // info (Standard-Fallback): Zeilenliste oder einzeiliger Text.
  if (preset.rows) {
    const rows = preset.rows(client);
    target.innerHTML = `<div class="status-info">${rows.map((r) => `
      <div><span>${esc(r.label)}</span><b>${esc(r.raw != null ? r.raw : String(r.value ?? "—"))}</b></div>`).join("")}</div>`;
  } else {
    target.innerHTML = `<div class="widget-text">${esc(preset.text ? preset.text(client) : formatClientValue(preset, v))}</div>`;
  }
}

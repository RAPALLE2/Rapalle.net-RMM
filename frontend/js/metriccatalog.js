// metriccatalog.js
// ----------------
// Modulare Bibliothek von Metrik-"Presets", die sich als Dashboard-Widgets
// anzeigen lassen. Jedes Preset weiß, wie es aus dem aktuellen Client-Zustand
// einen Wert (oder eine Verteilung) berechnet - egal ob als Zahl, Donut,
// Linien-Chart, Pie oder Tabelle dargestellt.
//
// Zwei Arten:
//   - scope "fleet":   ein aggregierter Wert über ALLE Clients
//                      (z.B. Gesamt-Stromverbrauch, Gesamt-CPU-Kapazität)
//   - scope "perhost": ein Wert PRO Client (für Tabellen / Pie / Bar),
//                      z.B. CPU-Modell, RAM-Takt, Ping-Zeit
//
// Darstellungsarten (kind), die ein Preset unterstützt, stehen in .charts.

import { formatBytes } from "./utils.js";
import { groupBy } from "./fleetcharts.js";
import { getFleetIncludeVirtual } from "./persist.js";
// (getFleetIncludeVirtual entfernt - Zählweise ist jetzt pro Widget einstellbar)

const fmtPct = (v) => `${Math.round(v)}%`;
const fmtW = (v) => `${Math.round(v)} W`;
const fmtMs = (v) => `${v} ms`;
const fmtMHz = (v) => `${(v / 1000).toFixed(2)} GHz`;
const fmtC = (v) => `${v} °C`;
const fmtBps = (v) => `${formatBytes(v)}/s`;

// Aktive (nicht-Kind-)Clients.
// Pro-Widget-Einstellung: Zählt dieses Widget ALLE Geräte oder nur physische?
// Wird von dashwidgets/clientmetrics vor dem Rendern gesetzt (setHostScope)
// und danach zurückgesetzt. Default null = globale Profil-Einstellung greift.
let _hostScope = null;   // null | "all" | "physical"
export function setHostScope(scope) { _hostScope = scope; }
export function clearHostScope() { _hostScope = null; }

function hosts(state) {
  let list = state.clients || [];
  // Zählweise bestimmen: eine explizit gesetzte Pro-Widget-Zählweise hat
  // Vorrang; sonst greift die globale Profil-Einstellung "VMs & LXCs als
  // vollwertig mitzählen" (getFleetIncludeVirtual).
  //   physical   -> nur physische Geräte
  //   all        -> alle Geräte (VMs/LXCs zählen mit)
  let scope = _hostScope;
  if (scope == null) scope = getFleetIncludeVirtual() ? "all" : "physical";
  if (scope === "physical") {
    // WICHTIG: Nur echte VMs/LXCs bzw. Kind-Clients ausschließen. Alles ohne
    // gesetzten Typ gilt als physisch (Default), damit Geräte nie
    // versehentlich ganz verschwinden.
    list = list.filter((c) => !c.parent_client_id && (c.device_type || "physical") === "physical");
  }
  // BUGFIX: Früher wurden Kind-Clients (parent_client_id gesetzt, z.B. eine
  // VM, die einem physischen Host untergeordnet ist) IMMER herausgefiltert -
  // dadurch fehlten sie im Dashboard auch bei Zählweise "alle Geräte".
  // Bei scope "all" zählen jetzt wirklich ALLE Geräte mit.
  return list;
}
function onlineHosts(state) { return hosts(state).filter((c) => c.online && c.metrics); }

// Summe/ Mittel-Helfer über online Clients.
const sum = (arr, f) => arr.reduce((s, c) => s + (f(c) || 0), 0);
const avg = (arr, f) => (arr.length ? sum(arr, f) / arr.length : 0);

// -----------------------------------------------------------------
// Preset-Definitionen. id ist stabil (wird in Widgets gespeichert).
// value(state)  -> Zahl (für number/gauge/donut/line)
// donut(state)  -> {value, max, label, sub}  (optional, für Donut/Gauge)
// rows(state)   -> [{label, value, raw}]      (für table/pie/bar)
// charts        -> erlaubte Darstellungen
// unit/format   -> Anzeige
// -----------------------------------------------------------------
export const PRESETS = [
  // ---------- FLEET-AGGREGATE ----------
  {
    id: "fleet.count", scope: "fleet", group: "Flotte", label: "Verwaltete Clients",
    charts: ["number"], value: (s) => hosts(s).length,
  },
  {
    id: "fleet.online", scope: "fleet", group: "Flotte", label: "Online / Offline",
    charts: ["donut", "pie", "number"],
    value: (s) => hosts(s).filter((c) => c.online).length,
    donut: (s) => ({ value: hosts(s).filter((c) => c.online).length, max: hosts(s).length, label: "Online", sub: `${hosts(s).length} gesamt` }),
    rows: (s) => {
      const h = hosts(s);
      return [
        { label: "Online", value: h.filter((c) => c.online).length, color: "#3ecf8e" },
        { label: "Offline", value: h.filter((c) => !c.online).length, color: "#64748b" },
      ];
    },
  },
  {
    id: "fleet.cpuCapacity", scope: "fleet", group: "CPU", label: "Gesamte CPU-Kapazität (Threads)",
    charts: ["number"], format: (v) => `${v} Threads`,
    value: (s) => sum(onlineHosts(s), (c) => c.metrics.cpuThreads),
  },
  {
    id: "fleet.cpuLoadAvg", scope: "fleet", group: "CPU", label: "Ø CPU-Auslastung (Flotte)",
    charts: ["gauge", "donut", "number", "line"], unit: "%", format: fmtPct, max: 100,
    value: (s) => Math.round(avg(onlineHosts(s), (c) => c.metrics.cpuLoad)),
    donut: (s) => ({ value: Math.round(avg(onlineHosts(s), (c) => c.metrics.cpuLoad)), max: 100, label: "CPU Ø", sub: `${onlineHosts(s).length} online` }),
  },
  {
    id: "fleet.cpuLoadWeighted", scope: "fleet", group: "CPU", label: "CPU-Auslastung gewichtet (Threads)",
    charts: ["gauge", "donut", "number"], unit: "%", format: fmtPct, max: 100,
    value: (s) => {
      const h = onlineHosts(s);
      const cap = sum(h, (c) => c.metrics.cpuThreads);
      const used = sum(h, (c) => c.metrics.cpuThreads * (c.metrics.cpuLoad / 100));
      return cap ? Math.round((used / cap) * 100) : 0;
    },
  },
  {
    id: "fleet.ramTotal", scope: "fleet", group: "RAM", label: "Gesamter RAM (Kapazität)",
    charts: ["number"], format: formatBytes,
    value: (s) => sum(onlineHosts(s), (c) => c.metrics.memTotal),
  },
  {
    id: "fleet.ramUsed", scope: "fleet", group: "RAM", label: "RAM belegt (Flotte)",
    charts: ["donut", "gauge", "number"], format: formatBytes,
    value: (s) => sum(onlineHosts(s), (c) => c.metrics.memUsed),
    donut: (s) => {
      const used = sum(onlineHosts(s), (c) => c.metrics.memUsed);
      const total = sum(onlineHosts(s), (c) => c.metrics.memTotal);
      return { value: used, max: total, label: "RAM", sub: `${formatBytes(used)} / ${formatBytes(total)}`, pctText: true };
    },
  },
  {
    id: "fleet.power", scope: "fleet", group: "Strom", label: "Gesamter Stromverbrauch",
    charts: ["number", "line", "bar"], format: fmtW,
    value: (s) => Math.round(sum(onlineHosts(s), (c) => c.metrics.powerWatts || 0)),
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.powerWatts).map((c) => ({ label: c.hostname, value: c.metrics.powerWatts, raw: fmtW(c.metrics.powerWatts) })),
  },
  {
    id: "fleet.diskTotal", scope: "fleet", group: "Disk", label: "Gesamter Speicher (Kapazität)",
    charts: ["number"], format: formatBytes,
    value: (s) => sum(onlineHosts(s), (c) => c.metrics.diskTotal),
  },
  {
    id: "fleet.netIn", scope: "fleet", group: "Netzwerk", label: "Netzwerk-Eingang gesamt",
    charts: ["number", "line"], format: fmtBps,
    value: (s) => sum(onlineHosts(s), (c) => c.metrics.netIn || 0),
  },
  {
    id: "fleet.netOut", scope: "fleet", group: "Netzwerk", label: "Netzwerk-Ausgang gesamt",
    charts: ["number", "line"], format: fmtBps,
    value: (s) => sum(onlineHosts(s), (c) => c.metrics.netOut || 0),
  },
  {
    id: "fleet.osDist", scope: "fleet", group: "Verteilung", label: "Betriebssystem-Verteilung",
    charts: ["pie", "donut", "table"],
    rows: (s) => distribution(hosts(s), (c) => osKey(c)),
  },
  {
    id: "fleet.gpuDist", scope: "fleet", group: "Verteilung", label: "GPU-Modelle (Verteilung)",
    charts: ["pie", "table"],
    rows: (s) => distribution(hosts(s), (c) => (c.metrics?.gpuModels?.[0]) || "unbekannt"),
  },
  {
    id: "fleet.archDist", scope: "fleet", group: "Verteilung", label: "Architektur (Verteilung)",
    charts: ["pie", "donut", "table"],
    rows: (s) => distribution(hosts(s), (c) => c.metrics?.arch || c.arch || "?"),
  },
  {
    id: "fleet.agentDist", scope: "fleet", group: "Verteilung", label: "Agent-Versionen (Verteilung)",
    charts: ["pie", "table"],
    rows: (s) => distribution(hosts(s), (c) => c.agent_version || "unbekannt"),
  },
  // ---------- FLOTTEN-ÜBERSICHT (interaktive Donuts wie im Dashboard) ----------
  // Diese drei Presets sind die frühere feste "Flotten-Übersicht" - jetzt als
  // normale Widgets: frei verschiebbar, editierbar, löschbar, neu einfügbar.
  // segments(state) liefert Gruppen INKLUSIVE der betroffenen Client-Namen
  // (für den Hover-Tooltip); rows() erlaubt zusätzlich pie/bar/table.
  {
    id: "fleet.statusDonut", scope: "fleet", group: "Flotten-Übersicht",
    label: "Agenten: Online / Offline", charts: ["fleetdonut", "pie", "bar", "table"],
    segments: (s) => groupBy(hosts(s),
      (c) => (c.status_override === "maintenance" ? "Wartung" : (c.online ? "Online" : "Offline")),
      (label) => label === "Online" ? "#3ecf8e" : (label === "Wartung" ? "#f5a524" : "#64748b")),
    rows: (s) => groupBy(hosts(s),
      (c) => (c.status_override === "maintenance" ? "Wartung" : (c.online ? "Online" : "Offline")),
      (label) => label === "Online" ? "#3ecf8e" : (label === "Wartung" ? "#f5a524" : "#64748b"))
      .map((g) => ({ label: g.label, value: g.count, color: g.color })),
  },
  {
    id: "fleet.osDonut", scope: "fleet", group: "Flotten-Übersicht",
    label: "Betriebssysteme", charts: ["fleetdonut", "pie", "bar", "table"],
    segments: (s) => groupBy(hosts(s), (c) => osKey(c)),
    rows: (s) => groupBy(hosts(s), (c) => osKey(c)).map((g) => ({ label: g.label, value: g.count, color: g.color })),
  },
  {
    id: "fleet.versionDonut", scope: "fleet", group: "Flotten-Übersicht",
    label: "Agent-Versionen", charts: ["fleetdonut", "pie", "bar", "table"],
    segments: (s) => groupBy(hosts(s), (c) => c.agent_version || "unbekannt"),
    rows: (s) => groupBy(hosts(s), (c) => c.agent_version || "unbekannt").map((g) => ({ label: g.label, value: g.count, color: g.color })),
  },
  {
    // Kompakte "Flotten-Übersicht" als EIN Widget: Online/Offline, Betriebs-
    // systeme und Agent-Versionen als drei kleine Kreisdiagramme.
    id: "fleet.overview", scope: "fleet", group: "Flotte", label: "Flotten-Übersicht",
    charts: ["overview"],
    sections: (s) => [
      { title: "Online / Offline", rows: [
        { label: "Online", value: hosts(s).filter((c) => c.online).length, color: "#3ecf8e" },
        { label: "Offline", value: hosts(s).filter((c) => !c.online).length, color: "#64748b" },
      ] },
      { title: "Betriebssysteme", rows: distribution(hosts(s), (c) => osKey(c)) },
      { title: "Agent-Versionen", rows: distribution(hosts(s), (c) => c.agent_version || "unbekannt") },
    ],
  },

  // ---------- PER-HOST (Tabellen / Pie / Bar) ----------
  {
    id: "host.cpuLoad", scope: "perhost", group: "CPU", label: "CPU-Auslastung je Client",
    charts: ["table", "bar"], unit: "%",
    rows: (s) => onlineHosts(s).map((c) => ({ label: c.hostname, value: c.metrics.cpuLoad, raw: fmtPct(c.metrics.cpuLoad) })).sort((a, b) => b.value - a.value),
  },
  // ---------- RAM je Client ----------
  {
    id: "host.ramPct", scope: "perhost", group: "RAM", label: "RAM-Auslastung je Client",
    charts: ["bar", "table", "pie"], unit: "%", format: fmtPct,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.memTotal).map((c) => {
      const p = Math.round((c.metrics.memUsed / c.metrics.memTotal) * 100);
      return { label: c.hostname, value: p, raw: `${p}% (${formatBytes(c.metrics.memUsed)} / ${formatBytes(c.metrics.memTotal)})` };
    }).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.ramUsed", scope: "perhost", group: "RAM", label: "RAM belegt je Client",
    charts: ["bar", "table"], format: formatBytes,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.memUsed != null).map((c) => ({ label: c.hostname, value: c.metrics.memUsed, raw: formatBytes(c.metrics.memUsed) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.ramTotal", scope: "perhost", group: "RAM", label: "RAM-Kapazität je Client",
    charts: ["bar", "table"], format: formatBytes,
    rows: (s) => hosts(s).filter((c) => c.metrics?.memTotal).map((c) => ({ label: c.hostname, value: c.metrics.memTotal, raw: formatBytes(c.metrics.memTotal) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.swap", scope: "perhost", group: "RAM", label: "Swap-Auslastung je Client",
    charts: ["bar", "table"], unit: "%", format: fmtPct,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.swapTotal).map((c) => {
      const p = Math.round((c.metrics.swapUsed / c.metrics.swapTotal) * 100);
      return { label: c.hostname, value: p, raw: `${p}%` };
    }).sort((a, b) => b.value - a.value),
  },
  // ---------- GPU je Client ----------
  {
    id: "host.gpuLoad", scope: "perhost", group: "GPU", label: "GPU-Auslastung je Client",
    charts: ["bar", "table", "pie"], unit: "%", format: fmtPct,
    rows: (s) => onlineHosts(s).filter((c) => (c.metrics.gpus || [])[0]?.load != null).map((c) => ({ label: c.hostname, value: c.metrics.gpus[0].load, raw: fmtPct(c.metrics.gpus[0].load) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.gpuTemp", scope: "perhost", group: "GPU", label: "GPU-Temperatur je Client",
    charts: ["bar", "table"], format: fmtC,
    rows: (s) => onlineHosts(s).filter((c) => (c.metrics.gpus || [])[0]?.temp != null).map((c) => ({ label: c.hostname, value: c.metrics.gpus[0].temp, raw: fmtC(c.metrics.gpus[0].temp) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.gpuMem", scope: "perhost", group: "GPU", label: "GPU-Speicher belegt je Client",
    charts: ["bar", "table"], format: formatBytes,
    rows: (s) => onlineHosts(s).filter((c) => (c.metrics.gpus || [])[0]?.memUsed != null).map((c) => ({ label: c.hostname, value: c.metrics.gpus[0].memUsed, raw: `${formatBytes(c.metrics.gpus[0].memUsed)}${c.metrics.gpus[0].memTotal ? " / " + formatBytes(c.metrics.gpus[0].memTotal) : ""}` })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.gpuPower", scope: "perhost", group: "GPU", label: "GPU-Leistung je Client",
    charts: ["bar", "table"], format: fmtW,
    rows: (s) => onlineHosts(s).filter((c) => (c.metrics.gpus || [])[0]?.power != null).map((c) => ({ label: c.hostname, value: c.metrics.gpus[0].power, raw: fmtW(c.metrics.gpus[0].power) })).sort((a, b) => b.value - a.value),
  },
  // ---------- Disk & Netzwerk je Client ----------
  {
    id: "host.diskPct", scope: "perhost", group: "Disk", label: "Disk-Auslastung je Client",
    charts: ["bar", "table", "pie"], unit: "%", format: fmtPct,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.diskTotal).map((c) => {
      const p = Math.round((c.metrics.diskUsed / c.metrics.diskTotal) * 100);
      return { label: c.hostname, value: p, raw: `${p}% (${formatBytes(c.metrics.diskUsed)} / ${formatBytes(c.metrics.diskTotal)})` };
    }).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.netIn", scope: "perhost", group: "Netzwerk", label: "Netzwerk ↓ je Client",
    charts: ["bar", "table"], format: fmtBps,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.netIn != null).map((c) => ({ label: c.hostname, value: c.metrics.netIn, raw: fmtBps(c.metrics.netIn) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.netOut", scope: "perhost", group: "Netzwerk", label: "Netzwerk ↑ je Client",
    charts: ["bar", "table"], format: fmtBps,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.netOut != null).map((c) => ({ label: c.hostname, value: c.metrics.netOut, raw: fmtBps(c.metrics.netOut) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.ipList", scope: "perhost", group: "Identität", label: "IP-Adresse je Client",
    charts: ["table"],
    rows: (s) => hosts(s).map((c) => ({ label: c.hostname, raw: c.ip || c.metrics?.ip || "—" })),
  },
  {
    id: "host.macList", scope: "perhost", group: "Identität", label: "MAC-Adresse je Client",
    charts: ["table"],
    rows: (s) => hosts(s).map((c) => ({ label: c.hostname, raw: c.metrics?.mac || "—" })),
  },
  {
    id: "host.deviceTypeList", scope: "perhost", group: "Identität", label: "Gerätetyp je Client",
    charts: ["table"],
    rows: (s) => hosts(s).map((c) => ({ label: c.hostname, raw: ({ vm: "VM", lxc: "LXC", physical: "Physisch" }[c.device_type || "physical"]) })),
  },
  // ---------- RAM/GPU/Disk-Aggregate über die Flotte ----------
  {
    id: "fleet.ramLoadAvg", scope: "fleet", group: "RAM", label: "Ø RAM-Auslastung (Flotte)",
    charts: ["gauge", "donut", "number"], unit: "%", format: fmtPct, max: 100,
    value: (s) => Math.round(avg(onlineHosts(s).filter((c) => c.metrics.memTotal), (c) => (c.metrics.memUsed / c.metrics.memTotal) * 100)),
    donut: (s) => ({ value: Math.round(avg(onlineHosts(s).filter((c) => c.metrics.memTotal), (c) => (c.metrics.memUsed / c.metrics.memTotal) * 100)), max: 100, label: "RAM Ø", sub: `${onlineHosts(s).length} online` }),
  },
  {
    id: "fleet.gpuLoadAvg", scope: "fleet", group: "GPU", label: "Ø GPU-Auslastung (Flotte)",
    charts: ["gauge", "donut", "number"], unit: "%", format: fmtPct, max: 100,
    value: (s) => Math.round(avg(onlineHosts(s).filter((c) => (c.metrics.gpus || [])[0]), (c) => c.metrics.gpus[0].load || 0)),
    donut: (s) => ({ value: Math.round(avg(onlineHosts(s).filter((c) => (c.metrics.gpus || [])[0]), (c) => c.metrics.gpus[0].load || 0)), max: 100, label: "GPU Ø", sub: `${onlineHosts(s).filter((c) => (c.metrics.gpus || [])[0]).length} mit GPU` }),
  },
  {
    id: "fleet.gpuPowerTotal", scope: "fleet", group: "GPU", label: "GPU-Leistung gesamt (Flotte)",
    charts: ["number", "line"], format: fmtW,
    value: (s) => Math.round(sum(onlineHosts(s), (c) => (c.metrics.gpus || [])[0]?.power || 0)),
  },
  {
    id: "fleet.diskLoadAvg", scope: "fleet", group: "Disk", label: "Ø Disk-Auslastung (Flotte)",
    charts: ["gauge", "donut", "number"], unit: "%", format: fmtPct, max: 100,
    value: (s) => Math.round(avg(onlineHosts(s).filter((c) => c.metrics.diskTotal), (c) => (c.metrics.diskUsed / c.metrics.diskTotal) * 100)),
    donut: (s) => ({ value: Math.round(avg(onlineHosts(s).filter((c) => c.metrics.diskTotal), (c) => (c.metrics.diskUsed / c.metrics.diskTotal) * 100)), max: 100, label: "Disk Ø", sub: `${onlineHosts(s).length} online` }),
  },
  {
    id: "host.cpuModel", scope: "perhost", group: "Hardware", label: "CPU-Modell je Client",
    charts: ["table"],
    rows: (s) => hosts(s).map((c) => ({ label: c.hostname, raw: c.metrics?.cpuModel || "—" })),
  },
  {
    id: "host.cpuFreq", scope: "perhost", group: "CPU", label: "CPU-Takt je Client",
    charts: ["table", "bar"], format: fmtMHz,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.cpuFreq).map((c) => ({ label: c.hostname, value: c.metrics.cpuFreq, raw: fmtMHz(c.metrics.cpuFreq) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.cpuTemp", scope: "perhost", group: "Temperatur", label: "CPU-Temperatur je Client",
    charts: ["table", "bar"], format: fmtC,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.cpuTemp != null).map((c) => ({ label: c.hostname, value: c.metrics.cpuTemp, raw: fmtC(c.metrics.cpuTemp) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.ramModel", scope: "perhost", group: "Hardware", label: "RAM-Module je Client",
    charts: ["table"],
    rows: (s) => hosts(s).map((c) => {
      const mods = c.metrics?.ramModules || [];
      const desc = mods.length
        ? mods.map((m) => (m.vendor ? m.vendor + " " : "") + (m.size ? formatBytes(m.size) : (m.size_str || "")) + (m.speed ? " @" + m.speed + "MHz" : (m.speed_str ? " @" + m.speed_str : ""))).join(", ")
        : "—";
      return { label: c.hostname, raw: desc };
    }),
  },
  {
    id: "host.gpuModel", scope: "perhost", group: "Hardware", label: "GPU-Modell je Client",
    charts: ["table"],
    rows: (s) => hosts(s).map((c) => ({ label: c.hostname, raw: (c.metrics?.gpuModels || []).join(", ") || "—" })),
  },
  {
    id: "host.power", scope: "perhost", group: "Strom", label: "Stromverbrauch je Client",
    charts: ["table", "bar", "pie"], format: fmtW,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.powerWatts != null).map((c) => ({ label: c.hostname, value: c.metrics.powerWatts, raw: fmtW(c.metrics.powerWatts) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.fan", scope: "perhost", group: "Sensorik", label: "Lüfterdrehzahl je Client",
    charts: ["table", "bar"], format: (v) => `${v} U/min`,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.fanSpeed != null).map((c) => ({ label: c.hostname, value: c.metrics.fanSpeed, raw: `${c.metrics.fanSpeed} U/min` })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.diskRW", scope: "perhost", group: "Disk", label: "Disk Lese-/Schreibrate je Client",
    charts: ["table"],
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.diskRead != null).map((c) => ({ label: c.hostname, raw: `↓${fmtBps(c.metrics.diskRead)} ↑${fmtBps(c.metrics.diskWrite || 0)}` })),
  },
  {
    id: "host.io", scope: "perhost", group: "Disk", label: "Disk-Durchsatz (R+W) je Client",
    charts: ["bar", "table"], format: fmtBps,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.diskRead != null).map((c) => ({ label: c.hostname, value: (c.metrics.diskRead || 0) + (c.metrics.diskWrite || 0), raw: fmtBps((c.metrics.diskRead || 0) + (c.metrics.diskWrite || 0)) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.pingGoogle", scope: "perhost", group: "Netzwerk", label: "Ping zu Google (8.8.8.8)",
    charts: ["table", "bar"], format: fmtMs,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.ping?.google != null).map((c) => ({ label: c.hostname, value: c.metrics.ping.google, raw: fmtMs(c.metrics.ping.google) })).sort((a, b) => a.value - b.value),
  },
  {
    id: "host.pingCf", scope: "perhost", group: "Netzwerk", label: "Ping zu Cloudflare (1.1.1.1)",
    charts: ["table", "bar"], format: fmtMs,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.ping?.cloudflare != null).map((c) => ({ label: c.hostname, value: c.metrics.ping.cloudflare, raw: fmtMs(c.metrics.ping.cloudflare) })).sort((a, b) => a.value - b.value),
  },
  {
    id: "host.load", scope: "perhost", group: "CPU", label: "Load-Average (1 min) je Client",
    charts: ["table", "bar"],
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.load1 != null).map((c) => ({ label: c.hostname, value: c.metrics.load1, raw: String(c.metrics.load1) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.uptime", scope: "perhost", group: "System", label: "Uptime je Client",
    charts: ["table"],
    rows: (s) => onlineHosts(s).map((c) => ({ label: c.hostname, value: c.metrics.uptime, raw: fmtUptime(c.metrics.uptime) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.procs", scope: "perhost", group: "System", label: "Prozessanzahl je Client",
    charts: ["table", "bar"],
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.procCount != null).map((c) => ({ label: c.hostname, value: c.metrics.procCount, raw: String(c.metrics.procCount) })).sort((a, b) => b.value - a.value),
  },
  {
    id: "host.battery", scope: "perhost", group: "Strom", label: "Akkustand je Client",
    charts: ["table", "bar"], format: fmtPct,
    rows: (s) => onlineHosts(s).filter((c) => c.metrics.battery).map((c) => ({ label: c.hostname, value: c.metrics.battery.percent, raw: `${c.metrics.battery.percent}%${c.metrics.battery.plugged ? " ⚡" : ""}` })).sort((a, b) => a.value - b.value),
  },
];

export function presetById(id) { return PRESETS.find((p) => p.id === id) || null; }

// Nach Gruppe sortierte Presets (für den Auswahl-Dialog).
export function presetsByGroup() {
  const map = new Map();
  for (const p of PRESETS) {
    if (!map.has(p.group)) map.set(p.group, []);
    map.get(p.group).push(p);
  }
  return [...map.entries()];
}

// --- Hilfsfunktionen ---
function osKey(c) {
  const p = (c.platform || "").toLowerCase();
  if (p.includes("win")) return "Windows";
  if (p.includes("darwin") || p.includes("mac")) return "macOS";
  if (p.includes("linux")) return "Linux";
  return c.platform || "unbekannt";
}
function distribution(list, keyFn) {
  const map = new Map();
  for (const c of list) {
    const k = keyFn(c) || "unbekannt";
    map.set(k, (map.get(k) || 0) + 1);
  }
  return [...map.entries()].sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }));
}
function fmtUptime(sec) {
  if (!sec) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

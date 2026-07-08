// panel.js
// --------
// Rendert den mittleren Hauptbereich, abhängig von der Sidebar-Auswahl:
//   - Client   -> Detailansicht (Status | Übersicht mit Metrics/Notes/Disk | Actions)
//   - Location -> aggregierte Übersicht aller Clients dieser Location
//   - Tenant   -> aggregierte Übersicht, nach Location gruppiert
//
// Die Metrics-Ansicht bietet: Gradient-Donuts (CPU/RAM/Disk), CPU-Cores/Threads,
// RAM used/frei, Netzwerk-Graph mit In/Out-Toggle, beschriftete Verlaufs-
// Diagramme mit Hover-Tooltip und Zeitspannen-Dropdown.

import { state, findClient } from "./state.js";
import { hasClientPerm, hasGlobalPerm } from "./state.js";
import {
  formatBytes, formatUptime, esc,
  gradientDonutSvg, CPU_GRADIENT, RAM_GRADIENT, DISK_GRADIENT,
  interactiveChart,
} from "./utils.js";
import { getHistoryRange, TIME_RANGES, seedHistory, hasSeeded } from "./metricshistory.js";
import { openWindow } from "./windowmanager.js";
import { api } from "./api.js";
import { t, osIcon, osLabel } from "./i18n.js";

// Zustand der Übersicht (pro Ansicht gemerkt)
let activeTab = "metrics";       // "metrics" | "notes" | "disk"
let showNetIn = true;
let showNetOut = true;
let timeRangeIndex = 0;           // Index in TIME_RANGES (Standard: 5 Min)

const content = () => document.getElementById("main-content");

export function renderMainContent() {
  const el = content();
  if (!state.selection) {
    el.innerHTML = `<div class="empty-state">${t("select_hint")}</div>`;
    return;
  }
  if (state.selection.type === "client") renderClientView(el, state.selection.id);
  else if (state.selection.type === "location") renderAggregateView(el, "location", state.selection.id);
  else if (state.selection.type === "tenant") renderAggregateView(el, "tenant", state.selection.id);
}

// -----------------------------------------------------------------
// Status-Hilfen
// -----------------------------------------------------------------

function statusInfo(client) {
  if (client.status_override === "maintenance") return { cls: "status-maintenance", label: t("status_maintenance") };
  if (client.online) return { cls: "status-online", label: t("status_online") };
  return { cls: "status-offline", label: t("status_offline") };
}

// -----------------------------------------------------------------
// CLIENT-DETAILANSICHT
// -----------------------------------------------------------------

function renderClientView(el, clientId) {
  const client = findClient(clientId);
  if (!client) { el.innerHTML = `<div class="empty-state">${t("client_not_found")}</div>`; return; }

  const status = statusInfo(client);
  const m = client.metrics;

  el.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <h2 style="margin:0">
        <span class="status-dot-lg ${status.cls}"></span>${esc(client.hostname)}
      </h2>
      <span style="color:var(--subtext);font-size:13px">${osIcon(client.platform, client.release)} ${esc(osLabel(client.platform, client.release))} · ${esc(client.ip || "")}</span>
    </div>

    <div class="dash-grid">
      <!-- LINKE SPALTE: Status + Aktionen untereinander -->
      <div class="dash-left-col">
        <div class="panel dash-panel">
          <h3>${t("status")}</h3>
          <div class="status-label"><span class="status-dot-lg ${status.cls}"></span>${status.label}</div>
          <div class="status-info">
            <div><span>${t("os")}</span><b>${osIcon(client.platform, client.release)} ${esc(osLabel(client.platform, client.release))}</b></div>
            <div><span>${t("ip")}</span><b>${esc(client.ip || "–")}</b></div>
            <div><span>${t("hostname")}</span><b>${esc(client.hostname)}</b></div>
            <div><span>${t("arch")}</span><b>${esc(client.arch || "?")}</b></div>
            <div><span>${t("uptime")}</span><b>${m ? formatUptime(m.uptime) : "–"}</b></div>
          </div>
          <div class="quick-actions">
            <button data-quick="reboot" ${client.online ? "" : "disabled"}>⟳ ${t("reboot")}</button>
            <button data-quick="shutdown" ${client.online ? "" : "disabled"}>⏻ ${t("shutdown")}</button>
            ${hasClientPerm(client.id, "manage_agent") ? `
            <button data-quick="update" ${client.online ? "" : "disabled"}>⬆️ ${t("update_agent")}</button>
            <button data-quick="uninstall" ${client.online ? "" : "disabled"} title="Agent deinstallieren">🗑️ Agent deinstallieren</button>` : ""}
            ${hasClientPerm(client.id, "manage_clients") ? `<button data-quick="edit">✎ ${t("edit")}</button>` : ""}
          </div>
        </div>

        <div class="panel actions-panel">
          <h3>${t("actions")}</h3>
          ${hasClientPerm(client.id, "use_explorer") ? `<button class="action-btn" data-action="explorer" ${client.online ? "" : "disabled"}>📁 ${t("file_explorer")}</button>` : ""}
          ${hasClientPerm(client.id, "use_terminal") ? `<button class="action-btn" data-action="terminal" ${client.online ? "" : "disabled"}>⌨️ ${t("terminal")}</button>` : ""}
          ${hasClientPerm(client.id, "use_screen") ? `<button class="action-btn" data-action="vnc" ${client.online ? "" : "disabled"}>🖥️ ${t("remote_screen")}</button>` : ""}
          ${hasClientPerm(client.id, "use_guacamole") ? `<button class="action-btn" data-action="guacamole">🕹️ Guacamole</button>` : ""}
          ${hasClientPerm(client.id, "use_taskmanager") ? `<button class="action-btn" data-action="taskmanager" ${client.online ? "" : "disabled"}>📋 ${t("task_manager")}</button>` : ""}
        </div>
      </div>

      <!-- RECHTE SPALTE: Metrics-Übersicht -->
      <div class="panel dash-panel">
        <h3>
          ${t("overview")}
          <span class="tab-bar">
            <button class="tab-btn ${activeTab === "metrics" ? "active" : ""}" data-tab="metrics">${t("tab_metrics")}</button>
            <button class="tab-btn ${activeTab === "notes" ? "active" : ""}" data-tab="notes">${t("tab_notes")}</button>
            <button class="tab-btn ${activeTab === "disk" ? "active" : ""}" data-tab="disk">${t("tab_disk")}</button>
          </span>
        </h3>
        <div id="overview-tab-content" class="overview-content"></div>
      </div>
    </div>
  `;

  // Übersichts-Tab-Inhalt einsetzen (als DOM, wegen interaktiver Charts)
  fillOverview(el, client);

  el.querySelectorAll("[data-tab]").forEach((btn) =>
    btn.addEventListener("click", () => { activeTab = btn.dataset.tab; renderMainContent(); })
  );
  el.querySelectorAll("[data-quick]").forEach((btn) =>
    btn.addEventListener("click", () => handleQuickAction(btn.dataset.quick, client))
  );
  el.querySelectorAll("[data-action]").forEach((btn) =>
    btn.addEventListener("click", () => handleAction(btn.dataset.action, client))
  );
}

// Füllt den Übersichts-Tab (Metrics/Notes/Disk) als DOM-Inhalt
function fillOverview(el, client) {
  const target = el.querySelector("#overview-tab-content");
  if (!target) return;

  // Beim ersten Anzeigen dieses Clients die gespeicherte Historie vom Backend
  // holen, damit die Graphen nicht bei 0 anfangen. Danach einmal neu rendern.
  if (!hasSeeded(client.id)) {
    seedHistory(client.id, []); // sofort als "geladen" markieren -> kein Doppelabruf
    api.getMetricsHistory(client.id)
      .then((res) => {
        seedHistory(client.id, res.points || []);
        if (state.selection && state.selection.type === "client" && state.selection.id === client.id) {
          fillOverview(el, client);
        }
      })
      .catch(() => { /* keine Historie / offline: einfach ignorieren */ });
  }

  target.innerHTML = "";
  const m = client.metrics;

  // --- NOTES ---
  if (activeTab === "notes") {
    const ta = document.createElement("textarea");
    ta.className = "notes-textarea";
    ta.placeholder = t("notes_placeholder");
    ta.value = client.notes || "";
    ta.addEventListener("change", async () => {
      await api.updateClient(client.id, { notes: ta.value });
      client.notes = ta.value;
    });
    target.appendChild(ta);
    return;
  }

  if (!m) {
    target.innerHTML = `<div style="color:var(--subtext);padding:20px 0">${t("no_live_data")}</div>`;
    return;
  }

  // --- DISK ---
  if (activeTab === "disk") {
    const disks = (m.disks && m.disks.length) ? m.disks
      : [{ device: "System", mountpoint: "", used: m.diskUsed, total: m.diskTotal }];
    const row = document.createElement("div");
    row.className = "metrics-row";
    row.style.flexWrap = "wrap";
    row.innerHTML = disks.map((d) => {
      const pct = d.total ? (d.used / d.total) * 100 : 0;
      const label = esc(d.device || d.mountpoint || "Disk");
      return `<div class="donut-wrap">${gradientDonutSvg(pct, DISK_GRADIENT, label, `${formatBytes(d.used)} / ${formatBytes(d.total)}`)}</div>`;
    }).join("");
    target.appendChild(row);
    return;
  }

  // --- METRICS ---
  const cpuPct = m.cpuLoad ?? 0;
  const ramPct = m.memTotal ? (m.memUsed / m.memTotal) * 100 : 0;
  const swapTotal = m.swapTotal ?? 0;
  const swapUsed = m.swapUsed ?? 0;
  const swapPct = swapTotal ? (swapUsed / swapTotal) * 100 : 0;

  // Optionaler Swap-Donut (nur wenn der Client überhaupt Swap hat, z.B. Linux).
  const swapDonut = swapTotal > 0 ? `
    <div class="donut-wrap">
      ${gradientDonutSvg(swapPct, RAM_GRADIENT, "Swap", `${formatBytes(swapUsed)} / ${formatBytes(swapTotal)}`)}
      <div class="metric-sub">${t("free")}: ${formatBytes(swapTotal - swapUsed)}</div>
    </div>` : "";

  // Obere Reihe: nur CPU-Donut + Infos und RAM-Donut + Infos.
  // Der Netzwerk-Verlauf wandert nach unten zu den CPU-/RAM-Charts, damit
  // alle drei Diagramme exakt gleich groß sind.
  const topRow = document.createElement("div");
  topRow.className = "metrics-row";
  topRow.innerHTML = `
    <div class="donut-wrap">
      ${gradientDonutSvg(cpuPct, CPU_GRADIENT, "CPU", "")}
      <div class="metric-sub">${m.cpuCores ?? "?"} ${t("cores")} · ${m.cpuThreads ?? "?"} ${t("threads")}</div>
    </div>
    <div class="donut-wrap">
      ${gradientDonutSvg(ramPct, RAM_GRADIENT, "RAM", `${formatBytes(m.memUsed)} / ${formatBytes(m.memTotal)}`)}
      <div class="metric-sub">${t("free")}: ${formatBytes(m.memAvailable ?? (m.memTotal - m.memUsed))}</div>
    </div>
    ${swapDonut}
  `;
  target.appendChild(topRow);

  const range = getHistoryRange(client.id, TIME_RANGES[timeRangeIndex].ms);

  // Zeitspannen-Dropdown
  const rangeBar = document.createElement("div");
  rangeBar.style.cssText = "display:flex;justify-content:flex-end;align-items:center;gap:8px;margin:14px 0 4px";
  rangeBar.innerHTML = `
    <span style="font-size:11px;color:var(--subtext)">${t("time_range")}:</span>
    <select id="time-range-select" style="padding:3px 6px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
      ${TIME_RANGES.map((r, i) => `<option value="${i}" ${i === timeRangeIndex ? "selected" : ""}>${r.label}</option>`).join("")}
    </select>`;
  target.appendChild(rangeBar);

  // Verlaufs-Diagramme (beschriftet, mit Hover-Tooltip)
  const chartsRow = document.createElement("div");
  chartsRow.style.cssText = "display:flex;gap:20px;flex-wrap:wrap";

  const cpuChartWrap = document.createElement("div");
  cpuChartWrap.className = "mini-chart";
  cpuChartWrap.innerHTML = `<div style="font-size:11px;color:var(--subtext);margin-bottom:4px">${t("cpu_history")} (%)</div>`;
  cpuChartWrap.appendChild(interactiveChart(
    [{ label: "CPU", color: "#a97cff", values: range.cpu, timestamps: range.ts }],
    { unit: "%", yMax: 100 }
  ));

  const ramChartWrap = document.createElement("div");
  ramChartWrap.className = "mini-chart";
  ramChartWrap.innerHTML = `<div style="font-size:11px;color:var(--subtext);margin-bottom:4px">${t("ram_history")} (%)</div>`;
  ramChartWrap.appendChild(interactiveChart(
    [{ label: "RAM", color: "#c7d34a", values: range.ram, timestamps: range.ts }],
    { unit: "%", yMax: 100 }
  ));

  // Netzwerk-Chart - gleiche Größe wie CPU/RAM, mit In/Out-Toggle im Header.
  const netChartWrap = document.createElement("div");
  netChartWrap.className = "mini-chart";
  const netHeader = document.createElement("div");
  netHeader.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:6px;flex-wrap:wrap";
  netHeader.innerHTML = `
    <span style="font-size:11px;color:var(--subtext)">${t("network")}</span>
    <span style="font-size:11px">
      <label style="color:#3ecf8e;margin-right:8px;cursor:pointer">
        <input type="checkbox" id="net-in-toggle" ${showNetIn ? "checked" : ""} /> ↓ ${formatBytes(m.netIn ?? 0)}/s
      </label>
      <label style="color:#f5a524;cursor:pointer">
        <input type="checkbox" id="net-out-toggle" ${showNetOut ? "checked" : ""} /> ↑ ${formatBytes(m.netOut ?? 0)}/s
      </label>
    </span>`;
  netChartWrap.appendChild(netHeader);
  const netSeries = [];
  if (showNetIn) netSeries.push({ label: "↓ In", color: "#3ecf8e", values: range.netIn, timestamps: range.ts });
  if (showNetOut) netSeries.push({ label: "↑ Out", color: "#f5a524", values: range.netOut, timestamps: range.ts });
  if (netSeries.length) {
    netChartWrap.appendChild(interactiveChart(netSeries, {
      formatValue: (v) => formatBytes(v) + "/s",
    }));
  }

  chartsRow.appendChild(cpuChartWrap);
  chartsRow.appendChild(ramChartWrap);
  chartsRow.appendChild(netChartWrap);
  target.appendChild(chartsRow);

  // --- Handler für Toggles + Zeitspanne ---
  const rerender = () => fillOverview(el, client);
  const netIn = target.querySelector("#net-in-toggle");
  const netOut = target.querySelector("#net-out-toggle");
  const rangeSel = target.querySelector("#time-range-select");
  if (netIn) netIn.addEventListener("change", () => { showNetIn = netIn.checked; rerender(); });
  if (netOut) netOut.addEventListener("change", () => { showNetOut = netOut.checked; rerender(); });
  if (rangeSel) rangeSel.addEventListener("change", () => { timeRangeIndex = parseInt(rangeSel.value); rerender(); });
}

// -----------------------------------------------------------------
// Quick Actions & Actions
// -----------------------------------------------------------------

async function handleQuickAction(action, client) {
  if (action === "edit") {
    openWindow({ key: `edit-${client.id}`, appId: "edit-client", title: `${t("edit")} — ${client.hostname}`, props: { clientId: client.id }, w: 480, h: 520 });
    return;
  }
  if (action === "reboot" || action === "shutdown") {
    const label = action === "reboot" ? t("reboot") : t("shutdown");
    if (!confirm(`${client.hostname}: ${label}?`)) return;
    const isWin = (client.platform || "").toLowerCase().includes("windows");
    let cmd;
    if (action === "reboot") cmd = isWin ? "shutdown /r /t 0" : "sudo reboot";
    else cmd = isWin ? "shutdown /s /t 0" : "sudo shutdown -h now";
    try { await api.execOnClient(client.id, cmd); window.notify?.(t("command_sent"), "success"); }
    catch (e) { window.notify?.("Fehler: " + e.message, "error"); }
    return;
  }
  if (action === "update") {
    if (!confirm(`${client.hostname}: Agent auf die neueste Version aktualisieren?\nDer Agent lädt die neue Version, ersetzt sich selbst und startet neu.`)) return;
    try {
      await api.updateAgent(client.id);
      window.notify?.(`Agent-Update auf ${client.hostname} gestartet. Der Client verbindet sich in Kürze neu.`, "success", 8000);
    } catch (e) {
      window.notify?.("Update fehlgeschlagen: " + e.message, "error");
    }
    return;
  }
  if (action === "uninstall") {
    if (!confirm(`${client.hostname}: Agent WIRKLICH deinstallieren?\n\n` +
      `Es wird: 1) der Agent gestoppt, 2) alle Agent-Daten auf dem Client gelöscht,\n` +
      `3) der Client aus dem Dashboard entfernt – aber nur, wenn er wirklich offline geht.\n\n` +
      `Das kann bis zu 60 Sekunden dauern. Bitte warten.`)) return;
    window.notify?.(`Deinstalliere Agent auf ${client.hostname}… (bis zu 60 s, bitte warten)`, "info", 60000);
    try {
      const res = await api.uninstallAgent(client.id);
      if (res && res.removed) {
        // Aus dem Dashboard entfernt. Auswahl leeren, falls dieser Client gewählt war.
        if (state.selection && state.selection.type === "client" && state.selection.id === client.id) {
          state.selection = null;
        }
        window.notify?.(`Agent auf ${client.hostname} deinstalliert und aus dem Dashboard entfernt.`, "success", 8000);
        renderMainContent();
      } else {
        window.notify?.(`Deinstallation auf ${client.hostname} abgeschlossen.`, "success", 6000);
      }
    } catch (e) {
      window.notify?.("Deinstallation fehlgeschlagen: " + e.message, "error", 12000);
    }
  }
}

function handleAction(action, client) {
  const colorProps = { clientColor: client.color };
  const props = { clientId: client.id, clientName: client.hostname, platform: client.platform };
  if (action === "terminal") {
    // Eindeutiger Key pro Klick -> mehrere Terminals desselben Clients gleichzeitig.
    const suffix = (window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`).slice(0, 8);
    const openCount = state.windows.filter((w) => w.appId === "terminal" && w.props?.clientId === client.id).length;
    const title = openCount > 0
      ? `${t("terminal")} — ${client.hostname} (${openCount + 1})`
      : `${t("terminal")} — ${client.hostname}`;
    openWindow({ key: `terminal-${client.id}-${suffix}`, appId: "terminal", title, props, ...colorProps });
  }
  else if (action === "explorer") openWindow({ key: `explorer-${client.id}`, appId: "explorer", title: `${t("file_explorer")} — ${client.hostname}`, props, ...colorProps });
  else if (action === "vnc") openWindow({ key: `vnc-${client.id}`, appId: "vnc", title: `${t("remote_screen")} — ${client.hostname}`, props, ...colorProps, w: 800, h: 600 });
  else if (action === "guacamole") openWindow({
    key: `guac-${client.id}`, appId: "guacamole",
    title: `Guacamole — ${client.hostname}`,
    props: { clientId: client.id, clientName: client.hostname, host: client.ip, platform: client.platform },
    ...colorProps, w: 900, h: 640,
  });
  else if (action === "taskmanager") openWindow({ key: `task-${client.id}`, appId: "taskmanager", title: `${t("task_manager")} — ${client.hostname}`, props, ...colorProps });
}

// -----------------------------------------------------------------
// AGGREGIERTE ANSICHT (Tenant / Location)
// -----------------------------------------------------------------

function clientCard(c) {
  const m = c.metrics;
  const status = statusInfo(c);
  const cpuPct = m ? m.cpuLoad ?? 0 : 0;
  const ramPct = m && m.memTotal ? (m.memUsed / m.memTotal) * 100 : 0;
  return `
    <div class="panel" style="cursor:pointer" data-open-client="${c.id}">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <strong><span class="status-dot-lg ${status.cls}"></span>${esc(c.hostname)}</strong>
        <span style="font-size:11px;color:var(--subtext)">${osIcon(c.platform, c.release)} ${esc(c.ip || "")}</span>
      </div>
      <div style="margin-top:10px;font-size:12px;color:var(--subtext)">
        CPU ${Math.round(cpuPct)}% · RAM ${Math.round(ramPct)}%${m ? " · " + formatUptime(m.uptime) : ""}
      </div>
    </div>`;
}

function renderAggregateView(el, type, id) {
  let title, html = "";

  if (type === "tenant") {
    const tenant = state.hierarchy.tenants.find((x) => x.id === id);
    title = tenant ? tenant.name : "Tenant";
    // Clients nach Location gruppieren, mit Überschrift pro Location
    const locations = state.hierarchy.locations.filter((l) => l.tenant_id === id);
    let totalOnline = 0, totalCount = 0;

    for (const loc of locations) {
      const clients = state.clients.filter((c) => c.location_id === loc.id && !c.parent_client_id);
      if (!clients.length) continue;
      totalCount += clients.length;
      totalOnline += clients.filter((c) => c.online).length;
      html += `
        <h3 style="margin:18px 0 8px;color:var(--subtext);font-size:13px;text-transform:uppercase">
          📍 ${esc(loc.name)} <span style="font-weight:400">(${clients.filter((c) => c.online).length}/${clients.length})</span>
        </h3>
        <div class="client-grid">${clients.map(clientCard).join("")}</div>`;
    }

    // Clients ohne Location (aber im Tenant)
    const noLoc = state.clients.filter((c) => c.tenant_id === id && !c.location_id && !c.parent_client_id);
    if (noLoc.length) {
      totalCount += noLoc.length;
      totalOnline += noLoc.filter((c) => c.online).length;
      html += `<h3 style="margin:18px 0 8px;color:var(--subtext);font-size:13px;text-transform:uppercase">${t("no_location")}</h3>
               <div class="client-grid">${noLoc.map(clientCard).join("")}</div>`;
    }

    el.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <h2 style="margin:0">${esc(title)}</h2>
        <span style="color:var(--subtext);font-size:13px">${totalOnline}/${totalCount} ${t("online")}</span>
      </div>
      ${html || `<div class="empty-state">${t("no_devices")}</div>`}`;

  } else {
    const location = state.hierarchy.locations.find((l) => l.id === id);
    title = location ? location.name : "Standort";
    const clients = state.clients.filter((c) => c.location_id === id && !c.parent_client_id);
    const online = clients.filter((c) => c.online).length;
    el.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <h2 style="margin:0">📍 ${esc(title)}</h2>
        <span style="color:var(--subtext);font-size:13px">${online}/${clients.length} ${t("online")}</span>
      </div>
      <div class="client-grid">${clients.map(clientCard).join("") || `<div class="empty-state">${t("no_devices")}</div>`}</div>`;
  }

  el.querySelectorAll("[data-open-client]").forEach((card) =>
    card.addEventListener("click", () => {
      state.selection = { type: "client", id: card.dataset.openClient };
      renderMainContent();
      // Sidebar-Pfad zum Client aufklappen, damit er sichtbar ist
      import("./sidebar.js").then((m) => m.revealClient(card.dataset.openClient));
    })
  );
}

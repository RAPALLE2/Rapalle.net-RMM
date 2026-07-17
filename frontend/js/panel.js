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
import { formatBytes, formatUptime, esc, gradientDonutSvg, CPU_GRADIENT, RAM_GRADIENT, DISK_GRADIENT, interactiveChart, uiConfirm, uiPrompt } from "./utils.js";
import { getHistoryRange, TIME_RANGES, seedHistory, hasSeeded } from "./metricshistory.js";
import { openWindow } from "./windowmanager.js";
import { api } from "./api.js";
import { renderDashboard } from "./apps/dashboard.js";
import { favStarHtml, favState, cycleFav } from "./sidebar.js";
import { renderClientLayout } from "./dashlayout.js";
import { hideFleetTip, showFleetTip, attachHoverTip, scaleToContainer } from "./fleetcharts.js";

// Zeilen-Hover (wie Dashboard/clientmetrics): Zeile hervorheben + Tooltip
// mit Label + VOLLSTÄNDIGEM Wert genau dieser Zeile.
function bindRowHover(rowEl, tipFn) {
  rowEl.style.borderRadius = rowEl.style.borderRadius || "6px";
  rowEl.style.cursor = "default";
  rowEl.addEventListener("mouseenter", (e) => { rowEl.style.background = "var(--panel-2, #1b2740)"; showFleetTip(tipFn(), e.clientX, e.clientY); });
  rowEl.addEventListener("mousemove", (e) => showFleetTip(tipFn(), e.clientX, e.clientY));
  rowEl.addEventListener("mouseleave", () => { rowEl.style.background = ""; hideFleetTip(); });
}

// Neueste ausgelieferte Agent-Version (einmalig laden) für den "veraltet"-Hinweis.
let _latestAgentVersion = null;
api.getAgentVersion().then((r) => { _latestAgentVersion = r && r.version; }).catch(() => {});

// Zeigt die gemeldete Agent-Version; weicht sie von der neuesten ab, wird ein
// deutlicher "veraltet"-Hinweis angezeigt (häufige Ursache dafür, dass
// Update/Uninstall über das Dashboard nicht greift).
function agentVersionBadge(client) {
  const v = client.agent_version;
  if (!v) return `<span style="color:var(--subtext)">unbekannt</span>`;
  if (_latestAgentVersion && v !== _latestAgentVersion) {
    return `<span title="Neu: ${esc(_latestAgentVersion)}">${esc(v)} <span style="color:var(--warn)">⚠ veraltet</span></span>`;
  }
  return `<span>${esc(v)}</span>`;
}
import { t, osIcon, osLabel } from "./i18n.js";

// Zustand der Übersicht (pro Ansicht gemerkt). Die Tab-Auswahl wird jetzt PRO
// Ordner-Instanz gehalten (mehrere Übersicht-Ordner möglich), Default hier.
let showNetIn = true;
let showNetOut = true;
let timeRangeIndex = 0;           // Index in TIME_RANGES (Standard: 5 Min)

const content = () => document.getElementById("main-content");

export function renderMainContent() {
  // Ansicht wird gewechselt/neu gebaut: alle schwebenden Hover-Tooltips
  // entfernen (sonst konnten sie über den Client-Wechsel hinaus stehenbleiben).
  try { hideFleetTip(); } catch {}
  const el = content();
  if (!state.selection || state.selection.type === "dashboard") {
    renderDashboard(el);
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

  el.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <h2 style="margin:0;display:flex;align-items:center;gap:8px">
        <span class="status-dot-lg ${status.cls}"></span>${esc(client.hostname)}
        ${favStarHtml("clients", client.id)}
      </h2>
      <span style="display:flex;align-items:center;gap:12px">
        <span style="color:var(--subtext);font-size:13px">${osIcon(client.platform, client.release)} ${esc(osLabel(client.platform, client.release))} · ${esc(client.ip || "")}</span>
        <span id="dash-layout-toolbar"></span>
      </span>
    </div>
    <div id="dash-layout-host"></div>
  `;

  // Anpassbares Layout (Status/Aktionen/Übersicht-Ordner) rendern.
  renderClientLayout(el.querySelector("#dash-layout-host"),
                     el.querySelector("#dash-layout-toolbar"), client);

  maybePromptDeviceType(client);
}

// -----------------------------------------------------------------
// Auto-Erkennung des Gerätetyps: Der Agent meldet, wenn er in einer VM oder
// einem LXC-Container läuft. Beim ersten Öffnen des Client-Panels wird der
// Nutzer gefragt, ob der Client als VM/LXC (mit Host-Auswahl) oder weiterhin
// als physisch geführt werden soll. Die Antwort wird gespeichert
// (device_type_ack), danach kommt die Frage nie wieder.
// -----------------------------------------------------------------
const _dtPromptShown = new Set();   // pro Sitzung nur einmal je Client

function maybePromptDeviceType(client) {
  const detected = client.detected_device_type;
  if (!detected || !["vm", "lxc"].includes(detected)) return;
  if (client.device_type_ack) return;
  if ((client.device_type || "physical") !== "physical") return;   // schon manuell gesetzt
  if (_dtPromptShown.has(client.id)) return;
  _dtPromptShown.add(client.id);

  const label = detected === "vm" ? "virtuelle Maschine (VM)" : "LXC-Container";
  // Mögliche Hosts: physische Clients (außer dem Client selbst).
  const hosts = state.clients.filter((c) =>
    c.id !== client.id && (c.device_type || "physical") === "physical");

  const overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;inset:0;z-index:9400;background:rgba(0,0,0,0.5);" +
    "display:flex;align-items:center;justify-content:center";
  overlay.innerHTML = `
    <div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:22px;width:440px;max-width:92vw">
      <h3 style="margin:0 0 8px">🔎 Gerätetyp erkannt: ${esc(label)}</h3>
      <p style="color:var(--subtext);font-size:13px;margin:0 0 12px">
        Der Agent auf <b>${esc(client.hostname)}</b> hat erkannt, dass er in einer
        ${esc(label)} läuft. Soll der Client entsprechend eingeordnet werden?
        Das beeinflusst Zählweisen, Layout-Presets und die Remote-Screen-Abfrage.
      </p>
      <div class="form-row">
        <label>Übergeordneter Host (optional)</label>
        <select id="dt-host">
          <option value="">— kein Host zuordnen —</option>
          ${hosts.map((h) => `<option value="${esc(h.id)}">${esc(h.hostname)}</option>`).join("")}
        </select>
      </div>
      <div id="dt-error" class="form-error hidden"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="taskbar-btn" id="dt-keep">Als physisch behalten</button>
        <button class="btn-primary" id="dt-apply" style="width:auto;margin:0">Als ${detected === "vm" ? "VM" : "LXC"} übernehmen</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const finish = async (fields) => {
    try {
      const updated = await api.updateClient(client.id, { ...fields, device_type_ack: 1 });
      Object.assign(client, updated);   // lokalen Zustand aktualisieren
      overlay.remove();
      renderMainContent();
      window.notify?.(
        fields.device_type === "physical"
          ? `${client.hostname} bleibt als physisches Gerät geführt.`
          : `${client.hostname} ist jetzt als ${fields.device_type.toUpperCase()} eingeordnet.`,
        "success");
    } catch (e) {
      const err = overlay.querySelector("#dt-error");
      err.textContent = e.message; err.classList.remove("hidden");
    }
  };
  overlay.querySelector("#dt-keep").addEventListener("click", () =>
    finish({ device_type: "physical" }));
  overlay.querySelector("#dt-apply").addEventListener("click", () =>
    finish({ device_type: detected, parent_client_id: overlay.querySelector("#dt-host").value || null }));
}

// -----------------------------------------------------------------
// Wiederverwendbare Bausteine ("Parts") der Client-Ansicht. Jeder Part
// rendert sich in ein übergebenes Ziel-Element und kann sowohl im Dashboard-
// Layout als auch in einem herausgelösten Fenster (apps/panelpart.js) leben.
// -----------------------------------------------------------------

// --- STATUS-Part ---
// Layout: AKTIONS-BUTTONS LINKS untereinander, TEXT (Status + Info-Zeilen)
// RECHTS - füllt die Box gut aus. 1x1-optimiert + proportionales Mitwachsen;
// jede Info-Zeile hat einen eigenen Hover (Highlight + Tooltip mit Label und
// VOLLEM Wert dieser Zeile, z.B. kompletter Hostname/OS-String).
export function renderStatusPart(target, client) {
  const status = statusInfo(client);
  const m = client.metrics;
  const rows = [
    { label: t("os"), raw: `${osLabel(client.platform, client.release)}`, html: `${osIcon(client.platform, client.release)} ${esc(osLabel(client.platform, client.release))}` },
    { label: t("ip"), raw: client.ip || "–" },
    { label: t("hostname"), raw: client.hostname || "–" },
    { label: t("arch"), raw: client.arch || "?" },
    { label: t("uptime"), raw: m ? formatUptime(m.uptime) : "–" },
    { label: "Agent", raw: client.agent_version || "–", html: agentVersionBadge(client) },
  ];
  target.innerHTML = "";
  // STRETCH statt Skalieren: Buttons links, Text rechts - die beiden Spalten
  // dehnen sich immer über die KOMPLETTE Panelfläche aus (gleiches Layout,
  // voller Platz), die Schrift bleibt konstant lesbar.
  const holder = document.createElement("div");
  holder.style.cssText = "width:100%;height:100%;display:flex;align-items:center;gap:20px";
  const box = holder;
  box.innerHTML = `
    <div class="quick-actions" style="flex:none;width:180px;display:flex;flex-direction:column;gap:7px;margin-top:0;flex-wrap:nowrap;justify-content:center;align-self:stretch">
      <button data-quick="reboot" ${client.online ? "" : "disabled"}>⟳ ${t("reboot")}</button>
      <button data-quick="shutdown" ${client.online ? "" : "disabled"}>⏻ ${t("shutdown")}</button>
      ${hasClientPerm(client.id, "manage_agent") ? `
      <button data-quick="update" ${client.online ? "" : "disabled"}>⬆️ ${t("update_agent")}</button>
      <button data-quick="uninstall" ${client.online ? "" : "disabled"} title="Agent deinstallieren">🗑️ Agent deinstallieren</button>` : ""}
      ${hasClientPerm(client.id, "manage_clients") ? `<button data-quick="edit">✎ ${t("edit")}</button>` : ""}
    </div>
    <div style="flex:1;min-width:0;display:flex;flex-direction:column;justify-content:center">
      <div class="status-label" style="font-size:15px"><span class="status-dot-lg ${status.cls}"></span>${status.label}</div>
      <div class="status-info" style="margin-top:10px">
        ${rows.map((r, i) => `<div data-i="${i}" style="padding:2px 4px;font-size:14px"><span>${esc(r.label)}</span><b style="word-break:normal">${r.html || esc(r.raw)}</b></div>`).join("")}
      </div>
    </div>`;
  target.appendChild(holder);
  box.querySelectorAll(".status-info > div[data-i]").forEach((rowEl) => {
    const r = rows[+rowEl.dataset.i];
    bindRowHover(rowEl, () => `<b>${esc(r.label)}</b><br>${esc(r.raw)}`);
  });
  box.querySelectorAll("[data-quick]").forEach((btn) =>
    btn.addEventListener("click", () => handleQuickAction(btn.dataset.quick, client)));
}

// --- AKTIONEN-Part ---
// 1x1-optimiert (feste natürliche Breite) + proportionales Mitwachsen:
// die Aktions-Buttons skalieren mit der Panelgröße mit.
export function renderActionsPart(target, client) {
  target.innerHTML = "";
  const holder = document.createElement("div");
  holder.style.cssText = "width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:visible";
  const box = document.createElement("div");
  box.style.cssText = "width:240px;display:flex;flex-direction:column;gap:8px";
  box.innerHTML = `
    ${hasClientPerm(client.id, "use_explorer") ? `<button class="action-btn" data-action="explorer" ${client.online ? "" : "disabled"}>📁 ${t("file_explorer")}</button>` : ""}
    ${hasClientPerm(client.id, "use_terminal") ? `<button class="action-btn" data-action="terminal" ${client.online ? "" : "disabled"}>⌨️ ${t("terminal")}</button>` : ""}
    ${hasClientPerm(client.id, "use_screen") ? `<button class="action-btn" data-action="vnc" ${client.online ? "" : "disabled"}>🖥️ ${t("remote_screen")}</button>` : ""}
    ${hasClientPerm(client.id, "use_guacamole") ? `<button class="action-btn" data-action="guacamole">🕹️ Guacamole</button>` : ""}
    ${hasClientPerm(client.id, "use_taskmanager") ? `<button class="action-btn" data-action="taskmanager" ${client.online ? "" : "disabled"}>📋 ${t("task_manager")}</button>` : ""}`;
  holder.appendChild(box);
  target.appendChild(holder);
  box.querySelectorAll("[data-action]").forEach((btn) =>
    btn.addEventListener("click", () => handleAction(btn.dataset.action, client)));
  scaleToContainer(holder);
}

// --- WEBSITES-Part (Quick-Access) ---
export function renderWebsitesPart(target, client) {
  // Bei Favoriten-Änderung (Stern-Klick, auch anderswo) neu rendern, damit
  // Favoriten sofort nach oben rutschen. Listener nur EINMAL pro Ziel binden.
  if (!target._wsFavListener) {
    target._wsFavListener = (e) => {
      if (!document.body.contains(target)) {
        window.removeEventListener("favorites-changed", target._wsFavListener);
        return;
      }
      if (e.detail?.kind === "websites") renderWebsitesPart(target, client);
    };
    window.addEventListener("favorites-changed", target._wsFavListener);
  }
  target.innerHTML = `<div style="color:var(--subtext);font-size:12px">Lädt…</div>`;
  api.getClientWebsites(client.id).then((sites) => {
    target.innerHTML = "";
    if (!sites || !sites.length) {
      target.innerHTML = `<div style="color:var(--subtext);font-size:12px;margin-bottom:6px">Keine Websites verknüpft.</div>`;
    } else {
      // Favoriten-Hierarchie im Widget: goldene Sterne (Seitenleiste+Dashboard)
      // ganz oben, dann Akzent 1 (Seitenleiste), dann Akzent 2 (Dashboard),
      // ganz unten Websites ohne Stern. Innerhalb der Gruppen bleibt die
      // Reihenfolge stabil.
      const favRank = (w) => {
        const f = favState("websites", w.id);
        if (f.s && f.d) return 0;   // ★ gold  = beide
        if (f.s) return 1;          // ★ Akzent 1 = Seitenleiste
        if (f.d) return 2;          // ★ Akzent 2 = Dashboard
        return 3;                   // ☆ kein Favorit
      };
      sites = [...sites].sort((a, b) => favRank(a) - favRank(b));
      target.innerHTML = sites.map((w) => {
        const dotColor = !w.monitor_enabled ? "var(--subtext)"
          : w.last_status === "up" ? "var(--online, #3ecf8e)"
          : w.last_status === "down" ? "var(--danger, #ff4d6d)" : "var(--subtext)";
        const favMeta = { name: w.name, url: w.url, clientId: client.id, clientHostname: client.hostname };
        return `
          <div class="action-btn" data-ws="${esc(w.id)}" style="display:flex;align-items:center;gap:8px;padding-right:8px">
            <a href="${esc(w.url)}" target="_blank" rel="noopener noreferrer"
               style="display:flex;align-items:center;gap:8px;text-decoration:none;flex:1;color:inherit">
              <span style="color:${dotColor}">●</span><span>${esc(w.name)}</span>
            </a>
            ${favStarHtml("websites", w.id, favMeta)}
            <button class="taskbar-btn" data-ws-del="${esc(w.id)}" data-ws-name="${esc(w.name)}"
              title="Website-Verknüpfung löschen"
              style="padding:1px 6px;font-size:10px;border-color:var(--danger);color:var(--danger)">🗑</button>
          </div>`;
      }).join("");
      // BUGFIX: Der Favoriten-Stern im Website-WIDGET reagierte nicht auf
      // Klicks (weder öffnen noch favorisieren). Deshalb wird der Stern hier
      // zusätzlich DIREKT verkabelt. Kein Doppel-Toggle möglich: Greift der
      // globale, delegierte Capture-Handler (sidebar.js), stoppt der die
      // Propagation und dieser Listener feuert gar nicht erst.
      target.querySelectorAll(".fav-star[data-fav]").forEach((star) => {
        star.addEventListener("mousedown", (e) => { e.preventDefault(); e.stopPropagation(); });
        star.addEventListener("click", (e) => {
          e.preventDefault(); e.stopPropagation();
          const [kind, id] = star.dataset.fav.split(":");
          let meta;
          if (star.dataset.favMeta) { try { meta = JSON.parse(star.dataset.favMeta); } catch {} }
          // Stern-Pop-Animation abspielen, dann umschalten.
          // cycleFav feuert "favorites-changed" -> das Widget rendert sich
          // (inkl. neuer Sortierung) über den Listener oben selbst neu.
          star.classList.add("fav-pop");
          setTimeout(() => cycleFav(kind, id, meta), 120);
        });
      });
      // Hover PRO Website: Tooltip mit Name, vollständiger URL und dem
      // Monitoring-Status GENAU DIESER Website (statt nur des Panel-Namens).
      target.querySelectorAll("[data-ws]").forEach((rowEl) => {
        const w = sites.find((s) => String(s.id) === rowEl.dataset.ws);
        if (!w) return;
        const statusTxt = !w.monitor_enabled ? "kein Monitoring"
          : w.last_status === "up" ? "online (up)"
          : w.last_status === "down" ? `DOWN${w.last_error ? ": " + w.last_error : ""}`
          : "noch nicht geprüft";
        const color = !w.monitor_enabled ? "var(--subtext)"
          : w.last_status === "up" ? "var(--online, #3ecf8e)"
          : w.last_status === "down" ? "var(--danger, #ff4d6d)" : "var(--subtext)";
        rowEl.addEventListener("mouseenter", (e) => showFleetTip(tipHtml(), e.clientX, e.clientY));
        rowEl.addEventListener("mousemove", (e) => showFleetTip(tipHtml(), e.clientX, e.clientY));
        rowEl.addEventListener("mouseleave", () => hideFleetTip());
        function tipHtml() {
          return `<b>${esc(w.name)}</b><br><span style="color:var(--subtext)">${esc(w.url)}</span><br><span style="color:${color}">●</span> ${esc(statusTxt)}`;
        }
      });
    }

    // "+ Website hinzufügen" direkt im Widget (kein Umweg mehr über
    // "Client bearbeiten"). Anlegen/Löschen aktualisiert nur dieses Panel.
    const addBtn = document.createElement("button");
    addBtn.className = "action-btn";
    addBtn.style.cssText = "width:100%;justify-content:center;color:var(--accent);margin-top:4px";
    addBtn.textContent = "+ Website hinzufügen";
    addBtn.addEventListener("click", async () => {
      const name = await uiPrompt("Website hinzufügen", {
        description: `Anzeigename der Website für "${client.hostname}":`,
        placeholder: "z.B. Web-Interface" });
      if (name === null || !name.trim()) return;
      const url = await uiPrompt("Website hinzufügen", {
        description: "Vollständige URL (inkl. http(s)://):",
        value: client.ip ? `http://${client.ip}:80/` : "",
        placeholder: "https://192.168.1.10:8006" });
      if (url === null || !url.trim()) return;
      const monitor = await uiConfirm("Uptime-Monitoring aktivieren?", {
        description: "Ja: Die Website wird regelmäßig geprüft und der Status farbig angezeigt.\nNein: nur als Schnellzugriff-Link speichern.",
        okText: "Mit Monitoring", cancelText: "Ohne Monitoring" });
      try {
        await api.createClientWebsite(client.id, {
          name: name.trim(), url: url.trim(), monitor_enabled: !!monitor,
        });
        window.notify?.(`Website "${name.trim()}" verknüpft.`, "success");
        renderWebsitesPart(target, client);   // nur dieses Panel neu laden
      } catch (e) { window.notify?.("Anlegen fehlgeschlagen: " + e.message, "error"); }
    });
    target.appendChild(addBtn);

    target.querySelectorAll("[data-ws-del]").forEach((b) =>
      b.addEventListener("click", async (e) => {
        e.preventDefault(); e.stopPropagation();
        if (!(await uiConfirm(`Website "${b.dataset.wsName}" entfernen?`, {
          okText: "Entfernen", danger: true }))) return;
        try {
          await api.deleteClientWebsite(client.id, b.dataset.wsDel);
          window.notify?.("Website entfernt.", "success");
          renderWebsitesPart(target, client);
        } catch (err) { window.notify?.("Löschen fehlgeschlagen: " + err.message, "error"); }
      }));
  }).catch(() => { target.innerHTML = `<div style="color:var(--subtext);font-size:12px">Keine Websites.</div>`; });
}

// Ob ein Client überhaupt verknüpfte Websites hat (für Auto-Ausblenden im Default-Layout).
export function clientHasWebsites(clientId) {
  return api.getClientWebsites(clientId).then((s) => !!(s && s.length)).catch(() => false);
}

// --- ÜBERSICHT-Part (einzelne Sub-Ansicht: metrics|notes|disk) ---
// tab wählt die anzuzeigende Sub-Ansicht. rerender() erlaubt Toggles/Zeitspanne.
export function renderOverviewSub(target, client, tab, rerender) {
  // Historie beim ersten Anzeigen laden.
  if (!hasSeeded(client.id)) {
    seedHistory(client.id, []);
    api.getMetricsHistory(client.id).then((res) => {
      seedHistory(client.id, res.points || []);
      if (state.selection && state.selection.type === "client" && state.selection.id === client.id) rerender?.();
    }).catch(() => {});
  }
  target.innerHTML = "";
  const m = client.metrics;

  if (tab === "notes") {
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

  if (!m) { target.innerHTML = `<div style="color:var(--subtext);padding:20px 0">${t("no_live_data")}</div>`; return; }

  if (tab === "disk") {
    const disks = (m.disks && m.disks.length) ? m.disks
      : [{ device: "System", mountpoint: "", used: m.diskUsed, total: m.diskTotal }];
    const row = document.createElement("div");
    row.className = "metrics-row";
    row.style.flexWrap = "wrap";
    row.innerHTML = disks.map((d, i) => {
      const pct = d.total ? (d.used / d.total) * 100 : 0;
      const label = esc(d.device || d.mountpoint || "Disk");
      return `<div class="donut-wrap" data-disk="${i}" style="border-radius:10px;padding:4px">${gradientDonutSvg(pct, DISK_GRADIENT, label, `${formatBytes(d.used)} / ${formatBytes(d.total)}`)}</div>`;
    }).join("");
    target.appendChild(row);
    // Hover PRO Datenträger: Donut hervorheben + Tooltip mit exakten Werten
    // GENAU DIESES Datenträgers (Gerät, belegt/gesamt, frei, Prozent).
    row.querySelectorAll("[data-disk]").forEach((el) => {
      const d = disks[+el.dataset.disk];
      const pct = d.total ? Math.round((d.used / d.total) * 100) : 0;
      bindRowHover(el, () => `<b>${esc(d.device || d.mountpoint || "Disk")}</b>${d.mountpoint && d.device ? ` <span style="color:var(--subtext)">(${esc(d.mountpoint)})</span>` : ""}<br>` +
        `Belegt: <b>${formatBytes(d.used)}</b> / ${formatBytes(d.total)} (${pct}%)<br>` +
        `<span style="color:var(--subtext)">Frei: ${formatBytes((d.total || 0) - (d.used || 0))}</span>`);
    });
    return;
  }

  // metrics
  const cpuPct = m.cpuLoad ?? 0;
  const ramPct = m.memTotal ? (m.memUsed / m.memTotal) * 100 : 0;
  const swapTotal = m.swapTotal ?? 0, swapUsed = m.swapUsed ?? 0;
  const swapPct = swapTotal ? (swapUsed / swapTotal) * 100 : 0;
  const swapDonut = swapTotal > 0 ? `
    <div class="donut-wrap" data-donut="swap" style="border-radius:10px;padding:4px">
      ${gradientDonutSvg(swapPct, RAM_GRADIENT, "Swap", `${formatBytes(swapUsed)} / ${formatBytes(swapTotal)}`)}
      <div class="metric-sub">${t("free")}: ${formatBytes(swapTotal - swapUsed)}</div>
    </div>` : "";

  const topRow = document.createElement("div");
  topRow.className = "metrics-row";
  topRow.innerHTML = `
    <div class="donut-wrap" data-donut="cpu" style="border-radius:10px;padding:4px">
      ${gradientDonutSvg(cpuPct, CPU_GRADIENT, "CPU", "")}
      <div class="metric-sub">${m.cpuCores ?? "?"} ${t("cores")} · ${m.cpuThreads ?? "?"} ${t("threads")}</div>
    </div>
    <div class="donut-wrap" data-donut="ram" style="border-radius:10px;padding:4px">
      ${gradientDonutSvg(ramPct, RAM_GRADIENT, "RAM", `${formatBytes(m.memUsed)} / ${formatBytes(m.memTotal)}`)}
      <div class="metric-sub">${t("free")}: ${formatBytes(m.memAvailable ?? (m.memTotal - m.memUsed))}</div>
    </div>
    ${swapDonut}`;
  target.appendChild(topRow);

  // Hover PRO Donut: hervorheben + Tooltip mit den exakten Live-Werten
  // GENAU DIESER Metrik (nicht nur der Panel-Name).
  {
    const donutTips = {
      cpu: () => `<b>CPU-Auslastung</b><br><b>${Math.round(cpuPct)}%</b>` +
        (m.cpuModel ? `<br><span style="color:var(--subtext)">${esc(m.cpuModel)}</span>` : "") +
        `<br><span style="color:var(--subtext)">${m.cpuCores ?? "?"} ${t("cores")} · ${m.cpuThreads ?? "?"} ${t("threads")}${m.cpuFreq ? ` · ${Math.round(m.cpuFreq)} MHz` : ""}</span>`,
      ram: () => `<b>RAM-Auslastung</b><br><b>${Math.round(ramPct)}%</b> — ${formatBytes(m.memUsed)} / ${formatBytes(m.memTotal)}` +
        `<br><span style="color:var(--subtext)">${t("free")}: ${formatBytes(m.memAvailable ?? (m.memTotal - m.memUsed))}</span>`,
      swap: () => `<b>Swap-Auslastung</b><br><b>${Math.round(swapPct)}%</b> — ${formatBytes(swapUsed)} / ${formatBytes(swapTotal)}` +
        `<br><span style="color:var(--subtext)">${t("free")}: ${formatBytes(swapTotal - swapUsed)}</span>`,
    };
    topRow.querySelectorAll("[data-donut]").forEach((el) => bindRowHover(el, donutTips[el.dataset.donut] || (() => "")));
  }

  const range = getHistoryRange(client.id, TIME_RANGES[timeRangeIndex].ms);
  const rangeBar = document.createElement("div");
  rangeBar.style.cssText = "display:flex;justify-content:flex-end;align-items:center;gap:8px;margin:14px 0 4px";
  rangeBar.innerHTML = `
    <span style="font-size:11px;color:var(--subtext)">${t("time_range")}:</span>
    <select class="ov-time-range" style="padding:3px 6px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
      ${TIME_RANGES.map((r, i) => `<option value="${i}" ${i === timeRangeIndex ? "selected" : ""}>${r.label}</option>`).join("")}
    </select>`;
  target.appendChild(rangeBar);

  const chartsRow = document.createElement("div");
  chartsRow.style.cssText = "display:flex;gap:20px;flex-wrap:wrap";
  const cpuChartWrap = document.createElement("div");
  cpuChartWrap.className = "mini-chart";
  cpuChartWrap.innerHTML = `<div style="font-size:11px;color:var(--subtext);margin-bottom:4px">${t("cpu_history")} (%)</div>`;
  cpuChartWrap.appendChild(interactiveChart([{ label: "CPU", color: "#a97cff", values: range.cpu, timestamps: range.ts }], { unit: "%", yMax: 100 }));
  const ramChartWrap = document.createElement("div");
  ramChartWrap.className = "mini-chart";
  ramChartWrap.innerHTML = `<div style="font-size:11px;color:var(--subtext);margin-bottom:4px">${t("ram_history")} (%)</div>`;
  ramChartWrap.appendChild(interactiveChart([{ label: "RAM", color: "#c7d34a", values: range.ram, timestamps: range.ts }], { unit: "%", yMax: 100 }));
  const netChartWrap = document.createElement("div");
  netChartWrap.className = "mini-chart";
  const netHeader = document.createElement("div");
  netHeader.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:6px;flex-wrap:wrap";
  netHeader.innerHTML = `
    <span style="font-size:11px;color:var(--subtext)">${t("network")}</span>
    <span style="font-size:11px">
      <label style="color:#3ecf8e;margin-right:8px;cursor:pointer"><input type="checkbox" class="ov-net-in" ${showNetIn ? "checked" : ""} /> ↓ ${formatBytes(m.netIn ?? 0)}/s</label>
      <label style="color:#f5a524;cursor:pointer"><input type="checkbox" class="ov-net-out" ${showNetOut ? "checked" : ""} /> ↑ ${formatBytes(m.netOut ?? 0)}/s</label>
    </span>`;
  netChartWrap.appendChild(netHeader);
  const netSeries = [];
  if (showNetIn) netSeries.push({ label: "↓ In", color: "#3ecf8e", values: range.netIn, timestamps: range.ts });
  if (showNetOut) netSeries.push({ label: "↑ Out", color: "#f5a524", values: range.netOut, timestamps: range.ts });
  if (netSeries.length) netChartWrap.appendChild(interactiveChart(netSeries, { formatValue: (v) => formatBytes(v) + "/s" }));
  chartsRow.appendChild(cpuChartWrap); chartsRow.appendChild(ramChartWrap); chartsRow.appendChild(netChartWrap);
  target.appendChild(chartsRow);

  const netIn = target.querySelector(".ov-net-in");
  const netOut = target.querySelector(".ov-net-out");
  const rangeSel = target.querySelector(".ov-time-range");
  if (netIn) netIn.addEventListener("change", () => { showNetIn = netIn.checked; rerender?.(); });
  if (netOut) netOut.addEventListener("change", () => { showNetOut = netOut.checked; rerender?.(); });
  if (rangeSel) rangeSel.addEventListener("change", () => { timeRangeIndex = parseInt(rangeSel.value); rerender?.(); });
}

// Metadaten für die einzelnen Sub-Ansichten eines Übersicht-Ordners.
export const OVERVIEW_SUBS = {
  metrics: () => t("tab_metrics"),
  notes: () => t("tab_notes"),
  disk: () => t("tab_disk"),
};

// -----------------------------------------------------------------
// Quick Actions & Actions
// -----------------------------------------------------------------

export async function handleQuickAction(action, client) {
  if (action === "edit") {
    openWindow({ singleton: true, key: `edit-${client.id}`, appId: "edit-client", title: `${t("edit")} — ${client.hostname}`, props: { clientId: client.id }, w: 480, h: 520 });
    return;
  }
  if (action === "reboot" || action === "shutdown") {
    const label = action === "reboot" ? t("reboot") : t("shutdown");
    if (!(await uiConfirm(`${client.hostname}: ${label}?`, { okText: label, danger: true }))) return;
    const isWin = (client.platform || "").toLowerCase().includes("windows");
    let cmd;
    if (action === "reboot") cmd = isWin ? "shutdown /r /t 0" : "sudo reboot";
    else cmd = isWin ? "shutdown /s /t 0" : "sudo shutdown -h now";
    try { await api.execOnClient(client.id, cmd); window.notify?.(t("command_sent"), "success"); }
    catch (e) { window.notify?.("Fehler: " + e.message, "error"); }
    return;
  }
  if (action === "update") {
    if (!(await uiConfirm(`${client.hostname}: Agent aktualisieren?`, { description: "Der Agent lädt die neue Version, ersetzt sich selbst und startet neu.\nDas kann bis zu 60 Sekunden dauern (Bestätigung wird abgewartet).", okText: "Aktualisieren" }))) return;
    window.notify?.(`Aktualisiere Agent auf ${client.hostname}… (bis zu 60 s, bitte warten)`, "info", 60000, { tag: "agent-update:" + client.id });
    try {
      const res = await api.updateAgent(client.id);
      if (res && res.updated) {
        window.notify?.(`Agent auf ${client.hostname} erfolgreich aktualisiert und wieder verbunden.`, "success", 8000, { tag: "agent-update:" + client.id });
      } else {
        window.notify?.(`Agent-Update auf ${client.hostname} abgeschlossen.`, "success", 6000, { tag: "agent-update:" + client.id });
      }
    } catch (e) {
      window.notify?.("Update fehlgeschlagen: " + e.message, "error", 14000, { tag: "agent-update:" + client.id });
    }
    return;
  }
  if (action === "uninstall") {
    if (!(await uiConfirm(`${client.hostname}: Agent WIRKLICH deinstallieren?`, {
      description: "Es wird: 1) der Agent gestoppt, 2) alle Agent-Daten auf dem Client gelöscht,\n" +
        "3) der Client aus dem Dashboard entfernt – aber nur, wenn er wirklich offline geht.\n\n" +
        "Das kann bis zu 60 Sekunden dauern. Bitte warten.",
      okText: "Deinstallieren", danger: true }))) return;
    window.notify?.(`Deinstalliere Agent auf ${client.hostname}… (bis zu 60 s, bitte warten)`, "info", 60000, { tag: "agent-uninstall:" + client.id });
    try {
      const res = await api.uninstallAgent(client.id);
      if (res && res.removed) {
        // Aus dem Dashboard entfernt. Auswahl leeren, falls dieser Client gewählt war.
        if (state.selection && state.selection.type === "client" && state.selection.id === client.id) {
          state.selection = null;
        }
        window.notify?.(`Agent auf ${client.hostname} deinstalliert und aus dem Dashboard entfernt.`, "success", 8000, { tag: "agent-uninstall:" + client.id });
        renderMainContent();
      } else {
        window.notify?.(`Deinstallation auf ${client.hostname} abgeschlossen.`, "success", 6000, { tag: "agent-uninstall:" + client.id });
      }
    } catch (e) {
      window.notify?.("Deinstallation fehlgeschlagen: " + e.message, "error", 12000, { tag: "agent-uninstall:" + client.id });
    }
  }
}

export function handleAction(action, client) {
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

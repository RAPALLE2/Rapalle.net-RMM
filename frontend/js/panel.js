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
import { hasClientPerm, hasGlobalPerm, isAdmin } from "./state.js";
import { formatBytes, formatUptime, esc, gradientDonutSvg, CPU_GRADIENT, RAM_GRADIENT, DISK_GRADIENT, interactiveChart, uiConfirm, uiPrompt } from "./utils.js";
import { getHistoryRange, TIME_RANGES, seedHistory, hasSeeded } from "./metricshistory.js";
import { openWindow } from "./windowmanager.js";
import { subjectPickerHtml, readSubjectPicker, initSubjectPicker } from "./subjectpicker.js";
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
    // Ohne das Recht 'see_dashboard' bleibt die Dashboard-Ansicht bewusst eine
    // leere schwarze Seite (keine Zahlen, Widgets oder Favoriten).
    if (!isAdmin() && !hasGlobalPerm("see_dashboard")) {
      el.innerHTML = `<div style="position:absolute;inset:0;background:#000"></div>`;
      return;
    }
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
    <div class="client-view-head" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
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
      <h3 style="margin:0 0 8px">${t("dt_detected", { label: esc(label) })}</h3>
      <p style="color:var(--subtext);font-size:13px;margin:0 0 12px">
        Der Agent auf <b>${esc(client.hostname)}</b> hat erkannt, dass er in einer
        ${t("dt_question", { label: esc(label) })}
        ${t("dt_hint")}
      </p>
      <div class="form-row">
        <label>${t("dt_parent")}</label>
        <select id="dt-host">
          <option value="">— kein Host zuordnen —</option>
          ${hosts.map((h) => `<option value="${esc(h.id)}">${esc(h.hostname)}</option>`).join("")}
        </select>
      </div>
      <div id="dt-error" class="form-error hidden"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button class="taskbar-btn" id="dt-keep">Als physisch behalten</button>
        <button class="btn-primary" id="dt-apply" style="width:auto;margin:0">${t("dt_apply", { kind: detected === "vm" ? "VM" : "LXC" })}</button>
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
          ? t("dt_keep_physical", { host: client.hostname })
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
  holder.style.cssText = "width:100%;height:100%;display:flex;align-items:center;gap:14px 20px;flex-wrap:wrap;min-width:0;align-content:center";
  const box = holder;
  box.innerHTML = `
    <div class="quick-actions" style="flex:none;width:180px;display:flex;flex-direction:column;gap:7px;margin-top:0;flex-wrap:nowrap;justify-content:center;align-self:stretch">
      ${hasClientPerm(client.id, "c_power") ? `
      <button data-quick="reboot" ${client.online ? "" : "disabled"}>🔄 ${t("reboot")}</button>
      <button data-quick="shutdown" ${client.online ? "" : "disabled"}>📴 ${t("shutdown")}</button>` : ""}
      ${hasClientPerm(client.id, "manage_agent") ? `
      <button data-quick="update" ${client.online ? "" : "disabled"}>⬆️ ${t("update_agent")}</button>
      <button data-quick="uninstall" ${client.online ? "" : "disabled"} title="${t("uninstall_agent")}">🗑️ ${t("uninstall_agent")}</button>` : ""}
      ${hasClientPerm(client.id, "manage_clients") ? `<button data-quick="edit">✏️ ${t("edit")}</button>` : ""}
    </div>
    <div style="flex:1;min-width:200px;display:flex;flex-direction:column;justify-content:center">
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
    ${hasClientPerm(client.id, "c_explorer_view") ? `<button class="action-btn" data-action="explorer" ${client.online ? "" : "disabled"}>📁 ${t("file_explorer")}</button>` : ""}
    ${hasClientPerm(client.id, "c_terminal_console") ? `<button class="action-btn" data-action="terminal" ${client.online ? "" : "disabled"}>⌨️ ${t("terminal")}</button>` : ""}
    ${hasClientPerm(client.id, "c_screen_view") ? `<button class="action-btn" data-action="vnc" ${client.online ? "" : "disabled"}>🖥️ ${t("remote_screen")}${hasClientPerm(client.id, "c_screen") ? "" : " 👁️"}</button>` : ""}
    ${hasClientPerm(client.id, "c_guacamole") ? `<button class="action-btn" data-action="guacamole">🕹️ Guacamole</button>` : ""}
    ${hasClientPerm(client.id, "c_taskmanager_view") ? `<button class="action-btn" data-action="taskmanager" ${client.online ? "" : "disabled"}>📋 ${t("task_manager")}</button>` : ""}`;
  holder.appendChild(box);
  target.appendChild(holder);
  box.querySelectorAll("[data-action]").forEach((btn) =>
    btn.addEventListener("click", () => handleAction(btn.dataset.action, client)));
  scaleToContainer(holder);
}

// --- WEBSITES-Part (Quick-Access) ---
// =================================================================
// WEBSITES-WIDGET (vollständig)
// Enthält ALLES, was früher unter "Client bearbeiten" stand:
// anlegen (Name, URL, Öffnen-in, Monitoring + Modus + Intervall),
// bearbeiten, Öffnungsart umschalten, Monitoring an/aus, Favoriten-Stern,
// Status-Ampel mit Tooltip und Löschen.
// =================================================================
const WS_NOTIFY_LABELS = { down: "bei DOWN", up: "bei UP", always: "immer" };
const WS_INTERVALS = [
  [30, "30 Sekunden"], [60, "1 Minute"], [300, "5 Minuten"],
  [900, "15 Minuten"], [1800, "30 Minuten"], [3600, "1 Stunde"],
];
function wsIntervalLabel(sec) {
  if (!sec) return "–";
  if (sec >= 3600) return `${Math.round(sec / 3600)} h`;
  if (sec >= 60) return `${Math.round(sec / 60)} min`;
  return `${sec} s`;
}

// Formular für Anlegen (w = null) bzw. Bearbeiten (w = Website-Objekt).
function wsFormHtml(w, client) {
  const v = w || { name: "", url: client.ip ? `http://${client.ip}:80/` : "",
                   open_mode: "external", monitor_enabled: 0,
                   monitor_notify: "down", monitor_interval_seconds: 300 };
  return `
    <div class="ws-form" style="border:1px solid var(--border);border-radius:8px;padding:8px;margin:6px 0;display:flex;flex-direction:column;gap:6px">
      <input class="ws-f-name" type="text" placeholder="Name (z.B. Proxmox Web-UI)" value="${esc(v.name)}" />
      <input class="ws-f-url" type="text" placeholder="https://…" value="${esc(v.url)}" />
      <select class="ws-f-openmode">
        <option value="external" ${v.open_mode !== "internal" ? "selected" : ""}>${t("ws_open_external")}</option>
        <option value="internal" ${v.open_mode === "internal" ? "selected" : ""}>${t("ws_open_internal")}</option>
      </select>
      <label style="display:flex;gap:6px;align-items:center;font-size:12px">
        <input class="ws-f-mon" type="checkbox" ${v.monitor_enabled ? "checked" : ""} /> Uptime-Monitoring aktivieren
      </label>
      <div class="ws-f-monopts" style="${v.monitor_enabled ? "" : "display:none"};display:flex;flex-direction:column;gap:6px">
        <select class="ws-f-notify">
          <option value="down" ${v.monitor_notify === "down" ? "selected" : ""}>Benachrichtigen: wenn DOWN</option>
          <option value="up" ${v.monitor_notify === "up" ? "selected" : ""}>Benachrichtigen: wenn UP</option>
          <option value="always" ${v.monitor_notify === "always" ? "selected" : ""}>Benachrichtigen: immer (jeder Scan)</option>
        </select>
        <select class="ws-f-interval">
          ${WS_INTERVALS.map(([sec, lbl]) =>
            `<option value="${sec}" ${Number(v.monitor_interval_seconds) === sec ? "selected" : ""}>Scan alle ${lbl}</option>`).join("")}
        </select>
        <div style="font-size:11px;color:var(--subtext)">
          ${t("ws_notify_hint")}
        </div>
      </div>
      <div class="ws-f-error form-error hidden"></div>
      <div style="display:flex;gap:6px">
        <button class="btn-primary ws-f-save" style="flex:1;margin:0">${w ? t("save") : t("ws_add")}</button>
        <button class="taskbar-btn ws-f-cancel">${t("cancel")}</button>
      </div>
    </div>`;
}

// Verkabelt ein Formular. onDone(fields) bekommt die eingegebenen Werte.
function bindWsForm(formEl, onDone, onCancel) {
  const monCb = formEl.querySelector(".ws-f-mon");
  const opts = formEl.querySelector(".ws-f-monopts");
  monCb.addEventListener("change", () => { opts.style.display = monCb.checked ? "flex" : "none"; });
  // Klicks im Formular dürfen das Panel weder ziehen noch aufklappen.
  formEl.addEventListener("mousedown", (e) => e.stopPropagation());
  formEl.addEventListener("click", (e) => e.stopPropagation());
  formEl.querySelector(".ws-f-cancel").addEventListener("click", onCancel);
  formEl.querySelector(".ws-f-save").addEventListener("click", async () => {
    const err = formEl.querySelector(".ws-f-error");
    err.classList.add("hidden");
    const name = formEl.querySelector(".ws-f-name").value.trim();
    const url = formEl.querySelector(".ws-f-url").value.trim();
    if (!name || !url) { err.textContent = t("u_name_und_url_erforderlich"); err.classList.remove("hidden"); return; }
    try {
      await onDone({
        name, url,
        open_mode: formEl.querySelector(".ws-f-openmode").value,
        monitor_enabled: monCb.checked,
        monitor_notify: formEl.querySelector(".ws-f-notify").value,
        monitor_interval_seconds: parseInt(formEl.querySelector(".ws-f-interval").value, 10),
      });
    } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
  });
}

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
  const mayEdit = isAdmin() || hasClientPerm(client.id, "c_websites_edit");
  const reload = () => renderWebsitesPart(target, client);

  target.innerHTML = `<div style="color:var(--subtext);font-size:12px">${t("loading")}</div>`;
  api.getClientWebsites(client.id).then((sites) => {
    target.innerHTML = "";
    if (!sites || !sites.length) {
      target.innerHTML = `<div style="color:var(--subtext);font-size:12px;margin-bottom:6px">${t("ws_none")}</div>`;
    } else {
      // Favoriten-Hierarchie im Widget: goldene Sterne (Seitenleiste+Dashboard)
      // ganz oben, dann Akzent 1 (Seitenleiste), dann Akzent 2 (Dashboard),
      // ganz unten Websites ohne Stern.
      const favRank = (w) => {
        const f = favState("websites", w.id);
        if (f.s && f.d) return 0;
        if (f.s) return 1;
        if (f.d) return 2;
        return 3;
      };
      sites = [...sites].sort((a, b) => favRank(a) - favRank(b));
      target.innerHTML = sites.map((w) => {
        const dotColor = !w.monitor_enabled ? "var(--subtext)"
          : w.last_status === "up" ? "var(--online, #3ecf8e)"
          : w.last_status === "down" ? "var(--danger, #ff4d6d)" : "var(--subtext)";
        const favMeta = { name: w.name, url: w.url, clientId: client.id, clientHostname: client.hostname, open_mode: w.open_mode };
        return `
          <div class="action-btn" data-ws="${esc(w.id)}" style="display:flex;align-items:center;gap:8px;padding-right:8px">
            <a href="${esc(w.url)}" data-ws-openlink="${esc(w.id)}"
               style="display:flex;align-items:center;gap:8px;text-decoration:none;flex:1;min-width:0;color:inherit;cursor:pointer">
              <span style="color:${dotColor}">●</span>
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(w.name)}${w.open_mode === "internal" ? " 🪟" : ""}</span>
            </a>
            ${favStarHtml("websites", w.id, favMeta)}
            ${mayEdit ? `
              <button class="taskbar-btn" data-ws-openmode="${esc(w.id)}"
                title="${t("ws_tip_mode", { mode: w.open_mode === "internal" ? t("ws_mode_internal") : t("ws_mode_external") })}"
                style="padding:1px 6px;font-size:10px">${w.open_mode === "internal" ? "🪟" : "🔗"}</button>
              <button class="taskbar-btn" data-ws-mon="${esc(w.id)}"
                title="Monitoring ${w.monitor_enabled ? `an (alle ${wsIntervalLabel(w.monitor_interval_seconds)}, ${WS_NOTIFY_LABELS[w.monitor_notify] || w.monitor_notify}) – Klick: aus` : "aus – Klick: an"}"
                style="padding:1px 6px;font-size:10px;${w.monitor_enabled ? "" : "opacity:.55"}">📡</button>
              <button class="taskbar-btn" data-ws-edit="${esc(w.id)}" title="Bearbeiten"
                style="padding:1px 6px;font-size:10px">✏️</button>
              <button class="taskbar-btn" data-ws-del="${esc(w.id)}" data-ws-name="${esc(w.name)}"
                title="${t("ws_tip_delete")}"
                style="padding:1px 6px;font-size:10px;border-color:var(--danger);color:var(--danger)">🗑</button>` : ""}
          </div>
          <div class="ws-editbox" data-ws-editbox="${esc(w.id)}" style="display:none"></div>`;
      }).join("");

      // Öffnen nach open_mode: 'internal' -> internes Browser-Fenster,
      // sonst normaler externer Tab.
      target.querySelectorAll("[data-ws-openlink]").forEach((a) =>
        a.addEventListener("click", (e) => {
          e.preventDefault();
          const s2 = sites.find((x) => x.id === a.dataset.wsOpenlink);
          if (s2) import("./apps/webbrowser.js").then((m) => m.openWebsiteEntry(s2));
        })
      );
      // BUGFIX: Der Favoriten-Stern im Website-WIDGET reagierte nicht auf
      // Klicks. Deshalb wird der Stern hier zusätzlich DIREKT verkabelt.
      target.querySelectorAll(".fav-star[data-fav]").forEach((star) => {
        star.addEventListener("mousedown", (e) => { e.preventDefault(); e.stopPropagation(); });
        star.addEventListener("click", (e) => {
          e.preventDefault(); e.stopPropagation();
          const [kind, id] = star.dataset.fav.split(":");
          let meta;
          if (star.dataset.favMeta) { try { meta = JSON.parse(star.dataset.favMeta); } catch {} }
          star.classList.remove("fav-pop");
          void star.offsetWidth;
          star.classList.add("fav-pop");
          cycleFav(kind, id, meta);
        });
      });

      // Öffnungsart umschalten
      target.querySelectorAll("[data-ws-openmode]").forEach((b) =>
        b.addEventListener("click", async (e) => {
          e.preventDefault(); e.stopPropagation();
          const w = sites.find((x) => x.id === b.dataset.wsOpenmode);
          try {
            await api.updateClientWebsite(client.id, w.id,
              { open_mode: w.open_mode === "internal" ? "external" : "internal" });
            reload();
          } catch (err) { window.notify?.(t("ws_change_failed", { err: err.message }), "error"); }
        }));

      // Monitoring an/aus
      target.querySelectorAll("[data-ws-mon]").forEach((b) =>
        b.addEventListener("click", async (e) => {
          e.preventDefault(); e.stopPropagation();
          const w = sites.find((x) => x.id === b.dataset.wsMon);
          try {
            await api.updateClientWebsite(client.id, w.id, { monitor_enabled: !w.monitor_enabled });
            reload();
          } catch (err) { window.notify?.(t("ws_change_failed", { err: err.message }), "error"); }
        }));

      // Bearbeiten (Formular direkt unter der Zeile aufklappen)
      target.querySelectorAll("[data-ws-edit]").forEach((b) =>
        b.addEventListener("click", (e) => {
          e.preventDefault(); e.stopPropagation();
          const w = sites.find((x) => x.id === b.dataset.wsEdit);
          const box = target.querySelector(`[data-ws-editbox="${w.id}"]`);
          if (box.style.display !== "none") { box.style.display = "none"; box.innerHTML = ""; return; }
          box.style.display = "";
          box.innerHTML = wsFormHtml(w, client);
          bindWsForm(box.querySelector(".ws-form"),
            async (fields) => {
              await api.updateClientWebsite(client.id, w.id, fields);
              window.notify?.("Website gespeichert", "success");
              reload();
            },
            () => { box.style.display = "none"; box.innerHTML = ""; });
        }));

      // Status-Tooltip pro Zeile
      target.querySelectorAll("[data-ws]").forEach((rowEl) => {
        const w = sites.find((x) => x.id === rowEl.dataset.ws);
        if (!w) return;
        const statusTxt = !w.monitor_enabled ? t("u_kein_monitoring")
          : w.last_status === "up" ? "erreichbar"
          : w.last_status === "down" ? t("ws_unreachable", { detail: w.last_error ? ": " + w.last_error : "" })
          : t("ws_unchecked");
        const color = !w.monitor_enabled ? "var(--subtext)"
          : w.last_status === "up" ? "var(--online, #3ecf8e)"
          : w.last_status === "down" ? "var(--danger, #ff4d6d)" : "var(--subtext)";
        const tipHtml = () => `<b>${esc(w.name)}</b><br><span style="color:var(--subtext)">${esc(w.url)}</span>`
          + `<br><span style="color:${color}">●</span> ${esc(statusTxt)}`
          + (w.monitor_enabled ? `<br><span style="color:var(--subtext)">Scan alle ${wsIntervalLabel(w.monitor_interval_seconds)}, benachrichtigen ${WS_NOTIFY_LABELS[w.monitor_notify] || w.monitor_notify}</span>` : "")
          + (w.last_checked ? `<br><span style="color:var(--subtext)">${t("ws_last_check", { date: new Date(w.last_checked).toLocaleString() })}</span>` : "");
        rowEl.addEventListener("mouseenter", (e) => showFleetTip(tipHtml(), e.clientX, e.clientY));
        rowEl.addEventListener("mousemove", (e) => showFleetTip(tipHtml(), e.clientX, e.clientY));
        rowEl.addEventListener("mouseleave", () => hideFleetTip());
      });

      // Löschen
      target.querySelectorAll("[data-ws-del]").forEach((b) =>
        b.addEventListener("click", async (e) => {
          e.preventDefault(); e.stopPropagation();
          if (!(await uiConfirm(`Website "${b.dataset.wsName}" entfernen?`, {
            okText: "Entfernen", danger: true }))) return;
          try {
            await api.deleteClientWebsite(client.id, b.dataset.wsDel);
            window.notify?.("Website entfernt.", "success");
            reload();
          } catch (err) { window.notify?.(t("ws_delete_failed", { err: err.message }), "error"); }
        }));
    }

    // "+ Website hinzufügen": klappt das vollständige Formular direkt im
    // Widget auf (Name, URL, Öffnen-in, Monitoring + Modus + Intervall).
    if (!mayEdit) return;
    const addBox = document.createElement("div");
    const addBtn = document.createElement("button");
    addBtn.className = "action-btn";
    addBtn.style.cssText = "width:100%;justify-content:center;color:var(--accent);margin-top:4px";
    addBtn.textContent = t("ws_add_btn");
    addBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (addBox.innerHTML) { addBox.innerHTML = ""; return; }
      addBox.innerHTML = wsFormHtml(null, client);
      bindWsForm(addBox.querySelector(".ws-form"),
        async (fields) => {
          await api.createClientWebsite(client.id, fields);
          window.notify?.(t("ws_linked", { name: fields.name }), "success");
          reload();
        },
        () => { addBox.innerHTML = ""; });
    });
    target.appendChild(addBtn);
    target.appendChild(addBox);
  }).catch(() => { target.innerHTML = `<div style="color:var(--subtext);font-size:12px">Keine Websites.</div>`; });
}


// =================================================================
// CHILDREN-WIDGET: VMs und LXC-Container, die auf DIESEM Client laufen
// (clients mit parent_client_id === client.id). Pro Eintrag:
//   - Status-Ampel (online/offline) + Typ-Icon (🖥️ VM / 📦 LXC)
//   - Kurzinfo (CPU/RAM, sofern online)
//   - Button "Öffnen": wählt den Client in der Seitenleiste aus
//   - Button "Fenster": öffnet ihn als eigenes Fenster (wie Desktop-Drop)
// =================================================================
export function childClientsOf(clientId) {
  return (state.clients || []).filter((c) => c.parent_client_id === clientId);
}

export function renderChildrenPart(target, client) {
  // Live mitlaufen: kommen neue Metriken für einen der Gäste (oder ändert sich
  // die Client-Liste), das Panel neu zeichnen. Listener nur EINMAL pro Ziel.
  if (!target._kidsListener) {
    target._kidsListener = (e) => {
      if (!document.body.contains(target)) {
        window.removeEventListener("metrics-updated", target._kidsListener);
        return;
      }
      const id = e?.detail?.id;
      if (!id || childClientsOf(client.id).some((c) => c.id === id)) {
        renderChildrenPart(target, client);
      }
    };
    window.addEventListener("metrics-updated", target._kidsListener);
  }
  target.innerHTML = "";
  const kids = childClientsOf(client.id)
    .sort((a, b) => (b.online - a.online) || String(a.hostname || "").localeCompare(String(b.hostname || "")));

  if (!kids.length) {
    target.innerHTML = `<div style="color:var(--subtext);font-size:12px">
      Keine VMs oder LXC-Container zugeordnet.<br>
      ${t("kids_hint")}</div>`;
    return;
  }

  const head = document.createElement("div");
  head.style.cssText = "font-size:11.5px;color:var(--subtext);margin-bottom:4px";
  const onlineCount = kids.filter((c) => c.online).length;
  head.textContent = `${kids.length} Gast${kids.length === 1 ? "" : "-Systeme"} · ${onlineCount} online`;
  target.appendChild(head);

  for (const kid of kids) {
    const isLxc = (kid.device_type || "") === "lxc";
    const dot = kid.online ? "var(--online, #3ecf8e)" : "var(--subtext)";
    const cpu = kid.metrics?.cpuLoad;
    const memPct = kid.metrics?.memTotal
      ? Math.round((kid.metrics.memUsed || 0) / kid.metrics.memTotal * 100) : null;
    const info = kid.online && (cpu != null || memPct != null)
      ? `${cpu != null ? `CPU ${Math.round(cpu)}%` : ""}${cpu != null && memPct != null ? " · " : ""}${memPct != null ? `RAM ${memPct}%` : ""}`
      : (kid.online ? "online" : "offline");

    const row = document.createElement("div");
    row.className = "action-btn";
    row.style.cssText = "display:flex;align-items:center;gap:8px;padding-right:8px";
    row.innerHTML = `
      <span style="color:${dot};flex:none">●</span>
      <span style="flex:none">${isLxc ? "📦" : "🖥️"}</span>
      <span style="flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.2;text-align:left">
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(kid.hostname || kid.id)}</span>
        <span style="font-size:10.5px;color:var(--subtext)">${esc(info)}</span>
      </span>
      <button class="taskbar-btn" data-kid-open="${esc(kid.id)}" title="${t("kids_select")}"
        style="padding:1px 7px;font-size:10px">${t("kid_open")}</button>
      <button class="taskbar-btn" data-kid-win="${esc(kid.id)}" title="${t("kid_open_win")}"
        style="padding:1px 6px;font-size:10px">↗️</button>`;
    target.appendChild(row);
  }

  // "Öffnen": Client in der Seitenleiste auswählen (Hauptansicht wechselt).
  target.querySelectorAll("[data-kid-open]").forEach((b) =>
    b.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      const id = b.dataset.kidOpen;
      import("./sidebar.js").then((m) => {
        try { m.selectClientExternal(id); }
        catch { state.selection = { type: "client", id }; }
      }).catch(() => { state.selection = { type: "client", id }; });
    }));

  // "↗️": als eigenes Fenster öffnen (gleiche Ansicht wie beim Desktop-Drop).
  target.querySelectorAll("[data-kid-win]").forEach((b) =>
    b.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      const kid = findClient(b.dataset.kidWin);
      if (!kid) return;
      openWindow({
        singleton: true,
        key: `panelpart-${kid.id}-client-x`, appId: "panelpart",
        title: kid.hostname,
        props: { clientId: kid.id, part: "client" },
        clientColor: kid.color, w: 900, h: 620,
      });
    }));
}

// Ob ein Client überhaupt verknüpfte Websites hat (für Auto-Ausblenden im Default-Layout).
export function clientHasWebsites(clientId) {
  return api.getClientWebsites(clientId).then((s) => !!(s && s.length)).catch(() => false);
}

// =================================================================
// NOTIZEN mit Sichtbarkeit + Aktivitätsprotokoll
//   für alle           -> jeder, der den Client sehen darf
//   nur für mich       -> ausschließlich der Verfasser
//   für bestimmte      -> Verfasser + ausgewählte Benutzer
// Ändern/Löschen darf der Verfasser (Admins zum Aufräumen ebenfalls, sehen
// aber den Inhalt fremder privater Notizen nicht).
// =================================================================
const NOTE_VIS = {
  all: { icon: "🌍", label: t("note_vis_all") },
  private: { icon: "🔒", label: t("note_vis_private") },
  custom: { icon: "👥", label: t("note_vis_custom") },
};

export function renderNotesPart(target, client) {
  const mayEdit = isAdmin() || hasClientPerm(client.id, "c_notes_edit");
  let showLog = false;
  let users = null;              // {users:[…], groups:[…]}
  let editingId = null;
  let addOpen = false;           // "Notiz hinzufügen" standardmäßig zu

  target.innerHTML = `<div style="color:var(--subtext);font-size:12px">${t("loading")}</div>`;

  async function load() {
    let notes = [];
    try { notes = await api.getNotes(client.id); }
    catch (e) {
      target.innerHTML = `<div style="color:var(--danger);font-size:12px">${esc(e.message)}</div>`;
      return;
    }
    if (!users) { try { users = await api.getNotesUsers(client.id); } catch { users = { users: [], groups: [] }; } }
    draw(notes);
  }

  // Anzeigename einer Freigabe ({type,id} oder alte reine Benutzer-ID).
  function shareName(item) {
    const type = typeof item === "string" ? "user" : (item.type || "user");
    const id = typeof item === "string" ? item : item.id;
    if (type === "group") {
      const g = (users?.groups || []).find((x) => x.id === id);
      return g ? `👥 ${g.name}` : "👥 ?";
    }
    const u = (users?.users || []).find((x) => x.id === id);
    return u ? u.username : id;
  }

  // Formular für neue Notiz bzw. zum Bearbeiten.
  function formHtml(note) {
    const v = note?.visibility || "all";
    return `
      <div class="note-form" style="border:1px solid var(--border);border-radius:8px;padding:8px;display:flex;flex-direction:column;gap:6px;margin-bottom:8px">
        <textarea class="nf-text" rows="3" placeholder="Notiz schreiben…"
          style="resize:vertical;min-height:56px">${esc(note?.text || "")}</textarea>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <select class="nf-vis" style="flex:1;min-width:150px">
            ${Object.entries(NOTE_VIS).map(([k, o]) =>
              `<option value="${k}" ${v === k ? "selected" : ""}>${o.icon} Sichtbar ${o.label}</option>`).join("")}
          </select>
          <label style="display:flex;gap:5px;align-items:center;font-size:12px;color:var(--subtext)">
            <input type="checkbox" class="nf-pin" ${note?.pinned ? "checked" : ""} /> anheften
          </label>
        </div>
        <div class="nf-share" style="${v === "custom" ? "" : "display:none"}">
          ${subjectPickerHtml(users, note?.shared_with || [], { name: "nf-subj" })}
        </div>
        <div class="nf-error form-error hidden"></div>
        <div style="display:flex;gap:6px">
          <button class="btn-primary nf-save" style="flex:1;margin:0">${note ? t("save") : "+ Notiz anlegen"}</button>
          ${note ? `<button class="taskbar-btn nf-cancel">Abbrechen</button>` : ""}
        </div>
      </div>`;
  }

  function bindForm(el, note) {
    const visSel = el.querySelector(".nf-vis");
    const shareBox = el.querySelector(".nf-share");
    visSel.addEventListener("change", () => {
      shareBox.style.display = visSel.value === "custom" ? "" : "none";
    });
    initSubjectPicker(shareBox);
    el.addEventListener("mousedown", (e) => e.stopPropagation());
    el.querySelector(".nf-cancel")?.addEventListener("click", () => {
      editingId = null; addOpen = false; load();
    });
    el.querySelector(".nf-save").addEventListener("click", async () => {
      const err = el.querySelector(".nf-error");
      err.classList.add("hidden");
      const text = el.querySelector(".nf-text").value.trim();
      if (!text) { err.textContent = "Die Notiz ist leer"; err.classList.remove("hidden"); return; }
      const data = {
        text,
        visibility: visSel.value,
        pinned: el.querySelector(".nf-pin").checked,
        shared_with: readSubjectPicker(el.querySelector(".nf-share")),
      };
      if (data.visibility === "custom" && !data.shared_with.length) {
        err.textContent = t("note_pick_user");
        err.classList.remove("hidden"); return;
      }
      try {
        if (note) await api.updateNote(client.id, note.id, data);
        else { await api.createNote(client.id, data); addOpen = false; }
        editingId = null;
        load();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });
  }

  function draw(notes) {
    target.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:6px;min-height:0">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <span style="font-size:11.5px;color:var(--subtext)">${notes.length} Notiz${notes.length === 1 ? "" : "en"}</span>
          <button class="taskbar-btn" id="note-log" style="font-size:11px">${t("note_history")}</button>
        </div>
        <div id="note-logbox" style="display:none"></div>
        <div id="note-add"></div>
        <div id="note-list" style="display:flex;flex-direction:column;gap:6px"></div>
      </div>`;

    // "Notiz hinzufügen" ist standardmäßig EINGEKLAPPT - das Formular nimmt
    // sonst dauerhaft den halben Platz im Widget ein. Ein Klick öffnet es;
    // der Zustand bleibt beim Neuzeichnen erhalten (addOpen).
    if (mayEdit && !editingId) {
      const box = target.querySelector("#note-add");
      const btn = document.createElement("button");
      btn.className = "action-btn";
      btn.style.cssText = "width:100%;justify-content:center;color:var(--accent)";
      btn.textContent = addOpen ? t("note_add_cancel") : t("note_add");
      btn.addEventListener("click", () => { addOpen = !addOpen; draw(notes); });
      box.appendChild(btn);
      if (addOpen) {
        const form = document.createElement("div");
        form.style.marginTop = "6px";
        form.innerHTML = formHtml(null);
        box.appendChild(form);
        bindForm(form.querySelector(".note-form"), null);
        form.querySelector(".nf-text")?.focus();
      }
    }

    const listEl = target.querySelector("#note-list");
    if (!notes.length) {
      listEl.innerHTML = `<div style="color:var(--subtext);font-size:12px">Noch keine Notizen.</div>`;
    } else {
      listEl.innerHTML = notes.map((n) => {
        if (editingId === n.id) return `<div class="note-edit" data-edit="${esc(n.id)}"></div>`;
        const vinfo = NOTE_VIS[n.visibility] || NOTE_VIS.all;
        const shared = n.visibility === "custom" && (n.shared_with || []).length
          ? ` (${n.shared_with.map(shareName).map(esc).join(", ")})` : "";
        const when = new Date(n.updated_at || n.created_at).toLocaleString("de-DE");
        return `
          <div class="panel" style="padding:7px 9px;${n.pinned ? "border-color:var(--accent)" : ""}">
            <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">
              <div style="font-size:11px;color:var(--subtext)">
                ${n.pinned ? "📌 " : ""}${vinfo.icon} ${esc(vinfo.label)}${shared}
                · ${esc(n.author_name || "?")} · ${when}
              </div>
              ${n.can_edit ? `<div style="display:flex;gap:4px;flex:none">
                ${n.hidden ? "" : `<button class="taskbar-btn" data-nedit="${esc(n.id)}" style="padding:0 5px;font-size:10px">✏️</button>`}
                <button class="taskbar-btn" data-ndel="${esc(n.id)}" style="padding:0 5px;font-size:10px;border-color:var(--danger);color:var(--danger)">🗑</button>
              </div>` : ""}
            </div>
            <div style="font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:3px">
              ${n.hidden ? `<i style="color:var(--subtext)">${t("note_private_hidden")}</i>` : esc(n.text)}
            </div>
          </div>`;
      }).join("");

      // Bearbeiten-Formular an der Stelle der Notiz einsetzen
      const editEl = listEl.querySelector("[data-edit]");
      if (editEl) {
        const note = notes.find((x) => x.id === editEl.dataset.edit);
        editEl.innerHTML = formHtml(note);
        bindForm(editEl.querySelector(".note-form"), note);
      }
      listEl.querySelectorAll("[data-nedit]").forEach((b) =>
        b.addEventListener("click", () => { editingId = b.dataset.nedit; draw(notes); }));
      listEl.querySelectorAll("[data-ndel]").forEach((b) =>
        b.addEventListener("click", async () => {
          if (!(await uiConfirm(t("note_delete_q"), { okText: t("delete"), danger: true }))) return;
          try { await api.deleteNote(client.id, b.dataset.ndel); load(); }
          catch (e) { window.notify?.(e.message, "error"); }
        }));
    }

    // Aktivitätsprotokoll ein-/ausklappen
    const logBtn = target.querySelector("#note-log");
    const logBox = target.querySelector("#note-logbox");
    logBox.style.display = showLog ? "" : "none";
    if (showLog) fillLog(logBox);
    logBtn.addEventListener("click", () => {
      showLog = !showLog;
      logBox.style.display = showLog ? "" : "none";
      if (showLog) fillLog(logBox);
    });
  }

  async function fillLog(box) {
    box.innerHTML = `<div style="color:var(--subtext);font-size:11.5px">Lade Verlauf…</div>`;
    try {
      const log = await api.getNotesActivity(client.id);
      box.innerHTML = log.length ? `
        <div style="border:1px solid var(--border);border-radius:8px;padding:6px;max-height:160px;overflow:auto">
          ${log.map((e) => `
            <div style="font-size:11.5px;padding:2px 0;display:flex;gap:6px">
              <span style="color:var(--subtext);flex:none">${new Date(e.created_at).toLocaleString("de-DE")}</span>
              <span style="flex:none"><b>${esc(e.actor_name)}</b></span>
              <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                ${esc(e.label)}${e.details ? " – " + esc(e.details) : ""}</span>
            </div>`).join("")}
        </div>`
        : `<div style="color:var(--subtext);font-size:11.5px">${t("note_no_activity")}</div>`;
    } catch (e) {
      box.innerHTML = `<div style="color:var(--danger);font-size:11.5px">${esc(e.message)}</div>`;
    }
  }

  load();
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

  if (tab === "notes") return renderNotesPart(target, client);

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
    catch (e) { window.notify?.(t("error_prefix", { err: e.message }), "error"); }
    return;
  }
  if (action === "update") {
    if (!(await uiConfirm(t("agent_update_q", { host: client.hostname }), { description: t("agent_update_desc"), okText: t("agent_update_ok") }))) return;
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
    if (!(await uiConfirm(t("agent_uninstall_q", { host: client.hostname }), {
      description: t("agent_uninstall_desc"),
      okText: t("agent_uninstall_ok"), danger: true }))) return;
    window.notify?.(t("agent_uninstall_run", { host: client.hostname }), "info", 60000, { tag: "agent-uninstall:" + client.id });
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


// ==========================================================================
// PATCH-PANEL: verfügbare Software-Updates eines Clients
// --------------------------------------------------------------------------
// Suchen, Liste der Programme mit verfügbarer neuer Version, Installieren.
// Der Scan läuft im Hintergrund (Reverse Proxies brechen lange Anfragen ab),
// deshalb wird der Fortschritt abgefragt statt auf eine Antwort zu warten.
// ==========================================================================

const PATCH_LEVEL_COLORS = {
  security: "#ff4d6d", critical: "#f5a524", important: "#facc15",
  moderate: "#4da6ff", low: "#64748b", feature: "#a78bfa", other: "#7f93ad",
};

export function renderPatchesPart(bodyEl, client) {
  let pollTimer = null;
  let busy = false;

  const stop = () => { clearTimeout(pollTimer); pollTimer = null; };
  // Wird das Panel neu aufgebaut, darf kein Timer weiterlaufen.
  bodyEl.addEventListener("DOMNodeRemovedFromDocument", stop);

  async function load() {
    let data;
    try {
      data = await api.getClientPatches(client.id);
    } catch (e) {
      bodyEl.innerHTML = `<div style="color:var(--danger);font-size:12px;padding:6px">${esc(e.message)}</div>`;
      return;
    }
    const patches = data.patches || [];
    const online = data.client?.online;
    const sec = patches.filter((p) => p.level === "security").length;

    bodyEl.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%;gap:6px">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span style="font-size:18px;font-weight:600">${patches.length}</span>
          <span style="font-size:11px;color:var(--subtext)">${t("patch_open")}</span>
          ${sec ? `<span style="font-size:10px;padding:1px 6px;border-radius:99px;
            background:#ff4d6d22;color:#ff4d6d;border:1px solid #ff4d6d55">
            ${sec} ${t("patch_col_security")}</span>` : ""}
          <span style="flex:1"></span>
          <button class="taskbar-btn" data-scan style="padding:1px 7px;font-size:11px"
            ${online ? "" : "disabled"} title="${esc(t("patch_scan_btn"))}">🔍</button>
          <button class="taskbar-btn" data-all style="padding:1px 7px;font-size:11px"
            ${online && patches.length ? "" : "disabled"}>${t("patch_install_all")}</button>
        </div>
        <div data-status style="font-size:10px;color:var(--subtext);min-height:12px"></div>
        <div data-list style="flex:1;overflow:auto;display:flex;flex-direction:column;gap:3px">
          ${patches.length ? patches.map((p) => `
            <div style="display:flex;align-items:center;gap:6px;padding:3px 5px;
                 border:1px solid var(--border);border-radius:6px;background:var(--panel-2)">
              <span style="width:6px;height:6px;border-radius:99px;flex:none;
                background:${PATCH_LEVEL_COLORS[p.level] || PATCH_LEVEL_COLORS.other}"></span>
              <div style="flex:1;min-width:0">
                <div style="font-size:11px;overflow:hidden;text-overflow:ellipsis;
                     white-space:nowrap" title="${esc(p.name)}">${esc(p.name)}</div>
                <div style="font-size:9px;color:var(--subtext)">
                  ${esc(p.current_version || "—")} → ${esc(p.available_version || "?")}</div>
              </div>
              <button class="taskbar-btn" data-one="${esc(p.id)}" data-uid="${esc(p.uid)}"
                data-src="${esc(p.source)}" data-name="${esc(p.name)}"
                style="padding:0 5px;font-size:10px;flex:none"
                ${online ? "" : "disabled"}>⬆</button>
            </div>`).join("")
            : `<div style="font-size:11px;color:var(--subtext);padding:4px">${
                 data.client?.patch_last_scan ? t("patch_none_open") : t("patch_never_scanned")}</div>`}
        </div>
      </div>`;

    const statusEl = bodyEl.querySelector("[data-status]");
    const setStatus = (txt) => { if (statusEl) statusEl.textContent = txt || ""; };

    bodyEl.querySelector("[data-scan]")?.addEventListener("click", async () => {
      if (busy) return;
      busy = true;
      setStatus(t("patch_scanning"));
      try {
        const r = await api.scanPatches(client.id);
        if (r.hint) window.notify?.(r.hint, "warn", 8000);
      } catch (e) {
        busy = false; setStatus(""); return window.notify?.(e.message, "error");
      }
      poll();
    });

    const install = async (items) => {
      if (busy || !items.length) return;
      busy = true;
      setStatus(t("patch_installing", { what: items.length }));
      try {
        const r = await api.applyPatches(client.id, items);
        window.notify?.(t("patch_result", {
          ok: (r.installed || []).length, failed: (r.failed || []).length }),
          (r.failed || []).length ? "warn" : "success");
      } catch (e) {
        window.notify?.(e.message, "error");
      }
      busy = false; setStatus(""); load();
    };

    bodyEl.querySelector("[data-all]")?.addEventListener("click", () =>
      install(patches.map((p) => ({ uid: p.uid, source: p.source, name: p.name }))));
    bodyEl.querySelectorAll("[data-one]").forEach((b) =>
      b.addEventListener("click", () => install([{
        uid: b.dataset.uid, source: b.dataset.src, name: b.dataset.name }])));

    function poll() {
      const started = Date.now();
      const tick = async () => {
        let job = null;
        try { job = await api.getPatchJob(client.id); } catch {}
        // Nur abbrechen, wenn das Backend den Auftrag KENNT. Eine leere
        // Antwort heisst "noch nicht eingetragen", nicht "schon fertig".
        if (job && job.known && !job.running) {
          busy = false; setStatus("");
          if (job.error) window.notify?.(job.error, "error", 9000);
          return load();
        }
        if (job && job.running) {
          setStatus(job.detail ? `${job.phase} - ${job.detail}` : (job.phase || ""));
        }
        if (Date.now() - started > 1800000) { busy = false; setStatus(""); return load(); }
        pollTimer = setTimeout(tick, 2500);
      };
      pollTimer = setTimeout(tick, 600);
    }

    // War beim Öffnen schon ein Auftrag unterwegs, direkt mitverfolgen.
    api.getPatchJob(client.id).then((job) => {
      if (job && job.running) { busy = true; setStatus(t("patch_scanning")); poll(); }
    }).catch(() => {});
  }

  load();
}

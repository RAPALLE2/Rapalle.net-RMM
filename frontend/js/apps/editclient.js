// apps/editclient.js
// ------------------
// Inhalt des "Client bearbeiten"-Fensters (öffnet sich über den Bearbeiten-
// Button im Status-Panel). Erlaubt: Name, Tenant, Location, Ordner, Farbe,
// Wartungsmodus, Aktiv/Deaktiviert - sowie das Löschen des Clients.

import { state, findClient } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";
import { closeWindow } from "../windowmanager.js";
import { favStarHtml } from "../sidebar.js";

// Wird von app.js gesetzt, um nach Änderungen alles neu zu laden
let onChanged = null;
export function setEditOnChanged(fn) { onChanged = fn; }

export function renderEditClient(body, win) {
  const client = findClient(win.props.clientId);
  if (!client) {
    body.innerHTML = `<div style="padding:20px;color:var(--danger)">Client nicht gefunden.</div>`;
    return;
  }

  // Auswahlmöglichkeiten für Tenant/Location/Ordner aus der Hierarchie
  const tenantOptions = state.hierarchy.tenants
    .map((t) => `<option value="${t.id}" ${client.tenant_id === t.id ? "selected" : ""}>${esc(t.name)}</option>`)
    .join("");

  // Locations werden nach gewähltem Tenant gefiltert (hier initial alle des aktuellen Tenants)
  function locationOptionsFor(tenantId) {
    return state.hierarchy.locations
      .filter((l) => l.tenant_id === tenantId)
      .map((l) => `<option value="${l.id}" ${client.location_id === l.id ? "selected" : ""}>${esc(l.name)}</option>`)
      .join("");
  }

  // Ordner einer Location als (verschachtelte) Optionsliste. Unterordner werden
  // mit Einrückung dargestellt. selectedId wird vorausgewählt.
  function folderOptionsFor(locationId, selectedId) {
    if (!locationId) return "";
    const all = state.hierarchy.folders.filter((f) => f.location_id === locationId);
    const out = [];
    const walk = (parentId, depth) => {
      all.filter((f) => (f.parent_folder_id || null) === parentId).forEach((f) => {
        const indent = depth > 0 ? "\u00A0\u00A0".repeat(depth) + "↳ " : "";
        out.push(`<option value="${f.id}" ${selectedId === f.id ? "selected" : ""}>${indent}${esc(f.name)}</option>`);
        walk(f.id, depth + 1);
      });
    };
    walk(null, 0);
    return out.join("");
  }

  body.innerHTML = `
    <div class="settings-section">
      <div class="form-row">
        <label>Anzeigename</label>
        <input type="text" id="ec-name" value="${esc(client.hostname)}" />
      </div>

      <div class="form-row">
        <label>Tenant</label>
        <select id="ec-tenant"><option value="">— nicht zugeordnet —</option>${tenantOptions}</select>
      </div>

      <div class="form-row">
        <label>Standort</label>
        <select id="ec-location"><option value="">— keiner —</option>${locationOptionsFor(client.tenant_id)}</select>
      </div>

      <div class="form-row">
        <label>Ordner</label>
        <select id="ec-folder"><option value="">— keiner —</option>${folderOptionsFor(client.location_id, client.folder_id)}</select>
      </div>

      <div class="form-row">
        <label>Farbe (Identität in Taskleiste/Sidebar)</label>
        <input type="color" id="ec-color" value="${esc(client.color || "#38bdf8")}" style="height:38px" />
      </div>

      <div class="form-row">
        <label>Status</label>
        <select id="ec-status">
          <option value="" ${!client.status_override ? "selected" : ""}>Automatisch (online/offline)</option>
          <option value="maintenance" ${client.status_override === "maintenance" ? "selected" : ""}>Wartung</option>
        </select>
      </div>

      <div class="form-row">
        <label><input type="checkbox" id="ec-active" ${client.active ? "checked" : ""} /> Aktiv (deaktivierte Clients bleiben sichtbar, werden aber nicht überwacht)</label>
      </div>

      <div class="form-row">
        <label>Gerätetyp</label>
        <select id="ec-devtype">
          <option value="physical" ${(client.device_type || "physical") === "physical" ? "selected" : ""}>💻 Physisches Gerät</option>
          <option value="vm" ${client.device_type === "vm" ? "selected" : ""}>🖥️ Virtuelle Maschine (VM)</option>
          <option value="lxc" ${client.device_type === "lxc" ? "selected" : ""}>📦 LXC-Container</option>
        </select>
      </div>

      <div class="form-row" id="ec-host-row" style="${client.device_type === "vm" || client.device_type === "lxc" ? "" : "display:none"}">
        <label>Host (optional) — auf welchem physischen Gerät läuft diese VM/CT?</label>
        <select id="ec-host">
          <option value="">— kein Host / unbekannt —</option>
          ${state.clients
            .filter((c) => c.id !== client.id && (c.device_type || "physical") === "physical")
            .map((c) => `<option value="${c.id}" ${client.parent_client_id === c.id ? "selected" : ""}>${esc(c.hostname)}</option>`)
            .join("")}
        </select>
      </div>

      <h3 style="margin-top:22px">🔗 Verknüpfte Websites (Quick Access)</h3>
      <p style="color:var(--subtext);font-size:12px;margin:4px 0 10px">
        Binde Websites an diesen Client (z.B. Web-Interfaces, Portale). Favoriten (★)
        werden zusätzlich im Dashboard angeheftet. Optional prüft der Uptime-Monitor
        die URL regelmäßig und benachrichtigt per Webhook und In-App-Notification.
      </p>
      <div id="ec-websites-list" style="margin-bottom:10px"></div>

      <div class="panel" style="padding:10px">
        <div class="form-row">
          <label>Name</label>
          <input type="text" id="ec-ws-name" placeholder="z.B. Proxmox Web-UI" />
        </div>
        <div class="form-row">
          <label>URL</label>
          <input type="text" id="ec-ws-url" placeholder="https://..." />
        </div>
        <div class="form-row">
          <label style="color:var(--subtext);font-size:12px">Favoriten setzt du nach dem Anlegen über den Stern in der Liste (☆ → Seitenleiste → Dashboard → beide).</label>
        </div>
        <div class="form-row">
          <label><input type="checkbox" id="ec-ws-monitor" /> Uptime-Monitoring aktivieren</label>
        </div>
        <div id="ec-ws-monitor-opts" style="display:none">
          <div class="form-row">
            <label>Benachrichtigen…</label>
            <select id="ec-ws-notify">
              <option value="down" selected>…wenn der Scan fehlgeschlagen ist (Website DOWN)</option>
              <option value="up">…wenn der Scan erfolgreich war (Website UP)</option>
              <option value="always">…immer (nach jedem Scan)</option>
            </select>
          </div>
          <div class="form-row">
            <label>Delay zwischen den Scans</label>
            <select id="ec-ws-interval">
              <option value="30">30 Sekunden</option>
              <option value="60">1 Minute</option>
              <option value="300" selected>5 Minuten</option>
              <option value="900">15 Minuten</option>
              <option value="1800">30 Minuten</option>
              <option value="3600">1 Stunde</option>
            </select>
          </div>
          <p style="color:var(--subtext);font-size:11px;margin:2px 0 6px">
            Hinweis: Bei „DOWN" / „UP" wird nur beim Statuswechsel benachrichtigt (kein Spam).
            „Immer" sendet nach jedem einzelnen Scan.
          </p>
        </div>
        <div id="ec-ws-error" class="form-error hidden"></div>
        <button class="btn-primary" id="ec-ws-add" style="margin-top:4px">+ Website hinzufügen</button>
      </div>

      <div id="ec-error" class="form-error hidden"></div>

      <div style="display:flex;gap:8px;margin-top:16px">
        <button class="btn-primary" id="ec-save" style="margin:0">Speichern</button>
        <button class="action-btn" id="ec-delete" style="border-color:var(--danger);color:var(--danger)">Löschen</button>
      </div>
    </div>
  `;

  // -----------------------------------------------------------------
  // Verknüpfte Websites: Liste laden/rendern, hinzufügen, ändern, löschen
  // -----------------------------------------------------------------
  const NOTIFY_LABELS = { down: "bei DOWN", up: "bei UP", always: "immer" };

  function intervalLabel(sec) {
    if (sec >= 3600) return `${Math.round(sec / 3600)} h`;
    if (sec >= 60) return `${Math.round(sec / 60)} min`;
    return `${sec} s`;
  }

  async function loadWebsites() {
    const listEl = body.querySelector("#ec-websites-list");
    if (!listEl) return;
    try {
      const sites = await api.getClientWebsites(client.id);
      if (!sites.length) {
        listEl.innerHTML = `<div style="color:var(--subtext);font-size:12px">Noch keine Websites verknüpft.</div>`;
        return;
      }
      listEl.innerHTML = sites.map((w) => {
        const dot = !w.monitor_enabled ? "" :
          w.last_status === "up"
            ? `<span title="Erreichbar" style="color:var(--online,#3ecf8e)">●</span>`
            : w.last_status === "down"
              ? `<span title="Nicht erreichbar${w.last_error ? ": " + esc(w.last_error) : ""}" style="color:var(--danger,#ff4d6d)">●</span>`
              : `<span title="Noch nicht geprüft" style="color:var(--subtext)">●</span>`;
        const monitorInfo = w.monitor_enabled
          ? `<span style="font-size:11px;color:var(--subtext)">Monitoring: alle ${intervalLabel(w.monitor_interval_seconds)}, ${NOTIFY_LABELS[w.monitor_notify] || w.monitor_notify}</span>`
          : `<span style="font-size:11px;color:var(--subtext)">kein Monitoring</span>`;
        return `
        <div class="panel" style="margin-bottom:6px;padding:8px 10px;display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div style="min-width:0">
            <div style="display:flex;align-items:center;gap:6px">
              ${dot}
              <a href="${esc(w.url)}" target="_blank" rel="noopener noreferrer" style="font-weight:600">${esc(w.name)}</a>
            </div>
            <div style="font-size:11px;color:var(--subtext);font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(w.url)}</div>
            ${monitorInfo}
          </div>
          <div style="display:flex;gap:6px;flex-shrink:0;align-items:center">
            ${favStarHtml("websites", w.id, { name: w.name, url: w.url, clientId: client.id, clientHostname: client.hostname })}
            <button class="taskbar-btn" data-ws-mon="${w.id}" title="Monitoring ${w.monitor_enabled ? "ausschalten" : "einschalten"}">${w.monitor_enabled ? "📡 an" : "📡 aus"}</button>
            <button class="taskbar-btn" data-ws-del="${w.id}">🗑</button>
          </div>
        </div>`;
      }).join("");

      listEl.querySelectorAll("[data-ws-mon]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          try {
            const s = sites.find((x) => x.id === btn.dataset.wsMon);
            await api.updateClientWebsite(client.id, s.id, { monitor_enabled: !s.monitor_enabled });
          } catch (e) {
            window.notify?.("Ändern fehlgeschlagen: " + e.message, "error");
          }
          loadWebsites();
        })
      );
      listEl.querySelectorAll("[data-ws-del]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!confirm("Website-Verknüpfung löschen?")) return;
          try {
            await api.deleteClientWebsite(client.id, btn.dataset.wsDel);
          } catch (e) {
            window.notify?.("Löschen fehlgeschlagen: " + e.message, "error");
          }
          loadWebsites();
        })
      );
    } catch (e) {
      listEl.innerHTML = `<div style="color:var(--danger);font-size:12px">${esc(e.message)}</div>`;
    }
  }

  // Monitoring-Optionen nur zeigen, wenn Monitoring aktiviert ist
  const wsMonitorCheck = body.querySelector("#ec-ws-monitor");
  wsMonitorCheck.addEventListener("change", () => {
    body.querySelector("#ec-ws-monitor-opts").style.display = wsMonitorCheck.checked ? "" : "none";
  });

  body.querySelector("#ec-ws-add").addEventListener("click", async () => {
    const err = body.querySelector("#ec-ws-error");
    err.classList.add("hidden");
    const name = body.querySelector("#ec-ws-name").value.trim();
    const url = body.querySelector("#ec-ws-url").value.trim();
    if (!name || !url) {
      err.textContent = "Name und URL erforderlich"; err.classList.remove("hidden"); return;
    }
    try {
      await api.createClientWebsite(client.id, {
        name, url,
        monitor_enabled: wsMonitorCheck.checked,
        monitor_notify: body.querySelector("#ec-ws-notify").value,
        monitor_interval_seconds: parseInt(body.querySelector("#ec-ws-interval").value, 10),
      });
      body.querySelector("#ec-ws-name").value = "";
      body.querySelector("#ec-ws-url").value = "";
      window.notify?.("Website verknüpft", "success");
      loadWebsites();
    } catch (e) {
      err.textContent = e.message; err.classList.remove("hidden");
    }
  });

  loadWebsites();

  const tenantSel = body.querySelector("#ec-tenant");
  const locationSel = body.querySelector("#ec-location");
  const folderSel = body.querySelector("#ec-folder");
  const devTypeSel = body.querySelector("#ec-devtype");
  const hostRow = body.querySelector("#ec-host-row");

  // Ordner-Auswahl passend zur aktuell gewählten Location neu aufbauen.
  function refreshFolders() {
    folderSel.innerHTML = `<option value="">— keiner —</option>` +
      folderOptionsFor(locationSel.value, null);
  }

  // Host-Auswahl nur zeigen, wenn VM oder LXC gewählt ist
  devTypeSel.addEventListener("change", () => {
    const isVirtual = devTypeSel.value === "vm" || devTypeSel.value === "lxc";
    hostRow.style.display = isVirtual ? "" : "none";
  });

  // Wenn der Tenant gewechselt wird, die Standort-Auswahl passend neu befüllen
  // (und danach die Ordner-Auswahl zurücksetzen, da sie an der Location hängt).
  tenantSel.addEventListener("change", () => {
    locationSel.innerHTML = `<option value="">— keiner —</option>` +
      state.hierarchy.locations
        .filter((l) => l.tenant_id === tenantSel.value)
        .map((l) => `<option value="${l.id}">${esc(l.name)}</option>`)
        .join("");
    refreshFolders();
  });

  // Standortwechsel -> Ordner-Auswahl neu aufbauen.
  locationSel.addEventListener("change", refreshFolders);

  body.querySelector("#ec-save").addEventListener("click", async () => {
    try {
      await api.updateClient(client.id, {
        hostname: body.querySelector("#ec-name").value,
        tenant_id: tenantSel.value || null,
        location_id: locationSel.value || null,
        folder_id: folderSel.value || null,
        color: body.querySelector("#ec-color").value,
        status_override: body.querySelector("#ec-status").value || null,
        active: body.querySelector("#ec-active").checked,
        device_type: devTypeSel.value,
        // Host nur setzen, wenn VM/LXC — sonst kein Parent
        parent_client_id: (devTypeSel.value === "vm" || devTypeSel.value === "lxc")
          ? (body.querySelector("#ec-host").value || null)
          : null,
      });
      closeWindow(win.key);
      if (onChanged) await onChanged();
    } catch (e) {
      const err = body.querySelector("#ec-error");
      err.textContent = e.message;
      err.classList.remove("hidden");
    }
  });

  body.querySelector("#ec-delete").addEventListener("click", async () => {
    if (!confirm(`Client "${client.hostname}" wirklich löschen?`)) return;
    await api.deleteClient(client.id);
    state.selection = null;
    closeWindow(win.key);
    if (onChanged) await onChanged();
  });
}

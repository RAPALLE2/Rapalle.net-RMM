// apps/editclient.js
// ------------------
// Inhalt des "Client bearbeiten"-Fensters (öffnet sich über den Bearbeiten-
// Button im Status-Panel). Erlaubt: Name, Tenant, Location, Ordner, Farbe,
// Wartungsmodus, Aktiv/Deaktiviert - sowie das Löschen des Clients.

import { state, findClient } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";
import { closeWindow } from "../windowmanager.js";

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

      <div id="ec-error" class="form-error hidden"></div>

      <div style="display:flex;gap:8px;margin-top:16px">
        <button class="btn-primary" id="ec-save" style="margin:0">Speichern</button>
        <button class="action-btn" id="ec-delete" style="border-color:var(--danger);color:var(--danger)">Löschen</button>
      </div>
    </div>
  `;

  const tenantSel = body.querySelector("#ec-tenant");
  const locationSel = body.querySelector("#ec-location");
  const devTypeSel = body.querySelector("#ec-devtype");
  const hostRow = body.querySelector("#ec-host-row");

  // Host-Auswahl nur zeigen, wenn VM oder LXC gewählt ist
  devTypeSel.addEventListener("change", () => {
    const isVirtual = devTypeSel.value === "vm" || devTypeSel.value === "lxc";
    hostRow.style.display = isVirtual ? "" : "none";
  });

  // Wenn der Tenant gewechselt wird, die Standort-Auswahl passend neu befüllen
  tenantSel.addEventListener("change", () => {
    locationSel.innerHTML = `<option value="">— keiner —</option>` +
      state.hierarchy.locations
        .filter((l) => l.tenant_id === tenantSel.value)
        .map((l) => `<option value="${l.id}">${esc(l.name)}</option>`)
        .join("");
  });

  body.querySelector("#ec-save").addEventListener("click", async () => {
    try {
      await api.updateClient(client.id, {
        hostname: body.querySelector("#ec-name").value,
        tenant_id: tenantSel.value || null,
        location_id: locationSel.value || null,
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

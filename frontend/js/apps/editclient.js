// apps/editclient.js
// ------------------
// Inhalt des "Client bearbeiten"-Fensters (öffnet sich über den Bearbeiten-
// Button im Status-Panel). Erlaubt: Name, Tenant, Location, Ordner, Farbe,
// Wartungsmodus, Aktiv/Deaktiviert - sowie das Löschen des Clients.

import { state, findClient } from "../state.js";
import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";
import { closeWindow, openWindow } from "../windowmanager.js";

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

      <div class="form-row">
        <label>Garantie läuft bis (optional)</label>
        <input type="date" id="ec-warranty"
               value="${client.warranty_until ? new Date(client.warranty_until).toISOString().slice(0, 10) : ""}" />
        <div style="font-size:11.5px;color:var(--subtext);margin-top:3px">
          Grundlage für die Garantie-Benachrichtigungen und die Garantie-Übersicht im Dashboard.
        </div>
      </div>

      <div class="form-row">
        <label>Automatisches Agent-Update</label>
        <select id="ec-autoupdate">
          <option value="global" ${(client.auto_update || "global") === "global" ? "selected" : ""}>Globale Einstellung verwenden</option>
          <option value="on" ${client.auto_update === "on" ? "selected" : ""}>An — veralteter Agent aktualisiert sich selbst</option>
          <option value="off" ${client.auto_update === "off" ? "selected" : ""}>Aus — nie automatisch aktualisieren</option>
        </select>
      </div>

      <div id="ec-error" class="form-error hidden"></div>

      <div style="display:flex;gap:8px;margin-top:16px">
        <button class="btn-primary" id="ec-save" style="margin:0">Speichern</button>
        <button class="action-btn" id="ec-delete" style="border-color:var(--danger);color:var(--danger)">Löschen</button>
      </div>

      <h3 style="margin-top:22px">🔗 Verknüpfte Websites</h3>
      <p style="color:var(--subtext);font-size:12px;margin:4px 0 10px">
        Websites werden jetzt komplett im <b>Websites-Widget</b> der Client-Ansicht
        verwaltet – dort legst du sie an, änderst Name/URL, die Öffnungsart
        (interner oder externer Browser) und das Uptime-Monitoring inkl.
        Benachrichtigungs-Modus und Scan-Intervall.
      </p>
      <button class="action-btn" id="ec-ws-goto" style="width:auto">🔗 Websites-Widget öffnen</button>

    </div>
  `;

  // Websites: komplette Verwaltung liegt im Websites-Widget (panel.js).
  // Hier nur noch ein Sprung dorthin - als eigenes Fenster, damit man den
  // Bearbeiten-Dialog nicht verliert.
  body.querySelector("#ec-ws-goto")?.addEventListener("click", () => {
    openWindow({
      singleton: true,
      key: `panelpart-${client.id}-websites-edit`, appId: "panelpart",
      title: `🔗 Websites — ${client.hostname}`,
      props: { clientId: client.id, part: "websites" },
      clientColor: client.color, w: 460, h: 520,
    });
  });

  const tenantSel = body.querySelector("#ec-tenant");
  // Live-Refresh der Tenant-Liste bei Hierarchie-Änderungen (Auswahl bleibt).
  const onHierarchyChanged = () => {
    if (!document.body.contains(body)) {
      window.removeEventListener("rmm:hierarchy-changed", onHierarchyChanged);
      return;
    }
    const cur = tenantSel.value;
    tenantSel.innerHTML = `<option value="">— nicht zugeordnet —</option>` +
      state.hierarchy.tenants.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
    if ([...tenantSel.options].some((o) => o.value === cur)) tenantSel.value = cur;
    tenantSel.dispatchEvent(new Event("change"));
  };
  window.addEventListener("rmm:hierarchy-changed", onHierarchyChanged);
  const locationSel = body.querySelector("#ec-location");
  const folderSel = body.querySelector("#ec-folder");
  const devTypeSel = body.querySelector("#ec-devtype");
  // Gerätetyp IMMER frisch: state.clients kann veraltet sein (z.B. wenn der
  // Agent den Typ per Auto-Erkennung gerade erst gemeldet hat). Deshalb wird
  // der Client beim Öffnen direkt vom Backend geholt und der Typ übernommen -
  // solange der Nutzer die Auswahl noch nicht selbst angefasst hat. Damit
  // steht bei einer VM/LXC nie mehr fälschlich "Physisches Gerät", auch nach
  // Fenster-/Seiten-Reload.
  let devTypeTouched = false;
  devTypeSel.addEventListener("change", () => { devTypeTouched = true; });
  (async () => {
    try {
      const fresh = await api.getClient(client.id);
      if (!fresh || !document.body.contains(body)) return;
      // Frische Felder in die state-Referenz übernehmen (gleiche Objekt-Ref).
      Object.assign(client, fresh);
      if (!devTypeTouched && fresh.device_type && devTypeSel.value !== fresh.device_type) {
        devTypeSel.value = fresh.device_type;
        devTypeSel.dispatchEvent(new Event("change"));   // Host-Zeile ein-/ausblenden
        devTypeTouched = false;                          // programmatisch, nicht vom Nutzer
      }
    } catch { /* offline o.ä. - Auswahl aus state bleibt */ }
  })();
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
        auto_update: body.querySelector("#ec-autoupdate").value,
        // Garantie-Ende: Ende des gewählten Tages (23:59:59), leer = keine Garantie
        warranty_until: body.querySelector("#ec-warranty").value
          ? new Date(body.querySelector("#ec-warranty").value + "T23:59:59").getTime()
          : null,
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
    if (!(await uiConfirm(`Client "${client.hostname}" wirklich löschen?`, { okText: "Client löschen", danger: true }))) return;
    await api.deleteClient(client.id);
    state.selection = null;
    closeWindow(win.key);
    if (onChanged) await onChanged();
  });
}

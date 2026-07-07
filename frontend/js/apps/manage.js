// apps/manage.js
// --------------
// Verwaltungs-Fenster für die Hierarchie: Tenants (Kunden/Firmen) anlegen,
// und innerhalb eines Tenants Standorte (Locations) anlegen. Optional lassen
// sich auch Ordner innerhalb einer Location anlegen.
//
// Nach jeder Änderung wird die Sidebar über den onChanged-Callback (aus app.js)
// neu geladen, damit die neuen Einträge sofort sichtbar sind.

import { state } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";

// Wird von app.js gesetzt, um nach Änderungen Hierarchie + Sidebar neu zu laden
let onChanged = null;
export function setManageOnChanged(fn) { onChanged = fn; }

export function renderManage(body, win) {
  function draw() {
    const tenants = state.hierarchy.tenants;

    body.innerHTML = `
      <div class="settings-section">
        <h3>Tenant anlegen</h3>
        <div class="form-row">
          <label>Name des Tenants (Kunde/Firma)</label>
          <input type="text" id="mg-tenant-name" placeholder="z.B. Muster GmbH" />
        </div>
        <div class="form-row">
          <label>Farbe</label>
          <input type="color" id="mg-tenant-color" value="#2dd4bf" style="height:38px;width:80px" />
        </div>
        <button class="btn-primary" id="mg-add-tenant" style="margin-top:4px">+ Tenant anlegen</button>
        <div id="mg-error" class="form-error hidden"></div>

        <h3 style="margin-top:26px">Bestehende Tenants &amp; Standorte</h3>
        <div id="mg-list"></div>
      </div>
    `;

    // ---- Tenant anlegen ----
    body.querySelector("#mg-add-tenant").addEventListener("click", async () => {
      const name = body.querySelector("#mg-tenant-name").value.trim();
      const color = body.querySelector("#mg-tenant-color").value;
      const err = body.querySelector("#mg-error");
      err.classList.add("hidden");
      if (!name) { err.textContent = "Bitte einen Namen eingeben"; err.classList.remove("hidden"); return; }
      try {
        await api.createTenant(name, color);
        if (onChanged) await onChanged();
        draw(); // Fensterinhalt neu aufbauen mit aktualisierter Liste
      } catch (e) {
        err.textContent = e.message; err.classList.remove("hidden");
      }
    });

    // ---- Liste aller Tenants mit ihren Locations ----
    const listEl = body.querySelector("#mg-list");
    if (!tenants.length) {
      listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">Noch keine Tenants angelegt.</div>`;
    } else {
      listEl.innerHTML = tenants.map((t) => {
        const locations = state.hierarchy.locations.filter((l) => l.tenant_id === t.id);
        const isUncat = t.name === "Uncategorized";
        const locationRows = locations.map((l) => {
          const isDefaultLoc = isUncat && l.name === "Default";
          return `<div style="display:flex;align-items:center;padding:4px 0 4px 20px;color:var(--subtext);font-size:13px">
            <span style="flex:1">📍 ${esc(l.name)}</span>
            ${isDefaultLoc ? "" : `<button class="action-btn" data-del-loc="${l.id}" data-loc-name="${esc(l.name)}" title="Standort löschen (Clients wandern nach Uncategorized/Default)">🗑</button>`}
          </div>`;
        }).join("") || `<div style="padding:4px 0 4px 20px;color:var(--subtext);font-size:12px">— noch keine Standorte —</div>`;

        return `
          <div class="panel" style="margin-bottom:10px">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
              <span class="dot" style="width:10px;height:10px;border-radius:50%;background:${esc(t.color)}"></span>
              <strong style="flex:1">${esc(t.name)}</strong>
              ${isUncat ? `<span style="color:var(--subtext);font-size:11px" title="Auffangbecken für Clients aus gelöschten Tenants/Standorten">geschützt</span>`
                        : `<button class="action-btn" data-del-tenant="${t.id}" data-tenant-name="${esc(t.name)}" title="Tenant löschen (Clients wandern nach Uncategorized/Default)">🗑</button>`}
            </div>
            ${locationRows}
            <div style="display:flex;gap:6px;margin-top:10px">
              <input type="text" placeholder="Neuer Standort..." data-loc-input="${t.id}"
                style="flex:1;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
              <button class="action-btn" data-add-loc="${t.id}">+ Standort</button>
            </div>
          </div>
        `;
      }).join("");

      // ---- Standort anlegen (pro Tenant) ----
      listEl.querySelectorAll("[data-add-loc]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const tenantId = btn.dataset.addLoc;
          const input = listEl.querySelector(`[data-loc-input="${tenantId}"]`);
          const name = input.value.trim();
          if (!name) return;
          try {
            await api.createLocation(tenantId, name);
            if (onChanged) await onChanged();
            draw();
          } catch (e) {
            alert("Fehler: " + e.message);
          }
        })
      );

      // ---- Tenant löschen (Clients wandern nach Uncategorized/Default) ----
      listEl.querySelectorAll("[data-del-tenant]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const name = btn.dataset.tenantName;
          if (!confirm(`Tenant "${name}" wirklich löschen?\n\nAlle Standorte und Ordner darin werden entfernt.\nAlle Clients werden nach "Uncategorized / Default" verschoben (kein Client geht verloren).`)) return;
          try {
            const res = await api.deleteTenant(btn.dataset.delTenant);
            window.notify?.(`Tenant gelöscht — ${res.moved_clients} Client(s) nach Uncategorized/Default verschoben`, "success");
            if (onChanged) await onChanged();
            draw();
          } catch (e) {
            alert("Fehler: " + e.message);
          }
        })
      );

      // ---- Standort löschen (Clients wandern nach Uncategorized/Default) ----
      listEl.querySelectorAll("[data-del-loc]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const name = btn.dataset.locName;
          if (!confirm(`Standort "${name}" wirklich löschen?\n\nAlle Ordner darin werden entfernt.\nAlle Clients werden nach "Uncategorized / Default" verschoben (kein Client geht verloren).`)) return;
          try {
            const res = await api.deleteLocation(btn.dataset.delLoc);
            window.notify?.(`Standort gelöscht — ${res.moved_clients} Client(s) nach Uncategorized/Default verschoben`, "success");
            if (onChanged) await onChanged();
            draw();
          } catch (e) {
            alert("Fehler: " + e.message);
          }
        })
      );
    }
  }

  draw();
}

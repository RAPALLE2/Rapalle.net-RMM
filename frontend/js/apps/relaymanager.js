// apps/relaymanager.js
// --------------------
// Explorer-Relay-Verwaltung mit zwei Spalten:
//   links  = Clients mit INAKTIVEM Relay
//   rechts = Clients mit AKTIVEM Relay
// In der Mitte ein „Umschalten"-Button: ausgewählte Clients wechseln die Seite
// (inaktiv -> aktiv bzw. aktiv -> inaktiv).

import { api } from "../api.js";
import { esc } from "../utils.js";
import { isAdmin as userIsAdmin, state } from "../state.js";

export function renderRelayManager(body, win) {
  let clients = [];
  const selected = new Set();   // ausgewählte Client-IDs
  const isAdmin = (() => { try { return userIsAdmin(); } catch { return false; } })();

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div style="padding:12px 14px;border-bottom:1px solid var(--border)">
        <div style="font-size:15px;font-weight:700">🔌 Explorer-Relay verwalten</div>
        <div style="color:var(--subtext);font-size:12px;margin-top:2px">
          Client auswählen und in die Mitte auf „Umschalten" – so wird der Relay für
          ihn ein- bzw. ausgeschaltet.
          ${isAdmin ? "" : '<b style="color:var(--warn,#f5a524)"> Nur Administratoren können umschalten.</b>'}
        </div>
      </div>

      <div style="flex:1;display:grid;grid-template-columns:1fr auto 1fr;gap:0;min-height:0">
        <!-- Links: inaktiv -->
        <div style="display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--border)">
          <div style="padding:8px 12px;font-weight:600;color:var(--danger,#ff4d6d)">
            ● Inaktiv <span id="rm-count-off" style="color:var(--subtext);font-weight:400"></span>
          </div>
          <div id="rm-list-off" style="flex:1;overflow:auto;padding:6px"></div>
        </div>

        <!-- Mitte: Umschalten -->
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:0 14px">
          <button id="rm-switch" class="btn-primary" style="margin:0;white-space:nowrap"
                  ${isAdmin ? "" : "disabled"}>⇄ Umschalten</button>
          <div id="rm-selinfo" style="color:var(--subtext);font-size:12px;text-align:center"></div>
        </div>

        <!-- Rechts: aktiv -->
        <div style="display:flex;flex-direction:column;min-height:0;border-left:1px solid var(--border)">
          <div style="padding:8px 12px;font-weight:600;color:var(--online,#3ecf8e)">
            ● Aktiv <span id="rm-count-on" style="color:var(--subtext);font-weight:400"></span>
          </div>
          <div id="rm-list-on" style="flex:1;overflow:auto;padding:6px"></div>
        </div>
      </div>
    </div>
  `;

  const listOff = body.querySelector("#rm-list-off");
  const listOn = body.querySelector("#rm-list-on");
  const countOff = body.querySelector("#rm-count-off");
  const countOn = body.querySelector("#rm-count-on");
  const selInfo = body.querySelector("#rm-selinfo");
  const switchBtn = body.querySelector("#rm-switch");

  // Tenant/Standort-Namen für den Untertitel auflösen (falls vorhanden).
  function subtitle(c) {
    const parts = [];
    const tenants = state.hierarchy?.tenants || [];
    const locations = state.hierarchy?.locations || [];
    const t = tenants.find((x) => x.id === c.tenant_id);
    const l = locations.find((x) => x.id === c.location_id);
    if (t) parts.push(t.name);
    if (l) parts.push(l.name);
    if (c.ip) parts.push(c.ip);
    return parts.join(" · ");
  }

  function clientRow(c) {
    const isSel = selected.has(c.id);
    const dot = c.online ? "var(--online,#3ecf8e)" : "var(--subtext)";
    const sub = subtitle(c);
    return `
      <div class="rm-item" data-id="${esc(c.id)}"
           style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;cursor:pointer;margin-bottom:4px;
                  ${isSel ? "background:var(--accent,#38bdf8);color:#04222e" : "background:var(--panel-2,#0f1626)"}">
        <span style="color:${isSel ? "#04222e" : dot}">●</span>
        <div style="min-width:0;flex:1">
          <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.hostname || c.id)}</div>
          ${sub ? `<div style="font-size:11px;opacity:.8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(sub)}</div>` : ""}
        </div>
        ${isSel ? "<span>✓</span>" : ""}
      </div>`;
  }

  function draw() {
    const off = clients.filter((c) => !c.relay_enabled)
      .sort((a, b) => (a.hostname || "").localeCompare(b.hostname || ""));
    const on = clients.filter((c) => c.relay_enabled)
      .sort((a, b) => (a.hostname || "").localeCompare(b.hostname || ""));

    listOff.innerHTML = off.map(clientRow).join("") ||
      `<div style="color:var(--subtext);padding:10px;font-size:13px">Keine inaktiven Clients.</div>`;
    listOn.innerHTML = on.map(clientRow).join("") ||
      `<div style="color:var(--subtext);padding:10px;font-size:13px">Keine aktiven Clients.</div>`;
    countOff.textContent = `(${off.length})`;
    countOn.textContent = `(${on.length})`;

    body.querySelectorAll(".rm-item").forEach((el) =>
      el.addEventListener("click", () => {
        const id = el.dataset.id;
        if (selected.has(id)) selected.delete(id); else selected.add(id);
        draw();
      }));

    const n = selected.size;
    selInfo.textContent = n ? `${n} ausgewählt` : "nichts ausgewählt";
    if (isAdmin) switchBtn.disabled = n === 0;
  }

  async function load() {
    listOff.innerHTML = listOn.innerHTML = `<div style="color:var(--subtext);padding:10px">Lädt…</div>`;
    try {
      clients = await api.getClients();
      // Auswahl auf noch existierende Clients begrenzen
      for (const id of [...selected]) if (!clients.find((c) => c.id === id)) selected.delete(id);
      draw();
    } catch (e) {
      listOff.innerHTML = `<div style="color:var(--danger);padding:10px">${esc(e.message)}</div>`;
    }
  }

  switchBtn.addEventListener("click", async () => {
    if (!isAdmin || selected.size === 0) return;
    switchBtn.disabled = true;
    const ids = [...selected];
    let ok = 0, fail = 0;
    for (const id of ids) {
      try { await api.toggleRelay(id); ok++; }
      catch { fail++; }
    }
    selected.clear();
    window.notify?.(`Umgeschaltet: ${ok}${fail ? `, Fehler: ${fail}` : ""}`, fail ? "warn" : "success");
    // andere offene Fenster (Client-Explorer-Relay-Tab) mitziehen
    window.dispatchEvent(new CustomEvent("relay-changed", { detail: { bulk: true } }));
    await load();
    try { state.clients = clients; } catch {}
  });

  // Externe Änderungen (Umschalten im Client-Explorer-Relay-Tab) übernehmen.
  function onRelayChanged() {
    if (!document.body.contains(body)) {
      window.removeEventListener("relay-changed", onRelayChanged);
      return;
    }
    load();
  }
  window.addEventListener("relay-changed", onRelayChanged);

  load();
}

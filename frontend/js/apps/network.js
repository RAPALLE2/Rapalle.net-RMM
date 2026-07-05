// apps/network.js
// ---------------
// Netzwerk-Scanner: durchsucht ein Subnetz nach erreichbaren Geräten und
// bietet pro Gerät einen Portscan an. Das Subnetz kann manuell eingegeben
// werden (z.B. 192.168.5.) oder leer bleiben (dann eigenes Subnetz).

import { api } from "../api.js";
import { esc } from "../utils.js";
import { openWindow } from "../windowmanager.js";

export function renderNetwork(body, win) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar" style="flex-wrap:wrap;gap:8px">
        <input type="text" id="net-subnet-${win.key}" placeholder="Subnetz z.B. 192.168.1. (leer = eigenes)"
          style="flex:1;min-width:180px;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
        <button class="btn-primary" id="net-scan-${win.key}" style="width:auto;margin:0">🔍 Scannen</button>
      </div>
      <div id="net-status-${win.key}" style="padding:6px 10px;font-size:12px;color:var(--subtext)"></div>
      <div style="flex:1;overflow:auto">
        <table class="data-table">
          <thead>
            <tr><th>IP</th><th>Hostname</th><th>MAC</th><th style="width:130px">Ports</th></tr>
          </thead>
          <tbody id="net-body-${win.key}"><tr><td colspan="4" style="color:var(--subtext)">Noch nicht gescannt.</td></tr></tbody>
        </table>
      </div>
    </div>
  `;

  const subnetInput = body.querySelector(`#net-subnet-${win.key}`);
  const scanBtn = body.querySelector(`#net-scan-${win.key}`);
  const statusEl = body.querySelector(`#net-status-${win.key}`);
  const tbody = body.querySelector(`#net-body-${win.key}`);

  async function scan() {
    const subnet = subnetInput.value.trim() || null;
    statusEl.textContent = "Scanne Netzwerk (kann ~15 Sekunden dauern)...";
    tbody.innerHTML = `<tr><td colspan="4" style="color:var(--subtext)">Läuft...</td></tr>`;
    scanBtn.disabled = true;
    try {
      const res = await api.scanNetwork(subnet);
      renderDevices(res.devices);
      statusEl.textContent = `${res.devices.length} Geräte gefunden${res.subnet ? " in " + res.subnet + "x" : ""}.`;
    } catch (e) {
      statusEl.textContent = "";
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    } finally {
      scanBtn.disabled = false;
    }
  }

  function renderDevices(devices) {
    if (!devices.length) {
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--subtext)">Keine Geräte gefunden.</td></tr>`;
      return;
    }
    tbody.innerHTML = devices.map((d) => `
      <tr>
        <td>${esc(d.ip)}</td>
        <td>${esc(d.hostname || "–")}</td>
        <td style="color:var(--subtext);font-family:monospace;font-size:11px">${esc(d.mac || "–")}</td>
        <td><button class="taskbar-btn" data-portscan="${esc(d.ip)}">Portscan</button></td>
      </tr>
    `).join("");

    // Portscan-Button öffnet das Portscan-Programm mit schon eingetragener IP.
    tbody.querySelectorAll("[data-portscan]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const ip = btn.dataset.portscan;
        openWindow({
          key: `portscan-${ip}`, appId: "portscan",
          title: `Portscan — ${ip}`, props: { ip }, w: 560, h: 480,
        });
      })
    );
  }

  scanBtn.addEventListener("click", scan);
  subnetInput.addEventListener("keydown", (e) => { if (e.key === "Enter") scan(); });

  // Beim Öffnen letztes Ergebnis laden (falls vorhanden)
  api.lastScan().then((res) => {
    if (res.devices && res.devices.length) {
      renderDevices(res.devices);
      statusEl.textContent = `${res.devices.length} Geräte (letzter Scan).`;
    }
  }).catch(() => {});
}

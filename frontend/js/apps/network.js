// apps/network.js
// ---------------
// Netzwerk-Scanner: durchsucht ein Subnetz nach erreichbaren Geräten und
// bietet pro Gerät einen Portscan an. Das Subnetz kann manuell eingegeben
// werden (z.B. 192.168.5.) oder leer bleiben (dann eigenes Subnetz).

import { api } from "../api.js";
import { esc } from "../utils.js";

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
      <tr data-portrow="${esc(d.ip)}" style="display:none"><td colspan="4" style="background:var(--panel-2)"></td></tr>
    `).join("");

    tbody.querySelectorAll("[data-portscan]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const ip = btn.dataset.portscan;
        const row = tbody.querySelector(`[data-portrow="${ip}"]`);
        const cell = row.querySelector("td");
        row.style.display = "";
        cell.innerHTML = `<span style="color:var(--subtext)">Scanne Ports auf ${esc(ip)}...</span>`;
        btn.disabled = true;
        try {
          const res = await api.portScan(ip);
          if (!res.ports.length) {
            cell.innerHTML = `<span style="color:var(--subtext)">Keine offenen Ports (der gängigen Ports) gefunden.</span>`;
          } else {
            cell.innerHTML = `<b>Offene Ports:</b> ` + res.ports.map((p) =>
              `<span style="display:inline-block;background:var(--panel);border:1px solid var(--accent);border-radius:5px;padding:2px 8px;margin:3px;font-size:12px">
                ${p.port} <span style="color:var(--subtext)">${esc(p.service)}</span>
              </span>`).join("");
          }
        } catch (e) {
          cell.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
        } finally {
          btn.disabled = false;
        }
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

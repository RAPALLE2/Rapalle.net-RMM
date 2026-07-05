// apps/portscan.js
// ----------------
// Eigenständiger Portscanner. Kann aus dem Startmenü geöffnet werden (dann gibt
// man die IP selbst ein) oder aus dem Netzwerk-Scanner (dann ist die IP schon
// vorausgefüllt, siehe win.props.ip).
//
// Scan-Modi:
//   - standard: die gängigen Ports (schnell)
//   - all:      alle Ports 1-65535 (dauert länger)
//   - custom:   frei angegebene Ports/Bereiche, z.B. "22,80,8000-8100"

import { api } from "../api.js";
import { esc } from "../utils.js";
import { t } from "../i18n.js";

export function renderPortscan(body, win) {
  const presetIp = win.props?.ip || "";

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar" style="flex-wrap:wrap;gap:8px">
        <input type="text" id="ps-ip-${win.key}" placeholder="${t("ps_ip_placeholder")}" value="${esc(presetIp)}"
          style="flex:1;min-width:150px;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
        <select id="ps-mode-${win.key}" style="padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)">
          <option value="standard">${t("ps_mode_standard")}</option>
          <option value="all">${t("ps_mode_all")}</option>
          <option value="custom">${t("ps_mode_custom")}</option>
        </select>
        <button class="btn-primary" id="ps-scan-${win.key}" style="width:auto;margin:0">🔍 ${t("ps_scan")}</button>
      </div>
      <div id="ps-custom-row-${win.key}" class="explorer-toolbar" style="display:none">
        <input type="text" id="ps-ports-${win.key}" placeholder="${t("ps_custom_placeholder")}"
          style="flex:1;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
      </div>
      <div id="ps-status-${win.key}" style="padding:6px 10px;font-size:12px;color:var(--subtext)"></div>
      <div id="ps-result-${win.key}" style="flex:1;overflow:auto;padding:4px 10px"></div>
    </div>
  `;

  const ipInput = body.querySelector(`#ps-ip-${win.key}`);
  const modeSel = body.querySelector(`#ps-mode-${win.key}`);
  const customRow = body.querySelector(`#ps-custom-row-${win.key}`);
  const portsInput = body.querySelector(`#ps-ports-${win.key}`);
  const scanBtn = body.querySelector(`#ps-scan-${win.key}`);
  const statusEl = body.querySelector(`#ps-status-${win.key}`);
  const resultEl = body.querySelector(`#ps-result-${win.key}`);

  modeSel.addEventListener("change", () => {
    customRow.style.display = modeSel.value === "custom" ? "flex" : "none";
  });

  async function scan() {
    const ip = ipInput.value.trim();
    if (!ip) { statusEl.textContent = t("ps_need_ip"); return; }
    const mode = modeSel.value;
    const ports = portsInput.value.trim();
    if (mode === "custom" && !ports) { statusEl.textContent = t("ps_need_ports"); return; }

    statusEl.textContent = mode === "all" ? t("ps_scanning_all") : t("ps_scanning");
    resultEl.innerHTML = "";
    scanBtn.disabled = true;
    try {
      const res = await api.portScan(ip, mode, ports);
      const scannedInfo = res.scanned ? ` (${res.scanned} ${t("ps_ports_checked")})` : "";
      if (!res.ports.length) {
        statusEl.textContent = t("ps_none") + scannedInfo;
        resultEl.innerHTML = `<div style="color:var(--subtext)">${t("ps_none")}</div>`;
      } else {
        statusEl.textContent = `${res.ports.length} ${t("ps_open_found")}${scannedInfo}`;
        resultEl.innerHTML = `<b>${t("ps_open_ports")}:</b><div style="margin-top:6px">` + res.ports.map((p) =>
          `<span style="display:inline-block;background:var(--panel);border:1px solid var(--accent);border-radius:5px;padding:2px 8px;margin:3px;font-size:12px">
            ${p.port} <span style="color:var(--subtext)">${esc(p.service)}</span>
          </span>`).join("") + `</div>`;
      }
    } catch (e) {
      statusEl.textContent = "";
      resultEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
    } finally {
      scanBtn.disabled = false;
    }
  }

  scanBtn.addEventListener("click", scan);
  ipInput.addEventListener("keydown", (e) => { if (e.key === "Enter") scan(); });
  portsInput.addEventListener("keydown", (e) => { if (e.key === "Enter") scan(); });

  // Wenn die IP schon vorausgefüllt ist (aus dem Netzwerk-Scanner), direkt scannen.
  if (presetIp) setTimeout(scan, 50);
  else setTimeout(() => ipInput.focus(), 50);
}

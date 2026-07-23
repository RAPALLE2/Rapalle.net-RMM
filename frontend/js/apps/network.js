// apps/network.js
// ---------------
// Netzwerk-Scanner. Das Ziel kann angegeben werden als
//   192.168.1.      -> ein /24 (254 Adressen)
//   10.10           -> ein /16, also 256 x 254 Adressen
//   10.10.0.0/16    -> beliebiges CIDR (ab /8)
//   leer            -> eigenes Subnetz
// Große Netze laufen als Hintergrund-Job mit Fortschrittsanzeige. Der
// ⚡ Speed-Up verteilt die /24-Blöcke auf mehrere parallele Worker.

import { api } from "../api.js";
import { esc } from "../utils.js";
import { openWindow } from "../windowmanager.js";
import { registerCleanup } from "../windowmanager.js";

const SPEEDS = {
  normal: { label: "🐢 Normal", hint: "schont Netz und CPU" },
  fast:   { label: "⚡ Speed-Up", hint: "8 Subnetze gleichzeitig – empfohlen" },
  turbo:  { label: "🚀 Turbo", hint: "24 Subnetze parallel (max. 512 Pings gleichzeitig)" },
};

export function renderNetwork(body, win) {
  let speed = "fast";
  let jobId = null;
  let poll = null;
  let destroyed = false;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar" style="flex-wrap:wrap;gap:8px">
        <input type="text" id="net-subnet" placeholder="Ziel: 192.168.1.  ·  10.10 (= /16)  ·  10.10.0.0/16  ·  leer = eigenes Netz"
          style="flex:1;min-width:220px;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
        <div id="net-speeds" style="display:flex;gap:4px">
          ${Object.entries(SPEEDS).map(([k, v]) =>
            `<button class="taskbar-btn net-speed" data-speed="${k}" title="${esc(v.hint)}">${v.label}</button>`).join("")}
        </div>
        <button class="btn-primary" id="net-scan" style="width:auto;margin:0">🔍 Scannen</button>
        <button class="taskbar-btn" id="net-stop" style="display:none">✋ Abbrechen</button>
      </div>
      <div id="net-status" style="padding:6px 10px;font-size:12px;color:var(--subtext)"></div>
      <div id="net-progress" style="display:none;padding:0 10px 6px">
        <div style="height:8px;border-radius:4px;background:var(--panel-2);overflow:hidden">
          <div id="net-bar" style="height:100%;width:0%;background:var(--accent);transition:width .25s"></div>
        </div>
      </div>
      <div style="flex:1;overflow:auto">
        <table class="data-table">
          <thead><tr><th>IP</th><th>Hostname</th><th>MAC</th><th style="width:130px">Ports</th></tr></thead>
          <tbody id="net-body"><tr><td colspan="4" style="color:var(--subtext)">Noch nicht gescannt.</td></tr></tbody>
        </table>
      </div>
    </div>`;

  const subnetInput = body.querySelector("#net-subnet");
  const scanBtn = body.querySelector("#net-scan");
  const stopBtn = body.querySelector("#net-stop");
  const statusEl = body.querySelector("#net-status");
  const progEl = body.querySelector("#net-progress");
  const barEl = body.querySelector("#net-bar");
  const tbody = body.querySelector("#net-body");

  // ---- Geschwindigkeit wählen ----
  function drawSpeeds() {
    body.querySelectorAll(".net-speed").forEach((b) => {
      const on = b.dataset.speed === speed;
      b.style.background = on ? "var(--accent)" : "";
      b.style.color = on ? "#fff" : "";
    });
  }
  body.querySelectorAll(".net-speed").forEach((b) =>
    b.addEventListener("click", () => { speed = b.dataset.speed; drawSpeeds(); showPreview(); }));
  drawSpeeds();

  // ---- Vorschau: wie groß wird der Scan? ----
  let previewTimer = null;
  async function showPreview() {
    if (jobId) return;
    clearTimeout(previewTimer);
    previewTimer = setTimeout(async () => {
      try {
        const p = await api.scanPreview(subnetInput.value.trim() || null);
        const est = estimate(p.hosts, speed);
        statusEl.textContent =
          `${p.label}: ${p.subnets} Subnetz${p.subnets === 1 ? "" : "e"}, `
          + `${p.hosts.toLocaleString("de-DE")} Adressen – geschätzt ${est}.`;
        statusEl.style.color = "var(--subtext)";
      } catch (e) {
        statusEl.textContent = e.message;
        statusEl.style.color = "var(--danger)";
      }
    }, 250);
  }
  // Grobe Schätzung: Adressen / effektive Nebenläufigkeit * Ping-Timeout.
  // Das Backend deckelt die Gesamtzahl gleichzeitiger Pings (MAX_TOTAL_PINGS),
  // damit nicht tausende Systemprozesse gleichzeitig laufen - die Schätzung
  // muss dieselbe Obergrenze berücksichtigen, sonst verspricht sie zu viel.
  const MAX_PAR = 512;
  function estimate(hosts, sp) {
    const par = Math.min({ normal: 64, fast: 8 * 96, turbo: 24 * 128 }[sp] || 64, MAX_PAR);
    const sec = Math.max(2, Math.round((hosts / par) * 1.1));
    if (sec < 90) return `${sec} Sekunden`;
    if (sec < 5400) return `${Math.round(sec / 60)} Minuten`;
    return `${(sec / 3600).toFixed(1)} Stunden`;
  }
  subnetInput.addEventListener("input", showPreview);
  subnetInput.addEventListener("keydown", (e) => { if (e.key === "Enter") scan(); });

  // ---- Scan starten / verfolgen ----
  async function scan() {
    if (jobId) return;
    tbody.innerHTML = `<tr><td colspan="4" style="color:var(--subtext)">Scan läuft…</td></tr>`;
    scanBtn.disabled = true;
    try {
      const res = await api.scanStart(subnetInput.value.trim() || null, speed);
      jobId = res.job_id;
      stopBtn.style.display = "";
      progEl.style.display = "";
      poll = setInterval(pollJob, 700);
      pollJob();
    } catch (e) {
      scanBtn.disabled = false;
      statusEl.style.color = "var(--danger)";
      statusEl.textContent = e.message;
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  }

  async function pollJob() {
    if (!jobId || destroyed) return;
    let job;
    try { job = await api.scanJob(jobId); }
    catch (e) { finish(); statusEl.textContent = e.message; return; }

    const pct = job.total_hosts ? (job.done_hosts / job.total_hosts) * 100 : 0;
    barEl.style.width = Math.min(100, pct).toFixed(1) + "%";
    statusEl.style.color = "var(--subtext)";
    statusEl.textContent =
      `${job.label} · ${job.done_subnets}/${job.total_subnets} Subnetze · `
      + `${job.done_hosts.toLocaleString("de-DE")}/${job.total_hosts.toLocaleString("de-DE")} Adressen `
      + `(${pct.toFixed(1)} %) · ${job.found} Geräte gefunden`
      + (job.max_parallel ? ` · ${job.max_parallel} Pings parallel` : "");

    if (job.status === "done" || job.status === "cancelled" || job.status === "error") {
      finish();
      if (job.status === "error") {
        statusEl.style.color = "var(--danger)";
        statusEl.textContent = job.error || "Scan fehlgeschlagen";
        tbody.innerHTML = `<tr><td colspan="4" style="color:var(--danger)">${esc(job.error || "Fehler")}</td></tr>`;
        return;
      }
      renderDevices(job.devices || []);
      const dur = job.finished && job.started ? Math.round((job.finished - job.started) / 1000) : null;
      statusEl.textContent =
        `${(job.devices || []).length} Geräte in ${job.label}`
        + (dur != null ? ` · ${dur < 90 ? dur + " s" : Math.round(dur / 60) + " min"}` : "")
        + (job.status === "cancelled" ? " (abgebrochen)" : "");
    }
  }

  function finish() {
    clearInterval(poll); poll = null; jobId = null;
    scanBtn.disabled = false;
    stopBtn.style.display = "none";
    progEl.style.display = "none";
  }

  stopBtn.addEventListener("click", async () => {
    if (!jobId) return;
    try { await api.scanCancel(jobId); } catch {}
  });
  scanBtn.addEventListener("click", scan);

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
      </tr>`).join("");

    // Portscan-Button öffnet das Portscan-Programm mit schon eingetragener IP.
    tbody.querySelectorAll("[data-portscan]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const ip = btn.dataset.portscan;
        openWindow({
          key: `portscan-${ip}`, appId: "portscan",
          title: `Portscan — ${ip}`, props: { ip }, w: 560, h: 480,
        });
      }));
  }

  // Beim Öffnen letztes Ergebnis laden (falls vorhanden)
  api.lastScan().then((res) => {
    if (res.devices && res.devices.length) {
      renderDevices(res.devices);
      statusEl.textContent = `${res.devices.length} Geräte (letzter Scan).`;
    } else showPreview();
  }).catch(() => showPreview());

  if (win?.key) registerCleanup(win.key, () => { destroyed = true; clearInterval(poll); });
}

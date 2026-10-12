// Einstellungsmenue des Moduls "Guacamole".
//
//  1. Docker-Container (guacd): Zustand ueberwachen, starten, stoppen, neu starten.
//  2. Manuell verbinden: Host/Port eines eigenen guacd eintragen und testen.
//
// Die Zugangsdaten stehen weiterhin in den RMM-Einstellungen (guacd_host / guacd_port),
// damit sich am Verhalten des Backends nichts aendert.

import { api } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";

const POLL_MS = 5000;

function ago(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d) || d.getFullYear() < 2000) return "–";
  const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (s < 90) return `${s} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} Tage`;
}

export async function renderSettings(root, ctx) {
  const s = await api.getSettings();
  root.innerHTML = `
    <div>
      <h3 style="margin:0 0 6px">🐳 Docker-Container (guacd)</h3>
      <p style="color:var(--subtext);font-size:13px;margin:0 0 8px">
        Das RMM kann den Guacamole-Daemon (guacd) selbst als Container starten und überwachen.
        Beim Starten werden Host und Port automatisch unten eingetragen.
      </p>
      <div id="gs-docker" style="font-size:13px;color:var(--subtext)">${t("loading")}</div>

      <h3 style="margin:24px 0 6px">🔌 Manuell verbinden</h3>
      <p style="color:var(--subtext);font-size:13px;margin:0 0 8px">
        Du betreibst guacd selbst (anderer Server/Container)? Dann Host und Port hier eintragen.
      </p>
      <div style="display:flex;gap:12px">
        <div class="form-row" style="flex:2"><label>${t("guac_host")}</label>
          <input type="text" id="gs-host" placeholder="192.168.1.20" value="${esc(s.guacd_host || "")}" /></div>
        <div class="form-row" style="flex:1"><label>${t("guac_port")}</label>
          <input type="number" min="1" max="65535" id="gs-port" value="${esc(String(s.guacd_port ?? 4822))}" /></div>
      </div>
      <div id="gs-status" style="font-size:13px;margin:2px 0 8px;color:var(--subtext)">${t("guac_loading")}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <button class="btn-primary" id="gs-save">${t("save")}</button>
        <button class="btn" id="gs-test">${t("guac_test")}</button>
        <span id="gs-msg" style="font-size:12px"></span>
      </div>
    </div>`;

  const q = (sel) => root.querySelector(sel);
  const box = q("#gs-docker");
  const statusEl = q("#gs-status");
  const msg = q("#gs-msg");
  let timer = null;
  let busy = false;

  // ---------------------------------------------------------------- Erreichbarkeit
  async function refreshStatus() {
    statusEl.textContent = t("guac_loading");
    try {
      const st = await api.guacStatus();
      statusEl.innerHTML = st.available
        ? `<span style="color:#3ecf8e">● guacd ${t("guac_reachable")}</span>`
        : `<span style="color:var(--warn,#f5a524)">○ guacd ${t("guac_unreachable")}</span>`;
    } catch (e) { statusEl.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; }
  }

  // ---------------------------------------------------------------- Docker
  async function drawDocker() {
    if (!root.isConnected) { clearInterval(timer); return; }      // Fenster/Tab wurde geschlossen
    let info, d;
    try {
      info = await api.dockerServices();
      if (!info.is_docker) { box.innerHTML = `<div style="border:1px solid var(--border);border-radius:8px;padding:10px">Das RMM läuft nicht in Docker – hier gibt es nichts zu verwalten. Nutze „Manuell verbinden“.</div>`; clearInterval(timer); return; }
      if (!info.socket) { box.innerHTML = `<div style="border:1px solid var(--warn,#f5a524);border-radius:8px;padding:10px">⚠ ${t("set_docker_no_socket")}</div>`; clearInterval(timer); return; }
      d = await ctx.req("/api/admin/docker/services/guacd/details");
    } catch (e) { box.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; return; }
    const on = d.running;
    const color = on ? "#3ecf8e" : d.state === "absent" ? "var(--subtext)" : "#ff4d6d";
    const health = d.health ? ` · Gesundheit: <b>${esc(d.health)}</b>` : "";
    const warn = d.oom_killed ? `<div style="color:#ff4d6d;margin-top:4px">⚠ Container wurde wegen Speichermangel beendet (OOM).</div>` : "";
    const err = d.error ? `<div style="color:#ff4d6d;margin-top:4px">${esc(d.error)}</div>` : "";
    box.innerHTML = `
      <div style="border:1px solid var(--border);border-radius:10px;padding:12px 14px">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span style="font-size:18px;color:${color}">${on ? "🟢" : "⚪"}</span>
          <div style="flex:1;min-width:200px">
            <div style="color:var(--text)"><b>${esc(d.label)}</b> <code style="font-size:11px">${esc(d.container)}</code></div>
            <div style="font-size:12px">Zustand: <b style="color:${color}">${esc(d.state)}</b>${health}
              ${on ? ` · läuft seit ${ago(d.started_at)}` : d.exists ? ` · beendet vor ${ago(d.finished_at)} (Exit ${d.exit_code})` : ""}
              · Neustarts: ${d.restart_count ?? 0}</div>
            <div style="font-size:11px;color:var(--subtext)">Image: ${esc(d.image)}</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            ${on ? `<button class="btn" data-dk="restart">↻ Neu starten</button>
                    <button class="btn btn-warn" data-dk="stop">■ Stoppen</button>`
                 : `<button class="btn-primary" data-dk="start">▶ Starten</button>`}
            <button class="btn" data-dk="refresh" title="Jetzt aktualisieren">⟳</button>
          </div>
        </div>${warn}${err}
        <div style="font-size:11px;color:var(--subtext);margin-top:8px">Live-Überwachung: aktualisiert sich alle ${POLL_MS / 1000} Sekunden, solange dieses Menü offen ist.</div>
      </div>`;
    box.querySelectorAll("[data-dk]").forEach((b) => b.addEventListener("click", () => action(b)));
  }

  async function action(btn) {
    const kind = btn.dataset.dk;
    if (kind === "refresh") return drawDocker();
    if (busy) return;
    busy = true; btn.disabled = true; const orig = btn.textContent; btn.textContent = "…";
    try {
      if (kind === "start") {
        const r = await api.dockerEnable("guacd");
        if (r?.guacd?.host) { q("#gs-host").value = r.guacd.host; q("#gs-port").value = String(r.guacd.port || 4822); }
        window.notify?.("guacd gestartet", "success");
      } else if (kind === "stop") {
        await api.dockerDisable("guacd"); window.notify?.("guacd gestoppt", "info");
      } else if (kind === "restart") {
        await ctx.req("/api/admin/docker/services/guacd/restart", { method: "POST" });
        window.notify?.("guacd neu gestartet", "success");
      }
    } catch (e) { window.notify?.(e.message, "error", 9000); btn.textContent = orig; }
    busy = false;
    await drawDocker(); await refreshStatus();
  }

  // ---------------------------------------------------------------- Manuell
  q("#gs-save").addEventListener("click", async () => {
    msg.style.color = ""; msg.textContent = "";
    try {
      await api.updateSettings({ guacd_host: q("#gs-host").value.trim(), guacd_port: parseInt(q("#gs-port").value, 10) || 4822 });
      msg.style.color = "#3ecf8e"; msg.textContent = "✓ " + t("general_saved");
      await refreshStatus();
    } catch (e) { msg.style.color = "var(--danger)"; msg.textContent = e.message; }
  });
  q("#gs-test").addEventListener("click", async (e) => {
    const b = e.currentTarget, o = b.textContent; b.disabled = true; b.textContent = "…";
    await refreshStatus(); b.disabled = false; b.textContent = o;
  });

  await drawDocker();
  timer = setInterval(drawDocker, POLL_MS);
  refreshStatus();
}

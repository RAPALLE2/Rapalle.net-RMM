// settings.js (Modul "sql")
// -------------------------
// Datenbank des RMM verwalten (frueher Einstellungen -> Allgemein -> Datenbank).
// Wird an zwei Stellen gezeigt:
//   * Modul-App -> SQL-Datenbank -> Einstellungen   (renderSettings)
//   * eigene App "Datenbank" im Startmenue          (renderDatabaseApp)
//
// Register:
//   Lokale Datei   SQLite im Backend (backend/data/data.sqlite) - Standard
//   Extern         MySQL/MariaDB, PostgreSQL oder SQLite-Datei auf einer Freigabe
//   Docker         MariaDB-Nachbarcontainer - NUR wenn das Backend im Container laeuft
//   Sicherungen    Backup erstellen (backend/data/backup) und zurueckspielen
//
// Beim Deaktivieren des Moduls wird eine externe DB automatisch in die lokale
// Datei zurueckkopiert (Backend: module.py -> on_disable).

import { api } from "/js/app/api.js";
import { esc, uiConfirm } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";

export function renderDatabaseApp(body) {
  body.innerHTML = `<div style="padding:16px;height:100%;overflow:auto;box-sizing:border-box"></div>`;
  return renderSettings(body.firstElementChild);
}

export async function renderSettings(root) {
  let tab = "local";
  let info = null;       // {config}
  let docker = null;     // {is_docker, socket, service, credentials}
  let pgTimer = null;
  let pgMisses = 0;

  root.innerHTML = `<div class="sqlm">
    <div id="sq-status" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;
         padding:10px 12px;border:1px solid var(--border);border-radius:8px;margin-bottom:12px;font-size:13px">
      ${t("set_db_loading")}
    </div>
    <div class="tab-bar" id="sq-tabs" style="padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:12px"></div>
    <div id="sq-body"></div>
    <div id="sq-error" class="form-error hidden"></div>
    <div id="sq-progress" style="display:none;margin:12px 0;border:1px solid var(--border);border-radius:8px;padding:10px;font-size:12.5px">
      <div style="display:flex;justify-content:space-between;gap:10px">
        <span id="sq-pg-phase"></span><span id="sq-pg-count" style="color:var(--subtext)"></span>
      </div>
      <div style="height:8px;background:var(--panel-2);border-radius:5px;margin:8px 0;overflow:hidden">
        <div id="sq-pg-bar" style="height:100%;width:0%;background:var(--accent);transition:width .25s"></div>
      </div>
      <div id="sq-pg-log" style="max-height:150px;overflow:auto;font-family:ui-monospace,monospace;
           font-size:11.5px;color:var(--subtext);white-space:pre-wrap"></div>
    </div>
  </div>`;

  const $ = (sel) => root.querySelector(sel);
  const showErr = (msg) => { const e = $("#sq-error"); e.textContent = msg; e.classList.remove("hidden"); };
  const clearErr = () => $("#sq-error").classList.add("hidden");
  const isExt = () => info?.config?.mode === "external";
  const dockerActive = () => {
    const c = info?.config || {}, d = docker?.credentials || {};
    return isExt() && d.host && c.host === d.host;
  };

  // ------------------------------------------------------------ Status + Register
  function drawStatus() {
    const c = info?.config || {};
    let label, icon;
    if (dockerActive()) { icon = "🐳"; label = `Docker-Container (${esc(c.host)})`; }
    else if (isExt()) { icon = "🌐"; label = `Extern · ${esc(c.type || "")} · ${esc(c.type === "sqlite" ? c.database : c.host)}`; }
    else { icon = "💾"; label = "Lokale Datei (SQLite)"; }
    $("#sq-status").innerHTML = `<span style="font-size:18px">${icon}</span>
      <span>${t("set_db_current_mode")}: <b>${label}</b></span>`;
  }

  function drawTabs() {
    const tabs = [["local", "💾 Lokale Datei"], ["external", "🌐 Externe Datenbank"]];
    if (docker?.is_docker) tabs.push(["docker", "🐳 Docker-Container"]);
    tabs.push(["backups", "🗂️ Sicherungen"]);
    if (!tabs.some(([k]) => k === tab)) tab = "local";
    $("#sq-tabs").innerHTML = tabs.map(([k, l]) =>
      `<button class="tab-btn${k === tab ? " active" : ""}" data-tab="${k}">${l}</button>`).join("");
    $("#sq-tabs").querySelectorAll("[data-tab]").forEach((b) =>
      b.addEventListener("click", () => { tab = b.dataset.tab; clearErr(); drawTabs(); drawBody(); }));
  }

  function drawBody() {
    if (tab === "local") return drawLocal();
    if (tab === "external") return drawExternal();
    if (tab === "docker") return drawDocker();
    return drawBackups();
  }

  const hint = (html) => `<p style="color:var(--subtext);font-size:13px;line-height:1.5;margin:0 0 12px">${html}</p>`;

  // ------------------------------------------------------------ Lokal
  function drawLocal() {
    $("#sq-body").innerHTML = `
      ${hint("Standard: Alle Daten liegen in der SQLite-Datei <code>backend/data/data.sqlite</code>. "
        + "Diese Datei ist auch im externen Modus die Arbeitskopie des Backends.")}
      ${isExt()
        ? `${hint("Gerade ist eine externe Datenbank aktiv. Beim Zurückschalten wird ihr kompletter Stand in die lokale Datei kopiert; vorher wird automatisch eine Sicherung angelegt. Das Backend startet danach neu.")}
           <button class="btn-primary" id="sq-to-local">→ ${t("set_db_back_to_local")}</button>`
        : `<div style="display:flex;gap:8px;align-items:center"><button class="btn" disabled>✓ ${t("set_db_local_active")}</button></div>`}`;
    $("#sq-to-local")?.addEventListener("click", () => doSwitch("local", { ...(info?.config || {}), password: "" }));
  }

  // ------------------------------------------------------------ Extern
  function drawExternal() {
    const c = info?.config || {};
    $("#sq-body").innerHTML = `
      ${hint("MySQL/MariaDB, PostgreSQL oder eine SQLite-Datei z. B. auf einer Netzwerkfreigabe. "
        + "Beim Umschalten werden alle lokalen Daten dorthin kopiert; das Backend gleicht danach laufend ab.")}
      <div class="form-row"><label>${t("set_db_type")}</label>
        <select id="sq-type">
          <option value="mysql">MySQL / MariaDB</option>
          <option value="postgres">PostgreSQL</option>
          <option value="sqlite">${t("set_db_sqlite_file")}</option>
        </select></div>
      <div style="display:flex;gap:12px" id="sq-hostrow">
        <div class="form-row" style="flex:2"><label>Host</label><input type="text" id="sq-host" placeholder="192.168.1.50"></div>
        <div class="form-row" style="flex:1"><label>Port</label><input type="number" id="sq-port" min="1" max="65535"></div>
      </div>
      <div style="display:flex;gap:12px" id="sq-userrow">
        <div class="form-row" style="flex:1"><label>${t("username")}</label><input type="text" id="sq-user"></div>
        <div class="form-row" style="flex:1"><label>${t("password")}</label>
          <input type="password" id="sq-pass" placeholder="${t("u_unverandert_lassen_leer")}"></div>
      </div>
      <div class="form-row"><label id="sq-dblabel">${t("set_db_name")}</label>
        <input type="text" id="sq-name" placeholder="rapalle_rmm"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
        <button class="btn" id="sq-test">${t("set_test_conn")}</button>
        ${isExt() && !dockerActive()
          ? `<button class="btn" disabled>✓ ${t("set_db_external_active")}</button>`
          : `<button class="btn-primary" id="sq-to-ext">→ ${t("set_db_to_external")}</button>`}
      </div>`;
    const typeSel = $("#sq-type");
    typeSel.value = c.type || "mysql";
    $("#sq-host").value = c.host || "";
    $("#sq-port").value = c.port || "";
    $("#sq-user").value = c.user || "";
    $("#sq-name").value = c.database || "";
    const adapt = () => {
      const file = typeSel.value === "sqlite";
      $("#sq-hostrow").style.display = file ? "none" : "flex";
      $("#sq-userrow").style.display = file ? "none" : "flex";
      $("#sq-dblabel").textContent = file ? "Dateipfad (z. B. /mnt/share/rmm.sqlite)" : t("set_db_name");
      const port = $("#sq-port");
      if (!file && (!port.value || port.value === "0")) port.value = typeSel.value === "postgres" ? "5432" : "3306";
    };
    typeSel.addEventListener("change", adapt);
    adapt();
    const cfg = () => ({
      type: typeSel.value, host: $("#sq-host").value.trim(),
      port: parseInt($("#sq-port").value, 10) || 0, user: $("#sq-user").value.trim(),
      password: $("#sq-pass").value, database: $("#sq-name").value.trim(),
    });
    $("#sq-test").addEventListener("click", () => testConn(cfg()));
    $("#sq-to-ext")?.addEventListener("click", () => doSwitch("external", cfg()));
  }

  // ------------------------------------------------------------ Docker
  function drawDocker() {
    const box = $("#sq-body");
    if (!docker?.socket) {
      box.innerHTML = `<div style="border:1px solid var(--warn);border-radius:8px;padding:10px;font-size:13px">
        ⚠ ${t("set_docker_no_socket")}</div>`;
      return;
    }
    const sv = docker.service || {};
    const on = !!sv.running;
    const cr = docker.credentials || {};
    const state = on ? t("set_dk_running") : (sv.state === "absent" ? t("set_dk_absent") : (sv.state || "?"));
    box.innerHTML = `
      ${hint("Das Backend läuft im Container und kann eine MariaDB als Nachbar-Container starten. "
        + "Zugangsdaten werden beim ersten Start erzeugt und hier hinterlegt. Das Daten-Volume bleibt beim Abschalten erhalten.")}
      <div style="display:flex;gap:10px;align-items:center;padding:10px 12px;border:1px solid var(--border);border-radius:8px">
        <span style="font-size:16px">${on ? "🟢" : "⚪"}</span>
        <div style="flex:1;min-width:0">
          <div>${esc(sv.label || "MariaDB")}</div>
          <div style="font-size:11.5px;color:var(--subtext)">${esc(sv.image || "")} · ${esc(state)}</div>
        </div>
        <button class="btn${on ? " btn-warn" : ""}" id="sq-dk-toggle" ${dockerActive() && on ? "disabled title=\"Wird gerade als Datenbank benutzt\"" : ""}>
          ${on ? t("set_dk_off") : t("set_dk_on")}</button>
      </div>
      ${cr.host ? `<div style="font-size:12px;color:var(--subtext);margin:10px 0">
          Host <code>${esc(cr.host)}:${esc(cr.port)}</code> · Datenbank <code>${esc(cr.database)}</code> · Benutzer <code>${esc(cr.user)}</code></div>` : ""}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
        ${dockerActive()
          ? `<button class="btn" disabled>✓ Docker-Datenbank aktiv</button>`
          : `<button class="btn-primary" id="sq-dk-use" ${on && cr.host ? "" : "disabled title=\"Erst den Container starten\""}>→ Docker-Datenbank verwenden</button>`}
      </div>`;
    $("#sq-dk-toggle")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true; btn.textContent = on ? "…" : t("set_dk_starting");
      try {
        if (on) await api.dockerDisable("db"); else await api.dockerEnable("db");
        window.notify?.(on ? t("set_dk_stopped") : t("set_dk_running"), "success");
        await reload();
      } catch (err) {
        window.notify?.(err.message, "error", 9000);
        drawDocker();
      }
    });
    $("#sq-dk-use")?.addEventListener("click", () => doSwitch("external", { ...cr }));
  }

  // ------------------------------------------------------------ Sicherungen
  async function drawBackups() {
    $("#sq-body").innerHTML = `
      ${hint("Eine Sicherung enthält immer den <b>kompletten</b> Stand (auch im externen Modus, weil die lokale "
        + "Datei die Arbeitskopie ist). Ablage: <code>backend/data/backup</code>. Vor jedem Umschalten wird "
        + "automatisch eine Sicherung angelegt; es werden die letzten 5 aufbewahrt.")}
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <button class="btn-primary" id="sq-backup">💾 Datenbank-Backup erstellen</button>
        <button class="btn btn-icon" id="sq-bk-refresh" title="${t("exp_refresh")}">⟳</button>
      </div>
      <div id="sq-bk-list" style="font-size:12.5px;color:var(--subtext)">${t("loading")}</div>`;
    $("#sq-bk-refresh").addEventListener("click", loadBackups);
    $("#sq-backup").addEventListener("click", async (e) => {
      const btn = e.currentTarget, orig = btn.textContent;
      btn.disabled = true; btn.textContent = "…";
      try {
        const r = await api.databaseBackup();
        window.notify?.(`${t("set_db_backup_done")} ${r.path ? "(" + r.path.split(/[\\/]/).pop() + ")" : ""}`, "success", 5000);
        loadBackups();
      } catch (err) { window.notify?.(err.message, "error", 9000); }
      btn.disabled = false; btn.textContent = orig;
    });
    loadBackups();
  }

  async function loadBackups() {
    const box = $("#sq-bk-list");
    if (!box) return;
    try {
      const list = (await api.databaseBackups()).backups || [];
      if (!list.length) { box.textContent = t("set_db_no_backups"); return; }
      box.innerHTML = list.map((b) => `
        <div style="display:flex;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">
          <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;color:var(--text)">${esc(b.name)}</span>
          <span>${(b.size / 1048576).toFixed(1)} MB</span>
          <span>${new Date(b.at).toLocaleString()}</span>
          <button class="btn btn-sm btn-warn" data-restore="${esc(b.path)}">${t("set_db_restore")}</button>
        </div>`).join("");
      box.querySelectorAll("[data-restore]").forEach((btn) => btn.addEventListener("click", async () => {
        if (!(await uiConfirm(t("set_db_restore_q"), { description: t("set_db_restore_desc"), danger: true }))) return;
        try {
          await api.databaseRestoreBackup(btn.dataset.restore);
          window.notify?.(t("set_db_restoring"), "success", 10000);
          setTimeout(() => location.reload(), 9000);
        } catch (e) { window.notify?.(e.message, "error", 9000); }
      }));
    } catch (e) {
      box.textContent = `${t("set_db_backups_unavailable")}: ${e.message}`;
    }
  }

  // ------------------------------------------------------------ Aktionen
  async function testConn(cfg) {
    clearErr();
    try {
      await api.testDatabase(cfg);
      window.notify?.(t("u_verbindung_erfolgreich"), "success");
    } catch (e) { showErr(e.message); }
  }

  async function doSwitch(mode, cfg) {
    clearErr();
    const ok = await uiConfirm(
      mode === "external" ? t("set_db_to_external_q") : t("set_db_to_local_q"),
      { description: mode === "external" ? t("u_alle_daten_werden_von_der_lokalen_") : t("u_der_stand_der_externen_datenbank_w") });
    if (!ok) return;
    try {
      await api.switchDatabase({ ...cfg, mode });
      startPolling();
    } catch (e) { showErr(e.message); }
  }

  const PHASE_TEXT = {
    backup: t("set_db_phase_backup"), dump: t("set_db_phase_dump"),
    restore: t("set_db_phase_restore"), fertig: t("set_db_phase_done"),
  };

  function drawProgress(p) {
    $("#sq-progress").style.display = "";
    $("#sq-pg-phase").textContent = PHASE_TEXT[p.phase] || p.phase || "…";
    $("#sq-pg-count").textContent = p.total
      ? `${p.done}/${p.total} ${t("set_db_tables")} · ${p.rows} ${t("set_db_rows")}`
      : (p.rows ? `${p.rows} ${t("set_db_rows")}` : "");
    const bar = $("#sq-pg-bar");
    bar.style.width = p.total ? `${Math.round((p.done / p.total) * 100)}%` : "0%";
    bar.style.background = p.ok === false ? "var(--danger)" : "var(--accent)";
    const lines = [...(p.log || [])];
    if (p.errors?.length) {
      lines.push("", `── ${p.errors.length} ${t("errors")} ──`);
      for (const e of p.errors) lines.push(`  ${e.table}: ${e.error}`);
    }
    const log = $("#sq-pg-log");
    log.textContent = lines.join("\n");
    log.scrollTop = log.scrollHeight;
  }

  async function pollProgress() {
    if (!root.isConnected) { clearInterval(pgTimer); return; }
    try {
      const p = await api.databaseProgress();
      pgMisses = 0;
      drawProgress(p);
      if (p.running) return;
      clearInterval(pgTimer); pgTimer = null;
      if (p.ok) {
        window.notify?.(`${p.detail} — ${t("set_backend_restarting")}`, "success", 10000);
        setTimeout(() => location.reload(), 9000);
      } else {
        showErr(p.detail || t("set_db_switch_failed"));
        window.notify?.(p.restored ? t("set_db_failed_restored") : t("set_db_failed_unchanged"), "error", 14000);
      }
    } catch (e) {
      // Beim Neustart bricht die Verbindung kurz ab - erst nach mehreren Fehlversuchen aufgeben.
      pgMisses++;
      $("#sq-pg-log").textContent += `\n(${t("set_db_poll_failed")}: ${e.message})`;
      if (pgMisses >= 5) {
        clearInterval(pgTimer); pgTimer = null;
        $("#sq-pg-phase").textContent = t("set_db_progress_lost");
        window.notify?.(t("set_db_progress_lost_hint"), "warn", 14000);
      }
    }
  }

  function startPolling() {
    pgMisses = 0;
    drawProgress({ phase: "backup", done: 0, total: 0, rows: 0, log: [] });
    clearInterval(pgTimer);
    pgTimer = setInterval(pollProgress, 2000);
    pollProgress();
  }

  // ------------------------------------------------------------ Laden
  async function reload() {
    try {
      [info, docker] = await Promise.all([
        api.getDatabaseInfo(),
        api.databaseDocker().catch(() => ({ is_docker: false })),
      ]);
    } catch (e) {
      $("#sq-status").innerHTML = `<span style="color:var(--danger)">${t("error")}: ${esc(e.message)}</span>`;
      return;
    }
    if (dockerActive() && tab === "local") tab = "docker";
    else if (isExt() && tab === "local") tab = "external";
    drawStatus(); drawTabs(); drawBody();
    // Laeuft gerade ein Wechsel (z.B. Fenster neu geoeffnet), Fortschritt weiter zeigen.
    try { const p = await api.databaseProgress(); if (p.running) startPolling(); } catch { /* egal */ }
  }

  await reload();
}

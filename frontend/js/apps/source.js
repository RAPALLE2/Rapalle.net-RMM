// apps/source.js
// --------------
// Admin-"Source"-Werkzeuge (nur Super-Admin). Drei Bereiche:
//   - Shell:      Live-Konsole auf dem Backend-Host (PTY über Socket.IO).
//   - Explorer:   Dateibaum über backend/frontend/agent inkl. Datei-Editor.
//   - Datenbank:  Tabellen ansehen + beliebiges SQL (inkl. Key/Value & Arrays).
//   - Migration:  Kompletten Stand exportieren und auf einer anderen Instanz
//                 wieder einspielen (Datenbank + Aufzeichnungen + Medien).
//
// Wird vom Settings-Tab "Source" aufgerufen: renderSource(container).

import { api, getToken } from "../api.js";
import { helpDot } from "../help.js";
import { state } from "../state.js";
import { esc, formatBytes, uiConfirm, uiPrompt, uiConfirmTwice, downloadText } from "../utils.js";
import { dashboardSocket } from "../socket.js";
import { MiniTerm } from "./miniterm.js";
import { t } from "../i18n.js";

export function renderSource(container) {
  container.innerHTML = `
    <div id="src-runtime" style="margin-bottom:10px"></div>
    <div style="display:flex;gap:6px;margin-bottom:10px">
      <button class="tab-btn active" data-src="log">🖥️ Backend-Ausgabe</button>
      <button class="tab-btn" data-src="explorer">📁 Explorer</button>
      <button class="tab-btn" data-src="db">🗄️ Datenbank</button>
      <button class="tab-btn" data-src="migrate">📦 Migration</button>
      <button class="tab-btn" data-src="diag">🩺 Diagnose</button>
    </div>
    <div id="src-panel" style="min-height:420px"></div>
  `;
  renderRuntimeBanner(container.querySelector("#src-runtime"));
  const panel = container.querySelector("#src-panel");
  const tabs = container.querySelectorAll("[data-src]");

  let cleanup = null;
  function show(which) {
    tabs.forEach((b) => b.classList.toggle("active", b.dataset.src === which));
    if (cleanup) { try { cleanup(); } catch {} cleanup = null; }
    panel.innerHTML = "";
    if (which === "log") cleanup = renderBackendLog(panel);
    else if (which === "explorer") renderExplorer(panel);
    else if (which === "migrate") renderMigrate(panel);
    else if (which === "diag") cleanup = renderDiagnostics(panel);
    else renderDb(panel);
  }
  tabs.forEach((b) => b.addEventListener("click", () => show(b.dataset.src)));
  show("log");

  // Aufräumen, wenn der Container aus dem DOM verschwindet (Tab-Wechsel/Fenster zu).
  return () => { if (cleanup) { try { cleanup(); } catch {} } };
}

// ---------------------------------------------------------------
// 0) Installationsart: Docker-Container oder natives Programm
//
// Im Container sind Port und gebundene Adresse beim Container-Start fest
// verdrahtet. Wer sie in backend/.env ändert, sperrt sich aus (der Container
// mappt weiter den alten Port). Darum steht der Hinweis hier ganz oben.
// ---------------------------------------------------------------
async function renderRuntimeBanner(host) {
  if (!host) return;
  let info;
  try {
    info = await api.sourceRuntime();
  } catch {
    // Älteres Backend ohne /api/source/runtime -> einfach nichts anzeigen.
    host.innerHTML = "";
    return;
  }

  const docker = !!info.is_docker;
  const color = docker ? "var(--accent)" : "var(--ok, var(--accent))";
  const icon = docker ? "🐳" : "💻";
  const title = docker ? t("src_install_docker") : t("src_install_native");
  const desc = docker ? t("src_install_docker_desc") : t("src_install_native_desc");
  const locked = (info.locked_settings || []).join(", ");

  host.innerHTML = `
    <div style="display:flex;gap:10px;align-items:flex-start;padding:8px 10px;
                border:1px solid ${color};border-radius:8px;background:var(--panel-2)">
      <div style="font-size:18px;line-height:1.2">${icon}</div>
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;font-size:13px;color:${color}">${esc(title)}</div>
        <div style="font-size:11px;color:var(--subtext);margin-top:2px">${esc(desc)}</div>
        <div style="font-size:11px;color:var(--subtext);margin-top:4px">
          ${t("src_install_bound")}: <code>${esc(info.host || "?")}:${esc(String(info.port || "?"))}</code>
          · Python <code>${esc(info.python || "?")}</code>
          ${info.container_name ? ` · Container <code>${esc(info.container_name)}</code>` : ""}
        </div>
        ${locked ? `
        <div style="font-size:11px;color:var(--warn);margin-top:4px">
          ⚠ ${t("src_install_locked")}: <code>${esc(locked)}</code>
        </div>` : ""}
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------
// 1) Backend-Ausgabe (Live-Log, read-only) + Backend-Neustart
// ---------------------------------------------------------------
function renderBackendLog(panel) {
  panel.innerHTML = `
    <div style="font-size:12px;color:var(--subtext);margin-bottom:6px">
      Live-Ausgabe des laufenden Backends (<code>run.py</code>) — stdout/stderr, read-only.
    </div>
    <div style="display:flex;flex-direction:column;height:440px;background:#0b0f14;border:1px solid var(--border);border-radius:8px;overflow:hidden">
      <div style="display:flex;gap:6px;padding:6px 8px;border-bottom:1px solid var(--border);align-items:center">
        <span style="font-size:11px;color:var(--subtext)">Backend-Ausgabe (folgt automatisch der neuesten Zeile)</span>
        <span style="flex:1"></span>
        <select id="src-log-fmt" title="Export-Format"
          style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:11px">
          <option value="json">JSON</option>
          <option value="xml">XML</option>
          <option value="txt">TXT</option>
        </select>
        <button class="taskbar-btn" id="src-log-download" title="Kompletten Log herunterladen">⬇ Export</button>
        <button class="taskbar-btn" id="src-log-clear">🗑 Leeren</button>
        <button class="taskbar-btn" id="src-log-restart" style="border-color:var(--warn);color:var(--warn)">🔄 Backend neu starten</button>
        <button class="taskbar-btn" id="src-log-stop" style="border-color:var(--danger);color:var(--danger)">📴 Backend stoppen</button>
      </div>
      <div id="src-log-host" style="flex:1;min-height:0"></div>
      <div id="src-log-status" style="font-size:11px;color:var(--subtext);padding:2px 8px;border-top:1px solid var(--border)">Verbinde…</div>
    </div>
  `;
  const host = panel.querySelector("#src-log-host");
  const statusEl = panel.querySelector("#src-log-status");

  // MiniTerm nur zur ANZEIGE (keine Eingabe). Zeigt immer die neuesten Zeilen.
  const term = new MiniTerm(host, { onData: () => {}, onResize: () => {} });
  const ro = new ResizeObserver(() => { try { term.fit(); } catch {} });
  ro.observe(host);

  let gotHistory = false;
  // Kompletter Roh-Log als Puffer (für den Export). ANSI-Farbcodes werden
  // beim Export entfernt, im Terminal aber normal angezeigt.
  let logBuffer = "";
  const onHistory = (p) => {
    gotHistory = true;
    logBuffer += p.data || "";
    term.write(p.data || "");
    statusEl.textContent = "Verbunden — Live-Ausgabe.";
  };
  const onLine = (p) => { logBuffer += p.data || ""; term.write(p.data || ""); };
  dashboardSocket.on("backend-log-history", onHistory);
  dashboardSocket.on("backend-log", onLine);
  dashboardSocket.emit("backend-log-open", { token: getToken() });

  setTimeout(() => {
    if (!gotHistory) {
      term.write(t("src_no_log") + t("src_backend_old") + "\r\n");
      statusEl.textContent = t("src_backend_outdated");
    }
  }, 3000);

  panel.querySelector("#src-log-clear").addEventListener("click", () => { term.write("\x1b[2J\x1b[H"); });

  // --- Export des kompletten Logs (JSON / XML / TXT) ---
  panel.querySelector("#src-log-download").addEventListener("click", () => {
    const fmt = panel.querySelector("#src-log-fmt").value;
    exportLogText(logBuffer, "backend-log", fmt);
  });

  panel.querySelector("#src-log-restart").addEventListener("click", async () => {
    const ok = await uiConfirm(t("src_restart_q"), {
      description: t("src_restart_desc"),
      okText: t("src_restart_ok"), danger: true,
    });
    if (!ok) return;
    try {
      await api.restartBackend();
      window.notify?.(t("src_restarting"), "info", 12000);
      setTimeout(() => window.location.reload(), 6000);
    } catch (e) { window.notify?.(t("src_restart_fail", { err: e.message }), "error"); }
  });

  panel.querySelector("#src-log-stop").addEventListener("click", async () => {
    const ok = await uiConfirm(t("src_stop_q"), {
      description: t("src_stop_full_desc"),
      okText: "Stoppen", danger: true,
    });
    if (!ok) return;
    try {
      await api.stopBackend();
      window.notify?.(t("src_stopping"), "info", 12000);
    } catch (e) { window.notify?.(t("src_stop_fail", { err: e.message }), "error"); }
  });

  return () => {
    dashboardSocket.off("backend-log-history", onHistory);
    dashboardSocket.off("backend-log", onLine);
    dashboardSocket.emit("backend-log-close", {});
    try { ro.disconnect(); } catch {}
  };
}

// ---------------------------------------------------------------
// Export-Helfer: Log-Text als JSON / XML / TXT herunterladen.
// ANSI-Escape-Sequenzen (Farben etc.) werden entfernt.
// ---------------------------------------------------------------
export function exportLogText(raw, baseName, fmt) {
  const clean = String(raw || "").replace(/\x1b\[[0-9;?]*[ -\/]*[@-~]/g, "");
  const lines = clean.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const stamp = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 19);
  if (fmt === "json") {
    const payload = JSON.stringify({ exported_at: new Date().toISOString(),
      source: baseName, line_count: lines.length, lines }, null, 2);
    downloadText(`${baseName}-${stamp}.json`, payload, "application/json");
  } else if (fmt === "xml") {
    const escXml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const body = lines.map((l, i) => `  <line n="${i + 1}">${escXml(l)}</line>`).join("\n");
    const payload = `<?xml version="1.0" encoding="UTF-8"?>\n` +
      `<log source="${baseName}" exported_at="${new Date().toISOString()}" line_count="${lines.length}">\n${body}\n</log>`;
    downloadText(`${baseName}-${stamp}.xml`, payload, "application/xml");
  } else {
    downloadText(`${baseName}-${stamp}.txt`, clean, "text/plain");
  }
}

// ---------------------------------------------------------------
// 2) Datei-Explorer
// ---------------------------------------------------------------
async function renderExplorer(panel) {
  panel.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px" id="src-roots"></div>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
      <input type="file" id="src-zip-input" accept=".zip" style="display:none" />
      <button class="taskbar-btn" id="src-zip-btn" title="${t("src_zip_tip")}">${t("src_zip_btn")}</button>
      <button class="taskbar-btn" id="src-new-dir" title="${t("src_newdir_tip")}">${t("src_newdir_btn")}</button>
      <button class="taskbar-btn" id="src-new-file" title="${t("src_newfile_tip")}">${t("src_newfile_btn")}</button>
      <button class="taskbar-btn" id="src-clear-dist" title="${t("src_dist_tip")}">${t("src_dist_btn")}</button>
      <span id="src-zip-status" style="font-size:12px;color:var(--subtext)"></span>
    </div>
    <div style="display:flex;gap:10px;min-height:0">
      <div style="flex:0 0 300px;border:1px solid var(--border);border-radius:8px;overflow:auto;max-height:440px">
        <div id="src-path" style="padding:6px 8px;border-bottom:1px solid var(--border);font-size:11px;color:var(--subtext);word-break:break-all"></div>
        <div id="src-entries"></div>
      </div>
      <div style="flex:1;min-width:0;border:1px solid var(--border);border-radius:8px;display:flex;flex-direction:column;max-height:440px">
        <div id="src-file-bar" style="padding:6px 8px;border-bottom:1px solid var(--border);font-size:12px;display:flex;align-items:center;gap:8px">
          <span id="src-file-name" style="flex:1;color:var(--subtext)">${t("src_no_file")}</span>
          <button class="btn-primary" id="src-file-save" style="width:auto;margin:0;display:none">${t("save")}</button>
        </div>
        <textarea id="src-file-content" spellcheck="false"
          style="flex:1;min-height:360px;border:0;background:#0b0f14;color:#e6edf3;font-family:monospace;font-size:12px;padding:10px;resize:none;outline:none" placeholder="${t("src_pick_file")}"></textarea>
      </div>
    </div>
  `;
  const rootsEl = panel.querySelector("#src-roots");
  const entriesEl = panel.querySelector("#src-entries");
  const pathEl = panel.querySelector("#src-path");
  const nameEl = panel.querySelector("#src-file-name");
  const saveBtn = panel.querySelector("#src-file-save");
  const contentEl = panel.querySelector("#src-file-content");
  let openPath = null;
  let curDir = "backend";   // aktuell geöffneter Ordner (für ZIP-Ziel)

  // --- dist/ leeren ---
  // Dort sammeln sich die gebauten Agent-Installationspakete: jeder Build legt
  // neue Dateien ab, alte Versionen bleiben liegen.
  panel.querySelector("#src-clear-dist").addEventListener("click", async () => {
    if (!confirm(t("src_dist_confirm"))) return;
    const status = panel.querySelector("#src-zip-status");
    status.textContent = "…";
    try {
      const r = await api.sourceClearDist();
      const kb = Math.round((r.freed || 0) / 1024);
      status.textContent = r.note
        ? r.note
        : t("src_dist_cleared", { n: r.removed, kb }) +
          (r.errors && r.errors.length ? ` — ${r.errors.length} ${t("errors")}` : "");
      // Ansicht auffrischen, falls dist gerade offen ist.
      if (curDir === "dist" || String(curDir).startsWith("dist/")) loadDir(curDir);
    } catch (e) {
      status.textContent = e.message;
    }
    setTimeout(() => { status.textContent = ""; }, 6000);
  });

  // --- ZIP-Upload ---
  const zipInput = panel.querySelector("#src-zip-input");
  const zipStatus = panel.querySelector("#src-zip-status");
  panel.querySelector("#src-zip-btn").addEventListener("click", () => zipInput.click());
  zipInput.addEventListener("change", async () => {
    const f = zipInput.files && zipInput.files[0];
    if (!f) return;
    zipStatus.textContent = t("src_extracting", { name: f.name });
    try {
      const res = await api.sourceUploadZip(f, curDir);
      const where = res.project_update ? t("src_project_root") : ("/" + (res.dest || ""));
      zipStatus.textContent = t("src_extracted", { n: res.count, where });
      window.notify?.(t("src_extracted_from", { n: res.count, name: f.name, where }) + (res.skipped?.length ? ` (${t("src_skipped", { n: res.skipped.length })})` : ""), "success", 8000);
      loadDir(curDir);   // Ansicht aktualisieren
    } catch (e) {
      zipStatus.textContent = "";
      window.notify?.(t("src_zip_fail", { err: e.message }), "error", 10000);
    } finally {
      zipInput.value = "";   // gleiche Datei erneut wählbar machen
    }
  });


  // --- Neuer Ordner / Neue Datei im aktuellen Verzeichnis ---
  panel.querySelector("#src-new-dir").addEventListener("click", async () => {
    const name = await uiPrompt(t("src_newdir"), {
      description: t("src_foldername", { path: curDir || t("src_root") }),
      placeholder: "z.B. scripts" });
    if (!name || !name.trim()) return;
    try {
      await api.sourceMkdir(joinPath(curDir, name.trim()));
      window.notify?.(t("src_dir_created", { name: name.trim() }), "success");
      loadDir(curDir);
    } catch (e) { window.notify?.(t("src_dir_create_fail", { err: e.message }), "error"); }
  });
  panel.querySelector("#src-new-file").addEventListener("click", async () => {
    const name = await uiPrompt(t("src_newfile"), {
      description: t("src_filename", { path: curDir || t("src_root") }),
      placeholder: "z.B. notes.txt" });
    if (!name || !name.trim()) return;
    try {
      const p = joinPath(curDir, name.trim());
      await api.sourceNewFile(p);
      window.notify?.(t("src_file_created", { name: name.trim() }), "success");
      await loadDir(curDir);
      openFile(p);   // direkt im Editor öffnen
    } catch (e) { window.notify?.(t("src_file_create_fail", { err: e.message }), "error"); }
  });

  try {
    const r = await api.sourceRoots();
    rootsEl.innerHTML = r.roots.map((x) =>
      `<button class="taskbar-btn" data-root="${esc(x.path)}">${esc(x.name)}</button>`).join("");
    rootsEl.querySelectorAll("[data-root]").forEach((b) =>
      b.addEventListener("click", () => loadDir(b.dataset.root)));
  } catch (e) {
    rootsEl.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
    return;
  }

  async function loadDir(path) {
    try {
      const d = await api.sourceList(path);
      curDir = d.path || "";     // aktuellen Ordner für den ZIP-Upload merken
      pathEl.textContent = "/" + (d.path || "");
      const up = d.path ? `<div class="src-row" data-dir="${esc(d.parent)}" style="padding:6px 8px;cursor:pointer;font-size:12px">📁 ..</div>` : "";
      entriesEl.innerHTML = up + d.entries.map((e) => {
        const icon = e.type === "dir" ? "📁" : "📄";
        const full = joinPath(d.path, e.name);
        const sub = e.type === "file" ? `<span style="color:var(--subtext);font-size:10px">${formatBytes(e.size)}</span>` : "";
        const attr = e.type === "dir" ? `data-dir="${esc(full)}"` : `data-file="${esc(full)}"`;
        return `<div class="src-row" ${attr} style="padding:6px 8px;cursor:pointer;font-size:12px;border-top:1px solid var(--border);display:flex;align-items:center;gap:6px">
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${icon} ${esc(e.name)}</span>
          ${sub}
          <button class="taskbar-btn" data-ren="${esc(full)}" title="Umbenennen / Verschieben" style="padding:1px 5px;font-size:10px">✏️</button>
          <button class="taskbar-btn" data-del="${esc(full)}" data-isdir="${e.type === "dir" ? "1" : ""}" title="${t("delete")}" style="padding:1px 5px;font-size:10px;border-color:var(--danger);color:var(--danger)">🗑</button>
        </div>`;
      }).join("");
      entriesEl.querySelectorAll("[data-dir]").forEach((el) =>
        el.addEventListener("click", () => loadDir(el.dataset.dir)));
      entriesEl.querySelectorAll("[data-file]").forEach((el) =>
        el.addEventListener("click", () => openFile(el.dataset.file)));
      // Umbenennen / Verschieben (Pfad relativ zum Projekt editierbar)
      entriesEl.querySelectorAll("[data-ren]").forEach((btn) =>
        btn.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const src = btn.dataset.ren;
          const dst = await uiPrompt("Umbenennen / Verschieben", {
            description: "Neuer Pfad (relativ zur Projekt-Wurzel):", value: src });
          if (!dst || dst.trim() === "" || dst.trim() === src) return;
          try {
            await api.sourceRename(src, dst.trim());
            window.notify?.("Umbenannt: " + src + " → " + dst.trim(), "success");
            if (openPath === src) { openPath = dst.trim(); nameEl.textContent = "/" + openPath; }
            loadDir(curDir);
          } catch (e) { window.notify?.(t("src_rename_fail", { err: e.message }), "error"); }
        }));
      // Löschen (Ordner rekursiv -> doppelte Nachfrage)
      entriesEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const target = btn.dataset.del;
          const isDir = btn.dataset.isdir === "1";
          const ok = isDir
            ? await uiConfirmTwice(t("src_folder_del_q", { path: target }), {
                description: t("src_folder_del_desc"),
                okText: t("src_folder_del") })
            : await uiConfirm(t("src_file_del_q", { path: target }), { okText: t("delete"), danger: true });
          if (!ok) return;
          try {
            await api.sourceDelete(target);
            window.notify?.(t("src_deleted", { path: target }), "success");
            if (openPath === target) { openPath = null; nameEl.textContent = t("src_no_file"); contentEl.value = ""; saveBtn.style.display = "none"; }
            loadDir(curDir);
          } catch (e) { window.notify?.(t("src_delete_fail", { err: e.message }), "error"); }
        }));
    } catch (e) {
      entriesEl.innerHTML = `<div style="padding:8px;color:var(--danger)">${esc(e.message)}</div>`;
    }
  }

  async function openFile(path) {
    try {
      const f = await api.sourceRead(path);
      openPath = path;
      nameEl.textContent = "/" + path;
      if (f.too_large) { contentEl.value = `[${t("src_too_large", { size: formatBytes(f.size) })}]`; saveBtn.style.display = "none"; }
      else if (f.binary) { contentEl.value = `[${t("src_binary")}]`; saveBtn.style.display = "none"; }
      else { contentEl.value = f.content; saveBtn.style.display = ""; }
    } catch (e) {
      nameEl.textContent = t("error") + ": " + e.message;
    }
  }

  saveBtn.addEventListener("click", async () => {
    if (!openPath) return;
    try {
      await api.sourceWrite(openPath, contentEl.value);
      window.notify?.(t("src_file_saved", { path: openPath }), "success");
    } catch (e) { window.notify?.(t("src_save_fail", { err: e.message }), "error"); }
  });

  loadDir("backend");
}

function joinPath(dir, name) { return dir ? `${dir}/${name}` : name; }

// ---------------------------------------------------------------
// 3) Datenbank
// ---------------------------------------------------------------
async function renderDb(panel) {
  panel.innerHTML = `
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
      <button class="taskbar-btn" id="src-db-reload" title="${t("src_reload_tables")}">🔄 ${t("exp_refresh")}</button>
      <button class="taskbar-btn" id="src-db-newtable" title="${t("src_newtable_tip")}">${t("src_newtable_btn")}</button>
      <button class="taskbar-btn" id="src-db-backup" title="${t("src_db_backup_tip")}">💾 ${t("src_db_backup_btn")}</button>
      <span style="font-size:11px;color:var(--subtext)">${t("src_db_hint")}</span>
    </div>
    <div style="display:flex;gap:10px;min-height:0">
      <div style="flex:0 0 220px;border:1px solid var(--border);border-radius:8px;overflow:auto;max-height:440px">
        <div style="padding:6px 8px;border-bottom:1px solid var(--border);font-size:11px;color:var(--subtext)">${t("set_db_tables")}</div>
        <div id="src-tables"></div>
      </div>
      <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:8px">
        <div style="display:flex;gap:6px">
          <input id="src-sql" placeholder="${t("src_sql_ph")}"
            style="flex:1;padding:7px 10px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-family:monospace;font-size:12px" />
          <button class="btn-primary" id="src-sql-run" style="width:auto;margin:0">${t("src_run")}</button>
        </div>
        <div id="src-db-toolbar" style="display:none;gap:6px;align-items:center">
          <strong id="src-db-tname" style="font-size:12px"></strong>
          <span style="flex:1"></span>
          <button class="taskbar-btn" id="src-db-addrow" title="${t("src_row_insert")}">${t("src_row_btn")}</button>
          <button class="taskbar-btn" id="src-db-droptable" style="border-color:var(--danger);color:var(--danger)" title="${t("src_table_drop")}">${t("src_droptable_btn")}</button>
        </div>
        <div id="src-db-result" style="border:1px solid var(--border);border-radius:8px;overflow:auto;max-height:400px"></div>
      </div>
    </div>
  `;
  const tablesEl = panel.querySelector("#src-tables");
  const resultEl = panel.querySelector("#src-db-result");
  const sqlInput = panel.querySelector("#src-sql");
  const toolbarEl = panel.querySelector("#src-db-toolbar");
  const tnameEl = panel.querySelector("#src-db-tname");
  let curTable = null;   // aktuell geladene Tabelle (fuer Editier-Aktionen)

  async function loadTables() {
    try {
      const res = await api.sourceDbTables();
      tablesEl.innerHTML = res.tables.map((x) =>
        `<div class="src-row" data-table="${esc(x.name)}" style="padding:6px 8px;cursor:pointer;font-size:12px;border-top:1px solid var(--border);display:flex;justify-content:space-between">
          <span>${esc(x.name)}</span><span style="color:var(--subtext)">${x.count ?? "?"}</span></div>`).join("");
      tablesEl.querySelectorAll("[data-table]").forEach((el) =>
        el.addEventListener("click", () => loadTable(el.dataset.table)));
    } catch (e) {
      tablesEl.innerHTML = `<div style="padding:8px;color:var(--danger)">${esc(e.message)}</div>`;
    }
  }
  loadTables();

  // ---- Neu laden (Tabellenliste + aktuell geöffnete Tabelle) ----
  panel.querySelector("#src-db-reload").addEventListener("click", () => {
    loadTables();
    if (curTable) loadTable(curTable);
    window.notify?.("Datenbank-Ansicht neu geladen.", "info", 2500);
  });

  // ---- Backup ----
  panel.querySelector("#src-db-backup").addEventListener("click", async () => {
    const ok = await uiConfirm(t("src_backup_q"), {
      description: t("src_backup_desc"),
      okText: t("src_backup_ok") });
    if (!ok) return;
    try {
      const r = await api.sourceDbBackup();
      window.notify?.(`Backup erstellt: ${r.path} (${formatBytes(r.size)})`, "success", 8000);
    } catch (e) { window.notify?.(t("src_backup_fail", { err: e.message }), "error"); }
  });

  // ---- Tabelle erstellen ----
  panel.querySelector("#src-db-newtable").addEventListener("click", async () => {
    const name = await uiPrompt(t("src_newtable_name"), { placeholder: t("src_newtable_ph") });
    if (!name || !name.trim()) return;
    const cols = await uiPrompt("Spalten-Definition (SQL)", {
      description: 'z.B.:  id INTEGER PRIMARY KEY, name TEXT, created_at INTEGER',
      placeholder: "id INTEGER PRIMARY KEY, name TEXT" });
    if (!cols || !cols.trim()) return;
    try {
      await api.sourceDbCreateTable(name.trim(), cols.trim());
      window.notify?.(t("src_table_created", { name: name.trim() }), "success");
      loadTables();
      loadTable(name.trim());
    } catch (e) { window.notify?.(t("src_create_fail", { err: e.message }), "error"); }
  });

  // ---- Tabelle löschen (doppelte Nachfrage) ----
  panel.querySelector("#src-db-droptable").addEventListener("click", async () => {
    if (!curTable) return;
    const ok = await uiConfirmTwice(t("src_table_drop_q", { table: curTable }), {
      description: t("src_table_drop_desc"),
      okText: t("src_table_drop"),
      secondTitle: t("src_drop_final_q", { name: curTable }),
      secondDescription: t("src_table_drop_warn") });
    if (!ok) return;
    try {
      await api.sourceDbDropTable(curTable);
      window.notify?.(t("src_table_dropped", { name: curTable }), "success");
      curTable = null; toolbarEl.style.display = "none";
      resultEl.innerHTML = "";
      loadTables();
    } catch (e) { window.notify?.(t("src_delete_fail", { err: e.message }), "error"); }
  });

  // ---- Zeile einfügen ----
  panel.querySelector("#src-db-addrow").addEventListener("click", async () => {
    if (!curTable) return;
    try {
      await api.sourceDbInsertRow(curTable, {});
      window.notify?.(t("src_row_inserted"), "success");
      loadTable(curTable); loadTables();
    } catch (e) { window.notify?.(t("src_insert_fail", { err: e.message }), "error"); }
  });

  function renderTable(columns, rows, rowids = null, editable = false) {
    if (!rows.length) { resultEl.innerHTML = `<div style="padding:10px;color:var(--subtext)">Keine Zeilen.</div>`; return; }
    const canEdit = editable && rowids;
    const head = columns.map((c) => `<th style="text-align:left;padding:6px 8px;position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--border)">${esc(c)}</th>`).join("")
      + (canEdit ? `<th style="position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--border)"></th>` : "");
    const body = rows.map((r, ri) => `<tr data-rowid="${canEdit ? rowids[ri] : ""}">${r.map((v, ci) => {
      let disp = v === null ? '<span style="color:var(--subtext)">NULL</span>' : esc(String(v));
      return `<td data-col="${ci}" style="padding:5px 8px;border-bottom:1px solid var(--border);font-family:monospace;font-size:11px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${canEdit ? "cursor:cell" : ""}" title="${v === null ? "" : esc(String(v))}">${disp}</td>`;
    }).join("")}${canEdit ? `<td style="padding:2px 6px;border-bottom:1px solid var(--border);white-space:nowrap">
        <button class="taskbar-btn" data-delrow="${rowids[ri]}" title="${t("src_row_delete")}" style="padding:1px 5px;font-size:10px;border-color:var(--danger);color:var(--danger)">🗑</button>
      </td>` : ""}</tr>`).join("");
    resultEl.innerHTML = `<table style="border-collapse:collapse;width:100%;font-size:12px"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;

    if (!canEdit) return;

    // Zeile löschen
    resultEl.querySelectorAll("[data-delrow]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const rid = Number(btn.dataset.delrow);
        const ok = await uiConfirmTwice(t("src_row_del_q", { rid, table: curTable }), {
          okText: t("src_row_delete") });
        if (!ok) return;
        try {
          await api.sourceDbDeleteRow(curTable, rid);
          window.notify?.(t("src_row_deleted"), "success");
          loadTable(curTable); loadTables();
        } catch (e) { window.notify?.(t("src_delete_fail", { err: e.message }), "error"); }
      }));

    // Zelle per RECHTSKLICK löschen (auf NULL setzen) - mit doppelter Nachfrage.
    resultEl.querySelectorAll("td[data-col]").forEach((td) =>
      td.addEventListener("contextmenu", async (e) => {
        e.preventDefault();
        const tr = td.closest("tr");
        const rid = Number(tr.dataset.rowid);
        const colName = columns[Number(td.dataset.col)];
        const ok = await uiConfirmTwice(t("src_cell_del_q", { table: curTable, col: colName, rid }), {
          description: t("src_cell_del_desc"),
          okText: t("src_cell_delete") });
        if (!ok) return;
        try {
          await api.sourceDbSetCell(curTable, rid, colName, null);
          window.notify?.(t("src_cell_deleted"), "success");
          loadTable(curTable);
        } catch (err) { window.notify?.(t("src_delete_fail", { err: err.message }), "error"); }
      }));

    // Zelle per Doppelklick editieren (leerer Wert nach Nachfrage = NULL / Zelle leeren)
    resultEl.querySelectorAll("td[data-col]").forEach((td) =>
      td.addEventListener("dblclick", async () => {
        const tr = td.closest("tr");
        const rid = Number(tr.dataset.rowid);
        const colName = columns[Number(td.dataset.col)];
        const oldVal = td.title;
        const val = await uiPrompt(`"${curTable}"."${colName}" (rowid ${rid}) bearbeiten`, {
          description: t("src_cell_hint"), value: oldVal });
        if (val === null) return;
        let newVal = val;
        if (val === "") {
          const ok = await uiConfirm("Zelle leeren (NULL setzen)?", { okText: "Zelle leeren", danger: true });
          if (!ok) return;
          newVal = null;
        }
        try {
          await api.sourceDbSetCell(curTable, rid, colName, newVal);
          window.notify?.("Zelle gespeichert.", "success");
          loadTable(curTable);
        } catch (e) { window.notify?.(t("src_save_fail", { err: e.message }), "error"); }
      }));
  }

  async function loadTable(name) {
    sqlInput.value = `SELECT * FROM ${name}`;
    try {
      const d = await api.sourceDbTable(name, 300, 0);
      curTable = name;
      tnameEl.textContent = `Tabelle: ${name} (${d.total} Zeilen)`;
      toolbarEl.style.display = "flex";
      renderTable(d.columns, d.rows, d.rowids || null, true);
    } catch (e) { resultEl.innerHTML = `<div style="padding:10px;color:var(--danger)">${esc(e.message)}</div>`; }
  }

  async function runSql() {
    const sql = sqlInput.value.trim();
    if (!sql) return;
    try {
      const d = await api.sourceDbQuery(sql);
      curTable = null; toolbarEl.style.display = "none";
      if (d.kind === "rows") renderTable(d.columns, d.rows);
      else {
        resultEl.innerHTML = `<div style="padding:10px;color:var(--online)">OK — ${d.rowcount} Zeile(n) betroffen.</div>`;
        loadTables();   // Counts / neue Tabellen aktualisieren
      }
    } catch (e) { resultEl.innerHTML = `<div style="padding:10px;color:var(--danger)">${esc(e.message)}</div>`; }
  }
  panel.querySelector("#src-sql-run").addEventListener("click", runSql);
  sqlInput.addEventListener("keydown", (e) => { if (e.key === "Enter") runSql(); });
}

// ---------------------------------------------------------------
// 4) Migration - komplette Instanz auf einen anderen Server umziehen
//
// Der Export nimmt die GESAMTE Datenbank mit (Clients, Benutzer, Rechte,
// Einstellungen, Dashboard-Widgets, Tickets, Skripte, Audit-Log …) sowie die
// Aufzeichnungen, die Medien-Bibliothek und das Branding. Auf dem Zielsystem
// wird dasselbe Archiv wieder eingespielt.
// ---------------------------------------------------------------
async function renderMigrate(host) {
  host.innerHTML = `<div style="padding:8px;color:var(--subtext);font-size:13px">Ermittle Umfang…</div>`;

  let info;
  try {
    info = await api.migrateInfo();
  } catch (e) {
    host.innerHTML = `<div style="padding:8px;color:var(--danger)">${esc(e.message)}</div>`;
    return;
  }

  const mb = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB"
                                  : Math.max(1, Math.round(b / 1024)) + " KB");
  const d = info.dirs || {};

  host.innerHTML = `
    <div style="max-width:760px">

      <h3 style="margin:2px 0 4px;font-size:14px">${t("mg_title")}</h3>
      <p style="color:var(--subtext);font-size:13px">
        ${t("mg_hint", { tables: info.database.tables, rows: info.database.rows.toLocaleString() })}
      </p>

      <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin:10px 0">
        <label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0">
          <input type="checkbox" checked disabled />
          <span>${t("set_db")} <span style="color:var(--subtext)">(${mb(info.database.size)}) – ${t("mg_always")}</span></span>
        </label>
        <label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0;cursor:pointer">
          <input type="checkbox" id="mg-rec" checked />
          <span>${t("mg_recordings")}
            <span style="color:var(--subtext)">(${t("mg_files", { n: d.recordings?.files || 0 })}, ${mb(d.recordings?.size || 0)})</span></span>
        </label>
        <label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0;cursor:pointer">
          <input type="checkbox" id="mg-media" checked />
          <span>${t("mg_media")}
            <span style="color:var(--subtext)">(${t("mg_files", { n: d.media_files?.files || 0 })}, ${mb(d.media_files?.size || 0)})</span></span>
        </label>
        <label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0;cursor:pointer">
          <input type="checkbox" id="mg-brand" checked />
          <span>${t("set_br_title")}
            <span style="color:var(--subtext)">(${t("mg_files", { n: d.branding?.files || 0 })}, ${mb(d.branding?.size || 0)})</span></span>
        </label>
        <label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0;cursor:pointer">
          <input type="checkbox" id="mg-secrets" />
          <span>${t("mg_secrets")}
            ${helpDot(t("mg_secrets_help"))}</span>
        </label>
        <div style="font-size:12px;color:var(--subtext);margin-top:6px">
          ${t("mg_est_size")}: <b id="mg-size">${mb(info.total_size)}</b> (${t("mg_before_zip")})
        </div>
        <button class="btn-primary" id="mg-export" style="width:auto;margin-top:10px">
          ⬇ ${t("mg_export")}
        </button>
        <span id="mg-export-msg" style="margin-left:10px;font-size:12px;color:var(--subtext)"></span>
      </div>

      <h3 style="margin:18px 0 4px;font-size:14px">${t("mg_import_title")}</h3>
      <div style="background:var(--panel-2);border-left:3px solid var(--warn,#f5a524);
                  border-radius:6px;padding:10px;font-size:13px;margin-bottom:10px">
        ${t("mg_import_warn")}
      </div>
      <label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0;cursor:pointer">
        <input type="checkbox" id="mg-in-secrets" />
        <span>${t("mg_in_secrets")}</span>
      </label>
      <label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0;cursor:pointer">
        <input type="checkbox" id="mg-in-backup" checked />
        <span>${t("mg_in_backup")}</span>
      </label>
      <div style="display:flex;gap:8px;align-items:center;margin-top:10px">
        <button class="taskbar-btn" id="mg-pick">📂 ${t("mg_pick")}</button>
        <span id="mg-file" style="font-size:12px;color:var(--subtext)">${t("mg_no_file")}</span>
      </div>
      <div id="mg-bar-box" style="display:none;height:6px;border-radius:3px;background:var(--panel-2);margin-top:10px">
        <div id="mg-bar" style="height:100%;width:0%;background:var(--accent);border-radius:3px"></div>
      </div>
      <button class="btn-primary" id="mg-import" disabled
              style="width:auto;margin-top:10px;background:var(--danger)">
        ⚠ ${t("mg_overwrite")}
      </button>
      <div id="mg-import-msg" style="font-size:12px;color:var(--subtext);margin-top:8px;white-space:pre-wrap"></div>
      <input type="file" id="mg-input" accept=".zip" hidden />
    </div>`;

  // --- Export ---------------------------------------------------------
  const opts = () => ({
    recordings: host.querySelector("#mg-rec").checked,
    media: host.querySelector("#mg-media").checked,
    branding: host.querySelector("#mg-brand").checked,
    secrets: host.querySelector("#mg-secrets").checked,
  });

  // Geschätzte Größe an die Auswahl anpassen.
  const recalc = () => {
    const o = opts();
    let total = info.database.size;
    if (o.recordings) total += d.recordings?.size || 0;
    if (o.media) total += d.media_files?.size || 0;
    if (o.branding) total += d.branding?.size || 0;
    host.querySelector("#mg-size").textContent = mb(total);
  };
  ["#mg-rec", "#mg-media", "#mg-brand"].forEach((id) =>
    host.querySelector(id).addEventListener("change", recalc));

  host.querySelector("#mg-export").addEventListener("click", () => {
    const msg = host.querySelector("#mg-export-msg");
    msg.textContent = t("mg_building");
    // Der Server baut das Archiv erst beim Abruf; bei vielen Aufzeichnungen
    // dauert das eine Weile. Deshalb ein einfacher Link statt fetch:
    // der Browser zeigt den Fortschritt dann selbst an.
    window.location.href = api.migrateExportUrl(opts());
    setTimeout(() => { msg.textContent = ""; }, 20000);
  });

  // --- Import ---------------------------------------------------------
  const fileIn = host.querySelector("#mg-input");
  const importBtn = host.querySelector("#mg-import");
  let chosen = null;

  host.querySelector("#mg-pick").addEventListener("click", () => fileIn.click());
  fileIn.addEventListener("change", () => {
    chosen = fileIn.files[0] || null;
    host.querySelector("#mg-file").textContent = chosen
      ? `${chosen.name} (${mb(chosen.size)})` : t("mg_no_file");
    importBtn.disabled = !chosen;
  });

  importBtn.addEventListener("click", async () => {
    if (!chosen) return;
    const sure = await uiConfirm(
      t("mg_overwrite_q"),
      { description: t("mg_overwrite_desc"),
        okText: t("mg_overwrite_ok"), danger: true });
    if (!sure) return;

    const msg = host.querySelector("#mg-import-msg");
    const box = host.querySelector("#mg-bar-box");
    const bar = host.querySelector("#mg-bar");
    importBtn.disabled = true;
    box.style.display = "";
    msg.textContent = t("mg_uploading");
    try {
      const r = await api.migrateImport(chosen, {
        secrets: host.querySelector("#mg-in-secrets").checked,
        backup: host.querySelector("#mg-in-backup").checked,
      }, (p) => { bar.style.width = (p * 100).toFixed(0) + "%"; });
      const m = r.manifest || {};
      msg.textContent =
        t("mg_done", { list: (r.restored || []).join(", ") }) + "\n"
        + t("mg_source", { date: m.created_iso || "?", version: m.version || "?" }) + "\n"
        + (r.backup ? t("mg_backup_at", { path: r.backup }) + "\n" : "")
        + t("mg_restart_note");
      window.notify?.(t("mg_applied"), "success", 12000);
    } catch (e) {
      msg.textContent = t("mg_failed") + ": " + e.message;
      importBtn.disabled = false;
    }
  });
}


// ---------------------------------------------------------------
// Diagnose / Wartungsmodus
//
// Zweck: Wenn Backend oder Agenten "nach kurzer Zeit" abstürzen, steht die
// Ursache fast nie in der Fehlermeldung. Sie steht in der Kurve davor -
// Speicher, der stetig wächst; Dateideskriptoren, die nicht zurückkommen;
// Hintergrundschleifen, die still sterben. Der Wartungsmodus schreibt genau
// das mit, auf beiden Seiten und in einer gemeinsamen Zeitachse.
//
// Der Ringpuffer läuft IMMER mit, nur das Wegschreiben hängt am Schalter.
// Deshalb sind beim Einschalten die letzten Minuten schon dabei.
// ---------------------------------------------------------------
function renderDiagnostics(host) {
  host.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:12px">
      <div id="dg-state" style="border:1px solid var(--border);border-radius:9px;padding:12px"></div>

      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
        <label style="font-size:12px;color:var(--subtext)">Laufzeit
          <select id="dg-min" style="display:block;margin-top:4px">
            <option value="60">1 Stunde</option>
            <option value="120" selected>2 Stunden</option>
            <option value="480">8 Stunden</option>
            <option value="1440">24 Stunden</option>
            <option value="0">Bis zum Abschalten</option>
          </select>
        </label>
        <label style="font-size:12px;color:var(--subtext);flex:1;min-width:200px">Grund (steht im Log)
          <input id="dg-reason" type="text" placeholder="z.B. Absturz nach ca. 10 min"
                 style="display:block;width:100%;margin-top:4px">
        </label>
        <label style="font-size:12px;display:flex;align-items:center;gap:6px;padding-bottom:6px">
          <input type="checkbox" id="dg-agents" checked> Agenten mitschreiben
        </label>
        <button class="btn-primary" id="dg-toggle" style="margin:0">Einschalten</button>
        <button class="taskbar-btn" id="dg-bundle">⬇️ Diagnosepaket</button>
        <button class="taskbar-btn" id="dg-clear">🗑️ Logs leeren</button>
      </div>

      <div id="dg-errors" style="border:1px solid var(--border);border-radius:9px;padding:10px"></div>
      <div id="dg-metrics" style="border:1px solid var(--border);border-radius:9px;padding:10px"></div>

      <div style="display:flex;align-items:center;gap:8px">
        <b style="font-size:13px">Live-Ausgabe</b>
        <label style="font-size:12px;display:flex;align-items:center;gap:5px;margin-left:auto">
          <input type="checkbox" id="dg-follow" checked> mitlaufen
        </label>
        <button class="taskbar-btn" id="dg-refresh">⟳</button>
      </div>
      <pre id="dg-log" style="flex:1;min-height:280px;max-height:46vh;overflow:auto;margin:0;
        background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:10px;
        font-family:ui-monospace,monospace;font-size:11.5px;white-space:pre-wrap"></pre>
    </div>`;

  const $ = (id) => host.querySelector(id);
  let active = false;
  let timer = null;

  function fmtBytes(n) {
    n = Number(n) || 0;
    const u = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
  }

  async function loadState() {
    let st;
    try { st = await api.diagStatus(); }
    catch (e) {
      $("#dg-state").innerHTML = `<span style="color:var(--danger,#ff4d6d)">${esc(e.message)}</span>`;
      return;
    }
    active = !!st.active;
    $("#dg-toggle").textContent = active ? "Ausschalten" : "Einschalten";
    $("#dg-agents").checked = st.agents_included !== false;

    const c = st.counters || {};
    $("#dg-state").innerHTML = `
      <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap">
        <span style="font-size:15px">${active ? "🟢" : "⚪"}</span>
        <b>${active ? "Wartungsmodus läuft" : "Wartungsmodus aus"}</b>
        ${active && st.until ? `<span style="color:var(--subtext);font-size:12px">
          bis ${new Date(st.until).toLocaleString()}</span>` : ""}
        <span style="margin-left:auto;font-size:12px;color:var(--subtext)">
          Fehler: <b>${c.errors || 0}</b> · Warnungen: <b>${c.warnings || 0}</b>
          · Agentenfehler: <b>${c.agent_errors || 0}</b>
        </span>
      </div>
      <div style="margin-top:7px;font-size:12px;padding:6px 9px;border-radius:7px;
           background:${(st.loop_dumps || 0) > 0 ? "#ff4d6d18" : "var(--panel-2)"}">
        Ereignisschleife: längste Blockade <b>${(st.loop_worst_s ?? 0).toFixed
          ? st.loop_worst_s.toFixed(1) : st.loop_worst_s || 0}s</b>
        · Stack-Abzüge: <b>${st.loop_dumps || 0}</b>
        ${(st.loop_dumps || 0) > 0
          ? `<span style="color:var(--danger,#ff4d6d)"> – siehe
             backend-blockaden.log im Diagnosepaket, dort steht die
             blockierende Zeile.</span>`
          : `<span style="color:var(--subtext)"> – alles flüssig.</span>`}
      </div>
      <div style="font-size:11.5px;color:var(--subtext);margin-top:6px">
        Verzeichnis: ${esc(st.dir || "")}
        ${(st.files || []).length
          ? "· " + st.files.map((f) => `${esc(f.name)} (${fmtBytes(f.size)})`).join(" · ")
          : "· noch keine Dateien"}
      </div>`;

    renderMetrics(st.samples || []);
    renderErrors(st.error_codes || {});
  }

  // Fehler nach Kenncode. Das ist die Ansicht, die man zuerst braucht:
  // Ein Blick sagt, WELCHE Art Fehler auftritt und wie oft - ohne dass
  // man erst hunderte Protokollzeilen durchsehen muss.
  function renderErrors(summary) {
    const box = $("#dg-errors");
    const counters = summary.counters || {};
    const codes = Object.entries(counters);
    if (!codes.length) {
      box.innerHTML = `<span style="color:var(--online,#3ecf8e);font-size:12.5px">
        ✓ Keine Fehler gemeldet.</span>`;
      return;
    }
    const rows = codes.map(([code, n]) => {
      const last = (summary.recent || []).filter((r) => r.code === code).pop();
      return `<tr>
        <td style="padding:2px 10px 2px 0;font-family:ui-monospace,monospace">
          <b>${esc(code)}</b></td>
        <td style="padding:2px 10px 2px 0;text-align:right">${n}×</td>
        <td style="padding:2px 0;color:var(--subtext)">
          ${esc(last ? (last.doing || "") + (last.error ? " – " + last.error : "") : "")}
        </td></tr>`;
    }).join("");
    box.innerHTML = `
      <div style="font-size:12px;color:var(--subtext);margin-bottom:5px">
        Fehler nach Kenncode (insgesamt ${summary.total || 0}) – nach dem Code
        lässt sich im Protokoll und in <code>docker logs</code> suchen.
      </div>
      <table style="font-size:12px;border-collapse:collapse;width:100%">${rows}</table>`;
  }

  // Die Messwerte sind das eigentliche Werkzeug: Eine Kurve, die stetig
  // steigt und nie zurückkommt, zeigt das Leck deutlicher als jeder
  // Stacktrace. Deshalb hier Anfang und Ende nebeneinander.
  function renderMetrics(samples) {
    const box = $("#dg-metrics");
    if (!samples.length) {
      box.innerHTML = `<span style="color:var(--subtext);font-size:12px">
        Noch keine Messpunkte – der erste kommt nach wenigen Sekunden.</span>`;
      return;
    }
    const first = samples[0];
    const last = samples[samples.length - 1];
    const keys = [
      ["rss_mb", "Speicher (MB)"], ["fds", "Dateideskriptoren"],
      ["threads", "Threads"], ["tasks", "asyncio-Aufgaben"],
      ["conns", "Sockets"], ["agents", "Agenten"],
      ["pending", "offene Anfragen"], ["tunnels", "VPN-Tunnel"],
      ["inflight", "Anfragen in Arbeit"], ["lag_max", "größte Loop-Verzög. (s)"],
    ];
    const rows = keys.filter(([k]) => last[k] !== undefined).map(([k, label]) => {
      const a = Number(first[k] ?? 0), b = Number(last[k] ?? 0);
      const grow = b - a;
      // Deutlich markieren, was wächst - das ist der Hinweis auf ein Leck.
      const color = grow > 0 && a > 0 && b > a * 1.5
        ? "var(--danger,#ff4d6d)" : grow > 0 ? "var(--warn,#f5a524)" : "var(--subtext)";
      return `<tr>
        <td style="padding:2px 10px 2px 0">${label}</td>
        <td style="padding:2px 10px 2px 0;text-align:right">${a}</td>
        <td style="padding:2px 10px 2px 0;text-align:right"><b>${b}</b></td>
        <td style="padding:2px 0;text-align:right;color:${color}">
          ${grow > 0 ? "+" : ""}${grow}</td></tr>`;
    }).join("");
    box.innerHTML = `
      <div style="font-size:12px;color:var(--subtext);margin-bottom:5px">
        Verlauf über ${Math.round((last.t - first.t) / 60000)} Minuten
        (${samples.length} Messpunkte) – stetig steigende Werte deuten auf ein Leck.
      </div>
      <table style="font-size:12px;border-collapse:collapse">
        <tr style="color:var(--subtext)"><td></td><td style="text-align:right">Start</td>
        <td style="text-align:right">jetzt</td><td style="text-align:right">Δ</td></tr>
        ${rows}
      </table>`;
  }

  async function loadLog() {
    try {
      const text = await api.diagTail(400);
      const pre = $("#dg-log");
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 40;
      pre.textContent = text || "(noch nichts)";
      if ($("#dg-follow").checked && atBottom) pre.scrollTop = pre.scrollHeight;
    } catch { /* Log-Abruf darf die Ansicht nicht stören */ }
  }

  $("#dg-toggle").addEventListener("click", async () => {
    $("#dg-toggle").disabled = true;
    try {
      if (active) await api.diagDisable();
      else await api.diagEnable(parseInt($("#dg-min").value, 10) || 0,
                                $("#dg-reason").value, $("#dg-agents").checked);
      await loadState();
    } catch (e) {
      window.notify?.(e.message, "error");
    } finally { $("#dg-toggle").disabled = false; }
  });

  $("#dg-bundle").addEventListener("click", async () => {
    // Über fetch statt Direktlink, weil der Download den Auth-Header braucht.
    try {
      const res = await fetch(api.diagBundleUrl(), {
        headers: { Authorization: `Bearer ${getToken() || ""}` },
      });
      if (!res.ok) throw new Error(`Fehler ${res.status}`);
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `rmm-diagnose-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "")}.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 8000);
    } catch (e) {
      window.notify?.("Download fehlgeschlagen: " + e.message, "error");
    }
  });

  $("#dg-clear").addEventListener("click", async () => {
    if (!(await uiConfirm("Alle Diagnose-Logs löschen?",
      { description: "Bereits heruntergeladene Pakete bleiben erhalten." }))) return;
    try { await api.diagClear(); await loadState(); await loadLog(); }
    catch (e) { window.notify?.(e.message, "error"); }
  });

  $("#dg-refresh").addEventListener("click", () => { loadState(); loadLog(); });

  loadState();
  loadLog();
  timer = setInterval(() => { loadState(); loadLog(); }, 5000);
  return () => clearInterval(timer);
}

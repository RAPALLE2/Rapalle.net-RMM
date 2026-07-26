// apps/source.js
// --------------
// Admin-"Source"-Werkzeuge (nur Super-Admin). Drei Bereiche:
//   - Shell:      Live-Konsole auf dem Backend-Host (PTY über Socket.IO).
//   - Explorer:   Dateibaum über backend/frontend/agent inkl. Datei-Editor.
//   - Datenbank:  Tabellen ansehen + beliebiges SQL (inkl. Key/Value & Arrays).
//
// Wird vom Settings-Tab "Source" aufgerufen: renderSource(container).

import { api, getToken } from "../api.js";
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
      description: "Das Dashboard trennt sich kurz und verbindet sich danach automatisch wieder.",
      okText: "Neu starten", danger: true,
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
      <span id="src-zip-status" style="font-size:12px;color:var(--subtext)"></span>
    </div>
    <div style="display:flex;gap:10px;min-height:0">
      <div style="flex:0 0 300px;border:1px solid var(--border);border-radius:8px;overflow:auto;max-height:440px">
        <div id="src-path" style="padding:6px 8px;border-bottom:1px solid var(--border);font-size:11px;color:var(--subtext);word-break:break-all"></div>
        <div id="src-entries"></div>
      </div>
      <div style="flex:1;min-width:0;border:1px solid var(--border);border-radius:8px;display:flex;flex-direction:column;max-height:440px">
        <div id="src-file-bar" style="padding:6px 8px;border-bottom:1px solid var(--border);font-size:12px;display:flex;align-items:center;gap:8px">
          <span id="src-file-name" style="flex:1;color:var(--subtext)">Keine Datei geöffnet</span>
          <button class="btn-primary" id="src-file-save" style="width:auto;margin:0;display:none">Speichern</button>
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

  // --- ZIP-Upload ---
  const zipInput = panel.querySelector("#src-zip-input");
  const zipStatus = panel.querySelector("#src-zip-status");
  panel.querySelector("#src-zip-btn").addEventListener("click", () => zipInput.click());
  zipInput.addEventListener("change", async () => {
    const f = zipInput.files && zipInput.files[0];
    if (!f) return;
    zipStatus.textContent = `Extrahiere ${f.name}…`;
    try {
      const res = await api.sourceUploadZip(f, curDir);
      const where = res.project_update ? "Projekt-Root" : ("/" + (res.dest || ""));
      zipStatus.textContent = `${res.count} Datei(en) nach ${where} extrahiert.`;
      window.notify?.(`${res.count} Datei(en) aus ${f.name} nach ${where} extrahiert${res.skipped?.length ? ` (${res.skipped.length} übersprungen)` : ""}.`, "success", 8000);
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
            ? await uiConfirmTwice(`Ordner "/${target}" löschen?`, {
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
      if (f.too_large) { contentEl.value = `[Datei zu groß: ${formatBytes(f.size)}]`; saveBtn.style.display = "none"; }
      else if (f.binary) { contentEl.value = "[Binärdatei — nicht anzeigbar]"; saveBtn.style.display = "none"; }
      else { contentEl.value = f.content; saveBtn.style.display = ""; }
    } catch (e) {
      nameEl.textContent = "Fehler: " + e.message;
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
      <button class="taskbar-btn" id="src-db-backup" title="Konsistente Kopie der Datenbank als backend/data.sqlite.bak erstellen">💾 Backup (data.sqlite.bak)</button>
      <span style="font-size:11px;color:var(--subtext)">Doppelklick = Zelle editieren · Rechtsklick = Zelle löschen (NULL) · Buttons je Zeile/Tabelle</span>
    </div>
    <div style="display:flex;gap:10px;min-height:0">
      <div style="flex:0 0 220px;border:1px solid var(--border);border-radius:8px;overflow:auto;max-height:440px">
        <div style="padding:6px 8px;border-bottom:1px solid var(--border);font-size:11px;color:var(--subtext)">Tabellen</div>
        <div id="src-tables"></div>
      </div>
      <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:8px">
        <div style="display:flex;gap:6px">
          <input id="src-sql" placeholder="${t("src_sql_ph")}"
            style="flex:1;padding:7px 10px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-family:monospace;font-size:12px" />
          <button class="btn-primary" id="src-sql-run" style="width:auto;margin:0">Ausführen</button>
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
    const ok = await uiConfirmTwice(`Tabelle "${curTable}" komplett löschen?`, {
      description: t("src_table_drop_desc"),
      okText: t("src_table_drop"),
      secondTitle: `"${curTable}" endgültig löschen?`,
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
        const ok = await uiConfirmTwice(`Zeile (rowid ${rid}) aus "${curTable}" löschen?`, {
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
        const ok = await uiConfirmTwice(`Zelle "${curTable}"."${colName}" (rowid ${rid}) löschen?`, {
          description: t("src_cell_del_desc"),
          okText: t("src_cell_delete") });
        if (!ok) return;
        try {
          await api.sourceDbSetCell(curTable, rid, colName, null);
          window.notify?.(t("src_cell_deleted"), "success");
          loadTable(curTable);
        } catch (err) { window.notify?.("Löschen fehlgeschlagen: " + err.message, "error"); }
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

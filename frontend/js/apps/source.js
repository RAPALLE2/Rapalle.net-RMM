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
import { esc, formatBytes } from "../utils.js";
import { dashboardSocket } from "../socket.js";
import { MiniTerm } from "./miniterm.js";

export function renderSource(container) {
  container.innerHTML = `
    <div style="display:flex;gap:6px;margin-bottom:10px">
      <button class="tab-btn active" data-src="log">🖥️ Backend-Ausgabe</button>
      <button class="tab-btn" data-src="explorer">📁 Explorer</button>
      <button class="tab-btn" data-src="db">🗄️ Datenbank</button>
    </div>
    <div id="src-panel" style="min-height:420px"></div>
  `;
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
        <button class="taskbar-btn" id="src-log-clear">🗑 Leeren</button>
        <button class="taskbar-btn" id="src-log-restart" style="border-color:var(--warn);color:var(--warn)">↻ Backend neu starten</button>
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
  const onHistory = (p) => {
    gotHistory = true;
    term.write(p.data || "");
    statusEl.textContent = "Verbunden — Live-Ausgabe.";
  };
  const onLine = (p) => { term.write(p.data || ""); };
  dashboardSocket.on("backend-log-history", onHistory);
  dashboardSocket.on("backend-log", onLine);
  dashboardSocket.emit("backend-log-open", { token: getToken() });

  setTimeout(() => {
    if (!gotHistory) {
      term.write("\x1b[31m[Backend antwortet nicht auf die Log-Anbindung]\x1b[0m\r\n" +
        "Das laufende Backend kennt die Source-Ausgabe noch nicht -> Backend mit aktueller Version NEU STARTEN.\r\n");
      statusEl.textContent = "Backend veraltet / nicht neu gestartet.";
    }
  }, 3000);

  panel.querySelector("#src-log-clear").addEventListener("click", () => { term.write("\x1b[2J\x1b[H"); });
  panel.querySelector("#src-log-restart").addEventListener("click", async () => {
    if (!confirm("Backend wirklich neu starten?\n\nDas Dashboard trennt sich kurz und verbindet sich danach automatisch wieder.")) return;
    try {
      await api.restartBackend();
      window.notify?.("Backend startet neu… Die Seite lädt in ein paar Sekunden neu.", "info", 12000);
      setTimeout(() => window.location.reload(), 6000);
    } catch (e) { window.notify?.("Neustart fehlgeschlagen: " + e.message, "error"); }
  });

  return () => {
    dashboardSocket.off("backend-log-history", onHistory);
    dashboardSocket.off("backend-log", onLine);
    dashboardSocket.emit("backend-log-close", {});
    try { ro.disconnect(); } catch {}
  };
}

// ---------------------------------------------------------------
// 2) Datei-Explorer
// ---------------------------------------------------------------
async function renderExplorer(panel) {
  panel.innerHTML = `
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px" id="src-roots"></div>
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
          style="flex:1;min-height:360px;border:0;background:#0b0f14;color:#e6edf3;font-family:monospace;font-size:12px;padding:10px;resize:none;outline:none" placeholder="Datei links auswählen…"></textarea>
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
      pathEl.textContent = "/" + (d.path || "");
      const up = d.path ? `<div class="src-row" data-dir="${esc(d.parent)}" style="padding:6px 8px;cursor:pointer;font-size:12px">📁 ..</div>` : "";
      entriesEl.innerHTML = up + d.entries.map((e) => {
        const icon = e.type === "dir" ? "📁" : "📄";
        const sub = e.type === "file" ? `<span style="color:var(--subtext);font-size:10px;float:right">${formatBytes(e.size)}</span>` : "";
        const attr = e.type === "dir" ? `data-dir="${esc(joinPath(d.path, e.name))}"` : `data-file="${esc(joinPath(d.path, e.name))}"`;
        return `<div class="src-row" ${attr} style="padding:6px 8px;cursor:pointer;font-size:12px;border-top:1px solid var(--border)">${icon} ${esc(e.name)} ${sub}</div>`;
      }).join("");
      entriesEl.querySelectorAll("[data-dir]").forEach((el) =>
        el.addEventListener("click", () => loadDir(el.dataset.dir)));
      entriesEl.querySelectorAll("[data-file]").forEach((el) =>
        el.addEventListener("click", () => openFile(el.dataset.file)));
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
      window.notify?.("Datei gespeichert: /" + openPath, "success");
    } catch (e) { window.notify?.("Speichern fehlgeschlagen: " + e.message, "error"); }
  });

  loadDir("backend");
}

function joinPath(dir, name) { return dir ? `${dir}/${name}` : name; }

// ---------------------------------------------------------------
// 3) Datenbank
// ---------------------------------------------------------------
async function renderDb(panel) {
  panel.innerHTML = `
    <div style="display:flex;gap:10px;min-height:0">
      <div style="flex:0 0 220px;border:1px solid var(--border);border-radius:8px;overflow:auto;max-height:440px">
        <div style="padding:6px 8px;border-bottom:1px solid var(--border);font-size:11px;color:var(--subtext)">Tabellen</div>
        <div id="src-tables"></div>
      </div>
      <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:8px">
        <div style="display:flex;gap:6px">
          <input id="src-sql" placeholder="SQL ausführen, z.B. SELECT * FROM settings"
            style="flex:1;padding:7px 10px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-family:monospace;font-size:12px" />
          <button class="btn-primary" id="src-sql-run" style="width:auto;margin:0">Ausführen</button>
        </div>
        <div id="src-db-result" style="border:1px solid var(--border);border-radius:8px;overflow:auto;max-height:400px"></div>
      </div>
    </div>
  `;
  const tablesEl = panel.querySelector("#src-tables");
  const resultEl = panel.querySelector("#src-db-result");
  const sqlInput = panel.querySelector("#src-sql");

  try {
    const t = await api.sourceDbTables();
    tablesEl.innerHTML = t.tables.map((x) =>
      `<div class="src-row" data-table="${esc(x.name)}" style="padding:6px 8px;cursor:pointer;font-size:12px;border-top:1px solid var(--border);display:flex;justify-content:space-between">
        <span>${esc(x.name)}</span><span style="color:var(--subtext)">${x.count ?? "?"}</span></div>`).join("");
    tablesEl.querySelectorAll("[data-table]").forEach((el) =>
      el.addEventListener("click", () => loadTable(el.dataset.table)));
  } catch (e) {
    tablesEl.innerHTML = `<div style="padding:8px;color:var(--danger)">${esc(e.message)}</div>`;
  }

  function renderTable(columns, rows) {
    if (!rows.length) { resultEl.innerHTML = `<div style="padding:10px;color:var(--subtext)">Keine Zeilen.</div>`; return; }
    const head = columns.map((c) => `<th style="text-align:left;padding:6px 8px;position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--border)">${esc(c)}</th>`).join("");
    const body = rows.map((r) => `<tr>${r.map((v) => {
      let disp = v === null ? '<span style="color:var(--subtext)">NULL</span>' : esc(String(v));
      return `<td style="padding:5px 8px;border-bottom:1px solid var(--border);font-family:monospace;font-size:11px;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${v === null ? "" : esc(String(v))}">${disp}</td>`;
    }).join("")}</tr>`).join("");
    resultEl.innerHTML = `<table style="border-collapse:collapse;width:100%;font-size:12px"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  async function loadTable(name) {
    sqlInput.value = `SELECT * FROM ${name}`;
    try {
      const d = await api.sourceDbTable(name, 300, 0);
      renderTable(d.columns, d.rows);
    } catch (e) { resultEl.innerHTML = `<div style="padding:10px;color:var(--danger)">${esc(e.message)}</div>`; }
  }

  async function runSql() {
    const sql = sqlInput.value.trim();
    if (!sql) return;
    try {
      const d = await api.sourceDbQuery(sql);
      if (d.kind === "rows") renderTable(d.columns, d.rows);
      else { resultEl.innerHTML = `<div style="padding:10px;color:var(--online)">OK — ${d.rowcount} Zeile(n) betroffen.</div>`;
             // Tabellenliste evtl. aktualisieren (Counts)
           }
    } catch (e) { resultEl.innerHTML = `<div style="padding:10px;color:var(--danger)">${esc(e.message)}</div>`; }
  }
  panel.querySelector("#src-sql-run").addEventListener("click", runSql);
  sqlInput.addEventListener("keydown", (e) => { if (e.key === "Enter") runSql(); });
}

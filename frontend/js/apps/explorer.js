// apps/explorer.js
// ----------------
// Vollwertiger Datei-Explorer. Läuft gegen einen Remote-Client (props.clientId)
// oder gegen den Backend-Server selbst (kein clientId).
//
// Funktionen:
//   - Doppelklick auf Ordner: hineinnavigieren
//   - Breadcrumb-Pfad oben: einzelne Ebenen direkt anspringen
//   - Zurück-Button + Aktualisieren
//   - Doppelklick auf Datei (nur bei Remote-Client): herunterladen
//   - Dateigröße + Änderungsdatum

import { api } from "../api.js";
import { formatBytes, esc } from "../utils.js";

export function renderExplorer(body, win) {
  const { clientId, clientName } = win.props;
  const history = [""]; // Navigations-Historie für den Zurück-Button

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar">
        <button id="exp-back-${win.key}">←</button>
        <button id="exp-refresh-${win.key}">⟳</button>
        <div id="exp-crumbs-${win.key}" style="flex:1;color:var(--subtext);overflow-x:auto;white-space:nowrap"></div>
      </div>
      <div style="flex:1;overflow:auto">
        <table class="explorer-table">
          <thead>
            <tr><th>Name</th><th style="width:110px">Größe</th><th style="width:170px">Geändert</th></tr>
          </thead>
          <tbody id="exp-tbody-${win.key}"></tbody>
        </table>
      </div>
    </div>
  `;

  const tbody = body.querySelector(`#exp-tbody-${win.key}`);
  const crumbs = body.querySelector(`#exp-crumbs-${win.key}`);
  const backBtn = body.querySelector(`#exp-back-${win.key}`);
  const refreshBtn = body.querySelector(`#exp-refresh-${win.key}`);

  function renderCrumbs(path) {
    const where = clientId ? clientName : "Server";
    if (!path) {
      crumbs.innerHTML = `<b>${esc(where)}</b> : Laufwerke`;
      return;
    }
    const sep = path.includes("\\") ? "\\" : "/";
    const parts = path.split(sep).filter(Boolean);
    crumbs.innerHTML = `<b>${esc(where)}</b> : ` + parts.map((p) => esc(p)).join(" › ");
  }

  async function downloadFile(entry) {
    if (!clientId) {
      alert("Download ist nur für Remote-Clients verfügbar.");
      return;
    }
    try {
      const res = await api.readClientFile(clientId, entry.path);
      // Base64 -> Blob -> Download auslösen
      const bytes = Uint8Array.from(atob(res.data), (c) => c.charCodeAt(0));
      const blob = new Blob([bytes]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = res.name || entry.name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert("Download fehlgeschlagen: " + e.message);
    }
  }

  async function load(path) {
    tbody.innerHTML = `<tr><td colspan="3" style="color:var(--subtext)">Lädt...</td></tr>`;
    try {
      const res = clientId
        ? await api.listClientFs(clientId, path)
        : await api.listServerFs(path);

      renderCrumbs(res.path);
      tbody.innerHTML = "";

      if (!res.entries.length) {
        tbody.innerHTML = `<tr><td colspan="3" style="color:var(--subtext)">Leerer Ordner</td></tr>`;
      }

      for (const entry of res.entries) {
        const tr = document.createElement("tr");
        const icon = entry.isDir ? "📁" : "📄";
        const size = entry.isDir ? "" : formatBytes(entry.size);
        const mtime = entry.mtime ? new Date(entry.mtime).toLocaleString("de-DE") : "";
        tr.innerHTML = `<td>${icon} ${esc(entry.name)}</td><td>${size}</td><td style="color:var(--subtext)">${mtime}</td>`;
        tr.style.cursor = "pointer";

        if (entry.isDir) {
          tr.title = "Doppelklick: öffnen";
          tr.addEventListener("dblclick", () => { history.push(entry.path); load(entry.path); });
        } else {
          tr.title = "Doppelklick: herunterladen";
          tr.addEventListener("dblclick", () => downloadFile(entry));
        }
        tbody.appendChild(tr);
      }
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="3" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  }

  backBtn.addEventListener("click", () => {
    if (history.length > 1) { history.pop(); load(history[history.length - 1]); }
  });
  refreshBtn.addEventListener("click", () => load(history[history.length - 1]));

  load(""); // Startansicht: Laufwerke (Windows) bzw. Root/Home (Linux)
}

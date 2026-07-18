// apps/taskmanager.js
// -------------------
// Vollwertiger Remote-Task-Manager. Holt die strukturierte Prozessliste vom
// Agenten (über psutil: PID, Name, Benutzer, CPU%, RAM%) und zeigt sie
// sortierbar an. Ein "Beenden"-Button beendet den gewählten Prozess.
// Aktualisiert sich automatisch alle 3 Sekunden.

import { api } from "../api.js";
import { registerCleanup } from "../windowmanager.js";
import { esc, uiConfirm } from "../utils.js";

export function renderTaskManager(body, win) {
  const { clientId, clientName } = win.props;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar" style="flex-wrap:wrap;gap:8px">
        <input type="text" id="tm-search-${win.key}" placeholder="🔍 Prozess suchen..."
          style="flex:1;min-width:140px;padding:6px 10px;border-radius:7px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:13px" />
        <div style="display:flex;gap:4px;align-items:center">
          <span style="color:var(--subtext);font-size:12px">Sortieren:</span>
          <button class="tm-sort-btn" data-sortbtn="cpu" id="tm-sb-cpu-${win.key}">CPU</button>
          <button class="tm-sort-btn" data-sortbtn="mem" id="tm-sb-mem-${win.key}">RAM</button>
          <button class="tm-sort-btn" data-sortbtn="name" id="tm-sb-name-${win.key}">Name</button>
        </div>
        <label style="color:var(--subtext);font-size:12px;display:flex;align-items:center;gap:4px">
          <input type="checkbox" id="tm-auto-${win.key}" checked /> Auto
        </label>
        <button id="tm-refresh-${win.key}">🔄</button>
      </div>
      <div style="padding:6px 10px;font-size:12px;color:var(--subtext)" id="tm-summary-${win.key}"></div>
      <div style="flex:1;overflow:auto">
        <table class="data-table">
          <thead>
            <tr>
              <th data-sort="name" style="cursor:pointer">Prozess</th>
              <th data-sort="pid" style="width:80px;cursor:pointer">PID</th>
              <th data-sort="cpu" style="width:80px;cursor:pointer">CPU %</th>
              <th data-sort="mem" style="width:80px;cursor:pointer">RAM %</th>
              <th style="width:80px"></th>
            </tr>
          </thead>
          <tbody id="tm-body-${win.key}"><tr><td colspan="5" style="color:var(--subtext)">Lädt...</td></tr></tbody>
        </table>
      </div>
    </div>
  `;

  const tbody = body.querySelector(`#tm-body-${win.key}`);
  const summary = body.querySelector(`#tm-summary-${win.key}`);
  const refreshBtn = body.querySelector(`#tm-refresh-${win.key}`);
  const autoToggle = body.querySelector(`#tm-auto-${win.key}`);
  const searchInput = body.querySelector(`#tm-search-${win.key}`);

  let sortKey = "mem";
  let sortDesc = true;
  let processes = [];

  function renderTable() {
    // Suchfilter anwenden (Prozessname, case-insensitive)
    const query = (searchInput.value || "").toLowerCase().trim();
    let filtered = processes;
    if (query) {
      filtered = processes.filter((p) =>
        (p.name || "").toLowerCase().includes(query) ||
        (p.username || "").toLowerCase().includes(query) ||
        String(p.pid).includes(query)
      );
    }

    const sorted = [...filtered].sort((a, b) => {
      let av = a[sortKey], bv = b[sortKey];
      // Name immer alphabetisch (aufsteigend, wenn sortDesc=false)
      if (typeof av === "string") { av = av.toLowerCase(); bv = (bv || "").toLowerCase(); }
      if (av < bv) return sortDesc ? 1 : -1;
      if (av > bv) return sortDesc ? -1 : 1;
      return 0;
    });

    tbody.innerHTML = sorted.map((p) => `
      <tr>
        <td title="${esc(p.username)}">${esc(p.name)}</td>
        <td>${p.pid}</td>
        <td>${p.cpu?.toFixed ? p.cpu.toFixed(1) : p.cpu}</td>
        <td>${p.mem}</td>
        <td><button class="taskbar-btn" data-kill="${p.pid}" data-name="${esc(p.name)}">Beenden</button></td>
      </tr>
    `).join("") || `<tr><td colspan="5" style="color:var(--subtext)">Keine Treffer.</td></tr>`;

    summary.textContent = query
      ? `${sorted.length} von ${processes.length} Prozessen (gefiltert)`
      : `${processes.length} Prozesse · sortiert nach ${sortKey.toUpperCase()} ${sortDesc ? "↓" : "↑"}`;

    // aktiven Sortier-Button hervorheben
    body.querySelectorAll(".tm-sort-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.sortbtn === sortKey)
    );

    tbody.querySelectorAll("[data-kill]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!(await uiConfirm(`Prozess "${btn.dataset.name}" (PID ${btn.dataset.kill}) beenden?`, { okText: "Beenden", danger: true }))) return;
        try {
          await api.killProcess(clientId, parseInt(btn.dataset.kill));
          load();
        } catch (e) { window.notify?.("Fehler: " + e.message, "error"); }
      })
    );
  }

  async function load() {
    try {
      const res = await api.listProcesses(clientId);
      processes = res.processes;
      renderTable();
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="5" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  }

  // Spalten-Sortierung per Klick auf die Kopfzeile
  body.querySelectorAll("[data-sort]").forEach((th) =>
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (sortKey === key) sortDesc = !sortDesc;
      else { sortKey = key; sortDesc = key !== "name"; } // Name standardmäßig A-Z
      renderTable();
    })
  );

  // Sortier-Buttons in der Toolbar (CPU / RAM / Name)
  body.querySelectorAll(".tm-sort-btn").forEach((btn) =>
    btn.addEventListener("click", () => {
      const key = btn.dataset.sortbtn;
      if (sortKey === key) sortDesc = !sortDesc;
      else { sortKey = key; sortDesc = key !== "name"; }
      renderTable();
    })
  );

  // Suchfeld: filtert live (kein Reload nötig, arbeitet auf den geladenen Daten)
  searchInput.addEventListener("input", renderTable);

  refreshBtn.addEventListener("click", load);

  // Automatische Aktualisierung alle 3 Sekunden
  const interval = setInterval(() => {
    if (autoToggle.checked) load();
  }, 3000);
  registerCleanup(win.key, () => clearInterval(interval));

  load();
}

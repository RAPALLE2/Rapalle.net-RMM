// apps/audit.js
// -------------
// Zeigt das Audit-Log (Aktivitätsprotokoll): Logins, Terminal-Befehle,
// Datei-Downloads, Client-/Benutzer-Änderungen, Screen-Sessions u.v.m.
// Bei Screen-Sessions gibt es einen Button, der direkt zur Aufzeichnung führt.

import { api } from "../api.js";
import { esc } from "../utils.js";
import { openWindow } from "../windowmanager.js";
import { t } from "../i18n.js";

// Aktionen menschenlesbar machen (mit Emoji für schnelle Orientierung)
const ACTION_LABELS = {
  "settings.default_layout_set": "🗂️ Standard-Layout gesetzt",
  "settings.default_layout_cleared": "🗂️ Standard-Layout entfernt",
  "login.success": "🔓 Login erfolgreich",
  "login.failed": "⛔ Login fehlgeschlagen",
  "password.changed": "🔑 Passwort geändert",
  "file.download": "⬇️ Datei heruntergeladen",
  "terminal.exec": "⌨️ Terminal-Befehl",
  "terminal.session": "⌨️ Terminal-Sitzung",
  "terminal.bulk_exec": "⚡ Bulk-Befehl",
  "process.kill": "❌ Prozess beendet",
  "screen.session_started": "🖥️ Remote-Session gestartet",
  "recording.deleted": "🗑️ Aufzeichnung gelöscht",
  "client.updated": "✏️ Client geändert",
  "client.deleted": "🗑️ Client gelöscht",
  "user.created": "👤 Benutzer angelegt",
  "user.deleted": "👤 Benutzer gelöscht",
  "group.created": "👥 Gruppe angelegt",
  "group.updated": "👥 Gruppe geändert",
  "group.deleted": "👥 Gruppe gelöscht",
  "webhook.created": "🔔 Webhook angelegt",
  "webhook.deleted": "🔔 Webhook gelöscht",
  "realm.created": "🏢 Realm angelegt",
  "realm.deleted": "🏢 Realm gelöscht",
  "automation.created": "🔁 Automation angelegt",
  "automation.executed": "🔁 Automation ausgeführt",
  "automation.deleted": "🔁 Automation gelöscht",
  "guac.connect": "🕹️ Remote-Sitzung (Guacamole)",
  "guac.recording": "⏺️ Guacamole-Replay",
  "error.reported": "🔴 Fehler gemeldet",
  "error.warn": "🟠 Warnung",
  "agent.update_triggered": "⬆️ Agent-Update ausgelöst",
  "agent.update_all_triggered": "⬆️ Agent-Update für ALLE Clients",
  "agent.update_all_completed": "✅ Agent-Update (alle) abgeschlossen",
  "relay.auto_closed": "🔌 Relay automatisch geschlossen",
  "backend.crash": "💥 Backend abgestürzt",
  "backend.restarted": "♻️ Backend neu gestartet",
  "frontend.crash_recovered": "💥 Frontend-Absturz erkannt",
  "rdp.file_generated": "🖥️ RDP-Datei erzeugt",
  "script.created": "📜 Skript angelegt",
  "script.deleted": "📜 Skript gelöscht",
  "tenant.created": "🏢 Tenant angelegt",
  "location.created": "📍 Standort angelegt",
  "folder.created": "📁 Ordner angelegt",
};

export function renderAudit(body, win) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar" style="flex-wrap:wrap;gap:8px">
        <span style="color:var(--subtext)">Aktivitätsprotokoll (letzte 200, 30 Tage)</span>
        <select id="au-filter-user-${win.key}" style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
          <option value="">Alle Benutzer</option>
        </select>
        <select id="au-filter-kind-${win.key}" style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
          <option value="">Alle Aktionen</option>
        </select>
        <span style="flex:1"></span>
        <button id="au-refresh-${win.key}">🔄</button>
      </div>
      <div style="flex:1;overflow:auto">
        <table class="data-table">
          <thead><tr>
            <th style="width:150px;cursor:pointer" data-sort="ts">${t("audit_when")} ⇅</th>
            <th style="cursor:pointer" data-sort="username">${t("audit_user")} ⇅</th>
            <th style="cursor:pointer" data-sort="action">${t("audit_action")} ⇅</th>
            <th>${t("audit_details")}</th>
          </tr></thead>
          <tbody id="au-body-${win.key}"><tr><td colspan="4" style="color:var(--subtext)">Lädt...</td></tr></tbody>
        </table>
      </div>
    </div>
  `;

  const tbody = body.querySelector(`#au-body-${win.key}`);
  const userSel = body.querySelector(`#au-filter-user-${win.key}`);
  const kindSel = body.querySelector(`#au-filter-kind-${win.key}`);

  // Aktionen, die eine Remote-Session darstellen (für den Sammelfilter).
  const SESSION_ACTIONS = new Set([
    "screen.session_started", "guac.connect", "guac.recording",
    "terminal.exec", "terminal.session", "terminal.bulk_exec", "rdp.file_generated",
  ]);

  let allEntries = [];        // ungefilterte Rohdaten
  let sortKey = "ts";
  let sortDir = -1;           // -1 = neueste zuerst

  let existingRecIds = null;   // Set der Replay-IDs, deren Datei noch existiert

  // ---- Highlight aus einer Benachrichtigung ("📋 Im Audit öffnen") ----
  // Das notify-System schickt "audit-highlight" mit einem Textausschnitt
  // (needle) des Eintrags. Wir laden neu (der Eintrag ist ggf. gerade erst
  // entstanden), scrollen zum passenden Eintrag und heben ihn kurz hervor.
  let pendingHighlight = null;

  function applyPendingHighlight() {
    if (!pendingHighlight) return;
    const { needle, ts } = pendingHighlight;
    const cand = allEntries.find((e) =>
      (e.details || "").includes(needle) && Math.abs((e.ts || 0) - ts) < 10 * 60 * 1000);
    if (!cand) return;   // (noch) nicht da - beim nächsten Render erneut versuchen
    pendingHighlight = null;
    const tr = tbody.querySelector(`[data-audit-id="${cand.id}"]`);
    if (!tr) return;
    tr.scrollIntoView({ block: "center", behavior: "smooth" });
    tr.classList.add("audit-flash");
    setTimeout(() => tr.classList.remove("audit-flash"), 3000);
  }

  const onHighlight = (ev) => {
    if (!document.body.contains(body)) {
      window.removeEventListener("audit-highlight", onHighlight);
      return;
    }
    pendingHighlight = ev.detail || null;
    // Filter zurücksetzen, damit der Eintrag sicher sichtbar ist.
    userSel.value = ""; kindSel.value = "";
    load();   // frisch laden (der Eintrag wurde evtl. gerade erst geschrieben)
  };
  window.addEventListener("audit-highlight", onHighlight);

  async function load() {
    try {
      allEntries = await api.getAuditLog();
      // Welche Replays existieren noch? getRecordings() räumt fehlende Dateien
      // serverseitig auf und liefert file_exists. Fehlt die Datei, wird im
      // Audit-Log der "Replay ansehen"-Button durch einen Hinweis ersetzt.
      try {
        const recs = await api.getRecordings();
        existingRecIds = new Set(recs.filter((r) => r.file_exists !== false).map((r) => r.id));
      } catch { existingRecIds = null; }   // Fehler -> nicht fälschlich sperren
      // User-Filter-Dropdown mit den vorkommenden Benutzern füllen.
      const users = [...new Set(allEntries.map((e) => e.username).filter(Boolean))].sort();
      const cur = userSel.value;
      userSel.innerHTML = `<option value="">Alle Benutzer</option>` +
        users.map((u) => `<option value="${esc(u)}">${esc(u)}</option>`).join("");
      userSel.value = cur;

      // Aktions-Filter mit den tatsächlich vorkommenden Aktionen füllen
      // (menschenlesbares Label, technischer Wert). "__sessions" bleibt oben.
      const actions = [...new Set(allEntries.map((e) => e.action).filter(Boolean))].sort();
      const curK = kindSel.value;
      kindSel.innerHTML = `<option value="">Alle Aktionen</option>` +
        `<option value="__sessions">Nur Remote-Sessions</option>` +
        actions.map((a) => `<option value="${esc(a)}">${esc(ACTION_LABELS[a] || a)}</option>`).join("");
      kindSel.value = curK;
      render();
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  }

  function render() {
    const uFilter = userSel.value;
    const kFilter = kindSel.value;
    let rows = allEntries.filter((e) => {
      if (uFilter && e.username !== uFilter) return false;
      if (kFilter === "__sessions" && !SESSION_ACTIONS.has(e.action)) return false;
      else if (kFilter && kFilter !== "__sessions" && e.action !== kFilter) return false;
      return true;
    });

    rows.sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey];
      if (sortKey === "ts") { va = a.ts; vb = b.ts; }
      else { va = String(va || "").toLowerCase(); vb = String(vb || "").toLowerCase(); }
      if (va < vb) return -1 * sortDir;
      if (va > vb) return 1 * sortDir;
      return 0;
    });

    tbody.innerHTML = "";
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--subtext)">Keine Einträge.</td></tr>`;
      return;
    }
    for (const e of rows) {
      const tr = document.createElement("tr");
      const label = ACTION_LABELS[e.action] || e.action;

      let detailsHtml = esc(e.details || "");
      let recId = null;
      if (e.action === "screen.session_started" && (e.details || "").startsWith("rec:")) {
        recId = e.details.slice(4);
        detailsHtml = (existingRecIds && !existingRecIds.has(recId))
          ? `<span style="color:var(--danger);font-size:12px">Replay gibt's nicht mehr</span>`
          : `<button class="taskbar-btn" data-rec="${esc(recId)}">${t("view_recording")}</button>`;
      }
      const guacMatch = (e.details || "").match(/\/api\/recordings\/([A-Za-z0-9_-]+)/);
      if (e.action === "guac.recording" && guacMatch) {
        recId = guacMatch[1];
        detailsHtml = (existingRecIds && !existingRecIds.has(recId))
          ? `<span style="color:var(--danger);font-size:12px">Replay gibt's nicht mehr</span>`
          : `<button class="taskbar-btn" data-rec="${esc(recId)}">▶ Replay ansehen</button>`;
      }
      // Terminal-Sitzung (neu): kompakter Eintrag mit Replay-Verweis "rec:<id> ..."
      const termRec = e.action === "terminal.session" && (e.details || "").match(/^rec:([A-Za-z0-9_-]+)\s*(.*)$/);
      if (termRec) {
        recId = termRec[1];
        detailsHtml = ((existingRecIds && !existingRecIds.has(recId))
          ? `<span style="color:var(--danger);font-size:12px">Replay gibt's nicht mehr</span> `
          : `<button class="taskbar-btn" data-rec="${esc(recId)}">▶ Replay ansehen</button> `) +
          `<span style="font-size:11px">${esc(termRec[2] || "")}</span>`;
      }
      // Terminal-Sitzung (alt): mehrzeiliger Verlauf -> aufklappbarer <pre>-Block.
      if (e.action === "terminal.session" && (e.details || "").includes("Sitzungsverlauf")) {
        const full = e.details;
        detailsHtml = `<details><summary style="cursor:pointer">Sitzungsverlauf anzeigen</summary>` +
          `<pre style="white-space:pre-wrap;max-height:320px;overflow:auto;background:var(--panel-2);padding:8px;border-radius:6px;margin-top:6px;font-size:11px">${esc(full)}</pre></details>`;
      }

      tr.innerHTML = `
        <td style="color:var(--subtext)">${new Date(e.ts).toLocaleString("de-DE")}</td>
        <td>${esc(e.username || "—")}</td>
        <td>${esc(label)}${e.target ? ` <span style="color:var(--subtext);font-size:11px">→ ${esc(String(e.target).slice(0, 12))}</span>` : ""}</td>
        <td style="color:var(--subtext)">${detailsHtml}</td>`;
      tr.dataset.auditId = e.id || "";
      tbody.appendChild(tr);
    }

    applyPendingHighlight();

    tbody.querySelectorAll("[data-rec]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const recId = btn.dataset.rec;
        openWindow({
          key: `recordings-${recId}`, appId: "recordings",
          title: t("recordings"), props: { recId }, w: 820, h: 560,
        });
      })
    );
  }

  // Filter-Dropdowns + Spalten-Sortierung verkabeln
  userSel.addEventListener("change", render);
  kindSel.addEventListener("change", render);
  body.querySelectorAll("[data-sort]").forEach((th) =>
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (sortKey === key) sortDir *= -1;      // gleiche Spalte -> Richtung umdrehen
      else { sortKey = key; sortDir = key === "ts" ? -1 : 1; }
      render();
    })
  );

  body.querySelector(`#au-refresh-${win.key}`).addEventListener("click", load);
  load();
}

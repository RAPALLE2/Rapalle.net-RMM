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
// Aktion -> Übersetzungsschlüssel. Bewusst NICHT die fertigen Texte:
// diese Map wird beim Laden des Moduls einmal ausgewertet, fertige Texte
// würden in der beim Laden aktiven Sprache einfrieren. Der Sprachwechsel
// rendert nur neu, er lädt die Seite nicht neu.
const ACTION_KEYS = {
  "settings.default_layout_set": "audit_settings_default_layout_set",
  "settings.default_layout_cleared": "audit_settings_default_layout_cleared",
  "login.success": "audit_login_success",
  "login.failed": "audit_login_failed",
  "password.changed": "audit_password_changed",
  "file.download": "audit_file_download",
  "terminal.exec": "audit_terminal_exec",
  "terminal.session": "audit_terminal_session",
  "terminal.bulk_exec": "audit_terminal_bulk_exec",
  "process.kill": "audit_process_kill",
  "screen.session_started": "audit_screen_session_started",
  "recording.deleted": "audit_recording_deleted",
  "recording.viewed": "audit_recording_viewed",
  "client.updated": "audit_client_updated",
  "client.deleted": "audit_client_deleted",
  "user.created": "audit_user_created",
  "user.deleted": "audit_user_deleted",
  "group.created": "audit_group_created",
  "group.updated": "audit_group_updated",
  "group.deleted": "audit_group_deleted",
  "webhook.created": "audit_webhook_created",
  "webhook.deleted": "audit_webhook_deleted",
  "realm.created": "audit_realm_created",
  "realm.deleted": "audit_realm_deleted",
  "automation.created": "audit_automation_created",
  "automation.executed": "audit_automation_executed",
  "automation.deleted": "audit_automation_deleted",
  "guac.connect": "audit_guac_connect",
  "guac.recording": "audit_guac_recording",
  "error.reported": "audit_error_reported",
  "error.warn": "audit_error_warn",
  "agent.update_triggered": "audit_agent_update_triggered",
  "agent.update_all_triggered": "audit_agent_update_all_triggered",
  "agent.update_all_completed": "audit_agent_update_all_completed",
  "relay.auto_closed": "audit_relay_auto_closed",
  "backend.crash": "audit_backend_crash",
  "backend.restarted": "audit_backend_restarted",
  "frontend.crash_recovered": "audit_frontend_crash_recovered",
  "rdp.file_generated": "audit_rdp_file_generated",
  "script.created": "audit_script_created",
  "script.deleted": "audit_script_deleted",
  "tenant.created": "audit_tenant_created",
  "location.created": "audit_location_created",
  "folder.created": "audit_folder_created",
  "patch.scan": "audit_patch_scan",
  "patch.apply": "audit_patch_apply",
  "patch.auto_applied": "audit_patch_auto_applied",
  "privacy.self_export": "audit_privacy_self_export",
  "privacy.export_other": "audit_privacy_export_other",
  "privacy.user_erased": "audit_privacy_user_erased",
  "privacy.purge": "audit_privacy_purge",
};

// Auflösung erst beim Zugriff - dadurch stimmt die Sprache immer.
const actionLabel = (a) => (ACTION_KEYS[a] ? t(ACTION_KEYS[a]) : a);

export function renderAudit(body, win) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar" style="flex-wrap:wrap;gap:8px">
        <span style="color:var(--subtext)">${t("ad_title")}</span>
        <select id="au-filter-user-${win.key}" style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
          <option value="">${t("ad_all_users")}</option>
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
          <tbody id="au-body-${win.key}"><tr><td colspan="4" style="color:var(--subtext)">${t("loading")}</td></tr></tbody>
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
      userSel.innerHTML = `<option value="">${t("ad_all_users")}</option>` +
        users.map((u) => `<option value="${esc(u)}">${esc(u)}</option>`).join("");
      userSel.value = cur;

      // Aktions-Filter mit den tatsächlich vorkommenden Aktionen füllen
      // (menschenlesbares Label, technischer Wert). "__sessions" bleibt oben.
      const actions = [...new Set(allEntries.map((e) => e.action).filter(Boolean))].sort();
      const curK = kindSel.value;
      kindSel.innerHTML = `<option value="">Alle Aktionen</option>` +
        `<option value="__sessions">Nur Remote-Sessions</option>` +
        actions.map((a) => `<option value="${esc(a)}">${esc(actionLabel(a))}</option>`).join("");
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
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--subtext)">${t("pm_no_entries")}</td></tr>`;
      return;
    }
    for (const e of rows) {
      const tr = document.createElement("tr");
      const label = actionLabel(e.action);

      let detailsHtml = esc(e.details || "");
      let recId = null;
      if (e.action === "screen.session_started" && (e.details || "").startsWith("rec:")) {
        recId = e.details.slice(4);
        detailsHtml = (existingRecIds && !existingRecIds.has(recId))
          ? `<span style="color:var(--danger);font-size:12px">${t("ad_replay_gone")}</span>`
          : `<button class="taskbar-btn" data-rec="${esc(recId)}">${t("view_recording")}</button>`;
      }
      const guacMatch = (e.details || "").match(/\/api\/recordings\/([A-Za-z0-9_-]+)/);
      if (e.action === "guac.recording" && guacMatch) {
        recId = guacMatch[1];
        detailsHtml = (existingRecIds && !existingRecIds.has(recId))
          ? `<span style="color:var(--danger);font-size:12px">${t("ad_replay_gone")}</span>`
          : `<button class="taskbar-btn" data-rec="${esc(recId)}">▶ ${t("view_recording")}</button>`;
      }
      // Terminal-Sitzung (neu): kompakter Eintrag mit Replay-Verweis "rec:<id> ..."
      const termRec = e.action === "terminal.session" && (e.details || "").match(/^rec:([A-Za-z0-9_-]+)\s*(.*)$/);
      if (termRec) {
        recId = termRec[1];
        detailsHtml = ((existingRecIds && !existingRecIds.has(recId))
          ? `<span style="color:var(--danger);font-size:12px">${t("ad_replay_gone")}</span> `
          : `<button class="taskbar-btn" data-rec="${esc(recId)}">▶ ${t("view_recording")}</button> `) +
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

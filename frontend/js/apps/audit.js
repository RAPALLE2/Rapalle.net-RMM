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
  "login.success": "🔓 Login erfolgreich",
  "login.failed": "⛔ Login fehlgeschlagen",
  "password.changed": "🔑 Passwort geändert",
  "file.download": "⬇️ Datei heruntergeladen",
  "terminal.exec": "⌨️ Terminal-Befehl",
  "terminal.bulk_exec": "⚡ Bulk-Befehl",
  "process.kill": "❌ Prozess beendet",
  "screen.session_started": "🖥️ Remote-Session gestartet",
  "recording.deleted": "🗑️ Aufzeichnung gelöscht",
  "client.updated": "✎ Client geändert",
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
      <div class="explorer-toolbar">
        <span style="flex:1;color:var(--subtext)">Aktivitätsprotokoll (letzte 200 Einträge, 30 Tage Aufbewahrung)</span>
        <button id="au-refresh-${win.key}">⟳</button>
      </div>
      <div style="flex:1;overflow:auto">
        <table class="data-table">
          <thead><tr>
            <th style="width:150px">${t("audit_when")}</th>
            <th>${t("audit_user")}</th>
            <th>${t("audit_action")}</th>
            <th>${t("audit_details")}</th>
          </tr></thead>
          <tbody id="au-body-${win.key}"><tr><td colspan="4" style="color:var(--subtext)">Lädt...</td></tr></tbody>
        </table>
      </div>
    </div>
  `;

  const tbody = body.querySelector(`#au-body-${win.key}`);

  async function load() {
    try {
      const entries = await api.getAuditLog();
      tbody.innerHTML = "";
      if (!entries.length) {
        tbody.innerHTML = `<tr><td colspan="4" style="color:var(--subtext)">Keine Einträge.</td></tr>`;
        return;
      }
      for (const e of entries) {
        const tr = document.createElement("tr");
        const label = ACTION_LABELS[e.action] || e.action;

        // Screen-Session mit Aufzeichnungs-Verknüpfung (details = "rec:<id>")
        let detailsHtml = esc(e.details || "");
        let recId = null;
        if (e.action === "screen.session_started" && (e.details || "").startsWith("rec:")) {
          recId = e.details.slice(4);
          detailsHtml = `<button class="taskbar-btn" data-rec="${esc(recId)}">${t("view_recording")}</button>`;
        }
        // Guacamole-Replay (details = "Replay: /api/recordings/<id>")
        const guacMatch = (e.details || "").match(/\/api\/recordings\/([A-Za-z0-9_-]+)/);
        if (e.action === "guac.recording" && guacMatch) {
          recId = guacMatch[1];
          detailsHtml = `<button class="taskbar-btn" data-rec="${esc(recId)}">▶ Replay ansehen</button>`;
        }

        tr.innerHTML = `
          <td style="color:var(--subtext)">${new Date(e.ts).toLocaleString("de-DE")}</td>
          <td>${esc(e.username || "—")}</td>
          <td>${esc(label)}${e.target ? ` <span style="color:var(--subtext);font-size:11px">→ ${esc(String(e.target).slice(0, 12))}</span>` : ""}</td>
          <td style="color:var(--subtext)">${detailsHtml}</td>`;
        tbody.appendChild(tr);
      }

      // "Aufzeichnung ansehen"-Buttons verkabeln -> Recordings-App mit genau
      // dieser Aufzeichnung öffnen (eigenes Fenster pro Aufzeichnung).
      tbody.querySelectorAll("[data-rec]").forEach((btn) =>
        btn.addEventListener("click", () => {
          const recId = btn.dataset.rec;
          openWindow({
            key: `recordings-${recId}`, appId: "recordings",
            title: t("recordings"), props: { recId }, w: 820, h: 560,
          });
        })
      );
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  }

  body.querySelector(`#au-refresh-${win.key}`).addEventListener("click", load);
  load();
}

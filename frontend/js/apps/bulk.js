// apps/bulk.js
// ------------
// Bulk Remote Shell: einen Befehl oder ein gespeichertes Skript auf MEHREREN
// ausgewählten Clients gleichzeitig ausführen. Zeigt pro Client das Ergebnis.

import { state } from "../state.js";
import { api } from "../api.js";
import { attachScriptPicker } from "../scriptpicker.js";
import { registerCleanup } from "../windowmanager.js";
import { esc } from "../utils.js";
import { t } from "../i18n.js";

export function renderBulk(body, win) {
  async function draw() {
    // Nur online-Clients kann man ansprechen
    const clients = state.clients.filter((c) => c.online);

    let scripts = [];
    try { scripts = await api.getScripts(); } catch { /* egal */ }

    body.innerHTML = `
      <div class="settings-section">
        <h3>Bulk Remote Shell</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("bk_hint")}
        </p>

        <div class="form-row">
          <label>${t("bk_pick_devices", { n: clients.length })}</label>
          <div style="max-height:150px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
            <label style="display:block;padding:3px 0;font-size:13px">
              <input type="checkbox" id="bulk-all" /> <b>${t("bk_select_all")}</b>
            </label>
            ${clients.map((c) => `
              <label style="display:block;padding:3px 0;font-size:13px">
                <input type="checkbox" class="bulk-client" value="${c.id}" />
                <span class="status-dot" style="background:var(--online);display:inline-block;width:7px;height:7px;border-radius:50%"></span>
                ${esc(c.hostname)} <span style="color:var(--subtext)">(${esc(c.platform || "")})</span>
              </label>`).join("") || `<span style="color:var(--subtext)">Keine Online-Geräte.</span>`}
          </div>
        </div>

        <div class="form-row">
          <label>${t("bk_use_script")}</label>
          <button class="taskbar-btn" id="bulk-script" style="width:auto;align-self:flex-start;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📜 ${t("bk_pick_script")}</button>
        </div>

        <div class="form-row">
          <label>${t("bk_command")}</label>
          <textarea id="bulk-cmd" style="min-height:60px;font-family:monospace;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" placeholder="z.B. hostname"></textarea>
        </div>

        <button class="btn-primary" id="bulk-run" style="margin-top:4px">⚡ ${t("bk_run_all")}</button>

        <h3 style="margin-top:24px">Ergebnisse</h3>
        <div id="bulk-results"></div>
      </div>
    `;

    const allCheck = body.querySelector("#bulk-all");
    const clientChecks = () => Array.from(body.querySelectorAll(".bulk-client"));
    allCheck?.addEventListener("change", () => clientChecks().forEach((cb) => (cb.checked = allCheck.checked)));

    // Skript-Auswahl (Suche + Ordnerstruktur) füllt das Befehlsfeld
    const detachPicker = attachScriptPicker({
      button: body.querySelector("#bulk-script"),
      scripts,
      onPick: (sc) => {
        body.querySelector("#bulk-cmd").value = sc.command;
        body.querySelector("#bulk-script").textContent = `📜 ${sc.name}`;
      },
    });
    registerCleanup(win.key, () => detachPicker());

    body.querySelector("#bulk-run").addEventListener("click", async () => {
      const selected = clientChecks().filter((cb) => cb.checked).map((cb) => cb.value);
      const command = body.querySelector("#bulk-cmd").value.trim();
      const resultsEl = body.querySelector("#bulk-results");

      if (!selected.length) { resultsEl.innerHTML = `<span style="color:var(--warn)">${t("bk_none_selected")}</span>`; return; }
      if (!command) { resultsEl.innerHTML = `<span style="color:var(--warn)">${t("bk_no_command")}</span>`; return; }

      resultsEl.innerHTML = `<span style="color:var(--subtext)">${t("bk_running", { n: selected.length })}</span>`;
      try {
        const results = await api.bulkExec(selected, command);
        // results: { clientId: {ok, stdout, stderr, code, error} }
        resultsEl.innerHTML = Object.entries(results).map(([cid, r]) => {
          const client = state.clients.find((c) => c.id === cid);
          const name = client ? client.hostname : cid;
          if (r.ok) {
            const out = (r.stdout || "").trim() || (r.stderr || "").trim() || `(Exit ${r.code})`;
            return `<div class="panel" style="margin-bottom:8px">
              <strong style="color:var(--accent)">✓ ${esc(name)}</strong>
              <pre style="background:var(--panel-2);padding:8px;border-radius:6px;margin-top:6px;font-size:12px;white-space:pre-wrap;overflow-x:auto">${esc(out)}</pre>
            </div>`;
          }
          return `<div class="panel" style="margin-bottom:8px">
            <strong style="color:var(--danger)">✗ ${esc(name)}</strong>
            <div style="color:var(--danger);font-size:12px;margin-top:4px">${esc(r.error || t("u_fehler"))}</div>
          </div>`;
        }).join("");
      } catch (e) {
        resultsEl.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
      }
    });
  }

  draw();
}

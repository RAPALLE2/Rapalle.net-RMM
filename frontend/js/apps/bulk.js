// apps/bulk.js
// ------------
// Bulk Remote Shell: einen Befehl oder ein gespeichertes Skript auf MEHREREN
// ausgewählten Clients gleichzeitig ausführen. Zeigt pro Client das Ergebnis.

import { state } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";

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
          Wähle Geräte aus und führe einen Befehl oder ein gespeichertes Skript
          gleichzeitig auf allen aus.
        </p>

        <div class="form-row">
          <label>Geräte auswählen (${clients.length} online)</label>
          <div style="max-height:150px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
            <label style="display:block;padding:3px 0;font-size:13px">
              <input type="checkbox" id="bulk-all" /> <b>Alle auswählen</b>
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
          <label>Gespeichertes Skript einsetzen (optional)</label>
          <select id="bulk-script">
            <option value="">— eigenen Befehl eingeben —</option>
            ${scripts.map((s) => `<option value="${esc(s.command)}">${esc(s.name)} (${esc(s.os)})</option>`).join("")}
          </select>
        </div>

        <div class="form-row">
          <label>Befehl</label>
          <textarea id="bulk-cmd" style="min-height:60px;font-family:monospace;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" placeholder="z.B. hostname"></textarea>
        </div>

        <button class="btn-primary" id="bulk-run" style="margin-top:4px">⚡ Auf allen ausgewählten ausführen</button>

        <h3 style="margin-top:24px">Ergebnisse</h3>
        <div id="bulk-results"></div>
      </div>
    `;

    const allCheck = body.querySelector("#bulk-all");
    const clientChecks = () => Array.from(body.querySelectorAll(".bulk-client"));
    allCheck?.addEventListener("change", () => clientChecks().forEach((cb) => (cb.checked = allCheck.checked)));

    // Skript-Auswahl füllt das Befehlsfeld
    body.querySelector("#bulk-script").addEventListener("change", (e) => {
      if (e.target.value) body.querySelector("#bulk-cmd").value = e.target.value;
    });

    body.querySelector("#bulk-run").addEventListener("click", async () => {
      const selected = clientChecks().filter((cb) => cb.checked).map((cb) => cb.value);
      const command = body.querySelector("#bulk-cmd").value.trim();
      const resultsEl = body.querySelector("#bulk-results");

      if (!selected.length) { resultsEl.innerHTML = `<span style="color:var(--warn)">Keine Geräte ausgewählt.</span>`; return; }
      if (!command) { resultsEl.innerHTML = `<span style="color:var(--warn)">Kein Befehl eingegeben.</span>`; return; }

      resultsEl.innerHTML = `<span style="color:var(--subtext)">Führe auf ${selected.length} Geräten aus...</span>`;
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
            <div style="color:var(--danger);font-size:12px;margin-top:4px">${esc(r.error || "Fehler")}</div>
          </div>`;
        }).join("");
      } catch (e) {
        resultsEl.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
      }
    });
  }

  draw();
}

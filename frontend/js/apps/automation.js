// apps/automation.js
// ------------------
// Automationen: einen Befehl/ein Skript auf bestimmten Clients zu festen
// Intervallen automatisch ausführen lassen (z.B. jede Nacht Updates).

import { state } from "../state.js";
import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";
import { t } from "../i18n.js";
import { condenseHints } from "../help.js";

export function renderAutomation(body, win) {
  // Erklaertexte dieser Seite in "?"-Symbole umwandeln - einmal direkt nach
  // dem Zeichnen und einmal verzoegert fuer nachgeladene Bereiche.
  setTimeout(() => condenseHints(body), 0);
  setTimeout(() => condenseHints(body), 400);

  async function draw() {
    const clients = state.clients;
    let scripts = [];
    try { scripts = await api.getScripts(); } catch { /* egal */ }

    body.innerHTML = `
      <div class="settings-section">
        <h3>Neue Automation</h3>
        <div class="form-row">
          <label>Name</label>
          <input type="text" id="au-name" placeholder="${t("u_z_b_nachtliches_update")}" />
        </div>
        <div class="form-row">
          <label>Gespeichertes Skript einsetzen (optional)</label>
          <select id="au-script">
            <option value="">— eigenen Befehl —</option>
            ${scripts.map((s) => `<option value="${esc(s.command)}">${esc(s.name)}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label>Befehl</label>
          <textarea id="au-cmd" style="min-height:56px;font-family:monospace;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)"></textarea>
        </div>
        <div class="form-row">
          <label>Ziel-Clients</label>
          <div style="max-height:120px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
            ${clients.map((c) => `
              <label style="display:block;padding:2px 0;font-size:13px">
                <input type="checkbox" class="au-client" value="${c.id}" /> ${esc(c.hostname)}
              </label>`).join("") || `<span style="color:var(--subtext)">Keine Clients.</span>`}
          </div>
        </div>
        <div class="form-row">
          <label>${t("au_run_every")}</label>
          <div style="display:flex;gap:8px">
            <input type="number" id="au-interval" value="60" min="1" style="width:100px" />
            <select id="au-unit">
              <option value="60">${t("au_minutes")}</option>
              <option value="3600">${t("au_hours")}</option>
              <option value="86400">${t("au_days")}</option>
            </select>
          </div>
        </div>
        <div id="au-error" class="form-error hidden"></div>
        <button class="btn-primary" id="au-add" style="margin-top:4px">+ Automation anlegen</button>

        <h3 style="margin-top:24px">Aktive Automationen</h3>
        <div id="au-list"></div>
      </div>
    `;

    body.querySelector("#au-script").addEventListener("change", (e) => {
      if (e.target.value) body.querySelector("#au-cmd").value = e.target.value;
    });

    body.querySelector("#au-add").addEventListener("click", async () => {
      const name = body.querySelector("#au-name").value.trim();
      const command = body.querySelector("#au-cmd").value.trim();
      const clientIds = Array.from(body.querySelectorAll(".au-client")).filter((c) => c.checked).map((c) => c.value);
      const interval = parseInt(body.querySelector("#au-interval").value);
      const unit = parseInt(body.querySelector("#au-unit").value);
      const err = body.querySelector("#au-error");
      err.classList.add("hidden");
      if (!name || !command) { err.textContent = t("u_name_und_befehl_erforderlich"); err.classList.remove("hidden"); return; }
      if (!clientIds.length) { err.textContent = t("u_mindestens_einen_client_wahlen"); err.classList.remove("hidden"); return; }
      try {
        await api.createAutomation({ name, command, client_ids: clientIds, interval_seconds: interval * unit });
        window.notify?.("Automation angelegt", "success");
        draw();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });

    loadList();
  }

  async function loadList() {
    const listEl = body.querySelector("#au-list");
    try {
      const autos = await api.getAutomations();
      if (!autos.length) {
        listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">${t("au_none")}</div>`;
        return;
      }
      listEl.innerHTML = autos.map((a) => {
        const targets = a.client_ids.map((id) => {
          const c = state.clients.find((x) => x.id === id);
          return c ? esc(c.hostname) : id.slice(0, 6);
        }).join(", ");
        const every = a.interval_seconds >= 86400 ? `${a.interval_seconds / 86400} Tage`
          : a.interval_seconds >= 3600 ? `${a.interval_seconds / 3600} Std`
          : `${a.interval_seconds / 60} Min`;
        return `
          <div class="panel" style="margin-bottom:10px">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <strong>${esc(a.name)}</strong>
              <span style="font-size:11px;color:${a.enabled ? "var(--accent)" : "var(--subtext)"}">
                ${a.enabled ? "● aktiv" : "○ pausiert"}
              </span>
            </div>
            <pre style="background:var(--panel-2);padding:8px;border-radius:6px;margin:8px 0;font-size:12px;white-space:pre-wrap">${esc(a.command)}</pre>
            <div style="font-size:12px;color:var(--subtext)">Alle ${every} · Ziele: ${targets || "—"}${a.last_run ? " · zuletzt: " + new Date(a.last_run).toLocaleString("de-DE") : ""}</div>
            <div style="display:flex;gap:6px;margin-top:8px">
              <button class="taskbar-btn" data-runs="${a.id}">📋 Ergebnisse</button>
              <button class="taskbar-btn" data-toggle="${a.id}">${a.enabled ? t("au_pause") : t("au_enable")}</button>
              <button class="taskbar-btn" data-del="${a.id}">${t("delete")}</button>
            </div>
            <div class="au-runs" id="au-runs-${a.id}" style="display:none;margin-top:8px"></div>
          </div>`;
      }).join("");

      listEl.querySelectorAll("[data-toggle]").forEach((btn) =>
        btn.addEventListener("click", async () => { await api.toggleAutomation(btn.dataset.toggle); loadList(); })
      );
      listEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!(await uiConfirm(t("u_automation_loschen"), { okText: t("delete"), danger: true }))) return;
          await api.deleteAutomation(btn.dataset.del); loadList();
        })
      );
      // Ergebnisliste je Durchlauf ein-/ausklappen
      listEl.querySelectorAll("[data-runs]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const box = listEl.querySelector(`#au-runs-${btn.dataset.runs}`);
          if (box.style.display === "block") { box.style.display = "none"; return; }
          box.style.display = "block";
          box.innerHTML = `<div style="color:var(--subtext);font-size:12px">Lade Ergebnisse…</div>`;
          try {
            const { runs } = await api.getAutomationRuns(btn.dataset.runs);
            if (!runs.length) {
              box.innerHTML = `<div style="color:var(--subtext);font-size:12px">${t("au_no_runs")}</div>`;
              return;
            }
            box.innerHTML = runs.map((run) => {
              const when = new Date(run.started_at).toLocaleString("de-DE");
              const okCount = run.results.filter((r) => r.ok && (r.exit_code === 0 || r.exit_code == null)).length;
              const rows = run.results.map((r) => {
                const good = r.ok && (r.exit_code === 0 || r.exit_code == null);
                const output = (r.stdout || "").trim() || (r.stderr || "").trim() || t("u_keine_ausgabe");
                return `
                  <div style="border-top:1px solid var(--border);padding:6px 0">
                    <div style="display:flex;justify-content:space-between;font-size:12px">
                      <span>${good ? "✅" : "❌"} <strong>${esc(r.client_hostname || r.client_id.slice(0,6))}</strong></span>
                      <span style="color:var(--subtext)">Exit ${r.exit_code ?? "—"}</span>
                    </div>
                    <pre style="background:var(--panel-2);padding:6px;border-radius:5px;margin:4px 0 0;font-size:11px;white-space:pre-wrap;max-height:140px;overflow:auto">${esc(output)}</pre>
                  </div>`;
              }).join("");
              return `
                <div class="panel" style="background:var(--panel-2);margin-bottom:8px">
                  <div style="display:flex;justify-content:space-between;font-size:12px;font-weight:600">
                    <span>${when}</span>
                    <span style="color:${okCount === run.results.length ? "var(--accent)" : "var(--danger)"}">
                      ${okCount}/${run.results.length} OK
                    </span>
                  </div>
                  ${rows}
                </div>`;
            }).join("");
          } catch (e) {
            box.innerHTML = `<div style="color:var(--danger);font-size:12px">${esc(e.message)}</div>`;
          }
        })
      );
    } catch (e) {
      listEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
    }
  }

  draw();
}

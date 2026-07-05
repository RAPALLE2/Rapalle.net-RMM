// apps/scripts.js
// ---------------
// Verwaltet gespeicherte Skripte: Name + Befehl (mehrzeilig möglich, z.B.
// "apt update && apt upgrade -y") + Ziel-Betriebssystem (windows/linux/any).
// Von hier aus kann man ein Skript auch direkt auf einem Client ausführen.

import { state } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";

export function renderScripts(body, win) {
  function draw() {
    body.innerHTML = `
      <div class="settings-section">
        <h3>Neues Skript</h3>
        <div class="form-row">
          <label>Name</label>
          <input type="text" id="sc-name" placeholder="z.B. update" />
        </div>
        <div class="form-row">
          <label>Befehl (mehrzeilig möglich)</label>
          <textarea id="sc-cmd" style="min-height:70px;font-family:monospace;padding:8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" placeholder="apt update && apt upgrade -y"></textarea>
        </div>
        <div class="form-row">
          <label>Betriebssystem</label>
          <select id="sc-os">
            <option value="any">Alle</option>
            <option value="linux">Linux</option>
            <option value="windows">Windows</option>
          </select>
        </div>
        <div id="sc-error" class="form-error hidden"></div>
        <button class="btn-primary" id="sc-add" style="margin-top:4px">+ Skript speichern</button>

        <h3 style="margin-top:24px">Gespeicherte Skripte</h3>
        <div id="sc-list"></div>
      </div>
    `;

    body.querySelector("#sc-add").addEventListener("click", async () => {
      const name = body.querySelector("#sc-name").value.trim();
      const command = body.querySelector("#sc-cmd").value.trim();
      const os = body.querySelector("#sc-os").value;
      const err = body.querySelector("#sc-error");
      err.classList.add("hidden");
      if (!name || !command) { err.textContent = "Name und Befehl erforderlich"; err.classList.remove("hidden"); return; }
      try {
        await api.createScript({ name, command, os });
        draw();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });

    loadList();
  }

  async function loadList() {
    const listEl = body.querySelector("#sc-list");
    try {
      const scripts = await api.getScripts();
      if (!scripts.length) {
        listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">Noch keine Skripte gespeichert.</div>`;
        return;
      }
      // Online-Clients für die "Ausführen auf..."-Auswahl
      const onlineClients = state.clients.filter((c) => c.online);
      const clientOptions = onlineClients.map((c) => `<option value="${c.id}">${esc(c.hostname)}</option>`).join("");

      listEl.innerHTML = scripts.map((s) => `
        <div class="panel" style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <strong>${esc(s.name)}</strong>
            <span style="font-size:11px;color:var(--subtext);text-transform:uppercase">${esc(s.os)}</span>
          </div>
          <pre style="background:var(--panel-2);padding:8px;border-radius:6px;margin:8px 0;font-size:12px;white-space:pre-wrap;overflow-x:auto">${esc(s.command)}</pre>
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
            <select data-run-target="${s.id}" style="padding:5px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
              <option value="">Client wählen...</option>${clientOptions}
            </select>
            <button class="action-btn" data-run="${s.id}">▶ Ausführen</button>
            <button class="taskbar-btn" data-del="${s.id}">Löschen</button>
            <span data-result="${s.id}" style="font-size:12px;color:var(--subtext)"></span>
          </div>
        </div>
      `).join("");

      // Ausführen
      listEl.querySelectorAll("[data-run]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const scriptId = btn.dataset.run;
          const script = scripts.find((s) => s.id === scriptId);
          const targetSel = listEl.querySelector(`[data-run-target="${scriptId}"]`);
          const resultEl = listEl.querySelector(`[data-result="${scriptId}"]`);
          const clientId = targetSel.value;
          if (!clientId) { resultEl.textContent = "Bitte Client wählen"; return; }
          resultEl.textContent = "Läuft...";
          try {
            const res = await api.execOnClient(clientId, script.command);
            resultEl.innerHTML = `<span style="color:var(--accent)">✓ Exit ${res.code}</span>`;
          } catch (e) {
            resultEl.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
          }
        })
      );

      // Löschen
      listEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!confirm("Skript löschen?")) return;
          await api.deleteScript(btn.dataset.del);
          loadList();
        })
      );
    } catch (e) {
      listEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
    }
  }

  draw();
}

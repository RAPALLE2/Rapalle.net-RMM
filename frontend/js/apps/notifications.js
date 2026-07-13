// apps/notifications.js
// ---------------------
// Benachrichtigungen per Webhook. Man kann Discord-Channel-Webhooks oder
// benutzerdefinierte Webhooks anlegen und testen. Das Backend schickt beim
// Test (und später bei Ereignissen) eine Nachricht an die URL.

import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";

export function renderNotifications(body, win) {
  function draw() {
    body.innerHTML = `
      <div class="settings-section">
        <h3>Webhook hinzufügen</h3>
        <p style="color:var(--subtext);font-size:13px">
          Sende Benachrichtigungen an einen Chat-Kanal oder ein eigenes System.
          Für Discord: im Channel unter „Integrationen → Webhooks" eine Webhook-URL
          erstellen und hier einfügen.
        </p>

        <div class="form-row">
          <label>Typ</label>
          <select id="nt-type">
            <option value="discord">Discord</option>
            <option value="custom">Benutzerdefiniert (Custom)</option>
          </select>
        </div>
        <div class="form-row">
          <label>Name</label>
          <input type="text" id="nt-name" placeholder="z.B. Alerts-Channel" />
        </div>
        <div class="form-row">
          <label>Webhook-URL</label>
          <input type="text" id="nt-url" placeholder="https://discord.com/api/webhooks/..." />
        </div>
        <div id="nt-error" class="form-error hidden"></div>
        <button class="btn-primary" id="nt-add" style="margin-top:4px">+ Webhook speichern</button>

        <h3 style="margin-top:24px">Konfigurierte Webhooks</h3>
        <div id="nt-list"></div>
      </div>
    `;

    body.querySelector("#nt-add").addEventListener("click", async () => {
      const name = body.querySelector("#nt-name").value.trim();
      const url = body.querySelector("#nt-url").value.trim();
      const type = body.querySelector("#nt-type").value;
      const err = body.querySelector("#nt-error");
      err.classList.add("hidden");
      if (!name || !url) { err.textContent = "Name und URL erforderlich"; err.classList.remove("hidden"); return; }
      try {
        await api.createWebhook({ name, url, type });
        window.notify?.("Webhook gespeichert", "success");
        draw();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });

    loadList();
  }

  async function loadList() {
    const listEl = body.querySelector("#nt-list");
    try {
      const hooks = await api.getWebhooks();
      if (!hooks.length) {
        listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">Noch keine Webhooks konfiguriert.</div>`;
        return;
      }
      listEl.innerHTML = hooks.map((w) => `
        <div class="panel" style="margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
          <div>
            <strong>${esc(w.name)}</strong>
            <span style="font-size:11px;color:var(--subtext);margin-left:6px">${esc(w.type)}</span>
            <div style="font-size:11px;color:var(--subtext);font-family:monospace;margin-top:2px">${esc(w.url.slice(0, 48))}…</div>
          </div>
          <div style="display:flex;gap:6px">
            <button class="taskbar-btn" data-test="${w.id}">Testen</button>
            <button class="taskbar-btn" data-del="${w.id}">Löschen</button>
          </div>
        </div>
      `).join("");

      listEl.querySelectorAll("[data-test]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          btn.textContent = "...";
          try {
            await api.testWebhook(btn.dataset.test);
            window.notify?.("Test-Benachrichtigung gesendet", "success");
          } catch (e) {
            window.notify?.("Test fehlgeschlagen: " + e.message, "error");
          }
          btn.textContent = "Testen";
        })
      );
      listEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!(await uiConfirm("Webhook löschen?", { okText: "Löschen", danger: true }))) return;
          await api.deleteWebhook(btn.dataset.del); loadList();
        })
      );
    } catch (e) {
      listEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
    }
  }

  draw();
}

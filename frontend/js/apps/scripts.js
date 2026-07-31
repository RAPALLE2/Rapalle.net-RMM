// apps/scripts.js
// ---------------
// Verwaltet gespeicherte Skripte: Name + Befehl (mehrzeilig möglich, z.B.
// "apt update && apt upgrade -y") + Ziel-Betriebssystem (windows/linux/any).
// Von hier aus kann man ein Skript auch direkt auf einem Client ausführen.

import { state } from "../state.js";
import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";
import { t } from "../i18n.js";

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
          <label>${t("sc_command")}</label>
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
        <div class="form-row">
          <label>Ordner (optional)</label>
          <input type="text" id="sc-folder" list="sc-folder-list" placeholder="z.B. Wartung" />
          <datalist id="sc-folder-list"></datalist>
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
      const folder = body.querySelector("#sc-folder").value.trim();
      const err = body.querySelector("#sc-error");
      err.classList.add("hidden");
      if (!name || !command) { err.textContent = t("u_name_und_befehl_erforderlich"); err.classList.remove("hidden"); return; }
      try {
        await api.createScript({ name, command, os, folder });
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
        listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">${t("sc_none")}</div>`;
        return;
      }
      // Online-Clients für die "Ausführen auf..."-Auswahl
      const onlineClients = state.clients.filter((c) => c.online);
      const clientOptions = onlineClients.map((c) => `<option value="${c.id}">${esc(c.hostname)}</option>`).join("");

      // Vorhandene Ordner sammeln (für Datalist im Formular + Verschieben-Auswahl)
      const folders = [...new Set(scripts.map((s) => (s.folder || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
      const dl = body.querySelector("#sc-folder-list");
      if (dl) dl.innerHTML = folders.map((f) => `<option value="${esc(f)}"></option>`).join("");

      const renderScript = (s) => `
        <div class="panel" style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
            <strong>${esc(s.name)}</strong>
            <span style="font-size:11px;color:var(--subtext);text-transform:uppercase">${esc(s.os)}</span>
          </div>
          <pre style="background:var(--panel-2);padding:8px;border-radius:6px;margin:8px 0;font-size:12px;white-space:pre-wrap;overflow-x:auto">${esc(s.command)}</pre>
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
            <select data-run-target="${s.id}" style="padding:5px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
              <option value="">${t("sc_pick_client")}</option>${clientOptions}
            </select>
            <button class="action-btn" data-run="${s.id}">▶ ${t("src_run")}</button>
            <select data-move="${s.id}" title="In Ordner verschieben" style="padding:5px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
              <option value="">📁 ${(s.folder || "").trim() ? esc(s.folder) : "Kein Ordner"}</option>
              ${(s.folder || "").trim() ? `<option value="__root__">(Kein Ordner)</option>` : ""}
              ${folders.filter((f) => f !== (s.folder || "").trim()).map((f) => `<option value="${esc(f)}">${esc(f)}</option>`).join("")}
              <option value="__new__">+ ${t("exp_new_folder")}…</option>
            </select>
            <button class="taskbar-btn" data-del="${s.id}">${t("delete")}</button>
            <span data-result="${s.id}" style="font-size:12px;color:var(--subtext)"></span>
          </div>
        </div>`;

      // Nach Ordnern gruppiert anzeigen: erst alle Ordner (auf-/zuklappbar),
      // dann Skripte ohne Ordner.
      const byFolder = new Map();
      for (const s of scripts) {
        const key = (s.folder || "").trim();
        if (!byFolder.has(key)) byFolder.set(key, []);
        byFolder.get(key).push(s);
      }
      let html = "";
      for (const f of folders) {
        html += `
          <details open style="margin-bottom:10px">
            <summary style="cursor:pointer;font-weight:600;padding:6px 4px">📁 ${esc(f)} <span style="color:var(--subtext);font-weight:normal;font-size:12px">(${byFolder.get(f).length})</span></summary>
            <div style="margin:8px 0 0 14px;border-left:2px solid var(--border);padding-left:12px">
              ${byFolder.get(f).map(renderScript).join("")}
            </div>
          </details>`;
      }
      const rootScripts = byFolder.get("") || [];
      if (rootScripts.length) {
        html += folders.length
          ? `<div style="font-weight:600;padding:6px 4px">📄 Ohne Ordner <span style="color:var(--subtext);font-weight:normal;font-size:12px">(${rootScripts.length})</span></div>`
          : "";
        html += rootScripts.map(renderScript).join("");
      }
      listEl.innerHTML = html;

      // In Ordner verschieben
      listEl.querySelectorAll("[data-move]").forEach((sel) =>
        sel.addEventListener("change", async () => {
          const s = scripts.find((x) => x.id === sel.dataset.move);
          if (!s || !sel.value) return;
          let folder = sel.value;
          if (folder === "__root__") folder = "";
          if (folder === "__new__") {
            const { uiPrompt } = await import("../utils.js");
            folder = await uiPrompt("Neuer Ordner", { placeholder: "Ordnername" });
            if (!folder || !folder.trim()) { sel.value = ""; return; }
            folder = folder.trim();
          }
          try {
            await api.updateScript(s.id, { name: s.name, command: s.command, os: s.os, folder });
            loadList();
          } catch (e) { sel.value = ""; console.warn(e); }
        })
      );

      // Ausführen
      listEl.querySelectorAll("[data-run]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const scriptId = btn.dataset.run;
          const script = scripts.find((s) => s.id === scriptId);
          const targetSel = listEl.querySelector(`[data-run-target="${scriptId}"]`);
          const resultEl = listEl.querySelector(`[data-result="${scriptId}"]`);
          const clientId = targetSel.value;
          if (!clientId) { resultEl.textContent = t("u_bitte_client_wahlen"); return; }
          resultEl.textContent = t("u_lauft_3");
          try {
            const res = await api.execOnClient(clientId, script.command);
            const ok = res.code === 0;
            const out = [res.stdout, res.stderr].filter((x) => x && x.trim()).join("\n").trim();
            resultEl.innerHTML = `<span style="color:${ok ? "var(--accent)" : "var(--danger)"}">${ok ? "✓" : "✗"} Exit ${res.code}</span>`;
            // Ausgabe des Clients direkt unter dem Skript anzeigen.
            let pre = listEl.querySelector(`[data-output="${scriptId}"]`);
            if (!pre) {
              pre = document.createElement("pre");
              pre.dataset.output = scriptId;
              pre.style.cssText = "background:var(--panel-2);border:1px solid var(--border);padding:8px;" +
                "border-radius:6px;margin:8px 0 0;font-size:12px;white-space:pre-wrap;overflow:auto;max-height:240px";
              resultEl.closest(".panel")?.appendChild(pre);
            }
            pre.textContent = out || t("u_keine_ausgabe");
          } catch (e) {
            resultEl.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
          }
        })
      );

      // Löschen
      listEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!(await uiConfirm(t("u_skript_loschen"), { okText: t("delete"), danger: true }))) return;
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

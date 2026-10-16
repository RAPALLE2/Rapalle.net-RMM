// settings.js (Modul "deployment")
// ---------------------------------
// Deployment-Server: öffentliche Seite /deployment (Einstellungen) und die
// Dateien darin. Gezeigt in der Modul-App (Einstellungen) und als App
// "Deployment" im Startmenü (Recht manage_settings).
// Backend: deployment_routes.py

import { api } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";


export function renderDeploymentApp(body) {
  body.innerHTML = `<div style="padding:16px;height:100%;overflow:auto;box-sizing:border-box"></div>`;
  return renderDeploymentSettings(body.firstElementChild);
}

export async function renderDeploymentSettings(root) {
  root.innerHTML = `<div style="font-size:13px">
    <div class="tab-bar" style="padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:12px">
      <button class="tab-btn active" data-t="page">🌐 Seite</button>
      <button class="tab-btn" data-t="files">📁 Dateien</button>
    </div>
    <div data-pane="page">
      <p style="color:var(--subtext);margin-top:0">${t("set_deploy_hint")}</p>
      <div id="ge-deploy" style="color:var(--subtext)">${t("loading")}</div>
    </div>
    <div data-pane="files" style="display:none"></div>
  </div>`;
  root.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => {
    root.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("active", x === b));
    root.querySelectorAll("[data-pane]").forEach((p) => { p.style.display = p.dataset.pane === b.dataset.t ? "" : "none"; });
    if (b.dataset.t === "files") drawFiles();
  }));

  // ---------------- Seite ----------------
    (async () => {
      const box = root.querySelector("#ge-deploy");
      if (!box) return;
      let cfg;
      try {
        cfg = await api.getDeployment();
      } catch (e) {
        box.textContent = t("set_unavailable") + ": " + e.message;
        return;
      }
      const origin = location.origin;
      box.innerHTML = `
        <label style="display:flex;gap:9px;align-items:flex-start;padding:7px 8px;
                      border-radius:8px;cursor:pointer;
                      background:${cfg.public ? "var(--panel-2)" : "transparent"}">
          <input type="checkbox" id="dep-public" style="margin-top:3px" ${cfg.public ? "checked" : ""} />
          <span>
            <span style="color:var(--text)">${t("set_dep_public")}</span>
            <div style="font-size:11.5px;margin-top:2px">
              <code>${esc(origin)}/deployment</code> · ${t("set_dep_public_hint")}
            </div>
          </span>
        </label>

        <div class="form-row" style="margin-top:10px">
          <label>${t("set_dep_page_title")}</label>
          <input type="text" id="dep-title" value="${esc(cfg.title || "Deployment")}" />
        </div>

        <div style="margin-top:10px">
          <label style="display:block;margin-bottom:4px">${t("set_dep_html")}</label>
          <textarea id="dep-html" spellcheck="false" rows="10"
            placeholder="${t("set_dep_html_ph")}"
            style="width:100%;font-family:ui-monospace,monospace">${esc(cfg.html || "")}</textarea>
          <div style="font-size:11.5px;margin-top:4px">
            Wird der Code leer gelassen, zeigt die Seite automatisch alle Dateien
            aus dem Ordner. Eigene Dateien verlinkst du mit
            <code>/deployment/&lt;dateiname&gt;</code>, Bilder z.B. mit
            <code>&lt;img src="/deployment/logo.png"&gt;</code>.
          </div>
        </div>

        <div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap">
          <button class="btn-primary" id="dep-save">${t("save")}</button>
          <a class="btn" href="/deployment" target="_blank" rel="noopener">${t("set_open_page")} ↗</a>
          <span style="font-size:11.5px">Dateien verwaltest du unten unter „Dateien“.</span>
        </div>`;

      box.querySelector("#dep-save").addEventListener("click", async (e) => {
        const btn = e.currentTarget; const orig = btn.textContent;
        btn.disabled = true; btn.textContent = "…";
        try {
          const res = await api.saveDeployment({
            html: box.querySelector("#dep-html").value,
            title: box.querySelector("#dep-title").value,
            public: box.querySelector("#dep-public").checked,
          });
          cfg.public = res.public;
          window.notify?.("Deployment-Seite gespeichert.", "success", 4000);
        } catch (err) {
          window.notify?.(err.message, "error", 9000);
        }
        btn.disabled = false; btn.textContent = orig;
      });
    })();


  // ---------------- Dateien ----------------
  // Gemeinsamer Datei-Manager des Moduls "webserver" (Deployment setzt den Webserver voraus)
  const pane = root.querySelector('[data-pane="files"]');
  async function drawFiles() {
    const { renderFileManager } = await import("/modules/frontend/webserver/files.js");
    renderFileManager(pane, { api: "/api/deployment", publicBase: "/deployment" });
  }
}

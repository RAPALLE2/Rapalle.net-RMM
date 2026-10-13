// settings.js (Modul "deployment")
// ---------------------------------
// Deployment-Server: öffentliche Seite /deployment (Einstellungen) und die
// Dateien darin. Gezeigt in der Modul-App (Einstellungen) und als App
// "Deployment" im Startmenü (Recht manage_settings).
// Backend: deployment_routes.py

import { api, getToken } from "/js/app/api.js";
import { esc, uiConfirm, uiPrompt } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";

async function dreq(path, opts = {}) {
  const token = getToken() || "";
  const headers = { ...(token ? { Authorization: "Bearer " + token } : {}) };
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch("/api/deployment" + path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* egal */ }
    throw new Error(msg);
  }
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res;
}
const fmt = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : b >= 1024 ? Math.round(b / 1024) + " KB" : b + " B");

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
    if (b.dataset.t === "files") drawFiles("");
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
  const pane = root.querySelector('[data-pane="files"]');
  async function drawFiles(path) {
    let r;
    try { r = await dreq(`/files?path=${encodeURIComponent(path)}`); }
    catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    const link = (name) => `${location.origin}/deployment/${[...path.split("/").filter(Boolean), name].map(encodeURIComponent).join("/")}`;
    pane.innerHTML = `
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
        ${path ? `<button class="btn btn-sm btn-icon" data-up title="Hoch">↑</button>` : ""}
        <span style="flex:1;color:var(--subtext)">📁 /deployment/${esc(path)}</span>
        <span style="font-size:11.5px;color:${r.public ? "var(--warn)" : "var(--subtext)"}">${r.public ? "🌐 öffentlich erreichbar" : "🔒 Seite nicht freigegeben"}</span>
        <button class="btn btn-sm" data-mk>📁 Neuer Ordner</button>
        <button class="btn-primary btn-sm" data-upl>⬆ Hochladen</button>
        <input type="file" data-file multiple hidden>
      </div>
      <div data-drop style="border:1px solid var(--border);border-radius:8px">
      ${r.entries.length ? r.entries.map((e) => `
        <div style="display:flex;gap:8px;align-items:center;padding:6px 10px;border-bottom:1px solid var(--border)">
          <span style="cursor:${e.is_dir ? "pointer" : "default"};flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" ${e.is_dir ? `data-dir="${esc(e.name)}"` : ""}>
            ${e.is_dir ? "📁" : "📄"} ${esc(e.name)}</span>
          <span style="color:var(--subtext);font-size:11.5px">${e.is_dir ? "" : fmt(e.size)}</span>
          ${!e.is_dir ? `<button class="btn-tool" data-copy="${esc(link(e.name))}" title="Link kopieren">🔗</button>` : ""}
          <button class="btn-tool" data-ren="${esc(e.name)}" title="Umbenennen">✏️</button>
          <button class="btn-tool" data-del="${esc(e.name)}" title="Löschen">🗑️</button>
        </div>`).join("") : `<div style="padding:14px;color:var(--subtext)">Leer – Dateien hierher ziehen oder „Hochladen“.</div>`}
      </div>`;
    const sub = (n) => [path, n].filter(Boolean).join("/");
    pane.querySelector("[data-up]")?.addEventListener("click", () => drawFiles(path.split("/").slice(0, -1).join("/")));
    pane.querySelectorAll("[data-dir]").forEach((el) => el.addEventListener("click", () => drawFiles(sub(el.dataset.dir))));
    pane.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
      navigator.clipboard?.writeText(b.dataset.copy); window.notify?.("Link kopiert", "success");
    }));
    pane.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm(`„${b.dataset.del}“ löschen?`, { okText: "Löschen", danger: true }))) return;
      try { await dreq("/delete", { method: "POST", body: JSON.stringify({ path: sub(b.dataset.del) }) }); drawFiles(path); }
      catch (e) { window.notify?.(e.message, "error"); }
    }));
    pane.querySelectorAll("[data-ren]").forEach((b) => b.addEventListener("click", async () => {
      const n = await uiPrompt("Umbenennen", { value: b.dataset.ren });
      if (!n?.trim() || n === b.dataset.ren) return;
      try { await dreq("/move", { method: "POST", body: JSON.stringify({ src: sub(b.dataset.ren), dst: sub(n.trim()) }) }); drawFiles(path); }
      catch (e) { window.notify?.(e.message, "error"); }
    }));
    pane.querySelector("[data-mk]").addEventListener("click", async () => {
      const n = await uiPrompt("Neuer Ordner", { placeholder: "Ordnername" });
      if (!n?.trim()) return;
      try { await dreq("/mkdir", { method: "POST", body: JSON.stringify({ path: sub(n.trim()) }) }); drawFiles(path); }
      catch (e) { window.notify?.(e.message, "error"); }
    });
    const upload = async (files) => {
      if (!files.length) return;
      const fd = new FormData(); fd.append("path", path); files.forEach((f) => fd.append("files", f));
      try { await dreq("/upload", { method: "POST", body: fd }); window.notify?.(`${files.length} Datei(en) hochgeladen`, "success"); }
      catch (e) { window.notify?.(e.message, "error"); }
      drawFiles(path);
    };
    const fi = pane.querySelector("[data-file]");
    pane.querySelector("[data-upl]").addEventListener("click", () => fi.click());
    fi.addEventListener("change", () => upload([...fi.files]));
    const drop = pane.querySelector("[data-drop]");
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.style.outline = "2px dashed var(--accent)"; });
    drop.addEventListener("dragleave", () => { drop.style.outline = ""; });
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.style.outline = ""; upload([...(e.dataTransfer?.files || [])]); });
  }
}

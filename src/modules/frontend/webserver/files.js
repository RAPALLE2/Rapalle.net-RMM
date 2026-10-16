// files.js (Modul "webserver") - gemeinsamer Datei-Manager fuer Website-Ordner
// ---------------------------------------------------------------------------
// Benutzt von den Websites (App "Webserver") UND vom Deployment-Server.
//   renderFileManager(pane, { api: "/api/webserver/sites/<name>", publicBase: "/<name>" })
//   api        Basis der Datei-Schnittstelle: GET {api}/files?path=, POST {api}/upload|mkdir|delete|move
//   publicBase oeffentliche Adresse fuer "Link kopieren" (z.B. "/deployment")
import { getToken } from "/js/app/api.js";
import { esc, uiConfirm, uiPrompt } from "/js/app/utils.js";

async function freq(url, opts = {}) {
  const token = getToken() || "";
  const headers = { ...(token ? { Authorization: "Bearer " + token } : {}) };
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(url, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* egal */ }
    throw new Error(msg);
  }
  return res.json();
}
const fmt = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : b >= 1024 ? Math.round(b / 1024) + " KB" : b + " B");

export function renderFileManager(pane, { api, publicBase }) {
  const draw = async (path) => {
    let r;
    try { r = await freq(`${api}/files?path=${encodeURIComponent(path)}`); }
    catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    const link = (name) => `${location.origin}${publicBase}/${[...path.split("/").filter(Boolean), name].map(encodeURIComponent).join("/")}`;
    pane.innerHTML = `
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
        ${path ? `<button class="btn btn-sm btn-icon" data-up title="Hoch">↑</button>` : ""}
        <span style="flex:1;color:var(--subtext);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📁 ${esc(publicBase)}/${esc(path)}</span>
        <span style="font-size:11.5px;color:${r.public ? "var(--warn)" : "var(--subtext)"}">${r.public ? "🌐 öffentlich erreichbar" : "🔒 nicht freigegeben"}</span>
        <button class="btn btn-sm" data-mk>📁 Neuer Ordner</button>
        <button class="btn btn-sm" data-new>📝 Neue Datei</button>
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
    const post = (what, body) => freq(`${api}/${what}`, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body) });
    pane.querySelector("[data-up]")?.addEventListener("click", () => draw(path.split("/").slice(0, -1).join("/")));
    pane.querySelectorAll("[data-dir]").forEach((el) => el.addEventListener("click", () => draw(sub(el.dataset.dir))));
    pane.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
      navigator.clipboard?.writeText(b.dataset.copy); window.notify?.("Link kopiert", "success");
    }));
    pane.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm(`„${b.dataset.del}“ löschen?`, { okText: "Löschen", danger: true }))) return;
      try { await post("delete", { path: sub(b.dataset.del) }); draw(path); } catch (e) { window.notify?.(e.message, "error"); }
    }));
    pane.querySelectorAll("[data-ren]").forEach((b) => b.addEventListener("click", async () => {
      const n = await uiPrompt("Umbenennen", { value: b.dataset.ren });
      if (!n?.trim() || n === b.dataset.ren) return;
      try { await post("move", { src: sub(b.dataset.ren), dst: sub(n.trim()) }); draw(path); } catch (e) { window.notify?.(e.message, "error"); }
    }));
    pane.querySelector("[data-mk]").addEventListener("click", async () => {
      const n = await uiPrompt("Neuer Ordner", { placeholder: "Ordnername" });
      if (!n?.trim()) return;
      try { await post("mkdir", { path: sub(n.trim()) }); draw(path); } catch (e) { window.notify?.(e.message, "error"); }
    });
    pane.querySelector("[data-new]").addEventListener("click", async () => {
      const n = await uiPrompt("Neue Datei", { placeholder: "index.html" });
      if (!n?.trim()) return;
      const fd = new FormData(); fd.append("path", path); fd.append("files", new File([""], n.trim(), { type: "text/plain" }));
      try { await post("upload", fd); draw(path); } catch (e) { window.notify?.(e.message, "error"); }
    });
    const upload = async (files) => {
      if (!files.length) return;
      const fd = new FormData(); fd.append("path", path); files.forEach((f) => fd.append("files", f));
      try { await post("upload", fd); window.notify?.(`${files.length} Datei(en) hochgeladen`, "success"); }
      catch (e) { window.notify?.(e.message, "error"); }
      draw(path);
    };
    const fi = pane.querySelector("[data-file]");
    pane.querySelector("[data-upl]").addEventListener("click", () => fi.click());
    fi.addEventListener("change", () => upload([...fi.files]));
    const drop = pane.querySelector("[data-drop]");
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.style.outline = "2px dashed var(--accent)"; });
    drop.addEventListener("dragleave", () => { drop.style.outline = ""; });
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.style.outline = ""; upload([...(e.dataTransfer?.files || [])]); });
  };
  draw("");
}

// app.js (Modul "dist") - Verteilung verwalten (/dist)
// ----------------------------------------------------
// Ordner (enrollment/agent/client + eigene), je Ordner öffentlich/privat, Dateien
// hochladen/löschen, öffentliche Links kopieren. Alles unter rmm.url.de/dist.

import { getToken } from "/js/app/api.js";
import { esc, uiConfirm } from "/js/app/utils.js";

async function dq(path, opts = {}) {
  const headers = { Authorization: "Bearer " + (getToken() || "") };
  if (opts.body !== undefined && typeof opts.body !== "string" && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json"; opts = { ...opts, body: JSON.stringify(opts.body) };
  }
  const res = await fetch("/api/dist" + path, { ...opts, headers });
  if (!res.ok) { let m = res.statusText; try { const j = await res.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* egal */ } throw new Error(m); }
  return res.json();
}

const sz = (n) => { let f = n; for (const u of ["B", "KB", "MB", "GB"]) { if (f < 1024 || u === "GB") return (u === "B" ? f + " B" : f.toFixed(1) + " " + u); f /= 1024; } };
const copy = (t) => navigator.clipboard?.writeText(t).then(() => window.notify?.("Link kopiert", "success", 1500));

export function renderDist(body) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;min-height:0;font-size:13px">
      <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--border)">
        <b style="font-size:14px">📦 Verteilung</b>
        <span style="color:var(--subtext);font-size:12px;flex:1">öffentlich erreichbar unter <code>/dist/&lt;ordner&gt;</code></span>
        <button class="btn btn-sm" data-new>＋ Ordner</button>
      </div>
      <div style="flex:1;display:flex;min-height:0">
        <div data-folders style="width:230px;border-right:1px solid var(--border);overflow:auto;padding:8px"></div>
        <div data-main style="flex:1;overflow:auto;padding:12px 14px">Wähle einen Ordner.</div>
      </div>
    </div>`;
  const foldersEl = body.querySelector("[data-folders]");
  const main = body.querySelector("[data-main]");
  let folders = [], active = null;

  const loadFolders = async () => {
    try { folders = (await dq("/folders")).folders; } catch (e) { foldersEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    if (!active && folders.length) active = folders[0].name;
    foldersEl.innerHTML = folders.map((f) => `
      <div class="dist-f" data-f="${esc(f.name)}" style="display:flex;align-items:center;gap:7px;padding:7px 9px;border-radius:8px;cursor:pointer;${f.name === active ? "background:color-mix(in srgb,var(--accent) 16%,transparent)" : ""}">
        <span>${f.public ? "🌐" : "🔒"}</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><b>${esc(f.label)}</b>
          <div style="font-size:11px;color:var(--subtext)">${f.files} · ${sz(f.size)}</div></span>
      </div>`).join("");
    foldersEl.querySelectorAll("[data-f]").forEach((el) => el.addEventListener("click", () => { active = el.dataset.f; loadFolders(); drawMain(); }));
    drawMain();
  };

  const drawMain = async () => {
    const f = folders.find((x) => x.name === active);
    if (!f) { main.innerHTML = `<div style="color:var(--subtext)">Kein Ordner.</div>`; return; }
    let data;
    try { data = await dq(`/folders/${encodeURIComponent(active)}/files`); } catch (e) { main.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    main.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
        <b style="font-size:15px">${f.public ? "🌐" : "🔒"} ${esc(f.label)}</b>
        <code style="font-size:12px;color:var(--subtext)">/dist/${esc(f.name)}</code>
        <span style="flex:1"></span>
        <label style="display:flex;gap:6px;align-items:center;font-size:12.5px;cursor:pointer">
          <input type="checkbox" data-public ${f.public ? "checked" : ""}> öffentlich</label>
        <button class="btn btn-sm" data-rename>Umbenennen</button>
        ${f.core ? "" : `<button class="btn btn-sm btn-danger" data-delfolder>Ordner löschen</button>`}
      </div>
      <div data-drop style="border:2px dashed var(--border);border-radius:10px;padding:16px;text-align:center;color:var(--subtext);margin-bottom:10px;cursor:pointer">
        Dateien hierher ziehen oder <b>klicken zum Auswählen</b> (exe, msi, deb, scripts …)
        <input type="file" data-fileinput multiple style="display:none">
      </div>
      ${data.files.length ? `<table class="data-table" style="width:100%;font-size:12.5px">
        <thead><tr><th style="text-align:left">Datei</th><th>Größe</th><th style="text-align:left">Öffentlicher Link</th><th></th></tr></thead>
        <tbody>${data.files.map((it) => `<tr>
          <td><b>${esc(it.name)}</b></td><td style="text-align:center">${sz(it.size)}</td>
          <td><code style="font-size:11.5px">${esc(it.url || ("/dist/" + f.name + "/" + it.name))}</code></td>
          <td style="white-space:nowrap;text-align:right">
            <button class="btn-tool" data-copy="${esc(it.url || ("/dist/" + f.name + "/" + it.name))}" title="Link kopieren">📋</button>
            <button class="btn-tool" data-dl="${esc("/dist/" + f.name + "/" + it.name)}" title="Herunterladen">⬇</button>
            <button class="btn-tool" data-del="${esc(it.name)}" title="Löschen">🗑️</button></td></tr>`).join("")}</tbody></table>`
        : `<div style="color:var(--subtext)">Noch keine Dateien.</div>`}`;

    main.querySelector("[data-public]").addEventListener("change", async (e) => {
      try { await dq(`/folders/${encodeURIComponent(active)}`, { method: "PATCH", body: { public: e.target.checked } }); loadFolders(); }
      catch (err) { window.notify?.(err.message, "error"); }
    });
    main.querySelector("[data-rename]").addEventListener("click", async () => {
      const lbl = prompt("Anzeigename:", f.label); if (lbl == null) return;
      try { await dq(`/folders/${encodeURIComponent(active)}`, { method: "PATCH", body: { label: lbl } }); loadFolders(); } catch (err) { window.notify?.(err.message, "error"); }
    });
    main.querySelector("[data-delfolder]")?.addEventListener("click", async () => {
      if (!(await uiConfirm(`Ordner „${f.label}“ mit allen Dateien löschen?`, { okText: "Löschen", danger: true }))) return;
      try { await dq(`/folders/${encodeURIComponent(active)}`, { method: "DELETE" }); active = null; loadFolders(); } catch (err) { window.notify?.(err.message, "error"); }
    });
    // Upload (Klick + Drag&Drop)
    const drop = main.querySelector("[data-drop]"), input = main.querySelector("[data-fileinput]");
    const upload = async (fileList) => {
      if (!fileList || !fileList.length) return;
      const fd = new FormData();
      for (const file of fileList) fd.append("files", file);
      drop.textContent = "⏳ lade hoch …";
      try { await dq(`/folders/${encodeURIComponent(active)}/upload`, { method: "POST", body: fd }); window.notify?.("Hochgeladen", "success"); loadFolders(); }
      catch (err) { window.notify?.(err.message, "error"); drawMain(); }
    };
    drop.addEventListener("click", () => input.click());
    input.addEventListener("change", () => upload(input.files));
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.style.borderColor = "var(--accent)"; });
    drop.addEventListener("dragleave", () => { drop.style.borderColor = "var(--border)"; });
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.style.borderColor = "var(--border)"; upload(e.dataTransfer.files); });
    main.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copy(b.dataset.copy)));
    main.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => window.open(b.dataset.dl, "_blank")));
    main.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm(`„${b.dataset.del}“ löschen?`, { okText: "Löschen", danger: true }))) return;
      try { await dq(`/folders/${encodeURIComponent(active)}/files/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); loadFolders(); } catch (err) { window.notify?.(err.message, "error"); }
    }));
  };

  body.querySelector("[data-new]").addEventListener("click", async () => {
    const name = prompt("Ordnername (nur Buchstaben/Zahlen/-_.):"); if (!name) return;
    try { await dq("/folders", { method: "POST", body: { name, label: name, public: true } }); active = name; loadFolders(); }
    catch (e) { window.notify?.(e.message, "error", 6000); }
  });
  loadFolders();
}

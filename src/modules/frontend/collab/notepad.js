// notepad.js (Modul "collab", Teilfunktion "notepad")
// ---------------------------------------------------
// Notizheft:
//   * "Schmierzettel" = temporäre Notizen (ohne Ordner). Sie bleiben bei
//     Neustart/Update erhalten (Server), bis man auf "🧹 Aufräumen" klickt.
//   * Ordner speichern Notizen dauerhaft; "Aufräumen" lässt sie in Ruhe.
//   * Gespeichert wird automatisch beim Tippen.
// Backend: notepad_routes.py (/api/collab/notepad)

import { getToken } from "/js/app/api.js";
import { esc, uiConfirm, uiPrompt, uiChoice } from "/js/app/utils.js";
import { registerCleanup } from "/js/app/windowmanager.js";

const TEMP = "__temp__";       // Ansicht "Schmierzettel"
// Notiz-IDs sind Pfade ("Ordner/Titel.md") - jedes Segment einzeln kodieren.
const encId = (id) => String(id).split("/").map(encodeURIComponent).join("/");
// Automatisch vergebener Titel ("Notiz 2026-10-13 21.38.08") -> Titelfeld leer lassen
const isAutoTitle = (t) => /^Notiz \d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}( \(\d+\))?$/.test(t || "");

async function req(path, opts = {}) {
  const token = getToken() || "";
  const res = await fetch("/api/collab/notepad" + path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}), ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* egal */ }
    throw new Error(msg);
  }
  return res.json();
}

export async function renderNotepad(body, win) {
  let data = { folders: [], notes: [] };
  let view = TEMP;              // TEMP oder folder_id
  let currentId = null;         // geöffnete Notiz
  let saveTimer = null;
  let pending = null;           // {id, fields}

  body.innerHTML = `
    <div class="np" style="display:flex;height:100%;min-height:0;font-size:13px">
      <aside style="width:200px;flex:none;border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0">
        <div style="padding:10px;display:flex;flex-direction:column;gap:6px">
          <button class="btn-primary btn-block" id="np-new">＋ Neue Notiz</button>
        </div>
        <div id="np-folders" style="flex:1;overflow:auto;padding:0 6px 6px"></div>
        <div style="padding:8px 10px;border-top:1px solid var(--border)">
          <button class="btn btn-sm btn-block" id="np-newfolder">📁 Neuer Ordner</button>
        </div>
      </aside>
      <section style="width:240px;flex:none;border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0">
        <div style="padding:10px;display:flex;gap:6px;align-items:center;border-bottom:1px solid var(--border)">
          <b id="np-viewname" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></b>
          <span id="np-viewtools"></span>
        </div>
        <div id="np-list" style="flex:1;overflow:auto"></div>
      </section>
      <main style="flex:1;min-width:0;display:flex;flex-direction:column;min-height:0">
        <div id="np-editor" style="flex:1;display:flex;flex-direction:column;min-height:0"></div>
      </main>
    </div>`;

  const $ = (s) => body.querySelector(s);

  // ------------------------------------------------------------ Speichern
  async function flush() {
    clearTimeout(saveTimer); saveTimer = null;
    if (!pending) return;
    const { id, fields } = pending; pending = null;
    try {
      const n = await req(`/notes/${encId(id)}`, { method: "PATCH", body: JSON.stringify(fields) });
      // Der Dateiname folgt dem Titel -> die ID kann sich geändert haben.
      if (n.id !== id) {
        if (currentId === id) currentId = n.id;
        if (pending?.id === id) pending.id = n.id;
      }
      const local = data.notes.find((x) => x.id === id);
      if (local) {
        // Objekt AKTUALISIEREN statt ersetzen: der Editor hält eine Referenz darauf
        // und muss die neue ID kennen. Weitergetippten Text/Titel nicht überschreiben.
        const { text: _t, title: _ti, ...rest } = n;
        Object.assign(local, rest);
      }
      const st = $("#np-state"); if (st) st.textContent = "✓ gespeichert";
      drawList();
    } catch (e) { window.notify?.("Notiz nicht gespeichert: " + e.message, "error"); }
  }
  function queueSave(id, fields) {
    if (pending && pending.id !== id) flush();
    pending = { id, fields: { ...(pending?.fields || {}), ...fields } };
    const st = $("#np-state"); if (st) st.textContent = "… speichert";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 600);
  }
  window.addEventListener("beforeunload", flush);
  if (win?.key) registerCleanup(win.key, () => { flush(); window.removeEventListener("beforeunload", flush); });

  // ------------------------------------------------------------ Ordner
  function drawFolders() {
    const tempCount = data.notes.filter((n) => !n.folder_id).length;
    const item = (id, icon, name, count) => `
      <div class="np-f" data-f="${esc(id)}" style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:6px;cursor:pointer;
           ${view === id ? "background:rgba(var(--accent-rgb),.14);color:var(--accent)" : ""}">
        <span>${icon}</span><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(name)}</span>
        <span style="font-size:11px;color:var(--subtext)">${count}</span>
      </div>`;
    $("#np-folders").innerHTML =
      item(TEMP, "🗒️", "Schmierzettel", tempCount)
      + `<div style="font-size:11px;color:var(--subtext);padding:10px 8px 4px">ORDNER</div>`
      + (data.folders.length
        ? data.folders.map((f) => item(f.id, "📁", f.name, data.notes.filter((n) => n.folder_id === f.id).length)).join("")
        : `<div style="font-size:12px;color:var(--subtext);padding:4px 8px">Noch keine Ordner</div>`);
    $("#np-folders").querySelectorAll("[data-f]").forEach((el) =>
      el.addEventListener("click", async () => { await flush(); view = el.dataset.f; currentId = null; drawAll(); }));
  }

  // ------------------------------------------------------------ Liste
  function notesInView() {
    return data.notes.filter((n) => (view === TEMP ? !n.folder_id : n.folder_id === view));
  }

  function drawList() {
    const folder = data.folders.find((f) => f.id === view);
    $("#np-viewname").textContent = view === TEMP ? "🗒️ Schmierzettel" : `📁 ${folder?.name || ""}`;
    $("#np-viewtools").innerHTML = view === TEMP
      ? `<button class="btn btn-sm btn-warn" id="np-clean" title="Alle temporären Notizen löschen (Ordner bleiben)">🧹 Aufräumen</button>`
      : `<button class="btn-tool" id="np-rename" title="Ordner umbenennen">✏️</button>
         <button class="btn-tool" id="np-delfolder" title="Ordner löschen">🗑️</button>`;
    $("#np-clean")?.addEventListener("click", clean);
    $("#np-rename")?.addEventListener("click", renameFolder);
    $("#np-delfolder")?.addEventListener("click", deleteFolder);

    const list = notesInView();
    $("#np-list").innerHTML = list.length ? list.map((n) => {
      const title = n.title || (n.text || "").split("\n")[0] || "Ohne Titel";
      const prev = (n.text || "").replace(/\s+/g, " ").slice(0, 80);
      return `<div data-n="${esc(n.id)}" style="padding:9px 12px;border-bottom:1px solid var(--border);cursor:pointer;
                ${n.id === currentId ? "background:rgba(var(--accent-rgb),.10)" : ""}">
          <div style="font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(title)}</div>
          <div style="font-size:11.5px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(prev) || "&nbsp;"}</div>
          <div style="font-size:10.5px;color:var(--subtext);margin-top:2px">${new Date(n.updated).toLocaleString()}</div>
        </div>`;
    }).join("") : `<div style="padding:14px;font-size:12.5px;color:var(--subtext)">${view === TEMP
      ? "Keine temporären Notizen. Mit „＋ Neue Notiz“ eine anlegen."
      : "Dieser Ordner ist leer. Notizen lassen sich über „In Ordner speichern“ hierher verschieben."}</div>`;
    $("#np-list").querySelectorAll("[data-n]").forEach((el) =>
      el.addEventListener("click", async () => { await flush(); currentId = el.dataset.n; drawList(); drawEditor(); }));
  }

  // ------------------------------------------------------------ Editor
  function drawEditor() {
    const n = data.notes.find((x) => x.id === currentId);
    const box = $("#np-editor");
    if (!n) {
      box.innerHTML = `<div style="margin:auto;color:var(--subtext);font-size:13px;text-align:center;padding:20px">
        Notiz auswählen oder neu anlegen.<br><span style="font-size:12px">Temporäre Notizen bleiben erhalten, bis du aufräumst.<br>
        Gespeichert in deinem Space unter <b>${esc(data.location || "Mein Space/Notizen")}</b> (auch im Explorer und per Relay).</span></div>`;
      return;
    }
    const folderName = data.folders.find((f) => f.id === n.folder_id)?.name;
    box.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;padding:10px;border-bottom:1px solid var(--border);flex-wrap:wrap">
        <input type="text" id="np-title" placeholder="${esc(isAutoTitle(n.title) ? n.title : "Titel")}" style="flex:1;min-width:120px"
          value="${esc(isAutoTitle(n.title) ? "" : (n.title || ""))}">
        <button class="btn btn-sm" id="np-move">${n.folder_id ? "📁 Verschieben" : "💾 In Ordner speichern"}</button>
        <button class="btn btn-sm btn-danger btn-icon" id="np-del" title="Notiz löschen">🗑️</button>
      </div>
      <textarea id="np-text" spellcheck="true" placeholder="Hier schreiben …"
        style="flex:1;border:0;border-radius:0;resize:none;padding:12px;font-size:13.5px;line-height:1.55;background:transparent;box-shadow:none"></textarea>
      <div style="display:flex;justify-content:space-between;padding:4px 10px;font-size:11px;color:var(--subtext);border-top:1px solid var(--border)">
        <span>${n.folder_id ? `📁 ${esc(folderName || "")}` : "🗒️ temporär"}</span><span id="np-state">✓ gespeichert</span>
      </div>`;
    const ta = $("#np-text");
    ta.value = n.text || "";
    $("#np-title").addEventListener("input", (e) => { n.title = e.target.value; queueSave(n.id, { title: n.title }); });
    ta.addEventListener("input", () => { n.text = ta.value; queueSave(n.id, { text: n.text }); });
    $("#np-del").addEventListener("click", async () => {
      if (!(await uiConfirm("Notiz löschen?", { okText: "Löschen", danger: true }))) return;
      pending = null; clearTimeout(saveTimer);
      try {
        await req(`/notes/${encId(n.id)}`, { method: "DELETE" });
        data.notes = data.notes.filter((x) => x.id !== n.id);
        currentId = null; drawAll();
      } catch (e) { window.notify?.(e.message, "error"); }
    });
    $("#np-move").addEventListener("click", () => moveNote(n));
    if (!n.text && !n.title) ta.focus();
  }

  // ------------------------------------------------------------ Aktionen
  async function newNote() {
    await flush();
    try {
      const n = await req("/notes", { method: "POST", body: JSON.stringify({ title: "", text: "", folder_id: view === TEMP ? null : view }) });
      data.notes.unshift(n);
      currentId = n.id; drawAll();
    } catch (e) { window.notify?.(e.message, "error"); }
  }

  async function clean() {
    const count = data.notes.filter((n) => !n.folder_id).length;
    if (!count) { window.notify?.("Keine temporären Notizen vorhanden.", "info"); return; }
    if (!(await uiConfirm(`${count} temporäre Notiz${count === 1 ? "" : "en"} löschen?`,
      { description: "Notizen in Ordnern bleiben erhalten.", okText: "Aufräumen", danger: true }))) return;
    await flush();
    try {
      await req("/clean", { method: "POST" });
      data.notes = data.notes.filter((n) => n.folder_id);
      if (!data.notes.some((n) => n.id === currentId)) currentId = null;
      drawAll();
      window.notify?.("Schmierzettel aufgeräumt.", "success");
    } catch (e) { window.notify?.(e.message, "error"); }
  }

  async function newFolder() {
    const name = await uiPrompt("Neuer Ordner", { placeholder: "Name des Ordners" });
    if (!name?.trim()) return null;
    try {
      const f = await req("/folders", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      data.folders.push(f);
      drawFolders();
      return f;
    } catch (e) { window.notify?.(e.message, "error"); return null; }
  }

  async function moveNote(n) {
    await flush();
    const opts = data.folders.filter((f) => f.id !== n.folder_id).map((f) => ({ label: `📁 ${f.name}`, value: f.id }));
    if (n.folder_id) opts.unshift({ label: "🗒️ Zurück zu den Schmierzetteln (temporär)", value: TEMP });
    opts.push({ label: "＋ Neuer Ordner …", value: "__new__" });
    let target = await uiChoice("Notiz speichern in …", opts);
    if (!target) return;
    if (target === "__new__") { const f = await newFolder(); if (!f) return; target = f.id; }
    try {
      const upd = await req(`/notes/${encId(n.id)}`, { method: "PATCH", body: JSON.stringify({ folder_id: target === TEMP ? null : target }) });
      Object.assign(n, upd);
      currentId = n.id;
      view = n.folder_id || TEMP;
      drawAll();
    } catch (e) { window.notify?.(e.message, "error"); }
  }

  async function renameFolder() {
    const f = data.folders.find((x) => x.id === view);
    if (!f) return;
    const name = await uiPrompt("Ordner umbenennen", { value: f.name });
    if (!name?.trim()) return;
    try {
      const nf = await req(`/folders/${encodeURIComponent(f.id)}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
      data = await req("");                 // IDs der Notizen im Ordner haben sich geändert
      view = nf.id; currentId = null; drawAll();
    }
    catch (e) { window.notify?.(e.message, "error"); }
  }

  async function deleteFolder() {
    const f = data.folders.find((x) => x.id === view);
    if (!f) return;
    if (!(await uiConfirm(`Ordner „${f.name}“ löschen?`,
      { description: "Die Notizen darin gehen nicht verloren - sie werden wieder zu temporären Notizen (Schmierzettel).", okText: "Löschen", danger: true }))) return;
    try {
      await req(`/folders/${encodeURIComponent(f.id)}`, { method: "DELETE" });
      data = await req("");                 // Notizen liegen jetzt im Schmierzettel (neue IDs)
      view = TEMP; currentId = null; drawAll();
    } catch (e) { window.notify?.(e.message, "error"); }
  }

  function drawAll() { drawFolders(); drawList(); drawEditor(); }

  $("#np-new").addEventListener("click", newNote);
  $("#np-newfolder").addEventListener("click", newFolder);

  try { data = await req(""); }
  catch (e) { $("#np-editor").innerHTML = `<div style="padding:16px;color:var(--danger)">${esc(e.message)}</div>`; }
  drawAll();
}

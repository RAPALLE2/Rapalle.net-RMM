// app.js (Modul "webserver") - App "Webserver"
// --------------------------------------------
// Links: Websites (anlegen mit Recht "Websites verwalten"). Rechts: die gewaehlte
// Website mit den Registern Dateien | Einstellungen (Titel, oeffentlich, Dateiliste,
// Mitglieder + Rollen, Adresse aendern, loeschen).
// Backend: webserver_routes.py (/api/webserver)

import { getToken } from "/js/app/api.js";
import { initSubjectPicker, readSubjectPicker, subjectPickerHtml } from "/js/app/subjectpicker.js";
import { esc, uiConfirm, uiPrompt } from "/js/app/utils.js";
import { renderFileManager } from "./files.js";

async function wreq(path, opts = {}) {
  const token = getToken() || "";
  const headers = { ...(token ? { Authorization: "Bearer " + token } : {}) };
  if (opts.body !== undefined && typeof opts.body !== "string") opts = { ...opts, body: JSON.stringify(opts.body) };
  if (opts.body) headers["Content-Type"] = "application/json";
  const res = await fetch("/api/webserver" + path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* egal */ }
    throw new Error(msg);
  }
  return res.json();
}

export function renderWebserver(body) {
  body.innerHTML = `
    <div style="display:flex;height:100%;min-height:0;font-size:13px">
      <div style="width:230px;flex:0 0 auto;border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0">
        <div style="padding:10px;display:flex;gap:6px;align-items:center;border-bottom:1px solid var(--border)">
          <b style="flex:1">🌍 Websites</b><button class="btn btn-sm" data-new style="display:none">＋ Neu</button></div>
        <div data-list style="flex:1;overflow:auto;padding:6px"></div>
      </div>
      <div data-main style="flex:1;min-width:0;overflow:auto;padding:12px">
        <div style="color:var(--subtext)">Website links auswählen.</div></div>
    </div>`;
  const listEl = body.querySelector("[data-list]");
  const main = body.querySelector("[data-main]");
  let data = { sites: [], manage: false };
  let current = null;

  async function loadList(select) {
    try { data = await wreq("/sites"); } catch (e) { listEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    body.querySelector("[data-new]").style.display = data.manage ? "" : "none";
    listEl.innerHTML = data.sites.length ? data.sites.map((s) => `
      <div data-s="${esc(s.name)}" style="padding:8px 10px;border-radius:6px;cursor:pointer;margin-bottom:2px;
           ${s.name === (select || current) ? "background:var(--panel-2);" : ""}">
        <div style="display:flex;gap:6px;align-items:center"><b style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${esc(s.title || s.name)}</b>
          <span title="${s.kind === "proxy" ? "Weiterleitung" : s.public ? "öffentlich" : "nicht freigegeben"}">${s.kind === "proxy" ? "↪" : ""}${s.public ? "🌐" : "🔒"}</span></div>
        <div style="font-size:11.5px;color:var(--subtext)">/${esc(s.name)}</div></div>`).join("")
      : `<div style="color:var(--subtext);padding:8px">${data.manage ? "Noch keine Website – „＋ Neu“." : "Du bist noch keiner Website zugeordnet."}</div>`;
    listEl.querySelectorAll("[data-s]").forEach((el) => el.addEventListener("click", () => openSite(el.dataset.s)));
    if (select) openSite(select);
  }

  function openSite(name, tab = "files") {
    current = name;
    listEl.querySelectorAll("[data-s]").forEach((el) => { el.style.background = el.dataset.s === name ? "var(--panel-2)" : ""; });
    const s = data.sites.find((x) => x.name === name);
    if (!s) return;
    const url = `${location.origin}/${s.name}/`;
    main.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
        <b style="font-size:15px">${esc(s.title || s.name)}</b>
        <a href="${esc(url)}" target="_blank" rel="noopener" style="font-size:12px">${esc(url)}</a>
        <button class="btn-tool" data-copy title="Adresse kopieren">🔗</button>
        <span style="flex:1"></span>
        <span style="font-size:12px;color:${s.public ? "var(--warn)" : "var(--subtext)"}">${s.public ? "🌐 öffentlich – ohne Anmeldung erreichbar" : "🔒 nicht freigegeben"}</span>
      </div>
      <div class="tab-bar" style="margin-bottom:10px">
        <button class="tab-btn ${tab === "files" ? "active" : ""}" data-t="files">📁 Dateien</button>
        ${s.level >= 2 ? `<button class="tab-btn ${tab === "settings" ? "active" : ""}" data-t="settings">⚙ Einstellungen</button>` : ""}
      </div>
      <div data-pane></div>`;
    main.querySelector("[data-copy]").addEventListener("click", () => { navigator.clipboard?.writeText(url); window.notify?.("Adresse kopiert", "success"); });
    main.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => openSite(name, b.dataset.t)));
    const pane = main.querySelector("[data-pane]");
    if (tab === "files" && s.kind === "proxy") {
      const up = s.upstream || {};
      pane.innerHTML = `<div style="border:1px solid var(--border);border-radius:8px;padding:12px">↪ <b>Weiterleitung</b> – diese Website hat keine Dateien,
        sie leitet jeden Abruf weiter an ${up.provider === "docker" ? `den Docker-Dienst <b>${esc(up.ref?.service || "")}</b> (Port ${esc(String(up.ref?.port || ""))})
        – verwaltet in der App „Docker“ unter „Veröffentlichen“` : `<code>${esc(up.url || "")}</code>`}.</div>`;
    } else if (tab === "files") renderFileManager(pane, { api: `/api/webserver/sites/${encodeURIComponent(name)}`, publicBase: `/${name}` });
    else drawSettings(pane, s);
  }

  async function drawSettings(pane, s) {
    let dir = { users: [], groups: [] };
    try { dir = await wreq("/directory"); } catch { /* ohne Liste */ }
    const uname = (id) => dir.users.find((u) => u.id === id)?.username || id;
    const gname = (id) => dir.groups.find((g) => g.id === id)?.name || id;
    let members = (s.members || []).map((m) => ({ ...m }));
    pane.innerHTML = `
      <div class="form-row"><label>Titel</label><input data-f="title" value="${esc(s.title || "")}"></div>
      <label style="display:flex;gap:8px;align-items:center;margin:8px 0;cursor:pointer">
        <input type="checkbox" data-f="public" ${s.public ? "checked" : ""} style="width:16px;height:16px;appearance:auto">
        <span><b>Öffentlich erreichbar</b> unter <code>/${esc(s.name)}/</code> – <b>ohne Anmeldung</b>. Nur einschalten, wenn im Ordner nichts Vertrauliches liegt.</span></label>
      <label style="display:flex;gap:8px;align-items:center;margin:8px 0;cursor:pointer">
        <input type="checkbox" data-f="listing" ${s.listing ? "checked" : ""} style="width:16px;height:16px;appearance:auto">
        <span>Automatische Dateiliste für Ordner ohne <code>index.html</code></span></label>
      <h4 style="margin:14px 0 6px">Mitglieder <span style="font-weight:400;color:var(--subtext);font-size:12px">– sehen den Ordner der Website in der App und im Explorer/Relay unter „Websites“</span></h4>
      <div data-mem></div>
      <div style="display:flex;gap:6px;margin:6px 0 14px"><button class="btn btn-sm" data-add>＋ Mitglieder hinzufügen</button></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn-primary" data-save>Speichern</button>
        ${data.manage ? `<button class="btn" data-rename>Adresse ändern …</button><button class="btn btn-danger" data-del>Website löschen</button>` : ""}
      </div>`;
    const memBox = pane.querySelector("[data-mem]");
    const drawMem = () => {
      memBox.innerHTML = members.length ? members.map((m, i) => `
        <div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
          <span style="flex:1">${m.type === "user" ? "👤 " + esc(uname(m.id)) : "👥 " + esc(gname(m.id))}</span>
          <select data-role="${i}" style="width:auto"><option value="editor" ${m.role !== "admin" ? "selected" : ""}>Bearbeiten (Dateien)</option>
            <option value="admin" ${m.role === "admin" ? "selected" : ""}>Admin (+ Einstellungen)</option></select>
          <button class="btn-tool" data-rm="${i}" title="Entfernen">✕</button></div>`).join("")
        : `<div style="color:var(--subtext)">Noch keine Mitglieder. Benutzer mit „Websites verwalten“ haben immer Zugriff.</div>`;
      memBox.querySelectorAll("[data-role]").forEach((sel) => sel.addEventListener("change", () => { members[+sel.dataset.role].role = sel.value; }));
      memBox.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { members.splice(+b.dataset.rm, 1); drawMem(); }));
    };
    drawMem();
    pane.querySelector("[data-add]").addEventListener("click", () => {
      const ov = document.createElement("div");
      ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
      ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;width:440px;max-width:100%;max-height:85vh;display:flex;flex-direction:column">
          <b style="margin-bottom:8px">Mitglieder hinzufügen</b>
          <div data-pick style="flex:1;overflow:auto">${subjectPickerHtml(dir, [], { name: "wsm" })}</div>
          <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Hinzufügen</button></div></div>`;
      document.body.appendChild(ov);
      initSubjectPicker(ov.querySelector("[data-pick]"));
      ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
      ov.querySelector("[data-ok]").addEventListener("click", () => {
        for (const sbj of readSubjectPicker(ov.querySelector("[data-pick]"))) {
          if (!members.some((m) => m.type === sbj.type && m.id === sbj.id)) members.push({ ...sbj, role: "editor" });
        }
        ov.remove(); drawMem();
      });
    });
    pane.querySelector("[data-save]").addEventListener("click", async () => {
      const pub = pane.querySelector('[data-f="public"]').checked;
      if (pub && !s.public && !(await uiConfirm(`„${s.name}“ öffentlich machen?`, {
        description: "Alles im Ordner der Website ist dann für jeden mit dem Link abrufbar – ohne Anmeldung.", okText: "Öffentlich machen" }))) return;
      try {
        await wreq(`/sites/${encodeURIComponent(s.name)}`, { method: "PATCH", body: {
          title: pane.querySelector('[data-f="title"]').value, public: pub,
          listing: pane.querySelector('[data-f="listing"]').checked, members } });
        window.notify?.("Gespeichert", "success");
        await loadList(s.name);
        openSite(s.name, "settings");
      } catch (e) { window.notify?.(e.message, "error"); }
    });
    pane.querySelector("[data-rename]")?.addEventListener("click", async () => {
      const n = await uiPrompt("Neue Adresse (rmm.url.de/…)", { value: s.name });
      if (!n?.trim() || n.trim() === s.name) return;
      try { const r = await wreq(`/sites/${encodeURIComponent(s.name)}`, { method: "PATCH", body: { new_name: n.trim() } });
        current = r.name; await loadList(r.name); } catch (e) { window.notify?.(e.message, "error"); }
    });
    pane.querySelector("[data-del]")?.addEventListener("click", async () => {
      if (!(await uiConfirm(`Website „${s.name}“ löschen?`, { description: "Die Website und ALLE ihre Dateien werden endgültig gelöscht.", okText: "Endgültig löschen", danger: true }))) return;
      try { await wreq(`/sites/${encodeURIComponent(s.name)}`, { method: "DELETE" }); current = null;
        main.innerHTML = `<div style="color:var(--subtext)">Website gelöscht.</div>`; loadList(); } catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  body.querySelector("[data-new]").addEventListener("click", async () => {
    const name = await uiPrompt("Adresse der neuen Website (rmm.url.de/…)", { placeholder: "z. B. firmen-wiki" });
    if (!name?.trim()) return;
    const title = (await uiPrompt("Titel (optional)", { placeholder: name.trim() })) || "";
    try { const r = await wreq("/sites", { method: "POST", body: { name: name.trim(), title } });
      window.notify?.(`Website /${r.name}/ angelegt (noch nicht öffentlich)`, "success"); loadList(r.name); }
    catch (e) { window.notify?.(e.message, "error", 8000); }
  });
  loadList();
}

// spaces.js (Modul "spaces")
// --------------------------
// Dateibrowser für die Spaces - als eigene App "Spaces" und als Register im
// Explorer (explorer.js holt renderSpacesBrowser über getModuleApi("spaces")).
//
//   Pfade sind dieselben wie im Relay:  Mein Space / Teams / Öffentlich / Mit mir geteilt / Alle Spaces
//   Werkzeuge: Ordner, Hochladen (auch Drag & Drop), Umbenennen, Löschen, Freigeben,
//              Teamspaces/Public Spaces verwalten, Git (Status, Commit, Pull, Push, Sync, Branches, Verlauf)
//
// Backend: /api/spaces (spaces_routes.py) und /api/spaces/git (git_ops.py)

import { getToken } from "/js/app/api.js";
import { esc, uiConfirm, uiPrompt, uiChoice } from "/js/app/utils.js";

// ------------------------------------------------------------------ HTTP
export async function sreq(path, opts = {}) {
  const token = getToken() || "";
  const headers = { ...(token ? { Authorization: "Bearer " + token } : {}), ...(opts.headers || {}) };
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch("/api/spaces" + path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* egal */ }
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res;
}
const q = (p) => encodeURIComponent(p);
const join = (...a) => a.filter(Boolean).join("/");
const fmtSize = (b) => (b >= 1073741824 ? (b / 1073741824).toFixed(1) + " GB" : b >= 1048576 ? (b / 1048576).toFixed(1) + " MB"
  : b >= 1024 ? Math.round(b / 1024) + " KB" : b + " B");
const ICON = (e) => {
  if (e.is_dir) return e.virtual ? "🗂️" : "📁";
  const ext = (e.name.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"].includes(ext)) return "🖼️";
  if (["mp3", "wav", "ogg", "flac", "m4a", "aac", "opus"].includes(ext)) return "🎵";
  if (["mp4", "webm", "mkv", "mov"].includes(ext)) return "🎬";
  if (["zip", "7z", "rar", "gz", "tar"].includes(ext)) return "🗜️";
  if (["md", "txt", "log", "json", "yml", "yaml", "ini", "csv", "py", "js", "sh", "ps1", "html", "css"].includes(ext)) return "📝";
  if (ext === "pdf") return "📕";
  return "📄";
};
const isImage = (n) => /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(n);
const isText = (n) => /\.(md|txt|log|json|ya?ml|ini|csv|py|js|sh|ps1|html|css|conf|xml)$/i.test(n);

/** Datei mit Anmeldung laden -> Objekt-URL (für <img>, <audio>, Download). */
export async function blobUrl(url) {
  const token = getToken() || "";
  const res = await fetch(url, { headers: token ? { Authorization: "Bearer " + token } : {} });
  if (!res.ok) throw new Error(res.status === 401 ? "Bitte anmelden" : res.statusText);
  return URL.createObjectURL(await res.blob());
}

async function download(path) {
  const u = await blobUrl(`/api/spaces/fs/download?path=${q(path)}`);
  const a = document.createElement("a");
  a.href = u; a.download = path.split("/").pop(); document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(u), 30000);
}

/** Bild/Datei für Chat oder Tickets in den eigenen Space hochladen -> {token, url, name, mime}. */
export async function uploadAttachment(file, area = "chat") {
  const fd = new FormData();
  fd.append("file", file); fd.append("area", area);
  return sreq("/attach", { method: "POST", body: fd });
}

// ------------------------------------------------------------------ Dialog-Hilfe
function modal(title, html, { wide = false } = {}) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;width:${wide ? 720 : 480}px;max-width:100%;
      max-height:90vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.5);font-size:13px">
    <div style="display:flex;align-items:center;padding:12px 14px;border-bottom:1px solid var(--border)">
      <b style="flex:1">${esc(title)}</b><button class="btn-tool" data-x>✕</button></div>
    <div data-body style="padding:14px;overflow:auto">${html}</div></div>`;
  const close = () => ov.remove();
  ov.querySelector("[data-x]").addEventListener("click", close);
  ov.addEventListener("mousedown", (e) => { if (e.target === ov) close(); });
  document.body.appendChild(ov);
  return { el: ov.querySelector("[data-body]"), close };
}

let _dir = null;
async function directory() { if (!_dir) _dir = await sreq("/directory"); return _dir; }

function pickerHtml(dir, selUsers = [], selGroups = []) {
  return `
    <input type="search" data-filter placeholder="Benutzer oder Gruppe suchen …" style="width:100%;margin-bottom:6px">
    <div data-pick style="max-height:220px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:4px 8px">
      ${dir.groups.map((g) => `<label data-name="${esc(g.name.toLowerCase())}" style="display:flex;padding:3px 0">
        <input type="checkbox" data-g="${esc(g.id)}" ${selGroups.includes(g.id) ? "checked" : ""}> 👥 ${esc(g.name)}</label>`).join("")}
      ${dir.users.map((u) => `<label data-name="${esc((u.username + " " + u.display_name).toLowerCase())}" style="display:flex;padding:3px 0">
        <input type="checkbox" data-u="${esc(u.id)}" ${selUsers.includes(u.id) ? "checked" : ""}> 👤 ${esc(u.display_name || u.username)}
        <span style="color:var(--subtext)">&nbsp;(${esc(u.username)})</span></label>`).join("")}
    </div>`;
}
function wirePicker(root) {
  root.querySelector("[data-filter]")?.addEventListener("input", (e) => {
    const v = e.target.value.toLowerCase();
    root.querySelectorAll("[data-pick] label").forEach((l) => { l.style.display = l.dataset.name.includes(v) ? "flex" : "none"; });
  });
  return () => ({
    users: [...root.querySelectorAll("[data-u]:checked")].map((c) => c.dataset.u),
    groups: [...root.querySelectorAll("[data-g]:checked")].map((c) => c.dataset.g),
  });
}

// ------------------------------------------------------------------ Freigeben
async function shareDialog(path, ref, onDone) {
  const dir = await directory();
  const all = await sreq("/shares");
  const name = path.split("/").pop();
  const m = modal(`„${name}“ freigeben`, `
    <div class="tab-bar" style="margin-bottom:12px">
      <button class="tab-btn active" data-k="users">👥 An Benutzer/Gruppen</button>
      <button class="tab-btn" data-k="link">🔗 Per Link</button>
    </div>
    <div data-pane="users">${pickerHtml(dir)}
      <label style="margin-top:8px"><input type="checkbox" data-write> Empfänger dürfen auch ändern</label>
    </div>
    <div data-pane="link" style="display:none;color:var(--subtext);line-height:1.5">
      Jeder <b>angemeldete</b> Benutzer mit dem Link kann lesen. Ohne Anmeldung ist nichts abrufbar.
    </div>
    <div class="form-row" style="margin-top:10px"><label>Gültig für (Tage, leer = unbegrenzt)</label>
      <input type="number" data-exp min="1" style="width:120px"></div>
    <div style="display:flex;gap:8px;margin-top:6px"><button class="btn-primary" data-ok>Freigeben</button></div>
    <div data-existing style="margin-top:14px"></div>`);
  let kind = "users";
  m.el.querySelectorAll("[data-k]").forEach((b) => b.addEventListener("click", () => {
    kind = b.dataset.k;
    m.el.querySelectorAll("[data-k]").forEach((x) => x.classList.toggle("active", x === b));
    m.el.querySelectorAll("[data-pane]").forEach((p) => { p.style.display = p.dataset.pane === kind ? "" : "none"; });
  }));
  const pick = wirePicker(m.el);
  const drawExisting = (shares) => {
    const box = m.el.querySelector("[data-existing]");
    const mine = shares.filter((s) => ref && s.space_id === ref.space_id && s.path === ref.space_rel);
    box.innerHTML = mine.length ? `<div style="font-size:11px;color:var(--subtext);margin-bottom:4px">BESTEHENDE FREIGABEN</div>` + mine.map((s) => `
      <div style="display:flex;gap:8px;align-items:center;padding:5px 0;border-top:1px solid var(--border)">
        <span style="flex:1">${s.kind === "link" ? "🔗 Link" : `👥 ${s.users.length} Benutzer, ${s.groups.length} Gruppen${s.write ? " · ändern" : ""}`}</span>
        ${s.kind === "link" ? `<button class="btn btn-sm" data-copy="${esc(location.origin + "/#space-share=" + s.token)}">Link kopieren</button>` : ""}
        <button class="btn btn-sm btn-danger" data-del="${esc(s.id)}">Entfernen</button></div>`).join("") : "";
    box.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
      navigator.clipboard?.writeText(b.dataset.copy); window.notify?.("Link kopiert", "success");
    }));
    box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      await sreq(`/shares/${b.dataset.del}`, { method: "DELETE" });
      drawExisting((await sreq("/shares")).mine);
    }));
  };
  drawExisting(all.mine);
  m.el.querySelector("[data-ok]").addEventListener("click", async () => {
    const exp = parseInt(m.el.querySelector("[data-exp]").value, 10) || null;
    const sel = pick();
    try {
      const s = await sreq("/shares", { method: "POST", body: JSON.stringify({
        path, kind, users: sel.users, groups: sel.groups, write: m.el.querySelector("[data-write]").checked, expires_days: exp }) });
      if (s.kind === "link") {
        const link = location.origin + "/#space-share=" + s.token;
        navigator.clipboard?.writeText(link);
        window.notify?.("Link erstellt und kopiert", "success");
      } else window.notify?.("Freigegeben", "success");
      drawExisting((await sreq("/shares")).mine);
      onDone?.();
    } catch (e) { window.notify?.(e.message, "error"); }
  });
}

// ------------------------------------------------------------------ Spaces verwalten
async function spaceDialog(kind, existing, onDone) {
  const dir = await directory();
  const users = (existing?.members || []).filter((x) => x.type === "user").map((x) => x.id);
  const groups = (existing?.members || []).filter((x) => x.type === "group").map((x) => x.id);
  const admins = new Set((existing?.members || []).filter((x) => x.role === "admin").map((x) => x.id));
  const m = modal(existing ? `${existing.name} bearbeiten` : (kind === "team" ? "Neuer Teamspace" : "Neuer Public Space"), `
    <div class="form-row"><label>Name</label><input type="text" data-name value="${esc(existing?.name || "")}"></div>
    <div class="form-row"><label>Beschreibung</label><input type="text" data-desc value="${esc(existing?.description || "")}"></div>
    ${kind === "team" ? `<div class="form-row"><label>Mitglieder</label>${pickerHtml(dir, users, groups)}</div>
      <div style="font-size:12px;color:var(--subtext)">Du bist automatisch Admin. Mitglieder können lesen und schreiben.</div>`
      : `<div style="font-size:12px;color:var(--subtext)">Public Spaces sind für alle mit dem Recht „Public Spaces benutzen“ les- und schreibbar.</div>`}
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn-primary" data-ok>${existing ? "Speichern" : "Anlegen"}</button>
      ${existing ? `<button class="btn btn-danger" data-del>Space löschen</button>` : ""}
    </div>`);
  const pick = kind === "team" ? wirePicker(m.el) : () => ({ users: [], groups: [] });
  m.el.querySelector("[data-ok]").addEventListener("click", async () => {
    const sel = pick();
    const members = [
      ...sel.users.map((id) => ({ type: "user", id, role: admins.has(id) ? "admin" : "member" })),
      ...sel.groups.map((id) => ({ type: "group", id, role: admins.has(id) ? "admin" : "member" })),
    ];
    if (existing?.owner && kind === "team" && !members.some((x) => x.id === existing.owner)) {
      members.unshift({ type: "user", id: existing.owner, role: "admin" });
    }
    const body = { name: m.el.querySelector("[data-name]").value, description: m.el.querySelector("[data-desc]").value };
    if (kind === "team") body.members = members;
    try {
      if (existing) await sreq(`/${encodeURIComponent(existing.id)}`, { method: "PATCH", body: JSON.stringify(body) });
      else await sreq("", { method: "POST", body: JSON.stringify({ kind, ...body }) });
      m.close(); onDone?.();
    } catch (e) { window.notify?.(e.message, "error"); }
  });
  m.el.querySelector("[data-del]")?.addEventListener("click", async () => {
    if (!(await uiConfirm(`„${existing.name}“ samt allen Dateien löschen?`, { okText: "Endgültig löschen", danger: true }))) return;
    try { await sreq(`/${encodeURIComponent(existing.id)}`, { method: "DELETE" }); m.close(); onDone?.(); }
    catch (e) { window.notify?.(e.message, "error"); }
  });
}

// ------------------------------------------------------------------ Git
async function gitDialog(path, refresh) {
  const m = modal(`Git · ${path.split("/").pop() || path}`, `<div data-git>Lade …</div>`, { wide: true });
  const box = m.el.querySelector("[data-git]");
  const g = (op, body = {}) => sreq(`/git/${op}`, { method: "POST", body: JSON.stringify({ path, ...body }) });
  const out = (txt) => { const o = box.querySelector("[data-out]"); if (o) { o.textContent = txt || ""; o.style.display = txt ? "" : "none"; } };
  async function run(op, body, label) {
    box.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try { const r = await g(op, body); window.notify?.(`${label} erledigt`, "success"); await draw(r.output); refresh?.(); }
    catch (e) { await draw(e.message); window.notify?.(`${label} fehlgeschlagen`, "error"); }
  }
  async function draw(lastOutput = "") {
    const av = await sreq("/git/available");
    if (!av.available) { box.innerHTML = `<div style="color:var(--warn)">⚠ Git ist auf dem Server nicht installiert.</div>`; return; }
    let st;
    try { st = await g("status"); } catch (e) { box.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    if (!st.repo) {
      box.innerHTML = `
        <p style="color:var(--subtext);margin-top:0">Dieser Ordner ist noch kein Git-Repository.</p>
        <div style="display:flex;gap:8px;margin-bottom:16px"><button class="btn-primary" data-init>Hier ein Repository anlegen (git init)</button></div>
        <h3 style="margin:0 0 8px">Repository hierher klonen</h3>
        <div class="form-row"><label>URL</label><input type="text" data-url placeholder="https://github.com/firma/projekt.git"></div>
        <div style="display:flex;gap:10px">
          <div class="form-row" style="flex:1"><label>Ordnername (optional)</label><input type="text" data-cname></div>
          <div class="form-row" style="flex:1"><label>Branch (optional)</label><input type="text" data-cbranch></div></div>
        <div style="display:flex;gap:10px">
          <div class="form-row" style="flex:1"><label>Benutzer (optional)</label><input type="text" data-cuser></div>
          <div class="form-row" style="flex:1"><label>Token / Passwort (optional)</label><input type="password" data-ctoken></div></div>
        <button class="btn-primary" data-clone>Klonen</button>
        <pre data-out style="display:none;margin-top:10px;max-height:200px;overflow:auto;background:var(--panel-2);padding:8px;border-radius:6px;font-size:11.5px;white-space:pre-wrap"></pre>`;
      out(lastOutput);
      box.querySelector("[data-init]").addEventListener("click", () => run("init", {}, "git init"));
      box.querySelector("[data-clone]").addEventListener("click", () => run("clone", {
        url: box.querySelector("[data-url]").value.trim(), name: box.querySelector("[data-cname]").value.trim() || null,
        branch: box.querySelector("[data-cbranch]").value.trim() || null, username: box.querySelector("[data-cuser]").value.trim() || null,
        token: box.querySelector("[data-ctoken]").value || null }, "Klonen"));
      return;
    }
    const br = await g("branches").catch(() => ({ branches: [] }));
    const w = st.writable;
    box.innerHTML = `
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
        <span>🌿 <b>${esc(st.branch || "?")}</b></span>
        ${st.remote ? `<span style="color:var(--subtext)">→ ${esc(st.remote)}</span>` : `<span style="color:var(--warn)">kein Remote</span>`}
        ${st.ahead ? `<span class="btn btn-sm active" style="pointer-events:none">↑ ${st.ahead}</span>` : ""}
        ${st.behind ? `<span class="btn btn-sm btn-warn" style="pointer-events:none">↓ ${st.behind}</span>` : ""}
        <span style="flex:1"></span>
        <select data-branch class="input-sm" ${w ? "" : "disabled"}>
          ${br.branches.map((b) => `<option value="${esc(b.name)}" ${b.current ? "selected" : ""}>${b.remote ? "☁ " : ""}${esc(b.name)}</option>`).join("")}
        </select>
        <button class="btn btn-sm" data-newbranch ${w ? "" : "disabled"}>＋ Branch</button>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">
        <button class="btn" data-op="fetch" ${st.remote ? "" : "disabled"}>⟳ Fetch</button>
        <button class="btn" data-op="pull" ${st.remote && w ? "" : "disabled"}>⬇ Pull</button>
        <button class="btn" data-op="push" ${st.remote && w ? "" : "disabled"}>⬆ Push</button>
        <button class="btn-primary" data-op="sync" ${st.remote && w ? "" : "disabled"}>⇅ Sync</button>
        <span style="flex:1"></span>
        <button class="btn btn-sm" data-remote ${w ? "" : "disabled"}>🔑 Remote & Zugang</button>
        <button class="btn btn-sm" data-log>🕘 Verlauf</button>
      </div>
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;margin-bottom:6px">
          <b>Änderungen (${st.files.length})</b>${st.files.length ? `<button class="btn btn-sm" data-diff>Diff anzeigen</button>` : ""}</div>
        ${st.files.length ? `<div style="max-height:140px;overflow:auto;font-family:ui-monospace,monospace;font-size:12px">
          ${st.files.map((f) => `<div><span style="color:var(--accent);display:inline-block;width:26px">${esc(f.code)}</span>${esc(f.file)}</div>`).join("")}</div>
          <div style="display:flex;gap:8px;margin-top:8px"><input type="text" data-msg placeholder="Commit-Nachricht" style="flex:1" ${w ? "" : "disabled"}>
          <button class="btn-primary" data-commit ${w ? "" : "disabled"}>✔ Commit</button></div>`
          : `<div style="color:var(--subtext)">Keine Änderungen – alles committet.</div>`}
      </div>
      <pre data-out style="display:none;max-height:240px;overflow:auto;background:var(--panel-2);padding:8px;border-radius:6px;font-size:11.5px;white-space:pre-wrap"></pre>`;
    out(lastOutput);
    box.querySelectorAll("[data-op]").forEach((b) => b.addEventListener("click", () => {
      const op = b.dataset.op;
      run(op, op === "sync" ? { message: box.querySelector("[data-msg]")?.value || null } : {}, op[0].toUpperCase() + op.slice(1));
    }));
    box.querySelector("[data-commit]")?.addEventListener("click", () => {
      const msg = box.querySelector("[data-msg]").value.trim();
      if (!msg) { window.notify?.("Bitte eine Commit-Nachricht eingeben", "warn"); return; }
      run("commit", { message: msg }, "Commit");
    });
    box.querySelector("[data-diff]")?.addEventListener("click", async () => { out((await g("diff")).diff || "(kein Unterschied)"); });
    box.querySelector("[data-log]").addEventListener("click", async () => {
      const r = await g("log", { limit: 50 });
      out(r.commits.map((c) => `${c.hash.slice(0, 8)}  ${c.date.slice(0, 16).replace("T", " ")}  ${c.author}\n          ${c.subject}`).join("\n") || "(keine Commits)");
    });
    box.querySelector("[data-branch]")?.addEventListener("change", (e) => run("checkout", { branch: e.target.value }, "Branch-Wechsel"));
    box.querySelector("[data-newbranch]")?.addEventListener("click", async () => {
      const n = await uiPrompt("Neuer Branch", { placeholder: "feature/name" });
      if (n?.trim()) run("checkout", { branch: n.trim(), create: true }, "Branch anlegen");
    });
    box.querySelector("[data-remote]")?.addEventListener("click", async () => {
      const m2 = modal("Remote & Zugang", `
        <div class="form-row"><label>Remote-URL (origin)</label><input type="text" data-u value="${esc(st.remote || "")}"></div>
        <div class="form-row"><label>Benutzer</label><input type="text" data-n value="${esc(st.username || "")}"></div>
        <div class="form-row"><label>Token / Passwort ${st.has_token ? "(gespeichert – leer lassen zum Behalten)" : ""}</label><input type="password" data-t></div>
        <label style="margin-bottom:10px"><input type="checkbox" data-clear> Gespeicherten Token löschen</label>
        <div style="font-size:12px;color:var(--subtext);margin-bottom:10px">Der Token wird verschlüsselt auf dem Server gespeichert und nie in das Repository geschrieben.</div>
        <button class="btn-primary" data-s>Speichern</button>`);
      m2.el.querySelector("[data-s]").addEventListener("click", async () => {
        const tok = m2.el.querySelector("[data-t]").value;
        const body = { url: m2.el.querySelector("[data-u]").value.trim(), username: m2.el.querySelector("[data-n]").value.trim() };
        if (m2.el.querySelector("[data-clear]").checked) body.token = "";
        else if (tok) body.token = tok;
        try { await g("remote", body); m2.close(); draw(); window.notify?.("Gespeichert", "success"); }
        catch (e) { window.notify?.(e.message, "error"); }
      });
    });
  }
  draw();
}

// ------------------------------------------------------------------ Browser
export async function renderSpacesBrowser(root, opts = {}) {
  let path = opts.path || "";
  let info = null;            // Antwort von /fs/list
  let meta = null;            // Antwort von /api/spaces
  const history = [];

  root.innerHTML = `
    <div class="spb" style="display:flex;flex-direction:column;height:100%;min-height:0;font-size:13px">
      <div style="display:flex;gap:6px;align-items:center;padding:8px 10px;border-bottom:1px solid var(--border);flex-wrap:wrap">
        <button class="btn btn-icon btn-sm" data-a="back" title="Zurück">←</button>
        <button class="btn btn-icon btn-sm" data-a="up" title="Übergeordneter Ordner">↑</button>
        <button class="btn btn-icon btn-sm" data-a="refresh" title="Aktualisieren">⟳</button>
        <div data-crumbs style="flex:1;min-width:120px;display:flex;gap:2px;align-items:center;overflow:hidden;white-space:nowrap"></div>
        <span data-tools style="display:flex;gap:6px;flex-wrap:wrap"></span>
      </div>
      <div data-list style="flex:1;overflow:auto;position:relative"></div>
      <div data-foot style="padding:5px 10px;border-top:1px solid var(--border);font-size:11.5px;color:var(--subtext);display:flex;gap:12px"></div>
      <input type="file" data-file multiple style="display:none">
    </div>`;
  const $ = (s) => root.querySelector(s);

  async function go(p, push = true) {
    if (push && p !== path) history.push(path);
    path = p;
    try {
      info = await sreq(`/fs/list?path=${q(path)}`);
    } catch (e) {
      $("[data-list]").innerHTML = `<div style="padding:16px;color:var(--danger)">${esc(e.message)}</div>`;
      info = null;
    }
    draw();
  }

  function crumbs() {
    const parts = path.split("/").filter(Boolean);
    const items = [`<button class="btn btn-ghost btn-sm" data-crumb="">☁️ Spaces</button>`];
    parts.forEach((p, i) => items.push(`<span style="color:var(--subtext)">›</span>
      <button class="btn btn-ghost btn-sm" data-crumb="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</button>`));
    $("[data-crumbs]").innerHTML = items.join("");
    $("[data-crumbs]").querySelectorAll("[data-crumb]").forEach((b) => b.addEventListener("click", () => go(b.dataset.crumb)));
  }

  function tools() {
    const t = [];
    const head = path.split("/")[0];
    if (info?.writable) {
      t.push(`<button class="btn btn-sm" data-a="mkdir">📁 Neuer Ordner</button>`);
      t.push(`<button class="btn btn-sm" data-a="newfile">📝 Neue Datei</button>`);
      t.push(`<button class="btn-primary btn-sm" data-a="upload">⬆ Hochladen</button>`);
      t.push(`<button class="btn btn-sm" data-a="git">🌿 Git</button>`);
    }
    if (info?.can_share && path.split("/").length > (head === "Mein Space" ? 1 : 2)) t.push(`<button class="btn btn-sm" data-a="share">🔗 Ordner freigeben</button>`);
    if (head === "Teams" && path.split("/").length === 1 && meta?.can_create?.team) t.push(`<button class="btn-primary btn-sm" data-a="newteam">＋ Teamspace</button>`);
    if (head === "Öffentlich" && path.split("/").length === 1 && meta?.can_create?.public) t.push(`<button class="btn-primary btn-sm" data-a="newpublic">＋ Public Space</button>`);
    const sp = currentSpace();
    if (sp && sp.level >= 3 && path.split("/").length === 2) t.push(`<button class="btn btn-sm" data-a="editspace">⚙️ Space verwalten</button>`);
    if (!path) t.push(`<button class="btn btn-sm" data-a="myshares">🔗 Meine Freigaben</button>`);
    $("[data-tools]").innerHTML = t.join("");
  }

  function currentSpace() {
    const [head, name] = path.split("/");
    if (!meta || !name) return null;
    const kind = head === "Teams" ? "team" : head === "Öffentlich" ? "public" : null;
    return kind ? meta.spaces.find((s) => s.kind === kind && s.name === name) : null;
  }

  function draw() {
    crumbs(); tools();
    const list = $("[data-list]");
    if (!info) return;
    const rows = info.entries;
    list.innerHTML = rows.length ? `
      <table class="data-table" style="width:100%">
        <thead><tr><th style="text-align:left">Name</th><th style="width:90px;text-align:right">Größe</th>
          <th style="width:160px">Geändert</th><th style="width:130px"></th></tr></thead>
        <tbody>${rows.map((e, i) => `
          <tr data-i="${i}" style="cursor:pointer">
            <td style="max-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${ICON(e)} ${esc(e.name)}</td>
            <td style="text-align:right;color:var(--subtext)">${e.is_dir ? "" : fmtSize(e.size)}</td>
            <td style="color:var(--subtext);white-space:nowrap">${e.mtime ? new Date(e.mtime).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : ""}</td>
            <td style="text-align:right;white-space:nowrap">
              ${!e.is_dir ? `<button class="btn-tool" data-dl="${i}" title="Herunterladen">⬇</button>` : ""}
              ${info.writable && !e.virtual ? `<button class="btn-tool" data-ren="${i}" title="Umbenennen">✏️</button>` : ""}
              ${info.can_share && !e.virtual ? `<button class="btn-tool" data-sh="${i}" title="Freigeben">🔗</button>` : ""}
              ${info.writable && !e.virtual ? `<button class="btn-tool" data-del="${i}" title="Löschen">🗑️</button>` : ""}
            </td></tr>`).join("")}</tbody></table>`
      : `<div style="padding:20px;color:var(--subtext);text-align:center">${info.virtual ? "Hier ist noch nichts." : "Leerer Ordner – Dateien hierher ziehen oder „Hochladen“."}</div>`;
    const foot = [];
    if (!info.virtual) foot.push(info.writable ? "✏️ Schreibzugriff" : "👁 nur lesen");
    if (!path && meta?.personal) foot.push(`Mein Space: ${fmtSize(meta.personal.used)}${meta.quota_mb ? ` von ${meta.quota_mb} MB` : ""}`);
    foot.push(`${rows.length} Einträge`);
    $("[data-foot]").innerHTML = foot.map((x) => `<span>${esc(x)}</span>`).join("");

    list.querySelectorAll("tr[data-i]").forEach((tr) => tr.addEventListener("dblclick", () => open(rows[+tr.dataset.i])));
    list.querySelectorAll("tr[data-i]").forEach((tr) => tr.addEventListener("click", (ev) => {
      if (ev.target.closest("button")) return;
      const e = rows[+tr.dataset.i];
      if (e.is_dir) open(e);
    }));
    const on = (attr, fn) => list.querySelectorAll(`[${attr}]`).forEach((b) => b.addEventListener("click", (ev) => {
      ev.stopPropagation(); fn(rows[+b.getAttribute(attr)]);
    }));
    on("data-dl", (e) => download(join(path, e.name)).catch((x) => window.notify?.(x.message, "error")));
    on("data-sh", (e) => shareDialog(join(path, e.name), { space_id: info.space_id, space_rel: join(info.space_rel, e.name) }));
    on("data-ren", async (e) => {
      const n = await uiPrompt("Umbenennen", { value: e.name });
      if (!n?.trim() || n === e.name) return;
      try { await sreq("/fs/move", { method: "POST", body: JSON.stringify({ src: join(path, e.name), dst: join(path, n.trim()) }) }); go(path, false); }
      catch (x) { window.notify?.(x.message, "error"); }
    });
    on("data-del", async (e) => {
      if (!(await uiConfirm(`„${e.name}“ löschen?`, { description: e.is_dir ? "Der Ordner wird mit allem Inhalt gelöscht." : "", okText: "Löschen", danger: true }))) return;
      try { await sreq("/fs/delete", { method: "POST", body: JSON.stringify({ path: join(path, e.name) }) }); go(path, false); }
      catch (x) { window.notify?.(x.message, "error"); }
    });
  }

  async function open(e) {
    const p = join(path, e.name);
    if (e.is_dir) return go(p);
    if (isImage(e.name)) {
      const u = await blobUrl(`/api/spaces/fs/download?path=${q(p)}&inline=1`);
      modal(e.name, `<img src="${u}" style="max-width:100%;max-height:70vh;display:block;margin:auto">`, { wide: true });
      setTimeout(() => URL.revokeObjectURL(u), 120000);
      return;
    }
    if (isText(e.name) && e.size < 2_000_000) {
      const res = await fetch(`/api/spaces/fs/download?path=${q(p)}&inline=1`, { headers: { Authorization: "Bearer " + getToken() } });
      const text = await res.text();
      const m = modal(e.name, `<textarea data-t spellcheck="false" style="width:100%;height:55vh;font-family:ui-monospace,monospace;font-size:12.5px"
          ${info.writable ? "" : "readonly"}></textarea>
        ${info.writable ? `<div style="margin-top:8px"><button class="btn-primary" data-save>Speichern</button></div>` : ""}`, { wide: true });
      m.el.querySelector("[data-t]").value = text;
      m.el.querySelector("[data-save]")?.addEventListener("click", async () => {
        try { await sreq("/fs/write", { method: "POST", body: JSON.stringify({ path: p, text: m.el.querySelector("[data-t]").value }) });
          window.notify?.("Gespeichert", "success"); m.close(); go(path, false); }
        catch (x) { window.notify?.(x.message, "error"); }
      });
      return;
    }
    download(p).catch((x) => window.notify?.(x.message, "error"));
  }

  async function upload(files) {
    if (!files?.length || !info?.writable) return;
    const fd = new FormData();
    fd.append("path", path);
    for (const f of files) fd.append("files", f);
    $("[data-foot]").innerHTML = `<span>⬆ Lade ${files.length} Datei(en) hoch …</span>`;
    try { await sreq("/fs/upload", { method: "POST", body: fd }); window.notify?.(`${files.length} Datei(en) hochgeladen`, "success"); }
    catch (e) { window.notify?.(e.message, "error"); }
    go(path, false);
  }

  root.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-a]");
    if (!b || !root.contains(b)) return;
    const a = b.dataset.a;
    if (a === "back") { if (history.length) go(history.pop(), false); }
    else if (a === "up") go(path.split("/").slice(0, -1).join("/"));
    else if (a === "refresh") { meta = await sreq("").catch(() => meta); go(path, false); }
    else if (a === "upload") $("[data-file]").click();
    else if (a === "mkdir" || a === "newfile") {
      const n = await uiPrompt(a === "mkdir" ? "Neuer Ordner" : "Neue Datei", { placeholder: a === "mkdir" ? "Ordnername" : "datei.txt" });
      if (!n?.trim()) return;
      try {
        if (a === "mkdir") await sreq("/fs/mkdir", { method: "POST", body: JSON.stringify({ path: join(path, n.trim()) }) });
        else await sreq("/fs/write", { method: "POST", body: JSON.stringify({ path: join(path, n.trim()), text: "" }) });
        go(path, false);
      } catch (e) { window.notify?.(e.message, "error"); }
    }
    else if (a === "share") shareDialog(path, { space_id: info.space_id, space_rel: info.space_rel });
    else if (a === "git") gitDialog(path, () => go(path, false));
    else if (a === "newteam" || a === "newpublic") spaceDialog(a === "newteam" ? "team" : "public", null, async () => { meta = await sreq(""); go(path, false); });
    else if (a === "editspace") {
      const sp = currentSpace();
      spaceDialog(sp.kind, sp, async () => { meta = await sreq(""); go(path.split("/")[0]); });
    }
    else if (a === "myshares") mySharesDialog();
  });
  $("[data-file]").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
  const list = $("[data-list]");
  list.addEventListener("dragover", (e) => { if (info?.writable) { e.preventDefault(); list.style.outline = "2px dashed var(--accent)"; } });
  list.addEventListener("dragleave", () => { list.style.outline = ""; });
  list.addEventListener("drop", (e) => { e.preventDefault(); list.style.outline = ""; upload([...(e.dataTransfer?.files || [])]); });

  meta = await sreq("").catch(() => null);
  await go(path, false);
}

async function mySharesDialog() {
  const all = await sreq("/shares");
  const m = modal("Meine Freigaben", all.mine.length ? all.mine.map((s) => `
    <div style="display:flex;gap:8px;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)">
      <span>${s.is_dir ? "📁" : "📄"}</span>
      <div style="flex:1;min-width:0"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.space_name)} / ${esc(s.path)}</div>
        <div style="font-size:11.5px;color:var(--subtext)">${s.kind === "link" ? "🔗 Link (nur Angemeldete)" : `👥 ${s.users.length} Benutzer · ${s.groups.length} Gruppen${s.write ? " · darf ändern" : ""}`}
        ${s.exists ? "" : " · ⚠ Ziel fehlt"}</div></div>
      ${s.kind === "link" ? `<button class="btn btn-sm" data-copy="${esc(location.origin + "/#space-share=" + s.token)}">Kopieren</button>` : ""}
      <button class="btn btn-sm btn-danger" data-del="${esc(s.id)}">Entfernen</button></div>`).join("")
    : `<div style="color:var(--subtext)">Du hast noch nichts freigegeben.</div>`, { wide: true });
  m.el.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
    navigator.clipboard?.writeText(b.dataset.copy); window.notify?.("Link kopiert", "success");
  }));
  m.el.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    await sreq(`/shares/${b.dataset.del}`, { method: "DELETE" }); m.close(); mySharesDialog();
  }));
}

// ------------------------------------------------------------------ Link-Freigabe öffnen
export async function openShareLink(token) {
  let info;
  try { info = await sreq(`/s/${encodeURIComponent(token)}`); }
  catch (e) { window.notify?.(`Freigabe: ${e.message}`, "error"); return; }
  const base = `/s/${encodeURIComponent(token)}`;
  const m = modal(`🔗 ${info.name} (von ${info.owner_name})`, `<div data-v>Lade …</div>`, { wide: true });
  const box = m.el.querySelector("[data-v]");
  async function show(sub) {
    if (!info.is_dir) {
      box.innerHTML = `<p>${ICON({ name: info.name })} ${esc(info.name)} · ${fmtSize(info.size)}</p>
        ${isImage(info.name) ? `<img data-img style="max-width:100%;max-height:60vh;display:block;margin:0 auto 10px">` : ""}
        <button class="btn-primary" data-dl>⬇ Herunterladen</button>`;
      if (isImage(info.name)) box.querySelector("[data-img]").src = await blobUrl(`/api/spaces${base}/download?inline=1`);
      box.querySelector("[data-dl]").addEventListener("click", async () => {
        const u = await blobUrl(`/api/spaces${base}/download`);
        const a = document.createElement("a"); a.href = u; a.download = info.name; a.click();
      });
      return;
    }
    const r = await sreq(`${base}/list?path=${q(sub)}`);
    box.innerHTML = `<div style="margin-bottom:6px;color:var(--subtext)">/${esc(sub)}</div>
      ${sub ? `<div data-up style="cursor:pointer;padding:4px 0">↑ ..</div>` : ""}
      ${r.entries.map((e) => `<div data-e="${esc(e.name)}" data-dir="${e.is_dir ? 1 : 0}" style="cursor:pointer;padding:4px 0;border-bottom:1px solid var(--border)">
        ${ICON(e)} ${esc(e.name)} <span style="color:var(--subtext);font-size:11.5px">${e.is_dir ? "" : fmtSize(e.size)}</span></div>`).join("") || "<i>leer</i>"}`;
    box.querySelector("[data-up]")?.addEventListener("click", () => show(sub.split("/").slice(0, -1).join("/")));
    box.querySelectorAll("[data-e]").forEach((el) => el.addEventListener("click", async () => {
      const p = join(sub, el.dataset.e);
      if (el.dataset.dir === "1") return show(p);
      const u = await blobUrl(`/api/spaces${base}/download?path=${q(p)}`);
      const a = document.createElement("a"); a.href = u; a.download = el.dataset.e; a.click();
    }));
  }
  show("");
}

// ------------------------------------------------------------------ App
export function renderSpacesApp(body, win) {
  return renderSpacesBrowser(body, { path: win?.props?.path || "" });
}

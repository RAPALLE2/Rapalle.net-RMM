// app.js (Modul "tenants") - mehrere Mandanten-Backends verbinden & wechseln
// --------------------------------------------------------------------------
// Jeder Mandant ist ein eigenes RMM-Backend (eigene DB/Settings/Rechte/Spaces/Agents).
// Diese App listet die freigegebenen Mandanten, lässt den Benutzer pro Mandant seine
// Zugangsdaten hinterlegen und öffnet das Dashboard eines Mandanten EINGEBETTET
// (iFrame im App-Fenster, Autologin per #client-token=). Offene Fenster im Haupt-RMM
// bleiben erhalten. Wer welchen Mandanten sehen darf, stellt ein Admin (Recht
// manage_tenants) in der Verwaltung ein. Die feinen Rechte liegen auf dem Mandanten.

import { getToken } from "/js/app/api.js";
import { state } from "/js/app/state.js";
import { esc, uiConfirm } from "/js/app/utils.js";
import { hasGlobalPerm } from "/js/app/state.js";

async function tq(path, opts = {}) {
  const headers = { Authorization: "Bearer " + (getToken() || "") };
  if (opts.body !== undefined && typeof opts.body !== "string") { headers["Content-Type"] = "application/json"; opts = { ...opts, body: JSON.stringify(opts.body) }; }
  const res = await fetch("/api/tenants" + path, { ...opts, headers });
  if (!res.ok) { let m = res.statusText; let code = res.status; try { const j = await res.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* egal */ } const e = new Error(m); e.status = code; throw e; }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

const canManage = () => { try { return hasGlobalPerm("manage_tenants"); } catch { return false; } };
const dot = (c) => `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${esc(c || "var(--accent)")}"></span>`;

export async function renderTenants(body) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;min-height:0;font-size:13px">
      <div data-bar style="display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--border);flex-wrap:wrap">
        <b style="font-size:14px">🏢 Mandanten</b>
        <div data-tabs style="display:flex;gap:4px;flex:1"></div>
        ${canManage() ? `<button class="btn btn-sm" data-admin>⚙️ Verwaltung</button>` : ""}
      </div>
      <div data-view style="flex:1;min-height:0;position:relative"></div>
    </div>`;
  const view = body.querySelector("[data-view]");
  const tabsEl = body.querySelector("[data-tabs]");
  let tenants = [];
  const open = {};        // tid -> {iframe} offene eingebettete Dashboards (bleiben geladen)
  let active = "home";    // "home" | tid

  const load = async () => { try { tenants = (await tq("")).tenants; } catch (e) { view.innerHTML = `<div style="padding:16px;color:var(--danger)">${esc(e.message)}</div>`; return; } drawTabs(); show(active); };

  const drawTabs = () => {
    tabsEl.innerHTML = `<button class="tab-btn ${active === "home" ? "active" : ""}" data-t="home">Übersicht</button>` +
      Object.keys(open).map((tid) => {
        const t = tenants.find((x) => x.id === tid) || { name: tid };
        return `<button class="tab-btn ${active === tid ? "active" : ""}" data-t="${esc(tid)}">${dot(t.color)} ${esc(t.name)} <span data-close="${esc(tid)}" title="Schließen" style="margin-left:4px;opacity:.6">✕</span></button>`;
      }).join("");
    tabsEl.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", (e) => { if (e.target.dataset.close) return; show(b.dataset.t); }));
    tabsEl.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", (e) => { e.stopPropagation(); closeTab(b.dataset.close); }));
  };

  const show = (which) => {
    active = which;
    // Alle Iframes verstecken, aktives zeigen (bleiben geladen -> Zustand erhalten)
    Object.entries(open).forEach(([tid, o]) => { o.wrap.style.display = tid === which ? "block" : "none"; });
    let home = view.querySelector("[data-home]");
    if (which === "home") { if (home) home.style.display = "block"; drawHome(); }
    else if (home) home.style.display = "none";
    drawTabs();
  };

  const closeTab = (tid) => { const o = open[tid]; if (o) { o.wrap.remove(); delete open[tid]; } if (active === tid) active = "home"; show(active); };

  const drawHome = () => {
    let home = view.querySelector("[data-home]");
    if (!home) { home = document.createElement("div"); home.dataset.home = "1"; home.style.cssText = "position:absolute;inset:0;overflow:auto;padding:14px 16px"; view.appendChild(home); }
    if (!tenants.length) { home.innerHTML = `<div style="color:var(--subtext)">Noch keine Mandanten freigegeben.${canManage() ? " Lege welche in der Verwaltung an." : ""}</div>`; return; }
    home.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px">
      ${tenants.map((t) => `
        <div style="border:1px solid var(--border);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;align-items:center;gap:8px"><span style="font-size:20px">${dot(t.color)}</span><b style="font-size:15px">${esc(t.name)}</b></div>
          ${t.note ? `<div style="font-size:12px;color:var(--subtext)">${esc(t.note)}</div>` : ""}
          <div style="font-size:11.5px;color:var(--subtext)">${esc((t.url || "").replace(/^https?:\/\//, ""))}</div>
          <div style="font-size:12px">${t.has_credentials ? `✅ angemeldet als <b>${esc(t.username)}</b>` : "⚠ keine Zugangsdaten"}</div>
          <div style="display:flex;gap:6px;margin-top:auto">
            <button class="btn-primary btn-sm" data-open="${esc(t.id)}">${t.has_credentials ? "Öffnen" : "Anmelden & öffnen"}</button>
            <button class="btn btn-sm" data-cred="${esc(t.id)}">Zugangsdaten</button>
            <button class="btn btn-sm" data-spaces="${esc(t.id)}" ${t.has_credentials ? "" : "disabled"}>Dateien</button>
          </div>
        </div>`).join("")}</div>`;
    home.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openTenant(b.dataset.open)));
    home.querySelectorAll("[data-cred]").forEach((b) => b.addEventListener("click", () => credDialog(tenants.find((x) => x.id === b.dataset.cred), load)));
    home.querySelectorAll("[data-spaces]").forEach((b) => b.addEventListener("click", () => spacesDialog(tenants.find((x) => x.id === b.dataset.spaces))));
  };

  const openTenant = async (tid, code) => {
    const t = tenants.find((x) => x.id === tid);
    if (!t) return;
    if (!t.has_credentials) { credDialog(t, () => { load().then(() => openTenant(tid)); }); return; }
    if (open[tid]) { show(tid); return; }
    let res;
    try { res = await tq(`/${tid}/open`, { method: "POST", body: { code: code || "" } }); }
    catch (e) {
      if (e.status === 401 && e.message === "2fa_required") { const c = prompt("2FA-Code für " + t.name + ":"); if (c) openTenant(tid, c); return; }
      if (e.status === 412) { credDialog(t, () => load().then(() => openTenant(tid))); return; }
      window.notify?.(e.message, "error", 7000); return;
    }
    const wrap = document.createElement("div");
    wrap.style.cssText = "position:absolute;inset:0;display:flex;flex-direction:column";
    wrap.innerHTML = `<div style="display:flex;align-items:center;gap:8px;padding:4px 8px;background:var(--panel-2);border-bottom:1px solid var(--border);font-size:12px">
        ${dot(t.color)} <b>${esc(t.name)}</b> <span style="color:var(--subtext)">${esc((res.base || "").replace(/^https?:\/\//, ""))}</span>
        <span style="flex:1"></span>
        <button class="btn btn-sm" data-reload>↻ Neu laden</button>
        <button class="btn btn-sm" data-ext title="In neuem Tab">↗</button></div>
      <iframe style="flex:1;border:0;width:100%;background:var(--bg)" src="${esc(res.url)}"></iframe>`;
    view.appendChild(wrap);
    const iframe = wrap.querySelector("iframe");
    wrap.querySelector("[data-reload]").addEventListener("click", async () => {
      try { const r = await tq(`/${tid}/open`, { method: "POST", body: {} }); iframe.src = r.url; } catch (e) { window.notify?.(e.message, "error"); }
    });
    wrap.querySelector("[data-ext]").addEventListener("click", () => window.open(res.url, "_blank", "noopener"));
    open[tid] = { wrap, iframe };
    show(tid);
  };

  body.querySelector("[data-admin]")?.addEventListener("click", () => adminDialog(load));
  load();
}

// ------------------------------------------------------------------ Zugangsdaten
function credDialog(t, done) {
  const ov = overlay(`<b style="font-size:15px">Zugangsdaten – ${esc(t.name)}</b>
    <div style="font-size:12px;color:var(--subtext);margin:4px 0 10px">Dein persönliches Login für dieses Mandanten-Backend. Wird verschlüsselt gespeichert und nur für deinen Zugriff verwendet.</div>
    <div style="display:grid;grid-template-columns:110px 1fr;gap:8px 10px;align-items:center">
      <label>Benutzername</label><input data-u value="${esc(t.username || "")}">
      <label>Passwort</label><input data-p type="password" placeholder="${t.has_credentials ? "•••• (unverändert lassen)" : ""}">
      <label>Realm</label><input data-r value="local" title="local oder AD-Realm-ID">
    </div>
    <div data-2fa style="display:none;margin-top:8px"><input data-code placeholder="2FA-Code (falls der Mandant es verlangt)" style="width:100%"></div>
    <div data-msg style="font-size:12px;margin-top:8px;color:var(--subtext)"></div>
    <div style="display:flex;gap:8px;justify-content:space-between;margin-top:14px">
      ${t.has_credentials ? `<button class="btn btn-sm btn-danger" data-clear>Entfernen</button>` : "<span></span>"}
      <span><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Speichern & prüfen</button></span></div>`);
  const $ = (s) => ov.querySelector(s);
  $("[data-x]").addEventListener("click", () => ov.remove());
  $("[data-clear]")?.addEventListener("click", async () => { try { await tq(`/${t.id}/credentials`, { method: "DELETE" }); ov.remove(); done(); window.notify?.("Zugangsdaten entfernt", "success"); } catch (e) { window.notify?.(e.message, "error"); } });
  $("[data-ok]").addEventListener("click", async () => {
    const m = $("[data-msg]"); m.textContent = "⏳ prüfe …";
    try {
      await tq(`/${t.id}/credentials`, { method: "PUT", body: { username: $("[data-u]").value.trim(), password: $("[data-p]").value, realm: $("[data-r]").value.trim() || "local", code: $("[data-code]").value.trim() } });
      ov.remove(); done(); window.notify?.("Zugangsdaten gespeichert", "success");
    } catch (e) {
      if (e.status === 401 && e.message === "2fa_required") { $("[data-2fa]").style.display = "block"; m.textContent = "Bitte zusätzlich den 2FA-Code eingeben."; return; }
      m.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
    }
  });
}

// ------------------------------------------------------------------ Mandanten-Dateien (Spaces)
async function spacesDialog(t, path = "") {
  const ov = overlay(`<b style="font-size:15px">Dateien – ${esc(t.name)}</b><div data-body style="margin-top:10px;min-height:120px">Lade …</div>
    <div style="text-align:right;margin-top:12px"><button class="btn" data-x>Schließen</button></div>`, 680);
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
  const b = ov.querySelector("[data-body]");
  const nav = async (p) => {
    b.innerHTML = "⏳ …";
    let d; try { d = await tq(`/${t.id}/spaces?path=${encodeURIComponent(p)}`); } catch (e) { b.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    const entries = d.entries || d.items || [];
    const parent = p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
    b.innerHTML = `<div style="font-size:12px;color:var(--subtext);margin-bottom:6px">📁 /${esc(p)}</div>
      ${p ? `<div data-up style="cursor:pointer;padding:4px 0">⬆ ..</div>` : ""}
      ${entries.length ? entries.map((e) => `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
        <span>${e.is_dir || e.dir ? "📁" : "📄"}</span>
        <span style="flex:1;${e.is_dir || e.dir ? "cursor:pointer" : ""}" ${e.is_dir || e.dir ? `data-dir="${esc(e.path || (p ? p + "/" : "") + (e.name || ""))}"` : ""}>${esc(e.name || "")}</span>
        ${e.is_dir || e.dir ? "" : `<button class="btn btn-sm" data-dl="${esc(e.path || (p ? p + "/" : "") + (e.name || ""))}">⬇</button>`}</div>`).join("") : `<div style="color:var(--subtext)">Leer.</div>`}`;
    b.querySelector("[data-up]")?.addEventListener("click", () => nav(parent));
    b.querySelectorAll("[data-dir]").forEach((el) => el.addEventListener("click", () => nav(el.dataset.dir)));
    b.querySelectorAll("[data-dl]").forEach((el) => el.addEventListener("click", () => window.open(`/api/tenants/${t.id}/spaces/file?path=${encodeURIComponent(el.dataset.dl)}`, "_blank")));
  };
  nav(path);
}

// ------------------------------------------------------------------ Verwaltung (Admin)
async function adminDialog(done) {
  let ts = [], dir = { users: [], groups: [] };
  try { ts = (await tq("/admin")).tenants; dir = await tq("/directory"); } catch (e) { window.notify?.(e.message, "error"); return; }
  const ov = overlay(`<div style="display:flex;align-items:center;gap:8px"><b style="font-size:15px;flex:1">Mandanten-Verwaltung</b>
      <button class="btn-primary btn-sm" data-add>＋ Mandant</button><button class="btn btn-sm" data-x>Schließen</button></div>
    <div data-list style="margin-top:12px"></div>`, 760);
  ov.querySelector("[data-x]").addEventListener("click", () => { ov.remove(); done(); });
  const list = ov.querySelector("[data-list]");
  const draw = () => {
    list.innerHTML = ts.length ? ts.map((t) => `
      <div style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px"><b>${esc(t.name)}</b>
          <span style="font-size:11.5px;color:var(--subtext)">${esc((t.url || "").replace(/^https?:\/\//, ""))}</span>
          <span style="flex:1"></span>
          <button class="btn btn-sm" data-edit="${esc(t.id)}">Bearbeiten</button>
          <button class="btn btn-sm btn-danger" data-del="${esc(t.id)}">Löschen</button></div>
        <div style="font-size:12px;color:var(--subtext);margin-top:4px">Sichtbar für: ${(t.allow && t.allow.length) ? esc(t.allow.map((a) => (a.type === "user" ? "👤" : "👥") + nameOf(a, dir)).join(", ")) : "alle"}</div>
      </div>`).join("") : `<div style="color:var(--subtext)">Noch keine Mandanten.</div>`;
    list.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => tenantForm(ts.find((x) => x.id === b.dataset.edit), dir, reload)));
    list.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm("Mandant entfernen? (Zugangsdaten aller Benutzer dafür werden gelöscht)", { okText: "Entfernen", danger: true }))) return;
      try { await tq(`/admin/${b.dataset.del}`, { method: "DELETE" }); reload(); } catch (e) { window.notify?.(e.message, "error"); }
    }));
  };
  const reload = async () => { try { ts = (await tq("/admin")).tenants; } catch { /* egal */ } draw(); };
  ov.querySelector("[data-add]").addEventListener("click", () => tenantForm(null, dir, reload));
  draw();
}

function nameOf(a, dir) {
  if (a.type === "user") return (dir.users.find((u) => u.id === a.id) || {}).username || a.id.slice(0, 6);
  return (dir.groups.find((g) => g.id === a.id) || {}).name || a.id.slice(0, 6);
}

function tenantForm(t, dir, done) {
  const ed = !!t;
  t = t || { verify_tls: true, allow: [], color: "#2dd4bf" };
  const allow = new Set((t.allow || []).map((a) => a.type + ":" + a.id));
  const ov = overlay(`<b style="font-size:15px">${ed ? "Mandant bearbeiten" : "Mandant anlegen"}</b>
    <div style="display:grid;grid-template-columns:120px 1fr;gap:8px 10px;align-items:center;margin-top:12px">
      <label>Name</label><input data-f="name" value="${esc(t.name || "")}" placeholder="z. B. Firma Nord">
      <label>Backend-URL</label><input data-f="url" value="${esc(t.url || "")}" placeholder="https://rmm-nord.firma.de">
      <label>Farbe</label><input data-f="color" type="color" value="${esc(t.color || "#2dd4bf")}" style="width:60px;height:30px;padding:0">
      <label>Notiz</label><input data-f="note" value="${esc(t.note || "")}">
      <label>TLS prüfen</label><label style="display:flex;gap:6px;align-items:center;font-size:12.5px"><input type="checkbox" data-f="verify_tls" ${t.verify_tls ? "checked" : ""}> Zertifikat des Mandanten prüfen</label>
    </div>
    <div style="font-weight:600;margin:12px 0 4px;font-size:12.5px">Sichtbar für (leer = alle)</div>
    <div style="display:flex;gap:6px;margin-bottom:6px"><input data-filter placeholder="Benutzer/Gruppe filtern" style="flex:1"></div>
    <div data-allow style="max-height:200px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:6px"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>${ed ? "Speichern" : "Anlegen"}</button></div>`, 620);
  const $ = (s) => ov.querySelector(s);
  const drawAllow = () => {
    const f = $("[data-filter]").value.toLowerCase();
    const row = (type, id, label, icon) => allow.has(type + ":" + id) || !f || label.toLowerCase().includes(f)
      ? `<label style="display:flex;gap:6px;align-items:center;padding:3px 0;cursor:pointer"><input type="checkbox" data-a="${type}:${esc(id)}" ${allow.has(type + ":" + id) ? "checked" : ""}> ${icon} ${esc(label)}</label>` : "";
    $("[data-allow]").innerHTML =
      `<div style="font-size:11px;color:var(--subtext)">Gruppen</div>` + dir.groups.map((g) => row("group", g.id, g.name, "👥")).join("") +
      `<div style="font-size:11px;color:var(--subtext);margin-top:4px">Benutzer</div>` + dir.users.map((u) => row("user", u.id, u.username, "👤")).join("");
    $("[data-allow]").querySelectorAll("[data-a]").forEach((c) => c.addEventListener("change", () => { c.checked ? allow.add(c.dataset.a) : allow.delete(c.dataset.a); }));
  };
  $("[data-filter]").addEventListener("input", drawAllow); drawAllow();
  $("[data-x]").addEventListener("click", () => ov.remove());
  $("[data-ok]").addEventListener("click", async () => {
    const payload = { name: $('[data-f="name"]').value.trim(), url: $('[data-f="url"]').value.trim(),
      color: $('[data-f="color"]').value, note: $('[data-f="note"]').value.trim(), verify_tls: $('[data-f="verify_tls"]').checked,
      allow: [...allow].map((x) => { const [type, id] = x.split(":"); return { type, id }; }) };
    try { if (ed) await tq(`/admin/${t.id}`, { method: "PUT", body: payload }); else await tq("/admin", { method: "POST", body: payload }); ov.remove(); done(); window.notify?.("Gespeichert", "success", 1500); }
    catch (e) { window.notify?.(e.message, "error", 6000); }
  });
}

// ------------------------------------------------------------------ Helfer
function overlay(html, width = 520) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;width:${width}px;max-width:100%;max-height:calc(100dvh - 24px);overflow:auto;font-size:13px">${html}</div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  return ov;
}

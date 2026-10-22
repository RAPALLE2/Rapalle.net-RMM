// app.js (Modul "addir") - Active Directory verwalten
// ----------------------------------------------------
// Reiter: Verbindungen | Durchsuchen (User/Gruppen/OUs/Computer) | Massen-Import.
// Aktionen über Dialoge: User anlegen/bearbeiten/verschieben/PW/sperren/entsperren/
// Ablauf/löschen; Gruppe anlegen + Mitglieder; OU anlegen; verschieben per Ziel-OU.

import { getToken } from "/js/app/api.js";
import { esc, uiConfirm } from "/js/app/utils.js";

async function aq(path, opts = {}) {
  const headers = { Authorization: "Bearer " + (getToken() || "") };
  if (opts.body !== undefined && typeof opts.body !== "string") { headers["Content-Type"] = "application/json"; opts = { ...opts, body: JSON.stringify(opts.body) }; }
  const res = await fetch("/api/ad" + path, { ...opts, headers });
  if (!res.ok) { let m = res.statusText; try { const j = await res.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* egal */ } throw new Error(m); }
  return res.json();
}

const rdnName = (dn) => (dn || "").split(",")[0].split("=").slice(1).join("=");
let CONN = null;        // aktive Verbindungs-ID
let TREE = [];          // OUs/Container der aktiven Verbindung

export function renderAd(body) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;min-height:0;font-size:13px">
      <div class="tab-bar" style="padding:8px 12px 0;flex:0 0 auto">
        ${["conn:Verbindungen", "browse:Durchsuchen", "bulk:Massen-Import"].map((t, i) => {
          const [k, l] = t.split(":"); return `<button class="tab-btn ${i ? "" : "active"}" data-tab="${k}">${l}</button>`;
        }).join("")}
      </div>
      <div style="padding:6px 12px;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">
        <span style="color:var(--subtext);font-size:12px">AD-Verbindung</span>
        <select data-conn style="min-width:240px"></select>
      </div>
      <div data-pane style="flex:1;overflow:auto;padding:12px 14px;min-height:0"></div>
    </div>`;
  const pane = body.querySelector("[data-pane]");
  const connSel = body.querySelector("[data-conn]");
  let tab = "conn";
  const select = (t) => { tab = t; body.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === t)); draw(); };
  body.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => select(b.dataset.tab)));

  const loadConns = async () => {
    let d; try { d = await aq("/connections"); } catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return []; }
    const cs = d.connections;
    connSel.innerHTML = cs.length ? cs.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("") : `<option value="">keine</option>`;
    if (cs.length && !CONN) CONN = cs[0].id;
    if (CONN) connSel.value = CONN;
    return cs;
  };
  connSel.addEventListener("change", async () => { CONN = connSel.value; TREE = []; if (tab !== "conn") draw(); });

  const draw = async () => {
    if (tab === "conn") return drawConns(pane, loadConns, () => { CONN = connSel.value || CONN; });
    if (!CONN) { pane.innerHTML = `<div style="color:var(--subtext)">Erst eine AD-Verbindung anlegen (Reiter „Verbindungen“).</div>`; return; }
    if (!TREE.length) { try { TREE = (await aq(`/${CONN}/tree`)).tree; } catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; } }
    if (tab === "browse") return drawBrowse(pane);
    if (tab === "bulk") return drawBulk(pane);
  };
  loadConns().then(draw);
}

// ============================================================ Verbindungen
async function drawConns(pane, loadConns, onChange) {
  let cs; try { cs = await loadConns(); } catch { cs = []; }
  pane.innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:10px"><button class="btn-primary btn-sm" data-add>＋ AD-Verbindung</button></div>
    ${cs.length ? cs.map((c) => `
      <div style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px"><b>${esc(c.name)}</b>
          <span style="font-size:11px;color:var(--subtext)">${esc(c.server || "")}${c.port ? ":" + c.port : ""} ${c.use_ssl ? "· LDAPS" : c.start_tls ? "· StartTLS" : ""}</span>
          <span style="flex:1"></span>
          <button class="btn btn-sm" data-test="${esc(c.id)}">Testen</button>
          <button class="btn btn-sm" data-edit="${esc(c.id)}">Bearbeiten</button>
          <button class="btn btn-sm btn-danger" data-del="${esc(c.id)}">Löschen</button></div>
        <div style="font-size:12px;color:var(--subtext);margin-top:4px">Basis: <code>${esc(c.base_dn || "")}</code> · Bind: ${esc(c.bind_user || "—")}
          · Passwort nur verschlüsselt: ${c.pw_requires_tls ? "ja" : "<span style='color:var(--warn,#d97706)'>nein</span>"}</div>
        <div data-testres="${esc(c.id)}" style="font-size:12px;margin-top:4px"></div>
      </div>`).join("") : `<div style="color:var(--subtext)">Noch keine Verbindungen.</div>`}`;
  pane.querySelector("[data-add]").addEventListener("click", () => connDialog(null, () => drawConns(pane, loadConns, onChange)));
  pane.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", async () => {
    const c = cs.find((x) => x.id === b.dataset.edit); connDialog(c, () => drawConns(pane, loadConns, onChange));
  }));
  pane.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!(await uiConfirm("Verbindung löschen?", { okText: "Löschen", danger: true }))) return;
    try { await aq(`/connections/${b.dataset.del}`, { method: "DELETE" }); if (CONN === b.dataset.del) CONN = null; drawConns(pane, loadConns, onChange); } catch (e) { window.notify?.(e.message, "error"); }
  }));
  pane.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    const out = pane.querySelector(`[data-testres="${b.dataset.test}"]`);
    out.textContent = "⏳ teste …";
    try { const r = await aq(`/connections/${b.dataset.test}/test`, { method: "POST" });
      out.innerHTML = r.ok ? `<span style="color:var(--online,#16a34a)">✓ verbunden${r.encrypted ? " (verschlüsselt)" : " – unverschlüsselt"}</span>` : `<span style="color:var(--danger)">✖ ${esc(r.error)}</span>`;
    } catch (e) { out.innerHTML = `<span style="color:var(--danger)">✖ ${esc(e.message)}</span>`; }
  }));
  onChange();
}

function connDialog(c, done) {
  const ed = !!c;
  c = c || { use_ssl: false, start_tls: false, verify_cert: false, pw_requires_tls: true };
  const ov = overlay(`<b style="font-size:15px">${ed ? "Verbindung bearbeiten" : "AD-Verbindung anlegen"}</b>
    <div style="display:grid;grid-template-columns:140px 1fr;gap:8px 10px;align-items:center;margin-top:12px">
      <label>Name</label><input data-f="name" value="${esc(c.name || "")}" placeholder="z. B. Firma-AD">
      <label>Server</label><input data-f="server" value="${esc(c.server || "")}" placeholder="dc01.firma.de oder ldaps://dc01.firma.de">
      <label>Port</label><input data-f="port" type="number" value="${esc(c.port || "")}" placeholder="leer = 389 / 636">
      <label>Basis-DN</label><input data-f="base_dn" value="${esc(c.base_dn || "")}" placeholder="DC=firma,DC=de">
      <label>Bind-Benutzer</label><input data-f="bind_user" value="${esc(c.bind_user || "")}" placeholder="CN=svc,OU=…,DC=firma,DC=de oder user@firma.de">
      <label>Bind-Passwort</label><input data-f="bind_password" type="password" placeholder="${ed && c.has_password ? "•••• (unverändert lassen)" : ""}">
      <label>Verschlüsselung</label><div style="display:flex;gap:12px;flex-wrap:wrap">
        <label style="display:flex;gap:5px;align-items:center;cursor:pointer"><input type="checkbox" data-f="use_ssl" ${c.use_ssl ? "checked" : ""}> LDAPS</label>
        <label style="display:flex;gap:5px;align-items:center;cursor:pointer"><input type="checkbox" data-f="start_tls" ${c.start_tls ? "checked" : ""}> StartTLS</label>
        <label style="display:flex;gap:5px;align-items:center;cursor:pointer"><input type="checkbox" data-f="verify_cert" ${c.verify_cert ? "checked" : ""}> Zertifikat prüfen</label></div>
      <label>Passwörter</label><label style="display:flex;gap:5px;align-items:center;cursor:pointer;font-size:12.5px">
        <input type="checkbox" data-f="pw_requires_tls" ${c.pw_requires_tls ? "checked" : ""}> Passwort-Änderungen nur über verschlüsselte Verbindung (empfohlen)</label>
    </div>
    <div data-msg style="font-size:12px;margin-top:8px;color:var(--subtext)"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
      <button class="btn" data-test>Testen</button><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>${ed ? "Speichern" : "Anlegen"}</button></div>`);
  const $ = (s) => ov.querySelector(s);
  const read = () => ({ name: $('[data-f="name"]').value.trim(), server: $('[data-f="server"]').value.trim(),
    port: parseInt($('[data-f="port"]').value, 10) || null, base_dn: $('[data-f="base_dn"]').value.trim(),
    bind_user: $('[data-f="bind_user"]').value.trim(), bind_password: $('[data-f="bind_password"]').value,
    use_ssl: $('[data-f="use_ssl"]').checked, start_tls: $('[data-f="start_tls"]').checked,
    verify_cert: $('[data-f="verify_cert"]').checked, pw_requires_tls: $('[data-f="pw_requires_tls"]').checked });
  $("[data-x]").addEventListener("click", () => ov.remove());
  $("[data-test]").addEventListener("click", async () => {
    const m = $("[data-msg]"); m.textContent = "⏳ teste …";
    try { const r = await aq("/connections-test", { method: "POST", body: read() });
      m.innerHTML = r.ok ? `<span style="color:var(--online,#16a34a)">✓ verbunden${r.encrypted ? " (verschlüsselt)" : " – unverschlüsselt"}${r.base_dn ? " · " + esc(r.base_dn) : ""}</span>` : `<span style="color:var(--danger)">✖ ${esc(r.error)}</span>`;
    } catch (e) { m.innerHTML = `<span style="color:var(--danger)">✖ ${esc(e.message)}</span>`; }
  });
  $("[data-ok]").addEventListener("click", async () => {
    try {
      if (ed) await aq(`/connections/${c.id}`, { method: "PUT", body: read() });
      else await aq("/connections", { method: "POST", body: read() });
      ov.remove(); done(); window.notify?.("Gespeichert", "success", 1500);
    } catch (e) { window.notify?.(e.message, "error", 6000); }
  });
}

// ============================================================ Durchsuchen
let KIND = "user";
function drawBrowse(pane) {
  pane.innerHTML = `
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
      <select data-kind>${[["user", "Benutzer"], ["group", "Gruppen"], ["ou", "OUs"], ["computer", "Computer"]].map(([k, l]) => `<option value="${k}" ${k === KIND ? "selected" : ""}>${l}</option>`).join("")}</select>
      <input data-q placeholder="Suche (Name, Benutzername, E-Mail …)" style="flex:1;min-width:180px">
      <select data-base style="max-width:280px"><option value="">— ganzer Baum —</option>${TREE.filter((t) => t.type !== "base").map((t) => `<option value="${esc(t.dn)}">${esc(t.name)}</option>`).join("")}</select>
      <button class="btn-primary btn-sm" data-go>Suchen</button>
      <span style="flex:1"></span>
      <button class="btn btn-sm" data-new-user>＋ Benutzer</button>
      <button class="btn btn-sm" data-new-group>＋ Gruppe</button>
      <button class="btn btn-sm" data-new-ou>＋ OU</button>
    </div>
    <div data-res style="color:var(--subtext)">Suche starten …</div>`;
  const kindSel = pane.querySelector("[data-kind]");
  kindSel.addEventListener("change", () => { KIND = kindSel.value; run(); });
  const run = async () => {
    const res = pane.querySelector("[data-res]");
    res.textContent = "⏳ …";
    try {
      const d = await aq(`/${CONN}/search?kind=${KIND}&q=${encodeURIComponent(pane.querySelector("[data-q]").value.trim())}&base=${encodeURIComponent(pane.querySelector("[data-base]").value)}`);
      res.innerHTML = renderResults(d.results, KIND);
      wireRows(res);
    } catch (e) { res.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; }
  };
  pane.querySelector("[data-go]").addEventListener("click", run);
  pane.querySelector("[data-q]").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  pane.querySelector("[data-new-user]").addEventListener("click", () => userDialog(null, run));
  pane.querySelector("[data-new-group]").addEventListener("click", () => groupDialog(run));
  pane.querySelector("[data-new-ou]").addEventListener("click", () => ouDialog(run));
  run();
}

function renderResults(rows, kind) {
  if (!rows.length) return `<div style="color:var(--subtext)">Keine Treffer.</div>`;
  return `<table class="data-table" style="width:100%;font-size:12.5px">
    <thead><tr><th style="text-align:left">Name</th><th style="text-align:left">${kind === "user" ? "Benutzer / E-Mail" : kind === "group" ? "Mitglieder" : "DN"}</th><th style="text-align:left">Status / OU</th><th></th></tr></thead>
    <tbody>${rows.map((r) => rowFor(r, kind)).join("")}</tbody></table>`;
}

function rowFor(r, kind) {
  const dn = r.dn || r.distinguishedName;
  const ou = (dn || "").split(",").slice(1).join(",");
  if (kind === "user") {
    const st = [r.enabled ? "" : "🔴 deaktiviert", r.locked ? "🔒 gesperrt" : ""].filter(Boolean).join(" ") || "🟢 aktiv";
    return `<tr data-dn="${esc(dn)}">
      <td><b>${esc(r.displayName || r.cn || r.sAMAccountName || "")}</b></td>
      <td>${esc(r.sAMAccountName || "")}${r.mail ? `<div style="color:var(--subtext)">${esc(r.mail)}</div>` : ""}</td>
      <td>${st}<div style="font-size:11px;color:var(--subtext)">${esc(rdnName(ou))}</div></td>
      <td style="white-space:nowrap;text-align:right">
        <button class="btn-tool" data-act="edit" title="Bearbeiten">✏️</button>
        <button class="btn-tool" data-act="pw" title="Passwort">🔑</button>
        <button class="btn-tool" data-act="toggle" title="Aktiv/Deaktiviert">⏯️</button>
        <button class="btn-tool" data-act="move" title="Verschieben">📁</button>
        <button class="btn-tool" data-act="more" title="Mehr">⋯</button></td></tr>`;
  }
  if (kind === "group") {
    const n = Array.isArray(r.member) ? r.member.length : (r.member ? 1 : 0);
    return `<tr data-dn="${esc(dn)}">
      <td><b>${esc(r.sAMAccountName || r.cn || r.name || "")}</b>${r.description ? `<div style="color:var(--subtext);font-size:11px">${esc(r.description)}</div>` : ""}</td>
      <td>${n} Mitglied(er)</td><td style="font-size:11px;color:var(--subtext)">${esc(rdnName(ou))}</td>
      <td style="white-space:nowrap;text-align:right">
        <button class="btn-tool" data-act="members" title="Mitglieder">👥</button>
        <button class="btn-tool" data-act="move" title="Verschieben">📁</button>
        <button class="btn-tool" data-act="del" title="Löschen">🗑️</button></td></tr>`;
  }
  if (kind === "ou") {
    return `<tr data-dn="${esc(dn)}">
      <td><b>${esc(r.ou || r.name || "")}</b></td><td style="font-size:11px;color:var(--subtext)"><code>${esc(dn)}</code></td>
      <td>${esc(r.description || "")}</td>
      <td style="white-space:nowrap;text-align:right">
        <button class="btn-tool" data-act="move" title="Verschieben">📁</button>
        <button class="btn-tool" data-act="del" title="Löschen">🗑️</button></td></tr>`;
  }
  // computer
  return `<tr data-dn="${esc(dn)}">
    <td><b>${esc(r.cn || r.sAMAccountName || "")}</b></td>
    <td>${esc(r.dNSHostName || "")}<div style="color:var(--subtext);font-size:11px">${esc(r.operatingSystem || "")}</div></td>
    <td>${r.enabled === false ? "🔴 deaktiviert" : "🟢 aktiv"}<div style="font-size:11px;color:var(--subtext)">${esc(rdnName((dn || "").split(",").slice(1).join(",")))}</div></td>
    <td style="white-space:nowrap;text-align:right"><button class="btn-tool" data-act="move" title="Verschieben">📁</button><button class="btn-tool" data-act="del" title="Löschen">🗑️</button></td></tr>`;
}

function wireRows(res) {
  res.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    const dn = b.closest("tr").dataset.dn;
    const act = b.dataset.act;
    const reload = () => res.closest("[data-pane]") && res.parentElement && res.querySelector("[data-act]") ? b.closest("table") && refreshBrowse(res) : null;
    const after = () => refreshBrowse(res);
    if (act === "edit") userDialog(dn, after);
    else if (act === "pw") pwDialog(dn, after);
    else if (act === "toggle") { try { const o = (await aq(`/${CONN}/object?dn=${encodeURIComponent(dn)}`)).object; const en = !(int(o.userAccountControl) & 0x2); await aq(`/${CONN}/user/enable`, { method: "POST", body: { dn, enabled: !en } }); after(); } catch (e) { window.notify?.(e.message, "error"); } }
    else if (act === "move") moveDialog(dn, after);
    else if (act === "members") membersDialog(dn, after);
    else if (act === "del") { if (await uiConfirm(`„${rdnName(dn)}“ löschen?`, { okText: "Löschen", danger: true })) { try { await aq(`/${CONN}/object?dn=${encodeURIComponent(dn)}`, { method: "DELETE" }); after(); } catch (e) { window.notify?.(e.message, "error"); } } }
    else if (act === "more") moreMenu(b, dn, after);
  }));
}
function int(v) { return parseInt(v || 0, 10) || 0; }
function refreshBrowse(res) { res.closest("[data-pane]").querySelector("[data-go]")?.click(); }

function moreMenu(btn, dn, after) {
  const menu = document.createElement("div");
  menu.style.cssText = "position:fixed;z-index:9600;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.4)";
  const r = btn.getBoundingClientRect(); menu.style.left = Math.max(8, r.right - 170) + "px"; menu.style.top = r.bottom + 4 + "px";
  const item = (label, fn) => { const el = document.createElement("button"); el.className = "btn"; el.style.cssText = "display:block;width:160px;text-align:left;margin:2px 0;padding:6px 8px"; el.textContent = label; el.addEventListener("click", () => { menu.remove(); fn(); }); menu.appendChild(el); };
  item("🔓 Entsperren", async () => { try { await aq(`/${CONN}/user/unlock`, { method: "POST", body: { dn } }); window.notify?.("Entsperrt", "success"); after(); } catch (e) { window.notify?.(e.message, "error"); } });
  item("⏳ Ablaufdatum …", () => expiryDialog(dn, after));
  item("👥 Gruppen zuweisen …", () => assignGroupDialog(dn, after));
  item("🗑️ Löschen", async () => { if (await uiConfirm(`„${rdnName(dn)}“ löschen?`, { okText: "Löschen", danger: true })) { try { await aq(`/${CONN}/object?dn=${encodeURIComponent(dn)}`, { method: "DELETE" }); after(); } catch (e) { window.notify?.(e.message, "error"); } } });
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener("click", function h() { menu.remove(); document.removeEventListener("click", h); }, { once: true }), 0);
}

// ---- Dialoge
function ouOptions(selected) {
  return TREE.map((t) => `<option value="${esc(t.dn)}" ${t.dn === selected ? "selected" : ""}>${esc(t.type === "base" ? t.name + " (Stamm)" : t.name)}</option>`).join("");
}

async function userDialog(dn, after) {
  let o = {};
  if (dn) { try { o = (await aq(`/${CONN}/object?dn=${encodeURIComponent(dn)}`)).object; } catch (e) { window.notify?.(e.message, "error"); return; } }
  const ed = !!dn;
  const ov = overlay(`<b style="font-size:15px">${ed ? "Benutzer bearbeiten" : "Benutzer anlegen"}</b>
    <div style="display:grid;grid-template-columns:130px 1fr;gap:8px 10px;align-items:center;margin-top:12px">
      ${ed ? "" : `<label>Ziel-OU</label><select data-f="ou">${ouOptions()}</select>`}
      <label>Anmeldename</label><input data-f="sam" value="${esc(o.sAMAccountName || "")}" ${ed ? "disabled" : ""} placeholder="mmuster">
      <label>Anzeigename</label><input data-f="displayName" value="${esc(o.displayName || "")}">
      <label>Vorname</label><input data-f="givenName" value="${esc(o.givenName || "")}">
      <label>Nachname</label><input data-f="sn" value="${esc(o.sn || "")}">
      <label>E-Mail</label><input data-f="mail" value="${esc(o.mail || "")}">
      <label>Telefon</label><input data-f="telephoneNumber" value="${esc(o.telephoneNumber || "")}">
      <label>Abteilung</label><input data-f="department" value="${esc(o.department || "")}">
      <label>Titel</label><input data-f="title" value="${esc(o.title || "")}">
      ${ed ? "" : `<label>Passwort</label><input data-f="password" type="password" placeholder="optional – sonst deaktiviert anlegen">
      <label></label><label style="display:flex;gap:6px;align-items:center;font-size:12.5px"><input type="checkbox" data-f="must_change"> Muss Passwort bei nächster Anmeldung ändern</label>`}
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>${ed ? "Speichern" : "Anlegen"}</button></div>`);
  const $ = (s) => ov.querySelector(s);
  $("[data-x]").addEventListener("click", () => ov.remove());
  $("[data-ok]").addEventListener("click", async () => {
    const flds = {}; ["displayName", "givenName", "sn", "mail", "telephoneNumber", "department", "title"].forEach((k) => { flds[k] = $(`[data-f="${k}"]`).value.trim(); });
    try {
      if (ed) await aq(`/${CONN}/object`, { method: "PATCH", body: { dn, attrs: flds } });
      else {
        flds.must_change = $('[data-f="must_change"]').checked;
        await aq(`/${CONN}/users`, { method: "POST", body: { ou_dn: $('[data-f="ou"]').value, sam: $('[data-f="sam"]').value.trim(), password: $('[data-f="password"]').value, enabled: !!$('[data-f="password"]').value, fields: flds } });
      }
      ov.remove(); after(); window.notify?.("Gespeichert", "success", 1500);
    } catch (e) { window.notify?.(e.message, "error", 6000); }
  });
}

function pwDialog(dn, after) {
  const ov = overlay(`<b style="font-size:15px">Passwort setzen – ${esc(rdnName(dn))}</b>
    <div style="margin-top:12px"><input data-pw type="password" placeholder="Neues Passwort" style="width:100%"></div>
    <label style="display:flex;gap:6px;align-items:center;margin-top:8px;font-size:12.5px"><input type="checkbox" data-mc> Muss Passwort bei nächster Anmeldung ändern</label>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Setzen</button></div>`);
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
  ov.querySelector("[data-ok]").addEventListener("click", async () => {
    const pw = ov.querySelector("[data-pw]").value;
    if (!pw) { window.notify?.("Passwort fehlt", "warning"); return; }
    try { await aq(`/${CONN}/user/password`, { method: "POST", body: { dn, password: pw, must_change: ov.querySelector("[data-mc]").checked } }); ov.remove(); after(); window.notify?.("Passwort gesetzt", "success"); } catch (e) { window.notify?.(e.message, "error", 7000); }
  });
}

function moveDialog(dn, after) {
  const ov = overlay(`<b style="font-size:15px">Verschieben – ${esc(rdnName(dn))}</b>
    <div style="margin-top:12px;display:grid;grid-template-columns:120px 1fr;gap:8px;align-items:center">
      <label>Ziel-OU</label><select data-ou>${ouOptions()}</select></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Verschieben</button></div>`);
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
  ov.querySelector("[data-ok]").addEventListener("click", async () => {
    try { await aq(`/${CONN}/move`, { method: "POST", body: { dn, new_parent: ov.querySelector("[data-ou]").value } }); ov.remove(); after(); window.notify?.("Verschoben", "success"); } catch (e) { window.notify?.(e.message, "error"); }
  });
}

function expiryDialog(dn, after) {
  const ov = overlay(`<b style="font-size:15px">Ablaufdatum – ${esc(rdnName(dn))}</b>
    <div style="margin-top:12px;display:flex;gap:8px;align-items:center">
      <label style="display:flex;gap:5px;align-items:center"><input type="radio" name="ex" value="never" checked> Nie</label>
      <label style="display:flex;gap:5px;align-items:center"><input type="radio" name="ex" value="date"> Am <input type="date" data-d></label></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Setzen</button></div>`);
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
  ov.querySelector("[data-ok]").addEventListener("click", async () => {
    const mode = ov.querySelector('input[name="ex"]:checked').value;
    const when = mode === "never" ? "never" : ov.querySelector("[data-d]").value;
    if (mode === "date" && !when) { window.notify?.("Datum fehlt", "warning"); return; }
    try { await aq(`/${CONN}/user/expiry`, { method: "POST", body: { dn, when } }); ov.remove(); after(); window.notify?.("Gespeichert", "success"); } catch (e) { window.notify?.(e.message, "error"); }
  });
}

function groupDialog(after) {
  const ov = overlay(`<b style="font-size:15px">Gruppe anlegen</b>
    <div style="display:grid;grid-template-columns:120px 1fr;gap:8px 10px;align-items:center;margin-top:12px">
      <label>Ziel-OU</label><select data-ou>${ouOptions()}</select>
      <label>Name</label><input data-name placeholder="z. B. Vertrieb">
      <label>Bereich</label><select data-scope><option value="global">Global</option><option value="domainlocal">Domänenlokal</option><option value="universal">Universal</option></select>
      <label>Typ</label><select data-sec><option value="1">Sicherheit</option><option value="0">Verteilung</option></select>
      <label>Beschreibung</label><input data-desc></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Anlegen</button></div>`);
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
  ov.querySelector("[data-ok]").addEventListener("click", async () => {
    try { await aq(`/${CONN}/groups`, { method: "POST", body: { ou_dn: ov.querySelector("[data-ou]").value, name: ov.querySelector("[data-name]").value.trim(), scope: ov.querySelector("[data-scope]").value, security: ov.querySelector("[data-sec]").value === "1", description: ov.querySelector("[data-desc]").value.trim() } }); ov.remove(); after(); window.notify?.("Gruppe angelegt", "success"); } catch (e) { window.notify?.(e.message, "error"); }
  });
}

function ouDialog(after) {
  const ov = overlay(`<b style="font-size:15px">OU anlegen</b>
    <div style="display:grid;grid-template-columns:120px 1fr;gap:8px 10px;align-items:center;margin-top:12px">
      <label>Übergeordnet</label><select data-parent>${ouOptions()}</select>
      <label>Name</label><input data-name placeholder="z. B. Vertrieb">
      <label>Beschreibung</label><input data-desc></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Anlegen</button></div>`);
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
  ov.querySelector("[data-ok]").addEventListener("click", async () => {
    try { await aq(`/${CONN}/ous`, { method: "POST", body: { parent_dn: ov.querySelector("[data-parent]").value, name: ov.querySelector("[data-name]").value.trim(), description: ov.querySelector("[data-desc]").value.trim() } }); ov.remove(); TREE = []; after(); window.notify?.("OU angelegt", "success"); } catch (e) { window.notify?.(e.message, "error"); }
  });
}

async function membersDialog(groupDn, after) {
  let members = [];
  try { members = (await aq(`/${CONN}/group-members?dn=${encodeURIComponent(groupDn)}`)).members; } catch (e) { window.notify?.(e.message, "error"); return; }
  const ov = overlay(`<b style="font-size:15px">Mitglieder – ${esc(rdnName(groupDn))}</b>
    <div style="display:flex;gap:6px;margin:12px 0"><input data-add placeholder="Benutzer suchen (Name/Anmeldename)" style="flex:1"><button class="btn btn-sm" data-search>Suchen</button></div>
    <div data-cands style="max-height:140px;overflow:auto;font-size:12px"></div>
    <div style="font-weight:600;margin:10px 0 4px">Aktuelle Mitglieder (${members.length})</div>
    <div data-list style="max-height:200px;overflow:auto"></div>
    <div style="text-align:right;margin-top:12px"><button class="btn" data-x>Schließen</button></div>`, 560);
  const $ = (s) => ov.querySelector(s);
  const drawList = () => { $("[data-list]").innerHTML = members.length ? members.map((m) => `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)"><span style="flex:1">${esc(m.name)} <span style="color:var(--subtext);font-size:11px">${esc(m.sam || "")}</span></span><button class="btn btn-sm btn-danger" data-rm="${esc(m.dn)}">Entfernen</button></div>`).join("") : `<div style="color:var(--subtext)">Keine Mitglieder.</div>`; wireList(); };
  const wireList = () => $("[data-list]").querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => { try { await aq(`/${CONN}/group/member`, { method: "POST", body: { group_dn: groupDn, member_dn: b.dataset.rm, add: false } }); members = members.filter((m) => m.dn !== b.dataset.rm); drawList(); after(); } catch (e) { window.notify?.(e.message, "error"); } }));
  $("[data-x]").addEventListener("click", () => ov.remove());
  const doSearch = async () => {
    try { const r = await aq(`/${CONN}/search?kind=user&q=${encodeURIComponent($("[data-add]").value.trim())}`); const have = new Set(members.map((m) => m.dn));
      $("[data-cands]").innerHTML = r.results.filter((u) => !have.has(u.dn)).slice(0, 20).map((u) => `<div style="display:flex;gap:8px;align-items:center;padding:3px 0"><span style="flex:1">${esc(u.displayName || u.sAMAccountName)} <span style="color:var(--subtext);font-size:11px">${esc(u.sAMAccountName || "")}</span></span><button class="btn btn-sm" data-pick="${esc(u.dn)}" data-name="${esc(u.displayName || u.sAMAccountName)}" data-sam="${esc(u.sAMAccountName || "")}">Hinzufügen</button></div>`).join("") || `<div style="color:var(--subtext)">Keine weiteren Treffer.</div>`;
      $("[data-cands]").querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", async () => { try { await aq(`/${CONN}/group/member`, { method: "POST", body: { group_dn: groupDn, member_dn: b.dataset.pick, add: true } }); members.push({ dn: b.dataset.pick, name: b.dataset.name, sam: b.dataset.sam }); drawList(); doSearch(); after(); } catch (e) { window.notify?.(e.message, "error"); } }));
    } catch (e) { window.notify?.(e.message, "error"); }
  };
  $("[data-search]").addEventListener("click", doSearch);
  $("[data-add]").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  drawList();
}

async function assignGroupDialog(userDn, after) {
  const ov = overlay(`<b style="font-size:15px">Gruppen zuweisen – ${esc(rdnName(userDn))}</b>
    <div style="display:flex;gap:6px;margin:12px 0"><input data-q placeholder="Gruppe suchen" style="flex:1"><button class="btn btn-sm" data-search>Suchen</button></div>
    <div data-res style="max-height:260px;overflow:auto;font-size:12.5px"></div>
    <div style="text-align:right;margin-top:12px"><button class="btn" data-x>Schließen</button></div>`, 520);
  const $ = (s) => ov.querySelector(s);
  $("[data-x]").addEventListener("click", () => ov.remove());
  const run = async () => { try { const r = await aq(`/${CONN}/search?kind=group&q=${encodeURIComponent($("[data-q]").value.trim())}`);
    $("[data-res]").innerHTML = r.results.slice(0, 30).map((g) => `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)"><span style="flex:1">${esc(g.sAMAccountName || g.cn)}</span><button class="btn btn-sm" data-g="${esc(g.dn)}">Hinzufügen</button></div>`).join("") || `<div style="color:var(--subtext)">Keine Treffer.</div>`;
    $("[data-res]").querySelectorAll("[data-g]").forEach((b) => b.addEventListener("click", async () => { try { await aq(`/${CONN}/group/member`, { method: "POST", body: { group_dn: b.dataset.g, member_dn: userDn, add: true } }); window.notify?.("Zur Gruppe hinzugefügt", "success"); after(); } catch (e) { window.notify?.(e.message, "error"); } }));
  } catch (e) { window.notify?.(e.message, "error"); } };
  $("[data-search]").addEventListener("click", run);
  $("[data-q]").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  run();
}

// ============================================================ Massen-Import
function drawBulk(pane) {
  pane.innerHTML = `
    <p style="color:var(--subtext);margin:0 0 8px">CSV mit Kopfzeile. Spalten: <code>sam,displayName,givenName,sn,mail,password,ou</code>
      (nur <code>sam</code> ist Pflicht; <code>ou</code> optional je Zeile, sonst die unten gewählte).</p>
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
      <label style="font-size:12px;color:var(--subtext)">Standard-OU</label><select data-ou style="min-width:240px">${ouOptions()}</select>
      <label style="font-size:12.5px;display:flex;gap:5px;align-items:center"><input type="checkbox" data-en checked> aktiviert anlegen</label></div>
    <textarea data-csv rows="10" style="width:100%;font-family:var(--mono,monospace);font-size:12px" placeholder="sam,displayName,givenName,sn,mail,password&#10;mmuster,Max Muster,Max,Muster,max@firma.de,Start!2026&#10;eschmidt,Eva Schmidt,Eva,Schmidt,eva@firma.de,Start!2026"></textarea>
    <div style="margin-top:8px"><button class="btn-primary btn-sm" data-go>Import starten</button></div>
    <div data-res style="margin-top:10px;font-size:12.5px"></div>`;
  pane.querySelector("[data-go]").addEventListener("click", async () => {
    const csv = pane.querySelector("[data-csv]").value.trim();
    if (!csv) { window.notify?.("CSV fehlt", "warning"); return; }
    const res = pane.querySelector("[data-res]"); res.textContent = "⏳ importiere …";
    try { const r = await aq(`/${CONN}/users/bulk`, { method: "POST", body: { ou_dn: pane.querySelector("[data-ou]").value, csv, default_enabled: pane.querySelector("[data-en]").checked } });
      res.innerHTML = `<div style="color:var(--online,#16a34a)">✓ ${r.created.length} Benutzer angelegt.</div>${r.errors.length ? `<div style="color:var(--danger);margin-top:6px"><b>${r.errors.length} Fehler:</b><ul style="margin:4px 0;padding-left:18px">${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}`;
    } catch (e) { res.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; }
  });
}

// ============================================================ Helfer
function overlay(html, width = 620) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;width:${width}px;max-width:100%;max-height:calc(100dvh - 24px);overflow:auto;font-size:13px">${html}</div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  return ov;
}

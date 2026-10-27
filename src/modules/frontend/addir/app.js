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
    <div class="ad-root" style="display:flex;height:100%;min-height:0;font-size:13px">
      <aside class="ad-side" style="width:230px;flex:0 0 230px;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:auto;background:var(--panel-2,var(--panel))">
        <div style="padding:14px 14px 10px;display:flex;align-items:center;gap:10px">
          <div style="width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--accent-2,#38bdf8));display:grid;place-items:center;color:#07211d;font-weight:900;font-size:18px">🗂️</div>
          <div style="min-width:0"><div data-conn-name style="font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">—</div>
            <div style="font-size:11.5px;color:var(--subtext)">AD-Management</div></div>
        </div>
        <div style="padding:0 10px">
          <select data-conn style="width:100%;margin-bottom:8px"></select>
        </div>
        <nav data-nav style="padding:4px 8px;display:flex;flex-direction:column;gap:2px"></nav>
      </aside>
      <main data-pane style="flex:1;overflow:auto;padding:0;min-height:0;position:relative"></main>
    </div>`;
  const pane = body.querySelector("[data-pane]");
  const connSel = body.querySelector("[data-conn]");
  const navEl = body.querySelector("[data-nav]");
  let view = "overview";
  let counts = {};

  const NAV = [
    { id: "overview", icon: "▦", label: "Übersicht" },
    { id: "computer", icon: "🖥️", label: "Computer", count: () => counts.computers },
    { id: "user", icon: "👤", label: "Benutzer", count: () => counts.users },
    { id: "group", icon: "👥", label: "Gruppen", count: () => counts.groups },
    { id: "ou", icon: "🗂️", label: "Organisationseinheiten", count: () => counts.ous },
    { sep: "Administration" },
    { id: "conn", icon: "🔌", label: "Verbindungen" },
    { id: "bulk", icon: "⬆️", label: "Massen-Import" },
  ];

  const drawNav = () => {
    navEl.innerHTML = NAV.map((n) => {
      if (n.sep) return `<div style="font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--subtext);padding:12px 10px 4px">${esc(n.sep)}</div>`;
      const c = n.count ? n.count() : null;
      const act = view === n.id;
      return `<a data-view="${n.id}" style="display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:9px;cursor:pointer;${act ? "background:color-mix(in srgb,var(--accent) 16%,transparent);font-weight:600" : ""}">
        <span style="width:18px;text-align:center;${act ? "color:var(--accent)" : ""}">${n.icon}</span>
        <span style="flex:1">${esc(n.label)}</span>
        ${c != null ? `<span style="font-size:11.5px;color:var(--subtext)">${c}</span>` : ""}</a>`;
    }).join("");
    navEl.querySelectorAll("[data-view]").forEach((a) => a.addEventListener("click", () => select(a.dataset.view)));
  };

  const select = (v) => { view = v; drawNav(); draw(); };

  const draw = async () => {
    if (view === "conn") return drawConns(pane, loadConns, afterConnChange);
    if (view === "bulk") { if (!ensureConn()) return; TREE.length ? drawBulk(pane) : loadTree().then(() => drawBulk(pane)); return; }
    if (view === "overview") { if (!ensureConn()) return; return drawDashboard(pane, () => select("overview")); }
    // computer/user/group/ou -> Browse mit fester Art
    if (!ensureConn()) return;
    KIND = view;
    const go = () => drawBrowse(pane);
    TREE.length ? go() : loadTree().then(go);
  };

  const ensureConn = () => {
    if (CONN) return true;
    pane.innerHTML = `<div style="padding:20px;color:var(--subtext)">Keine AD-Verbindung. Lege unter <b>Verbindungen</b> eine an.</div>`;
    return false;
  };

  const loadTree = async () => {
    try { TREE = (await aq(`/${CONN}/tree`)).tree; } catch { TREE = []; }
  };

  const loadConns = async () => {
    let d;
    try { d = await aq("/connections"); } catch (e) { pane.innerHTML = `<div style="padding:20px;color:var(--danger)">${esc(e.message)}</div>`; return []; }
    const cs = d.connections;
    connSel.innerHTML = cs.length ? cs.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("") : `<option value="">keine Verbindung</option>`;
    if (cs.length && !CONN) CONN = cs[0].id;
    if (CONN) connSel.value = CONN;
    const cur = cs.find((c) => c.id === CONN);
    body.querySelector("[data-conn-name]").textContent = cur ? cur.name : "AD-Management";
    return cs;
  };

  const afterConnChange = () => { CONN = connSel.value || CONN; };

  connSel.addEventListener("change", async () => {
    CONN = connSel.value; TREE = []; counts = {};
    const cs = await aq("/connections").then((d) => d.connections).catch(() => []);
    const cur = cs.find((c) => c.id === CONN);
    body.querySelector("[data-conn-name]").textContent = cur ? cur.name : "AD-Management";
    loadDashboardCounts().then(drawNav);
    draw();
  });

  async function loadDashboardCounts() {
    if (!CONN) return;
    try {
      const d = await aq(`/${CONN}/dashboard`);
      counts = { computers: d.computers.total, users: d.users.total, groups: d.groups, ous: d.ous };
      window.__adDash = window.__adDash || {}; window.__adDash[CONN] = d;
    } catch { /* zeigt die Dashboard-Seite den Fehler */ }
  }

  drawNav();
  loadConns().then(() => { loadDashboardCounts().then(drawNav); draw(); });
}

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

// ============================================================ Dashboard / Übersicht
function statCard(big, label, sub) {
  return `<div style="border:1px solid var(--border);border-radius:14px;padding:16px 18px;background:var(--panel)">
    <div style="font-size:12.5px;color:var(--subtext)">${esc(label)}</div>
    <div style="font-size:34px;font-weight:800;line-height:1.1;margin-top:2px">${big}</div>
    ${sub ? `<div style="font-size:12px;color:var(--subtext);margin-top:2px">${sub}</div>` : ""}</div>`;
}

function bar(segments) {
  // segments: [{v, color}]
  const total = segments.reduce((a, s) => a + s.v, 0) || 1;
  return `<div style="display:flex;height:8px;border-radius:6px;overflow:hidden;margin:10px 0 6px">${
    segments.map((s) => `<div style="width:${(s.v / total * 100).toFixed(1)}%;background:${s.color}"></div>`).join("")}</div>`;
}

function listRow(icon, title, meta, right) {
  return `<div style="display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--border)">
    <span style="font-size:16px">${icon}</span>
    <span style="flex:1;min-width:0"><b style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(title)}</b>
      ${meta ? `<span style="font-size:11.5px;color:var(--subtext)">${esc(meta)}</span>` : ""}</span>
    ${right || ""}</div>`;
}

function hbar(label, value, max, color) {
  const pct = max ? Math.max(2, value / max * 100) : 0;
  return `<div style="padding:7px 0;border-bottom:1px solid var(--border)">
    <div style="display:flex;justify-content:space-between;font-size:13px"><span>${esc(label)}</span><b>${value}</b></div>
    <div style="height:5px;border-radius:4px;background:var(--panel-2,#0d1626);margin-top:5px;overflow:hidden"><div style="width:${pct.toFixed(1)}%;height:100%;background:${color}"></div></div></div>`;
}

async function drawDashboard(pane, reload) {
  pane.innerHTML = `<div style="padding:22px 26px;color:var(--subtext)">⏳ Lade Übersicht …</div>`;
  let d;
  try { d = await aq(`/${CONN}/dashboard`); } catch (e) { pane.innerHTML = `<div style="padding:22px 26px;color:var(--danger)">${esc(e.message)}</div>`; return; }
  const c = d.computers, u = d.users;
  const ACC = "var(--accent)", WARN = "#e0a43b", DANGER = "#e06a7d", SUB = "var(--subtext)";
  const domain = (d.base_dn || "").split(",").map((p) => p.split("=").slice(1).join("=")).filter(Boolean).join(".");
  const maxOs = Math.max(1, ...d.os.map((x) => x.count));
  const maxOu = Math.max(1, ...d.per_ou.map((x) => x.count));
  pane.innerHTML = `
    <div style="padding:20px 26px;max-width:1200px;margin:0 auto">
      <div style="display:flex;align-items:flex-start;gap:12px;margin-bottom:6px">
        <div style="flex:1"><h1 style="margin:0;font-size:26px">Übersicht</h1>
          <div style="color:var(--subtext)">Zustand der Domäne auf einen Blick.</div></div>
        <button class="btn btn-sm" data-refresh>⟳ Aktualisieren</button>
      </div>
      <div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:14px">
        <div style="border:1px solid var(--border);border-radius:14px;padding:16px 18px;background:var(--panel)">
          <div style="font-size:12.5px;color:var(--subtext)">Computerkonten in der Domäne</div>
          <div style="font-size:40px;font-weight:800;line-height:1.05">${c.total}
            <span style="font-size:15px;font-weight:600;color:var(--subtext)">davon ${c.servers} Server, ${c.dc} Domain Controller</span></div>
          ${bar([{ v: c.active, color: ACC }, { v: c.inactive, color: WARN }, { v: c.disabled, color: DANGER }])}
          <div style="display:flex;gap:16px;font-size:12px;color:var(--subtext);flex-wrap:wrap">
            <span>🟩 ${c.active} Aktiv genutzt</span><span>🟨 ${c.inactive} Länger als ${d.inactive_days} Tage inaktiv</span><span>🟥 ${c.disabled} Deaktiviert</span></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:14px">
          ${statCard(u.active, "Aktive Benutzer", `${u.disabled} deaktiviert`)}
          ${statCard(d.groups, "Gruppen", "Sicherheits- und Verteilergruppen")}
          ${statCard(d.ous, "OUs und Container", esc(domain || d.base_dn))}
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px">
        <div style="border:1px solid var(--border);border-radius:14px;padding:16px 18px;background:var(--panel)">
          <div style="display:flex;align-items:center;margin-bottom:4px"><b style="flex:1">Inaktive Computer</b>
            <span style="font-size:12px;color:var(--subtext)">länger als ${d.inactive_days} Tage nicht angemeldet</span></div>
          ${d.inactive_computers.length ? d.inactive_computers.map((x) =>
            listRow("🖥️", x.name, "AD / " + x.ou,
              `<span style="font-size:11px;background:color-mix(in srgb,${WARN} 22%,transparent);color:${WARN};border-radius:8px;padding:2px 8px">${x.days == null ? "nie" : x.days + " T"}</span>`)).join("")
            : `<div style="color:var(--subtext);padding:8px 0">Keine inaktiven Computer.</div>`}
        </div>
        <div style="border:1px solid var(--border);border-radius:14px;padding:16px 18px;background:var(--panel)">
          <b>Betriebssysteme</b>
          <div style="margin-top:6px">${d.os.map((o) => hbar(o.name, o.count, maxOs, ACC)).join("") || `<div style="color:var(--subtext)">—</div>`}</div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px">
        <div style="border:1px solid var(--border);border-radius:14px;padding:16px 18px;background:var(--panel)">
          <b>Zuletzt angelegt</b>
          <div style="margin-top:6px">${d.recent.length ? d.recent.map((r) =>
            listRow(r.os.toLowerCase().includes("server") ? "🖳" : "🖥️", r.name, r.os,
              `<span style="font-size:12px;color:var(--subtext)">${esc(r.when)}</span>`)).join("")
            : `<div style="color:var(--subtext)">—</div>`}</div>
        </div>
        <div style="border:1px solid var(--border);border-radius:14px;padding:16px 18px;background:var(--panel)">
          <b>Computer je OU</b>
          <div style="margin-top:6px">${d.per_ou.map((o) => hbar(o.name, o.count, maxOu, "var(--accent-2,#38bdf8)")).join("") || `<div style="color:var(--subtext)">—</div>`}</div>
        </div>
      </div>
    </div>`;
  pane.querySelector("[data-refresh]").addEventListener("click", reload);
}

// ============================================================ Durchsuchen
// ============================================================ Durchsuchen
let KIND = "user";
const KIND_LABEL = { user: "Benutzer", group: "Gruppen", ou: "Organisationseinheiten", computer: "Computer" };
function drawBrowse(pane) {
  const title = KIND_LABEL[KIND] || "Objekte";
  const newBtn = KIND === "computer"
    ? ""
    : `<button class="btn-primary btn-sm" data-new>＋ ${KIND === "user" ? "Benutzer" : KIND === "group" ? "Gruppe" : "OU"}</button>`;
  pane.innerHTML = `
    <div style="padding:20px 26px;max-width:1200px;margin:0 auto">
      <h1 style="margin:0 0 2px;font-size:24px">${esc(title)}</h1>
      <div style="color:var(--subtext);margin-bottom:12px" data-count>Lade …</div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
        <input data-q placeholder="Suche …" style="flex:1;min-width:200px">
        <select data-base style="max-width:280px"><option value="">— ganzer Baum —</option>${TREE.filter((t) => t.type !== "base").map((t) => `<option value="${esc(t.dn)}">${esc(t.name)}</option>`).join("")}</select>
        <button class="btn btn-sm" data-go>Suchen</button>
        <span style="flex:1"></span>
        ${newBtn}
      </div>
      <div data-res style="color:var(--subtext)">⏳ …</div>
    </div>`;
  pane.querySelector("[data-new]")?.addEventListener("click", () => {
    if (KIND === "user") userDialog(null, run);
    else if (KIND === "group") groupDialog(run);
    else if (KIND === "ou") ouDialog(run);
  });
  const run = async () => {
    const res = pane.querySelector("[data-res]");
    res.textContent = "⏳ …";
    try {
      const d = await aq(`/${CONN}/search?kind=${KIND}&q=${encodeURIComponent(pane.querySelector("[data-q]").value.trim())}&base=${encodeURIComponent(pane.querySelector("[data-base]").value)}`);
      res.innerHTML = renderResults(d.results, KIND);
      const cnt = pane.querySelector("[data-count]"); if (cnt) cnt.textContent = `${d.results.length} ${KIND_LABEL[KIND] || ""}`;
      wireRows(res);
    } catch (e) { res.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; }
  };
  pane.querySelector("[data-go]").addEventListener("click", run);
  pane.querySelector("[data-q]").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
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

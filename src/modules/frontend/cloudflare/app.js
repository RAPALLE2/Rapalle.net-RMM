// app.js (Modul "cloudflare") - Cloudflare Tunnel verwalten
// ---------------------------------------------------------
// Register: Übersicht | Verbindung | Routen | Nodes | Hilfe
// Backend: cf_routes.py (/api/cloudflare), Login fuer Access: oidc_routes.py (/cf-oidc/...)

import { getToken } from "/js/app/api.js";
import { state } from "/js/app/state.js";
import { esc, uiConfirm } from "/js/app/utils.js";
import { initSubjectPicker, readSubjectPicker, subjectPickerHtml } from "/js/app/subjectpicker.js";

async function cq(path, opts = {}) {
  const token = getToken() || "";
  const headers = { ...(token ? { Authorization: "Bearer " + token } : {}) };
  if (opts.body !== undefined && typeof opts.body !== "string") opts = { ...opts, body: JSON.stringify(opts.body) };
  if (opts.body) headers["Content-Type"] = "application/json";
  const res = await fetch("/api/cloudflare" + path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* egal */ }
    throw new Error(msg);
  }
  return res.json();
}

const KIND = {
  dashboard: "RMM-Dashboard", webdav: "WebDAV", sftp: "SFTP (Relay)", ftp: "FTP (Relay)",
  console_ssh: "RMM-Konsole (SSH)", website: "Website / Webhost", docker: "Docker-Container",
  agent: "Agent: RDP / SSH / VNC", custom: "Eigene Adresse",
};
const MODE = {
  http: "Im Browser (https://…)", browser: "Im Browser über Cloudflare Access",
  client: "Mit cloudflared auf dem PC", warp: "Privates Netz (WARP-Client)",
};
const ACCESS = { none: "Offen (ohne Cloudflare-Login)", rmm: "RMM-Login (lokal / AD, mit 2FA)", otp: "Einmal-PIN per E-Mail" };
const ACC_SHORT = { none: "offen", rmm: "RMM-Login", otp: "E-Mail-PIN", service: "Service-Token" };
const lifeText = (rm) => !rm ? "" : rm.lifetime === 0 ? "dauerhaft" : rm.lifetime === "session" ? `bis Nutzungsende${rm.leases && Object.keys(rm.leases).length ? " · aktiv" : ""}`
  : `bis ${new Date((rm.expires || 0) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
const clientName = (id) => state.clients?.find((c) => c.id === id)?.hostname || id?.slice(0, 8) || "?";
const box = (html) => `<div style="border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:12px">${html}</div>`;
const fmtTime = (s) => (s ? new Date(s * 1000).toLocaleString() : "–");

export function renderCloudflare(body) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;min-height:0;font-size:13px">
      <div class="tab-bar" style="padding:8px 12px 0;flex:0 0 auto" data-tabs>
        ${["overview:Übersicht", "connect:Verbindung", "routes:Routen", "nodes:Nodes", "help:Hilfe"].map((t, i) => {
          const [k, l] = t.split(":");
          return `<button class="tab-btn ${i ? "" : "active"}" data-tab="${k}">${l}</button>`;
        }).join("")}
      </div>
      <div data-pane style="flex:1;overflow:auto;padding:12px 14px;min-height:0"></div>
    </div>`;
  const pane = body.querySelector("[data-pane]");
  let tab = "overview";
  let S = null;
  let timer = null;
  const load = async () => { S = await cq("/state"); return S; };
  const draw = async (reload = true) => {
    if (!body.isConnected) { clearInterval(timer); return; }
    try { if (reload || !S) await load(); } catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    ({ overview: drawOverview, connect: drawConnect, routes: drawRoutes, nodes: drawNodes, help: drawHelp })[tab](pane, S, draw, (t) => select(t));
  };
  const select = (t) => {
    tab = t;
    body.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
    draw(false);
  };
  body.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => select(b.dataset.tab)));
  draw();
  timer = setInterval(() => { if (tab === "overview" && body.isConnected) draw(); else if (!body.isConnected) clearInterval(timer); }, 5000);
}

// ------------------------------------------------------------------ Übersicht
function drawOverview(pane, S, redraw, select) {
  const c = S.connector || {};
  const ready = S.auth.configured && S.account.id;
  const t = S.tunnels.server || {};
  const st = !t.id ? ["⚪", "Noch kein Tunnel"] : c.running && c.connected ? ["🟢", "Verbunden"]
    : c.running ? ["🟡", "Verbindet …"] : c.download ? ["🟡", c.download] : ["🔴", "Getrennt"];
  pane.innerHTML = `
    ${!ready ? box(`<b>Erste Schritte</b><ol style="margin:6px 0 0 18px;padding:0;line-height:1.7">
        <li>Unter <a href="#" data-go="connect" style="color:var(--accent)">Verbindung</a> den API-Token eintragen und das Konto wählen.</li>
        <li>Unter <a href="#" data-go="routes" style="color:var(--accent)">Routen</a> festlegen, was erreichbar sein soll – der Tunnel wird dabei automatisch angelegt.</li></ol>`) : ""}
    ${box(`<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span style="font-size:15px">${st[0]} <b>${esc(st[1])}</b></span>
        <span style="color:var(--subtext)">${t.name ? `Tunnel <code>${esc(t.name)}</code>` : ""} · cloudflared ${esc(c.version || "")}${c.installed ? "" : " (wird geladen)"}</span>
        <span style="flex:1"></span>
        <button class="btn btn-sm" data-sync ${ready ? "" : "disabled"}>⟳ Jetzt abgleichen</button>
        ${c.running ? `<button class="btn btn-sm" data-conn="restart">Neu verbinden</button><button class="btn btn-sm btn-danger" data-conn="stop">Trennen</button>`
          : `<button class="btn btn-sm" data-conn="start" ${t.id ? "" : "disabled"}>Verbinden</button>`}
      </div>
      <div style="color:var(--subtext);font-size:12px;margin-top:6px">Konto: <b>${esc(S.account.name || "–")}</b> · Team: ${esc(S.team_domain || "–")}
        · Letzter Abgleich: ${fmtTime(S.last_sync)} · ${S.routes.length} Route(n)${c.restarts ? ` · Neustarts: ${c.restarts}` : ""}</div>
      ${S.last_error ? `<div style="color:var(--danger);font-size:12px;margin-top:6px">⚠ ${esc(S.last_error)}</div>` : ""}`)}
    ${S.routes.length ? box(`<b>Routen</b>${routeTable(S.routes, false)}`) : ""}
    ${box(`<b>Connector-Protokoll</b>
      <pre style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px;max-height:220px;overflow:auto;font-size:11.5px;margin:6px 0 0">${esc((c.log || []).join("\n") || "–")}</pre>`)}`;
  pane.querySelectorAll("[data-go]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); select(a.dataset.go); }));
  pane.querySelector("[data-sync]")?.addEventListener("click", (e) => doSync(e.currentTarget, redraw));
  pane.querySelectorAll("[data-conn]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try { await cq(`/connector/${b.dataset.conn}`, { method: "POST" }); } catch (e) { window.notify?.(e.message, "error"); }
    setTimeout(redraw, 800);
  }));
}

async function doSync(btn, redraw) {
  btn.disabled = true;
  btn.textContent = "⏳ Gleiche ab …";
  try {
    const r = await cq("/sync", { method: "POST" });
    window.notify?.(r.errors?.length ? `Abgeglichen mit Fehlern: ${r.errors.join("; ")}` : "Mit Cloudflare abgeglichen",
                    r.errors?.length ? "warning" : "success", 7000);
  } catch (e) { window.notify?.(e.message, "error", 8000); }
  redraw();
}

function routeTable(routes, actions) {
  return `<table class="data-table" style="width:100%;margin-top:6px">
    <thead><tr><th style="text-align:left">Name</th><th style="text-align:left">Ziel</th><th style="text-align:left">Erreichbar über</th>
      <th>Schutz</th><th>Status</th>${actions ? "<th></th>" : ""}</tr></thead>
    <tbody>${routes.map((r) => `<tr>
      <td><b>${esc(r.name)}</b><div style="font-size:11px;color:var(--subtext)">${esc(KIND[r.kind] || r.kind)}${r.kind === "agent"
        ? ` · ${esc(clientName(r.ref?.client_id))} ${esc((r.ref?.proto || "").toUpperCase())}${r.endpoint === "node" ? ` · Node ${esc(clientName(r.node_id))}` : " · über RMM-Server"}` : ""}</div></td>
      <td style="font-size:11.5px;color:var(--subtext)"><code>${esc(r.state?.service || "–")}</code></td>
      <td style="font-size:12px">${r.mode === "warp" ? `WARP: <code>${esc(r.state?.warp_address || "…")}</code>`
        : r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener" style="color:var(--accent)">${esc(r.url.replace("https://", ""))}</a>` : "–"}
        <div style="font-size:11px;color:var(--subtext)">${esc(MODE[r.mode] || r.mode || "")}${r.remote ? ` · Client-App, ${esc(lifeText(r.remote))}` : ""}</div></td>
      <td style="text-align:center;font-size:12px">${r.mode === "warp" ? "Zero Trust" : esc(ACC_SHORT[r.access] || r.access)}</td>
      <td style="text-align:center" title="${esc(r.state?.error || "")}">${r.state?.error ? "⚠️" : r.state?.synced ? "✅" : "…"}</td>
      ${actions ? `<td style="white-space:nowrap;text-align:right">
        ${r.mode === "client" || r.mode === "warp" ? `<button class="btn btn-sm" data-how="${esc(r.id)}">Verbinden</button>` : ""}
        ${r.remote ? "" : `<button class="btn-tool" data-edit="${esc(r.id)}" title="Bearbeiten">✏️</button>`}
        <button class="btn btn-sm btn-danger" data-del="${esc(r.id)}">Entfernen</button></td>` : ""}
    </tr>${r.state?.error ? `<tr><td colspan="${actions ? 6 : 5}" style="color:var(--danger);font-size:11.5px;padding-top:0">⚠ ${esc(r.state.error)}</td></tr>` : ""}`).join("")}</tbody></table>`;
}

// ------------------------------------------------------------------ Verbindung
function drawConnect(pane, S, redraw) {
  const a = S.auth || {};
  pane.innerHTML = `
    ${box(`<b>1. Cloudflare-Zugang</b>
      <p style="color:var(--subtext);font-size:12px;margin:4px 0 8px">Empfohlen: <b>API-Token</b> (Cloudflare → Mein Profil → API-Token → Token erstellen) mit den Rechten aus „Hilfe“.
        Alternativ der Global API Key mit der E-Mail des Kontos. Gespeichert wird verschlüsselt.</p>
      <div style="display:flex;gap:14px;margin-bottom:8px">
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer"><input type="radio" name="cfat" value="token" ${a.type !== "key" ? "checked" : ""} style="appearance:auto;width:15px;height:15px"> API-Token</label>
        <label style="display:flex;gap:6px;align-items:center;cursor:pointer"><input type="radio" name="cfat" value="key" ${a.type === "key" ? "checked" : ""} style="appearance:auto;width:15px;height:15px"> Global API Key</label>
      </div>
      <div data-tok><input type="password" data-token placeholder="${a.configured && a.type !== "key" ? "•••••••• (gespeichert – leer lassen zum Behalten)" : "API-Token"}" style="width:100%;max-width:520px"></div>
      <div data-key style="display:none;gap:8px;flex-wrap:wrap">
        <input type="email" data-email placeholder="E-Mail des Cloudflare-Kontos" value="${esc(a.email || "")}" style="width:260px">
        <input type="password" data-keyv placeholder="${a.configured && a.type === "key" ? "•••••••• (gespeichert)" : "Global API Key"}" style="width:260px">
      </div>
      <div style="margin-top:8px"><button class="btn-primary btn-sm" data-auth>Prüfen &amp; speichern</button>
        <span data-authmsg style="margin-left:8px;font-size:12px;color:var(--subtext)">${a.configured ? "✔ eingerichtet" : ""}</span></div>`)}
    ${box(`<b>2. Konto und öffentliche RMM-Adresse</b>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px">
        <select data-acc style="min-width:260px">${S.account.id ? `<option value="${esc(S.account.id)}">${esc(S.account.name)}</option>` : `<option value="">– erst Zugang prüfen –</option>`}</select>
        <input data-pub placeholder="https://rmm.firma.de" value="${esc(S.public_base || "")}" style="width:280px" title="Öffentliche Adresse des RMM (für den RMM-Login bei Cloudflare Access)">
        <button class="btn-primary btn-sm" data-accsave>Übernehmen</button>
        <button class="btn btn-sm" data-refresh ${S.account.id ? "" : "disabled"}>Zonen neu laden</button>
      </div>
      <p style="color:var(--subtext);font-size:12px;margin:8px 0 0">Die öffentliche RMM-Adresse brauchst du für den Schutz „RMM-Login“: Cloudflare schickt die Anmeldung dorthin
        (<code>/cf-oidc/…</code>). Am einfachsten legst du dafür eine Route „RMM-Dashboard“ an und trägst deren Adresse hier ein.</p>
      <div style="margin-top:8px;font-size:12px">Zonen: ${S.zones.length ? S.zones.map((z) => `<code>${esc(z.name)}</code>`).join(" ") : "–"}
        · Zero-Trust-Team: ${S.team_domain ? `<code>${esc(S.team_domain)}</code>` : `<span style="color:var(--warning, #f59e0b)">nicht eingerichtet – für Access/Login in Cloudflare „Zero Trust“ einmal öffnen</span>`}</div>`)}
    ${box(`<b>3. Fernzugriff der Client-App</b>
      <p style="color:var(--subtext);font-size:12px;margin:4px 0 8px">Öffnet jemand in der Client-App RDP, VNC oder SSH „über RMM“, legt das Modul dafür selbst eine Tunnel-Adresse
        <code>rmm-&lt;protokoll&gt;-&lt;agent&gt;.&lt;zone&gt;</code> an – geschützt mit einem eigenen Service-Token je Agent. Wie lange sie bleibt
        (bis Nutzungsende, Minuten, dauerhaft) wählt man beim Verbinden.</p>
      <div style="display:flex;gap:8px;align-items:center"><select data-rzone style="min-width:220px">
        ${S.zones.map((z) => `<option value="${esc(z.name)}" ${z.name === (S.remote?.zone || S.zones[0]?.name) ? "selected" : ""}>${esc(z.name)}</option>`).join("") || "<option value=''>– keine Zone –</option>"}
      </select><button class="btn btn-sm" data-rsave ${S.zones.length ? "" : "disabled"}>Speichern</button></div>`)}
    ${box(`<b>Alles entfernen</b>
      <p style="color:var(--subtext);font-size:12px;margin:4px 0 8px">Löscht alle Routen, DNS-Einträge, Access-Anwendungen, Logins, Tunnel (auch auf den Nodes) und den gespeicherten Zugang.</p>
      <button class="btn btn-sm btn-danger" data-reset ${a.configured ? "" : "disabled"}>Alles entfernen</button>`)}`;
  const kind = () => pane.querySelector('input[name="cfat"]:checked').value;
  const sw = () => {
    pane.querySelector("[data-tok]").style.display = kind() === "token" ? "" : "none";
    pane.querySelector("[data-key]").style.display = kind() === "key" ? "flex" : "none";
  };
  pane.querySelectorAll('input[name="cfat"]').forEach((r) => r.addEventListener("change", sw));
  sw();
  pane.querySelector("[data-auth]").addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true;
    const msg = pane.querySelector("[data-authmsg]");
    msg.textContent = "Prüfe …";
    try {
      const r = await cq("/auth", { method: "POST", body: { type: kind(), token: pane.querySelector("[data-token]").value.trim(),
        key: pane.querySelector("[data-keyv]").value.trim(), email: pane.querySelector("[data-email]").value.trim() } });
      msg.textContent = `✔ ${r.kind} gültig – ${r.accounts.length} Konto/Konten`;
      const sel = pane.querySelector("[data-acc]");
      sel.innerHTML = r.accounts.map((x) => `<option value="${esc(x.id)}" ${x.id === S.account.id ? "selected" : ""}>${esc(x.name)}</option>`).join("");
    } catch (err) { msg.textContent = "✖ " + err.message; }
    b.disabled = false;
  });
  pane.querySelector("[data-accsave]").addEventListener("click", async () => {
    try {
      const r = await cq("/account", { method: "POST", body: { account_id: pane.querySelector("[data-acc]").value, public_base: pane.querySelector("[data-pub]").value.trim() } });
      window.notify?.(`Konto übernommen – ${r.zones.length} Zone(n)`, "success");
      redraw();
    } catch (e) { window.notify?.(e.message, "error", 7000); }
  });
  pane.querySelector("[data-rsave]").addEventListener("click", async () => {
    try { await cq("/remote", { method: "POST", body: { zone: pane.querySelector("[data-rzone]").value } }); window.notify?.("Gespeichert", "success"); redraw(); }
    catch (e) { window.notify?.(e.message, "error"); }
  });
  pane.querySelector("[data-refresh]").addEventListener("click", async () => {
    try { await cq("/refresh", { method: "POST" }); redraw(); } catch (e) { window.notify?.(e.message, "error"); }
  });
  pane.querySelector("[data-reset]").addEventListener("click", async () => {
    if (!(await uiConfirm("Wirklich alles entfernen?", { description: "Alle Routen, Tunnel und Einträge in Cloudflare werden gelöscht.", okText: "Alles entfernen", danger: true }))) return;
    try { const r = await cq("/all", { method: "DELETE" }); window.notify?.(r.errors?.length ? r.errors.join("; ") : "Alles entfernt", r.errors?.length ? "warning" : "success"); redraw(); }
    catch (e) { window.notify?.(e.message, "error"); }
  });
}

// ------------------------------------------------------------------ Routen
function drawRoutes(pane, S, redraw) {
  const ready = S.auth.configured && S.account.id;
  const cat = S.catalog || {};
  const used = new Set(S.routes.map((r) => r.kind + ":" + JSON.stringify(r.ref || {})));
  const sugg = [
    ...(cat.services || []).map((s) => ({ label: s.label, hint: s.hint, preset: { kind: s.kind, name: s.label, ref: {} } })),
    ...(cat.websites || []).map((w) => ({ label: `Website ${w.name}`, preset: { kind: "website", name: `Website ${w.title}`, ref: { site: w.name } } })),
    ...(cat.docker || []).map((d) => ({ label: `${d.stack_name} / ${d.service}:${d.port}`, hint: d.mode === "path" ? `rmm/${d.name}/` : `Host-Port ${d.public_port}`,
                                        preset: { kind: "docker", name: `${d.stack_name} ${d.service}`, ref: { stack: d.stack, pub: d.pub } } })),
  ].filter((s) => !used.has(s.preset.kind + ":" + JSON.stringify(s.preset.ref)));
  pane.innerHTML = `
    ${!ready ? `<div style="color:var(--subtext);margin-bottom:10px">Zuerst unter „Verbindung“ den Zugang und das Konto einrichten.</div>` : ""}
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
      <button class="btn-primary btn-sm" data-add ${ready ? "" : "disabled"}>＋ Route</button>
      <button class="btn btn-sm" data-sync ${ready ? "" : "disabled"}>⟳ Abgleichen</button>
      <span style="color:var(--subtext);font-size:12px">Die Ziele werden bei jedem Abgleich aus den aktuell genutzten Ports bestimmt.</span>
    </div>
    ${sugg.length && ready ? `<div style="margin-bottom:10px"><div style="font-size:12px;color:var(--subtext);margin-bottom:4px">Genutzte Dienste – antippen zum Freigeben:</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px">${sugg.map((s, i) => `<button class="btn btn-sm" data-sugg="${i}" title="${esc(s.hint || "")}">＋ ${esc(s.label)}</button>`).join("")}
      <button class="btn btn-sm" data-sugg="agent">＋ Agent (RDP/SSH/VNC)</button></div></div>` : ""}
    ${S.routes.length ? routeTable(S.routes, true) : `<div style="color:var(--subtext)">Noch keine Routen.</div>`}`;
  pane.querySelector("[data-add]")?.addEventListener("click", () => routeDialog(S, null, redraw));
  pane.querySelector("[data-sync]")?.addEventListener("click", (e) => doSync(e.currentTarget, redraw));
  pane.querySelectorAll("[data-sugg]").forEach((b) => b.addEventListener("click", () =>
    routeDialog(S, b.dataset.sugg === "agent" ? { kind: "agent", name: "", ref: {} } : sugg[+b.dataset.sugg].preset, redraw)));
  pane.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => routeDialog(S, S.routes.find((r) => r.id === b.dataset.edit), redraw)));
  pane.querySelectorAll("[data-how]").forEach((b) => b.addEventListener("click", () => howTo(S.routes.find((r) => r.id === b.dataset.how))));
  pane.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    const r = S.routes.find((x) => x.id === b.dataset.del);
    if (!(await uiConfirm(`„${r.name}“ entfernen?`, { description: "DNS-Eintrag, Access-Anwendung, Login und Weiterleitung werden gelöscht.", okText: "Entfernen", danger: true }))) return;
    try { await cq(`/routes/${r.id}`, { method: "DELETE" }); window.notify?.("Route entfernt", "success"); redraw(); }
    catch (e) { window.notify?.(e.message, "error"); }
  }));
}

function protoOf(kind, ref) {
  if (["dashboard", "webdav", "website", "docker"].includes(kind)) return "http";
  if (kind === "sftp") return "tcp";
  if (kind === "ftp") return "ftp";
  if (kind === "console_ssh") return "ssh";
  if (kind === "agent") return ref.proto || "rdp";
  const u = (ref.url || "").toLowerCase();
  return ["ssh", "rdp", "tcp"].find((p) => u.startsWith(p + "://")) || "http";
}

async function routeDialog(S, r, redraw) {
  const ed = r?.id ? r : null;
  r = { name: "", kind: "dashboard", ref: {}, endpoint: "server", node_id: "", mode: "", hostname: "", access: "none", allow: [], emails: [], ...(r || {}) };
  r.ref = { ...(r.ref || {}) };
  let dir = { users: [], groups: [] };
  try { dir = await cq("/directory"); } catch { /* ohne Auswahl */ }
  const zones = S.zones || [];
  const zoneOf = (h) => zones.find((z) => h === z.name || h.endsWith("." + z.name));
  const z0 = zoneOf(r.hostname || "") || zones[0];
  const sub0 = r.hostname && z0 ? (r.hostname === z0.name ? "" : r.hostname.slice(0, -(z0.name.length + 1))) : "";
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:12px";
  const cat = S.catalog || {};
  const clients = [...(state.clients || [])].sort((a, b) => (a.hostname || "").localeCompare(b.hostname || ""));
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;width:620px;max-width:100%;max-height:calc(100dvh - 24px);overflow:auto;font-size:13px">
    <b style="font-size:15px">${ed ? "Route bearbeiten" : "Neue Route"}</b>
    <div style="display:grid;grid-template-columns:150px 1fr;gap:8px 10px;align-items:center;margin-top:12px">
      <label>Name</label><input data-f="name" value="${esc(r.name)}" placeholder="z. B. Dashboard">
      <label>Was?</label><select data-f="kind">${Object.entries(KIND).map(([k, l]) => `<option value="${k}" ${k === r.kind ? "selected" : ""}>${l}</option>`).join("")}</select>
      <label data-row="site">Website</label><select data-row="site" data-ref="site">${(cat.websites || []).map((w) => `<option value="${esc(w.name)}" ${w.name === r.ref.site ? "selected" : ""}>${esc(w.title)} (/${esc(w.name)}/)</option>`).join("") || "<option value=''>– keine Websites –</option>"}</select>
      <label data-row="docker">Container</label><select data-row="docker" data-ref="dockerpub">${(cat.docker || []).map((d) => `<option value="${esc(d.stack + "|" + d.pub)}" ${d.stack === r.ref.stack && d.pub === r.ref.pub ? "selected" : ""}>${esc(d.stack_name)} / ${esc(d.service)}:${d.port} (${d.mode === "path" ? "rmm/" + esc(d.name) + "/" : "Port " + d.public_port})</option>`).join("") || "<option value=''>– keine Veröffentlichungen (Modul Docker) –</option>"}</select>
      <label data-row="agent">Agent</label><select data-row="agent" data-ref="client_id">${clients.map((c) => `<option value="${esc(c.id)}" ${c.id === r.ref.client_id ? "selected" : ""}>${esc(c.hostname)}${c.ip ? " – " + esc(c.ip) : ""}</option>`).join("")}</select>
      <label data-row="agent">Protokoll / Port</label><div data-row="agent" style="display:flex;gap:6px">
        <select data-ref="proto">${["rdp", "ssh", "vnc"].map((p) => `<option value="${p}" ${p === (r.ref.proto || "rdp") ? "selected" : ""}>${p.toUpperCase()}</option>`).join("")}</select>
        <input data-ref="port" type="number" min="1" max="65535" value="${esc(r.ref.port || "")}" placeholder="Standard" style="width:110px">
        <select data-ref="host_mode"><option value="local">Gerät selbst</option><option value="custom" ${r.ref.host_mode === "custom" ? "selected" : ""}>andere Adresse</option></select>
        <input data-ref="host" value="${esc(r.ref.host || "")}" placeholder="IP/Name" style="width:120px"></div>
      <label data-row="agent">Tunnel endet</label><div data-row="agent" style="display:flex;gap:6px;flex-wrap:wrap">
        <select data-f="endpoint"><option value="server">am RMM-Server (Gateway, Modul Netzwerk)</option><option value="node" ${r.endpoint === "node" ? "selected" : ""}>auf einer Node im Netz des Agents</option></select>
        <select data-f="node_id" data-nodesel>${(S.nodes || []).map((n) => `<option value="${esc(n.client_id)}" ${n.client_id === r.node_id ? "selected" : ""}>${esc(clientName(n.client_id))}${n.online ? "" : " (offline)"}</option>`).join("") || "<option value=''>– keine Node –</option>"}</select></div>
      <label data-row="custom">Adresse</label><input data-row="custom" data-ref="url" value="${esc(r.ref.url || "")}" placeholder="http://10.0.0.5:8080 · tcp://host:port · ssh://host:22 · rdp://host:3389">
      <label>Erreichbar über</label><select data-f="mode"></select>
      <label data-row="host">Adresse</label><div data-row="host" style="display:flex;gap:4px;align-items:center">
        <input data-sub value="${esc(sub0)}" placeholder="z. B. rmm" style="width:160px"><span>.</span>
        <select data-zone>${zones.map((z) => `<option value="${esc(z.name)}" ${z0 && z.name === z0.name ? "selected" : ""}>${esc(z.name)}</option>`).join("")}</select></div>
      <label data-row="acc">Schutz</label><select data-row="acc" data-f="access">${Object.entries(ACCESS).map(([k, l]) => `<option value="${k}" ${k === r.access ? "selected" : ""}>${l}</option>`).join("")}</select>
      <label data-row="allow" style="align-self:start">Erlaubt</label><div data-row="allow"><div style="font-size:11.5px;color:var(--subtext);margin-bottom:4px">Nichts gewählt = jedes RMM-Konto.</div><div data-pick>${subjectPickerHtml(dir, r.allow || [], { name: "cfal" })}</div></div>
      <label data-row="otp" style="align-self:start">E-Mail-Adressen</label><textarea data-row="otp" data-f="emails" rows="3" placeholder="eine pro Zeile">${esc((r.emails || []).join("\n"))}</textarea>
    </div>
    <div data-note style="font-size:12px;color:var(--subtext);margin-top:10px"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>${ed ? "Speichern" : "Anlegen"}</button></div></div>`;
  document.body.appendChild(ov);
  initSubjectPicker(ov.querySelector("[data-pick]"));
  const $ = (s) => ov.querySelector(s);
  const ref = () => {
    const o = {};
    const kind = $('[data-f="kind"]').value;
    if (kind === "website") o.site = $('[data-ref="site"]').value;
    if (kind === "docker") { const [s, p] = $('[data-ref="dockerpub"]').value.split("|"); o.stack = s; o.pub = p; }
    if (kind === "agent") {
      o.client_id = $('[data-ref="client_id"]').value; o.proto = $('[data-ref="proto"]').value;
      const p = parseInt($('[data-ref="port"]').value, 10); if (p) o.port = p;
      o.host_mode = $('[data-ref="host_mode"]').value; if (o.host_mode === "custom") o.host = $('[data-ref="host"]').value.trim();
    }
    if (kind === "custom") o.url = $('[data-ref="url"]').value.trim();
    return o;
  };
  const sync = () => {
    const kind = $('[data-f="kind"]').value;
    const proto = protoOf(kind, ref());
    const modes = S.modes[proto] || ["http"];
    const ms = $('[data-f="mode"]');
    const keep = ms.value || r.mode;
    ms.innerHTML = modes.map((m) => `<option value="${m}" ${m === keep ? "selected" : ""}>${MODE[m]}</option>`).join("");
    const mode = ms.value;
    const show = (key, on) => ov.querySelectorAll(`[data-row="${key}"]`).forEach((el) => { el.style.display = on ? "" : "none"; });
    show("site", kind === "website"); show("docker", kind === "docker"); show("agent", kind === "agent"); show("custom", kind === "custom");
    show("host", mode !== "warp"); show("acc", mode !== "warp");
    const acc = $('[data-f="access"]');
    acc.querySelector('option[value="none"]').disabled = mode === "browser";
    if (mode === "browser" && acc.value === "none") acc.value = "rmm";
    show("allow", mode !== "warp" && acc.value === "rmm"); show("otp", mode !== "warp" && acc.value === "otp");
    $("[data-nodesel]").style.display = $('[data-f="endpoint"]').value === "node" ? "" : "none";
    $('[data-ref="host"]').style.display = $('[data-ref="host_mode"]').value === "custom" ? "" : "none";
    const notes = {
      browser: "Cloudflare zeigt das Terminal bzw. den Bildschirm direkt im Browser – nach der Anmeldung.",
      client: proto === "rdp" ? "Am PC: cloudflared access rdp … und dann Remotedesktop auf localhost (Anleitung unter „Verbinden“)."
        : "Am PC wird cloudflared gebraucht (Anleitung unter „Verbinden“ in der Routenliste).",
      warp: "Keine öffentliche Adresse: erreichbar für Geräte mit WARP-Client im Zero-Trust-Team (privates Netz).",
      http: kind === "website" ? "Die Website ist dann unter https://<Adresse>/<name>/ erreichbar." : "",
    };
    $("[data-note]").textContent = (kind === "ftp" ? "FTP öffnet für jede Übertragung eine neue Verbindung und geht daher nur über WARP. Besser: SFTP oder WebDAV. " : "")
      + (notes[mode] || "") + (acc.value === "rmm" && mode !== "warp" && !S.public_base ? " ⚠ Für den RMM-Login fehlt noch die öffentliche RMM-Adresse (Verbindung)." : "");
    if (!$('[data-f="name"]').dataset.touched && !ed) {
      const nm = kind === "agent" ? `${clientName($('[data-ref="client_id"]').value)} ${($('[data-ref="proto"]').value || "").toUpperCase()}` : $('[data-f="name"]').value || KIND[kind];
      $('[data-f="name"]').value = nm;
    }
  };
  $('[data-f="name"]').addEventListener("input", (e) => { e.target.dataset.touched = "1"; });
  ov.querySelectorAll("select, input").forEach((el) => el.addEventListener("change", sync));
  sync();
  $("[data-x]").addEventListener("click", () => ov.remove());
  $("[data-ok]").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    const mode = $('[data-f="mode"]').value;
    const sub = $("[data-sub]").value.trim().toLowerCase().replace(/\.$/, "");
    const zone = $("[data-zone]").value;
    const body = {
      id: ed?.id || "", name: $('[data-f="name"]').value.trim(), kind: $('[data-f="kind"]').value, ref: ref(),
      endpoint: $('[data-f="endpoint"]').value, node_id: $('[data-f="node_id"]').value || "", mode,
      hostname: mode === "warp" ? "" : (sub ? `${sub}.${zone}` : zone), access: mode === "warp" ? "none" : $('[data-f="access"]').value,
      allow: readSubjectPicker(ov.querySelector("[data-pick]")),
      emails: $('[data-f="emails"]').value.split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean),
    };
    b.disabled = true; b.textContent = "⏳ Richte ein …";
    try {
      const res = await cq("/routes", { method: "POST", body });
      ov.remove();
      const err = res.route?.state?.error;
      window.notify?.(err ? `Gespeichert, aber: ${err}` : "Route eingerichtet", err ? "warning" : "success", 8000);
      redraw();
    } catch (err) { window.notify?.(err.message, "error", 8000); b.disabled = false; b.textContent = ed ? "Speichern" : "Anlegen"; }
  });
}

function howTo(r) {
  const h = r.hostname;
  const proto = r.proto;
  let html;
  if (r.mode === "warp") {
    html = `<p>Der Dienst hat keine öffentliche Adresse. Erreichbar ist er für Geräte mit dem <b>Cloudflare WARP-Client</b>, die im Zero-Trust-Team angemeldet sind
      (Geräteregeln in Cloudflare Zero Trust → Einstellungen → WARP-Client; „Split Tunnels“ müssen das Netz einschließen).</p>
      <p>Adresse: <code>${esc(r.state?.warp_address || "…")}</code>${proto === "rdp" ? " – im Remotedesktop eintragen" : proto === "ftp" ? " – im FTP-Programm (passiv) eintragen" : ""}</p>`;
  } else {
    const cmds = proto === "ssh"
      ? [`ssh -o ProxyCommand="cloudflared access ssh --hostname %h" BENUTZER@${h}`,
         `# oder dauerhaft in ~/.ssh/config:\nHost ${h}\n  ProxyCommand cloudflared access ssh --hostname %h`]
      : proto === "rdp"
        ? [`cloudflared access rdp --hostname ${h} --url rdp://localhost:13389`, "# danach Remotedesktop (mstsc) mit localhost:13389 verbinden"]
        : [`cloudflared access tcp --hostname ${h} --url localhost:${proto === "vnc" ? 15900 : 12022}`,
           proto === "vnc" ? "# danach VNC-Viewer mit localhost:15900 verbinden" : "# danach z. B. SFTP-Programm mit localhost:12022 verbinden"];
    html = `<p>Auf dem PC wird <b>cloudflared</b> gebraucht (<a href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/" target="_blank" rel="noopener" style="color:var(--accent)">Download</a>).
      ${r.access !== "none" ? "Beim ersten Verbinden öffnet sich der Browser für die Anmeldung." : ""}</p>
      ${cmds.map((c) => `<pre style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px;font-size:12px;white-space:pre-wrap;user-select:all">${esc(c)}</pre>`).join("")}`;
  }
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;width:560px;max-width:100%;font-size:13px">
    <b style="font-size:15px">Verbinden: ${esc(r.name)}</b>${html}
    <div style="text-align:right;margin-top:10px"><button class="btn" data-x>Schließen</button></div></div>`;
  document.body.appendChild(ov);
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
}

// ------------------------------------------------------------------ Nodes
function drawNodes(pane, S, redraw) {
  const nt = S.tunnels.nodes || {};
  const nodes = S.nodes || [];
  pane.innerHTML = `<p style="color:var(--subtext);font-size:12px;margin-top:0">Für Routen „Agent: RDP/SSH/VNC“ mit <b>Tunnel endet auf einer Node</b> bekommt jede Node einen eigenen Tunnel.
      Das Modul verteilt den Connector (Container <code>rmm-cloudflared</code>, Docker auf der Node nötig) selbst – er erreicht die Geräte im Netz der Node direkt.</p>
    ${Object.keys(nt).length ? `<table class="data-table" style="width:100%"><thead><tr><th style="text-align:left">Node</th><th style="text-align:left">Tunnel</th><th>Routen</th><th>Connector</th><th></th></tr></thead><tbody>
      ${Object.entries(nt).map(([cid, t]) => {
        const n = nodes.find((x) => x.client_id === cid);
        const cnt = S.routes.filter((r) => r.endpoint === "node" && r.node_id === cid).length;
        return `<tr><td><b>${esc(clientName(cid))}</b> ${n?.online ? "🟢" : "⚪"}</td><td><code>${esc(t.name || "")}</code></td><td style="text-align:center">${cnt}</td>
          <td style="text-align:center" title="${esc(t.error || "")}">${t.deployed ? "✅ verteilt" : t.error ? "⚠️ " + esc(t.error) : "…"}</td>
          <td style="text-align:right"><button class="btn btn-sm btn-danger" data-rmnode="${esc(cid)}" ${cnt ? "disabled title='Zuerst die Routen entfernen'" : ""}>Entfernen</button></td></tr>`;
      }).join("")}</tbody></table>` : `<div style="color:var(--subtext)">Noch keine Node-Tunnel.</div>`}
    <div style="margin-top:12px;font-size:12px;color:var(--subtext)">Nodes im RMM: ${nodes.length ? nodes.map((n) => `${esc(clientName(n.client_id))}${n.online ? "" : " (offline)"}`).join(", ") : "keine (Agent → „Node verwalten“)"}</div>`;
  pane.querySelectorAll("[data-rmnode]").forEach((b) => b.addEventListener("click", async () => {
    if (!(await uiConfirm("Node-Tunnel entfernen?", { okText: "Entfernen", danger: true }))) return;
    try { await cq(`/nodes/${b.dataset.rmnode}`, { method: "DELETE" }); redraw(); } catch (e) { window.notify?.(e.message, "error"); }
  }));
}

// ------------------------------------------------------------------ Hilfe
function drawHelp(pane) {
  pane.innerHTML = `<div style="max-width:760px;line-height:1.6">
    <h3 style="margin-top:0">API-Token anlegen</h3>
    <p>Cloudflare → Mein Profil → API-Token → <i>Token erstellen</i> → <i>Benutzerdefiniertes Token</i> mit diesen Rechten:</p>
    <ul style="margin-top:0">
      <li>Konto → <b>Cloudflare Tunnel</b>: Bearbeiten</li>
      <li>Konto → <b>Access: Apps und Richtlinien</b>: Bearbeiten</li>
      <li>Konto → <b>Access: Organisationen, Identitätsanbieter und Gruppen</b>: Bearbeiten</li>
      <li>Konto → <b>Zero Trust</b>: Bearbeiten (private Netze / WARP)</li>
      <li>Zone → <b>DNS</b>: Bearbeiten · Zone → <b>Zone</b>: Lesen (für alle Zonen, die du nutzen willst)</li>
    </ul>
    <h3>Erreichbar über</h3>
    <ul style="margin-top:0">
      <li><b>Im Browser</b> – Dashboard, WebDAV, Websites, Container: normale https-Adresse.</li>
      <li><b>Im Browser über Cloudflare Access</b> – SSH als Web-Terminal, VNC als Web-Bildschirm (immer mit Login).</li>
      <li><b>Mit cloudflared auf dem PC</b> – SSH, SFTP, RDP, VNC über einen lokalen Port (Anleitung je Route unter „Verbinden“).</li>
      <li><b>Privates Netz (WARP)</b> – keine öffentliche Adresse; Geräte mit WARP-Client im Zero-Trust-Team erreichen die IP direkt. Einziger Weg für FTP.</li>
    </ul>
    <h3>Schutz</h3>
    <ul style="margin-top:0">
      <li><b>RMM-Login</b> – Cloudflare Access fragt vor dem Zugriff die RMM-Anmeldung ab (lokale Konten oder AD/LDAP, inkl. 2FA). Pro Route lassen sich Benutzer/Gruppen einschränken.
        Voraussetzung: öffentliche RMM-Adresse (Verbindung) und ein eingerichtetes Zero-Trust-Team. Die Login-Adressen <code>/cf-oidc/…</code> nimmt das Modul automatisch vom Schutz aus.</li>
      <li><b>Einmal-PIN per E-Mail</b> – Cloudflare schickt einen Code an die eingetragenen Adressen.</li>
      <li><b>Offen</b> – nur die eigene Anmeldung des Dienstes (z. B. RMM-Login im Dashboard).</li>
    </ul>
    <h3>Agents: wo endet der Tunnel?</h3>
    <ul style="margin-top:0">
      <li><b>Am RMM-Server</b>: das Modul legt eine dauerhafte Weiterleitung (Modul „Netzwerk“) zum Agent an – der Agent muss online sein.</li>
      <li><b>Auf einer Node</b>: cloudflared läuft auf der Node und verbindet direkt zum Gerät im lokalen Netz (auch Geräte ohne Agent über „andere Adresse“).</li>
    </ul></div>`;
}

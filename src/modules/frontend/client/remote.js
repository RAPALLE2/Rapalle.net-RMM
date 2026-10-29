// remote.js (Modul "client") - RDP / VNC / SSH / Terminal aus dem Dashboard extern öffnen
// ----------------------------------------------------------------------------------------
// Läuft das Dashboard im Fenster der Client-App (ab 1.4: "#client-app=<version>" bzw.
// Android-Brücke window.RMM.launch), übernimmt dieses Modul die Panel-Knöpfe
//   "Terminal"       -> SSH-Client ODER RMM-Terminal im eigenen App-Fenster
//   "Remote-Bildschirm" -> Remotedesktop (RDP) ODER VNC-Viewer
// und lässt die App die passende Software lokal starten (stabile, sichere Verbindung).
// Im normalen Browser bleibt alles wie es ist (Web-Variante).
//
// Ziel: lokale IP + Standard-Port | eigene Adresse/Port | über RMM (Cloudflare-Tunnel ->
// Backend -> Agent; Lebensdauer: bis Nutzungsende / Minuten / dauerhaft) | RMM-Gateway.
// Ablauf: POST /api/client/launch -> Startschein -> rapalle-rmm://launch?s=…&t=…
//         (die App löst den Schein mit IHRER Anmeldung ein: fremde Seiten können nichts starten)
// Profil: "Standardmäßig extern / im Browser / jedes Mal fragen" (GET/PUT /api/client/prefs).

import { getToken } from "/js/app/api.js";
import { esc, uiConfirm } from "/js/app/utils.js";

const PROTO = {
  rdp: { label: "Remotedesktop (RDP)", port: 3389 },
  vnc: { label: "VNC", port: 5900 },
  ssh: { label: "SSH-Client", port: 22 },
  shell: { label: "RMM-Terminal im eigenen Fenster" },
};
const LIFE = [["session", "Bis Nutzungsende"], ["10", "10 Minuten"], ["30", "30 Minuten"], ["60", "1 Stunde"],
              ["120", "2 Stunden"], ["0", "Dauerhaft"]];
let _mode = null;          // web | external | ask (Profil)

async function creq(path, opts = {}) {
  const headers = { Authorization: "Bearer " + (getToken() || "") };
  if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; opts = { ...opts, body: JSON.stringify(opts.body) }; }
  const res = await fetch("/api/client" + path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* egal */ }
    throw new Error(msg);
  }
  return res.json();
}

// ------------------------------------------------------------------ Umgebung
export function clientAppKind() {
  if (window.RMM && typeof window.RMM.launch === "function") return "android";
  try { if (localStorage.getItem("rmm_client_app")) return "desktop"; } catch { /* egal */ }
  return null;
}

// Im NORMALEN Browser: Ist die PC-App auf diesem Rechner installiert? (Merker nach dem ersten
// erfolgreichen Start bzw. per Haken im Profil.) Dann oeffnen auch hier RDP/SSH/VNC direkt die App –
// der Link rapalle-rmm:// ist systemweit registriert.
export function hasPcApp() {
  try { return localStorage.getItem("rmm_has_pc_app") === "1"; } catch { return false; }
}
export function setPcApp(on) {
  try { on ? localStorage.setItem("rmm_has_pc_app", "1") : localStorage.removeItem("rmm_has_pc_app"); } catch { /* egal */ }
}
const isMobile = () => /Android|iPhone|iPad/i.test(navigator.userAgent);

export async function loadOpenMode() {
  try { _mode = (await creq("/prefs")).open_mode; } catch { _mode = _mode || "external"; }
  return _mode;
}

// ------------------------------------------------------------------ Panel-Übernahme
// Rückgabe true = erledigt (kein Browser-Fenster). Umschalt-/Alt-Klick öffnet immer die Auswahl.
export function actionHandler(action, client, ev) {
  if (action !== "terminal" && action !== "vnc") return false;
  if (!clientAppKind() && !hasPcApp()) return false;
  if (_mode === null) loadOpenMode();
  const mode = _mode || "external";
  const force = ev && (ev.shiftKey || ev.altKey);
  if (mode === "web" && !force) return false;
  openExternal(action, client, { ask: mode === "ask" || force });
  return true;
}

const kindKey = (cid, action) => `rmm_ext_kind:${cid}:${action}`;

export async function openInPcApp(client) {
  // eigener Panel-Knopf: Auswahl RDP/VNC bzw. SSH immer anzeigen
  dialog("all", client, null);
}

async function openExternal(action, client, { ask }) {
  const remembered = (() => { try { return localStorage.getItem(kindKey(client.id, action)); } catch { return null; } })();
  if (!ask && remembered === "shell") return launch(client, { protocol: "shell" });
  if (!ask && remembered && remembered !== "shell") {
    try {
      const info = await creq(`/targets/${encodeURIComponent(client.id)}?protocol=${remembered}`);
      if (info.has_saved_default) {
        window.notify?.(`${PROTO[remembered].label} für ${client.hostname} wird geöffnet … (Ziel ändern: Umschalt+Klick)`, "info", 4500);
        return launch(client, { protocol: remembered, ...info.default });
      }
    } catch { /* -> Auswahl */ }
  }
  dialog(action, client, remembered);
}

// rapalle-rmm://-Link oeffnen. Im normalen Browser pruefen, ob die App wirklich startete
// (das Fenster verliert dann den Fokus); sonst Hinweis mit Download.
export function openAppLink(url, ticket) {
  if (clientAppKind() === "android") { window.RMM.launch(ticket); return; }
  let left = false;
  const onBlur = () => { left = true; };
  window.addEventListener("blur", onBlur, { once: true });
  const a = document.createElement("a");
  a.href = url;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => a.remove(), 1000);
  if (clientAppKind()) return;                        // im App-Fenster selbst: kein Test noetig
  setTimeout(() => {
    window.removeEventListener("blur", onBlur);
    if (left || document.hidden) { setPcApp(true); return; }
    window.notify?.("Die PC-App hat sich nicht gemeldet. Ist sie installiert und angemeldet? "
      + "Download: Profil → Client-App & Geräte. (Beim ersten Mal fragt der Browser, ob er den Link öffnen darf.)", "warn", 15000);
  }, 2500);
}

async function launch(client, params, remember = false) {
  const res = await creq("/launch", { method: "POST", body: { client_id: client.id, remember, ...params } });
  openAppLink(res.url, res.ticket);
}

// VPN: eigenen Fernzugriffs-Tunnel (Benutzer + Agent) holen; die App richtet ihn vor dem Verbinden ein
async function vpnQuick(client) {
  const res = await fetch("/api/vpn/quick", { method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + (getToken() || "") },
    body: JSON.stringify({ client_id: client.id }) });
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(d.detail || `VPN: Fehler ${res.status}`);
  return d;
}
const isPrivate = (ip) => /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(ip || "");

// ------------------------------------------------------------------ Auswahl
async function dialog(action, client, remembered) {
  let vpnOn = false;
  try { vpnOn = (await import("/js/app/modules.js")).isModuleOn("vpn"); } catch { /* egal */ }
  const kinds = action === "terminal" ? ["ssh", "shell"] : action === "all" ? ["rdp", "vnc", "ssh", "shell"] : ["rdp", "vnc"];
  let kind = kinds.includes(remembered) ? remembered : kinds[0];
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;width:500px;max-width:100%;max-height:calc(100dvh - 24px);overflow:auto;font-size:13px">
    <b style="font-size:15px">${action === "terminal" ? "Terminal" : action === "all" ? "In PC-App öffnen" : "Fernbild"} – ${esc(client.hostname)}</b>
    <div style="color:var(--subtext);font-size:12px;margin:2px 0 10px">Wird mit der Client-App lokal geöffnet.</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px" data-kinds>
      ${kinds.map((k) => `<button class="btn btn-sm" data-kind="${k}">${PROTO[k].label}</button>`).join("")}</div>
    <div data-body></div>
    <label style="display:flex;gap:8px;align-items:center;margin-top:10px;cursor:pointer;font-size:12.5px;color:var(--subtext)">
      <input type="checkbox" data-remember> Für dieses Gerät merken und künftig direkt öffnen</label>
    <div style="display:flex;gap:8px;justify-content:space-between;align-items:center;margin-top:14px;flex-wrap:wrap">
      <button class="btn btn-sm" data-web>Im Browser öffnen</button>
      <span><button class="btn" data-x>Abbrechen</button> <button class="btn-primary" data-ok>Öffnen</button></span>
    </div></div>`;
  document.body.appendChild(ov);
  const $ = (s) => ov.querySelector(s);
  const close = () => ov.remove();
  $("[data-x]").addEventListener("click", close);
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  $("[data-web]").addEventListener("click", async () => {
    close();
    const { handleAction } = await import("/js/app/panel.js");
    handleAction(action === "all" ? (kind === "ssh" || kind === "shell" ? "terminal" : "vnc") : action, client, null, { forceWeb: true });
  });
  let info = null;
  const drawBody = async () => {
    ov.querySelectorAll("[data-kind]").forEach((b) => b.classList.toggle("btn-primary", b.dataset.kind === kind));
    const body = $("[data-body]");
    if (kind === "shell") {
      info = null;
      body.innerHTML = `<div style="color:var(--subtext);font-size:12.5px">Öffnet das RMM-Terminal (über den Agent, ohne offenen Port) in einem eigenen App-Fenster.</div>`;
      return;
    }
    body.innerHTML = `<div style="color:var(--subtext)">Lade …</div>`;
    try { info = await creq(`/targets/${encodeURIComponent(client.id)}?protocol=${kind}`); }
    catch (e) { body.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    const d = info.default || {};
    const std = PROTO[kind].port;
    const opt = (v, label, extra = "", dis = false, title = "") => `<label style="display:flex;gap:8px;align-items:center;margin:4px 0;cursor:${dis ? "not-allowed" : "pointer"};opacity:${dis ? 0.55 : 1}" title="${esc(title)}">
        <input type="radio" name="tgt" value="${v}" ${d.host_mode === v ? "checked" : ""} ${dis ? "disabled" : ""} style="appearance:auto;width:15px;height:15px">${label}${extra}</label>`;
    body.innerHTML = `
      <div style="font-weight:600;font-size:12.5px;margin-bottom:2px">Ziel</div>
      ${opt("local", `Lokale IP <code>${esc(info.local_ip || "–")}</code>`, "", !info.local_ip, info.local_ip ? "" : "Agent hat keine lokale IP gemeldet")}
      ${opt("custom", "Eigene Adresse", ` <input data-host value="${esc(d.host_mode === "custom" ? d.host : "")}" placeholder="IP oder Name" style="width:170px;margin-left:6px">`)}
      ${opt("tunnel", "Über RMM (Cloudflare-Tunnel)", "", !info.tunnel?.ok || clientAppKind() === "android",
            clientAppKind() === "android" ? "Nur in der Desktop-App" : info.tunnel?.reason || "")}
      ${info.gateway_available ? opt("gateway", "RMM-Gateway (Port am Server, nur für diese IP)") : ""}
      ${vpnOn ? opt("vpn", "Über VPN (WireGuard-Tunnel zu diesem Gerät)", isPrivate(info.local_ip) ? ` <span style="font-size:11px;color:#3ecf8e">empfohlen – Gerät ist nicht öffentlich erreichbar</span>` : "",
                  clientAppKind() === "android", "Auf Android die VPN-Datei per QR-Code in die WireGuard-App übernehmen") : ""}
      ${!info.tunnel?.ok ? `<div style="font-size:11.5px;color:var(--subtext);margin:0 0 4px 23px">Tunnel: ${esc(info.tunnel?.reason || "")}</div>` : ""}
      <div data-portrow style="display:flex;gap:8px;align-items:center;margin-top:8px">
        <span style="font-weight:600;font-size:12.5px;width:70px">Port</span>
        <select data-pm><option value="standard">Standard (${std})</option><option value="custom" ${d.port_mode === "custom" ? "selected" : ""}>Eigener</option></select>
        <input data-port type="number" min="1" max="65535" value="${esc(d.port || "")}" style="width:100px"></div>
      <div data-liferow style="display:flex;gap:8px;align-items:center;margin-top:8px">
        <span style="font-weight:600;font-size:12.5px;width:70px">Offen</span>
        <select data-life>${LIFE.filter(([v]) => v !== "0" || info.may_unlimited).map(([v, l]) => `<option value="${v}" ${String(d.lifetime || "session") === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      ${kind === "ssh" ? `<div style="display:flex;gap:8px;align-items:center;margin-top:8px">
        <span style="font-weight:600;font-size:12.5px;width:70px">Benutzer</span>
        <input data-user value="${esc(d.user || "")}" placeholder="z. B. root oder administrator" style="width:200px"></div>
        <div style="font-size:11.5px;color:var(--subtext);margin-top:4px">Auf dem Gerät muss ein SSH-Server laufen.</div>` : ""}
      ${(info.tunnels || []).length ? `<div style="margin-top:10px;font-size:12px"><b>Offene Tunnel zu diesem Gerät</b>
        ${info.tunnels.map((t) => `<div style="display:flex;gap:8px;align-items:center;margin-top:4px"><code>${esc(t.proto?.toUpperCase())}</code>
          <span style="flex:1;color:var(--subtext)">${t.lifetime === 0 ? "dauerhaft" : t.lifetime === "session" ? "bis Nutzungsende" : "bis " + new Date(t.expires * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}${t.active ? ` · ${t.active} verbunden` : ""}</span>
          <button class="btn btn-sm btn-danger" data-closet="${esc(t.route_id)}">Schließen</button></div>`).join("")}</div>` : ""}`;
    if (!body.querySelector('input[name="tgt"]:checked')) {
      // Agent nicht oeffentlich (private IP) und kein Cloudflare-Tunnel -> gleich VPN anbieten
      const pref = vpnOn && isPrivate(info.local_ip) && !info.tunnel?.ok ? body.querySelector('input[value="vpn"]:not([disabled])') : null;
      const first = pref || body.querySelector('input[name="tgt"]:not([disabled])');
      if (first) first.checked = true;
    }
    const sync = () => {
      const tgt = body.querySelector('input[name="tgt"]:checked')?.value;
      body.querySelector("[data-portrow]").style.display = tgt === "gateway" ? "none" : "flex";
      body.querySelector("[data-port]").style.display = body.querySelector("[data-pm]").value === "custom" ? "" : "none";
      body.querySelector("[data-liferow]").style.display = tgt === "tunnel" ? "flex" : "none";
    };
    body.querySelectorAll("input, select").forEach((el) => el.addEventListener("change", sync));
    body.querySelector("[data-host]")?.addEventListener("focus", () => { body.querySelector('input[value="custom"]').checked = true; sync(); });
    body.querySelectorAll("[data-closet]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm("Tunnel schließen?", { okText: "Schließen", danger: true }))) return;
      try { await creq(`/tunnels/${b.dataset.closet}/close`, { method: "POST" }); window.notify?.("Tunnel geschlossen", "success"); drawBody(); }
      catch (e) { window.notify?.(e.message, "error"); }
    }));
    sync();
  };
  ov.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => { kind = b.dataset.kind; drawBody(); }));
  drawBody();
  $("[data-ok]").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    let remember = $("[data-remember]").checked;
    let params = { protocol: kind };
    if (kind !== "shell") {
      const body = $("[data-body]");
      const tgt = body.querySelector('input[name="tgt"]:checked')?.value;
      if (!tgt) { window.notify?.("Bitte ein Ziel wählen", "warning"); return; }
      params = { protocol: kind, host_mode: tgt, host: body.querySelector("[data-host]")?.value.trim() || "",
                 port_mode: tgt === "gateway" ? "gateway" : body.querySelector("[data-pm]").value,
                 port: parseInt(body.querySelector("[data-port]").value, 10) || null,
                 lifetime: body.querySelector("[data-life]")?.value || "session",
                 user: body.querySelector("[data-user]")?.value.trim() || "" };
    }
    btn.disabled = true;
    try {
      if (params.host_mode === "vpn") {
        window.notify?.("VPN-Tunnel wird vorbereitet …", "info", 4000);
        const q = await vpnQuick(client);
        params = { ...params, host_mode: "custom", host: q.agent_ip, vpn_tunnel: q.tunnel };
        remember = false;
      }
      if (remember) {
        try { localStorage.setItem(kindKey(client.id, action === "all" ? (kind === "ssh" || kind === "shell" ? "terminal" : "vnc") : action), kind); } catch { /* egal */ }
      }
      await launch(client, params, remember && kind !== "shell");
      close();
      window.notify?.(`${PROTO[kind].label}: Client-App öffnet …`, "success", 3500);
    } catch (err) { window.notify?.(err.message, "error", 8000); btn.disabled = false; }
  });
}

// ------------------------------------------------------------------ Profil
export async function renderOpenModePref(host) {
  const mode = await loadOpenMode();
  host.innerHTML = `
    <div style="font-weight:600;font-size:13px;margin:0 0 4px">Fernzugriff in der Client-App</div>
    <div style="color:var(--subtext);font-size:12px;margin-bottom:6px">Terminal und Remote-Bildschirm eines Agents öffnen – im Fenster der Client-App und (mit dem Haken unten)
      auch im normalen Browser. Zusätzlich gibt es im Agent-Panel den Knopf „🖥 In PC-App öffnen“. Umschalt+Klick zeigt jederzeit die Auswahl.</div>
    <select data-openmode style="max-width:420px">
      <option value="external" ${mode === "external" ? "selected" : ""}>Extern mit passender Software (RDP, VNC, SSH) – Standard</option>
      <option value="ask" ${mode === "ask" ? "selected" : ""}>Jedes Mal fragen</option>
      <option value="web" ${mode === "web" ? "selected" : ""}>Im Browser-Fenster (wie ohne App)</option>
    </select>
    <label style="display:flex;gap:8px;align-items:center;margin-top:8px;font-size:12.5px;cursor:pointer">
      <input type="checkbox" data-haspc ${hasPcApp() ? "checked" : ""}> Die PC-App ist auf DIESEM Rechner installiert – Terminal/Remote-Bildschirm auch im normalen Browser direkt mit der App öffnen</label>`;
  host.querySelector("[data-haspc]").addEventListener("change", (e) => setPcApp(e.target.checked));
  host.querySelector("[data-openmode]").addEventListener("change", async (e) => {
    try { await creq("/prefs", { method: "PUT", body: { open_mode: e.target.value } }); _mode = e.target.value; window.notify?.("Gespeichert", "success", 2000); }
    catch (err) { window.notify?.(err.message, "error"); }
  });
}

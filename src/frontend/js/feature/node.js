// node.js (feature) - Node-Verwaltung (Core)
// -------------------------------------------
// Eine NODE ist ein eigener Prozess neben dem Agenten auf einem Client. Er prueft Docker
// und fuehrt Node-MODULE aus (z.B. VPN, Proxy, Netzlaufwerke). Dieses Fenster gibt es
//   * pro Client  (props.clientId)  - aktivieren/deaktivieren, Docker, Module, Log
//   * als Uebersicht (ohne props)   - alle Nodes im Ueberblick

import { api } from "../app/api.js";
import { esc, uiConfirm } from "../app/utils.js";
import { dashboardSocket } from "../app/socket.js";
import { openWindow } from "../app/windowmanager.js";

const STATE = {
  off:        { label: "nicht aktiv",          color: "var(--subtext)" },
  installing: { label: "wird installiert …",   color: "#f5a524" },
  installed:  { label: "installiert",          color: "#3ecf8e" },
  removing:   { label: "wird entfernt …",      color: "#f5a524" },
  error:      { label: "Fehler",               color: "#ff4d6d" },
};
const MOD_STATE = { running: "läuft", stopped: "gestoppt", partial: "teilweise", "no-docker": "Docker fehlt", error: "Fehler", unknown: "?" };
const ago = (ms) => { if (!ms) return "–"; const s = Math.round((Date.now() - ms) / 1000); return s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`; };
const chip = (text, color) => `<span style="font-size:11px;padding:2px 8px;border-radius:99px;border:1px solid ${color};color:${color}">${esc(text)}</span>`;

export function renderNode(body, win) {
  const clientId = win?.props?.clientId;
  if (!clientId) return renderOverview(body);
  renderDetail(body, win, clientId);
}

// ------------------------------------------------------------------ Uebersicht
async function renderOverview(body) {
  body.innerHTML = `<div style="padding:16px;height:100%;overflow:auto;box-sizing:border-box">
    <h2 style="margin:0 0 6px;font-size:18px">🧱 Nodes</h2>
    <p style="margin:0 0 14px;color:var(--subtext);font-size:12px;line-height:1.5">
      Eine Node ist ein eigener Prozess neben dem Agenten. Aktivieren: <b>Client-Panel → Update-Knopf → „Node verwalten“</b>.</p>
    <div id="nv-list" style="color:var(--subtext)">Lade …</div></div>`;
  const list = body.querySelector("#nv-list");
  const draw = async () => {
    if (!body.isConnected) return;
    try {
      const { nodes } = await api.listNodes();
      list.innerHTML = nodes.length ? nodes.map((n) => {
        const s = STATE[n.state] || STATE.off;
        const dk = n.docker?.daemon ? "🐳 läuft" : n.docker?.installed ? "🐳 Dienst aus" : "🐳 fehlt";
        return `<div data-open="${esc(n.client_id)}" style="cursor:pointer;display:flex;gap:12px;align-items:center;padding:10px 12px;border:1px solid var(--border);border-radius:10px;margin-bottom:8px;background:var(--panel)">
          <span style="font-size:18px">${n.online ? "🟢" : "⚪"}</span>
          <div style="flex:1;min-width:0"><div style="color:var(--text)">${esc(n.hostname || n.client_id)}</div>
            <div style="font-size:11px;color:var(--subtext)">${esc(n.os || "")} · v${esc(n.version || "?")} · ${dk} · ${Object.keys(n.modules || {}).length} Modul(e)</div></div>
          ${chip(n.online ? "online" : s.label, n.online ? "#3ecf8e" : s.color)}</div>`;
      }).join("") : `<div>Noch keine Node aktiviert.</div>`;
      list.querySelectorAll("[data-open]").forEach((el) => el.onclick = () => {
        const id = el.dataset.open, n = nodes.find((x) => x.client_id === id);
        openWindow({ key: `node-${id}`, appId: "node", title: `Node – ${n?.hostname || id}`, props: { clientId: id, clientName: n?.hostname }, w: 820, h: 680 });
      });
    } catch (e) { list.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; }
  };
  draw();
  const h = () => { if (!body.isConnected) dashboardSocket.off("node:update", h); else draw(); };
  dashboardSocket.on("node:update", h);
}

// ------------------------------------------------------------------ Detail
function renderDetail(body, win, clientId) {
  let st = null, avail = [], busy = false;
  body.innerHTML = `<div class="nd-root" style="padding:14px 16px;height:100%;overflow:auto;box-sizing:border-box"></div>`;
  const root = body.querySelector(".nd-root");

  async function load() {
    try {
      [st, avail] = await Promise.all([api.nodeState(clientId), api.nodeModules().then((r) => r.modules).catch(() => [])]);
    } catch (e) { root.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    draw();
    loadLog();
  }
  async function loadLog() {
    const pre = root.querySelector("#nd-log"); if (!pre) return;
    try { const { lines } = await api.nodeLogs(clientId); pre.textContent = lines.slice(-60).join("\n") || "(noch keine Einträge)"; pre.scrollTop = pre.scrollHeight; } catch { /* egal */ }
  }
  async function act(label, fn, okMsg) {
    if (busy) return; busy = true;
    window.notify?.(`${label} …`, "info", 4000, { tag: `node:${clientId}` });
    try { await fn(); if (okMsg) window.notify?.(okMsg, "success", 5000, { tag: `node:${clientId}` }); }
    catch (e) { window.notify?.(`${label}: ${e.message}`, "error", 12000, { tag: `node:${clientId}` }); }
    busy = false; await load();
  }

  function dockerCard() {
    const d = st.docker || {};
    if (!st.online) return `<div style="color:var(--subtext);font-size:13px">Die Node ist offline – Docker-Zustand unbekannt.</div>`;
    const row = (ok, text) => `<span style="margin-right:14px;color:${ok ? "#3ecf8e" : "#ff4d6d"}">${ok ? "●" : "○"} ${text}</span>`;
    return `<div>${row(d.installed, "installiert")}${row(d.daemon, `Dienst läuft${d.version ? " (" + esc(d.version) + ")" : ""}`)}${row(d.compose, `Compose${d.compose_version ? " " + esc(d.compose_version) : ""}`)}</div>
      ${d.hint ? `<div style="margin-top:8px;font-size:12px;color:var(--warn,#f5a524);line-height:1.5">⚠ ${esc(d.hint)}</div>` : ""}
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        ${d.can_install ? `<button class="btn-primary" id="nd-docker-install">🐳 Docker installieren</button>` : ""}
        <button class="btn" id="nd-refresh">⟳ Neu prüfen</button></div>`;
  }

  function paramForm(m) {
    return (m.params || []).map((p) => `<label style="font-size:12px;color:var(--subtext);margin-right:10px">${esc(p.label || p.key)}
      <input data-param="${esc(p.key)}" type="${p.type === "number" ? "number" : "text"}" value="${esc(p.default ?? "")}" style="width:110px"></label>`).join("");
  }

  function modulesCard() {
    const inst = st.modules || {};
    const ids = Object.keys(inst);
    const installed = ids.length ? ids.map((id) => {
      const m = inst[id], ms = MOD_STATE[m.state] || m.state;
      const color = m.state === "running" ? "#3ecf8e" : m.state === "error" || m.state === "no-docker" ? "#ff4d6d" : "var(--subtext)";
      const acts = (avail.find((a) => a.id === id)?.actions || ["up", "down", "restart", "logs"]).filter((a) => a !== "status");
      const label = { up: "▶ Start", down: "■ Stopp", restart: "↻ Neustart", logs: "📜 Log" };
      return `<div style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:8px;background:var(--panel)">
        <div style="display:flex;align-items:center;gap:10px"><b style="flex:1">${esc(id)} <span style="font-weight:400;color:var(--subtext);font-size:12px">v${esc(m.version || "?")}</span></b>${chip(ms, color)}</div>
        ${(m.containers || []).map((c) => `<div style="font-size:11px;color:var(--subtext);margin-top:3px">${esc(c.name || c.service)} · ${esc(c.status || c.state || "")}</div>`).join("")}
        <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">${acts.map((a) => `<button class="btn" data-mact="${a}" data-mid="${esc(id)}">${label[a] || a}</button>`).join("")}
          <button class="btn btn-danger" data-mrem="${esc(id)}" style="margin-left:auto">🗑 Entfernen</button></div>
        <pre class="nd-mlog" data-mlog="${esc(id)}" style="display:none;margin:8px 0 0;max-height:200px;overflow:auto;font-size:11px;background:var(--panel-2);padding:8px;border-radius:6px;white-space:pre-wrap"></pre></div>`;
    }).join("") : `<div style="color:var(--subtext);font-size:13px;margin-bottom:8px">Keine Node-Module installiert.</div>`;
    const deployable = avail.filter((a) => !inst[a.id]);
    const add = deployable.length ? `<div style="border-top:1px solid var(--border);padding-top:10px;margin-top:6px">
      <div style="font-size:12px;color:var(--subtext);margin-bottom:6px">Modul auf dieser Node bereitstellen</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
        <select id="nd-newmod">${deployable.map((a) => `<option value="${esc(a.id)}" ${a.active ? "" : "disabled"}>${esc(a.icon)} ${esc(a.name)}${a.active ? "" : " (Modul nicht aktiv)"}</option>`).join("")}</select>
        <span id="nd-params"></span><button class="btn-primary" id="nd-deploy">Bereitstellen</button></div></div>` : "";
    return installed + add;
  }

  function draw() {
    const s = STATE[st.state] || STATE.off;
    const on = st.state !== "off";
    root.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <h2 style="margin:0;font-size:18px">🧱 Node – ${esc(win.props.clientName || st.hostname || clientId)}</h2>
        ${chip(st.online ? "online" : s.label, st.online ? "#3ecf8e" : s.color)}
        ${st.version ? `<span style="font-size:12px;color:var(--subtext)">v${esc(st.version)}${st.update_available ? " · <b style='color:#f5a524'>Update verfügbar</b> <button class='btn btn-sm' id='nd-update' style='margin-left:6px'>Jetzt aktualisieren</button>" : ""}</span>` : ""}
        <span style="flex:1"></span>
        ${on ? `<button class="btn" id="nd-disable">Deaktivieren</button>` : `<button class="btn-primary" id="nd-enable">Node aktivieren</button>`}
      </div>
      ${st.error ? `<div style="background:rgba(255,77,109,.1);border:1px solid #ff4d6d;border-radius:8px;padding:8px 10px;margin-bottom:12px;font-size:13px">⚠ ${esc(st.error)}</div>` : ""}
      ${!on ? `<p style="color:var(--subtext);font-size:13px;line-height:1.55;max-width:620px">Die Node ist ein eigener Prozess neben dem Agenten. Beim Aktivieren lädt der Agent sie vom Server, richtet sie als Dienst ein und startet sie.
        Sie prüft dann Docker (oder meldet, dass es installiert werden muss) und führt Node-Module aus – z.B. VPN oder Proxy als Docker-Container.</p>` : `
      <h3 style="margin:14px 0 6px;font-size:13px">🐳 Docker</h3>${dockerCard()}
      <h3 style="margin:18px 0 6px;font-size:13px">📦 Node-Module</h3>${st.online ? modulesCard() : `<div style="color:var(--subtext);font-size:13px">Die Node ist offline.</div>`}
      <h3 style="margin:18px 0 6px;font-size:13px">📜 Log</h3>
      <pre id="nd-log" style="margin:0;max-height:180px;overflow:auto;font-size:11px;background:var(--panel-2);padding:8px;border-radius:6px;white-space:pre-wrap"></pre>`}`;
    const q = (s) => root.querySelector(s);
    q("#nd-enable")?.addEventListener("click", () => act("Node aktivieren", () => api.nodeEnable(clientId), "Installation gestartet"));
    q("#nd-disable")?.addEventListener("click", async () => {
      if (await uiConfirm("Node deaktivieren?", { description: "Alle Node-Module werden gestoppt, der Node-Prozess und seine Dateien werden vom Client entfernt.", okText: "Deaktivieren", danger: true }))
        act("Node deaktivieren", () => api.nodeDisable(clientId), "Node wird entfernt");
    });
    q("#nd-update")?.addEventListener("click", () => act("Node aktualisieren", () => api.nodeUpdate(clientId), "Update angefordert"));
    q("#nd-refresh")?.addEventListener("click", () => act("Neu prüfen", () => api.nodeRefresh(clientId)));
    q("#nd-docker-install")?.addEventListener("click", async () => {
      if (await uiConfirm("Docker auf dem Client installieren?", { description: "Das offizielle Installationsskript von get.docker.com wird auf dem Client ausgeführt (kann einige Minuten dauern).", okText: "Installieren" }))
        act("Docker installieren", () => api.nodeDockerInstall(clientId), "Docker installiert");
    });
    root.querySelectorAll("[data-mact]").forEach((b) => b.onclick = async () => {
      const id = b.dataset.mid, a = b.dataset.mact;
      if (a === "logs") {
        try { const r = await api.nodeModuleAction(clientId, id, "logs", { lines: 100 }); const pre = root.querySelector(`[data-mlog="${id}"]`); pre.style.display = ""; pre.textContent = r.result?.log || "(leer)"; }
        catch (e) { window.notify?.(e.message, "error", 9000); }
        return;
      }
      act(`${id}: ${a}`, () => api.nodeModuleAction(clientId, id, a));
    });
    root.querySelectorAll("[data-mrem]").forEach((b) => b.onclick = async () => {
      const id = b.dataset.mrem;
      if (await uiConfirm(`Modul „${id}“ von der Node entfernen?`, { okText: "Entfernen", danger: true })) act(`${id} entfernen`, () => api.nodeRemoveModule(clientId, id), "Entfernt");
    });
    const sel = q("#nd-newmod"), params = q("#nd-params");
    const showParams = () => { const m = avail.find((a) => a.id === sel.value); params.innerHTML = m ? paramForm(m) : ""; };
    if (sel) { sel.onchange = showParams; showParams(); }
    q("#nd-deploy")?.addEventListener("click", () => {
      const p = {}; root.querySelectorAll("[data-param]").forEach((i) => { p[i.dataset.param] = i.type === "number" ? Number(i.value) : i.value; });
      act(`${sel.value} bereitstellen`, () => api.nodeDeploy(clientId, sel.value, p), "Bereitgestellt");
    });
  }

  const onUpdate = (p) => {
    if (!body.isConnected) { dashboardSocket.off("node:update", onUpdate); return; }
    if (p?.client_id === clientId && !busy) { st = p; draw(); loadLog(); }
  };
  dashboardSocket.on("node:update", onUpdate);
  load();
}

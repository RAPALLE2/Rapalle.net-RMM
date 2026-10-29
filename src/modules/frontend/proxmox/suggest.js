// suggest.js (Modul "proxmox") - Vorschlaege: Agenten, die als VM/CT in Proxmox laufen
// -----------------------------------------------------------------------------------
// Hinweis unten rechts + Dialog: einzeln oder alle uebernehmen (als VM/CT markieren und dem
// HEAD unterordnen) bzw. verwerfen. Backend: match_routes.py
//
// HEAD = uebergeordneter Agent. In einem Proxmox-Cluster (pve1, pve2, ...) hat oft nur ein Agent
// die Proxmox-Verbindung - die Gaeste sollen aber unter dem Agenten ihres eigenen Nodes stehen.
//   * "Head je Proxmox-Node" (oben, generell): gilt fuer ALLE Gaeste dieses Nodes (auch bestehende).
//   * Auswahl je Zeile: die ERSTE Aenderung auf einem Node legt den Head fuer den ganzen Node fest
//     (Beispiel: einen Gast auf pve2 dem Agenten pve2 geben -> alle pve2-Gaeste gehen zu pve2, die
//     pve1-Gaeste bleiben bei pve1). Danach ist eine Aenderung je Zeile eine Ausnahme nur fuer diesen Gast.
// "Nicht erneut fragen" blendet die aktuellen Vorschlaege dauerhaft aus (pro Benutzer, wird synchronisiert);
// im Proxmox-Dashboard (Register "Zuordnung") sind sie weiter erreichbar.
import { esc } from "/js/app/utils.js";
import { state } from "/js/app/state.js";
import { preq } from "./common.js";

const SEEN = "rmm_pve_suggest_seen";                       // "Später" (nur diese Sitzung)
const neverKey = () => `rmm_pve_never:${state.user?.username || ""}`;
const sid = (s) => `${s.client_id}:${s.host}:${s.vmid}`;

function neverSet() {
  try { return new Set(JSON.parse(localStorage.getItem(neverKey()) || "[]")); } catch { return new Set(); }
}
function addNever(list) {
  const n = neverSet();
  list.forEach((s) => n.add(sid(s)));
  localStorage.setItem(neverKey(), JSON.stringify([...n].slice(-2000)));
}

export function maybeShowBanner(suggestions, onChange, extra = {}) {
  const never = neverSet();
  const open = suggestions.filter((s) => !never.has(sid(s)));
  const ids = open.map(sid).sort().join(",");
  document.getElementById("pve-suggest-banner")?.remove();
  if (!open.length || sessionStorage.getItem(SEEN) === ids) return;
  const el = document.createElement("div");
  el.id = "pve-suggest-banner";
  el.style.cssText = "position:fixed;right:16px;bottom:64px;z-index:9300;background:var(--panel);border:1px solid var(--accent);" +
    "border-radius:10px;padding:14px 16px;width:360px;max-width:calc(100vw - 32px);box-shadow:0 8px 28px rgba(0,0,0,.45);font-size:13px";
  el.innerHTML = `<div style="display:flex;gap:10px;align-items:flex-start">
      <span style="font-size:20px">🖧</span>
      <div style="flex:1"><b>${open.length} Agent(en) laufen in Proxmox</b>
        <div style="color:var(--subtext);margin-top:2px">${esc(open.slice(0, 3).map((s) => s.client_name).join(", "))}${open.length > 3 ? " …" : ""}
        – als VM/CT einsortieren und dem Head (Proxmox-Node) unterordnen?</div>
        <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap"><button class="btn btn-primary btn-sm" data-open>Ansehen</button>
          <button class="btn btn-sm" data-later>Später</button>
          <button class="btn btn-sm btn-ghost" data-never title="Diese Vorschläge nicht mehr als Hinweis anzeigen">Nicht erneut fragen</button></div></div></div>`;
  document.body.appendChild(el);
  el.querySelector("[data-later]").addEventListener("click", () => { sessionStorage.setItem(SEEN, ids); el.remove(); });
  el.querySelector("[data-never]").addEventListener("click", () => {
    addNever(open); el.remove();
    window.notify?.("Wird nicht mehr nachgefragt – erreichbar im Proxmox-Dashboard unter „Zuordnung“.", "info", 6000);
  });
  el.querySelector("[data-open]").addEventListener("click", () => { el.remove(); openSuggestDialog(open, onChange, extra); });
}

export function openSuggestDialog(suggestions, onChange, extra = {}) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px 18px;width:760px;max-width:100%;
      max-height:90vh;display:flex;flex-direction:column;font-size:13px;box-sizing:border-box">
    <div style="display:flex;align-items:center;margin-bottom:6px"><b style="flex:1;font-size:15px">🖧 Agenten in Proxmox erkannt</b><button class="btn-tool" data-x>✕</button></div>
    <p style="color:var(--subtext);margin:0 0 10px">Diese Agenten laufen laut Proxmox in einer VM bzw. einem Container. Übernehmen markiert sie als
      <b>VM</b>/<b>CT</b> und ordnet sie dem <b>Head</b> unter – dem Agenten des Proxmox-Nodes, auf dem der Gast läuft.
      Im Agent-Panel gibt es dann „In Proxmox öffnen“.</p>
    <div data-heads></div>
    <div data-list style="flex:1;overflow:auto;min-height:80px"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;flex-wrap:wrap">
      <button class="btn btn-ghost" data-never style="margin-right:auto">Nicht erneut fragen</button>
      <button class="btn" data-x2>Schließen</button>
      <button class="btn btn-primary" data-all>Alle übernehmen</button></div></div>`;
  document.body.appendChild(ov);
  let list = [...suggestions];
  let cands = extra.candidates || [];
  const override = {};                       // client_id -> head (Ausnahme nur fuer diesen Gast)
  const setNodes = new Set();                // "host|node", deren Head hier schon festgelegt wurde
  const nodeKey = (s) => `${s.host}|${s.node}`;
  const close = () => { ov.remove(); onChange?.(); };
  ov.querySelector("[data-x]").onclick = close;
  ov.querySelector("[data-x2]").onclick = close;
  ov.querySelector("[data-never]").onclick = () => {
    addNever(list);
    window.notify?.("Wird nicht mehr nachgefragt – erreichbar im Proxmox-Dashboard unter „Zuordnung“.", "info", 6000);
    close();
  };

  const nameOf = (id) => (cands.find((c) => c.id === id) || (state.clients || []).find((c) => c.id === id) || {}).hostname || id;
  const opts = (sel, exclude) => {
    const items = cands.filter((c) => c.id !== exclude);
    if (sel && !items.some((c) => c.id === sel)) items.unshift({ id: sel, hostname: nameOf(sel) });
    return items.map((c) => `<option value="${esc(c.id)}" ${c.id === sel ? "selected" : ""}>${esc(c.hostname)}${c.connected ? " 🖧" : ""}</option>`).join("");
  };
  const headOf = (s) => override[s.client_id] || s.head;

  async function reload() {
    try {
      const g = await preq("/guest-map");
      const ids = new Set(list.map((s) => s.client_id));
      list = (g.suggestions || []).filter((s) => ids.has(s.client_id));
      cands = g.candidates?.length ? g.candidates : cands;
    } catch (e) { window.notify?.(e.message, "error"); }
    draw();
  }

  async function setNodeHead(host, node, head) {
    try {
      const r = await preq("/heads", { method: "PUT", body: { host, node, head } });
      setNodes.add(`${host}|${node}`);
      window.notify?.(`Head für Node ${node}: ${nameOf(head)}` + (r.moved ? ` (${r.moved} bestehende Zuordnung(en) umgehängt)` : ""), "success", 4000);
      // Ausnahmen, die jetzt dem Node-Head entsprechen, sind keine mehr
      list.forEach((s) => { if (nodeKey(s) === `${host}|${node}` && override[s.client_id] === head) delete override[s.client_id]; });
    } catch (e) { window.notify?.(e.message, "error"); }
    await reload();
  }

  const draw = () => {
    // ---- generell: Head je Proxmox-Node
    const nodes = new Map();
    list.forEach((s) => { if (!nodes.has(nodeKey(s))) nodes.set(nodeKey(s), s); });
    ov.querySelector("[data-heads]").innerHTML = nodes.size ? `
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:10px">
        <div style="font-weight:600;margin-bottom:6px">Head je Proxmox-Node <span style="font-weight:400;color:var(--subtext)">(gilt für alle Gäste des Nodes)</span></div>
        ${[...nodes.values()].map((s) => `
          <div data-node="${esc(nodeKey(s))}" style="display:flex;gap:8px;align-items:center;padding:3px 0;flex-wrap:wrap">
            <span style="min-width:170px">Node <b>${esc(s.node)}</b> <span style="color:var(--subtext)">(${esc(s.host_name)})</span></span>
            <select data-nodehead style="flex:1;min-width:180px">${opts(s.head)}</select>
            <span style="font-size:11px;color:var(--subtext)">${s.head_source === "set" ? "festgelegt" : s.head_source === "hostname" ? "erkannt (Hostname)" : "verbundener Agent"}</span>
          </div>`).join("")}
      </div>` : "";
    ov.querySelectorAll("[data-node]").forEach((row) => {
      const [host, node] = row.dataset.node.split("|");
      row.querySelector("[data-nodehead]").onchange = (e) => setNodeHead(host, node, e.target.value);
    });

    // ---- je Gast
    ov.querySelector("[data-list]").innerHTML = list.length ? list.map((s) => `
      <div data-c="${esc(s.client_id)}" style="display:flex;gap:10px;align-items:center;padding:8px 2px;border-bottom:1px solid var(--border);flex-wrap:wrap">
        <span style="font-size:18px">${s.type === "qemu" ? "🖥" : "📦"}</span>
        <div style="flex:1;min-width:220px"><b>${esc(s.client_name)}</b>
          <div style="font-size:12px;color:var(--subtext)">${s.type === "qemu" ? "VM" : "CT"} ${s.vmid} „${esc(s.name)}“ · Node ${esc(s.node)} (${esc(s.host_name)}) · erkannt über ${esc(s.reason)}</div></div>
        <label style="display:flex;gap:6px;align-items:center;font-size:12px">Head
          <select data-head style="max-width:190px">${opts(headOf(s), s.client_id)}</select></label>
        ${override[s.client_id] ? `<span title="Ausnahme nur für diesen Gast" style="font-size:11px;color:var(--warning,#d97706)">Ausnahme</span>` : ""}
        <button class="btn btn-sm" data-ign>Nein</button><button class="btn btn-primary btn-sm" data-acc>Übernehmen</button></div>`).join("")
      : `<div style="color:var(--subtext);padding:8px 0">Alles erledigt.</div>`;
    ov.querySelectorAll("[data-c]").forEach((row) => {
      const cid = row.dataset.c;
      const s = list.find((x) => x.client_id === cid);
      row.querySelector("[data-acc]").onclick = () => act(cid, "accept");
      row.querySelector("[data-ign]").onclick = () => act(cid, "ignore");
      row.querySelector("[data-head]").onchange = (e) => {
        const v = e.target.value;
        if (!setNodes.has(nodeKey(s)) && s.head_source !== "set") {
          // erste Wahl auf diesem Node -> gilt fuer den ganzen Node (alle anderen Gaeste dort ziehen mit)
          setNodeHead(s.host, s.node, v);
        } else {
          if (v === s.head) delete override[cid]; else override[cid] = v;
          draw();
        }
      };
    });
    ov.querySelector("[data-all]").disabled = !list.length;
  };

  async function act(cid, what) {
    const s = list.find((x) => x.client_id === cid);
    try {
      const body = what === "accept" ? { head: headOf(s), remember: !override[cid] } : undefined;
      const r = await preq(`/suggestions/${encodeURIComponent(cid)}/${what}`, { method: "POST", body });
      if (what === "accept" && r.client) window.dispatchEvent(new CustomEvent("rmm-client-updated", { detail: r.client }));
      list = list.filter((x) => x.client_id !== cid); draw();
    } catch (e) { window.notify?.(e.message, "error"); }
  }
  ov.querySelector("[data-all]").onclick = async () => {
    for (const s of [...list]) await act(s.client_id, "accept");
    window.notify?.("Zuordnungen übernommen", "success");
  };
  draw();
}

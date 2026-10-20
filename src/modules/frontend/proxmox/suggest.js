// suggest.js (Modul "proxmox") - Vorschlaege: Agenten, die als VM/CT in Proxmox laufen
// -----------------------------------------------------------------------------------
// Hinweis unten rechts + Dialog: einzeln oder alle uebernehmen (als VM/CT markieren und dem
// Proxmox-Host zuordnen) bzw. verwerfen. Backend: match_routes.py
import { esc } from "/js/app/utils.js";
import { preq } from "./common.js";

const SEEN = "rmm_pve_suggest_seen";

export function maybeShowBanner(suggestions, onChange) {
  const ids = suggestions.map((s) => `${s.client_id}:${s.vmid}`).sort().join(",");
  document.getElementById("pve-suggest-banner")?.remove();
  if (!suggestions.length || sessionStorage.getItem(SEEN) === ids) return;
  const el = document.createElement("div");
  el.id = "pve-suggest-banner";
  el.style.cssText = "position:fixed;right:16px;bottom:64px;z-index:9300;background:var(--panel);border:1px solid var(--accent);" +
    "border-radius:10px;padding:12px 14px;width:340px;max-width:calc(100vw - 32px);box-shadow:0 8px 28px rgba(0,0,0,.45);font-size:13px";
  el.innerHTML = `<div style="display:flex;gap:8px;align-items:flex-start">
      <span style="font-size:20px">🖧</span>
      <div style="flex:1"><b>${suggestions.length} Agent(en) laufen in Proxmox</b>
        <div style="color:var(--subtext);margin-top:2px">${esc(suggestions.slice(0, 3).map((s) => s.client_name).join(", "))}${suggestions.length > 3 ? " …" : ""}
        – als VM/CT markieren und dem Node zuordnen?</div>
        <div style="display:flex;gap:6px;margin-top:8px"><button class="btn-primary btn-sm" data-open>Ansehen</button>
          <button class="btn btn-sm" data-later>Später</button></div></div></div>`;
  document.body.appendChild(el);
  el.querySelector("[data-later]").addEventListener("click", () => { sessionStorage.setItem(SEEN, ids); el.remove(); });
  el.querySelector("[data-open]").addEventListener("click", () => { el.remove(); openSuggestDialog(suggestions, onChange); });
}

export function openSuggestDialog(suggestions, onChange) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;width:640px;max-width:100%;
      max-height:88vh;display:flex;flex-direction:column;font-size:13px">
    <div style="display:flex;align-items:center;margin-bottom:6px"><b style="flex:1;font-size:15px">🖧 Agenten in Proxmox erkannt</b><button class="btn-tool" data-x>✕</button></div>
    <p style="color:var(--subtext);margin:0 0 10px">Diese Agenten laufen laut Proxmox in einer VM bzw. einem Container. Übernehmen markiert sie als
      <b>VM</b>/<b>CT</b> und ordnet sie dem Proxmox-Host zu – im Agent-Panel gibt es dann „In Proxmox öffnen“.</p>
    <div data-list style="flex:1;overflow:auto"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px"><button class="btn" data-x2>Schließen</button>
      <button class="btn-primary" data-all>Alle übernehmen</button></div></div>`;
  document.body.appendChild(ov);
  let list = [...suggestions];
  const close = () => { ov.remove(); onChange?.(); };
  ov.querySelector("[data-x]").onclick = close;
  ov.querySelector("[data-x2]").onclick = close;
  const draw = () => {
    ov.querySelector("[data-list]").innerHTML = list.length ? list.map((s) => `
      <div data-c="${esc(s.client_id)}" style="display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
        <span style="font-size:18px">${s.type === "qemu" ? "🖥" : "📦"}</span>
        <div style="flex:1;min-width:0"><b>${esc(s.client_name)}</b>
          <div style="font-size:12px;color:var(--subtext)">${s.type === "qemu" ? "VM" : "CT"} ${s.vmid} „${esc(s.name)}“ auf ${esc(s.host_name)} (Node ${esc(s.node)}) · erkannt über ${esc(s.reason)}</div></div>
        <button class="btn btn-sm" data-ign>Nein</button><button class="btn-primary btn-sm" data-acc>Übernehmen</button></div>`).join("")
      : `<div style="color:var(--subtext);padding:8px 0">Alles erledigt.</div>`;
    ov.querySelectorAll("[data-c]").forEach((row) => {
      const cid = row.dataset.c;
      row.querySelector("[data-acc]").onclick = () => act(cid, "accept");
      row.querySelector("[data-ign]").onclick = () => act(cid, "ignore");
    });
    ov.querySelector("[data-all]").disabled = !list.length;
  };
  async function act(cid, what) {
    try {
      const r = await preq(`/suggestions/${encodeURIComponent(cid)}/${what}`, { method: "POST" });
      if (what === "accept" && r.client) window.dispatchEvent(new CustomEvent("rmm-client-updated", { detail: r.client }));
      list = list.filter((s) => s.client_id !== cid); draw();
    } catch (e) { window.notify?.(e.message, "error"); }
  }
  ov.querySelector("[data-all]").onclick = async () => {
    for (const s of [...list]) await act(s.client_id, "accept");
    window.notify?.("Zuordnungen übernommen", "success");
  };
  draw();
}

// console.js (Modul "proxmox") - Konsole von VMs/LXC
// --------------------------------------------------
// * Knoepfe in der Gast-Ansicht (Event "rmm-pve-guest" aus dashboard.js):
//     🖥 Konsole        -> eigenes Fenster mit noVNC, durch das RMM getunnelt
//     🌐 Weboberfläche  -> Proxmox-Weboberflaeche im Browser (direkt / ueber Weiterleitung)
// * renderConsole: Fenster-App "proxmox-console" (nicht im Startmenue)
// Backend: console_routes.py

import { getToken } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";
import { openWindow } from "/js/app/windowmanager.js";
import { preq } from "./common.js";

// ------------------------------------------------------------------ Knoepfe in der Gast-Ansicht
window.addEventListener("rmm-pve-guest", (ev) => {
  const { slot, cid, guest } = ev.detail || {};
  if (!slot) return;
  slot.innerHTML = `<span style="display:inline-flex;gap:6px;position:relative">
      <button class="btn btn-sm btn-primary" data-con>🖥 Konsole</button>
      <button class="btn btn-sm" data-web>🌐 Weboberfläche ▾</button>
      <div data-menu style="display:none;position:absolute;right:0;top:100%;margin-top:4px;z-index:50;background:var(--panel);
           border:1px solid var(--border);border-radius:8px;padding:4px;min-width:250px;box-shadow:0 8px 24px rgba(0,0,0,.4)">
        <button class="btn btn-ghost btn-sm btn-block" data-via="direct" style="justify-content:flex-start">Direkt öffnen (Proxmox im selben Netz)</button>
        <button class="btn btn-ghost btn-sm btn-block" data-via="forward" style="justify-content:flex-start">Über RMM-Weiterleitung (nur meine IP)</button>
      </div></span>`;
  slot.querySelector("[data-con]").addEventListener("click", () => openConsole(cid, guest));
  const menu = slot.querySelector("[data-menu]");
  slot.querySelector("[data-web]").addEventListener("click", (e) => { e.stopPropagation(); menu.style.display = menu.style.display === "none" ? "" : "none"; });
  document.addEventListener("click", () => { menu.style.display = "none"; }, { once: true });
  slot.querySelectorAll("[data-via]").forEach((b) => b.addEventListener("click", async () => {
    menu.style.display = "none";
    // Fenster sofort oeffnen (Popup-Blocker), Adresse danach setzen
    const w = window.open("about:blank", "_blank");
    try {
      const r = await preq(`/${encodeURIComponent(cid)}/guests/${encodeURIComponent(guest.node)}/${guest.type}/${guest.vmid}/webui`,
                           { method: "POST", body: { via: b.dataset.via } });
      if (w) w.location.href = r.url; else window.open(r.url, "_blank");
      if (r.note) window.notify?.(r.note, "info", 9000);
    } catch (e) { w?.close(); window.notify?.(e.message, "error", 9000); }
  }));
});

export function openConsole(cid, guest) {
  openWindow({ key: `pve-con-${cid}-${guest.vmid}`, appId: "proxmox-console",
               title: `Konsole — ${guest.name || guest.vmid} (${guest.type === "qemu" ? "VM" : "LXC"} ${guest.vmid})`,
               props: { clientId: cid, node: guest.node, type: guest.type, vmid: guest.vmid }, w: 1024, h: 700 });
}

// ------------------------------------------------------------------ Konsolenfenster
export function renderConsole(body, win) {
  const p = win?.props || {};
  body.innerHTML = `
    <div style="height:100%;display:flex;flex-direction:column;background:#000;min-height:0">
      <div style="display:flex;gap:6px;align-items:center;padding:6px 8px;background:var(--panel);border-bottom:1px solid var(--border);flex-wrap:wrap">
        <span data-st style="flex:1;font-size:12px;color:var(--subtext)">Verbinde …</span>
        <button class="btn btn-sm" data-cad title="Strg+Alt+Entf senden">Strg+Alt+Entf</button>
        <button class="btn btn-sm" data-paste title="Text als Tastatureingabe senden">Text einfügen</button>
        <button class="btn btn-sm active" data-scale title="An Fenster anpassen">Skalieren</button>
        <button class="btn btn-sm" data-full title="Vollbild">⛶</button>
        <button class="btn btn-sm" data-re title="Neu verbinden">⟳</button>
      </div>
      <div data-screen style="flex:1;min-height:0;overflow:hidden"></div>
    </div>`;
  const st = body.querySelector("[data-st]");
  const screen = body.querySelector("[data-screen]");
  let rfb = null;
  let scale = true;
  const status = (t, bad) => { st.textContent = t; st.style.color = bad ? "var(--danger)" : "var(--subtext)"; };

  async function connect() {
    if (rfb) { try { rfb.disconnect(); } catch { /* egal */ } rfb = null; }
    screen.innerHTML = "";
    status("Fordere Konsole an …");
    let s;
    try {
      s = await preq(`/${encodeURIComponent(p.clientId)}/guests/${encodeURIComponent(p.node)}/${p.type}/${p.vmid}/console`, { method: "POST" });
    } catch (e) { status(`❌ ${e.message}`, true); return; }
    let RFB;
    try { RFB = (await import("./novnc/core/rfb.js")).default; }
    catch (e) { status(`❌ noVNC konnte nicht geladen werden: ${e.message}`, true); return; }
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${s.ws}`;
    status("Verbinde …");
    // Dashboard-Sitzung als WebSocket-Unterprotokoll (nicht in der Adresse -> nicht im Server-Log)
    rfb = new RFB(screen, url, { credentials: { password: s.password }, wsProtocols: ["binary", `rmmtoken.${getToken() || ""}`] });
    rfb.scaleViewport = scale;
    rfb.resizeSession = false;
    rfb.background = "#000";
    rfb.addEventListener("connect", () => status(`Verbunden · ${p.type === "qemu" ? "VM" : "LXC"} ${p.vmid} auf ${p.node}`));
    rfb.addEventListener("disconnect", (e) => status(e.detail?.clean ? "Getrennt" : "❌ Verbindung verloren – „⟳“ zum Neuverbinden", !e.detail?.clean));
    rfb.addEventListener("securityfailure", (e) => status(`❌ Anmeldung an der Konsole fehlgeschlagen: ${esc(e.detail?.reason || "")}`, true));
    rfb.addEventListener("desktopname", (e) => { if (e.detail?.name) status(`Verbunden · ${e.detail.name}`); });
  }

  body.querySelector("[data-cad]").addEventListener("click", () => rfb?.sendCtrlAltDel());
  body.querySelector("[data-scale]").addEventListener("click", (e) => {
    scale = !scale; e.currentTarget.classList.toggle("active", scale); if (rfb) rfb.scaleViewport = scale;
  });
  body.querySelector("[data-full]").addEventListener("click", () => screen.requestFullscreen?.());
  body.querySelector("[data-re]").addEventListener("click", connect);
  body.querySelector("[data-paste]").addEventListener("click", async () => {
    const text = prompt("Text, der als Tastatureingabe in die Konsole getippt wird:");
    if (!text || !rfb) return;
    for (const ch of text) {
      const code = ch === "\n" ? 0xff0d : ch.codePointAt(0) < 256 ? ch.codePointAt(0) : 0x01000000 + ch.codePointAt(0);
      rfb.sendKey(code, null, true); rfb.sendKey(code, null, false);
    }
  });
  connect();
  return () => { try { rfb?.disconnect(); } catch { /* egal */ } };
}

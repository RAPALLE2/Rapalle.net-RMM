// init.js (Modul "wol") - Wake-on-LAN im Agent-Panel
// --------------------------------------------------
// Fügt dem Panel die Aktion „Einschalten (WOL)“ hinzu – nur wenn der PC OFFLINE ist
// und der Benutzer das Recht c_power hat. Klick öffnet einen Dialog: MAC(s) (automatisch
// erkannt + manuell), Absender (Backend oder eine Node) und „Jetzt wecken“.

import { nodeHint } from "/js/app/nodechoice.js";
import { registerClientAction, getModuleApi } from "/js/app/modules.js";
import { getToken } from "/js/app/api.js";
import { hasClientPerm } from "/js/app/state.js";
import { esc } from "/js/app/utils.js";

async function wq(path, opts = {}) {
  const headers = { Authorization: "Bearer " + (getToken() || "") };
  if (opts.body !== undefined && typeof opts.body !== "string") { headers["Content-Type"] = "application/json"; opts = { ...opts, body: JSON.stringify(opts.body) }; }
  const res = await fetch("/api/wol" + path, { ...opts, headers });
  if (!res.ok) { let m = res.statusText; try { const j = await res.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* egal */ } throw new Error(m); }
  return res.json();
}

export function init() {
  registerClientAction("wol", {
    label(client) {
      if (client.online) return null;                       // läuft schon
      if (!hasClientPerm(client.id, "c_power")) return null;
      // VM/CT in Proxmox (zugeordnet): direkt ueber Proxmox starten
      const g = getModuleApi("proxmox")?.guestOf?.(client.id);
      if (g) return "⏻ Einschalten (Proxmox)";
      return "⏻ Einschalten (WOL)";
    },
    onClick(client) { wolDialog(client); },
  });
}

async function wolDialog(client) {
  let st;
  try { st = await wq(`/${encodeURIComponent(client.id)}`); } catch (e) { window.notify?.(e.message, "error"); return; }
  const cfg = st.config;
  const nodes = st.nodes || [];
  const px = st.proxmox;                         // Zuordnung zu einer Proxmox-VM/CT oder null
  const pxOk = !!(px && px.may_start);
  const defVia = pxOk && (cfg.via === "auto" || !cfg.via) ? "auto" : cfg.via;
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;width:460px;max-width:100%;font-size:13px">
    <b style="font-size:15px">⏻ Einschalten – ${esc(client.hostname || client.id)}</b>
    ${px ? `<div style="border:1px solid var(--accent);border-radius:8px;padding:8px 10px;margin:8px 0 10px;font-size:12.5px">
      🖧 Läuft als <b>${px.type === "qemu" ? "VM" : "CT"} ${esc(String(px.vmid))}</b>${px.name ? ` „${esc(px.name)}“` : ""} auf <b>${esc(px.host_name)}</b> (Node ${esc(px.node)}).
      ${pxOk ? "Wird direkt über Proxmox gestartet – Wake-on-LAN ist dafür nicht nötig."
             : `<span style="color:var(--warning,#d97706)">Zum Starten über Proxmox fehlt dir das Recht „Gäste ein-/ausschalten“ am Host – es bleibt Wake-on-LAN.</span>`}</div>` : ""}
    <div style="color:var(--subtext);font-size:12px;margin:4px 0 12px">Wake-on-LAN sendet ein „Magic Packet“. Funktioniert nur, wenn das Gerät WOL unterstützt und es im BIOS/der Netzwerkkarte aktiviert ist.</div>
    <label style="display:flex;gap:7px;align-items:center;margin-bottom:10px;cursor:pointer">
      <input type="checkbox" data-enabled ${cfg.enabled ? "checked" : ""}> Wake-on-LAN für dieses Gerät erlauben</label>
    <div style="font-size:12px;color:var(--subtext);margin-bottom:3px">Erkannte MAC-Adressen</div>
    <div style="font-size:12.5px;margin-bottom:8px">${st.macs.length ? st.macs.map((m) => `<code>${esc(m)}</code>`).join(" ") : "<span style='color:var(--warning,#d97706)'>keine automatisch erkannt</span>"}</div>
    <div style="font-size:12px;color:var(--subtext);margin-bottom:3px">Zusätzliche MAC(s), kommagetrennt</div>
    <input data-macs value="${esc(cfg.macs.join(", "))}" placeholder="z. B. AA:BB:CC:11:22:33" style="width:100%;margin-bottom:10px">
    <div style="display:flex;gap:8px;margin-bottom:10px">
      <div style="flex:1"><div style="font-size:12px;color:var(--subtext);margin-bottom:3px">Senden über</div>
        <select data-via style="width:100%">
          <option value="auto" ${defVia === "auto" ? "selected" : ""}>${pxOk ? "Automatisch (Proxmox, dann Node, dann Backend)" : "Automatisch (Node bevorzugt)"}</option>
          ${pxOk ? `<option value="proxmox" ${defVia === "proxmox" ? "selected" : ""}>Proxmox (VM/CT starten)</option>` : ""}
          <option value="backend" ${cfg.via === "backend" ? "selected" : ""}>RMM-Server (Backend)</option>
          ${nodes.map((n) => `<option value="${esc(n.id)}" ${cfg.via === n.id ? "selected" : ""} ${n.active ? "" : "disabled"}>🖧 Node: ${esc(n.name)}${nodeHint(n)}</option>`).join("")}
        </select></div>
      <div style="width:110px"><div style="font-size:12px;color:var(--subtext);margin-bottom:3px">Port</div>
        <input data-port type="number" value="${cfg.port}" style="width:100%"></div>
    </div>
    <div style="font-size:12px;color:var(--subtext);margin-bottom:3px">Broadcast-Adresse (optional)</div>
    <input data-broadcast value="${esc(cfg.broadcast || "")}" placeholder="automatisch (z. B. 192.168.1.255)" style="width:100%;margin-bottom:6px">
    <div data-msg style="font-size:12px;margin:8px 0;min-height:16px"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn" data-x>Schließen</button>
      <button class="btn" data-save>Speichern</button>
      <button class="btn btn-primary" data-wake>⏻ ${pxOk ? "Jetzt starten" : "Jetzt wecken"}</button></div></div>`;
  document.body.appendChild(ov);
  const $ = (s) => ov.querySelector(s);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  $("[data-x]").addEventListener("click", () => ov.remove());
  const readCfg = () => ({
    macs: $("[data-macs]").value.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean),
    via: $("[data-via]").value, port: parseInt($("[data-port]").value, 10) || 9,
    broadcast: $("[data-broadcast]").value.trim(), enabled: $("[data-enabled]").checked,
  });
  const save = async () => { await wq(`/${encodeURIComponent(client.id)}`, { method: "PUT", body: readCfg() }); };
  $("[data-save]").addEventListener("click", async () => {
    try { await save(); window.notify?.("Gespeichert", "success", 1500); } catch (e) { $("[data-msg]").innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; }
  });
  $("[data-wake]").addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true;
    $("[data-msg]").textContent = "⏳ sende Magic Packet …";
    try {
      await save();                                         // Konfig zuerst sichern
      const r = await wq(`/${encodeURIComponent(client.id)}/wake`, { method: "POST", body: { via: $("[data-via]").value } });
      $("[data-msg]").innerHTML = r.via === "proxmox"
        ? `<span style="color:var(--online,#16a34a)">✓ ${esc(r.detail || "Über Proxmox gestartet")}. Der Agent meldet sich in Kürze.</span>`
        : `<span style="color:var(--online,#16a34a)">✓ Gesendet über ${esc(r.via)} (${r.ok_count} Paket(e)). Der PC sollte in Kürze starten.</span>`;
      window.notify?.(r.via === "proxmox" ? `${client.hostname || client.id} wird über Proxmox gestartet`
        : `WOL gesendet an ${client.hostname || client.id}`, "success", 4000);
    } catch (err) {
      $("[data-msg]").innerHTML = `<span style="color:var(--danger)">${esc(err.message)}</span>`;
    }
    b.disabled = false;
  });
}

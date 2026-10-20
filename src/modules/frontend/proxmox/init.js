// init.js (Modul "proxmox") - Knopf im Agent-Panel + Vorschlaege
//   Gast (Agent laeuft in einer Proxmox-VM/CT und ist zugeordnet)  -> "🖧 In Proxmox öffnen" (Dashboard beim Gast)
//   physisches Geraet mit Proxmox + Recht "Dashboard sehen"           -> "🖧 Proxmox"
//   physisches Geraet ohne Proxmox + Recht "Proxmox verwalten"        -> "🖧 Proxmox verbinden"
//   VM/CT ohne Zuordnung                                               -> kein Knopf (Proxmox laeuft auf Hosts)
// Vorschlaege (Agenten, die laut Proxmox in VMs/CTs laufen) erscheinen als Hinweis unten rechts.
import { registerClientAction, registerModuleApi } from "/js/app/modules.js";
import { renderMainContent } from "/js/app/panel.js";
import { hasClientPerm, state } from "/js/app/state.js";
import { openWindow } from "/js/app/windowmanager.js";
import { preq } from "./common.js";
import { openConnectionDialog } from "./connect.js";
import "./console.js";      // Konsolen-Knoepfe in der Gast-Ansicht (Event "rmm-pve-guest")
import { maybeShowBanner, openSuggestDialog } from "./suggest.js";

let connected = new Set();
let guestMap = {};
let suggestions = [];

async function refresh() {
  try { connected = new Set((await preq("/clients")).clients.map((c) => c.client_id)); } catch { /* Modul aus / kein Zugriff */ }
  try {
    const g = await preq("/guest-map");
    guestMap = g.map || {};
    suggestions = g.suggestions || [];
    maybeShowBanner(suggestions, refresh);
  } catch { /* egal */ }
}

export function openDashboard(client, guest = null) {
  openWindow({ key: `proxmox-${client.id}`, appId: "proxmox", title: `Proxmox — ${client.hostname || ""}`,
               props: { clientId: client.id, guest }, w: 1000, h: 680, clientColor: client.color });
}

function hostOf(entry) {
  return (state.clients || []).find((c) => c.id === entry.host) || { id: entry.host, hostname: entry.host_name };
}

export function init() {
  refresh();
  setInterval(refresh, 60000);
  window.addEventListener("rmm-client-updated", (e) => {
    const c = (state.clients || []).find((x) => x.id === e.detail?.id);
    if (c) Object.assign(c, e.detail);
    refresh().then(() => { try { renderMainContent(); } catch { /* egal */ } });
  });
  registerModuleApi("proxmox", { openDashboard, refresh, isConnected: (id) => connected.has(id),
                                 guestOf: (id) => guestMap[id] || null,
                                 openSuggestions: () => openSuggestDialog(suggestions, refresh) });
  registerClientAction("proxmox", {
    label(client) {
      const g = guestMap[client.id];
      if (g && g.can_open) return "🖧 In Proxmox öffnen";
      if ((client.device_type || "physical") !== "physical") return null;
      if (connected.has(client.id) && hasClientPerm(client.id, "pve_access")) return "🖧 Proxmox";
      if (!connected.has(client.id) && hasClientPerm(client.id, "pve_manage")) return "🖧 Proxmox verbinden";
      return null;
    },
    onClick(client) {
      const g = guestMap[client.id];
      if (g && g.can_open) return openDashboard(hostOf(g), { node: g.node, type: g.type, vmid: g.vmid, name: g.name });
      if (connected.has(client.id)) return openDashboard(client);
      openConnectionDialog(client, async (changed) => {
        if (!changed) return;
        await refresh();
        if (connected.has(client.id)) openDashboard(client);
      });
    },
  });
}

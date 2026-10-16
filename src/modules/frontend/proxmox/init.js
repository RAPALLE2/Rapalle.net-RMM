// init.js (Modul "proxmox") - Knopf im Agent-Panel
//   Agent mit Proxmox + Recht "Proxmox: Dashboard sehen"  -> "🖧 Proxmox" (Dashboard)
//   Agent ohne Proxmox + Recht "Proxmox verwalten"         -> "🖧 Proxmox verbinden" (Dialog)
import { registerClientAction, registerModuleApi } from "/js/app/modules.js";
import { hasClientPerm } from "/js/app/state.js";
import { openWindow } from "/js/app/windowmanager.js";
import { preq } from "./common.js";
import { openConnectionDialog } from "./connect.js";
import "./console.js";      // Konsolen-Knoepfe in der Gast-Ansicht (Event "rmm-pve-guest")

let connected = new Set();

async function refresh() {
  try { connected = new Set((await preq("/clients")).clients.map((c) => c.client_id)); }
  catch { /* Modul aus / kein Zugriff */ }
}

export function openDashboard(client) {
  openWindow({ key: `proxmox-${client.id}`, appId: "proxmox", title: `Proxmox — ${client.hostname || ""}`,
               props: { clientId: client.id }, w: 1000, h: 680, clientColor: client.color });
}

export function init() {
  refresh();
  setInterval(refresh, 60000);
  registerModuleApi("proxmox", { openDashboard, refresh, isConnected: (id) => connected.has(id) });
  registerClientAction("proxmox", {
    label(client) {
      if (connected.has(client.id) && hasClientPerm(client.id, "pve_access")) return "🖧 Proxmox";
      if (!connected.has(client.id) && hasClientPerm(client.id, "pve_manage")) return "🖧 Proxmox verbinden";
      return null;
    },
    onClick(client) {
      if (connected.has(client.id)) return openDashboard(client);
      openConnectionDialog(client, async (changed) => {
        if (!changed) return;
        await refresh();
        if (connected.has(client.id)) openDashboard(client);
      });
    },
  });
}

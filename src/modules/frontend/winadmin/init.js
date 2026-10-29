// init.js (Modul "winadmin") - Windows: Registry-Editor + Windows-Updates
// --------------------------------------------------------------------------
// Fügt bei WINDOWS-Maschinen zwei Panel-Aktionen hinzu. Beide öffnen ein
// ECHTES FENSTER (Fenstermanager: verschieben, minimieren, Taskleiste, mehrere
// Clients gleichzeitig) statt eines Popups:
//   🗝️ Registry          (Recht c_registry)               -> App "winadmin-registry"
//   ⬇️ Windows-Updates   (Recht c_win_update/c_patch_view) -> App "winadmin-updates"
//                         Automatik aus/ein + Patching des Clients (Modul patching)
// Die Fenster selbst stehen in windows.js.

import { registerClientAction } from "/js/app/modules.js";
import { hasClientPerm } from "/js/app/state.js";
import { openWindow } from "/js/app/windowmanager.js";

const isWin = (c) => (c.platform || "").toLowerCase().includes("win");

function open(appId, client, title, w, h) {
  openWindow({ key: `${appId}-${client.id}`, appId, title: `${title} – ${client.hostname || client.id}`,
               props: { clientId: client.id, clientName: client.hostname || client.id },
               w, h, clientColor: client.color });
}

export function init() {
  registerClientAction("winadmin", {
    label(client) {
      if (!isWin(client) || !client.online) return null;
      if (!hasClientPerm(client.id, "c_registry")) return null;
      return "🗝️ Registry";
    },
    onClick(client) { open("winadmin-registry", client, "🗝️ Registry", 860, 620); },
  });
  registerClientAction("winadmin", {
    label(client) {
      if (!isWin(client)) return null;
      if (!hasClientPerm(client.id, "c_win_update") && !hasClientPerm(client.id, "c_patch_view")) return null;
      return "⬇️ Windows-Updates";
    },
    onClick(client) { open("winadmin-updates", client, "⬇️ Windows-Updates", 820, 640); },
  });
}

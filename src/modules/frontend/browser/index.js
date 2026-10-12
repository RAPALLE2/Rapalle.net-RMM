// Browser-Modul: init-Skript. Wird einmal nach dem Laden des Moduls ausgefuehrt.
// Stellt dem Core die Funktionen bereit, die er braucht (Seitenleiste, Dashboard,
// Startmenue), und traegt gespeicherte Web-Apps ins Startmenue ein.

import { registerModuleApi } from "/js/app/modules.js";
import { addCatalogEntry } from "/js/app/startmenu.js";
import { openWindow } from "/js/app/windowmanager.js";
import { getWebApps, openWebsiteEntry } from "./webbrowser.js";

export function init() {
  registerModuleApi("browser", {
    openWebsiteEntry,
    getWebApps,
    // Gespeicherte Web-App ("webapp:<id>") aus dem Startmenue oeffnen.
    openWebApp(appKey) {
      const wa = getWebApps().find((a) => `webapp:${a.id}` === appKey);
      if (wa) openWindow({ key: appKey, appId: "webapp", title: `${wa.icon} ${wa.name}`,
                           props: { url: wa.url, name: wa.name, icon: wa.icon }, w: 1020, h: 720 });
    },
  });
  for (const wa of getWebApps()) addCatalogEntry(`webapp:${wa.id}`, wa.icon, wa.name);
}

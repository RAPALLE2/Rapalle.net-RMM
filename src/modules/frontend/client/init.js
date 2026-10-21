// init.js (Modul "client")
// - Profil-Bereich "Client-App & Geräte" (+ Einstellung: Fernzugriff extern / Browser / fragen)
// - in der Client-App: Terminal / Remote-Bildschirm des Agent-Panels extern öffnen (remote.js)
import { registerActionHandler, registerModuleApi } from "/js/app/modules.js";
import { renderProfileSection } from "./client.js";
import { actionHandler, clientAppKind, loadOpenMode, renderOpenModePref } from "./remote.js";

async function profileSection(host) {
  await renderProfileSection(host);
  const box = document.createElement("div");
  box.style.cssText = "margin:0 0 16px;padding-bottom:12px;border-bottom:1px solid var(--border)";
  host.prepend(box);
  renderOpenModePref(box);
}

export function init() {
  registerModuleApi("client", { renderProfileSection: profileSection });
  registerActionHandler("client", actionHandler);
  if (clientAppKind()) setTimeout(loadOpenMode, 1500);
}

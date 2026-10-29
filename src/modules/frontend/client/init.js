// init.js (Modul "client")
// - Profil-Bereich "Client-App & Geräte" (+ Einstellung: Fernzugriff extern / Browser / fragen)
// - in der Client-App: Terminal / Remote-Bildschirm des Agent-Panels extern öffnen (remote.js)
import { registerActionHandler, registerModuleApi, registerClientAction } from "/js/app/modules.js";
import { hasClientPerm } from "/js/app/state.js";
import { renderProfileSection } from "./client.js";
import { actionHandler, clientAppKind, hasPcApp, loadOpenMode, openInPcApp, openAppLink, renderOpenModePref } from "./remote.js";

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
  if (clientAppKind() || hasPcApp()) setTimeout(loadOpenMode, 1500);
  // Agent-Panel: RDP / VNC / SSH direkt mit der PC-App (auch aus dem normalen Browser).
  // Ist das Gerät nicht öffentlich erreichbar, bietet die Auswahl gleich „Über VPN“ an.
  registerClientAction("client", {
    label(client) {
      if (clientAppKind() === "android" || /Android|iPhone|iPad/i.test(navigator.userAgent)) return null;
      if (!["c_screen", "c_terminal_console"].some((p) => hasClientPerm(client.id, p))) return null;
      return "🖥 In PC-App öffnen (RDP/VNC/SSH)";
    },
    onClick(client) { openInPcApp(client); },
  });
  // fuer das VPN-Modul: Tunnel-Datei direkt in WireGuard aktivieren
  registerModuleApi("client", { renderProfileSection: profileSection, openAppLink });
}

// init.js (Modul "client") - stellt den Profil-Bereich bereit
import { registerModuleApi } from "/js/app/modules.js";
import { renderProfileSection } from "./client.js";

export function init() {
  registerModuleApi("client", { renderProfileSection });
}

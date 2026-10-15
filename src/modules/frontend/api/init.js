// init.js (Modul "api") - meldet das Modul an, damit z.B. die Benutzer-Vorlage
// "API-Benutzer" beim Anlegen eines Kontos angeboten wird (getModuleApi("api")).
import { registerModuleApi } from "/js/app/modules.js";

export function init() {
  registerModuleApi("api", { available: true });
}

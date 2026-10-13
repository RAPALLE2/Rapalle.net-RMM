// init.js (Modul "spaces") - läuft einmal pro Seitenaufruf
// --------------------------------------------------------
// Stellt die Spaces-Funktionen anderen Teilen zur Verfügung
// (getModuleApi("spaces")): Explorer-Register, Bilder in Chat/Tickets,
// Musik im Audioplayer. Öffnet außerdem Freigabe-Links (#space-share=<token>).

import { registerModuleApi } from "/js/app/modules.js";
import { renderSpacesBrowser, uploadAttachment, blobUrl, openShareLink, sreq } from "./spaces.js";

export function init() {
  registerModuleApi("spaces", { renderSpacesBrowser, uploadAttachment, blobUrl, openShareLink, request: sreq });
  const check = () => {
    const m = /space-share=([A-Za-z0-9_-]+)/.exec(location.hash || "");
    if (!m) return;
    history.replaceState(null, "", location.pathname + location.search);
    openShareLink(m[1]);
  };
  check();
  window.addEventListener("hashchange", check);
}

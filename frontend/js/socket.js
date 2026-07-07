// socket.js
// ---------
// Verbindet sich mit dem "/dashboard" Namespace des Backends, um Live-Updates
// zu bekommen (Client online/offline, neue Metriken), ohne ständig die REST-API
// abzufragen ("Polling"). Die Bibliothek "io()" kommt aus dem <script>-Tag in
// index.html (CDN), ist also schon global verfügbar, wenn diese Datei läuft.

import { BACKEND_URL } from "./config.js";

export const dashboardSocket = io(`${BACKEND_URL}/dashboard`, {
  autoConnect: true,
  reconnection: true,
});

// --- Auto-Reload nach Backend-Neustart ---------------------------------
// Das Backend vergibt pro Prozessstart eine zufällige BOOT_ID (/api/boot-id).
// Wir merken uns die erste gesehene ID und laden die Seite neu, sobald sie
// sich ändert - dann wurde das Backend neu gestartet (neuer Code/Deploy).
// Gepollt wird nur, wenn der Tab sichtbar ist (schont Ressourcen).
let _knownBootId = null;

async function _checkBootId() {
  if (document.hidden) return;
  try {
    const res = await fetch(`${BACKEND_URL}/api/boot-id`, { cache: "no-store" });
    if (!res.ok) return;
    const { boot_id } = await res.json();
    if (!boot_id) return;
    if (_knownBootId === null) {
      _knownBootId = boot_id; // erste Messung: nur merken
    } else if (boot_id !== _knownBootId) {
      console.info("[boot] Backend neu gestartet - lade Webconsole neu.");
      location.reload();
    }
  } catch {
    // Backend gerade offline (mitten im Neustart) - beim nächsten Poll klappt's.
  }
}

setInterval(_checkBootId, 5000);
// Beim Wiedersichtbarwerden sofort prüfen (Restart während Tab im Hintergrund).
document.addEventListener("visibilitychange", () => { if (!document.hidden) _checkBootId(); });
_checkBootId();

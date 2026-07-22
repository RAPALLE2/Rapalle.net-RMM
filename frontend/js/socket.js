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
// Das Backend schickt beim VERBINDEN seine BOOT_ID (pro Prozessstart zufällig)
// über den Socket ("boot:id"). Wir merken uns die erste ID; kommt nach einem
// automatischen Reconnect eine ANDERE ID, wurde das Backend neu gestartet
// (neuer Code/Deploy) -> Seite neu laden. Kein Polling mehr nötig.
let _knownBootId = null;

// --- Server-Benachrichtigungen (z.B. Uptime-Monitor) ---------------------
// Das Backend kann jederzeit eine "normale" In-App-Notification an alle
// Dashboards schicken. Wird als Toast über notify.js angezeigt.
dashboardSocket.on("notify", ({ message, level }) => {
  // source "webhook": diese Meldungen kommen vom Server (Uptime-Monitor,
  // Webhook-Ereignisse) und werden in der Benachrichtigungs-Zentrale
  // entsprechend gekennzeichnet.
  if (window.notify && message) window.notify(message, level || "info", 8000, { source: "webhook" });
});

dashboardSocket.on("boot:id", ({ boot_id }) => {
  if (!boot_id) return;
  if (_knownBootId === null) {
    _knownBootId = boot_id;             // erste Verbindung: nur merken
  } else if (boot_id !== _knownBootId) {
    console.info("[boot] Backend neu gestartet - lade Webconsole neu.");
    location.reload();
  }
});

// --- Relay automatisch geschlossen (Ablaufzeit erreicht) -----------------
// Schließt das Backend einen Explorer-Relay automatisch (relay_expires_at),
// schickt es ein "relay-changed"-Event. Wir spiegeln das auf das gleiche
// Fenster-Event, auf das die Relay-App und der Relay-Tab im Datei-Explorer
// ohnehin schon hören - so aktualisieren sich beide Ansichten sofort.
dashboardSocket.on("relay-changed", (detail = {}) => {
  try {
    window.dispatchEvent(new CustomEvent("relay-changed", { detail }));
  } catch {}
  if (detail && detail.auto && window.notify) {
    window.notify("Ein Explorer-Relay wurde automatisch geschlossen (Zeit abgelaufen).",
      "info", 6000, { source: "webhook" });
  }
});

// --- Silent-Modus verbraucht -------------------------------------------
// Der einmalige Remote-Bildschirm-Silent-Modus wurde beim Sitzungsstart
// genutzt und serverseitig wieder deaktiviert. Aufs Fenster-Event spiegeln,
// damit der Toggle im Profil (falls offen) sofort wieder AUS geht.
dashboardSocket.on("silent-screen:consumed", (detail = {}) => {
  try {
    window.dispatchEvent(new CustomEvent("silent-screen-consumed", { detail }));
  } catch {}
});

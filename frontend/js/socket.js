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

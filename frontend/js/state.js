// state.js
// --------
// Statt eines großen Frontend-Frameworks (React/Vue/...) benutzen wir hier
// ein simples, zentrales JavaScript-Objekt als "Datenspeicher" der App.
// Andere Module lesen/ändern diesen Zustand und rufen danach die render()
// Funktionen der betroffenen Bereiche auf. Das ist bewusst einfach gehalten,
// damit man als Python-Entwickler ohne JS-Framework-Erfahrung genau sieht,
// wo welche Daten herkommen.

export const state = {
  user: null,              // aktuell eingeloggter Benutzer ({username, role, ...})
  hierarchy: { tenants: [], locations: [], folders: [] },
  clients: [],             // alle Clients (aus /api/clients)
  selection: null,         // aktuell in der Sidebar ausgewählt: {type: "tenant"|"location"|"client", id}
  windows: [],             // aktuell offene Fenster: {key, appId, title, x, y, w, h, minimized, props}
  focusOrder: [],          // Reihenfolge der Fenster nach zuletzt fokussiert (letztes = oberstes)
};

export function findClient(id) {
  return state.clients.find((c) => c.id === id);
}

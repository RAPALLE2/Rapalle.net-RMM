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
  perms: null,             // effektive Rechte: {admin, global:{perm:bool}, clients:{id:{perm:bool}}}
  hierarchy: { tenants: [], locations: [], folders: [] },
  clients: [],             // alle Clients (aus /api/clients)
  selection: null,         // aktuell in der Sidebar ausgewählt: {type: "tenant"|"location"|"client", id}
  windows: [],             // aktuell offene Fenster: {key, appId, title, x, y, w, h, minimized, props}
  focusOrder: [],          // Reihenfolge der Fenster nach zuletzt fokussiert (letztes = oberstes)
  sidebar: { width: 260, collapsed: false },  // Breite + Ein-/Ausklapp-Zustand (persistiert)
};

export function findClient(id) {
  return state.clients.find((c) => c.id === id);
}

// -----------------------------------------------------------------
// Rechte-Helfer fürs Frontend-Gating. Quelle: /api/auth/effective.
// WICHTIG: reine UI-Bequemlichkeit - die harte Prüfung macht das Backend.
// -----------------------------------------------------------------
export function isAdmin() {
  return !!(state.perms && state.perms.admin);
}

// Globales Recht (z.B. "see_audit", "manage_users", "login").
export function hasGlobalPerm(perm) {
  if (isAdmin()) return true;
  return !!(state.perms && state.perms.global && state.perms.global[perm]);
}

// Client-bezogenes Recht. Fehlt der Client in perms.clients -> nicht sichtbar.
export function hasClientPerm(clientId, perm) {
  if (isAdmin()) return true;
  const c = state.perms && state.perms.clients && state.perms.clients[clientId];
  return !!(c && c[perm]);
}

// Ist der Client für den Benutzer überhaupt sichtbar?
export function clientVisible(clientId) {
  if (isAdmin()) return true;
  return !!(state.perms && state.perms.clients && state.perms.clients[clientId]);
}

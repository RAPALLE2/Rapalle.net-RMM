// api.js
// ------
// Kleiner Helfer für alle Aufrufe an das Backend. Bündelt zwei Dinge, die
// sonst in jeder einzelnen fetch()-Aufruf wiederholt werden müssten:
//   1. Automatisch den Login-Token (JWT) aus dem localStorage mitschicken
//   2. Fehler einheitlich behandeln (Backend liefert {"detail": "..."} bei Fehlern)

import { BACKEND_URL } from "./config.js";
import { t } from "./i18n.js";

export function getToken() {
  return localStorage.getItem("rmm_token");
}

// Ein 403 vom Server bedeutet fast immer "Recht fehlt". Wir versuchen aus der
// Meldung den technischen Rechte-Schlüssel und daraus eine verständliche
// Beschreibung zu gewinnen und zeigen dann den Dialog mit Ticket-Angebot.
//
// Kein harter Import von permdenied.js: api.js wird von sehr vielen Modulen
// geladen, ein Ringschluss wäre vorprogrammiert. Deshalb das globale Fenster.
let _lastPermDialog = 0;
function _permissionDialog(message, path) {
  try {
    const show = window.showPermissionDenied;
    if (typeof show !== "function") return;
    // Nur bei Meldungen, die wirklich nach fehlendem Recht klingen. Ein 403
    // kann auch andere Gründe haben (abgelaufene Freigabe, Sperre) - da wäre
    // ein Ticket-Angebot verwirrend.
    const looksLikePerm = /Fehlendes Recht|nicht erlaubt|Kein Zugriff|Nur für Administratoren/i
      .test(message || "");
    if (!looksLikePerm) return;
    // Endpunkte, die im Hintergrund dauernd laufen, sollen keine Fenster
    // aufpoppen - sonst kommt der Dialog beim Laden des Dashboards ungefragt.
    if (/\/api\/auth\/(effective|me)|\/api\/notify|\/api\/clients$/.test(path || "")) return;
    // Bei mehreren parallelen Aufrufen nicht mehrere Fenster stapeln.
    const now = Date.now();
    if (now - _lastPermDialog < 8000) return;
    _lastPermDialog = now;

    const m = /Fehlendes Recht:\s*([\w:.\-]+)/i.exec(message || "");
    const perm = m ? m[1] : null;
    // Client-ID aus dem Pfad ziehen, wenn eine drinsteht.
    const cm = /\/clients?\/([0-9a-f-]{8,})/i.exec(path || "");
    show({
      action: _actionFromPerm(perm) || message || "Diese Aktion",
      perm,
      clientId: cm ? cm[1] : null,
      detail: perm ? "" : message,
    });
  } catch { /* Dialog ist Komfort - niemals den Fehler verschlucken lassen */ }
}

// Technische Rechte-Schlüssel in Klartext. Unbekannte Schlüssel werden
// unverändert durchgereicht - besser der Schlüssel als gar nichts.
const _PERM_TEXT = {
  admin_settings: "Server-Einstellungen ändern",
  manage_settings: "Einstellungen ändern",
  see_source: "Quellcode ansehen",
  edit_source: "Quellcode bearbeiten",
  see_audit: "Audit-Log ansehen",
  see_permissions: "Berechtigungen ansehen",
  manage_permissions: "Berechtigungen ändern",
  create_users: "Benutzer anlegen",
  manage_clients: "Client bearbeiten",
  manage_agent: "Agent aktualisieren",
  access_clients: "auf diesen Client zugreifen",
  c_terminal: "Terminal öffnen",
  c_screen: "Remote-Bildschirm starten",
  c_explorer_view: "Dateien ansehen",
  c_explorer_edit: "Dateien ändern",
  c_taskmanager_view: "Task-Manager öffnen",
  c_taskmanager_kill: "Prozesse beenden",
  c_power: "Client herunterfahren oder neu starten",
  c_relay: "Relay freigeben",
  c_delete: "Client löschen",
  use_relay: "das Relay benutzen",
  ticket_create: "ein Ticket erstellen",
  manage_privacy: "Datenschutz-Vorgänge bearbeiten",
  manage_patching: "Updates verwalten",
  customize_dashboard: "das Dashboard anpassen",
};

function _actionFromPerm(perm) {
  if (!perm) return null;
  return _PERM_TEXT[perm] || perm;
}

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BACKEND_URL}${path}`, { ...options, headers });

  if (!res.ok) {
    let message = `Fehler ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail || message;
    } catch { /* Antwort war kein JSON, egal */ }
    // Fehlt ein Recht, soll der Benutzer nicht nur eine rote Zeile sehen,
    // sondern gleich die Möglichkeit bekommen, sich beim Support zu melden.
    // Deshalb hier zentral abgefangen - so gilt es für JEDEN Aufruf, ohne
    // dass jede App das selbst einbauen muss.
    if (res.status === 403) _permissionDialog(message, path);
    throw new Error(message);
  }

  // Manche Endpunkte (z.B. Skript-Downloads) liefern kein JSON zurück
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return res.json();
  return res.text();
}

export const api = {
  // --- Screen-Recording (Guacamole 1:1-Video) ---
  uploadRecordingVideo: async (clientId, hostname, startedAt, endedAt, blob) => {
    const q = new URLSearchParams({ client_id: clientId, hostname: hostname || "", started_at: String(startedAt), ended_at: String(endedAt) });
    const token = getToken();
    const res = await fetch(`${BACKEND_URL}/api/recordings/upload?${q.toString()}`, {
      method: "POST",
      headers: { "Content-Type": "video/webm", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: blob,
    });
    if (!res.ok) throw new Error(`Upload fehlgeschlagen (${res.status})`);
    return res.json();
  },
  getRecordingVideoBlob: async (recId) => {
    const token = getToken();
    const res = await fetch(`${BACKEND_URL}/api/recordings/${recId}/video`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(`Video laden fehlgeschlagen (${res.status})`);
    return res.blob();
  },
  // --- Auth ---
  login: (username, password, realm = "local", code = "") =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password, realm, code }) }),
  // --- Zwei-Faktor-Anmeldung (TOTP) ---
  totpStatus: () => request("/api/auth/2fa/status"),
  totpSetup: () => request("/api/auth/2fa/setup", { method: "POST" }),
  totpActivate: (code) => request("/api/auth/2fa/activate", { method: "POST", body: JSON.stringify({ code }) }),
  // Bestaetigung wahlweise per Passwort ODER per Code aus der App - SSO-Konten
  // haben lokal gar kein Passwort.
  totpDisable: (password, code = "") => request("/api/auth/2fa/disable", { method: "POST", body: JSON.stringify({ password, code }) }),
  totpNewBackupCodes: (password, code = "") => request("/api/auth/2fa/backup-codes", { method: "POST", body: JSON.stringify({ password, code }) }),
  getLoginRealms: () => request("/api/auth/realms"),
  me: () => request("/api/auth/me"),
  // Serverseitig gespeicherte UI-Einstellungen (in jedem Browser gleich)
  getUiPrefs: () => request("/api/auth/ui-prefs"),
  saveUiPrefs: (keys) => request("/api/auth/ui-prefs", {
    method: "PUT", body: JSON.stringify({ keys }) }),
  // Teil-Update (Merge): nur die uebergebenen Schluessel aendern. Wird vom
  // Frontend fuer JEDEN Sync benutzt, damit ein leerer Browser-Cache niemals
  // den Server-Stand loeschen kann.
  mergeUiPrefs: (keys) => request("/api/auth/ui-prefs", {
    method: "PATCH", body: JSON.stringify({ keys }) }),
  // Einmaliger Silent-Modus für den Remote-Bildschirm
  getSilentScreen: () => request("/api/auth/silent-screen"),
  setSilentScreen: (enabled) => request("/api/auth/silent-screen", {
    method: "PUT", body: JSON.stringify({ enabled: !!enabled }) }),

  // ---- Spotify ----
  getSpotifyConfig: () => request("/api/auth/spotify-config"),

  // ---- AI-Chat ----
  aiConnections: () => request("/api/ai/connections"),
  aiCreateConnection: (body) => request("/api/ai/connections", {
    method: "POST", body: JSON.stringify(body) }),
  aiUpdateConnection: (id, body) => request(`/api/ai/connections/${id}`, {
    method: "PUT", body: JSON.stringify(body) }),
  aiDeleteConnection: (id) => request(`/api/ai/connections/${id}`, { method: "DELETE" }),
  aiShareSubjects: () => request("/api/ai/share-subjects"),
  aiChat: (connectionId, messages) => request("/api/ai/chat", {
    method: "POST", body: JSON.stringify({ connection_id: connectionId, messages }) }),

  // ---- Gaming-Hub-Scoreboard ----
  gameScores: () => request("/api/games/scores"),
  submitGameScore: (game, score) => request("/api/games/scores", {
    method: "POST", body: JSON.stringify({ game, score }) }),

  // ---- Tickets ----
  tickets: () => request("/api/tickets"),
  ticket: (id) => request(`/api/tickets/${id}`),
  createTicket: (body) => request("/api/tickets", {
    method: "POST", body: JSON.stringify(body) }),
  updateTicket: (id, body) => request(`/api/tickets/${id}`, {
    method: "PUT", body: JSON.stringify(body) }),
  deleteTicket: (id) => request(`/api/tickets/${id}`, { method: "DELETE" }),
  setTicketAssignees: (id, assignees) => request(`/api/tickets/${id}/assignees`, {
    method: "PUT", body: JSON.stringify({ assignees }) }),
  setTicketStatus: (id, status) => request(`/api/tickets/${id}/status`, {
    method: "PUT", body: JSON.stringify({ status }) }),
  addTicketComment: (id, text, visibility = "all", sharedWith = []) =>
    request(`/api/tickets/${id}/comments`, {
      method: "POST",
      body: JSON.stringify({ text, visibility, shared_with: sharedWith }) }),
  ticketSubjects: () => request("/api/tickets/meta/subjects"),
  // Datei-Upload als roher Body (kein multipart) - Dateiname als Query-Param.
  uploadTicketFile: async (id, file) => {
    const token = localStorage.getItem("rmm_token");
    const res = await fetch(`/api/tickets/${id}/files?filename=${encodeURIComponent(file.name)}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Upload fehlgeschlagen (${res.status})`);
    return res.json();
  },
  // Anhang mit Auth-Header laden und als Blob-URL zurückgeben (für <img>/Download).
  fetchTicketFile: async (id, fileId) => {
    const token = localStorage.getItem("rmm_token");
    const res = await fetch(`/api/tickets/${id}/files/${fileId}`, {
      headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(`Download fehlgeschlagen (${res.status})`);
    return URL.createObjectURL(await res.blob());
  },
  deleteTicketFile: (id, fileId) => request(`/api/tickets/${id}/files/${fileId}`, { method: "DELETE" }),
  changePassword: (current_password, new_password) =>
    request("/api/auth/change-password", { method: "POST", body: JSON.stringify({ current_password, new_password }) }),
  updateProfile: (data) => request("/api/auth/profile", { method: "PUT", body: JSON.stringify(data) }),

  // --- Hierarchie ---
  getHierarchy: () => request("/api/hierarchy"),
  createTenant: (name, color) => request("/api/tenants", { method: "POST", body: JSON.stringify({ name, color }) }),
  createLocation: (tenant_id, name) => request("/api/locations", { method: "POST", body: JSON.stringify({ tenant_id, name }) }),
  deleteTenant: (tenant_id) => request(`/api/tenants/${tenant_id}`, { method: "DELETE" }),
  deleteLocation: (location_id) => request(`/api/locations/${location_id}`, { method: "DELETE" }),
  createFolder: (location_id, name, parent_folder_id = null) =>
    request("/api/folders", { method: "POST", body: JSON.stringify({ location_id, name, parent_folder_id }) }),
  deleteFolder: (folder_id) => request(`/api/folders/${folder_id}`, { method: "DELETE" }),

  // --- Clients ---
  getClients: () => request("/api/clients"),
  getClient: (id) => request(`/api/clients/${id}`),
  getMetricsHistory: (id) => request(`/api/clients/${id}/metrics/history`),
  updateClient: (id, fields) => request(`/api/clients/${id}`, { method: "PUT", body: JSON.stringify(fields) }),
  deleteClient: (id) => request(`/api/clients/${id}`, { method: "DELETE" }),
  execOnClient: (id, command, session, shell = "auto", elevated = false) =>
    request(`/api/clients/${id}/exec`, { method: "POST", body: JSON.stringify({ command, session, shell, elevated }) }),
  bulkExec: (client_ids, command) => request("/api/clients/bulk-exec", { method: "POST", body: JSON.stringify({ client_ids, command }) }),
  listClientFs: (id, path) => request(`/api/clients/${id}/fs?path=${encodeURIComponent(path)}`),
  listProcesses: (id) => request(`/api/clients/${id}/processes`),
  killProcess: (id, pid) => request(`/api/clients/${id}/processes/kill`, { method: "POST", body: JSON.stringify({ pid }) }),
  readClientFile: (id, path) => request(`/api/clients/${id}/fs/read?path=${encodeURIComponent(path)}`),
  writeClientFile: (id, path, data) => request(`/api/clients/${id}/fs/write`, { method: "POST", body: JSON.stringify({ path, data }) }),
  mkdirClient: (id, path) => request(`/api/clients/${id}/fs/mkdir`, { method: "POST", body: JSON.stringify({ path }) }),
  deleteClientPath: (id, path) => request(`/api/clients/${id}/fs/delete`, { method: "POST", body: JSON.stringify({ path }) }),
  renameClientPath: (id, src, dst) => request(`/api/clients/${id}/fs/rename`, { method: "POST", body: JSON.stringify({ src, dst }) }),

  // --- Server-Dateisystem: lesen/schreiben ---
  readServerFile: (path) => request(`/api/server-files/read?path=${encodeURIComponent(path)}`),
  writeServerFile: (path, data) => request(`/api/server-files/write`, { method: "POST", body: JSON.stringify({ path, data }) }),
  mkdirServer: (path) => request(`/api/server-files/mkdir`, { method: "POST", body: JSON.stringify({ path }) }),
  deleteServerPath: (path) => request(`/api/server-files/delete`, { method: "POST", body: JSON.stringify({ path }) }),
  renameServerPath: (src, dst) => request(`/api/server-files/rename`, { method: "POST", body: JSON.stringify({ src, dst }) }),

  // --- Client-Websites (Quick Access / Favoriten / Uptime-Monitoring) ---
  getClientWebsites: (id) => request(`/api/clients/${id}/websites`),
  createClientWebsite: (id, data) => request(`/api/clients/${id}/websites`, { method: "POST", body: JSON.stringify(data) }),
  updateClientWebsite: (id, websiteId, data) => request(`/api/clients/${id}/websites/${websiteId}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteClientWebsite: (id, websiteId) => request(`/api/clients/${id}/websites/${websiteId}`, { method: "DELETE" }),
  getServerAddress: () => request("/api/server-address"),
  getRelayStatus: (clientId) => request(`/api/relay/status?client_id=${encodeURIComponent(clientId)}`),
  toggleRelay: (clientId, autoCloseMinutes = 0) => request(`/api/relay/toggle?client_id=${encodeURIComponent(clientId)}${autoCloseMinutes ? `&auto_close_minutes=${encodeURIComponent(autoCloseMinutes)}` : ""}`, { method: "POST" }),
  getFavoriteWebsites: () => request("/api/clients/websites/favorites"),

  // --- Server-Dateisystem (Backend-Rechner selbst) ---
  listServerFs: (path) => request(`/api/server-files?path=${encodeURIComponent(path)}`),

  // --- Netzwerk-Scan ---
  // --- Medien-Bibliothek (Audio-Player / Media-Hub) ---
  getMedia: (kind) => request(`/api/media${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`),
  addMedia: (data) => request("/api/media", { method: "POST", body: JSON.stringify(data) }),
  updateMedia: (id, data) => request(`/api/media/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteMedia: (id) => request(`/api/media/${id}`, { method: "DELETE" }),
  // Favoriten (Stern) - darf jeder setzen, der den Eintrag sehen darf
  toggleMediaFavorite: (id) => request(`/api/media/${id}/favorite`, { method: "POST" }),
  getFavoriteMedia: () => request("/api/media?favorite=true"),
  // Wiedergabelisten (Radio, lokale Dateien, Links - auch gemischt)
  getPlaylists: (favOnly = false) => request(`/api/media/playlists${favOnly ? "?favorite=true" : ""}`),
  createPlaylist: (data) => request("/api/media/playlists", { method: "POST", body: JSON.stringify(data) }),
  updatePlaylist: (id, data) => request(`/api/media/playlists/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  setPlaylistItems: (id, item_ids) => request(`/api/media/playlists/${id}/items`, { method: "POST", body: JSON.stringify({ item_ids }) }),
  deletePlaylist: (id) => request(`/api/media/playlists/${id}`, { method: "DELETE" }),
  mediaFileUrl: (id) => `/api/media/${id}/file?token=${encodeURIComponent(localStorage.getItem("rmm_token") || "")}`,
  uploadMedia: async (file, shared = false, onProgress) => {
    const token = localStorage.getItem("rmm_token");
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/media/upload?filename=${encodeURIComponent(file.name)}&shared=${shared}`);
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch {}
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(data?.detail || `Upload fehlgeschlagen (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("Upload fehlgeschlagen (Netzwerk)"));
      xhr.send(file);
    });
  },

  // --- Client-Notizen (Sichtbarkeit + Aktivitätsprotokoll) ---
  getNotes: (clientId) => request(`/api/clients/${clientId}/notes`),
  getNotesActivity: (clientId) => request(`/api/clients/${clientId}/notes/activity`),
  getNotesUsers: (clientId) => request(`/api/clients/${clientId}/notes/users`),
  createNote: (clientId, data) => request(`/api/clients/${clientId}/notes`, { method: "POST", body: JSON.stringify(data) }),
  updateNote: (clientId, noteId, data) => request(`/api/clients/${clientId}/notes/${noteId}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteNote: (clientId, noteId) => request(`/api/clients/${clientId}/notes/${noteId}`, { method: "DELETE" }),

  // --- Tickets: Protokoll, Kommentar-Sichtbarkeit ---
  getTicketActivity: (ticketId) => request(`/api/tickets/${ticketId}/activity`),
  getTicketUsers: () => request("/api/tickets/meta/users"),
  deleteTicketComment: (ticketId, commentId) => request(`/api/tickets/${ticketId}/comments/${commentId}`, { method: "DELETE" }),

  // --- Organigramm (Über-/Unterstellung, Arbeitsbereiche) ---
  getOrgTree: () => request("/api/org/tree"),
  setOrgParent: (data) => request("/api/org/parent", { method: "PUT", body: JSON.stringify(data) }),
  setWorkspace: (userId, workspace) => request(`/api/org/users/${userId}/workspace`, {
    method: "PUT", body: JSON.stringify({ workspace }) }),
  getWorkspaces: () => request("/api/org/workspaces"),

  // --- Kalender ---
  getEvents: (start, end) => request(`/api/calendar/events?start=${start}&end=${end}`),
  getEventTargets: () => request("/api/calendar/targets"),
  createEvent: (data) => request("/api/calendar/events", { method: "POST", body: JSON.stringify(data) }),
  updateEvent: (id, data) => request(`/api/calendar/events/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteEvent: (id) => request(`/api/calendar/events/${id}`, { method: "DELETE" }),

  // --- Software-Patching ---
  getPatchOverview: () => request("/api/patches/overview"),
  getClientPatches: (id, status) => request(
    `/api/patches/client/${id}${status ? `?status=${status}` : ""}`),
  scanPatches: (id) => request(`/api/patches/client/${id}/scan`, { method: "POST" }),
  // Suche und Installation laufen im Hintergrund weiter; hier wird der
  // Fortschritt geholt. Wichtig hinter Reverse Proxies: eine synchrone
  // Antwort würde dort in einen Gateway-Timeout laufen (Cloudflare 524).
  getPatchJob: (id) => request(`/api/patches/client/${id}/job`),
  getPatchReadiness: (id) => request(`/api/patches/client/${id}/readiness`),
  // Kurzer Funktionstest auf dem Client (Sekunden, kein voller Scan).
  patchSelftest: (id) => request(`/api/patches/client/${id}/selftest`, { method: "POST" }),
  applyPatches: (id, items) => request(`/api/patches/client/${id}/apply`, {
    method: "POST", body: JSON.stringify({ items: items || [] }) }),
  excludePatch: (patchId, excluded) => request(`/api/patches/${patchId}/exclude`, {
    method: "POST", body: JSON.stringify({ excluded }) }),
  getPatchRules: () => request("/api/patches/rules"),
  setPatchGlobalSwitch: (enabled) => request("/api/patches/rules/global/switch", {
    method: "PUT", body: JSON.stringify({ enabled }) }),
  savePatchGlobalRule: (values) => request("/api/patches/rules/global", {
    method: "PUT", body: JSON.stringify(values) }),
  savePatchClientRule: (id, values) => request(`/api/patches/rules/client/${id}`, {
    method: "PUT", body: JSON.stringify(values) }),
  deletePatchClientRule: (id) => request(`/api/patches/rules/client/${id}`, { method: "DELETE" }),
  setPatchPolicy: (id, policy) => request(`/api/patches/policy/${id}`, {
    method: "PUT", body: JSON.stringify({ policy }) }),
  getPatchRuns: (clientId) => request(
    `/api/patches/runs${clientId ? `?client_id=${clientId}` : ""}`),
  runPatchAuto: () => request("/api/patches/auto/run", { method: "POST" }),

  // --- Datenschutz / DSGVO ---
  // Auskunft nach Art. 15/20 kommt als Datei-Download, deshalb roher fetch
  // statt request() - der würde JSON parsen statt eine Datei anzubieten.
  getMyPrivacyData: () => request("/api/privacy/me"),
  downloadPrivacyExport: async (userId) => {
    const path = userId ? `/api/privacy/users/${userId}/export` : "/api/privacy/me/export";
    const res = await fetch(`${BACKEND_URL}${path}`, {
      headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) {
      let msg = `Fehler ${res.status}`;
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    const cd = res.headers.get("content-disposition") || "";
    const match = cd.match(/filename="?([^"]+)"?/);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = match ? match[1] : "auskunft.json";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  },
  requestErasure: (kind, reason) => request("/api/privacy/me/erasure-request", {
    method: "POST", body: JSON.stringify({ kind, reason }) }),
  getMyErasureRequests: () => request("/api/privacy/me/erasure-request"),
  getPrivacyReport: () => request("/api/privacy/report"),
  setRetention: (values) => request("/api/privacy/retention", {
    method: "PUT", body: JSON.stringify({ values }) }),
  runPrivacyPurge: () => request("/api/privacy/purge", { method: "POST" }),
  getErasureRequests: (status) => request(
    `/api/privacy/erasure-requests${status ? `?status=${status}` : ""}`),
  resolveErasureRequest: (id, status, note) => request(
    `/api/privacy/erasure-requests/${id}/resolve`, {
      method: "POST", body: JSON.stringify({ status, note }) }),
  eraseUser: (userId, mode, confirmUsername) => request(
    `/api/privacy/users/${userId}/erase`, {
      method: "POST", body: JSON.stringify({ mode, confirm_username: confirmUsername }) }),

  // --- Todos (persönliche Liste, streng privat) ---
  // 'today' ist immer das Datum des BROWSERS (YYYY-MM-DD). Damit setzt der
  // Server wiederkehrende Todos in der Zeitzone des Nutzers zurück.
  getTodos: (today) => request(`/api/todos?today=${encodeURIComponent(today)}`),
  createTodo: (data, today) => request(`/api/todos?today=${encodeURIComponent(today)}`, {
    method: "POST", body: JSON.stringify(data) }),
  updateTodo: (id, data) => request(`/api/todos/${id}`, {
    method: "PUT", body: JSON.stringify(data) }),
  toggleTodo: (id, done, today) => request(`/api/todos/${id}/toggle`, {
    method: "POST", body: JSON.stringify({ done, today }) }),
  moveTodo: (id, categoryId, order) => request(`/api/todos/${id}/move`, {
    method: "POST", body: JSON.stringify({ category_id: categoryId, order: order || [] }) }),
  archiveTodo: (id) => request(`/api/todos/${id}/archive`, { method: "POST" }),
  unarchiveTodo: (id) => request(`/api/todos/${id}/unarchive`, { method: "POST" }),
  archiveDoneTodos: () => request("/api/todos/archive-done", { method: "POST" }),
  deleteTodo: (id) => request(`/api/todos/${id}`, { method: "DELETE" }),
  createTodoCategory: (data) => request("/api/todos/categories", {
    method: "POST", body: JSON.stringify(data) }),
  updateTodoCategory: (id, data) => request(`/api/todos/categories/${id}`, {
    method: "PUT", body: JSON.stringify(data) }),
  deleteTodoCategory: (id) => request(`/api/todos/categories/${id}`, { method: "DELETE" }),
  reorderTodoCategories: (ids) => request("/api/todos/categories/order", {
    method: "PUT", body: JSON.stringify({ ids }) }),

  scanNetwork: (subnet) => request(`/api/network/scan${subnet ? `?subnet=${encodeURIComponent(subnet)}` : ""}`),
  // Job-basierter Scan (große Netze wie 10.10 = /16, mit Fortschritt + Speed-Up)
  scanPreview: (target) => request(`/api/network/scan/preview${target ? `?target=${encodeURIComponent(target)}` : ""}`),
  scanStart: (target, speed) => request(
    `/api/network/scan/start?speed=${encodeURIComponent(speed || "normal")}${target ? `&target=${encodeURIComponent(target)}` : ""}`,
    { method: "POST" }),
  scanJob: (jobId) => request(`/api/network/scan/job/${jobId}`),
  scanCancel: (jobId) => request(`/api/network/scan/job/${jobId}/cancel`, { method: "POST" }),
  lastScan: () => request("/api/network/scan/last"),
  portScan: (ip, mode = "standard", ports = "") => {
    const q = new URLSearchParams({ ip, mode });
    if (mode === "custom" && ports) q.set("ports", ports);
    return request(`/api/network/portscan?${q.toString()}`);
  },

  // Apache Guacamole (extern gehostet)
  guacStatus: () => request("/api/guac/status"),
  createGuacToken: (protocol, params, clientId, hostname, record = true) =>
    request("/api/guac/token", { method: "POST", body: JSON.stringify({ protocol, params, client_id: clientId, hostname, record }) }),
  getGuacProfile: (clientId) => request(`/api/guac/profile/${clientId}`),
  saveGuacProfile: (clientId, profile) =>
    request(`/api/guac/profile/${clientId}`, { method: "PUT", body: JSON.stringify(profile) }),
  // Mehrere gespeicherte Logins pro Client (MIT Passwort)
  listGuacProfiles: (clientId) => request(`/api/guac/profiles/${clientId}`),
  addGuacProfile: (clientId, profile) =>
    request(`/api/guac/profiles/${clientId}`, { method: "POST", body: JSON.stringify(profile) }),
  deleteGuacProfile: (clientId, profileId) =>
    request(`/api/guac/profiles/${clientId}/${profileId}`, { method: "DELETE" }),

  // --- Benutzerverwaltung ---
  getUsers: () => request("/api/users"),
  createUser: (data) => request("/api/users", { method: "POST", body: JSON.stringify(data) }),
  deleteUser: (id) => request(`/api/users/${id}`, { method: "DELETE" }),

  // --- Onboarding ---
  createEnrollmentToken: (tenant_id, location_id, client_name) =>
    request("/api/enrollment/tokens", { method: "POST", body: JSON.stringify({ tenant_id, location_id, client_name }) }),
  // Fertige Installationspakete (.exe/.msi/.deb/.rpm/.run) auflisten bzw. bauen
  listInstallers: () => request("/api/enrollment/installers"),
  buildInstallers: (targets = "auto") =>
    request("/api/enrollment/installers/build", { method: "POST", body: JSON.stringify({ targets }) }),

  // --- Audit-Log ---
  getAuditLog: () => request("/api/audit"),
  logError: (message, level = "error", context = null, action = null) =>
    request("/api/audit/log-error", { method: "POST", body: JSON.stringify({ message, level, context, action }) }),

  // --- Session-Aufzeichnungen ---
  getRecordings: () => request("/api/recordings"),
  // Organisationsweite Standard-Layouts (Dashboard-Widgets & Client-Panels)
  getDefaultLayouts: () => request("/api/admin/default-layouts"),
  setDefaultLayout: (kind, layout) =>
    request("/api/admin/default-layouts", { method: "POST", body: JSON.stringify({ kind, layout }) }),
  clearDefaultLayout: (kind) =>
    request(`/api/admin/default-layouts/${kind}`, { method: "DELETE" }),
  getRecordingFrames: (id) => request(`/api/recordings/${id}/frames`),
  deleteRecording: (id) => request(`/api/recordings/${id}`, { method: "DELETE" }),

  // --- Agent-Verwaltung ---
  updateAgent: (clientId) => request(`/api/clients/${clientId}/update-agent`, { method: "POST" }),
  updateAllAgents: (opts = {}) => request("/api/clients/update-all-agents", {
    method: "POST", body: JSON.stringify({ include_offline: !!opts.include_offline }) }),
  uninstallAgent: (clientId) => request(`/api/clients/${clientId}/uninstall-agent`, { method: "POST" }),

  // --- Effektive Rechte des eingeloggten Benutzers (Frontend-Gating) ---
  getEffectivePermissions: () => request("/api/auth/effective"),

  // --- Feingranulare Rechte-Grants (Permissions-App) ---
  getPermissionCatalog: () => request("/api/admin/permission-catalog"),
  getGrants: (subjectType, subjectId) => request(`/api/admin/grants/${subjectType}/${subjectId}`),
  setGrants: (subjectType, subjectId, grants) =>
    request(`/api/admin/grants/${subjectType}/${subjectId}`, { method: "PUT", body: JSON.stringify({ grants }) }),

  // --- Backend neu starten (Admin) ---
  restartBackend: () => request("/api/admin/restart", { method: "POST" }),
  stopBackend: () => request("/api/admin/stop", { method: "POST" }),

  // --- Neueste ausgelieferte Agent-Version (für "veraltet"-Hinweis) ---
  getAgentVersion: () => request("/api/agent/version"),

  // --- Source-Tab (Admin): Explorer + Datenbank ---
  sourceRoots: () => request("/api/source/roots"),
  // Installationsart: Docker-Container oder natives Programm
  sourceRuntime: () => request("/api/source/runtime"),
  // Relay: FTP-Zugang (teilt sich den Port mit dem Dashboard)
  relayFtpConfig: () => request("/api/relay/ftp"),

  // --- Server-eigene Relay-Ordner (Storage / Deployment) ---
  storageSections: () => request("/api/storage/sections"),
  storageList: (section, path = "") => request(
    `/api/storage/list?section=${encodeURIComponent(section)}&path=${encodeURIComponent(path)}`),
  storageDownloadUrl: (section, path) =>
    `/api/storage/download?section=${encodeURIComponent(section)}&path=${encodeURIComponent(path)}`,
  // Datei-Upload (multipart, deshalb nicht ueber request()).
  storageUpload: async (section, path, file) => {
    const token = localStorage.getItem("rmm_token");
    const fd = new FormData();
    fd.append("file", file, file.name);
    const q = `section=${encodeURIComponent(section)}&path=${encodeURIComponent(path || "")}`;
    const res = await fetch(`/api/storage/upload?${q}`, {
      method: "POST", headers: { Authorization: `Bearer ${token}` }, body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail
      || `Upload fehlgeschlagen (${res.status})`);
    return res.json();
  },
  storageMkdir: (section, path) => request("/api/storage/mkdir", {    method: "POST", body: JSON.stringify({ section, path }) }),
  storageDelete: (section, path) => request("/api/storage/delete", {
    method: "POST", body: JSON.stringify({ section, path }) }),
  storageMove: (section, path, dst) => request("/api/storage/move", {
    method: "POST", body: JSON.stringify({ section, path, dst }) }),
  getDeployment: () => request("/api/storage/deployment"),  saveDeployment: (body) => request("/api/storage/deployment", {
    method: "POST", body: JSON.stringify(body) }),
  relayFtpMode: (mode, webdav) => request("/api/relay/ftp", {
    method: "POST",
    body: JSON.stringify(webdav === undefined ? { mode } : { mode, webdav }) }),
  // --- Container-Dienste (nur im Docker-Betrieb) ---
  dockerServices: () => request("/api/admin/docker/services"),
  dockerEnable: (key) => request(`/api/admin/docker/services/${key}/enable`, { method: "POST" }),
  dockerDisable: (key) => request(`/api/admin/docker/services/${key}/disable`, { method: "POST" }),
  dockerDbCredentials: () => request("/api/admin/docker/db-credentials"),
  // dist/ leeren (gebaute Agent-Installationspakete)
  sourceClearDist: () => request("/api/source/dist/clear", { method: "POST" }),
  // --- Migration: komplette Instanz umziehen ---
  migrateInfo: () => request("/api/source/migrate/info"),
  migrateExportUrl: (opts = {}) => {
    const q = new URLSearchParams({
      recordings: opts.recordings !== false,
      media: opts.media !== false,
      branding: opts.branding !== false,
      secrets: !!opts.secrets,
      token: localStorage.getItem("rmm_token") || "",
    });
    return `/api/source/migrate/export?${q}`;
  },
  migrateImport: (file, opts = {}, onProgress) => new Promise((resolve, reject) => {
    // XMLHttpRequest statt fetch: nur damit gibt es einen Fortschrittsbalken,
    // und das Archiv kann durchaus mehrere Gigabyte gross sein.
    const q = new URLSearchParams({ secrets: !!opts.secrets, backup: opts.backup !== false });
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/source/migrate/import?${q}`);
    xhr.setRequestHeader("Authorization", `Bearer ${localStorage.getItem("rmm_token") || ""}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText || "{}"); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(body.detail || `Fehler ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Verbindung zum Server verloren"));
    xhr.send(file);
  }),
  sourceList: (path = "") => request(`/api/source/list?path=${encodeURIComponent(path)}`),
  sourceRead: (path) => request(`/api/source/read?path=${encodeURIComponent(path)}`),
  sourceWrite: (path, content) =>
    request("/api/source/write", { method: "PUT", body: JSON.stringify({ path, content }) }),
  sourceDbTables: () => request("/api/source/db/tables"),
  sourceDbTable: (name, limit = 200, offset = 0) =>
    request(`/api/source/db/table?name=${encodeURIComponent(name)}&limit=${limit}&offset=${offset}`),
  sourceDbQuery: (sql) =>
    request("/api/source/db/query", { method: "POST", body: JSON.stringify({ sql }) }),
  // Explorer: Ordner/Datei anlegen, löschen, umbenennen
  sourceMkdir: (path) => request("/api/source/mkdir", { method: "POST", body: JSON.stringify({ path }) }),
  sourceNewFile: (path) => request("/api/source/newfile", { method: "POST", body: JSON.stringify({ path }) }),
  sourceDelete: (path) => request("/api/source/delete", { method: "POST", body: JSON.stringify({ path }) }),
  sourceRename: (src, dst) => request("/api/source/rename", { method: "POST", body: JSON.stringify({ src, dst }) }),
  // Datenbank: Editieren / Löschen / Anlegen / Backup
  sourceDbSetCell: (table, rowid, column, value) =>
    request("/api/source/db/cell", { method: "PUT", body: JSON.stringify({ table, rowid, column, value }) }),
  sourceDbDeleteRow: (table, rowid) =>
    request("/api/source/db/delete-row", { method: "POST", body: JSON.stringify({ table, rowid }) }),
  sourceDbInsertRow: (table, values = {}) =>
    request("/api/source/db/insert-row", { method: "POST", body: JSON.stringify({ table, values }) }),
  sourceDbDropTable: (table) =>
    request("/api/source/db/drop-table", { method: "POST", body: JSON.stringify({ table }) }),
  sourceDbCreateTable: (name, columns) =>
    request("/api/source/db/create-table", { method: "POST", body: JSON.stringify({ name, columns }) }),
  sourceDbBackup: () => request("/api/source/db/backup", { method: "POST" }),
  // ZIP hochladen + extrahieren (multipart; NICHT über request(), da FormData).
  sourceUploadZip: async (fileObj, path = "") => {
    const fd = new FormData();
    fd.append("file", fileObj);
    fd.append("path", path || "");
    const res = await fetch(`${BACKEND_URL}/api/source/upload-zip`, {
      method: "POST",
      headers: { Authorization: `Bearer ${getToken()}` },   // KEIN Content-Type (Browser setzt boundary)
      body: fd,
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    return res.json();
  },

  // --- AD-Gruppen aus einem Realm laden / importieren (Admin) ---
  getRealmAdGroups: (realmId) => request(`/api/admin/realms/${realmId}/ad-groups`),
  importRealmAdGroups: (realmId, names = []) =>
    request(`/api/admin/realms/${realmId}/ad-groups/import`, { method: "POST", body: JSON.stringify({ names }) }),

  // --- Branding (Logos per Upload ersetzen) ---
  getBranding: () => request("/api/admin/branding"),
  // Roh-Upload (KEIN multipart, KEIN JSON-Header) - Backend liest den Body direkt.
  uploadBranding: async (name, file) => {
    const token = getToken();
    const res = await fetch(`${BACKEND_URL}/api/admin/branding/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: file,
    });
    if (!res.ok) {
      let msg = "Upload fehlgeschlagen";
      try { const j = await res.json(); msg = j.detail || msg; } catch {}
      throw new Error(msg);
    }
    return res.json();
  },

  // --- Versionen (Backend + Agent, Basis fürs Auto-Update) ---
  getVersions: () => request("/api/version"),

  // Liefert die URL zur .rdp-Datei (Download öffnet den nativen RDP-Client)
  rdpFileUrl: (clientId) => `/api/clients/${clientId}/rdp-file`,
  // Lädt die .rdp-Datei (mit Auth) und stößt den Download im Browser an
  downloadRdpFile: async (clientId, hostname) => {
    const token = getToken();
    const res = await fetch(`/api/clients/${clientId}/rdp-file`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      let msg = t("u_rdp_datei_konnte_nicht_erstellt_we");
      try { const j = await res.json(); msg = j.detail || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${hostname || "client"}.rdp`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  // --- Scripts ---
  getScripts: () => request("/api/scripts"),
  createScript: (data) => request("/api/scripts", { method: "POST", body: JSON.stringify(data) }),
  updateScript: (id, data) => request(`/api/scripts/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteScript: (id) => request(`/api/scripts/${id}`, { method: "DELETE" }),

  // --- Rechte / Gruppen ---
  getSettings: () => request("/api/admin/settings"),  updateSettings: (data) => request("/api/admin/settings", { method: "PUT", body: JSON.stringify(data) }),
  getServerUpdateInfo: () => request("/api/admin/update/info"),
  setServerUpdateRepo: (url) => request("/api/admin/update/repo", { method: "PUT", body: JSON.stringify({ url }) }),
  runServerUpdate: (target, tag) => request("/api/admin/update/run", { method: "POST", body: JSON.stringify({ target, tag: tag || null }) }),
  getDatabaseInfo: () => request("/api/admin/database/info"),
  testDatabase: (cfg) => request("/api/admin/database/test", { method: "POST", body: JSON.stringify(cfg) }),
  switchDatabase: (cfg) => request("/api/admin/database/switch", { method: "POST", body: JSON.stringify(cfg) }),

  getPermissions: () => request("/api/admin/permissions"),
  getGroups: () => request("/api/admin/groups"),
  createGroup: (data) => request("/api/admin/groups", { method: "POST", body: JSON.stringify(data) }),
  updateGroup: (id, data) => request(`/api/admin/groups/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteGroup: (id) => request(`/api/admin/groups/${id}`, { method: "DELETE" }),
  setGroupUnmanaged: (id, unmanaged) => request(`/api/admin/groups/${id}/unmanaged`, { method: "PUT", body: JSON.stringify({ unmanaged }) }),
  getUserGroups: (uid) => request(`/api/admin/users/${uid}/groups`),
  setUserGroups: (uid, group_ids) => request(`/api/admin/users/${uid}/groups`, { method: "PUT", body: JSON.stringify({ group_ids }) }),

  // --- Realms (Verzeichnis / AD) ---
  getRealms: () => request("/api/admin/realms"),
  createRealm: (data) => request("/api/admin/realms", { method: "POST", body: JSON.stringify(data) }),
  updateRealm: (id, data) => request(`/api/admin/realms/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  testRealm: (id) => request(`/api/admin/realms/${id}/test`, { method: "POST" }),
  deleteRealm: (id) => request(`/api/admin/realms/${id}`, { method: "DELETE" }),

  // --- Webhooks / Benachrichtigungen ---
  getWebhooks: () => request("/api/admin/webhooks"),
  createWebhook: (data) => request("/api/admin/webhooks", { method: "POST", body: JSON.stringify(data) }),
  updateWebhook: (id, data) => request(`/api/admin/webhooks/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  testWebhook: (id) => request(`/api/admin/webhooks/${id}/test`, { method: "POST" }),
  deleteWebhook: (id) => request(`/api/admin/webhooks/${id}`, { method: "DELETE" }),

  // --- Benachrichtigungs-Regeln + SMTP (Notification-Rework) ---
  getNotifyCatalog: () => request("/api/notify/catalog"),
  getNotifyRules: () => request("/api/notify/rules"),
  createNotifyRule: (data) => request("/api/notify/rules", { method: "POST", body: JSON.stringify(data) }),
  updateNotifyRule: (id, data) => request(`/api/notify/rules/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteNotifyRule: (id) => request(`/api/notify/rules/${id}`, { method: "DELETE" }),
  testNotifyRule: (id) => request(`/api/notify/rules/${id}/test`, { method: "POST" }),
  getSmtp: () => request("/api/notify/smtp"),
  setSmtp: (data) => request("/api/notify/smtp", { method: "POST", body: JSON.stringify(data) }),
  testSmtp: (to) => request("/api/notify/smtp/test", { method: "POST", body: JSON.stringify({ to }) }),

  // --- Chat (DMs + Gruppen) ---
  chatUsers: () => request("/api/chat/users"),
  chatConversations: () => request("/api/chat/conversations"),
  chatUnread: () => request("/api/chat/unread"),
  chatCreate: (data) => request("/api/chat/conversations", { method: "POST", body: JSON.stringify(data) }),
  chatMessages: (id, before) => request(`/api/chat/conversations/${id}/messages${before ? `?before=${before}` : ""}`),
  chatSend: (id, text) => request(`/api/chat/conversations/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  chatRead: (id) => request(`/api/chat/conversations/${id}/read`, { method: "POST" }),
  chatRename: (id, name) => request(`/api/chat/conversations/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  chatAddMember: (id, userId) => request(`/api/chat/conversations/${id}/members`, { method: "POST", body: JSON.stringify({ user_id: userId }) }),
  chatRemoveMember: (id, userId) => request(`/api/chat/conversations/${id}/members/${userId}`, { method: "DELETE" }),
  chatSetAdmin: (id, userId, isAdmin) => request(`/api/chat/conversations/${id}/admins`, { method: "POST", body: JSON.stringify({ user_id: userId, is_admin: isAdmin }) }),
  chatDelete: (id) => request(`/api/chat/conversations/${id}`, { method: "DELETE" }),

  // --- Automationen ---
  getAutomations: () => request("/api/admin/automations"),
  createAutomation: (data) => request("/api/admin/automations", { method: "POST", body: JSON.stringify(data) }),
  toggleAutomation: (id) => request(`/api/admin/automations/${id}/toggle`, { method: "POST" }),
  deleteAutomation: (id) => request(`/api/admin/automations/${id}`, { method: "DELETE" }),
  getAutomationRuns: (id) => request(`/api/admin/automations/${id}/runs`),
};

export function saveToken(token) {
  localStorage.setItem("rmm_token", token);
}

export function clearToken() {
  localStorage.removeItem("rmm_token");
}

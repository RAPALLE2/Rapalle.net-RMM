// api.js
// ------
// Kleiner Helfer für alle Aufrufe an das Backend. Bündelt zwei Dinge, die
// sonst in jeder einzelnen fetch()-Aufruf wiederholt werden müssten:
//   1. Automatisch den Login-Token (JWT) aus dem localStorage mitschicken
//   2. Fehler einheitlich behandeln (Backend liefert {"detail": "..."} bei Fehlern)

import { BACKEND_URL } from "./config.js";

export function getToken() {
  return localStorage.getItem("rmm_token");
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
    throw new Error(message);
  }

  // Manche Endpunkte (z.B. Skript-Downloads) liefern kein JSON zurück
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return res.json();
  return res.text();
}

export const api = {
  // --- Auth ---
  login: (username, password, realm = "local") =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password, realm }) }),
  getLoginRealms: () => request("/api/auth/realms"),
  me: () => request("/api/auth/me"),
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
  getFavoriteWebsites: () => request("/api/clients/websites/favorites"),

  // --- Server-Dateisystem (Backend-Rechner selbst) ---
  listServerFs: (path) => request(`/api/server-files?path=${encodeURIComponent(path)}`),

  // --- Netzwerk-Scan ---
  scanNetwork: (subnet) => request(`/api/network/scan${subnet ? `?subnet=${encodeURIComponent(subnet)}` : ""}`),
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

  // --- Benutzerverwaltung ---
  getUsers: () => request("/api/users"),
  createUser: (data) => request("/api/users", { method: "POST", body: JSON.stringify(data) }),
  deleteUser: (id) => request(`/api/users/${id}`, { method: "DELETE" }),

  // --- Onboarding ---
  createEnrollmentToken: (tenant_id, location_id, client_name) =>
    request("/api/enrollment/tokens", { method: "POST", body: JSON.stringify({ tenant_id, location_id, client_name }) }),

  // --- Audit-Log ---
  getAuditLog: () => request("/api/audit"),
  logError: (message, level = "error", context = null) =>
    request("/api/audit/log-error", { method: "POST", body: JSON.stringify({ message, level, context }) }),

  // --- Session-Aufzeichnungen ---
  getRecordings: () => request("/api/recordings"),
  getRecordingFrames: (id) => request(`/api/recordings/${id}/frames`),
  deleteRecording: (id) => request(`/api/recordings/${id}`, { method: "DELETE" }),

  // --- Agent-Verwaltung ---
  updateAgent: (clientId) => request(`/api/clients/${clientId}/update-agent`, { method: "POST" }),
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

  // --- Neueste ausgelieferte Agent-Version (für "veraltet"-Hinweis) ---
  getAgentVersion: () => request("/api/agent/version"),

  // --- Source-Tab (Admin): Explorer + Datenbank ---
  sourceRoots: () => request("/api/source/roots"),
  sourceList: (path = "") => request(`/api/source/list?path=${encodeURIComponent(path)}`),
  sourceRead: (path) => request(`/api/source/read?path=${encodeURIComponent(path)}`),
  sourceWrite: (path, content) =>
    request("/api/source/write", { method: "PUT", body: JSON.stringify({ path, content }) }),
  sourceDbTables: () => request("/api/source/db/tables"),
  sourceDbTable: (name, limit = 200, offset = 0) =>
    request(`/api/source/db/table?name=${encodeURIComponent(name)}&limit=${limit}&offset=${offset}`),
  sourceDbQuery: (sql) =>
    request("/api/source/db/query", { method: "POST", body: JSON.stringify({ sql }) }),
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
      let msg = "RDP-Datei konnte nicht erstellt werden";
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

  getPermissions: () => request("/api/admin/permissions"),
  getGroups: () => request("/api/admin/groups"),
  createGroup: (data) => request("/api/admin/groups", { method: "POST", body: JSON.stringify(data) }),
  updateGroup: (id, data) => request(`/api/admin/groups/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteGroup: (id) => request(`/api/admin/groups/${id}`, { method: "DELETE" }),
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
  testWebhook: (id) => request(`/api/admin/webhooks/${id}/test`, { method: "POST" }),
  deleteWebhook: (id) => request(`/api/admin/webhooks/${id}`, { method: "DELETE" }),

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

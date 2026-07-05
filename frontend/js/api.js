// api.js
// ------
// Kleiner Helfer für alle Aufrufe an das Backend. Bündelt zwei Dinge, die
// sonst in jeder einzelnen fetch()-Aufruf wiederholt werden müssten:
//   1. Automatisch den Login-Token (JWT) aus dem localStorage mitschicken
//   2. Fehler einheitlich behandeln (Backend liefert {"detail": "..."} bei Fehlern)

import { BACKEND_URL } from "./config.js";

function getToken() {
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
  createFolder: (location_id, name, parent_folder_id = null) =>
    request("/api/folders", { method: "POST", body: JSON.stringify({ location_id, name, parent_folder_id }) }),

  // --- Clients ---
  getClients: () => request("/api/clients"),
  getClient: (id) => request(`/api/clients/${id}`),
  getMetricsHistory: (id) => request(`/api/clients/${id}/metrics/history`),
  updateClient: (id, fields) => request(`/api/clients/${id}`, { method: "PUT", body: JSON.stringify(fields) }),
  deleteClient: (id) => request(`/api/clients/${id}`, { method: "DELETE" }),
  execOnClient: (id, command, session) => request(`/api/clients/${id}/exec`, { method: "POST", body: JSON.stringify({ command, session }) }),
  bulkExec: (client_ids, command) => request("/api/clients/bulk-exec", { method: "POST", body: JSON.stringify({ client_ids, command }) }),
  listClientFs: (id, path) => request(`/api/clients/${id}/fs?path=${encodeURIComponent(path)}`),
  listProcesses: (id) => request(`/api/clients/${id}/processes`),
  killProcess: (id, pid) => request(`/api/clients/${id}/processes/kill`, { method: "POST", body: JSON.stringify({ pid }) }),
  readClientFile: (id, path) => request(`/api/clients/${id}/fs/read?path=${encodeURIComponent(path)}`),

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
  getSettings: () => request("/api/admin/settings"),
  updateSettings: (data) => request("/api/admin/settings", { method: "PUT", body: JSON.stringify(data) }),

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
};

export function saveToken(token) {
  localStorage.setItem("rmm_token", token);
}

export function clearToken() {
  localStorage.removeItem("rmm_token");
}

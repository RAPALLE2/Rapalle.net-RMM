// spotify.js
// ----------
// Spotify-Anmeldung per OAuth "Authorization Code + PKCE" - läuft komplett im
// Browser, OHNE Client-Secret. Voraussetzung: Ein Admin hat in den
// Einstellungen die Client-ID einer Spotify-App hinterlegt und im Spotify
// Developer Dashboard die Redirect-URI "<server-url>/" freigegeben.
//
// Ablauf:
//   1. login(): Verifier/Challenge erzeugen, zu accounts.spotify.com leiten
//   2. Spotify leitet zurück auf unsere Wurzel-URL (?code=...&state=rmm-spotify:...)
//   3. handleSpotifyCallback() (beim App-Boot) tauscht den Code gegen Tokens
//   4. getSpotifyToken() liefert ein gültiges Access-Token (auto-refresh)
import { api } from "./api.js";

const TOKENS_KEY = "rmm_spotify_tokens";
const PKCE_KEY = "rmm_spotify_pkce";
const STATE_PREFIX = "rmm-spotify:";

const SCOPES = [
  "streaming",                      // Web Playback SDK (Premium)
  "user-read-email", "user-read-private",
  "user-read-playback-state", "user-modify-playback-state",
  "playlist-read-private", "playlist-read-collaborative",
].join(" ");

let _clientIdCache = null;
let _configError = null;
export async function getSpotifyClientId() {
  if (_clientIdCache !== null) return _clientIdCache;
  try {
    const res = await api.getSpotifyConfig();
    _clientIdCache = res.client_id || "";
    _configError = null;
  } catch (e) {
    // Endpunkt fehlt (altes Backend) oder nicht erreichbar - NICHT dauerhaft
    // cachen, damit es nach einem Backend-Update ohne Reload klappt.
    _clientIdCache = null;
    _configError = e?.message || "unbekannt";
    return "";
  }
  return _clientIdCache;
}
// Für die UI: unterscheidet "keine Client-ID hinterlegt" von "Endpunkt fehlt".
export async function getSpotifyConfigState() {
  const clientId = await getSpotifyClientId();
  return { clientId, error: _configError };
}

const redirectUri = () => window.location.origin + "/";

// ---- PKCE-Helfer ----
function randomString(len) {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  const arr = crypto.getRandomValues(new Uint8Array(len));
  return [...arr].map((v) => chars[v % chars.length]).join("");
}
async function sha256Base64Url(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// ---- Login starten (leitet den Browser zu Spotify um) ----
export async function spotifyLogin() {
  const clientId = await getSpotifyClientId();
  if (!clientId) throw new Error("Keine Spotify Client-ID hinterlegt (Einstellungen → Spotify)");
  const verifier = randomString(64);
  const state = STATE_PREFIX + randomString(16);
  localStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state }));
  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: redirectUri(),
    scope: SCOPES,
    state,
    code_challenge_method: "S256",
    code_challenge: await sha256Base64Url(verifier),
  });
  window.location.href = "https://accounts.spotify.com/authorize?" + params;
}

// ---- Callback beim App-Boot verarbeiten ----
export async function handleSpotifyCallback() {
  const q = new URLSearchParams(window.location.search);
  const state = q.get("state") || "";
  if (!state.startsWith(STATE_PREFIX)) return false;
  // URL sofort säubern, egal was passiert.
  const cleanUrl = () => history.replaceState({}, "", window.location.pathname);
  const stored = JSON.parse(localStorage.getItem(PKCE_KEY) || "null");
  localStorage.removeItem(PKCE_KEY);
  const code = q.get("code");
  if (q.get("error")) {
    cleanUrl();
    window.notify?.("Spotify-Anmeldung abgebrochen: " + q.get("error"), "warn");
    return true;
  }
  if (!code || !stored || stored.state !== state) { cleanUrl(); return true; }
  try {
    const clientId = await getSpotifyClientId();
    const res = await fetch("https://accounts.spotify.com/api/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: clientId,
        grant_type: "authorization_code",
        code,
        redirect_uri: redirectUri(),
        code_verifier: stored.verifier,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error_description || data.error || res.status);
    _storeTokens(data);
    window.notify?.("✅ Mit Spotify verbunden", "success");
  } catch (e) {
    window.notify?.("Spotify-Token-Tausch fehlgeschlagen: " + e.message, "error", 8000);
  }
  cleanUrl();
  return true;
}

function _storeTokens(data) {
  const old = JSON.parse(localStorage.getItem(TOKENS_KEY) || "{}");
  localStorage.setItem(TOKENS_KEY, JSON.stringify({
    access_token: data.access_token,
    refresh_token: data.refresh_token || old.refresh_token,
    expires_at: Date.now() + (data.expires_in || 3600) * 1000 - 60000,
  }));
}

export function spotifyLoggedIn() {
  const t = JSON.parse(localStorage.getItem(TOKENS_KEY) || "null");
  return !!(t && t.refresh_token);
}
export function spotifyLogout() {
  localStorage.removeItem(TOKENS_KEY);
}

// ---- Gültiges Access-Token liefern (bei Bedarf refreshen) ----
export async function getSpotifyToken() {
  const t = JSON.parse(localStorage.getItem(TOKENS_KEY) || "null");
  if (!t) return null;
  if (t.access_token && Date.now() < t.expires_at) return t.access_token;
  try {
    const clientId = await getSpotifyClientId();
    const res = await fetch("https://accounts.spotify.com/api/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: clientId,
        grant_type: "refresh_token",
        refresh_token: t.refresh_token,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error_description || data.error || res.status);
    _storeTokens(data);
    return data.access_token;
  } catch (e) {
    console.warn("[spotify] Refresh fehlgeschlagen:", e);
    spotifyLogout();
    return null;
  }
}

// ---- Kleiner Web-API-Helfer ----
export async function spotifyApi(path, opts = {}) {
  const token = await getSpotifyToken();
  if (!token) throw new Error("Nicht mit Spotify angemeldet");
  const res = await fetch("https://api.spotify.com/v1" + path, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`,
               ...(opts.body ? { "Content-Type": "application/json" } : {}),
               ...(opts.headers || {}) },
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.error?.message || `Spotify-API ${res.status}`);
  return data;
}

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
  // Für die Bibliothek im Media-Hub: gespeicherte Songs und Alben lesen.
  "user-library-read", "user-top-read",
].join(" ");

let _clientIdCache = null;
let _redirectCache = "";
let _configError = null;
export async function getSpotifyClientId() {
  if (_clientIdCache !== null) return _clientIdCache;
  try {
    const res = await api.getSpotifyConfig();
    _clientIdCache = res.client_id || "";
    _redirectCache = (res.redirect_uri || "").trim();
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
// Für die UI: unterscheidet "keine Client-ID hinterlegt" von "Endpunkt fehlt"
// und liefert die Redirect-URI, die im Spotify-Dashboard stehen MUSS.
export async function getSpotifyConfigState() {
  const clientId = await getSpotifyClientId();
  return { clientId, error: _configError, redirectUri: await currentRedirectUri() };
}

// Die Redirect-URI muss ZEICHENGENAU mit dem Eintrag in der Spotify-App
// übereinstimmen - sonst antwortet Spotify mit
// "redirect_uri: Not matching configuration".
// Sie wird NICHT separat gepflegt, sondern vom Backend aus der
// "Vollständigen URL" (Einstellungen → Allgemein) abgeleitet. Ist dort nichts
// hinterlegt, gilt die Adresse, unter der das Dashboard geöffnet wurde.
// Wichtig: Spotify akzeptiert nur https:// ODER die Loopback-Adresse
// http://127.0.0.1:<port>/ - ein http://<LAN-IP>/ lässt sich dort gar nicht
// eintragen (siehe Hinweis im Audio Player und in den Einstellungen).
async function currentRedirectUri() {
  if (_clientIdCache === null) { try { await getSpotifyClientId(); } catch {} }
  // Die hinterlegte URL wird UNVERÄNDERT übernommen (Spotify vergleicht
  // zeichengenau - ein erzwungener Schrägstrich würde einen Spotify-Eintrag
  // ohne Schrägstrich brechen). Nur ohne Einstellung ergänzen wir "/".
  const uri = _redirectCache || (window.location.origin + "/");
  // Absicherung: In Sonderfällen (sandboxed iframe, file://) liefert
  // window.location.origin den String "null" - dann wäre die URI Müll und
  // Spotify meldet "No redirect URI configured". Lieber sauber melden.
  if (!/^https?:\/\/[^/\s]+(\/\S*)?$/.test(uri)) {
    throw new Error(
      "Keine gültige Redirect-URI ermittelbar (" + uri + "). Trage unter "
      + "Einstellungen → Allgemein die „Vollständige URL“ ein.");
  }
  return uri;
}
export { currentRedirectUri as spotifyRedirectUri };

// Alles, was für die Fehlersuche beim Login zählt - für die Anzeige im Player.
export async function spotifyDiagnostics() {
  const clientId = await getSpotifyClientId();
  let uri = "", uriError = null;
  try { uri = await currentRedirectUri(); }
  catch (e) { uriError = e.message; }
  const loopback = /^http:\/\/127\.0\.0\.1(:\d+)?(\/|$)/.test(uri);
  return {
    clientId, redirectUri: uri, uriError, configError: _configError,
    // Zweite Schreibweise (mit/ohne Schrägstrich) - beide im Spotify-
    // Dashboard einzutragen erspart die häufigste Fehlerquelle.
    altUri: uri.endsWith("/") ? uri.slice(0, -1) : uri + "/",
    fromSetting: !!_redirectCache,
    schemeOk: uri.startsWith("https://") || loopback,
    authorizeUrl: clientId && uri
      ? "https://accounts.spotify.com/authorize?client_id=" + encodeURIComponent(clientId)
        + "&redirect_uri=" + encodeURIComponent(uri) + "&response_type=code…"
      : "",
  };
}

// ---- PKCE-Helfer ----
function randomString(len) {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  return [...randomBytes(len)].map((v) => chars[v % chars.length]).join("");
}
// SHA-256 in reinem JS. Nötig, weil `crypto.subtle` nur in einem SECURE
// CONTEXT existiert (https:// oder localhost). Wird das Dashboard über
// http://<ip> aufgerufen, ist crypto.subtle undefined - genau das war die
// Ursache von "Cannot read properties of undefined (reading 'digest')"
// beim Spotify-Login. Der Fallback liefert dasselbe Ergebnis.
function sha256Bytes(bytes) {
  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
  let h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
           0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
  const ml = bytes.length * 8;
  const withPad = new Uint8Array((((bytes.length + 9) >> 6) + 1) << 6);
  withPad.set(bytes);
  withPad[bytes.length] = 0x80;
  new DataView(withPad.buffer).setUint32(withPad.length - 4, ml >>> 0, false);
  new DataView(withPad.buffer).setUint32(withPad.length - 8, Math.floor(ml / 4294967296), false);

  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  const w = new Uint32Array(64);
  const view = new DataView(withPad.buffer);
  for (let off = 0; off < withPad.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[i] + w[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0;
      d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h = [(h[0] + a) >>> 0, (h[1] + b) >>> 0, (h[2] + c) >>> 0, (h[3] + d) >>> 0,
         (h[4] + e) >>> 0, (h[5] + f) >>> 0, (h[6] + g) >>> 0, (h[7] + hh) >>> 0];
  }
  const out = new Uint8Array(32);
  const ov = new DataView(out.buffer);
  h.forEach((v, i) => ov.setUint32(i * 4, v, false));
  return out;
}

function base64Url(bytes) {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sha256Base64Url(text) {
  const data = new TextEncoder().encode(text);
  // Bevorzugt die native (schnellere, geprüfte) Implementierung …
  if (globalThis.crypto?.subtle?.digest) {
    try {
      const digest = await crypto.subtle.digest("SHA-256", data);
      return base64Url(new Uint8Array(digest));
    } catch { /* fällt unten auf die JS-Variante zurück */ }
  }
  // … sonst der JS-Fallback (http:// ohne Secure Context).
  return base64Url(sha256Bytes(data));
}

// Zufall: crypto.getRandomValues gibt es auch ohne Secure Context; nur als
// letzte Reserve auf Math.random ausweichen.
function randomBytes(len) {
  const arr = new Uint8Array(len);
  if (globalThis.crypto?.getRandomValues) crypto.getRandomValues(arr);
  else for (let i = 0; i < len; i++) arr[i] = Math.floor(Math.random() * 256);
  return arr;
}

// ---- Login starten (leitet den Browser zu Spotify um) ----
export async function spotifyLogin() {
  const clientId = await getSpotifyClientId();
  if (!clientId) throw new Error("Keine Spotify Client-ID hinterlegt (Einstellungen → Spotify)");
  const verifier = randomString(64);
  const state = STATE_PREFIX + randomString(16);
  // Die verwendete URI mitspeichern: Beim späteren Token-Tausch MUSS exakt
  // dieselbe geschickt werden, auch wenn die Einstellung zwischenzeitlich
  // geändert wurde.
  const redirect = await currentRedirectUri();
  localStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state, redirect }));
  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: redirect,
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
        redirect_uri: stored.redirect || (await currentRedirectUri()),
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

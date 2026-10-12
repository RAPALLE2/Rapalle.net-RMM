// Audio-Player-Modul: init-Skript. Verarbeitet die Rueckkehr vom Spotify-Login
// (OAuth-Weiterleitung mit ?code=...), die frueher beim Seitenstart im Core lief.

import { handleSpotifyCallback } from "./spotify.js";

export async function init() {
  try { await handleSpotifyCallback(); } catch { /* kein Callback oder nicht angemeldet */ }
}

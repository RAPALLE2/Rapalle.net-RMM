// apps/audioplayer.js
// -------------------
// 🎵 Audio Player: YouTube-Videos/-Playlists mit vollen eigenen Controls
// (Play/Pause, Skip, Shuffle, Fortschritt, Lautstärke, Video sichtbar),
// Spotify-Links als offizielles Embed (Spotify erlaubt externe Steuerung
// nur mit Premium-OAuth - daher steuert man dort im Widget selbst),
// Internet-Radio (Presets + eigene Stream-URL) über <audio> mit Visualizer.
import { esc, uiConfirm, uiPrompt } from "../utils.js";
import { registerCleanup } from "../windowmanager.js";
import { api } from "../api.js";
import { t } from "../i18n.js";
import { getSpotifyClientId, spotifyLogin, spotifyLoggedIn, spotifyLogout,
         getSpotifyToken, spotifyApi, spotifyDiagnostics } from "../spotify.js";

// ---- Spotify Web Playback SDK (einmal global laden) ----
let _spSdkPromise = null;
function loadSpotifySdk() {
  if (window.Spotify?.Player) return Promise.resolve(window.Spotify);
  if (_spSdkPromise) return _spSdkPromise;
  _spSdkPromise = new Promise((resolve, reject) => {
    const prev = window.onSpotifyWebPlaybackSDKReady;
    window.onSpotifyWebPlaybackSDKReady = () => { prev?.(); resolve(window.Spotify); };
    const s = document.createElement("script");
    s.src = "https://sdk.scdn.co/spotify-player.js";
    s.onerror = () => reject(new Error(t("u_spotify_sdk_nicht_erreichbar_inter")));
    document.head.appendChild(s);
    setTimeout(() => reject(new Error("Spotify-SDK Timeout")), 15000);
  });
  return _spSdkPromise;
}

// Es darf immer nur EINE Wiedergabe geben: Wird der Player ein zweites Mal
// gerendert (zweites Fenster, wiederhergestellte Session, Re-Render), wird
// die alte Instanz hier hart gestoppt - sonst spielt sie unsichtbar weiter
// und lässt sich nicht mehr anhalten.
let _activePlayerStop = null;

// Kategorien des Bibliotheks-Menüs (linke Schublade).
const LIB_TABS = [
  { key: "radio",   icon: "📻", label: "Radio" },
  { key: "spotify", icon: "🟢", label: "Spotify" },
  { key: "youtube", icon: "▶️", label: "YouTube" },
  { key: "local",   icon: "💾", label: "Lokal" },
];

const RADIO_STATIONS = [
  { name: "Radio Paradise", genre: "Eclectic Rock", url: "https://stream.radioparadise.com/aac-320", emoji: "🌴" },
  { name: "SomaFM Groove Salad", genre: "Chill / Ambient", url: "https://ice1.somafm.com/groovesalad-128-mp3", emoji: "🥗" },
  { name: "SomaFM DEF CON", genre: "Hacker Electro", url: "https://ice1.somafm.com/defcon-128-mp3", emoji: "💻" },
  { name: "Deutschlandfunk", genre: "Nachrichten", url: "https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3", emoji: "🗞️" },
  { name: "SomaFM Drone Zone", genre: "Ambient / Space", url: "https://ice1.somafm.com/dronezone-128-mp3", emoji: "🚀" },
  { name: "SomaFM Lush", genre: "Vocal / Downtempo", url: "https://ice1.somafm.com/lush-128-mp3", emoji: "🌸" },
  { name: "BBC World Service", genre: "News (EN)", url: "https://stream.live.vc.bbcmedia.co.uk/bbc_world_service", emoji: "🌍" },
  { name: "FluxFM", genre: "Alternative", url: "https://streams.fluxfm.de/live/mp3-320/audio/", emoji: "🎸" },
];

// ---- YouTube IFrame API (einmal global laden) ----
let _ytApiPromise = null;
function loadYouTubeApi() {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (_ytApiPromise) return _ytApiPromise;
  _ytApiPromise = new Promise((resolve, reject) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => { prev?.(); resolve(window.YT); };
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    s.onerror = () => reject(new Error(t("u_youtube_api_nicht_erreichbar_inter")));
    document.head.appendChild(s);
    setTimeout(() => reject(new Error("YouTube-API Timeout")), 15000);
  });
  return _ytApiPromise;
}

function parseSource(raw) {
  const url = raw.trim();
  if (!url) return null;
  try {
    const u = new URL(url.includes("://") ? url : "https://" + url);
    const host = u.hostname.replace(/^www\./, "");
    if (host.includes("youtube.com") || host === "youtu.be") {
      const list = u.searchParams.get("list");
      let video = u.searchParams.get("v");
      if (host === "youtu.be") video = u.pathname.slice(1);
      if (list || video) return { kind: "youtube", list, video };
    }
    if (host.includes("spotify.com")) {
      // /playlist/ID, /album/ID, /track/ID, /artist/ID  -> Embed-Pfad
      const m = u.pathname.match(/\/(playlist|album|track|artist|show|episode)\/([A-Za-z0-9]+)/);
      if (m) return { kind: "spotify", type: m[1], id: m[2] };
    }
    return { kind: "stream", url: u.toString() };
  } catch {
    return null;
  }
}

const fmtTime = (s) => {
  if (!isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
};

export function renderAudioPlayer(body, win) {
  // Alte Player-Instanz (falls vorhanden) sofort und vollständig stoppen.
  try { _activePlayerStop?.(); } catch {}

  let yt = null;              // YT.Player-Instanz
  let audio = null;           // <audio> für Radio/Streams
  let progressTimer = null;
  let shuffleOn = false;
  let currentKind = null;

  body.innerHTML = `
    <div class="ap-root" style="display:flex;flex-direction:column;height:100%;
         background:radial-gradient(1200px 600px at 20% -10%, #21375e 0%, #0a1420 55%, #070d16 100%);color:#e8eefc">
      <style>
        .ap-btn { background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);color:#e8eefc;
          border-radius:50%;width:44px;height:44px;font-size:17px;cursor:pointer;transition:all .12s;
          display:flex;align-items:center;justify-content:center }
        .ap-btn:hover { background:rgba(255,255,255,.18);transform:scale(1.07) }
        .ap-btn.big { width:58px;height:58px;font-size:22px;background:#3ecf8e;border-color:#3ecf8e;color:#06281a }
        .ap-btn.big:hover { background:#54e0a1 }
        .ap-btn.active { background:#4da6ff;border-color:#4da6ff;color:#04121f }
        .ap-range { -webkit-appearance:none;appearance:none;height:5px;border-radius:3px;background:rgba(255,255,255,.18);
          outline:none;cursor:pointer }
        .ap-range::-webkit-slider-thumb { -webkit-appearance:none;width:13px;height:13px;border-radius:50%;
          background:#3ecf8e;box-shadow:0 0 8px #3ecf8e99 }
        .ap-range::-moz-range-thumb { width:12px;height:12px;border-radius:50%;background:#3ecf8e;border:none }
        .ap-eq { display:flex;gap:4px;align-items:flex-end;height:46px }
        .ap-eq span { width:7px;border-radius:3px;background:linear-gradient(180deg,#3ecf8e,#4da6ff);
          animation:apEq 1s ease-in-out infinite;transform-origin:bottom }
        .ap-eq.paused span { animation-play-state:paused;height:8px !important }
        @keyframes apEq { 0%,100%{transform:scaleY(.3)} 50%{transform:scaleY(1)} }
        .ap-chip { background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:12px;
          padding:10px 12px;cursor:pointer;text-align:left;transition:all .12s;color:#e8eefc }
        .ap-chip:hover { background:rgba(255,255,255,.15);transform:translateY(-2px) }
      </style>

      <div style="display:flex;gap:8px;padding:12px 14px;align-items:center">
        <button class="ap-btn" id="ap-menu" title="Bibliothek ein-/ausblenden"
          style="width:38px;height:38px;border-radius:11px;font-size:16px;flex:none">☰</button>
        <span style="font-size:20px">🎵</span>
        <input id="ap-url" placeholder="${t("u_youtube_video_playlist_spotify_lin")}"
          style="flex:1;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:20px;
          color:#e8eefc;padding:9px 16px;font-size:13px;outline:none" />
        <button class="btn-primary" id="ap-load" style="margin:0;width:auto;border-radius:20px">Laden</button>
        <button class="taskbar-btn" id="ap-save" style="border-radius:20px;display:none;white-space:nowrap"
          title="Aktuelle Quelle in der Bibliothek merken">⭐ Merken</button>
        <button class="taskbar-btn" id="ap-spotify" style="border-radius:20px;display:none;white-space:nowrap"></button>
      </div>

      <!-- Bibliothek (links) + Bühne (rechts). Die Bibliothek lebt in einem
           EIGENEN Container und wird beim Umschalten der Kategorie neu
           gezeichnet - die Bühne bleibt dabei unangetastet, deshalb läuft die
           Wiedergabe beim Stöbern einfach weiter. -->
      <div style="flex:1;display:flex;min-height:0;gap:0">
        <div id="ap-lib" style="width:290px;flex:none;display:flex;flex-direction:column;min-height:0;
             border-right:1px solid rgba(255,255,255,.12);background:rgba(0,0,0,.18)">
          <div id="ap-lib-tabs" style="display:flex;gap:4px;padding:8px 8px 6px;flex:none;flex-wrap:wrap"></div>
          <div id="ap-lib-body" style="flex:1;overflow:auto;padding:0 8px 10px;min-height:0"></div>
        </div>
        <div id="ap-stage" style="flex:1;display:flex;align-items:center;justify-content:center;padding:0 14px;min-height:0;overflow:auto"></div>
      </div>

      <div id="ap-nowplaying" style="text-align:center;padding:6px 20px 0;min-height:22px">
        <span id="ap-title" style="font-weight:700;font-size:14px"></span>
        <span id="ap-sub" style="color:#8ea2c6;font-size:12px;margin-left:8px"></span>
      </div>

      <div id="ap-progress-row" style="display:none;align-items:center;gap:10px;padding:8px 22px 0">
        <span id="ap-cur" style="font-size:11px;color:#8ea2c6;min-width:34px;text-align:right">0:00</span>
        <input type="range" id="ap-progress" class="ap-range" style="flex:1" min="0" max="1000" value="0" />
        <span id="ap-dur" style="font-size:11px;color:#8ea2c6;min-width:34px">0:00</span>
      </div>

      <div id="ap-controls" style="display:none;align-items:center;justify-content:center;gap:14px;padding:12px 20px 16px">
        <button class="ap-btn" id="ap-shuffle" title="Shuffle">🔀</button>
        <button class="ap-btn" id="ap-prev" title="${t("exp_back")}">⏮</button>
        <button class="ap-btn big" id="ap-play" title="Play/Pause">▶</button>
        <button class="ap-btn" id="ap-next" title="Weiter (Skip)">⏭</button>
        <div style="display:flex;align-items:center;gap:8px;margin-left:14px">
          <span style="font-size:14px">🔊</span>
          <input type="range" id="ap-vol" class="ap-range" style="width:100px" min="0" max="100" value="80" />
        </div>
      </div>
    </div>
  `;

  const stage = body.querySelector("#ap-stage");
  const titleEl = body.querySelector("#ap-title");
  const subEl = body.querySelector("#ap-sub");
  const controls = body.querySelector("#ap-controls");
  const progressRow = body.querySelector("#ap-progress-row");
  const progressEl = body.querySelector("#ap-progress");
  const curEl = body.querySelector("#ap-cur");
  const durEl = body.querySelector("#ap-dur");
  const playBtn = body.querySelector("#ap-play");
  const shuffleBtn = body.querySelector("#ap-shuffle");
  const volEl = body.querySelector("#ap-vol");
  const saveBtn = body.querySelector("#ap-save");
  // Beschreibt die gerade laufende Quelle - Grundlage für "⭐ Merken".
  let currentSource = null;      // {kind, title, subtitle, url}

  // =================================================================
  // BIBLIOTHEK (linke Schublade)
  // Wichtig: Das Menü lebt in #ap-lib und ist von der Bühne (#ap-stage)
  // GETRENNT. Kategorie wechseln, stöbern oder das Menü auf-/zuklappen
  // rührt die laufende Wiedergabe deshalb nicht an - es wird nur dann etwas
  // gestartet, wenn man einen Eintrag wirklich anklickt.
  // =================================================================
  let libTab = "radio";
  let libOpen = true;
  let libItems = [];            // gespeicherte Einträge vom Server
  let localQueue = [];          // lokal gewählte Dateien (ohne Upload)
  let localIndex = -1;

  const libEl = body.querySelector("#ap-lib");
  const libTabsEl = body.querySelector("#ap-lib-tabs");
  const libBodyEl = body.querySelector("#ap-lib-body");

  function toggleLib(force) {
    libOpen = force != null ? force : !libOpen;
    libEl.style.display = libOpen ? "flex" : "none";
  }
  body.querySelector("#ap-menu").addEventListener("click", () => toggleLib());

  function drawLibTabs() {
    libTabsEl.innerHTML = LIB_TABS.map((tab) => `
      <button class="ap-chip" data-libtab="${tab.key}" style="padding:5px 9px;border-radius:9px;font-size:12px;
        ${libTab === tab.key ? "background:rgba(77,166,255,.3);border-color:#4da6ff" : ""}">
        ${tab.icon} ${esc(tab.label)}
      </button>`).join("");
    libTabsEl.querySelectorAll("[data-libtab]").forEach((b) =>
      b.addEventListener("click", () => { libTab = b.dataset.libtab; drawLib(); }));
  }

  const libRow = (icon, title, sub, actions = "") => `
    <div class="ap-chip lib-row" style="display:flex;align-items:center;gap:8px;padding:7px 9px;margin-bottom:5px">
      <span style="font-size:17px;flex:none">${icon}</span>
      <span style="flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.25">
        <span style="font-size:12.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(title)}</span>
        <span style="font-size:10.5px;color:#8ea2c6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(sub || "")}</span>
      </span>
      ${actions}
    </div>`;

  const delBtn = (id) => `<button class="ap-btn" data-libdel="${esc(id)}" title="Aus der Bibliothek entfernen"
    style="width:24px;height:24px;font-size:11px;flex:none">🗑</button>`;

  // Eigene Einträge lassen sich für alle Benutzer freigeben (oder wieder
  // privat schalten). Fremde Einträge zeigen den Schalter nicht.
  const shareBtn = (it) => `<button class="ap-btn" data-libshare="${esc(it.id)}"
    title="${it.shared ? "Für alle freigegeben – Klick: wieder privat" : t("u_nur_fur_mich_klick_fur_alle_freige")}"
    style="width:24px;height:24px;font-size:11px;flex:none;${it.shared ? "background:rgba(62,207,142,.28);border-color:#3ecf8e" : ""}">
    ${it.shared ? "🌍" : "🔒"}</button>`;

  // Aktions-Buttons eines Bibliotheks-Eintrags (nur für den Besitzer).
  const itemActions = (it) => (it.can_edit ? shareBtn(it) + delBtn(it.id) : "");

  async function loadLibItems() {
    try { libItems = await api.getMedia(); } catch { libItems = []; }
  }

  function itemsOf(kind) { return libItems.filter((i) => i.kind === kind); }

  async function drawLib() {
    drawLibTabs();
    libBodyEl.innerHTML = `<div style="color:#8ea2c6;font-size:12px;padding:8px">Lädt…</div>`;
    if (libTab === "radio") return drawRadioTab();
    if (libTab === "spotify") return drawSpotifyTab();
    if (libTab === "youtube") return drawYouTubeTab();
    if (libTab === "local") return drawLocalTab();
  }

  // ---- 📻 Radio -----------------------------------------------------
  function drawRadioTab() {
    const own = itemsOf("radio");
    libBodyEl.innerHTML = `
      <div style="font-size:11px;color:#8ea2c6;margin:4px 2px">Sender</div>
      ${RADIO_STATIONS.map((r, i) =>
        `<div data-radio="${i}">${libRow(r.emoji, r.name, r.genre)}</div>`).join("")}
      <div style="font-size:11px;color:#8ea2c6;margin:10px 2px 4px">Eigene Streams</div>
      ${own.map((it) => `<div data-play="${esc(it.id)}">${libRow("🎶", it.title, it.subtitle || it.url,
        itemActions(it))}</div>`).join("")
        || `<div style="font-size:11.5px;color:#8ea2c6;padding:2px 4px">Noch keine eigenen Streams.</div>`}
      <button class="ap-chip" id="ap-add-radio" style="width:100%;text-align:center;margin-top:8px;font-size:12px">
        ＋ Stream-URL merken</button>`;

    libBodyEl.querySelectorAll("[data-radio]").forEach((el) =>
      el.addEventListener("click", () => {
        const r = RADIO_STATIONS[+el.dataset.radio];
        playStream(r.url, `📻 ${r.name}`, r.genre);
      }));
    libBodyEl.querySelectorAll("[data-play]").forEach((el) =>
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-libdel],[data-libshare]")) return;
        const it = libItems.find((x) => x.id === el.dataset.play);
        if (it) playStream(it.url, `📻 ${it.title}`, it.subtitle || it.url);
      }));
    bindDelete();
    libBodyEl.querySelector("#ap-add-radio").addEventListener("click", async () => {
      const url = await uiPrompt("Stream-URL merken", { placeholder: "https://…/stream.mp3" });
      if (!url || !url.trim()) return;
      const name = await uiPrompt("Name des Senders", { value: "Mein Stream" });
      if (name === null) return;
      try {
        await api.addMedia({ kind: "radio", title: (name || "Stream").trim(), url: url.trim() });
        await loadLibItems(); drawLib();
      } catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  // ---- 🟢 Spotify ---------------------------------------------------
  async function drawSpotifyTab() {
    if (!spotifyLoggedIn()) {
      libBodyEl.innerHTML = `
        <div style="font-size:12px;color:#8ea2c6;padding:8px 4px;line-height:1.5">
          Nicht mit Spotify verbunden.<br>
          Melde dich oben rechts an, dann erscheinen hier deine
          <b>Playlists</b>, <b>gespeicherten Songs</b> und <b>Alben</b>.
        </div>`;
      return;
    }
    try {
      const [pl, tracks, albums] = await Promise.all([
        spotifyApi("/me/playlists?limit=50").catch(() => null),
        spotifyApi("/me/tracks?limit=50").catch(() => null),
        spotifyApi("/me/albums?limit=50").catch(() => null),
      ]);
      const plItems = (pl?.items || []).filter(Boolean);
      const trItems = (tracks?.items || []).map((i) => i.track).filter(Boolean);
      const alItems = (albums?.items || []).map((i) => i.album).filter(Boolean);
      libBodyEl.innerHTML = `
        <div style="font-size:11px;color:#8ea2c6;margin:4px 2px">Playlists (${plItems.length})</div>
        ${plItems.map((p) => `<div data-sp="playlist:${esc(p.id)}">${libRow("🎧", p.name,
          `${p.tracks?.total ?? "?"} Titel · ${p.owner?.display_name || ""}`)}</div>`).join("")
          || `<div style="font-size:11.5px;color:#8ea2c6;padding:2px 4px">Keine Playlists.</div>`}
        <div style="font-size:11px;color:#8ea2c6;margin:10px 2px 4px">Gespeicherte Songs (${trItems.length})</div>
        ${trItems.map((tr) => `<div data-sp="track:${esc(tr.id)}">${libRow("🎵", tr.name,
          (tr.artists || []).map((a) => a.name).join(", "))}</div>`).join("")
          || `<div style="font-size:11.5px;color:#8ea2c6;padding:2px 4px">Keine gespeicherten Songs.</div>`}
        <div style="font-size:11px;color:#8ea2c6;margin:10px 2px 4px">Alben (${alItems.length})</div>
        ${alItems.map((a) => `<div data-sp="album:${esc(a.id)}">${libRow("💿", a.name,
          (a.artists || []).map((x) => x.name).join(", "))}</div>`).join("")
          || `<div style="font-size:11.5px;color:#8ea2c6;padding:2px 4px">Keine Alben.</div>`}`;
      libBodyEl.querySelectorAll("[data-sp]").forEach((el) =>
        el.addEventListener("click", () => {
          const [type, id] = el.dataset.sp.split(":");
          playSpotify({ kind: "spotify", type, id });
        }));
    } catch (e) {
      libBodyEl.innerHTML = `<div style="font-size:12px;color:#ff8ba0;padding:8px 4px">
        Spotify-Bibliothek nicht abrufbar: ${esc(e.message)}<br>
        <span style="color:#8ea2c6">Tipp: einmal ab- und wieder anmelden – die Berechtigung
        für die Bibliothek ist neu hinzugekommen.</span></div>`;
    }
  }

  // ---- ▶️ YouTube ---------------------------------------------------
  function drawYouTubeTab() {
    const own = itemsOf("youtube");
    libBodyEl.innerHTML = `
      <div style="font-size:11px;color:#8ea2c6;margin:4px 2px">Gemerkte Videos & Playlists</div>
      ${own.map((it) => `<div data-play="${esc(it.id)}">${libRow(
        it.subtitle === "Playlist" ? "📃" : "▶️", it.title,
        `${it.subtitle || "YouTube"}${it.shared ? " · geteilt" : ""} · ${it.owner_name}`,
        itemActions(it))}</div>`).join("")
        || `<div style="font-size:11.5px;color:#8ea2c6;padding:2px 4px">
             Noch nichts gemerkt. Lade oben einen YouTube-Link und klicke „⭐ Merken“.</div>`}
      <button class="ap-chip" id="ap-add-yt" style="width:100%;text-align:center;margin-top:8px;font-size:12px">
        ＋ YouTube-Link merken</button>`;
    libBodyEl.querySelectorAll("[data-play]").forEach((el) =>
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-libdel],[data-libshare]")) return;
        const it = libItems.find((x) => x.id === el.dataset.play);
        const src = it && parseSource(it.url);
        if (src?.kind === "youtube") playYouTube(src);
      }));
    bindDelete();
    libBodyEl.querySelector("#ap-add-yt").addEventListener("click", async () => {
      const url = await uiPrompt("YouTube-Link merken", { placeholder: "https://www.youtube.com/watch?v=… oder …?list=…" });
      if (!url || !url.trim()) return;
      const src = parseSource(url);
      if (src?.kind !== "youtube") { window.notify?.(t("u_kein_gultiger_youtube_link"), "warn"); return; }
      const name = await uiPrompt("Name", { value: src.list ? "Playlist" : "Video" });
      if (name === null) return;
      try {
        await api.addMedia({ kind: "youtube", title: (name || "YouTube").trim(),
                             subtitle: src.list ? "Playlist" : "Video", url: url.trim() });
        await loadLibItems(); drawLib();
      } catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  // ---- 💾 Lokale Dateien --------------------------------------------
  function drawLocalTab() {
    const own = itemsOf("local");
    libBodyEl.innerHTML = `
      <div style="font-size:11px;color:#8ea2c6;margin:4px 2px">Vom Server (hochgeladen)</div>
      ${own.map((it) => `<div data-playfile="${esc(it.id)}">${libRow(
        (it.mime || "").startsWith("video") ? "🎬" : "🎵", it.title,
        `${(it.size / 1048576).toFixed(1)} MB${it.shared ? " · geteilt" : ""} · ${it.owner_name}`,
        itemActions(it))}</div>`).join("")
        || `<div style="font-size:11.5px;color:#8ea2c6;padding:2px 4px">Noch nichts hochgeladen.</div>`}

      <div style="display:flex;gap:6px;margin-top:8px">
        <button class="ap-chip" id="ap-upload" style="flex:1;text-align:center;font-size:12px">⬆️ Hochladen</button>
        <button class="ap-chip" id="ap-openlocal" style="flex:1;text-align:center;font-size:12px">📂 Öffnen</button>
      </div>
      <div id="ap-up-progress" style="display:none;height:5px;border-radius:3px;background:rgba(255,255,255,.15);margin-top:6px">
        <div id="ap-up-bar" style="height:100%;width:0%;background:#3ecf8e;border-radius:3px"></div>
      </div>
      <div style="font-size:10.5px;color:#8ea2c6;margin-top:4px">
        „Öffnen“ spielt Dateien direkt von diesem Rechner – ohne Upload.
      </div>

      ${localQueue.length ? `
        <div style="font-size:11px;color:#8ea2c6;margin:10px 2px 4px">Aktuelle Wiedergabeliste (${localQueue.length})</div>
        ${localQueue.map((f, i) => `<div data-localq="${i}">${libRow(
          f.type.startsWith("video") ? "🎬" : "🎵", f.name,
          `${(f.size / 1048576).toFixed(1)} MB${i === localIndex ? t("u_lauft") : ""}`)}</div>`).join("")}` : ""}
      <input type="file" id="ap-file" accept="audio/*,video/*" multiple hidden />
      <input type="file" id="ap-file-up" accept="audio/*,video/*" hidden />`;

    libBodyEl.querySelectorAll("[data-playfile]").forEach((el) =>
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-libdel],[data-libshare]")) return;
        const it = libItems.find((x) => x.id === el.dataset.playfile);
        if (it) playServerFile(it);
      }));
    libBodyEl.querySelectorAll("[data-localq]").forEach((el) =>
      el.addEventListener("click", () => playLocalIndex(+el.dataset.localq)));
    bindDelete();

    const fileIn = libBodyEl.querySelector("#ap-file");
    libBodyEl.querySelector("#ap-openlocal").addEventListener("click", () => fileIn.click());
    fileIn.addEventListener("change", () => {
      const files = [...fileIn.files];
      if (!files.length) return;
      localQueue = files;
      drawLib();
      playLocalIndex(0);
    });

    const upIn = libBodyEl.querySelector("#ap-file-up");
    libBodyEl.querySelector("#ap-upload").addEventListener("click", () => upIn.click());
    upIn.addEventListener("change", async () => {
      const f = upIn.files[0];
      if (!f) return;
      const box = libBodyEl.querySelector("#ap-up-progress");
      const bar = libBodyEl.querySelector("#ap-up-bar");
      box.style.display = "";
      try {
        await api.uploadMedia(f, false, (p) => { bar.style.width = (p * 100).toFixed(0) + "%"; });
        window.notify?.(`„${f.name}“ hochgeladen`, "success");
        await loadLibItems(); drawLib();
      } catch (e) {
        window.notify?.("Upload fehlgeschlagen: " + e.message, "error");
        box.style.display = "none";
      }
    });
  }

  function bindDelete() {
    libBodyEl.querySelectorAll("[data-libshare]").forEach((b) =>
      b.addEventListener("click", async (e) => {
        e.preventDefault(); e.stopPropagation();
        const it = libItems.find((x) => x.id === b.dataset.libshare);
        if (!it) return;
        try {
          await api.updateMedia(it.id, { shared: !it.shared });
          window.notify?.(!it.shared ? t("u_fur_alle_freigegeben") : "Wieder privat", "success", 2500);
          await loadLibItems(); drawLib();
        } catch (err) { window.notify?.(err.message, "error"); }
      }));
    libBodyEl.querySelectorAll("[data-libdel]").forEach((b) =>
      b.addEventListener("click", async (e) => {
        e.preventDefault(); e.stopPropagation();
        if (!(await uiConfirm("Eintrag aus der Bibliothek entfernen?",
              { okText: "Entfernen", danger: true }))) return;
        try { await api.deleteMedia(b.dataset.libdel); await loadLibItems(); drawLib(); }
        catch (err) { window.notify?.(err.message, "error"); }
      }));
  }

  // ---------------- Start-Ansicht der BÜHNE ----------------
  // (nur die Bühne - die Bibliothek bleibt stehen)
  function showHome() {
    cleanup();
    currentKind = null;
    titleEl.textContent = ""; subEl.textContent = "";
    controls.style.display = "none";
    progressRow.style.display = "none";
    saveBtn.style.display = "none";
    stage.innerHTML = `
      <div style="max-width:520px;width:100%;text-align:center">
        <div style="font-size:56px;margin-bottom:4px">🎧</div>
        <div style="font-weight:800;font-size:18px;margin-bottom:2px">Was möchtest du hören?</div>
        <div style="color:#8ea2c6;font-size:12.5px;line-height:1.6">
          Links in der <b>Bibliothek</b> stöbern – Radio, deine Spotify-Playlists,
          gemerkte YouTube-Links und lokale Dateien.<br>
          Oder oben einen Link einfügen. Das Menü kannst du jederzeit mit ☰
          ein- und ausblenden, auch während etwas läuft.
        </div>
      </div>`;
  }

  function cleanup() {
    clearInterval(progressTimer); progressTimer = null;
    try { yt?.stopVideo?.(); } catch {}
    try { yt?.destroy(); } catch {}
    yt = null;
    if (audio) {
      // Hart beenden: pausieren, Quelle leeren und neu laden - sonst kann ein
      // laufender Stream im Hintergrund weiterspielen.
      try { audio.pause(); } catch {}
      try { audio.src = ""; audio.load(); } catch {}
      try { audio.remove(); } catch {}
      audio = null;
    }
    if (mediaEl) {
      try { mediaEl.pause(); } catch {}
      // Blob-URLs wieder freigeben, sonst bleibt die Datei im Speicher.
      try { if (mediaEl.src.startsWith("blob:")) URL.revokeObjectURL(mediaEl.src); } catch {}
      try { mediaEl.src = ""; mediaEl.load(); } catch {}
      try { mediaEl.remove(); } catch {}
      mediaEl = null;
    }
    clearInterval(spProgressTimer); spProgressTimer = null;
    if (spPlayer) {
      // Nur pausieren + trennen; die Instanz wird beim nächsten Spotify-Start
      // neu aufgebaut (disconnect gibt das Gerät frei).
      try { spPlayer.pause?.(); } catch {}
      try { spPlayer.disconnect?.(); } catch {}
      spPlayer = null; spDeviceId = null;
    }
    shuffleOn = false; spShuffle = false;
    shuffleBtn.classList.remove("active");
    playBtn.textContent = "▶";
    // "Merken" gilt immer nur für die GERADE laufende Quelle.
    currentSource = null;
    saveBtn.style.display = "none";
  }
  // Diese Instanz ist ab jetzt die "aktive" - eine später gerenderte Instanz
  // ruft diesen Stopper auf, bevor sie selbst loslegt.
  _activePlayerStop = cleanup;

  // ---------------- YouTube ----------------
  async function playYouTube(src) {
    cleanup();
    currentKind = "youtube";
    stage.innerHTML = `<div style="width:100%;max-width:860px;aspect-ratio:16/9;border-radius:14px;overflow:hidden;
      box-shadow:0 12px 40px rgba(0,0,0,.55);background:#000" id="ap-yt-frame">
      <div style="display:flex;align-items:center;justify-content:center;height:100%;color:#8ea2c6">⏳ YouTube lädt…</div></div>`;
    titleEl.textContent = ""; subEl.textContent = "YouTube";
    try {
      const YT = await loadYouTubeApi();
      const holder = document.createElement("div");
      stage.querySelector("#ap-yt-frame").innerHTML = "";
      stage.querySelector("#ap-yt-frame").appendChild(holder);
      yt = new YT.Player(holder, {
        width: "100%", height: "100%",
        videoId: src.video || undefined,
        playerVars: {
          listType: src.list ? "playlist" : undefined,
          list: src.list || undefined,
          autoplay: 1, rel: 0,
        },
        events: {
          onReady: () => {
            yt.setVolume(+volEl.value);
            controls.style.display = "flex";
            progressRow.style.display = "flex";
            startProgress();
          },
          onStateChange: (e) => {
            playBtn.textContent = e.data === 1 ? "⏸" : "▶";
            const d = yt.getVideoData?.() || {};
            if (d.title) titleEl.textContent = d.title;
            const pl = yt.getPlaylist?.() || [];
            subEl.textContent = pl.length
              ? `YouTube-Playlist · Titel ${(yt.getPlaylistIndex?.() ?? 0) + 1}/${pl.length}${shuffleOn ? " · 🔀" : ""}`
              : "YouTube";
          },
        },
      });
    } catch (e) {
      stage.innerHTML = `<div style="color:#ff8ba0">⚠ ${esc(e.message)}</div>`;
    }
  }

  // ---------------- Spotify ----------------
  let spPlayer = null, spDeviceId = null, spShuffle = false;

  function playSpotify(src) {
    // Angemeldet? -> Vollplayer mit eigenen Controls (Premium nötig).
    // Sonst: offizielles Embed + Login-Hinweis.
    if (spotifyLoggedIn()) playSpotifyFull(src);
    else playSpotifyEmbed(src, true);
  }

  function playSpotifyEmbed(src, showLoginHint) {
    cleanup();
    currentKind = "spotify";
    controls.style.display = "none";      // Spotify steuert man im Widget selbst
    progressRow.style.display = "none";
    const tall = src.type === "track" || src.type === "episode" ? 152 : 420;
    stage.innerHTML = `
      <div style="width:100%;max-width:680px">
        <iframe src="https://open.spotify.com/embed/${src.type}/${src.id}?theme=0"
          width="100%" height="${tall}" frameborder="0" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"
          style="border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.55)"></iframe>
        <div style="color:#8ea2c6;font-size:11px;text-align:center;margin-top:8px">
          ${showLoginHint
            ? `Tipp: Mit <b>Spotify-Login</b> (Button oben rechts) und Premium spielt der
               Player volle Titel mit eigenen Controls (Play/Skip/Shuffle).`
            : `Vollplayer nicht verfügbar - Wiedergabe läuft im Spotify-Widget.`}
        </div>
      </div>`;
    titleEl.textContent = "Spotify"; subEl.textContent = src.type;
  }

  async function playSpotifyFull(src) {
    cleanup();
    currentKind = "spotify-full";
    spShuffle = false;
    stage.innerHTML = `<div style="color:#8ea2c6">⏳ Verbinde mit Spotify…</div>`;
    titleEl.textContent = ""; subEl.textContent = "Spotify";
    try {
      const Spotify = await loadSpotifySdk();
      // Player nur einmal pro Fenster anlegen und wiederverwenden.
      if (!spPlayer) {
        spPlayer = new Spotify.Player({
          name: "RAPALLE RMM Player",
          volume: (+volEl.value) / 100,
          getOAuthToken: (cb) => { getSpotifyToken().then((tok) => cb(tok || "")); },
        });
        spDeviceId = await new Promise((resolve, reject) => {
          spPlayer.addListener("ready", ({ device_id }) => resolve(device_id));
          spPlayer.addListener("initialization_error", (e) => reject(new Error(e.message)));
          spPlayer.addListener("authentication_error", (e) => reject(new Error("Anmeldung abgelaufen: " + e.message)));
          spPlayer.addListener("account_error", () =>
            reject(Object.assign(new Error(t("u_spotify_premium_notig_fur_den_voll")), { noPremium: true })));
          spPlayer.addListener("player_state_changed", onSpState);
          spPlayer.connect();
          setTimeout(() => reject(new Error("Spotify-Player Timeout")), 15000);
        });
      }
      // Wiedergabe der URI auf UNSEREM Gerät starten.
      const uri = `spotify:${src.type}:${src.id}`;
      const body = src.type === "track" ? { uris: [uri] } : { context_uri: uri };
      await spotifyApi(`/me/player/play?device_id=${spDeviceId}`, {
        method: "PUT", body: JSON.stringify(body) });
      stage.innerHTML = `
        <div style="text-align:center">
          <img id="ap-sp-cover" style="width:250px;height:250px;border-radius:14px;object-fit:cover;
            box-shadow:0 12px 44px rgba(0,0,0,.6);background:#132132" />
          <div style="color:#1DB954;font-size:11px;margin-top:10px;font-weight:700">● Spotify Premium · Vollplayer</div>
        </div>`;
      controls.style.display = "flex";
      progressRow.style.display = "flex";
      startSpProgress();
    } catch (e) {
      if (e.noPremium) {
        window.notify?.("Kein Spotify Premium - zeige stattdessen das Widget", "warn", 5000);
        playSpotifyEmbed(src, false);
      } else {
        stage.innerHTML = `<div style="color:#ff8ba0">⚠ ${esc(e.message)}</div>`;
      }
    }
  }

  function onSpState(st) {
    if (!st || currentKind !== "spotify-full") return;
    playBtn.textContent = st.paused ? "▶" : "⏸";
    const tr = st.track_window?.current_track;
    if (tr) {
      titleEl.textContent = tr.name;
      subEl.textContent = tr.artists?.map((a) => a.name).join(", ") + (spShuffle ? " · 🔀" : "");
      const img = stage.querySelector("#ap-sp-cover");
      const art = tr.album?.images?.[0]?.url;
      if (img && art && img.src !== art) img.src = art;
    }
    const dur = (st.duration || 0) / 1000, cur = (st.position || 0) / 1000;
    if (dur > 0 && !seeking) progressEl.value = Math.round((cur / dur) * 1000);
    curEl.textContent = fmtTime(cur);
    durEl.textContent = fmtTime(dur);
  }

  let spProgressTimer = null;
  function startSpProgress() {
    clearInterval(spProgressTimer);
    spProgressTimer = setInterval(async () => {
      if (currentKind !== "spotify-full" || !spPlayer || seeking) return;
      const st = await spPlayer.getCurrentState().catch(() => null);
      if (st) onSpState(st);
    }, 1000);
  }

  // ---------------- Radio / Direkt-Stream ----------------
  function playStream(url, title, sub) {
    cleanup();
    currentKind = "stream";
    audio = document.createElement("audio");
    audio.src = url;
    audio.volume = (+volEl.value) / 100;
    audio.play().catch((e) => { subEl.textContent = "⚠ " + e.message; });
    stage.innerHTML = `
      <div style="text-align:center">
        <div style="width:190px;height:190px;border-radius:50%;margin:0 auto 16px;display:flex;align-items:center;
          justify-content:center;font-size:70px;background:conic-gradient(#3ecf8e,#4da6ff,#c77dff,#3ecf8e);
          box-shadow:0 0 60px #3ecf8e44;animation:apSpin 14s linear infinite">
          <div style="width:170px;height:170px;border-radius:50%;background:#0a1420;display:flex;align-items:center;justify-content:center">📻</div>
        </div>
        <style>@keyframes apSpin { to { transform:rotate(360deg) } }</style>
        <div class="ap-eq" id="ap-eq" style="justify-content:center">
          ${[0,1,2,3,4,5,6].map((i) => `<span style="height:${10 + (i % 4) * 11}px;animation-delay:${i * 0.12}s"></span>`).join("")}
        </div>
      </div>`;
    titleEl.textContent = title || "Stream";
    subEl.textContent = sub || url;
    controls.style.display = "flex";
    progressRow.style.display = "none";   // Live-Radio hat keinen Fortschritt
    playBtn.textContent = "⏸";
    audio.addEventListener("playing", () => {
      playBtn.textContent = "⏸";
      stage.querySelector("#ap-eq")?.classList.remove("paused");
    });
    audio.addEventListener("pause", () => {
      playBtn.textContent = "▶";
      stage.querySelector("#ap-eq")?.classList.add("paused");
    });
    audio.addEventListener("error", () => { subEl.textContent = t("u_stream_nicht_erreichbar"); });
  }

  // ---------------- Lokale Dateien (MP3/MP4) ----------------
  // Audio bekommt eine Plattenoptik, Video ein echtes <video>-Element.
  // Der Fortschrittsbalken funktioniert hier ganz normal (Spulen inklusive).
  let mediaEl = null;            // <audio> oder <video> für lokale Wiedergabe

  function playMedia(url, title, sub, isVideo, onEnded) {
    cleanup();
    currentKind = "local";
    if (isVideo) {
      stage.innerHTML = `<video id="ap-video" style="width:100%;max-width:860px;max-height:100%;
        border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.55);background:#000" controls></video>`;
      mediaEl = stage.querySelector("#ap-video");
      mediaEl.controls = false;         // wir steuern über die eigenen Buttons
    } else {
      stage.innerHTML = `
        <div style="text-align:center">
          <div style="width:190px;height:190px;border-radius:50%;margin:0 auto 16px;display:flex;align-items:center;
            justify-content:center;font-size:66px;background:conic-gradient(#c77dff,#4da6ff,#3ecf8e,#c77dff);
            box-shadow:0 0 60px #4da6ff44;animation:apSpin 16s linear infinite">
            <div style="width:170px;height:170px;border-radius:50%;background:#0a1420;display:flex;align-items:center;justify-content:center">🎵</div>
          </div>
          <style>@keyframes apSpin { to { transform:rotate(360deg) } }</style>
          <div class="ap-eq" id="ap-eq" style="justify-content:center">
            ${[0,1,2,3,4,5,6].map((i) => `<span style="height:${10 + (i % 4) * 11}px;animation-delay:${i * 0.12}s"></span>`).join("")}
          </div>
        </div>`;
      mediaEl = document.createElement("audio");
    }
    mediaEl.src = url;
    mediaEl.volume = (+volEl.value) / 100;
    mediaEl.play().catch((e) => { subEl.textContent = "⚠ " + e.message; });
    titleEl.textContent = title;
    subEl.textContent = sub || "";
    controls.style.display = "flex";
    progressRow.style.display = "flex";
    playBtn.textContent = "⏸";
    mediaEl.addEventListener("play", () => {
      playBtn.textContent = "⏸";
      stage.querySelector("#ap-eq")?.classList.remove("paused");
    });
    mediaEl.addEventListener("pause", () => {
      playBtn.textContent = "▶";
      stage.querySelector("#ap-eq")?.classList.add("paused");
    });
    mediaEl.addEventListener("timeupdate", () => {
      if (seeking || !mediaEl?.duration) return;
      progressEl.value = Math.round((mediaEl.currentTime / mediaEl.duration) * 1000);
      curEl.textContent = fmtTime(mediaEl.currentTime);
      durEl.textContent = fmtTime(mediaEl.duration);
    });
    mediaEl.addEventListener("ended", () => onEnded?.());
    mediaEl.addEventListener("error", () => { subEl.textContent = t("u_datei_nicht_abspielbar"); });
  }

  // Datei vom eigenen Rechner (kein Upload) - Blob-URL, Playlist-fähig.
  function playLocalIndex(i) {
    if (i < 0 || i >= localQueue.length) return;
    localIndex = i;
    const f = localQueue[i];
    const url = URL.createObjectURL(f);
    playMedia(url, f.name, `Lokale Datei · ${i + 1}/${localQueue.length}`,
      (f.type || "").startsWith("video"),
      () => playLocalIndex(shuffleOn
        ? Math.floor(Math.random() * localQueue.length) : localIndex + 1));
    currentSource = null;            // lokale Datei lässt sich nicht "merken"
    saveBtn.style.display = "none";
    if (libTab === "local") drawLib();
  }

  // Datei aus der Server-Bibliothek (mit Range-Support -> Spulen klappt).
  function playServerFile(item) {
    playMedia(api.mediaFileUrl(item.id), item.title,
      `Bibliothek · ${item.owner_name}`, (item.mime || "").startsWith("video"));
    currentSource = null;
    saveBtn.style.display = "none";
  }

  // ---------------- Fortschritt (YouTube) ----------------
  let seeking = false;
  function startProgress() {
    clearInterval(progressTimer);
    progressTimer = setInterval(() => {
      if (!yt?.getDuration || seeking) return;
      const dur = yt.getDuration() || 0;
      const cur = yt.getCurrentTime?.() || 0;
      if (dur > 0) progressEl.value = Math.round((cur / dur) * 1000);
      curEl.textContent = fmtTime(cur);
      durEl.textContent = fmtTime(dur);
    }, 500);
  }
  progressEl.addEventListener("input", () => { seeking = true; });
  progressEl.addEventListener("change", async () => {
    if (currentKind === "youtube" && yt?.getDuration) {
      yt.seekTo((progressEl.value / 1000) * yt.getDuration(), true);
    } else if (currentKind === "spotify-full" && spPlayer) {
      const st = await spPlayer.getCurrentState().catch(() => null);
      if (st?.duration) spPlayer.seek((progressEl.value / 1000) * st.duration);
    } else if (currentKind === "local" && mediaEl?.duration) {
      mediaEl.currentTime = (progressEl.value / 1000) * mediaEl.duration;
    }
    seeking = false;
  });

  // ---------------- Controls ----------------
  playBtn.addEventListener("click", () => {
    if (currentKind === "youtube" && yt) {
      yt.getPlayerState() === 1 ? yt.pauseVideo() : yt.playVideo();
    } else if (currentKind === "spotify-full" && spPlayer) {
      spPlayer.togglePlay();
    } else if (currentKind === "local" && mediaEl) {
      mediaEl.paused ? mediaEl.play() : mediaEl.pause();
    } else if (audio) {
      audio.paused ? audio.play() : audio.pause();
    }
  });
  body.querySelector("#ap-next").addEventListener("click", () => {
    if (currentKind === "youtube") yt?.nextVideo?.();
    else if (currentKind === "spotify-full") spPlayer?.nextTrack?.();
    else if (currentKind === "local" && localQueue.length) {
      playLocalIndex(shuffleOn ? Math.floor(Math.random() * localQueue.length)
                               : (localIndex + 1) % localQueue.length);
    }
  });
  body.querySelector("#ap-prev").addEventListener("click", () => {
    if (currentKind === "youtube") yt?.previousVideo?.();
    else if (currentKind === "spotify-full") spPlayer?.previousTrack?.();
    else if (currentKind === "local" && localQueue.length) {
      playLocalIndex((localIndex - 1 + localQueue.length) % localQueue.length);
    }
  });
  shuffleBtn.addEventListener("click", async () => {
    if (currentKind === "youtube" && yt?.setShuffle) {
      shuffleOn = !shuffleOn;
      yt.setShuffle(shuffleOn);
      shuffleBtn.classList.toggle("active", shuffleOn);
      window.notify?.(shuffleOn ? "🔀 Shuffle an" : "Shuffle aus", "info", 2000);
    } else if (currentKind === "local") {
      shuffleOn = !shuffleOn;
      shuffleBtn.classList.toggle("active", shuffleOn);
      window.notify?.(shuffleOn ? "🔀 Shuffle an" : "Shuffle aus", "info", 2000);
    } else if (currentKind === "spotify-full") {
      spShuffle = !spShuffle;
      try {
        await spotifyApi(`/me/player/shuffle?state=${spShuffle}&device_id=${spDeviceId}`, { method: "PUT" });
        shuffleBtn.classList.toggle("active", spShuffle);
        window.notify?.(spShuffle ? "🔀 Shuffle an" : "Shuffle aus", "info", 2000);
      } catch (e) { spShuffle = !spShuffle; window.notify?.(e.message, "error"); }
    }
  });
  volEl.addEventListener("input", () => {
    if (yt?.setVolume) yt.setVolume(+volEl.value);
    if (audio) audio.volume = (+volEl.value) / 100;
    if (mediaEl) mediaEl.volume = (+volEl.value) / 100;
    if (spPlayer) spPlayer.setVolume((+volEl.value) / 100).catch(() => {});
  });

  // ---------------- Laden ----------------
  function loadFromInput() {
    const raw = body.querySelector("#ap-url").value.trim();
    const src = parseSource(raw);
    if (!src) { window.notify?.(t("u_link_nicht_erkannt_youtube_spotify"), "warn"); return; }
    if (src.kind === "youtube") {
      playYouTube(src);
      setSource({ kind: "youtube", title: src.list ? "YouTube-Playlist" : "YouTube-Video",
                  subtitle: src.list ? "Playlist" : "Video", url: raw });
    } else if (src.kind === "spotify") {
      playSpotify(src);
      setSource({ kind: "spotify", title: `Spotify-${src.type}`, subtitle: src.type, url: raw });
    } else {
      playStream(src.url, "🎶 Stream", src.url);
      setSource({ kind: "radio", title: "Stream", subtitle: "", url: src.url });
    }
  }

  // Merkt sich die laufende Quelle und blendet "⭐ Merken" ein.
  function setSource(src) {
    currentSource = src;
    saveBtn.style.display = src ? "inline-block" : "none";
  }
  saveBtn.addEventListener("click", async () => {
    if (!currentSource) return;
    const name = await uiPrompt("In der Bibliothek merken", {
      description: t("u_unter_welchem_namen_soll_der_eintr"),
      value: titleEl.textContent || currentSource.title });
    if (name === null) return;
    try {
      await api.addMedia({ ...currentSource, title: (name || currentSource.title).trim() });
      window.notify?.("In der Bibliothek gespeichert", "success");
      await loadLibItems();
      libTab = currentSource.kind === "spotify" ? "spotify" : currentSource.kind;
      if (libTab === "spotify") libTab = "youtube";   // Spotify-Tab zeigt das Konto
      toggleLib(true); drawLib();
    } catch (e) { window.notify?.(e.message, "error"); }
  });
  body.querySelector("#ap-load").addEventListener("click", loadFromInput);
  body.querySelector("#ap-url").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadFromInput();
  });

  // ---- Spotify-Login-Chip (IMMER sichtbar; erklärt sich selbst) ----
  const spBtn = body.querySelector("#ap-spotify");
  let spState = { clientId: "", error: null };
  async function refreshSpotifyChip() {
    const { getSpotifyConfigState } = await import("../spotify.js");
    spState = await getSpotifyConfigState();
    spBtn.style.display = "inline-block";
    if (spotifyLoggedIn()) {
      spBtn.textContent = "🟢 Spotify verbunden · Abmelden";
      spBtn.title = t("u_spotify_verbindung_trennen");
    } else if (spState.clientId) {
      spBtn.textContent = "🎧 Mit Spotify anmelden";
      spBtn.title = "Spotify-Konto verbinden (Premium = Vollplayer)";
    } else {
      spBtn.textContent = "🎧 Spotify einrichten…";
      spBtn.title = t("u_spotify_login_ist_noch_nicht_einge");
    }
  }
  spBtn.addEventListener("click", async () => {
    if (spotifyLoggedIn()) {
      spotifyLogout();
      if (currentKind === "spotify-full") showHome();
      // Zeigt die komplette Einrichtung auf der Bühne - mit der EXAKTEN URI, die
  // im Spotify-Dashboard stehen muss, und Kopier-Button. Das ist deutlich
  // hilfreicher als ein kurzer Toast, der wieder verschwindet.
  function showSpotifySetup(diag) {
    cleanup();
    currentKind = null;
    controls.style.display = "none";
    progressRow.style.display = "none";
    titleEl.textContent = "Spotify einrichten"; subEl.textContent = "";
    const uri = diag.redirectUri || (window.location.origin + "/");
    const problem = diag.uriError
      ? esc(diag.uriError)
      : !diag.schemeOk
        ? `Spotify akzeptiert <code>${esc(uri)}</code> nicht. Erlaubt sind nur
           <b>https://…</b> oder die Loopback-Adresse <code>http://127.0.0.1:PORT/</code>.`
        : "";
    stage.innerHTML = `
      <div style="max-width:620px;width:100%;font-size:13px;line-height:1.6">
        <div style="font-size:34px;text-align:center">🎧</div>
        <div style="font-weight:800;font-size:16px;text-align:center;margin-bottom:10px">
          Spotify-Login einrichten</div>
        ${problem ? `<div style="background:rgba(255,77,109,.14);border:1px solid #ff4d6d;
          border-radius:10px;padding:9px 12px;margin-bottom:12px">⚠ ${problem}</div>` : ""}
        <div style="background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);
             border-radius:10px;padding:10px 12px;margin-bottom:12px">
          <div style="color:#8ea2c6;font-size:11.5px;margin-bottom:4px">
            Diese Redirect-URI wird gesendet
            (${diag.fromSetting ? "aus „Vollständige URL“" : "aktuelle Adresse dieser Seite"}):</div>
          <div style="display:flex;gap:6px;align-items:center">
            <code id="ap-sp-uri" style="flex:1;padding:5px 8px;background:rgba(0,0,0,.35);
              border-radius:6px;overflow-wrap:anywhere">${esc(uri)}</code>
            <button class="ap-btn" id="ap-sp-copy" title="Kopieren"
              style="width:30px;height:30px;font-size:13px;flex:none">📋</button>
          </div>
          ${diag.altUri ? `
          <div style="color:#8ea2c6;font-size:11.5px;margin:8px 0 4px">
            Trage im Dashboard am besten <b>beide</b> Schreibweisen ein – Spotify
            vergleicht zeichengenau, und der Schrägstrich am Ende ist die häufigste
            Fehlerquelle:</div>
          <div style="display:flex;gap:6px;align-items:center">
            <code style="flex:1;padding:5px 8px;background:rgba(0,0,0,.35);
              border-radius:6px;overflow-wrap:anywhere">${esc(diag.altUri)}</code>
            <button class="ap-btn" id="ap-sp-copy2" title="Kopieren"
              style="width:30px;height:30px;font-size:13px;flex:none">📋</button>
          </div>` : ""}
        </div>
        <ol style="margin:0 0 12px 18px;padding:0;color:#c9d6ee">
          <li>Auf <b>developer.spotify.com/dashboard</b> deine App öffnen (oder neu anlegen).</li>
          <li><b>Settings</b> → <b>Redirect URIs</b>: obige URI(s) einfügen, jeweils auf
              <b>Add</b> klicken und unten <b>Save</b> nicht vergessen – ohne Speichern
              meldet Spotify „No redirect URI configured“.</li>
          <li>Schreibweise <b>exakt</b> vergleichen: Groß-/Kleinschreibung, jeder Buchstabe
              im Hostnamen, Port und Schrägstrich. Schon ein Zeichen Unterschied ergibt
              „redirect_uri: Not matching configuration“.</li>
          <li>Unter <b>APIs used</b> muss <b>Web API</b> (und für den Vollplayer
              <b>Web Playback SDK</b>) aktiviert sein.</li>
          <li>Die <b>Client ID</b> aus der App in
              <b>Einstellungen → Allgemein → Spotify</b> eintragen.</li>
        </ol>
        <div style="color:#8ea2c6;font-size:12px">
          Die Redirect-URI wird aus <b>Einstellungen → Allgemein → „Vollständige URL“</b>
          gebildet. Läuft das Dashboard nur über HTTP im LAN, öffne es über
          <code>http://127.0.0.1:PORT/</code> – Spotify lässt HTTP sonst nicht zu.
        </div>
        <div style="text-align:center;margin-top:14px">
          <button class="btn-primary" id="ap-sp-retry" style="width:auto;margin:0;border-radius:20px">
            Erneut versuchen</button>
        </div>
      </div>`;
    const copyTo = async (text) => {
      try { await navigator.clipboard.writeText(text); window.notify?.("URI kopiert", "success", 2000); }
      catch { window.notify?.(t("u_bitte_manuell_kopieren") + text, "info", 8000); }
    };
    stage.querySelector("#ap-sp-copy").addEventListener("click", () => copyTo(uri));
    stage.querySelector("#ap-sp-copy2")?.addEventListener("click", () => copyTo(diag.altUri));
    stage.querySelector("#ap-sp-retry").addEventListener("click", async () => {
      const d2 = await spotifyDiagnostics();
      if (d2.uriError || !d2.schemeOk) { showSpotifySetup(d2); return; }
      try { await spotifyLogin(); } catch (e) { window.notify?.(e.message, "error", 8000); }
    });
  }

  refreshSpotifyChip();
      window.notify?.("Spotify getrennt", "info");
      return;
    }
    await refreshSpotifyChip();   // Stand frisch holen (Backend evtl. inzwischen aktualisiert)
    if (spState.clientId) {
      // Vor dem Weiterleiten prüfen, ob die Redirect-URI überhaupt zu Spotify
      // passt - sonst landet man auf einer kryptischen Spotify-Fehlerseite
      // ("No redirect URI configured" / "Not matching configuration").
      const diag = await spotifyDiagnostics();
      if (diag.uriError || !diag.schemeOk) { showSpotifySetup(diag); return; }
      // Vor der Weiterleitung merken, damit wir nach einem Fehlschlag von
      // Spotify direkt die Anleitung mit der gesendeten URI zeigen können.
      try { sessionStorage.setItem("ap_sp_pending", "1"); } catch {}
      try { await spotifyLogin(); }   // leitet den Browser zu Spotify um
      catch (e) { window.notify?.(e.message, "error", 8000); }
      return;
    }
    // Noch nicht eingerichtet -> vollständige Anleitung auf der Bühne zeigen.
    if (!spState.error) { showSpotifySetup(await spotifyDiagnostics()); return; }
    if (spState.error) {
      window.notify?.(
        "Spotify-Login: Backend-Endpunkt fehlt (/api/auth/spotify-config). " +
        t("u_bitte_auth_routes_py_admin_routes_") +
        "und das Backend neu starten. (" + spState.error + ")", "error", 12000);
    } else {
      window.notify?.(
        "Spotify-Login einrichten: 1) Auf developer.spotify.com eine App anlegen, " +
        "2) dort als Redirect-URI \"" + (spState.redirectUri || window.location.origin + "/") +
        "\" eintragen (zeichengenau, mit / am Ende), " +
        "3) die Client-ID in Einstellungen → Allgemein → Spotify speichern. " +
        "Danach erscheint hier \"Mit Spotify anmelden\".", "info", 14000);
    }
  });
  refreshSpotifyChip();

  // Kam der Browser gerade von einem FEHLGESCHLAGENEN Spotify-Login zurück
  // (Spotify hängt ?error=… an), zeigen wir sofort die Anleitung statt nur
  // einer Fehlermeldung, die man leicht übersieht.
  (async () => {
    let pending = false;
    try { pending = sessionStorage.getItem("ap_sp_pending") === "1"; } catch {}
    const err = new URLSearchParams(window.location.search).get("error");
    if (!pending && !err) return;
    try { sessionStorage.removeItem("ap_sp_pending"); } catch {}
    if (err && !spotifyLoggedIn()) {
      window.notify?.("Spotify meldete: " + err, "error", 9000);
      showSpotifySetup(await spotifyDiagnostics());
    }
  })();

  // Bibliothek initial laden und zeichnen (unabhängig von der Bühne).
  loadLibItems().then(drawLib);

  // Beim Schließen des Fensters die Wiedergabe stoppen.
  if (win?.key) registerCleanup(win.key, () => {
    cleanup();
    if (_activePlayerStop === cleanup) _activePlayerStop = null;
  });
  showHome();
}

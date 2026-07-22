// apps/audioplayer.js
// -------------------
// 🎵 Audio Player: YouTube-Videos/-Playlists mit vollen eigenen Controls
// (Play/Pause, Skip, Shuffle, Fortschritt, Lautstärke, Video sichtbar),
// Spotify-Links als offizielles Embed (Spotify erlaubt externe Steuerung
// nur mit Premium-OAuth - daher steuert man dort im Widget selbst),
// Internet-Radio (Presets + eigene Stream-URL) über <audio> mit Visualizer.
import { esc } from "../utils.js";
import { registerCleanup } from "../windowmanager.js";
import { getSpotifyClientId, spotifyLogin, spotifyLoggedIn, spotifyLogout,
         getSpotifyToken, spotifyApi } from "../spotify.js";

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
    s.onerror = () => reject(new Error("Spotify-SDK nicht erreichbar (Internet nötig)"));
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

const RADIO_STATIONS = [
  { name: "Radio Paradise", genre: "Eclectic Rock", url: "https://stream.radioparadise.com/aac-320", emoji: "🌴" },
  { name: "SomaFM Groove Salad", genre: "Chill / Ambient", url: "https://ice1.somafm.com/groovesalad-128-mp3", emoji: "🥗" },
  { name: "SomaFM DEF CON", genre: "Hacker Electro", url: "https://ice1.somafm.com/defcon-128-mp3", emoji: "💻" },
  { name: "Deutschlandfunk", genre: "Nachrichten", url: "https://st01.sslstream.dlf.de/dlf/01/128/mp3/stream.mp3", emoji: "🗞️" },
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
    s.onerror = () => reject(new Error("YouTube-API nicht erreichbar (Internet nötig)"));
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
        <span style="font-size:20px">🎵</span>
        <input id="ap-url" placeholder="YouTube-Video/-Playlist, Spotify-Link oder Stream-URL einfügen…"
          style="flex:1;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);border-radius:20px;
          color:#e8eefc;padding:9px 16px;font-size:13px;outline:none" />
        <button class="btn-primary" id="ap-load" style="margin:0;width:auto;border-radius:20px">Laden</button>
        <button class="taskbar-btn" id="ap-spotify" style="border-radius:20px;display:none;white-space:nowrap"></button>
      </div>

      <div id="ap-stage" style="flex:1;display:flex;align-items:center;justify-content:center;padding:0 14px;min-height:0"></div>

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
        <button class="ap-btn" id="ap-prev" title="Zurück">⏮</button>
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

  // ---------------- Start-Ansicht (Radio-Presets) ----------------
  function showHome() {
    cleanup();
    currentKind = null;
    titleEl.textContent = ""; subEl.textContent = "";
    controls.style.display = "none";
    progressRow.style.display = "none";
    stage.innerHTML = `
      <div style="max-width:560px;width:100%;text-align:center">
        <div style="font-size:56px;margin-bottom:4px">🎧</div>
        <div style="font-weight:800;font-size:18px;margin-bottom:2px">Was möchtest du hören?</div>
        <div style="color:#8ea2c6;font-size:12px;margin-bottom:16px">
          Oben einen YouTube- oder Spotify-Link einfügen - oder direkt Radio hören:
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px">
          ${RADIO_STATIONS.map((r, i) => `
            <button class="ap-chip" data-radio="${i}">
              <div style="font-size:22px">${r.emoji}</div>
              <div style="font-weight:700;margin:2px 0">📻 ${esc(r.name)}</div>
              <div style="color:#8ea2c6;font-size:11px">${esc(r.genre)}</div>
            </button>`).join("")}
        </div>
      </div>`;
    stage.querySelectorAll("[data-radio]").forEach((b) =>
      b.addEventListener("click", () => {
        const r = RADIO_STATIONS[+b.dataset.radio];
        playStream(r.url, `📻 ${r.name}`, r.genre);
      }));
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
          getOAuthToken: (cb) => { getSpotifyToken().then((t) => cb(t || "")); },
        });
        spDeviceId = await new Promise((resolve, reject) => {
          spPlayer.addListener("ready", ({ device_id }) => resolve(device_id));
          spPlayer.addListener("initialization_error", (e) => reject(new Error(e.message)));
          spPlayer.addListener("authentication_error", (e) => reject(new Error("Anmeldung abgelaufen: " + e.message)));
          spPlayer.addListener("account_error", () =>
            reject(Object.assign(new Error("Spotify Premium nötig für den Vollplayer"), { noPremium: true })));
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
    audio.addEventListener("error", () => { subEl.textContent = "⚠ Stream nicht erreichbar"; });
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
    }
    seeking = false;
  });

  // ---------------- Controls ----------------
  playBtn.addEventListener("click", () => {
    if (currentKind === "youtube" && yt) {
      yt.getPlayerState() === 1 ? yt.pauseVideo() : yt.playVideo();
    } else if (currentKind === "spotify-full" && spPlayer) {
      spPlayer.togglePlay();
    } else if (audio) {
      audio.paused ? audio.play() : audio.pause();
    }
  });
  body.querySelector("#ap-next").addEventListener("click", () => {
    if (currentKind === "youtube") yt?.nextVideo?.();
    else if (currentKind === "spotify-full") spPlayer?.nextTrack?.();
  });
  body.querySelector("#ap-prev").addEventListener("click", () => {
    if (currentKind === "youtube") yt?.previousVideo?.();
    else if (currentKind === "spotify-full") spPlayer?.previousTrack?.();
  });
  shuffleBtn.addEventListener("click", async () => {
    if (currentKind === "youtube" && yt?.setShuffle) {
      shuffleOn = !shuffleOn;
      yt.setShuffle(shuffleOn);
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
    if (spPlayer) spPlayer.setVolume((+volEl.value) / 100).catch(() => {});
  });

  // ---------------- Laden ----------------
  function loadFromInput() {
    const src = parseSource(body.querySelector("#ap-url").value);
    if (!src) { window.notify?.("Link nicht erkannt - YouTube-, Spotify- oder Stream-URL einfügen", "warn"); return; }
    if (src.kind === "youtube") playYouTube(src);
    else if (src.kind === "spotify") playSpotify(src);
    else playStream(src.url, "🎶 Stream", src.url);
  }
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
      spBtn.title = "Spotify-Verbindung trennen";
    } else if (spState.clientId) {
      spBtn.textContent = "🎧 Mit Spotify anmelden";
      spBtn.title = "Spotify-Konto verbinden (Premium = Vollplayer)";
    } else {
      spBtn.textContent = "🎧 Spotify einrichten…";
      spBtn.title = "Spotify-Login ist noch nicht eingerichtet - Klick für Anleitung";
    }
  }
  spBtn.addEventListener("click", async () => {
    if (spotifyLoggedIn()) {
      spotifyLogout();
      if (currentKind === "spotify-full") showHome();
      refreshSpotifyChip();
      window.notify?.("Spotify getrennt", "info");
      return;
    }
    await refreshSpotifyChip();   // Stand frisch holen (Backend evtl. inzwischen aktualisiert)
    if (spState.clientId) {
      try { await spotifyLogin(); }   // leitet den Browser zu Spotify um
      catch (e) { window.notify?.(e.message, "error", 6000); }
      return;
    }
    // Noch nicht eingerichtet -> erklären, was fehlt (statt unsichtbarem Button).
    if (spState.error) {
      window.notify?.(
        "Spotify-Login: Backend-Endpunkt fehlt (/api/auth/spotify-config). " +
        "Bitte auth_routes.py + admin_routes.py aus der Spotify-Lieferung einspielen " +
        "und das Backend neu starten. (" + spState.error + ")", "error", 12000);
    } else {
      window.notify?.(
        "Spotify-Login einrichten: 1) Auf developer.spotify.com eine App anlegen, " +
        "2) dort als Redirect-URI \"" + window.location.origin + "/\" eintragen, " +
        "3) die Client-ID in Einstellungen → Allgemein → Spotify speichern. " +
        "Danach erscheint hier \"Mit Spotify anmelden\".", "info", 14000);
    }
  });
  refreshSpotifyChip();

  // Beim Schließen des Fensters die Wiedergabe stoppen.
  if (win?.key) registerCleanup(win.key, () => {
    cleanup();
    if (_activePlayerStop === cleanup) _activePlayerStop = null;
  });
  showHome();
}

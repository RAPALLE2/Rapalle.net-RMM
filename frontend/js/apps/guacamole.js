// apps/guacamole.js
// -----------------
// Voller Apache-Guacamole-Client im Browser (RDP / VNC / SSH / Telnet).
// Nutzt guacamole-common-js (per CDN in index.html eingebunden -> globales
// "Guacamole"-Objekt) und verbindet sich über den WebSocket-Tunnel des Backends
// (/guac/tunnel) mit guacd, der wiederum RDP/VNC/SSH zum Zielrechner spricht.
//
// Ablauf:
//   1. Kleiner Verbindungsdialog (Protokoll, Host, Port, Zugangsdaten).
//   2. Backend-Token holen (Zugangsdaten bleiben serverseitig).
//   3. Guacamole.Client mit WebSocketTunnel verbinden, Anzeige + Maus/Tastatur.

import { api } from "../api.js";
import { esc } from "../utils.js";

const DEFAULT_PORTS = { rdp: "3389", vnc: "5900", ssh: "22", telnet: "23" };

export function renderGuacamole(body, win) {
  const p = win.props || {};
  const clientId = p.clientId || null;
  const host = p.host || p.ip || "";
  const presetProtocol = p.protocol || (p.platform === "windows" ? "rdp" : "ssh");

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#000">
      <div id="guac-bar-${win.key}" style="display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--panel-2);font-size:12px;flex-wrap:wrap">
        <span id="guac-status-${win.key}" style="color:var(--subtext)">Nicht verbunden</span>
        <span style="flex:1"></span>
        <button class="taskbar-btn" id="guac-cad-${win.key}" style="display:none">Strg+Alt+Entf</button>
        <button class="taskbar-btn" id="guac-disc-${win.key}" style="display:none">Trennen</button>
      </div>

      <div id="guac-form-${win.key}" style="padding:14px;overflow:auto;color:var(--text)">
        <div style="display:grid;grid-template-columns:120px 1fr;gap:8px 10px;max-width:460px">
          <label style="align-self:center;color:var(--subtext)">Protokoll</label>
          <select id="gf-proto-${win.key}" style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)">
            <option value="rdp">RDP (Windows-Desktop)</option>
            <option value="vnc">VNC</option>
            <option value="ssh">SSH (Shell)</option>
            <option value="telnet">Telnet</option>
          </select>
          <label style="align-self:center;color:var(--subtext)">Host / IP</label>
          <input type="text" id="gf-host-${win.key}" value="${esc(host)}" placeholder="192.168.1.50"
            style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
          <label style="align-self:center;color:var(--subtext)">Port</label>
          <input type="text" id="gf-port-${win.key}" value="${DEFAULT_PORTS[presetProtocol] || ""}"
            style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
          <label style="align-self:center;color:var(--subtext)">Benutzer</label>
          <input type="text" id="gf-user-${win.key}" autocomplete="off"
            style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
          <label style="align-self:center;color:var(--subtext)">Passwort</label>
          <input type="password" id="gf-pass-${win.key}" autocomplete="new-password"
            style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
          <label style="align-self:center;color:var(--subtext)" id="gf-dom-label-${win.key}">Domäne</label>
          <input type="text" id="gf-domain-${win.key}" placeholder="(optional)"
            style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)" />
          <label style="align-self:center;color:var(--subtext)">Auflösung</label>
          <select id="gf-res-${win.key}" style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)">
            <option value="1024x768">1024 × 768 (klein, stabil)</option>
            <option value="1280x720" selected>1280 × 720 (Standard)</option>
            <option value="1366x768">1366 × 768</option>
            <option value="1600x900">1600 × 900</option>
            <option value="1920x1080">1920 × 1080 (scharf, mehr Last)</option>
          </select>
          <label style="align-self:center;color:var(--subtext)">Qualität</label>
          <select id="gf-qual-${win.key}" style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)">
            <option value="low" selected>Flüssig (niedrig, empfohlen)</option>
            <option value="balanced">Ausgewogen</option>
            <option value="high">Scharf (hoch, mehr Last)</option>
          </select>
        </div>
        <div id="guac-form-msg-${win.key}" style="margin-top:10px;font-size:12px;color:var(--subtext)"></div>
        <button class="btn-primary" id="gf-connect-${win.key}" style="width:auto;margin-top:12px">🔌 Verbinden</button>
      </div>

      <div id="guac-display-${win.key}" tabindex="0"
        style="flex:1;display:none;align-items:center;justify-content:center;overflow:hidden;position:relative;outline:none"></div>
    </div>
  `;

  const statusEl = body.querySelector(`#guac-status-${win.key}`);
  const formEl = body.querySelector(`#guac-form-${win.key}`);
  const formMsg = body.querySelector(`#guac-form-msg-${win.key}`);
  const displayEl = body.querySelector(`#guac-display-${win.key}`);
  const protoSel = body.querySelector(`#gf-proto-${win.key}`);
  const portInput = body.querySelector(`#gf-port-${win.key}`);
  const connectBtn = body.querySelector(`#gf-connect-${win.key}`);
  const cadBtn = body.querySelector(`#guac-cad-${win.key}`);
  const discBtn = body.querySelector(`#guac-disc-${win.key}`);
  const domLabel = body.querySelector(`#gf-dom-label-${win.key}`);
  const domainInput = body.querySelector(`#gf-domain-${win.key}`);
  const resSel = body.querySelector(`#gf-res-${win.key}`);
  const qualSel = body.querySelector(`#gf-qual-${win.key}`);

  protoSel.value = presetProtocol;
  function syncProtoUI() {
    const proto = protoSel.value;
    if (!portInput.value || Object.values(DEFAULT_PORTS).includes(portInput.value)) {
      portInput.value = DEFAULT_PORTS[proto] || "";
    }
    const showDomain = proto === "rdp";
    domLabel.style.display = showDomain ? "" : "none";
    domainInput.style.display = showDomain ? "" : "none";
  }
  protoSel.addEventListener("change", syncProtoUI);
  syncProtoUI();

  let client = null;
  let keyboard = null;
  let mouse = null;
  let sizePoll = null;
  let lastCW = 0, lastCH = 0;
  let disconnected = false;
  let watchdogTimer = null;
  let lastBeat = Date.now();
  let lastScale = 0;
  let rescalePending = false;

  function stopSizePoll() {
    if (sizePoll) { clearInterval(sizePoll); sizePoll = null; }
  }

  function setStatus(text, color) {
    statusEl.textContent = text;
    statusEl.style.color = color || "var(--subtext)";
  }

  // Zeigt das Formular wieder an, damit man per Klick neu verbinden kann
  // (statt das Fenster tot dastehen zu lassen).
  function showReconnect(reason) {
    try {
      formEl.style.display = "";
      displayEl.style.display = "none";
      connectBtn.disabled = false;
      connectBtn.textContent = "🔄 Neu verbinden";
      discBtn.style.display = "none";
      cadBtn.style.display = "none";
      formMsg.innerHTML = `<span style="color:var(--warn)">${esc(reason || "Getrennt.")} — Du kannst neu verbinden.</span>`;
    } catch {}
  }

  // Watchdog gegen Einfrieren: misst, wie pünktlich ein 1s-Timer feuert. Wird der
  // Hauptthread zu lange blockiert (> 6s), schließt sich das Guacamole-Fenster
  // selbst, damit das restliche RMM-GUI wieder bedienbar wird.
  let _visHandler = null;
  let _stallStrikes = 0;
  function startWatchdog() {
    lastBeat = Date.now();
    _stallStrikes = 0;
    stopWatchdog();
    _visHandler = () => { if (!document.hidden) { lastBeat = Date.now(); _stallStrikes = 0; } };
    document.addEventListener("visibilitychange", _visHandler);
    watchdogTimer = setInterval(() => {
      const now = Date.now();
      const gap = now - lastBeat;
      lastBeat = now;
      if (document.hidden) { _stallStrikes = 0; return; }  // Hintergrund-Tab ignorieren
      if (gap > 10000) {
        // Erst nach ZWEI aufeinanderfolgenden langen Blockaden schließen,
        // damit ein einzelner Ruckler nicht sofort das Fenster killt.
        _stallStrikes++;
        if (_stallStrikes >= 2) {
          console.warn(`[guac] Hauptthread wiederholt >10s blockiert -> schließe Guacamole-Fenster.`);
          forceClose("GUI war mehrfach blockiert – Guacamole wurde automatisch geschlossen.");
        }
      } else {
        _stallStrikes = 0;
      }
    }, 1000);
  }
  function stopWatchdog() {
    if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
    if (_visHandler) { document.removeEventListener("visibilitychange", _visHandler); _visHandler = null; }
  }

  // Sauberes Trennen + Fenster schließen (Notausgang).
  function forceClose(reason) {
    try { setStatus(reason, "var(--danger)"); } catch {}
    cleanupClient();
    import("../windowmanager.js").then(({ closeWindow }) => {
      try { closeWindow(win.key); } catch {}
    });
  }

  function buildParams() {
    const proto = protoSel.value;
    const params = {
      hostname: body.querySelector(`#gf-host-${win.key}`).value.trim(),
      port: portInput.value.trim() || DEFAULT_PORTS[proto] || "",
      username: body.querySelector(`#gf-user-${win.key}`).value,
      password: body.querySelector(`#gf-pass-${win.key}`).value,
    };

    // Auflösung (feste Größe -> sauberer Bildaufbau, im Browser skaliert)
    const [rw, rh] = (resSel.value || "1280x720").split("x").map((n) => parseInt(n, 10) || 0);
    const width = rw || 1280, height = rh || 720;

    if (proto === "rdp") {
      params.domain = domainInput.value.trim();
      params["ignore-cert"] = "true";
      params.security = "any";
      params.width = String(width);
      params.height = String(height);
      params.dpi = "96";

      // Qualität: bestimmt Farbtiefe, Effekte und (nur "hoch") verlustfreie Bilder.
      const q = qualSel.value;
      if (q === "high") {
        params["color-depth"] = "32";
        params["force-lossless"] = "true";
      } else if (q === "balanced") {
        params["color-depth"] = "24";
        params["enable-wallpaper"] = "false";
        params["enable-desktop-composition"] = "false";
      } else {
        // low / flüssig: möglichst wenig Grafiklast
        params["color-depth"] = "16";
        params["enable-wallpaper"] = "false";
        params["enable-theming"] = "false";
        params["enable-full-window-drag"] = "false";
        params["enable-desktop-composition"] = "false";
        params["enable-menu-animations"] = "false";
      }
      params["enable-font-smoothing"] = "true";
    } else if (proto === "vnc") {
      params.width = String(width);
      params.height = String(height);
    }
    return { proto, params, width, height };
  }

  // Lädt guacamole-common-js bei Bedarf nach. Reihenfolge: lokal (offline)
  // zuerst, dann verschiedene CDN-Pfade als Fallback. Sobald window.Guacamole
  // existiert, sind wir fertig.
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("Konnte nicht laden: " + src));
      document.head.appendChild(s);
    });
  }

  async function ensureGuacamoleLoaded() {
    if (window.Guacamole) return true;
    const sources = [
      "/js/vendor/guacamole-common.min.js",                                  // selbst gehostet (offline)
      "https://cdn.jsdelivr.net/npm/guacamole-common-js@1.5.0",              // CDN (main)
      "https://cdn.jsdelivr.net/npm/guacamole-common-js@1.5.0/dist/guacamole-common.min.js",
      "https://unpkg.com/guacamole-common-js@1.5.0",
    ];
    for (const src of sources) {
      try {
        await loadScript(src);
        if (window.Guacamole) return true;
      } catch { /* nächste Quelle versuchen */ }
    }
    return !!window.Guacamole;
  }

  async function connect() {
    const { proto, params, width, height } = buildParams();
    if (!params.hostname) { formMsg.textContent = "Bitte Host/IP angeben."; return; }
<<<<<<< HEAD
    // Alten Client/Keyboard/Mouse IMMER zuerst aufräumen (auch bei Reconnect
    // nach normalem Trennen): sonst bleibt der alte Guacamole.Keyboard auf
    // displayEl registriert und feuert (Closure über "client") zusätzlich an
    // den neuen Client -> jede Taste wird doppelt gesendet, und die alte
    // Session/der alte Tunnel bleiben unbemerkt offen.
    cleanupClient();
=======
>>>>>>> 121be5b1eb8311af2753307a41e15cfe58c4b368
    // Zustand für (Neu-)Verbindung zurücksetzen
    disconnected = false;
    lastScale = 0;
    connectBtn.textContent = "🔌 Verbinden";

    formMsg.textContent = "Lade Guacamole-Bibliothek…";
    if (!(await ensureGuacamoleLoaded())) {
      formMsg.innerHTML = `<span style="color:var(--danger)">guacamole-common-js konnte nicht geladen werden.<br>` +
        `Bei Servern ohne Internet: Datei einmalig herunterladen und unter ` +
        `<code>frontend/js/vendor/guacamole-common.min.js</code> ablegen ` +
        `(siehe Hinweis in der Konsole).</span>`;
      console.error("[guac] guacamole-common-js nicht ladbar. Offline-Lösung: " +
        "Datei von https://cdn.jsdelivr.net/npm/guacamole-common-js@1.5.0 herunterladen " +
        "und als frontend/js/vendor/guacamole-common.min.js speichern.");
      return;
    }
    formMsg.textContent = "Token wird geholt...";
    connectBtn.disabled = true;

    let token;
    try {
      const res = await api.createGuacToken(proto, params, clientId);
      token = res.token;
    } catch (e) {
      formMsg.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
      connectBtn.disabled = false;
      return;
    }

    // Anzeige vorbereiten
    formEl.style.display = "none";
    displayEl.style.display = "flex";
    cadBtn.style.display = proto === "rdp" ? "" : "none";
    discBtn.style.display = "";
    setStatus("Verbinde...", "var(--subtext)");

    const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
    const tunnelUrl = `${wsProto}//${location.host}/guac/tunnel`;
    const tunnel = new Guacamole.WebSocketTunnel(tunnelUrl);
    client = new Guacamole.Client(tunnel);

    const displayElement = client.getDisplay().getElement();
    displayEl.innerHTML = "";
    displayEl.appendChild(displayElement);

    const display = client.getDisplay();

    // Skalierung fit-to-window. Wichtig: gegen Rückkopplungsschleifen absichern
    // (display.scale löst sonst ggf. wieder ein Resize aus -> Endlosschleife ->
    //  eingefrorenes GUI). Deshalb per requestAnimationFrame entkoppeln und nur
    //  bei echter Änderung skalieren.
    function rescale() {
      if (rescalePending) return;
      rescalePending = true;
      requestAnimationFrame(() => {
        rescalePending = false;
        if (!client) return;
        const rw = display.getWidth(), rh = display.getHeight();
        if (!rw || !rh) return;
        const cw = displayEl.clientWidth, ch = displayEl.clientHeight;
        if (!cw || !ch) return;
        const scale = Math.min(cw / rw, ch / rh) || 1;
        if (Math.abs(scale - lastScale) < 0.005) return;   // keine echte Änderung
        lastScale = scale;
        try { display.scale(scale); } catch {}
      });
    }
    display.onresize = rescale;
    // KEIN ResizeObserver (kann sich mit dem Fenster-Layout aufschaukeln ->
    // eingefrorenes GUI). Stattdessen ein leichtes Polling: nur wenn sich die
    // Containergröße wirklich ändert, wird neu skaliert (rückkopplungsfrei).
    function startSizePoll() {
      stopSizePoll();
      lastCW = displayEl.clientWidth; lastCH = displayEl.clientHeight;
      sizePoll = setInterval(() => {
        const cw = displayEl.clientWidth, ch = displayEl.clientHeight;
        if (cw !== lastCW || ch !== lastCH) { lastCW = cw; lastCH = ch; rescale(); }
      }, 500);
    }
    startSizePoll();

    client.onstatechange = (st) => {
      // 0 IDLE,1 CONNECTING,2 WAITING,3 CONNECTED,4 DISCONNECTING,5 DISCONNECTED
      if (st === 3) {
        setStatus(`Verbunden (${proto.toUpperCase()})`, "var(--accent)");
        startWatchdog();   // erst ab hier auf Einfrieren achten (nicht während des Aufbaus)
      } else if (st === 5 && !disconnected) {
<<<<<<< HEAD
        cleanupClient();
=======
        stopWatchdog();
>>>>>>> 121be5b1eb8311af2753307a41e15cfe58c4b368
        setStatus("Verbindung getrennt", "var(--danger)");
        showReconnect("Verbindung wurde getrennt.");
      }
    };
    client.onerror = (err) => {
      stopWatchdog();
      const msg = err && err.message ? err.message : "unbekannt";
      setStatus("Fehler: " + msg, "var(--danger)");
      cleanupClient();
      showReconnect("Fehler: " + msg);
    };

    // Gewählte Auflösung an den Tunnel (guacd-Handshake).
    const connectData = `token=${encodeURIComponent(token)}&GUAC_WIDTH=${width}&GUAC_HEIGHT=${height}&GUAC_DPI=96`;
    try {
      client.connect(connectData);
    } catch (e) {
      setStatus("Verbindung fehlgeschlagen: " + e.message, "var(--danger)");
      showReconnect("Verbindung fehlgeschlagen: " + e.message);
      return;
    }

    // --- Maus (mit Skalierung auf die echte Auflösung) ---
    mouse = new Guacamole.Mouse(displayElement);
    const sendMouse = (mouseState) => {
      try {
        const scale = display.getScale() || 1;
        if (scale && scale !== 1) {
          mouseState.x = mouseState.x / scale;
          mouseState.y = mouseState.y / scale;
        }
        client.sendMouseState(mouseState);
      } catch { /* Maus-Event ignorieren, niemals das GUI blockieren */ }
    };
    mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = sendMouse;

    // --- Tastatur (nur wenn der Anzeigebereich fokussiert ist) ---
    keyboard = new Guacamole.Keyboard(displayEl);
    keyboard.onkeydown = (sym) => { try { client.sendKeyEvent(1, sym); } catch {} };
    keyboard.onkeyup = (sym) => { try { client.sendKeyEvent(0, sym); } catch {} };
    displayEl.addEventListener("mouseenter", () => displayEl.focus());
    displayEl.focus();
  }

  function cleanupClient() {
    disconnected = true;
    stopWatchdog();
    stopSizePoll();
    try { if (keyboard) { keyboard.onkeydown = null; keyboard.onkeyup = null; } } catch {}
    try { if (mouse) { mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = null; } } catch {}
    try { if (client) client.disconnect(); } catch {}
    client = null;
  }

  connectBtn.addEventListener("click", connect);
  discBtn.addEventListener("click", () => { cleanupClient(); setStatus("Getrennt", "var(--subtext)"); });
  cadBtn.addEventListener("click", () => {
    if (!client) return;
    // Strg+Alt+Entf: Keysyms 0xFFE3 (Ctrl), 0xFFE9 (Alt), 0xFFFF (Delete)
    [0xFFE3, 0xFFE9, 0xFFFF].forEach((k) => client.sendKeyEvent(1, k));
    [0xFFFF, 0xFFE9, 0xFFE3].forEach((k) => client.sendKeyEvent(0, k));
  });

  // Aufräumen, wenn das Fenster geschlossen wird
  import("../windowmanager.js").then(({ registerCleanup }) => {
    registerCleanup(win.key, cleanupClient);
  });

  // guacd-Verfügbarkeit prüfen und ggf. Hinweis anzeigen
  api.guacStatus().then((s) => {
    if (!s.available) {
      formMsg.innerHTML = `<span style="color:var(--warn)">Hinweis: guacd ist derzeit nicht erreichbar. ` +
        `Richte es unter <b>Einstellungen → Allgemein → Remote-Gateway (Guacamole)</b> ` +
        `mit einem Klick ein (installiert Docker und startet guacd automatisch).</span>`;
    }
  }).catch(() => {});
}

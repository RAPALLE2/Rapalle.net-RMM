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
        <span class="guac-tools-${win.key}" style="display:none;align-items:center;gap:6px">
          <button class="taskbar-btn" id="guac-mod-ctrl-${win.key}" title="Strg gedrückt halten (Toggle) · Doppelklick = einmal tippen">Strg</button>
          <button class="taskbar-btn" id="guac-mod-alt-${win.key}" title="Alt gedrückt halten (Toggle) · Doppelklick = einmal tippen">Alt</button>
          <button class="taskbar-btn" id="guac-mod-win-${win.key}" title="Windows-Taste gedrückt halten (Toggle) · Doppelklick = einmal tippen">Win</button>
          <button class="taskbar-btn" id="guac-key-tab-${win.key}" title="Tab senden">Tab</button>
          <button class="taskbar-btn" id="guac-key-esc-${win.key}" title="Esc senden">Esc</button>
        </span>
        <button class="taskbar-btn" id="guac-cad-${win.key}" style="display:none">Strg+Alt+Entf</button>
        <span class="guac-tools-${win.key}" style="display:none;align-items:center;gap:6px">
          <button class="taskbar-btn" id="guac-clip-send-${win.key}" title="Lokale Zwischenablage an den Remote-PC senden">📋→</button>
          <button class="taskbar-btn" id="guac-clip-get-${win.key}" title="Zuletzt vom Remote-PC empfangene Zwischenablage übernehmen">→📋</button>
        </span>
        <button class="taskbar-btn" id="guac-disc-${win.key}" style="display:none">Trennen</button>
      </div>

      <div id="guac-form-${win.key}" style="padding:14px;overflow:auto;color:var(--text)">
        <div style="display:grid;grid-template-columns:120px 1fr;gap:8px 10px;max-width:460px">
          ${clientId ? `
          <label style="align-self:center;color:var(--subtext)">Login</label>
          <div style="display:flex;gap:6px">
            <select id="gf-profile-${win.key}" title="Gespeichertes Login auswählen"
              style="flex:1;padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)">
              <option value="">— Login auswählen —</option>
            </select>
            <button class="taskbar-btn" id="gf-profile-del-${win.key}" title="Gewähltes Login löschen">🗑️</button>
          </div>` : ""}
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
          <label style="align-self:center;color:var(--subtext)" id="gf-res-label-${win.key}">Auflösung</label>
          <select id="gf-res-${win.key}" style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)">
            <option value="1024x768">1024 × 768 (klein, stabil)</option>
            <option value="1280x720" selected>1280 × 720 (Standard)</option>
            <option value="1366x768">1366 × 768</option>
            <option value="1600x900">1600 × 900</option>
            <option value="1920x1080">1920 × 1080 (scharf, mehr Last)</option>
          </select>
          <label style="align-self:center;color:var(--subtext)" id="gf-qual-label-${win.key}">Qualität</label>
          <select id="gf-qual-${win.key}" style="padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text)">
            <option value="low" selected>Flüssig (niedrig, empfohlen)</option>
            <option value="balanced">Ausgewogen</option>
            <option value="high">Scharf (hoch, mehr Last)</option>
          </select>
        </div>
        <div id="guac-form-msg-${win.key}" style="margin-top:10px;font-size:12px;color:var(--subtext)"></div>
        <div style="display:flex;gap:8px;margin-top:12px">
          <button class="btn-primary" id="gf-connect-${win.key}" style="width:auto">🔌 Verbinden</button>
          ${clientId ? `<button class="action-btn" id="gf-save-${win.key}" title="Login für diesen Client speichern (inkl. Passwort)">💾 Login speichern</button>` : ""}
        </div>
      </div>

      <div id="guac-display-${win.key}" tabindex="0"
        style="flex:1;display:none;align-items:center;justify-content:center;overflow:hidden;position:relative;outline:none"></div>

      <div id="guac-sendbar-${win.key}" style="flex:none;display:none;align-items:center;gap:6px;padding:6px 10px;background:var(--panel-2);font-size:12px;border-top:1px solid var(--border)">
        <span style="color:var(--subtext)">Text senden:</span>
        <select id="guac-layout-${win.key}" title="Tastaturlayout: Zeichen werden als layout-unabhängige Keysyms gesendet - 1:1 ist fast immer richtig"
          style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px">
          <option value="raw">1:1 (empfohlen)</option>
          <option value="us">US-Layout</option>
          <option value="de">DE-Layout</option>
        </select>
        <input type="text" id="guac-text-${win.key}" placeholder="Text eingeben, dann Senden oder Enter..."
          style="flex:1;padding:5px 8px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text)" />
        <label style="color:var(--subtext);display:flex;align-items:center;gap:4px" title="Nach dem Text zusätzlich Enter senden">
          <input type="checkbox" id="guac-enter-${win.key}" /> +Enter
        </label>
        <button class="taskbar-btn" id="guac-send-${win.key}">Senden</button>
      </div>
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

  const qualLabel = body.querySelector(`#gf-qual-label-${win.key}`);
  const resLabel = body.querySelector(`#gf-res-label-${win.key}`);

  protoSel.value = presetProtocol;
  function syncProtoUI() {
    const proto = protoSel.value;
    if (!portInput.value || Object.values(DEFAULT_PORTS).includes(portInput.value)) {
      portInput.value = DEFAULT_PORTS[proto] || "";
    }
    const showDomain = proto === "rdp";
    domLabel.style.display = showDomain ? "" : "none";
    domainInput.style.display = showDomain ? "" : "none";
    // Qualität und Auflösung sind nur für Bild-Protokolle (RDP/VNC) relevant.
    // Bei SSH/Telnet rendert guacd ein Terminal - beides ausblenden (die
    // Terminalgröße wird automatisch aus der Fenstergröße bestimmt).
    const gfx = proto === "rdp" || proto === "vnc";
    if (qualLabel) qualLabel.style.display = gfx ? "" : "none";
    qualSel.style.display = gfx ? "" : "none";
    if (resLabel) resLabel.style.display = gfx ? "" : "none";
    resSel.style.display = gfx ? "" : "none";
  }
  protoSel.addEventListener("change", syncProtoUI);
  syncProtoUI();

  // ---------------------------------------------------------------
  // Zusatz-Werkzeuge (wie beim Remote Screen): Sticky-Modifier, Tab/Esc,
  // Clipboard-Sync, Text-Sendeleiste. Sichtbar erst ab Verbindung.
  // ---------------------------------------------------------------
  const KEYSYM = { Control: 0xFFE3, Alt: 0xFFE9, Meta: 0xFFEB,
                   Tab: 0xFF09, Escape: 0xFF1B, Delete: 0xFFFF, Enter: 0xFF0D };
  const toolSpans = body.querySelectorAll(`.guac-tools-${win.key}`);
  const sendBar = body.querySelector(`#guac-sendbar-${win.key}`);
  const guacTextInput = body.querySelector(`#guac-text-${win.key}`);
  const guacEnterChk = body.querySelector(`#guac-enter-${win.key}`);
  const guacModState = { Control: false, Alt: false, Meta: false };
  const guacModBtns = {
    Control: body.querySelector(`#guac-mod-ctrl-${win.key}`),
    Alt: body.querySelector(`#guac-mod-alt-${win.key}`),
    Meta: body.querySelector(`#guac-mod-win-${win.key}`),
  };
  let remoteClipboard = "";     // zuletzt vom Remote-PC empfangene Zwischenablage

  function showTools(show) {
    toolSpans.forEach((el) => { el.style.display = show ? "inline-flex" : "none"; });
    if (sendBar) sendBar.style.display = show ? "flex" : "none";
  }
  function updateGuacModBtn(name) {
    const b = guacModBtns[name];
    if (!b) return;
    b.style.background = guacModState[name] ? "var(--accent)" : "";
    b.style.color = guacModState[name] ? "#0b0f14" : "";
  }
  // Alle gehaltenen Modifier loslassen (bei Trennen/Fensterschluss wichtig,
  // damit auf dem Remote-PC keine Taste "hängen" bleibt).
  function releaseGuacMods(sendUp = true) {
    Object.keys(guacModState).forEach((name) => {
      if (guacModState[name] && sendUp) {
        try { client?.sendKeyEvent(0, KEYSYM[name]); } catch {}
      }
      guacModState[name] = false;
      updateGuacModBtn(name);
    });
  }
  Object.keys(guacModBtns).forEach((name) => {
    guacModBtns[name]?.addEventListener("click", () => {
      if (!client) return;
      guacModState[name] = !guacModState[name];
      try { client.sendKeyEvent(guacModState[name] ? 1 : 0, KEYSYM[name]); } catch {}
      updateGuacModBtn(name);
      displayEl.focus();
    });
    // Doppelklick: Taste sofort einmal tippen (z.B. Win -> Startmenü). Die
    // beiden Einzelklicks davor haben gedrückt+losgelassen - Zustand ist
    // danach sauber "aus", hier nur noch der Tipp.
    guacModBtns[name]?.addEventListener("dblclick", (e) => {
      e.preventDefault();
      if (!client) return;
      releaseGuacMods();
      try { client.sendKeyEvent(1, KEYSYM[name]); client.sendKeyEvent(0, KEYSYM[name]); } catch {}
      displayEl.focus();
    });
  });
  function guacTapKey(sym) {
    if (!client) return;
    try { client.sendKeyEvent(1, sym); client.sendKeyEvent(0, sym); } catch {}
    displayEl.focus();
  }
  body.querySelector(`#guac-key-tab-${win.key}`)?.addEventListener("click", () => guacTapKey(KEYSYM.Tab));
  body.querySelector(`#guac-key-esc-${win.key}`)?.addEventListener("click", () => guacTapKey(KEYSYM.Escape));

  // Zeichen -> X11-Keysym (layout-unabhängig): ASCII/Latin-1 direkt,
  // alle anderen Unicode-Zeichen über den 0x01000000-Bereich.
  function keysymFromChar(ch) {
    const cp = ch.codePointAt(0);
    if (cp === 10 || cp === 13) return KEYSYM.Enter;
    if (cp === 9) return KEYSYM.Tab;
    return cp < 0x100 ? cp : (0x01000000 | cp);
  }
  function guacSendText() {
    if (!client || !guacTextInput) return;
    const text = guacTextInput.value;
    if (!text) return;
    for (const ch of text) guacTapKey(keysymFromChar(ch));
    if (guacEnterChk?.checked) guacTapKey(KEYSYM.Enter);
    guacTextInput.value = "";
    guacTextInput.focus();
  }
  body.querySelector(`#guac-send-${win.key}`)?.addEventListener("click", guacSendText);
  guacTextInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); guacSendText(); }
  });

  // --- Zwischenablage synchronisieren ---
  // Senden: lokale Zwischenablage lesen und als Guacamole-Clipboard-Stream
  // an den Remote-PC schicken (guacd setzt sie dort in der Session).
  body.querySelector(`#guac-clip-send-${win.key}`)?.addEventListener("click", async () => {
    if (!client) return;
    let text = "";
    try { text = await navigator.clipboard.readText(); } catch {}
    if (!text) {
      window.notify?.("Lokale Zwischenablage ist leer oder Zugriff verweigert (HTTPS nötig).", "warn");
      return;
    }
    try {
      const stream = client.createClipboardStream("text/plain");
      const writer = new Guacamole.StringWriter(stream);
      writer.sendText(text);
      writer.sendEnd();
      window.notify?.("Zwischenablage an Remote-Session gesendet", "success");
    } catch (e) {
      window.notify?.("Senden fehlgeschlagen: " + e.message, "error");
    }
    displayEl.focus();
  });
  // Holen: guacd pusht Clipboard-Änderungen der Remote-Session automatisch
  // (client.onclipboard, siehe connect()). Der Button übernimmt den zuletzt
  // empfangenen Stand in die lokale Zwischenablage.
  body.querySelector(`#guac-clip-get-${win.key}`)?.addEventListener("click", async () => {
    if (!remoteClipboard) {
      window.notify?.("Noch nichts vom Remote-PC empfangen - dort erst etwas kopieren.", "warn");
      return;
    }
    try {
      await navigator.clipboard.writeText(remoteClipboard);
      window.notify?.("Remote-Zwischenablage übernommen (" + remoteClipboard.length + " Zeichen)", "success");
    } catch {
      prompt("Remote-Zwischenablage (Strg+C zum Kopieren):", remoteClipboard);
    }
  });

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
  // 1:1-Bildschirmaufzeichnung (genau das, was der Nutzer sieht) via MediaRecorder
  let mediaRecorder = null;
  let recChunks = [];
  let recStartedAt = 0;

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

  // Watchdog gegen Einfrieren: misst, wie pünktlich ein 1s-Timer feuert. Ein
  // kurzzeitig durch hohe Render-Last blockierter Hauptthread darf die laufende
  // RDP-Session NICHT mehr automatisch schließen (das war ein Hauptgrund für
  // "schließt nach kurzer Zeit"). Er warnt nur noch dezent; echte Abbrüche
  // behandeln onerror / onstatechange(state 5).
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
        _stallStrikes++;
        if (_stallStrikes >= 2) {
          console.warn("[guac] Hauptthread mehrfach kurz blockiert (hohe Render-Last) – Session bleibt bestehen.");
          try { setStatus("Hohe Last – Bild kann kurz ruckeln", "var(--warn)"); } catch {}
          _stallStrikes = 0;   // nur warnen, NICHT schließen
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

  // Sauberes Trennen + Fenster schließen. Nur noch manuell/als Notausgang
  // verfügbar - der Watchdog ruft das bewusst NICHT mehr automatisch auf.
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
        // low / flüssig: möglichst wenig Grafiklast.
        // color-depth 24 statt 16: 16bpp erzeugt auf modernen Windows-RDP-Servern
        // sichtbares Banding/Geisterbilder; 24bpp ist mit abgeschalteten Effekten
        // kaum teurer, aber deutlich sauberer.
        params["color-depth"] = "24";
        params["enable-wallpaper"] = "false";
        params["enable-theming"] = "false";
        params["enable-full-window-drag"] = "false";
        params["enable-desktop-composition"] = "false";
        params["enable-menu-animations"] = "false";
      }
      params["enable-font-smoothing"] = "true";

      // GEGEN FRAME-LAYERING / GEISTERBILDER ("alte Frames bleiben stehen"):
      // guacd wiederverwendet sonst gecachte Bitmaps, Glyphen und Offscreen-
      // Surfaces. Wenn dieser Cache und der tatsächliche Bildschirminhalt
      // auseinanderlaufen, werden geänderte Bereiche NICHT sauber neu gezeichnet
      // -> alte Inhalte "kleben" unter/neben dem neuen Bild. Das Abschalten der
      // drei RDP-Caches zwingt guacd, geänderte Regionen immer frisch zu senden.
      // Kostet etwas mehr Bandbreite, liefert aber ein vollständig aktualisiertes,
      // artefaktfreies Bild.
      params["disable-bitmap-caching"] = "true";
      params["disable-offscreen-caching"] = "true";
      params["disable-glyph-caching"] = "true";

      // GEGEN DIE BLOCK-/LAYERING-ARTEFAKTE (blaue Blöcke, stehengebliebene
      // Regionen, Cursor-Reste) auf modernen Windows-Servern:
      // guacd 1.6.0 aktiviert die RDP Graphics Pipeline Extension (GFX/RDPGFX)
      // STANDARDMÄSSIG. Auf Windows Server (2019/2022) erzeugt GFX aber genau
      // diese Grafikfehler - offiziell dokumentiert ("if you find it causing
      // problems, disable it") und im Apache-Bugtracker (GUACAMOLE-1863) sowie
      // in aktuellen Setups (guacd 1.6.0 + Windows Server) bestätigt: GFX aus ->
      // guacd fällt auf den stabilen Basis-Grafikpfad zurück, Artefakte weg.
      // Der Parameter wurde in guacd von "enable-gfx" auf "disable-gfx" umbenannt;
      // wir setzen beide Schreibweisen - der Handshake sendet nur die, die die
      // installierte guacd-Version tatsächlich anfordert, die andere wird ignoriert.
      params["disable-gfx"] = "true";        // ab GFX-Default (1.6.0)
      params["enable-gfx"] = "false";        // ältere/alternative Schreibweise
      params["enable-gfx-h264"] = "false";   // H.264/AVC-Variante ebenfalls aus
    } else if (proto === "vnc") {
      params.width = String(width);
      params.height = String(height);
      // Qualität -> Farbtiefe (guacd-VNC): weniger Farben = weniger Bandbreite.
      const q = qualSel.value;
      params["color-depth"] = q === "high" ? "32" : q === "balanced" ? "24" : "16";
      // Cursor lokal rendern lassen (flüssiger); Server, die das nicht können,
      // ignorieren den Parameter.
      params.cursor = "local";
    }
    return { proto, params, width, height };
  }

  // Lädt guacamole-common-js bei Bedarf nach. WICHTIG: Das npm-Paket 1.5.0
  // enthält KEIN klassisches Browser-Bundle mehr - nur noch:
  //   dist/esm/guacamole-common(.min).js  -> ES-Modul (braucht import())
  //   dist/cjs/guacamole-common(.min).js  -> CommonJS (braucht module-Shim,
  //                                          als <script> geladen: "module is
  //                                          not defined")
  // Reihenfolge: lokal (offline) zuerst, dann CDN. Sobald window.Guacamole
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

  // Prüft, ob ein Kandidat wie das Guacamole-API aussieht, und macht ihn global.
  function adoptGuacamole(candidate) {
    const G = candidate?.default ?? candidate?.Guacamole ?? candidate;
    if (G && G.Client && G.WebSocketTunnel) { window.Guacamole = G; return true; }
    return !!(window.Guacamole && window.Guacamole.Client);
  }

  // ESM-Build per dynamischem import() laden.
  async function loadEsm(src) {
    const mod = await import(/* @vite-ignore */ src);
    return adoptGuacamole(mod);
  }

  // CJS-Build laden: Quelltext holen und mit module/exports-Shim ausführen
  // (direkt als <script> eingebunden wirft er "module is not defined").
  async function loadCjs(src) {
    const res = await fetch(src);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const code = await res.text();
    const module = { exports: {} };
    new Function("module", "exports", code)(module, module.exports);
    return adoptGuacamole(module.exports);
  }

  async function ensureGuacamoleLoaded() {
    if (window.Guacamole) return true;
    const attempts = [
      // 1) Selbst gehostet (offline-fähig, empfohlen):
      //    UMD/klassisches Script ODER ESM - beide Varianten werden probiert.
      () => loadScript("/js/vendor/guacamole-common.min.js").then(() => adoptGuacamole(window.Guacamole)),
      () => loadEsm("/js/vendor/guacamole-common.min.js"),
      () => loadCjs("/js/vendor/guacamole-common.min.js"),
      // 2) CDN, ESM-Build (existiert in 1.5.0 wirklich - dist/guacamole-common.min.js gibt es NICHT mehr!)
      () => loadEsm("https://cdn.jsdelivr.net/npm/guacamole-common-js@1.5.0/dist/esm/guacamole-common.min.js"),
      () => loadEsm("https://unpkg.com/guacamole-common-js@1.5.0/dist/esm/guacamole-common.min.js"),
      // 3) CDN, CJS-Build mit Shim (letzter Fallback)
      () => loadCjs("https://cdn.jsdelivr.net/npm/guacamole-common-js@1.5.0/dist/cjs/guacamole-common.min.js"),
      () => loadCjs("https://unpkg.com/guacamole-common-js@1.5.0/dist/cjs/guacamole-common.min.js"),
    ];
    for (const attempt of attempts) {
      try {
        if (await attempt()) return true;
      } catch { /* nächste Quelle versuchen */ }
      if (window.Guacamole && window.Guacamole.Client) return true;
    }
    return !!window.Guacamole;
  }

  async function connect() {
    const { proto, params, width, height } = buildParams();
    if (!params.hostname) { formMsg.textContent = "Bitte Host/IP angeben."; return; }
    // Alten Client/Keyboard/Mouse IMMER zuerst aufräumen (auch bei Reconnect
    // nach normalem Trennen): sonst bleibt der alte Guacamole.Keyboard auf
    // displayEl registriert und feuert (Closure über "client") zusätzlich an
    // den neuen Client -> jede Taste wird doppelt gesendet, und die alte
    // Session/der alte Tunnel bleiben unbemerkt offen.
    cleanupClient();
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
      console.error("[guac] guacamole-common-js nicht ladbar. Offline-Lösung: Datei von " +
        "https://cdn.jsdelivr.net/npm/guacamole-common-js@1.5.0/dist/esm/guacamole-common.min.js " +
        "herunterladen und als frontend/js/vendor/guacamole-common.min.js speichern " +
        "(ESM wird unterstützt; alternativ funktioniert auch jedes klassische UMD-Bundle).");
      return;
    }
    formMsg.textContent = "Token wird geholt...";
    connectBtn.disabled = true;

    let token;
    try {
      const res = await api.createGuacToken(proto, params, clientId, params.hostname || p.clientName || host);
      token = res.token;
    } catch (e) {
      formMsg.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
      connectBtn.disabled = false;
      return;
    }

    // Anzeige vorbereiten
    formEl.style.display = "none";
    displayEl.style.display = "flex";
    cadBtn.style.display = "";     // Strg+Alt+Entf für alle Protokolle verfügbar
    discBtn.style.display = "";
    setStatus("Verbinde...", "var(--subtext)");

    const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
    const tunnelUrl = `${wsProto}//${location.host}/guac/tunnel`;
    const tunnel = new Guacamole.WebSocketTunnel(tunnelUrl);
    // guacamole-common-js schließt den Tunnel per Default schon nach 15 s ohne
    // empfangene Daten (receiveTimeout) und meldet nach 1,5 s "instabil". Bei
    // kurzen Puffer-/Netzwerk-Hängern (Reverse-Proxy, WLAN) trennt das sonst
    // vorschnell -> großzügiger einstellen. guacd sendet regelmäßig sync-Pings,
    // solange der Weg frei ist, also ist ein längeres Fenster unkritisch.
    try {
      tunnel.receiveTimeout = 30000;
      tunnel.unstableThreshold = 6000;
    } catch {}
    client = new Guacamole.Client(tunnel);

    // Clipboard-Änderungen der Remote-Session empfangen und zwischenspeichern
    // (Übernahme in die lokale Zwischenablage per →📋-Button, da Browser
    // Schreibzugriffe nur nach Nutzeraktion erlauben).
    client.onclipboard = (stream, mimetype) => {
      if (!/^text\//.test(mimetype)) { try { stream.sendAck("OK", 0); } catch {} return; }
      try {
        const reader = new Guacamole.StringReader(stream);
        let buf = "";
        reader.ontext = (t) => { buf += t; };
        reader.onend = () => { remoteClipboard = buf; };
      } catch {}
    };

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
        showTools(true);
        startWatchdog();   // erst ab hier auf Einfrieren achten (nicht während des Aufbaus)
        // 1:1-Aufzeichnung starten (nur wenn an einen Client gebunden, da die
        // Aufzeichnung diesem zugeordnet und rechtegeprüft gespeichert wird).
        // Gilt für ALLE Protokolle: RDP, VNC und auch SSH/Telnet - guacd
        // rendert das Terminal als Bild auf die Canvas, die hier mitgeschnitten
        // wird. SSH/Telnet-Sitzungen werden damit als Video-Replay geloggt
        // statt als einzelne Befehle/Ausgaben.
        if (clientId) setTimeout(() => startScreenRecording(display), 600);
      } else if (st === 5 && !disconnected) {
        cleanupClient();
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
        // WICHTIG: mouseState NIEMALS mutieren! Guacamole.Mouse verwendet
        // intern EIN wiederverwendetes State-Objekt für alle Events. Wird
        // hier direkt durch die Skalierung geteilt, teilt jedes weitere
        // Event (z.B. mouseup direkt nach mousedown, ohne Move dazwischen)
        // DENSELBEN Wert erneut -> Koordinaten schrumpfen pro Klick Richtung
        // (0,0): "Cursor wandert bei jedem Klick in die Ecke, springt beim
        // Bewegen zurück". Deshalb: skalierte KOPIE senden.
        const scale = display.getScale() || 1;
        const scaled = new Guacamole.Mouse.State(
          mouseState.x / scale,
          mouseState.y / scale,
          mouseState.left,
          mouseState.middle,
          mouseState.right,
          mouseState.up,
          mouseState.down
        );
        client.sendMouseState(scaled);
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

  // -----------------------------------------------------------------
  // 1:1-Bildschirmaufzeichnung: greift die tatsächlich gerenderte Guacamole-
  // Canvas (das, was der Nutzer sieht) per captureStream ab und nimmt sie mit
  // MediaRecorder als WebM auf. Beim Trennen wird das Video hochgeladen.
  // -----------------------------------------------------------------
  function startScreenRecording(display) {
    if (mediaRecorder) return;
    try {
      if (!window.MediaRecorder || !display) return;
      const layer = display.getDefaultLayer && display.getDefaultLayer();
      const canvas = layer && layer.getCanvas && layer.getCanvas();
      if (!canvas || !canvas.captureStream) return;
      const stream = canvas.captureStream(12);   // 12 Bilder/s
      let mime = "video/webm;codecs=vp9";
      if (!MediaRecorder.isTypeSupported(mime)) mime = "video/webm;codecs=vp8";
      if (!MediaRecorder.isTypeSupported(mime)) mime = "video/webm";
      recChunks = [];
      mediaRecorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 2500000 });
      mediaRecorder.ondataavailable = (e) => { if (e.data && e.data.size) recChunks.push(e.data); };
      recStartedAt = Date.now();
      mediaRecorder.start(2000);   // alle 2 s ein Datenblock (robust bei langen Sessions)
    } catch (e) {
      mediaRecorder = null;
      console.warn("[guac] Aufzeichnung konnte nicht starten:", e);
    }
  }

  async function stopScreenRecording() {
    const mr = mediaRecorder;
    mediaRecorder = null;
    if (!mr) return;
    try {
      const stopped = new Promise((res) => { mr.onstop = res; });
      if (mr.state !== "inactive") mr.stop();
      await stopped;
      if (!recChunks.length) return;
      const blob = new Blob(recChunks, { type: "video/webm" });
      recChunks = [];
      const hostname = (body.querySelector(`#gf-host-${win.key}`)?.value || p.clientName || "").trim();
      await api.uploadRecordingVideo(clientId || "guac", hostname, recStartedAt, Date.now(), blob);
      window.notify?.("Aufzeichnung gespeichert", "success");
    } catch (e) {
      console.warn("[guac] Aufzeichnung-Upload fehlgeschlagen:", e);
    }
  }

  function cleanupClient() {
    releaseGuacMods();       // gehaltene Strg/Alt/Win-Tasten sauber loslassen
    showTools(false);
    disconnected = true;
    stopScreenRecording();   // Aufnahme flushen + hochladen (fire-and-forget)
    stopWatchdog();
    stopSizePoll();
    try { if (keyboard) { keyboard.onkeydown = null; keyboard.onkeyup = null; } } catch {}
    try { if (mouse) { mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = null; } } catch {}
    try { if (client) client.disconnect(); } catch {}
    client = null;
  }

  connectBtn.addEventListener("click", connect);
  discBtn.addEventListener("click", () => { cleanupClient(); setStatus("Getrennt", "var(--subtext)"); });

  // --- Gespeicherte Logins (MEHRERE pro Client, MIT Passwort) ---
  // Auswahl-Dropdown neben dem Speichern-Button: Logins werden getrennt je
  // Protokoll benannt (z.B. eigenes Login für SSH/Telnet und eines für RDP).
  const userInput = body.querySelector(`#gf-user-${win.key}`);
  const passInput = body.querySelector(`#gf-pass-${win.key}`);
  const hostInput = body.querySelector(`#gf-host-${win.key}`);
  const saveBtn = body.querySelector(`#gf-save-${win.key}`);
  const profileSel = body.querySelector(`#gf-profile-${win.key}`);
  const profileDelBtn = body.querySelector(`#gf-profile-del-${win.key}`);
  let savedProfiles = [];

  function applyProfile(prof) {
    if (!prof) return;
    if (prof.protocol) protoSel.value = prof.protocol;
    if (prof.host) hostInput.value = prof.host;
    if (prof.port) portInput.value = prof.port;
    userInput.value = prof.username || "";
    passInput.value = prof.password || "";
    domainInput.value = prof.domain || "";
    if (prof.resolution) resSel.value = prof.resolution;
    if (prof.quality) qualSel.value = prof.quality;
    syncProtoUI();
    formMsg.innerHTML = `<span style="color:var(--subtext)">Login „${esc(prof.name || "")}" geladen${prof.password ? " (inkl. Passwort)" : " (ohne Passwort)"}.</span>`;
  }

  function renderProfileOptions(selectedId) {
    if (!profileSel) return;
    profileSel.innerHTML = `<option value="">— Login auswählen —</option>` +
      savedProfiles.map((pr) =>
        `<option value="${esc(pr.id)}">${esc(pr.name || "?")} · ${esc((pr.protocol || "").toUpperCase())}</option>`
      ).join("");
    if (selectedId) profileSel.value = selectedId;
  }

  async function loadProfiles(preselectId) {
    if (!clientId || !profileSel) return;
    try {
      const res = await api.listGuacProfiles(clientId);
      savedProfiles = res.profiles || [];
      renderProfileOptions(preselectId);
      if (preselectId) {
        applyProfile(savedProfiles.find((pr) => pr.id === preselectId));
        return;
      }
      // Passendes Login zum voreingestellten Protokoll automatisch anwenden.
      const match = savedProfiles.find((pr) => pr.protocol === protoSel.value) || null;
      if (match) { profileSel.value = match.id; applyProfile(match); }
      else if (!savedProfiles.length) {
        // Rückwärtskompatibilität: altes Einzel-Profil (ohne Passwort) laden.
        api.getGuacProfile(clientId).then((r) => {
          if (r && r.profile) applyProfile({ ...r.profile, name: "Alt-Profil" });
        }).catch(() => {});
      }
    } catch { /* Liste optional */ }
  }

  profileSel?.addEventListener("change", () => {
    applyProfile(savedProfiles.find((pr) => pr.id === profileSel.value));
  });

  profileDelBtn?.addEventListener("click", async () => {
    const id = profileSel?.value;
    if (!id) { window.notify?.("Kein Login ausgewählt.", "warn"); return; }
    const prof = savedProfiles.find((pr) => pr.id === id);
    if (!confirm(`Login „${prof?.name || id}" wirklich löschen?`)) return;
    try {
      await api.deleteGuacProfile(clientId, id);
      window.notify?.("Login gelöscht", "success");
      loadProfiles();
    } catch (e) {
      window.notify?.("Löschen fehlgeschlagen: " + e.message, "error");
    }
  });

  if (clientId) loadProfiles();

  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const proto = protoSel.value;
      const defName = `${proto.toUpperCase()} ${userInput.value ? userInput.value + "@" : ""}${hostInput.value.trim()}`;
      const name = prompt("Name für dieses Login:", defName);
      if (name === null) return;
      const profile = {
        name: (name || defName).trim(),
        protocol: proto,
        host: hostInput.value.trim(),
        port: portInput.value.trim(),
        username: userInput.value,
        password: passInput.value,          // wird MIT gespeichert
        domain: domainInput.value.trim(),
        resolution: resSel.value,
        quality: qualSel.value,
      };
      try {
        const res = await api.addGuacProfile(clientId, profile);
        window.notify?.("Guacamole-Login gespeichert (inkl. Passwort)", "success");
        loadProfiles(res?.profile?.id);
      } catch (e) {
        window.notify?.("Speichern fehlgeschlagen: " + e.message, "error");
      }
    });
  }

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

// apps/vnc.js
// -----------
// Vollwertiger Remote Screen: empfängt Live-Bildschirm-Frames vom Agenten
// (als JPEG-Base64 über Socket.IO) und zeigt sie an. Maus- und Tastatur-
// Eingaben werden zurück an den Agenten geschickt, der sie lokal ausführt.
//
// Ablauf:
//   1. Fenster öffnet -> "screen-start" ans Backend -> Agent beginnt zu streamen
//   2. Eingehende "screen-frame" Events -> Bild ins <img> setzen
//   3. Maus/Tastatur im Bild -> Koordinaten umrechnen -> "screen-input" senden
//   4. Fenster schließt -> "screen-stop" senden (siehe cleanup in app.js)

import { dashboardSocket } from "../socket.js";
import { registerCleanup } from "../windowmanager.js";
import { state } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";
import { renderTerminal } from "./terminal.js";

export function renderVnc(body, win) {
  const { clientId, clientName } = win.props;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#000">
      <div style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:var(--panel-2);font-size:12px">
        <span id="vnc-status-${win.key}" style="color:var(--subtext)">Verbinde...</span>
        <span style="flex:1"></span>
        <label style="color:var(--subtext);display:flex;align-items:center;gap:4px">
          <input type="checkbox" id="vnc-control-${win.key}" checked /> Steuerung aktiv
        </label>
        <button class="taskbar-btn" id="vnc-ctrlaltdel-${win.key}">Strg+Alt+Entf</button>
      </div>
      <div style="flex:1;overflow:hidden;display:flex;align-items:center;justify-content:center;position:relative">
        <img id="vnc-img-${win.key}" style="max-width:100%;max-height:100%;object-fit:contain;cursor:crosshair"
             tabindex="0" />
      </div>
      <div style="display:flex;align-items:center;gap:6px;padding:6px 10px;background:var(--panel-2);font-size:12px">
        <span style="color:var(--subtext)">Text senden:</span>
        <select id="vnc-layout-${win.key}" title="Tastaturlayout des Ziel-PCs"
          style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px">
          <option value="raw">1:1 (empfohlen)</option>
          <option value="us">US-Layout</option>
          <option value="de">DE-Layout</option>
        </select>
        <input type="text" id="vnc-text-${win.key}" placeholder="Text eingeben, dann Senden oder Enter..."
          style="flex:1;padding:5px 8px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text)" />
        <button class="taskbar-btn" id="vnc-send-${win.key}">Senden</button>
      </div>
    </div>
  `;

  const layoutSel = body.querySelector(`#vnc-layout-${win.key}`);
  const textInput = body.querySelector(`#vnc-text-${win.key}`);
  const sendBtn = body.querySelector(`#vnc-send-${win.key}`);

  const img = body.querySelector(`#vnc-img-${win.key}`);
  const statusEl = body.querySelector(`#vnc-status-${win.key}`);
  const controlToggle = body.querySelector(`#vnc-control-${win.key}`);
  const ctrlAltDelBtn = body.querySelector(`#vnc-ctrlaltdel-${win.key}`);

  // Echte Bildschirmauflösung des Clients (kommt mit jedem Frame mit),
  // um Klick-Koordinaten korrekt umzurechnen.
  let remoteWidth = 1920;
  let remoteHeight = 1080;
  let framesReceived = 0;
  let rdpActive = false;  // true, wenn gerade das experimentelle RDP-Streaming läuft
  let shellActive = false; // true, sobald auf Shell-Modus umgeschaltet wurde

  // Schaltet das Fenster auf eine eingebettete Shell um (headless/Shell-only).
  // Wird automatisch ausgelöst, wenn der Agent 'screen-mode: shell' meldet.
  function switchToShell(reason) {
    if (shellActive) return;
    shellActive = true;
    // Screen-Streaming beenden und alle Screen-Listener abmelden
    try { dashboardSocket.emit("screen-stop", { clientId }); } catch {}
    dashboardSocket.off("screen-frame", onFrame);
    dashboardSocket.off("screen-error", onError);
    dashboardSocket.off("screen-mode", onMode);
    window.removeEventListener("mouseup", onMouseUp);

    // Fenster leeren, Hinweis-Banner + Terminal einsetzen
    body.innerHTML = "";
    body.style.display = "flex";
    body.style.flexDirection = "column";
    body.style.height = "100%";
    const banner = document.createElement("div");
    banner.style.cssText = "padding:6px 10px;background:var(--panel-2);font-size:12px;color:var(--subtext);border-bottom:1px solid var(--border)";
    banner.textContent = "🖥️ → ⌨️ " + (reason || "Kein grafischer Bildschirm – Shell geöffnet.");
    body.appendChild(banner);
    const termHost = document.createElement("div");
    termHost.style.cssText = "flex:1;min-height:0";
    body.appendChild(termHost);
    // Bestehendes Terminal-Programm im selben Fenster rendern (eigene Session,
    // persistentes Arbeitsverzeichnis usw. - läuft über denselben Agent-Kanal).
    renderTerminal(termHost, win);
  }

  // --- Eingehende Frames verarbeiten ---
  function onFrame(data) {
    if (data.id !== clientId) return; // nur Frames für diesen Client
    // Falls das RDP-/Fehler-Overlay noch offen ist und jetzt ein Bild kommt: weg damit
    const overlay = body.querySelector(`#rdp-offer-${win.key}`);
    if (overlay) overlay.remove();
    img.src = "data:image/jpeg;base64," + data.image;
    remoteWidth = data.width;
    remoteHeight = data.height;
    framesReceived++;
    const mode = rdpActive ? "RDP" : "Live";
    statusEl.textContent = `Verbunden (${mode}) · ${remoteWidth}×${remoteHeight} · ${framesReceived} Frames`;
    statusEl.style.color = "var(--accent)";
  }

  function onError(data) {
    if (data.id !== clientId) return;
    statusEl.textContent = "Fehler: " + data.error;
    statusEl.style.color = "var(--danger)";
    showRdpOffer(data.error);
  }

  // Agent meldet den möglichen Zugriffsmodus. mode='shell' -> direkt Shell öffnen.
  function onMode(data) {
    if (data.id !== clientId) return;
    if (data.mode === "shell") switchToShell(data.reason);
  }

  // Zeigt bei einem Bildschirm-Fehler (typisch: headless VM) ein Angebot,
  // stattdessen per RDP zu verbinden - nativ (zuverlässig) oder experimentell.
  function showRdpOffer(errorText) {
    // Nur einmal anzeigen
    if (body.querySelector(`#rdp-offer-${win.key}`)) return;
    const imgArea = img.parentElement;
    const overlay = document.createElement("div");
    overlay.id = `rdp-offer-${win.key}`;
    overlay.style.cssText = `
      position:absolute; inset:0; display:flex; flex-direction:column;
      align-items:center; justify-content:center; gap:14px; padding:24px;
      background:rgba(10,20,32,0.92); text-align:center; z-index:5;`;
    overlay.innerHTML = `
      <div style="font-size:34px">🖥️</div>
      <div style="color:var(--text);font-size:15px;font-weight:600">Bildschirm nicht erfassbar</div>
      <div style="color:var(--subtext);font-size:13px;max-width:420px;line-height:1.5">
        ${esc(errorText || "")}<br><br>
        Bei headless VMs kann man sich stattdessen per <b>RDP</b> verbinden —
        RDP erzeugt selbst eine Sitzung, die sich fernsteuern lässt.
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:4px">
        <button class="btn-primary" id="rdp-native-${win.key}" style="width:auto;margin:0">
          ⬇️ Per RDP verbinden (nativ)
        </button>
        <button class="taskbar-btn" id="rdp-shell-${win.key}">⌨️ Shell öffnen</button>
        <button class="taskbar-btn" id="rdp-embed-${win.key}">
          🧪 Im Browser versuchen (experimentell)
        </button>
        <button class="taskbar-btn" id="rdp-retry-${win.key}">↻ Erneut streamen</button>
      </div>
      <div id="rdp-embed-msg-${win.key}" style="font-size:12px;color:var(--subtext);max-width:420px"></div>
    `;
    imgArea.appendChild(overlay);

    // Shell im selben Fenster öffnen (funktioniert immer, auch headless)
    overlay.querySelector(`#rdp-shell-${win.key}`).addEventListener("click", () => {
      switchToShell("Shell geöffnet.");
    });

    // Nativer RDP-Download (zuverlässig): lädt die .rdp-Datei -> mstsc öffnet sich
    overlay.querySelector(`#rdp-native-${win.key}`).addEventListener("click", async () => {
      try {
        await api.downloadRdpFile(clientId, clientName);
        window.notify?.("RDP-Datei heruntergeladen — öffne sie, um dich zu verbinden.", "success");
      } catch (e) {
        window.notify?.(e.message, "error");
      }
    });

    // Experimentelles RDP-Streaming im Browser (pyrdp-Gateway)
    overlay.querySelector(`#rdp-embed-${win.key}`).addEventListener("click", () => {
      const msg = overlay.querySelector(`#rdp-embed-msg-${win.key}`);
      msg.textContent = "Versuche RDP-Verbindung über das Backend...";
      // Frames kommen über denselben screen-frame-Kanal -> Overlay ausblenden,
      // sobald ein Frame ankommt (onFrame entfernt es).
      rdpActive = true;
      dashboardSocket.emit("rdp-start", { clientId });
    });

    // Erneut unser eigenes Streaming versuchen
    overlay.querySelector(`#rdp-retry-${win.key}`).addEventListener("click", () => {
      overlay.remove();
      statusEl.textContent = "Verbinde...";
      statusEl.style.color = "var(--subtext)";
      dashboardSocket.emit("screen-start", { clientId, username: state.user?.username || "unbekannt" });
    });
  }

  dashboardSocket.on("screen-frame", onFrame);
  dashboardSocket.on("screen-error", onError);
  dashboardSocket.on("screen-mode", onMode);

  // Agent anweisen, mit dem Streaming zu beginnen (Username für die Aufnahme)
  dashboardSocket.emit("screen-start", { clientId, username: state.user?.username || "unbekannt" });

  // --- Koordinaten vom angezeigten Bild auf die echte Auflösung umrechnen ---
  function mapCoords(clientX, clientY) {
    const rect = img.getBoundingClientRect();
    // Das Bild wird mit object-fit:contain angezeigt -> tatsächlich sichtbarer
    // Bildbereich kann schmaler/niedriger als das <img>-Element sein.
    const imgRatio = remoteWidth / remoteHeight;
    const boxRatio = rect.width / rect.height;

    let renderW, renderH, offsetX, offsetY;
    if (boxRatio > imgRatio) {
      // seitliche Balken
      renderH = rect.height;
      renderW = rect.height * imgRatio;
      offsetX = (rect.width - renderW) / 2;
      offsetY = 0;
    } else {
      // oben/unten Balken
      renderW = rect.width;
      renderH = rect.width / imgRatio;
      offsetX = 0;
      offsetY = (rect.height - renderH) / 2;
    }

    const relX = clientX - rect.left - offsetX;
    const relY = clientY - rect.top - offsetY;
    // in echte Bildschirmkoordinaten umrechnen
    const x = Math.round((relX / renderW) * remoteWidth);
    const y = Math.round((relY / renderH) * remoteHeight);
    return { x, y };
  }

  function controlEnabled() {
    return controlToggle.checked;
  }

  // --- Maus mit echtem Drag&Drop ---
  // Wir merken uns, ob gerade eine Taste gedrückt ist, damit "move" auch
  // während des Ziehens korrekt gesendet wird (Betriebssystem am Client zieht dann).
  let mouseDown = false;

  img.addEventListener("mousemove", (e) => {
    if (!controlEnabled()) return;
    const { x, y } = mapCoords(e.clientX, e.clientY);
    // Beim Ziehen ebenfalls "move" senden - der Client hält ja die Taste gedrückt
    dashboardSocket.emit("screen-input", { clientId, type: "move", x, y });
  });

  img.addEventListener("mousedown", (e) => {
    if (!controlEnabled()) return;
    e.preventDefault();
    img.focus(); // damit Tastatureingaben ankommen
    mouseDown = true;
    const { x, y } = mapCoords(e.clientX, e.clientY);
    const button = e.button === 2 ? "right" : "left";
    // Taste DRÜCKEN (nicht sofort klicken) -> ermöglicht Halten + Ziehen
    dashboardSocket.emit("screen-input", { clientId, type: "down", x, y, button });
  });

  // mouseup auf dem gesamten Fenster abfangen (falls man außerhalb des Bildes loslässt)
  function onMouseUp(e) {
    if (!mouseDown || !controlEnabled()) return;
    mouseDown = false;
    const { x, y } = mapCoords(e.clientX, e.clientY);
    const button = e.button === 2 ? "right" : "left";
    dashboardSocket.emit("screen-input", { clientId, type: "up", x, y, button });
  }
  img.addEventListener("mouseup", onMouseUp);
  window.addEventListener("mouseup", onMouseUp);

  img.addEventListener("dblclick", (e) => {
    if (!controlEnabled()) return;
    e.preventDefault();
    const { x, y } = mapCoords(e.clientX, e.clientY);
    dashboardSocket.emit("screen-input", { clientId, type: "double", x, y });
  });

  // Rechtsklick-Menü des Browsers im Bild unterdrücken (wir leiten Rechtsklick weiter)
  img.addEventListener("contextmenu", (e) => e.preventDefault());

  img.addEventListener("wheel", (e) => {
    if (!controlEnabled()) return;
    e.preventDefault();
    dashboardSocket.emit("screen-input", { clientId, type: "scroll", dy: e.deltaY > 0 ? -1 : 1 });
  });

  // --- Tastatur ---
  // Wir hören auf keydown, während das Bild fokussiert ist.
  img.addEventListener("keydown", (e) => {
    if (!controlEnabled()) return;
    e.preventDefault();

    // Tastenkombination mit Modifiern (Strg/Alt) -> als "combo" senden
    if (e.ctrlKey || e.altKey || e.metaKey) {
      const keys = [];
      if (e.ctrlKey) keys.push("Control");
      if (e.altKey) keys.push("Alt");
      if (e.metaKey) keys.push("Meta");
      if (e.shiftKey) keys.push("Shift");
      // Die eigentliche Taste (nicht die Modifier selbst)
      if (!["Control", "Alt", "Meta", "Shift"].includes(e.key)) {
        keys.push(e.key.length === 1 ? e.key.toLowerCase() : e.key);
      }
      if (keys.length) dashboardSocket.emit("screen-input", { clientId, type: "combo", keys });
      return;
    }

    // Normales druckbares Zeichen -> als Text tippen
    if (e.key.length === 1) {
      dashboardSocket.emit("screen-input", { clientId, type: "text", text: e.key });
    } else {
      // Sondertaste (Enter, Backspace, Pfeile, ...)
      dashboardSocket.emit("screen-input", { clientId, type: "key", key: e.key });
    }
  });

  // Strg+Alt+Entf Button (lässt sich aus dem Browser nicht als echte Taste abfangen)
  ctrlAltDelBtn.addEventListener("click", () => {
    dashboardSocket.emit("screen-input", { clientId, type: "combo", keys: ["Control", "Alt", "Delete"] });
  });

  // --- Text senden (kompletten Text auf einmal an den Cursor des Clients tippen) ---
  function sendText() {
    const text = textInput.value;
    if (!text) return;
    const layout = layoutSel.value;
    // Bei "raw"/1:1 wird der Text exakt so getippt wie eingegeben (empfohlen).
    // Die Layout-Auswahl ist für Fälle, in denen der Ziel-PC ein bestimmtes
    // Layout erwartet; der Agent tippt die Zeichen per pynput, das i.d.R.
    // die Zeichen unabhängig vom Layout korrekt einsetzt.
    dashboardSocket.emit("screen-input", { clientId, type: "text", text, layout });
    textInput.value = "";
    textInput.focus();
  }
  sendBtn.addEventListener("click", sendText);
  textInput.addEventListener("keydown", (e) => {
    // Enter sendet; Strg+V (Einfügen) im Feld normal zulassen
    if (e.key === "Enter") { e.preventDefault(); sendText(); }
  });

  // --- Aufräumen, wenn das Fenster geschlossen wird ---
  registerCleanup(win.key, () => {
    dashboardSocket.off("screen-frame", onFrame);
    dashboardSocket.off("screen-error", onError);
    dashboardSocket.off("screen-mode", onMode);
    dashboardSocket.emit("screen-stop", { clientId });
    if (rdpActive) dashboardSocket.emit("rdp-stop", { clientId });
    window.removeEventListener("mouseup", onMouseUp);
  });
}

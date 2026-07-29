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
import { attachPinchZoom } from "../touchgestures.js";
import { registerCleanup } from "../windowmanager.js";
import { state } from "../state.js";
import { api } from "../api.js";
import { esc, mapKeyboardText, uiPrompt } from "../utils.js";
import { renderTerminal } from "./terminal.js";
import { t } from "../i18n.js";

export function renderVnc(body, win) {
  const { clientId, clientName } = win.props;

  // WICHTIG zum Layout: Der Fenster-Body (.rmm-window-body) ist ein Flex-Item
  // mit "flex:1" und "overflow:auto", hat also KEINE feste Hoehe. Ein
  // "height:100%" darauf laeuft ins Leere (die Hoehe haengt am Inhalt, der
  // Inhalt an der Hoehe). Genau daran lag das nicht zentrierte Bild: Der
  // Bildbereich wuchs auf die volle Bildhoehe, "max-height:100%" griff nie,
  // das Bild wurde auf die volle Fensterbreite gestreckt und unten
  // abgeschnitten - man sah nur noch den mittleren Ausschnitt (die untere
  // Leiste "Text senden" war deshalb ebenfalls aus dem Fenster geschoben).
  // Loesung: absolut im Body verankern (der ist position:relative) - damit
  // hat der Rahmen IMMER die echte Fenstergroesse als Bezug.
  body.style.overflow = "hidden";
  body.innerHTML = `
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;background:#000">
      <div style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:var(--panel-2);font-size:12px;flex-wrap:wrap">
        <button class="taskbar-btn bar-opts-toggle" title="Optionen ein-/ausklappen">⚙️</button>
        <span id="vnc-status-${win.key}" style="color:var(--subtext)">Verbinde...</span>
        <button class="taskbar-btn" id="vnc-fs-${win.key}" title="Browser-Vollbild an/aus (Esc beendet ebenfalls)">Vollbild</button>
        <span style="flex:1"></span>
        <span class="bar-opts">
        <label style="color:var(--subtext);display:flex;align-items:center;gap:4px">
          <input type="checkbox" id="vnc-control-${win.key}" checked /> Steuerung aktiv
        </label>
        <select id="vnc-monitor-${win.key}" style="display:none;padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px"
          title="${t("u_bildschirm_des_remote_pcs_auswahle")}"></select>
        <span style="width:1px;height:16px;background:var(--border)"></span>
        <button class="taskbar-btn" id="vnc-mod-ctrl-${win.key}" title="${t("u_strg_gedruckt_halten_toggle_wirkt_")}">Strg</button>
        <button class="taskbar-btn" id="vnc-mod-alt-${win.key}" title="${t("u_alt_gedruckt_halten_toggle_wirkt_a")}">Alt</button>
        <button class="taskbar-btn" id="vnc-mod-win-${win.key}" title="${t("u_windows_taste_gedruckt_halten_togg")}">Win</button>
        <button class="taskbar-btn" id="vnc-key-tab-${win.key}" title="Tab senden">Tab</button>
        <button class="taskbar-btn" id="vnc-key-esc-${win.key}" title="Esc senden">Esc</button>
        <button class="taskbar-btn" id="vnc-ctrlaltdel-${win.key}">Strg+Alt+Entf</button>
        <span style="width:1px;height:16px;background:var(--border)"></span>
        <button class="taskbar-btn" id="vnc-clip-send-${win.key}" title="Lokale Zwischenablage an den Remote-PC senden">📋→</button>
        <button class="taskbar-btn" id="vnc-clip-get-${win.key}" title="Zwischenablage des Remote-PCs holen">→📋</button>
        </span>
      </div>
      <div style="flex:1 1 0;min-height:0;min-width:0;overflow:hidden;display:flex;align-items:center;justify-content:center;position:relative">
        <img id="vnc-img-${win.key}" style="display:block;max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;cursor:crosshair"
             tabindex="0" />
      </div>
      <div class="bar-optrow" style="display:flex;align-items:center;gap:6px;padding:6px 10px;background:var(--panel-2);font-size:12px;flex-wrap:wrap">
        <span style="color:var(--subtext)">Text senden:</span>
        <select id="vnc-layout-${win.key}" title="Tastaturlayout des Ziel-PCs"
          style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px">
          <option value="raw">1:1 (empfohlen)</option>
          <option value="us">US-Layout</option>
          <option value="de">DE-Layout</option>
        </select>
        <input type="text" id="vnc-text-${win.key}" placeholder="${t("guac_text_ph")}"
          style="flex:1;padding:5px 8px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text)" />
        <button class="taskbar-btn" id="vnc-send-${win.key}">Senden</button>
      </div>
    </div>
  `;

  const layoutSel = body.querySelector(`#vnc-layout-${win.key}`);
  const textInput = body.querySelector(`#vnc-text-${win.key}`);
  const sendBtn = body.querySelector(`#vnc-send-${win.key}`);

  const img = body.querySelector(`#vnc-img-${win.key}`);

  // ---- Browser-Vollbild (Desktop + Handy): Button toggelt, Esc beendet ----
  const fsBtnV = body.querySelector(`#vnc-fs-${win.key}`);
  if (fsBtnV) {
    const fsRoot = () => body.closest(".rmm-window") || body;
    const fsLabel = () => { fsBtnV.textContent = document.fullscreenElement ? "✕ Vollbild beenden" : "Vollbild"; };
    fsBtnV.addEventListener("click", () => {
      if (document.fullscreenElement) { document.exitFullscreen?.(); return; }
      const el = fsRoot();
      (el.requestFullscreen?.() || el.webkitRequestFullscreen?.());
    });
    const onFsChange = () => {
      if (!document.body.contains(fsBtnV)) { document.removeEventListener("fullscreenchange", onFsChange); return; }
      fsLabel();
    };
    document.addEventListener("fullscreenchange", onFsChange);
    fsLabel();
  }
  const statusEl = body.querySelector(`#vnc-status-${win.key}`);
  const controlToggle = body.querySelector(`#vnc-control-${win.key}`);
  const ctrlAltDelBtn = body.querySelector(`#vnc-ctrlaltdel-${win.key}`);
  const monitorSel = body.querySelector(`#vnc-monitor-${win.key}`);

  // Bildschirm wechseln (Multi-Monitor): direkte Auswahl per Dropdown.
  monitorSel.addEventListener("change", () => {
    const next = parseInt(monitorSel.value, 10);   // 0 = Alle Bildschirme
    if (!isNaN(next) && next !== monitorIndex) {
      dashboardSocket.emit("screen-set-monitor", { clientId, monitor: next });
    }
  });

  // --- Sticky-Modifier (wie in Proxmox): Strg/Alt/Win als Toggle. Aktive
  //     Modifier werden auf alle folgenden Tastatureingaben angewendet. ---
  const modState = { Control: false, Alt: false, Meta: false };
  const modBtns = {
    Control: body.querySelector(`#vnc-mod-ctrl-${win.key}`),
    Alt: body.querySelector(`#vnc-mod-alt-${win.key}`),
    Meta: body.querySelector(`#vnc-mod-win-${win.key}`),
  };
  function updateModBtn(name) {
    const b = modBtns[name];
    if (!b) return;
    b.style.background = modState[name] ? "var(--accent)" : "";
    b.style.color = modState[name] ? "#0b0f14" : "";
  }
  Object.keys(modBtns).forEach((name) => {
    modBtns[name].addEventListener("click", () => {
      modState[name] = !modState[name];
      updateModBtn(name);
      img.focus();
    });
    // Doppelklick: Taste SOFORT einmal drücken+loslassen (z.B. Win öffnet das
    // Startmenü). Die zwei Einzelklicks davor haben den Toggle an+aus
    // geschaltet - hier sicherheitshalber auf "aus" setzen.
    modBtns[name].addEventListener("dblclick", (e) => {
      e.preventDefault();
      modState[name] = false;
      updateModBtn(name);
      dashboardSocket.emit("screen-input", { clientId, type: "key", key: name });
      img.focus();
    });
  });
  function activeMods() {
    return Object.keys(modState).filter((k) => modState[k]);
  }
  // Schickt eine Taste unter Berücksichtigung der aktiven Toggles. Nach einer
  // Kombination werden die Toggles automatisch gelöst (wie in Proxmox).
  function sendKeyWithMods(key) {
    const mods = activeMods();
    if (mods.length) {
      dashboardSocket.emit("screen-input", { clientId, type: "combo", keys: [...mods, key] });
      releaseMods();
    } else if (key.length === 1) {
      dashboardSocket.emit("screen-input", { clientId, type: "text", text: key });
    } else {
      dashboardSocket.emit("screen-input", { clientId, type: "key", key });
    }
  }
  function releaseMods() {
    Object.keys(modState).forEach((k) => { modState[k] = false; updateModBtn(k); });
  }
  body.querySelector(`#vnc-key-tab-${win.key}`).addEventListener("click", () => { sendKeyWithMods("Tab"); img.focus(); });
  body.querySelector(`#vnc-key-esc-${win.key}`).addEventListener("click", () => { sendKeyWithMods("Escape"); img.focus(); });

  // --- Zwischenablage synchronisieren ---
  // Senden: lokale Zwischenablage lesen -> Agent setzt sie am Remote-PC.
  body.querySelector(`#vnc-clip-send-${win.key}`).addEventListener("click", async () => {
    let text = "";
    try { text = await navigator.clipboard.readText(); } catch {}
    if (!text) {
      window.notify?.(t("guac_clip_empty"), "warn");
      return;
    }
    dashboardSocket.emit("screen-input", { clientId, type: "clipboard-set", text });
    window.notify?.("Zwischenablage an Remote-PC gesendet", "success");
  });
  // Holen: Agent liest die Remote-Zwischenablage -> hier in die lokale schreiben.
  body.querySelector(`#vnc-clip-get-${win.key}`).addEventListener("click", () => {
    dashboardSocket.emit("screen-clipboard-get", { clientId });
  });
  async function onClipboard(data) {
    if (data.id !== clientId) return;
    if (data.error) { window.notify?.("Remote-Zwischenablage: " + data.error, "error"); return; }
    const text = data.text || "";
    try {
      await navigator.clipboard.writeText(text);
      window.notify?.(t("u_remote_zwischenablage_ubernommen") + text.length + " Zeichen)", "success");
    } catch {
      // Clipboard-API blockiert (z.B. HTTP): Text zum manuellen Kopieren zeigen.
      uiPrompt("Remote-Zwischenablage", {
        description: t("guac_copy_manual"),
        value: text, okText: t("close") });
    }
  }
  dashboardSocket.on("screen-clipboard", onClipboard);

  // Echte Bildschirmauflösung des Clients (kommt mit jedem Frame mit),
  // um Klick-Koordinaten korrekt umzurechnen.
  let remoteWidth = 1920;
  let remoteHeight = 1080;
  let framesReceived = 0;
  let monitorCount = 1;
  let monitorIndex = 1;
  let rdpActive = false;  // true, wenn gerade das experimentelle RDP-Streaming läuft
  let shellActive = false; // true, sobald auf Shell-Modus umgeschaltet wurde

  // Öffnet den Guacamole-Client (RDP/VNC/SSH über extern gehostetes guacd)
  // für diesen Client in einem eigenen Fenster.
  function openGuacWindow() {
    import("../windowmanager.js").then(({ openWindow }) => {
      const c = state.clients?.find((x) => x.id === clientId);
      openWindow({
        key: `guac-${clientId}`, appId: "guacamole",
        title: `Guacamole — ${clientName}`,
        props: { clientId, clientName, host: c?.ip || "", platform: c?.platform },
        w: 900, h: 640,
      });
    });
  }

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
    dashboardSocket.off("screen-clipboard", onClipboard);
    window.removeEventListener("mouseup", onMouseUp);

    // Fenster leeren, Hinweis-Banner (inkl. Guacamole-Option) + Terminal einsetzen
    // Gleiche Falle wie oben: height:100% auf dem Fenster-Body wirkt nicht.
    // Absolut verankern, damit das Terminal die echte Fensterhoehe bekommt.
    body.innerHTML = "";
    body.style.display = "block";
    body.style.overflow = "hidden";
    const shellRoot = document.createElement("div");
    shellRoot.style.cssText = "position:absolute;inset:0;display:flex;flex-direction:column";
    body.appendChild(shellRoot);
    const banner = document.createElement("div");
    banner.style.cssText = "display:flex;align-items:center;gap:10px;padding:6px 10px;background:var(--panel-2);font-size:12px;color:var(--subtext);border-bottom:1px solid var(--border);flex-wrap:wrap";
    const label = document.createElement("span");
    label.style.flex = "1";
    label.textContent = "🖥️ → ⌨️ " + (reason || t("u_kein_grafischer_bildschirm_shell_g"));
    const guacBtn = document.createElement("button");
    guacBtn.className = "taskbar-btn";
    guacBtn.textContent = t("u_uber_guacamole_rdp_vnc_ssh");
    guacBtn.addEventListener("click", openGuacWindow);
    banner.appendChild(label);
    banner.appendChild(guacBtn);
    shellRoot.appendChild(banner);
    const termHost = document.createElement("div");
    termHost.style.cssText = "flex:1;min-height:0";
    shellRoot.appendChild(termHost);
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
    // Multi-Monitor: Button anzeigen/aktualisieren, wenn mehr als ein Bildschirm da ist.
    if (typeof data.monitor_count === "number") {
      monitorCount = data.monitor_count;
      monitorIndex = (typeof data.monitor_index === "number") ? data.monitor_index : 1;
      if (monitorSel) {
        monitorSel.style.display = monitorCount > 1 ? "" : "none";
        // Optionen nur bei Änderung neu aufbauen (nicht bei jedem Frame).
        // Index 0 = Gesamtfläche ALLER Bildschirme nebeneinander.
        if (monitorSel.options.length !== monitorCount + 1) {
          monitorSel.innerHTML = `<option value="0">🖥️ Alle Bildschirme</option>` +
            Array.from({ length: monitorCount }, (_, i) =>
              `<option value="${i + 1}">🖥️ Bildschirm ${i + 1}/${monitorCount}</option>`).join("");
        }
        if (parseInt(monitorSel.value, 10) !== monitorIndex) monitorSel.value = String(monitorIndex);
      }
    }
    framesReceived++;
    const mode = rdpActive ? "RDP" : "Live";
    statusEl.textContent = `Verbunden (${mode}) · ${remoteWidth}×${remoteHeight} · ${framesReceived} Frames`;
    statusEl.style.color = "var(--accent)";
  }

  function onError(data) {
    if (data.id !== clientId) return;
    // Rohen Fehlertext nur in die Konsole - die Oberfläche bleibt freundlich.
    console.warn("[screen] Fehler vom Agent:", data.error);
    if (data.consent_denied) {
      // Der Benutzer am Gerät hat nicht zugestimmt - das ist KEIN technischer
      // Fehler, deshalb ohne Alternativen-Angebot anzeigen.
      statusEl.textContent = t("u_am_gerat_abgelehnt_keine_bestatigu");
      statusEl.style.color = "var(--danger)";
      return;
    }
    statusEl.textContent = t("u_kein_bildschirm_verfugbar");
    statusEl.style.color = "var(--danger)";
    showRdpOffer(data.error);
  }

  // Agent meldet den möglichen Zugriffsmodus. mode='shell' -> direkt Shell öffnen.
  function onMode(data) {
    if (data.id !== clientId) return;
    if (data.mode === "shell") switchToShell(data.reason);
    if (data.mode === "consent") {
      // Am Gerät wird gerade um Zustimmung gebeten.
      statusEl.textContent = t("u_warte_auf_bestatigung_am_gerat");
      statusEl.style.color = "var(--subtext)";
    }
  }

  // Zeigt bei einem Bildschirm-Fehler (typisch: headless VM) eine freundliche
  // Auswahl an Alternativen. Bewusst OHNE die rohe Fehlermeldung (die steht
  // klein in der Statusleiste und im agent.log) und OHNE RDP-Datei-Download /
  // experimentelles Browser-RDP - beides wurde entfernt.
  function showRdpOffer(errorText) {
    // Nur einmal anzeigen
    if (body.querySelector(`#rdp-offer-${win.key}`)) return;
    const imgArea = img.parentElement;
    const overlay = document.createElement("div");
    overlay.id = `rdp-offer-${win.key}`;
    overlay.style.cssText = `
      position:absolute; inset:0; display:flex; flex-direction:column;
      align-items:center; justify-content:center; gap:10px; padding:24px;
      background:radial-gradient(ellipse at center, rgba(16,28,44,0.97), rgba(8,14,24,0.99));
      text-align:center; z-index:5;`;
    overlay.innerHTML = `
      <div style="width:72px;height:72px;border-radius:50%;display:flex;align-items:center;justify-content:center;
                  background:var(--panel-2);border:1px solid var(--border);font-size:32px;margin-bottom:4px">🖥️</div>
      <div style="color:var(--text);font-size:16px;font-weight:600">Kein Bildschirm verfügbar</div>
      <div style="color:var(--subtext);font-size:13px;max-width:400px;line-height:1.55">
        Auf diesem Gerät läuft gerade keine erfassbare Bildschirmsitzung
        (typisch bei Servern und headless VMs). Kein Problem — so kommst du
        trotzdem drauf:
      </div>
      <div style="display:flex;flex-direction:column;gap:8px;margin-top:10px;width:100%;max-width:340px">
        <button class="btn-primary" id="rdp-guac-${win.key}" style="width:100%;margin:0;display:flex;align-items:center;gap:10px;justify-content:center">
          🕹️ Remote-Sitzung öffnen (RDP/VNC/SSH)
        </button>
        <button class="taskbar-btn" id="rdp-shell-${win.key}" style="width:100%;display:flex;align-items:center;gap:10px;justify-content:center">
          ⌨️ Shell öffnen
        </button>
        <button class="taskbar-btn" id="rdp-retry-${win.key}" style="width:100%;display:flex;align-items:center;gap:10px;justify-content:center">
          🔄 Bildschirm erneut versuchen
        </button>
      </div>
    `;
    imgArea.appendChild(overlay);

    // Shell im selben Fenster öffnen (funktioniert immer, auch headless)
    overlay.querySelector(`#rdp-shell-${win.key}`).addEventListener("click", () => {
      switchToShell(t("u_shell_geoffnet"));
    });

    // Über Guacamole verbinden (RDP/VNC/SSH über guacd) - eigenes Fenster
    overlay.querySelector(`#rdp-guac-${win.key}`).addEventListener("click", openGuacWindow);

    // Erneut unser eigenes Streaming versuchen
    overlay.querySelector(`#rdp-retry-${win.key}`).addEventListener("click", () => {
      overlay.remove();
      statusEl.textContent = "Verbinde...";
      statusEl.style.color = "var(--subtext)";
      dashboardSocket.emit("screen-start", { clientId, username: state.user?.username || "unbekannt" });
      _announceSessionStart(clientId);
    });
  }

  // Lokales Signal "Remote-Session gestartet": Das Profil (falls offen) schaltet
  // den einmaligen Silent-Modus-Toggle damit SOFORT wieder aus - unabhängig
  // davon, ob das Backend-Event ankommt.
  function _announceSessionStart(cid) {
    try {
      window.dispatchEvent(new CustomEvent("screen-session-started", { detail: { clientId: cid } }));
    } catch {}
  }

  dashboardSocket.on("screen-frame", onFrame);
  dashboardSocket.on("screen-error", onError);
  dashboardSocket.on("screen-mode", onMode);

  // Agent anweisen, mit dem Streaming zu beginnen (Username für die Aufnahme)
  dashboardSocket.emit("screen-start", { clientId, username: state.user?.username || "unbekannt" });
  _announceSessionStart(clientId);

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

  // ---- Touch: Zoomen/Schieben und Halten = Rechtsklick -------------------
  // Auf dem Handy gibt es keine rechte Maustaste, und die Gegenstelle ist ohne
  // Zoom kaum bedienbar. mapCoords() rechnet weiterhin richtig, weil der Zoom
  // nur die Darstellung veraendert - die getBoundingClientRect() des Bildes
  // waechst mit.
  const zoom = attachPinchZoom(img.parentElement, img, { min: 1, max: 6 });
  registerCleanup(win.key, () => zoom.destroy());

  // ---- Touch-Bedienung -----------------------------------------------
  // Ein Finger gehört der Gegenstelle, damit sich dort Fenster ziehen und
  // Symbole verschieben lassen. Unterschieden wird nach Bewegung und Zeit:
  //
  //   kurz tippen           -> Linksklick
  //   bewegen               -> ziehen mit gedrückter linker Taste
  //   halten ohne Bewegung  -> Rechtsklick
  //
  // Die Entscheidung fällt erst NACH dem Aufsetzen: Würde beim Aufsetzen
  // sofort "gedrückt" gesendet, käme beim Halten zusätzlich ein Linksklick an
  // und aus einem Rechtsklick würde ein versehentliches Ziehen.
  const TOUCH_HOLD_MS = 500;     // ab hier gilt es als Halten
  const TOUCH_MOVE_PX = 10;      // ab hier gilt es als Ziehen
  let touchStart = null;
  let touchDragging = false;
  let touchHoldTimer = null;

  const touchSend = (type, cx, cy, button = "left") => {
    const { x, y } = mapCoords(cx, cy);
    dashboardSocket.emit("screen-input", { clientId, type, x, y, button });
  };

  img.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" || !controlEnabled()) return;
    img.focus();
    touchStart = { cx: e.clientX, cy: e.clientY };
    touchDragging = false;
    clearTimeout(touchHoldTimer);
    touchHoldTimer = setTimeout(() => {
      // Gehalten ohne Bewegung -> Rechtsklick.
      if (!touchStart || touchDragging) return;
      try { navigator.vibrate?.(18); } catch {}
      const { cx, cy } = touchStart;
      touchSend("down", cx, cy, "right");
      setTimeout(() => touchSend("up", cx, cy, "right"), 60);
      touchStart = null;
    }, TOUCH_HOLD_MS);
  }, { passive: true });

  img.addEventListener("pointermove", (e) => {
    if (e.pointerType === "mouse" || !touchStart || !controlEnabled()) return;
    const moved = Math.abs(e.clientX - touchStart.cx) + Math.abs(e.clientY - touchStart.cy);
    if (!touchDragging && moved > TOUCH_MOVE_PX) {
      // Erst jetzt die Taste drücken - und zwar am AUFSETZPUNKT. Nur so
      // greift die Gegenstelle den Fenstertitel dort, wo der Finger startete.
      clearTimeout(touchHoldTimer);
      touchDragging = true;
      touchSend("down", touchStart.cx, touchStart.cy, "left");
    }
    if (touchDragging) {
      touchSend("move", e.clientX, e.clientY);
      e.preventDefault();
    }
  }, { passive: false });

  img.addEventListener("pointerup", (e) => {
    if (e.pointerType === "mouse" || !touchStart) return;
    clearTimeout(touchHoldTimer);
    if (touchDragging) {
      touchSend("up", e.clientX, e.clientY, "left");
    } else if (controlEnabled()) {
      // Kurzes Tippen -> vollständiger Linksklick an derselben Stelle.
      const { cx, cy } = touchStart;
      touchSend("down", cx, cy, "left");
      setTimeout(() => touchSend("up", cx, cy, "left"), 40);
    }
    touchStart = null;
    touchDragging = false;
  });

  img.addEventListener("pointercancel", () => {
    clearTimeout(touchHoldTimer);
    if (touchDragging && touchStart) touchSend("up", touchStart.cx, touchStart.cy, "left");
    touchStart = null;
    touchDragging = false;
  }, { passive: true });

  // Das Auswahl-/Lupenmenü des Browsers stört beim Ziehen.
  img.addEventListener("contextmenu", (e) => {
    if (e.pointerType !== "mouse") e.preventDefault();
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

    // Sticky-Modifier (Toolbar-Toggles) auf die Taste anwenden.
    if (activeMods().length) {
      sendKeyWithMods(e.key.length === 1 ? e.key.toLowerCase() : e.key);
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
    // Layout-Kompensation (andersrum, auf Nutzerwunsch): bei Auswahl "us"
    // werden die Zeichen vor dem Senden ueber die Positions-Tabelle ersetzt
    // (Reverse-Effekt); "de"/"raw" = 1:1 senden.
    const mapped = mapKeyboardText(text, layout);
    dashboardSocket.emit("screen-input", { clientId, type: "text", text: mapped, layout });
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
    dashboardSocket.off("screen-clipboard", onClipboard);
    dashboardSocket.emit("screen-stop", { clientId });
    if (rdpActive) dashboardSocket.emit("rdp-stop", { clientId });
    window.removeEventListener("mouseup", onMouseUp);
  });
}

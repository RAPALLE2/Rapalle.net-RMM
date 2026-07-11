// apps/terminal.js
// ----------------
// ECHTES interaktives Terminal. Der Agent hält eine dauerhafte Shell-Session
// (PTY) offen; hier wird sie mit einem eingebauten Terminal-Emulator
// (miniterm.js, KEIN CDN/xterm nötig -> funktioniert auch im abgeschotteten
// LAN ohne Internet) verbunden.
//
// Zusammenhängende Session: 'cd' wirkt dauerhaft, nano/vim & interaktive
// Programme laufen, Verlauf per Pfeiltasten, Farben. Windows: cmd/powershell
// wählbar (Agent spawnt die jeweilige .exe direkt).

import { esc } from "../utils.js";
import { state } from "../state.js";
import { dashboardSocket } from "../socket.js";
import { registerCleanup } from "../windowmanager.js";
import { MiniTerm } from "./miniterm.js";

export function renderTerminal(body, win) {
  const { clientId, clientName } = win.props;
  const client = state.clients?.find((c) => c.id === clientId);
  const isWindows = (client?.platform || "").toLowerCase().includes("win");

  const sessionId = (window.crypto?.randomUUID?.() ||
    `term-${Date.now()}-${Math.random().toString(36).slice(2)}`);

  let shellMode = isWindows ? "cmd" : "auto";
  let term = null;

  const shellBar = isWindows ? `
      <div style="display:flex;gap:6px;padding:6px 8px;border-bottom:1px solid var(--border);align-items:center;flex-wrap:wrap">
        <span style="font-size:11px;color:var(--subtext)">Shell:</span>
        <select id="term-shell-${win.key}" style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
          <option value="cmd">CMD</option>
          <option value="powershell">PowerShell</option>
        </select>
        <span style="font-size:11px;color:var(--subtext)">gilt beim (Neu-)Start</span>
        <span style="flex:1"></span>
        <select id="term-script-${win.key}" title="Gespeichertes Skript in dieser Shell ausführen"
          style="max-width:160px;padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
          <option value="">📜 Skript…</option>
        </select>
        <button class="taskbar-btn" id="term-script-run-${win.key}" title="Gewähltes Skript ausführen">▶</button>
        <button class="taskbar-btn" id="term-clear-${win.key}" title="Bildschirm leeren (sendet cls/clear)">🧹 Clear</button>
        <button class="taskbar-btn" id="term-restart-${win.key}">↻ Neustart</button>
      </div>` : `
      <div style="display:flex;gap:6px;padding:6px 8px;border-bottom:1px solid var(--border);align-items:center">
        <span style="font-size:11px;color:var(--subtext)">Interaktive Shell auf ${esc(clientName || "Client")}</span>
        <span style="flex:1"></span>
        <select id="term-script-${win.key}" title="Gespeichertes Skript in dieser Shell ausführen"
          style="max-width:160px;padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
          <option value="">📜 Skript…</option>
        </select>
        <button class="taskbar-btn" id="term-script-run-${win.key}" title="Gewähltes Skript ausführen">▶</button>
        <button class="taskbar-btn" id="term-clear-${win.key}" title="Bildschirm leeren (sendet clear)">🧹 Clear</button>
        <button class="taskbar-btn" id="term-restart-${win.key}">↻ Neustart</button>
      </div>`;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#0b0f14">
      ${shellBar}
      <div id="term-host-${win.key}" style="flex:1 1 0;min-height:0;overflow:hidden;position:relative"></div>
      <div id="term-status-${win.key}" style="flex:none;font-size:11px;color:var(--subtext);padding:2px 8px;border-top:1px solid var(--border)">Verbinde…</div>
      <div style="flex:none;display:flex;align-items:center;gap:6px;padding:6px 8px;background:var(--panel-2);font-size:12px;border-top:1px solid var(--border)">
        <span style="color:var(--subtext)">Text senden:</span>
        <select id="term-layout-${win.key}" title="Tastaturlayout: Text wird 1:1 als Zeichen an die Shell (PTY) gesendet - die Layout-Auswahl entspricht dem Remote-Screen und ist nur für Sonderfälle relevant"
          style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px">
          <option value="raw">1:1 (empfohlen)</option>
          <option value="us">US-Layout</option>
          <option value="de">DE-Layout</option>
        </select>
        <input type="text" id="term-text-${win.key}" placeholder="Text eingeben, dann Senden oder Enter..."
          style="flex:1;padding:5px 8px;border-radius:5px;border:1px solid var(--border);background:var(--panel);color:var(--text)" />
        <label style="color:var(--subtext);display:flex;align-items:center;gap:4px" title="Nach dem Text zusätzlich Enter senden">
          <input type="checkbox" id="term-enter-${win.key}" /> +Enter
        </label>
        <button class="taskbar-btn" id="term-send-${win.key}">Senden</button>
      </div>
    </div>
  `;

  const host = body.querySelector(`#term-host-${win.key}`);
  const statusEl = body.querySelector(`#term-status-${win.key}`);
  const shellSel = body.querySelector(`#term-shell-${win.key}`);
  if (shellSel) shellSel.addEventListener("change", () => { shellMode = shellSel.value; });

  // --- Socket-Events (nach Session gefiltert) ---
  let gotOutput = false;
  let gotAck = false;
  let agentOnline = false;
  function onOutput(p) {
    if (p.session !== sessionId || !term) return;
    gotOutput = true;
    term.write(p.data || "");
  }
  function onExit(p) {
    if (p.session !== sessionId || !term) return;
    term.write("\r\n\x1b[33m[Session beendet]\x1b[0m\r\n");
    statusEl.textContent = "Session beendet — ↻ Neustart zum Wiederverbinden.";
  }
  function onAck(p) {
    if (p.session !== sessionId) return;
    gotAck = true;
    agentOnline = !!p.agent_online;
  }
  dashboardSocket.on("term-output", onOutput);
  dashboardSocket.on("term-exit", onExit);
  dashboardSocket.on("term-ack", onAck);

  function openSession() {
    gotOutput = false; gotAck = false; agentOnline = false;
    dashboardSocket.emit("term-open", {
      clientId, session: sessionId, shell: shellMode,
      cols: term.cols, rows: term.rows,
      username: state.user?.username || "unbekannt",
    });
    statusEl.textContent = `Verbunden (${isWindows ? shellMode : "shell"}). Klicke ins Terminal und tippe.`;
    // Präzise Diagnose nach 3s, falls keine Ausgabe kam.
    setTimeout(() => {
      if (gotOutput || !term) return;
      if (!gotAck) {
        term.write("\x1b[31m[Backend antwortet nicht auf das Terminal]\x1b[0m\r\n" +
          "Das laufende Backend kennt die Terminal-Funktion nicht.\r\n" +
          "-> Backend mit der aktuellen Version NEU STARTEN.\r\n");
        statusEl.textContent = "Backend veraltet / nicht neu gestartet.";
      } else if (!agentOnline) {
        term.write("\x1b[31m[Client ist offline]\x1b[0m\r\n" +
          "Der Agent dieses Clients ist nicht verbunden.\r\n");
        statusEl.textContent = "Client offline.";
      } else {
        term.write("\x1b[33m[Agent antwortet nicht auf die Shell]\x1b[0m\r\n" +
          "Backend + Client sind verbunden, aber der Agent startet keine Shell.\r\n" +
          "-> Auf dem Client läuft vermutlich noch die alte Agent-Version.\r\n" +
          "   Agent stoppen und neu ausrollen. (Windows: pywinpty wird beim\r\n" +
          "   Agentenstart automatisch nachinstalliert.)\r\n");
        statusEl.textContent = "Agent ohne Terminal-Unterstützung — neu ausrollen.";
      }
    }, 3000);
  }

  // Terminal-Emulator aufbauen (sofort, keine externen Abhängigkeiten).
  term = new MiniTerm(host, {
    onData: (data) => dashboardSocket.emit("term-input", { clientId, session: sessionId, data }),
    onResize: (cols, rows) => dashboardSocket.emit("term-resize", { clientId, session: sessionId, cols, rows }),
  });

  // Bei Größenänderung des Fensters neu einpassen.
  const ro = new ResizeObserver(() => { try { term.fit(); } catch {} });
  ro.observe(host);
  win._termRo = ro;

  openSession();
  setTimeout(() => term.focus(), 50);

  // --- Clear-Button: sendet den passenden Befehl an die Shell ---
  // Windows-Shells (cmd/powershell) kennen 'cls', Unix-Shells 'clear'.
  body.querySelector(`#term-clear-${win.key}`)?.addEventListener("click", () => {
    const cmd = isWindows ? "cls" : "clear";
    dashboardSocket.emit("term-input", { clientId, session: sessionId, data: cmd + "\r" });
    term.focus();
  });

  // --- Skript ausführen: gespeicherte Skripte laden (passend zum OS des
  //     Clients gefiltert) und den Befehl direkt in die laufende Shell tippen.
  //     Mehrzeilige Skripte werden Zeile für Zeile mit Enter gesendet. ---
  const scriptSel = body.querySelector(`#term-script-${win.key}`);
  let termScripts = [];
  import("../api.js").then(({ api }) => api.getScripts()).then((scripts) => {
    termScripts = (scripts || []).filter((sc) =>
      sc.os === "any" || (isWindows ? sc.os === "windows" : sc.os === "linux"));
    if (scriptSel) {
      scriptSel.innerHTML = `<option value="">📜 Skript…</option>` +
        termScripts.map((sc) => `<option value="${esc(sc.id)}">${esc(sc.name)}</option>`).join("");
    }
  }).catch(() => {});
  function runSelectedScript() {
    const sc = termScripts.find((x) => x.id === scriptSel?.value);
    if (!sc) return;
    const lines = String(sc.command || "").replace(/\r\n/g, "\n").split("\n").filter((l) => l.trim() !== "");
    for (const line of lines) {
      dashboardSocket.emit("term-input", { clientId, session: sessionId, data: line + "\r" });
    }
    term.focus();
  }
  body.querySelector(`#term-script-run-${win.key}`)?.addEventListener("click", runSelectedScript);
  scriptSel?.addEventListener("change", runSelectedScript);

  // --- Text-Sendebox (wie beim Remote Screen): kompletten Text auf einmal
  //     an die Shell schicken. Der Text geht 1:1 als Zeichen in das PTY -
  //     ein Tastaturlayout spielt dabei technisch keine Rolle, die Auswahl
  //     ist zur Konsistenz mit dem Remote-Screen vorhanden. ---
  const termTextInput = body.querySelector(`#term-text-${win.key}`);
  const termSendBtn = body.querySelector(`#term-send-${win.key}`);
  const termEnterChk = body.querySelector(`#term-enter-${win.key}`);
  function sendTermText() {
    const text = termTextInput.value;
    if (!text) return;
    dashboardSocket.emit("term-input", {
      clientId, session: sessionId,
      data: text + (termEnterChk?.checked ? "\r" : ""),
    });
    termTextInput.value = "";
    termTextInput.focus();
  }
  termSendBtn?.addEventListener("click", sendTermText);
  termTextInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); sendTermText(); }
  });

  // Neustart (auch für Shell-Wechsel auf Windows)
  body.querySelector(`#term-restart-${win.key}`)?.addEventListener("click", () => {
    dashboardSocket.emit("term-close", { clientId, session: sessionId });
    statusEl.textContent = "Starte neu…";
    setTimeout(openSession, 150);
    term.focus();
  });

  // Aufräumen beim Schließen
  registerCleanup(win.key, () => {
    dashboardSocket.off("term-output", onOutput);
    dashboardSocket.off("term-exit", onExit);
    dashboardSocket.off("term-ack", onAck);
    dashboardSocket.emit("term-close", { clientId, session: sessionId });
    try { win._termRo?.disconnect(); } catch {}
    try { term?.dispose(); } catch {}
  });
}

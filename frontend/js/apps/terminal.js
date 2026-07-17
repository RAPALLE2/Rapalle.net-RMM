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

import { esc, mapKeyboardText } from "../utils.js";
import { exportLogText } from "./source.js";
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
        <span style="position:relative;display:inline-flex">
          <button class="taskbar-btn" id="term-script-${win.key}" title="Gespeichertes Skript wählen (mit Suche und Ordnern)" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📜 Skript…</button>
          <div id="term-script-menu-${win.key}" class="hidden" style="position:absolute;top:calc(100% + 4px);right:0;z-index:60;width:280px;max-height:320px;display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.4)">
            <input type="text" id="term-script-search-${win.key}" placeholder="🔍 Skript suchen…" style="margin:8px;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px" />
            <div id="term-script-list-${win.key}" style="overflow-y:auto;padding:0 6px 8px"></div>
          </div>
        </span>
        <button class="taskbar-btn" id="term-script-run-${win.key}" title="Gewähltes Skript ausführen">▶</button>
        <button class="taskbar-btn" id="term-agentcon-${win.key}" title="Zwischen Shell und Agent-Konsole (Log des Agenten) umschalten">🤖 Agent-Konsole</button>
        <select id="term-fmt-${win.key}" title="Export-Format" style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:11px">
          <option value="json">JSON</option><option value="xml">XML</option><option value="txt">TXT</option>
        </select>
        <button class="taskbar-btn" id="term-export-${win.key}" title="Kompletten Terminal-Log herunterladen">⬇ Export</button>
        <button class="taskbar-btn" id="term-clear-${win.key}" title="Bildschirm leeren (sendet cls/clear)">🧹 Clear</button>
        <button class="taskbar-btn" id="term-restart-${win.key}">↻ Neustart</button>
      </div>` : `
      <div style="display:flex;gap:6px;padding:6px 8px;border-bottom:1px solid var(--border);align-items:center">
        <span style="font-size:11px;color:var(--subtext)">Interaktive Shell auf ${esc(clientName || "Client")}</span>
        <span style="flex:1"></span>
        <span style="position:relative;display:inline-flex">
          <button class="taskbar-btn" id="term-script-${win.key}" title="Gespeichertes Skript wählen (mit Suche und Ordnern)" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">📜 Skript…</button>
          <div id="term-script-menu-${win.key}" class="hidden" style="position:absolute;top:calc(100% + 4px);right:0;z-index:60;width:280px;max-height:320px;display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.4)">
            <input type="text" id="term-script-search-${win.key}" placeholder="🔍 Skript suchen…" style="margin:8px;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px" />
            <div id="term-script-list-${win.key}" style="overflow-y:auto;padding:0 6px 8px"></div>
          </div>
        </span>
        <button class="taskbar-btn" id="term-script-run-${win.key}" title="Gewähltes Skript ausführen">▶</button>
        <button class="taskbar-btn" id="term-agentcon-${win.key}" title="Zwischen Shell und Agent-Konsole (Log des Agenten) umschalten">🤖 Agent-Konsole</button>
        <select id="term-fmt-${win.key}" title="Export-Format" style="padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:11px">
          <option value="json">JSON</option><option value="xml">XML</option><option value="txt">TXT</option>
        </select>
        <button class="taskbar-btn" id="term-export-${win.key}" title="Kompletten Terminal-Log herunterladen">⬇ Export</button>
        <button class="taskbar-btn" id="term-clear-${win.key}" title="Bildschirm leeren (sendet clear)">🧹 Clear</button>
        <button class="taskbar-btn" id="term-restart-${win.key}">↻ Neustart</button>
      </div>`;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#0b0f14">
      ${shellBar}
      <div id="term-host-${win.key}" style="flex:1 1 0;min-height:0;overflow:hidden;position:relative"></div>
      <div id="term-agenthost-${win.key}" style="flex:1 1 0;min-height:0;overflow:hidden;position:relative;display:none"></div>
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
  let termLogBuffer = "";   // kompletter Roh-Log dieser Session (fuer den Export)
  function onOutput(p) {
    if (p.session !== sessionId || !term) return;
    gotOutput = true;
    termLogBuffer += p.data || "";
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

  // --- Agent-Konsole: Log des Agenten (Historie + live) statt der Shell ---
  const agentHost = body.querySelector(`#term-agenthost-${win.key}`);
  const agentBtn = body.querySelector(`#term-agentcon-${win.key}`);
  let agentTerm = null;          // eigener (read-only) MiniTerm fuer den Agent-Log
  let agentView = false;         // gerade Agent-Konsole sichtbar?
  let agentConLogBuffer = "";    // Puffer fuer den Export

  function onAgentConsoleHistory(p) {
    if (!p || p.id !== clientId || !agentTerm) return;
    agentConLogBuffer = p.data || "";
    agentTerm.write("\x1b[2J\x1b[H");   // Ansicht leeren, dann Historie schreiben
    agentTerm.write(p.data || "");
  }
  function onAgentConsoleLine(p) {
    if (!p || p.id !== clientId || !agentTerm) return;
    agentConLogBuffer += p.data || "";
    agentTerm.write(p.data || "");
  }
  function onAgentConsoleAck(p) {
    if (!p || p.id !== clientId || !agentTerm) return;
    if (!p.agent_online) {
      agentTerm.write("\r\n\x1b[31m[Client ist offline - Agent-Konsole nicht verfuegbar]\x1b[0m\r\n");
    }
  }
  dashboardSocket.on("agent-console-history", onAgentConsoleHistory);
  dashboardSocket.on("agent-console", onAgentConsoleLine);
  dashboardSocket.on("agent-console-ack", onAgentConsoleAck);

  function setAgentView(on) {
    agentView = on;
    host.style.display = on ? "none" : "";
    agentHost.style.display = on ? "" : "none";
    if (agentBtn) {
      agentBtn.textContent = on ? "⌨ Shell" : "🤖 Agent-Konsole";
      agentBtn.title = on ? "Zurueck zur interaktiven Shell" : "Zwischen Shell und Agent-Konsole (Log des Agenten) umschalten";
    }
    if (on) {
      if (!agentTerm) {
        // Nur Anzeige - Eingaben werden ignoriert (der Agent-Log ist read-only).
        agentTerm = new MiniTerm(agentHost, { onData: () => {}, onResize: () => {} });
      }
      statusEl.textContent = "Agent-Konsole (read-only) - Log des Agenten auf diesem Client.";
      dashboardSocket.emit("agent-console-open", { clientId, username: state.user?.username || "unbekannt" });
      setTimeout(() => { try { agentTerm.fit(); } catch {} }, 30);
    } else {
      dashboardSocket.emit("agent-console-close", { clientId });
      statusEl.textContent = "Shell aktiv. Klicke ins Terminal und tippe.";
      setTimeout(() => { try { term.fit(); term.focus(); } catch {} }, 30);
    }
  }
  agentBtn?.addEventListener("click", () => setAgentView(!agentView));
  // Groessenaenderungen auch fuer die Agent-Konsole einpassen
  const roAgent = new ResizeObserver(() => { try { agentTerm?.fit(); } catch {} });
  roAgent.observe(agentHost);

  // --- Clear-Button: sendet den passenden Befehl an die Shell ---
  // Windows-Shells (cmd/powershell) kennen 'cls', Unix-Shells 'clear'.
  // --- Export des kompletten Terminal-Logs (JSON / XML / TXT) ---
  body.querySelector(`#term-export-${win.key}`)?.addEventListener("click", () => {
    const fmt = body.querySelector(`#term-fmt-${win.key}`)?.value || "json";
    const base = agentView ? `agent-console-${(clientName || clientId || "client")}` : `terminal-${(clientName || clientId || "client")}`;
    exportLogText(agentView ? agentConLogBuffer : termLogBuffer, base, fmt);
  });

  body.querySelector(`#term-clear-${win.key}`)?.addEventListener("click", () => {
    const cmd = isWindows ? "cls" : "clear";
    dashboardSocket.emit("term-input", { clientId, session: sessionId, data: cmd + "\r" });
    term.focus();
  });

  // --- Skript ausführen: gespeicherte Skripte laden (passend zum OS des
  //     Clients gefiltert) und den Befehl direkt in die laufende Shell tippen.
  //     Auswahl über ein durchsuchbares Menü MIT Ordnerstruktur.
  //     Mehrzeilige Skripte werden Zeile für Zeile mit Enter gesendet. ---
  const scriptBtn = body.querySelector(`#term-script-${win.key}`);
  const scriptMenu = body.querySelector(`#term-script-menu-${win.key}`);
  const scriptSearch = body.querySelector(`#term-script-search-${win.key}`);
  const scriptList = body.querySelector(`#term-script-list-${win.key}`);
  let termScripts = [];
  let selectedScript = null;

  function renderScriptMenu() {
    const q = (scriptSearch?.value || "").trim().toLowerCase();
    const match = (sc) => !q ||
      sc.name.toLowerCase().includes(q) ||
      (sc.folder || "").toLowerCase().includes(q) ||
      (sc.command || "").toLowerCase().includes(q);
    const filtered = termScripts.filter(match);
    if (!filtered.length) {
      scriptList.innerHTML = `<div style="color:var(--subtext);font-size:12px;padding:6px">Keine Skripte gefunden.</div>`;
      return;
    }
    // Nach Ordnern gruppieren (Ordner alphabetisch, "ohne Ordner" zuletzt)
    const groups = new Map();
    for (const sc of filtered) {
      const f = (sc.folder || "").trim();
      if (!groups.has(f)) groups.set(f, []);
      groups.get(f).push(sc);
    }
    const keys = [...groups.keys()].sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)));
    const item = (sc, indent) => `
      <div data-pick="${esc(sc.id)}" style="padding:5px 8px 5px ${indent}px;border-radius:6px;cursor:pointer;font-size:12px;display:flex;gap:6px;align-items:center${selectedScript?.id === sc.id ? ";background:var(--panel-2)" : ""}"
           onmouseover="this.style.background='var(--panel-2)'" onmouseout="this.style.background='${selectedScript?.id === sc.id ? "var(--panel-2)" : ""}'">
        <span>📜</span><span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(sc.name)}</span>
        <span style="font-size:10px;color:var(--subtext);text-transform:uppercase">${esc(sc.os)}</span>
      </div>`;
    scriptList.innerHTML = keys.map((f) => f === ""
      ? groups.get(f).map((sc) => item(sc, 8)).join("")
      : `<div style="font-size:11px;color:var(--subtext);font-weight:600;padding:6px 4px 2px">📁 ${esc(f)}</div>` +
        groups.get(f).map((sc) => item(sc, 20)).join("")
    ).join("");
    scriptList.querySelectorAll("[data-pick]").forEach((el) =>
      el.addEventListener("click", () => {
        selectedScript = termScripts.find((x) => x.id === el.dataset.pick) || null;
        if (selectedScript) scriptBtn.textContent = `📜 ${selectedScript.name}`;
        scriptMenu.classList.add("hidden");
        term.focus();
      })
    );
  }

  import("../api.js").then(({ api }) => api.getScripts()).then((scripts) => {
    termScripts = (scripts || []).filter((sc) =>
      sc.os === "any" || (isWindows ? sc.os === "windows" : sc.os === "linux"));
  }).catch(() => {});

  scriptBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    const show = scriptMenu.classList.contains("hidden");
    scriptMenu.classList.toggle("hidden", !show);
    if (show) { renderScriptMenu(); scriptSearch.value = ""; renderScriptMenu(); scriptSearch.focus(); }
  });
  scriptSearch?.addEventListener("input", renderScriptMenu);
  scriptSearch?.addEventListener("keydown", (e) => e.stopPropagation());
  scriptMenu?.addEventListener("click", (e) => e.stopPropagation());
  document.addEventListener("click", function closeMenu(e) {
    if (!document.body.contains(scriptMenu)) { document.removeEventListener("click", closeMenu); return; }
    if (!scriptMenu.classList.contains("hidden") && !scriptMenu.contains(e.target) && e.target !== scriptBtn) {
      scriptMenu.classList.add("hidden");
    }
  });

  function runSelectedScript() {
    const sc = selectedScript;
    if (!sc) { scriptBtn?.click(); return; }   // noch nichts gewählt -> Menü öffnen
    const lines = String(sc.command || "").replace(/\r\n/g, "\n").split("\n").filter((l) => l.trim() !== "");
    for (const line of lines) {
      dashboardSocket.emit("term-input", { clientId, session: sessionId, data: line + "\r" });
    }
    term.focus();
  }
  body.querySelector(`#term-script-run-${win.key}`)?.addEventListener("click", runSelectedScript);
  // Bewusst KEIN Ausführen beim Auswählen im Dropdown - erst der ▶-Button
  // sendet das Skript an die Shell.

  // --- Text-Sendebox (wie beim Remote Screen): kompletten Text auf einmal
  //     an die Shell schicken. Der Text geht 1:1 als Zeichen in das PTY -
  //     ein Tastaturlayout spielt dabei technisch keine Rolle, die Auswahl
  //     ist zur Konsistenz mit dem Remote-Screen vorhanden. ---
  const termTextInput = body.querySelector(`#term-text-${win.key}`);
  const termSendBtn = body.querySelector(`#term-send-${win.key}`);
  const termEnterChk = body.querySelector(`#term-enter-${win.key}`);
  const termLayoutSel = body.querySelector(`#term-layout-${win.key}`);
  function sendTermText() {
    const text = termTextInput.value;
    if (!text) return;
    // Layout-Kompensation (andersrum, auf Nutzerwunsch): "us" gewählt ->
    // Zeichen werden vor dem Senden über die Positions-Tabelle ersetzt
    // (Reverse-Effekt); "de"/"raw" = 1:1 senden.
    const mapped = mapKeyboardText(text, termLayoutSel?.value || "raw");
    dashboardSocket.emit("term-input", {
      clientId, session: sessionId,
      data: mapped + (termEnterChk?.checked ? "\r" : ""),
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
    dashboardSocket.off("agent-console-history", onAgentConsoleHistory);
    dashboardSocket.off("agent-console", onAgentConsoleLine);
    dashboardSocket.off("agent-console-ack", onAgentConsoleAck);
    if (agentView) dashboardSocket.emit("agent-console-close", { clientId });
    try { roAgent.disconnect(); } catch {}
    try { agentTerm?.dispose(); } catch {}
    dashboardSocket.emit("term-close", { clientId, session: sessionId });
    try { win._termRo?.disconnect(); } catch {}
    try { term?.dispose(); } catch {}
  });
}

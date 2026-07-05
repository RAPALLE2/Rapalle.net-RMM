// apps/terminal.js
// ----------------
// Inhalt eines Terminal-Fensters. Schickt eingegebene Befehle über die REST-API
// an den Agenten des Clients und zeigt die Ausgabe an.
//
// Clipboard: Kopieren/Einfügen funktioniert im Browser-Textfeld ganz normal
// (Strg+C/Strg+V im Eingabefeld). Zusätzlich kann man Ausgabe markieren und
// mit den normalen Browser-Mitteln kopieren. Ein "Kopieren"-Button für die
// gesamte Ausgabe ist unten eingebaut.

import { api } from "../api.js";
import { esc } from "../utils.js";

export function renderTerminal(body, win) {
  const { clientId, clientName } = win.props;

  // Eindeutige Session-ID pro Terminal-Fenster. Der Agent hält dazu ein
  // eigenes Arbeitsverzeichnis, damit "cd" über mehrere Befehle hinweg wirkt.
  const sessionId = (window.crypto?.randomUUID?.() || `term-${Date.now()}-${Math.random().toString(36).slice(2)}`);

  body.innerHTML = `
    <div class="terminal-body">
      <div style="display:flex;gap:6px;padding:6px 8px;border-bottom:1px solid var(--border);align-items:center">
        <span style="font-size:11px;color:var(--subtext)">Skript:</span>
        <select id="term-script-${win.key}" style="flex:1;padding:4px;border-radius:5px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
          <option value="">— laden... —</option>
        </select>
        <button class="taskbar-btn" id="term-runscript-${win.key}">▶ Ausführen</button>
      </div>
      <div class="terminal-output" id="term-out-${win.key}"></div>
      <div class="terminal-input-row">
        <span style="color:var(--accent)">$</span>
        <input type="text" id="term-in-${win.key}" placeholder="Befehl eingeben und Enter..." autocomplete="off" />
        <button class="taskbar-btn" id="term-copy-${win.key}" title="Ausgabe kopieren">⧉</button>
      </div>
    </div>
  `;

  const scriptSel = body.querySelector(`#term-script-${win.key}`);
  const runScriptBtn = body.querySelector(`#term-runscript-${win.key}`);

  const out = body.querySelector(`#term-out-${win.key}`);
  const input = body.querySelector(`#term-in-${win.key}`);
  const copyBtn = body.querySelector(`#term-copy-${win.key}`);

  // Befehlshistorie (mit Pfeil hoch/runter durchblätterbar)
  const cmdHistory = [];
  let historyIndex = -1;

  function addLine(text, cls = "") {
    const div = document.createElement("div");
    if (cls) div.className = cls;
    div.textContent = text;
    out.appendChild(div);
    out.scrollTop = out.scrollHeight;
  }

  addLine(`Verbunden mit ${clientName}`, "terminal-line-info");

  async function runCommand(cmd) {
    addLine(`$ ${cmd}`, "terminal-line-cmd");
    cmdHistory.push(cmd);
    historyIndex = cmdHistory.length;

    try {
      const res = await api.execOnClient(clientId, cmd, sessionId);
      if (res.stdout) addLine(res.stdout.replace(/\s+$/, ""));
      if (res.stderr) addLine(res.stderr.replace(/\s+$/, ""), "terminal-line-err");
      if (!res.stdout && !res.stderr) addLine(`(Exit-Code ${res.code}, keine Ausgabe)`, "terminal-line-info");
    } catch (e) {
      addLine(e.message, "terminal-line-err");
    }
  }

  // Gespeicherte Skripte ins Dropdown laden (Command wird als value hinterlegt)
  (async () => {
    try {
      const scripts = await api.getScripts();
      scriptSel.innerHTML = `<option value="">— Skript wählen —</option>` +
        scripts.map((s) => `<option value="${esc(s.command)}">${esc(s.name)} (${esc(s.os)})</option>`).join("");
    } catch {
      scriptSel.innerHTML = `<option value="">— keine Skripte —</option>`;
    }
  })();

  // Ausgewähltes Skript im Terminal ausführen
  runScriptBtn.addEventListener("click", () => {
    const cmd = scriptSel.value;
    if (cmd) runCommand(cmd);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const cmd = input.value.trim();
      if (cmd) {
        runCommand(cmd);
        input.value = "";
      }
    } else if (e.key === "ArrowUp") {
      // vorherigen Befehl aus der Historie holen
      if (historyIndex > 0) {
        historyIndex--;
        input.value = cmdHistory[historyIndex] || "";
      }
      e.preventDefault();
    } else if (e.key === "ArrowDown") {
      if (historyIndex < cmdHistory.length - 1) {
        historyIndex++;
        input.value = cmdHistory[historyIndex] || "";
      }
      e.preventDefault();
    }
  });

  // "Ausgabe kopieren"-Button -> gesamte Terminal-Ausgabe in die Zwischenablage
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(out.innerText);
      copyBtn.textContent = "✓";
      setTimeout(() => (copyBtn.textContent = "⧉"), 1000);
    } catch {
      alert("Kopieren nicht möglich (Browser-Berechtigung fehlt).");
    }
  });

  // Beim Öffnen direkt das Eingabefeld fokussieren
  setTimeout(() => input.focus(), 50);
}

import { t } from "../i18n.js";
import { attachLongPress } from "../touchgestures.js";
// apps/miniterm.js
// ----------------
// Schlanker, abhängigkeitsfreier Terminal-Emulator (VT100/ANSI-Subset).
// Bewusst OHNE xterm.js/CDN, damit das Terminal auch in abgeschotteten LANs
// ohne Internetzugang funktioniert. Deckt ab, was ein Remote-Shell-Terminal
// braucht: Zeichenausgabe, Zeilen/Spalten-Puffer, Cursor-Bewegung, Farben
// (SGR), Löschsequenzen, Backspace/CR/LF, Scrolling, sowie Tastatureingabe
// inkl. Steuertasten (Pfeile, Enter, Backspace, Tab, Strg-C usw.).
//
// API:
//   const term = new MiniTerm(hostEl, { onData, onResize });
//   term.write(str);   // Ausgabe vom Server einspeisen
//   term.focus();      // Eingabefokus setzen
//   term.dispose();

const COLORS = [
  "#0b0f14", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5",
  "#414868", "#ff9e9e", "#b9f27c", "#ffc777", "#9fb4ff", "#d7b9ff", "#a4e8ff", "#ffffff",
];

export class MiniTerm {
  constructor(host, opts = {}) {
    this.host = host;
    this.onData = opts.onData || (() => {});
    this.onResize = opts.onResize || (() => {});
    this.cols = 80;
    this.rows = 24;
    this.cx = 0;             // Cursor-Spalte
    this.cy = 0;             // Cursor-Zeile
    this.fg = 7; this.bg = 0; this.bold = false;
    this.buffer = [];        // Array von Zeilen; jede Zeile = Array von Zellen {ch, fg, bg, bold}
    this._parseState = "text";
    this._csi = "";
    this._scrollTop = 0;
    // Scrollverhalten: Solange der Benutzer unten steht, wandert die
    // Ansicht mit. Scrollt er hoch, bleibt sie stehen - sonst waere ein
    // Blick in die Historie unmöglich, weil jede neue Zeile zurückreisst.
    this._stick = true;
    this._pending = null;
    this._build();
    this._measure();
    this._render();
  }

  _build() {
    this.host.style.cssText += ";overflow:hidden;position:relative;background:#0b0f14;";
    // Ausgabe-Fläche - AUSWÄHLBAR, damit man Text mit der Maus markieren und
    // mit Strg+C / Rechtsklick kopieren kann.
    this.screen = document.createElement("pre");
    this.screen.style.cssText =
      "margin:0;padding:6px 8px;font-family:Menlo,Consolas,'DejaVu Sans Mono',monospace;" +
      "font-size:13px;line-height:1.2;color:#c0caf5;white-space:pre;outline:none;" +
      "height:100%;box-sizing:border-box;overflow-y:auto;cursor:text;user-select:text;";
    this.host.appendChild(this.screen);

    // Merken, ob der Benutzer gerade unten steht. Nur dann wird beim
    // nächsten Schreiben nachgescrollt.
    this.screen.addEventListener("scroll", () => {
      const rest = this.screen.scrollHeight - this.screen.scrollTop
                   - this.screen.clientHeight;
      this._stick = rest < 24;
      if (this._jump) this._jump.style.display = this._stick ? "none" : "block";
    });

    // Knopf "ans Ende springen". Beim Zurückblättern verliert man sonst
    // leicht den Anschluss an das, was gerade passiert.
    this._jump = document.createElement("button");
    this._jump.textContent = "↓ Ende";
    this._jump.title = "Zum Ende der Ausgabe springen";
    this._jump.style.cssText =
      "position:absolute;right:14px;bottom:10px;display:none;z-index:3;" +
      "background:#1a2233;color:#c0caf5;border:1px solid #2a3648;border-radius:14px;" +
      "padding:3px 10px;font-size:11.5px;cursor:pointer;opacity:.9";
    this._jump.addEventListener("click", () => {
      this._stick = true;
      this.screen.scrollTop = this.screen.scrollHeight;
      this._jump.style.display = "none";
      this.input.focus();
    });
    this.host.appendChild(this._jump);

    // Verstecktes Eingabefeld für Tastatur/IME/Paste. Off-screen, damit es die
    // Textauswahl im Screen nicht stört; wird bei Klick OHNE Selektion fokussiert.
    this.input = document.createElement("textarea");
    this.input.style.cssText =
      "position:absolute;width:1px;height:1px;padding:0;border:0;left:0;top:0;" +
      "opacity:0;resize:none;overflow:hidden;";
    this.input.autocapitalize = "off";
    this.input.autocomplete = "off";
    this.input.spellcheck = false;
    this.host.appendChild(this.input);

    // Klick: hat der Nutzer Text markiert -> Fokus NICHT stehlen (Kopieren
    // möglich). Sonst Eingabe fokussieren, damit Tippen ankommt.
    this.screen.addEventListener("mouseup", () => {
      const sel = window.getSelection?.().toString() || "";
      if (!sel) setTimeout(() => this.input.focus(), 0);
    });
    // Rechtsklick-Verhalten wie in klassischen Terminals (PuTTY-Stil):
    //   - Text markiert  -> Rechtsklick KOPIERT die Auswahl (und hebt sie auf)
    //   - nichts markiert -> Rechtsklick FÜGT die Zwischenablage EIN
    // Das native Browser-Kontextmenü wird dafür unterdrückt.
    // Rechtsklick-Verhalten - umschaltbar im Profil ("Terminal"-Abschnitt):
    //   "direct" (Standard, PuTTY-Stil):
    //     - Text markiert  -> Rechtsklick KOPIERT die Auswahl (und hebt sie auf)
    //     - nichts markiert -> Rechtsklick FÜGT die Zwischenablage EIN
    //   "menu": Rechtsklick öffnet ein Kontextmenü (Kopieren/Einfügen/...)
    // Die Einstellung wird bei JEDEM Rechtsklick frisch gelesen, damit sie
    // ohne Terminal-Neustart wirkt (localStorage-Key, serverseitig gesynct).
    // Auf dem Handy gibt es keine rechte Maustaste: langes Halten loest
    // denselben Weg aus wie ein Rechtsklick (Einfuegen bzw. Kontextmenue -
    // je nach Einstellung weiter unten).
    attachLongPress(this.screen, (cx, cy) => {
      this.screen.dispatchEvent(new MouseEvent("contextmenu", {
        bubbles: true, cancelable: true, clientX: cx, clientY: cy, button: 2,
      }));
    });

    this.screen.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const mode = (localStorage.getItem("rmm_term_rightclick") || "direct");
      const sel = window.getSelection?.().toString() || "";
      if (mode === "menu") {
        this._showCtxMenu(e.clientX, e.clientY, sel);
        return;
      }
      if (sel) {
        this._copy(sel);
        try { window.getSelection().removeAllRanges(); } catch {}
      } else {
        this._paste();
      }
      setTimeout(() => this.input.focus(), 0);
    });

    this.input.addEventListener("keydown", (e) => this._onKey(e));
    this.input.addEventListener("input", () => {
      if (this.input.value) { this.onData(this.input.value); this.input.value = ""; }
    });
    this.input.addEventListener("paste", (e) => {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData).getData("text");
      if (text) this.onData(text);
    });
  }

  _measure() {
    // Zeichenbreite/-höhe messen, um cols/rows zu bestimmen.
    const probe = document.createElement("span");
    probe.style.cssText = "font-family:Menlo,Consolas,monospace;font-size:13px;line-height:1.2;visibility:hidden;position:absolute;";
    probe.textContent = "MMMMMMMMMMMMMMMMMMMM"; // 20 Zeichen
    this.host.appendChild(probe);
    const cw = probe.getBoundingClientRect().width / 20 || 8;
    const chH = 13 * 1.2;
    probe.remove();
    const rect = this.host.getBoundingClientRect();
    this.cols = Math.max(20, Math.floor((rect.width - 16) / cw));
    this.rows = Math.max(6, Math.floor((rect.height - 12) / chH));
    // Puffer auf Größe bringen
    while (this.buffer.length < this.rows) this.buffer.push(this._blankLine());
  }

  fit() {
    const oldCols = this.cols, oldRows = this.rows;
    this._measure();
    if (this.cols !== oldCols || this.rows !== oldRows) {
      this.onResize(this.cols, this.rows);
      this._render();
    }
  }

  _blankLine() {
    const line = [];
    for (let i = 0; i < this.cols; i++) line.push({ ch: " ", fg: 7, bg: 0, bold: false });
    return line;
  }

  _ensureLine(y) {
    while (this.buffer.length <= y) this.buffer.push(this._blankLine());
  }

  // ---- Ausgabe verarbeiten (VT100/ANSI-Subset) ----
  write(str) {
    for (let i = 0; i < str.length; i++) {
      const ch = str[i];
      if (this._parseState === "text") {
        if (ch === "\x1b") { this._parseState = "esc"; }
        else this._putChar(ch);
      } else if (this._parseState === "esc") {
        if (ch === "[") { this._parseState = "csi"; this._csi = ""; }
        else if (ch === "]") { this._parseState = "osc"; }
        else { this._parseState = "text"; } // andere ESC-Sequenzen ignorieren
      } else if (this._parseState === "csi") {
        if (/[0-9;?]/.test(ch)) { this._csi += ch; }
        else { this._handleCsi(ch, this._csi); this._parseState = "text"; }
      } else if (this._parseState === "osc") {
        if (ch === "\x07") { this._parseState = "text"; }        // BEL beendet OSC
        else if (ch === "\x1b") { this._parseState = "osc_esc"; }
      } else if (this._parseState === "osc_esc") {
        this._parseState = "text";                               // ESC\ beendet OSC
      }
    }
    this._render();
  }

  _putChar(ch) {
    if (ch === "\r") { this.cx = 0; return; }
    if (ch === "\n") { this._newline(); return; }
    if (ch === "\b") { this.cx = Math.max(0, this.cx - 1); return; }
    if (ch === "\t") { this.cx = Math.min(this.cols - 1, (this.cx + 8) & ~7); return; }
    if (ch === "\x07") return; // Bell ignorieren
    if (ch < " ") return;      // andere Steuerzeichen ignorieren

    this._ensureLine(this.cy);
    if (this.cx >= this.cols) { this.cx = 0; this._newline(); }
    this.buffer[this.cy][this.cx] = { ch, fg: this.fg, bg: this.bg, bold: this.bold };
    this.cx++;
  }

  _newline() {
    this.cy++;
    if (this.cy >= this.buffer.length) this.buffer.push(this._blankLine());
    // Historie begrenzen. 10.000 Zeilen sind bei ~80 Zeichen rund 1 MB -
    // vertretbar, und man kann weit genug zurückblättern, um die Ausgabe
    // eines längeren Befehls vollständig zu sehen.
    const maxLines = 10000;
    if (this.buffer.length > maxLines) this.buffer = this.buffer.slice(-maxLines);
  }

  _handleCsi(cmd, params) {
    const args = params.split(";").filter((x) => x !== "").map((n) => parseInt(n, 10));
    const a0 = isNaN(args[0]) ? 0 : args[0];
    switch (cmd) {
      case "H": case "f": { // Cursor positionieren
        const row = (args[0] || 1) - 1, col = (args[1] || 1) - 1;
        this.cy = this._viewTop() + Math.max(0, row);
        this.cx = Math.max(0, col);
        this._ensureLine(this.cy);
        break;
      }
      case "A": this.cy = Math.max(this._viewTop(), this.cy - (a0 || 1)); break;
      case "B": this.cy = this.cy + (a0 || 1); this._ensureLine(this.cy); break;
      case "C": this.cx = Math.min(this.cols - 1, this.cx + (a0 || 1)); break;
      case "D": this.cx = Math.max(0, this.cx - (a0 || 1)); break;
      case "G": this.cx = Math.max(0, (a0 || 1) - 1); break;
      case "J": this._eraseDisplay(a0); break;
      case "K": this._eraseLine(a0); break;
      case "m": this._setGraphics(args); break;
      default: break; // unbekannte Sequenzen ignorieren
    }
  }

  _viewTop() { return Math.max(0, this.buffer.length - this.rows); }

  _eraseDisplay(mode) {
    if (mode === 2 || mode === 3) {
      // gesamten sichtbaren Bereich löschen
      const top = this._viewTop();
      for (let y = top; y < this.buffer.length; y++) this.buffer[y] = this._blankLine();
      this.cx = 0; this.cy = top;
    } else if (mode === 0) {
      // vom Cursor bis Ende
      this._ensureLine(this.cy);
      for (let x = this.cx; x < this.cols; x++) this.buffer[this.cy][x] = { ch: " ", fg: 7, bg: 0, bold: false };
      for (let y = this.cy + 1; y < this.buffer.length; y++) this.buffer[y] = this._blankLine();
    }
  }

  _eraseLine(mode) {
    this._ensureLine(this.cy);
    const line = this.buffer[this.cy];
    if (mode === 0) { for (let x = this.cx; x < this.cols; x++) line[x] = { ch: " ", fg: 7, bg: 0, bold: false }; }
    else if (mode === 1) { for (let x = 0; x <= this.cx; x++) line[x] = { ch: " ", fg: 7, bg: 0, bold: false }; }
    else { for (let x = 0; x < this.cols; x++) line[x] = { ch: " ", fg: 7, bg: 0, bold: false }; }
  }

  _setGraphics(args) {
    if (!args.length) args = [0];
    for (const n of args) {
      if (n === 0) { this.fg = 7; this.bg = 0; this.bold = false; }
      else if (n === 1) this.bold = true;
      else if (n === 22) this.bold = false;
      else if (n >= 30 && n <= 37) this.fg = n - 30;
      else if (n >= 90 && n <= 97) this.fg = n - 90 + 8;
      else if (n === 39) this.fg = 7;
      else if (n >= 40 && n <= 47) this.bg = n - 40;
      else if (n >= 100 && n <= 107) this.bg = n - 100 + 8;
      else if (n === 49) this.bg = 0;
    }
  }

  // ---- Darstellung ----
  // Wie viele Zeilen Historie tatsächlich gezeichnet werden. Der Puffer
  // hält mehr; alles auf einmal zu zeichnen wäre bei jedem Tastendruck zu
  // teuer. 3000 Zeilen sind reichlich und bleiben flüssig.
  static get RENDER_LINES() { return 3000; }

  _render() {
    // VORHER wurde nur das Sichtfenster der letzten `rows` Zeilen gezeichnet
    // und danach hart ans Ende gescrollt. Der 5000-Zeilen-Puffer existierte
    // also, war aber nie zu sehen - Zurückblättern war unmöglich. Jetzt wird
    // die Historie mitgezeichnet, und das Nachscrollen passiert nur, wenn
    // der Benutzer ohnehin unten steht.
    const top = Math.max(0, this.buffer.length - MiniTerm.RENDER_LINES);
    const frag = [];
    for (let y = top; y < this.buffer.length; y++) {
      const line = this.buffer[y];
      let html = "", runFg = -1, runBg = -1, runBold = false, open = false;
      for (let x = 0; x < line.length; x++) {
        const c = line[x];
        const isCursor = (y === this.cy && x === this.cx);
        if (c.fg !== runFg || c.bg !== runBg || c.bold !== runBold || isCursor) {
          if (open) html += "</span>";
          runFg = c.fg; runBg = c.bg; runBold = c.bold;
          const fgc = COLORS[c.bold && runFg < 8 ? runFg + 8 : runFg] || COLORS[7];
          const bgc = isCursor ? "#c0caf5" : (c.bg ? COLORS[c.bg] : "transparent");
          const fg2 = isCursor ? "#0b0f14" : fgc;
          html += `<span style="color:${fg2};background:${bgc};${c.bold ? "font-weight:bold;" : ""}">`;
          open = true;
        }
        html += this._escHtml(c.ch);
      }
      if (open) html += "</span>";
      frag.push(html);
    }
    this.screen.innerHTML = frag.join("\n");
    if (this._stick) {
      this.screen.scrollTop = this.screen.scrollHeight;
      if (this._jump) this._jump.style.display = "none";
    } else if (this._jump) {
      this._jump.style.display = "block";
    }
  }

  _escHtml(ch) {
    if (ch === "&") return "&amp;";
    if (ch === "<") return "&lt;";
    if (ch === ">") return "&gt;";
    return ch;
  }

  // ---- Tastatur -> Terminalsequenzen ----
  _onKey(e) {
    const k = e.key;

    // --- Blättern in der Historie ---
    // Shift+Bild-auf/ab ist die übliche Belegung im Terminal. Ohne Shift
    // gehören die Tasten der Anwendung (z.B. less oder ein Editor), deshalb
    // wird nur die Kombination mit Shift abgefangen.
    if (e.shiftKey && (k === "PageUp" || k === "PageDown")) {
      e.preventDefault();
      const step = this.screen.clientHeight * 0.9;
      this.screen.scrollTop += (k === "PageUp" ? -step : step);
      return;
    }
    // Strg+Shift+Pos1/Ende: an den Anfang bzw. ans Ende der Historie.
    if (e.ctrlKey && e.shiftKey && (k === "Home" || k === "End")) {
      e.preventDefault();
      if (k === "Home") {
        this.screen.scrollTop = 0;
      } else {
        this._stick = true;
        this.screen.scrollTop = this.screen.scrollHeight;
      }
      return;
    }

    // --- Copy/Paste zuerst behandeln ---
    const sel = window.getSelection?.().toString() || "";
    // Strg+Shift+C = kopieren (Terminal-Konvention). Strg+C nur kopieren, wenn
    // etwas markiert ist - sonst als Abbruch (SIGINT) an die Shell senden.
    if ((e.ctrlKey && e.shiftKey && (k === "C" || k === "c")) ||
        (e.ctrlKey && !e.shiftKey && (k === "c" || k === "C") && sel)) {
      if (sel) {
        e.preventDefault();
        this._copy(sel);
        return;
      }
      // keine Selektion -> unten als \x03 behandeln
    }
    // Strg+V / Strg+Shift+V = einfügen. Async aus der Zwischenablage lesen und
    // senden; das native paste-Event greift zusätzlich als Fallback.
    if (e.ctrlKey && (k === "v" || k === "V")) {
      e.preventDefault();
      this._paste();
      return;
    }
    // Klassiker: Strg+Einfg = Kopieren, Shift+Einfg = Einfügen.
    if (k === "Insert" && e.ctrlKey && sel) {
      e.preventDefault();
      this._copy(sel);
      return;
    }
    if (k === "Insert" && e.shiftKey) {
      e.preventDefault();
      this._paste();
      return;
    }

    let seq = null;
    if (k === "Enter") seq = "\r";
    else if (k === "Backspace") seq = "\x7f";
    else if (k === "Tab") seq = "\t";
    else if (k === "Escape") seq = "\x1b";
    else if (k === "ArrowUp") seq = "\x1b[A";
    else if (k === "ArrowDown") seq = "\x1b[B";
    else if (k === "ArrowRight") seq = "\x1b[C";
    else if (k === "ArrowLeft") seq = "\x1b[D";
    else if (k === "Home") seq = "\x1b[H";
    else if (k === "End") seq = "\x1b[F";
    else if (k === "Delete") seq = "\x1b[3~";
    else if (k === "PageUp") seq = "\x1b[5~";
    else if (k === "PageDown") seq = "\x1b[6~";
    else if (e.ctrlKey && k.length === 1) {
      const code = k.toLowerCase().charCodeAt(0);
      if (code >= 97 && code <= 122) seq = String.fromCharCode(code - 96);
    }
    if (seq !== null) {
      e.preventDefault();
      this.onData(seq);
    }
    // Normale Zeichen laufen über das 'input'-Event (IME/Umlaute/AltGr).
  }

  _copy(text) {
    try {
      navigator.clipboard.writeText(text);
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = text; ta.style.cssText = "position:fixed;opacity:0";
        document.body.appendChild(ta); ta.select();
        document.execCommand("copy"); ta.remove();
      } catch {}
    }
  }

  // Kleines Kontextmenü fürs Terminal (Modus "menu").
  _showCtxMenu(x, y, sel) {
    this._closeCtxMenu();
    const menu = document.createElement("div");
    this._ctxMenu = menu;
    menu.style.cssText =
      "position:fixed;z-index:9999;background:var(--panel);border:1px solid var(--border);" +
      "border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.5);padding:4px;min-width:190px;font-size:13px";
    const items = [
      { label: "📋  Kopieren", hint: "Strg+Shift+C", disabled: !sel, fn: () => {
          this._copy(sel);
          try { window.getSelection().removeAllRanges(); } catch {}
        } },
      { label: t("u_einfugen"), hint: "Strg+V", fn: () => this._paste() },
      { label: "🔎  Alles kopieren", fn: () => this._copy(this._allText()) },
      { label: "✖  Auswahl aufheben", disabled: !sel, fn: () => {
          try { window.getSelection().removeAllRanges(); } catch {}
        } },
    ];
    for (const it of items) {
      const row = document.createElement("div");
      row.style.cssText = "display:flex;gap:14px;justify-content:space-between;padding:6px 12px;" +
        `border-radius:6px;white-space:nowrap;${it.disabled ? "opacity:.45" : "cursor:pointer"}`;
      row.innerHTML = `<span>${it.label}</span>` +
        (it.hint ? `<span style="color:var(--subtext);font-size:11px">${it.hint}</span>` : "");
      if (!it.disabled) {
        row.addEventListener("mouseenter", () => { row.style.background = "var(--panel-2)"; });
        row.addEventListener("mouseleave", () => { row.style.background = "transparent"; });
        row.addEventListener("click", (e) => {
          e.stopPropagation();
          this._closeCtxMenu();
          it.fn();
          setTimeout(() => this.input.focus(), 0);
        });
      }
      menu.appendChild(row);
    }
    document.body.appendChild(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = Math.min(x, window.innerWidth - r.width - 8) + "px";
    menu.style.top = Math.min(y, window.innerHeight - r.height - 8) + "px";
    this._ctxCloser = () => this._closeCtxMenu();
    setTimeout(() => document.addEventListener("click", this._ctxCloser, { once: true }), 0);
  }
  _closeCtxMenu() {
    if (this._ctxMenu) { this._ctxMenu.remove(); this._ctxMenu = null; }
    if (this._ctxCloser) { document.removeEventListener("click", this._ctxCloser); this._ctxCloser = null; }
  }
  // Kompletter sichtbarer Puffer als Text (für "Alles kopieren").
  _allText() {
    try {
      return this.buffer.map((row) => row.map((c) => c?.ch || " ").join("").replace(/\s+$/, "")).join("\n");
    } catch { return ""; }
  }

  async _paste() {
    let text = "";
    try { text = await navigator.clipboard.readText(); } catch { text = ""; }
    if (text) this.onData(text);
    // Falls die Clipboard-API blockiert (HTTP): das native paste-Event
    // (Strg+V löst es aus) übernimmt. Nichts weiter zu tun.
  }

  focus() { try { this.input.focus(); } catch {} }
  dispose() { try { this.host.innerHTML = ""; } catch {} }
}

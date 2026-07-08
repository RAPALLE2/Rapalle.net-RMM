// windowmanager.js
// -----------------
// Verwaltet alle offenen "Fenster" (Terminal, File-Explorer, Einstellungen, ...).
//
// WICHTIG (Architektur): Jedes Fenster wird GENAU EINMAL als DOM-Element
// erstellt und dann behalten. Bewegen/Größe ändern/Fokus setzen aktualisiert
// nur noch einzelne CSS-Eigenschaften des vorhandenen Elements - es wird NICHT
// das ganze HTML neu gebaut. Dadurch bleiben Event-Handler, Eingabefokus und
// laufende Inhalte (Terminal-Ausgabe, Live-Bild) erhalten.
//
// Ein Fenster-Objekt (in state.windows) sieht so aus:
//   { key, appId, title, x, y, w, h, minimized, props, clientColor,
//     _el (das DOM-Element), _rendered (ob der Inhalt schon gerendert wurde) }

import { state } from "./state.js";
import { esc } from "./utils.js";

// Optionale Aufräum-Funktionen pro Fenster-Key (z.B. VNC: Stream stoppen,
// Socket-Listener entfernen). Apps können hier eine Funktion hinterlegen.
const cleanupFns = {};
export function registerCleanup(key, fn) { cleanupFns[key] = fn; }

// Wird von app.js gesetzt: Funktion, die den Inhalt eines Fensters rendert.
let contentRenderer = null;
export function setContentRenderer(fn) { contentRenderer = fn; }

// Wird von app.js gesetzt: Callback, wenn sich die Fensterliste ändert
// (damit die Taskbar aktualisiert werden kann).
let onWindowsChanged = null;
export function setOnWindowsChanged(fn) { onWindowsChanged = fn; }

function notifyChanged() {
  if (onWindowsChanged) onWindowsChanged();
}

let cascade = 0; // sorgt für leicht versetzte Positionierung neuer Fenster

const layer = () => document.getElementById("window-layer");

// Bei Größenänderung des Browserfensters alle offenen Fenster wieder
// vollständig in den sichtbaren Bereich klemmen (sonst können sie beim
// Verkleinern hinter Topbar/Taskleiste rutschen und unerreichbar werden).
let _resizeClampTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(_resizeClampTimer);
  _resizeClampTimer = setTimeout(() => {
    for (const win of state.windows) {
      if (!win.minimized && !win.maximized) clampWindowIntoView(win);
    }
  }, 100);
});

// -----------------------------------------------------------------
// Öffnen / Schließen / Fokus / Minimieren
// -----------------------------------------------------------------

export function openWindow({ key, appId, title, props = {}, clientColor = null, w = 640, h = 460,
                            x = null, y = null, minimized = false, maximized = false, focus = true }) {
  const existing = state.windows.find((win) => win.key === key);
  if (existing) {
    // Fenster existiert schon -> wiederherstellen und nach vorne holen
    existing.minimized = false;
    if (existing._el) existing._el.style.display = "flex";
    focusWindow(key);
    return;
  }

  cascade = (cascade + 1) % 6;
  const win = {
    key, appId, title, props, clientColor,
    x: x != null ? x : 60 + cascade * 26,
    y: y != null ? y : 30 + cascade * 22,
    w, h,
    minimized: !!minimized,
    maximized: !!maximized,
    _el: null,
    _rendered: false,
  };
  state.windows.push(win);
  state.focusOrder.push(key);

  createWindowElement(win); // EINMALIG das DOM-Element bauen
  if (win.maximized) {
    // Maximiert wiederherstellen: gespeicherte Normalgeometrie merken, dann maximieren.
    win._restore = { x: win.x, y: win.y, w: win.w, h: win.h };
    win.maximized = false;      // toggleMaximize erwartet den Ausgangszustand
    toggleMaximize(key);
  } else {
    clampWindowIntoView(win);
  }
  if (win.minimized && win._el) win._el.style.display = "none";
  applyFocusZIndex();
  if (focus && !win.minimized) focusWindow(key);
  notifyChanged();
}

// Klemmt ein Fenster vollständig in den sichtbaren Fensterbereich (layer).
export function clampWindowIntoView(win) {
  const lay = layer();
  if (!lay || !win._el) return;
  const maxX = Math.max(0, lay.clientWidth - win._el.offsetWidth);
  const maxY = Math.max(0, lay.clientHeight - win._el.offsetHeight);
  win.x = Math.min(Math.max(0, win.x), maxX);
  win.y = Math.min(Math.max(0, win.y), maxY);
  win._el.style.left = `${win.x}px`;
  win._el.style.top = `${win.y}px`;
}

export function closeWindow(key) {
  // Falls die App eine Aufräum-Funktion registriert hat (z.B. VNC-Stream stoppen)
  const win = state.windows.find((w) => w.key === key);
  // Schließ-Animation abspielen, dann tatsächlich entfernen.
  if (win && win._el && !win._closing) {
    win._closing = true;
    win._el.classList.add("win-closing");
    setTimeout(() => _removeWindow(key), 150);
    return;
  }
  _removeWindow(key);
}

function _removeWindow(key) {
  if (cleanupFns[key]) {
    try { cleanupFns[key](); } catch (e) { console.error(e); }
    delete cleanupFns[key];
  }

  const win = state.windows.find((w) => w.key === key);
  if (win && win._el) win._el.remove(); // DOM-Element wirklich entfernen

  state.windows = state.windows.filter((w) => w.key !== key);
  state.focusOrder = state.focusOrder.filter((k) => k !== key);
  applyFocusZIndex();
  notifyChanged();
}

export function focusWindow(key) {
  state.focusOrder = state.focusOrder.filter((k) => k !== key);
  state.focusOrder.push(key);
  applyFocusZIndex();
  notifyChanged();
}

export function toggleMinimize(key) {
  const win = state.windows.find((w) => w.key === key);
  if (!win) return;
  win.minimized = !win.minimized;
  if (win._el) {
    if (win.minimized) {
      // Einklapp-Animation, dann ausblenden.
      win._el.classList.add("win-minimizing");
      setTimeout(() => {
        win._el.style.display = "none";
        win._el.classList.remove("win-minimizing");
      }, 190);
    } else {
      win._el.style.display = "flex";
      win._el.classList.remove("win-minimizing");
      win._el.style.animation = "win-open 0.18s cubic-bezier(0.22,1,0.36,1)";
      setTimeout(() => { if (win._el) win._el.style.animation = ""; }, 200);
    }
  }
  if (!win.minimized) focusWindow(key);
  notifyChanged();
}

export function toggleMaximize(key) {
  const win = state.windows.find((w) => w.key === key);
  if (!win || !win._el) return;

  // Für die Dauer des Wechsels sanfte Geometrie-Transition aktivieren.
  win._el.classList.add("win-animate-geo");
  setTimeout(() => { if (win._el) win._el.classList.remove("win-animate-geo"); }, 230);

  if (win.maximized) {
    // Wiederherstellen: gemerkte Position/Größe zurücksetzen
    win.maximized = false;
    win._el.style.left = `${win._restore.x}px`;
    win._el.style.top = `${win._restore.y}px`;
    win._el.style.width = `${win._restore.w}px`;
    win._el.style.height = `${win._restore.h}px`;
    win.x = win._restore.x; win.y = win._restore.y;
    win.w = win._restore.w; win.h = win._restore.h;
  } else {
    // Maximieren: aktuelle Maße merken, dann auf den ganzen Fensterbereich strecken
    win._restore = { x: win.x, y: win.y, w: win.w, h: win.h };
    win.maximized = true;
    win._el.style.left = "0px";
    win._el.style.top = "0px";
    win._el.style.width = "100%";
    win._el.style.height = "100%";
  }
  focusWindow(key);
  notifyChanged();
}

export function minimizeAll() {
  state.windows.forEach((win) => {
    win.minimized = true;
    if (win._el) win._el.style.display = "none";
  });
  notifyChanged();
}

// -----------------------------------------------------------------
// z-Index (welches Fenster liegt oben) anhand der Fokus-Reihenfolge
// -----------------------------------------------------------------

function applyFocusZIndex() {
  state.windows.forEach((win) => {
    if (win._el) {
      win._el.style.zIndex = 100 + state.focusOrder.indexOf(win.key);
    }
  });
}

// -----------------------------------------------------------------
// DOM-Element eines Fensters EINMALIG erstellen
// -----------------------------------------------------------------

function createWindowElement(win) {
  const el = document.createElement("div");
  el.className = "rmm-window";
  el.style.left = `${win.x}px`;
  el.style.top = `${win.y}px`;
  el.style.width = `${win.w}px`;
  el.style.height = `${win.h}px`;
  el.style.display = win.minimized ? "none" : "flex";

  // Klick irgendwo aufs Fenster -> nach vorne holen
  el.addEventListener("mousedown", () => focusWindow(win.key));

  // --- Titelleiste ---
  const titlebar = document.createElement("div");
  titlebar.className = "rmm-window-titlebar";

  const titleSpan = document.createElement("span");
  titleSpan.textContent = win.title;

  const controls = document.createElement("span");
  controls.className = "win-controls";

  const minBtn = document.createElement("button");
  minBtn.textContent = "–";
  minBtn.title = "Minimieren";
  // Wichtig: mousedown statt click verhindern, dass das Drag/Focus dazwischenfunkt
  minBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleMinimize(win.key); });
  minBtn.addEventListener("mousedown", (e) => e.stopPropagation());

  const maxBtn = document.createElement("button");
  maxBtn.textContent = "□";
  maxBtn.title = "Vollbild / Wiederherstellen";
  maxBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleMaximize(win.key); });
  maxBtn.addEventListener("mousedown", (e) => e.stopPropagation());

  const closeBtn = document.createElement("button");
  closeBtn.textContent = "✕";
  closeBtn.title = "Schließen";
  closeBtn.addEventListener("click", (e) => { e.stopPropagation(); closeWindow(win.key); });
  closeBtn.addEventListener("mousedown", (e) => e.stopPropagation());

  controls.appendChild(minBtn);
  controls.appendChild(maxBtn);
  controls.appendChild(closeBtn);
  titlebar.appendChild(titleSpan);
  titlebar.appendChild(controls);
  makeDraggable(titlebar, el, win);
  // Doppelklick auf die Titelleiste = maximieren/wiederherstellen (wie üblich)
  titlebar.addEventListener("dblclick", (e) => {
    if (e.target.closest(".win-controls")) return;
    toggleMaximize(win.key);
  });
  el.appendChild(titlebar);

  // --- Inhalt ---
  const body = document.createElement("div");
  body.className = "rmm-window-body";
  el.appendChild(body);

  // --- Resize-Griffe: Ecke (beide Achsen) + Kanten (rechts, unten) ---
  const resizeHandle = document.createElement("div");
  resizeHandle.className = "rmm-window-resize-handle";
  makeResizable(resizeHandle, el, win, "both");
  el.appendChild(resizeHandle);

  const edgeR = document.createElement("div");
  edgeR.className = "rmm-window-resize-edge-r";
  makeResizable(edgeR, el, win, "x");
  el.appendChild(edgeR);

  const edgeB = document.createElement("div");
  edgeB.className = "rmm-window-resize-edge-b";
  makeResizable(edgeB, el, win, "y");
  el.appendChild(edgeB);

  win._el = el;
  layer().appendChild(el);

  // Inhalt EINMALIG rendern (danach lebt er eigenständig weiter)
  if (contentRenderer) {
    contentRenderer(body, win);
    win._rendered = true;
  }
}

// -----------------------------------------------------------------
// Verschieben (Drag) - aktualisiert live, ohne Neu-Rendern
// -----------------------------------------------------------------

function makeDraggable(handle, windowEl, win) {
  handle.addEventListener("mousedown", (e) => {
    // Klicks auf die Steuerungs-Buttons nicht als Drag interpretieren
    if (e.target.closest(".win-controls")) return;
    e.preventDefault();
    focusWindow(win.key);

    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = win.x;
    const startTop = win.y;

    function onMove(ev) {
      let nx = startLeft + (ev.clientX - startX);
      let ny = startTop + (ev.clientY - startY);
      // In die Grenzen des Fensterbereichs (layer) klemmen, damit kein Rand
      // je außerhalb landet. layer sitzt zwischen Topbar (oben) und Taskleiste
      // (unten); Fenster-Koordinaten sind relativ dazu.
      const lay = layer();
      if (lay) {
        const maxX = Math.max(0, lay.clientWidth - windowEl.offsetWidth);
        const maxY = Math.max(0, lay.clientHeight - windowEl.offsetHeight);
        nx = Math.min(Math.max(0, nx), maxX);
        ny = Math.min(Math.max(0, ny), maxY);
      }
      win.x = nx;
      win.y = ny;
      windowEl.style.left = `${win.x}px`;
      windowEl.style.top = `${win.y}px`;
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      notifyChanged();   // neue Position speichern
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

// -----------------------------------------------------------------
// Größe ändern (Resize) - ebenfalls live
// -----------------------------------------------------------------

function makeResizable(handle, windowEl, win, axis = "both") {
  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    // Wenn maximiert, erst wiederherstellen (sonst ergibt Resizen keinen Sinn)
    if (win.maximized) return;

    const startX = e.clientX;
    const startY = e.clientY;
    const startW = win.w;
    const startH = win.h;
    // Während des Ziehens Textauswahl verhindern (sonst wird alles markiert)
    document.body.style.userSelect = "none";

    function onMove(ev) {
      const lay = layer();
      if (axis === "x" || axis === "both") {
        let nw = Math.max(380, startW + (ev.clientX - startX));
        if (lay) nw = Math.min(nw, lay.clientWidth - win.x);  // nicht über rechten Rand
        win.w = nw;
        windowEl.style.width = `${win.w}px`;
      }
      if (axis === "y" || axis === "both") {
        let nh = Math.max(260, startH + (ev.clientY - startY));
        if (lay) nh = Math.min(nh, lay.clientHeight - win.y);  // nicht über unteren Rand
        win.h = nh;
        windowEl.style.height = `${win.h}px`;
      }
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
      notifyChanged();   // neue Größe speichern
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

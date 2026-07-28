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

// -----------------------------------------------------------------
// Mehrfach-Instanzen: Standardmäßig holt ein erneutes Öffnen das bereits
// vorhandene Fenster nach vorne. Wer wirklich eine ZWEITE Instanz will, hält
// beim Klick die Umschalttaste (Shift) gedrückt.
//
// Warum ein globaler Merker statt eines Parameters: Die Apps öffnen Fenster an
// dutzenden Stellen (Startmenü, Kacheln, Kontextmenüs, Tastenkürzel). Den
// Modifier überall durchzureichen wäre fehleranfällig - hier wird er einmal
// zentral mitgeschnitten.
// -----------------------------------------------------------------
let _shiftDown = false;
try {
  window.addEventListener("keydown", (e) => { if (e.key === "Shift") _shiftDown = true; }, true);
  window.addEventListener("keyup", (e) => { if (e.key === "Shift") _shiftDown = false; }, true);
  // Maus-/Touch-Ereignisse tragen den echten Zustand - zuverlässiger als
  // keydown/keyup, wenn zwischendurch der Fokus gewechselt hat.
  window.addEventListener("mousedown", (e) => { _shiftDown = !!e.shiftKey; }, true);
  window.addEventListener("click", (e) => { _shiftDown = !!e.shiftKey; }, true);
  window.addEventListener("blur", () => { _shiftDown = false; });
} catch {}

// Für Apps, die selbst entscheiden wollen (z.B. „Neues Terminal"-Knopf).
export function wantsNewInstance() { return _shiftDown; }

const layer = () => document.getElementById("window-layer");

// Bei Größenänderung des Browserfensters alle offenen Fenster wieder
// vollständig in den sichtbaren Bereich klemmen (sonst können sie beim
// Verkleinern hinter Topbar/Taskleiste rutschen und unerreichbar werden).
let _resizeClampTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(_resizeClampTimer);
  _resizeClampTimer = setTimeout(() => {
    for (const win of state.windows) {
      if (!win.minimized && !win.maximized && !win.snap) clampWindowIntoView(win);
    }
    relayoutSnapped(false);   // gesnappte Fenster an die neue Layer-Größe anpassen
    updateSnapDividers();
  }, 100);
});

// -----------------------------------------------------------------
// Öffnen / Schließen / Fokus / Minimieren
// -----------------------------------------------------------------

export function openWindow({ key, appId, title, props = {}, clientColor = null, w = 640, h = 460,
                            x = null, y = null, minimized = false, maximized = false, focus = true,
                            singleton = false, pinned = false, newInstance = null }) {
  // Alte, noch mit '#' persistierte Keys reparieren (CSS-Selektor-sicher).
  key = String(key).replace(/#/g, "--");
  const existing = state.windows.find((win) => win.key === key);
  // Soll wirklich eine ZWEITE Instanz entstehen?
  //   newInstance === true/false -> Aufrufer entscheidet hart
  //   newInstance === null       -> Umschalttaste entscheidet
  const forceNew = newInstance === true || (newInstance !== false && _shiftDown);
  if (existing && !singleton && forceNew) {
    // Mehrfach-Instanzen: für die neue Instanz einen eindeutigen Schlüssel
    // ableiten (key--2, key--3, ...) und ein FRISCHES Fenster bauen.
    // WICHTIG: KEIN '#' im Suffix! Die Apps bauen Element-IDs aus win.key
    // (z.B. querySelector(`#term-text-${win.key}`)) - ein '#' im Key machte
    // diese Selektoren ungültig, wodurch jede 2./3. Instanz "unfunktionell"
    // war (v.a. mehrere Terminals desselben Clients). Suffix "--n".
    let n = 2;
    while (state.windows.some((win) => win.key === `${key}--${n}`)) n++;
    key = `${key}--${n}`;
  } else if (existing) {
    // Fenster existiert schon -> Inhalt/Props aktualisieren (wichtig, wenn
    // dasselbe "Slot" mit anderem Inhalt herausgelöst wird), wiederherstellen
    // und nach vorne holen.
    existing.props = props;
    existing.title = title;
    existing.clientColor = clientColor;
    existing.minimized = false;
    if (existing._el) {
      existing._el.style.display = "flex";
      const titleEl = existing._el.querySelector(".rmm-window-title-text, .rmm-window-title");
      if (titleEl) titleEl.textContent = title;
      const body = existing._el.querySelector(".rmm-window-body");
      if (body && contentRenderer) { body.innerHTML = ""; contentRenderer(body, existing); }
    }
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
    pinned: !!pinned,
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
  clearSnapAssist();
  updateSnapDividers();
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
  updateSnapDividers();
  notifyChanged();
}

export function toggleMaximize(key) {
  const win = state.windows.find((w) => w.key === key);
  if (!win || !win._el) return;

  // Ein gesnapptes Fenster verlässt beim Maximieren das Snap-Layout.
  if (win.snap) { win.snap = null; updateSnapDividers(); }
  clearSnapAssist();

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
  // Angepinnte Fenster (📌) liegen IMMER über allen normalen Fenstern -
  // untereinander gilt weiterhin die Fokus-Reihenfolge. Offset 2000 bleibt
  // sicher unter Dialogen (9500), Pickern (8000) und Drag-Karten (9999).
  state.windows.forEach((win) => {
    if (win._el) {
      win._el.style.zIndex = (win.pinned ? 2000 : 100) + state.focusOrder.indexOf(win.key);
    }
  });
}

export function togglePin(key) {
  const win = state.windows.find((w) => w.key === key);
  if (!win) return;
  win.pinned = !win.pinned;
  if (win._pinBtn) {
    win._pinBtn.classList.toggle("pinned", win.pinned);
    win._pinBtn.title = win.pinned ? "Nicht mehr anheften" : "Anheften (immer im Vordergrund)";
  }
  focusWindow(key);   // wendet auch den neuen z-Index an
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
  titleSpan.className = "rmm-window-title";
  titleSpan.textContent = win.title;

  const controls = document.createElement("span");
  controls.className = "win-controls";

  const pinBtn = document.createElement("button");
  // Nur das Icon (Glyph) wird schräg gestellt/gedreht, NICHT die Button-Box.
  // Dadurch bleibt der Hover-/Angepinnt-Hintergrund immer gerade.
  pinBtn.innerHTML = '<span class="win-pin-glyph">📌</span>';
  pinBtn.className = "win-pin-btn" + (win.pinned ? " pinned" : "");
  pinBtn.title = win.pinned ? "Nicht mehr anheften" : "Anheften (immer im Vordergrund)";
  pinBtn.addEventListener("click", (e) => { e.stopPropagation(); togglePin(win.key); });
  pinBtn.addEventListener("mousedown", (e) => e.stopPropagation());
  win._pinBtn = pinBtn;

  const minBtn = document.createElement("button");
  minBtn.textContent = "–";
  minBtn.title = "Minimieren";
  // Wichtig: mousedown statt click verhindern, dass das Drag/Focus dazwischenfunkt
  minBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleMinimize(win.key); });
  minBtn.addEventListener("mousedown", (e) => e.stopPropagation());

  const maxBtn = document.createElement("button");
  maxBtn.className = "win-max-btn";   // NUR am Handy per CSS versteckt (Fenster dort eh Vollbild)
  maxBtn.textContent = "□";
  maxBtn.title = "Vollbild / Wiederherstellen";
  maxBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleMaximize(win.key); });
  maxBtn.addEventListener("mousedown", (e) => e.stopPropagation());

  const closeBtn = document.createElement("button");
  closeBtn.textContent = "✕";
  closeBtn.title = "Schließen";
  closeBtn.addEventListener("click", (e) => { e.stopPropagation(); closeWindow(win.key); });
  closeBtn.addEventListener("mousedown", (e) => e.stopPropagation());

  controls.appendChild(pinBtn);
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
    clearSnapAssist();

    const startX = e.clientX;
    const startY = e.clientY;
    let startLeft = win.x;
    let startTop = win.y;
    // Wo (relativ, 0..1) hat der Nutzer die Titelleiste gegriffen? Wird beim
    // Loslösen aus Snap/Maximiert gebraucht, damit das Fenster unter dem
    // Cursor "einrastet" statt wegzuspringen (Windows-Verhalten).
    const grabFrac = (e.clientX - windowEl.getBoundingClientRect().left) /
                     Math.max(1, windowEl.offsetWidth);
    let dragging = false;
    let released = false;   // wurde ein gesnapptes/maximiertes Fenster schon gelöst?

    function releaseFromSnap(ev) {
      // Fenster aus Snap/Maximiert lösen: alte Größe wiederherstellen und so
      // positionieren, dass der Cursor proportional auf der Titelleiste bleibt.
      const r = (win.maximized ? win._restore : win._snapRestore) || { w: 640, h: 460 };
      win.maximized = false;
      win.snap = null;
      win.w = r.w; win.h = r.h;
      const lay = layer();
      const layRect = lay ? lay.getBoundingClientRect() : { left: 0, top: 0 };
      win.x = Math.round(ev.clientX - layRect.left - grabFrac * r.w);
      win.y = Math.max(0, Math.round(ev.clientY - layRect.top - 14));
      windowEl.style.width = `${win.w}px`;
      windowEl.style.height = `${win.h}px`;
      // Bezugspunkte neu setzen, damit die Drag-Formel nahtlos weiterläuft.
      startLeft = win.x - (ev.clientX - startX);
      startTop = win.y - (ev.clientY - startY);
      updateSnapDividers();
    }

    function onMove(ev) {
      if (!dragging && Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY) < 4) return;
      dragging = true;
      if (!released && (win.snap || win.maximized)) { releaseFromSnap(ev); released = true; }

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

      // Snap-Zone unter dem Cursor ermitteln + Vorschau anzeigen.
      showSnapPreview(zoneForPointer(ev.clientX, ev.clientY));
    }
    function onUp(ev) {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      const zone = dragging ? zoneForPointer(ev.clientX, ev.clientY) : null;
      showSnapPreview(null);
      if (zone) snapWindowTo(win.key, zone);
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
    // Manuelles Resizen löst das Fenster aus dem Snap-Layout (aktuelle
    // Geometrie bleibt als Ausgangspunkt erhalten).
    if (win.snap) { win.snap = null; updateSnapDividers(); }

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

// =================================================================
// SNAP-SYSTEM: Windows-artige Fensteranordnung
// -----------------------------------------------------------------
// - Fenster an eine Kante/Ecke ziehen -> rastet mit Animation ein:
//     oben mittig            = Maximieren
//     links/rechts           = halbe Breite (2 Fenster nebeneinander)
//     oben/unten (seitlich)  = halbe Höhe   (2 Fenster übereinander)
//     Ecken                  = Viertel (3er-/4er-Layouts)
// - Die Trennlinien merken sich globale Teilungsverhältnisse (splits.v/h).
//   Zieht man an einer Grenze, an der auf BEIDEN Seiten gesnappte Fenster
//   liegen, werden ALLE angrenzenden Fenster gemeinsam resized (wie Windows).
// - Snap-Assist: nach dem Einrasten zeigt jede noch freie Nachbar-Zone eine
//   Auswahl der übrigen Fenster - Klick snappt das Fenster dorthin.
// =================================================================

// Teilungen PRO SEGMENT (0..1): So kann z.B. bei 4 Fenstern die Grenze
// zwischen den beiden UNTEREN unabhängig von den oberen verschoben werden.
//   vt = vertikale Linie in der oberen Reihe    vb = in der unteren Reihe
//   hl = horizontale Linie in der linken Spalte hr = in der rechten Spalte
// Liegt ein Fenster über die volle Höhe/Breite an (left/right bzw. top/
// bottom), werden die betroffenen Segmente automatisch gekoppelt.
const splits = { vt: 0.5, vb: 0.5, hl: 0.5, hr: 0.5 };
let _snapPreviewEl = null;
let _dividerEls = [];
let _assistEls = [];

function _activeZones() {
  return state.windows.filter((w) => w.snap && !w.minimized).map((w) => w.snap);
}

// Effektive Linien: Vollhöhen-/Vollbreiten-Fenster erzwingen EINE Linie.
function _lines() {
  const zones = _activeZones();
  const fullH = zones.includes("left") || zones.includes("right");
  const fullW = zones.includes("top") || zones.includes("bottom");
  return {
    vt: splits.vt,
    vb: fullH ? splits.vt : splits.vb,
    hl: splits.hl,
    hr: fullW ? splits.hl : splits.hr,
  };
}

// Zonen, die links/rechts bzw. oben/unten der Trennlinie liegen.
const _LEFT_ZONES = ["left", "tl", "bl"];
const _RIGHT_ZONES = ["right", "tr", "br"];
const _TOP_ZONES = ["top", "tl", "tr"];
const _BOTTOM_ZONES = ["bottom", "bl", "br"];

// Ziel-Rechteck (px) einer Zone, abhängig von den aktuellen Teilungen.
function snapRect(zone) {
  const lay = layer();
  if (!lay) return null;
  const W = lay.clientWidth, H = lay.clientHeight;
  const L = _lines();
  const vt = Math.round(W * L.vt), vb = Math.round(W * L.vb);
  const hl = Math.round(H * L.hl), hr = Math.round(H * L.hr);
  switch (zone) {
    case "max":    return { x: 0, y: 0, w: W, h: H };
    case "left":   return { x: 0, y: 0, w: vt, h: H };
    case "right":  return { x: vt, y: 0, w: W - vt, h: H };
    case "top":    return { x: 0, y: 0, w: W, h: hl };
    case "bottom": return { x: 0, y: hl, w: W, h: H - hl };
    case "tl":     return { x: 0, y: 0, w: vt, h: hl };
    case "tr":     return { x: vt, y: 0, w: W - vt, h: hr };
    case "bl":     return { x: 0, y: hl, w: vb, h: H - hl };
    case "br":     return { x: vb, y: hr, w: W - vb, h: H - hr };
  }
  return null;
}

// Welche Zone liegt unter dem Mauszeiger? (null = keine)
function zoneForPointer(cx, cy) {
  const lay = layer();
  if (!lay) return null;
  const r = lay.getBoundingClientRect();
  const x = cx - r.left, y = cy - r.top, W = r.width, H = r.height;
  const T = 14;    // Kanten-Nähe in px
  const C = 150;   // Ecken-Bereich entlang der Kante in px
  const nearL = x < T, nearR = x > W - T, nearT = y < T, nearB = y > H - T;
  if (!nearL && !nearR && !nearT && !nearB) return null;
  if (nearT) {
    if (x < C) return "tl";
    if (x > W - C) return "tr";
    if (x > W / 3 && x < (2 * W) / 3) return "max";   // oben MITTIG = maximieren
    return "top";                                       // oben seitlich = obere Hälfte
  }
  if (nearB) {
    if (x < C) return "bl";
    if (x > W - C) return "br";
    return "bottom";
  }
  if (nearL) return y < C ? "tl" : (y > H - C ? "bl" : "left");
  return y < C ? "tr" : (y > H - C ? "br" : "right");
}

// Halbtransparente Vorschau der Ziel-Zone während des Ziehens.
function showSnapPreview(zone) {
  const lay = layer();
  if (!lay) return;
  if (!zone) {
    if (_snapPreviewEl) { _snapPreviewEl.style.opacity = "0"; }
    return;
  }
  const rect = snapRect(zone);
  if (!rect) return;
  if (!_snapPreviewEl) {
    _snapPreviewEl = document.createElement("div");
    _snapPreviewEl.className = "snap-preview";
    lay.appendChild(_snapPreviewEl);
  }
  _snapPreviewEl.style.opacity = "1";
  _snapPreviewEl.style.left = `${rect.x}px`;
  _snapPreviewEl.style.top = `${rect.y}px`;
  _snapPreviewEl.style.width = `${rect.w}px`;
  _snapPreviewEl.style.height = `${rect.h}px`;
}

// Geometrie anwenden (optional mit sanfter Animation über win-animate-geo).
function applySnapGeometry(win, rect, animate = true) {
  if (!win._el || !rect) return;
  if (animate) {
    win._el.classList.add("win-animate-geo");
    setTimeout(() => { if (win._el) win._el.classList.remove("win-animate-geo"); }, 230);
  }
  win.x = rect.x; win.y = rect.y; win.w = rect.w; win.h = rect.h;
  win._el.style.left = `${rect.x}px`;
  win._el.style.top = `${rect.y}px`;
  win._el.style.width = `${rect.w}px`;
  win._el.style.height = `${rect.h}px`;
}

// Fenster in eine Zone snappen.
export function snapWindowTo(key, zone, { assist = true, animate = true } = {}) {
  const win = state.windows.find((w) => w.key === key);
  if (!win || !win._el) return;
  const rect = snapRect(zone);
  if (!rect) return;
  // Sonderfall "max" (Fenster oben mittig hochgezogen): Das ist ein ECHTES
  // Maximieren, kein Snap. Früher wurde hier win.snap="max" gesetzt und
  // win.maximized blieb false - ein Klick auf den Maximize-Button hat das
  // Fenster dann erneut "maximiert" (keine sichtbare Änderung) statt es
  // wiederherzustellen. Jetzt läuft der Fall über toggleMaximize, damit
  // der Button das Fenster korrekt auf die alte Größe zurückbringt.
  if (zone === "max") {
    win.snap = null;
    win.minimized = false;
    win._el.style.display = "flex";
    if (!win.maximized) toggleMaximize(key);
    else { focusWindow(key); notifyChanged(); }
    updateSnapDividers();
    return;
  }
  // Vor dem ERSTEN Snap die normale Geometrie merken (fürs Herausziehen).
  if (!win.snap && !win.maximized) {
    win._snapRestore = { x: win.x, y: win.y, w: win.w, h: win.h };
  }
  win.maximized = false;
  win.snap = zone;
  win.minimized = false;
  // Rastet ein Fenster über die volle Höhe/Breite ein, die getrennten
  // Segmente wieder auf EINE Linie zusammenführen (sonst gäbe es Lücken).
  if (zone === "left" || zone === "right") splits.vb = splits.vt;
  if (zone === "top" || zone === "bottom") splits.hr = splits.hl;
  win._el.style.display = "flex";
  applySnapGeometry(win, rect, animate);
  focusWindow(key);
  updateSnapDividers();
  notifyChanged();
  if (assist) showSnapAssist(win, zone);
}

// Alle gesnappten Fenster an die aktuellen Teilungen/Layer-Größe anpassen.
function relayoutSnapped(animate = false) {
  for (const win of state.windows) {
    if (win.snap && !win.minimized) {
      applySnapGeometry(win, snapRect(win.snap), animate);
    }
  }
}

// -----------------------------------------------------------------
// Gemeinsame Grenzen: unsichtbare Griffe auf den Trennlinien. Ziehen
// verschiebt die Teilung und resized ALLE angrenzenden Fenster live.
// -----------------------------------------------------------------
function updateSnapDividers() {
  const lay = layer();
  if (!lay) return;
  // Alte Griffe entfernen und aus dem aktuellen Layout neu aufbauen.
  _dividerEls.forEach((el) => { try { el.remove(); } catch {} });
  _dividerEls = [];

  const zones = _activeZones();
  const has = (z) => zones.includes(z);
  const W = lay.clientWidth, H = lay.clientHeight;
  const L = _lines();
  const fullH = has("left") || has("right");   // Vollhöhen-Fenster vorhanden?
  const fullW = has("top") || has("bottom");   // Vollbreiten-Fenster vorhanden?

  // Wer grenzt wo an die vertikale Linie?
  const leftTop = has("tl") || has("left"),  rightTop = has("tr") || has("right");
  const leftBot = has("bl") || has("left"),  rightBot = has("br") || has("right");
  // ... und an die horizontale Linie?
  const topLeft = has("tl") || has("top"),   botLeft = has("bl") || has("bottom");
  const topRight = has("tr") || has("top"),  botRight = has("br") || has("bottom");

  const segs = [];
  const hMid = H * ((L.hl + L.hr) / 2);   // Übergabepunkt zwischen oberem/unterem v-Segment
  const vMid = W * ((L.vt + L.vb) / 2);   // ... zwischen linkem/rechtem h-Segment

  // Vertikale Segmente. keys = welche Teilungen dieses Segment steuert:
  // grenzt ein Vollhöhen-Fenster an, bewegt der Griff BEIDE (vt+vb) -> ganze
  // Linie; sonst nur das eigene Segment (z.B. nur die Grenze der unteren zwei).
  const vBoth = leftTop && rightTop && leftBot && rightBot;
  if (leftTop && rightTop) {
    segs.push({ axis: "v", keys: fullH ? ["vt", "vb"] : ["vt"],
                x: W * L.vt, y0: 0, y1: vBoth ? hMid : H });
  }
  if (leftBot && rightBot) {
    segs.push({ axis: "v", keys: fullH ? ["vt", "vb"] : ["vb"],
                x: W * L.vb, y0: vBoth ? hMid : 0, y1: H });
  }
  // Horizontale Segmente (analog, gekoppelt bei Vollbreiten-Fenstern).
  const hBoth = topLeft && botLeft && topRight && botRight;
  if (topLeft && botLeft) {
    segs.push({ axis: "h", keys: fullW ? ["hl", "hr"] : ["hl"],
                y: H * L.hl, x0: 0, x1: hBoth ? vMid : W });
  }
  if (topRight && botRight) {
    segs.push({ axis: "h", keys: fullW ? ["hl", "hr"] : ["hr"],
                y: H * L.hr, x0: hBoth ? vMid : 0, x1: W });
  }

  for (const seg of segs) {
    const el = document.createElement("div");
    el.className = "snap-divider " + (seg.axis === "v" ? "snap-divider-v" : "snap-divider-h");
    if (seg.axis === "v") {
      el.style.left = `${Math.round(seg.x) - 4}px`;
      el.style.top = `${Math.round(seg.y0)}px`;
      el.style.width = "8px";
      el.style.height = `${Math.round(seg.y1 - seg.y0)}px`;
    } else {
      el.style.top = `${Math.round(seg.y) - 4}px`;
      el.style.left = `${Math.round(seg.x0)}px`;
      el.style.height = "8px";
      el.style.width = `${Math.round(seg.x1 - seg.x0)}px`;
    }
    _makeDividerDraggable(el, seg.axis, seg.keys);
    lay.appendChild(el);
    _dividerEls.push(el);
  }
}

function _makeDividerDraggable(el, axis, keys) {
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  el.addEventListener("mousedown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    // LINKSKLICK: die ganze Trennlinie verschieben -> ALLE angrenzenden Fenster
    //   werden gemeinsam angepasst (Windows-Standard).
    // RECHTSKLICK: nur dieses Segment -> NUR die beiden Fenster an dieser Grenze.
    const rightClick = e.button === 2;
    const affected = rightClick ? keys
      : (axis === "v" ? ["vt", "vb"] : ["hl", "hr"]);
    el.classList.add("dragging");
    document.body.style.userSelect = "none";
    document.body.style.cursor = axis === "v" ? "ew-resize" : "ns-resize";

    function onMove(ev) {
      const lay = layer();
      if (!lay) return;
      const r = lay.getBoundingClientRect();
      const frac = axis === "v"
        ? Math.min(0.85, Math.max(0.15, (ev.clientX - r.left) / r.width))
        : Math.min(0.85, Math.max(0.15, (ev.clientY - r.top) / r.height));
      for (const k of affected) splits[k] = frac;
      relayoutSnapped(false);   // live, ohne Animation (folgt dem Cursor)
      // Griffe NICHT neu aufbauen (das würde diesen Drag abbrechen), nur
      // den aktiven Griff mitführen.
      if (axis === "v") el.style.left = `${Math.round(r.width * frac) - 4}px`;
      else el.style.top = `${Math.round(r.height * frac) - 4}px`;
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      el.classList.remove("dragging");
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      updateSnapDividers();   // Segmente/Längen jetzt sauber neu berechnen
      notifyChanged();
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

// -----------------------------------------------------------------
// Snap-Assist: freie Nachbar-Zonen zeigen eine Auswahl der übrigen
// Fenster (wie Windows) - Klick snappt das gewählte Fenster dorthin.
// -----------------------------------------------------------------
const _COMPLEMENTS = {
  left: ["right"], right: ["left"], top: ["bottom"], bottom: ["top"],
  tl: ["tr", "bl", "br"], tr: ["tl", "br", "bl"],
  bl: ["br", "tl", "tr"], br: ["bl", "tr", "tl"], max: [],
};

export function clearSnapAssist() {
  _assistEls.forEach((el) => { try { el.remove(); } catch {} });
  _assistEls = [];
  document.removeEventListener("keydown", _assistEsc, true);
  document.removeEventListener("mousedown", _assistOutside, true);
  window.removeEventListener("blur", clearSnapAssist);
}
function _assistEsc(e) { if (e.key === "Escape") clearSnapAssist(); }

// Klick IRGENDWO außerhalb der Vorschlags-Flächen schließt ALLE Vorschläge.
// Vorher blieben sie stehen, wenn man z.B. in ein Fenster, auf den Desktop
// oder in die Taskleiste geklickt hat. Capture-Phase, damit es auch greift,
// wenn das Ziel den Klick selbst stoppt.
function _assistOutside(e) {
  if (!_assistEls.length) return;
  // Klicks INNERHALB einer Vorschlagsfläche behandelt showSnapAssist selbst
  // (Kachel = Fenster einrasten, freie Fläche = nur diese Zone schließen).
  for (const el of _assistEls) {
    if (el === e.target || el.contains(e.target)) return;
  }
  clearSnapAssist();
}

function showSnapAssist(sourceWin, zone) {
  clearSnapAssist();
  const lay = layer();
  if (!lay) return;
  const occupied = new Set(
    state.windows.filter((w) => w.snap && w.key !== sourceWin.key).map((w) => w.snap)
  );
  const empty = (_COMPLEMENTS[zone] || []).filter((z) => !occupied.has(z));
  // Kandidaten: alle anderen, noch nicht gesnappten Fenster.
  let candidates = state.windows.filter((w) => w.key !== sourceWin.key && !w.snap);
  if (!empty.length || !candidates.length) return;

  for (const z of empty) {
    const rect = snapRect(z);
    if (!rect) continue;
    const overlay = document.createElement("div");
    overlay.className = "snap-assist";
    overlay.style.left = `${rect.x + 8}px`;
    overlay.style.top = `${rect.y + 8}px`;
    overlay.style.width = `${rect.w - 16}px`;
    overlay.style.height = `${rect.h - 16}px`;
    overlay.dataset.zone = z;
    lay.appendChild(overlay);
    _assistEls.push(overlay);
  }
  if (!_assistEls.length) return;

  function renderTiles() {
    candidates = candidates.filter((w) => state.windows.includes(w) && !w.snap);
    for (const overlay of [..._assistEls]) {
      overlay.innerHTML = "";
      if (!candidates.length) { clearSnapAssist(); return; }
      for (const cand of candidates) {
        const tile = document.createElement("button");
        tile.className = "snap-assist-tile";
        tile.innerHTML = `${cand.clientColor ? `<span class="client-dot" style="background:${esc(cand.clientColor)}"></span>` : ""}<span>${esc(cand.title)}</span>`;
        tile.addEventListener("click", (e) => {
          e.stopPropagation();
          const targetZone = overlay.dataset.zone;
          overlay.remove();
          _assistEls = _assistEls.filter((x) => x !== overlay);
          snapWindowTo(cand.key, targetZone, { assist: false });
          if (_assistEls.length) renderTiles(); else clearSnapAssist();
        });
        overlay.appendChild(tile);
      }
      // Klick auf die freie Fläche = Auswahl abbrechen (nur diese Zone).
      overlay.addEventListener("mousedown", (e) => {
        if (e.target === overlay) {
          overlay.remove();
          _assistEls = _assistEls.filter((x) => x !== overlay);
          if (!_assistEls.length) clearSnapAssist();
        }
      });
    }
  }
  renderTiles();
  document.addEventListener("keydown", _assistEsc, true);
  document.addEventListener("mousedown", _assistOutside, true);
  // Verlässt der Fokus das Fenster (Tab-Wechsel), ebenfalls aufräumen.
  window.addEventListener("blur", clearSnapAssist);
}

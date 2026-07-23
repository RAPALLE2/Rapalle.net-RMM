// taskbar.js
// ----------
// Rendert die untere Taskleiste: für jedes offene Fenster einen Button.
// - Farbpunkt = Client-Identität (jedes Gerät hat seine eigene Farbe)
// - Klick auf einen Button: Fenster nach vorne holen bzw. wiederherstellen,
//   ist es schon aktiv -> minimieren (ein Klick genügt, kein Hover-Menü).
// - Bei VIELEN offenen Fenstern schaltet die Leiste automatisch in den
//   Icon-Modus: nur noch das Symbol, der Titel erscheint beim Überfahren.

import { state } from "./state.js";
import { focusWindow, toggleMinimize, minimizeAll } from "./windowmanager.js";
import { esc } from "./utils.js";

// Symbol je App. Die Schlüssel entsprechen den appId-Werten aus openWindow()
// und decken den kompletten Startmenü-Katalog ab, damit in der Leiste nie
// das Ersatzzeichen "◻" auftaucht.
export const APP_ICON = {
  // Client-Werkzeuge
  terminal: "⌨️",
  explorer: "📁",
  vnc: "🖥️",
  guacamole: "🖥️",
  taskmanager: "📋",
  panelpart: "🧩",
  fleetwidget: "📈",
  // Verwaltung
  clients: "📊",
  manage: "🏢",
  "edit-client": "✏️",
  "add-client": "➕",
  permissions: "🔐",
  settings: "⚙️",
  profile: "👤",
  audit: "📝",
  // Automatisierung & Skripte
  scripts: "📜",
  bulk: "⚡",
  automation: "🔁",
  // Netzwerk
  network: "📡",
  speedtest: "🚀",
  portscan: "🎯",
  "relay-manager": "🔌",
  webbrowser: "🌐",
  webapp: "🌐",
  // Kommunikation & Medien
  chat: "💬",
  tickets: "🎫",
  aichat: "🤖",
  audioplayer: "🎵",
  recordings: "🎥",
  notifycenter: "🔔",
  // Spiele
  gaminghub: "🎮",
  towerdefense: "🏰",
};

export const appIcon = (appId) => APP_ICON[appId] || "◻";

// Ab so vielen Fenstern werden nur noch Symbole gezeigt. Zusätzlich wird
// umgeschaltet, wenn die Buttons rechnerisch breiter wären als die Leiste -
// so bleibt die Leiste auch auf schmalen Bildschirmen bedienbar.
const ICON_ONLY_FROM = 7;
const MIN_LABEL_WIDTH = 108;   // px, die ein Button mit Text mindestens braucht

export function renderTaskbar() {
  const container = document.getElementById("taskbar-windows");
  container.innerHTML = "";

  const activeKey = state.focusOrder[state.focusOrder.length - 1];
  const count = state.windows.length;
  const avail = container.clientWidth || 0;
  const iconOnly = count >= ICON_ONLY_FROM
    || (avail > 0 && count * MIN_LABEL_WIDTH > avail);
  container.classList.toggle("icons-only", iconOnly);

  state.windows.forEach((win) => {
    const btn = document.createElement("button");
    const isActive = win.key === activeKey && !win.minimized;
    btn.className = "taskbar-window-btn"
      + (isActive ? " active" : "")
      + (win.minimized ? " minimized" : "");

    const dot = win.clientColor
      ? `<span class="client-dot" style="background:${esc(win.clientColor)}"></span>`
      : "";
    const icon = appIcon(win.appId);

    // Im Icon-Modus bleibt der Titel als Tooltip erhalten (nativ + eigener
    // Hover-Tooltip weiter unten, weil der native erst nach ~1 s erscheint).
    btn.innerHTML = iconOnly
      ? `${dot}<span class="tb-icon">${icon}</span>`
      : `${dot}<span class="tb-icon">${icon}</span><span class="tb-label">${esc(win.title)}</span>`;
    btn.title = win.title;
    btn.setAttribute("aria-label", win.title);

    if (iconOnly) bindTaskbarTip(btn, win.title);

    btn.addEventListener("click", () => {
      if (isActive) {
        toggleMinimize(win.key); // schon vorne -> wegklappen
      } else {
        if (win.minimized) toggleMinimize(win.key);
        focusWindow(win.key);
      }
      renderTaskbar();
    });

    container.appendChild(btn);
  });
}

// ------------------------------------------------------------------
// Eigener Hover-Tooltip: erscheint sofort und wird ÜBER der Leiste
// angezeigt (der native title-Tooltip käme verzögert und teils außerhalb).
// ------------------------------------------------------------------
let tipEl = null;
function taskbarTip() {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "taskbar-tip";
    tipEl.style.cssText = `position:fixed;z-index:99999;pointer-events:none;display:none;
      background:var(--panel-2,#1b2740);color:var(--text,#e6edf7);
      border:1px solid var(--border,#2b3a56);border-radius:7px;
      padding:4px 9px;font-size:12px;white-space:nowrap;
      box-shadow:0 6px 18px rgba(0,0,0,.4)`;
    document.body.appendChild(tipEl);
  }
  return tipEl;
}
function bindTaskbarTip(btn, text) {
  btn.addEventListener("mouseenter", () => {
    const tip = taskbarTip();
    tip.textContent = text;
    tip.style.display = "block";
    const r = btn.getBoundingClientRect();
    // Erst einblenden, dann messen (sonst ist die Breite 0) und mittig
    // über dem Button platzieren, ohne den Bildschirmrand zu verlassen.
    const w = tip.offsetWidth;
    let left = r.left + r.width / 2 - w / 2;
    left = Math.max(6, Math.min(left, window.innerWidth - w - 6));
    tip.style.left = left + "px";
    tip.style.top = (r.top - tip.offsetHeight - 7) + "px";
  });
  btn.addEventListener("mouseleave", hideTaskbarTip);
  btn.addEventListener("click", hideTaskbarTip);
}
function hideTaskbarTip() {
  if (tipEl) tipEl.style.display = "none";
}

export function initTaskbar() {
  document.getElementById("taskbar-minimize-all-btn").addEventListener("click", () => {
    minimizeAll();
    renderTaskbar();
  });

  // Fensterbreite ändert sich -> ggf. zwischen Text- und Icon-Modus wechseln.
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderTaskbar, 120);
  });

  // Uhr aktualisieren
  const clock = document.getElementById("taskbar-clock");
  function tick() {
    const now = new Date();
    clock.textContent =
      now.toLocaleDateString("de-DE") + " · " +
      now.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  }
  tick();
  setInterval(tick, 30000);
}

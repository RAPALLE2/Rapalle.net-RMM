// taskbar.js
// ----------
// Rendert die untere Taskleiste: für jedes offene Fenster einen Button.
// - Farbpunkt = Client-Identität (jedes Gerät hat seine eigene Farbe)
// - Klick auf einen Button: Fenster nach vorne holen bzw. wiederherstellen,
//   ist es schon aktiv -> minimieren (ein Klick genügt, kein Hover-Menü).

import { state } from "./state.js";
import { focusWindow, toggleMinimize, minimizeAll } from "./windowmanager.js";
import { esc } from "./utils.js";

// Icon je nach App-Typ (Service-Typ farblich/visuell unterscheidbar)
const APP_ICON = {
  terminal: "⌨️",
  explorer: "📁",
  vnc: "🖥️",
  taskmanager: "📋",
  settings: "⚙️",
  network: "📡",
  audit: "📝",
  "edit-client": "✏️",
  "add-client": "➕",
};

export function renderTaskbar() {
  const container = document.getElementById("taskbar-windows");
  container.innerHTML = "";

  const activeKey = state.focusOrder[state.focusOrder.length - 1];

  state.windows.forEach((win) => {
    const btn = document.createElement("button");
    const isActive = win.key === activeKey && !win.minimized;
    btn.className = "taskbar-window-btn" + (isActive ? " active" : "");

    const dot = win.clientColor
      ? `<span class="client-dot" style="background:${esc(win.clientColor)}"></span>`
      : "";

    btn.innerHTML = `${dot} ${APP_ICON[win.appId] || "◻"} ${esc(win.title)}`;

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

export function initTaskbar() {
  document.getElementById("taskbar-minimize-all-btn").addEventListener("click", () => {
    minimizeAll();
    renderTaskbar();
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

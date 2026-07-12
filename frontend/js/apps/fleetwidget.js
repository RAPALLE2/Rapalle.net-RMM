// apps/fleetwidget.js
// -------------------
// Zeigt ein aus dem Flotten-Dashboard herausgelöstes Widget als eigenes
// Fenster. Aktualisiert sich live bei neuen Metriken. Persistiert über das
// Fenster-System (props.widget), ist also nach Reload wieder da.

import { registerCleanup } from "../windowmanager.js";
import { dashboardSocket } from "../socket.js";
import { renderWidgetBody, widgetTitle, pushWidgetHistory } from "../dashwidgets.js";

export function renderFleetWidget(body, win) {
  const wdg = win.props.widget;
  if (!wdg) { body.innerHTML = `<div style="padding:20px;color:var(--subtext)">Kein Widget.</div>`; return; }

  body.innerHTML = `<div class="fleetwidget-wrap"><div class="dash-w-body" id="fw-body-${win.key}"></div></div>`;
  const target = body.querySelector(`#fw-body-${win.key}`);

  function draw() {
    if (wdg.kind === "line") pushWidgetHistory(wdg);
    renderWidgetBody(target, wdg);
  }
  draw();

  // Bei jedem Metrik-Update der Flotte neu zeichnen.
  function onMetrics() { draw(); }
  dashboardSocket.on("client:metrics", onMetrics);
  // Fallback-Timer (falls keine Live-Events), z.B. für reine Verteilungen.
  const timer = setInterval(draw, 5000);

  registerCleanup(win.key, () => {
    dashboardSocket.off("client:metrics", onMetrics);
    clearInterval(timer);
  });
}

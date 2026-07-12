// apps/panelpart.js
// -----------------
// Rendert einen aus dem Dashboard herausgelösten Baustein (Status, Aktionen,
// Websites oder einen Übersicht-Ordner mit Metrics/Notes/Disk) als eigenes
// Fenster. Der Client bleibt dabei ganz normal im Dashboard/der Seitenleiste;
// dieses Fenster ist nur eine zusätzliche, dauerhaft sichtbare Ansicht.
//
// Wird über die Fenster-Persistenz automatisch bei Reload wieder geöffnet
// (props: clientId, part, subs, activeSub).

import { findClient } from "../state.js";
import { esc } from "../utils.js";
import { registerCleanup } from "../windowmanager.js";
import { dashboardSocket } from "../socket.js";
import {
  renderStatusPart, renderActionsPart, renderWebsitesPart,
  renderOverviewSub, OVERVIEW_SUBS,
} from "../panel.js";

export function renderPanelPart(body, win) {
  const { clientId, part } = win.props;
  let activeSub = win.props.activeSub || (win.props.subs && win.props.subs[0]) || "metrics";
  const subs = win.props.subs || null;

  function draw() {
    const client = findClient(clientId);
    if (!client) {
      body.innerHTML = `<div style="padding:20px;color:var(--subtext)">Client nicht gefunden.</div>`;
      return;
    }

    // Ganzer Client als Fenster: komplette (anpassbare) Detailansicht einbetten.
    if (part === "client") {
      body.innerHTML = `
        <div style="padding:12px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <strong style="font-size:14px">${esc(client.hostname)}</strong>
            <span id="pp-toolbar-${win.key}"></span>
          </div>
          <div id="pp-layout-${win.key}"></div>
        </div>`;
      import("../dashlayout.js").then(({ renderClientLayout }) => {
        renderClientLayout(
          body.querySelector(`#pp-layout-${win.key}`),
          body.querySelector(`#pp-toolbar-${win.key}`),
          client
        );
      });
      return;
    }

    body.innerHTML = `
      <div class="panelpart-wrap">
        ${part === "folder" && subs ? `
          <div class="tab-bar panelpart-tabs">
            ${subs.map((s) => `<button class="tab-btn ${s === activeSub ? "active" : ""}" data-sub="${esc(s)}">${OVERVIEW_SUBS[s] ? OVERVIEW_SUBS[s]() : esc(s)}</button>`).join("")}
          </div>` : ""}
        <div class="panelpart-body ${part === "actions" || part === "websites" ? "actions-panel" : ""} ${part === "folder" ? "overview-content" : ""}"></div>
      </div>
    `;

    const target = body.querySelector(".panelpart-body");
    if (part === "status") renderStatusPart(target, client);
    else if (part === "actions") renderActionsPart(target, client);
    else if (part === "websites") renderWebsitesPart(target, client);
    else if (part === "cmetric") {
      import("../clientmetrics.js").then(({ renderClientMetric }) =>
        renderClientMetric(target, client, { id: win.key, metric: win.props.metric, kind: win.props.kind }));
    }
    else if (part === "folder") {
      const rerenderFolder = () => renderOverviewSub(target, client, activeSub, rerenderFolder);
      rerenderFolder();
    }

    body.querySelectorAll("[data-sub]").forEach((b) =>
      b.addEventListener("click", () => {
        activeSub = b.dataset.sub;
        win.props.activeSub = activeSub;   // in die Fenster-Persistenz übernehmen
        draw();
      })
    );
  }

  draw();

  // Live aktualisieren, wenn neue Metriken für DIESEN Client eintreffen
  // (Status-/Übersicht-Fenster sollen mitlaufen wie das Dashboard).
  function onMetrics(p) {
    if (!p || p.id !== clientId) return;
    // Nur bei Ansichten mit Live-Daten neu zeichnen.
    if (part === "status" || part === "folder" || part === "cmetric") draw();
  }
  dashboardSocket.on("client:metrics", onMetrics);

  registerCleanup(win.key, () => {
    dashboardSocket.off("client:metrics", onMetrics);
  });
}

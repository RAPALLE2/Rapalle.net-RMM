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
  // NEU: komplette Panel-Definitionen der Ordner-Kinder (inkl. metric/kind bei
  // selbst hinzugefügten Metrik-Widgets). Behebt den Bug, dass herausgelöste
  // Ordner-Widgets immer generisch als "cmetric" gerendert wurden. "subs"
  // bleibt als Fallback für alte, bereits persistierte Fenster erhalten.
  const panels = Array.isArray(win.props.panels) && win.props.panels.length ? win.props.panels : null;
  let activePanelId = win.props.activePanelId
    || (panels && panels[0] ? panels[0].id : null);

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
        ${part === "folder" && panels ? `
          <div class="tab-bar panelpart-tabs">
            ${panels.map((p) => `<button class="tab-btn ${p.id === activePanelId ? "active" : ""}" data-panelid="${esc(p.id)}">${esc(p._label || (OVERVIEW_SUBS[p.type] ? OVERVIEW_SUBS[p.type]() : p.type))}</button>`).join("")}
          </div>` : ""}
        ${part === "folder" && !panels && subs ? `
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
      if (panels) {
        // Aktives Kind anhand seiner KOMPLETTEN Definition rendern.
        const p = panels.find((x) => x.id === activePanelId) || panels[0];
        if (p && p.type === "cmetric") {
          // Selbst hinzugefügtes Metrik-Widget: exakt mit metric + kind rendern.
          import("../clientmetrics.js").then(({ renderClientMetric }) =>
            renderClientMetric(target, client, { id: `${win.key}-${p.id}`, metric: p.metric, kind: p.kind }));
        } else if (p) {
          const rerenderFolder = () => renderOverviewSub(target, client, p.type, rerenderFolder);
          rerenderFolder();
        }
      } else {
        // Fallback: altes Fenster ohne panels-Prop (nur Typen bekannt).
        const rerenderFolder = () => renderOverviewSub(target, client, activeSub, rerenderFolder);
        rerenderFolder();
      }
    }

    body.querySelectorAll("[data-panelid]").forEach((b) =>
      b.addEventListener("click", () => {
        activePanelId = b.dataset.panelid;
        win.props.activePanelId = activePanelId;   // in die Fenster-Persistenz übernehmen
        draw();
      })
    );
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
    // Nur die Werte/SVGs im bestehenden Fenster ersetzen - NICHT das ganze
    // Fenster neu aufbauen (Tabs/Scroll/Hover bleiben erhalten).
    if (part !== "status" && part !== "folder" && part !== "cmetric") return;
    const client = findClient(clientId);
    const target = body.querySelector(".panelpart-body");
    if (!client || !target) { draw(); return; }
    if (part === "status") { target.innerHTML = ""; renderStatusPart(target, client); return; }
    if (part === "cmetric") {
      import("../clientmetrics.js").then(({ renderClientMetric }) =>
        renderClientMetric(target, client, { id: win.key, metric: win.props.metric, kind: win.props.kind }));
      return;
    }
    // folder: nur den aktiven Inhalt ersetzen (Tab-Leiste bleibt stehen).
    // Notes/Text-Tabs und Bereiche mit Eingabe-Fokus werden NICHT angefasst.
    const liveTypes = new Set(["cmetric", "status", "metrics", "disk"]);
    const focused = target.contains(document.activeElement) &&
      /^(TEXTAREA|INPUT|SELECT)$/.test(document.activeElement.tagName);
    if (focused) return;
    if (panels) {
      const p2 = panels.find((x) => x.id === activePanelId) || panels[0];
      if (p2 && !liveTypes.has(p2.type)) return;
      if (p2 && p2.type === "cmetric") {
        import("../clientmetrics.js").then(({ renderClientMetric }) =>
          renderClientMetric(target, client, { id: `${win.key}-${p2.id}`, metric: p2.metric, kind: p2.kind }));
      } else if (p2) {
        const rr = () => renderOverviewSub(target, client, p2.type, rr);
        rr();
      }
    } else {
      if (!liveTypes.has(activeSub)) return;
      const rr = () => renderOverviewSub(target, client, activeSub, rr);
      rr();
    }
  }
  dashboardSocket.on("client:metrics", onMetrics);

  registerCleanup(win.key, () => {
    dashboardSocket.off("client:metrics", onMetrics);
  });
}

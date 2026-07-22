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
  renderOverviewSub, OVERVIEW_SUBS, renderChildrenPart,
} from "../panel.js";
import { warrantyInfo } from "../dashwidgets.js";

// Herausgelöstes Garantie-Panel (nur Anzeige - geändert wird das Datum im
// Dashboard-Bearbeiten-Modus bzw. unter "Client bearbeiten").
function renderWarrantyView(target, client) {
  const info = warrantyInfo(client.warranty_until);
  target.innerHTML = `
    <div style="padding:14px;display:flex;flex-direction:column;gap:6px">
      <div style="font-size:26px;font-weight:800;color:${info.color};line-height:1.15">${esc(info.text)}</div>
      <div style="font-size:13px;color:var(--subtext)">${esc(info.sub)}</div>
    </div>`;
}

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

  // -----------------------------------------------------------------
  // BUGFIX: Ordner-Kinder vom Typ "actions", "websites" oder "status"
  // wurden im herausgelösten Fenster IMMER als Metrics gerendert, weil
  // renderOverviewSub nur metrics/notes/disk kennt und alles Unbekannte
  // auf Metrics zurückfällt. Dieser Router leitet jeden Kind-Typ an den
  // korrekten Renderer weiter (identisch zur Client-Ansicht).
  // -----------------------------------------------------------------
  function renderFolderChild(target, client, type) {
    target.classList.toggle("actions-panel", type === "actions" || type === "websites");
    if (type === "actions")       return renderActionsPart(target, client);
    if (type === "websites")      return renderWebsitesPart(target, client);
    if (type === "status")        { target.innerHTML = ""; return renderStatusPart(target, client); }
    if (type === "warranty")      { target.innerHTML = ""; return renderWarrantyView(target, client); }
    if (type === "children")      { target.innerHTML = ""; return renderChildrenPart(target, client); }
    const rr = () => renderOverviewSub(target, client, type, rr);
    rr();
  }

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
        <div class="panelpart-body ${part === "actions" || part === "websites" || part === "children" ? "actions-panel" : ""} ${part === "folder" ? "overview-content" : ""}"></div>
      </div>
    `;

    const target = body.querySelector(".panelpart-body");
    if (part === "status") renderStatusPart(target, client);
    else if (part === "actions") renderActionsPart(target, client);
    else if (part === "websites") renderWebsitesPart(target, client);
    else if (part === "warranty") renderWarrantyView(target, client);
    else if (part === "children") renderChildrenPart(target, client);
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
          renderFolderChild(target, client, p.type);
        }
      } else {
        // Fallback: altes Fenster ohne panels-Prop (nur Typen bekannt).
        renderFolderChild(target, client, activeSub);
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
        renderFolderChild(target, client, p2.type);   // status/metrics/disk korrekt
      }
    } else {
      if (!liveTypes.has(activeSub)) return;
      renderFolderChild(target, client, activeSub);
    }
  }
  dashboardSocket.on("client:metrics", onMetrics);

  registerCleanup(win.key, () => {
    dashboardSocket.off("client:metrics", onMetrics);
  });
}

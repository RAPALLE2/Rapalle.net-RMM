// apps/dashboard.js
// -----------------
// Das "echte" Dashboard: drei Donut-Diagramme (hohl in der Mitte) über alle
// verwalteten Clients:
//   1. Agenten online / offline
//   2. Betriebssystem-Verteilung
//   3. Agent-Versionen auf den Clients
// Beim Hovern über ein Segment (oder einen Legendeneintrag) erscheint ein
// Tooltip mit der genauen Anzahl und den betroffenen Clients.

import { state } from "../state.js";
import { esc } from "../utils.js";
import { favClientIds, favWebsiteList, favStarHtml, selectClientExternal,
         favPinList, openPin, pinIcon, removePin } from "../sidebar.js";
import { renderFleetWidgets, refreshFleetWidgets } from "../fleetdash.js";

// (Die Donut-/Tooltip-/groupBy-Logik der Flotten-Übersicht lebt jetzt in
//  fleetcharts.js und wird über die Widgets [kind "fleetdonut"] gerendert.)

export function renderDashboard(target) {
  // ALLE verwalteten Clients zählen - auch VMs/LXCs, die einem Host
  // untergeordnet sind (parent_client_id). Vorher fehlten diese in der Zahl.
  const clients = state.clients || [];

  target.innerHTML = `
    <div class="dash-wrap">
      <div class="dash-head">
        <h2 style="margin:0">Dashboard</h2>
        <span style="color:var(--subtext);font-size:13px">${clients.length} verwaltete Clients</span>
        <span style="flex:1"></span>
        <span id="fleet-widgets-toolbar"></span>
      </div>
      <div id="dash-favorites" style="display:none;margin-bottom:14px"></div>
      <div id="fleet-widgets" style="margin-bottom:18px"></div>
    </div>
  `;
  // Die frühere FESTE "Flotten-Übersicht" (drei Donuts) ist jetzt Teil der
  // Widgets oben (fleetdash.js migriert sie einmalig) - dort frei verschieb-,
  // editier-, lösch- und wieder einfügbar ("+ Widget" -> Gruppe
  // "Flotten-Übersicht"). Die Donut-Darstellung selbst lebt in fleetcharts.js.

  // Benutzerdefinierte, modulare Widgets (editierbar, verschiebbar, herauslösbar).
  const widgetHost = target.querySelector("#fleet-widgets");
  renderFleetWidgets(widgetHost, target.querySelector("#fleet-widgets-toolbar"));

  // Live-Refresh der Widget-Werte bei neuen Metriken.
  if (!target._widgetListener) {
    target._widgetListener = () => {
      if (document.body.contains(target)) refreshFleetWidgets(target.querySelector("#fleet-widgets"));
    };
    window.addEventListener("metrics-updated", target._widgetListener);
  }
  // Edit-Umschalter aus den Einstellungen -> Widget-Bereich komplett neu
  // aufbauen (Werkzeugleiste + Griffe erscheinen/verschwinden).
  if (!target._dasheditListener) {
    target._dasheditListener = () => {
      if (document.body.contains(target))
        renderFleetWidgets(target.querySelector("#fleet-widgets"), target.querySelector("#fleet-widgets-toolbar"));
    };
    window.addEventListener("dashedit-changed", target._dasheditListener);
  }

  // Dashboard-Favoriten: Clients UND Websites, die (per Stern) für das Dashboard
  // markiert sind. Reagiert live auf Änderungen am Favoriten-Zustand.
  function renderDashFavorites() {
    const box = target.querySelector("#dash-favorites");
    if (!box) return;
    const favClients = state.clients.filter((c) => favClientIds("d").includes(c.id));
    const favSites = favWebsiteList("d");
    const pins = favPinList("d");
    if (!favClients.length && !favSites.length && !pins.length) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "";

    const clientCards = favClients.map((c) => `
      <div class="panel" data-dashfav-client="${esc(c.id)}"
           style="display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer">
        <span style="color:${c.online ? "var(--online, #3ecf8e)" : "var(--subtext)"}">●</span>
        <span style="font-weight:600">${esc(c.hostname)}</span>
        ${favStarHtml("clients", c.id)}
      </div>`).join("");

    const siteCards = favSites.map((w) => {
      const meta = { name: w.name, url: w.url, clientId: w.clientId, clientHostname: w.clientHostname, open_mode: w.open_mode };
      return `
      <div class="panel" style="display:flex;align-items:center;gap:8px;padding:8px 12px">
        <a href="${esc(w.url || "")}" data-dash-web="${esc(w.id)}"
           title="${esc(w.url || "")}" style="display:flex;align-items:center;gap:8px;text-decoration:none;color:var(--text);cursor:pointer">
          <span>${w.open_mode === "internal" ? "🪟" : "🔗"}</span><span style="font-weight:600">${esc(w.name || w.url || "")}</span>
          <span style="font-size:11px;color:var(--subtext)">${esc(w.clientHostname || "")}</span>
        </a>
        ${favStarHtml("websites", w.id, meta)}
      </div>`;
    }).join("");

    // Selbst angeheftete Eintraege (Apps, App-Unterseiten, Client-Sitzungen, Links)
    const pinCards = pins.map((p) => `
      <div class="panel fav-pin" data-dash-pin="${esc(p.id)}"
           title="${esc(p.sub || p.label || "")}"
           style="display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer">
        <span class="fav-pin-ico">${esc(pinIcon(p))}</span>
        <span style="font-weight:600">${esc(p.label || "Favorit")}</span>
        <span class="fav-star both fav-pin-del" data-pin-del="${esc(p.id)}" title="Favorit entfernen">\u2605</span>
      </div>`).join("");

    box.innerHTML = `
      <h3 style="margin:0 0 8px;font-size:14px;color:var(--subtext)">\u2605 Angeheftet</h3>
      <div style="display:flex;flex-wrap:wrap;gap:8px">${pinCards}${clientCards}${siteCards}</div>`;

    box.querySelectorAll("[data-dash-pin]").forEach((el) =>
      el.addEventListener("click", (e) => {
        const del = e.target.closest("[data-pin-del]");
        if (del) { e.stopPropagation(); removePin(del.dataset.pinDel); renderDashFavorites(); return; }
        openPin(pins.find((x) => x.id === el.dataset.dashPin));
      })
    );
    // Website-Favoriten nach open_mode öffnen (intern/extern).
    box.querySelectorAll("[data-dash-web]").forEach((a) =>
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const w = favSites.find((x) => String(x.id) === a.dataset.dashWeb);
        if (w) import("./webbrowser.js").then((m) => m.openWebsiteEntry(w));
      })
    );

    box.querySelectorAll("[data-dashfav-client]").forEach((el) =>
      el.addEventListener("click", (e) => {
        if (e.target.closest(".fav-star")) return;
        const id = el.dataset.dashfavClient;
        try { selectClientExternal(id); } catch { state.selection = { type: "client", id }; }
      }));
  }
  renderDashFavorites();
  // Bei Favoriten-Änderungen (Stern woanders geklickt) neu aufbauen.
  if (!target._favListener) {
    target._favListener = () => { if (document.body.contains(target)) renderDashFavorites(); };
    window.addEventListener("favorites-changed", target._favListener);
  }

}

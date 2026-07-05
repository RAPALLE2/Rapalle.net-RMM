// sidebar.js
// ----------
// Baut die linke Seitenleiste als aufklappbaren Baum:
//   Tenant -> Location -> (Ordner ->) Client -> (VM/CT als Kind-Client)
//
// Die Daten kommen aus state.hierarchy (Tenants/Locations/Folders) und
// state.clients. Beim Klick auf einen Eintrag wird state.selection gesetzt und
// das Haupt-Panel neu gerendert (renderMainContent aus panel.js).

import { state } from "./state.js";
import { esc } from "./utils.js";
import { t } from "./i18n.js";

// Wird von app.js gesetzt, um bei Auswahl das Haupt-Panel zu aktualisieren
let onSelectCallback = null;
export function setOnSelect(fn) {
  onSelectCallback = fn;
}

// Merkt sich, welche Knoten aufgeklappt sind (damit ein Re-Render sie nicht zuklappt)
const expanded = new Set();

function toggleExpand(nodeId) {
  if (expanded.has(nodeId)) expanded.delete(nodeId);
  else expanded.add(nodeId);
  renderSidebar();
}

// Klappt den Pfad zu einem Client auf (Tenant + Location + evtl. Host-Client),
// damit der ausgewählte Client in der Sidebar immer sichtbar ist.
// Wird aufgerufen, wenn man z.B. aus der Geräteübersicht einen Client öffnet.
export function revealClient(clientId) {
  const client = state.clients.find((c) => c.id === clientId);
  if (!client) return;
  if (client.tenant_id) expanded.add(client.tenant_id);
  if (client.location_id) expanded.add(client.location_id);
  if (client.folder_id) expanded.add(client.folder_id);
  // Falls es eine VM/CT unter einem Host ist, auch den Host aufklappen
  if (client.parent_client_id) expanded.add(client.parent_client_id);
  renderSidebar();
}

function select(type, id) {
  state.selection = { type, id };
  renderSidebar();
  if (onSelectCallback) onSelectCallback();
}

// Baut das kleine "online"-Pünktchen für einen Client
function clientDot(client) {
  let color = "var(--subtext)"; // offline
  if (client.status_override === "maintenance") color = "var(--warn)";
  else if (client.online) color = "var(--online)";
  return `<span class="dot" style="background:${color}"></span>`;
}

// Rendert rekursiv die Ordnerstruktur + Clients innerhalb einer Location
function renderFolderChildren(locationId, parentFolderId) {
  const folders = state.hierarchy.folders.filter(
    (f) => f.location_id === locationId && f.parent_folder_id === parentFolderId
  );
  const clients = state.clients.filter(
    (c) => c.location_id === locationId && c.folder_id === parentFolderId && !c.parent_client_id
  );

  let html = "";

  // Unter-Ordner
  for (const folder of folders) {
    const isOpen = expanded.has(folder.id);
    html += `
      <div class="tree-node">
        <div class="tree-row" data-toggle="${folder.id}">
          <span>${isOpen ? "▾" : "▸"}</span> 📁 ${esc(folder.name)}
        </div>
        ${isOpen ? `<div class="tree-children">${renderFolderChildren(locationId, folder.id)}</div>` : ""}
      </div>
    `;
  }

  // Clients in diesem Ordner (bzw. direkt in der Location, wenn parentFolderId null ist)
  for (const client of clients) {
    html += renderClientNode(client);
  }

  return html;
}

// Rendert einen einzelnen Client + seine VM/CT-Kinder
function renderClientNode(client) {
  const children = state.clients.filter((c) => c.parent_client_id === client.id);
  const isOpen = expanded.has(client.id);
  const selected = state.selection?.type === "client" && state.selection.id === client.id;

  return `
    <div class="tree-node">
      <div class="tree-row ${selected ? "selected" : ""}" data-select-client="${client.id}">
        ${children.length ? `<span data-toggle="${client.id}">${isOpen ? "▾" : "▸"}</span>` : `<span style="width:10px;display:inline-block"></span>`}
        ${clientDot(client)} ${esc(client.hostname)}
      </div>
      ${isOpen && children.length ? `<div class="tree-children">${children.map(renderClientNode).join("")}</div>` : ""}
    </div>
  `;
}

export function renderSidebar() {
  const tree = document.getElementById("sidebar-tree");
  if (!tree) return;

  let html = "";

  for (const tenant of state.hierarchy.tenants) {
    const isOpen = expanded.has(tenant.id);
    const selected = state.selection?.type === "tenant" && state.selection.id === tenant.id;
    const locations = state.hierarchy.locations.filter((l) => l.tenant_id === tenant.id);

    html += `
      <div class="tree-node">
        <div class="tree-row ${selected ? "selected" : ""}">
          <span data-toggle="${tenant.id}">${isOpen ? "▾" : "▸"}</span>
          <span data-select-tenant="${tenant.id}" style="flex:1">
            <span class="dot" style="background:${esc(tenant.color)}"></span> ${esc(tenant.name)}
          </span>
        </div>
    `;

    if (isOpen) {
      html += `<div class="tree-children">`;
      for (const location of locations) {
        const locOpen = expanded.has(location.id);
        const locSelected = state.selection?.type === "location" && state.selection.id === location.id;
        html += `
          <div class="tree-node">
            <div class="tree-row ${locSelected ? "selected" : ""}">
              <span data-toggle="${location.id}">${locOpen ? "▾" : "▸"}</span>
              <span data-select-location="${location.id}" style="flex:1">📍 ${esc(location.name)}</span>
            </div>
            ${locOpen ? `<div class="tree-children">${renderFolderChildren(location.id, null)}</div>` : ""}
          </div>
        `;
      }
      html += `</div>`;
    }

    html += `</div>`;
  }

  // Clients, die noch keinem Tenant zugeordnet sind ("nicht zugeordnet")
  const unassigned = state.clients.filter((c) => !c.tenant_id && !c.parent_client_id);
  if (unassigned.length) {
    html += `
      <div class="tree-node">
        <div class="tree-row" style="color:var(--subtext)">${t("not_assigned")}</div>
        <div class="tree-children">${unassigned.map(renderClientNode).join("")}</div>
      </div>
    `;
  }

  tree.innerHTML = html || `<div style="color:var(--subtext);padding:10px;font-size:12px">${t("no_devices_sidebar")}</div>`;

  // Event-Listener anhängen (nachdem das HTML im DOM ist)
  tree.querySelectorAll("[data-toggle]").forEach((el) =>
    el.addEventListener("click", (e) => { e.stopPropagation(); toggleExpand(el.dataset.toggle); })
  );
  tree.querySelectorAll("[data-select-tenant]").forEach((el) =>
    el.addEventListener("click", (e) => { e.stopPropagation(); select("tenant", el.dataset.selectTenant); })
  );
  tree.querySelectorAll("[data-select-location]").forEach((el) =>
    el.addEventListener("click", (e) => { e.stopPropagation(); select("location", el.dataset.selectLocation); })
  );
  tree.querySelectorAll("[data-select-client]").forEach((el) =>
    el.addEventListener("click", (e) => { e.stopPropagation(); select("client", el.dataset.selectClient); })
  );
}

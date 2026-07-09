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
import { api } from "./api.js";

// Wird von app.js gesetzt, um bei Auswahl das Haupt-Panel zu aktualisieren
let onSelectCallback = null;
export function setOnSelect(fn) {
  onSelectCallback = fn;
}

// Merkt sich, welche Knoten aufgeklappt sind (damit ein Re-Render sie nicht zuklappt)
const expanded = new Set();

// -----------------------------------------------------------------
// Favoriten-System: Clients UND Tenants können mit ★ markiert werden.
// Gespeichert pro Benutzer im Browser (localStorage), damit die Auswahl
// Reloads überlebt. Format: { clients: [id,...], tenants: [id,...] }
// -----------------------------------------------------------------
let _favUser = "anon";
const favorites = { clients: new Set(), tenants: new Set() };
let favExpanded = true;   // Favoriten-Sektion aufgeklappt?

function _favKey() { return `rapalle-favs:${_favUser}`; }

export function initFavorites(username) {
  _favUser = username || "anon";
  favorites.clients.clear();
  favorites.tenants.clear();
  try {
    const raw = localStorage.getItem(_favKey());
    if (raw) {
      const d = JSON.parse(raw);
      (d.clients || []).forEach((id) => favorites.clients.add(id));
      (d.tenants || []).forEach((id) => favorites.tenants.add(id));
      if (typeof d.expanded === "boolean") favExpanded = d.expanded;
    }
  } catch {}
}

function _saveFavorites() {
  try {
    localStorage.setItem(_favKey(), JSON.stringify({
      clients: [...favorites.clients],
      tenants: [...favorites.tenants],
      expanded: favExpanded,
    }));
  } catch {}
}

function isFav(kind, id) { return favorites[kind]?.has(id); }

function toggleFav(kind, id) {
  const set = favorites[kind];
  if (!set) return;
  if (set.has(id)) set.delete(id);
  else set.add(id);
  _saveFavorites();
  renderSidebar();
}

// Kleiner Stern-Button (gefüllt = Favorit). Bekommt eine Klick-Animation.
function favStar(kind, id) {
  const on = isFav(kind, id);
  return `<span class="fav-star ${on ? "on" : ""}" data-fav="${kind}:${id}" title="Favorit">${on ? "★" : "☆"}</span>`;
}

// Für die Persistenz (persist.js): aktuellen Aufklapp-Zustand lesen/setzen.
export function getExpandedIds() { return [...expanded]; }
export function setExpandedIds(ids) {
  expanded.clear();
  for (const id of ids || []) expanded.add(id);
}

// Wird von app.js gesetzt, damit Änderungen am Aufklapp-Zustand gespeichert werden.
let onTreeStateChanged = null;
export function setOnTreeStateChanged(fn) { onTreeStateChanged = fn; }

function toggleExpand(nodeId) {
  if (expanded.has(nodeId)) expanded.delete(nodeId);
  else expanded.add(nodeId);
  renderSidebar();
  if (onTreeStateChanged) onTreeStateChanged();
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
  if (onTreeStateChanged) onTreeStateChanged();
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
        <div class="tree-row" data-toggle="${folder.id}" data-drop-folder="${folder.id}">
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
      <div class="tree-row row-anim ${selected ? "selected" : ""}" data-select-client="${client.id}" data-drag-client="${client.id}" draggable="true">
        ${children.length ? `<span data-toggle="${client.id}">${isOpen ? "▾" : "▸"}</span>` : `<span style="width:10px;display:inline-block"></span>`}
        ${clientDot(client)} <span style="flex:1">${esc(client.hostname)}</span>
        ${favStar("clients", client.id)}
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
        <div class="tree-row row-anim ${selected ? "selected" : ""}" data-drop-tenant="${tenant.id}">
          <span data-toggle="${tenant.id}">${isOpen ? "▾" : "▸"}</span>
          <span data-select-tenant="${tenant.id}" style="flex:1">
            <span class="dot" style="background:${esc(tenant.color)}"></span> ${esc(tenant.name)}
          </span>
          ${favStar("tenants", tenant.id)}
        </div>
    `;

    if (isOpen) {
      html += `<div class="tree-children">`;
      for (const location of locations) {
        const locOpen = expanded.has(location.id);
        const locSelected = state.selection?.type === "location" && state.selection.id === location.id;
        html += `
          <div class="tree-node">
            <div class="tree-row ${locSelected ? "selected" : ""}" data-drop-location="${location.id}">
              <span data-toggle="${location.id}">${locOpen ? "▾" : "▸"}</span>
              <span data-select-location="${location.id}" style="flex:1">📍 ${esc(location.name)}</span>
            </div>
            ${locOpen ? `<div class="tree-children">${renderFolderChildren(location.id, null)}</div>` : ""}
          </div>
        `;
      }
      // Clients, die direkt im Tenant liegen (Tenant gesetzt, aber keine Location)
      // - z.B. nach dem Ziehen auf einen Tenant. Damit sie sichtbar bleiben.
      const tenantClients = state.clients.filter(
        (c) => c.tenant_id === tenant.id && !c.location_id && !c.parent_client_id
      );
      for (const c of tenantClients) html += renderClientNode(c);
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

  // Stern-Klicks (Favorit an/aus) - mit kleiner Pop-Animation.
  tree.querySelectorAll("[data-fav]").forEach((el) =>
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      el.classList.add("fav-pop");
      const [kind, id] = el.dataset.fav.split(":");
      setTimeout(() => toggleFav(kind, id), 120);  // Animation kurz sichtbar lassen
    })
  );

  // --- Drag & Drop: Client in anderen Tenant / Location / Ordner ziehen ---
  tree.querySelectorAll("[data-drag-client]").forEach((el) => {
    el.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", el.dataset.dragClient);
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("dragging");
    });
    el.addEventListener("dragend", () => {
      el.classList.remove("dragging");
      tree.querySelectorAll(".drag-over").forEach((x) => x.classList.remove("drag-over"));
    });
  });

  const dropSel = "[data-drop-tenant],[data-drop-location],[data-drop-folder]";
  tree.querySelectorAll(dropSel).forEach((el) => {
    el.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.stopPropagation();               // nur das innerste Ziel hervorheben
      e.dataTransfer.dropEffect = "move";
      tree.querySelectorAll(".drag-over").forEach((x) => x.classList.remove("drag-over"));
      el.classList.add("drag-over");
    });
    el.addEventListener("dragleave", (e) => {
      if (!el.contains(e.relatedTarget)) el.classList.remove("drag-over");
    });
    el.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      el.classList.remove("drag-over");
      const clientId = e.dataTransfer.getData("text/plain");
      moveClient(clientId, {
        tenant: el.dataset.dropTenant,
        location: el.dataset.dropLocation,
        folder: el.dataset.dropFolder,
      });
    });
  });

  renderFavorites();
}

// Verschiebt einen Client per Drag&Drop in einen Tenant/eine Location/einen Ordner.
// Wirkt wie das Bearbeiten der Eigenschaften (tenant_id/location_id/folder_id).
async function moveClient(clientId, target) {
  const c = state.clients.find((x) => x.id === clientId);
  if (!c) return;

  let fields = null;
  if (target.folder) {
    const folder = state.hierarchy.folders.find((f) => f.id === target.folder);
    if (!folder) return;
    const loc = state.hierarchy.locations.find((l) => l.id === folder.location_id);
    fields = { tenant_id: loc ? loc.tenant_id : c.tenant_id, location_id: folder.location_id, folder_id: folder.id };
  } else if (target.location) {
    const loc = state.hierarchy.locations.find((l) => l.id === target.location);
    if (!loc) return;
    fields = { tenant_id: loc.tenant_id, location_id: loc.id, folder_id: null };
  } else if (target.tenant) {
    fields = { tenant_id: target.tenant, location_id: null, folder_id: null };
  }
  if (!fields) return;

  // Keine Änderung? -> nichts tun.
  if (c.tenant_id === fields.tenant_id &&
      (c.location_id || null) === (fields.location_id || null) &&
      (c.folder_id || null) === (fields.folder_id || null)) {
    return;
  }

  try {
    await api.updateClient(clientId, fields);
    Object.assign(c, fields);            // lokalen Zustand sofort aktualisieren
    // Zielpfad aufklappen, damit der Client sichtbar bleibt.
    if (fields.tenant_id) expanded.add(fields.tenant_id);
    if (fields.location_id) expanded.add(fields.location_id);
    if (fields.folder_id) expanded.add(fields.folder_id);
    renderSidebar();
    if (onSelectCallback) onSelectCallback();   // Hauptpanel ggf. aktualisieren
    window.notify?.(`${c.hostname} verschoben.`, "success", 2500);
  } catch (e) {
    window.notify?.("Verschieben fehlgeschlagen: " + e.message, "error", 6000);
  }
}

// -----------------------------------------------------------------
// Favoriten-Sektion oben in der Sidebar rendern
// -----------------------------------------------------------------
function renderFavorites() {
  const box = document.getElementById("sidebar-fav-list");
  const header = document.getElementById("fav-header");
  if (!box || !header) return;

  // Aufklapp-Zustand der Favoriten-Sektion
  const caret = header.querySelector(".fav-caret");
  if (caret) caret.textContent = favExpanded ? "▾" : "▸";
  box.style.display = favExpanded ? "block" : "none";

  const favTenants = state.hierarchy.tenants.filter((tn) => favorites.tenants.has(tn.id));
  const favClients = state.clients.filter((c) => favorites.clients.has(c.id));

  if (!favTenants.length && !favClients.length) {
    box.innerHTML = `<div class="fav-empty">Noch keine Favoriten – tippe auf ☆ neben einem Client oder Tenant.</div>`;
  } else {
    let h = "";
    for (const tn of favTenants) {
      h += `
        <div class="tree-row row-anim fav-row" data-fav-select="tenant:${tn.id}">
          <span class="dot" style="background:${esc(tn.color)}"></span>
          <span style="flex:1">${esc(tn.name)}</span>
          <span class="fav-star on" data-fav="tenants:${tn.id}">★</span>
        </div>`;
    }
    for (const c of favClients) {
      h += `
        <div class="tree-row row-anim fav-row" data-fav-select="client:${c.id}">
          ${clientDot(c)} <span style="flex:1">${esc(c.hostname)}</span>
          <span class="fav-star on" data-fav="clients:${c.id}">★</span>
        </div>`;
    }
    box.innerHTML = h;
  }

  // Klick auf Favorit -> auswählen (+ Pfad aufklappen bei Clients)
  box.querySelectorAll("[data-fav-select]").forEach((el) =>
    el.addEventListener("click", (e) => {
      if (e.target.closest("[data-fav]")) return;  // Stern-Klick separat
      const [type, id] = el.dataset.favSelect.split(":");
      if (type === "client") revealClient(id);
      select(type, id);
    })
  );
  // Stern in der Favoritenliste entfernt den Favoriten (mit Animation).
  box.querySelectorAll("[data-fav]").forEach((el) =>
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      el.classList.add("fav-pop");
      const [kind, id] = el.dataset.fav.split(":");
      setTimeout(() => toggleFav(kind, id), 120);
    })
  );
}

// Favoriten-Header (Ein-/Ausklappen) + Dashboard-Tab verkabeln. Wird einmal
// von app.js aufgerufen.
export function initSidebarNav() {
  const header = document.getElementById("fav-header");
  if (header) header.addEventListener("click", () => {
    favExpanded = !favExpanded;
    _saveFavorites();
    renderFavorites();
  });
  const dash = document.getElementById("sidebar-dashboard");
  if (dash) dash.addEventListener("click", () => {
    // Platzhalter für die Zukunft - aktuell nur visuelles Feedback.
    dash.classList.add("nav-pulse");
    setTimeout(() => dash.classList.remove("nav-pulse"), 400);
  });
}

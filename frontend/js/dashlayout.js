// dashlayout.js
// -------------
// Anpassbare Client-Detailansicht. Der Nutzer kann Bausteine ("Parts") wie
// Status, Aktionen und Übersicht-Ordner frei anordnen, in Größe ändern, in
// mehrere Ordner aufteilen und einzelne Sub-Ansichten (Metrics/Notes/Disk)
// zwischen Ordnern verschieben - alles PRO BENUTZER gespeichert (persist.js).
//
// Layout-Modell (dashLayout):
//   { cols: 12, panels: [ Panel, ... ] }
//   Panel:
//     { id, type:"status"|"actions"|"websites"|"folder", w: <1..12 Spalten>,
//       title?, subs?: ["metrics","notes","disk", ...], activeSub? }
//
// Im Edit-Modus (Profil -> "Dashboard bearbeiten") lässt sich alles per
// Drag&Drop umsortieren, die Breite ziehen, Ordner anlegen/umbenennen/leeren,
// Sub-Ansichten zwischen Ordnern schieben und Parts als eigenes Fenster
// herauslösen. Außerhalb des Edit-Modus kann man Parts trotzdem per Drag an
// der Kopfzeile in ein eigenes Fenster ziehen (Client bleibt im Dashboard).

import { t } from "./i18n.js";
import { esc } from "./utils.js";
import {
  renderStatusPart, renderActionsPart, renderWebsitesPart, clientHasWebsites,
  renderOverviewSub, OVERVIEW_SUBS,
} from "./panel.js";
import { openWindow } from "./windowmanager.js";
import {
  getDashLayout, setDashLayout, getDashEdit, setDashEdit, scheduleSave,
} from "./persist.js";
import { state } from "./state.js";

let _uid = 0;
const nid = (p) => `${p}-${Date.now().toString(36)}-${(_uid++).toString(36)}`;

// Standard-Layout, falls der Nutzer noch keins angepasst hat: links Status +
// Aktionen (schmal), rechts ein Übersicht-Ordner (breit) - wie bisher.
function defaultLayout() {
  return {
    cols: 12,
    panels: [
      { id: nid("p"), type: "status", w: 4 },
      { id: nid("p"), type: "actions", w: 4 },
      { id: nid("p"), type: "folder", title: t("overview"),
        subs: ["metrics", "notes", "disk"], activeSub: "metrics", w: 8 },
    ],
  };
}

function currentLayout() {
  let l = getDashLayout();
  if (!l || !Array.isArray(l.panels) || !l.panels.length) {
    l = defaultLayout();
    setDashLayout(l);
  }
  // Sicherheit: IDs nachziehen (alte gespeicherte Layouts).
  l.cols = l.cols || 12;
  for (const p of l.panels) { if (!p.id) p.id = nid("p"); if (!p.w) p.w = 6; }
  return l;
}

function saveLayout() { scheduleSave(state); }

const PART_LABEL = {
  status: () => t("status"),
  actions: () => t("actions"),
  websites: () => "🔗 Websites",
};

// Titel eines Panels (Ordner tragen einen frei wählbaren Namen).
function panelTitle(p) {
  if (p.type === "folder") return p.title || t("overview");
  return (PART_LABEL[p.type] || (() => p.type))();
}

// =================================================================
// Haupt-Renderer (von panel.js aufgerufen)
// =================================================================
export function renderClientLayout(host, toolbarHost, client) {
  if (!host) return;
  const layout = currentLayout();
  const edit = getDashEdit();

  // Toolbar: Edit-Umschalter + (im Edit) Aktionen zum Hinzufügen.
  if (toolbarHost) {
    toolbarHost.innerHTML = `
      <button class="dash-edit-toggle ${edit ? "on" : ""}" title="Dashboard-Layout bearbeiten (an/aus)">
        ${edit ? "✓ Bearbeiten" : "✎ Bearbeiten"}
      </button>
      ${edit ? `
        <span class="dash-edit-tools">
          <button data-add="folder" title="Neuen Ordner anlegen">+ Ordner</button>
          <button data-add="status" title="Status hinzufügen">+ Status</button>
          <button data-add="actions" title="Aktionen hinzufügen">+ Aktionen</button>
          <button data-add="websites" title="Websites hinzufügen">+ Websites</button>
          <button data-reset title="Auf Standard zurücksetzen">↺ Standard</button>
        </span>` : ""}
    `;
    toolbarHost.querySelector(".dash-edit-toggle").addEventListener("click", () => {
      setDashEdit(!edit); saveLayout(); renderClientLayout(host, toolbarHost, client);
    });
    toolbarHost.querySelectorAll("[data-add]").forEach((b) =>
      b.addEventListener("click", () => addPanel(b.dataset.add, host, toolbarHost, client)));
    toolbarHost.querySelector("[data-reset]")?.addEventListener("click", () => {
      if (!confirm("Layout auf Standard zurücksetzen?")) return;
      setDashLayout(defaultLayout()); saveLayout(); renderClientLayout(host, toolbarHost, client);
    });
  }

  host.className = "dash-layout" + (edit ? " editing" : "");
  host.style.setProperty("--dash-cols", layout.cols);
  host.innerHTML = "";

  layout.panels.forEach((panel, idx) => {
    const card = buildPanel(panel, client, { host, toolbarHost, edit, layout, idx });
    host.appendChild(card);
  });

  // Auto-Ausblenden: Websites-Panel ohne verknüpfte Websites dezent markieren.
  const wsPanel = layout.panels.find((p) => p.type === "websites");
  if (wsPanel) {
    clientHasWebsites(client.id).then((has) => {
      const c = host.querySelector(`[data-panel="${wsPanel.id}"]`);
      if (c && !has && !edit) c.style.display = "none";
    });
  }
}

// =================================================================
// Ein Panel (Karte) bauen
// =================================================================
function buildPanel(panel, client, ctx) {
  const { host, toolbarHost, edit, layout, idx } = ctx;
  const card = document.createElement("div");
  card.className = "panel dash-lp";
  card.dataset.panel = panel.id;
  card.style.gridColumn = `span ${Math.max(1, Math.min(layout.cols, panel.w || 6))}`;

  // --- Kopfzeile ---
  const head = document.createElement("div");
  head.className = "dash-lp-head";
  const titleEl = document.createElement("span");
  titleEl.className = "dash-lp-title";
  titleEl.textContent = panelTitle(panel);
  head.appendChild(titleEl);

  const tools = document.createElement("span");
  tools.className = "dash-lp-tools";

  // Ordner-Tabs (Sub-Ansichten)
  if (panel.type === "folder") {
    const tabbar = document.createElement("span");
    tabbar.className = "tab-bar dash-folder-tabs";
    (panel.subs || []).forEach((sub) => {
      const b = document.createElement("button");
      b.className = "tab-btn" + (panel.activeSub === sub ? " active" : "");
      b.textContent = (OVERVIEW_SUBS[sub] ? OVERVIEW_SUBS[sub]() : sub);
      b.draggable = edit;    // im Edit-Modus Sub zwischen Ordnern ziehen
      b.dataset.sub = sub;
      b.addEventListener("click", () => {
        panel.activeSub = sub; saveLayout();
        fillPanelBody(bodyEl, panel, client);
        tabbar.querySelectorAll(".tab-btn").forEach((x) => x.classList.toggle("active", x.dataset.sub === sub));
      });
      if (edit) {
        b.addEventListener("dragstart", (e) => {
          e.stopPropagation();
          e.dataTransfer.setData("text/x-dash-sub", JSON.stringify({ from: panel.id, sub }));
          e.dataTransfer.effectAllowed = "move";
        });
      }
      tabbar.appendChild(b);
    });
    tools.appendChild(tabbar);
  }

  // Pop-out-Button (immer verfügbar - auch ohne Edit-Modus)
  const popBtn = document.createElement("button");
  popBtn.className = "dash-lp-btn";
  popBtn.title = "Als eigenes Fenster herauslösen";
  popBtn.textContent = "⧉";
  popBtn.addEventListener("click", (e) => { e.stopPropagation(); detachPanel(panel, client); });
  tools.appendChild(popBtn);

  if (edit) {
    const del = document.createElement("button");
    del.className = "dash-lp-btn";
    del.title = "Entfernen";
    del.textContent = "✕";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      layout.panels = layout.panels.filter((p) => p.id !== panel.id);
      saveLayout(); renderClientLayout(host, toolbarHost, client);
    });
    if (panel.type === "folder") {
      const ren = document.createElement("button");
      ren.className = "dash-lp-btn";
      ren.title = "Ordner umbenennen";
      ren.textContent = "✎";
      ren.addEventListener("click", (e) => {
        e.stopPropagation();
        const name = prompt("Ordnername:", panel.title || t("overview"));
        if (name !== null) { panel.title = name.trim() || t("overview"); saveLayout(); titleEl.textContent = panelTitle(panel); }
      });
      tools.appendChild(ren);
    }
    tools.appendChild(del);
  }

  head.appendChild(tools);
  card.appendChild(head);

  // --- Körper ---
  const bodyEl = document.createElement("div");
  bodyEl.className = "dash-lp-body";
  card.appendChild(bodyEl);
  fillPanelBody(bodyEl, panel, client);

  // --- Interaktion: Herauslösen per Drag an der Kopfzeile (immer),
  //     Umsortieren + Sub-Drop nur im Edit-Modus. ---
  attachPanelInteractions(card, head, panel, client, ctx);

  // --- Breiten-Resizer (nur Edit) ---
  if (edit) {
    const grip = document.createElement("div");
    grip.className = "dash-lp-resizer";
    attachWidthResize(grip, card, panel, client, ctx);
    card.appendChild(grip);
  }

  return card;
}

function fillPanelBody(bodyEl, panel, client) {
  bodyEl.innerHTML = "";
  if (panel.type === "status") return renderStatusPart(bodyEl, client);
  if (panel.type === "actions") { bodyEl.className = "dash-lp-body actions-panel"; return renderActionsPart(bodyEl, client); }
  if (panel.type === "websites") { bodyEl.className = "dash-lp-body actions-panel"; return renderWebsitesPart(bodyEl, client); }
  if (panel.type === "folder") {
    bodyEl.className = "dash-lp-body overview-content";
    const sub = panel.activeSub || (panel.subs && panel.subs[0]) || "metrics";
    return renderOverviewSub(bodyEl, client, sub, () => fillPanelBody(bodyEl, panel, client));
  }
}

// =================================================================
// Panel hinzufügen
// =================================================================
function addPanel(type, host, toolbarHost, client) {
  const layout = currentLayout();
  if (type === "folder") {
    const name = prompt("Ordnername:", "Neuer Ordner");
    if (name === null) return;
    layout.panels.push({ id: nid("p"), type: "folder", title: name.trim() || "Ordner",
      subs: ["metrics"], activeSub: "metrics", w: 6 });
  } else {
    layout.panels.push({ id: nid("p"), type, w: 4 });
  }
  saveLayout();
  renderClientLayout(host, toolbarHost, client);
}

// =================================================================
// Panel als eigenes Fenster herauslösen (Client bleibt im Dashboard)
// =================================================================
export function detachPanel(panel, client) {
  const props = {
    clientId: client.id,
    part: panel.type,
    // Ordner-Konfiguration mitgeben, damit das Fenster dieselben Sub-Ansichten hat.
    subs: panel.subs ? [...panel.subs] : null,
    activeSub: panel.activeSub || null,
    partTitle: panelTitle(panel),
  };
  const key = `panelpart-${client.id}-${panel.type}-${(panel.subs || []).join(".") || "x"}`;
  openWindow({
    key, appId: "panelpart",
    title: `${panelTitle(panel)} — ${client.hostname}`,
    props, clientColor: client.color,
    w: panel.type === "folder" ? 720 : 380,
    h: panel.type === "folder" ? 520 : 420,
  });
}

// =================================================================
// Drag-Interaktionen
// =================================================================
function attachPanelInteractions(card, head, panel, client, ctx) {
  const { host, toolbarHost, edit, layout } = ctx;

  // Pointer-basiertes Ziehen an der Kopfzeile: kurzer Zug = umsortieren (Edit),
  // weiter Zug nach außen = als Fenster herauslösen (immer möglich).
  head.addEventListener("mousedown", (e) => {
    if (e.target.closest("button") || e.target.closest(".tab-btn")) return;
    const startX = e.clientX, startY = e.clientY;
    let mode = null;   // "reorder" | "detach"
    let placeholder = null;

    function onMove(ev) {
      const dx = ev.clientX - startX, dy = ev.clientY - startY;
      const dist = Math.abs(dx) + Math.abs(dy);
      if (!mode && dist > 6) {
        // Verlässt der Zeiger die Karte deutlich? -> Herauslösen. Sonst
        // (nur im Edit) umsortieren.
        mode = "start";
      }
      if (mode === "start" && dist > 10) {
        const r = host.getBoundingClientRect();
        const outside = ev.clientX < r.left - 20 || ev.clientX > r.right + 20 ||
                        ev.clientY < r.top - 40 || ev.clientY > r.bottom + 60;
        if (edit && !outside) {
          mode = "reorder";
          card.classList.add("dragging");
          placeholder = document.createElement("div");
          placeholder.className = "dash-lp-placeholder";
          placeholder.style.gridColumn = card.style.gridColumn;
          card.after(placeholder);
          card.style.position = "fixed";
          card.style.width = `${card.offsetWidth}px`;
          card.style.zIndex = "9999";
          card.style.pointerEvents = "none";
        } else {
          mode = "detach";
          card.classList.add("detach-hint");
        }
      }
      if (mode === "reorder") {
        card.style.left = `${ev.clientX - 40}px`;
        card.style.top = `${ev.clientY - 14}px`;
        const over = elementFromPanel(ev.clientX, ev.clientY, host, card);
        if (over && placeholder) {
          const rect = over.getBoundingClientRect();
          const after = ev.clientX > rect.left + rect.width / 2;
          if (after) over.after(placeholder); else over.before(placeholder);
        }
      }
    }
    function onUp(ev) {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      if (mode === "reorder") {
        // Neue Reihenfolge aus DOM (Platzhalterposition) übernehmen.
        card.style.cssText = card.style.cssText
          .replace(/position:[^;]+;?/, "").replace(/left:[^;]+;?/, "")
          .replace(/top:[^;]+;?/, "").replace(/z-index:[^;]+;?/, "")
          .replace(/pointer-events:[^;]+;?/, "");
        card.classList.remove("dragging");
        card.style.gridColumn = `span ${panel.w}`;
        if (placeholder) { placeholder.replaceWith(card); }
        commitOrderFromDom(host, layout);
        saveLayout();
      } else if (mode === "detach") {
        card.classList.remove("detach-hint");
        detachPanel(panel, client);
      }
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  // Ordner nehmen im Edit-Modus per Drop Sub-Ansichten von anderen Ordnern auf.
  if (edit && panel.type === "folder") {
    card.addEventListener("dragover", (e) => {
      if (e.dataTransfer.types.includes("text/x-dash-sub")) { e.preventDefault(); card.classList.add("drop-target"); }
    });
    card.addEventListener("dragleave", () => card.classList.remove("drop-target"));
    card.addEventListener("drop", (e) => {
      card.classList.remove("drop-target");
      const raw = e.dataTransfer.getData("text/x-dash-sub");
      if (!raw) return;
      e.preventDefault();
      const { from, sub } = JSON.parse(raw);
      if (from === panel.id) return;
      const src = layout.panels.find((p) => p.id === from);
      if (src) src.subs = (src.subs || []).filter((x) => x !== sub);
      if (!panel.subs.includes(sub)) panel.subs.push(sub);
      panel.activeSub = sub;
      // Leere Ordner behalten (Nutzer kann sie später wieder befüllen/entfernen).
      if (src && src.activeSub === sub) src.activeSub = src.subs[0] || null;
      saveLayout();
      renderClientLayout(host, toolbarHost, client);
    });
  }
}

function elementFromPanel(x, y, host, exclude) {
  const cards = [...host.querySelectorAll(".dash-lp")].filter((c) => c !== exclude);
  for (const c of cards) {
    const r = c.getBoundingClientRect();
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return c;
  }
  // Fallback: nächstes Element in derselben Zeile
  return cards.find((c) => {
    const r = c.getBoundingClientRect();
    return y >= r.top && y <= r.bottom;
  }) || null;
}

function commitOrderFromDom(host, layout) {
  const order = [...host.querySelectorAll(".dash-lp")].map((c) => c.dataset.panel);
  layout.panels.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
}

// =================================================================
// Breite ziehen (Grid-Spalten)
// =================================================================
function attachWidthResize(grip, card, panel, client, ctx) {
  const { host, layout } = ctx;
  grip.addEventListener("mousedown", (e) => {
    e.preventDefault(); e.stopPropagation();
    document.body.style.userSelect = "none";
    const hostRect = host.getBoundingClientRect();
    const colW = hostRect.width / layout.cols;
    const startX = e.clientX;
    const startW = panel.w;
    function onMove(ev) {
      const deltaCols = Math.round((ev.clientX - startX) / colW);
      panel.w = Math.max(2, Math.min(layout.cols, startW + deltaCols));
      card.style.gridColumn = `span ${panel.w}`;
    }
    function onUp() {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
      saveLayout();
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

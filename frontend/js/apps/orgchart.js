// apps/orgchart.js
// ----------------
// Organigramm: alle Benutzer und Gruppen als Baum "wer ist wem unterstellt".
//   - Per Ziehen (Drag & Drop) jemanden einem anderen unterstellen
//   - Arbeitsbereich (Abteilung) je Benutzer setzen
//   - Suche, Aufklappen/Zuklappen, Gruppierung nach Arbeitsbereich
// Rechte: 'see_org' zum Ansehen, 'manage_org' zum Ändern.

import { api } from "../api.js";
import { state, isAdmin, hasGlobalPerm } from "../state.js";
import { esc, uiConfirm } from "../utils.js";
import { isUnmanagedGroup } from "../subjectpicker.js";
import { t } from "../i18n.js";

const K = (n) => `${n.type}:${n.id}`;

export function renderOrgChart(body, win) {
  let tree = { nodes: [], links: {}, can_manage: false };
  let workspaces = [];
  let search = "";
  let groupByWorkspace = false;
  let viewMode = "list";           // "list" = Einrückungs-Baum, "chart" = Baumdiagramm
  let adFolderOpen = false;        // Ordner "Unverwaltete AD-Gruppen"
  const collapsed = new Set();

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
      <div class="explorer-toolbar" style="gap:8px;flex-wrap:wrap">
        <input type="text" id="og-search" placeholder="${t("u_person_oder_gruppe_suchen")}" style="flex:1;min-width:160px" />
        <label style="display:flex;gap:5px;align-items:center;font-size:12px;color:var(--subtext)">
          <input type="checkbox" id="og-byws" /> nach Arbeitsbereich
        </label>
        <button class="taskbar-btn" id="og-view" title="${t("u_zwischen_liste_und_baumdiagramm_we")}">🌳 Baumdiagramm</button>
        <button class="taskbar-btn" id="og-expand">Alle aufklappen</button>
        <button class="taskbar-btn" id="og-collapse">Alle zuklappen</button>
      </div>
      <div id="og-hint" style="padding:5px 12px;font-size:11.5px;color:var(--subtext)"></div>
      <div id="og-tree" style="flex:1;overflow:auto;padding:6px 10px 14px"></div>
    </div>`;

  const treeEl = body.querySelector("#og-tree");
  const hintEl = body.querySelector("#og-hint");

  body.querySelector("#og-search").addEventListener("input", (e) => {
    search = e.target.value.trim().toLowerCase(); draw();
  });
  body.querySelector("#og-byws").addEventListener("change", (e) => {
    groupByWorkspace = e.target.checked; draw();
  });
  body.querySelector("#og-view").addEventListener("click", (e) => {
    viewMode = viewMode === "list" ? "chart" : "list";
    e.target.textContent = viewMode === "list" ? "🌳 Baumdiagramm" : "☰ Liste";
    draw();
  });
  body.querySelector("#og-expand").addEventListener("click", () => { collapsed.clear(); draw(); });
  body.querySelector("#og-collapse").addEventListener("click", () => {
    tree.nodes.forEach((n) => { if (childrenOf(K(n)).length) collapsed.add(K(n)); });
    draw();
  });

  const nodeByKey = () => {
    const m = new Map();
    for (const n of tree.nodes) m.set(K(n), n);
    return m;
  };
  let byKey = new Map();

  function childrenOf(parentKey) {
    return tree.nodes.filter((n) => (tree.links[K(n)] || null) === parentKey);
  }
  // Unverwaltete AD-Gruppen: nur solche, die NICHT eingeordnet sind und selbst
  // niemanden unter sich haben - sonst würde man Teile des Baums verstecken.
  function isLooseAdGroup(n) {
    if (n.type !== "group" || !isUnmanagedGroup(n)) return false;
    const p = tree.links[K(n)];
    if (p && byKey.has(p)) return false;        // ist eingeordnet
    return childrenOf(K(n)).length === 0;       // hat keine Untergebenen
  }

  function allRoots() {
    return tree.nodes.filter((n) => {
      const p = tree.links[K(n)];
      return !p || !byKey.has(p);      // ohne Vorgesetzten oder verwaister Verweis
    });
  }
  function roots() { return allRoots().filter((n) => !isLooseAdGroup(n)); }
  function looseAdGroups() { return allRoots().filter(isLooseAdGroup); }

  // Ordner für die unverwalteten AD-Gruppen (standardmäßig zu, damit hunderte
  // importierte Verzeichnis-Gruppen das Organigramm nicht überladen).
  function adFolderEl() {
    const groups = looseAdGroups();
    if (!groups.length) return null;
    const wrap = document.createElement("div");
    wrap.style.cssText = "margin-top:10px;border:1px dashed var(--border);border-radius:8px;padding:5px 9px";
    const head = document.createElement("div");
    head.style.cssText = "cursor:pointer;font-size:12px;color:var(--subtext);user-select:none";
    head.textContent = `${adFolderOpen ? "▾" : "▸"} 📂 Unverwaltete AD-Gruppen (${groups.length})`;
    head.title = t("u_aus_dem_verzeichnis_importiert_im_");
    head.addEventListener("click", () => { adFolderOpen = !adFolderOpen; draw(); });
    wrap.appendChild(head);
    if (adFolderOpen) {
      const inner = document.createElement("div");
      inner.style.marginTop = "4px";
      for (const g of groups) inner.appendChild(nodeEl(g, 0));
      wrap.appendChild(inner);
    }
    return wrap;
  }

  async function load() {
    treeEl.innerHTML = `<div style="color:var(--subtext);font-size:13px;padding:10px">Lädt…</div>`;
    try {
      [tree, workspaces] = await Promise.all([
        api.getOrgTree(),
        api.getWorkspaces().catch(() => []),
      ]);
    } catch (e) {
      treeEl.innerHTML = `<div style="color:var(--danger);padding:10px">${esc(e.message)}</div>`;
      return;
    }
    byKey = nodeByKey();
    draw();
  }

  const mayManage = () => tree.can_manage || isAdmin() || hasGlobalPerm("manage_org");

  function draw() {
    byKey = nodeByKey();
    hintEl.textContent = mayManage()
      ? t("u_ziehen_und_auf_eine_person_gruppe_")
      : t("u_nur_ansicht_zum_andern_fehlt_das_r");

    if (search) return drawSearch();
    if (groupByWorkspace) return drawByWorkspace();
    if (viewMode === "chart") return drawChart();

    treeEl.innerHTML = "";
    treeEl.appendChild(rootDropZone());
    const rs = roots();
    if (!rs.length && !looseAdGroups().length) {
      treeEl.insertAdjacentHTML("beforeend",
        `<div style="color:var(--subtext);font-size:13px;padding:10px">Keine Benutzer oder Gruppen.</div>`);
      return;
    }
    for (const n of rs) treeEl.appendChild(nodeEl(n, 0));
    const folder = adFolderEl();
    if (folder) treeEl.appendChild(folder);
  }

  // ---------------------------------------------------------------
  // BAUMDIAGRAMM: klassische Organigramm-Darstellung mit Kästchen und
  // Verbindungslinien (rein mit CSS gezeichnet, kein SVG nötig).
  // Waagerecht scrollbar, damit auch breite Ebenen lesbar bleiben.
  // ---------------------------------------------------------------
  function chartNode(n) {
    const kids = childrenOf(K(n)).filter((c) => !isLooseAdGroup(c));
    const isUser = n.type === "user";
    const icon = isUser ? "👤" : (n.is_ad_group ? "🏢" : "👥");
    const isMe = tree.me && n.type === "user" && n.id === tree.me.id;
    const li = document.createElement("li");

    const boxWrap = document.createElement("div");
    boxWrap.className = "oc-boxwrap";
    const box = document.createElement("div");
    box.className = "oc-box" + (isMe ? " oc-me" : "");
    box.innerHTML = `
      <div class="oc-title">${icon} ${esc(n.name)}</div>
      ${isUser && n.workspace ? `<div class="oc-sub">🏷️ ${esc(n.workspace)}</div>` : ""}
      ${!isUser && isUnmanagedGroup(n) ? `<div class="oc-sub">AD, unverwaltet</div>` : ""}
      ${kids.length ? `<div class="oc-count">${kids.length} direkt unterstellt</div>` : ""}`;
    box.title = isUser ? `${n.name} (${n.username || ""})` : n.name;

    // Auf-/Zuklappen direkt am Kästchen
    if (kids.length) {
      const tgl = document.createElement("button");
      tgl.className = "oc-toggle";
      tgl.textContent = collapsed.has(K(n)) ? "+" : "−";
      tgl.title = collapsed.has(K(n)) ? "Untergebene einblenden" : "Untergebene ausblenden";
      tgl.addEventListener("click", (e) => {
        e.stopPropagation();
        if (collapsed.has(K(n))) collapsed.delete(K(n)); else collapsed.add(K(n));
        draw();
      });
      box.appendChild(tgl);
    }
    // Umhängen per Ziehen - wie in der Listenansicht
    if (mayManage()) {
      box.draggable = true;
      box.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", K(n)));
      box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("oc-drop"); });
      box.addEventListener("dragleave", () => box.classList.remove("oc-drop"));
      box.addEventListener("drop", async (e) => {
        e.preventDefault(); e.stopPropagation();
        box.classList.remove("oc-drop");
        await setParent(e.dataTransfer.getData("text/plain"), K(n));
      });
      box.addEventListener("dblclick", () => { if (n.type === "user") openWorkspaceDialog(n); });
    }
    boxWrap.appendChild(box);
    li.appendChild(boxWrap);

    if (kids.length && !collapsed.has(K(n))) {
      const ul = document.createElement("ul");
      for (const c of kids) ul.appendChild(chartNode(c));
      li.appendChild(ul);
    }
    return li;
  }

  function drawChart() {
    treeEl.innerHTML = "";
    treeEl.appendChild(rootDropZone());
    const rs = roots();
    if (!rs.length && !looseAdGroups().length) {
      treeEl.insertAdjacentHTML("beforeend",
        `<div style="color:var(--subtext);font-size:13px;padding:10px">Keine Benutzer oder Gruppen.</div>`);
      return;
    }
    const scroller = document.createElement("div");
    scroller.style.cssText = "overflow:auto;padding:8px 4px 16px";
    const ul = document.createElement("ul");
    ul.className = "oc-tree";
    for (const n of rs) ul.appendChild(chartNode(n));
    scroller.appendChild(ul);
    treeEl.appendChild(scroller);
    const folder = adFolderEl();
    if (folder) treeEl.appendChild(folder);
  }

  // Ablagefläche "Oberste Ebene"
  function rootDropZone() {
    const el = document.createElement("div");
    el.textContent = "⌂ Oberste Ebene (hierher ziehen = Zuordnung aufheben)";
    el.style.cssText = `font-size:11.5px;color:var(--subtext);border:1px dashed var(--border);
      border-radius:8px;padding:5px 9px;margin-bottom:8px`;
    if (!mayManage()) return el;
    el.addEventListener("dragover", (e) => { e.preventDefault(); el.style.borderColor = "var(--accent)"; });
    el.addEventListener("dragleave", () => { el.style.borderColor = "var(--border)"; });
    el.addEventListener("drop", async (e) => {
      e.preventDefault(); el.style.borderColor = "var(--border)";
      await setParent(e.dataTransfer.getData("text/plain"), null);
    });
    return el;
  }

  function badge(text, color) {
    return `<span style="font-size:10px;background:${color};color:#fff;border-radius:6px;
      padding:1px 6px;margin-left:5px">${esc(text)}</span>`;
  }

  function nodeRow(n) {
    const kids = childrenOf(K(n));
    const isUser = n.type === "user";
    const icon = isUser ? "👤" : (n.is_ad_group ? "🏢" : "👥");
    const row = document.createElement("div");
    row.className = "og-row";
    row.dataset.key = K(n);
    row.style.cssText = `display:flex;align-items:center;gap:7px;padding:5px 8px;border-radius:7px;
      font-size:13px;cursor:${mayManage() ? "grab" : "default"};border:1px solid transparent`;
    row.innerHTML = `
      ${kids.length ? `<button class="taskbar-btn og-toggle" style="width:20px;height:20px;padding:0;font-size:10px;flex:none">
          ${collapsed.has(K(n)) ? "▸" : "▾"}</button>`
        : `<span style="width:20px;flex:none"></span>`}
      <span style="flex:none">${icon}</span>
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
        ${esc(n.name)}
        ${isUser && n.username ? `<span style="color:var(--subtext);font-size:11px">@${esc(n.username)}</span>` : ""}
        ${n.role === "admin" ? badge("ADMIN", "var(--warn,#f5a524)") : ""}
        ${n.unmanaged ? badge("AD unverwaltet", "#64748b") : ""}
        ${isUser && n.workspace ? badge(n.workspace, "var(--accent)") : ""}
      </span>
      ${kids.length ? `<span style="font-size:11px;color:var(--subtext);flex:none">${kids.length} unterstellt</span>` : ""}
      ${isUser && mayManage() ? `<button class="taskbar-btn og-ws" style="font-size:10px;padding:1px 6px;flex:none"
          title="Arbeitsbereich zuweisen">🏷️</button>` : ""}`;

    row.querySelector(".og-toggle")?.addEventListener("click", (e) => {
      e.stopPropagation();
      if (collapsed.has(K(n))) collapsed.delete(K(n)); else collapsed.add(K(n));
      draw();
    });
    row.querySelector(".og-ws")?.addEventListener("click", (e) => {
      e.stopPropagation(); openWorkspaceDialog(n);
    });

    if (mayManage()) {
      row.draggable = true;
      row.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", K(n));
        e.dataTransfer.effectAllowed = "move";
        row.style.opacity = ".5";
      });
      row.addEventListener("dragend", () => { row.style.opacity = ""; });
      row.addEventListener("dragover", (e) => {
        e.preventDefault();
        row.style.borderColor = "var(--accent)";
        row.style.background = "rgba(var(--accent-rgb),.10)";
      });
      row.addEventListener("dragleave", () => {
        row.style.borderColor = "transparent"; row.style.background = "";
      });
      row.addEventListener("drop", async (e) => {
        e.preventDefault(); e.stopPropagation();
        row.style.borderColor = "transparent"; row.style.background = "";
        await setParent(e.dataTransfer.getData("text/plain"), K(n));
      });
    }
    return row;
  }

  function nodeEl(n, depth) {
    const wrap = document.createElement("div");
    wrap.style.marginLeft = depth ? "18px" : "0";
    wrap.style.borderLeft = depth ? "1px solid var(--border)" : "none";
    wrap.style.paddingLeft = depth ? "8px" : "0";
    wrap.appendChild(nodeRow(n));
    if (!collapsed.has(K(n))) {
      for (const c of childrenOf(K(n))) wrap.appendChild(nodeEl(c, depth + 1));
    }
    return wrap;
  }

  // Flache Trefferliste bei aktiver Suche (mit Pfad zum Vorgesetzten)
  function drawSearch() {
    const hits = tree.nodes.filter((n) =>
      `${n.name} ${n.username || ""} ${n.workspace || ""}`.toLowerCase().includes(search));
    treeEl.innerHTML = hits.length ? "" :
      `<div style="color:var(--subtext);font-size:13px;padding:10px">Keine Treffer.</div>`;
    for (const n of hits) {
      const box = document.createElement("div");
      box.appendChild(nodeRow(n));
      const path = [];
      let p = tree.links[K(n)];
      while (p && byKey.has(p) && path.length < 8) { path.push(byKey.get(p).name); p = tree.links[p]; }
      if (path.length) {
        const el = document.createElement("div");
        el.style.cssText = "font-size:11px;color:var(--subtext);margin:0 0 4px 34px";
        el.textContent = "↳ unterstellt: " + path.join(" ← ");
        box.appendChild(el);
      }
      treeEl.appendChild(box);
    }
  }

  // Nach Arbeitsbereich gruppiert (nur Benutzer)
  function drawByWorkspace() {
    const groups = new Map();
    for (const n of tree.nodes) {
      if (n.type !== "user") continue;
      const ws = n.workspace || "(ohne Arbeitsbereich)";
      if (!groups.has(ws)) groups.set(ws, []);
      groups.get(ws).push(n);
    }
    treeEl.innerHTML = "";
    [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).forEach(([ws, list]) => {
      const sec = document.createElement("div");
      sec.style.marginBottom = "10px";
      sec.innerHTML = `<div style="font-size:12px;font-weight:700;color:var(--accent);margin-bottom:3px">
        🏷️ ${esc(ws)} <span style="color:var(--subtext);font-weight:400">(${list.length})</span></div>`;
      list.sort((a, b) => a.name.localeCompare(b.name)).forEach((n) => sec.appendChild(nodeRow(n)));
      treeEl.appendChild(sec);
    });
  }

  async function setParent(childKey, parentKey) {
    if (!childKey || childKey === parentKey) return;
    const [ct, ...cRest] = childKey.split(":");
    const child_id = cRest.join(":");
    let parent_type = null, parent_id = null;
    if (parentKey) {
      const [pt, ...pRest] = parentKey.split(":");
      parent_type = pt; parent_id = pRest.join(":");
    }
    try {
      await api.setOrgParent({ child_type: ct, child_id, parent_type, parent_id });
      await load();
    } catch (e) { window.notify?.(e.message, "error", 6000); }
  }

  // Arbeitsbereich zuweisen (mit Vorschlägen aus bereits vergebenen)
  function openWorkspaceDialog(n) {
    const back = document.createElement("div");
    back.className = "widget-picker-back";
    back.innerHTML = `
      <div class="widget-picker" style="max-width:340px">
        <div class="wp-head"><strong>Arbeitsbereich – ${esc(n.name)}</strong>
          <button class="dash-w-btn" data-close>✕</button></div>
        <div class="wp-body" style="padding:10px">
          <input id="ws-input" list="ws-list" value="${esc(n.workspace || "")}"
                 placeholder="z.B. Technik, Vertrieb, Buchhaltung" style="width:100%" />
          <datalist id="ws-list">${workspaces.map((w) => `<option value="${esc(w)}"></option>`).join("")}</datalist>
          <div style="font-size:11.5px;color:var(--subtext);margin-top:6px">
            Leer lassen entfernt die Zuordnung.</div>
        </div>
        <div style="padding:10px;display:flex;justify-content:flex-end;gap:6px">
          <button class="taskbar-btn" data-close>Abbrechen</button>
          <button class="btn-primary" data-ok>Speichern</button>
        </div>
      </div>`;
    document.body.appendChild(back);
    const close = () => back.remove();
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    back.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
    back.querySelector("#ws-input").focus();
    back.querySelector("[data-ok]").addEventListener("click", async () => {
      const ws = back.querySelector("#ws-input").value.trim();
      close();
      try { await api.setWorkspace(n.id, ws); await load(); }
      catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  load();
}

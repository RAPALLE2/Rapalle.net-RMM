// contextmenu.js - einheitliches Rechtsklick-/Kontextmenü für das ganze Dashboard.
// -------------------------------------------------------------------------------
// showContextMenu(x, y, items)  zeigt ein Menü an der Position (x,y).
//   items: Array aus
//     { label, icon?, onClick, disabled?, danger?, submenu?, preview? }  oder
//     { separator: true }  oder
//     { header: "Text" }   (nicht klickbare Überschrift)
//   - submenu: Array weiterer items (klappt zur Seite auf)
//   - preview: HTMLElement, das statt eines Labels als Vorschau eingebettet wird
//             (z. B. eine kleine App-Vorschau im Taskleisten-Menü)
// bindContextMenu(el, builder)  hängt an ein Element ein Rechtsklick-Menü; builder(ev)
//   liefert die items (oder null, um nichts zu zeigen). Langes Antippen (Touch) öffnet
//   es ebenfalls. Gibt eine Abmeldefunktion zurück.
//
// Das Menü schließt bei Klick daneben, Escape, Scrollen oder Fensterwechsel. Es bleibt
// immer im sichtbaren Bereich (wird bei Bedarf nach links/oben geklappt).

import { esc } from "./utils.js";

let _open = null;        // aktuell offenes Wurzelmenü (Element)

export function closeContextMenu() {
  if (_open) { _open.remove(); _open = null; }
  document.removeEventListener("pointerdown", _outside, true);
  document.removeEventListener("keydown", _onKey, true);
  window.removeEventListener("blur", closeContextMenu);
  window.removeEventListener("resize", closeContextMenu);
  document.removeEventListener("scroll", closeContextMenu, true);
}

function _outside(e) { if (_open && !_open.contains(e.target) && !e.target.closest(".ctx-menu")) closeContextMenu(); }
function _onKey(e) { if (e.key === "Escape") closeContextMenu(); }

function buildMenu(items, depth = 0) {
  const menu = document.createElement("div");
  menu.className = "ctx-menu";
  menu.setAttribute("role", "menu");
  for (const it of items) {
    if (!it) continue;
    if (it.separator) { const s = document.createElement("div"); s.className = "ctx-sep"; menu.appendChild(s); continue; }
    if (it.header) { const h = document.createElement("div"); h.className = "ctx-header"; h.textContent = it.header; menu.appendChild(h); continue; }
    if (it.preview instanceof HTMLElement) {
      const wrap = document.createElement("div"); wrap.className = "ctx-preview"; wrap.appendChild(it.preview); menu.appendChild(wrap); continue;
    }
    const row = document.createElement("div");
    row.className = "ctx-item" + (it.disabled ? " disabled" : "") + (it.danger ? " danger" : "") + (it.submenu ? " has-sub" : "");
    row.setAttribute("role", "menuitem");
    row.innerHTML = `<span class="ctx-ic">${it.icon || ""}</span><span class="ctx-label">${esc(it.label || "")}</span>${it.submenu ? '<span class="ctx-arrow">▸</span>' : ""}`;
    if (!it.disabled) {
      if (it.submenu) {
        let sub = null;
        const openSub = () => {
          if (sub) return;
          sub = buildMenu(it.submenu, depth + 1);
          sub.classList.add("ctx-sub");
          document.body.appendChild(sub);
          const r = row.getBoundingClientRect();
          place(sub, r.right - 3, r.top, r);
        };
        const closeSub = () => { if (sub) { sub.remove(); sub = null; } };
        row.addEventListener("mouseenter", openSub);
        row.addEventListener("mouseleave", (e) => { if (!e.relatedTarget || !sub || !sub.contains(e.relatedTarget)) setTimeout(() => { if (sub && !sub.matches(":hover") && !row.matches(":hover")) closeSub(); }, 160); });
        row.addEventListener("click", (e) => { e.stopPropagation(); openSub(); });
      } else {
        row.addEventListener("click", (e) => { e.stopPropagation(); closeContextMenu(); try { it.onClick?.(e); } catch (err) { console.error(err); } });
      }
    }
    menu.appendChild(row);
  }
  return menu;
}

// Positioniert ein Menü möglichst bei (x,y), klappt aber bei Platzmangel um.
function place(menu, x, y, avoidRect = null) {
  menu.style.visibility = "hidden";
  menu.style.left = "0px"; menu.style.top = "0px";
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  let nx = x, ny = y;
  if (nx + mw > vw - 6) nx = (avoidRect ? avoidRect.left - mw + 3 : x - mw);
  if (nx < 6) nx = 6;
  if (ny + mh > vh - 6) ny = Math.max(6, vh - mh - 6);
  if (ny < 6) ny = 6;
  menu.style.left = nx + "px"; menu.style.top = ny + "px";
  menu.style.visibility = "visible";
}

export function showContextMenu(x, y, items) {
  closeContextMenu();
  if (!items || !items.length) return;
  const menu = buildMenu(items);
  document.body.appendChild(menu);
  place(menu, x, y);
  _open = menu;
  // Erst im nächsten Tick lauschen, damit der auslösende Klick nicht sofort schließt.
  setTimeout(() => {
    document.addEventListener("pointerdown", _outside, true);
    document.addEventListener("keydown", _onKey, true);
    window.addEventListener("blur", closeContextMenu);
    window.addEventListener("resize", closeContextMenu);
    document.addEventListener("scroll", closeContextMenu, true);
  }, 0);
  return menu;
}

export function bindContextMenu(el, builder) {
  const onCtx = (e) => {
    const items = builder(e);
    if (!items) return;
    e.preventDefault();
    e.stopPropagation();
    showContextMenu(e.clientX, e.clientY, items);
  };
  el.addEventListener("contextmenu", onCtx);

  // Touch: langes Drücken öffnet das Menü
  let touchTimer = null, startXY = null;
  const clearT = () => { if (touchTimer) { clearTimeout(touchTimer); touchTimer = null; } };
  const onStart = (e) => {
    if (e.pointerType !== "touch") return;
    const t = e;
    startXY = { x: t.clientX, y: t.clientY };
    clearT();
    touchTimer = setTimeout(() => {
      const items = builder({ clientX: startXY.x, clientY: startXY.y, target: e.target, preventDefault() {}, stopPropagation() {} });
      if (items) { if (navigator.vibrate) try { navigator.vibrate(12); } catch { /* egal */ } showContextMenu(startXY.x, startXY.y, items); }
    }, 480);
  };
  const onMove = (e) => { if (startXY && Math.hypot(e.clientX - startXY.x, e.clientY - startXY.y) > 10) clearT(); };
  el.addEventListener("pointerdown", onStart);
  el.addEventListener("pointermove", onMove);
  el.addEventListener("pointerup", clearT);
  el.addEventListener("pointercancel", clearT);

  return () => {
    el.removeEventListener("contextmenu", onCtx);
    el.removeEventListener("pointerdown", onStart);
    el.removeEventListener("pointermove", onMove);
    el.removeEventListener("pointerup", clearT);
    el.removeEventListener("pointercancel", clearT);
  };
}

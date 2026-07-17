// scriptpicker.js
// ---------------
// Wiederverwendbares Auswahlmenü für gespeicherte Skripte: Suchfeld oben,
// darunter die Skripte nach Ordnern gruppiert. Das Menü wird an document.body
// gehängt und fixed am Button positioniert (an den Viewport geklemmt), damit
// es nie von einem Fenster abgeschnitten wird. Gibt eine detach()-Funktion
// zurück, die das Menü wieder aufräumt (beim Schließen des Fensters aufrufen).

import { esc } from "./utils.js";

export function attachScriptPicker({ button, scripts, onPick }) {
  const menu = document.createElement("div");
  menu.className = "hidden";
  menu.style.cssText = "position:fixed;z-index:9200;width:280px;display:flex;flex-direction:column;" +
    "background:var(--panel);border:1px solid var(--border);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.4)";
  menu.innerHTML = `
    <input type="text" data-sp-search placeholder="🔍 Skript suchen…"
      style="margin:8px;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px" />
    <div data-sp-list style="overflow-y:auto;padding:0 6px 8px"></div>`;
  document.body.appendChild(menu);
  const search = menu.querySelector("[data-sp-search]");
  const list = menu.querySelector("[data-sp-list]");
  let current = typeof scripts === "function" ? [] : (scripts || []);

  function render() {
    const q = (search.value || "").trim().toLowerCase();
    const match = (sc) => !q ||
      sc.name.toLowerCase().includes(q) ||
      (sc.folder || "").toLowerCase().includes(q) ||
      (sc.command || "").toLowerCase().includes(q);
    const filtered = current.filter(match);
    if (!filtered.length) {
      list.innerHTML = `<div style="color:var(--subtext);font-size:12px;padding:6px">Keine Skripte gefunden.</div>`;
      return;
    }
    const groups = new Map();
    for (const sc of filtered) {
      const f = (sc.folder || "").trim();
      if (!groups.has(f)) groups.set(f, []);
      groups.get(f).push(sc);
    }
    const keys = [...groups.keys()].sort((a, b) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b)));
    const item = (sc, indent) => `
      <div data-sp-pick="${esc(sc.id)}" style="padding:5px 8px 5px ${indent}px;border-radius:6px;cursor:pointer;font-size:12px;display:flex;gap:6px;align-items:center"
           onmouseover="this.style.background='var(--panel-2)'" onmouseout="this.style.background=''">
        <span>📜</span><span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(sc.name)}</span>
        <span style="font-size:10px;color:var(--subtext);text-transform:uppercase">${esc(sc.os)}</span>
      </div>`;
    list.innerHTML = keys.map((f) => f === ""
      ? groups.get(f).map((sc) => item(sc, 8)).join("")
      : `<div style="font-size:11px;color:var(--subtext);font-weight:600;padding:6px 4px 2px">📁 ${esc(f)}</div>` +
        groups.get(f).map((sc) => item(sc, 20)).join("")
    ).join("");
    list.querySelectorAll("[data-sp-pick]").forEach((el) =>
      el.addEventListener("click", () => {
        const sc = current.find((x) => x.id === el.dataset.spPick);
        menu.classList.add("hidden");
        if (sc) onPick(sc);
      }));
  }

  function open() {
    if (typeof scripts === "function") current = scripts() || [];
    const r = button.getBoundingClientRect();
    const W = 280;
    let left = Math.min(r.left, window.innerWidth - W - 8);
    left = Math.max(8, left);
    menu.style.left = `${left}px`;
    menu.style.top = `${r.bottom + 4}px`;
    menu.style.maxHeight = `${Math.max(140, Math.min(320, window.innerHeight - r.bottom - 16))}px`;
    search.value = "";
    render();
    menu.classList.remove("hidden");
    search.focus();
  }

  button.addEventListener("click", (e) => {
    e.stopPropagation();
    if (menu.classList.contains("hidden")) open();
    else menu.classList.add("hidden");
  });
  search.addEventListener("input", render);
  search.addEventListener("keydown", (e) => e.stopPropagation());
  menu.addEventListener("click", (e) => e.stopPropagation());

  function onDocClick(e) {
    if (!document.body.contains(button)) { detach(); return; }
    if (!menu.classList.contains("hidden") && !menu.contains(e.target) && e.target !== button) {
      menu.classList.add("hidden");
    }
  }
  document.addEventListener("click", onDocClick);

  function detach() {
    document.removeEventListener("click", onDocClick);
    menu.remove();
  }
  return detach;
}

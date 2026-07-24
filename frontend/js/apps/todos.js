// apps/todos.js
// -------------
// Persönliche Todo-Liste im Stil eines kleinen Notion-Boards.
//   - Todos anlegen, abhaken, bearbeiten, löschen
//   - Kategorien = Wichtigkeits-Spalten (vier feste + eigene, frei sortierbar)
//   - Abgehaktes bleibt in seiner Kategorie und rutscht dort ans Ende
//   - Wiederkehrende Todos (🔁) sind am neuen Tag automatisch wieder offen,
//     inklusive Streak-Zähler
//   - Archiv als eigene Spalte, aus der man Einträge zurückholen kann
// Alles ist privat - der Server filtert strikt auf den eingeloggten Benutzer,
// es gibt bewusst keine Freigaben (Recht: 'use_todos').

import { api } from "../api.js";
import { esc, uiConfirm, uiPrompt } from "../utils.js";
import { registerCleanup } from "../windowmanager.js";

const pad = (n) => String(n).padStart(2, "0");
// Lokales Datum des BROWSERS - niemals toISOString() nehmen, das ist UTC und
// würde den Tages-Reset je nach Zeitzone um Stunden verschieben.
const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};
const fmtDate = (ms) => new Date(ms).toLocaleDateString("de-DE",
  { day: "2-digit", month: "2-digit", year: "2-digit" });

const COLORS = ["#f75c5c", "#f5a524", "#38bdf8", "#2dd4bf", "#a78bfa", "#7f93ad"];

export function renderTodos(body, win) {
  let cats = [];
  let todos = [];
  let day = todayStr();
  let showArchive = false;
  let dragId = null;
  // Merker, damit der Tages-Reset auch bei einem Fenster greift, das über
  // Mitternacht offen bleibt.
  let dayTimer = null;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
      <div class="explorer-toolbar" style="gap:8px;flex-wrap:wrap">
        <input id="todo-new" placeholder="Neues Todo… (Enter)" style="flex:1;min-width:180px;
          padding:6px 10px;border-radius:6px;border:1px solid var(--border);
          background:var(--panel-2);color:var(--text);font-size:13px">
        <select id="todo-new-cat" style="padding:6px 8px;border-radius:6px;
          border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px"></select>
        <label style="display:flex;align-items:center;gap:4px;font-size:12px;color:var(--subtext);cursor:pointer"
               title="Wird jeden Tag automatisch wieder auf offen gesetzt">
          <input type="checkbox" id="todo-new-rec"> 🔁 täglich
        </label>
        <button class="btn-primary" id="todo-add" style="width:auto;margin:0">+ Hinzufügen</button>
        <span style="flex:1"></span>
        <button class="taskbar-btn" id="todo-newcat" title="Eigene Kategorie anlegen">+ Kategorie</button>
        <button class="taskbar-btn" id="todo-sweep" title="Alle erledigten (nicht wiederkehrenden) ins Archiv">🧹 Aufräumen</button>
        <button class="taskbar-btn" id="todo-arch">📦 Archiv</button>
      </div>
      <div id="todo-board" style="flex:1;overflow:auto;padding:10px 12px 16px;
           display:flex;gap:12px;align-items:flex-start"></div>
    </div>`;

  const board = body.querySelector("#todo-board");
  const inputEl = body.querySelector("#todo-new");
  const catSel = body.querySelector("#todo-new-cat");
  const recEl = body.querySelector("#todo-new-rec");

  // ---------------------------------------------------------------
  // Laden
  // ---------------------------------------------------------------

  async function load() {
    day = todayStr();
    board.innerHTML = `<div style="color:var(--subtext);font-size:13px;padding:10px">Lädt…</div>`;
    try {
      const data = await api.getTodos(day);
      cats = data.categories || [];
      todos = data.todos || [];
    } catch (e) {
      board.innerHTML = `<div style="color:var(--danger);padding:10px">${esc(e.message)}</div>`;
      return;
    }
    fillCatSelect();
    draw();
  }

  function fillCatSelect() {
    const keep = catSel.value;
    const options = cats.filter((c) => c.builtin !== "archive");
    catSel.innerHTML = options
      .map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
    if (options.some((c) => c.id === keep)) catSel.value = keep;
    else {
      const mid = options.find((c) => c.builtin === "mid");
      if (mid) catSel.value = mid.id;
    }
  }

  // Fenster über Mitternacht offen? Dann einmal am Tageswechsel neu laden,
  // damit die wiederkehrenden Todos wieder offen dastehen.
  function armDayWatch() {
    clearTimeout(dayTimer);
    const now = new Date();
    const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 30);
    dayTimer = setTimeout(() => { load(); armDayWatch(); }, next - now);
  }

  // ---------------------------------------------------------------
  // Zeichnen
  // ---------------------------------------------------------------

  function draw() {
    const visible = cats.filter((c) => showArchive ? true : c.builtin !== "archive");
    board.innerHTML = visible.map(columnHtml).join("");
    board.querySelectorAll("[data-cat]").forEach(wireColumn);
    board.querySelectorAll("[data-todo]").forEach(wireCard);
    body.querySelector("#todo-arch").textContent = showArchive ? "📦 Archiv aus" : "📦 Archiv";
  }

  function columnHtml(cat) {
    const isArchive = cat.builtin === "archive";
    const items = todos.filter((t) => t.category_id === cat.id);
    const open = items.filter((t) => !t.done).length;
    return `
      <div data-cat="${esc(cat.id)}" style="flex:0 0 280px;display:flex;flex-direction:column;
           background:var(--panel-2);border:1px solid var(--border);border-radius:10px;
           max-height:100%;overflow:hidden">
        <div style="display:flex;align-items:center;gap:8px;padding:9px 11px;
             border-bottom:1px solid var(--border);border-top:3px solid ${esc(cat.color)};
             border-radius:9px 9px 0 0">
          <strong style="font-size:13px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                  title="${esc(cat.name)}">${esc(cat.name)}</strong>
          <span style="font-size:11px;color:var(--subtext)">${isArchive ? items.length : `${open}/${items.length}`}</span>
          ${cat.builtin ? "" : `<button data-editcat="${esc(cat.id)}" class="taskbar-btn"
             style="padding:1px 6px;font-size:11px" title="Kategorie umbenennen/löschen">⋯</button>`}
        </div>
        <div data-drop="${esc(cat.id)}" style="flex:1;overflow:auto;padding:8px;
             display:flex;flex-direction:column;gap:6px;min-height:60px">
          ${items.length ? items.map((t) => cardHtml(t, isArchive)).join("")
            : `<div style="color:var(--subtext);font-size:12px;padding:8px;text-align:center">leer</div>`}
        </div>
      </div>`;
  }

  function cardHtml(t, isArchive) {
    const overdue = t.due_at && !t.done && t.due_at < Date.now();
    return `
      <div data-todo="${esc(t.id)}" draggable="true" style="
           background:var(--panel);border:1px solid var(--border);border-radius:8px;
           padding:7px 9px;display:flex;gap:8px;align-items:flex-start;cursor:grab;
           opacity:${t.done ? "0.55" : "1"}">
        <input type="checkbox" data-check="${esc(t.id)}" ${t.done ? "checked" : ""}
               style="margin-top:2px;cursor:pointer">
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;line-height:1.3;word-break:break-word;
               text-decoration:${t.done ? "line-through" : "none"}">${esc(t.title)}</div>
          ${t.notes ? `<div style="font-size:11px;color:var(--subtext);margin-top:2px;
               white-space:pre-wrap;word-break:break-word">${esc(t.notes)}</div>` : ""}
          <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:3px;font-size:10px;color:var(--subtext)">
            ${t.recurring ? `<span title="Wiederkehrend – morgen wieder offen">🔁 täglich${
              t.streak > 1 ? ` · 🔥 ${t.streak}` : ""}</span>` : ""}
            ${t.due_at ? `<span style="color:${overdue ? "var(--danger)" : "inherit"}"
               title="Fällig">📅 ${fmtDate(t.due_at)}</span>` : ""}
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:2px">
          <button data-edit="${esc(t.id)}" class="taskbar-btn" style="padding:0 5px;font-size:11px"
                  title="Bearbeiten">✏️</button>
          ${isArchive
            ? `<button data-unarch="${esc(t.id)}" class="taskbar-btn" style="padding:0 5px;font-size:11px"
                 title="Zurückholen">↩️</button>`
            : `<button data-arch="${esc(t.id)}" class="taskbar-btn" style="padding:0 5px;font-size:11px"
                 title="Ins Archiv">📦</button>`}
          <button data-del="${esc(t.id)}" class="taskbar-btn" style="padding:0 5px;font-size:11px"
                  title="Löschen">🗑️</button>
        </div>
      </div>`;
  }

  // ---------------------------------------------------------------
  // Interaktion
  // ---------------------------------------------------------------

  function wireCard(el) {
    const id = el.dataset.todo;
    el.addEventListener("dragstart", (e) => {
      dragId = id;
      el.style.opacity = "0.4";
      e.dataTransfer.effectAllowed = "move";
      // Firefox startet ohne gesetzte Daten kein Drag.
      try { e.dataTransfer.setData("text/plain", id); } catch {}
    });
    el.addEventListener("dragend", () => { dragId = null; el.style.opacity = ""; });

    el.querySelector(`[data-check="${CSS.escape(id)}"]`)
      .addEventListener("change", (e) => toggle(id, e.target.checked));
    el.querySelector(`[data-edit="${CSS.escape(id)}"]`)
      .addEventListener("click", () => openEditor(todos.find((t) => t.id === id)));
    const arch = el.querySelector(`[data-arch="${CSS.escape(id)}"]`);
    if (arch) arch.addEventListener("click", () => act(() => api.archiveTodo(id)));
    const un = el.querySelector(`[data-unarch="${CSS.escape(id)}"]`);
    if (un) un.addEventListener("click", () => act(() => api.unarchiveTodo(id)));
    el.querySelector(`[data-del="${CSS.escape(id)}"]`).addEventListener("click", async () => {
      const t = todos.find((x) => x.id === id);
      if (!await uiConfirm(`„${t ? t.title : "Todo"}“ endgültig löschen?`)) return;
      act(() => api.deleteTodo(id));
    });
  }

  function wireColumn(col) {
    const catId = col.dataset.cat;
    const drop = col.querySelector("[data-drop]");
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.style.background = "rgba(var(--accent-rgb),0.07)";
    });
    drop.addEventListener("dragleave", () => { drop.style.background = ""; });
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.style.background = "";
      const id = dragId || e.dataTransfer.getData("text/plain");
      if (!id) return;
      const t = todos.find((x) => x.id === id);
      if (!t || t.category_id === catId) return;
      // Neue Reihenfolge der Zielspalte: offene zuerst, Karte ans Ende der offenen.
      const rest = todos.filter((x) => x.category_id === catId && x.id !== id);
      const order = [...rest.filter((x) => !x.done).map((x) => x.id), id,
                     ...rest.filter((x) => x.done).map((x) => x.id)];
      act(() => api.moveTodo(id, catId, order));
    });

    const editBtn = col.querySelector("[data-editcat]");
    if (editBtn) editBtn.addEventListener("click", () => editCategory(catId));
  }

  async function toggle(id, done) {
    const t = todos.find((x) => x.id === id);
    if (t) { t.done = done ? 1 : 0; draw(); }     // sofortiges Feedback
    try {
      await api.toggleTodo(id, done, todayStr());
    } catch (e) {
      alert(e.message);
    }
    load();
  }

  async function act(fn) {
    try { await fn(); } catch (e) { alert(e.message); }
    load();
  }

  // ---------------------------------------------------------------
  // Anlegen / Bearbeiten
  // ---------------------------------------------------------------

  async function addTodo() {
    const title = inputEl.value.trim();
    if (!title) return;
    inputEl.value = "";
    const recurring = recEl.checked;
    recEl.checked = false;
    await act(() => api.createTodo(
      { title, category_id: catSel.value || null, recurring }, todayStr()));
    inputEl.focus();
  }

  body.querySelector("#todo-add").addEventListener("click", addTodo);
  inputEl.addEventListener("keydown", (e) => { if (e.key === "Enter") addTodo(); });

  body.querySelector("#todo-arch").addEventListener("click", () => {
    showArchive = !showArchive;
    draw();
  });

  body.querySelector("#todo-sweep").addEventListener("click", async () => {
    if (!await uiConfirm("Alle erledigten Todos ins Archiv verschieben?",
                         { description: "Wiederkehrende Todos bleiben stehen." })) return;
    act(() => api.archiveDoneTodos());
  });

  body.querySelector("#todo-newcat").addEventListener("click", async () => {
    const name = await uiPrompt("Name der neuen Kategorie");
    if (!name || !name.trim()) return;
    const color = COLORS[cats.length % COLORS.length];
    act(() => api.createTodoCategory({ name: name.trim(), color }));
  });

  async function editCategory(catId) {
    const cat = cats.find((c) => c.id === catId);
    if (!cat) return;
    const name = await uiPrompt("Kategorie umbenennen (leer = löschen)",
                                { value: cat.name });
    if (name === null) return;
    if (!name.trim()) {
      if (!await uiConfirm(`Kategorie „${cat.name}“ löschen?`,
                           { description: "Enthaltene Todos wandern ins Archiv.", danger: true })) return;
      return act(() => api.deleteTodoCategory(catId));
    }
    act(() => api.updateTodoCategory(catId, { name: name.trim(), color: cat.color }));
  }

  function openEditor(todo) {
    if (!todo) return;
    const overlay = document.createElement("div");
    overlay.style.cssText = `position:absolute;inset:0;background:rgba(0,0,0,0.45);
      display:flex;align-items:center;justify-content:center;z-index:50`;
    overlay.innerHTML = `
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;
           padding:14px;width:min(380px,92%);display:flex;flex-direction:column;gap:9px">
        <strong style="font-size:13px">Todo bearbeiten</strong>
        <input id="ed-title" value="${esc(todo.title)}" style="padding:6px 9px;border-radius:6px;
          border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:13px">
        <textarea id="ed-notes" rows="4" placeholder="Notiz…" style="padding:6px 9px;border-radius:6px;
          border:1px solid var(--border);background:var(--panel-2);color:var(--text);
          font-size:12px;resize:vertical">${esc(todo.notes || "")}</textarea>
        <label style="font-size:11px;color:var(--subtext)">Kategorie</label>
        <select id="ed-cat" style="padding:6px 8px;border-radius:6px;border:1px solid var(--border);
          background:var(--panel-2);color:var(--text);font-size:12px">
          ${cats.map((c) => `<option value="${esc(c.id)}" ${c.id === todo.category_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}
        </select>
        <label style="font-size:11px;color:var(--subtext)">Fällig am (optional)</label>
        <input id="ed-due" type="date" value="${todo.due_at ? isoDate(todo.due_at) : ""}"
          style="padding:6px 9px;border-radius:6px;border:1px solid var(--border);
          background:var(--panel-2);color:var(--text);font-size:12px">
        <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer">
          <input type="checkbox" id="ed-rec" ${todo.recurring ? "checked" : ""}>
          🔁 wiederkehrend (jeden Tag neu abhaken)
        </label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:4px">
          <button class="taskbar-btn" id="ed-cancel">Abbrechen</button>
          <button class="btn-primary" id="ed-save" style="width:auto;margin:0">Speichern</button>
        </div>
      </div>`;
    body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    overlay.querySelector("#ed-cancel").addEventListener("click", close);
    overlay.querySelector("#ed-save").addEventListener("click", async () => {
      const title = overlay.querySelector("#ed-title").value.trim();
      if (!title) return;
      const dueRaw = overlay.querySelector("#ed-due").value;
      close();
      act(() => api.updateTodo(todo.id, {
        title,
        notes: overlay.querySelector("#ed-notes").value,
        category_id: overlay.querySelector("#ed-cat").value,
        recurring: overlay.querySelector("#ed-rec").checked,
        // Mittag als Uhrzeit, damit Zeitzonen-Verschiebungen den Tag nicht kippen.
        due_at: dueRaw ? new Date(`${dueRaw}T12:00:00`).getTime() : null,
      }));
    });
    overlay.querySelector("#ed-title").focus();
  }

  const isoDate = (ms) => {
    const d = new Date(ms);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  };

  // Tageswechsel-Timer aufräumen, wenn das Fenster geschlossen wird.
  if (win?.key) registerCleanup(win.key, () => clearTimeout(dayTimer));

  armDayWatch();
  load();
}

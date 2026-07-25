// notifycenter.js
// ---------------
// Benachrichtigungs-Zentrale: sammelt ALLE Benachrichtigungen (Toasts aus
// notify.js UND Server-/Webhook-Meldungen über den Socket) in localStorage
// und zeigt sie in einem eigenen Fenster (erreichbar über das Benutzermenü).
//
// Verhalten (wie gewünscht):
//   - Toast oben in der Mitte gehovert oder weggeXt/interagiert -> gelesen.
//   - Fenster: Umschalter "Nur neue" / "Alle anzeigen".
//     "Alle anzeigen" zeigt auch die bereits gelesenen; gelesene Einträge
//     haben eine Lösch-Option, plus "Alle gelesenen löschen".
//   - Buttons "Als gelesen markieren" (pro Eintrag) und "Alle als gelesen".
//   - Punkt am Benutzermenü-Button, solange ungelesene Meldungen da sind -
//     Farbe nach schwerster ungelesener Stufe (rot=error, orange=warn,
//     blau=info, grün=success).

import { esc, uiConfirm } from "./utils.js";
import { t } from "./i18n.js";

const STORE_KEY = "rmm_notifications";
const MAX_ENTRIES = 300;

const LEVEL_COLORS = {
  error: "#ff4d6d", warn: "#f5a524", info: "#4da6ff", success: "#3ecf8e",
};
// Schwere-Reihenfolge für die Punkt-Farbe (schwerste ungelesene gewinnt).
const SEVERITY = ["error", "warn", "info", "success"];

let _uid = 0;

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch { return []; }
}
function save(list) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(list.slice(0, MAX_ENTRIES))); } catch {}
  emitChanged();
}
function emitChanged() {
  window.dispatchEvent(new CustomEvent("rmm:notifications-changed"));
}

// Neue Benachrichtigung aufnehmen. Gibt die id zurück (damit der Toast sie
// beim Hover/Schließen als gelesen markieren kann).
export function recordNotification(message, level = "info", source = "app") {
  const list = load();
  const id = `n-${Date.now().toString(36)}-${(_uid++).toString(36)}`;
  list.unshift({
    id, ts: Date.now(),
    level: LEVEL_COLORS[level] ? level : "info",
    message: String(message == null ? "" : message),
    source, read: false,
  });
  save(list);
  return id;
}

export function markRead(id) {
  const list = load();
  const n = list.find((x) => x.id === id);
  if (n && !n.read) { n.read = true; save(list); }
}
export function markAllRead() {
  const list = load();
  let changed = false;
  for (const n of list) { if (!n.read) { n.read = true; changed = true; } }
  if (changed) save(list);
}
export function deleteNotification(id) {
  save(load().filter((x) => x.id !== id));
}
export function deleteAllRead() {
  save(load().filter((x) => !x.read));
}

// Für den Punkt am Benutzermenü-Button: Anzahl + schwerste ungelesene Stufe.
export function unreadInfo() {
  const unread = load().filter((x) => !x.read);
  if (!unread.length) return { count: 0, level: null, color: null };
  let level = "success";
  for (const n of unread) {
    if (SEVERITY.indexOf(n.level) < SEVERITY.indexOf(level)) level = n.level;
  }
  return { count: unread.length, level, color: LEVEL_COLORS[level] };
}

// Punkt-Element am Benutzermenü-Button (und optional weiteren Zielen) pflegen.
// Wird von app.js einmal aufgerufen; aktualisiert sich bei jeder Änderung.
export function attachUnreadDot(targetEl) {
  if (!targetEl) return;
  let dot = targetEl.querySelector(".notify-unread-dot");
  if (!dot) {
    dot = document.createElement("span");
    dot.className = "notify-unread-dot";
    dot.style.cssText = `display:none;width:9px;height:9px;border-radius:50%;
      margin-left:6px;vertical-align:middle;box-shadow:0 0 6px currentColor;`;
    targetEl.appendChild(dot);
  }
  const update = () => {
    const info = unreadInfo();
    if (info.count > 0) {
      dot.style.display = "inline-block";
      dot.style.background = info.color;
      dot.style.color = info.color;
      dot.title = `${info.count} neue Benachrichtigung(en)`;
    } else {
      dot.style.display = "none";
    }
  };
  window.addEventListener("rmm:notifications-changed", update);
  update();
}

function fmtTime(ts) {
  const d = new Date(ts);
  return d.toLocaleDateString() + " " + d.toLocaleTimeString();
}

// ------------------------------------------------------------------
// Fenster "Benachrichtigungen" (appId: notifycenter)
//
// WICHTIG: Es wird NIE die ganze Liste neu gebaut. Jede Änderung (gelesen,
// gelöscht, neue Meldung) wird per Reconcile nur auf die betroffene Zeile
// angewendet - Scroll-Position und alles Drumherum bleiben exakt stehen.
// ------------------------------------------------------------------
export function renderNotifyCenter(body, win) {
  let showAll = false;   // false = nur neue, true = alle anzeigen

  // ----- Grundgerüst EINMAL bauen (Header + Listen-Container) -----
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div style="display:flex;gap:8px;align-items:center;padding:10px 12px;border-bottom:1px solid var(--border);flex-wrap:wrap">
        <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:pointer" title="${t("u_ja_alle_meldungen_auch_gelesene_mi")}">
          <input type="checkbox" id="nc-showall" />
          Alles anzeigen <span id="nc-showall-state" style="color:var(--subtext)">(nein)</span>
        </label>
        <span id="nc-counts" style="font-size:11px;color:var(--subtext)"></span>
        <span style="flex:1"></span>
        <button class="taskbar-btn" id="nc-markall">✓ Alle als gelesen</button>
        <button class="taskbar-btn" id="nc-delread" style="border-color:var(--danger);color:var(--danger);display:none">🗑 Alle gelesenen löschen</button>
      </div>
      <div id="nc-list" style="flex:1;overflow:auto;padding:8px 10px;display:flex;flex-direction:column;gap:8px"></div>
      <div id="nc-empty" style="display:none;color:var(--subtext);font-size:13px;padding:14px;text-align:center"></div>
    </div>
  `;
  const listEl = body.querySelector("#nc-list");
  const emptyEl = body.querySelector("#nc-empty");
  const countsEl = body.querySelector("#nc-counts");
  const markAllBtn = body.querySelector("#nc-markall");
  const delReadBtn = body.querySelector("#nc-delread");
  const showAllChk = body.querySelector("#nc-showall");
  const showAllState = body.querySelector("#nc-showall-state");

  function buildRow(n) {
    const color = LEVEL_COLORS[n.level] || LEVEL_COLORS.info;
    const row = document.createElement("div");
    row.className = "panel";
    row.dataset.nid = n.id;
    row.dataset.read = n.read ? "1" : "0";
    row.style.cssText = `display:flex;gap:10px;align-items:flex-start;padding:10px 12px;
      border-left:3px solid ${color};${n.read ? "opacity:0.65" : ""}`;
    row.innerHTML = `
      <span style="width:9px;height:9px;border-radius:50%;background:${color};margin-top:4px;flex-shrink:0;${n.read ? "opacity:0.4" : `box-shadow:0 0 6px ${color}`}"></span>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;white-space:pre-wrap;word-break:break-word">${esc(n.message)}</div>
        <div style="font-size:11px;color:var(--subtext);margin-top:3px">
          ${fmtTime(n.ts)} · ${esc(n.level)} · ${n.source === "webhook" ? "Server/Webhook" : "App"} ${n.read ? "· gelesen" : ""}
        </div>
      </div>
      <div style="display:flex;gap:4px;flex-shrink:0">
        ${!n.read ? `<button class="taskbar-btn" data-read="${n.id}" title="Als gelesen markieren" style="padding:2px 7px;font-size:11px">✓</button>` : ""}
        ${n.read ? `<button class="taskbar-btn" data-del="${n.id}" title=t("delete") style="padding:2px 7px;font-size:11px;border-color:var(--danger);color:var(--danger)">🗑</button>` : ""}
      </div>
    `;
    row.querySelector("[data-read]")?.addEventListener("click", () => markRead(n.id));
    row.querySelector("[data-del]")?.addEventListener("click", () => deleteNotification(n.id));
    return row;
  }

  // ----- Reconcile: nur die betroffenen Zeilen einfügen/ersetzen/entfernen -----
  function reconcile() {
    const list = load();
    const shown = showAll ? list : list.filter((n) => !n.read);
    const unread = list.filter((n) => !n.read).length;

    // Header-Werte in-place aktualisieren (kein Neuaufbau).
    countsEl.textContent = `${unread} neu · ${list.length} gesamt`;
    markAllBtn.disabled = unread === 0;
    delReadBtn.style.display = showAll ? "" : "none";
    showAllState.textContent = showAll ? "(ja)" : "(nein)";
    showAllChk.checked = showAll;

    const wantIds = shown.map((n) => n.id);
    const wantSet = new Set(wantIds);

    // 1) Zeilen entfernen, die nicht mehr angezeigt werden (gelöscht bzw.
    //    im "Nur neue"-Modus gerade gelesen).
    for (const row of [...listEl.children]) {
      if (!wantSet.has(row.dataset.nid)) row.remove();
    }
    // 2) Zeilen einfügen/aktualisieren - Reihenfolge = Datenreihenfolge.
    shown.forEach((n, idx) => {
      const existing = listEl.querySelector(`[data-nid="${n.id}"]`);
      const refNode = listEl.children[idx] || null;
      if (!existing) {
        listEl.insertBefore(buildRow(n), refNode);
      } else {
        // Nur ersetzen, wenn sich der Lese-Zustand geändert hat.
        if (existing.dataset.read !== (n.read ? "1" : "0")) {
          listEl.replaceChild(buildRow(n), existing);
        } else if (existing !== refNode) {
          listEl.insertBefore(existing, refNode);
        }
      }
    });

    emptyEl.style.display = shown.length ? "none" : "";
    emptyEl.textContent = showAll ? "Keine Benachrichtigungen vorhanden." : "Keine neuen Benachrichtigungen. 🎉";
  }

  showAllChk.addEventListener("change", () => { showAll = showAllChk.checked; reconcile(); });
  markAllBtn.addEventListener("click", () => { markAllRead(); });
  delReadBtn.addEventListener("click", async () => {
    const ok = await uiConfirm(t("u_alle_gelesenen_benachrichtigungen_"), {
      okText: t("delete"), danger: true });
    if (ok) deleteAllRead();
  });

  // Jede Datenänderung (neuer Toast, gelesen, gelöscht) -> Reconcile.
  // Scroll-Position bleibt erhalten, weil der Listen-Container nie neu
  // gebaut wird - es ändern sich ausschließlich die betroffenen Zeilen.
  const onChanged = () => {
    if (!document.body.contains(body)) {
      window.removeEventListener("rmm:notifications-changed", onChanged);
      return;
    }
    reconcile();
  };
  window.addEventListener("rmm:notifications-changed", onChanged);

  reconcile();
}

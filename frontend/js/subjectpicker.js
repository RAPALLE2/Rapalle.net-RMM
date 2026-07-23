// subjectpicker.js
// ----------------
// Gemeinsame Auswahl-Liste für "für bestimmte …": Benutzer UND Gruppen.
//
// Wichtig für die Übersicht: UNVERWALTETE AD-Gruppen (aus dem Verzeichnis
// importiert, aber im RMM nicht gepflegt) landen in einem eigenen,
// zugeklappten Ordner. Sonst gehen die tatsächlich genutzten Gruppen in
// hunderten AD-Einträgen unter.
//
// Verwendung:
//   subjectPickerHtml({ users, groups }, selected, { name: "np" })
//   readSubjectPicker(rootEl)  ->  [{type:"user"|"group", id}]
//   initSubjectPicker(rootEl)  (Ordner auf-/zuklappen + Suche)

import { esc } from "./utils.js";

// Ist eine Gruppe "unverwaltet"? Entweder ausdrücklich markiert oder eine
// AD-Gruppe, die im RMM keinerlei Rechte besitzt.
export function isUnmanagedGroup(g) {
  if (g.unmanaged) return true;
  if (!g.is_ad_group) return false;
  const perms = g.permissions;
  if (Array.isArray(perms)) return perms.length === 0;
  if (typeof perms === "string") return perms.trim() === "";
  return false;   // unbekannt -> als verwaltet behandeln
}

/** Teilt Gruppen in "normal" und "unverwaltete AD-Gruppen". */
export function splitGroups(groups = []) {
  const managed = [], unmanaged = [];
  for (const g of groups) (isUnmanagedGroup(g) ? unmanaged : managed).push(g);
  const byName = (a, b) => String(a.name || "").localeCompare(String(b.name || ""));
  return { managed: managed.sort(byName), unmanaged: unmanaged.sort(byName) };
}

const key = (type, id) => `${type}:${id}`;

function selectedSet(selected = []) {
  const set = new Set();
  for (const s of selected) {
    if (typeof s === "string") set.add(key("user", s));           // Altbestand
    else if (s && s.id) set.add(key(s.type || "user", s.id));
  }
  return set;
}

function row(type, id, label, icon, checked, name) {
  return `<label class="sp-row" data-label="${esc(String(label).toLowerCase())}"
      style="display:flex;gap:7px;align-items:center;font-size:12.5px;padding:2px 0;cursor:pointer">
    <input type="checkbox" class="sp-item" name="${esc(name)}"
      value="${esc(key(type, id))}" ${checked ? "checked" : ""} />
    <span>${icon} ${esc(label)}</span>
  </label>`;
}

/**
 * @param data     {users:[{id,username}], groups:[{id,name,is_ad_group,unmanaged}]}
 * @param selected Liste aus {type,id} (oder Benutzer-IDs als String)
 * @param opts     {name} - name-Attribut der Checkboxen
 */
export function subjectPickerHtml(data, selected = [], opts = {}) {
  const name = opts.name || "sp";
  const users = data?.users || [];
  const { managed, unmanaged } = splitGroups(data?.groups || []);
  const sel = selectedSet(selected);
  const anyUnmanagedSelected = unmanaged.some((g) => sel.has(key("group", g.id)));

  const usersHtml = users.length
    ? users.map((u) => row("user", u.id, u.username, "👤", sel.has(key("user", u.id)), name)).join("")
    : `<div style="font-size:11.5px;color:var(--subtext)">Keine weiteren Benutzer.</div>`;

  const groupsHtml = managed.length
    ? managed.map((g) => row("group", g.id, g.name, g.is_ad_group ? "🏢" : "👥",
        sel.has(key("group", g.id)), name)).join("")
    : `<div style="font-size:11.5px;color:var(--subtext)">Keine Gruppen.</div>`;

  // Unverwaltete AD-Gruppen: eigener Ordner, standardmäßig zu (außer es ist
  // bereits eine davon ausgewählt - dann offen, damit man sie sieht).
  const unmanagedHtml = unmanaged.length ? `
    <details class="sp-folder" ${anyUnmanagedSelected ? "open" : ""}
             style="margin-top:6px;border:1px dashed var(--border);border-radius:7px;padding:4px 7px">
      <summary style="cursor:pointer;font-size:12px;color:var(--subtext);list-style:none">
        📂 Unverwaltete AD-Gruppen (${unmanaged.length})
        <span style="font-size:11px">– aus dem Verzeichnis, im RMM ohne Rechte</span>
      </summary>
      <div style="margin-top:4px">
        ${unmanaged.map((g) => row("group", g.id, g.name, "🏢",
            sel.has(key("group", g.id)), name)).join("")}
      </div>
    </details>` : "";

  return `
    <div class="subject-picker" style="display:flex;flex-direction:column;gap:4px">
      <input type="text" class="sp-search" placeholder="Suchen…"
             style="font-size:12px;padding:3px 7px" />
      <div class="sp-scroll" style="max-height:170px;overflow:auto;border:1px solid var(--border);
           border-radius:7px;padding:5px 7px">
        <div class="sp-section-title" style="font-size:11px;color:var(--subtext);margin:1px 0 2px">Benutzer</div>
        ${usersHtml}
        <div class="sp-section-title" style="font-size:11px;color:var(--subtext);margin:7px 0 2px">Gruppen</div>
        ${groupsHtml}
        ${unmanagedHtml}
      </div>
    </div>`;
}

/** Liest die Auswahl als [{type, id}] aus. */
export function readSubjectPicker(rootEl) {
  if (!rootEl) return [];
  return [...rootEl.querySelectorAll(".sp-item:checked")].map((i) => {
    const [type, ...rest] = i.value.split(":");
    return { type, id: rest.join(":") };
  });
}

/** Suche verdrahten (Ordner klappen von selbst über <details>). */
export function initSubjectPicker(rootEl) {
  if (!rootEl) return;
  const search = rootEl.querySelector(".sp-search");
  search?.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    rootEl.querySelectorAll(".sp-row").forEach((r) => {
      r.style.display = !q || (r.dataset.label || "").includes(q) ? "" : "none";
    });
    // Bei aktiver Suche den AD-Ordner öffnen, damit Treffer sichtbar sind.
    rootEl.querySelectorAll(".sp-folder").forEach((f) => { if (q) f.open = true; });
    // Abschnitts-Überschriften ausblenden, wenn darunter nichts übrig ist.
    rootEl.querySelectorAll(".sp-section-title").forEach((t) => {
      let el = t.nextElementSibling, visible = false;
      while (el && !el.classList.contains("sp-section-title")) {
        if (el.classList.contains("sp-row") && el.style.display !== "none") { visible = true; break; }
        if (el.classList.contains("sp-folder")) { visible = true; break; }
        el = el.nextElementSibling;
      }
      t.style.display = !q || visible ? "" : "none";
    });
  });
}

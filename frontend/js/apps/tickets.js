// apps/tickets.js
// ---------------
// Ticket-System: Liste + Detail. Funktionen (je nach Recht):
//   ticket_read    - Tickets sehen (nur Admins/Ersteller/Zugewiesene, Server filtert)
//   ticket_create  - neue Tickets anlegen
//   ticket_edit    - Titel/Beschreibung/Priorität/Fälligkeit/Clients ändern
//   ticket_comment - kommentieren + Dateien/Screenshots anhängen
//   ticket_assign  - Benutzer/Gruppen zuweisen
//   ticket_resolve - Status ändern / als gelöst markieren
//   ticket_delete  - Tickets löschen
import { api } from "../api.js";
import { state, isAdmin, hasGlobalPerm } from "../state.js";
import { esc, uiConfirm } from "../utils.js";
import { subjectPickerHtml, readSubjectPicker, initSubjectPicker, splitGroups } from "../subjectpicker.js";
// t() unter Alias: in dieser Datei ist "t" bereits als lokaler
// Variablenname belegt (Tenant/Target/Trigger/Token o.ä.).
import { t as tr } from "../i18n.js";
import { condenseHints } from "../help.js";

const STATUS = {
  open:        { label: "Offen",         color: "#4da6ff" },
  in_progress: { label: "In Bearbeitung", color: "#ffd166" },
  resolved:    { label: tr("u_gelost"),        color: "#3ecf8e" },
  closed:      { label: "Geschlossen",   color: "#8892a6" },
};
const PRIO = {
  low:      { label: "Niedrig",  color: "#8892a6" },
  normal:   { label: "Normal",   color: "#4da6ff" },
  high:     { label: "Hoch",     color: "#ffd166" },
  critical: { label: "Kritisch", color: "#ff4d6d" },
};

const fmtDate = (ms) => ms ? new Date(ms).toLocaleString("de-DE",
  { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "–";
const fmtDay = (ms) => ms ? new Date(ms).toLocaleDateString("de-DE") : "–";

// Sichtbarkeit von Kommentaren - identisch zu den Client-Notizen.
const COMMENT_VIS = {
  all: { icon: "🌍", label: "für alle" },
  private: { icon: "🔒", label: "nur für mich" },
  custom: { icon: "👥", label: "für bestimmte" },
};

export function renderTickets(body, win) {
  // Erklaertexte dieser Seite in "?"-Symbole umwandeln - einmal direkt nach
  // dem Zeichnen und einmal verzoegert fuer nachgeladene Bereiche.
  setTimeout(() => condenseHints(body), 0);
  setTimeout(() => condenseHints(body), 400);

  const may = (p) => isAdmin() || hasGlobalPerm(p);
  let tickets = [];
  let subjects = { users: [], groups: [] };
  let ticketUsers = { users: [], groups: [] };   // Auswahl für "für bestimmte"
  // Anzeigename einer Freigabe ({type,id} oder alte reine Benutzer-ID)
  const shareName = (item) => {
    const type = typeof item === "string" ? "user" : (item.type || "user");
    const id = typeof item === "string" ? item : item.id;
    if (type === "group") {
      const g = (ticketUsers.groups || []).find((x) => x.id === id);
      return g ? `👥 ${g.name}` : "👥 ?";
    }
    const u = (ticketUsers.users || []).find((x) => x.id === id);
    return u ? u.username : id;
  };
  let currentId = null;
  let statusFilter = "active";   // 'active' | 'all' | einzelner Status

  body.innerHTML = `
    <div style="display:flex;height:100%;background:var(--panel);position:relative">
      <div style="width:300px;min-width:300px;border-right:1px solid var(--border);display:flex;flex-direction:column">
        <div style="padding:8px 10px;border-bottom:1px solid var(--border);display:flex;gap:6px;align-items:center">
          <select id="tk-filter" style="font-size:12px">
            <option value="active">Aktive</option>
            <option value="all">Alle</option>
            <option value="open">Offen</option>
            <option value="in_progress">In Bearbeitung</option>
            <option value="resolved">Gelöst</option>
            <option value="closed">Geschlossen</option>
          </select>
          <span style="flex:1"></span>
          ${may("ticket_create") ? `<button class="btn-primary" id="tk-new" style="margin:0;width:auto;font-size:12px">＋ Ticket</button>` : ""}
        </div>
        <div id="tk-list" style="flex:1;overflow:auto"></div>
      </div>
      <div id="tk-detail" style="flex:1;overflow:auto;min-width:0"></div>
      <div id="tk-editor" style="display:none;position:absolute;inset:0;background:rgba(0,0,0,0.55);align-items:center;justify-content:center;z-index:5"></div>
    </div>
  `;
  const listEl = body.querySelector("#tk-list");
  const detailEl = body.querySelector("#tk-detail");
  const editorEl = body.querySelector("#tk-editor");

  body.querySelector("#tk-filter").addEventListener("change", (e) => {
    statusFilter = e.target.value; drawList();
  });
  body.querySelector("#tk-new")?.addEventListener("click", () => openEditor(null));

  const subjectLabel = (a) => {
    const list = a.subject_type === "user" ? subjects.users : subjects.groups;
    const hit = list.find((s) => s.id === a.subject_id);
    return (a.subject_type === "group" ? "👥 " : "👤 ") + (hit ? hit.label : a.subject_id);
  };
  const clientLabel = (cid) => {
    const c = state.clients.find((x) => x.id === cid);
    return c ? `🖥️ ${c.hostname}` : `🖥️ ${cid.slice(0, 8)}…`;
  };

  // ---------------- Liste ----------------
  function drawList() {
    let rows = tickets;
    if (statusFilter === "active") rows = rows.filter((t) => t.status === "open" || t.status === "in_progress");
    else if (statusFilter !== "all") rows = rows.filter((t) => t.status === statusFilter);
    if (!rows.length) {
      listEl.innerHTML = `<div style="padding:14px;color:var(--subtext);font-size:12px">Keine Tickets in dieser Ansicht.</div>`;
      return;
    }
    listEl.innerHTML = rows.map((t) => {
      const st = STATUS[t.status] || STATUS.open;
      const pr = PRIO[t.priority] || PRIO.normal;
      const overdue = t.due_date && t.due_date < Date.now() && t.status !== "resolved" && t.status !== "closed";
      return `
      <div data-id="${esc(t.id)}" style="padding:9px 10px;border-bottom:1px solid var(--border);cursor:pointer;
           background:${t.id === currentId ? "var(--panel-2)" : "transparent"}">
        <div style="font-weight:600;font-size:13px;display:flex;gap:6px;align-items:center">
          <span style="width:8px;height:8px;border-radius:50%;background:${pr.color};flex:none" title="Priorität: ${pr.label}"></span>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.title)}</span>
        </div>
        <div style="display:flex;gap:8px;font-size:11px;color:var(--subtext);margin-top:2px;align-items:center">
          <span style="color:${st.color}">● ${st.label}</span>
          <span>${fmtDay(t.created_at)}</span>
          ${overdue ? `<span style="color:#ff4d6d">⏰ überfällig</span>` : t.due_date ? `<span>⏰ ${fmtDay(t.due_date)}</span>` : ""}
          ${t.assignees?.length ? `<span>👥 ${t.assignees.length}</span>` : ""}
        </div>
      </div>`;
    }).join("");
    listEl.querySelectorAll("[data-id]").forEach((el) =>
      el.addEventListener("click", () => selectTicket(el.dataset.id)));
  }

  async function selectTicket(id) {
    currentId = id;
    drawList();
    detailEl.innerHTML = `<div style="padding:16px;color:var(--subtext)">Lade…</div>`;
    try {
      const t = await api.ticket(id);
      drawDetail(t);
    } catch (e) {
      detailEl.innerHTML = `<div style="padding:16px;color:var(--danger)">${esc(e.message)}</div>`;
    }
  }

  // ---------------- Detail ----------------
  function drawDetail(t) {
    const st = STATUS[t.status] || STATUS.open;
    const pr = PRIO[t.priority] || PRIO.normal;
    detailEl.innerHTML = `
      <div style="padding:16px 18px">
        <div style="display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap">
          <h2 style="margin:0;flex:1;min-width:200px">${esc(t.title)}</h2>
          ${may("ticket_edit") ? `<button class="taskbar-btn" id="tkd-edit" style="font-size:12px">✏️ Bearbeiten</button>` : ""}
          ${may("ticket_delete") ? `<button class="taskbar-btn" id="tkd-del" style="font-size:12px">🗑 Löschen</button>` : ""}
        </div>
        <div style="display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--subtext);margin:8px 0 12px">
          <span style="color:${st.color}">● ${st.label}</span>
          <span style="color:${pr.color}">Priorität: ${pr.label}</span>
          <span>Erstellt von <b>${esc(t.created_by)}</b> am ${fmtDate(t.created_at)}</span>
          <span>Aktualisiert: ${fmtDate(t.updated_at)}</span>
          <span>Fällig: <b>${fmtDay(t.due_date)}</b></span>
          ${t.status === "resolved" ? `<span style="color:#3ecf8e">Gelöst von ${esc(t.resolved_by || "?")} am ${fmtDate(t.resolved_at)}</span>` : ""}
        </div>

        ${may("ticket_resolve") ? `
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">
          ${t.status !== "in_progress" && t.status !== "resolved" && t.status !== "closed"
            ? `<button class="taskbar-btn" data-status="in_progress" style="font-size:12px">▶ In Bearbeitung</button>` : ""}
          ${t.status !== "resolved" && t.status !== "closed"
            ? `<button class="btn-primary" data-status="resolved" style="margin:0;width:auto;font-size:12px">✓ Als gelöst markieren</button>` : ""}
          ${t.status === "resolved"
            ? `<button class="taskbar-btn" data-status="closed" style="font-size:12px">Schließen</button>` : ""}
          ${(t.status === "resolved" || t.status === "closed")
            ? `<button class="taskbar-btn" data-status="open" style="font-size:12px">↩ Wieder öffnen</button>` : ""}
        </div>` : ""}

        <h3 style="margin:0 0 6px">Beschreibung</h3>
        <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:10px 12px;
             font-size:13px;white-space:pre-wrap;word-break:break-word">${esc(t.description) || "<i style='color:var(--subtext)'>Keine Beschreibung</i>"}</div>

        <h3 style="margin:16px 0 6px">Zugewiesen an
          ${may("ticket_assign") ? `<button class="taskbar-btn" id="tkd-assign" style="font-size:11px;margin-left:6px">Zuweisen…</button>` : ""}
        </h3>
        <div style="display:flex;gap:6px;flex-wrap:wrap;font-size:12px">
          ${t.assignees.length
            ? t.assignees.map((a) => `<span style="background:var(--panel-2);border:1px solid var(--border);border-radius:20px;padding:3px 10px">${esc(subjectLabel(a))}</span>`).join("")
            : `<span style="color:var(--subtext)">Niemand zugewiesen (nur Admins + Ersteller sehen dieses Ticket)</span>`}
        </div>

        <h3 style="margin:16px 0 6px">Verknüpfte Clients</h3>
        <div style="display:flex;gap:6px;flex-wrap:wrap;font-size:12px">
          ${t.clients.length
            ? t.clients.map((cid) => `<span style="background:var(--panel-2);border:1px solid var(--border);border-radius:20px;padding:3px 10px">${esc(clientLabel(cid))}</span>`).join("")
            : `<span style="color:var(--subtext)">Keine Clients verknüpft</span>`}
        </div>

        <h3 style="margin:16px 0 6px">Anhänge / Screenshots
          ${may("ticket_comment") ? `<button class="taskbar-btn" id="tkd-upload" style="font-size:11px;margin-left:6px">📎 Hochladen…</button>
            <input type="file" id="tkd-file" hidden />` : ""}
        </h3>
        <div id="tkd-files" style="display:flex;gap:10px;flex-wrap:wrap"></div>

        <h3 style="margin:16px 0 6px">Kommentare (${t.comments.length})
          <button class="taskbar-btn" id="tkd-activity" style="font-size:11px;margin-left:6px">🕓 Verlauf</button>
        </h3>
        <div id="tkd-activity-box" style="display:none;margin-bottom:10px"></div>
        <div style="display:flex;flex-direction:column;gap:8px">
          ${t.comments.map((c) => {
            const v = COMMENT_VIS[c.visibility] || COMMENT_VIS.all;
            const shared = c.visibility === "custom" && (c.shared_with || []).length
              ? ` (${c.shared_with.map(shareName).map(esc).join(", ")})` : "";
            return `
            <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px 12px;font-size:13px">
              <div style="font-size:11px;color:var(--subtext);margin-bottom:3px;display:flex;justify-content:space-between;gap:8px">
                <span><b>${esc(c.author)}</b> · ${fmtDate(c.created_at)}
                  · <span title="Sichtbarkeit">${v.icon} ${esc(v.label)}${shared}</span></span>
                ${c.can_edit ? `<button class="taskbar-btn" data-cdel="${esc(c.id)}"
                  style="padding:0 5px;font-size:10px;border-color:var(--danger);color:var(--danger)">🗑</button>` : ""}
              </div>
              <div style="white-space:pre-wrap;word-break:break-word">${c.hidden
                ? `<i style="color:var(--subtext)">Privater Kommentar – Inhalt nur für den Verfasser sichtbar.</i>`
                : esc(c.text)}</div>
            </div>`; }).join("") || `<span style="color:var(--subtext);font-size:12px">Noch keine Kommentare</span>`}
        </div>
        ${may("ticket_comment") ? `
        <div style="display:flex;flex-direction:column;gap:6px;margin-top:10px">
          <div style="display:flex;gap:8px">
            <textarea id="tkd-comment" rows="2" placeholder="Kommentar schreiben…"
              style="flex:1;resize:none;background:var(--panel-2);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px;font:inherit;font-size:13px"></textarea>
            <button class="btn-primary" id="tkd-comment-send" style="margin:0;width:auto">Senden</button>
          </div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <select id="tkd-comment-vis" style="max-width:230px">
              ${Object.entries(COMMENT_VIS).map(([k, o]) =>
                `<option value="${k}">${o.icon} Sichtbar ${o.label}</option>`).join("")}
            </select>
            <div id="tkd-comment-users" style="display:none;flex:1;min-width:220px">
              ${subjectPickerHtml(ticketUsers, [], { name: "tkd-subj" })}
            </div>
          </div>
        </div>` : ""}
      </div>
    `;

    // ---- Aktionen ----
    detailEl.querySelectorAll("[data-status]").forEach((b) =>
      b.addEventListener("click", async () => {
        try { drawDetail(await api.setTicketStatus(t.id, b.dataset.status)); await load(false); }
        catch (e) { window.notify?.(e.message, "error"); }
      }));
    detailEl.querySelector("#tkd-edit")?.addEventListener("click", () => openEditor(t));
    detailEl.querySelector("#tkd-del")?.addEventListener("click", async () => {
      if (!(await uiConfirm(`Ticket "${t.title}" löschen?`, { danger: true }))) return;
      try {
        await api.deleteTicket(t.id);
        currentId = null; detailEl.innerHTML = "";
        await load(false);
      } catch (e) { window.notify?.(e.message, "error"); }
    });
    detailEl.querySelector("#tkd-assign")?.addEventListener("click", () => openAssign(t));
    // Kommentar-Sichtbarkeit: Benutzerauswahl nur bei "für bestimmte" zeigen
    const visSel = detailEl.querySelector("#tkd-comment-vis");
    const usersBox = detailEl.querySelector("#tkd-comment-users");
    visSel?.addEventListener("change", () => {
      usersBox.style.display = visSel.value === "custom" ? "" : "none";
    });
    initSubjectPicker(usersBox);
    detailEl.querySelector("#tkd-comment-send")?.addEventListener("click", async () => {
      const ta = detailEl.querySelector("#tkd-comment");
      if (!ta.value.trim()) return;
      const visibility = visSel?.value || "all";
      const shared = readSubjectPicker(usersBox);
      if (visibility === "custom" && !shared.length) {
        window.notify?.(tr("note_pick_user"), "warn"); return;
      }
      try { drawDetail(await api.addTicketComment(t.id, ta.value, visibility, shared)); }
      catch (e) { window.notify?.(e.message, "error"); }
    });
    detailEl.querySelectorAll("[data-cdel]").forEach((b) =>
      b.addEventListener("click", async () => {
        if (!(await uiConfirm(tr("u_kommentar_loschen"), { okText: tr("delete"), danger: true }))) return;
        try { drawDetail(await api.deleteTicketComment(t.id, b.dataset.cdel)); }
        catch (e) { window.notify?.(e.message, "error"); }
      }));

    // Aktivitätsprotokoll des Tickets
    const actBtn = detailEl.querySelector("#tkd-activity");
    const actBox = detailEl.querySelector("#tkd-activity-box");
    actBtn?.addEventListener("click", async () => {
      const open = actBox.style.display === "none";
      actBox.style.display = open ? "" : "none";
      if (!open) return;
      actBox.innerHTML = `<div style="color:var(--subtext);font-size:12px">Lade Verlauf…</div>`;
      try {
        const log = await api.getTicketActivity(t.id);
        actBox.innerHTML = log.length ? `
          <div style="border:1px solid var(--border);border-radius:8px;padding:6px;max-height:200px;overflow:auto">
            ${log.map((e) => `
              <div style="font-size:12px;padding:2px 0;display:flex;gap:8px">
                <span style="color:var(--subtext);flex:none">${fmtDate(e.created_at)}</span>
                <span style="flex:none"><b>${esc(e.actor_name)}</b></span>
                <span style="flex:1;min-width:0">${esc(e.label)}${e.details ? " – " + esc(e.details) : ""}</span>
              </div>`).join("")}
          </div>` : `<div style="color:var(--subtext);font-size:12px">Noch keine Aktivität.</div>`;
      } catch (e) {
        actBox.innerHTML = `<div style="color:var(--danger);font-size:12px">${esc(e.message)}</div>`;
      }
    });

    // ---- Upload ----
    const fileInput = detailEl.querySelector("#tkd-file");
    detailEl.querySelector("#tkd-upload")?.addEventListener("click", () => fileInput.click());
    fileInput?.addEventListener("change", async () => {
      const f = fileInput.files[0];
      if (!f) return;
      try { drawDetail(await api.uploadTicketFile(t.id, f)); }
      catch (e) { window.notify?.(`Upload fehlgeschlagen: ${e.message}`, "error"); }
    });

    // ---- Anhänge rendern (Bilder mit Vorschau, Rest als Download-Kachel) ----
    const filesEl = detailEl.querySelector("#tkd-files");
    if (!t.files.length) {
      filesEl.innerHTML = `<span style="color:var(--subtext);font-size:12px">Keine Anhänge</span>`;
    } else {
      for (const f of t.files) {
        const card = document.createElement("div");
        card.style.cssText = "background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px;font-size:11px;max-width:160px";
        const isImg = (f.mime || "").startsWith("image/");
        card.innerHTML = `
          ${isImg ? `<div data-preview style="width:140px;height:90px;background:#0a1420;border-radius:6px;display:flex;align-items:center;justify-content:center;color:var(--subtext);cursor:pointer">Lade…</div>` : `<div style="font-size:26px;text-align:center">📄</div>`}
          <div style="margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(f.filename)}">${esc(f.filename)}</div>
          <div style="color:var(--subtext)">${(f.size / 1024).toFixed(0)} KB · ${esc(f.uploaded_by)}</div>
          <div style="display:flex;gap:4px;margin-top:4px">
            <button class="taskbar-btn" data-dl style="font-size:11px;padding:1px 7px">⬇</button>
            ${may("ticket_edit") ? `<button class="taskbar-btn" data-rm style="font-size:11px;padding:1px 7px">🗑</button>` : ""}
          </div>`;
        filesEl.appendChild(card);
        // Bild-Vorschau mit Auth-Header (Blob-URL).
        if (isImg) {
          api.fetchTicketFile(t.id, f.id).then((url) => {
            const pv = card.querySelector("[data-preview]");
            if (pv) { pv.innerHTML = `<img src="${url}" style="max-width:140px;max-height:90px;border-radius:6px" />`;
              pv.addEventListener("click", () => window.open(url, "_blank")); }
          }).catch(() => {});
        }
        card.querySelector("[data-dl]").addEventListener("click", async () => {
          try {
            const url = await api.fetchTicketFile(t.id, f.id);
            const a = document.createElement("a");
            a.href = url; a.download = f.filename; a.click();
          } catch (e) { window.notify?.(e.message, "error"); }
        });
        card.querySelector("[data-rm]")?.addEventListener("click", async () => {
          if (!(await uiConfirm(`Anhang "${f.filename}" löschen?`, { danger: true }))) return;
          try { drawDetail(await api.deleteTicketFile(t.id, f.id)); }
          catch (e) { window.notify?.(e.message, "error"); }
        });
      }
    }
  }

  // ---------------- Zuweisen-Dialog ----------------
  function openAssign(t) {
    const isChecked = (type, id) => t.assignees.some((a) => a.subject_type === type && a.subject_id === id);
    // Unverwaltete AD-Gruppen in einen eigenen Ordner, damit die Liste
    // bei vielen importierten Verzeichnis-Gruppen übersichtlich bleibt.
    const { managed: managedGroups, unmanaged: unmanagedGroups } = splitGroups(subjects.groups || []);
    const groupRow = (arr) => arr.map((g) => `
      <label style="display:flex;gap:6px;align-items:center;cursor:pointer">
        <input type="checkbox" data-stype="group" data-sid="${esc(g.id)}"
          ${isChecked("group", g.id) ? "checked" : ""}/> ${esc(g.label || g.name)}</label>`).join("");
    editorEl.style.display = "flex";
    editorEl.innerHTML = `
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px;width:380px;max-height:90%;overflow:auto">
        <h3 style="margin:0 0 10px">Ticket zuweisen</h3>
        <div style="border:1px solid var(--border);border-radius:8px;padding:8px;max-height:280px;overflow:auto;font-size:12px">
          <div style="font-weight:700;margin-bottom:4px">Benutzer</div>
          ${subjects.users.map((u) => `<label style="display:flex;gap:6px;align-items:center;cursor:pointer">
            <input type="checkbox" data-stype="user" data-sid="${esc(u.id)}" ${isChecked("user", u.id) ? "checked" : ""}/> ${esc(u.label)}</label>`).join("")}
          <div style="font-weight:700;margin:8px 0 4px">Gruppen</div>
          ${groupRow(managedGroups) || `<div style="color:var(--subtext)">Keine Gruppen vorhanden</div>`}
          ${unmanagedGroups.length ? `
            <details style="margin-top:8px;border:1px dashed var(--border);border-radius:7px;padding:4px 7px"
                     ${unmanagedGroups.some((g) => isChecked("group", g.id)) ? "open" : ""}>
              <summary style="cursor:pointer;color:var(--subtext);list-style:none">
                📂 Unverwaltete AD-Gruppen (${unmanagedGroups.length})</summary>
              <div style="margin-top:4px">${groupRow(unmanagedGroups)}</div>
            </details>` : ""}
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
          <button class="taskbar-btn" id="tka-cancel">Abbrechen</button>
          <button class="btn-primary" id="tka-save" style="margin:0;width:auto">Speichern</button>
        </div>
      </div>`;
    editorEl.querySelector("#tka-cancel").addEventListener("click", () => { editorEl.style.display = "none"; });
    editorEl.querySelector("#tka-save").addEventListener("click", async () => {
      const assignees = [...editorEl.querySelectorAll("input:checked")]
        .map((cb) => ({ subject_type: cb.dataset.stype, subject_id: cb.dataset.sid }));
      try {
        const updated = await api.setTicketAssignees(t.id, assignees);
        editorEl.style.display = "none";
        drawDetail(updated);
        await load(false);
      } catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  // ---------------- Erstellen/Bearbeiten-Dialog ----------------
  function openEditor(existing) {
    const isEdit = !!existing;
    const cliChecked = (cid) => existing?.clients?.includes(cid);
    editorEl.style.display = "flex";
    editorEl.innerHTML = `
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px;width:480px;max-height:92%;overflow:auto">
        <h3 style="margin:0 0 12px">${isEdit ? "Ticket bearbeiten" : "Neues Ticket"}</h3>
        <div class="form-row"><label>Titel</label>
          <input id="tke-title" value="${esc(existing?.title || "")}" placeholder="Kurze Zusammenfassung" /></div>
        <div class="form-row"><label>Beschreibung</label>
          <textarea id="tke-desc" rows="5" style="width:100%;background:var(--panel-2);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px;font:inherit;font-size:13px">${esc(existing?.description || "")}</textarea></div>
        <div class="form-row"><label>Priorität</label>
          <select id="tke-prio">
            ${Object.entries(PRIO).map(([k, v]) =>
              `<option value="${k}" ${(existing?.priority ?? "normal") === k ? "selected" : ""}>${v.label}</option>`).join("")}
          </select></div>
        <div class="form-row"><label>Fällig am</label>
          <input id="tke-due" type="date" value="${existing?.due_date ? new Date(existing.due_date).toISOString().slice(0, 10) : ""}" /></div>
        <div class="form-row"><label>Clients</label></div>
        <div style="border:1px solid var(--border);border-radius:8px;padding:8px;max-height:150px;overflow:auto;font-size:12px">
          ${state.clients.map((c) => `<label style="display:flex;gap:6px;align-items:center;cursor:pointer">
            <input type="checkbox" data-cid="${esc(c.id)}" ${cliChecked(c.id) ? "checked" : ""}/> 🖥️ ${esc(c.hostname)}</label>`).join("")
          || `<div style="color:var(--subtext)">Keine Clients sichtbar</div>`}
        </div>
        ${may("ticket_assign") ? `
        <div class="form-row" style="margin-top:10px"><label>Zuweisen an</label></div>
        <div id="tke-assign" style="border:1px solid var(--border);border-radius:8px;padding:8px;max-height:150px;overflow:auto;font-size:12px">
          <div style="font-weight:700;margin-bottom:4px">Benutzer</div>
          ${subjects.users.map((u) => `<label style="display:flex;gap:6px;align-items:center;cursor:pointer">
            <input type="checkbox" data-stype="user" data-sid="${esc(u.id)}"
              ${existing?.assignees?.some((a) => a.subject_type === "user" && a.subject_id === u.id) ? "checked" : ""}/> ${esc(u.label)}</label>`).join("")}
          <div style="font-weight:700;margin:8px 0 4px">Gruppen</div>
          ${subjects.groups.map((g) => `<label style="display:flex;gap:6px;align-items:center;cursor:pointer">
            <input type="checkbox" data-stype="group" data-sid="${esc(g.id)}"
              ${existing?.assignees?.some((a) => a.subject_type === "group" && a.subject_id === g.id) ? "checked" : ""}/> ${esc(g.label)}</label>`).join("")
          || `<div style="color:var(--subtext)">Keine Gruppen vorhanden</div>`}
        </div>` : ""}
        <div id="tke-error" class="hidden" style="color:var(--danger);font-size:12px;margin-top:8px"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
          <button class="taskbar-btn" id="tke-cancel">Abbrechen</button>
          <button class="btn-primary" id="tke-save" style="margin:0;width:auto">${isEdit ? tr("save") : "Erstellen"}</button>
        </div>
      </div>`;
    const q = (sel) => editorEl.querySelector(sel);
    q("#tke-cancel").addEventListener("click", () => { editorEl.style.display = "none"; });
    q("#tke-save").addEventListener("click", async () => {
      const due = q("#tke-due").value;
      const payload = {
        title: q("#tke-title").value.trim(),
        description: q("#tke-desc").value,
        priority: q("#tke-prio").value,
        due_date: due ? new Date(due + "T12:00:00").getTime() : null,
        clients: [...editorEl.querySelectorAll("[data-cid]:checked")].map((cb) => cb.dataset.cid),
        assignees: [...editorEl.querySelectorAll("#tke-assign input:checked")]
          .map((cb) => ({ subject_type: cb.dataset.stype, subject_id: cb.dataset.sid })),
      };
      try {
        const t = isEdit ? await api.updateTicket(existing.id, payload)
                         : await api.createTicket(payload);
        editorEl.style.display = "none";
        currentId = t.id;
        drawDetail(t);
        await load(false);
      } catch (e) {
        const err = q("#tke-error");
        err.textContent = e.message;
        err.classList.remove("hidden");
      }
    });
  }

  // ---------------- Laden ----------------
  async function load(selectFirst = true) {
    try {
      const [rows, subj, tusers] = await Promise.all([
        api.tickets(),
        api.ticketSubjects().catch(() => ({ users: [], groups: [] })),
        api.getTicketUsers().catch(() => ({ users: [], groups: [] })),
      ]);
      tickets = rows;
      subjects = subj;
      ticketUsers = tusers;
      drawList();
      if (selectFirst && !currentId && tickets.length) selectTicket(tickets[0].id);
      if (!tickets.length) {
        detailEl.innerHTML = `<div style="padding:20px;color:var(--subtext);font-size:13px">
          Keine Tickets sichtbar. Tickets sehen nur Admins, der Ersteller und
          zugewiesene Benutzer/Gruppen.</div>`;
      }
    } catch (e) {
      listEl.innerHTML = `<div style="padding:12px;color:var(--danger);font-size:12px">${esc(e.message)}</div>`;
    }
  }
  load();
}

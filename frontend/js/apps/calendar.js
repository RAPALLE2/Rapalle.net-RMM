// apps/calendar.js
// ----------------
// Kalender mit Monats- und Listenansicht.
//   - Termine anlegen/ändern/löschen (Ersteller bzw. 'manage_calendar')
//   - Ziele: Benutzer, Gruppen UND Clients (unverwaltete AD-Gruppen im
//     eigenen Ordner, wie überall sonst)
//   - Datum, Uhrzeit, Dauer und Wichtigkeit
// Vorgesetzte sehen laut Organigramm die Termine ihrer Untergebenen und
// dürfen dort eintragen - das erzwingt der Server.

import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";
import { splitGroups } from "../subjectpicker.js";
// t() unter Alias: in dieser Datei ist "t" bereits als lokaler
// Variablenname belegt (Tenant/Target/Trigger/Token o.ä.).
import { t as tr } from "../i18n.js";

const IMPORTANCE = {
  low: { label: "niedrig", color: "#64748b", icon: "○" },
  normal: { label: "normal", color: "#4da6ff", icon: "●" },
  high: { label: "hoch", color: "#f5a524", icon: "▲" },
  critical: { label: "kritisch", color: "#ff4d6d", icon: "⬤" },
};
const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const MONTHS = ["Januar", "Februar", tr("u_marz"), "April", "Mai", "Juni", "Juli",
  "August", "September", "Oktober", "November", "Dezember"];

const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const pad = (n) => String(n).padStart(2, "0");
const toDateInput = (ms) => {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};
const toTimeInput = (ms) => {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const fmtTime = (ms) => new Date(ms).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

export function renderCalendar(body, win) {
  let cursor = startOfDay(new Date());     // angezeigter Monat
  let events = [];
  let opts = { users: [], groups: [], clients: [], can_manage_all: false };
  let view = "month";                      // month | list

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
      <div class="explorer-toolbar" style="gap:8px;flex-wrap:wrap">
        <button class="taskbar-btn" id="cal-prev">‹</button>
        <button class="taskbar-btn" id="cal-today">Heute</button>
        <button class="taskbar-btn" id="cal-next">›</button>
        <strong id="cal-title" style="font-size:14px;margin:0 6px"></strong>
        <span style="flex:1"></span>
        <button class="taskbar-btn" id="cal-view">📋 Liste</button>
        <button class="btn-primary" id="cal-new" style="width:auto;margin:0">+ Termin</button>
      </div>
      <div id="cal-body" style="flex:1;overflow:auto;padding:8px 10px 14px"></div>
    </div>`;

  const bodyEl = body.querySelector("#cal-body");
  const titleEl = body.querySelector("#cal-title");

  body.querySelector("#cal-prev").addEventListener("click", () => { shift(-1); });
  body.querySelector("#cal-next").addEventListener("click", () => { shift(1); });
  body.querySelector("#cal-today").addEventListener("click", () => {
    cursor = startOfDay(new Date()); load();
  });
  body.querySelector("#cal-view").addEventListener("click", (e) => {
    view = view === "month" ? "list" : "month";
    e.target.textContent = view === "month" ? "📋 Liste" : "🗓️ Monat";
    draw();
  });
  body.querySelector("#cal-new").addEventListener("click", () => openEditor(null));

  function shift(months) {
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + months, 1);
    load();
  }

  function range() {
    // Ganzer Monat plus die angezeigten Randtage
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const last = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0);
    const from = new Date(first); from.setDate(first.getDate() - 7);
    const to = new Date(last); to.setDate(last.getDate() + 8);
    return [from.getTime(), to.getTime()];
  }

  async function load() {
    titleEl.textContent = `${MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}`;
    bodyEl.innerHTML = `<div style="color:var(--subtext);font-size:13px;padding:10px">Lädt…</div>`;
    const [from, to] = range();
    try {
      const [evs, o] = await Promise.all([
        api.getEvents(from, to),
        opts.users.length ? Promise.resolve(opts) : api.getEventTargets().catch(() => opts),
      ]);
      events = evs; opts = o;
    } catch (e) {
      bodyEl.innerHTML = `<div style="color:var(--danger);padding:10px">${esc(e.message)}</div>`;
      return;
    }
    draw();
  }

  const targetLabel = (t) => {
    if (t.type === "user") {
      const u = opts.users.find((x) => x.id === t.id);
      return `👤 ${u ? u.name : t.id}`;
    }
    if (t.type === "group") {
      const g = opts.groups.find((x) => x.id === t.id);
      return `👥 ${g ? g.name : t.id}`;
    }
    const c = opts.clients.find((x) => x.id === t.id);
    return `🖥️ ${c ? c.name : t.id}`;
  };

  function draw() {
    titleEl.textContent = view === "month"
      ? `${MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}`
      : `Termine ab ${new Date().toLocaleDateString("de-DE")}`;
    if (view === "list") return drawList();
    drawMonth();
  }

  // ---------------- Monatsansicht ----------------
  function drawMonth() {
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    // Montag als Wochenstart
    const offset = (first.getDay() + 6) % 7;
    const gridStart = new Date(first); gridStart.setDate(first.getDate() - offset);
    const todayKey = startOfDay(new Date()).getTime();

    let html = `<div style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px">
      ${DAYS.map((d) => `<div style="font-size:11px;color:var(--subtext);text-align:center;padding:2px 0">${d}</div>`).join("")}`;
    for (let i = 0; i < 42; i++) {
      const day = new Date(gridStart); day.setDate(gridStart.getDate() + i);
      const dayStart = startOfDay(day).getTime();
      const dayEnd = dayStart + 86400000 - 1;
      const inMonth = day.getMonth() === cursor.getMonth();
      const isToday = dayStart === todayKey;
      const dayEvents = events
        .filter((e) => e.start_at <= dayEnd && e.end_at >= dayStart)
        .sort((a, b) => a.start_at - b.start_at);
      html += `
        <div class="cal-day" data-date="${dayStart}"
          style="min-height:82px;border:1px solid ${isToday ? "var(--accent)" : "var(--border)"};
                 border-radius:8px;padding:3px 4px;background:${inMonth ? "var(--panel-2)" : "transparent"};
                 opacity:${inMonth ? 1 : .5};cursor:pointer;display:flex;flex-direction:column;gap:2px;overflow:hidden">
          <div style="font-size:11px;color:${isToday ? "var(--accent)" : "var(--subtext)"};font-weight:${isToday ? 700 : 400}">
            ${day.getDate()}</div>
          ${dayEvents.slice(0, 3).map((e) => {
            const imp = IMPORTANCE[e.importance] || IMPORTANCE.normal;
            return `<div class="cal-ev" data-id="${esc(e.id)}" title="${esc(e.title)}"
              style="font-size:10.5px;border-left:3px solid ${imp.color};background:rgba(255,255,255,.06);
                     border-radius:4px;padding:1px 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${e.all_day ? "" : esc(fmtTime(e.start_at)) + " "}${esc(e.title)}</div>`;
          }).join("")}
          ${dayEvents.length > 3 ? `<div style="font-size:10px;color:var(--subtext)">+${dayEvents.length - 3} weitere</div>` : ""}
        </div>`;
    }
    html += `</div>`;
    bodyEl.innerHTML = html;

    bodyEl.querySelectorAll(".cal-ev").forEach((el) =>
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const ev = events.find((x) => x.id === el.dataset.id);
        if (ev) openDetail(ev);
      }));
    bodyEl.querySelectorAll(".cal-day").forEach((el) =>
      el.addEventListener("click", () => openEditor(null, Number(el.dataset.date))));
  }

  // ---------------- Listenansicht ----------------
  function drawList() {
    const now = Date.now();
    const upcoming = events.filter((e) => e.end_at >= now).sort((a, b) => a.start_at - b.start_at);
    if (!upcoming.length) {
      bodyEl.innerHTML = `<div style="color:var(--subtext);font-size:13px;padding:10px">
        Keine anstehenden Termine in diesem Zeitraum.</div>`;
      return;
    }
    let lastDay = "";
    bodyEl.innerHTML = upcoming.map((e) => {
      const imp = IMPORTANCE[e.importance] || IMPORTANCE.normal;
      const day = new Date(e.start_at).toLocaleDateString("de-DE",
        { weekday: "long", day: "2-digit", month: "long" });
      const head = day !== lastDay
        ? `<div style="font-size:12px;color:var(--accent);font-weight:700;margin:10px 0 4px">${esc(day)}</div>` : "";
      lastDay = day;
      return `${head}
        <div class="cal-row" data-id="${esc(e.id)}"
          style="display:flex;gap:9px;align-items:flex-start;padding:6px 8px;border-radius:8px;
                 border-left:3px solid ${imp.color};background:var(--panel-2);margin-bottom:4px;cursor:pointer">
          <div style="font-size:12px;color:var(--subtext);flex:none;min-width:88px">
            ${e.all_day ? tr("u_ganztagig") : `${esc(fmtTime(e.start_at))} – ${esc(fmtTime(e.end_at))}`}</div>
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:600">${esc(e.title)}
              <span style="color:${imp.color};font-size:11px">${imp.icon} ${esc(imp.label)}</span></div>
            <div style="font-size:11px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
              ${(e.targets || []).map(targetLabel).map(esc).join(" · ") || "—"}
              ${e.location ? " · 📍 " + esc(e.location) : ""}</div>
          </div>
        </div>`;
    }).join("");
    bodyEl.querySelectorAll(".cal-row").forEach((el) =>
      el.addEventListener("click", () => {
        const ev = events.find((x) => x.id === el.dataset.id);
        if (ev) openDetail(ev);
      }));
  }

  // ---------------- Detail-Ansicht ----------------
  function openDetail(ev) {
    const imp = IMPORTANCE[ev.importance] || IMPORTANCE.normal;
    const mins = Math.round((ev.end_at - ev.start_at) / 60000);
    const back = document.createElement("div");
    back.className = "widget-picker-back";
    back.innerHTML = `
      <div class="widget-picker" style="max-width:430px">
        <div class="wp-head"><strong>${esc(ev.title)}</strong><button class="dash-w-btn" data-close>✕</button></div>
        <div class="wp-body" style="padding:12px;display:flex;flex-direction:column;gap:7px;font-size:13px">
          <div><span style="color:var(--subtext)">Wann:</span>
            ${esc(new Date(ev.start_at).toLocaleString("de-DE"))}
            ${ev.all_day ? tr("u_ganztagig_2") : `– ${esc(fmtTime(ev.end_at))} (${mins} Min.)`}</div>
          <div><span style="color:var(--subtext)">Wichtigkeit:</span>
            <span style="color:${imp.color}">${imp.icon} ${esc(imp.label)}</span></div>
          ${ev.location ? `<div><span style="color:var(--subtext)">Ort:</span> ${esc(ev.location)}</div>` : ""}
          <div><span style="color:var(--subtext)">Beteiligte:</span><br>
            ${(ev.targets || []).map((t) => `<span style="display:inline-block;background:var(--panel-2);
              border:1px solid var(--border);border-radius:20px;padding:2px 9px;margin:2px 3px 0 0;font-size:12px">
              ${esc(targetLabel(t))}</span>`).join("") || "—"}</div>
          ${ev.description ? `<div style="white-space:pre-wrap;border-top:1px solid var(--border);padding-top:7px">${esc(ev.description)}</div>` : ""}
          <div style="font-size:11px;color:var(--subtext);border-top:1px solid var(--border);padding-top:6px">
            Angelegt von ${esc(ev.created_by_name || "?")}</div>
        </div>
        ${ev.can_edit ? `<div style="padding:10px;display:flex;justify-content:flex-end;gap:6px">
          <button class="taskbar-btn" data-del style="border-color:var(--danger);color:var(--danger)">Löschen</button>
          <button class="btn-primary" data-edit>Bearbeiten</button>
        </div>` : ""}
      </div>`;
    document.body.appendChild(back);
    const close = () => back.remove();
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    back.querySelector("[data-close]").addEventListener("click", close);
    back.querySelector("[data-edit]")?.addEventListener("click", () => { close(); openEditor(ev); });
    back.querySelector("[data-del]")?.addEventListener("click", async () => {
      if (!(await uiConfirm(`Termin „${ev.title}“ löschen?`, { okText: tr("delete"), danger: true }))) return;
      close();
      try { await api.deleteEvent(ev.id); load(); }
      catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  // ---------------- Termin anlegen/bearbeiten ----------------
  function openEditor(ev, presetDate) {
    const start = ev ? ev.start_at : (presetDate ? presetDate + 9 * 3600000 : Date.now() + 3600000);
    const dur = ev ? Math.round((ev.end_at - ev.start_at) / 60000) : 60;
    const sel = new Set((ev?.targets || []).map((t) => `${t.type}:${t.id}`));
    const { managed, unmanaged } = splitGroups(opts.groups || []);

    // Nur Personen anbieten, für die man eintragen DARF (Server prüft erneut).
    const assignable = opts.users.filter((u) => u.assignable);
    const blocked = opts.users.length - assignable.length;

    const chk = (type, id, label, extra = "") => `
      <label style="display:flex;gap:6px;align-items:center;font-size:12.5px;padding:2px 0;cursor:pointer">
        <input type="checkbox" class="ce-target" data-type="${type}" value="${esc(id)}"
          ${sel.has(`${type}:${id}`) ? "checked" : ""} />
        <span>${esc(label)}${extra}</span></label>`;

    const back = document.createElement("div");
    back.className = "widget-picker-back";
    back.innerHTML = `
      <div class="widget-picker" style="max-width:520px">
        <div class="wp-head"><strong>${ev ? "Termin bearbeiten" : "Neuer Termin"}</strong>
          <button class="dash-w-btn" data-close>✕</button></div>
        <div class="wp-body" style="padding:12px;max-height:66vh;overflow:auto">
          <div class="form-row"><label>Titel</label>
            <input id="ce-title" value="${esc(ev?.title || "")}" placeholder="z.B. Wartungsfenster SRV-01" /></div>
          <div style="display:flex;gap:10px">
            <div class="form-row" style="flex:1"><label>Datum</label>
              <input type="date" id="ce-date" value="${toDateInput(start)}" /></div>
            <div class="form-row" style="flex:1"><label>Uhrzeit</label>
              <input type="time" id="ce-time" value="${toTimeInput(start)}" /></div>
            <div class="form-row" style="flex:1"><label>Dauer (Min.)</label>
              <input type="number" id="ce-dur" min="5" step="5" value="${dur}" /></div>
          </div>
          <div style="display:flex;gap:10px">
            <div class="form-row" style="flex:1"><label>Wichtigkeit</label>
              <select id="ce-imp">${Object.entries(IMPORTANCE).map(([k, v]) =>
                `<option value="${k}" ${(ev?.importance || "normal") === k ? "selected" : ""}>${v.icon} ${v.label}</option>`).join("")}</select></div>
            <div class="form-row" style="flex:1"><label>Ort (optional)</label>
              <input id="ce-loc" value="${esc(ev?.location || "")}" placeholder="Raum, Link…" /></div>
          </div>
          <label style="display:flex;gap:6px;align-items:center;font-size:12.5px;margin-bottom:8px">
            <input type="checkbox" id="ce-allday" ${ev?.all_day ? "checked" : ""} /> ganztägig
          </label>
          <div class="form-row"><label>Beschreibung</label>
            <textarea id="ce-desc" rows="2">${esc(ev?.description || "")}</textarea></div>

          <div class="form-row"><label>Für wen gilt der Termin?</label>
            <input type="text" id="ce-search" placeholder="Suchen…" style="margin-bottom:4px" />
            <div style="max-height:190px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:6px 8px">
              <div class="ce-sec" style="font-size:11px;color:var(--subtext);margin-bottom:2px">Personen</div>
              ${assignable.map((u) => chk("user", u.id, u.name,
                  u.workspace ? ` <span style="color:var(--subtext);font-size:11px">· ${esc(u.workspace)}</span>` : "")).join("")}
              ${blocked ? `<div style="font-size:11px;color:var(--subtext);margin-top:2px">
                ${blocked} weitere Person(en) nicht wählbar – nur für dich selbst und dein Team möglich.</div>` : ""}
              <div class="ce-sec" style="font-size:11px;color:var(--subtext);margin:7px 0 2px">Gruppen</div>
              ${managed.map((g) => chk("group", g.id, g.name)).join("") ||
                `<div style="font-size:11.5px;color:var(--subtext)">Keine Gruppen.</div>`}
              ${unmanaged.length ? `
                <details style="margin-top:6px;border:1px dashed var(--border);border-radius:7px;padding:4px 7px">
                  <summary style="cursor:pointer;font-size:12px;color:var(--subtext)">
                    📂 Unverwaltete AD-Gruppen (${unmanaged.length})</summary>
                  <div>${unmanaged.map((g) => chk("group", g.id, g.name)).join("")}</div>
                </details>` : ""}
              <div class="ce-sec" style="font-size:11px;color:var(--subtext);margin:7px 0 2px">Clients</div>
              ${opts.clients.map((c) => chk("client", c.id, c.name)).join("") ||
                `<div style="font-size:11.5px;color:var(--subtext)">Keine Clients.</div>`}
            </div>
            <div style="font-size:11px;color:var(--subtext);margin-top:4px">
              Ohne Auswahl gilt der Termin nur für dich.</div>
          </div>
          <div class="form-error hidden" id="ce-err"></div>
        </div>
        <div style="padding:10px;display:flex;justify-content:flex-end;gap:6px">
          <button class="taskbar-btn" data-close>Abbrechen</button>
          <button class="btn-primary" data-save>${ev ? tr("save") : "Anlegen"}</button>
        </div>
      </div>`;
    document.body.appendChild(back);
    const close = () => back.remove();
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    back.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
    back.querySelector("#ce-title").focus();

    // Suche über alle Auswahl-Zeilen
    back.querySelector("#ce-search").addEventListener("input", (e) => {
      const q = e.target.value.trim().toLowerCase();
      back.querySelectorAll(".ce-target").forEach((i) => {
        const row = i.closest("label");
        row.style.display = !q || row.textContent.toLowerCase().includes(q) ? "" : "none";
      });
    });
    // Ganztägig -> Uhrzeit/Dauer sind dann ohne Bedeutung
    const allday = back.querySelector("#ce-allday");
    const syncAllDay = () => {
      back.querySelector("#ce-time").disabled = allday.checked;
      back.querySelector("#ce-dur").disabled = allday.checked;
    };
    allday.addEventListener("change", syncAllDay); syncAllDay();

    back.querySelector("[data-save]").addEventListener("click", async () => {
      const err = back.querySelector("#ce-err");
      err.classList.add("hidden");
      const title = back.querySelector("#ce-title").value.trim();
      const date = back.querySelector("#ce-date").value;
      if (!title || !date) {
        err.textContent = tr("u_titel_und_datum_sind_erforderlich"); err.classList.remove("hidden"); return;
      }
      const isAllDay = allday.checked;
      const time = isAllDay ? "00:00" : (back.querySelector("#ce-time").value || "09:00");
      const startMs = new Date(`${date}T${time}`).getTime();
      // Ganztägig: 00:00 bis 23:59:59 DESSELBEN Tages (nicht 24 h ab Start -
      // das reichte bis in den Folgetag). Der Server rechnet das ohnehin noch
      // einmal sauber nach (_day_bounds), hier nur damit die Vorschau stimmt.
      const duration = isAllDay
        ? 24 * 60 - 1
        : Math.max(5, parseInt(back.querySelector("#ce-dur").value, 10) || 60);
      const targets = [...back.querySelectorAll(".ce-target:checked")]
        .map((i) => ({ type: i.dataset.type, id: i.value }));
      const payload = {
        title, description: back.querySelector("#ce-desc").value,
        location: back.querySelector("#ce-loc").value,
        start_at: startMs, duration_minutes: duration,
        all_day: isAllDay, importance: back.querySelector("#ce-imp").value,
        targets,
      };
      try {
        if (ev) await api.updateEvent(ev.id, payload);
        else await api.createEvent(payload);
        close();
        load();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });
  }

  load();
}

// apps/privacy.js
// ---------------
// Datenschutz-Zentrale. Zwei Ebenen in einem Fenster:
//
//   "Meine Daten"  - für JEDEN eingeloggten Benutzer. Auskunft nach Art. 15,
//                    Export nach Art. 20, Löschantrag nach Art. 17. Diese
//                    Rechte stehen Betroffenen zu, sie sind kein Admin-Feature.
//   "Verwaltung"   - nur mit Recht 'manage_privacy': Aufbewahrungsfristen,
//                    Bestandsübersicht, Löschanträge, Auskunft/Löschung für
//                    andere Personen.
//
// Bewusst NICHT unter "Einstellungen" versteckt: wer seine Betroffenenrechte
// wahrnehmen will, soll sie finden, ohne Admin-Bereiche zu durchsuchen.

import { api } from "../api.js";
import { esc, uiConfirm, uiPrompt } from "../utils.js";
import { state, hasGlobalPerm, isAdmin } from "../state.js";
import { t } from "../i18n.js";

const fmtTs = (ms) => ms ? new Date(Number(ms)).toLocaleString("de-DE") : "—";
const fmtBytes = (b) => {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(b) / Math.log(1024));
  return `${(b / Math.pow(1024, i)).toFixed(1)} ${u[i]}`;
};

export function renderPrivacy(body, win) {
  const canManage = isAdmin() || hasGlobalPerm("manage_privacy");
  let tab = "me";

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
      <div class="explorer-toolbar" style="gap:6px">
        <button class="tab-btn" data-tab="me">${t("privacy_tab_me")}</button>
        ${canManage ? `
          <button class="tab-btn" data-tab="retention">${t("privacy_tab_retention")}</button>
          <button class="tab-btn" data-tab="report">${t("privacy_tab_report")}</button>
          <button class="tab-btn" data-tab="requests">${t("privacy_tab_requests")}</button>
          <button class="tab-btn" data-tab="users">${t("privacy_tab_users")}</button>` : ""}
      </div>
      <div id="pv-body" style="flex:1;overflow:auto;padding:14px 16px 20px"></div>
    </div>`;

  const view = body.querySelector("#pv-body");
  body.querySelectorAll("[data-tab]").forEach((b) => {
    b.addEventListener("click", () => { tab = b.dataset.tab; draw(); });
  });

  const box = (title, inner, note = "") => `
    <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;
         padding:13px 15px;margin-bottom:12px">
      <strong style="font-size:13px;display:block;margin-bottom:${note ? "4px" : "9px"}">${title}</strong>
      ${note ? `<div style="font-size:11px;color:var(--subtext);margin-bottom:9px;line-height:1.45">${note}</div>` : ""}
      ${inner}
    </div>`;

  function draw() {
    body.querySelectorAll("[data-tab]").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === tab);
    });
    view.innerHTML = `<div style="color:var(--subtext);font-size:13px">${t("todo_loading")}</div>`;
    ({ me: drawMe, retention: drawRetention, report: drawReport,
       requests: drawRequests, users: drawUsers }[tab] || drawMe)();
  }

  const fail = (e) => {
    view.innerHTML = `<div style="color:var(--danger);font-size:13px">${esc(e.message)}</div>`;
  };

  // ---------------------------------------------------------------
  // Meine Daten
  // ---------------------------------------------------------------

  async function drawMe() {
    let data, reqs;
    try {
      [data, reqs] = await Promise.all([
        api.getMyPrivacyData(),
        api.getMyErasureRequests().catch(() => []),
      ]);
    } catch (e) { return fail(e); }

    const cats = data.kategorien || [];
    const open = (reqs || []).find((r) => r.status === "open");

    view.innerHTML = `
      ${box(t("privacy_my_data"),
        cats.length ? `<table style="width:100%;border-collapse:collapse;font-size:12px">
          ${cats.map((c) => `<tr>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${esc(c.label)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);text-align:right;
                color:var(--subtext)">${t("privacy_entries", { n: c.count })}</td></tr>`).join("")}
        </table>`
        : `<div style="font-size:12px;color:var(--subtext)">${t("privacy_nothing")}</div>`,
        t("privacy_my_data_note"))}

      ${box(t("privacy_export"),
        `<button class="btn-primary" id="pv-export" style="width:auto;margin:0">${t("privacy_export_btn")}</button>`,
        t("privacy_export_note"))}

      ${box(t("privacy_erase"),
        open
          ? `<div style="font-size:12px;color:var(--subtext)">
               ${t("privacy_erase_pending", { date: fmtTs(open.created_at) })}</div>`
          : `<div style="display:flex;gap:8px;flex-wrap:wrap">
               <button class="taskbar-btn" id="pv-req-content">${t("privacy_erase_content")}</button>
               <button class="taskbar-btn" id="pv-req-account" style="border-color:var(--danger);color:var(--danger)">${t("privacy_erase_account")}</button>
             </div>`,
        t("privacy_erase_note"))}

      ${(reqs || []).length ? box(t("privacy_my_requests"),
        `<table style="width:100%;border-collapse:collapse;font-size:12px">
          ${reqs.map((r) => `<tr>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${fmtTs(r.created_at)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${r.kind === "account" ? t("privacy_erase_q_account") : t("privacy_erase_q_content")}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${statusLabel(r.status)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${esc(r.note || "")}</td>
          </tr>`).join("")}
        </table>`) : ""}`;

    view.querySelector("#pv-export").addEventListener("click", async () => {
      try { await api.downloadPrivacyExport(null); }
      catch (e) { alert(e.message); }
    });
    const req = async (kind) => {
      const label = kind === "account" ? t("privacy_erase_q_account") : t("privacy_erase_q_content");
      if (!await uiConfirm(t("privacy_erase_q", { what: label }), {
        description: t("privacy_erase_q_note"),
        danger: kind === "account" })) return;
      const reason = await uiPrompt(t("privacy_reason"), { placeholder: t("privacy_reason_ph") });
      if (reason === null) return;
      try { await api.requestErasure(kind, reason || ""); } catch (e) { return alert(e.message); }
      draw();
    };
    view.querySelector("#pv-req-content")?.addEventListener("click", () => req("content"));
    view.querySelector("#pv-req-account")?.addEventListener("click", () => req("account"));
  }

  const statusLabel = (s) => ({
    open: t("privacy_status_open"), done: t("privacy_status_done"), rejected: t("privacy_status_rejected"),
  }[s] || s);

  // ---------------------------------------------------------------
  // Aufbewahrungsfristen
  // ---------------------------------------------------------------

  async function drawRetention() {
    let rep;
    try { rep = await api.getPrivacyReport(); } catch (e) { return fail(e); }

    view.innerHTML = `
      ${box(t("privacy_retention"),
        `<div style="display:flex;flex-direction:column;gap:11px">
          ${rep.retention.map((r) => `
            <div>
              <div style="display:flex;align-items:center;gap:9px">
                <label style="flex:1;font-size:12px">${esc(r.label)}</label>
                <input type="number" min="0" data-ret="${esc(r.key)}" value="${r.value}"
                  style="width:78px;padding:4px 7px;border-radius:6px;border:1px solid var(--border);
                  background:var(--panel);color:var(--text);font-size:12px;text-align:right">
                <span style="font-size:11px;color:var(--subtext);width:38px">${r.unit === "hours" ? t("privacy_hours") : t("privacy_days")}</span>
              </div>
              ${r.note ? `<div style="font-size:10px;color:var(--subtext);margin-top:2px;
                    line-height:1.4">${esc(r.note)}</div>` : ""}
            </div>`).join("")}
        </div>
        <div style="display:flex;gap:8px;margin-top:13px">
          <button class="btn-primary" id="pv-save" style="width:auto;margin:0">${t("save")}</button>
          <button class="taskbar-btn" id="pv-purge">${t("privacy_purge_now")}</button>
        </div>`,
        t("privacy_retention_note"))}

      ${box(t("privacy_last_run"),
        `<div style="font-size:12px;color:var(--subtext)">${fmtTs(rep.last_purge)}</div>`)}`;

    view.querySelector("#pv-save").addEventListener("click", async () => {
      const values = {};
      view.querySelectorAll("[data-ret]").forEach((i) => {
        values[i.dataset.ret] = parseInt(i.value, 10) || 0;
      });
      try {
        await api.setRetention(values);
        window.notify?.(t("privacy_saved"), "success");
      } catch (e) { alert(e.message); }
      draw();
    });
    view.querySelector("#pv-purge").addEventListener("click", async () => {
      if (!await uiConfirm(t("privacy_purge_q"), {
        description: t("privacy_purge_note"),
        danger: true })) return;
      try {
        const r = await api.runPrivacyPurge();
        const lines = Object.entries(r).map(([k, v]) => `${k}: ${v}`).join("\n");
        await uiConfirm(t("privacy_purge_done"), { description: lines, okText: "OK" });
      } catch (e) { alert(e.message); }
      draw();
    });
  }

  // ---------------------------------------------------------------
  // Bestandsübersicht
  // ---------------------------------------------------------------

  async function drawReport() {
    let rep;
    try { rep = await api.getPrivacyReport(); } catch (e) { return fail(e); }

    view.innerHTML = `
      ${box(t("privacy_rec_files"),
        `<div style="font-size:12px">${t("privacy_rec_count", { n: rep.recording_files, size: fmtBytes(rep.recording_bytes) })}</div>`,
        t("privacy_rec_note"))}

      ${box(t("privacy_stock"),
        `<table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="color:var(--subtext);font-size:11px;text-align:left">
            <th style="padding:3px 0">${t("privacy_col_area")}</th><th>${t("privacy_col_entries")}</th>
            <th>${t("privacy_col_oldest")}</th><th>${t("privacy_col_ondelete")}</th></tr>
          ${rep.items.filter((i) => i.count).map((i) => `<tr>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${esc(i.label)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${i.count}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${i.oldest ? fmtTs(i.oldest) : "—"}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${i.strategy === "delete" ? t("privacy_deleted") : t("privacy_anonymized")}</td>
          </tr>`).join("")}
        </table>`,
        t("privacy_stock_note"))}`;
  }

  // ---------------------------------------------------------------
  // Löschanträge
  // ---------------------------------------------------------------

  async function drawRequests() {
    let reqs;
    try { reqs = await api.getErasureRequests(); } catch (e) { return fail(e); }

    if (!reqs.length) {
      view.innerHTML = box(t("privacy_requests"),
        `<div style="font-size:12px;color:var(--subtext)">${t("privacy_no_requests")}</div>`);
      return;
    }

    view.innerHTML = reqs.map((r) => box(
      `${esc(r.username)} · ${r.kind === "account" ? t("privacy_erase_q_account") : t("privacy_erase_q_content")} · ${statusLabel(r.status)}`,
      `<div style="font-size:12px;color:var(--subtext);margin-bottom:8px">
         ${t("privacy_received", { date: fmtTs(r.created_at) })}
         ${r.reason ? `<br>${t("privacy_reason_label", { text: esc(r.reason) })}` : ""}
         ${r.note ? `<br>${t("privacy_decision", { text: esc(r.note) })}` : ""}
       </div>
       ${r.status === "open" ? `<div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="taskbar-btn" data-erase="${esc(r.user_id)}" data-name="${esc(r.username)}"
                  style="border-color:var(--danger);color:var(--danger)">${t("privacy_do_erase")}</button>
          <button class="taskbar-btn" data-reject="${esc(r.id)}">${t("privacy_reject")}</button>
        </div>` : ""}`)).join("");

    view.querySelectorAll("[data-erase]").forEach((b) => {
      b.addEventListener("click", () => eraseFlow(b.dataset.erase, b.dataset.name));
    });
    view.querySelectorAll("[data-reject]").forEach((b) => {
      b.addEventListener("click", async () => {
        const note = await uiPrompt(t("privacy_reject_note"), { description: t("privacy_reject_hint") });
        if (note === null) return;
        if (!note.trim()) return alert(t("privacy_reject_required"));
        try { await api.resolveErasureRequest(b.dataset.reject, "rejected", note.trim()); }
        catch (e) { return alert(e.message); }
        draw();
      });
    });
  }

  // ---------------------------------------------------------------
  // Personen: Auskunft & Löschung für andere
  // ---------------------------------------------------------------

  async function drawUsers() {
    let users;
    try { users = await api.getUsers(); } catch (e) { return fail(e); }

    view.innerHTML = box(t("privacy_persons"),
      `<table style="width:100%;border-collapse:collapse;font-size:12px">
        ${users.map((u) => `<tr>
          <td style="padding:5px 0;border-bottom:1px solid var(--border)">
            ${esc(u.display_name || u.username)}
            <span style="color:var(--subtext)">(${esc(u.username)})</span></td>
          <td style="padding:5px 0;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap">
            <button class="taskbar-btn" data-exp="${esc(u.id)}" style="padding:1px 7px;font-size:11px">${t("privacy_info_btn")}</button>
            ${u.id === state.user?.id ? "" :
              `<button class="taskbar-btn" data-del="${esc(u.id)}" data-name="${esc(u.username)}"
                 style="padding:1px 7px;font-size:11px;border-color:var(--danger);color:var(--danger)">${t("delete")}</button>`}
          </td></tr>`).join("")}
      </table>`,
      t("privacy_persons_note"));

    view.querySelectorAll("[data-exp]").forEach((b) => {
      b.addEventListener("click", async () => {
        try { await api.downloadPrivacyExport(b.dataset.exp); }
        catch (e) { alert(e.message); }
      });
    });
    view.querySelectorAll("[data-del]").forEach((b) => {
      b.addEventListener("click", () => eraseFlow(b.dataset.del, b.dataset.name));
    });
  }

  async function eraseFlow(userId, username) {
    const mode = await uiConfirm(t("privacy_erase_how", { name: username }), {
      description: t("privacy_erase_how_note"),
      okText: t("privacy_anonymize"), cancelText: t("privacy_hard") });
    // uiConfirm liefert true = OK-Button (anonymisieren), false = Abbruch.
    // Für "vollständig" wird bewusst ein zweiter, expliziter Schritt verlangt.
    const hard = mode === false
      ? await uiConfirm(t("privacy_hard_q"), {
          description: t("privacy_hard_note"),
          danger: true })
      : false;
    if (mode === false && !hard) return;

    const typed = await uiPrompt(t("privacy_confirm_name"), {
      description: t("privacy_confirm_expect", { name: username }), placeholder: username });
    if (typed === null) return;
    if (typed.trim() !== username) return alert(t("privacy_name_mismatch"));

    try {
      const r = await api.eraseUser(userId, hard ? "hard" : "anonymize", username);
      const lines = Object.entries(r).map(([k, v]) => `${k}: ${v}`).join("\n");
      await uiConfirm(t("privacy_erase_done"), { description: lines, okText: "OK" });
    } catch (e) { return alert(e.message); }
    draw();
  }

  draw();
}

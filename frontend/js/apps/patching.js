// apps/patching.js
// ----------------
// Software-Patching: Übersicht über die Flotte, Updates je Client und die
// Regeln der Automatik.
//
// Tabs:
//   Übersicht  - Zahlen nach Dringlichkeit, Clients nach Handlungsbedarf
//   Client     - Update-Liste eines Clients, einzeln oder gesammelt installieren
//   Automatik  - globale Regel + Ausnahmen je Client (Modell wie Agent-Auto-Update)
//   Verlauf    - was wurde wann installiert
//
// Zu den Dringlichkeitsstufen: nur Windows Update und die Security-Repos von
// apt/dnf liefern echte Einstufungen. Anwendungsupdates über winget haben
// KEINE - sie stehen als "Ohne Einstufung" da. Das ist wichtig zu sehen,
// bevor man eine Regel "nur Sicherheitsupdates" scharf schaltet.

import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";
import { state, hasGlobalPerm, isAdmin } from "../state.js";
import { t } from "../i18n.js";

// Farben fest, Bezeichnungen über t() - die Stufen heißen in jeder Sprache
// anders, die Farbe bleibt gleich.
const LEVEL_COLORS = {
  security: "#ff4d6d", critical: "#f5a524", important: "#facc15",
  moderate: "#4da6ff", low: "#64748b", feature: "#a78bfa", other: "#7f93ad",
};
const levelLabel = (lvl) => t(`lvl_${lvl}`);
const levelColor = (lvl) => LEVEL_COLORS[lvl] || LEVEL_COLORS.other;
const LEVEL_ORDER = Object.keys(LEVEL_COLORS);
const SOURCE_LABEL = {
  "windows-update": "Windows Update", winget: "winget", apt: "apt", dnf: "dnf",
};
// Wochentage: Kürzel aus der Locale, damit sie in jeder Sprache stimmen.
const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7].map((n) => [String(n),
  new Date(Date.UTC(2024, 0, n)).toLocaleDateString(undefined, { weekday: "short" })]);

const fmtTs = (ms) => ms ? new Date(Number(ms)).toLocaleString() : t("patch_never").toLowerCase();

export function renderPatching(body, win) {
  const canManage = isAdmin() || hasGlobalPerm("manage_patching");
  let tab = win?.props?.clientId ? "client" : "overview";
  let currentClient = win?.props?.clientId || null;
  let busy = false;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
      <div class="explorer-toolbar" style="gap:6px;flex-wrap:wrap">
        <button class="tab-btn" data-tab="overview">${t("patch_tab_overview")}</button>
        <button class="tab-btn" data-tab="client">${t("patch_tab_client")}</button>
        ${canManage ? `<button class="tab-btn" data-tab="auto">${t("patch_tab_auto")}</button>` : ""}
        <button class="tab-btn" data-tab="runs">${t("patch_tab_runs")}</button>
        <span style="flex:1"></span>
        <span id="pt-busy" style="font-size:11px;color:var(--subtext)"></span>
      </div>
      <div id="pt-body" style="flex:1;overflow:auto;padding:14px 16px 20px"></div>
    </div>`;

  const view = body.querySelector("#pt-body");
  const busyEl = body.querySelector("#pt-busy");
  body.querySelectorAll("[data-tab]").forEach((b) =>
    b.addEventListener("click", () => { tab = b.dataset.tab; draw(); }));

  const setBusy = (text) => {
    busy = !!text;
    busyEl.textContent = text || "";
  };
  const fail = (e) => {
    view.innerHTML = `<div style="color:var(--danger);font-size:13px">${esc(e.message)}</div>`;
  };
  const box = (title, inner, note = "") => `
    <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;
         padding:13px 15px;margin-bottom:12px">
      <strong style="font-size:13px;display:block;margin-bottom:${note ? "4px" : "9px"}">${title}</strong>
      ${note ? `<div style="font-size:11px;color:var(--subtext);margin-bottom:9px;line-height:1.45">${note}</div>` : ""}
      ${inner}
    </div>`;
  const pill = (level) => {
    const color = levelColor(level);
    return `<span style="display:inline-block;padding:1px 7px;border-radius:99px;
      font-size:10px;background:${color}22;color:${color};
      border:1px solid ${color}55;white-space:nowrap">${levelLabel(level)}</span>`;
  };

  function draw() {
    body.querySelectorAll("[data-tab]").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === tab));
    view.innerHTML = `<div style="color:var(--subtext);font-size:13px">${t("todo_loading")}</div>`;
    ({ overview: drawOverview, client: drawClient, auto: drawAuto,
       runs: drawRuns }[tab] || drawOverview)();
  }

  // ---------------------------------------------------------------
  // Übersicht
  // ---------------------------------------------------------------

  async function drawOverview() {
    let ov;
    try { ov = await api.getPatchOverview(); } catch (e) { return fail(e); }

    const levels = LEVEL_ORDER
      .map((l) => ({ key: l, n: ov.by_level[l] || 0, label: levelLabel(l), color: levelColor(l) }))
      .filter((l) => l.n > 0);
    const maxLevel = Math.max(1, ...levels.map((l) => l.n));

    view.innerHTML = `
      <div style="display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap">
        ${[[t("patch_open"), ov.total, "#4da6ff"],
           [t("patch_clients_affected"), ov.affected_clients, "#f5a524"],
           [t("patch_never"), ov.never_scanned, "#7f93ad"]].map(([l, v, c]) => `
          <div style="flex:1;min-width:120px;background:var(--panel-2);border:1px solid var(--border);
               border-radius:10px;padding:11px 13px">
            <div style="font-size:22px;font-weight:600;color:${c}">${v}</div>
            <div style="font-size:11px;color:var(--subtext)">${l}</div>
          </div>`).join("")}
      </div>

      ${box(t("patch_by_level"),
        levels.length ? `<div style="display:flex;flex-direction:column;gap:6px">
          ${levels.map((l) => `
            <div style="display:flex;align-items:center;gap:9px">
              <span style="width:110px;font-size:12px">${l.label}</span>
              <div style="flex:1;height:9px;background:var(--panel);border-radius:99px;overflow:hidden">
                <div style="width:${(l.n / maxLevel) * 100}%;height:100%;background:${l.color}"></div>
              </div>
              <span style="width:38px;text-align:right;font-size:12px">${l.n}</span>
            </div>`).join("")}
        </div>` : `<div style="font-size:12px;color:var(--subtext)">${t("patch_none_open")}</div>`,
        t("patch_level_note"))}

      ${box(t("patch_clients"),
        `<table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="color:var(--subtext);font-size:11px;text-align:left">
            <th style="padding:3px 0">${t("patch_col_client")}</th><th>${t("patch_col_open")}</th><th>${t("patch_col_security")}</th>
            <th>${t("patch_col_lastscan")}</th><th>${t("patch_col_auto")}</th><th></th></tr>
          ${ov.clients.map((c) => `<tr>
            <td style="padding:5px 0;border-bottom:1px solid var(--border)">
              ${c.online ? "🟢" : "⚫"} ${esc(c.hostname || c.id)}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border)">${c.patch_count || 0}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);
                color:${(c.patch_security || 0) ? "var(--danger)" : "inherit"}">${c.patch_security || 0}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${fmtTs(c.patch_last_scan)}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${
              { global: t("patch_mode_global"), on: t("patch_mode_on"), off: t("patch_mode_off") }[c.patch_policy || "global"]}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);text-align:right">
              <button class="taskbar-btn" data-open="${esc(c.id)}"
                style="padding:1px 7px;font-size:11px">${t("patch_open_btn")}</button></td>
          </tr>`).join("")}
        </table>`)}`;

    view.querySelectorAll("[data-open]").forEach((b) =>
      b.addEventListener("click", () => {
        currentClient = b.dataset.open; tab = "client"; draw();
      }));
  }

  // ---------------------------------------------------------------
  // Ein Client
  // ---------------------------------------------------------------

  async function drawClient() {
    const clients = (state.clients || []);
    if (!currentClient && clients.length) currentClient = clients[0].id;
    if (!currentClient) {
      view.innerHTML = box("Client", `<div style="font-size:12px;color:var(--subtext)">${t("patch_none_open")}</div>`);
      return;
    }

    let data;
    try { data = await api.getClientPatches(currentClient); } catch (e) { return fail(e); }
    const c = data.client;
    const patches = data.patches || [];

    view.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <select id="pt-client" style="padding:5px 8px;border-radius:6px;border:1px solid var(--border);
          background:var(--panel-2);color:var(--text);font-size:12px;min-width:180px">
          ${clients.map((x) => `<option value="${esc(x.id)}" ${x.id === currentClient ? "selected" : ""}>${esc(x.hostname || x.id)}</option>`).join("")}
        </select>
        <button class="taskbar-btn" id="pt-scan" ${c.online ? "" : "disabled"}>${t("patch_scan_btn")}</button>
        <button class="btn-primary" id="pt-all" style="width:auto;margin:0"
          ${c.online && patches.length ? "" : "disabled"}>${t("patch_install_all")}</button>
        <span style="font-size:11px;color:var(--subtext)">
          ${t("patch_lastscan", { date: fmtTs(c.patch_last_scan) })}${c.online ? "" : t("patch_offline")}</span>
      </div>

      ${c.patch_reboot ? `<div style="background:#f5a52418;border:1px solid #f5a52455;
        border-radius:8px;padding:9px 12px;margin-bottom:12px;font-size:12px">
        ${t("patch_reboot_warn")}</div>` : ""}

      ${patches.length ? `
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="color:var(--subtext);font-size:11px;text-align:left">
            <th style="padding:3px 0;width:26px"></th><th>${t("patch_col_name")}</th><th>${t("patch_col_level")}</th>
            <th>${t("patch_col_version")}</th><th>${t("patch_col_source")}</th><th></th></tr>
          ${patches.map((p) => `<tr>
            <td style="padding:5px 0;border-bottom:1px solid var(--border)">
              <input type="checkbox" data-sel="${esc(p.id)}" checked></td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border)">
              ${esc(p.name)}${p.needs_reboot ? ` <span title="${t("patch_reboot_tip")}">⟳</span>` : ""}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border)">${pill(p.level)}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);color:var(--subtext)">
              ${esc(p.current_version || "—")} → ${esc(p.available_version || "?")}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);color:var(--subtext)">
              ${esc(SOURCE_LABEL[p.source] || p.source)}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap">
              <button class="taskbar-btn" data-one="${esc(p.id)}" style="padding:1px 6px;font-size:11px"
                ${c.online ? "" : "disabled"}>${t("patch_install_one")}</button>
              <button class="taskbar-btn" data-skip="${esc(p.id)}" style="padding:1px 6px;font-size:11px"
                title="${t("patch_skip_tip")}">${t("patch_skip")}</button>
            </td></tr>`).join("")}
        </table>
        <div style="margin-top:10px">
          <button class="taskbar-btn" id="pt-sel">${t("patch_install_selected")}</button>
        </div>`
        : `<div style="font-size:12px;color:var(--subtext)">
             ${c.patch_last_scan ? t("patch_none_open") : t("patch_never_scanned")}</div>`}

      ${data.auto_rule ? `<div style="margin-top:16px;font-size:11px;color:var(--subtext)">
          ${t("patch_auto_on", { origin: data.auto_rule.scope === "global" ? t("patch_auto_global") : t("patch_auto_own"), n: data.auto_preview.length })}</div>`
        : `<div style="margin-top:16px;font-size:11px;color:var(--subtext)">
          ${t("patch_auto_off")}</div>`}`;

    view.querySelector("#pt-client").addEventListener("change", (e) => {
      currentClient = e.target.value; draw();
    });
    view.querySelector("#pt-scan")?.addEventListener("click", async () => {
      if (busy) return;
      setBusy(t("patch_scanning"));
      try { await api.scanPatches(currentClient); }
      catch (e) { alert(e.message); }
      setBusy("");
      draw();
    });

    const selected = () => patches
      .filter((p) => view.querySelector(`[data-sel="${CSS.escape(p.id)}"]`)?.checked)
      .map((p) => ({ uid: p.uid, source: p.source, name: p.name }));

    const install = async (items, label) => {
      if (busy) return;
      if (!items.length) return alert(t("patch_nothing_selected"));
      if (!await uiConfirm(t("patch_install_q", { n: items.length, host: c.hostname }), {
        description: t("patch_install_note") })) return;
      setBusy(t("patch_installing", { what: label }));
      try {
        const r = await api.applyPatches(currentClient, items);
        const failed = (r.failed || []).length;
        await uiConfirm(t("patch_done"), {
          description: t("patch_result", { ok: (r.installed || []).length, failed })
            + (r.needs_reboot ? `\n\n${t("patch_needs_reboot")}` : "")
            + (failed ? `\n\n${r.failed.map((f) => `${f.name}: ${f.error}`).join("\n")}` : ""),
          okText: "OK" });
      } catch (e) { alert(e.message); }
      setBusy("");
      draw();
    };

    view.querySelector("#pt-all")?.addEventListener("click", () =>
      install(patches.map((p) => ({ uid: p.uid, source: p.source, name: p.name })), t("patch_install_all")));
    view.querySelector("#pt-sel")?.addEventListener("click", () =>
      install(selected(), t("patch_install_selected")));
    view.querySelectorAll("[data-one]").forEach((b) => b.addEventListener("click", () => {
      const p = patches.find((x) => x.id === b.dataset.one);
      install([{ uid: p.uid, source: p.source, name: p.name }], p.name);
    }));
    view.querySelectorAll("[data-skip]").forEach((b) => b.addEventListener("click", async () => {
      try { await api.excludePatch(b.dataset.skip, true); } catch (e) { return alert(e.message); }
      draw();
    }));
  }

  // ---------------------------------------------------------------
  // Automatik
  // ---------------------------------------------------------------

  async function drawAuto() {
    let rules;
    try { rules = await api.getPatchRules(); } catch (e) { return fail(e); }
    const g = rules.global;
    const levels = new Set((g.levels || "").split(",").filter(Boolean));
    const days = new Set((g.weekdays || "").split(",").filter(Boolean));

    view.innerHTML = `
      ${box(t("patch_switch"),
        `<label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
          <input type="checkbox" id="pt-switch" ${rules.global_enabled ? "checked" : ""}>
          ${t("patch_switch_label")}
        </label>`,
        t("patch_switch_note"))}

      ${box(t("patch_global_rule"),
        `<div style="display:flex;flex-direction:column;gap:11px">
          <div>
            <div style="font-size:11px;color:var(--subtext);margin-bottom:4px">${t("patch_levels_label")}</div>
            <div style="display:flex;gap:7px;flex-wrap:wrap">
              ${LEVEL_ORDER.map((l) => `
                <label style="display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer">
                  <input type="checkbox" data-lvl="${l}" ${levels.has(l) ? "checked" : ""}>
                  ${levelLabel(l)}</label>`).join("")}
            </div>
          </div>
          <div>
            <div style="font-size:11px;color:var(--subtext);margin-bottom:4px">${t("patch_window")}</div>
            <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">
              <input type="time" id="pt-ws" value="${esc(g.window_start || "02:00")}" style="padding:4px 7px;
                border-radius:6px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px">
              <span style="font-size:12px;color:var(--subtext)">${t("patch_window_to")}</span>
              <input type="time" id="pt-we" value="${esc(g.window_end || "05:00")}" style="padding:4px 7px;
                border-radius:6px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12px">
              <span style="font-size:10px;color:var(--subtext)">${t("patch_window_hint")}</span>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:7px">
              ${WEEKDAYS.map(([n, l]) => `
                <label style="display:flex;align-items:center;gap:3px;font-size:11px;cursor:pointer">
                  <input type="checkbox" data-day="${n}" ${days.has(n) ? "checked" : ""}> ${l}</label>`).join("")}
            </div>
          </div>
          <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">
            <label style="font-size:12px;display:flex;align-items:center;gap:6px">
              ${t("patch_interval")}
              <input type="number" min="1" id="pt-int" value="${g.scan_interval_hours || 24}"
                style="width:62px;padding:4px 7px;border-radius:6px;border:1px solid var(--border);
                background:var(--panel);color:var(--text);font-size:12px;text-align:right"> ${t("privacy_hours")}
            </label>
            <label style="font-size:12px;display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" id="pt-reboot" ${g.auto_reboot ? "checked" : ""}>
              ${t("patch_autoreboot")}
            </label>
          </div>
          <div>
            <div style="font-size:11px;color:var(--subtext);margin-bottom:4px">
              ${t("patch_exclusions")}</div>
            <input id="pt-excl" value="${esc(g.exclusions || "")}" placeholder="z.B. Oracle.JavaRuntimeEnvironment"
              style="width:100%;padding:5px 8px;border-radius:6px;border:1px solid var(--border);
              background:var(--panel);color:var(--text);font-size:12px">
          </div>
          <div style="display:flex;gap:8px">
            <button class="btn-primary" id="pt-save" style="width:auto;margin:0">${t("save")}</button>
            <button class="taskbar-btn" id="pt-now">${t("patch_run_now")}</button>
          </div>
        </div>`,
        t("patch_rule_note"))}

      ${box(t("patch_mode_per_client"),
        `<table style="width:100%;border-collapse:collapse;font-size:12px">
          ${(state.clients || []).map((c) => `<tr>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${esc(c.hostname || c.id)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);text-align:right">
              <select data-policy="${esc(c.id)}" style="padding:3px 6px;border-radius:6px;
                border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:11px">
                ${[["global", t("patch_mode_global")], ["on", t("patch_mode_on")], ["off", t("patch_mode_off")]].map(([v, l]) =>
                  `<option value="${v}" ${(c.patch_policy || "global") === v ? "selected" : ""}>${l}</option>`).join("")}
              </select></td>
          </tr>`).join("")}
        </table>`,
        t("patch_mode_note"))}`;

    view.querySelector("#pt-switch").addEventListener("change", async (e) => {
      try { await api.setPatchGlobalSwitch(e.target.checked); }
      catch (err) { alert(err.message); draw(); }
    });
    view.querySelector("#pt-save").addEventListener("click", async () => {
      const values = {
        enabled: true,
        levels: LEVEL_ORDER.filter((l) => view.querySelector(`[data-lvl="${l}"]`).checked).join(","),
        weekdays: WEEKDAYS.map(([n]) => n).filter((n) => view.querySelector(`[data-day="${n}"]`).checked).join(","),
        window_start: view.querySelector("#pt-ws").value || "02:00",
        window_end: view.querySelector("#pt-we").value || "05:00",
        scan_interval_hours: parseInt(view.querySelector("#pt-int").value, 10) || 24,
        auto_reboot: view.querySelector("#pt-reboot").checked,
        exclusions: view.querySelector("#pt-excl").value,
      };
      if (!values.levels) return alert(t("patch_need_level"));
      try {
        await api.savePatchGlobalRule(values);
        window.notify?.(t("patch_rule_saved"), "success");
      } catch (e) { alert(e.message); }
      draw();
    });
    view.querySelector("#pt-now").addEventListener("click", async () => {
      if (busy) return;
      setBusy(t("patch_auto_running"));
      try {
        const r = await api.runPatchAuto();
        await uiConfirm(t("patch_cycle_done"), {
          description: t("patch_cycle_result", { scanned: r.scanned, patched: r.patched, errors: r.errors }),
          okText: "OK" });
      } catch (e) { alert(e.message); }
      setBusy("");
    });
    view.querySelectorAll("[data-policy]").forEach((sel) =>
      sel.addEventListener("change", async () => {
        try {
          await api.setPatchPolicy(sel.dataset.policy, sel.value);
          const c = (state.clients || []).find((x) => x.id === sel.dataset.policy);
          if (c) c.patch_policy = sel.value;
        } catch (e) { alert(e.message); draw(); }
      }));
  }

  // ---------------------------------------------------------------
  // Verlauf
  // ---------------------------------------------------------------

  // Ergebnis-Zelle als eigene Funktion: die Verschachtelung aus Template-
  // Literal + t()-Objektargument ist inline nicht mehr lesbar (und bricht).
  function runResult(r) {
    if (!r.finished_at) {
      return `<span style="color:var(--subtext)">${t("patch_running")}</span>`;
    }
    const err = r.failed
      ? `, <span style="color:var(--danger)">${t("patch_errors", { n: r.failed })}</span>`
      : "";
    return `${r.installed} ok${err}${r.needs_reboot ? " ⟳" : ""}`;
  }

  async function drawRuns() {
    let runs;
    try { runs = await api.getPatchRuns(); } catch (e) { return fail(e); }
    if (!runs.length) {
      view.innerHTML = box(t("patch_history"),
        `<div style="font-size:12px;color:var(--subtext)">${t("patch_no_runs")}</div>`);
      return;
    }
    view.innerHTML = box(t("patch_history"),
      `<table style="width:100%;border-collapse:collapse;font-size:12px">
        <tr style="color:var(--subtext);font-size:11px;text-align:left">
          <th style="padding:3px 0">${t("patch_col_when")}</th><th>${t("patch_col_client")}</th><th>${t("patch_col_trigger")}</th>
          <th>${t("patch_col_result")}</th></tr>
        ${runs.map((r) => `<tr>
          <td style="padding:5px 0;border-bottom:1px solid var(--border)">${fmtTs(r.started_at)}</td>
          <td style="padding:5px 0;border-bottom:1px solid var(--border)">${esc(r.hostname || r.client_id)}</td>
          <td style="padding:5px 0;border-bottom:1px solid var(--border);color:var(--subtext)">
            ${r.trigger === "auto" ? t("patch_trigger_auto") : `👤 ${esc(r.actor || "")}`}</td>
          <td style="padding:5px 0;border-bottom:1px solid var(--border)">
            ${runResult(r)}
            ${r.detail ? `<div style="font-size:10px;color:var(--subtext);margin-top:2px">${esc(String(r.detail).slice(0, 300))}</div>` : ""}
          </td></tr>`).join("")}
      </table>`);
  }

  draw();
}

// apps/patching.js
// ----------------
// Software-Patching: Übersicht über die Flotte, Updates je Client, Regeln der
// Automatik und Verlauf.
//
// Neu gefasst. Der alte Aufbau hat den Anwender im Unklaren gelassen: ein
// Klick auf "Jetzt suchen" führte entweder zu einem Ergebnis oder zu gar
// nichts, und im zweiten Fall war von aussen nicht zu erkennen, ob der
// Auftrag lief, gescheitert war oder nie angekommen ist. Deshalb jetzt:
//
//   * Vor dem Klick steht da, ob der Client überhaupt patchen kann
//     (readiness aus dem Backend) - der Knopf ist nicht einfach nur grau.
//   * Während des Auftrags ein echter Fortschritt mit Phase und Quelle.
//   * Nach dem Auftrag eine Aufschlüsselung JE QUELLE, inklusive Fehlertext.
//     "0 Updates" und "winget liess sich nicht starten" sehen nicht mehr
//     gleich aus.
//   * Ein Selbsttest-Knopf, der in Sekunden sagt, woran es liegt.
//
// Zu den Dringlichkeitsstufen: nur Windows Update und die Security-Repos von
// apt/dnf liefern echte Einstufungen. Anwendungsupdates über winget haben
// KEINE - sie stehen als "Ohne Einstufung" da. Das ist wichtig zu sehen,
// bevor man eine Regel "nur Sicherheitsupdates" scharf schaltet.

import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";
import { state, hasGlobalPerm, isAdmin } from "../state.js";
import { t } from "../i18n.js";
import { registerCleanup } from "../windowmanager.js";

// Farben fest, Bezeichnungen über t() - die Stufen heissen in jeder Sprache
// anders, die Farbe bleibt gleich.
const LEVEL_COLORS = {
  security: "#ff4d6d", critical: "#f5a524", important: "#facc15",
  moderate: "#4da6ff", low: "#64748b", feature: "#a78bfa", other: "#7f93ad",
};
const LEVEL_ORDER = Object.keys(LEVEL_COLORS);
const levelLabel = (lvl) => t(`lvl_${lvl}`);
const levelColor = (lvl) => LEVEL_COLORS[lvl] || LEVEL_COLORS.other;

const SOURCE_LABEL = {
  "windows-update": "Windows Update", winget: "winget", apt: "apt", dnf: "dnf",
};

// Wochentage: Kürzel aus der Locale, damit sie in jeder Sprache stimmen.
const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7].map((n) => [String(n),
  new Date(Date.UTC(2024, 0, n)).toLocaleDateString(undefined, { weekday: "short" })]);

const fmtTs = (ms) => ms ? new Date(Number(ms)).toLocaleString()
  : t("patch_never").toLowerCase();

// Phasen des Agenten in lesbaren Text übersetzen.
const PHASE_TEXT = {
  queued: "Auftrag angenommen…",
  asking: "Auftrag wird an den Agenten übergeben…",
  scanning: "Quelle wird abgefragt",
  scanned: "Quelle fertig",
  installing: "Installation läuft",
  installed: "installiert",
  done: "fertig",
  failed: "fehlgeschlagen",
  lost: "abgebrochen",
};

export function renderPatching(body, win) {
  const canManage = isAdmin() || hasGlobalPerm("manage_patching");
  let tab = win?.props?.clientId ? "client" : "overview";
  let currentClient = win?.props?.clientId || null;
  let pollTimer = null;
  let busyText = "";

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

  const setBusy = (text) => { busyText = text || ""; busyEl.textContent = busyText; };
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

  const banner = (text, color) => `
    <div style="background:${color}18;border:1px solid ${color}55;border-radius:8px;
         padding:9px 12px;margin-bottom:12px;font-size:12px;line-height:1.5">${text}</div>`;

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
      ${ov.unsupported_clients ? banner(t("patch_unsupported_banner", { n: ov.unsupported_clients }), "#f5a524") : ""}

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
            <th style="padding:3px 0">${t("patch_col_client")}</th><th>${t("patch_col_open")}</th>
            <th>${t("patch_col_security")}</th><th>${t("patch_col_lastscan")}</th>
            <th>${t("patch_col_auto")}</th><th></th></tr>
          ${ov.clients.map((c) => {
            const unsupported = c.online && (c.patch_protocol || 0) < 2;
            return `<tr>
            <td style="padding:5px 0;border-bottom:1px solid var(--border)">
              ${c.online ? "🟢" : "⚫"} ${esc(c.hostname || c.id)}
              ${unsupported ? `<span title="${t("patch_unsupported_tip")}" style="color:#f5a524">⚠</span>` : ""}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border)">${c.patch_count || 0}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);
                color:${(c.patch_security || 0) ? "var(--danger)" : "inherit"}">${c.patch_security || 0}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${fmtTs(c.patch_last_scan)}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${
              { global: t("patch_mode_global"), on: t("patch_mode_on"), off: t("patch_mode_off") }[c.patch_policy || "global"]}</td>
            <td style="padding:5px 0;border-bottom:1px solid var(--border);text-align:right">
              <button class="taskbar-btn" data-open="${esc(c.id)}"
                style="padding:1px 7px;font-size:11px">${t("patch_open_btn")}</button></td>
          </tr>`; }).join("")}
        </table>`)}`;

    view.querySelectorAll("[data-open]").forEach((b) =>
      b.addEventListener("click", () => {
        currentClient = b.dataset.open; tab = "client"; draw();
      }));
  }

  // ---------------------------------------------------------------
  // Ein Client
  // ---------------------------------------------------------------

  // Fortschrittsanzeige eines laufenden Auftrags.
  function jobBox(job) {
    if (!job || !job.running) return "";
    const phase = PHASE_TEXT[job.phase] || job.phase || "";
    const detail = job.detail ? ` – ${esc(job.detail)}` : "";
    const pct = job.total ? Math.round((job.done / job.total) * 100) : null;
    return `
      <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;
           padding:11px 14px;margin-bottom:12px">
        <div style="font-size:12px;margin-bottom:7px">
          <strong>${job.kind === "apply" ? t("patch_job_apply") : t("patch_job_scan")}</strong>
          – ${esc(phase)}${detail}${pct !== null ? ` (${job.done}/${job.total})` : ""}
        </div>
        <div style="height:6px;background:var(--panel);border-radius:99px;overflow:hidden">
          <div style="height:100%;background:#4da6ff;width:${pct === null ? 100 : pct}%;
               ${pct === null ? "opacity:.35" : ""}"></div>
        </div>
      </div>`;
  }

  // Aufschlüsselung je Quelle - der eigentliche Gewinn gegenüber früher.
  function sourceBox(job) {
    if (!job || job.running || !(job.sources || []).length) return "";
    return box(t("patch_sources_title"),
      `<table style="width:100%;border-collapse:collapse;font-size:12px">
        ${job.sources.map((s) => `<tr>
          <td style="padding:4px 0;border-bottom:1px solid var(--border);width:150px">
            ${esc(SOURCE_LABEL[s.source] || s.source || "?")}</td>
          <td style="padding:4px 0;border-bottom:1px solid var(--border)">
            ${s.ok
              ? `<span style="color:var(--subtext)">${t("patch_source_ok", { n: s.count ?? 0 })}${s.note ? ` – ${esc(s.note)}` : ""}</span>`
              : `<span style="color:var(--danger)">${esc(s.error || "Fehler")}</span>`}
          </td></tr>`).join("")}
      </table>`,
      t("patch_sources_note"));
  }

  function readinessBanner(r) {
    if (!r || r.reason === "ok") return "";
    const color = r.ready ? "#f5a524" : "#ff4d6d";
    const caps = r.capabilities || {};
    const extra = caps.protocol
      ? ` <span style="color:var(--subtext)">(${t("patch_protocol")} ${caps.protocol}${
          caps.agent_version ? `, Agent ${esc(caps.agent_version)}` : ""})</span>`
      : "";
    return banner(`${esc(r.message)}${extra}`, color);
  }

  // Fragt den Fortschritt ab, bis der Auftrag fertig ist. Kein Dauerlauf:
  // nach 30 Minuten wird aufgegeben, damit kein Timer unbemerkt weiterläuft.
  function pollJob(clientId) {
    const started = Date.now();
    const tick = async () => {
      let job = null;
      try { job = await api.getPatchJob(clientId); } catch { job = null; }

      // Abbrechen nur, wenn das Backend den Auftrag auch KENNT. Eine leere
      // Antwort bedeutet "noch nicht eingetragen", nicht "schon fertig" -
      // diese Verwechslung war der Grund, warum der Fortschritt früher
      // sofort wieder verschwand.
      if (job && job.known && !job.running) {
        setBusy("");
        if (job.error) {
          await uiConfirm(t("patch_failed"), { description: job.error, okText: "OK" });
        } else if (job.kind === "scan") {
          window.notify?.(t("patch_found", { n: job.found ?? 0 }), "success", 4000);
        } else if (job.kind === "apply") {
          window.notify?.(job.detail || t("patch_done"), "success", 6000);
        }
        draw();
        return;
      }
      if (job && job.running) {
        const phase = PHASE_TEXT[job.phase] || job.phase || "";
        setBusy(`${phase}${job.detail ? ` – ${job.detail}` : ""}`);
        const el = view.querySelector("#pt-job");
        if (el) el.outerHTML = `<div id="pt-job">${jobBox(job)}</div>`;
      }
      if (Date.now() - started > 1800000) { setBusy(""); draw(); return; }
      pollTimer = setTimeout(tick, 2000);
    };
    // Kurz warten: die Hintergrund-Aufgabe braucht einen Wimpernschlag,
    // bevor sie im Statusendpunkt auftaucht.
    pollTimer = setTimeout(tick, 600);
  }

  async function drawClient() {
    const clients = state.clients || [];
    if (!currentClient && clients.length) currentClient = clients[0].id;
    if (!currentClient) {
      view.innerHTML = box("Client",
        `<div style="font-size:12px;color:var(--subtext)">${t("patch_none_open")}</div>`);
      return;
    }

    let data;
    try { data = await api.getClientPatches(currentClient); } catch (e) { return fail(e); }
    const c = data.client;
    const patches = data.patches || [];
    const ready = data.readiness || {};
    const job = data.job || {};
    const canRun = ready.ready && !job.running;

    view.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <select id="pt-client" style="padding:5px 8px;border-radius:6px;border:1px solid var(--border);
          background:var(--panel-2);color:var(--text);font-size:12px;min-width:180px">
          ${clients.map((x) => `<option value="${esc(x.id)}" ${x.id === currentClient ? "selected" : ""}>${esc(x.hostname || x.id)}</option>`).join("")}
        </select>
        <button class="taskbar-btn" id="pt-scan" ${canRun ? "" : "disabled"}>${t("patch_scan_btn")}</button>
        <button class="btn-primary" id="pt-all" style="width:auto;margin:0"
          ${canRun && patches.length ? "" : "disabled"}>${t("patch_install_all")}</button>
        <button class="taskbar-btn" id="pt-test" ${c.online ? "" : "disabled"}>${t("patch_selftest")}</button>
        <span style="font-size:11px;color:var(--subtext)">
          ${t("patch_lastscan", { date: fmtTs(c.patch_last_scan) })}${c.online ? "" : t("patch_offline")}</span>
      </div>

      ${readinessBanner(ready)}
      <div id="pt-job">${jobBox(job)}</div>

      ${c.patch_reboot ? banner(t("patch_reboot_warn"), "#f5a524") : ""}

      ${patches.length ? `
        <table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="color:var(--subtext);font-size:11px;text-align:left">
            <th style="padding:3px 0;width:26px"></th><th>${t("patch_col_name")}</th>
            <th>${t("patch_col_level")}</th><th>${t("patch_col_version")}</th>
            <th>${t("patch_col_source")}</th><th></th></tr>
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
                ${canRun ? "" : "disabled"}>${t("patch_install_one")}</button>
              <button class="taskbar-btn" data-skip="${esc(p.id)}" style="padding:1px 6px;font-size:11px"
                title="${t("patch_skip_tip")}">${t("patch_skip")}</button>
            </td></tr>`).join("")}
        </table>
        <div style="margin-top:10px">
          <button class="taskbar-btn" id="pt-sel" ${canRun ? "" : "disabled"}>${t("patch_install_selected")}</button>
        </div>`
        : `<div style="font-size:12px;color:var(--subtext);margin-bottom:12px">
             ${c.patch_last_scan ? t("patch_none_open") : t("patch_never_scanned")}</div>`}

      <div style="margin-top:14px">${sourceBox(job)}</div>

      ${data.auto_rule ? `<div style="font-size:11px;color:var(--subtext)">
          ${t("patch_auto_on", { origin: data.auto_rule.scope === "global" ? t("patch_auto_global") : t("patch_auto_own"), n: data.auto_preview.length })}</div>`
        : `<div style="font-size:11px;color:var(--subtext)">${t("patch_auto_off")}</div>`}`;

    view.querySelector("#pt-client").addEventListener("change", (e) => {
      clearTimeout(pollTimer);
      currentClient = e.target.value;
      draw();
    });

    // Läuft beim Öffnen schon etwas, wird sofort weiterverfolgt.
    if (job.running) pollJob(currentClient);

    view.querySelector("#pt-scan")?.addEventListener("click", async () => {
      const id = currentClient;
      setBusy(t("patch_scanning"));
      try {
        const r = await api.scanPatches(id);
        if (r.already_running) window.notify?.(t("patch_already_running"), "info", 5000);
        if (r.hint) window.notify?.(r.hint, "warn", 8000);
      } catch (e) {
        setBusy("");
        return uiConfirm(t("patch_cannot_scan"), { description: e.message, okText: "OK" });
      }
      pollJob(id);
    });

    view.querySelector("#pt-test")?.addEventListener("click", async () => {
      setBusy(t("patch_selftest_running"));
      let r;
      try { r = await api.patchSelftest(currentClient); }
      catch (e) {
        setBusy("");
        return uiConfirm(t("patch_selftest"), { description: e.message, okText: "OK" });
      }
      setBusy("");
      const caps = r.capabilities || {};
      const lines = (r.checks || [])
        .map((ch) => `${ch.ok ? "OK  " : "FEHLER"} ${ch.name}: ${ch.detail || ""}`).join("\n");
      await uiConfirm(t("patch_selftest"), {
        description:
          `${t("patch_protocol")} ${caps.protocol || "?"} · Agent ${caps.agent_version || "?"}\n`
          + `${t("patch_col_source")}: ${(caps.sources || []).join(", ") || "—"}\n\n${lines}`,
        okText: "OK" });
    });

    const selected = () => patches
      .filter((p) => view.querySelector(`[data-sel="${CSS.escape(p.id)}"]`)?.checked)
      .map((p) => ({ uid: p.uid, source: p.source, name: p.name }));

    const install = async (items, label) => {
      if (!items.length) return uiConfirm(t("patch_nothing_selected"), { okText: "OK" });
      if (!await uiConfirm(t("patch_install_q", { n: items.length, host: c.hostname }), {
        description: t("patch_install_note") })) return;
      setBusy(t("patch_installing", { what: label }));
      try {
        await api.applyPatches(currentClient, items);
      } catch (e) {
        setBusy("");
        return uiConfirm(t("patch_failed"), { description: e.message, okText: "OK" });
      }
      // Die Installation läuft im Hintergrund weiter - auch wenn dieses
      // Fenster zwischendurch geschlossen wird.
      pollJob(currentClient);
    };

    view.querySelector("#pt-all")?.addEventListener("click", () =>
      install(patches.map((p) => ({ uid: p.uid, source: p.source, name: p.name })),
              t("patch_install_all")));
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
            <div style="font-size:11px;color:var(--subtext);margin-bottom:4px">${t("patch_exclusions")}</div>
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
      setBusy(t("patch_auto_running"));
      try {
        const r = await api.runPatchAuto();
        await uiConfirm(t("patch_cycle_done"), {
          description: t("patch_cycle_result", {
            scanned: r.scanned, patched: r.patched, errors: r.errors })
            + (r.skipped ? `\n${t("patch_cycle_skipped", { n: r.skipped })}` : ""),
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
          <th style="padding:3px 0">${t("patch_col_when")}</th><th>${t("patch_col_client")}</th>
          <th>${t("patch_col_trigger")}</th><th>${t("patch_col_result")}</th></tr>
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

  if (win?.key) registerCleanup(win.key, () => clearTimeout(pollTimer));

  draw();
}

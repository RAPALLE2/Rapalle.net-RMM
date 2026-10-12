// modules.js (feature) - Die Modul-App (Core)
// -------------------------------------------
// Module ansehen, aktivieren/deaktivieren, Teilfunktionen schalten, einstellen
// und Absturzberichte lesen. Nur fuer Administratoren (das Backend prueft das
// ebenfalls). Pro Modul gibt es ein eigenes Einstellungsmenue - dadurch bleibt
// die Einstellungen-App schlank.

import { esc, uiConfirm } from "../app/utils.js";
import { moduleRequest as req, loadModules, getModules, reportModuleCrash } from "../app/modules.js";

const STATUS = {
  enabled:  { label: "aktiv",        color: "#3ecf8e" },
  disabled: { label: "aus",          color: "var(--subtext)" },
  crashed:  { label: "abgestürzt",   color: "#ff4d6d" },
};
const PART = { backend: "Backend", frontend: "Frontend", agent: "Agent", client: "Client", node: "Node" };
const fmt = (ms) => (ms ? new Date(ms).toLocaleString() : "–");

export function renderModules(body, win) {
  let data = { modules: [], errors: [], crash_limit: 1 };
  let openId = null, tab = "overview";

  body.innerHTML = `<div class="mod-root" style="height:100%;overflow:auto;padding:14px 16px;box-sizing:border-box"></div>`;
  const root = body.querySelector(".mod-root");

  async function load(keepScroll = true) {
    const y = root.scrollTop;
    try { data = await req("/api/modules"); }
    catch (e) { root.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    draw();
    if (keepScroll) root.scrollTop = y;
  }
  const byId = (id) => data.modules.find((m) => m.id === id);
  const nameOf = (id) => byId(id)?.name || id;

  // ------------------------------------------------------------------ Aktionen
  async function sync() { await loadModules(); await load(); }

  async function enable(id, withDeps = false) {
    try {
      await req(`/api/modules/${id}/enable${withDeps ? "?with_deps=1" : ""}`, { method: "POST" });
      window.notify?.(`${nameOf(id)} aktiviert`, "success");
    } catch (e) {
      if (!withDeps && /Benötigt zuerst|Benoetigt zuerst/.test(e.message)) {
        if (await uiConfirm(`${nameOf(id)} braucht weitere Module`, { description: e.message + ". Diese gleich mit aktivieren?", okText: "Mit aktivieren" })) return enable(id, true);
        return;
      }
      window.notify?.(e.message, "error", 12000);
    }
    await sync();
  }
  async function disable(id, cascade = false) {
    try {
      await req(`/api/modules/${id}/disable${cascade ? "?cascade=1" : ""}`, { method: "POST" });
      window.notify?.(`${nameOf(id)} deaktiviert`, "info");
    } catch (e) {
      if (!cascade && /Wird noch ben/.test(e.message)) {
        if (await uiConfirm(`${nameOf(id)} wird noch benötigt`, { description: e.message + ". Diese gleich mit abschalten?", okText: "Alle abschalten" })) return disable(id, true);
        return;
      }
      window.notify?.(e.message, "error", 12000);
    }
    await sync();
  }

  // ------------------------------------------------------------------ Liste
  function badge(m) {
    const s = STATUS[m.status] || STATUS.disabled;
    return `<span style="font-size:11px;padding:2px 8px;border-radius:99px;border:1px solid ${s.color};color:${s.color}">${s.label}</span>`;
  }
  function card(m) {
    const crash = m.status === "crashed" && m.last_crash
      ? `<div style="margin-top:8px;font-size:12px;color:#ff4d6d">⚠ ${esc(m.last_crash.error)}</div>` : "";
    const parts = m.parts.map((p) => `<span style="font-size:10px;padding:1px 6px;border-radius:6px;background:var(--panel-2);color:var(--subtext)">${PART[p] || p}</span>`).join(" ");
    return `<div class="mod-card" data-open="${m.id}" style="cursor:pointer;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:6px">
      <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:24px">${esc(m.icon)}</span>
        <div style="flex:1;min-width:0"><div style="font-weight:600">${esc(m.name)} <span style="font-weight:400;color:var(--subtext);font-size:12px">v${esc(m.version)}</span></div>
          <div style="margin-top:2px">${parts}</div></div>
        ${badge(m)}
      </div>
      <div style="font-size:12px;color:var(--subtext);line-height:1.45">${esc(m.description)}</div>
      ${m.requires.length ? `<div style="font-size:11px;color:var(--subtext)">Benötigt: ${m.requires.map((r) => esc(nameOf(r))).join(", ")}</div>` : ""}
      ${crash}
      <div style="display:flex;gap:8px;margin-top:4px" data-stop>
        ${m.available === false ? `<span style="font-size:12px;color:var(--subtext)">${esc(m.unavailable_reason || "nicht verfügbar")}</span>`
          : m.enabled ? `<button class="btn" data-act="disable" data-id="${m.id}">Deaktivieren</button>`
                      : `<button class="btn-primary" data-act="enable" data-id="${m.id}">Aktivieren</button>`}
      </div>
    </div>`;
  }
  function drawList() {
    const cats = {};
    for (const m of data.modules) (cats[m.category] ||= []).push(m);
    const active = data.modules.filter((m) => m.enabled).length;
    root.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px">
        <h2 style="margin:0;font-size:18px">🧩 Module</h2>
        <span style="color:var(--subtext);font-size:12px">${active} von ${data.modules.length} aktiv</span>
        <span style="flex:1"></span>
        <label style="font-size:12px;color:var(--subtext)" title="Nach so vielen Abstürzen innerhalb von 10 Minuten wird ein Modul automatisch abgeschaltet.">Abschaltung nach
          <input id="mod-limit" type="number" min="1" max="50" value="${data.crash_limit}" style="width:52px"> Absturz/Abstürzen</label>
        <button class="btn" id="mod-rescan">↻ Neu einlesen</button>
      </div>
      <p style="margin:0 0 14px;color:var(--subtext);font-size:12px;line-height:1.5">Das RMM läuft ohne jedes Modul. Alle Module sind standardmäßig aus.
        Stürzt ein Modul ab, wird es automatisch deaktiviert und ein Crashbericht gespeichert – alles andere läuft weiter.</p>
      ${data.errors.length ? `<div style="background:rgba(255,77,109,.1);border:1px solid #ff4d6d;border-radius:8px;padding:8px 10px;margin-bottom:12px;font-size:12px">
        <b>Ungültige module.json:</b><br>${data.errors.map((e) => `${esc(e.path)}: ${esc(e.error)}`).join("<br>")}</div>` : ""}
      ${Object.keys(cats).sort().map((c) => `<h3 style="margin:16px 0 8px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--subtext)">${esc(c)}</h3>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px">${cats[c].map(card).join("")}</div>`).join("")}`;
    root.querySelector("#mod-rescan").onclick = async () => { await req("/api/modules/rescan", { method: "POST" }); await sync(); };
    root.querySelector("#mod-limit").onchange = async (e) => {
      try { await req("/api/modules/crash-policy", { method: "PUT", body: JSON.stringify({ crash_limit: parseInt(e.target.value, 10) || 1 }) }); window.notify?.("Gespeichert", "success", 1500); }
      catch (err) { window.notify?.(err.message, "error"); }
    };
    root.querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => { openId = el.dataset.open; tab = "overview"; draw(); }));
    root.querySelectorAll("[data-stop]").forEach((el) => el.addEventListener("click", (e) => e.stopPropagation()));
    root.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true; await (b.dataset.act === "enable" ? enable(b.dataset.id) : disable(b.dataset.id));
    }));
  }

  // ------------------------------------------------------------------ Detail
  function drawDetail(m) {
    const tabs = [["overview", "Übersicht"]];
    if (m.features.length) tabs.push(["features", "Funktionen"]);
    if (m.settings_schema.length || m.custom_settings_ui) tabs.push(["settings", "Einstellungen"]);
    tabs.push(["crashes", `Abstürze${m.crash_count ? ` (${m.crash_count})` : ""}`]);
    if (!tabs.some(([k]) => k === tab)) tab = "overview";
    root.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
        <button class="btn" id="mod-back">← Alle Module</button>
        <span style="font-size:24px">${esc(m.icon)}</span>
        <div style="flex:1"><div style="font-size:17px;font-weight:600">${esc(m.name)} <span style="font-weight:400;color:var(--subtext);font-size:12px">v${esc(m.version)}</span></div></div>
        ${badge(m)}
        ${m.available === false ? "" : m.enabled ? `<button class="btn" id="mod-toggle">Deaktivieren</button>` : `<button class="btn-primary" id="mod-toggle">Aktivieren</button>`}
      </div>
      <div class="tab-bar" style="padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:12px">
        ${tabs.map(([k, l]) => `<button class="tab-btn${k === tab ? " active" : ""}" data-tab="${k}">${l}</button>`).join("")}
      </div>
      <div id="mod-tab"></div>`;
    root.querySelector("#mod-back").onclick = () => { openId = null; draw(); };
    const tg = root.querySelector("#mod-toggle");
    if (tg) tg.onclick = async () => { tg.disabled = true; await (m.enabled ? disable(m.id) : enable(m.id)); };
    root.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { tab = b.dataset.tab; drawDetail(m); });
    const box = root.querySelector("#mod-tab");
    ({ overview: tabOverview, features: tabFeatures, settings: tabSettings, crashes: tabCrashes }[tab])(box, m);
  }

  function row(k, v) { return `<tr><td style="padding:4px 14px 4px 0;color:var(--subtext);vertical-align:top;white-space:nowrap">${k}</td><td style="padding:4px 0">${v}</td></tr>`; }

  function tabOverview(box, m) {
    const reqs = m.requires.map((r) => { const d = byId(r); const on = d?.enabled;
      return `<span style="color:${on ? "#3ecf8e" : "#f5a524"}">${on ? "●" : "○"} ${esc(nameOf(r))}</span>`; }).join(", ") || "–";
    const pip = m.pip.length ? m.pip.map((p) => `${esc(p)}${m.pip_missing.some((x) => x === p) ? ` <i style="color:#f5a524">(wird beim Aktivieren installiert)</i>` : ""}`).join(", ") : "–";
    const crash = m.status === "crashed" && m.last_crash ? `
      <div style="background:rgba(255,77,109,.1);border:1px solid #ff4d6d;border-radius:8px;padding:10px 12px;margin-bottom:12px;font-size:13px">
        <b>⚠ Dieses Modul ist abgestürzt und wurde deaktiviert.</b><br>
        <span style="color:var(--subtext)">${fmt(m.last_crash.ts)} · ${esc(m.last_crash.where)}</span><br>
        <code style="font-size:12px">${esc(m.last_crash.error)}</code><br>
        <a href="#" id="mod-showcrash" style="font-size:12px">Crashbericht ansehen →</a></div>` : "";
    box.innerHTML = `${crash}<p style="margin:0 0 12px;line-height:1.5">${esc(m.description)}</p>
      <table style="font-size:13px;border-collapse:collapse">
        ${row("ID", `<code>${esc(m.id)}</code>`)}${row("Autor", esc(m.author || "–"))}${row("Kategorie", esc(m.category))}
        ${row("Bestandteile", m.parts.map((p) => PART[p] || p).join(", "))}
        ${row("Benötigt Module", reqs)}${row("Benötigt von", m.required_by.map((r) => esc(nameOf(r))).join(", ") || "–")}
        ${row("Python-Pakete", pip)}${row("Aktiv seit", fmt(m.started_at))}
      </table>`;
    box.querySelector("#mod-showcrash")?.addEventListener("click", (e) => { e.preventDefault(); tab = "crashes"; drawDetail(m); });
  }

  function tabFeatures(box, m) {
    box.innerHTML = `<p style="margin:0 0 10px;color:var(--subtext);font-size:12px">Teilfunktionen dieses Moduls einzeln ein- oder ausschalten. ${m.enabled ? "Das Modul wird dabei kurz neu geladen." : "Wirkt beim nächsten Aktivieren."}</p>
      ${m.features.map((f) => `<label style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);cursor:pointer">
        <input type="checkbox" data-feat="${f.id}" ${f.enabled ? "checked" : ""} style="margin-top:3px">
        <span><b>${esc(f.label)}</b>${f.description ? `<br><span style="font-size:12px;color:var(--subtext)">${esc(f.description)}</span>` : ""}</span></label>`).join("")}`;
    box.querySelectorAll("[data-feat]").forEach((c) => c.addEventListener("change", async () => {
      c.disabled = true;
      try { await req(`/api/modules/${m.id}/features`, { method: "PUT", body: JSON.stringify({ features: { [c.dataset.feat]: c.checked } }) });
            window.notify?.("Gespeichert", "success", 1500); await loadModules(); }
      catch (e) { window.notify?.(e.message, "error", 10000); c.checked = !c.checked; }
      await load();
    }));
  }

  function field(f, val) {
    const id = `ms-${f.key}`, base = `width:100%;max-width:420px`;
    const opts = (f.options || []).map((o) => (typeof o === "object" ? o : { value: o, label: o }));
    let input;
    if (f.type === "bool") input = `<label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="${id}" ${val ? "checked" : ""}> ${esc(f.label)}</label>`;
    else if (f.type === "select") input = `<select id="${id}" style="${base}">${opts.map((o) => `<option value="${esc(o.value)}" ${o.value === val ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
    else if (f.type === "text") input = `<textarea id="${id}" rows="4" style="${base}">${esc(val ?? "")}</textarea>`;
    else input = `<input id="${id}" type="${f.type === "password" ? "password" : f.type === "number" ? "number" : "text"}" value="${esc(val ?? "")}" ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} style="${base}">`;
    return `<div class="form-row" style="margin-bottom:12px">${f.type === "bool" ? "" : `<label for="${id}">${esc(f.label)}</label>`}${input}
      ${f.description ? `<div style="font-size:11px;color:var(--subtext);margin-top:3px">${esc(f.description)}</div>` : ""}</div>`;
  }

  async function tabSettings(box, m) {
    box.innerHTML = "";
    if (m.settings_schema.length) {
      const form = document.createElement("div");
      form.innerHTML = m.settings_schema.map((f) => field(f, m.settings[f.key])).join("") +
        `<button class="btn-primary" id="ms-save">Speichern</button> <span id="ms-msg" style="font-size:12px;margin-left:8px"></span>`;
      box.appendChild(form);
      form.querySelector("#ms-save").onclick = async () => {
        const out = {};
        for (const f of m.settings_schema) {
          const el = form.querySelector(`#ms-${f.key}`);
          out[f.key] = f.type === "bool" ? el.checked : el.value;
        }
        const msg = form.querySelector("#ms-msg");
        try { await req(`/api/modules/${m.id}/settings`, { method: "PUT", body: JSON.stringify({ settings: out }) });
              msg.style.color = "#3ecf8e"; msg.textContent = "✓ gespeichert"; await load(); }
        catch (e) { msg.style.color = "var(--danger)"; msg.textContent = e.message; }
      };
    }
    if (m.custom_settings_ui) {
      const fm = getModules().find((x) => x.id === m.id);
      const holder = document.createElement("div");
      holder.style.marginTop = m.settings_schema.length ? "22px" : "0";
      box.appendChild(holder);
      if (!fm?.settings_ui) { holder.innerHTML = `<div style="color:var(--subtext);font-size:13px">Aktiviere das Modul, um sein Einstellungsmenü zu sehen.</div>`; return; }
      try {
        const mod = await import(/* @vite-ignore */ fm.base + fm.settings_ui.entry);
        await mod[fm.settings_ui.export || "renderSettings"](holder, { module: fm, req });
      } catch (e) {
        holder.innerHTML = `<div style="color:var(--danger);font-size:13px">Einstellungen konnten nicht geladen werden: ${esc(e.message)}</div>`;
        reportModuleCrash(m.id, "Einstellungsmenü", e, true);
      }
    }
  }

  async function tabCrashes(box, m) {
    box.innerHTML = `<div style="color:var(--subtext)">Lade …</div>`;
    let list = [];
    try { list = (await req(`/api/modules/${m.id}/crashes`)).crashes; } catch (e) { box.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    if (!list.length) { box.innerHTML = `<div style="color:var(--subtext)">Keine Abstürze aufgezeichnet. 👍</div>`; return; }
    box.innerHTML = `<div style="display:flex;justify-content:flex-end;margin-bottom:8px"><button class="btn btn-danger" id="mc-clear">Alle löschen</button></div>` +
      list.map((c, i) => `<details style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-bottom:8px" ${i === 0 ? "open" : ""}>
        <summary style="cursor:pointer"><b>${fmt(c.ts)}</b> · ${esc(c.where || "")} <span style="color:var(--subtext)">[${esc(c.source || "backend")}${c.disabled === false ? ", nur protokolliert" : ""}]</span><br>
          <code style="font-size:12px;color:#ff4d6d">${esc(c.error || "")}</code></summary>
        <pre style="margin:8px 0 0;font-size:11px;white-space:pre-wrap;max-height:260px;overflow:auto;background:var(--panel-2);padding:8px;border-radius:6px">${esc(c.traceback || "(kein Traceback)")}</pre></details>`).join("");
    box.querySelector("#mc-clear").onclick = async () => {
      if (!(await uiConfirm("Crashberichte löschen?", { okText: "Löschen" }))) return;
      await req(`/api/modules/${m.id}/crashes`, { method: "DELETE" }); await load();
    };
  }

  function draw() {
    const m = openId && byId(openId);
    if (m) drawDetail(m); else { openId = null; drawList(); }
  }
  load(false);
}

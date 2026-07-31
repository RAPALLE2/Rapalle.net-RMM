// apps/notifications.js
// ---------------------
// Benachrichtigungs-Rework (Tab "Benachrichtigungen" in den Einstellungen):
//   1. Webhooks     - Discord/Custom; Custom mit eigenen HTTP-Headern (JSON)
//                     und frei definierbarem Body-Template mit Platzhaltern
//   2. E-Mail/SMTP  - Serververbindung (Host/Port/Login/Sicherheit) + Test
//   3. Regeln       - "Wenn <Trigger> (auf Clients X/Y/Z), dann <Kanal> an
//                     <Ziel>" mit vielen Trigger-Arten (offline, Garantie,
//                     CPU/RAM/Disk/Temp-Schwellwerte, Websites, Logins, ...)

import { api } from "../api.js";
import { state } from "../state.js";
import { esc, uiConfirm } from "../utils.js";
// t() unter Alias: in dieser Datei ist "t" bereits als lokaler
// Variablenname belegt (Tenant/Target/Trigger/Token o.ä.).
import { t as tr } from "../i18n.js";
import { condenseHints } from "../help.js";

const PLACEHOLDER_HELP =
  "Platzhalter: {head} {body} {message} {client} {tenant} {location} {service} {level} {timestamp}";

export function renderNotifications(body, win) {
  // Erklaertexte dieser Seite in "?"-Symbole umwandeln - einmal direkt nach
  // dem Zeichnen und einmal verzoegert fuer nachgeladene Bereiche.
  setTimeout(() => condenseHints(body), 0);
  setTimeout(() => condenseHints(body), 400);

  let tab = "rules";
  let catalog = { triggers: [], channels: [] };
  let webhooks = [];

  async function draw() {
    body.innerHTML = `
      <div class="settings-section">
        <div style="display:flex;gap:6px;margin-bottom:14px">
          <button class="taskbar-btn nt-tab" data-tab="rules"    style="${tab === "rules" ? "background:var(--accent);color:#fff" : ""}">📋 Regeln</button>
          <button class="taskbar-btn nt-tab" data-tab="webhooks" style="${tab === "webhooks" ? "background:var(--accent);color:#fff" : ""}">🔗 Webhooks</button>
          <button class="taskbar-btn nt-tab" data-tab="smtp"     style="${tab === "smtp" ? "background:var(--accent);color:#fff" : ""}">✉️ E-Mail (SMTP)</button>
        </div>
        <div id="nt-content"></div>
      </div>`;
    body.querySelectorAll(".nt-tab").forEach((b) =>
      b.addEventListener("click", () => { tab = b.dataset.tab; draw(); }));
    const content = body.querySelector("#nt-content");
    try {
      if (!catalog.triggers.length) catalog = await api.getNotifyCatalog();
      webhooks = await api.getWebhooks();
    } catch (e) {
      content.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
      return;
    }
    if (tab === "webhooks") drawWebhooks(content);
    else if (tab === "smtp") drawSmtp(content);
    else drawRules(content);
  }

  // ================= WEBHOOKS =================

  function webhookForm(w) {
    const isEdit = !!w;
    w = w || { type: "custom", name: "", url: "", headers: "", body_template: "" };
    return `
      <div class="form-row"><label>Typ</label>
        <select id="wf-type">
          <option value="discord" ${w.type === "discord" ? "selected" : ""}>Discord</option>
          <option value="custom" ${w.type !== "discord" ? "selected" : ""}>Benutzerdefiniert (Custom)</option>
        </select></div>
      <div class="form-row"><label>Name</label>
        <input id="wf-name" value="${esc(w.name)}" placeholder="z.B. Alerts-Channel" /></div>
      <div class="form-row"><label>Webhook-URL</label>
        <input id="wf-url" value="${esc(w.url)}" placeholder="https://…" /></div>
      <div id="wf-custom" style="${(w.type || "custom") === "discord" ? "display:none" : ""}">
        <div class="form-row"><label>Eigene HTTP-Header (JSON-Objekt, optional)</label>
          <textarea id="wf-headers" rows="3" style="font-family:monospace;font-size:12px"
            placeholder='{"Authorization": "Bearer …", "X-Api-Key": "…"}'>${esc(w.headers || "")}</textarea></div>
        <div class="form-row"><label>Body-Template (optional, leer = Standard-JSON)</label>
          <textarea id="wf-body" rows="4" style="font-family:monospace;font-size:12px"
            placeholder='{"text": "{head}\\n{body}", "level": "{level}"}'>${esc(w.body_template || "")}</textarea>
          <div style="font-size:11px;color:var(--subtext);margin-top:3px">${PLACEHOLDER_HELP}</div></div>
      </div>
      <div id="wf-error" class="form-error hidden"></div>
      <button class="btn-primary" id="wf-save" style="margin-top:4px">${isEdit ? tr("u_anderungen_speichern") : "+ Webhook speichern"}</button>
      ${isEdit ? `<button class="taskbar-btn" id="wf-cancel" style="margin-left:6px">Abbrechen</button>` : ""}`;
  }

  function bindWebhookForm(root, editId, done) {
    const typeSel = root.querySelector("#wf-type");
    typeSel.addEventListener("change", () => {
      root.querySelector("#wf-custom").style.display = typeSel.value === "discord" ? "none" : "";
    });
    root.querySelector("#wf-save").addEventListener("click", async () => {
      const err = root.querySelector("#wf-error");
      err.classList.add("hidden");
      const data = {
        type: typeSel.value,
        name: root.querySelector("#wf-name").value.trim(),
        url: root.querySelector("#wf-url").value.trim(),
        headers: root.querySelector("#wf-headers")?.value.trim() || null,
        body_template: root.querySelector("#wf-body")?.value.trim() || null,
      };
      if (!data.name || !data.url) { err.textContent = tr("u_name_und_url_erforderlich"); err.classList.remove("hidden"); return; }
      if (data.headers) {
        try { const p = JSON.parse(data.headers); if (typeof p !== "object" || Array.isArray(p)) throw 0; }
        catch { err.textContent = tr("u_header_mussen_ein_json_objekt_sein"); err.classList.remove("hidden"); return; }
      }
      try {
        if (editId) await api.updateWebhook(editId, data);
        else await api.createWebhook(data);
        window.notify?.("Webhook gespeichert", "success");
        done();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });
    root.querySelector("#wf-cancel")?.addEventListener("click", done);
  }

  function drawWebhooks(root) {
    root.innerHTML = `
      <h3>${tr("nf_add_webhook")}</h3>
      <p style="color:var(--subtext);font-size:13px">
        ${tr("nf_webhook_hint")}</p>
      <div id="wh-add">${webhookForm(null)}</div>
      <h3 style="margin-top:22px">${tr("nf_configured_webhooks")}</h3>
      <div id="wh-list"></div>`;
    bindWebhookForm(root.querySelector("#wh-add"), null, draw);

    const listEl = root.querySelector("#wh-list");
    if (!webhooks.length) {
      listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">${tr("nf_no_webhooks")}</div>`;
      return;
    }
    listEl.innerHTML = webhooks.map((w) => `
      <div class="panel" style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div>
            <strong>${esc(w.name)}</strong>
            <span style="font-size:11px;color:var(--subtext);margin-left:6px">${esc(w.type)}</span>
            ${w.headers ? `<span style="font-size:10.5px;background:var(--panel-2,#1b2740);border-radius:6px;padding:1px 6px;margin-left:4px">Header</span>` : ""}
            ${w.body_template ? `<span style="font-size:10.5px;background:var(--panel-2,#1b2740);border-radius:6px;padding:1px 6px;margin-left:4px">Template</span>` : ""}
            <div style="font-size:11px;color:var(--subtext);font-family:monospace;margin-top:2px">${esc(String(w.url).slice(0, 56))}…</div>
          </div>
          <div style="display:flex;gap:6px">
            <button class="taskbar-btn" data-edit="${w.id}">${tr("edit")}</button>
            <button class="taskbar-btn" data-test="${w.id}">${tr("nf_test")}</button>
            <button class="taskbar-btn" data-del="${w.id}">${tr("delete")}</button>
          </div>
        </div>
        <div class="wh-editbox" data-editbox="${w.id}" style="display:none;margin-top:10px;border-top:1px solid var(--border);padding-top:10px"></div>
      </div>`).join("");

    listEl.querySelectorAll("[data-edit]").forEach((btn) => btn.addEventListener("click", () => {
      const box = listEl.querySelector(`[data-editbox="${btn.dataset.edit}"]`);
      const w = webhooks.find((x) => x.id === btn.dataset.edit);
      if (box.style.display === "none") {
        box.style.display = "";
        box.innerHTML = webhookForm(w);
        bindWebhookForm(box, w.id, draw);
      } else box.style.display = "none";
    }));
    listEl.querySelectorAll("[data-test]").forEach((btn) => btn.addEventListener("click", async () => {
      btn.textContent = "…";
      try { await api.testWebhook(btn.dataset.test); window.notify?.("Test-Benachrichtigung gesendet", "success"); }
      catch (e) { window.notify?.("Test fehlgeschlagen: " + e.message, "error"); }
      btn.textContent = "Testen";
    }));
    listEl.querySelectorAll("[data-del]").forEach((btn) => btn.addEventListener("click", async () => {
      if (!(await uiConfirm(tr("u_webhook_loschen_regeln_die_ihn_nut"), { okText: tr("delete"), danger: true }))) return;
      await api.deleteWebhook(btn.dataset.del); draw();
    }));
  }

  // ================= SMTP =================

  async function drawSmtp(root) {
    root.innerHTML = `<div style="color:var(--subtext);font-size:13px">Lade…</div>`;
    let cfg;
    try { cfg = await api.getSmtp(); }
    catch (e) { root.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    root.innerHTML = `
      <h3>${tr("nf_smtp_title")}</h3>
      <p style="color:var(--subtext);font-size:13px">
        ${tr("nf_smtp_hint")}</p>
      <div style="display:flex;gap:12px">
        <div class="form-row" style="flex:2"><label>${tr("nf_server_host")}</label>
          <input id="sm-host" value="${esc(cfg.host)}" placeholder="smtp.gmail.com" /></div>
        <div class="form-row" style="flex:1"><label>Port</label>
          <input id="sm-port" type="number" value="${esc(String(cfg.port))}" /></div>
        <div class="form-row" style="flex:1"><label>${tr("nf_security")}</label>
          <select id="sm-sec">
            <option value="starttls" ${cfg.security === "starttls" ? "selected" : ""}>STARTTLS (587)</option>
            <option value="ssl" ${cfg.security === "ssl" ? "selected" : ""}>SSL/TLS (465)</option>
            <option value="none" ${cfg.security === "none" ? "selected" : ""}>${tr("nf_none")}</option>
          </select></div>
      </div>
      <div style="display:flex;gap:12px">
        <div class="form-row" style="flex:1"><label>${tr("username")}</label>
          <input id="sm-user" value="${esc(cfg.user)}" placeholder="user@gmail.com" /></div>
        <div class="form-row" style="flex:1"><label>${tr("password")}</label>
          <input id="sm-pass" type="password" placeholder="${cfg.password ? tr("nf_pw_stored") : ""}" /></div>
      </div>
      <div class="form-row"><label>${tr("nf_from_addr")}</label>
        <input id="sm-from" value="${esc(cfg.from_addr)}" placeholder="rmm@firma.de (leer = Benutzername)" /></div>
      <div id="sm-error" class="form-error hidden"></div>
      <div style="display:flex;gap:8px;margin-top:6px;align-items:center">
        <button class="btn-primary" id="sm-save">${tr("save")}</button>
        <input id="sm-testto" placeholder="${tr("nf_test_to")}" style="flex:1;max-width:260px" />
        <button class="taskbar-btn" id="sm-test">${tr("nf_send_test")}</button>
      </div>`;
    const err = root.querySelector("#sm-error");
    root.querySelector("#sm-save").addEventListener("click", async () => {
      err.classList.add("hidden");
      const pass = root.querySelector("#sm-pass").value;
      try {
        await api.setSmtp({
          host: root.querySelector("#sm-host").value.trim(),
          port: parseInt(root.querySelector("#sm-port").value, 10) || 587,
          user: root.querySelector("#sm-user").value.trim(),
          password: pass === "" ? null : pass,   // leer lassen = unverändert
          from_addr: root.querySelector("#sm-from").value.trim(),
          security: root.querySelector("#sm-sec").value,
        });
        window.notify?.(tr("u_smtp_einstellungen_gespeichert"), "success");
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });
    root.querySelector("#sm-test").addEventListener("click", async () => {
      err.classList.add("hidden");
      const to = root.querySelector("#sm-testto").value.trim();
      if (!to) { err.textContent = tr("u_test_empfanger_angeben"); err.classList.remove("hidden"); return; }
      try { await api.testSmtp(to); window.notify?.("Test-Mail verschickt ✔", "success"); }
      catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });
  }

  // ================= REGELN =================

  const CHANNEL_LABELS = { email: "✉️ E-Mail", webhook: "🔗 Webhook", dashboard: "🖥️ Dashboard-Toast" };
  const PARAM_LABELS = { days_before: "Vorlauf (Tage)", threshold: "Schwellwert" };
  const PARAM_DEFAULTS = { days_before: 30, threshold: 90 };

  function triggerLabel(key) {
    return (catalog.triggers.find((t) => t.key === key) || {}).label || key;
  }

  function ruleForm(rule) {
    rule = rule || { trigger: "client_offline", channel: "email", client_ids: "", target: "", params: {}, name: "" };
    const selected = new Set((rule.client_ids || "").split(",").filter(Boolean));
    const clients = state.clients || [];
    return `
      <div class="form-row"><label>Name der Regel</label>
        <input class="rf-name" value="${esc(rule.name || "")}" placeholder="z.B. Garantie-Warnung Server" /></div>
      <div style="display:flex;gap:12px">
        <div class="form-row" style="flex:1"><label>Wenn (Trigger)</label>
          <select class="rf-trigger">
            ${catalog.triggers.map((t) => `<option value="${t.key}" ${rule.trigger === t.key ? "selected" : ""}>${esc(t.label)}</option>`).join("")}
          </select></div>
        <div class="form-row" style="flex:1"><label>Dann (Kanal)</label>
          <select class="rf-channel">
            ${catalog.channels.map((c) => `<option value="${c}" ${rule.channel === c ? "selected" : ""}>${CHANNEL_LABELS[c] || c}</option>`).join("")}
          </select></div>
      </div>
      <div class="rf-params"></div>
      <div class="form-row rf-target-row"><label class="rf-target-label"></label><span class="rf-target-slot"></span></div>
      <div class="form-row"><label>Clients (leer = alle Clients)</label>
        <div style="max-height:130px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:6px 8px">
          ${clients.map((c) => `
            <label style="display:flex;gap:7px;align-items:center;font-size:12.5px;padding:2px 0;cursor:pointer">
              <input type="checkbox" class="rf-client" value="${c.id}" ${selected.has(c.id) ? "checked" : ""} />
              <span>${esc(c.hostname || c.id)}</span>
            </label>`).join("") || `<span style="font-size:12px;color:var(--subtext)">Keine Clients vorhanden.</span>`}
        </div></div>
      <div class="form-error hidden rf-error"></div>`;
  }

  function bindRuleForm(root, rule) {
    const triggerSel = root.querySelector(".rf-trigger");
    const channelSel = root.querySelector(".rf-channel");

    function drawParams() {
      const t = catalog.triggers.find((x) => x.key === triggerSel.value) || { params: [] };
      const params = (rule && rule.params) || {};
      root.querySelector(".rf-params").innerHTML = t.params.map((p) => `
        <div class="form-row"><label>${PARAM_LABELS[p] || p}${p === "threshold" ? (triggerSel.value === "temp_high" ? " (°C)" : " (%)") : ""}</label>
          <input type="number" class="rf-param" data-param="${p}"
                 value="${esc(String(params[p] ?? PARAM_DEFAULTS[p] ?? ""))}" style="max-width:130px" /></div>`).join("");
    }
    function drawTarget() {
      const row = root.querySelector(".rf-target-row");
      const label = root.querySelector(".rf-target-label");
      const slot = root.querySelector(".rf-target-slot");
      const ch = channelSel.value;
      if (ch === "email") {
        row.style.display = "";
        label.textContent = "An E-Mail-Adresse(n), Komma-getrennt";
        slot.innerHTML = `<input class="rf-target" value="${esc(rule?.target || "")}" placeholder="user@gmail.de, chef@firma.de" style="width:100%" />`;
      } else if (ch === "webhook") {
        row.style.display = "";
        label.textContent = "An Webhook";
        slot.innerHTML = webhooks.length
          ? `<select class="rf-target" style="width:100%">${webhooks.map((w) =>
              `<option value="${w.id}" ${rule?.target === w.id ? "selected" : ""}>${esc(w.name)} (${esc(w.type)})</option>`).join("")}</select>`
          : `<span style="font-size:12.5px;color:var(--warn,#f5a524)">Erst im Tab „Webhooks" einen Webhook anlegen.</span>`;
      } else {
        row.style.display = "none";
        slot.innerHTML = "";
      }
    }
    triggerSel.addEventListener("change", drawParams);
    channelSel.addEventListener("change", drawTarget);
    drawParams(); drawTarget();

    return function collect() {
      const err = root.querySelector(".rf-error");
      err.classList.add("hidden");
      const params = {};
      root.querySelectorAll(".rf-param").forEach((i) => {
        if (i.value !== "") params[i.dataset.param] = parseFloat(i.value);
      });
      const data = {
        name: root.querySelector(".rf-name").value.trim()
          || triggerLabel(triggerSel.value),
        trigger: triggerSel.value,
        channel: channelSel.value,
        target: root.querySelector(".rf-target")?.value?.trim() || "",
        client_ids: [...root.querySelectorAll(".rf-client:checked")].map((i) => i.value).join(","),
        params,
      };
      if ((data.channel === "email" || data.channel === "webhook") && !data.target) {
        err.textContent = tr("u_ziel_e_mail_bzw_webhook_erforderli");
        err.classList.remove("hidden");
        return null;
      }
      return data;
    };
  }

  async function drawRules(root) {
    root.innerHTML = `<div style="color:var(--subtext);font-size:13px">Lade…</div>`;
    let rules = [];
    try { rules = await api.getNotifyRules(); }
    catch (e) { root.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }

    const clientName = (id) => (state.clients.find((c) => c.id === id) || {}).hostname || id;

    root.innerHTML = `
      <h3>${tr("nf_new_rule")}</h3>
      <p style="color:var(--subtext);font-size:13px">
        ${tr("nf_rule_hint")}</p>
      <div id="rl-add">${ruleForm(null)}
        <button class="btn-primary" id="rl-save" style="margin-top:4px">+ ${tr("nf_save_rule")}</button></div>
      <h3 style="margin-top:22px">${tr("nf_active_rules")} (${rules.length})</h3>
      <div id="rl-list"></div>`;

    const addCollect = bindRuleForm(root.querySelector("#rl-add"), null);
    root.querySelector("#rl-save").addEventListener("click", async () => {
      const data = addCollect();
      if (!data) return;
      try { await api.createNotifyRule(data); window.notify?.(tr("nf_rule_saved"), "success"); draw(); }
      catch (e) { window.notify?.(e.message, "error"); }
    });

    const listEl = root.querySelector("#rl-list");
    if (!rules.length) {
      listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">${tr("nf_no_rules")}</div>`;
      return;
    }
    listEl.innerHTML = rules.map((r) => {
      const ids = (r.client_ids || "").split(",").filter(Boolean);
      const clientsTxt = ids.length
        ? ids.slice(0, 3).map(clientName).map(esc).join(", ") + (ids.length > 3 ? ` +${ids.length - 3}` : "")
        : "alle Clients";
      let params = {};
      try { params = JSON.parse(r.params || "{}"); } catch {}
      const paramTxt = Object.entries(params).map(([k, v]) => `${PARAM_LABELS[k] || k}: ${v}`).join(", ");
      const targetTxt = r.channel === "webhook"
        ? ((webhooks.find((w) => w.id === r.target) || {}).name || tr("u_geloschter_webhook"))
        : r.target;
      return `
      <div class="panel" style="margin-bottom:8px;${r.enabled ? "" : "opacity:.55"}">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div style="min-width:0">
            <strong>${esc(r.name)}</strong>
            <div style="font-size:12px;color:var(--subtext);margin-top:2px">
              Wenn <b>${esc(triggerLabel(r.trigger))}</b>${paramTxt ? ` (${esc(paramTxt)})` : ""}
              auf <b>${clientsTxt}</b> →
              ${CHANNEL_LABELS[r.channel] || esc(r.channel)}${targetTxt ? ` an <b>${esc(targetTxt)}</b>` : ""}
            </div>
          </div>
          <div style="display:flex;gap:6px;flex:none">
            <button class="taskbar-btn" data-toggle="${r.id}" data-en="${r.enabled ? 0 : 1}">${r.enabled ? tr("nf_disable") : tr("au_enable")}</button>
            <button class="taskbar-btn" data-edit="${r.id}">${tr("edit")}</button>
            <button class="taskbar-btn" data-test="${r.id}">${tr("nf_test")}</button>
            <button class="taskbar-btn" data-del="${r.id}">${tr("delete")}</button>
          </div>
        </div>
        <div class="rl-editbox" data-editbox="${r.id}" style="display:none;margin-top:10px;border-top:1px solid var(--border);padding-top:10px"></div>
      </div>`;
    }).join("");

    listEl.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
      try { await api.updateNotifyRule(b.dataset.toggle, { enabled: b.dataset.en === "1" }); draw(); }
      catch (e) { window.notify?.(e.message, "error"); }
    }));
    listEl.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
      b.textContent = "…";
      try { await api.testNotifyRule(b.dataset.test); window.notify?.(tr("u_test_uber_den_regel_kanal_gesendet"), "success"); }
      catch (e) { window.notify?.(e.message, "error"); }
      b.textContent = tr("nf_test");
    }));
    listEl.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm(tr("u_regel_loschen"), { okText: tr("delete"), danger: true }))) return;
      await api.deleteNotifyRule(b.dataset.del); draw();
    }));
    listEl.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
      const box = listEl.querySelector(`[data-editbox="${b.dataset.edit}"]`);
      const r = rules.find((x) => x.id === b.dataset.edit);
      if (box.style.display !== "none") { box.style.display = "none"; return; }
      box.style.display = "";
      let params = {};
      try { params = JSON.parse(r.params || "{}"); } catch {}
      box.innerHTML = ruleForm({ ...r, params }) +
        `<button class="btn-primary rl-update" style="margin-top:4px">${tr("nf_save_changes")}</button>
         <button class="taskbar-btn rl-cancel" style="margin-left:6px">${tr("cancel")}</button>`;
      const collect = bindRuleForm(box, { ...r, params });
      box.querySelector(".rl-update").addEventListener("click", async () => {
        const data = collect();
        if (!data) return;
        try { await api.updateNotifyRule(r.id, data); window.notify?.("Regel aktualisiert", "success"); draw(); }
        catch (e) { window.notify?.(e.message, "error"); }
      });
      box.querySelector(".rl-cancel").addEventListener("click", () => { box.style.display = "none"; });
    }));
  }

  draw();
}

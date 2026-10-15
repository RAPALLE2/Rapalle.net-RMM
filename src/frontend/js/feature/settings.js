// apps/settings.js
// ----------------
// Einstellungen mit Reitern:
//   - Benutzer:        Benutzer anlegen/löschen, Gruppen zuweisen
//   - Gruppen & Rollen: Gruppen mit Rechten anlegen, an User/AD-Gruppen binden
//   - SSO / Verzeichnis: jetzt Modul "sso" (src/modules/frontend/sso)
//   - Benachrichtigungen: Verweis auf die Webhook-Verwaltung
//
// Das Rechte-System: Admins haben immer alle Rechte. Andere Benutzer erhalten
// Rechte über ihre Gruppen (z.B. Gruppe "Auditor" mit Recht "audit").

import { api } from "../app/api.js";
import { esc, uiConfirm } from "../app/utils.js";
import { condenseHints } from "../app/help.js";
import { buildSubTabs } from "../app/subtabs.js";
import { t } from "../app/i18n.js";
import { isAdmin, hasGlobalPerm } from "../app/state.js";
import { renderSource } from "./source.js";
import { registerCleanup } from "../app/windowmanager.js";
import { getModuleApi } from "../app/modules.js";

export function renderSettings(body, win) {
  // Direkt auf einem bestimmten Reiter oeffnen (z.B. angehefteter Favorit
  // "Einstellungen -> Source"). Faellt auf "general" zurueck, wenn nichts
  // uebergeben wurde oder das Recht fehlt (siehe draw()).
  let activeTab = (win && win.props && win.props.tab) || "general";
  let sourceCleanup = null;  // Aufräumen der Source-Shell (Socket/PTY) beim Verlassen

  // Beim Schließen des Einstellungen-Fensters die Source-Shell sicher beenden.
  if (win && win.key) {
    registerCleanup(win.key, () => { if (sourceCleanup) { try { sourceCleanup(); } catch {} } });
  }

  function draw() {
    // Vorherige Source-Shell sauber schließen, bevor neu gezeichnet wird.
    if (sourceCleanup) { try { sourceCleanup(); } catch {} sourceCleanup = null; }
    // Welche Reiter darf dieser Benutzer sehen?
    const admin = isAdmin();
    const canTab = {
      general: admin || hasGlobalPerm("see_settings"),
      users: admin || hasGlobalPerm("create_users"),
      branding: admin || hasGlobalPerm("manage_branding"),
      source: admin || hasGlobalPerm("see_source"),
    };
    const order = ["general", "users", "branding", "source"];
    const allowed = order.filter((k) => canTab[k]);
    if (!allowed.includes(activeTab)) activeTab = allowed[0] || "general";
    const tabLabel = { general: t("tab_general"), users: t("tab_users"),
      branding: "Branding",
      source: "Source" };
    body.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%">
        <div class="tab-bar" style="padding:10px 14px;border-bottom:1px solid var(--border);gap:6px">
          ${allowed.map((k) => `<button class="tab-btn ${activeTab === k ? "active" : ""}" data-t="${k}">${esc(tabLabel[k])}</button>`).join("")}
        </div>
        <div id="set-content" style="flex:1;overflow:auto"></div>
      </div>
    `;
    body.querySelectorAll("[data-t]").forEach((btn) =>
      btn.addEventListener("click", () => { activeTab = btn.dataset.t; draw(); })
    );
    const content = body.querySelector("#set-content");
    if (activeTab === "general") renderGeneralTab(content);
    else if (activeTab === "users") renderUsersTab(content);
    else if (activeTab === "branding") renderBrandingTab(content);
    else if (activeTab === "source") sourceCleanup = renderSource(content);
  }

  // ---------------- BRANDING (Logos per Upload ersetzen) ----------------
  async function renderBrandingTab(root) {
    root.innerHTML = `<div class="settings-section"><p style="color:var(--subtext);font-size:13px">Lade…</p></div>`;
    let data;
    try {
      data = await api.getBranding();
    } catch (e) {
      root.innerHTML = `<div class="settings-section"><div style="color:var(--danger)">${esc(e.message)}</div></div>`;
      return;
    }

    root.innerHTML = `
      <div class="settings-section">
        <h3>${t("set_br_title")}</h3>
        <p style="color:var(--subtext);font-size:13px;max-width:640px">
          ${t("set_br_hint")}
        </p>
        <div id="br-slots"></div>
        <div id="br-msg" style="margin-top:10px;font-size:13px"></div>
      </div>
    `;

    const slotsEl = root.querySelector("#br-slots");
    const msgEl = root.querySelector("#br-msg");

    slotsEl.innerHTML = data.slots.map((s) => `
      <div class="panel" style="display:flex;align-items:center;gap:14px;margin-bottom:10px;padding:10px">
        <div style="width:96px;height:56px;display:flex;align-items:center;justify-content:center;background:var(--panel-2);border-radius:8px;overflow:hidden">
          ${s.exists
            ? `<img src="${esc(s.url)}?v=${s.mtime}" alt="" style="max-width:100%;max-height:100%;object-fit:contain" data-preview="${esc(s.name)}" />`
            : `<span style="color:var(--subtext);font-size:11px">${t("set_br_missing")}</span>`}
        </div>
        <div style="flex:1">
          <strong style="font-size:13px">${esc(s.label)}</strong>
          <div style="color:var(--subtext);font-size:12px">${esc(s.name)}</div>
        </div>
        <input type="file" data-file="${esc(s.name)}" style="display:none"
          accept="image/*,.ico" />
        <button class="action-btn" data-pick="${esc(s.name)}">${t("set_br_pick")}</button>
      </div>
    `).join("");

    slotsEl.querySelectorAll("[data-pick]").forEach((btn) => {
      const name = btn.dataset.pick;
      const fileInput = slotsEl.querySelector(`[data-file="${name}"]`);
      btn.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", async () => {
        const file = fileInput.files[0];
        if (!file) return;
        msgEl.innerHTML = `<span style="color:var(--subtext)">${t("set_br_uploading", { name: esc(name) })}</span>`;
        try {
          const res = await api.uploadBranding(name, file);
          msgEl.innerHTML = `<span style="color:var(--accent)">✓ ${t("set_br_replaced", { name: esc(name), kb: Math.round((res.size || file.size) / 1024) })}</span>`;
          window.notify?.(t("set_br_saved", { name }), "success");
          // ALLE Stellen sofort aktualisieren (Cache-Busting via mtime):
          const bust = `?v=${res.mtime || Date.now()}`;
          const prev = slotsEl.querySelector(`[data-preview="${name}"]`);
          if (prev) prev.src = `/image/${name}${bust}`;
          if (name === "logo_r.png") {
            document.querySelectorAll(".topbar-logo").forEach((img) => { img.src = `/image/logo_r.png${bust}`; img.style.display = ""; });
            document.querySelectorAll('link[rel="icon"]').forEach((l) => { l.href = `/image/logo_r.png${bust}`; });
          }
          if (name === "login-bg.jpg") {
            // Der Login-Hintergrund kommt aus CSS (url("/image/login-bg.jpg"))
            // und wuerde sonst erst nach Strg+F5 erscheinen -> Inline-Override.
            const loginEl = document.querySelector(".login-screen, #login-screen");
            if (loginEl) loginEl.style.backgroundImage = `url("/image/login-bg.jpg${bust}")`;
          }
          if (!prev) renderBrandingTab(root); // Slot existierte vorher nicht -> neu zeichnen
        } catch (e) {
          msgEl.innerHTML = `<span style="color:var(--danger)">✗ ${esc(e.message)}</span>`;
          window.notify?.(`Branding-Upload "${name}" fehlgeschlagen: ${e.message}`, "error", 10000);
        } finally {
          fileInput.value = "";
        }
      });
    });
    condenseHints(root);
  }

  // ---------------- GENERAL ----------------
  async function renderGeneralTab(root) {
    root.innerHTML = `<div class="settings-section"><p style="color:var(--subtext);font-size:13px">${t("general_loading")}</p></div>`;
    let s;
    try {
      s = await api.getSettings();
    } catch (e) {
      root.innerHTML = `<div class="settings-section"><div style="color:var(--danger)">${esc(e.message)}</div></div>`;
      return;
    }

    root.innerHTML = `
      <div class="settings-section">
        <div data-adminsec>
        <h3>${t("general_server_title")}</h3>
        <p style="color:var(--subtext);font-size:13px">${t("general_server_hint")}</p>
        <div class="form-row">
          <label>${t("general_server_ip")}</label>
          <input type="text" id="ge-server-host" placeholder="192.168.1.10" value="${esc(s.server_host || "")}" />
        </div>
        <div class="form-row">
          <label>${t("general_server_domain")}</label>
          <input type="text" id="ge-server-domain" placeholder="rmm.meinefirma.de" value="${esc(s.server_domain || "")}" />
        </div>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>${t("general_backend_port")}</label>
            <input type="number" min="1" max="65535" id="ge-backend-port" value="${esc(String(s.server_backend_port ?? 4000))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>${t("general_frontend_port")}</label>
            <input type="number" min="1" max="65535" id="ge-frontend-port" value="${esc(String(s.server_frontend_port ?? 4000))}" />
          </div>
        </div>
        <div class="form-row">
          <label>${t("general_server_url_advanced")}</label>
          <input type="text" id="ge-server-url" placeholder="https://rmm.meinefirma.de" value="${esc(s.server_url || "")}" />
        </div>
        <p style="color:var(--subtext);font-size:12px;margin-top:-4px">
          ${t("set_proxy_hint", { origin: esc(window.location.origin) })}
        </p>

        <h3 style="margin-top:22px">${t("set_hostlock_title")}</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_hostlock_hint")}
        </p>
        <div style="background:#f5a52415;border:1px solid #f5a52455;border-radius:8px;
             padding:9px 12px;margin:8px 0;font-size:12px;line-height:1.5">
          ⚠️ ${t("set_hostlock_warn")}
        </div>
        <div class="form-row">
          <label>${t("set_hostlock_active")}</label>
          <select id="ge-hostlock">
            <option value="0" ${(s.host_lock_enabled || "0") !== "1" ? "selected" : ""}>${t("off")}</option>
            <option value="1" ${(s.host_lock_enabled || "0") === "1" ? "selected" : ""}>${t("on")}</option>
          </select>
        </div>
        <div class="form-row">
          <label>${t("set_hostlock_scope")}</label>
          <select id="ge-hostlock-scope">
            <option value="ui" ${(s.host_lock_scope || "ui") === "ui" ? "selected" : ""}>${t("set_hostlock_scope_ui")}</option>
            <option value="all" ${(s.host_lock_scope || "ui") === "all" ? "selected" : ""}>${t("set_hostlock_scope_all")}</option>
          </select>
        </div>
        <div class="form-row">
          <label>${t("set_hostlock_extra")}</label>
          <input type="text" id="ge-hostlock-extra" placeholder="rmm.intern, 10.0.0.5"
                 value="${esc(s.host_lock_extra || "")}" />
        </div>
        <div class="form-row">
          <label>${t("set_behind_proxy")}</label>
          <select id="ge-hostlock-proxy">
            <option value="0" ${(s.host_lock_trust_proxy || "0") !== "1" ? "selected" : ""}>${t("no")}</option>
            <option value="1" ${(s.host_lock_trust_proxy || "0") === "1" ? "selected" : ""}>${t("set_proxy_yes")}</option>
          </select>
        </div>
        <p style="color:var(--subtext);font-size:12px;margin-top:-4px">
          ${t("set_proxy_warn")}
        </p>
        </div>

        <h3 style="margin-top:24px">${t("general_metrics_title")}</h3>
        <p style="color:var(--subtext);font-size:13px">${t("general_metrics_hint")}</p>
        <div class="form-row">
          <label>${t("general_metrics_interval")}</label>
          <input type="number" min="10" step="10" id="ge-interval" value="${esc(String(s.metrics_interval_seconds ?? 60))}" />
        </div>
        <div class="form-row">
          <label>${t("general_metrics_retention")}</label>
          <input type="number" min="1" step="1" id="ge-retention" value="${esc(String(s.metrics_retention_hours ?? 1))}" />
        </div>
        <div class="form-row">
          <label>${t("general_replay_retention")}</label>
          <input type="number" min="1" step="1" id="ge-replay" value="${esc(String(s.replay_retention_days ?? 10))}" />
        </div>

        <div data-adminsec>
        <h3 style="margin-top:24px">Update</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_srvupd_hint")}
        </p>
        <div id="up-panel" style="margin-top:12px;border:1px solid var(--border);border-radius:8px;padding:12px;background:var(--panel-2)">
          <div id="up-loading" style="color:var(--subtext);font-size:13px">${t("set_srvupd_loading")}</div>
          <div id="up-content" class="hidden">
            <div style="font-size:13px;margin-bottom:10px">
              ${t("set_installed_version")}: <b id="up-current"></b>
            </div>
            <div class="form-row">
              <label>GitHub-Repo (backend/repo.txt)</label>
              <div style="display:flex;gap:8px">
                <input type="text" id="up-repo" style="flex:1" />
                <button class="btn" id="up-repo-save">${t("save")}</button>
              </div>
            </div>
            <div class="form-row">
              <label>${t("set_update_target")}</label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="radio" name="up-target" value="commit" checked /> <span id="up-lbl-commit">${t("set_latest_commit")}</span>
              </label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="radio" name="up-target" value="full" /> <span id="up-lbl-full">${t("set_latest_full")}</span>
              </label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="radio" name="up-target" value="any" /> <span id="up-lbl-any">${t("set_latest_any")}</span>
              </label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="radio" name="up-target" value="custom" /> Custom:
                <select id="up-custom" style="flex:1"></select>
              </label>
            </div>
            <div id="up-error" class="form-error hidden"></div>
            <button class="btn-primary" id="up-run" style="margin-top:6px">⬇️ ${t("set_install_update")}</button>

            <h4 style="margin-top:18px">Auto-Update</h4>
            <div class="form-row">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                <input type="checkbox" id="up-auto" ${(s.server_auto_update ?? "0") === "1" ? "checked" : ""} />
                ${t("set_auto_update_server")}
              </label>
            </div>
            <div class="form-row">
              <label>${t("set_auto_update_channel")}</label>
              <select id="up-auto-channel">
                <option value="commit" ${(s.server_auto_update_channel || "full") === "commit" ? "selected" : ""}>${t("set_latest_commit")}</option>
                <option value="full" ${(s.server_auto_update_channel || "full") === "full" ? "selected" : ""}>${t("set_latest_full")}</option>
                <option value="any" ${(s.server_auto_update_channel || "full") === "any" ? "selected" : ""}>${t("set_latest_any")}</option>
              </select>
            </div>
            <p style="color:var(--subtext);font-size:12px;margin:2px 0 0">
              ${t("set_auto_update_note", { save: esc(t("save")) })}
            </p>
          </div>
        </div>
        </div>

        <h3 style="margin-top:24px">${t("set_agent_autoupd")}</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_agent_autoupd_hint")}
        </p>
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="ge-autoupdate" ${(s.agent_auto_update ?? "0") === "1" ? "checked" : ""} />
            ${t("set_agent_autoupd_on")}
          </label>
        </div>
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="ge-autoupdate-offline" ${(s.agent_auto_update_offline ?? "1") === "1" ? "checked" : ""} />
            ${t("set_agent_autoupd_offline")}
          </label>
        </div>
        <p style="color:var(--subtext);font-size:12px;margin-top:-4px">
          ${t("set_agent_autoupd_offline_hint")}
        </p>
        <p style="color:var(--subtext);font-size:13px;margin-top:10px">
          ${t("set_update_all_hint")}
        </p>
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
            <input type="checkbox" id="ge-updateall-offline" />
            ${t("set_update_all_offline")}
          </label>
        </div>
        <div class="form-row">
          <button class="btn" id="ge-updateall">⬆️ ${t("set_update_all_now")}</button>
          <span id="ge-updateall-msg" style="margin-left:10px;font-size:12px;color:var(--subtext)"></span>
        </div>


        <div id="ge-error" class="form-error hidden"></div>
        <button class="btn-primary" id="ge-save" style="margin-top:8px">${t("save")}</button>



      </div>
    `;

    // ---- Rechte-Gating im General-Tab ----
    // Admin-Sektionen (Server-IP/Ports, GitHub-Update, Datenbank, guacd)
    // brauchen 'admin_settings'; die Standard-Einstellungen 'manage_settings'.
    // Mit nur 'see_settings' ist alles sichtbar, aber schreibgeschützt.
    // Spotify-Redirect-URI-Beispiel mit der echten Adresse füllen
    // Redirect-URI = "Vollständige URL" (server_url) mit / am Ende.
    // Kein eigenes Feld: Sie folgt live dem Eingabefeld darüber, damit man
    // sofort sieht, was im Spotify-Dashboard stehen muss.
    const mayAdminSet = isAdmin() || hasGlobalPerm("admin_settings");
    const mayManageSet = isAdmin() || hasGlobalPerm("manage_settings");
    if (!mayAdminSet) {
      root.querySelectorAll("[data-adminsec], [data-adminsec-h]").forEach((el) => el.remove());
    }
    if (!mayManageSet) {
      root.querySelectorAll("input, select, button, textarea").forEach((el) => {
        el.disabled = true;
        el.title = t("u_keine_berechtigung_standard_einste");
      });
    }

    root.querySelector("#ge-save").addEventListener("click", async () => {
      const err = root.querySelector("#ge-error");
      err.classList.add("hidden");
      // Hilfsfunktionen: Admin-Felder können (ohne 'admin_settings') aus dem
      // DOM entfernt sein - dann werden sie einfach nicht mitgeschickt.
      const val = (id) => root.querySelector(`#${id}`)?.value;
      const has = (id) => !!root.querySelector(`#${id}`);
      const payload = {
        metrics_interval_seconds: parseInt(val("ge-interval"), 10) || 60,
        metrics_retention_hours: parseInt(val("ge-retention"), 10) || 1,
        replay_retention_days: parseInt(val("ge-replay"), 10) || 10,
        agent_auto_update: root.querySelector("#ge-autoupdate").checked ? "1" : "0",
        agent_auto_update_offline: root.querySelector("#ge-autoupdate-offline").checked ? "1" : "0",
      };
      if (has("ge-server-host")) {
        payload.server_host = val("ge-server-host").trim();
        payload.server_domain = val("ge-server-domain").trim();
        payload.server_backend_port = parseInt(val("ge-backend-port"), 10) || 4000;
        payload.server_frontend_port = parseInt(val("ge-frontend-port"), 10) || 4000;
        payload.server_url = val("ge-server-url").trim();
        payload.host_lock_enabled = val("ge-hostlock");
        payload.host_lock_scope = val("ge-hostlock-scope");
        payload.host_lock_extra = val("ge-hostlock-extra").trim();
        payload.host_lock_trust_proxy = val("ge-hostlock-proxy");
      }
      // Auto-Update (Sektion "Update") hat keinen eigenen Speichern-Knopf mehr -
      // der Zustand haengt jetzt am grossen Knopf hier unten.
      if (has("up-auto")) {
        payload.server_auto_update = root.querySelector("#up-auto").checked ? "1" : "0";
        payload.server_auto_update_channel = val("up-auto-channel");
      }
      try {
        await api.updateSettings(payload);
        window.notify?.(t("general_saved"), "success");
      } catch (e) {
        err.textContent = e.message; err.classList.remove("hidden");
      }
    });

    root.querySelector("#ge-updateall")?.addEventListener("click", async () => {
      const btn = root.querySelector("#ge-updateall");
      const msg = root.querySelector("#ge-updateall-msg");
      const includeOffline = !!root.querySelector("#ge-updateall-offline")?.checked;
      const { uiConfirm } = await import("../app/utils.js");
      if (!(await uiConfirm("Alle Agenten jetzt aktualisieren?", {
        description: includeOffline
          ? t("u_fur_jeden_verbundenen_client_wird_")
          : t("u_fur_jeden_verbundenen_client_wird__2"),
        okText: "Jetzt aktualisieren" }))) return;
      btn.disabled = true;
      if (msg) msg.textContent = t("u_wird_ausgelost");
      try {
        const res = await api.updateAllAgents({ include_offline: includeOffline });
        const txt = t("set_upd_triggered", { n: res.triggered }) +
          (res.queued_offline ? `, ${t("set_upd_queued", { n: res.queued_offline })}` : "") +
          (res.offline && !res.queued_offline ? `, ${t("set_upd_skipped", { n: res.offline })}` : "");
        if (msg) msg.textContent = txt + " – " + t("set_upd_notify_short");
        window.notify?.(txt + ". " + t("set_upd_notify_long"), "info", 8000);
      } catch (e) {
        if (msg) msg.textContent = "";
        window.notify?.("Fehlgeschlagen: " + e.message, "error");
      } finally {
        btn.disabled = false;
      }
    });

    // ---- Admin-only Verkabelung (Server-Update): nur wenn
    // die Sektionen (mit 'admin_settings') überhaupt im DOM sind. ----
    if (mayAdminSet) {
    // ---- Server-Update (Settings -> Update) ----
    const upPanel = root.querySelector("#up-panel");
    let upInfoLoaded = false;

    async function loadUpdateInfo() {
      const loading = root.querySelector("#up-loading");
      const content = root.querySelector("#up-content");
      loading.classList.remove("hidden"); content.classList.add("hidden");
      try {
        const info = await api.getServerUpdateInfo();
        root.querySelector("#up-current").textContent =
          info.current_version + (info.current_commit ? ` (${t("set_as_of")}: ${String(info.current_commit).slice(0, 10)})` : "");
        root.querySelector("#up-repo").value = info.repo_url || "";
        const c = info.latest_commit || {};
        root.querySelector("#up-lbl-commit").textContent =
          `${t("set_latest_commit")}${c.sha ? ` — ${c.sha.slice(0, 10)}` : ""}${c.message ? `: ${c.message}` : ""}`;
        root.querySelector("#up-lbl-full").textContent =
          `${t("set_latest_full")}${info.latest_full_tag ? ` — ${info.latest_full_tag}` : ` — ${t("set_none_found")}`}`;
        root.querySelector("#up-lbl-any").textContent =
          `${t("set_latest_any")}${info.latest_any_tag ? ` — ${info.latest_any_tag}` : ` — ${t("set_none_found")}`}`;
        const sel = root.querySelector("#up-custom");
        sel.innerHTML = (info.releases || []).length
          ? info.releases.map((r) => `<option value="${esc(r.tag)}">${esc(r.tag)}${r.alpha ? " (Alpha)" : " (Full)"}</option>`).join("")
          : `<option value="">${t("set_no_releases")}</option>`;
        upInfoLoaded = true;
        loading.classList.add("hidden"); content.classList.remove("hidden");
      } catch (e) {
        loading.textContent = `${t("error")}: ${e.message}`;
      }
    }

    // Der Bereich ist jetzt sofort offen - die GitHub-Infos werden direkt beim
    // Öffnen des Reiters geladen (kein "Update-Optionen anzeigen" mehr).
    if (upPanel) loadUpdateInfo();

    root.querySelector("#up-repo-save").addEventListener("click", async () => {
      const err = root.querySelector("#up-error"); err.classList.add("hidden");
      try {
        await api.setServerUpdateRepo(root.querySelector("#up-repo").value.trim());
        window.notify?.(t("set_repo_saved"), "success");
        upInfoLoaded = false; loadUpdateInfo();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });

    root.querySelector("#up-run").addEventListener("click", async () => {
      const err = root.querySelector("#up-error"); err.classList.add("hidden");
      const target = root.querySelector('input[name="up-target"]:checked').value;
      const tag = target === "custom" ? root.querySelector("#up-custom").value : null;
      if (target === "custom" && !tag) { err.textContent = t("u_bitte_ein_release_wahlen"); err.classList.remove("hidden"); return; }
      const ok = await uiConfirm(t("set_srvupd_confirm"),
        t("u_das_backend_ladt_den_gewahlten_sta"));
      if (!ok) return;
      const btn = root.querySelector("#up-run");
      btn.disabled = true; btn.textContent = t("u_update_lauft");
      try {
        const res = await api.runServerUpdate(target, tag);
        btn.textContent = "✓ Update eingespielt — Backend startet neu…";
        window.notify?.(`${res.applied} eingespielt — Backend startet neu. Seite gleich neu laden.`, "success");
        setTimeout(() => location.reload(), 8000);
      } catch (e) {
        btn.disabled = false; btn.textContent = "⬇️ Update installieren";
        err.textContent = e.message; err.classList.remove("hidden");
      }
    });

    // Datenbank (lokal/extern/Docker-DB) und Sicherungen: Modul "sql"
    // (src/modules/frontend/sql/settings.js)
    } // Ende Admin-only Verkabelung (mayAdminSet)

    // Zum Schluss die Seite aufraeumen:
    //  1. Erklaertexte wandern in "?"-Symbole neben Ueberschrift/Beschriftung.
    //  2. Aus den Ueberschriften werden Unterpunkte - sichtbar ist immer nur
    //     einer, der Speichern-Knopf bleibt unten stehen.
    condenseHints(root);
    buildSubTabs(root, { key: "settings-general", pinned: ["#ge-error", "#ge-save"] });
  }

  // ---------------- USERS ----------------
  // Standard-Rechte-Vorlagen beim Benutzer-Anlegen.
  const VIEW_ONLY_ALLOW = [
    "login", "see_dashboard", "restore_session", "edit_profile_name",
    "access_clients", "see_replay", "see_audit", "see_source", "see_permissions",
  ];
  function presetGrants(preset) {
    // Liefert {role, grants} für die gewählte Vorlage.
    // "full_admin" ist der alte Name derselben Sache - beide fuehren zur
    // Rolle "admin". Zwei Bezeichnungen fuer ein Recht waren nur verwirrend.
    if (preset === "admin" || preset === "full_admin") return { role: "admin", grants: null };
    // Support: Rechte kommen aus der Standard-Gruppe "Alle Support", die beim
    // Anlegen automatisch zugewiesen wird - deshalb hier keine eigenen Grants.
    if (preset === "support") return { role: "support", grants: null };
    // API-Benutzer (Modul "api"): Rechte kommen aus der Standard-Gruppe "API-Benutzer"
    // (Recht "API benutzen"); weitere Rechte je nach Einsatzzweck dazugeben.
    if (preset === "api") return { role: "api", grants: null };
    if (preset === "view_only") {
      return { role: "viewer",
        grants: VIEW_ONLY_ALLOW.map((p) => ({ scope: "global", perm: p, effect: "allow" })) };
    }
    // login_only
    return { role: "viewer", grants: [{ scope: "global", perm: "login", effect: "allow" }] };
  }

  // Wiederverwendbarer Gruppen-Auswahl-Dialog (Mehrfachauswahl in EINEM Menü).
  // Unverwaltete (AD-)Gruppen liegen in einem standardmäßig eingeklappten Ordner.
  // Gibt ein Array der gewählten Gruppen-IDs zurück oder null bei Abbruch.
  function pickGroupsModal(groups, currentIds = []) {
    return new Promise((resolve) => {
      const cur = new Set(currentIds);
      const managed = groups.filter((g) => !g.unmanaged);
      const unmanaged = groups.filter((g) => g.unmanaged);
      const adBadge = (g) => g.is_ad_group
        ? ' <span style="color:var(--accent);font-size:10px">AD</span>' : "";
      const rowHtml = (g) => `
        <label style="display:flex;align-items:center;gap:8px;padding:5px 4px;font-size:13px;cursor:pointer;border-radius:6px">
          <input type="checkbox" class="gp-chk" value="${esc(g.id)}" ${cur.has(g.id) ? "checked" : ""} />
          <span>${esc(g.name)}${adBadge(g)}</span>
        </label>`;
      const overlay = document.createElement("div");
      overlay.style.cssText = `position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,0.5);
        display:flex;align-items:center;justify-content:center`;
      overlay.innerHTML = `
        <div style="background:var(--panel,#131c2b);color:var(--text,#e8eef7);border:1px solid var(--border,#2a3648);
          border-radius:12px;min-width:340px;max-width:460px;max-height:70vh;display:flex;flex-direction:column;
          padding:16px;box-shadow:0 16px 48px rgba(0,0,0,0.5)">
          <div style="font-size:14px;font-weight:600;margin-bottom:10px">Gruppen zuweisen</div>
          <div style="flex:1;overflow:auto">
            ${managed.length ? managed.map(rowHtml).join("")
              : `<div style="color:var(--subtext);font-size:12px">${t("set_no_managed_groups")}</div>`}
            ${unmanaged.length ? `
              <details style="margin-top:10px">
                <summary style="cursor:pointer;font-size:12px;color:var(--subtext)">📁 ${t("set_ad_unmanaged")} (${unmanaged.length})</summary>
                <div style="margin-top:4px">${unmanaged.map(rowHtml).join("")}</div>
              </details>` : ""}
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
            <button class="btn" id="gp-cancel">${t("cancel")}</button>
            <button class="btn-primary" id="gp-ok">${t("apply")}</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      const done = (val) => { overlay.remove(); resolve(val); };
      overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) done(null); });
      overlay.querySelector("#gp-cancel").addEventListener("click", () => done(null));
      overlay.querySelector("#gp-ok").addEventListener("click", () => {
        const ids = [...overlay.querySelectorAll(".gp-chk")].filter((c) => c.checked).map((c) => c.value);
        done(ids);
      });
    });
  }

  function renderUsersTab(root) {
    root.innerHTML = `
      <div class="settings-section">
        <h3>${t("set_create_user")}</h3>
        <div class="form-row"><label>${t("username")}</label><input type="text" id="su-username" /></div>
        <div class="form-row"><label>${t("set_display_name")}</label><input type="text" id="su-display" /></div>
        <div class="form-row">
          <label>${t("set_default_role")}</label>
          <select id="su-role">
            <option value="admin">${t("set_role_admin")}</option>
            <option value="support">${t("set_role_support")}</option>
            <option value="view_only">${t("set_role_view")}</option>
            <option value="login_only">${t("set_role_login")}</option>
            ${getModuleApi("api") ? `<option value="api">API-Benutzer (nur REST-API)</option>` : ""}
          </select>
        </div>
        <div class="form-row" id="su-pwmode-row">
          <label>${t("set_pw_mode")}</label>
          <select id="su-pwmode">
            <option value="otp">${t("set_pw_otp")}</option>
            <option value="fixed">${t("set_pw_fixed")}</option>
          </select>
        </div>
        <div class="form-row hidden" id="su-pw-row"><label>${t("password")}</label><input type="text" id="su-password" /></div>
        <div class="form-row">
          <label>${t("pm_groups")}</label>
          <div style="display:flex;align-items:center;gap:10px">
            <button class="btn" id="su-groups-btn" type="button">➕ ${t("set_add_groups")}</button>
            <span id="su-groups-info" style="color:var(--subtext);font-size:12px">${t("u_keine_ausgewahlt")}</span>
          </div>
        </div>
        <div id="su-error" class="form-error hidden"></div>
        <button class="btn-primary" id="su-create" style="margin-top:8px">${t("set_create_user")}</button>
        <div id="su-result" style="margin-top:14px"></div>

        <h3 style="margin-top:26px">${t("set_existing_users")}</h3>
        <table class="data-table">
          <thead><tr><th>${t("username")}</th><th>${t("name")}</th><th>${t("set_role")}</th><th>${t("pm_groups")}</th><th></th></tr></thead>
          <tbody id="su-list"><tr><td colspan="5" style="color:var(--subtext)">${t("loading")}</td></tr></tbody>
        </table>
      </div>
    `;

    const pwMode = root.querySelector("#su-pwmode");
    const pwRow = root.querySelector("#su-pw-row");
    pwMode.addEventListener("change", () => pwRow.classList.toggle("hidden", pwMode.value !== "fixed"));

    // Für "Gruppen hinzufügen" beim Anlegen vorgemerkte Gruppen.
    let pendingGroups = [];
    let allGroupsCache = [];
    api.getGroups().then((g) => { allGroupsCache = g || []; }).catch(() => {});
    const groupsInfo = root.querySelector("#su-groups-info");
    root.querySelector("#su-groups-btn").addEventListener("click", async () => {
      if (!allGroupsCache.length) {
        try { allGroupsCache = await api.getGroups(); } catch {}
      }
      if (!allGroupsCache.length) { window.notify?.(t("u_es_gibt_noch_keine_gruppen"), "warn"); return; }
      const picked = await pickGroupsModal(allGroupsCache, pendingGroups);
      if (picked === null) return;
      pendingGroups = picked;
      groupsInfo.textContent = picked.length
        ? t("set_groups_picked", { n: picked.length }) : t("u_keine_ausgewahlt");
    });

    async function loadUsers() {
      const list = root.querySelector("#su-list");
      try {
        const [users, groups] = await Promise.all([api.getUsers(), api.getGroups().catch(() => [])]);
        list.innerHTML = "";
        for (const u of users) {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td>${esc(u.username)}</td>
            <td>${esc(u.display_name)}</td>
            <td>${esc(u.role)}${(u.is_admin && u.admin_via === "permission")
                ? ` <span style="color:var(--warn);font-size:10px" title="${t("set_admin_via_perm")}">ADMIN*</span>` : ""}${
                u.role === "api" ? ` <span style="color:var(--subtext)">(API-Konto, Schlüssel statt Passwort)</span>`
                : u.must_change_pw ? ` <span style="color:var(--warn)">(${t("set_pw_change_pending")})</span>` : ""}</td>
            <td><button class="btn" data-groups="${u.id}">${t("pm_groups")}…</button></td>
            <td><button class="btn btn-danger" data-del="${u.id}">${t("delete")}</button></td>`;
          list.appendChild(tr);
        }
        list.querySelectorAll("[data-del]").forEach((btn) =>
          btn.addEventListener("click", async () => {
            if (!(await uiConfirm(t("u_benutzer_wirklich_loschen"), { okText: t("delete"), danger: true }))) return;
            try { await api.deleteUser(btn.dataset.del); loadUsers(); }
            catch (e) { window.notify?.(e.message, "error"); }
          })
        );
        list.querySelectorAll("[data-groups]").forEach((btn) =>
          btn.addEventListener("click", () => editUserGroups(btn.dataset.groups, groups))
        );
      } catch (e) {
        list.innerHTML = `<tr><td colspan="5" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
      }
    }

    async function editUserGroups(userId, groups) {
      if (!groups.length) { window.notify?.(t("u_es_gibt_noch_keine_gruppen_im_bere"), "warn"); return; }
      const current = await api.getUserGroups(userId).then((r) => r.group_ids).catch(() => []);
      // EIN Auswahlmenü statt Gruppe-für-Gruppe abzufragen.
      const chosen = await pickGroupsModal(groups, current);
      if (chosen === null) return;   // abgebrochen
      await api.setUserGroups(userId, chosen);
      window.notify?.("Gruppen aktualisiert", "success");
    }

    // API-Benutzer: kein Passwort, keine Erstanmeldung - der Server erzeugt einen API-Schlüssel.
    const roleSel = root.querySelector("#su-role");
    const syncApiRole = () => {
      const isApi = roleSel.value === "api";
      root.querySelector("#su-pwmode-row").classList.toggle("hidden", isApi);
      if (isApi) root.querySelector("#su-pw-row").classList.add("hidden");
      else root.querySelector("#su-pw-row").classList.toggle("hidden", pwMode.value !== "fixed");
    };
    roleSel.addEventListener("change", syncApiRole);

    root.querySelector("#su-create").addEventListener("click", async () => {
      const err = root.querySelector("#su-error");
      const result = root.querySelector("#su-result");
      err.classList.add("hidden");
      const isFixed = pwMode.value === "fixed";
      const username = root.querySelector("#su-username").value.trim();
      const preset = root.querySelector("#su-role").value;   // admin|view_only|login_only
      const { role, grants } = presetGrants(preset);
      const payload = {
        username,
        display_name: root.querySelector("#su-display").value.trim() || username,
        role,
        one_time_password: !isFixed,
        password: isFixed ? root.querySelector("#su-password").value : null,
      };
      if (!payload.username) { err.textContent = "Benutzername fehlt"; err.classList.remove("hidden"); return; }
      try {
        const res = await api.createUser(payload);
        // Standard-Rechte-Vorlage anwenden (Grants) + vorgemerkte Gruppen zuweisen.
        const newId = res.id || res.user_id || (res.user && res.user.id);
        if (newId) {
          if (grants) { try { await api.setGrants("user", newId, grants); } catch {} }
          if (pendingGroups.length) { try { await api.setUserGroups(newId, pendingGroups); } catch {} }
        }
        if (res.api_key) {
          result.innerHTML = `<div style="background:rgba(45,212,191,0.1);border:1px solid var(--accent);padding:10px;border-radius:6px">
            API-Schlüssel für <b>${esc(res.username)}</b> – <b>wird nur jetzt angezeigt</b>:
            <div style="display:flex;gap:6px;margin:8px 0"><code id="su-apikey" style="flex:1;min-width:0;overflow-x:auto;white-space:nowrap;
              background:var(--panel-2);padding:6px 8px;border-radius:6px;color:var(--accent)">${esc(res.api_key)}</code>
              <button class="btn btn-sm" id="su-apikey-copy">Kopieren</button></div>
            <span style="color:var(--subtext);font-size:12px">Anmeldung an der REST-API: Benutzername + API-Schlüssel
              (POST /api/v1/auth/login {"username", "api_key"}). Neuer Schlüssel: Modul-App → REST-API → API-Konten.</span></div>`;
          result.querySelector("#su-apikey-copy").addEventListener("click", () => {
            navigator.clipboard?.writeText(res.api_key); window.notify?.("API-Schlüssel kopiert", "success");
          });
        } else if (res.generated_password) {
          result.innerHTML = `<div style="background:rgba(45,212,191,0.1);border:1px solid var(--accent);padding:10px;border-radius:6px">
            ${t("set_otp_for")} <b>${esc(res.username)}</b>: <code style="color:var(--accent)">${esc(res.generated_password)}</code><br/>
            <span style="color:var(--subtext);font-size:12px">${t("set_otp_note")}</span></div>`;
        } else {
          result.innerHTML = `<span style="color:var(--accent)">${t("set_user_created")}</span>`;
        }
        root.querySelector("#su-username").value = "";
        root.querySelector("#su-display").value = "";
        pendingGroups = [];
        groupsInfo.textContent = t("u_keine_ausgewahlt");
        loadUsers();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });

    loadUsers();
  }

  // ---------------- GROUPS & ROLES ----------------
  async function renderGroupsTab(root) {
    root.innerHTML = `<div class="settings-section"><p style="color:var(--subtext);font-size:13px">${t("groups_intro")}</p><div id="gr-perms"></div></div>`;
    const permsWrap = root.querySelector("#gr-perms");
    let allPerms = [];
    try { allPerms = (await api.getPermissions()).permissions; } catch { allPerms = []; }

    const permLabels = {
      login: t("perm_login"), screen: t("perm_screen"), terminal: t("perm_terminal"),
      explorer: t("perm_explorer"), quick_actions: t("perm_quick"), audit: t("perm_audit"),
      manage_users: t("perm_manage_users"), manage_clients: t("perm_manage_clients"),
      automation: t("perm_automation"),
    };

    permsWrap.innerHTML = `
      <h3>Neue Gruppe / Rolle</h3>
      <div class="form-row"><label>${t("group_name")}</label><input type="text" id="gr-name" placeholder="z.B. Auditor" /></div>
      <div class="form-row">
        <label>${t("permissions")}</label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">
          ${allPerms.map((p) => `<label style="font-size:13px"><input type="checkbox" class="gr-perm" value="${p}" /> ${esc(permLabels[p] || p)}</label>`).join("")}
        </div>
      </div>
      <button class="btn-primary" id="gr-add" style="margin-top:4px">+ Gruppe anlegen</button>
      <h3 style="margin-top:24px">Gruppen</h3>
      <div id="gr-list"></div>
    `;

    permsWrap.querySelector("#gr-add").addEventListener("click", async () => {
      const name = permsWrap.querySelector("#gr-name").value.trim();
      const permissions = Array.from(permsWrap.querySelectorAll(".gr-perm")).filter((c) => c.checked).map((c) => c.value);
      if (!name) { window.notify?.("Name fehlt", "warn"); return; }
      try { await api.createGroup({ name, permissions }); window.notify?.("Gruppe angelegt", "success"); renderGroupsTab(root); }
      catch (e) { window.notify?.(e.message, "error"); }
    });

    const listEl = permsWrap.querySelector("#gr-list");
    try {
      const groups = await api.getGroups();
      listEl.innerHTML = groups.length ? groups.map((g) => `
        <div class="panel" style="margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">
          <div><strong>${esc(g.name)}</strong>
            <div style="font-size:11px;color:var(--subtext);margin-top:2px">${g.permissions.map((p) => esc(permLabels[p] || p)).join(", ") || t("set_no_perms")}</div>
          </div>
          <button class="btn btn-danger" data-del="${g.id}">${t("delete")}</button>
        </div>`).join("") : `<div style="color:var(--subtext);font-size:13px">${t("set_no_groups")}</div>`;
      listEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async () => { await api.deleteGroup(btn.dataset.del); renderGroupsTab(root); })
      );
    } catch (e) {
      listEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
    }
  }

  // ---------------- SSO / REALMS ----------------
  // SSO / Verzeichnis-Anmeldung (Realms): Modul "sso" (src/modules/frontend/sso/settings.js)

  // ---------------- NOTIFICATIONS ----------------
  // Benachrichtigungen (Regeln, Webhooks, SMTP): Modul "notify"
  // (src/modules/frontend/notify/notifications.js)

  draw();
}

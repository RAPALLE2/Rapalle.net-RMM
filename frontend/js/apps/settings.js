// apps/settings.js
// ----------------
// Einstellungen mit Reitern:
//   - Benutzer:        Benutzer anlegen/löschen, Gruppen zuweisen
//   - Gruppen & Rollen: Gruppen mit Rechten anlegen, an User/AD-Gruppen binden
//   - SSO / Verzeichnis: Active-Directory-Realm konfigurieren (noch nicht live)
//   - Benachrichtigungen: Verweis auf die Webhook-Verwaltung
//
// Das Rechte-System: Admins haben immer alle Rechte. Andere Benutzer erhalten
// Rechte über ihre Gruppen (z.B. Gruppe "Auditor" mit Recht "audit").

import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";
import { condenseHints } from "../help.js";
import { buildSubTabs, removeSubSection } from "../subtabs.js";
import { t } from "../i18n.js";
import { isAdmin, hasGlobalPerm } from "../state.js";
import { renderSource } from "./source.js";
import { renderNotifications } from "./notifications.js";
import { registerCleanup } from "../windowmanager.js";

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
      sso: admin || hasGlobalPerm("manage_sso"),
      branding: admin || hasGlobalPerm("manage_branding"),
      notifications: admin || hasGlobalPerm("manage_settings"),
      source: admin || hasGlobalPerm("see_source"),
    };
    const order = ["general", "users", "sso", "branding", "notifications", "source"];
    const allowed = order.filter((k) => canTab[k]);
    if (!allowed.includes(activeTab)) activeTab = allowed[0] || "sso";
    const tabLabel = { general: t("tab_general"), users: t("tab_users"),
      sso: t("tab_sso"), branding: "Branding",
      notifications: t("tab_notifications"), source: "Source" };
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
    else if (activeTab === "sso") renderSsoTab(content);
    else if (activeTab === "branding") renderBrandingTab(content);
    else if (activeTab === "notifications") renderNotifTab(content);
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
          if (prev) prev.src = `/images/${name}${bust}`;
          if (name === "logo_r.png") {
            document.querySelectorAll(".topbar-logo").forEach((img) => { img.src = `/images/logo_r.png${bust}`; img.style.display = ""; });
            document.querySelectorAll('link[rel="icon"]').forEach((l) => { l.href = `/images/logo_r.png${bust}`; });
          }
          if (name === "login-bg.jpg") {
            // Der Login-Hintergrund kommt aus CSS (url("/images/login-bg.jpg"))
            // und wuerde sonst erst nach Strg+F5 erscheinen -> Inline-Override.
            const loginEl = document.querySelector(".login-screen, #login-screen");
            if (loginEl) loginEl.style.backgroundImage = `url("/images/login-bg.jpg${bust}")`;
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

        <h3 style="margin-top:22px">🔐 VPN (WireGuard-kompatibel)</h3>
        <p style="color:var(--subtext);font-size:13px">
          Der Tunnel wird im Backend in reinem Python abgewickelt. Auf den
          verwalteten Geräten wird nichts installiert – sie bauen die
          Verbindung mit Bordmitteln auf.
        </p>
        <div style="background:#f5a52415;border:1px solid #f5a52455;border-radius:8px;
             padding:9px 12px;margin:8px 0;font-size:12px;line-height:1.5">
          Der Tunnel-Port ist <b>UDP</b>. Ein Reverse-Proxy (nginx, Traefik,
          Caddy) reicht kein UDP durch – dieser Port muss also direkt auf den
          Server bzw. Container zeigen und in der Firewall offen sein.
        </div>
        <div class="form-row">
          <label>VPN aktiv</label>
          <select id="ge-vpn-enabled">
            <option value="1"${(s.vpn_enabled ?? "1") === "1" ? " selected" : ""}>Ja</option>
            <option value="0"${(s.vpn_enabled ?? "1") === "0" ? " selected" : ""}>Nein</option>
          </select>
        </div>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>UDP-Port des Endpunkts</label>
            <input type="number" min="1" max="65535" id="ge-vpn-port"
                   placeholder="51820" value="${esc(String(s.vpn_port || ""))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>Tunnel-Netz</label>
            <input type="text" id="ge-vpn-subnet" placeholder="10.77.0.0/16"
                   value="${esc(s.vpn_subnet || "")}" />
          </div>
        </div>
        <div class="form-row">
          <label>Adresse des VPN-Endpunkts (optional)</label>
          <input type="text" id="ge-vpn-endpoint" placeholder="leer = Adresse des Servers von oben"
                 value="${esc(s.vpn_endpoint_host || "")}" />
        </div>
        <p style="color:var(--subtext);font-size:12px;margin-top:-4px">
          Nur ausfüllen, wenn das VPN unter einer <b>anderen</b> Adresse
          erreichbar ist als das Dashboard – hinter einem Reverse-Proxy ist das
          der Regelfall. Leer bedeutet: Host aus der Server-Adresse oben.
        </p>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>DNS im Tunnel (optional)</label>
            <input type="text" id="ge-vpn-dns" placeholder="192.168.1.1"
                   value="${esc(s.vpn_dns || "")}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>MTU</label>
            <input type="number" min="576" max="1500" id="ge-vpn-mtu"
                   value="${esc(String(s.vpn_mtu || 1380))}" />
          </div>
        </div>

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
                <button class="taskbar-btn" id="up-repo-save">${t("save")}</button>
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

        <div data-adminsec>
        <h3 style="margin-top:24px">${t("set_db")}</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_db_hint")}
        </p>
        <div id="dbx-loading" style="color:var(--subtext);font-size:13px">${t("set_db_loading")}</div>
        <div id="dbx-content" class="hidden">
          <div style="font-size:13px;margin-bottom:8px">${t("set_db_current_mode")}: <b id="dbx-mode"></b></div>
          <div class="form-row">
            <label>${t("set_db_type")}</label>
            <select id="dbx-type">
              <option value="mysql">MySQL / MariaDB</option>
              <option value="postgres">PostgreSQL</option>
              <option value="sqlite">${t("set_db_sqlite_file")}</option>
            </select>
          </div>
          <div style="display:flex;gap:12px" id="dbx-hostrow">
            <div class="form-row" style="flex:2">
              <label>Host</label>
              <input type="text" id="dbx-host" placeholder="192.168.1.50" />
            </div>
            <div class="form-row" style="flex:1">
              <label>Port</label>
              <input type="number" id="dbx-port" min="1" max="65535" />
            </div>
          </div>
          <div style="display:flex;gap:12px" id="dbx-userrow">
            <div class="form-row" style="flex:1">
              <label>${t("username")}</label>
              <input type="text" id="dbx-user" />
            </div>
            <div class="form-row" style="flex:1">
              <label>${t("password")}</label>
              <input type="password" id="dbx-pass" placeholder="${t("u_unverandert_lassen_leer")}" />
            </div>
          </div>
          <div class="form-row">
            <label id="dbx-dblabel">${t("set_db_name")}</label>
            <input type="text" id="dbx-name" placeholder="rapalle_rmm" />
          </div>
          <div id="dbx-error" class="form-error hidden"></div>
          <div id="dbx-progress" style="display:none;margin:10px 0;border:1px solid var(--border);
                                        border-radius:8px;padding:10px;font-size:12.5px">
            <div style="display:flex;justify-content:space-between;gap:10px">
              <span id="dbx-pg-phase" style="color:var(--text)"></span>
              <span id="dbx-pg-count" style="color:var(--subtext)"></span>
            </div>
            <div style="height:8px;background:var(--panel-2);border-radius:5px;margin:8px 0;overflow:hidden">
              <div id="dbx-pg-bar" style="height:100%;width:0%;background:var(--accent,#4da6ff);
                                          transition:width .25s"></div>
            </div>
            <div id="dbx-pg-log" style="max-height:150px;overflow:auto;font-family:ui-monospace,monospace;
                                        font-size:11.5px;color:var(--subtext);white-space:pre-wrap"></div>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
            <button class="taskbar-btn" id="dbx-test">${t("set_test_conn")}</button>
            <button class="btn-primary" id="dbx-to-external" style="width:auto;margin:0">→ ${t("set_db_to_external")}</button>
            <button class="btn-primary" id="dbx-to-local" style="width:auto;margin:0">→ ${t("set_db_to_local")}</button>
          </div>

          <h4 style="margin:18px 0 4px;font-size:13px">${t("set_db_backups")}</h4>
          <p style="color:var(--subtext);font-size:12px;margin:0 0 8px">
            ${t("set_db_backup_hint")}
          </p>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
            <button class="taskbar-btn" id="dbx-backup">${t("set_db_backup_now")}</button>
            <button class="taskbar-btn" id="dbx-backups-refresh">⟳</button>
          </div>
          <div id="dbx-backups" style="font-size:12px;color:var(--subtext)">${t("loading")}</div>
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
          <button class="taskbar-btn" id="ge-updateall">⬆️ ${t("set_update_all_now")}</button>
          <span id="ge-updateall-msg" style="margin-left:10px;font-size:12px;color:var(--subtext)"></span>
        </div>

        <h3 style="margin-top:24px">${t("set_recording")}</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_recording_hint")}
        </p>
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="ge-rec-enabled" ${(s.recording_enabled ?? "1") === "1" ? "checked" : ""} />
            ${t("set_recording_on")}
          </label>
        </div>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>${t("set_screen_quality")}</label>
            <input type="number" min="1" max="100" id="ge-screen-q" value="${esc(String(s.screen_record_quality ?? 40))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>${t("set_screen_fps")}</label>
            <input type="number" min="1" max="30" id="ge-screen-fps" value="${esc(String(s.screen_record_fps ?? 5))}" />
          </div>
        </div>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>${t("set_guac_quality")}</label>
            <input type="number" min="1" max="95" id="ge-guac-q" value="${esc(String(s.guac_record_quality ?? 50))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>Guacamole-Bilder/Sek.</label>
            <input type="number" min="1" max="30" id="ge-guac-fps" value="${esc(String(s.guac_record_fps ?? 8))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>Guacamole-Skalierung (0.1–1.0)</label>
            <input type="number" min="0.1" max="1" step="0.05" id="ge-guac-scale" value="${esc(String(s.guac_record_scale ?? 0.75))}" />
          </div>
        </div>

        <div id="ge-error" class="form-error hidden"></div>
        <button class="btn-primary" id="ge-save" style="margin-top:8px">${t("save")}</button>

        <h3 style="margin-top:24px">Spotify</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_spotify_hint")}
        </p>
        <div class="form-row">
          <label>Client-ID</label>
          <input id="ge-spotify-id" value="${esc(s.spotify_client_id ?? "")}" placeholder="z.B. 8a3f…" style="max-width:420px" />
        </div>
        <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;
                    padding:8px 10px;font-size:12px;color:var(--subtext);max-width:640px;line-height:1.55">
          <b style="color:var(--text)">${t("set_spotify_redirect")}</b>
          <div style="display:flex;gap:6px;align-items:center;margin:5px 0">
            <code id="ge-spotify-uri" style="flex:1;padding:4px 7px;background:var(--panel);
                  border:1px solid var(--border);border-radius:6px;overflow-wrap:anywhere"></code>
            <button class="taskbar-btn" id="ge-spotify-copy" type="button"
                    title="${t("set_spotify_copy")}" style="flex:none">📋</button>
          </div>
          ${t("set_spotify_uri_hint")}<br>
          <b style="color:var(--text)">${t("tip")}:</b> ${t("set_spotify_both")}
          (<code id="ge-spotify-alt"></code>).<br>
          <span id="ge-spotify-warn"></span>
          ${t("set_spotify_exact")}
        </div>

        <div data-adminsec>
        <h3 style="margin-top:24px">Relay</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_relay_hint")}
        </p>
        <div id="ge-relay" style="font-size:13px;color:var(--subtext)">${t("loading")}</div>

        <h3 style="margin-top:24px">${t("set_deploy_title")}</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_deploy_hint")}
        </p>
        <div id="ge-deploy" style="font-size:13px;color:var(--subtext)">${t("loading")}</div>
        </div>

        <div data-adminsec id="ge-docker-box" style="display:none">
        <h3 style="margin-top:24px">${t("set_docker_title")}</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_docker_hint")}
        </p>
        <div id="ge-docker-list" style="font-size:13px;color:var(--subtext)">${t("loading")}</div>
        </div>

        <h3 style="margin-top:26px" data-adminsec-h>${t("guac_title")}</h3>
        <div data-adminsec>
        <p style="color:var(--subtext);font-size:13px">${t("guac_hint")}</p>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:2">
            <label>${t("guac_host")}</label>
            <input type="text" id="ge-guacd-host" placeholder="192.168.1.20" value="${esc(s.guacd_host || "")}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>${t("guac_port")}</label>
            <input type="number" min="1" max="65535" id="ge-guacd-port" value="${esc(String(s.guacd_port ?? 4822))}" />
          </div>
        </div>
        <div id="guacd-status" style="font-size:13px;margin:2px 0 8px;color:var(--subtext)">${t("guac_loading")}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <button class="taskbar-btn" id="guacd-test">${t("guac_test")}</button>
          <span style="color:var(--subtext);font-size:12px">
            ${t("set_saved_below", { save: esc(t("save")) })}
          </span>
        </div>
        </div>
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
    const spUri = root.querySelector("#ge-spotify-uri");
    const spWarn = root.querySelector("#ge-spotify-warn");
    const urlInput = root.querySelector("#ge-server-url");
    // Exakt die eingetragene URL (Spotify vergleicht zeichengenau) - nur
    // mehrfache Schrägstriche am Ende werden auf einen reduziert.
    const effRedirect = () => {
      const base = (urlInput?.value ?? s.server_url ?? "").trim();
      if (!base) return window.location.origin + "/";
      return base.endsWith("/") ? base.replace(/\/+$/, "") + "/" : base;
    };
    function refreshRedirect() {
      const uri = effRedirect();
      if (spUri) spUri.textContent = uri;
      const altEl = root.querySelector("#ge-spotify-alt");
      if (altEl) altEl.textContent = uri.endsWith("/") ? uri.slice(0, -1) : uri + "/";
      if (!spWarn) return;
      // Spotify erlaubt nur https:// oder die Loopback-Adresse 127.0.0.1.
      const loopback = /^http:\/\/127\.0\.0\.1(:\d+)?(\/|$)/.test(uri);
      // Den erlaubten Loopback-Vorschlag mit dem TATSAECHLICHEN Port dieser
      // Installation zeigen - "PORT" als Platzhalter musste der Benutzer sonst
      // selbst ersetzen.
      const port = window.location.port || (window.location.protocol === "https:" ? "443" : "80");
      const loopbackExample = `http://127.0.0.1:${port}/`;
      spWarn.innerHTML = (uri.startsWith("http://") && !loopback)
        ? `<span style="color:var(--warn,#f5a524)">⚠ ${t("set_spotify_bad_uri", { example: esc(loopbackExample) })}</span><br>`
        : "";
    }
    urlInput?.addEventListener("input", refreshRedirect);
    refreshRedirect();
    root.querySelector("#ge-spotify-copy")?.addEventListener("click", async () => {
      const uri = effRedirect();
      try {
        await navigator.clipboard.writeText(uri);
        window.notify?.(t("set_spotify_copied"), "success", 2000);
      } catch {
        window.notify?.(t("u_kopieren_nicht_moglich_bitte_manue") + uri, "info", 8000);
      }
    });
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
        recording_enabled: root.querySelector("#ge-rec-enabled").checked ? "1" : "0",
        agent_auto_update: root.querySelector("#ge-autoupdate").checked ? "1" : "0",
        agent_auto_update_offline: root.querySelector("#ge-autoupdate-offline").checked ? "1" : "0",
        screen_record_quality: parseInt(val("ge-screen-q"), 10) || 40,
        screen_record_fps: parseInt(val("ge-screen-fps"), 10) || 5,
        guac_record_quality: parseInt(val("ge-guac-q"), 10) || 50,
        guac_record_fps: parseInt(val("ge-guac-fps"), 10) || 8,
        guac_record_scale: parseFloat(val("ge-guac-scale")) || 0.75,
        spotify_client_id: (val("ge-spotify-id") || "").trim(),
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
        // VPN: leerer Port/Netz bedeutet "Standard verwenden" - deshalb wird
        // hier NICHT auf einen Zahlenwert erzwungen, sondern der leere Text
        // durchgereicht.
        payload.vpn_enabled = val("ge-vpn-enabled");
        payload.vpn_port = (val("ge-vpn-port") || "").trim();
        payload.vpn_subnet = (val("ge-vpn-subnet") || "").trim();
        payload.vpn_endpoint_host = (val("ge-vpn-endpoint") || "").trim();
        payload.vpn_dns = (val("ge-vpn-dns") || "").trim();
        payload.vpn_mtu = (val("ge-vpn-mtu") || "1380").trim();
      }
      // Auto-Update (Sektion "Update") hat keinen eigenen Speichern-Knopf mehr -
      // der Zustand haengt jetzt am grossen Knopf hier unten.
      if (has("up-auto")) {
        payload.server_auto_update = root.querySelector("#up-auto").checked ? "1" : "0";
        payload.server_auto_update_channel = val("up-auto-channel");
      }
      // Genauso guacd (Host/Port) - ebenfalls ohne eigenen Knopf.
      if (has("ge-guacd-host")) {
        payload.guacd_host = val("ge-guacd-host").trim();
        payload.guacd_port = parseInt(val("ge-guacd-port"), 10) || 4822;
      }
      try {
        await api.updateSettings(payload);
        window.notify?.(t("general_saved"), "success");
        // guacd-Erreichbarkeit direkt neu pruefen, wenn die Sektion da ist.
        try { root._refreshGuacStatus?.(); } catch {}
      } catch (e) {
        err.textContent = e.message; err.classList.remove("hidden");
      }
    });

    root.querySelector("#ge-updateall")?.addEventListener("click", async () => {
      const btn = root.querySelector("#ge-updateall");
      const msg = root.querySelector("#ge-updateall-msg");
      const includeOffline = !!root.querySelector("#ge-updateall-offline")?.checked;
      const { uiConfirm } = await import("../utils.js");
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

    // ---- Admin-only Verkabelung (Server-Update, Datenbank, guacd): nur wenn
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

    // ---- Externe Datenbank (Settings -> Datenbank) ----
    function dbxConfig() {
      return {
        type: root.querySelector("#dbx-type").value,
        host: root.querySelector("#dbx-host").value.trim(),
        port: parseInt(root.querySelector("#dbx-port").value, 10) || 0,
        user: root.querySelector("#dbx-user").value.trim(),
        password: root.querySelector("#dbx-pass").value,
        database: root.querySelector("#dbx-name").value.trim(),
      };
    }

    function dbxAdaptForm() {
      const type = root.querySelector("#dbx-type").value;
      const isFile = type === "sqlite";
      root.querySelector("#dbx-hostrow").style.display = isFile ? "none" : "flex";
      root.querySelector("#dbx-userrow").style.display = isFile ? "none" : "flex";
      root.querySelector("#dbx-dblabel").textContent = isFile ? "Dateipfad (z.B. /mnt/share/rmm.sqlite)" : "Datenbank-Name";
      const port = root.querySelector("#dbx-port");
      if (!isFile && (!port.value || port.value === "0")) port.value = type === "postgres" ? "5432" : "3306";
    }
    root.querySelector("#dbx-type").addEventListener("change", dbxAdaptForm);

    (async () => {
      const loading = root.querySelector("#dbx-loading");
      try {
        const info = await api.getDatabaseInfo();
        const c = info.config || {};
        root.querySelector("#dbx-mode").textContent =
          c.mode === "external" ? `Extern (${c.type})` : "Lokal (SQLite)";
        root.querySelector("#dbx-type").value = c.type || "mysql";
        root.querySelector("#dbx-host").value = c.host || "";
        root.querySelector("#dbx-port").value = c.port || "";
        root.querySelector("#dbx-user").value = c.user || "";
        root.querySelector("#dbx-name").value = c.database || "";
        dbxAdaptForm();
        loading.classList.add("hidden");
        root.querySelector("#dbx-content").classList.remove("hidden");
        // Beide Knöpfe bleiben immer sichtbar - vorher verschwand jeweils
        // einer, was aussah, als gäbe es den Weg zurück gar nicht. Der Knopf
        // für den AKTUELLEN Modus ist stattdessen deaktiviert und beschriftet.
        const toExt = root.querySelector("#dbx-to-external");
        const toLoc = root.querySelector("#dbx-to-local");
        const isExt = c.mode === "external";
        toExt.style.display = ""; toLoc.style.display = "";
        toExt.disabled = isExt;  toLoc.disabled = !isExt;
        toExt.className = isExt ? "taskbar-btn" : "btn-primary";
        toLoc.className = isExt ? "btn-primary" : "taskbar-btn";
        toExt.textContent = isExt ? "✓ " + t("set_db_external_active")
                                  : "→ " + t("set_db_to_external");
        toLoc.textContent = isExt ? "→ " + t("set_db_back_to_local")
                                  : "✓ " + t("set_db_local_active");
        for (const b of [toExt, toLoc]) {
          b.style.opacity = b.disabled ? ".6" : "";
          b.style.cursor = b.disabled ? "default" : "pointer";
        }
      } catch (e) {
        loading.textContent = `${t("error")}: ${e.message}`;
      }
    })();

    root.querySelector("#dbx-test").addEventListener("click", async () => {
      const err = root.querySelector("#dbx-error"); err.classList.add("hidden");
      try {
        await api.testDatabase(dbxConfig());
        window.notify?.(t("u_verbindung_erfolgreich"), "success");
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });

    // --- Fortschritt des Datenbank-Wechsels -----------------------------
    const pgBox = root.querySelector("#dbx-progress");
    const pgPhase = root.querySelector("#dbx-pg-phase");
    const pgCount = root.querySelector("#dbx-pg-count");
    const pgBar = root.querySelector("#dbx-pg-bar");
    const pgLog = root.querySelector("#dbx-pg-log");
    let pgTimer = null;

    const PHASE_TEXT = {
      backup: t("set_db_phase_backup"),
      dump: t("set_db_phase_dump"),
      restore: t("set_db_phase_restore"),
      fertig: t("set_db_phase_done"),
    };

    function drawProgress(p) {
      pgBox.style.display = "";
      pgPhase.textContent = PHASE_TEXT[p.phase] || p.phase || "…";
      pgCount.textContent = p.total
        ? `${p.done}/${p.total} ${t("set_db_tables")} · ${p.rows} ${t("set_db_rows")}`
        : (p.rows ? `${p.rows} ${t("set_db_rows")}` : "");
      pgBar.style.width = p.total ? `${Math.round((p.done / p.total) * 100)}%` : "0%";
      const lines = [...(p.log || [])];
      // Fehler zusätzlich gesammelt ans Ende - sie sind das Wichtigste.
      if (p.errors?.length) {
        lines.push("", `── ${p.errors.length} ${t("errors")} ──`);
        for (const e of p.errors) lines.push(`  ${e.table}: ${e.error}`);
      }
      pgLog.textContent = lines.join("\n");
      pgLog.scrollTop = pgLog.scrollHeight;
      pgBar.style.background = p.ok === false ? "var(--danger,#f66)" : "var(--accent,#4da6ff)";
    }

    let pgMisses = 0;
    async function pollProgress() {
      try {
        const p = await api.databaseProgress();
        pgMisses = 0;
        drawProgress(p);
        if (p.running) return;                 // weiter pollen
        clearInterval(pgTimer); pgTimer = null;
        if (p.ok) {
          window.notify?.(`${p.detail} — ${t("set_backend_restarting")}`,
                          "success", 10000);
          setTimeout(() => location.reload(), 9000);
        } else {
          const err = root.querySelector("#dbx-error");
          err.textContent = p.detail || t("set_db_switch_failed");
          err.classList.remove("hidden");
          window.notify?.(
            p.restored
              ? t("set_db_failed_restored")
              : t("set_db_failed_unchanged"),
            "error", 14000);
          loadBackups();
        }
      } catch (e) {
        // Einzelne Aussetzer sind normal: Beim Neustart bricht die Verbindung
        // ab, und ein Rate-Limiter davor kann eine Abfrage mit 429 abweisen.
        // Deshalb erst nach mehreren Fehlversuchen hintereinander aufgeben -
        // sonst verliert man die Anzeige mitten im laufenden Wechsel.
        pgMisses++;
        pgLog.textContent += `\n(${t("set_db_poll_failed")}: ${e.message})`;
        if (pgMisses >= 5) {
          clearInterval(pgTimer); pgTimer = null;
          pgPhase.textContent = t("set_db_progress_lost");
          window.notify?.(t("set_db_progress_lost_hint"), "warn", 14000);
        }
      }
    }

    function startPolling() {
      pgMisses = 0;
      drawProgress({ phase: "backup", done: 0, total: 0, rows: 0, log: [] });
      clearInterval(pgTimer);
      // Bewusst nur alle 2 s: Das Dashboard laedt ohnehin viele Dateien, und
      // ein Proxy mit Rate-Limit soll durch das Polling nicht zusaetzlich
      // gereizt werden.
      pgTimer = setInterval(pollProgress, 2000);
      pollProgress();
    }

    // --- Sicherungen ----------------------------------------------------
    async function loadBackups() {
      const box = root.querySelector("#dbx-backups");
      if (!box) return;
      try {
        const res = await api.databaseBackups();
        const list = res.backups || [];
        if (!list.length) { box.textContent = t("set_db_no_backups"); return; }
        box.innerHTML = list.map((b) => `
          <div style="display:flex;gap:10px;align-items:center;padding:4px 0;
                      border-bottom:1px solid var(--border)">
            <span style="flex:1">${esc(b.name)}</span>
            <span>${(b.size / 1048576).toFixed(1)} MB</span>
            <span>${new Date(b.at).toLocaleString()}</span>
            <button class="taskbar-btn" style="padding:2px 8px"
                    data-restore="${esc(b.path)}">${t("set_db_restore")}</button>
          </div>`).join("");
        box.querySelectorAll("[data-restore]").forEach((btn) =>
          btn.addEventListener("click", async () => {
            const ok = await uiConfirm(t("set_db_restore_q"), {
              description: t("set_db_restore_desc") });
            if (!ok) return;
            try {
              await api.databaseRestoreBackup(btn.dataset.restore);
              window.notify?.(t("set_db_restoring"), "success", 10000);
              setTimeout(() => location.reload(), 9000);
            } catch (e) { window.notify?.(e.message, "error", 9000); }
          }));
      } catch (e) {
        box.textContent = t("set_db_backups_unavailable") + ": " + e.message;
      }
    }
    loadBackups();

    root.querySelector("#dbx-backups-refresh")?.addEventListener("click", loadBackups);
    root.querySelector("#dbx-backup")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget; const orig = btn.textContent;
      btn.disabled = true; btn.textContent = "…";
      try {
        await api.databaseBackup();
        window.notify?.(t("set_db_backup_done"), "success", 4000);
        loadBackups();
      } catch (err) { window.notify?.(err.message, "error", 9000); }
      btn.disabled = false; btn.textContent = orig;
    });

    async function dbxSwitch(mode) {
      const err = root.querySelector("#dbx-error"); err.classList.add("hidden");
      const ok = await uiConfirm(
        mode === "external" ? t("set_db_to_external_q") : t("set_db_to_local_q"),
        mode === "external"
          ? t("u_alle_daten_werden_von_der_lokalen_")
          : t("u_der_stand_der_externen_datenbank_w"));
      if (!ok) return;
      try {
        // Der Wechsel läuft jetzt im Hintergrund; der Fortschritt wird
        // abgefragt, damit man sieht, wo es hakt, statt minutenlang auf eine
        // einzige Antwort zu warten.
        await api.switchDatabase({ ...dbxConfig(), mode });
        startPolling();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    }
    root.querySelector("#dbx-to-external").addEventListener("click", () => dbxSwitch("external"));
    root.querySelector("#dbx-to-local").addEventListener("click", () => dbxSwitch("local"));

    // ---- Guacamole (extern gehostet): Host/Port speichern + Erreichbarkeit ----
    const guacStatusEl = root.querySelector("#guacd-status");

    async function refreshGuacStatus() {
      guacStatusEl.textContent = t("guac_loading");
      try {
        const st = await api.guacStatus();
        guacStatusEl.innerHTML = st.available
          ? `<span style="color:#3ecf8e">● guacd ${t("guac_reachable")}</span>`
          : `<span style="color:var(--warn)">○ guacd ${t("guac_unreachable")}</span>`;
      } catch (e) {
        guacStatusEl.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
      }
    }

    // Host/Port speichert der grosse "Speichern"-Knopf unten (siehe #ge-save).
    // Damit er den Status danach neu pruefen kann, wird die Funktion hier
    // am root-Element hinterlegt.
    root._refreshGuacStatus = refreshGuacStatus;

    root.querySelector("#guacd-test").addEventListener("click", async (e) => {
      const btn = e.currentTarget; const orig = btn.textContent;
      btn.disabled = true; btn.textContent = "…";
      await refreshGuacStatus();
      btn.disabled = false; btn.textContent = orig;
    });
    refreshGuacStatus();

    // ---------------- Relay: FTP-Zugang -----------------------------------
    (async () => {
      const box = root.querySelector("#ge-relay");
      if (!box) return;
      let cfg;
      try {
        cfg = await api.relayFtpConfig();
      } catch {
        box.textContent = t("set_ftp_unsupported");
        return;
      }
      const draw = () => {
        // WebDAV ist ein eigener Schalter (HTTP, client-first) und laesst sich
        // frei mit FTP/SFTP kombinieren. FTP und SFTP dagegen schliessen sich
        // gegenseitig aus, weil bei beiden der Server zuerst spricht - deshalb
        // eine Auswahl mit drei Moeglichkeiten statt zweier Haekchen.
        const opt = (val, label, hint, disabled = false) => `
          <label style="display:flex;gap:9px;align-items:flex-start;padding:7px 8px;
                        border-radius:8px;cursor:${disabled ? "not-allowed" : "pointer"};
                        opacity:${disabled ? ".55" : "1"};
                        background:${cfg.mode === val ? "var(--panel-2)" : "transparent"}">
            <input type="radio" name="ge-filemode" value="${val}" style="margin-top:3px"
                   ${cfg.mode === val ? "checked" : ""} ${disabled ? "disabled" : ""} />
            <span>
              <span style="color:var(--text)">${label}</span>
              <div style="font-size:11.5px;margin-top:2px">${hint}</div>
            </span>
          </label>`;

        box.innerHTML = `
          <label style="display:flex;gap:9px;align-items:flex-start;padding:7px 8px;
                        border-radius:8px;cursor:pointer;margin-bottom:6px;
                        background:${cfg.webdav ? "var(--panel-2)" : "transparent"}">
            <input type="checkbox" id="ge-webdav" style="margin-top:3px" ${cfg.webdav ? "checked" : ""} />
            <span>
              <span style="color:var(--text)">${t("set_webdav_label")}</span>
              <div style="font-size:11.5px;margin-top:2px">
                <code>http://&lt;server&gt;:${cfg.port}/dav/</code> · ${t("set_webdav_hint")}
              </div>
            </span>
          </label>

          <div style="margin:10px 0 4px;color:var(--text)">${t("set_extra_file_access")}</div>
          ${opt("off", t("set_none_word"), t("set_only_checked"))}
          ${opt("ftp", `FTP (Port ${cfg.port})`,
                `<code>ftp://&lt;server&gt;:${cfg.port}</code> · ${t("set_ftp_oneport")}`)}
          ${opt("sftp", `SFTP (Port ${cfg.port})`,
                cfg.sftp_available
                  ? `<code>sftp://&lt;server&gt;:${cfg.port}</code> · ${t("set_sftp_oneport")}`
                  : esc(cfg.sftp_reason),
                !cfg.sftp_available)}
          <div style="margin-top:8px;color:var(--warn,#f5a524)">⚠ ${esc(cfg.note)}</div>
          ${cfg.restart_pending ? `
            <div style="margin-top:6px;padding:8px;border:1px solid var(--warn,#f5a524);
                        border-radius:8px;color:var(--warn,#f5a524)">
              ⚠ ${t("set_listener_pending", { mode: esc(cfg.mode.toUpperCase()) })}
              ${cfg.listener_error ? `<div style="margin-top:4px;font-size:11.5px">${t("set_last_error")}: ${esc(cfg.listener_error)}</div>` : ""}
            </div>` : ""}
          ${(cfg.mode === "ftp" && !cfg.advertise_host) ? `
            <div style="margin-top:6px;font-size:11.5px">
              ${t("set_ftp_nat_hint")}
            </div>` : ""}
          <div style="margin-top:4px">${t("set_login_dashboard_creds")}</div>`;

        // Nach dem Speichern: Neustart gleich oder später? FTP/SFTP wirken erst
        // danach, weil dabei die Weiche vor dem Port auf-/abgebaut wird.
        const askRestart = async (res) => {
          if (!res || !res.needs_restart) {
            window.notify?.(res?.note || t("saved"), "success", 4000);
            return;
          }
          if (!cfg.may_restart) {
            window.notify?.(
              t("set_saved_needs_restart_admin"),
              "warn", 12000);
            return;
          }
          const { uiChoice } = await import("../utils.js");
          const choice = await uiChoice(t("set_restart_now_q"), [
            { label: t("set_restart_now"), value: "now" },
            { label: t("set_restart_later"), value: "later" },
          ], { description: t("set_restart_desc") });
          if (choice !== "now") {
            window.notify?.(t("set_saved_next_restart"), "info", 8000);
            return;
          }
          try {
            await api.restartBackend();
            window.notify?.(t("set_backend_restarting"), "success", 8000);
            setTimeout(() => location.reload(), 6000);
          } catch (err) {
            window.notify?.(t("set_restart_failed") + ": " + err.message
              + " — " + t("set_ask_admin_restart"), "error", 12000);
          }
        };

        box.querySelectorAll("[name=ge-filemode]").forEach((r) =>
          r.addEventListener("change", async (e) => {
            const val = e.currentTarget.value;
            const before = cfg.mode;
            try {
              const res = await api.relayFtpMode(val);
              cfg.mode = res.mode;
              draw();
              await askRestart(res);
            } catch (err) {
              cfg.mode = before;
              draw();
              window.notify?.(err.message, "error", 9000);
            }
          }));

        box.querySelector("#ge-webdav")?.addEventListener("change", async (e) => {
          const on = e.currentTarget.checked;
          const before = cfg.webdav;
          try {
            const res = await api.relayFtpMode(cfg.mode, on);
            cfg.webdav = res.webdav;
            draw();
            // Reine WebDAV-Änderung braucht keinen Neustart -> nur Bestätigung.
            await askRestart(res);
          } catch (err) {
            cfg.webdav = before;
            draw();
            window.notify?.(err.message, "error", 9000);
          }
        });
      };
      draw();
    })();

    // ---------------- Deployment-Seite ------------------------------------
    (async () => {
      const box = root.querySelector("#ge-deploy");
      if (!box) return;
      let cfg;
      try {
        cfg = await api.getDeployment();
      } catch (e) {
        box.textContent = t("set_unavailable") + ": " + e.message;
        return;
      }
      const origin = location.origin;
      box.innerHTML = `
        <label style="display:flex;gap:9px;align-items:flex-start;padding:7px 8px;
                      border-radius:8px;cursor:pointer;
                      background:${cfg.public ? "var(--panel-2)" : "transparent"}">
          <input type="checkbox" id="dep-public" style="margin-top:3px" ${cfg.public ? "checked" : ""} />
          <span>
            <span style="color:var(--text)">${t("set_dep_public")}</span>
            <div style="font-size:11.5px;margin-top:2px">
              <code>${esc(origin)}/deployment</code> · ${t("set_dep_public_hint")}
            </div>
          </span>
        </label>

        <div class="form-row" style="margin-top:10px">
          <label>${t("set_dep_page_title")}</label>
          <input type="text" id="dep-title" value="${esc(cfg.title || "Deployment")}" />
        </div>

        <div style="margin-top:10px">
          <label style="display:block;margin-bottom:4px">${t("set_dep_html")}</label>
          <textarea id="dep-html" spellcheck="false" rows="10"
            placeholder="${t("set_dep_html_ph")}"
            style="width:100%;box-sizing:border-box;font-family:ui-monospace,monospace;
                   font-size:12px;padding:8px;border-radius:8px;
                   border:1px solid var(--border);background:var(--panel-2);
                   color:var(--text)">${esc(cfg.html || "")}</textarea>
          <div style="font-size:11.5px;margin-top:4px">
            Wird der Code leer gelassen, zeigt die Seite automatisch alle Dateien
            aus dem Ordner. Eigene Dateien verlinkst du mit
            <code>/deployment/&lt;dateiname&gt;</code>, Bilder z.B. mit
            <code>&lt;img src="/deployment/logo.png"&gt;</code>.
          </div>
        </div>

        <div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap">
          <button class="btn-primary" id="dep-save" style="width:auto;margin:0">${t("save")}</button>
          <a class="taskbar-btn" href="/deployment" target="_blank" rel="noopener">${t("set_open_page")} ↗</a>
          <span style="font-size:11.5px">${t("set_dep_files_hint")}</span>
        </div>`;

      box.querySelector("#dep-save").addEventListener("click", async (e) => {
        const btn = e.currentTarget; const orig = btn.textContent;
        btn.disabled = true; btn.textContent = "…";
        try {
          const res = await api.saveDeployment({
            html: box.querySelector("#dep-html").value,
            title: box.querySelector("#dep-title").value,
            public: box.querySelector("#dep-public").checked,
          });
          cfg.public = res.public;
          window.notify?.("Deployment-Seite gespeichert.", "success", 4000);
        } catch (err) {
          window.notify?.(err.message, "error", 9000);
        }
        btn.disabled = false; btn.textContent = orig;
      });
    })();

    // ---------------- Container-Dienste (nur im Docker-Betrieb) -----------
    // Der Bereich bleibt versteckt, solange nicht sicher ist, dass wir in
    // einem Container laufen - nativ waere er sinnlos.
    const dockerBox = root.querySelector("#ge-docker-box");
    const dockerList = root.querySelector("#ge-docker-list");

    /** Guacamole- und Datenbank-Felder mit den Werten des Dienstes füllen. */
    function prefillFromDocker(data) {
      if (data && data.guacd && data.guacd.host) {
        const h = root.querySelector("#ge-guacd-host");
        const p = root.querySelector("#ge-guacd-port");
        if (h) h.value = data.guacd.host;
        if (p) p.value = String(data.guacd.port || 4822);
      }
      const c = data && data.db;
      if (c && c.host) {
        const set = (id, v) => { const el = root.querySelector(id); if (el && v != null) el.value = String(v); };
        set("#dbx-type", c.type || "mysql");
        set("#dbx-host", c.host);
        set("#dbx-port", c.port);
        set("#dbx-user", c.user);
        set("#dbx-pass", c.password);
        set("#dbx-name", c.database);
        root.querySelector("#dbx-type")?.dispatchEvent(new Event("change"));
      }
    }

    function drawDocker(info) {
      // Nativ installiert gibt es hier nichts zu tun -> Unterpunkt komplett
      // entfernen, damit kein leerer Knopf in der Leiste stehen bleibt.
      if (!info || !info.is_docker) { removeSubSection(dockerBox); return; }
      dockerBox.style.display = "";

      if (!info.socket) {
        dockerList.innerHTML = `
          <div style="border:1px solid var(--warn,#f5a524);border-radius:8px;padding:10px">
            ⚠ ${t("set_docker_no_socket")}
          </div>`;
        return;
      }

      dockerList.innerHTML = info.services.map((sv) => {
        const on = sv.running;
        const state = on ? t("set_dk_running") : (sv.state === "absent" ? t("set_dk_absent") : sv.state);
        return `
          <div style="display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
            <span style="font-size:16px">${on ? "🟢" : "⚪"}</span>
            <div style="flex:1;min-width:0">
              <div style="color:var(--text)">${esc(sv.label)}</div>
              <div style="font-size:11px">${esc(sv.purpose)} · ${esc(sv.image)} · ${esc(state)}</div>
            </div>
            <button class="taskbar-btn" data-dk="${esc(sv.key)}" data-on="${on ? "1" : "0"}">
              ${on ? t("set_dk_off") : t("set_dk_on")}
            </button>
          </div>`;
      }).join("");

      dockerList.querySelectorAll("[data-dk]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const key = btn.dataset.dk;
          const turnOff = btn.dataset.on === "1";
          const orig = btn.textContent;
          btn.disabled = true;
          btn.textContent = turnOff ? "…" : t("set_dk_starting");
          try {
            const r = turnOff ? await api.dockerDisable(key) : await api.dockerEnable(key);
            if (!turnOff) prefillFromDocker(r);
            drawDocker(r.status);
            window.notify?.(turnOff ? t("set_dk_stopped") : t("set_dk_running"), "success");
          } catch (e) {
            btn.disabled = false;
            btn.textContent = orig;
            window.notify?.(e.message, "error", 9000);
          }
        })
      );
    }

    (async () => {
      try {
        const info = await api.dockerServices();
        drawDocker(info);
        // Bereits vorhandene Zugangsdaten gleich eintragen, damit das
        // Datenbank-Formular nach einem Neuladen nicht wieder leer ist.
        if (info.is_docker && info.socket) {
          const creds = await api.dockerDbCredentials().catch(() => ({}));
          if (creds && creds.host) prefillFromDocker({ db: creds });
        }
      } catch {
        removeSubSection(dockerBox);
      }
    })();
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
            <button class="taskbar-btn" id="gp-cancel">${t("cancel")}</button>
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
          </select>
        </div>
        <div class="form-row">
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
            <button class="taskbar-btn" id="su-groups-btn" type="button">➕ ${t("set_add_groups")}</button>
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
                u.must_change_pw ? ` <span style="color:var(--warn)">(${t("set_pw_change_pending")})</span>` : ""}</td>
            <td><button class="taskbar-btn" data-groups="${u.id}">${t("pm_groups")}…</button></td>
            <td><button class="taskbar-btn" data-del="${u.id}">${t("delete")}</button></td>`;
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
        if (res.generated_password) {
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
          <button class="taskbar-btn" data-del="${g.id}">${t("delete")}</button>
        </div>`).join("") : `<div style="color:var(--subtext);font-size:13px">${t("set_no_groups")}</div>`;
      listEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async () => { await api.deleteGroup(btn.dataset.del); renderGroupsTab(root); })
      );
    } catch (e) {
      listEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
    }
  }

  // ---------------- SSO / REALMS ----------------
  async function renderSsoTab(root) {
    let editingId = null;   // null = neues Realm anlegen, sonst wird bearbeitet

    async function draw() {
      let realms = [];
      try { realms = await api.getRealms(); }
      catch (e) { root.innerHTML = `<div class="settings-section"><div style="color:var(--danger)">${esc(e.message)}</div></div>`; return; }

      const editing = editingId ? realms.find((r) => r.id === editingId) : null;

      root.innerHTML = `
        <div class="settings-section">
          <p style="color:var(--subtext);font-size:13px">${t("sso_intro")}</p>
          <div style="background: rgba(var(--accent-2-rgb), 0.08);border:1px solid var(--accent);border-radius:6px;padding:8px 10px;font-size:12px;color:var(--subtext);margin-bottom:14px">
            ${t("sso_group_hint")}
          </div>

          <h3>${editing ? t("sso_edit_realm") : t("sso_add_realm")}</h3>
          <div class="form-row"><label>${t("sso_realm_name")}</label><input type="text" id="rl-name" placeholder="z.B. Firma AD" value="${esc(editing?.name || "")}" /></div>
          <div class="form-row"><label>${t("sso_server")}</label><input type="text" id="rl-server" placeholder="dc01.firma.local" value="${esc(editing?.server || "")}" /></div>
          <div style="display:flex;gap:12px">
            <div class="form-row" style="flex:1"><label>${t("sso_port")}</label><input type="number" id="rl-port" placeholder="389 / 636" value="${editing?.port ?? ""}" /></div>
            <div class="form-row" style="flex:1;justify-content:flex-end">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;margin-top:20px">
                <input type="checkbox" id="rl-ssl" ${editing?.use_ssl ? "checked" : ""} /> ${t("sso_use_ssl")}
              </label>
            </div>
          </div>
          <div class="form-row"><label>${t("sso_base_dn")}</label><input type="text" id="rl-basedn" placeholder="DC=firma,DC=local" value="${esc(editing?.base_dn || "")}" /></div>
          <div class="form-row"><label>${t("sso_bind_user")}</label><input type="text" id="rl-binduser" placeholder="CN=svc,OU=...,DC=firma,DC=local" value="${esc(editing?.bind_user || "")}" /></div>
          <div class="form-row"><label>${t("sso_bind_pw")}</label><input type="password" id="rl-bindpw" placeholder="${editing ? t("sso_pw_unchanged") : ""}" /></div>
          <div class="form-row"><label>${t("sso_user_filter")}</label><input type="text" id="rl-filter" placeholder="(memberOf=CN=RMM-Users,...)" value="${esc(editing?.user_filter || "")}" /></div>
          <p style="color:var(--subtext);font-size:11px;margin:2px 0 8px">${t("sso_filter_hint")}</p>

          <div style="display:flex;gap:8px">
            <button class="btn-primary" id="rl-save" style="width:auto">${editing ? t("save") : "+ " + t("sso_add_realm")}</button>
            ${editing ? `<button class="taskbar-btn" id="rl-cancel">${t("cancel")}</button>` : ""}
          </div>

          <h3 style="margin-top:24px">${t("sso_connected")}</h3>
          <div id="rl-list"></div>
        </div>
      `;

      // --- Speichern / Aktualisieren ---
      root.querySelector("#rl-save").addEventListener("click", async () => {
        const payload = {
          name: root.querySelector("#rl-name").value.trim(),
          server: root.querySelector("#rl-server").value.trim(),
          base_dn: root.querySelector("#rl-basedn").value.trim(),
          bind_user: root.querySelector("#rl-binduser").value.trim(),
          bind_password: root.querySelector("#rl-bindpw").value,
          port: root.querySelector("#rl-port").value ? parseInt(root.querySelector("#rl-port").value, 10) : null,
          use_ssl: root.querySelector("#rl-ssl").checked,
          user_filter: root.querySelector("#rl-filter").value.trim(),
          enabled: editing ? !!editing.enabled : true,
        };
        if (!payload.name || !payload.server) { window.notify?.(t("u_name_und_server_erforderlich"), "warn"); return; }
        try {
          if (editing) { await api.updateRealm(editing.id, payload); window.notify?.(t("general_saved"), "success"); }
          else { await api.createRealm(payload); window.notify?.("Realm gespeichert", "success"); }
          editingId = null; draw();
        } catch (e) { window.notify?.(e.message, "error"); }
      });
      if (editing) root.querySelector("#rl-cancel").addEventListener("click", () => { editingId = null; draw(); });

      // --- Liste ---
      const listEl = root.querySelector("#rl-list");
      if (!realms.length) { listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">${t("sso_none")}</div>`; return; }
      listEl.innerHTML = realms.map((r) => {
        const proto = r.use_ssl ? "LDAPS" : "LDAP";
        const port = r.port ? ":" + r.port : "";
        const badge = r.enabled
          ? `<span style="font-size:11px;color:#3ecf8e">● ${t("status_online").toLowerCase()}</span>`
          : `<span style="font-size:11px;color:var(--subtext)">○ ${t("disabled")}</span>`;
        return `
        <div class="panel" style="margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div>
            <strong>${esc(r.name)}</strong> ${badge}
            <div style="font-size:11px;color:var(--subtext)">${proto} · ${esc(r.server)}${port}${r.base_dn ? " · " + esc(r.base_dn) : ""}</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
            <button class="taskbar-btn" data-test="${r.id}">${t("test")}</button>
            <button class="taskbar-btn" data-toggle="${r.id}">${r.enabled ? t("sso_disable") : t("sso_enable")}</button>
            <button class="taskbar-btn" data-edit="${r.id}">${t("edit")}</button>
            <button class="taskbar-btn" data-del="${r.id}">${t("delete")}</button>
          </div>
        </div>`;
      }).join("");

      listEl.querySelectorAll("[data-test]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          btn.textContent = "…";
          try { await api.testRealm(btn.dataset.test); window.notify?.(t("sso_test_ok"), "success"); }
          catch (e) { window.notify?.(t("sso_test_fail") + " " + e.message, "error"); }
          btn.textContent = t("test");
        })
      );
      listEl.querySelectorAll("[data-toggle]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const r = realms.find((x) => x.id === btn.dataset.toggle);
          try { await api.updateRealm(r.id, { name: r.name, server: r.server, enabled: !r.enabled }); draw(); }
          catch (e) { window.notify?.(e.message, "error"); }
        })
      );
      listEl.querySelectorAll("[data-edit]").forEach((btn) =>
        btn.addEventListener("click", () => { editingId = btn.dataset.edit; draw(); })
      );
      listEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (!(await uiConfirm(t("sso_delete_confirm"), { okText: t("delete"), danger: true }))) return;
          await api.deleteRealm(btn.dataset.del); if (editingId === btn.dataset.del) editingId = null; draw();
        })
      );
    }

    await draw();
    condenseHints(root);
  }

  // ---------------- NOTIFICATIONS (Webhook-Verwaltung) ----------------
  // Benachrichtigungen: kompletter Bereich (Regeln, Webhooks, SMTP) liegt
  // in apps/notifications.js - hier nur noch delegieren.
  function renderNotifTab(root) {
    renderNotifications(root, win);
    // Erklaertexte in "?"-Symbole umwandeln, sobald der Bereich steht.
    // (renderNotifications laedt asynchron nach - deshalb zweimal.)
    condenseHints(root);
    setTimeout(() => condenseHints(root), 400);
  }

  draw();
}

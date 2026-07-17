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
import { t } from "../i18n.js";
import { isAdmin } from "../state.js";
import { renderSource } from "./source.js";
import { registerCleanup } from "../windowmanager.js";

export function renderSettings(body, win) {
  let activeTab = "general";
  let sourceCleanup = null;  // Aufräumen der Source-Shell (Socket/PTY) beim Verlassen

  // Beim Schließen des Einstellungen-Fensters die Source-Shell sicher beenden.
  if (win && win.key) {
    registerCleanup(win.key, () => { if (sourceCleanup) { try { sourceCleanup(); } catch {} } });
  }

  function draw() {
    // Vorherige Source-Shell sauber schließen, bevor neu gezeichnet wird.
    if (sourceCleanup) { try { sourceCleanup(); } catch {} sourceCleanup = null; }
    body.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%">
        <div class="tab-bar" style="padding:10px 14px;border-bottom:1px solid var(--border);gap:6px">
          <button class="tab-btn ${activeTab === "general" ? "active" : ""}" data-t="general">${t("tab_general")}</button>
          <button class="tab-btn ${activeTab === "users" ? "active" : ""}" data-t="users">${t("tab_users")}</button>
          <button class="tab-btn ${activeTab === "groups" ? "active" : ""}" data-t="groups">${t("tab_groups")}</button>
          <button class="tab-btn ${activeTab === "sso" ? "active" : ""}" data-t="sso">${t("tab_sso")}</button>
          <button class="tab-btn ${activeTab === "branding" ? "active" : ""}" data-t="branding">Branding</button>
          <button class="tab-btn ${activeTab === "notifications" ? "active" : ""}" data-t="notifications">${t("tab_notifications")}</button>
          ${isAdmin() ? `<button class="tab-btn ${activeTab === "source" ? "active" : ""}" data-t="source">Source</button>` : ""}
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
    else if (activeTab === "groups") renderGroupsTab(content);
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
        <h3>Logos &amp; Bilder</h3>
        <p style="color:var(--subtext);font-size:13px;max-width:640px">
          Hier lassen sich alle Logos und der Login-Hintergrund gegen eigene
          Dateien austauschen. Format muss zum Slot passen (PNG/JPG/ICO),
          max. 8&nbsp;MB. Änderungen sind sofort aktiv — ggf. Seite neu laden
          (Strg+F5), da der Browser Bilder cached.
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
            : `<span style="color:var(--subtext);font-size:11px">— fehlt —</span>`}
        </div>
        <div style="flex:1">
          <strong style="font-size:13px">${esc(s.label)}</strong>
          <div style="color:var(--subtext);font-size:12px">${esc(s.name)}</div>
        </div>
        <input type="file" data-file="${esc(s.name)}" style="display:none"
          accept="image/*,.ico" />
        <button class="action-btn" data-pick="${esc(s.name)}">Datei wählen &amp; ersetzen…</button>
      </div>
    `).join("");

    slotsEl.querySelectorAll("[data-pick]").forEach((btn) => {
      const name = btn.dataset.pick;
      const fileInput = slotsEl.querySelector(`[data-file="${name}"]`);
      btn.addEventListener("click", () => fileInput.click());
      fileInput.addEventListener("change", async () => {
        const file = fileInput.files[0];
        if (!file) return;
        msgEl.innerHTML = `<span style="color:var(--subtext)">Lade "${esc(name)}" hoch…</span>`;
        try {
          const res = await api.uploadBranding(name, file);
          msgEl.innerHTML = `<span style="color:var(--accent)">✓ ${esc(name)} ersetzt (${Math.round((res.size || file.size) / 1024)} KB gespeichert).</span>`;
          window.notify?.(`Branding: "${name}" hochgeladen und gespeichert.`, "success");
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
          Öffentlicher Zugriff / Reverse-Proxy: Trage hier die <b>öffentliche Adresse</b> ein
          (z.B. <code>https://rmm.meinefirma.de</code>), unter der Agenten das Backend erreichen.
          Dieser Wert wird in die Agent-Installation eingebaut — sonst bekommt ein Agent die
          interne IP (<code>http://ip:4000</code>), die ein externer Client nicht erreichen kann.
        </p>

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

        <h3 style="margin-top:24px">Update</h3>
        <p style="color:var(--subtext);font-size:13px">
          Aktualisiert den RMM-Server direkt aus dem GitHub-Repo. Das Backend
          startet nach einem Update automatisch neu.
        </p>
        <button class="btn-primary" id="up-toggle" style="width:auto">🔄 Update-Optionen anzeigen</button>
        <div id="up-panel" class="hidden" style="margin-top:12px;border:1px solid var(--border);border-radius:8px;padding:12px;background:var(--panel-2)">
          <div id="up-loading" style="color:var(--subtext);font-size:13px">Lade Update-Informationen von GitHub…</div>
          <div id="up-content" class="hidden">
            <div style="font-size:13px;margin-bottom:10px">
              Installierte Version: <b id="up-current"></b>
            </div>
            <div class="form-row">
              <label>GitHub-Repo (backend/repo.txt)</label>
              <div style="display:flex;gap:8px">
                <input type="text" id="up-repo" style="flex:1" />
                <button class="taskbar-btn" id="up-repo-save">Speichern</button>
              </div>
            </div>
            <div class="form-row">
              <label>Update-Ziel</label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="radio" name="up-target" value="commit" checked /> <span id="up-lbl-commit">Neuester Commit</span>
              </label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="radio" name="up-target" value="full" /> <span id="up-lbl-full">Neuestes Full-Release</span>
              </label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="radio" name="up-target" value="any" /> <span id="up-lbl-any">Neuestes Release (Alpha + Full)</span>
              </label>
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
                <input type="radio" name="up-target" value="custom" /> Custom:
                <select id="up-custom" style="flex:1"></select>
              </label>
            </div>
            <div id="up-error" class="form-error hidden"></div>
            <button class="btn-primary" id="up-run" style="margin-top:6px">⬇️ Update installieren</button>

            <h4 style="margin-top:18px">Auto-Update</h4>
            <div class="form-row">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
                <input type="checkbox" id="up-auto" ${(s.server_auto_update ?? "0") === "1" ? "checked" : ""} />
                Server automatisch aktualisieren
              </label>
            </div>
            <div class="form-row">
              <label>Auto-Update-Kanal</label>
              <select id="up-auto-channel">
                <option value="commit" ${(s.server_auto_update_channel || "full") === "commit" ? "selected" : ""}>Neuester Commit</option>
                <option value="full" ${(s.server_auto_update_channel || "full") === "full" ? "selected" : ""}>Neuestes Full-Release</option>
                <option value="any" ${(s.server_auto_update_channel || "full") === "any" ? "selected" : ""}>Neuestes Release (Alpha + Full)</option>
              </select>
            </div>
            <button class="taskbar-btn" id="up-auto-save">Auto-Update speichern</button>
          </div>
        </div>

        <h3 style="margin-top:24px">Datenbank</h3>
        <p style="color:var(--subtext);font-size:13px">
          Speicherort der RMM-Daten: <b>Lokal</b> (SQLite-Datei im Backend) oder
          <b>Extern</b> (MySQL/MariaDB, PostgreSQL oder SQLite-Datei z.B. auf einer
          Netzwerkfreigabe). Beim Umschalten werden <b>alle Daten kopiert</b>
          (lokal → extern bzw. extern → lokal) und das Backend startet neu.
          Im externen Modus ist die externe DB der persistente Speicher und wird
          im Betrieb laufend synchron gehalten.
        </p>
        <div id="dbx-loading" style="color:var(--subtext);font-size:13px">Lade Datenbank-Status…</div>
        <div id="dbx-content" class="hidden">
          <div style="font-size:13px;margin-bottom:8px">Aktueller Modus: <b id="dbx-mode"></b></div>
          <div class="form-row">
            <label>Typ der externen Datenbank</label>
            <select id="dbx-type">
              <option value="mysql">MySQL / MariaDB</option>
              <option value="postgres">PostgreSQL</option>
              <option value="sqlite">SQLite-Datei (Pfad)</option>
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
              <label>Benutzer</label>
              <input type="text" id="dbx-user" />
            </div>
            <div class="form-row" style="flex:1">
              <label>Passwort</label>
              <input type="password" id="dbx-pass" placeholder="(unverändert lassen = leer)" />
            </div>
          </div>
          <div class="form-row">
            <label id="dbx-dblabel">Datenbank-Name</label>
            <input type="text" id="dbx-name" placeholder="rapalle_rmm" />
          </div>
          <div id="dbx-error" class="form-error hidden"></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
            <button class="taskbar-btn" id="dbx-test">Verbindung testen</button>
            <button class="btn-primary" id="dbx-to-external" style="width:auto;margin:0">→ Auf externe Datenbank umschalten</button>
            <button class="btn-primary" id="dbx-to-local" style="width:auto;margin:0">→ Auf lokale Datenbank umschalten</button>
          </div>
        </div>

        <h3 style="margin-top:24px">Agent Auto-Update</h3>
        <p style="color:var(--subtext);font-size:13px">
          Aktualisiert veraltete Agenten automatisch, sobald sie sich mit dem
          Backend verbinden (gleicher Mechanismus wie der „Agent aktualisieren"-Button).
          Pro Client übersteuerbar unter „Client bearbeiten" → Automatisches Agent-Update.
        </p>
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="ge-autoupdate" ${(s.agent_auto_update ?? "0") === "1" ? "checked" : ""} />
            Automatisches Agent-Update aktiviert (global)
          </label>
        </div>

        <h3 style="margin-top:24px">Aufnahme (Replays)</h3>
        <p style="color:var(--subtext);font-size:13px">
          Steuert, wie Remote-Sessions als Replay aufgezeichnet werden — für den
          Screen-Agenten und für Guacamole (RDP/VNC/SSH/Telnet). Höhere Werte =
          schärfer/flüssiger, aber größere Dateien.
        </p>
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="ge-rec-enabled" ${(s.recording_enabled ?? "1") === "1" ? "checked" : ""} />
            Aufzeichnung aktiviert
          </label>
        </div>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>Screen-Qualität (1–100)</label>
            <input type="number" min="1" max="100" id="ge-screen-q" value="${esc(String(s.screen_record_quality ?? 40))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>Screen-Bilder/Sek.</label>
            <input type="number" min="1" max="30" id="ge-screen-fps" value="${esc(String(s.screen_record_fps ?? 5))}" />
          </div>
        </div>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>Guacamole-Qualität (1–95)</label>
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

        <h3 style="margin-top:26px">${t("guac_title")}</h3>
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
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn-primary" id="guacd-save" style="width:auto;margin:0">${t("save")}</button>
          <button class="taskbar-btn" id="guacd-test">${t("guac_test")}</button>
        </div>
      </div>
    `;

    root.querySelector("#ge-save").addEventListener("click", async () => {
      const err = root.querySelector("#ge-error");
      err.classList.add("hidden");
      const payload = {
        server_host: root.querySelector("#ge-server-host").value.trim(),
        server_domain: root.querySelector("#ge-server-domain").value.trim(),
        server_backend_port: parseInt(root.querySelector("#ge-backend-port").value, 10) || 4000,
        server_frontend_port: parseInt(root.querySelector("#ge-frontend-port").value, 10) || 4000,
        server_url: root.querySelector("#ge-server-url").value.trim(),
        metrics_interval_seconds: parseInt(root.querySelector("#ge-interval").value, 10) || 60,
        metrics_retention_hours: parseInt(root.querySelector("#ge-retention").value, 10) || 1,
        replay_retention_days: parseInt(root.querySelector("#ge-replay").value, 10) || 10,
        recording_enabled: root.querySelector("#ge-rec-enabled").checked ? "1" : "0",
        agent_auto_update: root.querySelector("#ge-autoupdate").checked ? "1" : "0",
        screen_record_quality: parseInt(root.querySelector("#ge-screen-q").value, 10) || 40,
        screen_record_fps: parseInt(root.querySelector("#ge-screen-fps").value, 10) || 5,
        guac_record_quality: parseInt(root.querySelector("#ge-guac-q").value, 10) || 50,
        guac_record_fps: parseInt(root.querySelector("#ge-guac-fps").value, 10) || 8,
        guac_record_scale: parseFloat(root.querySelector("#ge-guac-scale").value) || 0.75,
      };
      try {
        await api.updateSettings(payload);
        window.notify?.(t("general_saved"), "success");
      } catch (e) {
        err.textContent = e.message; err.classList.remove("hidden");
      }
    });

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
          info.current_version + (info.current_commit ? ` (Stand: ${String(info.current_commit).slice(0, 10)})` : "");
        root.querySelector("#up-repo").value = info.repo_url || "";
        const c = info.latest_commit || {};
        root.querySelector("#up-lbl-commit").textContent =
          `Neuester Commit${c.sha ? ` — ${c.sha.slice(0, 10)}` : ""}${c.message ? `: ${c.message}` : ""}`;
        root.querySelector("#up-lbl-full").textContent =
          `Neuestes Full-Release${info.latest_full_tag ? ` — ${info.latest_full_tag}` : " — keins gefunden"}`;
        root.querySelector("#up-lbl-any").textContent =
          `Neuestes Release (Alpha + Full)${info.latest_any_tag ? ` — ${info.latest_any_tag}` : " — keins gefunden"}`;
        const sel = root.querySelector("#up-custom");
        sel.innerHTML = (info.releases || []).length
          ? info.releases.map((r) => `<option value="${esc(r.tag)}">${esc(r.tag)}${r.alpha ? " (Alpha)" : " (Full)"}</option>`).join("")
          : `<option value="">Keine Releases im Repo</option>`;
        upInfoLoaded = true;
        loading.classList.add("hidden"); content.classList.remove("hidden");
      } catch (e) {
        loading.textContent = `Fehler: ${e.message}`;
      }
    }

    root.querySelector("#up-toggle").addEventListener("click", () => {
      const show = upPanel.classList.contains("hidden");
      upPanel.classList.toggle("hidden", !show);
      root.querySelector("#up-toggle").textContent =
        show ? "🔄 Update-Optionen ausblenden" : "🔄 Update-Optionen anzeigen";
      if (show && !upInfoLoaded) loadUpdateInfo();
    });

    root.querySelector("#up-repo-save").addEventListener("click", async () => {
      const err = root.querySelector("#up-error"); err.classList.add("hidden");
      try {
        await api.setServerUpdateRepo(root.querySelector("#up-repo").value.trim());
        window.notify?.("Repo-URL gespeichert (backend/repo.txt)", "success");
        upInfoLoaded = false; loadUpdateInfo();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });

    root.querySelector("#up-run").addEventListener("click", async () => {
      const err = root.querySelector("#up-error"); err.classList.add("hidden");
      const target = root.querySelector('input[name="up-target"]:checked').value;
      const tag = target === "custom" ? root.querySelector("#up-custom").value : null;
      if (target === "custom" && !tag) { err.textContent = "Bitte ein Release wählen"; err.classList.remove("hidden"); return; }
      const ok = await uiConfirm("Server-Update installieren?",
        "Das Backend lädt den gewählten Stand aus GitHub, ersetzt seine Dateien und startet neu. Laufende Verbindungen brechen kurz ab.");
      if (!ok) return;
      const btn = root.querySelector("#up-run");
      btn.disabled = true; btn.textContent = "Update läuft…";
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

    root.querySelector("#up-auto-save").addEventListener("click", async () => {
      const err = root.querySelector("#up-error"); err.classList.add("hidden");
      try {
        await api.updateSettings({
          server_auto_update: root.querySelector("#up-auto").checked ? "1" : "0",
          server_auto_update_channel: root.querySelector("#up-auto-channel").value,
        });
        window.notify?.("Auto-Update-Einstellungen gespeichert", "success");
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
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
        root.querySelector(c.mode === "external" ? "#dbx-to-external" : "#dbx-to-local").style.display = "none";
      } catch (e) {
        loading.textContent = `Fehler: ${e.message}`;
      }
    })();

    root.querySelector("#dbx-test").addEventListener("click", async () => {
      const err = root.querySelector("#dbx-error"); err.classList.add("hidden");
      try {
        await api.testDatabase(dbxConfig());
        window.notify?.("Verbindung erfolgreich", "success");
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });

    async function dbxSwitch(mode) {
      const err = root.querySelector("#dbx-error"); err.classList.add("hidden");
      const ok = await uiConfirm(
        mode === "external" ? "Auf externe Datenbank umschalten?" : "Auf lokale Datenbank umschalten?",
        mode === "external"
          ? "Alle Daten werden von der lokalen SQLite in die externe Datenbank kopiert (ersetzt dort vorhandene RMM-Daten). Danach startet das Backend neu."
          : "Der Stand der externen Datenbank wird in die lokale SQLite kopiert. Danach startet das Backend neu und arbeitet lokal.");
      if (!ok) return;
      try {
        const res = await api.switchDatabase({ ...dbxConfig(), mode });
        window.notify?.(`${res.detail} — Backend startet neu, Seite lädt gleich neu.`, "success");
        setTimeout(() => location.reload(), 8000);
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

    async function saveGuacd() {
      try {
        await api.updateSettings({
          guacd_host: root.querySelector("#ge-guacd-host").value.trim(),
          guacd_port: parseInt(root.querySelector("#ge-guacd-port").value, 10) || 4822,
        });
        window.notify?.(t("general_saved"), "success");
        refreshGuacStatus();
      } catch (e) {
        window.notify?.(e.message, "error");
      }
    }

    root.querySelector("#guacd-save").addEventListener("click", saveGuacd);
    root.querySelector("#guacd-test").addEventListener("click", async (e) => {
      const btn = e.currentTarget; const orig = btn.textContent;
      btn.disabled = true; btn.textContent = "…";
      await refreshGuacStatus();
      btn.disabled = false; btn.textContent = orig;
    });
    refreshGuacStatus();
  }

  // ---------------- USERS ----------------
  function renderUsersTab(root) {
    root.innerHTML = `
      <div class="settings-section">
        <h3>Benutzer anlegen</h3>
        <div class="form-row"><label>Benutzername</label><input type="text" id="su-username" /></div>
        <div class="form-row"><label>Anzeigename</label><input type="text" id="su-display" /></div>
        <div class="form-row">
          <label>Rolle</label>
          <select id="su-role">
            <option value="admin">Administrator (Vollzugriff)</option>
            <option value="viewer">Betrachter (Rechte über Gruppen)</option>
          </select>
        </div>
        <div class="form-row">
          <label>Passwort-Modus</label>
          <select id="su-pwmode">
            <option value="otp">Einmalpasswort (User setzt beim ersten Login selbst)</option>
            <option value="fixed">Passwort direkt festlegen</option>
          </select>
        </div>
        <div class="form-row hidden" id="su-pw-row"><label>Passwort</label><input type="text" id="su-password" /></div>
        <div id="su-error" class="form-error hidden"></div>
        <button class="btn-primary" id="su-create" style="margin-top:8px">Benutzer anlegen</button>
        <div id="su-result" style="margin-top:14px"></div>

        <h3 style="margin-top:26px">Vorhandene Benutzer</h3>
        <table class="data-table">
          <thead><tr><th>Benutzer</th><th>Name</th><th>Rolle</th><th>Gruppen</th><th></th></tr></thead>
          <tbody id="su-list"><tr><td colspan="5" style="color:var(--subtext)">Lädt...</td></tr></tbody>
        </table>
      </div>
    `;

    const pwMode = root.querySelector("#su-pwmode");
    const pwRow = root.querySelector("#su-pw-row");
    pwMode.addEventListener("change", () => pwRow.classList.toggle("hidden", pwMode.value !== "fixed"));

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
            <td>${esc(u.role)}${u.must_change_pw ? ' <span style="color:var(--warn)">(PW-Wechsel offen)</span>' : ""}</td>
            <td><button class="taskbar-btn" data-groups="${u.id}">Gruppen…</button></td>
            <td><button class="taskbar-btn" data-del="${u.id}">Löschen</button></td>`;
          list.appendChild(tr);
        }
        list.querySelectorAll("[data-del]").forEach((btn) =>
          btn.addEventListener("click", async () => {
            if (!(await uiConfirm("Benutzer wirklich löschen?", { okText: "Löschen", danger: true }))) return;
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
      if (!groups.length) { window.notify?.("Erst Gruppen im Tab 'Gruppen & Rollen' anlegen.", "warn"); return; }
      const current = await api.getUserGroups(userId).then((r) => r.group_ids).catch(() => []);
      // Nacheinander pro Gruppe fragen (eigener Dialog statt confirm()).
      const chosen = [];
      for (const g of groups) {
        const yes = await uiConfirm(`Gruppe "${g.name}" zuweisen?`, {
          description: `Aktuell zugewiesen: ${current.includes(g.id) ? "ja" : "nein"}`,
          okText: "Zuweisen", cancelText: "Nicht zuweisen" });
        if (yes) chosen.push(g.id);
      }
      await api.setUserGroups(userId, chosen);
      window.notify?.("Gruppen aktualisiert", "success");
    }

    root.querySelector("#su-create").addEventListener("click", async () => {
      const err = root.querySelector("#su-error");
      const result = root.querySelector("#su-result");
      err.classList.add("hidden");
      const isFixed = pwMode.value === "fixed";
      const username = root.querySelector("#su-username").value.trim();
      const payload = {
        username,
        display_name: root.querySelector("#su-display").value.trim() || username,
        role: root.querySelector("#su-role").value,
        one_time_password: !isFixed,
        password: isFixed ? root.querySelector("#su-password").value : null,
      };
      if (!payload.username) { err.textContent = "Benutzername fehlt"; err.classList.remove("hidden"); return; }
      try {
        const res = await api.createUser(payload);
        if (res.generated_password) {
          result.innerHTML = `<div style="background:rgba(45,212,191,0.1);border:1px solid var(--accent);padding:10px;border-radius:6px">
            Einmalpasswort für <b>${esc(res.username)}</b>: <code style="color:var(--accent)">${esc(res.generated_password)}</code><br/>
            <span style="color:var(--subtext);font-size:12px">Jetzt notieren - wird nur einmal angezeigt.</span></div>`;
        } else {
          result.innerHTML = `<span style="color:var(--accent)">Benutzer angelegt.</span>`;
        }
        root.querySelector("#su-username").value = "";
        root.querySelector("#su-display").value = "";
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
            <div style="font-size:11px;color:var(--subtext);margin-top:2px">${g.permissions.map((p) => esc(permLabels[p] || p)).join(", ") || "keine Rechte"}</div>
          </div>
          <button class="taskbar-btn" data-del="${g.id}">Löschen</button>
        </div>`).join("") : `<div style="color:var(--subtext);font-size:13px">Noch keine Gruppen.</div>`;
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
          <div style="background:rgba(45,212,191,0.08);border:1px solid var(--accent);border-radius:6px;padding:8px 10px;font-size:12px;color:var(--subtext);margin-bottom:14px">
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
        if (!payload.name || !payload.server) { window.notify?.("Name und Server erforderlich", "warn"); return; }
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
          if (!(await uiConfirm(t("sso_delete_confirm"), { okText: "Löschen", danger: true }))) return;
          await api.deleteRealm(btn.dataset.del); if (editingId === btn.dataset.del) editingId = null; draw();
        })
      );
    }

    await draw();
  }

  // ---------------- NOTIFICATIONS (Webhook-Verwaltung) ----------------
  function renderNotifTab(root) {
    root.innerHTML = `
      <div class="settings-section">
        <h3>Benachrichtigungen (Webhooks)</h3>
        <p style="color:var(--subtext);font-size:13px">
          Sende Benachrichtigungen an einen Chat-Kanal oder ein eigenes System.
          Für Discord: im Channel unter „Integrationen → Webhooks" eine Webhook-URL
          erstellen und hier einfügen.
        </p>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>Typ</label>
            <select id="nt-type">
              <option value="discord">Discord</option>
              <option value="custom">Benutzerdefiniert (Custom)</option>
            </select>
          </div>
          <div class="form-row" style="flex:1">
            <label>Name</label>
            <input type="text" id="nt-name" placeholder="z.B. Alerts-Channel" />
          </div>
        </div>
        <div class="form-row">
          <label>Webhook-URL</label>
          <input type="text" id="nt-url" placeholder="https://discord.com/api/webhooks/..." />
        </div>
        <div id="nt-error" class="form-error hidden"></div>
        <button class="btn-primary" id="nt-add" style="margin-top:4px;width:auto">+ Webhook speichern</button>

        <h3 style="margin-top:24px">Konfigurierte Webhooks</h3>
        <div id="nt-list"></div>
      </div>
    `;

    async function ntLoadList() {
      const listEl = root.querySelector("#nt-list");
      try {
        const hooks = await api.getWebhooks();
        if (!hooks.length) {
          listEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">Noch keine Webhooks konfiguriert.</div>`;
          return;
        }
        listEl.innerHTML = hooks.map((w) => `
          <div class="panel" style="margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;gap:8px">
            <div style="min-width:0">
              <strong>${esc(w.name)}</strong>
              <span style="font-size:11px;color:var(--subtext);margin-left:6px">${esc(w.type)}</span>
              <div style="font-size:11px;color:var(--subtext);font-family:monospace;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(w.url.slice(0, 48))}…</div>
            </div>
            <div style="display:flex;gap:6px;flex-shrink:0">
              <button class="taskbar-btn" data-nt-test="${w.id}">Testen</button>
              <button class="taskbar-btn" data-nt-del="${w.id}">Löschen</button>
            </div>
          </div>`).join("");
        listEl.querySelectorAll("[data-nt-test]").forEach((btn) =>
          btn.addEventListener("click", async () => {
            btn.textContent = "...";
            try {
              await api.testWebhook(btn.dataset.ntTest);
              window.notify?.("Test-Benachrichtigung gesendet", "success");
            } catch (e) { window.notify?.("Test fehlgeschlagen: " + e.message, "error"); }
            btn.textContent = "Testen";
          }));
        listEl.querySelectorAll("[data-nt-del]").forEach((btn) =>
          btn.addEventListener("click", async () => {
            if (!(await uiConfirm("Webhook löschen?", { okText: "Löschen", danger: true }))) return;
            await api.deleteWebhook(btn.dataset.ntDel); ntLoadList();
          }));
      } catch (e) {
        listEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
      }
    }

    root.querySelector("#nt-add").addEventListener("click", async () => {
      const name = root.querySelector("#nt-name").value.trim();
      const url = root.querySelector("#nt-url").value.trim();
      const type = root.querySelector("#nt-type").value;
      const err = root.querySelector("#nt-error"); err.classList.add("hidden");
      if (!name || !url) { err.textContent = "Name und URL erforderlich"; err.classList.remove("hidden"); return; }
      try {
        await api.createWebhook({ name, url, type });
        root.querySelector("#nt-name").value = ""; root.querySelector("#nt-url").value = "";
        window.notify?.("Webhook gespeichert", "success");
        ntLoadList();
      } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
    });
    ntLoadList();
  }

  draw();
}

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
import { isAdmin, hasGlobalPerm } from "../state.js";
import { renderSource } from "./source.js";
import { renderNotifications } from "./notifications.js";
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
          Öffentlicher Zugriff / Reverse-Proxy: Trage hier die <b>öffentliche Adresse</b> ein
          (z.B. <code>https://rmm.meinefirma.de</code>), unter der Agenten das Backend erreichen.
          Dieser Wert wird in die Agent-Installation eingebaut — sonst bekommt ein Agent die
          interne IP (<code>http://ip:4000</code>), die ein externer Client nicht erreichen kann.
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
        </div>

        <div data-adminsec>
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
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="ge-autoupdate-offline" ${(s.agent_auto_update_offline ?? "1") === "1" ? "checked" : ""} />
            Offline-Clients aktualisieren, sobald sie wieder online sind
          </label>
        </div>
        <p style="color:var(--subtext);font-size:12px;margin-top:-4px">
          Ist der Haken gesetzt, werden veraltete Agents direkt beim Wiederverbinden
          aktualisiert. Ohne Haken werden nur Clients aktualisiert, die bereits
          online sind, wenn eine neue Agent-Version bereitsteht.
        </p>
        <p style="color:var(--subtext);font-size:13px;margin-top:10px">
          Alle Agenten <b>sofort</b> aktualisieren: Es wird für jeden aktuell
          verbundenen Client (den du verwalten darfst) ein Agent-Update ausgelöst.
          Die Agenten aktualisieren sich selbst und verbinden neu.
        </p>
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
            <input type="checkbox" id="ge-updateall-offline" />
            Auch Offline-Clients vormerken (Update, sobald sie wieder online sind)
          </label>
        </div>
        <div class="form-row">
          <button class="taskbar-btn" id="ge-updateall">⬆️ Alle Agenten jetzt aktualisieren</button>
          <span id="ge-updateall-msg" style="margin-left:10px;font-size:12px;color:var(--subtext)"></span>
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

        <h3 style="margin-top:24px">Spotify</h3>
        <p style="color:var(--subtext);font-size:13px">
          Client-ID einer eigenen Spotify-App (developer.spotify.com → Dashboard →
          App erstellen). Benutzer können sich dann im 🎵 Audio Player mit ihrem
          Spotify-Konto anmelden; mit Premium spielt der Player volle Titel mit
          eigenen Controls.
        </p>
        <div class="form-row">
          <label>Client-ID</label>
          <input id="ge-spotify-id" value="${esc(s.spotify_client_id ?? "")}" placeholder="z.B. 8a3f…" style="max-width:420px" />
        </div>
        <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;
                    padding:8px 10px;font-size:12px;color:var(--subtext);max-width:640px;line-height:1.55">
          <b style="color:var(--text)">Redirect-URI (im Spotify-Dashboard eintragen):</b>
          <div style="display:flex;gap:6px;align-items:center;margin:5px 0">
            <code id="ge-spotify-uri" style="flex:1;padding:4px 7px;background:var(--panel);
                  border:1px solid var(--border);border-radius:6px;overflow-wrap:anywhere"></code>
            <button class="taskbar-btn" id="ge-spotify-copy" type="button"
                    title="Redirect-URI kopieren" style="flex:none">📋</button>
          </div>
          Sie wird automatisch aus der <b>Vollständigen URL</b> oben übernommen – es gibt
          also kein separates Feld. Ist dort nichts eingetragen, wird die Adresse
          verwendet, unter der du das Dashboard gerade geöffnet hast.<br>
          <b style="color:var(--text)">Tipp:</b> Trage im Spotify-Dashboard sicherheitshalber
          <b>beide</b> Schreibweisen ein – mit und ohne Schrägstrich am Ende
          (<code id="ge-spotify-alt"></code>). Spotify vergleicht zeichengenau.<br>
          <span id="ge-spotify-warn"></span>
          Der Eintrag im Spotify-Dashboard muss <b>zeichengenau</b> übereinstimmen –
          inklusive Port und abschließendem <code>/</code>.
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
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn-primary" id="guacd-save" style="width:auto;margin:0">${t("save")}</button>
          <button class="taskbar-btn" id="guacd-test">${t("guac_test")}</button>
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
      spWarn.innerHTML = (uri.startsWith("http://") && !loopback)
        ? `<span style="color:var(--warn,#f5a524)">⚠ Spotify akzeptiert diese Adresse nicht:
             erlaubt sind nur <b>https://…</b> oder <code>http://127.0.0.1:PORT/</code>.
             Trage oben unter „Vollständige URL“ deine HTTPS-Adresse ein – oder öffne das
             Dashboard über <code>127.0.0.1</code>.</span><br>`
        : "";
    }
    urlInput?.addEventListener("input", refreshRedirect);
    refreshRedirect();
    root.querySelector("#ge-spotify-copy")?.addEventListener("click", async () => {
      const uri = effRedirect();
      try {
        await navigator.clipboard.writeText(uri);
        window.notify?.("Redirect-URI kopiert", "success", 2000);
      } catch {
        window.notify?.("Kopieren nicht möglich – bitte manuell übernehmen: " + uri, "info", 8000);
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
        el.title = "Keine Berechtigung (Standard-Einstellungen ändern)";
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
      const { uiConfirm } = await import("../utils.js");
      if (!(await uiConfirm("Alle Agenten jetzt aktualisieren?", {
        description: includeOffline
          ? "Für jeden verbundenen Client wird ein Agent-Update ausgelöst. Offline-Clients werden vorgemerkt und aktualisieren sich, sobald sie wieder online sind."
          : "Für jeden verbundenen Client wird ein Agent-Update ausgelöst.",
        okText: "Jetzt aktualisieren" }))) return;
      btn.disabled = true;
      if (msg) msg.textContent = "Wird ausgelöst…";
      try {
        const res = await api.updateAllAgents({ include_offline: includeOffline });
        const txt = `Ausgelöst für ${res.triggered} Client(s)` +
          (res.queued_offline ? `, ${res.queued_offline} offline vorgemerkt` : "") +
          (res.offline && !res.queued_offline ? `, ${res.offline} offline übersprungen` : "");
        if (msg) msg.textContent = txt + " – Benachrichtigung folgt, sobald alle wieder verbunden sind.";
        window.notify?.(txt + ". Du wirst benachrichtigt, sobald alle Clients wieder verbunden sind.", "info", 8000);
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
    } // Ende Admin-only Verkabelung (mayAdminSet)
  }

  // ---------------- USERS ----------------
  // Standard-Rechte-Vorlagen beim Benutzer-Anlegen.
  const VIEW_ONLY_ALLOW = [
    "login", "see_dashboard", "restore_session", "edit_profile_name",
    "access_clients", "see_replay", "see_audit", "see_source", "see_permissions",
  ];
  function presetGrants(preset) {
    // Liefert {role, grants} für die gewählte Vorlage.
    if (preset === "full_admin") return { role: "admin", grants: null };
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
              : '<div style="color:var(--subtext);font-size:12px">Keine verwalteten Gruppen.</div>'}
            ${unmanaged.length ? `
              <details style="margin-top:10px">
                <summary style="cursor:pointer;font-size:12px;color:var(--subtext)">📁 AD – unverwaltet (${unmanaged.length})</summary>
                <div style="margin-top:4px">${unmanaged.map(rowHtml).join("")}</div>
              </details>` : ""}
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
            <button class="taskbar-btn" id="gp-cancel">Abbrechen</button>
            <button class="btn-primary" id="gp-ok">Übernehmen</button>
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
        <h3>Benutzer anlegen</h3>
        <div class="form-row"><label>Benutzername</label><input type="text" id="su-username" /></div>
        <div class="form-row"><label>Anzeigename</label><input type="text" id="su-display" /></div>
        <div class="form-row">
          <label>Standard-Rechte</label>
          <select id="su-role">
            <option value="full_admin">Full Admin (Vollzugriff)</option>
            <option value="view_only">View Only (nur ansehen)</option>
            <option value="login_only">Login Only (nur anmelden)</option>
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
        <div class="form-row">
          <label>Gruppen</label>
          <div style="display:flex;align-items:center;gap:10px">
            <button class="taskbar-btn" id="su-groups-btn" type="button">➕ Gruppen hinzufügen</button>
            <span id="su-groups-info" style="color:var(--subtext);font-size:12px">keine ausgewählt</span>
          </div>
        </div>
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

    // Für "Gruppen hinzufügen" beim Anlegen vorgemerkte Gruppen.
    let pendingGroups = [];
    let allGroupsCache = [];
    api.getGroups().then((g) => { allGroupsCache = g || []; }).catch(() => {});
    const groupsInfo = root.querySelector("#su-groups-info");
    root.querySelector("#su-groups-btn").addEventListener("click", async () => {
      if (!allGroupsCache.length) {
        try { allGroupsCache = await api.getGroups(); } catch {}
      }
      if (!allGroupsCache.length) { window.notify?.("Es gibt noch keine Gruppen.", "warn"); return; }
      const picked = await pickGroupsModal(allGroupsCache, pendingGroups);
      if (picked === null) return;
      pendingGroups = picked;
      groupsInfo.textContent = picked.length
        ? `${picked.length} Gruppe(n) ausgewählt` : "keine ausgewählt";
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
                ? ' <span style="color:var(--warn);font-size:10px" title="Vollzugriff über das Recht super_admin">ADMIN*</span>' : ""}${
                u.must_change_pw ? ' <span style="color:var(--warn)">(PW-Wechsel offen)</span>' : ""}</td>
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
      if (!groups.length) { window.notify?.("Es gibt noch keine Gruppen (im Berechtigungen-Menü anlegen).", "warn"); return; }
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
      const preset = root.querySelector("#su-role").value;   // full_admin|view_only|login_only
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
            Einmalpasswort für <b>${esc(res.username)}</b>: <code style="color:var(--accent)">${esc(res.generated_password)}</code><br/>
            <span style="color:var(--subtext);font-size:12px">Jetzt notieren - wird nur einmal angezeigt.</span></div>`;
        } else {
          result.innerHTML = `<span style="color:var(--accent)">Benutzer angelegt.</span>`;
        }
        root.querySelector("#su-username").value = "";
        root.querySelector("#su-display").value = "";
        pendingGroups = [];
        groupsInfo.textContent = "keine ausgewählt";
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
  // Benachrichtigungen: kompletter Bereich (Regeln, Webhooks, SMTP) liegt
  // in apps/notifications.js - hier nur noch delegieren.
  function renderNotifTab(root) {
    renderNotifications(root, win);
  }

  draw();
}

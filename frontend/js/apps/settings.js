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
import { esc } from "../utils.js";
import { t } from "../i18n.js";

export function renderSettings(body, win) {
  let activeTab = "general";

  function draw() {
    body.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%">
        <div class="tab-bar" style="padding:10px 14px;border-bottom:1px solid var(--border);gap:6px">
          <button class="tab-btn ${activeTab === "general" ? "active" : ""}" data-t="general">${t("tab_general")}</button>
          <button class="tab-btn ${activeTab === "users" ? "active" : ""}" data-t="users">${t("tab_users")}</button>
          <button class="tab-btn ${activeTab === "groups" ? "active" : ""}" data-t="groups">${t("tab_groups")}</button>
          <button class="tab-btn ${activeTab === "sso" ? "active" : ""}" data-t="sso">${t("tab_sso")}</button>
          <button class="tab-btn ${activeTab === "notifications" ? "active" : ""}" data-t="notifications">${t("tab_notifications")}</button>
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
    else if (activeTab === "notifications") renderNotifTab(content);
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
          <label>${t("general_server_url")}</label>
          <input type="text" id="ge-server-url" placeholder="https://rmm.meinefirma.de" value="${esc(s.server_url || "")}" />
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

        <div id="ge-error" class="form-error hidden"></div>
        <button class="btn-primary" id="ge-save" style="margin-top:8px">${t("save")}</button>
      </div>
    `;

    root.querySelector("#ge-save").addEventListener("click", async () => {
      const err = root.querySelector("#ge-error");
      err.classList.add("hidden");
      const payload = {
        server_url: root.querySelector("#ge-server-url").value.trim(),
        metrics_interval_seconds: parseInt(root.querySelector("#ge-interval").value, 10) || 60,
        metrics_retention_hours: parseInt(root.querySelector("#ge-retention").value, 10) || 1,
        replay_retention_days: parseInt(root.querySelector("#ge-replay").value, 10) || 10,
      };
      try {
        await api.updateSettings(payload);
        window.notify?.(t("general_saved"), "success");
      } catch (e) {
        err.textContent = e.message; err.classList.remove("hidden");
      }
    });
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
            if (!confirm("Benutzer wirklich löschen?")) return;
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
      const chosen = groups.filter((g) =>
        confirm(`Gruppe "${g.name}" zuweisen?\n(OK = zuweisen, Abbrechen = nicht)\n\nAktuell zugewiesen: ${current.includes(g.id) ? "ja" : "nein"}`)
      ).map((g) => g.id);
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
          if (!confirm(t("sso_delete_confirm"))) return;
          await api.deleteRealm(btn.dataset.del); if (editingId === btn.dataset.del) editingId = null; draw();
        })
      );
    }

    await draw();
  }

  // ---------------- NOTIFICATIONS (Verweis auf die App) ----------------
  function renderNotifTab(root) {
    root.innerHTML = `
      <div class="settings-section">
        <p style="color:var(--subtext);font-size:13px">
          Webhooks (Discord/Custom) werden in der eigenen App „Benachrichtigungen"
          verwaltet. Öffne sie über das Startmenü.
        </p>
        <button class="btn-primary" id="open-notif" style="width:auto">Benachrichtigungen öffnen</button>
      </div>
    `;
    root.querySelector("#open-notif").addEventListener("click", () => {
      import("../windowmanager.js").then((m) =>
        m.openWindow({ key: "notifications", appId: "notifications", title: t("notifications"), w: 600, h: 560 })
      );
    });
  }

  draw();
}

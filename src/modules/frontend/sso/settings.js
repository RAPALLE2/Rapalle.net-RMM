// settings.js (Modul "sso")
// -------------------------
// Verzeichnis-Anmeldung (LDAP/Active Directory) verwalten - frueher Einstellungen
// -> Register "SSO". Gezeigt in der Modul-App (Einstellungen) und als eigene App
// "SSO / Verzeichnis" im Startmenue (Recht "manage_sso").
//
// Abschalten des Moduls geht nur, wenn mindestens ein lokaler, aktiver Admin
// existiert (Backend: guard.py). Der Kasten oben zeigt, ob das der Fall ist.

import { api } from "/js/app/api.js";
import { esc, uiConfirm } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";
import { condenseHints } from "/js/app/help.js";

export function renderSsoApp(body) {
  body.innerHTML = `<div style="height:100%;overflow:auto;box-sizing:border-box"></div>`;
  return renderSsoSettings(body.firstElementChild);
}

/** Hinweis: Wie viele lokale Admins gibt es? (Bedingung fuers Abschalten) */
async function drawGuard(box) {
  if (!box) return;
  try {
    const r = await api.ssoLocalAdmins();
    if (r.count > 0) {
      const names = r.admins.slice(0, 5).map((a) => `<code>${esc(a.username)}</code>`).join(", ");
      box.innerHTML = `<div style="border:1px solid var(--border);border-radius:8px;padding:8px 10px;color:var(--subtext)">
        ✅ Lokale Admin-Accounts: ${r.count} (${names}${r.count > 5 ? ", …" : ""}) – SSO kann jederzeit deaktiviert werden.</div>`;
    } else {
      box.innerHTML = `<div style="border:1px solid var(--warn);border-radius:8px;padding:8px 10px;color:var(--warn)">
        ⚠ Kein lokaler, aktiver Admin-Account vorhanden. Solange das so ist, lässt sich SSO nicht deaktivieren
        – sonst könnte sich niemand mehr anmelden. Lege einen lokalen Benutzer mit Passwort und Rolle „admin“
        oder dem Recht „Full Admin“ an.</div>`;
    }
  } catch { box.innerHTML = ""; }
}

export async function renderSsoSettings(root) {
  let editingId = null;   // null = neues Realm anlegen, sonst wird bearbeitet

  async function draw() {
    let realms = [];
    try { realms = await api.getRealms(); }
    catch (e) { root.innerHTML = `<div class="settings-section"><div style="color:var(--danger)">${esc(e.message)}</div></div>`; return; }

    const editing = editingId ? realms.find((r) => r.id === editingId) : null;

    root.innerHTML = `
      <div class="settings-section">
        <p style="color:var(--subtext);font-size:13px">${t("sso_intro")}</p>
        <div id="rl-guard" style="font-size:12.5px;margin-bottom:12px"></div>
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
          <button class="btn-primary" id="rl-save">${editing ? t("save") : "+ " + t("sso_add_realm")}</button>
          ${editing ? `<button class="btn" id="rl-cancel">${t("cancel")}</button>` : ""}
        </div>

        <h3 style="margin-top:24px">${t("sso_connected")}</h3>
        <div id="rl-list"></div>
      </div>
    `;

    drawGuard(root.querySelector("#rl-guard"));

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
          <button class="btn" data-test="${r.id}">${t("test")}</button>
          <button class="btn" data-toggle="${r.id}">${r.enabled ? t("sso_disable") : t("sso_enable")}</button>
          <button class="btn" data-edit="${r.id}">${t("edit")}</button>
          <button class="btn btn-danger" data-del="${r.id}">${t("delete")}</button>
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

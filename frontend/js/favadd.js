// favadd.js
// ---------
// Dialog hinter dem ★-Knopf in der Favoriten-Leiste.
//
// Damit kann man sich ALLES anheften, was das RMM öffnen kann - nicht nur die
// Dinge, die schon einen eigenen Stern haben:
//   - App               (z.B. "Terminal-Übersicht", "Tickets", "Browser")
//   - App mit Unterseite (z.B. "Einstellungen" direkt auf dem Reiter "Source")
//   - Client-Sitzung     (Terminal / Explorer / Bildschirm / Guacamole /
//                         Task-Manager für einen bestimmten Client)
//   - Website / URL      (intern im Browser-Fenster oder extern)
//   - Client / Tenant    (springt in der Seitenleiste dorthin)
//
// Gespeichert wird das Ergebnis als "Pin" in sidebar.js (siehe addPin()) und
// damit - wie alle UI-Einstellungen - serverseitig in der Datenbank.

import { esc } from "./utils.js";
import { state } from "./state.js";
import { addPin } from "./sidebar.js";
import { t } from "./i18n.js";

// Apps, die eine sinnvolle Unterseite kennen. Der Schlüssel ist die appId, der
// Wert die Liste möglicher props.tab-Werte mit Anzeigename.
// Nur Schluessel, keine fertigen Texte: Diese Tabelle wird beim Laden des
// Moduls ausgewertet, da steht die Sprache noch nicht fest. `label` ist ein
// Getter, damit t() erst beim Zugriff laeuft.
const APP_SUBPAGES = {
  settings: [
    { value: "general", get label() { return t("tab_general"); } },
    { value: "users", get label() { return t("tab_users"); } },
    { value: "sso", label: "SSO" },
    { value: "branding", label: "Branding" },
    { value: "notifications", get label() { return t("fa_notifications"); } },
    { value: "source", label: "Source" },
  ],
};

// Fenstergrößen für "App mit Unterseite" (sonst öffnet der normale Opener).
const APP_SIZE = {
  settings: { w: 560, h: 620 },
};

const SESSION_ACTIONS = [
  { value: "terminal", label: "Terminal", icon: "🖥️" },
  { value: "explorer", get label() { return t("fa_explorer"); }, icon: "📁" },
  { value: "vnc", get label() { return t("fa_screen"); }, icon: "🖵" },
  { value: "guacamole", label: "Guacamole (RDP/SSH)", icon: "🪟" },
  { value: "taskmanager", get label() { return t("fa_taskmanager"); }, icon: "📈" },
];

// App-Katalog aus den (versteckten) Startmenü-Knöpfen in index.html lesen -
// so bleibt der Dialog automatisch aktuell, wenn Apps dazukommen.
function readAppCatalog() {
  const out = [];
  document.querySelectorAll("#start-menu [data-app]").forEach((btn) => {
    const id = btn.dataset.app;
    if (!id) return;
    const iconEl = btn.querySelector(":scope > .app-icon");
    const labelEl = btn.querySelector(":scope > span:not(.app-icon)");
    out.push({
      id,
      icon: iconEl ? iconEl.textContent.trim() : "⭐",
      label: labelEl ? labelEl.textContent.trim() : id,
    });
  });
  return out.sort((a, b) => a.label.localeCompare(b.label));
}

const FIELD_CSS = `width:100%;box-sizing:border-box;padding:7px 10px;border-radius:6px;
  border:1px solid var(--border,#2a3648);background:var(--panel-2,#0e1520);
  color:var(--text,#e8eef7);font-size:13px`;

export function openAddFavoriteDialog() {
  const apps = readAppCatalog();
  const clients = [...state.clients].sort((a, b) =>
    String(a.hostname || "").localeCompare(String(b.hostname || ""), "de"));
  const tenants = state.hierarchy?.tenants || [];

  const overlay = document.createElement("div");
  overlay.className = "favadd-overlay";
  overlay.style.cssText = `position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,0.5);
    display:flex;align-items:center;justify-content:center;`;

  overlay.innerHTML = `
    <div class="favadd-box" style="background:var(--panel,#131c2b);color:var(--text,#e8eef7);
      border:1px solid var(--border,#2a3648);border-radius:12px;min-width:400px;max-width:520px;
      width:92vw;padding:18px;box-shadow:0 16px 48px rgba(0,0,0,0.5)">
      <div style="font-size:14px;font-weight:600;margin-bottom:4px">★ ${t("fa_title")}</div>
      <div style="font-size:12px;color:var(--subtext,#8fa3bd);margin-bottom:14px">
        ${t("fa_hint")}
      </div>

      <label style="display:block;font-size:12px;color:var(--subtext);margin-bottom:4px">${t("fa_kind")}</label>
      <select id="fa-kind" style="${FIELD_CSS};margin-bottom:12px">
        <option value="app">App</option>
        <option value="session">${t("fa_k_session")}</option>
        <option value="website">${t("fa_k_website")}</option>
        <option value="client">${t("fa_k_client")}</option>
        <option value="tenant">${t("fa_k_tenant")}</option>
      </select>

      <div id="fa-fields"></div>

      <label style="display:block;font-size:12px;color:var(--subtext);margin:12px 0 4px">${t("set_display_name")}</label>
      <input id="fa-label" type="text" placeholder="${t("fa_auto_label")}" style="${FIELD_CSS}" />

      <div style="display:flex;gap:16px;margin-top:12px;font-size:12px;color:var(--subtext)">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" id="fa-dim-s" checked /> ${t("fa_sidebar")}
        </label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" id="fa-dim-d" /> Dashboard
        </label>
      </div>

      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
        <button class="fa-cancel taskbar-btn">${t("cancel")}</button>
        <button class="fa-ok" style="border:1px solid var(--accent,#4da6ff);background:var(--accent,#4da6ff)22;
          color:var(--accent,#4da6ff);border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px">${t("fa_pin")}</button>
      </div>
    </div>`;

  document.body.appendChild(overlay);

  const kindSel = overlay.querySelector("#fa-kind");
  const fields = overlay.querySelector("#fa-fields");
  const labelInput = overlay.querySelector("#fa-label");

  const opt = (v, l) => `<option value="${esc(v)}">${esc(l)}</option>`;

  function drawFields() {
    const kind = kindSel.value;
    if (kind === "app") {
      fields.innerHTML = `
        <label style="display:block;font-size:12px;color:var(--subtext);margin-bottom:4px">App</label>
        <select id="fa-app" style="${FIELD_CSS}">
          ${apps.map((a) => opt(a.id, `${a.icon} ${a.label}`)).join("")}
        </select>
        <div id="fa-sub-wrap" style="display:none;margin-top:10px">
          <label style="display:block;font-size:12px;color:var(--subtext);margin-bottom:4px">Unterseite</label>
          <select id="fa-sub" style="${FIELD_CSS}"></select>
        </div>`;
      const appSel = fields.querySelector("#fa-app");
      const wrap = fields.querySelector("#fa-sub-wrap");
      const subSel = fields.querySelector("#fa-sub");
      const syncSub = () => {
        const subs = APP_SUBPAGES[appSel.value];
        if (subs) {
          wrap.style.display = "";
          subSel.innerHTML = opt("", t("fa_whole_app")) + subs.map((s) => opt(s.value, s.label)).join("");
        } else {
          wrap.style.display = "none";
          subSel.innerHTML = "";
        }
        suggestLabel();
      };
      appSel.addEventListener("change", syncSub);
      subSel.addEventListener("change", suggestLabel);
      syncSub();
    } else if (kind === "session") {
      fields.innerHTML = `
        <label style="display:block;font-size:12px;color:var(--subtext);margin-bottom:4px">Client</label>
        <select id="fa-client" style="${FIELD_CSS}">
          ${clients.map((c) => opt(c.id, c.hostname)).join("")}
        </select>
        <label style="display:block;font-size:12px;color:var(--subtext);margin:10px 0 4px">Sitzung</label>
        <select id="fa-action" style="${FIELD_CSS}">
          ${SESSION_ACTIONS.map((a) => opt(a.value, `${a.icon} ${a.label}`)).join("")}
        </select>`;
      fields.querySelector("#fa-client").addEventListener("change", suggestLabel);
      fields.querySelector("#fa-action").addEventListener("change", suggestLabel);
    } else if (kind === "website") {
      fields.innerHTML = `
        <label style="display:block;font-size:12px;color:var(--subtext);margin-bottom:4px">URL</label>
        <input id="fa-url" type="text" placeholder="https://…" style="${FIELD_CSS}" />
        <label style="display:block;font-size:12px;color:var(--subtext);margin:10px 0 4px">${t("fa_open_in")}</label>
        <select id="fa-mode" style="${FIELD_CSS}">
          ${opt("internal", "🪟 internem Browser-Fenster")}
          ${opt("external", "🔗 neuem Browser-Tab")}
        </select>`;
      fields.querySelector("#fa-url").addEventListener("input", suggestLabel);
    } else if (kind === "client") {
      fields.innerHTML = `
        <label style="display:block;font-size:12px;color:var(--subtext);margin-bottom:4px">Client</label>
        <select id="fa-target" style="${FIELD_CSS}">
          ${clients.map((c) => opt(c.id, c.hostname)).join("")}
        </select>`;
      fields.querySelector("#fa-target").addEventListener("change", suggestLabel);
    } else {
      fields.innerHTML = `
        <label style="display:block;font-size:12px;color:var(--subtext);margin-bottom:4px">Tenant</label>
        <select id="fa-target" style="${FIELD_CSS}">
          ${tenants.map((t) => opt(t.id, t.name)).join("")}
        </select>`;
      fields.querySelector("#fa-target").addEventListener("change", suggestLabel);
    }
    labelInput.dataset.auto = "1";
    suggestLabel();
  }

  // Anzeigename automatisch vorschlagen, solange der Benutzer nichts Eigenes
  // eingetippt hat.
  function suggestLabel() {
    if (labelInput.dataset.auto !== "1") return;
    labelInput.value = buildMeta().label || "";
  }
  labelInput.addEventListener("input", () => { labelInput.dataset.auto = "0"; });

  // Aus den Formularfeldern die zu speichernden Pin-Daten bauen.
  function buildMeta() {
    const kind = kindSel.value;
    if (kind === "app") {
      const appId = fields.querySelector("#fa-app")?.value;
      const app = apps.find((a) => a.id === appId);
      const sub = fields.querySelector("#fa-sub")?.value || "";
      const subLabel = (APP_SUBPAGES[appId] || []).find((s) => s.value === sub)?.label;
      return {
        kind: "app", appId, icon: app?.icon || "⭐",
        props: sub ? { tab: sub } : {},
        ...(APP_SIZE[appId] || {}),
        label: subLabel ? `${app?.label || appId} → ${subLabel}` : (app?.label || appId),
      };
    }
    if (kind === "session") {
      const clientId = fields.querySelector("#fa-client")?.value;
      const action = fields.querySelector("#fa-action")?.value;
      const c = clients.find((x) => x.id === clientId);
      const a = SESSION_ACTIONS.find((x) => x.value === action);
      return {
        kind: "session", clientId, action, icon: a?.icon,
        label: `${a?.label || action} — ${c?.hostname || ""}`.trim(),
        sub: c?.hostname || "",
      };
    }
    if (kind === "website") {
      const url = (fields.querySelector("#fa-url")?.value || "").trim();
      const open_mode = fields.querySelector("#fa-mode")?.value || "internal";
      let name = url;
      try { name = new URL(url).hostname || url; } catch {}
      return { kind: "website", url, open_mode, label: name, sub: url };
    }
    const targetId = fields.querySelector("#fa-target")?.value;
    if (kind === "client") {
      const c = clients.find((x) => x.id === targetId);
      return { kind: "client", targetId, label: c?.hostname || "" };
    }
    const tn = tenants.find((x) => x.id === targetId);
    return { kind: "tenant", targetId, label: tn?.name || "" };
  }

  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
  overlay.querySelector(".fa-cancel").addEventListener("click", close);

  overlay.querySelector(".fa-ok").addEventListener("click", () => {
    const meta = buildMeta();
    const typed = labelInput.value.trim();
    if (typed) meta.label = typed;
    if (meta.kind === "website" && !meta.url) {
      window.notify?.(t("fa_need_url"), "warn", 4000);
      return;
    }
    if ((meta.kind === "session" && !meta.clientId) ||
        ((meta.kind === "client" || meta.kind === "tenant") && !meta.targetId)) {
      window.notify?.(t("fa_need_entry"), "warn", 4000);
      return;
    }
    const dims = {
      s: overlay.querySelector("#fa-dim-s").checked,
      d: overlay.querySelector("#fa-dim-d").checked,
    };
    if (!dims.s && !dims.d) dims.s = true;
    addPin(meta, dims);
    window.notify?.(t("fa_pinned", { name: meta.label }), "success", 2500);
    close();
  });

  kindSel.addEventListener("change", drawFields);
  drawFields();
  setTimeout(() => kindSel.focus(), 30);
}

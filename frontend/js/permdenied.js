// permdenied.js
// -------------
// Ein Fenster für den Fall "dafür fehlt dir das Recht".
//
// Bisher endete so etwas in einer kurzen Fehlermeldung - der Benutzer wusste
// dann, dass es nicht geht, aber nicht, wie er weiterkommt. Deshalb bietet
// dieser Dialog direkt an, ein Ticket an den Support zu schicken.
//
// Was am Ticket FESTSTEHT (und deshalb nicht bearbeitbar ist):
//   Titel      die Sache, für die das Recht fehlt
//   Betrifft   der Client, an dem es fehlt - oder das Haupt-RMM
//   Zuweisung  die Support-Gruppe
// Frei wählbar bleiben nur Beschreibung und Priorität. Das ist Absicht: Die
// Anfrage soll nachvollziehbar genau das abbilden, was tatsächlich passiert
// ist, und nicht umgeschrieben werden können.

import { api } from "./api.js";
import { esc } from "./utils.js";
import { state } from "./state.js";
import { RMM_TARGET, targetLabel } from "./tickettarget.js";
import { t } from "./i18n.js";

// Nur Schluessel: Die Tabelle wird beim Laden des Moduls ausgewertet, die
// Sprache steht da noch nicht fest. Uebersetzt wird beim Aufbau des Dialogs.
const PRIO_KEY = {
  low: "tk_p_low",
  normal: "tk_p_normal",
  high: "tk_p_high",
  urgent: "pd_p_urgent",
};

/**
 * Zeigt das Fenster "Fehlende Berechtigung".
 *
 * @param {object} opts
 *   action    Klartext dessen, was nicht ging ("Terminal öffnen",
 *             "Einstellungen speichern"). Wird der Ticket-Titel.
 *   perm      optional: der technische Rechte-Schlüssel (nur als Hinweis).
 *   clientId  optional: Client, an dem das Recht fehlt. Ohne Angabe gilt die
 *             Anfrage dem Haupt-RMM.
 *   detail    optional: zusätzliche Erklärung im Fenster.
 */
export function showPermissionDenied(opts = {}) {
  const action = (opts.action || "Diese Aktion").trim();
  const clientId = opts.clientId || null;
  const target = clientId || RMM_TARGET;
  const title = clientId
    ? `Fehlende Berechtigung: ${action} (${clientName(clientId)})`
    : `Fehlende Berechtigung: ${action}`;

  const overlay = document.createElement("div");
  overlay.className = "permdenied-overlay";
  overlay.style.cssText = `position:fixed;inset:0;z-index:9600;background:rgba(0,0,0,0.55);
    display:flex;align-items:center;justify-content:center`;

  overlay.innerHTML = `
    <div style="background:var(--panel,#131c2b);color:var(--text,#e8eef7);
                border:1px solid var(--border,#2a3648);border-radius:12px;
                width:520px;max-width:94vw;max-height:92vh;overflow:auto;padding:18px;
                box-shadow:0 16px 48px rgba(0,0,0,0.5)">
      <div style="font-size:15px;font-weight:700;margin-bottom:6px">🔒 ${t("pd_title")}</div>
      <div style="font-size:13px;color:var(--subtext);line-height:1.5">
        ${t("pd_missing", { action: `<b style="color:var(--text)">${esc(action)}</b>` })}${clientId ? ` ${t("pd_on")} <b style="color:var(--text)">${esc(clientName(clientId))}</b>` : ""}.
        ${opts.detail ? `<div style="margin-top:6px">${esc(opts.detail)}</div>` : ""}
        ${opts.perm ? `<div style="margin-top:6px;font-size:11.5px">${t("pd_needed")}: <code>${esc(opts.perm)}</code></div>` : ""}
      </div>

      <div id="pd-form" style="display:none;margin-top:14px;border-top:1px solid var(--border);padding-top:14px">
        <div style="font-size:13px;font-weight:600;margin-bottom:8px">Anfrage an den Support</div>

        <div class="form-row"><label>${t("tk_title_label")}</label>
          <input id="pd-title" value="${esc(title)}" readonly
                 title="${t("pd_title_fixed")}"
                 style="opacity:.7;cursor:not-allowed" /></div>

        <div class="form-row"><label>Betrifft</label>
          <input id="pd-target" value="${esc(targetLabel(target))}" readonly
                 title="Ergibt sich aus der Stelle, an der das Recht fehlt."
                 style="opacity:.7;cursor:not-allowed" /></div>

        <div class="form-row"><label>Geht an</label>
          <input id="pd-assign" value="👥 Support" readonly
                 style="opacity:.7;cursor:not-allowed" /></div>

        <div class="form-row"><label>${t("tk_priority")}</label>
          <select id="pd-prio">
            ${Object.keys(PRIO_KEY).map((k) =>
              `<option value="${k}"${k === "normal" ? " selected" : ""}>${t(PRIO_KEY[k])}</option>`).join("")}
          </select></div>

        <div class="form-row"><label>Beschreibung</label></div>
        <textarea id="pd-desc" rows="5" placeholder="Wozu brauchst du den Zugriff?"
          style="width:100%;box-sizing:border-box;background:var(--panel-2);
                 border:1px solid var(--border);border-radius:8px;color:var(--text);
                 padding:8px;font:inherit;font-size:13px"></textarea>

        <div id="pd-error" style="display:none;color:var(--danger,#f66);font-size:12px;margin-top:8px"></div>
      </div>

      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;flex-wrap:wrap">
        <button class="taskbar-btn" id="pd-close">${t("close")}</button>
        <button class="btn-primary" id="pd-open" style="width:auto;margin:0">🎫 Ticket erstellen</button>
        <button class="btn-primary" id="pd-send" style="width:auto;margin:0;display:none">Absenden</button>
      </div>
    </div>`;

  document.body.appendChild(overlay);

  const q = (sel) => overlay.querySelector(sel);
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
  q("#pd-close").addEventListener("click", close);

  // Erst auf Knopfdruck das Formular zeigen - wer nur die Meldung lesen
  // wollte, soll nicht gleich ein Ticket vor sich haben.
  q("#pd-open").addEventListener("click", () => {
    q("#pd-form").style.display = "";
    q("#pd-open").style.display = "none";
    q("#pd-send").style.display = "";
    q("#pd-desc").focus();
  });

  q("#pd-send").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const err = q("#pd-error");
    err.style.display = "none";
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "…";
    try {
      const t = await api.createTicket({
        title,
        description: q("#pd-desc").value,
        priority: q("#pd-prio").value,
        clients: [target],
        // Die Zuweisung an den Support macht das Backend (perm_request) -
        // der Benutzer hat hier in der Regel kein 'ticket_assign'.
        assignees: [],
        perm_request: true,
      });
      close();
      window.notify?.(`Ticket #${String(t.id).slice(0, 8)} an den Support gesendet.`,
                      "success", 6000);
    } catch (ex) {
      err.textContent = ex.message;
      err.style.display = "";
      btn.disabled = false;
      btn.textContent = orig;
    }
  });
}

function clientName(id) {
  const c = state.clients.find((x) => x.id === id);
  return c ? c.hostname : String(id).slice(0, 8) + "…";
}

// Bequemer Wrapper: prüft ein Recht und zeigt bei Fehlen den Dialog.
// Rückgabe: true = erlaubt (Aufrufer macht weiter), false = Dialog gezeigt.
export function requirePerm(perm, action, clientId = null) {
  const ok = clientId
    ? hasClientPermSafe(clientId, perm)
    : hasGlobalPermSafe(perm);
  if (!ok) showPermissionDenied({ action, perm, clientId });
  return ok;
}

function hasGlobalPermSafe(perm) {
  if (state.perms?.admin) return true;
  return !!state.perms?.global?.[perm];
}

function hasClientPermSafe(clientId, perm) {
  if (state.perms?.admin) return true;
  return !!state.perms?.clients?.[clientId]?.[perm];
}

// Global erreichbar machen, damit auch Stellen ohne Import darauf kommen
// (z.B. Fehlerbehandlung in api.js bei einem 403 vom Server).
try {
  window.showPermissionDenied = showPermissionDenied;
} catch { /* ignorieren */ }

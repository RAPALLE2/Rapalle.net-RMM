// apps/relaymanager.js
// --------------------
// Explorer-Relay-Verwaltung mit zwei Spalten:
//   links  = Clients mit INAKTIVEM Relay
//   rechts = Clients mit AKTIVEM Relay
// In der Mitte ein „Umschalten"-Button: ausgewählte Clients wechseln die Seite
// (inaktiv -> aktiv bzw. aktiv -> inaktiv).

import { api } from "../api.js";
import { esc, uiChoice } from "../utils.js";
import { isAdmin as userIsAdmin, state, hasGlobalPerm, hasClientPerm } from "../state.js";
// t() unter Alias: in dieser Datei ist "t" bereits als lokaler
// Variablenname belegt (Tenant/Target/Trigger/Token o.ä.).
import { t as tr } from "../i18n.js";
import { publicBaseNow, loadPublicBase } from "../config.js";

// Auswahl-Optionen für das automatische Schließen eines Relays.
const RELAY_AUTOCLOSE_BASE = [
  { label: "Nach 10 Minuten", value: 10 },
  { label: "Nach 30 Minuten", value: 30 },
  { label: "Nach 1 Stunde", value: 60 },
  { label: "Nach 2 Stunden", value: 120 },
];
const RELAY_UNLIMITED_OPTION = { label: "Nie (dauerhaft offen)", value: 0 };

// „Nie" nur anbieten, wenn der Benutzer unbegrenzte Relays setzen darf
// (global relay_unlimited ODER c_relay_unlimited auf ALLEN betroffenen Clients).
function relayOptionsFor(clientIds) {
  const mayUnlimited = hasGlobalPerm("relay_unlimited")
    || (clientIds.length > 0 && clientIds.every((id) => hasClientPerm(id, "c_relay_unlimited")));
  return mayUnlimited ? [...RELAY_AUTOCLOSE_BASE, RELAY_UNLIMITED_OPTION] : [...RELAY_AUTOCLOSE_BASE];
}

// Kopieren mit Fallback (identisch zum Explorer): navigator.clipboard braucht
// einen sicheren Kontext; sonst über ein temporäres Textfeld + execCommand.
function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    try { document.execCommand("copy") ? resolve() : reject(new Error(tr("exp_copy_fail"))); }
    catch (e) { reject(e); }
    finally { ta.remove(); }
  });
}

export function renderRelayManager(body, win) {
  let clients = [];
  const selected = new Set();   // ausgewählte Client-IDs
  const isAdmin = (() => { try { return userIsAdmin(); } catch { return false; } })();
  // Kanonische Adresse im Hintergrund holen; bis dahin gilt location.
  loadPublicBase().catch(() => {});

  // Verbindungsdaten aus der KANONISCHEN Adresse der Installation ableiten -
  // nicht aus location. Wer das Dashboard intern per IP oeffnet, soll hier
  // trotzdem die Adresse sehen, unter der das Netzlaufwerk wirklich erreichbar
  // ist (Einstellung "Vollstaendige URL" bzw. Domain/Host + Port).
  // Die Werte werden bei jedem Aufbau der Anleitung neu gelesen, weil
  // loadPublicBase() erst kurz nach dem Oeffnen antwortet.
  function conn() {
    const b = publicBaseNow();
    const https = b.scheme === "https";
    const port = String(b.port || (https ? 443 : 80));
    // Standard-Ports gehoeren nicht in eine angezeigte Adresse.
    const netloc = b.netloc || (port === (https ? "443" : "80")
      ? b.host : `${b.host}:${port}`);
    return {
      https, host: b.host, port, netloc,
      davUrl: `${b.scheme}://${netloc}/dav`,
      // WebDAV-Schreibweise fuer Windows: host@port, NICHT host:port
      // (mit Doppelpunkt versucht Windows SMB -> Fehler 67).
      winPath: https ? `\\\\${b.host}@SSL@${port}\\dav`
                     : `\\\\${b.host}@${port}\\dav`,
      linuxUrl: `${https ? "davs" : "dav"}://${netloc}/dav`,
    };
  }

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div style="padding:12px 14px;border-bottom:1px solid var(--border)">
        <div style="font-size:15px;font-weight:700">🔌 ${tr("rm_title")}</div>
        <div style="color:var(--subtext);font-size:12px;margin-top:2px">
          ${tr("rm_hint")}
          ${isAdmin ? "" : `<b style="color:var(--warn,#f5a524)"> ${tr("rm_admin_only")}</b>`}
        </div>

        <details style="margin-top:8px" id="rm-guide">
          <summary style="cursor:pointer;color:var(--accent);font-size:12.5px;user-select:none">
            📄 Verbindungsanleitung anzeigen
          </summary>
          <div id="rm-guide-body" style="margin-top:10px"></div>
        </details>
      </div>

      <div style="flex:1;display:grid;grid-template-columns:1fr auto 1fr;gap:0;min-height:0">
        <!-- Links: inaktiv -->
        <div style="display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--border)">
          <div style="padding:8px 12px;font-weight:600;color:var(--danger,#ff4d6d)">
            ● Inaktiv <span id="rm-count-off" style="color:var(--subtext);font-weight:400"></span>
          </div>
          <div id="rm-list-off" style="flex:1;overflow:auto;padding:6px"></div>
        </div>

        <!-- Mitte: Umschalten -->
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;padding:0 14px">
          <button id="rm-switch" class="btn-primary" style="margin:0;white-space:nowrap"
                  ${isAdmin ? "" : "disabled"}>⇄ Umschalten</button>
          <div id="rm-selinfo" style="color:var(--subtext);font-size:12px;text-align:center"></div>
        </div>

        <!-- Rechts: aktiv -->
        <div style="display:flex;flex-direction:column;min-height:0;border-left:1px solid var(--border)">
          <div style="padding:8px 12px;font-weight:600;color:var(--online,#3ecf8e)">
            ● Aktiv <span id="rm-count-on" style="color:var(--subtext);font-weight:400"></span>
          </div>
          <div id="rm-list-on" style="flex:1;overflow:auto;padding:6px"></div>
        </div>
      </div>
    </div>
  `;

  const listOff = body.querySelector("#rm-list-off");
  const listOn = body.querySelector("#rm-list-on");
  const countOff = body.querySelector("#rm-count-off");
  const countOn = body.querySelector("#rm-count-on");
  const selInfo = body.querySelector("#rm-selinfo");
  const switchBtn = body.querySelector("#rm-switch");

  // ---- Verbindungsanleitung: exakt die Karten-/Copy-Button-Optik aus dem
  //      Client-Explorer (Adresse, net use, Anmeldung, grafische Anleitung). ----
  const username = (() => { try { return state.user?.username || ""; } catch { return ""; } })();

  const guideCard = (inner) =>
    `<div class="panel" style="padding:14px;margin-bottom:12px">${inner}</div>`;
  const copyField = (label, value) => `
    <div style="color:var(--subtext);font-size:13px">${label}</div>
    <div style="display:flex;gap:6px">
      <input type="text" readonly value="${esc(value)}" onclick="this.select()" style="flex:1;font-family:monospace" />
      <button class="taskbar-btn" data-copy="${esc(value)}">Kopieren</button>
    </div>`;

  function renderGuide() {
    const gb = body.querySelector("#rm-guide-body");
    if (!gb || gb.dataset.done) return;   // nur einmal aufbauen
    gb.dataset.done = "1";
    // Erst hier aufloesen: loadPublicBase() antwortet kurz nach dem Oeffnen,
    // die Anleitung wird aber erst beim Aufklappen gebaut - dann steht der
    // richtige Wert bereits zur Verfuegung.
    const C = conn();
    const scheme = C.https ? "https" : "http";
    const httpRoot = C.davUrl;                  // proto://host[:port]/dav
    const uncRoot = C.winPath;                  // \\\\host@[SSL@]port\\dav

    const address = guideCard(`
      <div style="font-weight:700;margin-bottom:6px">📍 ${tr("rm_one_drive")}</div>
      <div style="color:var(--subtext);font-size:13px;margin-bottom:10px">
        ${tr("exp_relay_one_desc")}
      </div>
      <div style="display:grid;grid-template-columns:auto 1fr;gap:8px 12px;align-items:center">
        ${copyField("Windows / macOS / Linux", httpRoot)}
      </div>
      <div style="color:var(--subtext);font-size:12px;margin-top:8px">
        ${tr("rm_enter_addr")}
      </div>`);

    const netUse = username ? guideCard(`
      <div style="font-weight:700;margin-bottom:6px">💽 ${tr("rm_as_drive")}</div>
      <div style="color:var(--subtext);font-size:13px;margin-bottom:8px">
        ${tr("rm_drive_hint")} ${tr("exp_relay_cmd_hint")}
      </div>
      ${copyField(tr("exp_relay_cmd"), `net use Z: ${uncRoot} /persistent:yes /user:${username} `)}
      <div style="color:var(--subtext);font-size:12px;margin-top:8px">
        ${tr("rm_letter_hint")}
        <code>${esc(uncRoot)}</code> ${tr("exp_relay_unc_hint")}
        <b>${tr("not_upper")}</b> <code>:${esc(C.port)}</code> ${tr("exp_relay_colon_warn")}
      </div>
      <div style="color:var(--subtext);font-size:12px;margin-top:6px">
        ${tr("rm_disconnect")} <code>net use Z: /delete</code>
      </div>`) : "";

    const login = guideCard(`
      <div style="font-weight:700;margin-bottom:6px">🔑 ${tr("rm_login")}</div>
      <div style="color:var(--subtext);font-size:13px;line-height:1.7">
        ${tr("exp_relay_login_desc")}
        <ul style="margin:6px 0 0;padding-left:18px">
          <li>${tr("username")}: <b>${esc(username || tr("exp_relay_your_user"))}</b></li>
          <li>${tr("exp_relay_pw_line")}</li>
        </ul>
      </div>`);

    const mac = guideCard(`
      <div style="font-weight:700;margin-bottom:6px">🍎 macOS (Finder)</div>
      <div style="color:var(--subtext);font-size:13px;margin-bottom:8px">
        ${tr("rm_mac_hint")}
      </div>
      ${copyField(tr("rm_server_addr"), httpRoot)}`);

    const linux = guideCard(`
      <div style="font-weight:700;margin-bottom:6px">🐧 Linux</div>
      <div style="color:var(--subtext);font-size:13px;margin-bottom:8px">
        ${tr("rm_linux_hint")}
      </div>
      <div style="display:grid;gap:8px">
        ${copyField(tr("rm_fm_addr"), C.linuxUrl)}
        ${copyField(tr("rm_mount_cmd"), `sudo mount -t davfs ${httpRoot} /mnt/rmm`)}
      </div>`);

    const guide = guideCard(`
      <div style="font-weight:700;margin-bottom:6px">🪟 ${tr("rm_win_gui")}</div>
      <ol style="margin:0;padding-left:18px;color:var(--subtext);font-size:13px;line-height:1.8">
        <li>${tr("exp_relay_step1")}</li>
        <li>${tr("exp_relay_step2")}</li>
        <li>${tr("exp_relay_step3")}</li>
        <li>${tr("exp_relay_step4")}</li>
      </ol>
      <div style="color:var(--subtext);font-size:12px;margin-top:8px">
        ${tr("exp_relay_warn_colon")}
      </div>
      ${C.https ? "" : `<div style="color:var(--warn,#f5a524);font-size:12px;margin-top:8px">
        ${tr("rm_https_note")}
      </div>`}`);

    gb.innerHTML = address + netUse + login + guide + mac + linux;

    gb.querySelectorAll("[data-copy]").forEach((b) =>
      b.addEventListener("click", () =>
        copyToClipboard(b.dataset.copy).then(
          () => window.notify?.("Kopiert", "success"),
          () => {
            const input = b.parentElement?.querySelector("input");
            if (input) { input.focus(); input.select(); }
            window.notify?.(tr("exp_copy_manual2"), "warning");
          })));
  }

  // Beim Aufklappen der Anleitung die Karten (einmalig) aufbauen.
  body.querySelector("#rm-guide")?.addEventListener("toggle", (e) => {
    if (e.target.open) renderGuide();
  });

  // Tenant/Standort-Namen für den Untertitel auflösen (falls vorhanden).
  function subtitle(c) {
    const parts = [];
    const tenants = state.hierarchy?.tenants || [];
    const locations = state.hierarchy?.locations || [];
    const t = tenants.find((x) => x.id === c.tenant_id);
    const l = locations.find((x) => x.id === c.location_id);
    if (t) parts.push(t.name);
    if (l) parts.push(l.name);
    if (c.ip) parts.push(c.ip);
    return parts.join(" · ");
  }

  function clientRow(c) {
    const isSel = selected.has(c.id);
    const dot = c.online ? "var(--online,#3ecf8e)" : "var(--subtext)";
    const sub = subtitle(c);
    return `
      <div class="rm-item" data-id="${esc(c.id)}"
           style="display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;cursor:pointer;margin-bottom:4px;
                  ${isSel ? "background:var(--accent,#38bdf8);color:#04222e" : "background:var(--panel-2,#0f1626)"}">
        <span style="color:${isSel ? "#04222e" : dot}">●</span>
        <div style="min-width:0;flex:1">
          <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.hostname || c.id)}</div>
          ${sub ? `<div style="font-size:11px;opacity:.8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(sub)}</div>` : ""}
        </div>
        ${isSel ? "<span>✓</span>" : ""}
      </div>`;
  }

  function draw() {
    const off = clients.filter((c) => !c.relay_enabled)
      .sort((a, b) => (a.hostname || "").localeCompare(b.hostname || ""));
    const on = clients.filter((c) => c.relay_enabled)
      .sort((a, b) => (a.hostname || "").localeCompare(b.hostname || ""));

    listOff.innerHTML = off.map(clientRow).join("") ||
      `<div style="color:var(--subtext);padding:10px;font-size:13px">Keine inaktiven Clients.</div>`;
    listOn.innerHTML = on.map(clientRow).join("") ||
      `<div style="color:var(--subtext);padding:10px;font-size:13px">Keine aktiven Clients.</div>`;
    countOff.textContent = `(${off.length})`;
    countOn.textContent = `(${on.length})`;

    body.querySelectorAll(".rm-item").forEach((el) =>
      el.addEventListener("click", () => {
        const id = el.dataset.id;
        if (selected.has(id)) selected.delete(id); else selected.add(id);
        draw();
      }));

    const n = selected.size;
    selInfo.textContent = n ? tr("rm_selected", { n }) : tr("u_nichts_ausgewahlt");
    if (isAdmin) switchBtn.disabled = n === 0;
  }

  async function load() {
    listOff.innerHTML = listOn.innerHTML = `<div style="color:var(--subtext);padding:10px">${tr("loading")}</div>`;
    try {
      clients = await api.getClients();
      // Auswahl auf noch existierende Clients begrenzen
      for (const id of [...selected]) if (!clients.find((c) => c.id === id)) selected.delete(id);
      draw();
    } catch (e) {
      listOff.innerHTML = `<div style="color:var(--danger);padding:10px">${esc(e.message)}</div>`;
    }
  }

  switchBtn.addEventListener("click", async () => {
    if (!isAdmin || selected.size === 0) return;
    const ids = [...selected];

    // Welche der ausgewählten Clients werden EINGESCHALTET (aktuell inaktiv)?
    // Nur dann ist das automatische Schließen relevant.
    const enabling = ids.filter((id) => {
      const c = clients.find((x) => x.id === id);
      return c && !c.relay_enabled;
    });
    let autoCloseMin = 0;
    if (enabling.length) {
      const choice = await uiChoice(
        tr("exp_relay_close_q"),
        relayOptionsFor(enabling),
        { description: enabling.length === 1 ? tr("rm_close_q_one") : tr("rm_close_q_many", { n: enabling.length }) });
      if (choice === null) return;   // abgebrochen -> nichts umschalten
      autoCloseMin = choice;
    }

    switchBtn.disabled = true;
    let ok = 0, fail = 0;
    for (const id of ids) {
      const c = clients.find((x) => x.id === id);
      const turningOn = c && !c.relay_enabled;
      try { await api.toggleRelay(id, turningOn ? autoCloseMin : 0); ok++; }
      catch { fail++; }
    }
    selected.clear();
    window.notify?.(`Umgeschaltet: ${ok}${fail ? `, Fehler: ${fail}` : ""}`, fail ? "warn" : "success");
    // andere offene Fenster (Client-Explorer-Relay-Tab) mitziehen
    window.dispatchEvent(new CustomEvent("relay-changed", { detail: { bulk: true } }));
    await load();
    try { state.clients = clients; } catch {}
  });

  // Externe Änderungen (Umschalten im Client-Explorer-Relay-Tab) übernehmen.
  function onRelayChanged() {
    if (!document.body.contains(body)) {
      window.removeEventListener("relay-changed", onRelayChanged);
      return;
    }
    load();
  }
  window.addEventListener("relay-changed", onRelayChanged);

  load();
}

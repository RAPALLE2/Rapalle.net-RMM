// apps/vpn.js
// -----------
// VPN-App: einen WireGuard-kompatiblen Tunnel auf einen Client ausstellen
// und alle offenen Tunnel im Blick behalten.
//
// Zwei Wege hinein:
//   * Startmenü -> "VPN"          : der Client wird hier ausgewählt
//   * Client-Panel -> Quick-Action: der Client steht schon fest (props.clientId)
//
// Wichtig für das Verständnis der Oberfläche: Die Tunnel-Datei wird GENAU
// EINMAL ausgegeben. Der private Schlüssel darin wird nirgends gespeichert -
// weder im Browser noch auf dem Server. Deshalb bietet die Übersicht auch
// keinen zweiten Download an, sondern nur "Schliessen".

import { api } from "../api.js";
import { esc, uiConfirm } from "../utils.js";
import { state, hasGlobalPerm, hasClientPerm } from "../state.js";
import { t } from "../i18n.js";
import { registerCleanup } from "../windowmanager.js";
import { dashboardSocket } from "../socket.js";

// Laufzeiten - bewusst dieselbe Staffelung wie beim Explorer-Relay, damit
// man sich nicht zwei unterschiedliche Listen merken muss.
const DURATIONS = [
  { label: "30 Minuten", value: 30 },
  { label: "1 Stunde", value: 60 },
  { label: "4 Stunden", value: 240 },
  { label: "8 Stunden", value: 480 },
  { label: "24 Stunden", value: 1440 },
  { label: "7 Tage", value: 10080 },
];
const UNLIMITED = { label: "Unbegrenzt (kein Ablauf)", value: 0 };

function mayUnlimited(clientId) {
  return hasGlobalPerm("vpn_unlimited")
    || (clientId && hasClientPerm(clientId, "c_vpn_unlimited"));
}

function fmtBytes(n) {
  n = Number(n) || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

function fmtRemaining(expiresAt, now) {
  if (!expiresAt) return "unbegrenzt";
  const ms = expiresAt - now;
  if (ms <= 0) return "abgelaufen";
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `noch ${mins} min`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `noch ${h} h ${mins % 60} min`;
  return `noch ${Math.floor(h / 24)} Tage`;
}

function download(filename, text) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.focus(); ta.select();
    try { document.execCommand("copy") ? resolve() : reject(new Error("Kopieren fehlgeschlagen")); }
    catch (e) { reject(e); }
    finally { ta.remove(); }
  });
}

export function renderVpn(body, win) {
  const preselected = win?.props?.clientId || null;
  let info = null;
  let nodeState = null;
  let tunnels = [];
  let now = Date.now();
  let selected = preselected;
  let busy = false;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div style="padding:12px 14px;border-bottom:1px solid var(--border)">
        <div style="font-size:15px;font-weight:700">🔐 VPN-Tunnel</div>
        <div style="color:var(--subtext);font-size:12px;margin-top:2px">
          Stellt eine Tunnel-Datei aus, die sich in jeden WireGuard-Client
          importieren lässt. Auf dem Gerät selbst wird nichts installiert.
        </div>
        <div id="vpn-server" style="font-size:12px;margin-top:8px;color:var(--subtext)"></div>
        <div id="vpn-check" style="font-size:12.5px;margin-top:8px"></div>
      </div>

      <div style="flex:1;display:grid;grid-template-columns:340px 1fr;min-height:0">
        <!-- Links: neuen Tunnel ausstellen -->
        <div style="display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--border);padding:12px;gap:10px;overflow:auto">
          <div style="font-weight:600;font-size:13px">Neuen Tunnel ausstellen</div>

          <label style="font-size:12px;color:var(--subtext)">Client
            <select id="vpn-client" style="width:100%;margin-top:4px"></select>
          </label>

          <label style="font-size:12px;color:var(--subtext)">Bezeichnung (optional)
            <input id="vpn-name" type="text" placeholder="z.B. Support-Zugang"
                   style="width:100%;margin-top:4px">
          </label>

          <label style="font-size:12px;color:var(--subtext)">Betriebsart
            <select id="vpn-mode" style="width:100%;margin-top:4px">
              <option value="client">Peer-to-Peer – nur dieses Gerät</option>
              <option value="site">Site-to-Site – ganzes Netz dahinter</option>
            </select>
            <div id="vpn-mode-hint" style="font-size:11px;margin-top:3px"></div>
          </label>

          <div id="vpn-node-box" style="font-size:11.5px;border:1px solid var(--border);
               border-radius:8px;padding:8px 10px;display:none"></div>

          <label style="font-size:12px;color:var(--subtext)">Gültig für
            <select id="vpn-minutes" style="width:100%;margin-top:4px"></select>
          </label>

          <label style="font-size:12px;color:var(--subtext)">Zusätzliche Netze (optional)
            <input id="vpn-routes" type="text" placeholder="192.168.10.0/24, 10.20.0.0/16"
                   style="width:100%;margin-top:4px">
            <div style="font-size:11px;margin-top:3px">
              Die Adresse des Clients ist immer enthalten. Hier lassen sich
              weitere Netze eintragen, die über diesen Client erreichbar sein
              sollen.
            </div>
          </label>

          <button id="vpn-create" class="btn-primary" style="margin-top:4px">
            🔐 Tunnel-Datei erstellen
          </button>
          <div id="vpn-hint" style="font-size:11.5px;color:var(--subtext)"></div>
        </div>

        <!-- Rechts: Übersicht -->
        <div style="display:flex;flex-direction:column;min-height:0">
          <div style="padding:9px 12px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--border)">
            <div style="font-weight:600;font-size:13px">Offene Tunnel
              <span id="vpn-count" style="color:var(--subtext);font-weight:400"></span>
            </div>
            <button id="vpn-refresh" class="taskbar-btn" style="margin-left:auto">⟳</button>
          </div>
          <div id="vpn-net" style="padding:8px 12px;border-bottom:1px solid var(--border)"></div>
          <div id="vpn-list" style="flex:1;overflow:auto;padding:8px"></div>
        </div>
      </div>
    </div>`;

  const $ = (id) => body.querySelector(id);
  const elClient = $("#vpn-client");
  const elMinutes = $("#vpn-minutes");
  const elList = $("#vpn-list");
  const elHint = $("#vpn-hint");

  // --- Client-Auswahl füllen -------------------------------------
  function fillClients() {
    const list = (state.clients || [])
      .filter((c) => hasClientPerm(c.id, "c_vpn"))
      .sort((a, b) => (a.hostname || "").localeCompare(b.hostname || ""));
    if (!list.length) {
      elClient.innerHTML = `<option value="">– kein Client mit VPN-Recht –</option>`;
      $("#vpn-create").disabled = true;
      return;
    }
    elClient.innerHTML = list.map((c) =>
      `<option value="${esc(c.id)}"${c.id === selected ? " selected" : ""}>
         ${c.online ? "🟢" : "⚪"} ${esc(c.hostname || c.id)}${c.ip ? ` (${esc(c.ip)})` : ""}
       </option>`).join("");
    if (!selected) selected = elClient.value;
    fillDurations();
  }

  function fillDurations() {
    const opts = mayUnlimited(selected) ? [...DURATIONS, UNLIMITED] : [...DURATIONS];
    elMinutes.innerHTML = opts.map((o, i) =>
      `<option value="${o.value}"${i === 1 ? " selected" : ""}>${esc(o.label)}</option>`).join("");
  }

  elClient.addEventListener("change", () => {
    selected = elClient.value;
    fillDurations();
    loadNodeState();
  });

  body.querySelector("#vpn-mode").addEventListener("change", updateModeHint);

  function updateModeHint() {
    const mode = body.querySelector("#vpn-mode").value;
    const alias = info?.loopback_alias || "10.77.0.1";
    const nets = (nodeState?.subnets || []);
    body.querySelector("#vpn-mode-hint").innerHTML = mode === "client"
      ? `<span style="color:var(--subtext)">Eine Punkt-zu-Punkt-Verbindung
         genau zu diesem Gerät – sonst nichts. Die Beschränkung wird auf der
         Gegenseite durchgesetzt, nicht in der Datei.<br>
         Dienste auf dem Gerät selbst erreichst du über
         <b style="color:var(--text)">${esc(alias)}</b> –
         <b>nicht</b> über <code>localhost</code>.</span>`
      : `<span style="color:var(--subtext)">Der Benutzer erreicht alles, was
         auch dieses Gerät erreicht.
         ${nets.length
           ? `Geroutet werden die tatsächlich gemeldeten Netze:
              <b style="color:var(--text)">${esc(nets.join(", "))}</b>.`
           : `<span style="color:var(--warn,#f5a524)">Dieses Gerät hat noch
              keine Netze gemeldet – dann wird ein /24 um seine Adresse
              angenommen. Nach einem Agent-Update stimmt es genau.</span>`}
         </span>`;
  }

  // Zustand der Node: davon hängt ab, ob der Tunnel direkt enden kann und
  // ob eine echte LAN-Adresse überhaupt anwählbar ist.
  async function loadNodeState() {
    const box = body.querySelector("#vpn-node-box");
    if (!selected) { box.style.display = "none"; return; }
    let st = null;
    try { st = await api.nodeState(selected); } catch { }
    nodeState = st;
    updateModeHint();
    if (!st || !st.is_node) {
      box.style.display = "";
      box.innerHTML = `<b>Kein Node-Gerät.</b> Der Tunnel endet im Backend
        (Relay-Betrieb) – die Nutzdaten laufen also über diesen Server.
        Über „Agent aktualisieren → Zu Node aufwerten“ endet der Tunnel
        direkt auf dem Gerät.`;
      return;
    }
    const direct = !!st.direct_possible;
    const l2 = !!(st.caps || {}).l2;
    box.style.display = "";
    box.innerHTML = `
      <div><b>Node</b> · ${direct
        ? `<span style="color:var(--online,#3ecf8e)">direkt erreichbar unter
           ${esc(st.endpoint)}</span> – der Tunnel endet auf dem Gerät, das
           Backend sieht keine Nutzdaten.`
        : `<span style="color:var(--warn,#f5a524)">von aussen nicht erreichbar</span>
           – der Tunnel läuft über das Backend.`}</div>
      <label style="display:flex;align-items:center;gap:6px;margin-top:7px">
        <input type="checkbox" id="vpn-l2" ${l2 && direct ? "" : "disabled"}>
        <span>Echte LAN-Adresse statt NAT</span>
      </label>
      <div style="margin-top:3px">${l2
        ? (direct ? "Die L2-Brücke dieser Node ist aktiv."
                  : "L2 setzt einen direkten Tunnel voraus.")
        : `Nicht verfügbar: ${esc((st.caps || {}).l2_reason || "Brücke nicht eingerichtet")}.
           Ohne sie läuft der Tunnel per NAT – erreichbar ist alles, nur mit
           der Adresse der Node als Absender.`}</div>
      ${l2 && direct ? `<input type="text" id="vpn-lan" placeholder="freie Adresse im LAN, z.B. 192.168.1.99"
             style="width:100%;margin-top:6px">` : ""}
      ${!l2 ? `<button class="taskbar-btn" id="vpn-setup-l2" style="margin-top:7px">
           🔧 L2-Brücke auf dieser Node einrichten …</button>` : ""}`;

    body.querySelector("#vpn-setup-l2")?.addEventListener("click",
      () => setupL2(selected, st));
  }

  // Einrichtung der L2-Brücke. Bewusst mit einer klaren Warnung davor:
  // Unter Windows wird dabei ein Netzwerktreiber installiert, unter Linux
  // greift der Agent auf Ethernet-Ebene zu. Beides ist ein Eingriff, den
  // niemand versehentlich auslösen soll.
  async function setupL2(clientId, st) {
    const win = /win/i.test(String(st?.platform || ""))
      || /win/i.test(String((state.clients || []).find((c) => c.id === clientId)?.platform || ""));
    const reason = (st?.caps || {}).l2_reason || "";
    const needsDriver = /npcap|treiber/i.test(reason) || win;

    const ok = await uiConfirm("L2-Brücke einrichten?", {
      description:
        (needsDriver
          ? "Auf diesem Gerät wird dafür der Netzwerktreiber Npcap "
            + "installiert (offizielle Quelle npcap.com, stille Installation). "
            + "Das ist ein Eingriff ins System des Kunden.\n\n"
          : "Der Agent greift dafür direkt auf Ethernet-Ebene zu und braucht "
            + "Root-Rechte.\n\n")
        + "Danach kann ein VPN-Benutzer eine echte Adresse aus dem LAN "
        + "bekommen, statt hinter der Adresse der Node zu stehen. Die Node "
        + "beantwortet dann ARP-Anfragen für diese Adresse.\n\n"
        + "Schlägt die Einrichtung fehl, passiert nichts Schlimmes: Die "
        + "Tunnel laufen weiter im NAT-Betrieb.",
      okText: needsDriver ? "Treiber installieren" : "Einrichten",
    });
    if (!ok) return;

    window.notify?.("L2-Brücke wird eingerichtet – das kann einige Minuten "
      + "dauern …", "info", 300000, { tag: "l2:" + clientId });
    try {
      const res = await api.nodeSetupL2(clientId, needsDriver, "");
      if (res.ok) {
        window.notify?.(`L2-Brücke aktiv auf ${res.interface || "dem Adapter"}`
          + (res.mac ? ` (${res.mac})` : ""), "success", 10000,
          { tag: "l2:" + clientId });
      } else {
        // Kein Fehler-Ton: Der NAT-Rückfall ist ein vorgesehener Zustand.
        window.notify?.(`L2 nicht möglich: ${res.reason}. `
          + "Die Tunnel laufen im NAT-Betrieb weiter.", "warning", 15000,
          { tag: "l2:" + clientId });
      }
    } catch (e) {
      window.notify?.("Einrichtung fehlgeschlagen: " + e.message, "error",
                      12000, { tag: "l2:" + clientId });
    }
    loadNodeState();
  }

  // --- Übersicht ---------------------------------------------------
  function renderList() {
    $("#vpn-count").textContent = tunnels.length ? `(${tunnels.length})` : "";
    if (!tunnels.length) {
      elList.innerHTML = `<div style="padding:24px;text-align:center;color:var(--subtext);font-size:13px">
        Zurzeit ist kein Tunnel offen.</div>`;
      return;
    }
    elList.innerHTML = tunnels.map((tn) => `
      <div style="border:1px solid var(--border);border-radius:9px;padding:10px 12px;margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-size:13px">${tn.connected ? "🟢" : "⚪"}</span>
          <b style="font-size:13.5px">${esc(tn.name || tn.hostname || "Tunnel")}</b>
          <span style="color:var(--subtext);font-size:12px">→ ${esc(tn.hostname || "")}</span>
          <span style="font-size:11px;padding:1px 6px;border-radius:6px;
                border:1px solid var(--border)" title="${tn.transport === "direct"
                  ? "Der Tunnel endet auf der Node – das Backend sieht keine Nutzdaten."
                  : "Der Tunnel endet im Backend."}">
            ${tn.transport === "direct" ? "direkt" : "über Backend"}</span>
          <span style="font-size:11px;padding:1px 6px;border-radius:6px;
                border:1px solid var(--border)">
            ${tn.mode === "site" ? "Site-to-Site" : "Peer-to-Peer"}${tn.l2 ? " · L2" : ""}</span>
          <span style="margin-left:auto;font-size:11.5px;color:var(--subtext)">
            ${esc(fmtRemaining(tn.expires_at, now))}
          </span>
          <button class="taskbar-btn vpn-revoke" data-id="${esc(tn.id)}"
                  title="Tunnel sofort schliessen">✖</button>
        </div>
        <div style="margin-top:6px;font-size:11.5px;color:var(--subtext);display:flex;gap:14px;flex-wrap:wrap">
          <span>Deine Adresse: <b>${esc(tn.address || "–")}</b></span>
          <span title="Dienste auf dem Gerät selbst – 'localhost' funktioniert nicht,
das zeigt immer auf den eigenen Rechner.">Gerät selbst:
            <b>${esc(info?.loopback_alias || "10.77.0.1")}</b></span>
          <span>Routen: ${esc(tn.allowed_ips || "–")}</span>
          <span>Verbindungen: ${tn.streams || 0}</span>
          <span>↓ ${fmtBytes(tn.rx_bytes)} / ↑ ${fmtBytes(tn.tx_bytes)}</span>
          ${tn.endpoint ? `<span>von ${esc(tn.endpoint)}</span>` : ""}
          <span>ausgestellt von ${esc(tn.username || "?")}</span>
        </div>
      </div>`).join("");

    elList.querySelectorAll(".vpn-revoke").forEach((b) =>
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          await api.vpnRevokeTunnel(b.dataset.id);
          window.notify?.("Tunnel geschlossen.", "success");
          await refresh();
        } catch (e) {
          window.notify?.("Schliessen fehlgeschlagen: " + e.message, "error");
          b.disabled = false;
        }
      }));
  }

  async function refresh() {
    try {
      const res = await api.vpnTunnels();
      tunnels = res.tunnels || [];
      now = res.now || Date.now();
      renderList();
    } catch (e) {
      elList.innerHTML = `<div style="padding:20px;color:var(--danger,#ff4d6d);font-size:12.5px">
        ${esc(e.message)}</div>`;
    }
  }

  // Übersicht des virtuellen Netzes. Sie beantwortet die Frage, die man
  // nach dem Verbinden als Erstes hat: Welche Geräte gibt es, und wie
  // erreiche ich sie?
  async function loadNetwork() {
    const box = $("#vpn-net");
    if (!box) return;
    try {
      const n = await api.vpnNetwork();
      const rows = (n.members || []).map((m) => `
        <tr>
          <td style="padding:1px 8px 1px 0">${m.kind === "client"
            ? (m.online ? "🟢" : "⚪") : "👤"}</td>
          <td style="padding:1px 10px 1px 0;font-family:ui-monospace,monospace">
            ${esc(m.address)}</td>
          <td style="padding:1px 10px 1px 0">${esc(m.hostname || m.label || "")}</td>
          <td style="padding:1px 0;color:var(--subtext)">${esc(m.fqdn || "")}</td>
        </tr>`).join("");
      box.innerHTML = `
        <details ${(n.members || []).length <= 8 ? "open" : ""}>
          <summary style="cursor:pointer;font-size:13px">
            <b>🌐 Virtuelles Netz</b>
            <span style="color:var(--subtext);font-weight:400">
              ${esc(n.subnet)} · Router ${esc(n.router)} · Zone .${esc(n.zone)}
              · ${n.clients} Geräte, ${n.users} Benutzer</span>
          </summary>
          <div style="color:var(--subtext);font-size:11.5px;margin:6px 0">
            Jedes Gerät hat eine feste Adresse. Mit einem Tunnel erreichst du
            alle – über die Adresse oder den Namen.
          </div>
          <table style="font-size:12px;border-collapse:collapse">${rows
            || `<tr><td style="color:var(--subtext)">Noch keine Mitglieder.</td></tr>`}</table>
        </details>`;
    } catch { box.innerHTML = ""; }
  }

  async function loadInfo() {
    try {
      info = await api.vpnInfo();
      const box = $("#vpn-server");
      if (!info.enabled) {
        box.innerHTML = `<b style="color:var(--warn,#f5a524)">VPN ist in den Einstellungen deaktiviert.</b>`;
      } else if (!info.running) {
        box.innerHTML = `<b style="color:var(--danger,#ff4d6d)">Der VPN-Endpunkt läuft nicht.</b>
          UDP-Port ${esc(String(info.port))} muss im Container und in der Firewall freigegeben sein.`;
      } else {
        box.innerHTML = info.endpoint_host
          ? `Endpunkt: <b>${esc(info.endpoint_host)}:${esc(String(info.port))}</b>
             <span style="opacity:.7">· Tunnel-Netz ${esc(info.subnet)}
             · Gerät selbst: ${esc(info.loopback_alias || "10.77.0.1")}</span>`
          : `<b style="color:var(--danger,#ff4d6d)">Die Server-Adresse ist nicht
             hinterlegt.</b> Ohne sie enthält die Tunnel-Datei kein Ziel und der
             Tunnel kommt nicht zustande. Einstellungen → Allgemein →
             Server-Adresse.`;
      if (!info.endpoint_host) $("#vpn-create").disabled = true;
      }
      renderCheck(info.check);
    } catch { /* Anzeige bleibt leer, Ausstellen meldet den Fehler dann klar */ }
  }

  // Die Endpunkt-Prüfung. Sie beantwortet die Frage, die man bei einem
  // nicht funktionierenden Tunnel als Erstes stellt - und die man ohne
  // Zahlen nicht beantworten kann: Kommt überhaupt etwas an?
  function renderCheck(check) {
    const box = $("#vpn-check");
    if (!check) { box.innerHTML = ""; return; }
    const look = {
      "verbunden":        ["🟢", "var(--online,#3ecf8e)"],
      "kein-handschlag":  ["🟠", "var(--warn,#f5a524)"],
      "nichts-empfangen": ["🔴", "var(--danger,#ff4d6d)"],
      "endpunkt-aus":     ["⚪", "var(--subtext)"],
    }[check.stage] || ["⚪", "var(--subtext)"];
    const st = check.stats || {};
    box.innerHTML = `
      <div style="border:1px solid ${look[1]}55;background:${look[1]}12;
           border-radius:8px;padding:9px 11px">
        <div><span style="font-size:14px">${look[0]}</span>
          <b>Endpunkt-Prüfung</b>
          <button class="taskbar-btn" id="vpn-check-refresh"
                  style="float:right;padding:1px 7px">⟳</button>
          <button class="taskbar-btn" id="vpn-selftest"
                  style="float:right;padding:1px 8px;margin-right:5px"
                  title="Prüft die WireGuard-Umsetzung gegen sich selbst und
wertet das letzte gescheiterte Handschlag-Paket aus.">🔬 Selbsttest</button></div>
        <div style="margin-top:5px;line-height:1.45">${esc(check.hint || "")}</div>
        <div style="margin-top:6px;color:var(--subtext);font-size:11.5px">
          Verbindungsversuche: <b>${st.initiations || 0}</b> ·
          Handschläge: <b>${st.handshakes || 0}</b> ·
          Daten: <b>${st.transport || 0}</b>
          ${st.probes ? ` · <span title="Prüf-Pakete der eigenen Nodes –
keine Verbindungsversuche von WireGuard-Clients.">Node-Prüfpakete:
            <b>${st.probes}</b></span>` : ""}
          ${st.errors ? ` · <span style="color:var(--danger,#ff4d6d)">Fehler:
            <b>${st.errors}</b> (${esc(st.last_error || "")})</span>` : ""}
          ${st.unknown_peer ? ` · unbekannter Schlüssel: <b>${st.unknown_peer}</b>` : ""}
          ${st.bad_mac ? ` · falscher Server-Schlüssel: <b>${st.bad_mac}</b>` : ""}
          ${st.junk ? ` · Datenmüll: <b>${st.junk}</b>` : ""}
          ${st.last_from ? ` · zuletzt von ${esc(st.last_from)}` : ""}
        </div>
      </div>`;
    box.querySelector("#vpn-check-refresh")?.addEventListener("click", loadInfo);
    box.querySelector("#vpn-selftest")?.addEventListener("click", runSelftest);
  }

  // Selbsttest: baut einen vollständigen Handschlag gegen die eigene
  // Umsetzung und spielt das letzte gescheiterte Paket eines echten
  // Clients Schritt für Schritt nach. Damit steht in einem Fenster, ob
  // das Problem im Server oder beim Client liegt.
  async function runSelftest() {
    const overlay = document.createElement("div");
    overlay.style.cssText = `position:fixed;inset:0;z-index:9600;
      background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center`;
    overlay.innerHTML = `<div style="background:var(--panel,#131c2b);color:var(--text);
      border:1px solid var(--border);border-radius:12px;width:min(760px,94vw);
      max-height:86vh;overflow:auto;padding:18px">
      <div style="font-size:15px;font-weight:700">🔬 VPN-Selbsttest</div>
      <div id="st-body" style="margin-top:12px;font-size:13px">läuft …</div>
      <div style="text-align:right;margin-top:14px">
        <button class="taskbar-btn" id="st-close">Schliessen</button></div></div>`;
    document.body.appendChild(overlay);
    overlay.querySelector("#st-close").addEventListener("click", () => overlay.remove());

    const rows = (steps) => (steps || []).map((s) => `
      <tr><td style="padding:2px 8px 2px 0">${s.ok ? "✅" : "❌"}</td>
      <td style="padding:2px 12px 2px 0">${esc(s.schritt)}</td>
      <td style="padding:2px 0;color:var(--subtext);font-family:ui-monospace,monospace;
          font-size:11.5px">${esc(s.detail || "")}</td></tr>`).join("");

    try {
      const r = await api.vpnSelftest();
      const p = r.gescheitertes_paket;
      overlay.querySelector("#st-body").innerHTML = `
        <div style="padding:9px 11px;border-radius:8px;
             background:${r.ok ? "#3ecf8e18" : "#ff4d6d18"};
             border:1px solid ${r.ok ? "#3ecf8e55" : "#ff4d6d55"}">
          <b>${r.ok ? "✅ Eigene Umsetzung in Ordnung" : "❌ Eigene Umsetzung fehlerhaft"}</b><br>
          <span style="color:var(--subtext)">${esc(r.meldung || "")}</span>
        </div>
        <table style="margin-top:10px;border-collapse:collapse">${rows(r.schritte)}</table>
        ${p ? `
          <div style="margin-top:16px;font-weight:600">Letztes gescheitertes Paket
            eines echten Clients <span style="font-weight:400;color:var(--subtext)">
            (von ${esc(p.von || "?")})</span></div>
          <table style="margin-top:6px;border-collapse:collapse">${rows(p.schritte)}</table>
          ${(p.schritte || []).some((s) => s.schritt.startsWith("Erschoepfende")
              && !s.ok)
            ? `<div style="margin-top:12px;padding:9px 11px;border-radius:8px;
                 background:#f5a52418;border:1px solid #f5a52455;font-size:12.5px">
               <b>Schlussfolgerung:</b> Keine Lesart des Pakets lässt sich mit
               dem privaten Schlüssel dieses Servers entschlüsseln. Das Paket
               wurde also für einen <b>anderen</b> Server verschlüsselt –
               obwohl mac1 zu diesem Server passt. Beides zusammen bedeutet:
               Es sind zwei verschiedene Server-Schlüssel im Umlauf. Meist
               stammt die Tunnel-Datei noch von vor einem Zurücksetzen der
               Datenbank. <b>Tunnel neu ausstellen und die alte .conf im
               WireGuard-Client löschen</b> (nicht nur deaktivieren – eine
               alte Verbindung sendet sonst weiter).</div>`
            : ""}
          <div style="margin-top:10px;color:var(--subtext);font-size:11.5px">
            Rohdaten des Pakets – enthält keine Geheimnisse, alles darin ist
            öffentlich oder verschlüsselt:</div>
          <textarea readonly style="width:100%;height:70px;margin-top:4px;font-size:10.5px;
            font-family:ui-monospace,monospace;background:var(--panel-2);color:var(--text);
            border:1px solid var(--border);border-radius:6px">${esc(p.hex || "")}</textarea>`
        : `<div style="margin-top:14px;color:var(--subtext)">
            Bisher ist kein Handschlag eines echten Clients gescheitert –
            es gibt also nichts nachzuspielen.</div>`}`;
    } catch (e) {
      overlay.querySelector("#st-body").innerHTML =
        `<span style="color:var(--danger,#ff4d6d)">${esc(e.message)}</span>`;
    }
  }

  // --- Tunnel ausstellen -------------------------------------------
  $("#vpn-create").addEventListener("click", async () => {
    if (busy) return;
    const clientId = elClient.value;
    if (!clientId) return;
    const minutes = parseInt(elMinutes.value, 10) || 0;
    const name = $("#vpn-name").value.trim();
    const routes = $("#vpn-routes").value.trim();
    const mode = $("#vpn-mode").value;
    const wantL2 = !!body.querySelector("#vpn-l2")?.checked;
    const lanAddress = (body.querySelector("#vpn-lan")?.value || "").trim();

    busy = true;
    $("#vpn-create").disabled = true;
    // Mitlaufende Anzeige. Bleibt der Server stumm, sieht man wenigstens,
    // dass gewartet wird und wie lange - statt eines Textes, der sich nie
    // mehr ändert.
    const startedAt = Date.now();
    elHint.textContent = "Tunnel wird ausgestellt …";
    const ticker = setInterval(() => {
      const s = Math.round((Date.now() - startedAt) / 1000);
      if (s >= 5) elHint.textContent = `Tunnel wird ausgestellt … (${s}s)`;
    }, 1000);
    try {
      const rec = await api.vpnCreateTunnel(clientId, minutes, name, routes,
        { mode, wantL2, lanAddress });
      elHint.textContent = "";
      showConfig(rec);
      await refresh();
    } catch (e) {
      elHint.innerHTML = `<span style="color:var(--danger,#ff4d6d)">${esc(e.message)}</span>`;
    } finally {
      clearInterval(ticker);
      busy = false;
      $("#vpn-create").disabled = false;
    }
  });

  // Das Ergebnis-Fenster. Es macht sehr deutlich, dass es die Datei nur
  // einmal gibt - sonst sucht man sie später vergeblich in der Übersicht.
  function showConfig(rec) {
    const overlay = document.createElement("div");
    overlay.style.cssText = `position:fixed;inset:0;z-index:9600;background:rgba(0,0,0,0.55);
      display:flex;align-items:center;justify-content:center`;
    overlay.innerHTML = `
      <div style="background:var(--panel,#131c2b);color:var(--text,#e8eef7);border:1px solid var(--border,#2a3648);
        border-radius:12px;width:min(620px,92vw);max-height:86vh;display:flex;flex-direction:column;
        padding:18px;box-shadow:0 16px 48px rgba(0,0,0,.5)">
        <div style="font-size:15px;font-weight:700">🔐 Tunnel-Datei ${esc(rec.filename || "tunnel.conf")}</div>
        <div style="font-size:12px;color:var(--warn,#f5a524);margin-top:6px">
          Diese Datei wird nur EINMAL angezeigt. Der private Schlüssel wird
          nirgends gespeichert – jetzt herunterladen oder kopieren.
        </div>
        <div style="font-size:12.5px;margin-top:10px;padding:9px 11px;border-radius:8px;
             background:var(--panel-2,#0e1520);border:1px solid var(--border,#2a3648)">
          <b>So verbindest du dich:</b><br>
          <span style="color:var(--subtext)">Dienste auf dem Gerät selbst
          (VNC, RDP, Weboberflächen) – statt <code>localhost</code>:</span><br>
          <code style="font-size:13px;color:var(--text)">${esc(rec.loopback_alias || "10.77.0.1")}:PORT</code>
          <span style="color:var(--subtext)"> · z.B. VNC:
          <code>${esc(rec.loopback_alias || "10.77.0.1")}:5900</code></span>
          ${rec.mode === "site" ? `<br><span style="color:var(--subtext)">
            Andere Geräte im Netz: über ihre normale Adresse.</span>` : ""}
        </div>
        <textarea readonly style="flex:1;min-height:240px;margin-top:12px;font-family:ui-monospace,monospace;
          font-size:12px;resize:vertical;background:var(--panel-2,#0e1520);color:var(--text,#e8eef7);
          border:1px solid var(--border,#2a3648);border-radius:8px;padding:10px">${esc(rec.config || "")}</textarea>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
          <button class="taskbar-btn vpn-copy">Kopieren</button>
          <button class="btn-primary vpn-dl" style="margin:0">Herunterladen</button>
          <button class="taskbar-btn vpn-close">Schliessen</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector(".vpn-dl").addEventListener("click", () =>
      download(rec.filename || "tunnel.conf", rec.config || ""));
    overlay.querySelector(".vpn-copy").addEventListener("click", () =>
      copyToClipboard(rec.config || "")
        .then(() => window.notify?.("In die Zwischenablage kopiert.", "success"))
        .catch(() => window.notify?.("Kopieren fehlgeschlagen.", "error")));
    overlay.querySelector(".vpn-close").addEventListener("click", () => overlay.remove());
  }

  $("#vpn-refresh").addEventListener("click", () => { refresh(); loadNetwork(); });

  // Live-Aktualisierung: Das Backend meldet Änderungen über den
  // Dashboard-Kanal - auch das automatische Schliessen abgelaufener Tunnel.
  // Der Timer ist nur der Rückfall, falls kein Ereignis durchkommt.
  const onChanged = () => refresh();
  dashboardSocket.on("vpn-changed", onChanged);
  const timer = setInterval(refresh, 10000);
  registerCleanup(win.key, () => {
    dashboardSocket.off("vpn-changed", onChanged);
    clearInterval(timer);
  });

  fillClients();
  loadNetwork();
  updateModeHint();
  loadNodeState();
  loadInfo();
  refresh();
}

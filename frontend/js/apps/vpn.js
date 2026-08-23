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
              <option value="client">Nur dieses Gerät (z.B. localhost:80)</option>
              <option value="site">Ganzes Netz dahinter (Site-to-Site)</option>
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
    body.querySelector("#vpn-mode-hint").innerHTML = mode === "client"
      ? `<span style="color:var(--subtext)">Der Tunnel lässt ausschliesslich
         Verbindungen zu diesem Gerät zu. Die Beschränkung wird auf der
         Gegenseite durchgesetzt, nicht in der Datei.</span>`
      : `<span style="color:var(--subtext)">Der Benutzer erreicht alles, was
         auch dieses Gerät erreicht.</span>`;
  }

  // Zustand der Node: davon hängt ab, ob der Tunnel direkt enden kann und
  // ob eine echte LAN-Adresse überhaupt anwählbar ist.
  async function loadNodeState() {
    const box = body.querySelector("#vpn-node-box");
    if (!selected) { box.style.display = "none"; return; }
    let st = null;
    try { st = await api.nodeState(selected); } catch { }
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
            ${tn.mode === "site" ? "ganzes Netz" : "nur Gerät"}${tn.l2 ? " · L2" : ""}</span>
          <span style="margin-left:auto;font-size:11.5px;color:var(--subtext)">
            ${esc(fmtRemaining(tn.expires_at, now))}
          </span>
          <button class="taskbar-btn vpn-revoke" data-id="${esc(tn.id)}"
                  title="Tunnel sofort schliessen">✖</button>
        </div>
        <div style="margin-top:6px;font-size:11.5px;color:var(--subtext);display:flex;gap:14px;flex-wrap:wrap">
          <span>Adresse: <b>${esc(tn.address || "–")}</b></span>
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
        box.innerHTML = `Endpunkt: <b>${esc(info.endpoint_host || "– Server-Adresse in den Einstellungen setzen –")}:${esc(String(info.port))}</b>
          <span style="opacity:.7">· Tunnel-Netz ${esc(info.subnet)}</span>`;
      }
    } catch { /* Anzeige bleibt leer, Ausstellen meldet den Fehler dann klar */ }
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

  $("#vpn-refresh").addEventListener("click", refresh);

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
  updateModeHint();
  loadNodeState();
  loadInfo();
  refresh();
}

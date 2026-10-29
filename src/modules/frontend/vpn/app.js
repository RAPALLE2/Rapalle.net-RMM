// app.js (Modul "vpn") - WireGuard-Tunnel verwalten
// --------------------------------------------------
// Ansichten IM Fenster (keine Popups): Liste -> Neuer Tunnel -> Details.
// Backend: /api/vpn/* (vpn_routes.py). Host = Backend oder Node mit aktivem Node-Modul "vpn".
import { getToken } from "/js/app/api.js";
import { esc, uiConfirm } from "/js/app/utils.js";
import { registerCleanup } from "/js/app/windowmanager.js";

async function vq(path, opts = {}) {
  const res = await fetch("/api/vpn" + path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + (getToken() || "") },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (opts.raw) { if (!res.ok) throw new Error(`Fehler ${res.status}`); return res; }
  const d = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(d.detail || `Fehler ${res.status}`);
  return d;
}

const DUR = [[1, "1 Stunde"], [4, "4 Stunden"], [8, "8 Stunden"], [24, "1 Tag"], [72, "3 Tage"], [168, "7 Tage"], [720, "30 Tage"]];
const KIND = { user: "👤 Benutzer", agent: "🖥 Agent", backend: "🗄 Backend" };
const fmtExp = (ts) => {
  if (!ts) return "unbegrenzt";
  const s = ts - Date.now() / 1000;
  if (s <= 0) return "abgelaufen";
  if (s < 3600) return `noch ${Math.ceil(s / 60)} min`;
  if (s < 172800) return `noch ${Math.round(s / 3600)} h`;
  return `noch ${Math.round(s / 86400)} Tage`;
};
const bytes = (n) => n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : n > 1e3 ? Math.round(n / 1e3) + " kB" : (n || 0) + " B";

async function download(tid, pid, name) {
  const res = await vq(`/tunnels/${tid}/peers/${pid}/config`, { raw: true });
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: name + ".conf" });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function downloadScript() {
  try {
    const res = await vq("/setup-script", { raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "vpn-host-setup.sh" });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    window.notify?.("Auf dem Host als root: sh vpn-host-setup.sh  ·  Proxmox-Host für einen CT: sh vpn-host-setup.sh --pve-ct <CTID>  ·  Docker-Host: --docker", "info", 15000);
  } catch (e) { window.notify?.(e.message, "error"); }
}

export function renderVpn(body, win) {
  body.innerHTML = `<div data-root style="height:100%;overflow:auto;box-sizing:border-box;padding:16px 20px 22px;font-size:13px"></div>`;
  const root = body.querySelector("[data-root]");
  let timer = null;
  const stop = () => { clearInterval(timer); timer = null; };
  if (win?.key) registerCleanup(win.key, stop);

  // ------------------------------------------------------------ Liste
  async function list() {
    stop();
    root.innerHTML = "Lade …";
    let d;
    try { d = await vq("/tunnels"); } catch (e) { root.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; return; }
    root.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <h2 style="margin:0;flex:1;font-size:20px">🛡️ VPN-Tunnel</h2>
        ${d.can_create ? `<button class="btn btn-sm" data-script title="Skript für Linux-Host, Proxmox-CT oder Docker-Host">⬇ Host-Einrichtungsskript</button>
          <button class="btn btn-primary" data-new>＋ Neuer Tunnel</button>` : ""}
      </div>
      <p style="color:var(--subtext);margin:0 0 14px">WireGuard-Tunnel mit Benutzern, Agents, Nodes und dem Backend. Host ist das Backend oder eine Node.
        Eigene Konfiguration hier herunterladen (oder per QR-Code ins Handy) – die Client-App richtet Tunnel automatisch ein und entfernt sie bei Ablauf.</p>
      ${d.tunnels.length ? d.tunnels.map((t) => `
        <div data-open="${esc(t.id)}" style="border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px;cursor:pointer;background:var(--panel-2);opacity:${t.state === "expired" ? .6 : 1}">
          <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
            <b style="font-size:14px;flex:1">${esc(t.name)}</b>
            <span style="font-size:12px;color:${t.state === "active" ? "#3ecf8e" : t.state === "error" ? "var(--danger)" : "var(--subtext)"}">${{ active: "aktiv", error: "Fehler", expired: "beendet", pending: "wird eingerichtet" }[t.state] || t.state}</span>
            <span style="font-size:12px;color:var(--subtext)">${t.state === "expired" ? "" : fmtExp(t.expires)}</span>
          </div>
          <div style="font-size:12px;color:var(--subtext);margin-top:3px">Host: ${esc(t.host_name)} · ${t.mode === "real" ? `reelles Netz ${esc(t.lan?.cidr || "")} (${t.ip_mode === "own" ? "eigene IPs" : "IP des Hosts"})` : `virtuelles Netz ${esc(t.subnet)}`}
            · ${t.peers.length} Teilnehmer${t.can_manage ? "" : " (du bist Teilnehmer)"}${t.error ? ` · <span style="color:var(--danger)">${esc(t.error)}</span>` : ""}
            ${(t.warnings || []).length && t.state === "active" ? ` · <span style="color:#f5a524">⚠ ${t.warnings.length} Hinweis(e)</span>` : ""}</div>
        </div>`).join("") : `<div style="color:var(--subtext)">Noch keine Tunnel.</div>`}`;
    root.querySelector("[data-new]")?.addEventListener("click", () => create(d));
    root.querySelector("[data-script]")?.addEventListener("click", downloadScript);
    root.querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => detail(el.dataset.open)));
  }

  // ------------------------------------------------------------ Neuer Tunnel
  async function create(meta) {
    stop();
    root.innerHTML = "Lade …";
    let hosts, cand;
    try { [hosts, cand] = await Promise.all([vq("/hosts"), vq("/candidates")]); }
    catch (e) { root.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; return; }
    const durs = DUR.filter(([h]) => meta.unlimited || h <= meta.max_hours);
    root.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px"><button class="btn btn-sm" data-back>← Zurück</button><h2 style="margin:0;font-size:18px">Neuer Tunnel</h2></div>
      <div style="display:grid;grid-template-columns:170px 1fr;gap:9px 12px;align-items:center;max-width:820px">
        <label>Name</label><input data-f="name" placeholder="z. B. Wartung Kunde Nord">
        <label>Host</label><select data-f="host">${hosts.hosts.map((h) => `<option value="${esc(h.id)}" ${h.available ? "" : "disabled"} data-ip="${esc(h.ip || "")}">${h.id === "backend" ? "🗄" : "🖧"} ${esc(h.name)}${h.available ? "" : ` (${esc(h.hint)})`}</option>`).join("")}</select>
        <label>Endpunkt (öffentlich)</label><input data-f="endpoint" placeholder="leer = automatisch (Adresse des Hosts:Port)">
        <label>Netz</label><div style="display:flex;gap:14px;flex-wrap:wrap">
          <label style="display:flex;gap:6px;align-items:center"><input type="radio" name="vm" value="virtual" checked> Virtuelles Netz</label>
          <label style="display:flex;gap:6px;align-items:center"><input type="radio" name="vm" value="real"> Reelles Netz des Hosts</label></div>
        <label data-virt>Virtuelles Netz</label><input data-virt data-f="subnet" placeholder="leer = automatisch (z. B. 10.77.3.0/24)">
        <label data-real>Schnittstelle / Netz</label><div data-real style="display:flex;gap:8px"><input data-f="iface" placeholder="eth0" style="width:110px"><input data-f="cidr" placeholder="192.168.1.0/24" style="flex:1"></div>
        <label data-real>Adressen</label><div data-real style="display:flex;gap:14px;flex-wrap:wrap">
          <label style="display:flex;gap:6px;align-items:center"><input type="radio" name="im" value="nat" checked> Alle mit der IP des Hosts (NAT)</label>
          <label style="display:flex;gap:6px;align-items:center"><input type="radio" name="im" value="own"> Jeder eine eigene IP im Netz</label></div>
        <label data-own>IP-Bereich</label><div data-own style="display:flex;gap:8px;align-items:center"><input data-f="pool_start" placeholder="192.168.1.200" style="flex:1"> bis <input data-f="pool_end" placeholder="192.168.1.220" style="flex:1"></div>
        <label>DNS (optional)</label><input data-f="dns" placeholder="z. B. 192.168.1.1">
        <label>Laufzeit</label><select data-f="hours">${durs.map(([h, l]) => `<option value="${h}" ${h === 8 ? "selected" : ""}>${l}</option>`).join("")}${meta.unlimited ? `<option value="0">unbegrenzt</option>` : ""}</select>
        <label>Client-App</label><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" data-f="auto" checked> Benutzer-Tunnel automatisch einrichten, verbinden und bei Ablauf löschen</label>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px;max-width:820px">
        <div><b>Benutzer</b><input data-filter-u placeholder="filtern" style="width:100%;margin:6px 0;box-sizing:border-box">
          <div data-users style="max-height:220px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:6px 8px"></div></div>
        <div><b>Agents &amp; Nodes</b><input data-filter-a placeholder="filtern" style="width:100%;margin:6px 0;box-sizing:border-box">
          <div data-agents style="max-height:220px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:6px 8px"></div>
          ${cand.backend ? `<label style="display:flex;gap:6px;align-items:center;margin-top:8px"><input type="checkbox" data-f="backend"> 🗄 Backend als Teilnehmer (wenn eine Node Host ist)</label>` : ""}</div>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;max-width:820px;margin-top:16px"><button class="btn" data-back2>Abbrechen</button><button class="btn btn-primary" data-ok>Tunnel anlegen</button></div>
      <div data-msg style="margin-top:8px;max-width:820px"></div>`;
    const $ = (s) => root.querySelector(s);
    const sel = { u: new Set(), a: new Set() };
    const drawList = (box, items, set, f, icon) => {
      box.innerHTML = items.filter((x) => set.has(x.id) || !f || x.name.toLowerCase().includes(f)).map((x) =>
        `<label style="display:flex;gap:6px;align-items:center;padding:2px 0"><input type="checkbox" value="${esc(x.id)}" ${set.has(x.id) ? "checked" : ""}> ${icon(x)} ${esc(x.name)}</label>`).join("")
        || `<span style="color:var(--subtext)">keine</span>`;
      box.querySelectorAll("input").forEach((c) => c.addEventListener("change", () => c.checked ? set.add(c.value) : set.delete(c.value)));
    };
    const du = () => drawList($("[data-users]"), cand.users, sel.u, $("[data-filter-u]").value.toLowerCase(), () => "👤");
    const da = () => drawList($("[data-agents]"), cand.agents, sel.a, $("[data-filter-a]").value.toLowerCase(), (x) => x.node ? "🖧" : "🖥");
    $("[data-filter-u]").addEventListener("input", du); $("[data-filter-a]").addEventListener("input", da); du(); da();
    const vis = () => {
      const real = root.querySelector('input[name="vm"]:checked').value === "real";
      const own = root.querySelector('input[name="im"]:checked').value === "own";
      root.querySelectorAll("[data-virt]").forEach((e) => e.style.display = real ? "none" : "");
      root.querySelectorAll("[data-real]").forEach((e) => e.style.display = real ? "" : "none");
      root.querySelectorAll("[data-own]").forEach((e) => e.style.display = real && own ? "" : "none");
    };
    root.querySelectorAll('input[name="vm"],input[name="im"]').forEach((r) => r.addEventListener("change", vis)); vis();
    $("[data-back]").onclick = list; $("[data-back2]").onclick = list;
    $("[data-ok]").onclick = async () => {
      const v = (k) => ($(`[data-f="${k}"]`)?.value || "").trim();
      const payload = {
        name: v("name"), host: v("host"), endpoint: v("endpoint"),
        mode: root.querySelector('input[name="vm"]:checked').value, ip_mode: root.querySelector('input[name="im"]:checked').value,
        subnet: v("subnet"), lan: { iface: v("iface"), cidr: v("cidr"), pool_start: v("pool_start"), pool_end: v("pool_end") },
        dns: v("dns"), hours: Number(v("hours")), auto: $('[data-f="auto"]').checked,
        members: { users: [...sel.u], agents: [...sel.a], backend: !!$('[data-f="backend"]')?.checked },
      };
      $("[data-ok]").disabled = true; $("[data-msg]").textContent = "⏳ Tunnel wird eingerichtet …";
      try {
        const r = await vq("/tunnels", { method: "POST", body: payload });
        if (!r.ok) window.notify?.(`Tunnel angelegt, Host meldet: ${r.error}`, "warn", 12000);
        else if ((r.tunnel.warnings || []).length) window.notify?.("Tunnel aktiv – mit Hinweisen (siehe Details)", "warn", 8000);
        else window.notify?.("Tunnel aktiv", "success", 3000);
        detail(r.tunnel.id);
      } catch (e) { $("[data-msg]").innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; $("[data-ok]").disabled = false; }
    };
  }

  // ------------------------------------------------------------ Details
  async function detail(tid) {
    stop();
    let t;
    try { t = (await vq("/tunnels")).tunnels.find((x) => x.id === tid); } catch (e) { root.textContent = e.message; return; }
    if (!t) return list();
    const m = t.can_manage, ended = t.state === "expired";
    root.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">
        <button class="btn btn-sm" data-back>← Alle Tunnel</button><h2 style="margin:0;font-size:18px;flex:1">${esc(t.name)}</h2>
        ${m && !ended ? `<select data-ext>${DUR.map(([h, l]) => `<option value="${h}">${l}</option>`).join("")}<option value="0">unbegrenzt</option></select>
          <button class="btn btn-sm" data-extend>Laufzeit ab jetzt setzen</button>
          ${t.state === "error" || (t.warnings || []).length ? `<button class="btn btn-sm" data-retry>↻ Erneut anwenden</button>` : ""}
          <button class="btn btn-sm btn-danger" data-expire>Verfallen lassen</button>` : ""}
        ${m ? `<button class="btn btn-sm btn-danger" data-del>Löschen</button>` : ""}
      </div>
      <div style="display:grid;grid-template-columns:150px 1fr;gap:4px 12px;margin-bottom:14px;font-size:12.5px">
        <span style="color:var(--subtext)">Zustand</span><span data-state>${esc(t.state)}${t.error ? ` – <span style="color:var(--danger)">${esc(t.error)}</span>` : ""}</span>
        <span style="color:var(--subtext)">Host</span><span>${esc(t.host_name)} · Schnittstelle <code>${esc(t.iface)}</code> · UDP-Port <code>${t.port}</code></span>
        <span style="color:var(--subtext)">Endpunkt</span><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${m && !ended ? `<input data-ep value="${esc(t.endpoint)}" style="min-width:240px"><button class="btn btn-sm" data-ep-save>Speichern</button>`
                        : `<code>${esc(t.endpoint)}</code>`}
          <span style="font-size:11.5px;color:var(--subtext)">Öffentliche Adresse:Port, unter der Teilnehmer den Host erreichen</span></span>
        <span style="color:var(--subtext)">Netz</span><span>${t.mode === "real" ? `reelles Netz ${esc(t.lan?.cidr || "")} über ${esc(t.lan?.iface || "")} – ${t.ip_mode === "own" ? `eigene IPs ${esc(t.lan?.pool_start)}–${esc(t.lan?.pool_end)}` : `NAT (IP des Hosts), intern ${esc(t.subnet)}`}` : `virtuell ${esc(t.subnet)} (Host ${esc(t.host_ip)})`}</span>
        <span style="color:var(--subtext)">Laufzeit</span><span>${ended ? "beendet" : fmtExp(t.expires)}</span>
      </div>
      ${(t.endpoint_warnings || []).length && !ended ? `<div style="border:1px solid var(--danger);border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12.5px">
        <b style="color:var(--danger)">Teilnehmer werden den Host so wahrscheinlich nicht erreichen:</b><ul style="margin:4px 0 0 18px;padding:0">${t.endpoint_warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
      ${(t.warnings || []).length && !ended ? `<div style="border:1px solid #f5a524;border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12.5px">
        <b style="color:#f5a524">Tunnel läuft, aber:</b><ul style="margin:4px 0 0 18px;padding:0">${t.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
      ${m && !ended ? `<div style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:14px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <b style="margin-right:4px">Endpunkt testen</b>
          <select data-tester style="min-width:220px"><option value="">Tester wird geladen …</option></select>
          <button class="btn btn-sm btn-primary" data-test>▶ Testen</button>
          <span style="flex:1"></span>
          <button class="btn btn-sm" data-tools title="wireguard-tools, iptables, iproute2 (ggf. wireguard-go) auf dem Host nachinstallieren">🔧 Werkzeuge auf dem Host installieren</button>
          <button class="btn btn-sm" data-script2>⬇ Einrichtungsskript</button>
        </div>
        <div style="font-size:11.5px;color:var(--subtext);margin-top:4px">Ein Tester (Agent/Node oder das Backend) baut einen echten WireGuard-Handshake zu <code>${esc(t.endpoint)}</code> auf –
          so wird geprüft, ob IP <b>und</b> UDP-Port erreichbar sind. Aussagekräftig nur von <b>außerhalb</b> des Host-Netzes.</div>
        <div data-testres style="margin-top:8px"></div></div>` : ""}
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px"><b style="flex:1">Teilnehmer (${t.peers.length})</b>
        ${m && !ended ? `<button class="btn btn-sm btn-primary" data-add>＋ Hinzufügen</button>` : ""}</div>
      <div data-addbox></div>
      <table class="data-table" style="width:100%;font-size:12.5px"><thead><tr><th style="text-align:left">Name</th><th style="text-align:left">Art</th><th style="text-align:left">IP</th><th style="text-align:left">Verbindung</th><th style="text-align:left">Ablauf</th><th></th></tr></thead>
        <tbody>${t.peers.map((p) => `<tr data-p="${esc(p.id)}">
          <td><b>${esc(p.name)}</b>${p.mine ? " (du)" : ""}</td><td>${KIND[p.kind] || p.kind}</td><td><code>${esc(p.ip)}</code></td>
          <td data-conn>${p.join_ok === false ? `<span style="color:var(--danger)" title="${esc(p.join_error)}">⚠ ${esc(p.join_error.slice(0, 60))}</span>` : "…"}</td>
          <td>${p.expires ? fmtExp(p.expires) : "wie Tunnel"}</td>
          <td style="text-align:right;white-space:nowrap">${!ended && (p.mine || m) && p.kind === "user" ? `<button class="btn-tool" data-dl="${esc(p.id)}" title="WireGuard-Datei herunterladen">⬇</button><button class="btn-tool" data-qr="${esc(p.id)}" title="QR-Code für die WireGuard-App">▦</button>` : ""}
            ${!ended && !p.mine ? `<button class="btn-tool" data-ping="${esc(p.id)}" title="Vom Host aus anpingen (Diagnose)">📶</button>` : ""}
            ${m || p.mine ? `<button class="btn-tool" data-rm="${esc(p.id)}" title="Aus dem Tunnel entfernen">✕</button>` : ""}</td></tr>`).join("")
          || `<tr><td colspan="6" style="color:var(--subtext)">Noch keine Teilnehmer.</td></tr>`}</tbody></table>
      <div data-qrbox style="margin-top:12px"></div>`;
    const $ = (s) => root.querySelector(s);
    $("[data-back]").onclick = list;
    $("[data-extend]")?.addEventListener("click", async () => {
      try { await vq(`/tunnels/${tid}`, { method: "PUT", body: { hours: Number($("[data-ext]").value) } }); detail(tid); }
      catch (e) { window.notify?.(e.message, "error", 6000); }
    });
    $("[data-script2]")?.addEventListener("click", downloadScript);
    if ($("[data-tester]")) {
      vq(`/tunnels/${tid}/testers`).then((r) => {
        const sel = $("[data-tester]");
        sel.innerHTML = r.testers.length ? r.testers.map((x) => `<option value="${esc(x.id)}">${x.id === "backend" ? "🗄" : "🖥"} ${esc(x.name)}${x.same_net ? " (gleiches Netz wie der Host – wenig aussagekräftig)" : ""}</option>`).join("")
          : `<option value="">kein Tester online</option>`;
      }).catch((e) => { $("[data-tester]").innerHTML = `<option value="">${esc(e.message)}</option>`; });
    }
    $("[data-test]")?.addEventListener("click", async () => {
      const via = $("[data-tester]").value, box = $("[data-testres]");
      if (!via) return;
      $("[data-test]").disabled = true;
      box.innerHTML = `<span style="color:var(--subtext)">⏳ Test läuft (bis 30 s) …</span>`;
      try {
        const r = await vq(`/tunnels/${tid}/test`, { method: "POST", body: { via } });
        box.innerHTML = `<div style="border:1px solid ${r.ok ? "#3ecf8e" : "var(--danger)"};border-radius:8px;padding:8px 12px">
          <b style="color:${r.ok ? "#3ecf8e" : "var(--danger)"}">${r.ok ? "✔ Erreichbar" : "✘ Nicht erreichbar"}</b> – ${esc(r.detail || "")}
          ${(r.steps || []).length ? `<div style="font-size:11.5px;color:var(--subtext);margin-top:4px">${r.steps.map(esc).join(" → ")}</div>` : ""}
          ${(r.endpoint_warnings || []).map((w) => `<div style="font-size:12px;color:#f5a524;margin-top:4px">⚠ ${esc(w)}</div>`).join("")}</div>`;
      } catch (e) { box.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; }
      $("[data-test]").disabled = false;
    });
    $("[data-tools]")?.addEventListener("click", async () => {
      const b = $("[data-tools]"); b.disabled = true; b.textContent = "⏳ installiere …";
      try {
        const r = await vq(`/hosts/${encodeURIComponent(t.host)}/tools`, { method: "POST" });
        const miss = Object.entries(r.tools || {}).filter(([, v]) => !v).map(([k]) => k);
        window.notify?.(r.ok !== false && !miss.length ? "Werkzeuge vorhanden – Tunnel wird neu angewendet"
          : `Werkzeuge: ${miss.join(", ") || r.error || r.hint || "teilweise"} fehlen noch – ggf. Einrichtungsskript benutzen`, miss.length ? "warn" : "success", 10000);
        await vq(`/tunnels/${tid}/retry`, { method: "POST" }).catch(() => {});
        detail(tid);
      } catch (e) { window.notify?.(e.message, "error", 8000); b.disabled = false; b.textContent = "🔧 Werkzeuge auf dem Host installieren"; }
    });
    $("[data-ep-save]")?.addEventListener("click", async () => {
      try {
        const r = await vq(`/tunnels/${tid}`, { method: "PUT", body: { endpoint: $("[data-ep]").value.trim() } });
        window.notify?.("Endpunkt gespeichert – Agents werden neu verbunden. Benutzer: Datei neu laden (die Client-App macht das selbst).", "success", 9000);
        detail(tid);
      } catch (e) { window.notify?.(e.message, "error", 6000); }
    });
    $("[data-retry]")?.addEventListener("click", async () => {
      try { const r = await vq(`/tunnels/${tid}/retry`, { method: "POST" }); window.notify?.(r.ok ? "Angewendet" : r.error, r.ok ? "success" : "error", 8000); detail(tid); }
      catch (e) { window.notify?.(e.message, "error", 6000); }
    });
    $("[data-expire]")?.addEventListener("click", async () => {
      if (!(await uiConfirm("Tunnel jetzt beenden? Alle Teilnehmer werden getrennt.", { okText: "Beenden", danger: true }))) return;
      await vq(`/tunnels/${tid}/expire`, { method: "POST" }).catch((e) => window.notify?.(e.message, "error")); detail(tid);
    });
    $("[data-del]")?.addEventListener("click", async () => {
      if (!(await uiConfirm("Tunnel löschen?", { okText: "Löschen", danger: true }))) return;
      await vq(`/tunnels/${tid}`, { method: "DELETE" }).catch((e) => window.notify?.(e.message, "error")); list();
    });
    root.querySelectorAll("[data-dl]").forEach((b) => b.onclick = () => download(tid, b.dataset.dl, t.peer_iface).catch((e) => window.notify?.(e.message, "error")));
    root.querySelectorAll("[data-qr]").forEach((b) => b.onclick = async () => {
      try {
        const res = await vq(`/tunnels/${tid}/peers/${b.dataset.qr}/config?qr=1`, { raw: true });
        const url = URL.createObjectURL(await res.blob());
        $("[data-qrbox]").innerHTML = `<div style="display:inline-block;background:#fff;padding:10px;border-radius:8px"><img src="${url}" style="width:240px;height:240px;display:block"></div>
          <div style="font-size:12px;color:var(--subtext);margin-top:4px">In der WireGuard-App: „+“ → „QR-Code scannen“.</div>`;
      } catch (e) { window.notify?.(e.message, "error"); }
    });
    root.querySelectorAll("[data-ping]").forEach((b) => b.onclick = async () => {
      const box = $("[data-qrbox]");
      box.innerHTML = `<span style="color:var(--subtext)">⏳ Host pingt ${esc(t.peers.find((x) => x.id === b.dataset.ping)?.ip || "")} …</span>`;
      try {
        const r = await vq(`/tunnels/${tid}/peers/${b.dataset.ping}/ping`, { method: "POST" });
        box.innerHTML = `<div style="border:1px solid ${r.ok ? "#3ecf8e" : "var(--danger)"};border-radius:8px;padding:8px 12px;font-size:12.5px">
          <b style="color:${r.ok ? "#3ecf8e" : "var(--danger)"}">${r.ok ? `✔ Host erreicht ${esc(r.ip)}` : `✘ Host erreicht ${esc(r.ip)} nicht`}</b>
          ${r.ok ? ` – ${r.loss}% Verlust${r.avg_ms != null ? `, Ø ${r.avg_ms} ms` : ""}` : ""}<div style="margin-top:4px">${esc(r.hint)}</div></div>`;
      } catch (e) { box.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; }
    });
    root.querySelectorAll("[data-rm]").forEach((b) => b.onclick = async () => {
      if (!(await uiConfirm("Teilnehmer aus dem Tunnel entfernen?", { okText: "Entfernen", danger: true }))) return;
      await vq(`/tunnels/${tid}/peers/${b.dataset.rm}`, { method: "DELETE" }).catch((e) => window.notify?.(e.message, "error")); detail(tid);
    });
    $("[data-add]")?.addEventListener("click", () => addBox(t));
    if (!ended) {
      const poll = async () => {
        try {
          const st = await vq(`/tunnels/${tid}/status`);
          root.querySelectorAll("[data-p]").forEach((tr) => {
            const s = st.peers[tr.dataset.p]; const td = tr.querySelector("[data-conn]");
            if (!s || td.textContent.startsWith("⚠")) return;
            td.innerHTML = s.connected ? `<span style="color:#3ecf8e">● verbunden</span> <span style="color:var(--subtext)">↓${bytes(s.rx)} ↑${bytes(s.tx)}</span>`
              : (s.handshake ? `<span style="color:var(--subtext)">zuletzt ${new Date(s.handshake * 1000).toLocaleTimeString()}</span>` : `<span style="color:var(--subtext)">nicht verbunden</span>`)
                + (s.hint ? `<div style="font-size:11px;color:#f5a524;max-width:340px;white-space:normal">${esc(s.hint)}</div>` : "");
          });
          if (st.error && m) $("[data-state]").innerHTML = `${esc(t.state)} – <span style="color:var(--danger)">Host: ${esc(st.error)}</span>`;
        } catch { /* weiter versuchen */ }
      };
      poll(); timer = setInterval(poll, 5000);
    }
  }

  async function addBox(t) {
    const box = root.querySelector("[data-addbox]");
    let cand; try { cand = await vq("/candidates"); } catch (e) { box.textContent = e.message; return; }
    const have = new Set(t.peers.map((p) => p.kind + ":" + p.ref));
    box.innerHTML = `<div style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:10px">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div><b>Benutzer</b><div style="max-height:160px;overflow:auto;margin-top:4px">${cand.users.filter((u) => !have.has("user:" + u.id)).map((u) => `<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" data-u="${esc(u.id)}"> 👤 ${esc(u.name)}</label>`).join("") || "–"}</div></div>
        <div><b>Agents &amp; Nodes</b><div style="max-height:160px;overflow:auto;margin-top:4px">${cand.agents.filter((a) => !have.has("agent:" + a.id) && a.id !== t.host).map((a) => `<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" data-a="${esc(a.id)}"> ${a.node ? "🖧" : "🖥"} ${esc(a.name)}</label>`).join("") || "–"}</div>
          ${cand.backend && t.host !== "backend" && !have.has("backend:backend") ? `<label style="display:flex;gap:6px;align-items:center;margin-top:6px"><input type="checkbox" data-b> 🗄 Backend</label>` : ""}</div></div>
      <div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap">Laufzeit der Neuen:
        <select data-h><option value="">wie der Tunnel</option>${DUR.map(([h, l]) => `<option value="${h}">${l}</option>`).join("")}</select>
        <span style="flex:1"></span><button class="btn btn-sm" data-x>Abbrechen</button><button class="btn btn-sm btn-primary" data-go>Hinzufügen</button></div></div>`;
    box.querySelector("[data-x]").onclick = () => { box.innerHTML = ""; };
    box.querySelector("[data-go]").onclick = async () => {
      const h = box.querySelector("[data-h]").value;
      const payload = { users: [...box.querySelectorAll("[data-u]:checked")].map((c) => c.dataset.u),
        agents: [...box.querySelectorAll("[data-a]:checked")].map((c) => c.dataset.a),
        backend: !!box.querySelector("[data-b]")?.checked, hours: h === "" ? null : Number(h) };
      try { const r = await vq(`/tunnels/${t.id}/peers`, { method: "POST", body: payload }); window.notify?.(`${r.added} hinzugefügt`, "success", 3000); detail(t.id); }
      catch (e) { window.notify?.(e.message, "error", 6000); }
    };
  }

  list();
}

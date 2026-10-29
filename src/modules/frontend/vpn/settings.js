// settings.js (Modul "vpn") - Zusatz im Register "Einstellungen" der Modul-App:
//   * Host-Einrichtungsskript herunterladen (Linux-Host/VM, Proxmox-CT, Docker-Host)
//   * Werkzeuge auf einem Host installieren (wireguard-tools, iptables, iproute2, ggf. wireguard-go)
//   * Verbindungstest: Host + Endpunkt-IP + UDP-Port per echtem WireGuard-Handshake pruefen -
//     auch BEVOR ein Tunnel existiert (dann legt das Backend kurz einen Test-Tunnel an).
import { getToken } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";

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

const box = (title, inner) => `<div style="border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:14px">
  <b style="display:block;margin-bottom:8px;font-size:14px">${title}</b>${inner}</div>`;

export async function renderSettings(holder) {
  holder.innerHTML = box("🧰 Host-Einrichtungsskript", `
      <p style="margin:0 0 8px;color:var(--subtext);font-size:12.5px">Bereitet einen Rechner als VPN-Host vor (als root ausführen):</p>
      <table class="data-table" style="width:100%;font-size:12.5px;margin-bottom:10px"><tbody>
        <tr><td style="white-space:nowrap"><code>sh vpn-host-setup.sh</code></td><td>im Host/CT/in der VM: Pakete (wireguard-tools, iptables, iproute2, ggf. wireguard-go), IP-Weiterleitung dauerhaft, öffentliche IP anzeigen</td></tr>
        <tr><td style="white-space:nowrap"><code>sh vpn-host-setup.sh --pve-ct 105</code></td><td>auf dem Proxmox-Host: WireGuard-/TUN-Modul laden und <code>/dev/net/tun</code> an CT 105 durchreichen, danach <code>pct reboot 105</code></td></tr>
        <tr><td style="white-space:nowrap"><code>sh vpn-host-setup.sh --docker</code></td><td>auf dem Docker-Host, wenn das Backend VPN-Host ist</td></tr>
        <tr><td style="white-space:nowrap"><code>sh vpn-host-setup.sh --check</code></td><td>nur prüfen</td></tr></tbody></table>
      <button class="btn btn-sm" data-script>⬇ vpn-host-setup.sh herunterladen</button>`)
    + box("📡 Verbindungstest (Endpunkt-IP und UDP-Port)", `
      <div style="display:grid;grid-template-columns:150px 1fr;gap:8px 12px;align-items:center;max-width:760px">
        <label>VPN-Host</label><div style="display:flex;gap:8px;flex-wrap:wrap"><select data-host style="flex:1;min-width:220px"><option>lädt …</option></select>
          <button class="btn btn-sm" data-tools title="fehlende Werkzeuge auf diesem Host nachinstallieren">🔧 Werkzeuge installieren</button></div>
        <label>Endpunkt (IP/Name)</label><input data-ep placeholder="öffentliche IP oder DynDNS-Name">
        <label>UDP-Port</label><input data-port type="number" min="1" max="65535" style="width:120px">
        <label>Tester</label><select data-via><option value="">–</option></select>
      </div>
      <div data-epwarn style="margin-top:8px"></div>
      <div style="font-size:11.5px;color:var(--subtext);margin:8px 0">Der Tester (ein Agent/eine Node oder das Backend) baut einen echten WireGuard-Handshake zum Endpunkt auf.
        Läuft auf diesem Port schon ein Tunnel des Hosts, wird er benutzt – sonst wird kurz ein Test-Tunnel angelegt und wieder entfernt.
        Aussagekräftig nur, wenn der Tester <b>außerhalb</b> des Host-Netzes sitzt.</div>
      <button class="btn btn-primary btn-sm" data-test>▶ Verbindung testen</button>
      <div data-res style="margin-top:10px"></div>`);
  const $ = (s) => holder.querySelector(s);

  $("[data-script]").onclick = async () => {
    try {
      const res = await vq("/setup-script", { raw: true });
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: "vpn-host-setup.sh" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) { window.notify?.(e.message, "error"); }
  };

  let hosts = [];
  try { hosts = (await vq("/hosts")).hosts; }
  catch (e) { $("[data-host]").innerHTML = `<option>${esc(e.message)}</option>`; return; }
  $("[data-host]").innerHTML = hosts.length ? hosts.map((h) => `<option value="${esc(h.id)}">${h.id === "backend" ? "🗄" : "🖧"} ${esc(h.name)}${h.available ? "" : ` (${esc(h.hint)})`}</option>`).join("")
    : `<option value="">kein Host verfügbar (Node-Modul „vpn“ aktivieren)</option>`;

  const loadDefaults = async () => {
    const host = $("[data-host]").value;
    if (!host) return;
    try {
      const d = await vq(`/probe/defaults?host=${encodeURIComponent(host)}&port=${Number($("[data-port]").value) || 0}`);
      $("[data-ep]").value = (d.endpoint || "").replace(/:\d+$/, "");
      if (!$("[data-port]").value) $("[data-port]").value = d.port;
      $("[data-via]").innerHTML = d.testers.length ? d.testers.map((x) => `<option value="${esc(x.id)}">${x.id === "backend" ? "🗄" : "🖥"} ${esc(x.name)}${x.same_net ? " (gleiches Netz wie der Host – wenig aussagekräftig)" : ""}</option>`).join("")
        : `<option value="">kein Tester online</option>`;
      $("[data-epwarn]").innerHTML = (d.warnings || []).map((w) => `<div style="font-size:12px;color:#f5a524">⚠ ${esc(w)}</div>`).join("");
    } catch (e) { $("[data-epwarn]").innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; }
  };
  $("[data-host]").onchange = () => { $("[data-port]").value = ""; loadDefaults(); };
  await loadDefaults();

  $("[data-tools]").onclick = async () => {
    const b = $("[data-tools]"); b.disabled = true; b.textContent = "⏳ installiere …";
    try {
      const r = await vq(`/hosts/${encodeURIComponent($("[data-host]").value)}/tools`, { method: "POST" });
      const miss = Object.entries(r.tools || {}).filter(([, v]) => !v).map(([k]) => k);
      window.notify?.(!miss.length && r.ok !== false ? "Werkzeuge vorhanden" : `Fehlt noch: ${miss.join(", ") || r.error || r.hint} – ggf. Einrichtungsskript benutzen`,
        !miss.length && r.ok !== false ? "success" : "warn", 10000);
    } catch (e) { window.notify?.(e.message, "error", 8000); }
    b.disabled = false; b.textContent = "🔧 Werkzeuge installieren";
  };

  $("[data-test]").onclick = async () => {
    const via = $("[data-via]").value, res = $("[data-res]");
    if (!via) { res.innerHTML = `<span style="color:var(--danger)">Kein Tester gewählt.</span>`; return; }
    $("[data-test]").disabled = true;
    res.innerHTML = `<span style="color:var(--subtext)">⏳ Test läuft (bis 30 s) …</span>`;
    try {
      const r = await vq("/probe", { method: "POST", body: { host: $("[data-host]").value, endpoint: $("[data-ep]").value.trim(),
        port: Number($("[data-port]").value) || 0, via } });
      res.innerHTML = `<div style="border:1px solid ${r.ok ? "#3ecf8e" : "var(--danger)"};border-radius:8px;padding:8px 12px">
        <b style="color:${r.ok ? "#3ecf8e" : "var(--danger)"}">${r.ok ? "✔ Erreichbar" : "✘ Nicht erreichbar"}</b> – ${esc(r.detail || "")}
        ${r.used_tunnel ? `<div style="font-size:11.5px;color:var(--subtext)">geprüft über den laufenden Tunnel „${esc(r.used_tunnel)}“</div>` : ""}
        ${(r.steps || []).length ? `<div style="font-size:11.5px;color:var(--subtext);margin-top:4px">${r.steps.map(esc).join(" → ")}</div>` : ""}
        ${[...(r.endpoint_warnings || []), ...(r.host_warnings || [])].map((w) => `<div style="font-size:12px;color:#f5a524;margin-top:4px">⚠ ${esc(w)}</div>`).join("")}</div>`;
    } catch (e) { res.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`; }
    $("[data-test]").disabled = false;
  };
}

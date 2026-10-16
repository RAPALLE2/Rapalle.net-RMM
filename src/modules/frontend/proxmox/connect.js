// connect.js (Modul "proxmox") - Proxmox mit einem Agenten verbinden
// ------------------------------------------------------------------
// Dialog: Adresse (lokal / eigene / RMM-Relay), Port, Anmeldung (API-Token oder
// Benutzer + Passwort), Zertifikat (Fingerabdruck / Pruefung), Test, Entfernen.
// Das ❔-Symbol oeffnet die Anleitung, was in Proxmox einzurichten ist.

import { esc, uiConfirm } from "/js/app/utils.js";
import { preq } from "./common.js";

export const HELP_HTML = `
  <h4 style="margin:0 0 6px">So richtest du Proxmox für das RMM ein</h4>
  <p style="margin:0 0 8px;color:var(--subtext)">Alles in der Proxmox-Weboberfläche unter <b>Rechenzentrum → Berechtigungen</b>.</p>
  <ol style="margin:0 0 10px 18px;padding:0;line-height:1.55">
    <li><b>Benutzer anlegen:</b> Benutzer → Hinzufügen → Name <code>rmm</code>, Realm
        <i>Proxmox VE authentication server</i> → ergibt <code>rmm@pve</code>.</li>
    <li><b>Rechte geben:</b> Berechtigungen → Hinzufügen → <i>Benutzer-Berechtigung</i>: Pfad <code>/</code>,
        Benutzer <code>rmm@pve</code>, Rolle <b>PVEAdmin</b> (alles steuern) oder <b>PVEAuditor</b> (nur ansehen),
        „Propagieren“ angehakt.</li>
    <li><b>Anmeldung wählen:</b>
      <ul style="margin:4px 0 0 16px;padding:0">
        <li><b>API-Token (empfohlen):</b> API-Token → Hinzufügen → Benutzer <code>rmm@pve</code>, Token-ID
            <code>rmm</code>, <b>„Privilegientrennung“ ausschalten</b> (sonst hat der Token keine Rechte). Das
            <b>Secret</b> wird nur einmal angezeigt – kopieren. Hier eintragen: Benutzer <code>rmm@pve</code>,
            Token-ID <code>rmm</code>, Secret.</li>
        <li><b>Benutzer + Passwort:</b> beim Benutzer ein Passwort setzen (Benutzer → Passwort). Nötig für die
            <b>Konsole direkt im Dashboard</b> – Proxmox stellt Konsolen-Tickets nur für Benutzer-Anmeldungen aus.</li>
      </ul></li>
  </ol>
  <h4 style="margin:0 0 6px">Adresse</h4>
  <ul style="margin:0 0 10px 18px;padding:0;line-height:1.55">
    <li><b>Lokale Adresse</b> – die IP des Agenten (Agent läuft auf dem Proxmox-Host); der RMM-Server muss sie erreichen.</li>
    <li><b>Eigene Adresse</b> – z. B. die Cluster-IP oder ein DNS-Name, erreichbar vom RMM-Server (auch über VPN).</li>
    <li><b>RMM-Relay</b> – die Verbindung läuft <b>durch den Agenten</b>; Proxmox muss vom Server aus nicht erreichbar
        sein. Ziel aus Sicht des Agenten: <code>127.0.0.1</code>, wenn der Agent auf dem Proxmox-Host läuft.
        Braucht das Modul „Netzwerk“ mit Port-Weiterleitung.</li>
  </ul>
  <p style="margin:0 0 4px">Port ist bei Proxmox <code>8006</code>.</p>
  <h4 style="margin:0 0 6px">Zertifikat</h4>
  <p style="margin:0;line-height:1.55">Proxmox nutzt ab Werk ein selbst signiertes Zertifikat. Verbindung speichern und
    <b>„Fingerabdruck holen“</b> klicken – das RMM vertraut dann genau diesem Zertifikat (sicher). Den Abdruck kannst du in
    Proxmox unter <i>Node → System → Zertifikate</i> vergleichen. „Zertifikat prüfen“ nur bei einem gültigen Zertifikat
    (z. B. Let's Encrypt) einschalten.</p>`;

export function showHelp() {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9600;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px;width:640px;
      max-width:100%;max-height:90vh;overflow:auto;font-size:13px">${HELP_HTML}
    <div style="text-align:right;margin-top:12px"><button class="btn-primary" data-x>Verstanden</button></div></div>`;
  ov.addEventListener("click", (e) => { if (e.target === ov || e.target.dataset.x !== undefined) ov.remove(); });
  document.body.appendChild(ov);
}

/** Verbindungsdialog fuer einen Agenten. onDone(connected:boolean) */
export async function openConnectionDialog(client, onDone) {
  let info;
  try { info = await preq(`/${encodeURIComponent(client.id)}/connection`); }
  catch (e) { window.notify?.(e.message, "error"); return; }
  const c = info.connection || { addr_mode: "local", port: 8006, auth: "token", relay_host: "127.0.0.1", verify_tls: false };
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
  ov.innerHTML = `<style>.pxchk{display:flex;align-items:center;gap:8px;margin:5px 0;flex-wrap:wrap;cursor:pointer}
      .pxchk input[type=radio],.pxchk input[type=checkbox]{appearance:auto;-webkit-appearance:auto;width:16px;height:16px;min-height:0;
        margin:0;padding:0;flex:0 0 auto;accent-color:var(--accent)}
      .pxchk input[type=text],.pxchk input:not([type]){height:var(--ui-h-sm,26px)}</style>
    <div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px;width:560px;max-width:100%;
                max-height:92vh;overflow:auto;font-size:13px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
        <b style="font-size:15px;flex:1">🖧 Proxmox – ${esc(info.hostname || client.hostname || "")}</b>
        <button class="btn-tool" data-help title="Anleitung: Was muss ich in Proxmox einrichten?">❔</button>
        <button class="btn-tool" data-close title="Schließen">✕</button>
      </div>
      <div class="form-row"><label>Bezeichnung (optional)</label><input data-f="label" value="${esc(c.label || "")}" placeholder="z. B. Cluster Büro"></div>
      <label style="font-weight:600;display:block;margin:6px 0 4px">Adresse</label>
      <label class="pxchk"><input type="radio" name="pxa" value="local"> Lokale Adresse des Agenten: <b>${esc(info.agent_ip || "nicht bekannt")}</b></label>
      <label class="pxchk"><input type="radio" name="pxa" value="custom"> Eigene Adresse:
        <input data-f="host" value="${esc(c.host || "")}" placeholder="pve.firma.lan / 10.0.0.5" style="flex:1;margin-left:6px"></label>
      <label class="pxchk"><input type="radio" name="pxa" value="relay"> RMM-Relay (durch den Agenten), Ziel beim Agenten:
        <input data-f="relay_host" value="${esc(c.relay_host || "127.0.0.1")}" style="width:130px;margin-left:6px"></label>
      <div class="form-row" style="margin-top:6px"><label>Port</label><input data-f="port" type="number" min="1" max="65535" value="${esc(String(c.port || 8006))}" style="width:110px"></div>
      <label style="font-weight:600;display:block;margin:6px 0 4px">Anmeldung</label>
      <label class="pxchk"><input type="radio" name="pxu" value="token"> API-Token (empfohlen)</label>
      <label class="pxchk"><input type="radio" name="pxu" value="password"> Benutzer + Passwort (nötig für die Konsole im Dashboard)</label>
      <div class="form-row"><label>Benutzer (name@realm)</label><input data-f="user" value="${esc(c.user || "")}" placeholder="rmm@pve"></div>
      <div data-auth="token">
        <div class="form-row"><label>Token-ID</label><input data-f="token_id" value="${esc(c.token_id || "")}" placeholder="rmm"></div>
        <div class="form-row"><label>Secret ${c.has_secret ? "(gespeichert – leer lassen zum Behalten)" : ""}</label><input data-f="secret" type="password" autocomplete="off"></div>
      </div>
      <div data-auth="password">
        <div class="form-row"><label>Passwort ${c.has_password ? "(gespeichert – leer lassen zum Behalten)" : ""}</label><input data-f="password" type="password" autocomplete="off"></div>
      </div>
      <label style="font-weight:600;display:block;margin:6px 0 4px">Zertifikat</label>
      <div class="form-row"><label>SHA-256-Fingerabdruck (selbst signiertes Zertifikat)</label>
        <input data-f="fingerprint" value="${esc(c.fingerprint || "")}" placeholder="AB:CD:…" style="font-family:ui-monospace,monospace;font-size:11.5px"></div>
      <div style="margin:-2px 0 6px"><button class="btn btn-sm" data-fp ${info.connection ? "" : "disabled"}
        title="${info.connection ? "Zertifikat über die eingestellte Adresse abrufen" : "Erst speichern"}">🔐 Fingerabdruck holen</button></div>
      <label class="pxchk"><input type="checkbox" data-f="verify_tls" ${c.verify_tls ? "checked" : ""}> Zertifikat prüfen (nur bei gültigem Zertifikat)</label>
      <div data-status style="min-height:18px;margin:10px 0;color:var(--subtext)"></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn-primary" data-save>Speichern &amp; testen</button>
        ${info.connection ? `<button class="btn btn-danger" data-del>Verbindung entfernen</button>` : ""}
      </div>
    </div>`;
  document.body.appendChild(ov);
  const $ = (s) => ov.querySelector(s);
  const f = (k) => ov.querySelector(`[data-f="${k}"]`);
  const status = (t, err) => { $("[data-status]").innerHTML = t; $("[data-status]").style.color = err ? "var(--danger)" : "var(--subtext)"; };
  ov.querySelector(`input[name="pxa"][value="${c.addr_mode || "local"}"]`).checked = true;
  ov.querySelector(`input[name="pxu"][value="${c.auth || "token"}"]`).checked = true;
  const syncAuth = () => {
    const a = ov.querySelector('input[name="pxu"]:checked').value;
    ov.querySelectorAll("[data-auth]").forEach((el) => { el.style.display = el.dataset.auth === a ? "" : "none"; });
  };
  ov.querySelectorAll('input[name="pxu"]').forEach((r) => r.addEventListener("change", syncAuth));
  syncAuth();
  f("host").addEventListener("focus", () => { ov.querySelector('input[name="pxa"][value="custom"]').checked = true; });
  f("relay_host").addEventListener("focus", () => { ov.querySelector('input[name="pxa"][value="relay"]').checked = true; });
  let changed = false;
  const close = () => { ov.remove(); onDone?.(changed); };
  $("[data-close]").addEventListener("click", close);
  $("[data-help]").addEventListener("click", showHelp);

  const body = () => ({
    addr_mode: ov.querySelector('input[name="pxa"]:checked').value, host: f("host").value.trim(),
    relay_host: f("relay_host").value.trim() || "127.0.0.1", port: parseInt(f("port").value, 10) || 8006,
    auth: ov.querySelector('input[name="pxu"]:checked').value, user: f("user").value.trim(),
    token_id: f("token_id").value.trim(), secret: f("secret").value, password: f("password").value,
    verify_tls: f("verify_tls").checked, fingerprint: f("fingerprint").value.trim(), label: f("label").value.trim(),
  });
  const cid = encodeURIComponent(client.id);
  $("[data-save]").addEventListener("click", async () => {
    status("Speichere …");
    try {
      await preq(`/${cid}/connection`, { method: "PUT", body: body() });
      changed = true;
      $("[data-fp]").disabled = false;
      status("Gespeichert – teste Verbindung …");
      const t = await preq(`/${cid}/test`, { method: "POST" });
      status(`✅ Verbunden: Proxmox VE ${esc(t.version || "?")} · Nodes: ${esc((t.nodes || []).join(", "))} · ${esc(t.address)}`);
    } catch (e) {
      const hint = e.status === 495 ? " – Tipp: „Fingerabdruck holen“ klicken." : "";
      status(`❌ ${esc(e.message)}${hint}`, true);
    }
  });
  $("[data-fp]").addEventListener("click", async () => {
    status("Hole Fingerabdruck …");
    try {
      const r = await preq(`/${cid}/fingerprint`, { method: "POST" });
      f("fingerprint").value = r.fingerprint;
      status("Fingerabdruck übernommen – jetzt „Speichern & testen“. (Vergleich in Proxmox: Node → System → Zertifikate)");
    } catch (e) { status(`❌ ${esc(e.message)}`, true); }
  });
  $("[data-del]")?.addEventListener("click", async () => {
    if (!(await uiConfirm("Proxmox-Verbindung dieses Agenten entfernen?", { description: "Zugangsdaten und Gast-Freigaben werden gelöscht. In Proxmox selbst ändert sich nichts.", okText: "Entfernen", danger: true }))) return;
    try { await preq(`/${cid}/connection`, { method: "DELETE" }); changed = true; close(); }
    catch (e) { status(`❌ ${esc(e.message)}`, true); }
  });
}

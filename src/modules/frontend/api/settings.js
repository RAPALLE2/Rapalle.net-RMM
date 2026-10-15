// settings.js (Modul "api") - Verwaltung der REST-API
// ---------------------------------------------------
// Register: Übersicht (Kurzanleitung) | Einstellungen | Anmeldungen | Protokoll
// Backend: /api/modules/api/settings (Einstellungen), /api/apimod/* (Tokens, Protokoll)

import { getToken } from "/js/app/api.js";
import { esc, uiConfirm } from "/js/app/utils.js";

async function req(path, opts = {}) {
  const token = getToken() || "";
  const headers = { ...(token ? { Authorization: "Bearer " + token } : {}) };
  if (opts.body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* egal */ }
    throw new Error(msg);
  }
  return res.json();
}
const ts = (s) => (s ? new Date(s * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "medium" }) : "–");
const MODE = { open: "Ohne Benutzer", user: "Mit Benutzer", secure: "Mit Benutzer (secure)" };

export async function renderSettings(root) {
  root.innerHTML = `<div style="font-size:13px">
    <div class="tab-bar" style="padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:12px">
      <button class="tab-btn active" data-t="ov">Übersicht</button>
      <button class="tab-btn" data-t="set">Einstellungen</button>
      <button class="tab-btn" data-t="acc">API-Konten</button>
      <button class="tab-btn" data-t="tok">Anmeldungen</button>
      <button class="tab-btn" data-t="log">Protokoll</button>
    </div>
    <div data-pane></div></div>`;
  const pane = root.querySelector("[data-pane]");
  const show = (t) => {
    root.querySelectorAll("[data-t]").forEach((b) => b.classList.toggle("active", b.dataset.t === t));
    ({ ov: drawOverview, set: drawSettings, acc: drawAccounts, tok: drawTokens, log: drawLog })[t]();
  };
  root.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => show(b.dataset.t)));

  // ---------------------------------------------------------------- Übersicht
  async function drawOverview() {
    pane.innerHTML = "Lade …";
    let o;
    try { o = await req("/api/apimod/overview"); } catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    const b = o.base;
    const login = o.mode === "secure"
      ? `TOKEN=$(curl -s -X POST ${b}/auth/login -H "Content-Type: application/json" \\
  -d '{"username":"api-user","api_key":"rmmak_…"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -H "Authorization: Bearer $TOKEN" ${b}/clients`
      : o.mode === "user"
        ? `curl -c jar.txt -X POST ${b}/auth/login -H "Content-Type: application/json" \\
  -d '{"username":"api-user","api_key":"rmmak_…"}'
curl -b jar.txt ${b}/clients`
        : `curl ${b}/clients        # läuft als eingestellter API-Benutzer`;
    pane.innerHTML = `
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-bottom:14px">
        ${[["Betriebsart", MODE[o.mode] || o.mode], ["Code gültig", o.mode === "secure" ? o.token_lifetime : "–"],
           ["Aktive Anmeldungen", o.active_tokens], ["Aufrufe (24 h)", o.calls_24h], ["Fehler (24 h)", o.errors_24h],
           ["Endpunkte", `${o.shortcuts} Kurzwege + ${o.bridge_routes} über die Brücke`]].map(([k, v]) => `
          <div style="border:1px solid var(--border);border-radius:8px;padding:8px 10px">
            <div style="font-size:11px;color:var(--subtext)">${k}</div><div style="font-weight:600;margin-top:2px">${esc(String(v))}</div></div>`).join("")}
      </div>
      <h4 style="margin:0 0 6px">So geht's</h4>
      <ol style="margin:0 0 12px 18px;padding:0;line-height:1.6;color:var(--subtext)">
        <li>Benutzer anlegen mit der Vorlage <b>„API-Benutzer“</b> – statt eines Passworts erzeugt der Server einen
            <b>API-Schlüssel</b> (nur einmal angezeigt). Alternativ einem normalen Konto das Recht „REST-API benutzen“ geben.</li>
        <li>Diesem Konto zusätzlich die Rechte geben, die es per API braucht – genau wie im Dashboard
            (z. B. „Client löschen“ zum Löschen von Clients). Admins dürfen alles.</li>
        <li>Anmelden und aufrufen:</li>
      </ol>
      <pre style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:10px;overflow:auto;font-size:12px;margin:0 0 10px">${esc(login)}</pre>
      <div style="color:var(--subtext)">Katalog aller Endpunkte: <code>GET ${esc(b)}/routes</code> ·
        jede Dashboard-Route: <code>${esc(b)}/call/&lt;pfad&gt;</code> (z. B. <code>/api/clients</code> → <code>${esc(b)}/call/clients</code>)</div>`;
  }

  // ---------------------------------------------------------------- Einstellungen
  async function drawSettings() {
    pane.innerHTML = "Lade …";
    let s;
    try { s = await req("/api/apimod/settings"); } catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    const schema = s.schema || [];
    const vals = s.values || {};
    pane.innerHTML = schema.map((f) => {
      const v = vals[f.key] ?? f.default ?? "";
      const id = `apiset-${f.key}`;
      const input = f.type === "select"
        ? `<select id="${id}">${(f.options || []).map((o) => `<option value="${esc(o.value)}" ${o.value === v ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`
        : `<input id="${id}" type="${f.type === "number" ? "number" : "text"}" value="${esc(String(v))}" ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""}>`;
      return `<div class="form-row"><label for="${id}">${esc(f.label)}</label>${input}</div>`;
    }).join("") + `
      <div style="color:var(--subtext);font-size:12px;margin:6px 0 12px">
        <b>Ohne Benutzer</b> ist nur mit einer IP-Freigabeliste sinnvoll – jeder, der den Server erreicht, kann sonst
        alles tun, was der API-Benutzer darf. Eine geänderte Code-Gültigkeit gilt für neue Anmeldungen.</div>
      <button class="btn-primary" data-save>Speichern</button>`;
    pane.querySelector("[data-save]").addEventListener("click", async () => {
      const out = {};
      schema.forEach((f) => { out[f.key] = pane.querySelector(`#apiset-${f.key}`).value; });
      if (out.mode === "open" && !out.service_user.trim()) {
        window.notify?.("Für „ohne Benutzer“ bitte einen API-Benutzer eintragen.", "warn"); return;
      }
      if (out.mode === "open" && !out.allow_ips.trim()
          && !(await uiConfirm("API ohne Anmeldung für ALLE Adressen öffnen?",
            { description: "Ohne IP-Freigabeliste kann jeder, der den Server erreicht, die API als API-Benutzer nutzen.", okText: "Trotzdem speichern", danger: true }))) return;
      try { await req("/api/modules/api/settings", { method: "PUT", body: JSON.stringify({ settings: out }) });
        window.notify?.("Gespeichert", "success"); }
      catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  // ---------------------------------------------------------------- API-Konten
  function showKey(username, key) {
    const ov = document.createElement("div");
    ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
    ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--accent);border-radius:10px;padding:16px;width:620px;max-width:100%;font-size:13px">
      <b>Neuer API-Schlüssel für ${esc(username)}</b>
      <p style="color:var(--subtext);margin:6px 0 10px">Wird nur jetzt angezeigt. Der alte Schlüssel und alle Anmeldungen dieses Kontos sind ab sofort ungültig.</p>
      <textarea readonly rows="4" style="width:100%;font-family:ui-monospace,monospace;font-size:12px">${esc(key)}</textarea>
      <div style="display:flex;gap:8px;margin-top:10px"><button class="btn-primary" data-c>Kopieren</button><button class="btn" data-x>Schließen</button></div></div>`;
    ov.querySelector("[data-c]").addEventListener("click", () => { navigator.clipboard?.writeText(key); window.notify?.("Kopiert", "success"); });
    ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
    document.body.appendChild(ov);
  }

  async function drawAccounts() {
    pane.innerHTML = "Lade …";
    let r;
    try { r = await req("/api/apimod/accounts"); } catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    pane.innerHTML = `
      <div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:10px">
        <p style="color:var(--subtext);margin:0;flex:1">API-Konten (Vorlage „API-Benutzer“) haben kein Passwort und keine Erstanmeldung,
          sondern einen API-Schlüssel. Neue Konten legst du unter Einstellungen → Benutzer an. Automatische Rotation:
          Register „Einstellungen“ – der neue Schlüssel kommt bei der Anmeldung mit (<code>new_api_key</code>).</p>
        <button class="btn btn-warn" data-rotall ${r.accounts.some((a) => a.key_active) ? "" : "disabled"}>Alle Schlüssel rotieren</button>
      </div>
      ${r.accounts.length ? `<table class="data-table" style="width:100%"><thead><tr>
        <th style="text-align:left">Konto</th><th>Schlüssel</th><th>Erstellt</th><th>Zuletzt benutzt</th><th>Nächste Rotation</th><th></th></tr></thead>
        <tbody>${r.accounts.map((a) => `<tr>
          <td><b>${esc(a.username)}</b>${a.display_name && a.display_name !== a.username ? `<div style="font-size:11px;color:var(--subtext)">${esc(a.display_name)}</div>` : ""}</td>
          <td style="color:${a.key_active ? "var(--accent)" : "var(--danger)"}">${a.key_active ? "aktiv" : "gesperrt"}</td>
          <td>${ts(a.key_created)}</td><td>${ts(a.key_last_used)}${a.key_last_ip ? ` <span style="color:var(--subtext)">(${esc(a.key_last_ip)})</span>` : ""}</td>
          <td>${!a.key_active ? "–" : typeof a.next_rotation === "string" ? `<span style="color:var(--warn)">${esc(a.next_rotation)}</span>`
              : a.next_rotation ? ts(a.next_rotation) : "nie"}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="btn btn-sm" data-rot="${esc(a.id)}" data-name="${esc(a.username)}">Neuer Schlüssel</button>
            ${a.key_active ? `<button class="btn btn-sm btn-danger" data-blk="${esc(a.id)}" data-name="${esc(a.username)}">Sperren</button>` : ""}</td></tr>`).join("")}
        </tbody></table>` : `<div style="color:var(--subtext)">Noch keine API-Konten.</div>`}`;
    pane.querySelector("[data-rotall]")?.addEventListener("click", async () => {
      if (!(await uiConfirm("Alle API-Schlüssel rotieren?", {
        description: "Jedes API-Konto bekommt bei seiner nächsten Anmeldung einen neuen Schlüssel (new_api_key). "
          + "Der bisherige gilt danach noch die eingestellte Übergangszeit.", okText: "Alle rotieren" }))) return;
      try { const res = await req("/api/apimod/accounts/rotate-all", { method: "POST" });
        window.notify?.(`${res.due} Schlüssel werden bei der nächsten Anmeldung erneuert`, "success"); drawAccounts(); }
      catch (e) { window.notify?.(e.message, "error"); }
    });
    pane.querySelectorAll("[data-rot]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm(`Neuen Schlüssel für „${b.dataset.name}“ erzeugen?`, { description: "Der bisherige Schlüssel funktioniert danach nicht mehr.", okText: "Neuen Schlüssel erzeugen", danger: true }))) return;
      try { const res = await req(`/api/apimod/accounts/${encodeURIComponent(b.dataset.rot)}/rotate`, { method: "POST" }); showKey(res.username, res.api_key); drawAccounts(); }
      catch (e) { window.notify?.(e.message, "error"); }
    }));
    pane.querySelectorAll("[data-blk]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm(`Schlüssel von „${b.dataset.name}“ sperren?`, { description: "Das Konto kann die API erst nach „Neuer Schlüssel“ wieder nutzen.", okText: "Sperren", danger: true }))) return;
      await req(`/api/apimod/accounts/${encodeURIComponent(b.dataset.blk)}/revoke`, { method: "POST" }); drawAccounts();
    }));
  }

  // ---------------------------------------------------------------- Anmeldungen
  async function drawTokens() {
    pane.innerHTML = "Lade …";
    let r;
    try { r = await req("/api/apimod/tokens"); } catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    pane.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
        <span style="flex:1;color:var(--subtext)">${r.tokens.length} aktive Anmeldung(en)</span>
        <button class="btn btn-danger" data-all ${r.tokens.length ? "" : "disabled"}>Alle sofort ablaufen lassen</button>
      </div>
      ${r.tokens.length ? `<table class="data-table" style="width:100%"><thead><tr>
        <th style="text-align:left">Benutzer</th><th>Art</th><th>Angemeldet</th><th>Zuletzt</th><th>Läuft ab</th><th>IP</th><th></th></tr></thead>
        <tbody>${r.tokens.map((t) => `<tr>
          <td><b>${esc(t.username || "?")}</b>${t.label ? `<div style="font-size:11px;color:var(--subtext)">${esc(t.label)}</div>` : ""}</td>
          <td>${t.kind === "secure" ? "Code" : "Sitzung"}</td><td>${ts(t.created)}</td><td>${ts(t.last_used)}</td>
          <td>${t.expires ? ts(t.expires) : "nie"}</td><td>${esc(t.last_ip || "")}</td>
          <td style="text-align:right"><button class="btn btn-sm btn-danger" data-rev="${esc(t.id)}">Widerrufen</button></td></tr>`).join("")}
        </tbody></table>` : `<div style="color:var(--subtext)">Keine aktiven Anmeldungen.</div>`}`;
    pane.querySelector("[data-all]")?.addEventListener("click", async () => {
      if (!(await uiConfirm("Alle API-Anmeldungen sofort ungültig machen?", { description: "Alle Aufrufer müssen sich neu anmelden.", okText: "Alle widerrufen", danger: true }))) return;
      const res = await req("/api/apimod/tokens/revoke-all", { method: "POST" });
      window.notify?.(`${res.revoked} Anmeldung(en) widerrufen`, "success"); drawTokens();
    });
    pane.querySelectorAll("[data-rev]").forEach((b) => b.addEventListener("click", async () => {
      await req(`/api/apimod/tokens/${encodeURIComponent(b.dataset.rev)}`, { method: "DELETE" }); drawTokens();
    }));
  }

  // ---------------------------------------------------------------- Protokoll
  let filt = { username: "", status: "", path: "" };
  async function drawLog(more = null) {
    if (!more) {
      pane.innerHTML = `
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
          <input type="text" data-f="username" placeholder="Benutzer" value="${esc(filt.username)}" style="width:140px">
          <input type="text" data-f="path" placeholder="Pfad enthält …" value="${esc(filt.path)}" style="flex:1;min-width:140px">
          <select data-f="status"><option value="">alle</option><option value="ok" ${filt.status === "ok" ? "selected" : ""}>erfolgreich</option>
            <option value="error" ${filt.status === "error" ? "selected" : ""}>Fehler</option></select>
          <button class="btn" data-go>Filtern</button>
          <button class="btn btn-danger" data-clear>Protokoll leeren</button>
        </div>
        <div style="overflow:auto"><table class="data-table" style="width:100%"><thead><tr>
          <th style="text-align:left">Zeit</th><th>Benutzer</th><th>Methode</th><th style="text-align:left">Pfad</th><th>Status</th><th>ms</th><th>IP</th></tr></thead>
          <tbody data-rows></tbody></table></div>
        <div style="margin-top:8px"><button class="btn btn-sm" data-more style="display:none">Ältere laden</button></div>`;
      pane.querySelector("[data-go]").addEventListener("click", () => {
        pane.querySelectorAll("[data-f]").forEach((el) => { filt[el.dataset.f] = el.value.trim(); }); drawLog();
      });
      pane.querySelector("[data-clear]").addEventListener("click", async () => {
        if (!(await uiConfirm("Gesamtes API-Protokoll löschen?", { okText: "Löschen", danger: true }))) return;
        await req("/api/apimod/log", { method: "DELETE" }); drawLog();
      });
    }
    const q = new URLSearchParams({ limit: "200", ...filt, ...(more ? { before: String(more) } : {}) });
    let r;
    try { r = await req(`/api/apimod/log?${q}`); } catch (e) { window.notify?.(e.message, "error"); return; }
    const tbody = pane.querySelector("[data-rows]");
    tbody.insertAdjacentHTML("beforeend", r.entries.map((e) => `<tr title="${esc(e.detail || "")}">
      <td style="white-space:nowrap">${ts(e.ts)}</td><td>${esc(e.username || "–")}</td><td>${esc(e.method)}</td>
      <td style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><code>${esc(e.path)}</code></td>
      <td style="color:${e.status >= 400 ? "var(--danger)" : "var(--accent)"}">${e.status}</td><td>${e.ms}</td><td>${esc(e.ip || "")}</td></tr>`).join("")
      || (more ? "" : `<tr><td colspan="7" style="color:var(--subtext)">Noch keine Aufrufe.</td></tr>`));
    const btn = pane.querySelector("[data-more]");
    btn.style.display = r.entries.length === 200 ? "" : "none";
    btn.onclick = () => drawLog(r.entries[r.entries.length - 1].id);
  }

  show("ov");
}

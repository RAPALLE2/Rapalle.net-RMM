// app.js (Modul "canary") - zwei Apps: IP-Tracker und Canary-Tokens
// -----------------------------------------------------------------
// IP-Tracker: Link anlegen (öffentlich erreichbar) + Weiterleitungsziel; wer ihn
//   öffnet, hinterlässt IP/Standort/Browser/OS und wird sofort weitergeleitet.
// Canary-Tokens: Web-Link, E-Mail-Pixel, Köder-Datei (docx/pdf/xlsx), QR, DNS;
//   Aufruf schlägt Alarm (Dashboard + optional Webhook/E-Mail über „Benachrichtigungen“).

import { getToken } from "/js/app/api.js";
import { esc, uiConfirm } from "/js/app/utils.js";

async function cq(path, opts = {}) {
  const headers = { Authorization: "Bearer " + (getToken() || "") };
  if (opts.body !== undefined && typeof opts.body !== "string") { headers["Content-Type"] = "application/json"; opts = { ...opts, body: JSON.stringify(opts.body) }; }
  const res = await fetch("/api/canary" + path, { ...opts, headers });
  if (!res.ok) { let m = res.statusText; try { const j = await res.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* egal */ } throw new Error(m); }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

const fmt = (ms) => ms ? new Date(ms).toLocaleString() : "–";
const copy = (text) => { navigator.clipboard?.writeText(text).then(() => window.notify?.("Kopiert", "success", 1500)); };
const flag = (cc) => cc ? String.fromCodePoint(...[...cc.toUpperCase()].map((c) => 127397 + c.charCodeAt(0))) : "";

function hitRow(h) {
  const g = h.geo || {};
  const loc = [g.city, g.region, g.country].filter(Boolean).join(", ");
  const net = [g.isp || g.org, g.asn].filter(Boolean).join(" · ");
  const tags = [h.bot ? "🤖 Bot/Script" : "", g.proxy ? "Proxy/VPN" : "", g.hosting ? "Rechenzentrum" : "", g.mobile ? "Mobilfunk" : ""].filter(Boolean).join(" · ");
  return `<tr>
    <td style="white-space:nowrap">${esc(fmt(h.at))}</td>
    <td><b>${esc(h.ip || "?")}</b>${h.via ? ` <span style="color:var(--subtext)">(${esc(h.via)})</span>` : ""}
      ${g.country_code ? " " + flag(g.country_code) : ""}</td>
    <td>${esc(loc || "–")}${net ? `<div style="font-size:11px;color:var(--subtext)">${esc(net)}</div>` : ""}</td>
    <td>${esc([h.os, h.browser].filter(Boolean).join(" / ") || "–")}<div style="font-size:11px;color:var(--subtext)">${esc(h.device || "")}${tags ? " · " + esc(tags) : ""}</div></td>
    <td style="font-size:11px;color:var(--subtext);max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(h.ua || "")}">${esc(h.query || h.referer || h.ua || "")}</td>
  </tr>`;
}

async function showHits(ref, title) {
  let data;
  try { data = await cq(`/hits?ref=${encodeURIComponent(ref)}`); } catch (e) { window.notify?.(e.message, "error"); return; }
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;width:min(1000px,100%);max-height:calc(100dvh - 24px);overflow:auto;padding:16px 18px">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px"><b style="font-size:15px;flex:1">Treffer – ${esc(title)}</b>
      <button class="btn btn-sm btn-danger" data-clear>Treffer löschen</button><button class="btn btn-sm" data-x>Schließen</button></div>
    ${data.hits.length ? `<table class="data-table" style="width:100%;font-size:12.5px">
      <thead><tr><th style="text-align:left">Zeit</th><th style="text-align:left">IP</th><th style="text-align:left">Standort / Netz</th><th style="text-align:left">System</th><th style="text-align:left">Weiteres</th></tr></thead>
      <tbody>${data.hits.map(hitRow).join("")}</tbody></table>` : `<div style="color:var(--subtext)">Noch keine Treffer.</div>`}</div>`;
  document.body.appendChild(ov);
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  ov.querySelector("[data-clear]").addEventListener("click", async () => {
    if (!(await uiConfirm("Alle Treffer dieses Eintrags löschen?", { okText: "Löschen", danger: true }))) return;
    try { await cq(`/hits?ref=${encodeURIComponent(ref)}`, { method: "DELETE" }); ov.remove(); } catch (e) { window.notify?.(e.message, "error"); }
  });
}

function linkField(label, value, extra = "") {
  return `<div style="display:flex;gap:6px;align-items:center;margin:2px 0">
    <span style="color:var(--subtext);font-size:12px;min-width:90px">${esc(label)}</span>
    <code style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px">${esc(value)}</code>
    <button class="btn-tool" data-copy="${esc(value)}" title="Kopieren">📋</button>${extra}</div>`;
}

// ================================================================= IP-TRACKER
export async function renderIpTracker(body) {
  body.innerHTML = `<div style="padding:14px 16px;font-size:13px">
    <p style="color:var(--subtext);margin:0 0 10px">Ein <b>IP-Tracker-Link</b> ist öffentlich erreichbar: Wer ihn öffnet,
      hinterlässt IP, Standort, Browser und Betriebssystem und wird danach <b>sofort</b> auf dein Ziel weitergeleitet.
      Erfasst wird, was der Browser serverseitig sendet (keine genaue Hardware).</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px">
      <label style="font-size:12px;color:var(--subtext)">Name<br><input data-name placeholder="z. B. Bewerbung Foto" style="width:220px"></label>
      <label style="font-size:12px;color:var(--subtext)">Weiterleiten auf (Ziel-URL)<br><input data-red placeholder="https://beispiel.de/bild.jpg" style="width:320px"></label>
      <button class="btn-primary btn-sm" data-add>＋ Link anlegen</button>
    </div>
    <div data-list>Lade …</div></div>`;
  const list = body.querySelector("[data-list]");
  const draw = async () => {
    let d; try { d = await cq("/trackers"); } catch (e) { list.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    list.innerHTML = d.trackers.length ? d.trackers.map((t) => `
      <div style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px"><b>${esc(t.name)}</b>
          <span style="font-size:11px;color:var(--subtext)">${t.count || 0} Treffer${t.last ? " · zuletzt " + esc(fmt(t.last)) : ""}</span>
          <span style="flex:1"></span>
          ${t.enabled ? "" : `<span style="font-size:11px;color:var(--warn,#d97706)">aus</span>`}
          <button class="btn btn-sm" data-hits="${esc(t.id)}">Treffer</button>
          <button class="btn btn-sm btn-danger" data-del="${esc(t.id)}">Löschen</button></div>
        ${linkField("Tracker-Link", t.url)}
        ${t.redirect ? linkField("Weiterleitung", t.redirect) : `<div style="font-size:11px;color:var(--warn,#d97706)">Kein Ziel – zeigt eine leere Seite.</div>`}
      </div>`).join("") : `<div style="color:var(--subtext)">Noch keine Tracker-Links.</div>`;
    list.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copy(b.dataset.copy)));
    list.querySelectorAll("[data-hits]").forEach((b) => b.addEventListener("click", () => {
      const t = d.trackers.find((x) => x.id === b.dataset.hits); showHits(t.id, t.name);
    }));
    list.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm("Tracker löschen?", { okText: "Löschen", danger: true }))) return;
      try { await cq(`/trackers/${b.dataset.del}`, { method: "DELETE" }); draw(); } catch (e) { window.notify?.(e.message, "error"); }
    }));
  };
  body.querySelector("[data-add]").addEventListener("click", async () => {
    const name = body.querySelector("[data-name]").value.trim();
    const red = body.querySelector("[data-red]").value.trim();
    if (!name) { window.notify?.("Name fehlt", "warning"); return; }
    try { await cq("/trackers", { method: "POST", body: { name, redirect: red } });
      body.querySelector("[data-name]").value = ""; body.querySelector("[data-red]").value = ""; draw();
      window.notify?.("Tracker angelegt", "success", 2000);
    } catch (e) { window.notify?.(e.message, "error"); }
  });
  draw();
}

// ================================================================= CANARY-TOKENS
const KIND_LABEL = { web: "Web-Link", email: "E-Mail-Pixel", file: "Köder-Datei", qr: "QR-Code", dns: "DNS" };
const KIND_HINT = {
  web: "Link, der bei jedem Aufruf Alarm schlägt (optional danach weiterleiten).",
  email: "Unsichtbares Bild für E-Mail-Signaturen/Mails – meldet das Öffnen der Mail.",
  file: "Word-/PDF-/Excel-Datei, die beim Öffnen nach Hause telefoniert.",
  qr: "QR-Code, der bei jedem Scan Alarm schlägt.",
  dns: "Schlägt an, sobald die Domain irgendwo aufgelöst wird (NS-Delegation nötig).",
};

export async function renderCanary(body) {
  body.innerHTML = `<div style="padding:14px 16px;font-size:13px">
    <p style="color:var(--subtext);margin:0 0 10px">Ein <b>Canary-Token</b> ist ein Köder: wird er benutzt, bekommst du Alarm –
      im Dashboard und, mit Modul „Benachrichtigungen“, per Webhook/E-Mail.</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:6px">
      <label style="font-size:12px;color:var(--subtext)">Name<br><input data-name placeholder="z. B. Passwörter.docx" style="width:200px"></label>
      <label style="font-size:12px;color:var(--subtext)">Art<br><select data-kind>${Object.entries(KIND_LABEL).map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select></label>
      <label data-row="file" style="font-size:12px;color:var(--subtext)">Dateityp<br><select data-fk><option value="docx">Word (.docx)</option><option value="pdf">PDF</option><option value="xlsx">Excel (.xlsx)</option></select></label>
      <label data-row="dns" style="font-size:12px;color:var(--subtext)">Domain<br><input data-host placeholder="canary.firma.de" style="width:200px"></label>
      <label data-row="redirect" style="font-size:12px;color:var(--subtext)">Weiterleiten (optional)<br><input data-red placeholder="https://…" style="width:200px"></label>
      <button class="btn-primary btn-sm" data-add>＋ Token</button>
    </div>
    <div data-hint style="font-size:12px;color:var(--subtext);margin-bottom:12px"></div>
    <div data-list>Lade …</div></div>`;
  const kindSel = body.querySelector("[data-kind]");
  const sync = () => {
    const k = kindSel.value;
    body.querySelector("[data-hint]").textContent = KIND_HINT[k] || "";
    body.querySelector('[data-row="file"]').style.display = k === "file" ? "" : "none";
    body.querySelector('[data-row="dns"]').style.display = k === "dns" ? "" : "none";
    body.querySelector('[data-row="redirect"]').style.display = (k === "web" || k === "qr") ? "" : "none";
  };
  kindSel.addEventListener("change", sync); sync();

  const list = body.querySelector("[data-list]");
  const draw = async () => {
    let d; try { d = await cq("/tokens"); } catch (e) { list.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    list.innerHTML = d.tokens.length ? d.tokens.map((t) => tokenCard(t)).join("") : `<div style="color:var(--subtext)">Noch keine Tokens.</div>`;
    list.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copy(b.dataset.copy)));
    list.querySelectorAll("[data-hits]").forEach((b) => b.addEventListener("click", () => {
      const t = d.tokens.find((x) => x.id === b.dataset.hits); showHits(t.id, t.name);
    }));
    list.querySelectorAll("[data-qr]").forEach((b) => b.addEventListener("click", () => window.open(`/api/canary/tokens/${b.dataset.qr}/qr.png?_=${Date.now()}`, "_blank")));
    list.querySelectorAll("[data-dl]").forEach((b) => b.addEventListener("click", () => window.open(b.dataset.dl, "_blank")));
    list.querySelectorAll("[data-snip]").forEach((b) => b.addEventListener("click", async () => {
      try { const s = await cq(`/tokens/${b.dataset.snip}/snippets`); copy(s.html || s.url || s.download_url || s.host || ""); }
      catch (e) { window.notify?.(e.message, "error"); }
    }));
    list.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm("Token löschen?", { okText: "Löschen", danger: true }))) return;
      try { await cq(`/tokens/${b.dataset.del}`, { method: "DELETE" }); draw(); } catch (e) { window.notify?.(e.message, "error"); }
    }));
  };
  body.querySelector("[data-add]").addEventListener("click", async () => {
    const name = body.querySelector("[data-name]").value.trim();
    const kind = kindSel.value;
    if (!name) { window.notify?.("Name fehlt", "warning"); return; }
    const payload = { name, kind, redirect: body.querySelector("[data-red]").value.trim(),
                      host: body.querySelector("[data-host]").value.trim(), file_kind: body.querySelector("[data-fk]").value };
    try { await cq("/tokens", { method: "POST", body: payload });
      body.querySelector("[data-name]").value = ""; body.querySelector("[data-host]").value = ""; body.querySelector("[data-red]").value = "";
      draw(); window.notify?.("Token angelegt", "success", 2000);
    } catch (e) { window.notify?.(e.message, "error", 7000); }
  });
  draw();
}

function tokenCard(t) {
  const head = `<div style="display:flex;align-items:center;gap:8px">
    <b>${esc(t.name)}</b> <span style="font-size:11px;border:1px solid var(--border);border-radius:999px;padding:1px 8px;color:var(--subtext)">${esc(KIND_LABEL[t.kind] || t.kind)}</span>
    <span style="font-size:11px;color:var(--subtext)">${t.count || 0} Treffer${t.last ? " · zuletzt " + esc(fmt(t.last)) : ""}</span>
    <span style="flex:1"></span>
    <button class="btn btn-sm" data-hits="${esc(t.id)}">Treffer</button>
    <button class="btn btn-sm btn-danger" data-del="${esc(t.id)}">Löschen</button></div>`;
  let detail = "";
  if (t.kind === "web") detail = linkField("Link", t.url);
  else if (t.kind === "email") detail = linkField("Pixel-URL", t.url) +
    `<div><button class="btn btn-sm" data-snip="${esc(t.id)}">HTML-Schnipsel kopieren</button></div>`;
  else if (t.kind === "qr") detail = linkField("Link", t.url) +
    `<div><button class="btn btn-sm" data-qr="${esc(t.id)}">QR-Bild öffnen</button></div>`;
  else if (t.kind === "file") detail = linkField("Download", t.url) +
    `<div><button class="btn btn-sm" data-dl="${esc(t.url)}">Datei herunterladen</button>
      <span style="font-size:11px;color:var(--subtext);margin-left:6px">${esc(t.file_name || "")}</span></div>`;
  else if (t.kind === "dns") detail = linkField("Domain", t.url) +
    `<div style="font-size:11px;color:var(--subtext)">Diese Domain per NS-Eintrag auf diesen Server delegieren.</div>`;
  return `<div style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;margin-bottom:8px">${head}
    ${t.note ? `<div style="font-size:12px;color:var(--subtext);margin:2px 0">${esc(t.note)}</div>` : ""}${detail}</div>`;
}

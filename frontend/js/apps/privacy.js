// apps/privacy.js
// ---------------
// Datenschutz-Zentrale. Zwei Ebenen in einem Fenster:
//
//   "Meine Daten"  - für JEDEN eingeloggten Benutzer. Auskunft nach Art. 15,
//                    Export nach Art. 20, Löschantrag nach Art. 17. Diese
//                    Rechte stehen Betroffenen zu, sie sind kein Admin-Feature.
//   "Verwaltung"   - nur mit Recht 'manage_privacy': Aufbewahrungsfristen,
//                    Bestandsübersicht, Löschanträge, Auskunft/Löschung für
//                    andere Personen.
//
// Bewusst NICHT unter "Einstellungen" versteckt: wer seine Betroffenenrechte
// wahrnehmen will, soll sie finden, ohne Admin-Bereiche zu durchsuchen.

import { api } from "../api.js";
import { esc, uiConfirm, uiPrompt } from "../utils.js";
import { state, hasGlobalPerm, isAdmin } from "../state.js";

const fmtTs = (ms) => ms ? new Date(Number(ms)).toLocaleString("de-DE") : "—";
const fmtBytes = (b) => {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(b) / Math.log(1024));
  return `${(b / Math.pow(1024, i)).toFixed(1)} ${u[i]}`;
};

export function renderPrivacy(body, win) {
  const canManage = isAdmin() || hasGlobalPerm("manage_privacy");
  let tab = "me";

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
      <div class="explorer-toolbar" style="gap:6px">
        <button class="tab-btn" data-tab="me">🙋 Meine Daten</button>
        ${canManage ? `
          <button class="tab-btn" data-tab="retention">⏳ Aufbewahrung</button>
          <button class="tab-btn" data-tab="report">📊 Bestand</button>
          <button class="tab-btn" data-tab="requests">📨 Löschanträge</button>
          <button class="tab-btn" data-tab="users">👤 Personen</button>` : ""}
      </div>
      <div id="pv-body" style="flex:1;overflow:auto;padding:14px 16px 20px"></div>
    </div>`;

  const view = body.querySelector("#pv-body");
  body.querySelectorAll("[data-tab]").forEach((b) => {
    b.addEventListener("click", () => { tab = b.dataset.tab; draw(); });
  });

  const box = (title, inner, note = "") => `
    <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;
         padding:13px 15px;margin-bottom:12px">
      <strong style="font-size:13px;display:block;margin-bottom:${note ? "4px" : "9px"}">${title}</strong>
      ${note ? `<div style="font-size:11px;color:var(--subtext);margin-bottom:9px;line-height:1.45">${note}</div>` : ""}
      ${inner}
    </div>`;

  function draw() {
    body.querySelectorAll("[data-tab]").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === tab);
    });
    view.innerHTML = `<div style="color:var(--subtext);font-size:13px">Lädt…</div>`;
    ({ me: drawMe, retention: drawRetention, report: drawReport,
       requests: drawRequests, users: drawUsers }[tab] || drawMe)();
  }

  const fail = (e) => {
    view.innerHTML = `<div style="color:var(--danger);font-size:13px">${esc(e.message)}</div>`;
  };

  // ---------------------------------------------------------------
  // Meine Daten
  // ---------------------------------------------------------------

  async function drawMe() {
    let data, reqs;
    try {
      [data, reqs] = await Promise.all([
        api.getMyPrivacyData(),
        api.getMyErasureRequests().catch(() => []),
      ]);
    } catch (e) { return fail(e); }

    const cats = data.kategorien || [];
    const open = (reqs || []).find((r) => r.status === "open");

    view.innerHTML = `
      ${box("Welche Daten sind zu mir gespeichert?",
        cats.length ? `<table style="width:100%;border-collapse:collapse;font-size:12px">
          ${cats.map((c) => `<tr>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${esc(c.label)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);text-align:right;
                color:var(--subtext)">${c.count} Einträge</td></tr>`).join("")}
        </table>`
        : `<div style="font-size:12px;color:var(--subtext)">Außer dem Benutzerkonto liegt nichts vor.</div>`,
        "Auskunft nach Art. 15 DSGVO. Der Export unten enthält diese Daten vollständig und maschinenlesbar (Art. 20).")}

      ${box("Daten exportieren",
        `<button class="btn-primary" id="pv-export" style="width:auto;margin:0">⬇ Auskunft als JSON herunterladen</button>`,
        "Enthält alle zu dir gespeicherten Daten. Passwort-Hashes sind bewusst ausgenommen – sie sind kein Auskunftsgegenstand und ihre Herausgabe wäre ein Sicherheitsrisiko.")}

      ${box("Löschung beantragen",
        open
          ? `<div style="font-size:12px;color:var(--subtext)">
               Antrag vom ${fmtTs(open.created_at)} liegt vor und wird geprüft.</div>`
          : `<div style="display:flex;gap:8px;flex-wrap:wrap">
               <button class="taskbar-btn" id="pv-req-content">Nur meine Inhalte löschen</button>
               <button class="taskbar-btn" id="pv-req-account" style="border-color:var(--danger);color:var(--danger)">Konto vollständig löschen</button>
             </div>`,
        "Art. 17 DSGVO. Der Antrag wird nicht sofort ausgeführt, sondern geprüft – Art. 17 Abs. 3 lässt Ausnahmen zu, etwa wenn Nachweispflichten entgegenstehen. Bearbeitung innerhalb eines Monats (Art. 12 Abs. 3).")}

      ${(reqs || []).length ? box("Meine Anträge",
        `<table style="width:100%;border-collapse:collapse;font-size:12px">
          ${reqs.map((r) => `<tr>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${fmtTs(r.created_at)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${r.kind === "account" ? "Konto" : "Inhalte"}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${statusLabel(r.status)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${esc(r.note || "")}</td>
          </tr>`).join("")}
        </table>`) : ""}`;

    view.querySelector("#pv-export").addEventListener("click", async () => {
      try { await api.downloadPrivacyExport(null); }
      catch (e) { alert(e.message); }
    });
    const req = async (kind) => {
      const label = kind === "account" ? "das gesamte Konto" : "alle eigenen Inhalte";
      if (!await uiConfirm(`Löschung für ${label} beantragen?`, {
        description: "Der Antrag wird von der Verwaltung geprüft und innerhalb eines Monats beantwortet.",
        danger: kind === "account" })) return;
      const reason = await uiPrompt("Grund (optional)", { placeholder: "z.B. Austritt aus dem Unternehmen" });
      if (reason === null) return;
      try { await api.requestErasure(kind, reason || ""); } catch (e) { return alert(e.message); }
      draw();
    };
    view.querySelector("#pv-req-content")?.addEventListener("click", () => req("content"));
    view.querySelector("#pv-req-account")?.addEventListener("click", () => req("account"));
  }

  const statusLabel = (s) => ({
    open: "⏳ offen", done: "✅ erledigt", rejected: "⛔ abgelehnt",
  }[s] || s);

  // ---------------------------------------------------------------
  // Aufbewahrungsfristen
  // ---------------------------------------------------------------

  async function drawRetention() {
    let rep;
    try { rep = await api.getPrivacyReport(); } catch (e) { return fail(e); }

    view.innerHTML = `
      ${box("Aufbewahrungsfristen",
        `<div style="display:flex;flex-direction:column;gap:11px">
          ${rep.retention.map((r) => `
            <div>
              <div style="display:flex;align-items:center;gap:9px">
                <label style="flex:1;font-size:12px">${esc(r.label)}</label>
                <input type="number" min="0" data-ret="${esc(r.key)}" value="${r.value}"
                  style="width:78px;padding:4px 7px;border-radius:6px;border:1px solid var(--border);
                  background:var(--panel);color:var(--text);font-size:12px;text-align:right">
                <span style="font-size:11px;color:var(--subtext);width:38px">${r.unit === "hours" ? "Std." : "Tage"}</span>
              </div>
              ${r.note ? `<div style="font-size:10px;color:var(--subtext);margin-top:2px;
                    line-height:1.4">${esc(r.note)}</div>` : ""}
            </div>`).join("")}
        </div>
        <div style="display:flex;gap:8px;margin-top:13px">
          <button class="btn-primary" id="pv-save" style="width:auto;margin:0">Speichern</button>
          <button class="taskbar-btn" id="pv-purge">🧹 Jetzt anwenden</button>
        </div>`,
        "Art. 5 Abs. 1 lit. e DSGVO – Daten dürfen nur so lange gespeichert werden, wie sie gebraucht werden. <b>0 = unbegrenzt</b>; das sollte eine begründete Entscheidung sein, kein Versehen. Die Fristen werden täglich automatisch angewendet.")}

      ${box("Letzter Durchlauf",
        `<div style="font-size:12px;color:var(--subtext)">${fmtTs(rep.last_purge)}</div>`)}`;

    view.querySelector("#pv-save").addEventListener("click", async () => {
      const values = {};
      view.querySelectorAll("[data-ret]").forEach((i) => {
        values[i.dataset.ret] = parseInt(i.value, 10) || 0;
      });
      try {
        await api.setRetention(values);
        window.notify?.("Fristen gespeichert", "success");
      } catch (e) { alert(e.message); }
      draw();
    });
    view.querySelector("#pv-purge").addEventListener("click", async () => {
      if (!await uiConfirm("Aufbewahrungsfristen jetzt anwenden?", {
        description: "Abgelaufene Daten werden endgültig entfernt bzw. anonymisiert.",
        danger: true })) return;
      try {
        const r = await api.runPrivacyPurge();
        const lines = Object.entries(r).map(([k, v]) => `${k}: ${v}`).join("\n");
        await uiConfirm("Durchlauf abgeschlossen", { description: lines, okText: "OK" });
      } catch (e) { alert(e.message); }
      draw();
    });
  }

  // ---------------------------------------------------------------
  // Bestandsübersicht
  // ---------------------------------------------------------------

  async function drawReport() {
    let rep;
    try { rep = await api.getPrivacyReport(); } catch (e) { return fail(e); }

    view.innerHTML = `
      ${box("Aufzeichnungs-Dateien",
        `<div style="font-size:12px">${rep.recording_files} Dateien · ${fmtBytes(rep.recording_bytes)}</div>`,
        "Bildschirm- und Terminal-Mitschnitte sind der eingriffsintensivste Datenbestand des Systems. Sie zeigen konkretes Verhalten am Arbeitsplatz – kurze Fristen und ein enger Kreis Zugriffsberechtigter sind hier wichtiger als irgendwo sonst.")}

      ${box("Datenbestände mit Personenbezug",
        `<table style="width:100%;border-collapse:collapse;font-size:12px">
          <tr style="color:var(--subtext);font-size:11px;text-align:left">
            <th style="padding:3px 0">Bereich</th><th>Einträge</th>
            <th>Ältester</th><th>Bei Löschung</th></tr>
          ${rep.items.filter((i) => i.count).map((i) => `<tr>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${esc(i.label)}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border)">${i.count}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${i.oldest ? fmtTs(i.oldest) : "—"}</td>
            <td style="padding:4px 0;border-bottom:1px solid var(--border);color:var(--subtext)">${i.strategy === "delete" ? "gelöscht" : "anonymisiert"}</td>
          </tr>`).join("")}
        </table>`,
        "Diese Übersicht hilft beim Verzeichnis der Verarbeitungstätigkeiten (Art. 30) – ersetzt es aber nicht: dort gehören zusätzlich Zweck, Rechtsgrundlage und Empfänger je Verarbeitung hinein.")}`;
  }

  // ---------------------------------------------------------------
  // Löschanträge
  // ---------------------------------------------------------------

  async function drawRequests() {
    let reqs;
    try { reqs = await api.getErasureRequests(); } catch (e) { return fail(e); }

    if (!reqs.length) {
      view.innerHTML = box("Löschanträge",
        `<div style="font-size:12px;color:var(--subtext)">Keine Anträge.</div>`);
      return;
    }

    view.innerHTML = reqs.map((r) => box(
      `${esc(r.username)} · ${r.kind === "account" ? "Konto" : "Inhalte"} · ${statusLabel(r.status)}`,
      `<div style="font-size:12px;color:var(--subtext);margin-bottom:8px">
         Eingegangen: ${fmtTs(r.created_at)}
         ${r.reason ? `<br>Grund: ${esc(r.reason)}` : ""}
         ${r.note ? `<br>Entscheidung: ${esc(r.note)}` : ""}
       </div>
       ${r.status === "open" ? `<div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="taskbar-btn" data-erase="${esc(r.user_id)}" data-name="${esc(r.username)}"
                  style="border-color:var(--danger);color:var(--danger)">Löschung ausführen</button>
          <button class="taskbar-btn" data-reject="${esc(r.id)}">Ablehnen</button>
        </div>` : ""}`)).join("");

    view.querySelectorAll("[data-erase]").forEach((b) => {
      b.addEventListener("click", () => eraseFlow(b.dataset.erase, b.dataset.name));
    });
    view.querySelectorAll("[data-reject]").forEach((b) => {
      b.addEventListener("click", async () => {
        const note = await uiPrompt("Begründung der Ablehnung", {
          description: "Art. 12 Abs. 4 DSGVO – Betroffene müssen die Gründe erfahren. Pflichtfeld." });
        if (note === null) return;
        if (!note.trim()) return alert("Eine Begründung ist erforderlich.");
        try { await api.resolveErasureRequest(b.dataset.reject, "rejected", note.trim()); }
        catch (e) { return alert(e.message); }
        draw();
      });
    });
  }

  // ---------------------------------------------------------------
  // Personen: Auskunft & Löschung für andere
  // ---------------------------------------------------------------

  async function drawUsers() {
    let users;
    try { users = await api.getUsers(); } catch (e) { return fail(e); }

    view.innerHTML = box("Auskunft & Löschung für einzelne Personen",
      `<table style="width:100%;border-collapse:collapse;font-size:12px">
        ${users.map((u) => `<tr>
          <td style="padding:5px 0;border-bottom:1px solid var(--border)">
            ${esc(u.display_name || u.username)}
            <span style="color:var(--subtext)">(${esc(u.username)})</span></td>
          <td style="padding:5px 0;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap">
            <button class="taskbar-btn" data-exp="${esc(u.id)}" style="padding:1px 7px;font-size:11px">⬇ Auskunft</button>
            ${u.id === state.user?.id ? "" :
              `<button class="taskbar-btn" data-del="${esc(u.id)}" data-name="${esc(u.username)}"
                 style="padding:1px 7px;font-size:11px;border-color:var(--danger);color:var(--danger)">Löschen</button>`}
          </td></tr>`).join("")}
      </table>`,
      "Jeder Zugriff auf fremde Daten wird im Audit-Log festgehalten. Auskunft nur auf tatsächliches Verlangen der betroffenen Person erteilen – sie ist kein allgemeines Einsichtsrecht.");

    view.querySelectorAll("[data-exp]").forEach((b) => {
      b.addEventListener("click", async () => {
        try { await api.downloadPrivacyExport(b.dataset.exp); }
        catch (e) { alert(e.message); }
      });
    });
    view.querySelectorAll("[data-del]").forEach((b) => {
      b.addEventListener("click", () => eraseFlow(b.dataset.del, b.dataset.name));
    });
  }

  async function eraseFlow(userId, username) {
    const mode = await uiConfirm(`„${username}" löschen – wie?`, {
      description:
        "ANONYMISIEREN (empfohlen): Datensätze mit Nachweisfunktion bleiben erhalten, " +
        "der Bezug zur Person wird gekappt. Deckt Art. 17 ab und respektiert die " +
        "Ausnahmen aus Abs. 3.\n\n" +
        "VOLLSTÄNDIG: alles wird entfernt, auch Audit-Einträge dieser Person. " +
        "Nur wählen, wenn keine Nachweispflicht entgegensteht.",
      okText: "Anonymisieren", cancelText: "Vollständig löschen" });
    // uiConfirm liefert true = OK-Button (anonymisieren), false = Abbruch.
    // Für "vollständig" wird bewusst ein zweiter, expliziter Schritt verlangt.
    const hard = mode === false
      ? await uiConfirm("Wirklich VOLLSTÄNDIG löschen?", {
          description: "Unumkehrbar. Auch Audit-Einträge dieser Person verschwinden.",
          danger: true })
      : false;
    if (mode === false && !hard) return;

    const typed = await uiPrompt("Zur Bestätigung den Benutzernamen eingeben", {
      description: `Erwartet: ${username}`, placeholder: username });
    if (typed === null) return;
    if (typed.trim() !== username) return alert("Benutzername stimmt nicht – abgebrochen.");

    try {
      const r = await api.eraseUser(userId, hard ? "hard" : "anonymize", username);
      const lines = Object.entries(r).map(([k, v]) => `${k}: ${v}`).join("\n");
      await uiConfirm("Löschung ausgeführt", { description: lines, okText: "OK" });
    } catch (e) { return alert(e.message); }
    draw();
  }

  draw();
}

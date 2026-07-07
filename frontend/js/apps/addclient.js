// apps/addclient.js
// -----------------
// Fenster "Client hinzufügen". Erzeugt einen Onboarding-Token (optional an
// Tenant/Standort/Wunschname gebunden) und zeigt dann drei Installationswege.

import { state } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";

export function renderAddClient(body, win) {
  const tenantOptions = state.hierarchy.tenants
    .map((t) => `<option value="${t.id}">${esc(t.name)}</option>`)
    .join("");

  body.innerHTML = `
    <div class="settings-section">
      <h3>Neuen Client hinzufügen</h3>

      <div class="panel" style="background:var(--panel-2);font-size:13px;color:var(--subtext);line-height:1.5">
        <b style="color:var(--text)">Was passiert bei der Installation?</b>
        <ol style="margin:8px 0 0;padding-left:18px">
          <li>Der Agent wird nach <code>C:\\Program Files\\RapalleRmmAgent</code> (Windows) bzw.
              <code>/opt/rapalle-agent</code> (Linux) installiert.</li>
          <li>Fehlt <b>Python</b>, wird es automatisch mitinstalliert (Windows: über winget
              bzw. den offiziellen Installer; klappt das nicht, wird ein Download-Link gezeigt).</li>
          <li>Die nötigen Bibliotheken werden in einer eigenen Umgebung installiert.</li>
          <li>Der Agent wird als <b>unsichtbarer Autostart</b> eingerichtet und startet sofort —
              du kannst das Installationsfenster danach schließen.</li>
          <li>Der Client meldet sich dann selbstständig hier im Dashboard an.</li>
        </ol>
      </div>

      <div class="form-row" style="margin-top:14px">
        <label>Wunschname für den Client (optional)</label>
        <input type="text" id="ac-name" placeholder="leer lassen = echter Hostname des Geräts" />
      </div>
      <div class="form-row">
        <label>Tenant (optional)</label>
        <select id="ac-tenant"><option value="">— nicht zuordnen (&rarr; „Uncategorized")</option>${tenantOptions}</select>
      </div>
      <div class="form-row">
        <label>Standort (optional)</label>
        <select id="ac-location"><option value="">— keiner —</option></select>
      </div>
      <p style="color:var(--subtext);font-size:12px;margin:4px 0 0">
        Ohne Tenant/Standort landet der Client automatisch im Tenant <b>„Uncategorized"</b>
        und kann später jederzeit per „Bearbeiten" verschoben werden.
      </p>

      <button class="btn-primary" id="ac-generate" style="margin-top:12px">Installations-Link erzeugen</button>

      <div id="ac-result" style="margin-top:20px"></div>
    </div>
  `;

  const tenantSel = body.querySelector("#ac-tenant");
  const locationSel = body.querySelector("#ac-location");

  tenantSel.addEventListener("change", () => {
    locationSel.innerHTML = `<option value="">— keiner —</option>` +
      state.hierarchy.locations
        .filter((l) => l.tenant_id === tenantSel.value)
        .map((l) => `<option value="${l.id}">${esc(l.name)}</option>`)
        .join("");
  });

  body.querySelector("#ac-generate").addEventListener("click", async () => {
    const resultEl = body.querySelector("#ac-result");
    const clientName = body.querySelector("#ac-name").value.trim() || null;
    resultEl.innerHTML = `<span style="color:var(--subtext)">Erzeuge Token...</span>`;
    try {
      const res = await api.createEnrollmentToken(tenantSel.value || null, locationSel.value || null, clientName);
      // Backend liefert jetzt ABSOLUTE URLs (aus den Server-Einstellungen) -
      // damit steht nie mehr "localhost" im Link, egal wie das Dashboard
      // geöffnet wurde. Fallback für alte Backends: origin voranstellen.
      const abs = (u) => (u && u.startsWith("http") ? u : window.location.origin + u);
      const landingUrl = abs(res.landing_url);
      const installShUrl = abs(res.install_sh_url);
      const installPs1Url = abs(res.install_ps1_url);
      const downloadUrl = abs(res.download_url);

      // Kleiner Helfer: <pre> mit Copy-Button daneben
      const copyRow = (text, idx) => `
        <div style="display:flex;gap:6px;align-items:stretch">
          <pre style="flex:1;background:var(--panel-2);padding:10px;border-radius:6px;overflow-x:auto;margin:0">${esc(text)}</pre>
          <button class="action-btn" data-copy="${idx}" title="In die Zwischenablage kopieren" style="align-self:center">📋</button>
        </div>`;
      const copyTexts = [
        landingUrl,
        `curl -sSL ${installShUrl} | sudo bash`,
        `iwr ${installPs1Url} -UseBasicParsing | iex`,
      ];

      resultEl.innerHTML = `
        <h3 style="font-size:13px">Weg 1 — Download-Seite (Link weitergeben)</h3>
        ${copyRow(copyTexts[0], 0)}

        <h3 style="font-size:13px;margin-top:16px">Weg 2 — Ein-Zeiler Linux (als root)</h3>
        ${copyRow(copyTexts[1], 1)}

        <h3 style="font-size:13px;margin-top:16px">Weg 2 — Ein-Zeiler Windows (PowerShell als Admin)</h3>
        ${copyRow(copyTexts[2], 2)}
        <p style="color:var(--subtext);font-size:12px;margin-top:4px">
          Der Windows-Installer installiert Python bei Bedarf selbst und richtet den
          unsichtbaren Autostart ein. Danach kannst du die PowerShell einfach schließen.
        </p>

        <h3 style="font-size:13px;margin-top:16px">Weg 3 — Direkter Download</h3>
        <a class="btn-primary" style="display:inline-block;width:auto;text-decoration:none"
           href="${esc(downloadUrl)}">Agent .zip herunterladen</a>
      `;

      // Copy-Handler (navigator.clipboard braucht HTTPS/localhost -> Fallback
      // über verstecktes Textarea + execCommand für HTTP-Setups)
      resultEl.querySelectorAll("[data-copy]").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const text = copyTexts[Number(btn.dataset.copy)];
          let ok = false;
          try {
            await navigator.clipboard.writeText(text);
            ok = true;
          } catch {
            try {
              const ta = document.createElement("textarea");
              ta.value = text;
              ta.style.cssText = "position:fixed;opacity:0";
              document.body.appendChild(ta);
              ta.select();
              ok = document.execCommand("copy");
              ta.remove();
            } catch { ok = false; }
          }
          btn.textContent = ok ? "✓" : "✗";
          setTimeout(() => { btn.textContent = "📋"; }, 1500);
        })
      );
    } catch (e) {
      resultEl.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
    }
  });
}

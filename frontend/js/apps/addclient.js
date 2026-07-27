// apps/addclient.js
// -----------------
// Fenster "Client hinzufügen". Erzeugt einen Onboarding-Token (optional an
// Tenant/Standort/Wunschname gebunden) und zeigt dann drei Installationswege.

import { state } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";
import { helpDot, condenseHints } from "../help.js";
// t() unter Alias: "t" ist hier bereits als lokaler Variablenname belegt.
import { t as tr } from "../i18n.js";

export function renderAddClient(body, win) {
  const tenantOptions = state.hierarchy.tenants
    .map((t) => `<option value="${t.id}">${esc(t.name)}</option>`)
    .join("");

  body.innerHTML = `
    <div class="settings-section">
      <h3>Neuen Client hinzufügen ${helpDot(
        "Was bei der Installation passiert: Der Agent wird nach " +
        "C:\\Program Files\\RapalleRmmAgent (Windows) bzw. /opt/rapalle-rmm-agent (Linux) " +
        "installiert. Fehlt Python, wird es automatisch mitinstalliert. Die nötigen " +
        "Bibliotheken kommen in eine eigene Umgebung. Der Agent wird als Dienst " +
        "eingerichtet, der beim Booten startet - auch ohne Anmeldung - und meldet sich " +
        "danach selbstständig hier im Dashboard an."
      )}</h3>

      <div class="form-row" style="margin-top:14px">
        <label>Wunschname für den Client (optional)</label>
        <input type="text" id="ac-name" placeholder="${tr("u_leer_lassen_echter_hostname_des_ge")}" />
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

  condenseHints(body);

  const tenantSel = body.querySelector("#ac-tenant");
  // Live-Refresh: Wird irgendwo ein Tenant/Standort angelegt/gelöscht
  // (Hierarchie ändert sich), die Auswahl-Listen SOFORT neu befüllen -
  // die aktuelle Auswahl bleibt erhalten. Kein Fenster-Neuöffnen mehr nötig.
  const onHierarchyChanged = () => {
    if (!document.body.contains(body)) {
      window.removeEventListener("rmm:hierarchy-changed", onHierarchyChanged);
      return;
    }
    const cur = tenantSel.value;
    tenantSel.innerHTML = `<option value="">— nicht zuordnen (&rarr; „Uncategorized")</option>` +
      state.hierarchy.tenants.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
    if ([...tenantSel.options].some((o) => o.value === cur)) tenantSel.value = cur;
    tenantSel.dispatchEvent(new Event("change"));   // Standort-Liste nachziehen
  };
  window.addEventListener("rmm:hierarchy-changed", onHierarchyChanged);
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

        <h3 style="font-size:13px;margin-top:16px">Weg 4 — Fertiges Installationspaket</h3>
        <div id="ac-installers" style="font-size:12px;color:var(--subtext)">Lade Pakete…</div>
      `;

      // Weg 4 nachladen: fertige .exe/.msi/.deb/.rpm/.run aus dem dist-Ordner.
      renderInstallers(resultEl.querySelector("#ac-installers"), res.token, abs,
                       res.base_url || window.location.origin);

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


// ---------------------------------------------------------------
// Weg 4: fertige Installationspakete (.exe / .msi / .deb / .rpm / .run)
//
// Gebaut werden sie serverseitig mit tools/build_installers.py; sie liegen im
// Ordner dist/. Der Onboarding-Token steckt NICHT im Paket (das wäre pro Token
// ein eigener Build) - er wird dem Installer beim Aufruf mitgegeben, damit das
// Gerät direkt beim gewählten Tenant/Standort landet.
// ---------------------------------------------------------------
function fmtSize(bytes) {
  return bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + " MB"
                          : Math.round(bytes / 1024) + " KB";
}

// Passender Aufrufbefehl je Paketart - inklusive Onboarding-Token, damit das
// Gerät direkt beim gewählten Tenant/Standort landet.
function installHint(name, token, backendUrl) {
  const n = name.toLowerCase();
  if (n.endsWith(".exe")) return `RapalleRmmAgent-Setup.exe /S /TOKEN=${token}`;
  // MSI: der Windows Installer reicht eigene Eigenschaften nicht an die
  // (deferred) Aktion durch -> das Gerät landet in "Uncategorized".
  if (n.endsWith(".msi")) return `msiexec /i ${name} /qn   (→ Uncategorized)`;
  if (n.endsWith(".bat")) return `${name} ${backendUrl} ${token}`;
  if (n.endsWith(".ps1")) return `powershell -ExecutionPolicy Bypass -File ${name} -Token ${token}`;
  if (n.endsWith(".pkg.tar.xz")) return `sudo RMM_ENROLLMENT_TOKEN=${token} pacman -U ${name}`;
  if (n.endsWith(".deb")) return `sudo RMM_ENROLLMENT_TOKEN=${token} apt install ./${name}`;
  if (n.endsWith(".rpm")) return `sudo RMM_ENROLLMENT_TOKEN=${token} dnf install ./${name}`;
  if (n.endsWith(".tar.gz") || n.endsWith(".tgz"))
    return `tar xzf ${name} && cd */ && sudo RMM_ENROLLMENT_TOKEN=${token} ./install.sh`;
  return `sudo RMM_ENROLLMENT_TOKEN=${token} ./${name}`;
}

async function renderInstallers(host, token, abs, backendUrl) {
  if (!host) return;
  let info;
  try {
    info = await api.listInstallers();
  } catch {
    host.textContent = "Installationspakete werden von diesem Backend noch nicht unterstützt.";
    return;
  }

  const list = info.installers || [];
  const rows = list.map((p) => {
    const url = abs(`/enroll/${token}/installer/${encodeURIComponent(p.name)}`);
    const hint = installHint(p.name, token, backendUrl);
    return `
      <div style="display:flex;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">
        <span style="font-size:16px">${p.icon || "📦"}</span>
        <div style="flex:1;min-width:0">
          <div style="color:var(--text)">${esc(p.label || p.name)}</div>
          <code style="font-size:11px">${esc(hint)}</code>
        </div>
        <span style="white-space:nowrap">${fmtSize(p.size || 0)}</span>
        <a class="action-btn" style="text-decoration:none" href="${esc(url)}">⬇ Laden</a>
      </div>`;
  }).join("");

  const buildable = Object.entries(info.can_build || {})
    .filter(([, v]) => v).map(([k]) => k);

  host.innerHTML = `
    ${list.length ? rows : `<p style="margin:0">Noch keine Pakete gebaut.</p>`}
    <p style="margin:8px 0 0">
      Baubar auf diesem Server: <b>${buildable.length ? esc(buildable.join(", ")) : "—"}</b>
    </p>
    ${info.build_available ? `
      <button class="action-btn" id="ac-build" style="margin-top:8px">🔨 Pakete jetzt bauen</button>
      <pre id="ac-build-log" style="display:none;margin-top:8px;max-height:220px;overflow:auto;
           background:var(--panel-2);padding:8px;border-radius:6px;font-size:11px"></pre>` : ""}
  `;

  const btn = host.querySelector("#ac-build");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const logEl = host.querySelector("#ac-build-log");
    btn.disabled = true;
    btn.textContent = "🔨 Baue… (kann 1-2 Minuten dauern)";
    logEl.style.display = "block";
    logEl.textContent = "Build läuft…";
    try {
      const r = await api.buildInstallers("auto");
      logEl.textContent = r.log || "(keine Ausgabe)";
      // Liste danach frisch aufbauen - der Build-Log bleibt darunter stehen.
      await renderInstallers(host, token, abs, backendUrl);
      const again = host.querySelector("#ac-build-log");
      if (again) { again.style.display = "block"; again.textContent = r.log || ""; }
    } catch (e) {
      logEl.textContent = e.message;
      btn.disabled = false;
      btn.textContent = "🔨 Pakete jetzt bauen";
    }
  });
}

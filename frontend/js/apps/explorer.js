// apps/explorer.js
// ----------------
// Vollwertiger Datei-Explorer. Läuft gegen einen Remote-Client (props.clientId)
// oder gegen den Backend-Server selbst (kein clientId).
//
// Funktionen:
//   - Navigieren (Doppelklick/Breadcrumb/Zurück/Aktualisieren)
//   - Spalten: Name · Größe · Geändert · Rechte (ls -al) · Besitzer:Gruppe
//   - Download, Upload (Auswahl + Drag & Drop)
//   - Ordner anlegen, Umbenennen, Löschen
//   - Textdateien editieren, Bilder ansehen
//   - "Relay"-Tab: Anleitung + fertige URL zum Netzlaufwerk-Verbinden

import { api } from "../api.js";
import { formatBytes, esc, uiConfirm, uiPrompt } from "../utils.js";
import { BACKEND_URL } from "../config.js";
import { isAdmin as userIsAdmin, state } from "../state.js";

const TEXT_EXT = new Set(["txt","log","md","json","xml","yml","yaml","ini","conf","cfg",
  "csv","js","ts","py","sh","bat","ps1","html","css","c","cpp","h","java","go","rs","sql","env"]);
const IMAGE_EXT = new Set(["png","jpg","jpeg","gif","webp","bmp","svg","ico"]);

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}
// Kopieren mit Fallback: navigator.clipboard existiert nur in sicheren
// Kontexten (HTTPS/localhost) - über plain HTTP greift der Textarea-Fallback.
function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  return new Promise((resolve, reject) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try { document.execCommand("copy") ? resolve() : reject(new Error("Kopieren nicht möglich")); }
    catch (err) { reject(err); } finally { ta.remove(); }
  });
}

function sep(path) { return path.includes("\\") ? "\\" : "/"; }
function joinPath(dir, name) {
  if (!dir) return name;
  const s = sep(dir);
  return dir.endsWith(s) ? dir + name : dir + s + name;
}
function parentOf(path) {
  const s = sep(path);
  const trimmed = path.replace(new RegExp(`\\${s}+$`), "");
  const idx = trimmed.lastIndexOf(s);
  return idx <= 0 ? "" : trimmed.slice(0, idx);
}

export function renderExplorer(body, win) {
  const { clientId, clientName } = win.props;
  const where = clientId ? clientName : "Server";
  const history = [""];
  let currentPath = "";
  let currentEntries = [];
  let mode = "files";

  const fs = clientId ? {
    list: (p) => api.listClientFs(clientId, p),
    read: (p) => api.readClientFile(clientId, p),
    write: (p, d) => api.writeClientFile(clientId, p, d),
    mkdir: (p) => api.mkdirClient(clientId, p),
    del: (p) => api.deleteClientPath(clientId, p),
    rename: (s, d) => api.renameClientPath(clientId, s, d),
  } : {
    list: (p) => api.listServerFs(p),
    read: (p) => api.readServerFile(p),
    write: (p, d) => api.writeServerFile(p, d),
    mkdir: (p) => api.mkdirServer(p),
    del: (p) => api.deleteServerPath(p),
    rename: (s, d) => api.renameServerPath(s, d),
  };

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar" style="gap:6px;flex-wrap:wrap">
        <button id="exp-tab-files-${win.key}" class="btn-primary" style="padding:4px 10px">📁 Dateien</button>
        ${clientId ? `<button id="exp-tab-relay-${win.key}" class="taskbar-btn">🔌 Relay (Netzlaufwerk)</button>` : ""}
      </div>

      <div id="exp-files-${win.key}" style="display:flex;flex-direction:column;flex:1;min-height:0">
        <div class="explorer-toolbar" style="flex-wrap:wrap;gap:6px">
          <button id="exp-back-${win.key}" title="Zurück">←</button>
          <button id="exp-refresh-${win.key}" title="Aktualisieren">🔄</button>
          <button id="exp-up-${win.key}" title="Übergeordneter Ordner">⬆️</button>
          <div id="exp-crumbs-${win.key}" title="Klicken, um den Pfad zu bearbeiten oder einzufügen" style="flex:1;color:var(--subtext);overflow-x:auto;white-space:nowrap;cursor:text"></div>
          <input id="exp-path-${win.key}" class="hidden" spellcheck="false" style="flex:1;font-family:monospace;font-size:12px;padding:4px 8px;border-radius:6px;border:1px solid var(--accent);background:var(--panel-2);color:var(--text)" />
          <button id="exp-copy-${win.key}" title="Aktuellen Pfad kopieren">📋</button>
          <button id="exp-mkdir-${win.key}" title="Neuer Ordner">➕📁</button>
          <button id="exp-upload-${win.key}" title="Datei hochladen">⬆️ Upload</button>
          <input type="file" id="exp-file-${win.key}" multiple style="display:none" />
        </div>
        <div id="exp-drop-${win.key}" style="flex:1;overflow:auto;position:relative">
          <table class="explorer-table">
            <thead>
              <tr>
                <th>Name</th>
                <th style="width:90px">Größe</th>
                <th style="width:150px">Geändert</th>
                <th style="width:110px">Rechte</th>
                <th style="width:130px">Besitzer</th>
                <th style="width:150px">Aktionen</th>
              </tr>
            </thead>
            <tbody id="exp-tbody-${win.key}"></tbody>
          </table>
        </div>
      </div>

      ${clientId ? `<div id="exp-relay-${win.key}" style="display:none;padding:16px;overflow:auto"></div>` : ""}
    </div>
  `;

  const tbody = body.querySelector(`#exp-tbody-${win.key}`);
  const crumbs = body.querySelector(`#exp-crumbs-${win.key}`);
  const dropZone = body.querySelector(`#exp-drop-${win.key}`);
  const fileInput = body.querySelector(`#exp-file-${win.key}`);
  const filesPane = body.querySelector(`#exp-files-${win.key}`);
  const relayPane = body.querySelector(`#exp-relay-${win.key}`);

  function renderCrumbs(path) {
    if (!path) { crumbs.innerHTML = `<b>${esc(where)}</b> : Laufwerke`; return; }
    const parts = path.split(sep(path)).filter(Boolean);
    crumbs.innerHTML = `<b>${esc(where)}</b> : ` + parts.map((p) => esc(p)).join(" › ");
  }

  // --- Pfad direkt bearbeiten/einfügen + kopieren ---
  const pathInput = body.querySelector(`#exp-path-${win.key}`);
  function openPathEdit() {
    pathInput.value = currentPath || "";
    crumbs.classList.add("hidden");
    pathInput.classList.remove("hidden");
    pathInput.focus(); pathInput.select();
  }
  function closePathEdit() {
    pathInput.classList.add("hidden");
    crumbs.classList.remove("hidden");
  }
  crumbs.addEventListener("click", openPathEdit);
  pathInput.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") { closePathEdit(); return; }
    if (e.key === "Enter") {
      const p = pathInput.value.trim();
      closePathEdit();
      load(p);   // leer = Laufwerksliste
    }
  });
  pathInput.addEventListener("blur", closePathEdit);
  body.querySelector(`#exp-copy-${win.key}`).addEventListener("click", () => {
    if (!currentPath) { window.notify?.("Kein Pfad geöffnet (Laufwerksliste).", "warning"); return; }
    copyToClipboard(currentPath).then(
      () => window.notify?.("Pfad kopiert", "success"),
      () => { openPathEdit(); window.notify?.("Automatisches Kopieren nicht möglich - Pfad ist markiert, bitte Strg+C drücken.", "warning"); });
  });

  async function downloadFile(entry) {
    try {
      const res = await fs.read(entry.path);
      const bytes = Uint8Array.from(atob(res.data), (c) => c.charCodeAt(0));
      const blob = new Blob([bytes]);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = res.name || entry.name; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { window.notify?.("Download fehlgeschlagen: " + e.message, "error"); }
  }

  async function viewImage(entry) {
    try {
      const res = await fs.read(entry.path);
      const ext = extOf(entry.name);
      const mime = ext === "svg" ? "image/svg+xml" : `image/${ext === "jpg" ? "jpeg" : ext}`;
      openOverlay(`
        <div style="text-align:center">
          <div style="margin-bottom:8px;color:var(--subtext)">${esc(entry.name)}</div>
          <img src="data:${mime};base64,${res.data}" style="max-width:100%;max-height:70vh;border-radius:8px" />
        </div>`);
    } catch (e) { window.notify?.("Bild konnte nicht geladen werden: " + e.message, "error"); }
  }

  async function editFile(entry) {
    try {
      const res = await fs.read(entry.path);
      let text;
      try { text = decodeURIComponent(escape(atob(res.data))); }
      catch { text = atob(res.data); }
      const overlay = openOverlay(`
        <div style="display:flex;flex-direction:column;height:70vh">
          <div style="margin-bottom:8px;color:var(--subtext)">✏️ ${esc(entry.name)}</div>
          <textarea id="exp-editor" style="flex:1;width:100%;font-family:monospace;font-size:13px;
            background:var(--panel-2,#0f1626);color:var(--text);border:1px solid var(--border);
            border-radius:8px;padding:10px;resize:none"></textarea>
          <div style="margin-top:10px;display:flex;gap:8px;justify-content:flex-end">
            <button class="taskbar-btn" id="exp-edit-cancel">Abbrechen</button>
            <button class="btn-primary" id="exp-edit-save" style="margin:0">Speichern</button>
          </div>
        </div>`);
      const ta = overlay.querySelector("#exp-editor");
      ta.value = text;
      overlay.querySelector("#exp-edit-cancel").addEventListener("click", closeOverlay);
      overlay.querySelector("#exp-edit-save").addEventListener("click", async () => {
        try {
          const b64 = btoa(unescape(encodeURIComponent(ta.value)));
          await fs.write(entry.path, b64);
          window.notify?.("Gespeichert", "success");
          closeOverlay();
          load(currentPath);
        } catch (e) { window.notify?.("Speichern fehlgeschlagen: " + e.message, "error"); }
      });
    } catch (e) { window.notify?.("Öffnen fehlgeschlagen: " + e.message, "error"); }
  }

  async function uploadFiles(fileList) {
    if (!currentPath) {
      window.notify?.("Bitte zuerst in ein Laufwerk/einen Ordner wechseln.", "warn");
      return;
    }
    for (const file of fileList) {
      try {
        const buf = await file.arrayBuffer();
        let bin = ""; const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
        const b64 = btoa(bin);
        await fs.write(joinPath(currentPath, file.name), b64);
        window.notify?.(`Hochgeladen: ${file.name}`, "success");
      } catch (e) { window.notify?.(`Upload fehlgeschlagen (${file.name}): ${e.message}`, "error"); }
    }
    load(currentPath);
  }

  async function doMkdir() {
    if (!currentPath) { window.notify?.("Bitte zuerst einen Ordner öffnen.", "warn"); return; }
    const name = await uiPrompt("Neuen Ordner anlegen", { description: "Name des neuen Ordners:" });
    if (!name) return;
    try { await fs.mkdir(joinPath(currentPath, name)); load(currentPath); }
    catch (e) { window.notify?.("Anlegen fehlgeschlagen: " + e.message, "error"); }
  }

  async function doRename(entry) {
    const name = await uiPrompt("Umbenennen", { description: "Neuer Name:", value: entry.name });
    if (!name || name === entry.name) return;
    try { await fs.rename(entry.path, joinPath(parentOf(entry.path), name)); load(currentPath); }
    catch (e) { window.notify?.("Umbenennen fehlgeschlagen: " + e.message, "error"); }
  }

  async function doDelete(entry) {
    if (!(await uiConfirm(`"${entry.name}" wirklich löschen?`, { okText: "Löschen", danger: true }))) return;
    try { await fs.del(entry.path); load(currentPath); }
    catch (e) { window.notify?.("Löschen fehlgeschlagen: " + e.message, "error"); }
  }

  async function load(path) {
    currentPath = path;
    tbody.innerHTML = `<tr><td colspan="6" style="color:var(--subtext)">Lädt...</td></tr>`;
    try {
      const res = await fs.list(path);
      currentEntries = res.entries || [];
      renderCrumbs(res.path);
      tbody.innerHTML = "";
      if (!currentEntries.length) {
        tbody.innerHTML = `<tr><td colspan="6" style="color:var(--subtext)">Leerer Ordner</td></tr>`;
      }
      for (const entry of currentEntries) {
        const tr = document.createElement("tr");
        const icon = entry.isDir ? "📁" : (IMAGE_EXT.has(extOf(entry.name)) ? "🖼️" : "📄");
        const size = entry.isDir ? "" : formatBytes(entry.size);
        const mtime = entry.mtime ? new Date(entry.mtime).toLocaleString("de-DE") : "";
        const perms = entry.perms || "";
        const modeHint = entry.mode ? ` (${entry.mode})` : "";
        const owner = (entry.owner || entry.group) ? `${esc(entry.owner || "")}:${esc(entry.group || "")}` : "";
        const isImg = !entry.isDir && IMAGE_EXT.has(extOf(entry.name));
        const isTxt = !entry.isDir && TEXT_EXT.has(extOf(entry.name));

        tr.innerHTML = `
          <td style="cursor:pointer">${icon} ${esc(entry.name)}</td>
          <td>${size}</td>
          <td style="color:var(--subtext)">${mtime}</td>
          <td style="font-family:monospace;font-size:12px;color:var(--subtext)" title="Modus${modeHint}">${esc(perms)}</td>
          <td style="font-size:12px;color:var(--subtext)">${owner}</td>
          <td></td>`;

        const actionsTd = tr.lastElementChild;
        const mkBtn = (label, title, fn) => {
          const b = document.createElement("button");
          b.className = "taskbar-btn"; b.textContent = label; b.title = title;
          b.style.marginRight = "4px";
          b.addEventListener("click", (ev) => { ev.stopPropagation(); fn(); });
          return b;
        };

        if (entry.isDir) {
          tr.firstElementChild.title = "Öffnen";
          tr.firstElementChild.addEventListener("dblclick", () => { history.push(entry.path); load(entry.path); });
        } else {
          if (isImg) actionsTd.appendChild(mkBtn("👁", "Ansehen", () => viewImage(entry)));
          if (isTxt) actionsTd.appendChild(mkBtn("✏️", "Bearbeiten", () => editFile(entry)));
          actionsTd.appendChild(mkBtn("⬇", "Download", () => downloadFile(entry)));
          tr.firstElementChild.title = isImg ? "Ansehen" : (isTxt ? "Bearbeiten" : "Download");
          tr.firstElementChild.addEventListener("dblclick", () =>
            isImg ? viewImage(entry) : (isTxt ? editFile(entry) : downloadFile(entry)));
        }
        if (currentPath) {
          actionsTd.appendChild(mkBtn("✏", "Umbenennen", () => doRename(entry)));
          actionsTd.appendChild(mkBtn("🗑", "Löschen", () => doDelete(entry)));
        }
        tbody.appendChild(tr);
      }
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="6" style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  }

  let overlayEl = null;
  function openOverlay(html) {
    closeOverlay();
    overlayEl = document.createElement("div");
    overlayEl.style.cssText = `position:absolute;inset:0;z-index:50;background:rgba(0,0,0,0.75);
      display:flex;align-items:center;justify-content:center;padding:24px`;
    const inner = document.createElement("div");
    inner.className = "panel";
    inner.style.cssText = "max-width:90%;width:820px;max-height:90%;overflow:auto;padding:16px;position:relative";
    inner.innerHTML = html;
    const close = document.createElement("button");
    close.textContent = "✕"; close.className = "taskbar-btn";
    close.style.cssText = "position:absolute;top:8px;right:8px;z-index:1";
    close.addEventListener("click", closeOverlay);
    inner.appendChild(close);
    overlayEl.appendChild(inner);
    overlayEl.addEventListener("click", (e) => { if (e.target === overlayEl) closeOverlay(); });
    filesPane.appendChild(overlayEl);
    return inner;
  }
  function closeOverlay() { if (overlayEl) { overlayEl.remove(); overlayEl = null; } }

  async function renderRelay() {
    if (!relayPane) return;
    relayPane.innerHTML = `<div style="color:var(--subtext);padding:20px">Lädt…</div>`;

    // --- Adresse bestimmen (die, über die das Dashboard läuft; Port aus Config) ---
    const testBase = (BACKEND_URL || window.location.origin).replace(/\/$/, "");
    const testIsLocalhost = /^https?:\/\/(localhost|127\.0\.0\.1)/i.test(testBase);
    let base = testBase;
    let backendPort = window.location.port || "";
    if (testIsLocalhost) {
      try {
        const res = await api.getServerAddress();
        if (res) {
          if (res.backend_port) backendPort = String(res.backend_port);
          if (res.base_url) {
            let cand = res.base_url.replace(/\/$/, "");
            if (!/^https?:\/\//i.test(cand)) cand = `${window.location.protocol}//${cand}`;
            base = cand;
          }
        }
      } catch {}
    }
    if (!/^https?:\/\//i.test(base)) base = `${window.location.protocol}//${base}`;

    const pm = base.match(/^(https?):\/\/([^/:]+)(?::(\d+))?/i);
    const scheme = pm ? pm[1].toLowerCase() : "http";
    const host = pm ? pm[2] : "";
    const port = (pm && pm[3]) || backendPort || (scheme === "https" ? "443" : "80");
    const baseWithPort = `${scheme}://${host}:${port}`;

    // Wurzel-Mount: EIN Netzlaufwerk, darin je Client ein Ordner.
    // Windows „Netzlaufwerk verbinden"/„Netzwerkadresse hinzufügen" akzeptiert die
    // http://ip:port/dav-Form (mit :Port) direkt - das ist die zuverlässige Variante.
    const httpRoot = `${baseWithPort}/dav`;

    // --- Status (pro Client) + Benutzer ---
    let enabled = false, isAdmin = false, username = "";
    try { enabled = (await api.getRelayStatus(clientId)).enabled; } catch {}
    try { isAdmin = userIsAdmin(); } catch {}
    try { username = state.user?.username || ""; } catch {}

    const card = (inner, accent) =>
      `<div class="panel" style="padding:16px;margin-bottom:14px${accent ? `;border-color:${accent}` : ""}">${inner}</div>`;

    const badge = enabled
      ? `<span style="background:var(--online,#3ecf8e);color:#052e1c;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600">● Freigegeben</span>`
      : `<span style="background:var(--danger,#ff4d6d);color:#3a0510;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600">● Gesperrt</span>`;

    const toggleBtn = `<button id="relay-toggle" class="${enabled ? "taskbar-btn" : "btn-primary"}" style="margin:0">
        ${enabled ? "Freigabe aufheben" : "Diesen Client freigeben"}</button>`;

    const header = card(`
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div>
          <div style="font-size:16px;font-weight:700;margin-bottom:4px">🔌 Explorer-Relay ${badge}</div>
          <div style="color:var(--subtext);font-size:13px">
            Gibt <b>${esc(clientName || clientId)}</b> im gemeinsamen Netzlaufwerk frei.
          </div>
        </div>
        ${toggleBtn}
      </div>`, enabled ? "var(--online,#3ecf8e)" : "var(--danger,#ff4d6d)");

    const copyField = (label, value) => `
      <div style="color:var(--subtext);font-size:13px">${label}</div>
      <div style="display:flex;gap:6px">
        <input type="text" readonly value="${esc(value)}" onclick="this.select()" style="flex:1;font-family:monospace" />
        <button class="taskbar-btn" data-copy="${esc(value)}">Kopieren</button>
      </div>`;

    const address = card(`
      <div style="font-weight:700;margin-bottom:6px">📍 Ein Netzlaufwerk für alle freigegebenen Clients</div>
      <div style="color:var(--subtext);font-size:13px;margin-bottom:10px">
        Du verbindest EINMAL dieses Laufwerk. Darin erscheint pro freigegebenem Client ein
        Ordner, und darin die Festplatten. Mehrere Clients gleichzeitig, ganz automatisch.
      </div>
      <div style="display:grid;grid-template-columns:auto 1fr;gap:8px 12px;align-items:center">
        ${copyField("Windows / macOS / Linux", httpRoot)}
      </div>
      <div style="color:var(--subtext);font-size:12px;margin-top:8px">
        In „Netzlaufwerk verbinden" bzw. „Netzwerkadresse hinzufügen" genau diese
        <b>http://…:Port/dav</b>-Adresse eintragen (mit Doppelpunkt vor dem Port).
      </div>`);

    const login = card(`
      <div style="font-weight:700;margin-bottom:6px">🔑 Anmeldung</div>
      <div style="color:var(--subtext);font-size:13px;line-height:1.7">
        Mit deinem <b>normalen Dashboard-Login</b> anmelden:
        <ul style="margin:6px 0 0;padding-left:18px">
          <li>Benutzername: <b>${esc(username || "dein Benutzername")}</b></li>
          <li>Passwort: dein gewohntes Dashboard-Passwort</li>
        </ul>
        <div style="font-size:12px;margin-top:8px">
          Hinweis: Nach diesem Update bitte einmal am Dashboard neu anmelden – dabei wird
          die Netzlaufwerk-Anmeldung für dein Konto scharf geschaltet.
        </div>
      </div>`);

    // Netzlaufwerk MIT Laufwerksbuchstaben (Z:). WICHTIG: Für net use die
    // WebDAV-UNC-Form \\\\host@Port\\dav verwenden! Die http://…-Form schlägt in
    // cmd mit Nicht-Standard-Port oft mit "Systemfehler 67" fehl - der grafische
    // Dialog ("Netzwerkadresse hinzufügen") nimmt dagegen die http-Form.
    // Bei https lautet die Form \\\\host@SSL@Port\\dav.
    const uncRoot = `\\\\${host}@${scheme === "https" ? "SSL@" : ""}${port}\\dav`;
    const netUse = username ? card(`
      <div style="font-weight:700;margin-bottom:6px">💽 Als Netzlaufwerk (mit Laufwerksbuchstaben Z:)</div>
      <div style="color:var(--subtext);font-size:13px;margin-bottom:8px">
        Zuverlässigster Weg für einen echten Laufwerksbuchstaben: in der
        <b>Eingabeaufforderung</b> (<code>cmd</code>) ausführen und dein Passwort direkt anhängen.
        Vorher muss der Dienst „WebClient" laufen (<code>net start webclient</code>).
      </div>
      ${copyField("Befehl (Passwort ans Ende anhängen)", `net use Z: ${uncRoot} /persistent:yes /user:${username} `)}
      <div style="color:var(--subtext);font-size:12px;margin-top:8px">
        Statt <code>Z:</code> geht jeder freie Buchstabe. <code>/persistent:yes</code> ist schon
        dabei, damit das Laufwerk nach dem Neustart bleibt. Die Form
        <code>\\\\${esc(host)}@${esc(port)}\\dav</code> ist die offizielle WebDAV-Schreibweise mit
        Port - <b>nicht</b> <code>:${esc(port)}</code> mit Doppelpunkt (das versucht SMB und
        endet in Fehler 67 bzw. 0x800704b3).
      </div>
      <div style="color:var(--subtext);font-size:12px;margin-top:6px">
        Trennen später mit: <code>net use Z: /delete</code>
      </div>`) : "";

    const guide = card(`
      <div style="font-weight:700;margin-bottom:6px">🪟 Windows – Netzlaufwerk verbinden (grafisch)</div>
      <ol style="margin:0;padding-left:18px;color:var(--subtext);font-size:13px;line-height:1.8">
        <li>Dienst „WebClient" muss laufen: in <code>cmd</code> (als Admin)
          <code>net start webclient</code>.</li>
        <li>Explorer → „Dieser PC" → „Netzlaufwerk verbinden".</li>
        <li>Laufwerksbuchstaben wählen, als Ordner die <b>http://…:Port/dav</b>-Adresse von oben
          eintragen (mit Doppelpunkt vor dem Port).</li>
        <li>„Verbindung mit anderen Anmeldeinformationen herstellen" anhaken → Fertig stellen →
          Dashboard-Login eingeben.</li>
      </ol>
      <div style="color:var(--subtext);font-size:12px;margin-top:8px">
        Wichtig: <b>niemals</b> die Form <code>\\\\host:Port\\dav</code> mit Doppelpunkt verwenden –
        damit versucht Windows SMB und meldet Fehler 67 bzw. <code>0x800704b3</code>. Im grafischen
        Dialog die <code>http://…/dav</code>-Adresse nehmen; in <code>cmd</code> (net use) die
        WebDAV-Form <code>\\\\host@Port\\dav</code> von oben.
      </div>
      <div style="color:var(--subtext);font-size:12px;margin-top:6px">
        Der jeweilige Client muss online sein, damit sein Ordner Inhalte zeigt.
      </div>`);

    if (!enabled) {
      relayPane.innerHTML = header + card(`
        <div style="color:var(--subtext);font-size:13px;line-height:1.7">
          Dieser Client ist <b>nicht freigegeben</b> und erscheint daher nicht im Netzlaufwerk.
          ${isAdmin ? "Gib ihn oben frei." : "Ein Administrator muss ihn freigeben."}
        </div>`) + address;
      wire();
      return;
    }

    relayPane.innerHTML = header + address + netUse + login + guide;
    wire();

    function wire() {
      const btn = relayPane.querySelector("#relay-toggle");
      if (btn) {
        if (!isAdmin) { btn.disabled = true; btn.title = "Nur Administratoren"; }
        else btn.addEventListener("click", async () => {
          btn.disabled = true;
          try {
            await api.toggleRelay(clientId);
            window.notify?.("Gespeichert", "success");
            window.dispatchEvent(new CustomEvent("relay-changed", { detail: { clientId } }));
            renderRelay();
          }
          catch (e) { window.notify?.("Fehler: " + e.message, "error"); btn.disabled = false; }
        });
      }
      relayPane.querySelectorAll("[data-copy]").forEach((b) =>
        b.addEventListener("click", () =>
          copyToClipboard(b.dataset.copy).then(
            () => window.notify?.("Kopiert", "success"),
            () => {
              // Letzter Ausweg: Feld markieren, damit Strg+C reicht.
              const input = b.parentElement?.querySelector("input");
              if (input) { input.focus(); input.select(); }
              window.notify?.("Automatisches Kopieren nicht möglich - Text ist markiert, bitte Strg+C drücken.", "warning");
            })));
    }
  }

  const tabFiles = body.querySelector(`#exp-tab-files-${win.key}`);
  const tabRelay = body.querySelector(`#exp-tab-relay-${win.key}`);
  function setMode(m) {
    mode = m;
    filesPane.style.display = m === "files" ? "flex" : "none";
    if (relayPane) relayPane.style.display = m === "relay" ? "block" : "none";
    tabFiles.className = m === "files" ? "btn-primary" : "taskbar-btn";
    if (tabRelay) tabRelay.className = m === "relay" ? "btn-primary" : "taskbar-btn";
    if (m === "relay") renderRelay();
  }
  tabFiles.addEventListener("click", () => setMode("files"));
  if (tabRelay) tabRelay.addEventListener("click", () => setMode("relay"));

  body.querySelector(`#exp-back-${win.key}`).addEventListener("click", () => {
    if (history.length > 1) { history.pop(); load(history[history.length - 1]); }
  });
  body.querySelector(`#exp-refresh-${win.key}`).addEventListener("click", () => load(currentPath));
  body.querySelector(`#exp-up-${win.key}`).addEventListener("click", () => {
    const p = parentOf(currentPath); history.push(p); load(p);
  });
  body.querySelector(`#exp-mkdir-${win.key}`).addEventListener("click", doMkdir);
  body.querySelector(`#exp-upload-${win.key}`).addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => { if (fileInput.files.length) uploadFiles(fileInput.files); fileInput.value = ""; });

  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.style.outline = "2px dashed var(--accent,#38bdf8)"; });
  dropZone.addEventListener("dragleave", () => { dropZone.style.outline = ""; });
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault(); dropZone.style.outline = "";
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });

  // Wenn der Relay woanders (z.B. in der „Explorer-Relay"-App) umgeschaltet
  // wird, den Relay-Tab hier aktualisieren. Listener räumt sich selbst auf,
  // sobald das Fenster geschlossen ist.
  function onRelayChanged() {
    if (!document.body.contains(body)) {
      window.removeEventListener("relay-changed", onRelayChanged);
      return;
    }
    if (mode === "relay") renderRelay();
  }
  window.addEventListener("relay-changed", onRelayChanged);

  load("");
}

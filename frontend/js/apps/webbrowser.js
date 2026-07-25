// apps/webbrowser.js
// ------------------
// 🌐 Interner Webbrowser: Webseiten direkt in einem Fenster der Webconsole
// öffnen (iframe) - mit URL-Leiste, eigener Vor/Zurück-Historie und Reload.
//
// Highlight "Web-Apps": Jede Seite lässt sich mit ⭐ "Als App speichern"
// als INTERNE APP registrieren. Sie bekommt dann einen eigenen Eintrag im
// Startmenü (Icon + Name) und öffnet direkt in ihrem eigenen Fenster.
// Gespeichert pro Benutzer (localStorage, serverseitig gesynct).
//
// Hinweis: Manche Seiten verbieten das Einbetten per X-Frame-Options/CSP
// (z.B. google.com) - dafür gibt es den "Extern öffnen"-Knopf.
import { esc, uiConfirm } from "../utils.js";
import { state } from "../state.js";
// t() unter Alias: in dieser Datei ist "t" bereits als lokaler
// Variablenname belegt (Tenant/Target/Trigger/Token o.ä.).
import { t as tr } from "../i18n.js";

// ---------------- Web-App-Speicher (pro Benutzer) ----------------
const storeKey = () => `rmm_webapps:${state.user?.username || "default"}`;

export function getWebApps() {
  try { return JSON.parse(localStorage.getItem(storeKey()) || "[]"); }
  catch { return []; }
}
function saveWebApps(list) {
  try { localStorage.setItem(storeKey(), JSON.stringify(list)); } catch {}
  import("../persist.js").then((m) => m.syncToServerSoon()).catch(() => {});
}

// Neue Web-App speichern und sofort im Startmenü registrieren.
export function addWebApp({ name, url, icon }) {
  const list = getWebApps();
  const id = "wa" + Date.now().toString(36) + Math.floor(Math.random() * 1e4).toString(36);
  const app = { id, name: name.trim(), url: url.trim(), icon: (icon || "🌐").trim() || "🌐" };
  list.push(app);
  saveWebApps(list);
  import("../startmenu.js").then((m) =>
    m.addCatalogEntry?.(`webapp:${id}`, app.icon, app.name)).catch(() => {});
  return app;
}

export function removeWebApp(id) {
  saveWebApps(getWebApps().filter((a) => a.id !== id));
  import("../startmenu.js").then((m) => m.removeCatalogEntry?.(`webapp:${id}`)).catch(() => {});
}

const normalizeUrl = (raw) => {
  const t = (raw || "").trim();
  if (!t) return "";
  return /^https?:\/\//i.test(t) ? t : "https://" + t;
};

// ---------------- Browser-UI ----------------
// props: { url, name, icon } -> gesperrter Start als Web-App (eigenes Fenster)
export function renderWebBrowser(body, win) {
  const startUrl = win?.props?.url ? normalizeUrl(win.props.url) : "";
  let history = [];        // eigene URL-Historie (iframe-intern ist cross-origin tabu)
  let idx = -1;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
      <div style="display:flex;gap:6px;align-items:center;padding:6px 8px;background:var(--panel-2);border-bottom:1px solid var(--border)">
        <button class="taskbar-btn" id="wb-back" title="${tr("exp_back")}">◀</button>
        <button class="taskbar-btn" id="wb-fwd" title="Vor">▶</button>
        <button class="taskbar-btn" id="wb-reload" title="Neu laden">🔄</button>
        <input id="wb-url" placeholder="https://…  (Enter zum Laden)"
          style="flex:1;background:var(--panel);border:1px solid var(--border);border-radius:16px;
          color:var(--text);padding:6px 14px;font-size:13px;outline:none" />
        <button class="taskbar-btn" id="wb-go" title="Laden">➜</button>
        <button class="taskbar-btn" id="wb-save" title="${tr("u_diese_seite_als_interne_app_speich")}">⭐ Als App</button>
        <button class="taskbar-btn" id="wb-ext" title="${tr("u_in_neuem_browser_tab_offnen")}">🔗</button>
      </div>
      <div id="wb-stage" style="flex:1;position:relative;background:#fff">
        <iframe id="wb-frame" style="position:absolute;inset:0;width:100%;height:100%;border:0;display:none"
          allow="clipboard-read; clipboard-write; fullscreen; autoplay"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads allow-modals"></iframe>
        <div id="wb-home" style="position:absolute;inset:0;overflow:auto;background:var(--panel);padding:22px"></div>
      </div>
      <div id="wb-hint" style="display:none;padding:5px 12px;font-size:11px;color:var(--subtext);background:var(--panel-2);border-top:1px solid var(--border)">
        Seite bleibt weiß/leer? Dann verbietet sie das Einbetten (X-Frame-Options/CSP) → mit 🔗 extern öffnen.
      </div>
      <div id="wb-dialog" style="display:none;position:absolute;inset:0;background:rgba(0,0,0,.55);align-items:center;justify-content:center;z-index:5"></div>
    </div>
  `;
  body.firstElementChild.style.position = "relative";

  const frame = body.querySelector("#wb-frame");
  const home = body.querySelector("#wb-home");
  const urlEl = body.querySelector("#wb-url");
  const hint = body.querySelector("#wb-hint");
  const dialog = body.querySelector("#wb-dialog");

  function currentUrl() { return idx >= 0 ? history[idx] : ""; }

  function navigate(raw, pushHistory = true) {
    const url = normalizeUrl(raw);
    if (!url) return;
    if (pushHistory) {
      history = history.slice(0, idx + 1);
      history.push(url);
      idx = history.length - 1;
    }
    urlEl.value = url;
    home.style.display = "none";
    frame.style.display = "block";
    hint.style.display = "block";
    frame.src = url;
    updateNav();
  }

  function updateNav() {
    body.querySelector("#wb-back").disabled = idx <= 0;
    body.querySelector("#wb-fwd").disabled = idx >= history.length - 1;
  }

  // ---------------- Startseite (Web-Apps + Schnellzugriff) ----------------
  function showHome() {
    frame.style.display = "none";
    frame.src = "about:blank";
    hint.style.display = "none";
    home.style.display = "block";
    const apps = getWebApps();
    home.innerHTML = `
      <div style="max-width:760px;margin:0 auto">
        <div style="text-align:center;margin-bottom:18px">
          <div style="font-size:46px">🌐</div>
          <div style="font-weight:800;font-size:17px">Interner Browser</div>
          <div style="color:var(--subtext);font-size:12px">
            Adresse oben eingeben - oder eine gespeicherte Web-App öffnen.
            Mit ⭐ wird jede Seite zur eigenen App im Startmenü.
          </div>
        </div>
        <h3 style="font-size:13px;margin:0 0 8px">Meine Web-Apps</h3>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px">
          ${apps.map((a) => `
            <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;padding:10px;position:relative">
              <button data-open="${esc(a.id)}" style="all:unset;cursor:pointer;display:block;width:100%">
                <div style="font-size:26px">${esc(a.icon)}</div>
                <div style="font-weight:700;font-size:13px;margin:3px 0 1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.name)}</div>
                <div style="color:var(--subtext);font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.url)}</div>
              </button>
              <button data-del="${esc(a.id)}" title="Web-App entfernen" class="taskbar-btn"
                style="position:absolute;top:6px;right:6px;font-size:10px;padding:1px 6px">✕</button>
            </div>`).join("")
          || `<div style="color:var(--subtext);font-size:12px">Noch keine - öffne eine Seite und klicke ⭐ „Als App".</div>`}
        </div>
      </div>`;
    home.querySelectorAll("[data-open]").forEach((b) =>
      b.addEventListener("click", () => {
        const a = getWebApps().find((x) => x.id === b.dataset.open);
        if (a) openWebAppWindow(a);
      }));
    home.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        const a = getWebApps().find((x) => x.id === b.dataset.del);
        if (!a) return;
        if (!(await uiConfirm(`Web-App "${a.name}" entfernen?`, { danger: true }))) return;
        removeWebApp(a.id);
        showHome();
      }));
  }

  // Web-App in EIGENEM Fenster öffnen (interne App).
  function openWebAppWindow(a) {
    import("../windowmanager.js").then((m) =>
      m.openWindow({
        key: `webapp:${a.id}`, appId: "webapp",
        title: `${a.icon} ${a.name}`, props: { url: a.url, name: a.name, icon: a.icon },
        w: 1020, h: 720,
      }));
  }

  // ---------------- "Als App speichern" ----------------
  function openSaveDialog() {
    const url = currentUrl() || urlEl.value.trim();
    if (!normalizeUrl(url)) { window.notify?.(tr("u_bitte_zuerst_eine_seite_laden"), "warn"); return; }
    let guess = "";
    try { guess = new URL(normalizeUrl(url)).hostname.replace(/^www\./, "").split(".")[0]; } catch {}
    guess = guess ? guess[0].toUpperCase() + guess.slice(1) : "Web-App";
    dialog.style.display = "flex";
    dialog.innerHTML = `
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px;width:380px">
        <h3 style="margin:0 0 12px">⭐ Als interne App speichern</h3>
        <div class="form-row"><label>Name</label>
          <input id="wbd-name" value="${esc(guess)}" /></div>
        <div class="form-row"><label>Icon (Emoji)</label>
          <input id="wbd-icon" value="🌐" maxlength="4" style="max-width:80px" /></div>
        <div class="form-row"><label>URL</label>
          <input id="wbd-url" value="${esc(normalizeUrl(url))}" /></div>
        <p style="color:var(--subtext);font-size:11px;margin:4px 0 0">
          Die App erscheint sofort im Startmenü und öffnet die Seite direkt
          in einem eigenen Fenster.</p>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
          <button class="taskbar-btn" id="wbd-cancel">Abbrechen</button>
          <button class="btn-primary" id="wbd-save" style="margin:0;width:auto">Speichern</button>
        </div>
      </div>`;
    dialog.querySelector("#wbd-cancel").addEventListener("click", () => { dialog.style.display = "none"; });
    dialog.querySelector("#wbd-save").addEventListener("click", () => {
      const name = dialog.querySelector("#wbd-name").value.trim();
      const u = normalizeUrl(dialog.querySelector("#wbd-url").value);
      if (!name || !u) return;
      const app = addWebApp({ name, url: u, icon: dialog.querySelector("#wbd-icon").value });
      dialog.style.display = "none";
      window.notify?.(`⭐ "${app.name}" ist jetzt eine App im Startmenü`, "success", 4000);
    });
  }

  // ---------------- Verkabelung ----------------
  body.querySelector("#wb-go").addEventListener("click", () => navigate(urlEl.value));
  urlEl.addEventListener("keydown", (e) => { if (e.key === "Enter") navigate(urlEl.value); });
  body.querySelector("#wb-back").addEventListener("click", () => {
    if (idx > 0) { idx--; navigate(history[idx], false); }
  });
  body.querySelector("#wb-fwd").addEventListener("click", () => {
    if (idx < history.length - 1) { idx++; navigate(history[idx], false); }
  });
  body.querySelector("#wb-reload").addEventListener("click", () => {
    if (currentUrl()) frame.src = currentUrl();
  });
  body.querySelector("#wb-ext").addEventListener("click", () => {
    const u = currentUrl() || normalizeUrl(urlEl.value);
    if (u) window.open(u, "_blank");
  });
  body.querySelector("#wb-save").addEventListener("click", openSaveDialog);

  if (startUrl) navigate(startUrl);
  else showHome();
}

// Renderer für gespeicherte Web-Apps (eigene Fenster, appId "webapp").
export function renderWebApp(body, win) {
  renderWebBrowser(body, win);
}

// Client-Website öffnen: je nach open_mode im INTERNEN Browser-Fenster
// (eigenes Fenster mit URL-Leiste) oder als externer Browser-Tab.
// Wird von Panel, Sidebar-Favoriten, Dashboard und Client-Bearbeiten genutzt.
export function openWebsiteEntry({ url, name, open_mode }) {
  if (!url) return;
  if (open_mode === "internal") {
    import("../windowmanager.js").then((m) =>
      m.openWindow({
        key: `website:${url}`, appId: "webapp",
        title: `🌐 ${name || url}`, props: { url, name: name || url, icon: "🌐" },
        w: 1020, h: 720,
      }));
  } else {
    window.open(url, "_blank", "noopener");
  }
}

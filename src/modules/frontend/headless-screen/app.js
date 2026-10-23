// app.js (Modul "headless-screen") - virtuellen Bildschirm pro Agent verwalten
// ---------------------------------------------------------------------------
// Fuer headless-Windows (Server/VM ohne Monitor): installiert einen virtuellen
// Bildschirmtreiber, damit es ueberhaupt einen Desktop zum Aufnehmen gibt.
// Der Server haelt das Treiberpaket bereit; die Installation laeuft auf dem
// jeweiligen Agenten (als SYSTEM). Die Mehrbenutzer-Auswahl selbst steckt im
// Remote-Bildschirm-Fenster (erscheint automatisch bei mehreren Benutzern).

import { getToken } from "/js/app/api.js";
import { state } from "/js/app/state.js";
import { esc, uiConfirm } from "/js/app/utils.js";
import { dashboardSocket } from "/js/app/socket.js";

async function hq(path) {
  const res = await fetch("/api/headless-screen" + path, {
    headers: { Authorization: "Bearer " + (getToken() || "") },
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  return res.json();
}

const winOnline = (c) => c.online && (c.platform || "").toLowerCase().includes("win");

export function renderHeadless(body) {
  body.innerHTML = `
    <div style="padding:14px 16px;font-size:13px;display:flex;flex-direction:column;gap:12px;height:100%;overflow:auto">
      <div data-pkg></div>
      <div>
        <div style="font-weight:600;margin-bottom:4px">Agent wählen (Windows)</div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <select data-client style="min-width:260px"></select>
          <button class="btn btn-sm" data-refresh>Status abrufen</button>
        </div>
      </div>
      <div data-status style="border:1px solid var(--border);border-radius:10px;padding:12px;min-height:60px">
        <span style="color:var(--subtext)">Bitte einen Agenten wählen.</span>
      </div>
      <div style="color:var(--subtext);font-size:12px;line-height:1.6">
        Der virtuelle Bildschirm wird nur gebraucht, wenn an einem Windows-PC <b>kein Monitor</b> hängt und
        <b>niemand</b> angemeldet ist – dann gibt es sonst keinen Desktop zum Aufnehmen. Ist jemand angemeldet
        (auch per RDP), funktioniert der Remote-Bildschirm ohne Treiber. Sind mehrere Benutzer angemeldet,
        fragt das Remote-Bildschirm-Fenster automatisch, wessen Sitzung verbunden werden soll.
      </div>
    </div>`;

  const sel = body.querySelector("[data-client]");
  const statusBox = body.querySelector("[data-status]");

  const fill = () => {
    const prev = sel.value;
    const list = [...(state.clients || [])].sort((a, b) => (a.hostname || "").localeCompare(b.hostname || ""));
    sel.innerHTML = list.map((c) => {
      const win = (c.platform || "").toLowerCase().includes("win");
      const tag = win ? (c.online ? "" : " (offline)") : " (kein Windows)";
      return `<option value="${esc(c.id)}" ${win && c.online ? "" : "disabled"}>${esc(c.hostname)}${tag}</option>`;
    }).join("") || `<option value="">Keine Agenten</option>`;
    if (prev) sel.value = prev;
  };
  fill();

  drawPkg(body.querySelector("[data-pkg]"));

  const draw = (st) => {
    const cid = sel.value;
    const c = state.clients?.find((x) => x.id === cid);
    if (!cid || !c) { statusBox.innerHTML = `<span style="color:var(--subtext)">Kein Agent gewählt.</span>`; return; }
    if (!st) { statusBox.innerHTML = `<span style="color:var(--subtext)">⏳ Frage ${esc(c.hostname)} …</span>`; return; }
    if (st.supported === false) {
      statusBox.innerHTML = `<b>${esc(c.hostname)}</b><div style="color:var(--subtext);margin-top:4px">Virtueller Bildschirm wird nur unter Windows unterstützt.</div>`;
      return;
    }
    const badge = st.present
      ? `<span style="color:var(--online,#16a34a)">● aktiv</span>`
      : st.installed
        ? `<span style="color:var(--warn,#d97706)">● Treiber installiert, kein Gerät aktiv</span>`
        : `<span style="color:var(--subtext)">○ nicht installiert</span>`;
    statusBox.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <b>${esc(c.hostname)}</b> ${badge}
        ${typeof st.monitors === "number" ? `<span style="color:var(--subtext);font-size:12px">Bildschirme: ${st.monitors}</span>` : ""}
        <span style="flex:1"></span>
        ${st.installed || st.present
          ? `<button class="btn btn-sm btn-danger" data-remove>Entfernen</button>`
          : `<button class="btn-primary btn-sm" data-install>Virtuellen Bildschirm installieren</button>`}
      </div>
      ${st.error ? `<div style="color:var(--danger);font-size:12px;margin-top:8px">${esc(st.error)}</div>` : ""}
      <div data-msg style="font-size:12px;margin-top:8px"></div>`;
    statusBox.querySelector("[data-install]")?.addEventListener("click", () => act("install", c));
    statusBox.querySelector("[data-remove]")?.addEventListener("click", async () => {
      if (!(await uiConfirm(`Virtuellen Bildschirm auf ${c.hostname} entfernen?`, { okText: "Entfernen", danger: true }))) return;
      act("remove", c);
    });
  };

  const act = (action, c) => {
    const msg = statusBox.querySelector("[data-msg]");
    if (msg) msg.innerHTML = `<span style="color:var(--subtext)">⏳ ${action === "install" ? "Installiere" : "Entferne"} … das kann einen Moment dauern.</span>`;
    dashboardSocket.emit(`vdisplay-${action}`, { clientId: c.id, username: state.user?.username || "" });
  };

  const requestStatus = () => {
    const cid = sel.value;
    if (!cid) return;
    draw(null);
    dashboardSocket.emit("vdisplay-status", { clientId: cid, username: state.user?.username || "" });
  };

  const onStatus = (d) => { if (d?.id === sel.value) draw(d); };
  const onResult = (d) => {
    if (d?.id !== sel.value) return;
    const msg = statusBox.querySelector("[data-msg]");
    if (msg) msg.innerHTML = d.ok
      ? `<span style="color:var(--online,#16a34a)">✓ ${esc(d.detail || "Erledigt.")}</span>`
      : `<span style="color:var(--danger)">${esc(d.error || "Fehlgeschlagen.")}</span>`;
  };
  dashboardSocket.on("vdisplay-status", onStatus);
  dashboardSocket.on("vdisplay-result", onResult);

  sel.addEventListener("change", requestStatus);
  body.querySelector("[data-refresh]").addEventListener("click", requestStatus);
  if (sel.value) requestStatus();

  // Aufräumen, wenn das Fenster schließt
  const mo = new MutationObserver(() => {
    if (!body.isConnected) {
      dashboardSocket.off("vdisplay-status", onStatus);
      dashboardSocket.off("vdisplay-result", onResult);
      mo.disconnect();
    }
  });
  mo.observe(document.body, { childList: true, subtree: true });
}

async function drawPkg(host) {
  let st;
  try { st = await hq("/status"); } catch (e) { host.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
  const ok = st.custom_url || st.have_local;
  host.innerHTML = `<div style="border:1px solid var(--border);border-radius:10px;padding:10px 12px;background:var(--panel-2)">
    ${ok
      ? `✅ Treiberpaket bereit${st.custom_url ? " (eigene Quelle)" : " (quelloffener Standard-Treiber)"}.`
      : st.downloading
        ? `⏳ Standard-Bildschirmtreiber wird heruntergeladen …`
        : st.auto_default
          ? `⏳ Beim Aktivieren wird automatisch der quelloffene, signierte Standard-Treiber geladen – nichts weiter nötig. Kommt er nicht an (kein Internet?), kannst du in den Modul-Einstellungen eine eigene <code>driver_url</code> angeben.`
          : `⚠ Kein Treiberpaket auf dem Server. In den Modul-Einstellungen eine <code>driver_url</code> (signiertes ZIP mit INF) angeben oder ein <code>driver.zip</code> in <code>storage/headless-screen/</code> ablegen.`}
    ${st.error ? `<div style="color:var(--danger);font-size:12px;margin-top:4px">${esc(st.error)}</div>` : ""}
  </div>`;
}

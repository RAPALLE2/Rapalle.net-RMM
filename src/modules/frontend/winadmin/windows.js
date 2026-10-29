// windows.js (Modul "winadmin") - Fenster "Registry" und "Windows-Updates"
// -----------------------------------------------------------------------
// renderRegistry(body, win)    App "winadmin-registry"  props.clientId
// renderWinUpdates(body, win)  App "winadmin-updates"   props.clientId
// Kommunikation wie bisher über den Dashboard-Socket (registry / wu-auto).

import { dashboardSocket } from "/js/app/socket.js";
import { state, hasClientPerm } from "/js/app/state.js";
import { esc, uiConfirm } from "/js/app/utils.js";
import { isModuleOn } from "/js/app/modules.js";
import { registerCleanup } from "/js/app/windowmanager.js";

const me = () => state.user?.username || "";
const clientOf = (win) => {
  const id = win?.props?.clientId;
  return (state.clients || []).find((c) => c.id === id) || { id, hostname: win?.props?.clientName || id };
};

// kleiner Dialog INNERHALB des Fensters (Wert bearbeiten) - bleibt am Fenster
function innerDialog(host, html, w = 440) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:absolute;inset:0;z-index:20;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:16px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px 18px;width:${w}px;max-width:100%;max-height:100%;overflow:auto;font-size:13px">${html}</div>`;
  host.appendChild(ov);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  return ov;
}

// ============================================================ Registry
const HIVES = ["HKLM", "HKCU", "HKCR", "HKU", "HKCC"];

export function renderRegistry(body, win) {
  const client = clientOf(win);
  const canEdit = hasClientPerm(client.id, "c_registry");
  body.innerHTML = `
    <div style="position:relative;height:100%;display:flex;flex-direction:column;box-sizing:border-box;padding:14px 16px;gap:10px;font-size:13px">
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <select data-hive>${HIVES.map((h) => `<option>${h}</option>`).join("")}</select>
        <button class="btn btn-sm" data-up title="Eine Ebene höher">⬆</button>
        <input data-path placeholder="SOFTWARE\\Microsoft\\Windows" style="flex:1;min-width:180px">
        <button class="btn btn-sm btn-primary" data-go>Öffnen</button>
      </div>
      <div data-body style="flex:1;min-height:0;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:10px 12px">Pfad eingeben und „Öffnen“.</div>
      <div data-msg style="font-size:12px;min-height:16px"></div>
    </div>`;
  const root = body.firstElementChild;
  const $ = (s) => root.querySelector(s);
  const view = $("[data-body]"), msg = $("[data-msg]");
  let rid = 0;
  const pending = {};
  const onResult = (d) => {
    if (d.id !== client.id) return;
    const cb = pending[d.rid]; if (cb) { delete pending[d.rid]; cb(d); }
  };
  dashboardSocket.on("registry-result", onResult);
  if (win?.key) registerCleanup(win.key, () => dashboardSocket.off("registry-result", onResult));

  const call = (op, extra = {}) => new Promise((resolve) => {
    const id = "r" + (++rid) + "-" + Date.now();
    pending[id] = resolve;
    const path = $("[data-hive]").value + "\\" + $("[data-path]").value.replace(/^\\+/, "");
    dashboardSocket.emit("registry", { clientId: client.id, username: me(), op, rid: id, path, ...extra });
    setTimeout(() => { if (pending[id]) { delete pending[id]; resolve({ ok: false, error: "Zeitüberschreitung – ist der Agent online?" }); } }, 15000);
  });

  const load = async () => {
    view.innerHTML = "⏳ …"; msg.textContent = "";
    const d = await call("list");
    if (!d.ok) { view.innerHTML = `<span style="color:var(--danger)">${esc(d.error || "Fehler")}</span>`; return; }
    const base = $("[data-path]").value.replace(/^\\+|\\+$/g, "");
    view.innerHTML = `
      ${d.subkeys.length ? `<div style="font-size:11px;color:var(--subtext);margin-bottom:4px">Unterschlüssel (${d.subkeys.length})</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:12px">${d.subkeys.map((k) =>
          `<button class="btn btn-sm" data-sub="${esc(k)}">📁 ${esc(k)}</button>`).join("")}</div>` : ""}
      <div style="font-size:11px;color:var(--subtext);margin-bottom:4px">Werte</div>
      <table class="data-table" style="width:100%;font-size:12.5px"><thead><tr><th style="text-align:left">Name</th><th style="text-align:left">Typ</th><th style="text-align:left">Daten</th>${canEdit ? "<th></th>" : ""}</tr></thead>
      <tbody>${d.values.length ? d.values.map((v) => `<tr>
        <td><b>${esc(v.name || "(Standard)")}</b></td><td>${esc(v.type)}</td>
        <td style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(String(v.data))}">${esc(String(v.data))}</td>
        ${canEdit ? `<td style="white-space:nowrap;text-align:right">
          <button class="btn-tool" data-edit="${esc(v.name)}" data-type="${esc(v.type)}" data-val="${esc(String(v.data))}" title="Bearbeiten">✏️</button>
          <button class="btn-tool" data-del="${esc(v.name)}" title="Löschen">🗑️</button></td>` : ""}</tr>`).join("")
        : `<tr><td colspan="4" style="color:var(--subtext)">Keine Werte.</td></tr>`}</tbody></table>
      ${canEdit ? `<div style="margin-top:10px"><button class="btn btn-sm" data-new>＋ Wert anlegen</button></div>` : ""}`;
    view.querySelectorAll("[data-sub]").forEach((b) => b.addEventListener("click", () => { $("[data-path]").value = (base ? base + "\\" : "") + b.dataset.sub; load(); }));
    view.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => valueDialog(b.dataset.edit, b.dataset.type, b.dataset.val)));
    view.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm(`Wert „${b.dataset.del}“ löschen?`, { okText: "Löschen", danger: true }))) return;
      const r = await call("delete", { name: b.dataset.del });
      msg.innerHTML = r.ok ? "✓ gelöscht" : `<span style="color:var(--danger)">${esc(r.error)}</span>`;
      if (r.ok) load();
    }));
    view.querySelector("[data-new]")?.addEventListener("click", () => valueDialog("", "REG_SZ", ""));
  };

  function valueDialog(name, type, val) {
    const d2 = innerDialog(root, `<b style="font-size:14px">${name ? "Wert bearbeiten" : "Neuer Wert"}</b>
      <div style="display:grid;grid-template-columns:90px 1fr;gap:8px;align-items:center;margin-top:12px">
        <label>Name</label><input data-n value="${esc(name)}" ${name ? "readonly" : ""}>
        <label>Typ</label><select data-t>${["REG_SZ", "REG_EXPAND_SZ", "REG_DWORD", "REG_QWORD", "REG_MULTI_SZ", "REG_BINARY"].map((x) => `<option ${x === type ? "selected" : ""}>${x}</option>`).join("")}</select>
        <label>Daten</label><input data-d value="${esc(val)}"></div>
      <div style="font-size:11px;color:var(--subtext);margin-top:8px">DWORD/QWORD: Zahl · MULTI_SZ: Zeilen mit \\n · BINARY: Hex</div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px"><button class="btn" data-x2>Abbrechen</button><button class="btn btn-primary" data-ok>Speichern</button></div>`);
    d2.querySelector("[data-x2]").addEventListener("click", () => d2.remove());
    d2.querySelector("[data-ok]").addEventListener("click", async () => {
      const r = await call("set", { name: d2.querySelector("[data-n]").value.trim(), type: d2.querySelector("[data-t]").value, data: d2.querySelector("[data-d]").value });
      if (r.ok) { d2.remove(); load(); } else { window.notify?.(r.error, "error", 6000); }
    });
  }

  $("[data-go]").addEventListener("click", load);
  $("[data-up]").addEventListener("click", () => {
    const p = $("[data-path]").value.replace(/^\\+|\\+$/g, "").split("\\"); p.pop();
    $("[data-path]").value = p.join("\\"); load();
  });
  $("[data-path]").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
}

// ============================================================ Windows-Updates
export function renderWinUpdates(body, win) {
  const client = clientOf(win);
  const canToggle = hasClientPerm(client.id, "c_win_update");
  const withPatching = isModuleOn("patching") && hasClientPerm(client.id, "c_patch_view");
  body.innerHTML = `
    <div style="height:100%;overflow:auto;box-sizing:border-box;padding:14px 16px 18px;font-size:13px;display:flex;flex-direction:column;gap:12px">
      <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;padding:12px 14px">
        <b style="display:block;margin-bottom:8px">Automatische Windows-Updates</b>
        <div data-status style="color:var(--subtext)">⏳ Status wird abgefragt …</div>
        <div data-msg style="font-size:12px;margin-top:6px;min-height:14px"></div>
        <div style="font-size:12px;color:var(--subtext);margin-top:4px">Auch bei ausgeschalteter Automatik lassen sich Updates jederzeit unten bzw. über die Update-Funktion suchen und installieren.</div>
      </div>
      ${withPatching ? `<div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;min-height:260px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><b style="flex:1">Updates / Patching</b>
          <button class="btn btn-sm" data-open-patching title="Volle Updates-App (Verlauf, Regeln) für diesen Client">🔄 Updates-App öffnen</button></div>
        <div data-patches class="actions-panel" style="flex:1;min-height:0;overflow:auto"></div>
      </div>` : `<div style="font-size:12px;color:var(--subtext)">${isModuleOn("patching") ? "Kein Recht „Patches ansehen“ auf diesem Client." : "Modul „Patching“ ist aus – Updates suchen/installieren gibt es, sobald es aktiviert ist."}</div>`}
    </div>`;
  const root = body.firstElementChild;
  const $ = (s) => root.querySelector(s);
  const onWu = (d) => {
    if (d.id !== client.id) return;
    if (d.error) { $("[data-msg]").innerHTML = `<span style="color:var(--danger)">${esc(d.error)}</span>`; return; }
    $("[data-msg]").textContent = "";
    const off = !!d.auto_disabled;
    $("[data-status]").innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span>Status: <b style="color:${off ? "var(--warning,#d97706)" : "var(--online,#16a34a)"}">${off ? "AUS" : "AN"}</b></span>
        ${canToggle ? `<button class="btn btn-sm ${off ? "btn-primary" : "btn-danger"}" data-toggle>${off ? "Automatik einschalten" : "Automatik ausschalten"}</button>` : ""}
      </div>`;
    $("[data-toggle]")?.addEventListener("click", () => {
      $("[data-msg]").textContent = "⏳ …";
      dashboardSocket.emit("wu-auto", { clientId: client.id, username: me(), disabled: !off });
    });
  };
  dashboardSocket.on("wu-auto", onWu);
  if (win?.key) registerCleanup(win.key, () => dashboardSocket.off("wu-auto", onWu));
  if (client.online) dashboardSocket.emit("wu-auto", { clientId: client.id, username: me() });
  else $("[data-status]").innerHTML = `<span style="color:var(--subtext)">Agent ist offline – Status nicht abrufbar.</span>`;

  if (withPatching) {
    import("/js/app/panel.js").then((m) => m.renderPatchesPart($("[data-patches]"), client))
      .catch((e) => { $("[data-patches]").textContent = e.message; });
    $("[data-open-patching]").addEventListener("click", () => {
      import("/js/app/windowmanager.js").then(({ openWindow }) => openWindow({
        key: `patching-${client.id}`, appId: "patching", title: `Updates – ${client.hostname || client.id}`,
        props: { clientId: client.id }, w: 940, h: 700 }));
    });
  }
}

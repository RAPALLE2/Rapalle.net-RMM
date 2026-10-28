// init.js (Modul "winadmin") - Windows: Registry-Editor + Automatik-Updates
// --------------------------------------------------------------------------
// Fügt bei WINDOWS-Maschinen zwei Panel-Aktionen hinzu:
//   🗝️ Registry   (Recht c_registry) - lesen/schreiben/löschen
//   ⬇️ Updates    (Recht c_win_update/c_patch_view) - Automatik-Updates aus/ein;
//                 installiert wird weiterhin über die Update-/Patch-Funktion.

import { registerClientAction } from "/js/app/modules.js";
import { dashboardSocket } from "/js/app/socket.js";
import { state } from "/js/app/state.js";
import { hasClientPerm } from "/js/app/state.js";
import { esc, uiConfirm } from "/js/app/utils.js";

const isWin = (c) => (c.platform || "").toLowerCase().includes("win");
const me = () => state.user?.username || "";

export function init() {
  registerClientAction("winadmin", {
    label(client) {
      if (!isWin(client) || !client.online) return null;
      if (!hasClientPerm(client.id, "c_registry")) return null;
      return "🗝️ Registry";
    },
    onClick(client) { registryDialog(client); },
  });
  registerClientAction("winadmin", {
    label(client) {
      if (!isWin(client) || !client.online) return null;
      if (!hasClientPerm(client.id, "c_win_update") && !hasClientPerm(client.id, "c_patch_view")) return null;
      return "⬇️ Windows-Updates";
    },
    onClick(client) { updateDialog(client); },
  });
}

function overlay(html, w = 640) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:12px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px 18px;width:${w}px;max-width:100%;max-height:calc(100dvh - 24px);overflow:auto;font-size:13px">${html}</div>`;
  document.body.appendChild(ov);
  ov.addEventListener("click", (e) => { if (e.target === ov) ov.remove(); });
  return ov;
}

// ============================================================ Registry
const HIVES = ["HKLM", "HKCU", "HKCR", "HKU", "HKCC"];

function registryDialog(client) {
  const canEdit = hasClientPerm(client.id, "c_registry");
  const ov = overlay(`
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><b style="font-size:15px;flex:1">🗝️ Registry – ${esc(client.hostname || client.id)}</b>
      <button class="btn btn-sm" data-x>Schließen</button></div>
    <div style="display:flex;gap:6px;margin-bottom:8px">
      <select data-hive>${HIVES.map((h) => `<option>${h}</option>`).join("")}</select>
      <input data-path placeholder="SOFTWARE\\Microsoft\\Windows" style="flex:1">
      <button class="btn btn-sm" data-go>Öffnen</button>
    </div>
    <div data-body style="min-height:160px;border:1px solid var(--border);border-radius:8px;padding:8px">Pfad eingeben und „Öffnen“.</div>
    <div data-msg style="font-size:12px;margin-top:6px;min-height:14px"></div>`, 720);
  const $ = (s) => ov.querySelector(s);
  $("[data-x]").addEventListener("click", () => ov.remove());
  const body = $("[data-body]"), msg = $("[data-msg]");
  let rid = 0;
  const pending = {};

  const onResult = (d) => {
    if (d.id !== client.id) return;
    const cb = pending[d.rid]; if (cb) { delete pending[d.rid]; cb(d); }
  };
  dashboardSocket.on("registry-result", onResult);
  const mo = new MutationObserver(() => { if (!document.body.contains(ov)) { dashboardSocket.off("registry-result", onResult); mo.disconnect(); } });
  mo.observe(document.body, { childList: true });

  const call = (op, extra = {}) => new Promise((resolve) => {
    const id = "r" + (++rid);
    pending[id] = resolve;
    const path = $("[data-hive]").value + "\\" + $("[data-path]").value.replace(/^\\+/, "");
    dashboardSocket.emit("registry", { clientId: client.id, username: me(), op, rid: id, path, ...extra });
    setTimeout(() => { if (pending[id]) { delete pending[id]; resolve({ ok: false, error: "Zeitüberschreitung" }); } }, 15000);
  });

  const load = async () => {
    body.innerHTML = "⏳ …"; msg.textContent = "";
    const d = await call("list");
    if (!d.ok) { body.innerHTML = `<span style="color:var(--danger)">${esc(d.error || "Fehler")}</span>`; return; }
    const base = $("[data-path]").value.replace(/^\\+|\\+$/g, "");
    body.innerHTML = `
      ${d.subkeys.length ? `<div style="font-size:11px;color:var(--subtext);margin-bottom:3px">Unterschlüssel</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px">${d.subkeys.map((k) =>
          `<button class="btn btn-sm" data-sub="${esc(k)}">📁 ${esc(k)}</button>`).join("")}</div>` : ""}
      <div style="font-size:11px;color:var(--subtext);margin-bottom:3px">Werte</div>
      <table class="data-table" style="width:100%;font-size:12.5px"><thead><tr><th style="text-align:left">Name</th><th style="text-align:left">Typ</th><th style="text-align:left">Daten</th>${canEdit ? "<th></th>" : ""}</tr></thead>
      <tbody>${d.values.length ? d.values.map((v) => `<tr>
        <td><b>${esc(v.name)}</b></td><td>${esc(v.type)}</td>
        <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(String(v.data))}</td>
        ${canEdit ? `<td style="white-space:nowrap;text-align:right">
          <button class="btn-tool" data-edit="${esc(v.name)}" data-type="${esc(v.type)}" data-val="${esc(String(v.data))}" title="Bearbeiten">✏️</button>
          <button class="btn-tool" data-del="${esc(v.name)}" title="Löschen">🗑️</button></td>` : ""}</tr>`).join("")
        : `<tr><td colspan="4" style="color:var(--subtext)">Keine Werte.</td></tr>`}</tbody></table>
      ${canEdit ? `<div style="margin-top:8px"><button class="btn btn-sm" data-new>＋ Wert anlegen</button></div>` : ""}`;
    body.querySelectorAll("[data-sub]").forEach((b) => b.addEventListener("click", () => { $("[data-path]").value = (base ? base + "\\" : "") + b.dataset.sub; load(); }));
    body.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => valueDialog(b.dataset.edit, b.dataset.type, b.dataset.val)));
    body.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm(`Wert „${b.dataset.del}“ löschen?`, { okText: "Löschen", danger: true }))) return;
      const r = await call("delete", { name: b.dataset.del }); msg.innerHTML = r.ok ? "✓ gelöscht" : `<span style="color:var(--danger)">${esc(r.error)}</span>`; if (r.ok) load();
    }));
    body.querySelector("[data-new]")?.addEventListener("click", () => valueDialog("", "REG_SZ", ""));
  };

  function valueDialog(name, type, val) {
    const d2 = overlay(`<b style="font-size:14px">${name ? "Wert bearbeiten" : "Neuer Wert"}</b>
      <div style="display:grid;grid-template-columns:90px 1fr;gap:8px;align-items:center;margin-top:10px">
        <label>Name</label><input data-n value="${esc(name)}" ${name ? "readonly" : ""}>
        <label>Typ</label><select data-t>${["REG_SZ", "REG_EXPAND_SZ", "REG_DWORD", "REG_QWORD", "REG_MULTI_SZ", "REG_BINARY"].map((x) => `<option ${x === type ? "selected" : ""}>${x}</option>`).join("")}</select>
        <label>Daten</label><input data-d value="${esc(val)}"></div>
      <div style="font-size:11px;color:var(--subtext);margin-top:6px">DWORD/QWORD: Zahl · MULTI_SZ: Zeilen mit \\n · BINARY: Hex</div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px"><button class="btn" data-x2>Abbrechen</button><button class="btn-primary" data-ok>Speichern</button></div>`, 440);
    d2.querySelector("[data-x2]").addEventListener("click", () => d2.remove());
    d2.querySelector("[data-ok]").addEventListener("click", async () => {
      const r = await call("set", { name: d2.querySelector("[data-n]").value.trim(), type: d2.querySelector("[data-t]").value, data: d2.querySelector("[data-d]").value });
      if (r.ok) { d2.remove(); load(); } else { window.notify?.(r.error, "error", 6000); }
    });
  }

  $("[data-go]").addEventListener("click", load);
  $("[data-path]").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });
}

// ============================================================ Windows-Updates (Automatik)
function updateDialog(client) {
  const canToggle = hasClientPerm(client.id, "c_win_update");
  const ov = overlay(`
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px"><b style="font-size:15px;flex:1">⬇️ Windows-Updates – ${esc(client.hostname || client.id)}</b>
      <button class="btn btn-sm" data-x>Schließen</button></div>
    <div data-status style="color:var(--subtext)">⏳ Status wird abgefragt …</div>
    <div style="font-size:12px;color:var(--subtext);margin-top:10px">Hinweis: Auch bei ausgeschalteter Automatik lassen sich Updates jederzeit über die <b>Update-Funktion</b> suchen und installieren.</div>
    <div data-msg style="font-size:12px;margin-top:8px;min-height:14px"></div>`, 520);
  const $ = (s) => ov.querySelector(s);
  $("[data-x]").addEventListener("click", () => ov.remove());
  const onWu = (d) => {
    if (d.id !== client.id) return;
    if (d.error) { $("[data-msg]").innerHTML = `<span style="color:var(--danger)">${esc(d.error)}</span>`; return; }
    const off = !!d.auto_disabled;
    $("[data-status]").innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span>Automatische Updates: <b style="color:${off ? "var(--warning,#d97706)" : "var(--online,#16a34a)"}">${off ? "AUS" : "AN"}</b></span>
        ${canToggle ? `<button class="btn btn-sm ${off ? "btn-primary" : "btn-danger"}" data-toggle>${off ? "Automatik einschalten" : "Automatik ausschalten"}</button>` : ""}
      </div>`;
    $("[data-toggle]")?.addEventListener("click", async () => {
      $("[data-msg]").textContent = "⏳ …";
      dashboardSocket.emit("wu-auto", { clientId: client.id, username: me(), disabled: !off });
    });
  };
  dashboardSocket.on("wu-auto", onWu);
  const mo = new MutationObserver(() => { if (!document.body.contains(ov)) { dashboardSocket.off("wu-auto", onWu); mo.disconnect(); } });
  mo.observe(document.body, { childList: true });
  dashboardSocket.emit("wu-auto", { clientId: client.id, username: me() });   // Status abfragen
}

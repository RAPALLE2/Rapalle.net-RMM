// client.js (Modul "client")
// --------------------------
// 1) Profil -> "Client-App & Geräte": Download (passend zum eigenen System)
//    und "Meine Geräte" (wo ist die App mit meinem Konto angemeldet) mit
//    Umbenennen/Abmelden. Abmelden wirkt sofort.
// 2) Modul-Einstellungen: Installer hochladen/löschen, alle Geräte (Admins).
// Backend: client_routes.py (/api/client)

import { getToken } from "/js/app/api.js";
import { esc, uiConfirm, uiPrompt } from "/js/app/utils.js";

async function creq(path, opts = {}) {
  const token = getToken() || "";
  const headers = { ...(token ? { Authorization: "Bearer " + token } : {}) };
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch("/api/client" + path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* egal */ }
    throw new Error(msg);
  }
  return (res.headers.get("content-type") || "").includes("json") ? res.json() : res;
}

const PLAT = {
  windows: { icon: "🪟", label: "Windows 10/11" },
  linux: { icon: "🐧", label: "Linux" },
  android: { icon: "🤖", label: "Android" },
};
const fmtSize = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : b >= 1024 ? Math.round(b / 1024) + " KB" : b + " B");
const ago = (ms) => {
  const s = (Date.now() - ms) / 1000;
  if (s < 90) return "gerade eben";
  if (s < 3600) return `vor ${Math.round(s / 60)} Min.`;
  if (s < 86400) return `vor ${Math.round(s / 3600)} Std.`;
  return new Date(ms).toLocaleDateString();
};
function myPlatform() {
  const ua = navigator.userAgent || "";
  if (/Android/i.test(ua)) return "android";
  if (/Windows/i.test(ua)) return "windows";
  if (/Linux|X11/i.test(ua)) return "linux";
  return null;
}


// ------------------------------------------------------------------ Profil
async function distInfo() {
  const res = await fetch("/client-dist/info");
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

const CMD_LABEL = {
  powershell: "Windows · PowerShell",
  cmd: "Windows · Eingabeaufforderung (CMD)",
  shell: "Linux · Terminal",
};

export async function renderProfileSection(host) {
  host.innerHTML = `<div style="color:var(--subtext);font-size:13px">Lade …</div>`;
  let info, devs;
  try {
    [info, devs] = await Promise.all([distInfo(), creq("/devices")]);
  } catch (e) {
    host.innerHTML = `<div style="color:var(--danger);font-size:13px">${esc(e.message)}</div>`;
    return;
  }
  const mine = myPlatform();
  const plats = Object.keys(PLAT);
  const byPlat = (p) => info.files.filter((f) => f.platform === p);
  host.innerHTML = `
    <p style="color:var(--subtext);font-size:13px;margin-top:0">
      Die Client-App ist das Dashboard als eigene App: Anmeldung bleibt gespeichert, das Relay erscheint als Laufwerk
      im Datei-Explorer, Remote-Shell im eigenen Fenster und Fernbild über Remotedesktop/VNC.</p>
    <div style="font-weight:600;font-size:13px;margin:4px 0 6px">Per Befehl installieren</div>
    ${Object.entries(info.commands).filter(([k]) => k !== "shell" || byPlat("linux").length)
      .filter(([k]) => k === "shell" || byPlat("windows").some((f) => f.ext === ".exe")).map(([k, cmd]) => `
      <div style="margin-bottom:8px">
        <div style="font-size:11.5px;color:var(--subtext);margin-bottom:3px">${CMD_LABEL[k]}</div>
        <div style="display:flex;gap:6px;align-items:center">
          <code style="flex:1;min-width:0;overflow-x:auto;white-space:nowrap;background:var(--panel-2);border:1px solid var(--border);
                border-radius:6px;padding:6px 8px;font-size:12px">${esc(cmd)}</code>
          <button class="btn btn-sm" data-copy="${esc(cmd)}">Kopieren</button>
        </div>
      </div>`).join("") || `<div style="color:var(--subtext);font-size:12.5px">Noch keine Pakete auf dem Server.</div>`}
    <div style="font-weight:600;font-size:13px;margin:14px 0 6px">Als Datei oder Paket</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-bottom:16px">
      ${plats.map((p) => {
        const list = byPlat(p);
        return `<div style="border:1px solid ${p === mine ? "var(--accent)" : "var(--border)"};border-radius:8px;padding:10px;
                    display:flex;flex-direction:column;gap:6px">
          <div style="font-weight:600">${PLAT[p].icon} ${PLAT[p].label}${p === mine ? ` <span style="font-size:11px;color:var(--accent)">· dein System</span>` : ""}</div>
          ${list.length ? list.map((f) => `
            <a class="${p === mine && f === list[0] ? "btn-primary" : "btn"} btn-sm" href="${esc(f.url)}" download
               title="${esc(f.name)} · ${fmtSize(f.size)}" style="justify-content:space-between">
              <span>⬇ ${esc(f.ext.replace(".", "").toUpperCase())} <span style="opacity:.7;font-weight:400">${esc(f.kind)}</span></span>
              <span style="opacity:.7;font-weight:400">${fmtSize(f.size)}</span></a>`).join("")
            : `<div style="font-size:11.5px;color:var(--subtext)">Noch nicht bereitgestellt.</div>`}
          ${list[0]?.version ? `<div style="font-size:11px;color:var(--subtext)">Version ${esc(list[0].version)}</div>` : ""}
        </div>`;
      }).join("")}
    </div>
    <h4 style="margin:0 0 6px">Meine Geräte</h4>
    <div data-devs></div>`;
  host.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => {
    navigator.clipboard?.writeText(b.dataset.copy);
    window.notify?.("Befehl kopiert", "success");
  }));
  drawDevices(host.querySelector("[data-devs]"), devs.devices, () => renderProfileSection(host));
}

function drawDevices(box, list, reload) {
  box.innerHTML = list.length ? `
    <table class="data-table" style="width:100%">
      <thead><tr><th style="text-align:left">Gerät</th><th>System</th><th>App</th><th>Zuletzt aktiv</th><th></th></tr></thead>
      <tbody>${list.map((d) => `
        <tr>
          <td>${PLAT[d.platform]?.icon || "💻"} <b>${esc(d.name)}</b>${d.current ? ` <span style="font-size:11px;color:var(--accent)">· dieses Gerät</span>` : ""}
            ${d.username && list.some((x) => x.username !== d.username) ? `<div style="font-size:11px;color:var(--subtext)">${esc(d.username)}</div>` : ""}</td>
          <td style="font-size:12px;color:var(--subtext)">${esc(d.os || d.platform)}</td>
          <td style="font-size:12px;color:var(--subtext)">${esc(d.app_version || "")}</td>
          <td style="font-size:12px;color:var(--subtext);white-space:nowrap" title="${esc(d.last_ip || "")}">${ago(d.last_seen)}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="btn-tool" data-ren="${esc(d.id)}" title="Umbenennen">✏️</button>
            <button class="btn btn-sm btn-danger" data-rm="${esc(d.id)}">Abmelden</button></td>
        </tr>`).join("")}</tbody></table>`
    : `<div style="color:var(--subtext);font-size:13px">Auf keinem Gerät ist die Client-App mit deinem Konto angemeldet.</div>`;
  box.querySelectorAll("[data-ren]").forEach((b) => b.addEventListener("click", async () => {
    const d = list.find((x) => x.id === b.dataset.ren);
    const n = await uiPrompt("Gerät umbenennen", { value: d.name });
    if (!n?.trim()) return;
    try { await creq(`/devices/${d.id}`, { method: "PATCH", body: JSON.stringify({ name: n.trim() }) }); reload(); }
    catch (e) { window.notify?.(e.message, "error"); }
  }));
  box.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => {
    const d = list.find((x) => x.id === b.dataset.rm);
    if (!(await uiConfirm(`„${d.name}“ abmelden?`, {
      description: d.current ? "Das ist dieses Gerät – du wirst sofort abgemeldet." : "Die App auf diesem Gerät wird sofort abgemeldet und muss sich neu anmelden.",
      okText: "Abmelden", danger: true }))) return;
    try {
      await creq(`/devices/${d.id}`, { method: "DELETE" });
      window.notify?.("Gerät abgemeldet", "success");
      if (d.current) location.reload(); else reload();
    } catch (e) { window.notify?.(e.message, "error"); }
  }));
}

// ------------------------------------------------------------------ Modul-Einstellungen
export async function renderSettings(root) {
  root.innerHTML = `<div style="font-size:13px">
    <h3 style="margin-top:0">Installer</h3>
    <p style="color:var(--subtext)">Mitgeliefert sind fertige Pakete für Windows (<code>.exe</code>, <code>.msi</code>, <code>.zip</code>),
      Linux (<code>.deb</code>, <code>.tar</code>, <code>.tar.gz</code>) und Android (<code>.apk</code>) aus <code>src/client/release</code>.
      Eigene Builds (<code>python3 src/client/build_installers.py</code>) oder neuere Versionen hier hochladen –
      hochgeladene Dateien haben Vorrang. Benutzer finden alles unter Profil → „Client-App &amp; Geräte“.</p>
    <div style="display:flex;gap:8px;margin-bottom:8px"><button class="btn-primary btn-sm" data-up>⬆ Installer hochladen</button>
      <input type="file" data-file accept=".exe,.msi,.msix,.deb,.rpm,.AppImage,.appimage,.apk,.aab,.flatpak" multiple hidden></div>
    <div data-inst></div>
    <h3>Alle Geräte</h3>
    <div data-all style="color:var(--subtext)">Lade …</div>
  </div>`;
  const drawInst = async () => {
    const box = root.querySelector("[data-inst]");
    try {
      const r = await creq("/downloads");
      box.innerHTML = r.downloads.length ? r.downloads.map((d) => `
        <div style="display:flex;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">
          <span>${PLAT[d.platform].icon}</span><span style="flex:1">${esc(d.name)}</span>
          <span style="color:var(--subtext);font-size:12px">${fmtSize(d.size)}</span>
          ${d.source === "upload" ? `<button class="btn btn-sm btn-danger" data-del="${esc(d.name)}">Löschen</button>`
            : `<span style="font-size:11.5px;color:var(--subtext)">mitgeliefert</span>`}</div>`).join("")
        : `<div style="color:var(--subtext)">Noch keine Installer bereitgestellt.</div>`;
      box.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
        if (!(await uiConfirm(`„${b.dataset.del}“ löschen?`, { okText: "Löschen", danger: true }))) return;
        await creq(`/installers/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); drawInst();
      }));
    } catch (e) { box.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; }
  };
  const fi = root.querySelector("[data-file]");
  root.querySelector("[data-up]").addEventListener("click", () => fi.click());
  fi.addEventListener("change", async () => {
    for (const f of fi.files) {
      const fd = new FormData(); fd.append("file", f);
      try { await creq("/installers", { method: "POST", body: fd }); window.notify?.(`${f.name} hochgeladen`, "success"); }
      catch (e) { window.notify?.(`${f.name}: ${e.message}`, "error"); }
    }
    fi.value = ""; drawInst();
  });
  drawInst();
  const all = root.querySelector("[data-all]");
  try {
    const r = await creq("/admin/devices");
    drawDevices(all, r.devices, () => renderSettings(root));
  } catch (e) { all.textContent = e.message.includes("manage_users") ? "Nur mit dem Recht „Benutzer verwalten“." : e.message; }
}

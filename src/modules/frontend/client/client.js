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
    <p style="color:var(--subtext)">Mitgeliefert sind fertige Pakete aus <code>src/client/release</code>. Selbst gebaute (unten,
      „Pakete bauen“) und hochgeladene Pakete liegen in <code>backend/storage/client/</code> und haben Vorrang.
      Benutzer finden alles unter Profil → „Client-App &amp; Geräte“.</p>
    <div style="display:flex;gap:8px;margin-bottom:8px"><button class="btn-primary btn-sm" data-up>⬆ Installer hochladen</button>
      <input type="file" data-file accept=".exe,.msi,.msix,.zip,.deb,.rpm,.xz,.gz,.tgz,.tar,.AppImage,.appimage,.apk,.aab,.flatpak" multiple hidden></div>
    <div data-inst></div>
    <h3>Pakete bauen</h3>
    <div data-build style="color:var(--subtext)">Lade …</div>
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
  drawBuild(root.querySelector("[data-build]"), drawInst);
  const all = root.querySelector("[data-all]");
  try {
    const r = await creq("/admin/devices");
    drawDevices(all, r.devices, () => renderSettings(root));
  } catch (e) { all.textContent = e.message.includes("manage_users") ? "Nur mit dem Recht „Benutzer verwalten“." : e.message; }
}

// ------------------------------------------------------------------ Pakete neu bauen
const FMT = {
  exe: "Windows .exe", zip: "Windows .zip", msi: "Windows .msi",
  deb: "Linux .deb", rpm: "Linux .rpm", pkg: "Arch .pkg.tar.xz", tar: "Linux .tar/.tar.gz", tgz: "Linux .tgz",
  apk: "Android .apk",
};
let buildTimer = null;

function envBox(env, b) {
  const fmtMB = (n) => (n ? ` · ${(n / 1048576).toFixed(0)} MB` : "");
  if (env.mode === "docker") {
    if (env.preparing) return `<div data-envlog-wrap style="margin-bottom:8px">
        <div>⏳ <b>Bau-Umgebung wird vorbereitet</b> – Docker-Image <code>${esc(env.image)}</code> mit allen Werkzeugen
          (Wine, Windows-Python + PyInstaller, wixl, rpm, JDK, Android). Das passiert einmalig beim Aktivieren des Moduls und
          dauert je nach Leitung einige Minuten.</div>
        <pre data-envlog style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px;max-height:200px;
             overflow:auto;font-size:11.5px;margin:6px 0 0">${esc((env.log || []).join("\n"))}</pre></div>`;
    if (env.ready) return `<p style="margin:0 0 8px">🐳 Bau-Umgebung bereit: <code>${esc(env.image)}</code>${fmtMB(env.size)} – alle Formate.
        <button class="btn btn-sm" data-prep="1" style="margin-left:6px">Neu vorbereiten</button></p>`;
    return `<div style="margin:0 0 8px">⚠ Bau-Umgebung fehlt${env.error ? ` – letzter Versuch: <span style="color:var(--danger)">${esc(env.error)}</span>` : ""}.
        <button class="btn-primary btn-sm" data-prep="0" style="margin-left:6px">Bau-Umgebung vorbereiten</button>
        ${env.log?.length ? `<pre style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px;max-height:160px;
             overflow:auto;font-size:11.5px;margin:6px 0 0">${esc(env.log.slice(-40).join("\n"))}</pre>` : ""}</div>`;
  }
  const why = env.docker && !env.socket
    ? "Das RMM läuft in Docker, aber ohne Docker-Socket (in docker-compose.yml unter „volumes“: <code>/var/run/docker.sock:/var/run/docker.sock</code>)."
    : "Das RMM läuft nicht in Docker.";
  if (env.mode === "native-windows" && env.preparing)
    return `<p style="margin:0 0 8px">⏳ Lade PyInstaller und WiX für die Windows-Pakete …</p>`;
  return `<p style="font-size:12px;margin:0 0 8px">ℹ ${why} Gebaut wird direkt auf dem Server mit den vorhandenen Werkzeugen
      (✖ = hier nicht möglich). <b>Alle Formate</b> gibt es im Docker-Betrieb: dort lädt das Modul beim Aktivieren ein Bau-Image.
      ${env.mode === "native-windows" && env.error ? `<br><span style="color:var(--danger)">${esc(env.error)}</span>` : ""}</p>`;
}

async function drawBuild(box, onDone) {
  let b;
  try { b = await creq("/build"); } catch (e) { box.textContent = e.message; return; }
  if (!b.available) { box.innerHTML = `<div>Der Client-Quellcode (<code>src/client</code>) fehlt auf diesem Server.</div>`; return; }
  if (b.running) return drawRunning(box, onDone);
  const t = b.tools || {};
  const env = b.env || {};
  const canBuild = Object.values(t).some(Boolean) && !env.preparing;
  box.innerHTML = `
    <p style="margin:0 0 8px">Baut die Client-Pakete direkt aus dem Quellcode neu (<code>src/client</code>) – wie die Agent-Pakete per
      Knopfdruck – und legt sie in <code>backend/storage/client/</code> ab. Benutzer bekommen sie sofort unter Profil → „Client-App &amp; Geräte“,
      die Apps per Auto-Update. Aktuelle Version im Quellcode: <b>${esc(b.version || "?")}</b>.</p>
    ${envBox(env, b)}
    <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
      ${Object.entries(FMT).map(([k, l]) => `<span style="font-size:12px;padding:3px 8px;border-radius:12px;border:1px solid var(--border);
        color:${t[k] ? "var(--accent)" : "var(--subtext)"}">${t[k] ? "✔" : "✖"} ${l}</span>`).join("")}
    </div>
    ${b.result ? `<p style="font-size:12px;margin:0 0 8px">Letzter Bau (${esc(b.by || "")}${b.where ? ", " + esc(b.where) : ""}, ${b.finished ? new Date(b.finished * 1000).toLocaleString() : ""}):
      ${b.result.stopped ? "abgebrochen" : b.result.ok ? "erfolgreich" : "fehlgeschlagen"} – ${b.result.built.length} Paket(e)
      ${b.result.failed.length ? `, Fehler: ${esc(b.result.failed.join("; "))}` : ""}</p>` : ""}
    <button class="btn-primary btn-sm" data-go ${canBuild ? "" : "disabled"}>🔨 Client-Pakete bauen</button>`;
  box.querySelector("[data-go]").addEventListener("click", () => askVersion(box, b, onDone));
  box.querySelectorAll("[data-prep]").forEach((btn) => btn.addEventListener("click", async () => {
    if (btn.dataset.prep === "1" && !(await uiConfirm("Bau-Umgebung neu vorbereiten?", {
      description: "Das Bau-Image wird neu angelegt (lädt alle Werkzeuge erneut herunter, einige Minuten).", okText: "Neu vorbereiten" }))) return;
    try { await creq("/build/prepare", { method: "POST", body: JSON.stringify({ force: btn.dataset.prep === "1" }) }); }
    catch (e) { window.notify?.(e.message, "error"); }
    drawBuild(box, onDone);
  }));
  // Waehrend der Vorbereitung regelmaessig auffrischen (Protokoll + Ende)
  clearInterval(buildTimer);
  if (env.preparing) {
    buildTimer = setInterval(async () => {
      if (!box.isConnected) { clearInterval(buildTimer); return; }
      let nb;
      try { nb = await creq("/build"); } catch { return; }
      const pre = box.querySelector("[data-envlog]");
      if (nb.env?.preparing && pre) {
        const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
        pre.textContent = (nb.env.log || []).join("\n");
        if (atEnd) pre.scrollTop = pre.scrollHeight;
        return;
      }
      clearInterval(buildTimer);
      if (nb.env && !nb.env.preparing)
        window.notify?.(nb.env.ready || nb.env.mode !== "docker" ? "Bau-Umgebung bereit" : "Bau-Umgebung fehlgeschlagen",
                        nb.env.ready || nb.env.mode !== "docker" ? "success" : "error", 6000);
      drawBuild(box, onDone);
    }, 2000);
    const pre = box.querySelector("[data-envlog]");
    if (pre) pre.scrollTop = pre.scrollHeight;
  }
}

function askVersion(box, b, onDone) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px;width:460px;max-width:100%;font-size:13px">
      <b style="font-size:15px">Client-Pakete bauen</b>
      <p style="color:var(--subtext);margin:6px 0 12px">Welche Version sollen die neuen Pakete bekommen?</p>
      <label style="display:flex;gap:8px;align-items:flex-start;margin-bottom:10px;cursor:pointer">
        <input type="radio" name="bv" value="replace" checked style="width:16px;height:16px;appearance:auto;margin-top:2px">
        <span><b>Version ${esc(b.version)} beibehalten</b><br><span style="color:var(--subtext)">Die vorhandenen ${esc(b.version)}-Pakete
          werden ersetzt. Bereits installierte Apps aktualisieren sich NICHT automatisch (gleiche Versionsnummer).</span></span></label>
      <label style="display:flex;gap:8px;align-items:flex-start;margin-bottom:12px;cursor:pointer">
        <input type="radio" name="bv" value="new" style="width:16px;height:16px;appearance:auto;margin-top:2px">
        <span><b>Neue Version</b> <input data-ver value="${esc(b.suggested)}" style="width:90px;height:24px;margin-left:4px"><br>
          <span style="color:var(--subtext)">Ältere Pakete werden entfernt; installierte Apps holen sich das Update automatisch.</span></span></label>
      <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn" data-x>Abbrechen</button>
        <button class="btn-primary" data-ok>Bauen</button></div></div>`;
  document.body.appendChild(ov);
  ov.querySelector("[data-ver]").addEventListener("focus", () => { ov.querySelector('input[value="new"]').checked = true; });
  ov.querySelector("[data-x]").addEventListener("click", () => ov.remove());
  ov.querySelector("[data-ok]").addEventListener("click", async () => {
    const mode = ov.querySelector('input[name="bv"]:checked').value;
    try {
      await creq("/build", { method: "POST", body: JSON.stringify({ mode, version: ov.querySelector("[data-ver]").value.trim() }) });
      ov.remove();
      drawRunning(box, onDone);
    } catch (e) { window.notify?.(e.message, "error"); }
  });
}

function drawRunning(box, onDone) {
  box.innerHTML = `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
      <span data-st style="flex:1">⏳ Baue Pakete …</span>
      <button class="btn btn-sm btn-danger" data-stop>■ Stopp</button></div>
    <pre data-log style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px;max-height:260px;
         overflow:auto;font-size:11.5px;margin:0"></pre>`;
  const log = box.querySelector("[data-log]");
  box.querySelector("[data-stop]").addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try { await creq("/build/stop", { method: "POST" }); } catch (err) { window.notify?.(err.message, "error"); }
  });
  clearInterval(buildTimer);
  const tick = async () => {
    if (!box.isConnected) { clearInterval(buildTimer); return; }
    let b;
    try { b = await creq("/build"); } catch { return; }
    log.textContent = (b.log || []).join("\n");
    log.scrollTop = log.scrollHeight;
    box.querySelector("[data-st]").textContent = `⏳ Baue Version ${b.building_version || ""} …`;
    if (!b.running) {
      clearInterval(buildTimer);
      const r = b.result || {};
      window.notify?.(r.stopped ? "Bauvorgang abgebrochen" : r.ok ? `${r.built.length} Paket(e) neu gebaut` : "Bauen fehlgeschlagen",
                      r.ok ? "success" : "error", 6000);
      onDone?.();
      drawBuild(box, onDone);
    }
  };
  buildTimer = setInterval(tick, 1500);
  tick();
}

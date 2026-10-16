// relayexplorer.js
// ----------------
// Explorer fuer das Relay im Dashboard.
//
// Register:
//   Clients      die per Explorer-Relay freigegebenen Clients (Mandant -> Standort
//                -> Ordner -> Client -> Laufwerke) - ueber dieselbe WebDAV-Schnittstelle
//                wie das Netzlaufwerk, angemeldet mit der Dashboard-Sitzung
// sowie die server-eigenen Ordner:
//   Spaces       eigene/Team-/Public Spaces und Freigaben (Modul "spaces", pro Benutzer)
//   Deployment   nur mit Recht "Deployment-Server verwalten"; zusaetzlich oeffentlich per Link erreichbar
//                unter /deployment/<datei>, sofern in den Einstellungen
//                freigegeben.
//
// Dieselben Ordner tauchen auch in WebDAV, FTP und SFTP auf - hier laeuft es
// nur ueber eine schlanke REST-API, damit Hoch- und Herunterladen im
// Dashboard ohne Netzlaufwerk funktioniert.

import { api } from "/js/app/api.js";
import { esc, uiConfirm, uiPrompt } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";
import { getModuleApi } from "/js/app/modules.js";

// ---------------------------------------------------------------- WebDAV (Register "Clients")
const CLIENTS = "Clients";
const davUrl = (p, dir = false) => "/dav/" + p.split("/").filter(Boolean).map(encodeURIComponent).join("/") + (dir && p ? "/" : "");
async function dav(method, p, { dir = false, body, headers = {} } = {}) {
  const token = localStorage.getItem("rmm_token") || "";
  const res = await fetch(davUrl(p, dir), { method, body, headers: { Authorization: `Bearer ${token}`, ...headers } });
  if (!res.ok && res.status !== 207) {
    const msg = res.status === 403 ? "Hier nicht erlaubt (nur lesen?)" : res.status === 404 ? "Nicht gefunden"
      : res.status === 503 ? "Relay nicht verfügbar" : `Fehler ${res.status}`;
    throw new Error(msg);
  }
  return res;
}
async function davList(p, hide = []) {
  const res = await dav("PROPFIND", p, { dir: true, headers: { Depth: "1" } });
  const xml = new DOMParser().parseFromString(await res.text(), "application/xml");
  const out = [];
  [...xml.getElementsByTagNameNS("DAV:", "response")].forEach((r, i) => {
    if (i === 0) return;                                        // der Ordner selbst
    const get = (tag) => r.getElementsByTagNameNS("DAV:", tag)[0]?.textContent || "";
    const href = decodeURIComponent(get("href").replace(/\/$/, "").split("/").pop() || "");
    const name = get("displayname") || href;
    if (!p && hide.includes(name)) return;                      // Server-Ordner haben eigene Register
    const mod = Date.parse(get("getlastmodified"));
    out.push({ name, is_dir: r.getElementsByTagNameNS("DAV:", "collection").length > 0,
               size: parseInt(get("getcontentlength") || "0", 10), mtime: Number.isFinite(mod) ? mod : 0 });
  });
  return out.sort((a, b) => (b.is_dir - a.is_dir) || a.name.localeCompare(b.name));
}

function fmtSize(n) {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}

function fmtDate(ms) {
  if (!ms) return "";
  try { return new Date(ms).toLocaleString(); } catch { return ""; }
}

export function renderRelayExplorer(body, win) {
  let sections = [];
  let section = win?.props?.section || "Spaces";
  let path = win?.props?.path || "";
  let writable = false;
  let publicOn = false;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;min-height:0">
      <div style="display:flex;gap:8px;align-items:center;padding:8px 10px;
                  border-bottom:1px solid var(--border);flex-wrap:wrap">
        <div id="rx-tabs" class="tab-bar"></div>
        <div style="flex:1"></div>
        <button class="btn" id="rx-up" title="${t("rx_up")}">⬆</button>
        <button class="btn" id="rx-refresh" title="${t("refresh")}">⟳</button>
        <button class="btn" id="rx-mkdir">＋ ${t("ec_folder")}</button>
        <button class="btn" id="rx-space" style="display:none"></button>
        <button class="btn-primary" id="rx-upload">⬆ Hochladen</button>
        <input type="file" id="rx-file" multiple style="display:none" />
      </div>
      <div id="rx-crumb" style="padding:6px 12px;font-size:12px;color:var(--subtext);
                                border-bottom:1px solid var(--border)"></div>
      <div id="rx-list" style="flex:1;overflow:auto;min-height:0"></div>
      <div id="rx-foot" style="padding:6px 12px;font-size:11.5px;color:var(--subtext);
                               border-top:1px solid var(--border)"></div>
    </div>`;

  const tabsEl = body.querySelector("#rx-tabs");
  const listEl = body.querySelector("#rx-list");
  const crumbEl = body.querySelector("#rx-crumb");
  const footEl = body.querySelector("#rx-foot");
  const fileEl = body.querySelector("#rx-file");

  function drawTabs() {
    tabsEl.innerHTML = sections.map((s) => `
      <button class="tab-btn${s.name === section ? " active" : ""}"
              data-sec="${esc(s.name)}">
        ${s.name === CLIENTS ? "🔌" : s.name === "Spaces" ? "☁️" : s.name === "Websites" ? "🌍" : s.name === "Deployment" ? "🌐" : "📦"} ${esc(s.name)}
      </button>`).join("");
    tabsEl.querySelectorAll("[data-sec]").forEach((b) =>
      b.addEventListener("click", () => {
        section = b.dataset.sec; path = ""; load();
      }));
  }

  function drawCrumb() {
    const parts = path.split("/").filter(Boolean);
    const links = [`<a href="#" data-crumb="">${esc(section)}</a>`];
    let acc = "";
    for (const p of parts) {
      acc = acc ? `${acc}/${p}` : p;
      links.push(`<a href="#" data-crumb="${esc(acc)}">${esc(p)}</a>`);
    }
    crumbEl.innerHTML = links.join(" / ");
    crumbEl.querySelectorAll("[data-crumb]").forEach((a) =>
      a.addEventListener("click", (e) => {
        e.preventDefault(); path = a.dataset.crumb; load();
      }));
  }

  async function load() {
    listEl.innerHTML = `<div style="padding:20px;color:var(--subtext)">Lade…</div>`;
    try {
      if (!sections.length) {
        const meta = await api.storageSections();
        sections = [...(meta.sections || []), { name: CLIENTS }];
        publicOn = !!meta.deployment_public;
        if (!sections.some((s) => s.name === section)) section = sections[0]?.name || "Spaces";
      }
      drawTabs();
      let res;
      if (section === CLIENTS) {
        // Ebenen Mandant/Standort/Client sind nur lesbar - Schreiben ab den Laufwerken (Server prueft).
        res = { entries: await davList(path, sections.map((x) => x.name)), writable: path.split("/").filter(Boolean).length >= 2 };
      } else {
        res = await api.storageList(section, path);
      }
      writable = !!res.writable;
      drawCrumb();
      drawSpaceTools();
      drawList(res.entries || []);
    } catch (e) {
      listEl.innerHTML = `<div style="padding:20px;color:var(--error,#f66)">${esc(e.message)}</div>`;
      footEl.textContent = "";
    }
  }

  // Spaces: Teamspaces/Public Spaces anlegen und Mitglieder verwalten (Modul "spaces")
  function drawSpaceTools() {
    const b = body.querySelector("#rx-space");
    const sp = getModuleApi("spaces");
    const parts = path.split("/").filter(Boolean);
    const kind = parts[0] === "Teams" ? "team" : parts[0] === "Öffentlich" ? "public" : null;
    b.style.display = "none";
    if (section !== "Spaces" || !sp || !kind || parts.length > 2) return;
    b.style.display = "";
    if (parts.length === 1) {
      b.textContent = kind === "team" ? "＋ Teamspace" : "＋ Public Space";
      b.onclick = () => sp.createSpace(kind, load);
    } else {
      b.textContent = "👥 Mitglieder & Einstellungen";
      b.onclick = () => sp.openSpaceSettings(kind, parts[1], () => { path = parts[0]; load(); });
    }
  }

  // Spaces: Sammelebenen ("Spaces", "Teams", "Öffentlich", "Mit mir geteilt", "Alle Spaces")
  // enthalten nur Spaces - dort nichts hochladen/loeschen (Teamspaces anlegen per "＋ Teamspace"/"＋ Ordner").
  function spacesLevel() {
    // Websites: in der Wurzel liegen nur die Websites selbst (anlegen/loeschen in der App "Webserver")
    if (section === "Websites") {
      const top = path.split("/").filter(Boolean).length === 0;
      return { collection: top, spaceRows: top };
    }
    if (section !== "Spaces") return { collection: false, spaceRows: false };
    const parts = path.split("/").filter(Boolean);
    const groups = ["Teams", "Öffentlich", "Mit mir geteilt", "Alle Spaces"];
    const collection = parts.length === 0 || (parts.length === 1 && groups.includes(parts[0]))
      || (parts[0] === "Alle Spaces" && parts.length <= 2);
    return { collection, spaceRows: collection };
  }

  function drawList(entries) {
    const lvl = spacesLevel();
    const canMk = writable && !(section === "Spaces" && path.split("/").filter(Boolean).length === 0)
      && !(lvl.collection && !["Teams", "Öffentlich"].includes(path));
    body.querySelector("#rx-mkdir").style.display = canMk ? "" : "none";
    body.querySelector("#rx-upload").style.display = writable && !lvl.collection ? "" : "none";

    if (!entries.length) {
      listEl.innerHTML = `<div style="padding:20px;color:var(--subtext)">
        Dieser Ordner ist leer.${writable ? "" : " (Nur lesen erlaubt.)"}</div>`;
    } else {
      listEl.innerHTML = entries.map((e) => {
        const p = path ? `${path}/${e.name}` : e.name;
        // Nur bei Deployment und nur fuer Dateien: der oeffentliche Link.
        const pub = (section === "Deployment" && publicOn && !e.is_dir)
          ? `<a class="btn btn-sm" target="_blank"
                rel="noopener" href="/deployment/${p.split("/").map(encodeURIComponent).join("/")}"
                title="${t("rx_public_link")}">🔗</a>` : "";
        return `
        <div class="tree-row" data-path="${esc(p)}" data-dir="${e.is_dir ? "1" : "0"}"
             style="display:flex;gap:10px;align-items:center;padding:6px 12px;
                    border-bottom:1px solid var(--border)">
          <span style="width:18px">${e.is_dir ? "📁" : "📄"}</span>
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                data-open>${esc(e.name)}</span>
          <span style="width:80px;text-align:right;font-size:11.5px;color:var(--subtext)">${fmtSize(e.size)}</span>
          <span style="width:150px;text-align:right;font-size:11.5px;color:var(--subtext)">${esc(fmtDate(e.mtime))}</span>
          ${pub}
          ${e.is_dir ? "" : `<a class="btn btn-sm"
              href="#" data-dl title="${t("rx_download")}">⬇</a>`}
          ${writable && !(lvl.spaceRows && !["Teams", "Öffentlich"].includes(path)) ? `<button class="btn btn-sm" data-ren title="${t("exp_rename")}">✏</button>` : ""}
          ${writable && !lvl.spaceRows ? `<button class="btn btn-sm btn-danger" data-del title="${t("delete")}">🗑</button>` : ""}
        </div>`;
      }).join("");
    }

    listEl.querySelectorAll("[data-path]").forEach((row) => {
      const p = row.dataset.path;
      const isDir = row.dataset.dir === "1";
      row.querySelector("[data-open]")?.addEventListener("click", () => {
        if (isDir) { path = p; load(); }
        else download(p);
      });
      // Der Download-Link braucht den Auth-Header, deshalb abfangen.
      row.querySelector("[data-dl]")?.addEventListener("click", (e) => {
        e.preventDefault(); download(p);
      });
      row.querySelector("[data-del]")?.addEventListener("click", async () => {
        const ok = await uiConfirm(t("rx_delete_q", { name: p.split("/").pop() }), {
          description: isDir ? t("rx_delete_dir_desc") : "" });
        if (!ok) return;
        try {
          if (section === CLIENTS) await dav("DELETE", p, { dir: isDir }); else await api.storageDelete(section, p);
          load();
        }
        catch (err) { window.notify?.(err.message, "error", 8000); }
      });
      row.querySelector("[data-ren]")?.addEventListener("click", async () => {
        const old = p.split("/").pop();
        const name = await uiPrompt(t("rx_new_name"), { value: old });
        if (!name || name === old) return;
        const dst = path ? `${path}/${name}` : name;
        try {
          if (section === CLIENTS) {
            await dav("MOVE", p, { dir: isDir, headers: { Destination: location.origin + davUrl(dst, isDir), Overwrite: "F" } });
          } else await api.storageMove(section, p, dst);
          load();
        }
        catch (err) { window.notify?.(err.message, "error", 8000); }
      });
    });

    const pubHint = (section === "Deployment")
      ? (publicOn
        ? " · " + t("rx_public_on")
        : " · " + t("rx_public_off"))
      : "";
    footEl.textContent = `${t("dw_entries", { n: entries.length })}${writable ? "" : " · " + t("rx_readonly")}${pubHint}`;
  }

  // Download mit Auth-Header (die API akzeptiert kein Cookie).
  async function download(p) {
    try {
      const token = localStorage.getItem("rmm_token");
      const res = section === CLIENTS ? await dav("GET", p) : await fetch(api.storageDownloadUrl(section, p), {
        headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`Download fehlgeschlagen (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = p.split("/").pop();
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
      window.notify?.(e.message, "error", 8000);
    }
  }

  body.querySelector("#rx-up").addEventListener("click", () => {
    if (!path) return;
    path = path.split("/").slice(0, -1).join("/");
    load();
  });
  body.querySelector("#rx-refresh").addEventListener("click", load);

  body.querySelector("#rx-mkdir").addEventListener("click", async () => {
    const name = await uiPrompt("Name des neuen Ordners");
    if (!name) return;
    try {
      const np = path ? `${path}/${name}` : name;
      if (section === CLIENTS) await dav("MKCOL", np, { dir: true }); else await api.storageMkdir(section, np);
      load();
    }
    catch (e) { window.notify?.(e.message, "error", 8000); }
  });

  body.querySelector("#rx-upload").addEventListener("click", () => fileEl.click());
  fileEl.addEventListener("change", async () => {
    const files = [...fileEl.files];
    fileEl.value = "";
    for (const f of files) {
      try {
        if (section === CLIENTS) await dav("PUT", path ? `${path}/${f.name}` : f.name, { body: f });
        else await api.storageUpload(section, path, f);
      }
      catch (e) { window.notify?.(`${f.name}: ${e.message}`, "error", 9000); }
    }
    load();
  });

  load();
}

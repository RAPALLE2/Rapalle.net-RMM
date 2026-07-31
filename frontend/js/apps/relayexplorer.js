// relayexplorer.js
// ----------------
// Ein Explorer NUR fuer das Relay selbst - ohne die einzelnen Clients.
//
// Gezeigt werden die beiden server-eigenen Ordner:
//   Storage      allgemeine Ablage (Installer, Skripte, Notizen, ...)
//   Deployment   dasselbe, zusaetzlich oeffentlich per Link erreichbar
//                unter /deployment/<datei>, sofern in den Einstellungen
//                freigegeben.
//
// Dieselben Ordner tauchen auch in WebDAV, FTP und SFTP auf - hier laeuft es
// nur ueber eine schlanke REST-API, damit Hoch- und Herunterladen im
// Dashboard ohne Netzlaufwerk funktioniert.

import { api } from "../api.js";
import { esc, uiConfirm, uiPrompt } from "../utils.js";
import { t } from "../i18n.js";

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
  let section = win?.props?.section || "Storage";
  let path = win?.props?.path || "";
  let writable = false;
  let publicOn = false;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;min-height:0">
      <div style="display:flex;gap:8px;align-items:center;padding:8px 10px;
                  border-bottom:1px solid var(--border);flex-wrap:wrap">
        <div id="rx-tabs" style="display:flex;gap:6px"></div>
        <div style="flex:1"></div>
        <button class="taskbar-btn" id="rx-up" title="${t("rx_up")}">⬆</button>
        <button class="taskbar-btn" id="rx-refresh" title="${t("refresh")}">⟳</button>
        <button class="taskbar-btn" id="rx-mkdir">＋ ${t("ec_folder")}</button>
        <button class="btn-primary" id="rx-upload" style="width:auto;margin:0">⬆ Hochladen</button>
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
      <button class="${s.name === section ? "btn-primary" : "taskbar-btn"}"
              data-sec="${esc(s.name)}" style="margin:0;width:auto">
        ${s.name === "Deployment" ? "🌐" : "📦"} ${esc(s.name)}
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
        sections = meta.sections || [];
        publicOn = !!meta.deployment_public;
        if (!sections.some((s) => s.name === section)) section = sections[0]?.name || "Storage";
      }
      drawTabs();
      const res = await api.storageList(section, path);
      writable = !!res.writable;
      drawCrumb();
      drawList(res.entries || []);
    } catch (e) {
      listEl.innerHTML = `<div style="padding:20px;color:var(--error,#f66)">${esc(e.message)}</div>`;
      footEl.textContent = "";
    }
  }

  function drawList(entries) {
    body.querySelector("#rx-mkdir").style.display = writable ? "" : "none";
    body.querySelector("#rx-upload").style.display = writable ? "" : "none";

    if (!entries.length) {
      listEl.innerHTML = `<div style="padding:20px;color:var(--subtext)">
        Dieser Ordner ist leer.${writable ? "" : " (Nur lesen erlaubt.)"}</div>`;
    } else {
      listEl.innerHTML = entries.map((e) => {
        const p = path ? `${path}/${e.name}` : e.name;
        // Nur bei Deployment und nur fuer Dateien: der oeffentliche Link.
        const pub = (section === "Deployment" && publicOn && !e.is_dir)
          ? `<a class="taskbar-btn" style="padding:2px 6px" target="_blank"
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
          ${e.is_dir ? "" : `<a class="taskbar-btn" style="padding:2px 6px"
              href="${api.storageDownloadUrl(section, p)}" data-dl title="${t("rx_download")}">⬇</a>`}
          ${writable ? `<button class="taskbar-btn" style="padding:2px 6px" data-ren title="${t("exp_rename")}">✏</button>
                        <button class="taskbar-btn" style="padding:2px 6px" data-del title="${t("delete")}">🗑</button>` : ""}
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
        try { await api.storageDelete(section, p); load(); }
        catch (err) { window.notify?.(err.message, "error", 8000); }
      });
      row.querySelector("[data-ren]")?.addEventListener("click", async () => {
        const old = p.split("/").pop();
        const name = await uiPrompt(t("rx_new_name"), { value: old });
        if (!name || name === old) return;
        const dst = path ? `${path}/${name}` : name;
        try { await api.storageMove(section, p, dst); load(); }
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
      const res = await fetch(api.storageDownloadUrl(section, p), {
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
    try { await api.storageMkdir(section, path ? `${path}/${name}` : name); load(); }
    catch (e) { window.notify?.(e.message, "error", 8000); }
  });

  body.querySelector("#rx-upload").addEventListener("click", () => fileEl.click());
  fileEl.addEventListener("change", async () => {
    const files = [...fileEl.files];
    fileEl.value = "";
    for (const f of files) {
      try { await api.storageUpload(section, path, f); }
      catch (e) { window.notify?.(`${f.name}: ${e.message}`, "error", 9000); }
    }
    load();
  });

  load();
}

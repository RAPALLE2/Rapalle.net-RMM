// app.js (Modul "docker") - App "Docker"
// --------------------------------------
// Links: Stacks + "Alle Container" + "Images / Volumes / Netzwerke" (mit Recht "Docker verwalten").
// Rechts: Stack (Aktionen, Container, Compose, Mitglieder, Protokoll), Container-Fenster
// (Logs live, Statistik, Konsole, Details), Assistent "Neuer Stack" (Compose / Katalog / Docker Hub).
// Backend: docker_routes.py (/api/docker)

import { getToken } from "/js/app/api.js";
import { initSubjectPicker, readSubjectPicker, subjectPickerHtml } from "/js/app/subjectpicker.js";
import { esc, uiConfirm } from "/js/app/utils.js";

async function dreq(path, opts = {}) {
  const token = getToken() || "";
  const headers = { ...(token ? { Authorization: "Bearer " + token } : {}) };
  if (opts.body !== undefined && typeof opts.body !== "string") opts = { ...opts, body: JSON.stringify(opts.body) };
  if (opts.body) headers["Content-Type"] = "application/json";
  const res = await fetch("/api/docker" + path, { ...opts, headers });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* egal */ }
    throw new Error(msg);
  }
  return res.json();
}
const fmtB = (b) => (!b ? "0 B" : b >= 1073741824 ? (b / 1073741824).toFixed(1) + " GB" : b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : b >= 1024 ? Math.round(b / 1024) + " KB" : b + " B");
const stateDot = (s) => `<span title="${esc(s || "")}" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${
  s === "running" ? "var(--online)" : s === "paused" || s === "restarting" ? "var(--warn)" : s === "exited" || s === "dead" ? "var(--danger)" : "var(--subtext)"}"></span>`;
const ROLE = { viewer: "Ansehen", operator: "Bedienen", admin: "Admin" };
const enc = encodeURIComponent;

function overlay(html, width = 640) {
  const ov = document.createElement("div");
  ov.style.cssText = "position:fixed;inset:0;z-index:9500;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;padding:16px";
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;width:${width}px;max-width:100%;
      max-height:92vh;display:flex;flex-direction:column;font-size:13px;min-height:0">${html}</div>`;
  document.body.appendChild(ov);
  return ov;
}
const logBox = (lines) => `<pre style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px;max-height:260px;
  overflow:auto;font-size:11.5px;margin:0;white-space:pre-wrap">${esc((lines || []).join("\n") || "–")}</pre>`;

export function renderDocker(body) {
  body.innerHTML = `
    <div style="display:flex;height:100%;min-height:0;font-size:13px">
      <div style="width:240px;flex:0 0 auto;border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0">
        <div style="padding:10px;display:flex;gap:6px;align-items:center;border-bottom:1px solid var(--border)">
          <b style="flex:1">🐳 Docker</b><button class="btn-tool" data-reload title="Aktualisieren">⟳</button>
          <button class="btn btn-sm" data-new style="display:none">＋ Stack</button></div>
        <div data-nav style="flex:1;overflow:auto;padding:6px"></div>
      </div>
      <div data-main style="flex:1;min-width:0;overflow:auto;padding:12px"><div style="color:var(--subtext)">Lade …</div></div>
    </div>`;
  const nav = body.querySelector("[data-nav]");
  const main = body.querySelector("[data-main]");
  let targets = [], stacks = [], manage = false, view = { kind: "home" };
  const tName = (t) => (targets.find((x) => x.target === t) || {}).name || t;

  async function load() {
    try {
      const [tg, st] = await Promise.all([dreq("/targets"), dreq("/stacks")]);
      targets = tg.targets; manage = st.manage; stacks = st.stacks;
    } catch (e) { nav.innerHTML = `<div style="color:var(--danger);padding:6px">${esc(e.message)}</div>`; return; }
    body.querySelector("[data-new]").style.display = manage ? "" : "none";
    const item = (key, label, sub, active) => `<div data-v="${esc(key)}" style="padding:7px 10px;border-radius:6px;cursor:pointer;margin-bottom:2px;
        ${active ? "background:var(--panel-2);" : ""}">${label}${sub ? `<div style="font-size:11.5px;color:var(--subtext)">${sub}</div>` : ""}</div>`;
    nav.innerHTML = `
      <div style="font-size:11px;color:var(--subtext);text-transform:uppercase;margin:4px 6px">Stacks</div>
      ${stacks.length ? stacks.map((s) => item("stack:" + s.id,
        `<div style="display:flex;gap:6px;align-items:center">${stateDot(s.error ? "dead" : s.running && s.running === s.containers ? "running" : s.running ? "paused" : "exited")}
          <b style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${esc(s.name)}</b></div>`,
        `${esc(tName(s.target))} · ${s.error ? "nicht erreichbar" : `${s.running || 0}/${s.containers || 0} laufen`}`,
        view.kind === "stack" && view.id === s.id)).join("")
        : `<div style="color:var(--subtext);padding:6px 10px">${manage ? "Noch keine Stacks – „＋ Stack“." : "Dir ist noch kein Stack freigegeben."}</div>`}
      <div style="font-size:11px;color:var(--subtext);text-transform:uppercase;margin:12px 6px 4px">Übersicht</div>
      ${item("containers", "📦 Alle Container", manage ? "auch außerhalb von Stacks" : "meiner Stacks", view.kind === "containers")}
      ${manage ? item("resources", "💿 Images / Volumes / Netzwerke", "", view.kind === "resources") : ""}
      <div style="font-size:11px;color:var(--subtext);text-transform:uppercase;margin:12px 6px 4px">Ziele</div>
      ${targets.map((t) => `<div style="padding:4px 10px;font-size:12px;display:flex;gap:6px;align-items:center">${stateDot(t.ok ? "running" : "dead")}
          <span style="flex:1">${esc(t.name)}</span><span style="color:var(--subtext)">${esc(t.version || (t.ok ? "" : "aus"))}</span></div>
          ${!t.ok && t.error ? `<div style="font-size:11px;color:var(--subtext);padding:0 10px 4px 24px">${esc(t.error)}</div>` : ""}
          ${t.update_available ? `<div style="font-size:11px;color:var(--warn);padding:0 10px 4px 24px">Node aktualisieren!</div>` : ""}`).join("")}`;
    nav.querySelectorAll("[data-v]").forEach((el) => el.addEventListener("click", () => {
      const v = el.dataset.v;
      view = v.startsWith("stack:") ? { kind: "stack", id: v.slice(6) } : { kind: v };
      load(); show();
    }));
  }

  function show() {
    if (view.kind === "stack") return drawStack(view.id, view.tab || "containers");
    if (view.kind === "containers") return drawAllContainers();
    if (view.kind === "resources") return drawResources();
    main.innerHTML = `<div style="color:var(--subtext)">Links einen Stack wählen${manage ? " oder mit „＋ Stack“ einen neuen anlegen" : ""}.</div>`;
  }

  // ------------------------------------------------------------ Container-Tabelle (wiederverwendet)
  function ctTable(target, list, canOperate, after) {
    if (!list.length) return `<div style="color:var(--subtext)">Keine Container.</div>`;
    return `<table class="data-table" style="width:100%"><thead><tr><th></th><th style="text-align:left">Name</th><th style="text-align:left">Image</th>
      <th style="text-align:left">Status</th><th>Ports</th><th></th></tr></thead><tbody>
      ${list.map((c) => `<tr data-ct="${esc(c.id)}" data-tg="${esc(target)}" style="cursor:pointer">
        <td>${stateDot(c.state)}</td><td><b>${esc(c.name)}</b>${c.project && !c.stack ? `<div style="font-size:11px;color:var(--subtext)">Projekt ${esc(c.project)}</div>` : ""}</td>
        <td style="font-size:12px">${esc(c.image)}</td><td style="font-size:12px">${esc(c.status || c.state)}</td>
        <td style="font-size:11.5px">${(c.ports || []).filter((p) => p.PublicPort).map((p) => `${p.PublicPort}→${p.PrivatePort}`).join(", ") || "–"}</td>
        <td style="white-space:nowrap;text-align:right">${canOperate ? (c.state === "running"
          ? `<button class="btn-tool" data-a="restart" title="Neu starten">⟲</button><button class="btn-tool" data-a="stop" title="Stoppen">■</button>`
          : `<button class="btn-tool" data-a="start" title="Starten">▶</button>`) : ""}</td></tr>`).join("")}</tbody></table>`;
  }
  function bindCtTable(root, after) {
    root.querySelectorAll("[data-ct]").forEach((tr) => {
      tr.addEventListener("click", (e) => { if (!e.target.closest("[data-a]")) openContainer(tr.dataset.tg, tr.dataset.ct, after); });
      tr.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", async () => {
        b.disabled = true;
        try { await dreq(`/containers/${enc(tr.dataset.tg)}/${enc(tr.dataset.ct)}/action/${b.dataset.a}`, { method: "POST" }); }
        catch (e) { window.notify?.(e.message, "error"); }
        after?.();
      }));
    });
  }

  // ------------------------------------------------------------ Stack
  async function drawStack(id, tab) {
    let d;
    try { d = await dreq(`/stacks/${id}`); } catch (e) { main.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
    const st = d.stack, lv = st.level;
    main.innerHTML = `
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
        <b style="font-size:16px">${esc(st.name)}</b>
        <span style="color:var(--subtext)">${esc(st.project)} · ${esc(tName(st.target))}${st.deployed ? ` · bereitgestellt ${new Date(st.deployed * 1000).toLocaleString()}` : " · noch nicht bereitgestellt"}</span>
        <span style="flex:1"></span>
        ${lv >= 3 ? `<button class="btn-primary btn-sm" data-sa="deploy" title="Container nach Compose (neu) erstellen">🚀 Bereitstellen</button>
          <button class="btn btn-sm" data-sa="pull" title="Images neu laden und neu erstellen">⬇ Aktualisieren</button>` : ""}
        ${lv >= 2 ? `<button class="btn btn-sm" data-sa="start">▶ Start</button><button class="btn btn-sm" data-sa="stop">■ Stopp</button>
          <button class="btn btn-sm" data-sa="restart">⟲ Neustart</button>` : ""}
        ${lv >= 3 ? `<button class="btn btn-sm" data-down>⏏ Herunterfahren</button><button class="btn btn-sm btn-danger" data-del>🗑 Löschen</button>` : ""}
      </div>
      ${d.error ? `<div style="color:var(--danger);margin-bottom:6px">${esc(d.error)}</div>` : ""}
      <div class="tab-bar" style="margin-bottom:10px">
        ${[["containers", "📦 Container"], ["compose", "📝 Compose"], ["publish", "🌐 Veröffentlichen"], lv >= 3 && ["members", "👥 Mitglieder"], ["log", "📜 Protokoll"]].filter(Boolean)
          .map(([k, l]) => `<button class="tab-btn ${k === tab ? "active" : ""}" data-t="${k}">${l}</button>`).join("")}
      </div>
      <div data-pane></div>`;
    main.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => { view.tab = b.dataset.t; drawStack(id, b.dataset.t); }));
    const pane = main.querySelector("[data-pane]");
    const runAct = async (path, opts, label) => {
      pane.innerHTML = `<div>⏳ ${esc(label)} …</div>`;
      main.querySelectorAll("[data-sa],[data-down],[data-del]").forEach((b) => { b.disabled = true; });
      let r;
      try { r = await dreq(path, opts); } catch (e) { r = { ok: false, log: [`❌ ${e.message}`] }; }
      window.notify?.(`${st.name} – ${label}: ${r.ok === false ? "fehlgeschlagen" : "fertig"}`, r.ok === false ? "error" : "success");
      await load();
      // Nur neu zeichnen, wenn man noch auf DIESEM Stack ist (Aktionen koennen dauern)
      if (view.kind === "stack" && view.id === id) { view.lastLog = r.log; view.tab = "log"; drawStack(id, "log"); }
    };
    main.querySelectorAll("[data-sa]").forEach((b) => b.addEventListener("click", () =>
      runAct(`/stacks/${id}/action/${b.dataset.sa}`, { method: "POST" }, b.textContent.trim())));
    main.querySelector("[data-down]")?.addEventListener("click", async () => {
      const ov = overlay(`<b>Stack herunterfahren?</b><p style="color:var(--subtext)">Alle Container und das Netzwerk des Stacks werden entfernt. Der Stack selbst bleibt und kann wieder bereitgestellt werden.</p>
        <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" data-vol style="width:16px;height:16px;appearance:auto"> auch die Volumes löschen (<b>Daten weg!</b>)</label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Herunterfahren</button></div>`, 440);
      ov.querySelector("[data-x]").onclick = () => ov.remove();
      ov.querySelector("[data-ok]").onclick = () => { const v = ov.querySelector("[data-vol]").checked; ov.remove();
        runAct(`/stacks/${id}/down`, { method: "POST", body: { volumes: v } }, "Herunterfahren"); };
    });
    main.querySelector("[data-del]")?.addEventListener("click", async () => {
      const ov = overlay(`<b>Stack „${esc(st.name)}“ löschen?</b><p style="color:var(--subtext)">Container und Netzwerk werden entfernt und der Stack aus der Liste gelöscht.</p>
        <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" data-vol style="width:16px;height:16px;appearance:auto"> auch die Volumes löschen (<b>Daten weg!</b>)</label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px"><button class="btn" data-x>Abbrechen</button><button class="btn btn-danger" data-ok>Endgültig löschen</button></div>`, 440);
      ov.querySelector("[data-x]").onclick = () => ov.remove();
      ov.querySelector("[data-ok]").onclick = async () => {
        const v = ov.querySelector("[data-vol]").checked; ov.remove();
        try { await dreq(`/stacks/${id}?volumes=${v}`, { method: "DELETE" }); window.notify?.("Stack gelöscht", "success"); view = { kind: "home" }; load(); show(); }
        catch (e) { window.notify?.(e.message, "error"); }
      };
    });

    if (tab === "containers") {
      pane.innerHTML = ctTable(st.target, d.containers, lv >= 2) + (d.containers.length ? "" :
        `<div style="color:var(--subtext);margin-top:6px">${st.deployed ? "Der Stack ist heruntergefahren." : "Noch nicht bereitgestellt"}${lv >= 3 ? " – „🚀 Bereitstellen“." : "."}</div>`);
      bindCtTable(pane, () => drawStack(id, "containers"));
    } else if (tab === "compose") {
      pane.innerHTML = `<textarea data-yml spellcheck="false" ${lv >= 3 ? "" : "readonly"} style="width:100%;min-height:340px;font-family:ui-monospace,monospace;
          font-size:12px;line-height:1.45;white-space:pre;tab-size:2">${esc(st.compose)}</textarea>
        ${lv >= 3 ? `<div style="display:flex;gap:8px;margin-top:8px"><button class="btn-primary btn-sm" data-save>Speichern</button>
          <button class="btn btn-sm" data-savedeploy>Speichern &amp; bereitstellen</button>
          <span style="color:var(--subtext);font-size:12px;align-self:center">Geprüft wird beim Speichern; wirksam erst nach „Bereitstellen“.</span></div>` : ""}`;
      const ta = pane.querySelector("[data-yml]");
      ta.addEventListener("keydown", (e) => { if (e.key === "Tab") { e.preventDefault(); const p = ta.selectionStart; ta.setRangeText("  ", p, ta.selectionEnd, "end"); } });
      const save = async (deploy) => {
        try { await dreq(`/stacks/${id}`, { method: "PUT", body: { compose: ta.value } }); window.notify?.("Compose gespeichert", "success"); }
        catch (e) { window.notify?.(e.message, "error", 9000); return; }
        if (deploy) runAct(`/stacks/${id}/action/deploy`, { method: "POST" }, "Bereitstellen");
      };
      pane.querySelector("[data-save]")?.addEventListener("click", () => save(false));
      pane.querySelector("[data-savedeploy]")?.addEventListener("click", () => save(true));
    } else if (tab === "publish") {
      let pb;
      try { pb = await dreq(`/stacks/${id}/publish`); } catch (e) { pane.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
      const svcs = Object.keys(pb.services);
      pane.innerHTML = `
        <p style="color:var(--subtext);margin-top:0">Dienste dieses Stacks von außen erreichbar machen – <b>ohne Anmeldung am RMM</b>.
          Schütze die Anwendung selbst (eigenes Login), bevor du sie veröffentlichst.</p>
        ${pb.publish.length ? `<table class="data-table" style="width:100%;margin-bottom:12px"><thead><tr><th style="text-align:left">Adresse</th><th style="text-align:left">Dienst</th><th>Art</th><th></th></tr></thead><tbody>
          ${pb.publish.map((e) => { const href = e.mode === "path" ? location.origin + e.url : e.url.startsWith(":") ? `${location.protocol}//${location.hostname}${e.url}` : `http://${e.url}`;
            return `<tr><td><a href="${esc(href)}" target="_blank" rel="noopener">${esc(href)}</a></td><td>${esc(e.service)}:${e.port}</td>
              <td>${e.mode === "path" ? "Unterpfad" : "eigener Port"}</td><td style="text-align:right">${lv >= 3 ? `<button class="btn-tool" data-unpub="${esc(e.id)}" title="Nicht mehr veröffentlichen">✕</button>` : ""}</td></tr>`; }).join("")}
          </tbody></table>` : `<div style="color:var(--subtext);margin-bottom:12px">Noch nichts veröffentlicht.</div>`}
        ${lv >= 3 ? (svcs.length ? `<div style="border:1px solid var(--border);border-radius:8px;padding:10px">
          <b>Neu veröffentlichen</b>
          <div style="display:flex;gap:16px;margin:8px 0;flex-wrap:wrap">
            <label style="display:flex;gap:6px;align-items:center"><input type="radio" name="pbm" value="path" checked style="width:16px;height:16px;appearance:auto">
              Unterpfad <code>${esc(location.host)}/&lt;name&gt;/</code></label>
            <label style="display:flex;gap:6px;align-items:center"><input type="radio" name="pbm" value="port" style="width:16px;height:16px;appearance:auto">
              Eigener Port <code>${pb.target === "local" ? esc(location.hostname) : "Node-IP"}:&lt;port&gt;</code></label></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
            <div><label style="font-size:12px;color:var(--subtext)">Dienst</label><select data-svc style="width:auto">${svcs.map((x) => `<option>${esc(x)}</option>`).join("")}</select></div>
            <div><label style="font-size:12px;color:var(--subtext)">Port im Container</label><input data-cport type="number" style="width:110px"></div>
            <div data-fpath><label style="font-size:12px;color:var(--subtext)">Name (Adresse)</label><input data-pname placeholder="${esc(st.project)}" style="width:180px"></div>
            <div data-fport style="display:none"><label style="font-size:12px;color:var(--subtext)">Öffentlicher Port</label><input data-pport type="number" placeholder="z. B. 8080" style="width:110px"></div>
            <button class="btn-primary btn-sm" data-pub>Veröffentlichen</button></div>
          <div data-phint style="font-size:11.5px;color:var(--subtext);margin-top:8px">Unterpfad: Die Anwendung läuft unter <code>/&lt;name&gt;/</code>. Manche Apps erwarten, an der Wurzel einer Domain zu laufen –
            dann deren „Basis-URL“ auf <code>/&lt;name&gt;</code> stellen oder „eigener Port“ wählen.</div></div>`
          : `<div style="color:var(--subtext)">Keine Dienste im Compose gefunden.</div>`) : ""}`;
      pane.querySelectorAll("[data-unpub]").forEach((b) => b.addEventListener("click", async () => {
        if (!(await uiConfirm("Nicht mehr veröffentlichen?", { okText: "Entfernen" }))) return;
        try { await dreq(`/stacks/${id}/publish/${b.dataset.unpub}`, { method: "DELETE" }); drawStack(id, "publish"); }
        catch (e) { window.notify?.(e.message, "error"); }
      }));
      const svcSel = pane.querySelector("[data-svc]");
      const fillPort = () => { const ports = pb.services[svcSel.value] || []; pane.querySelector("[data-cport]").value = ports[0] || ""; };
      if (svcSel) { svcSel.addEventListener("change", fillPort); fillPort(); }
      pane.querySelectorAll('input[name="pbm"]').forEach((r) => r.addEventListener("change", () => {
        const m = pane.querySelector('input[name="pbm"]:checked').value;
        pane.querySelector("[data-fpath]").style.display = m === "path" ? "" : "none";
        pane.querySelector("[data-fport]").style.display = m === "port" ? "" : "none";
        pane.querySelector("[data-phint]").innerHTML = m === "path" ? pane.querySelector("[data-phint]").innerHTML
          : `Eigener Port: Der Port wird direkt auf dem ${pb.target === "local" ? "RMM-Server" : "Node"} freigegeben (Container wird dafür neu erstellt). Firewall/Router ggf. öffnen.`;
      }));
      pane.querySelector("[data-pub]")?.addEventListener("click", async () => {
        const mode = pane.querySelector('input[name="pbm"]:checked').value;
        const body = { mode, service: svcSel.value, port: parseInt(pane.querySelector("[data-cport]").value, 10) || 0,
                       name: (pane.querySelector("[data-pname]").value || st.project).trim(), public_port: parseInt(pane.querySelector("[data-pport]").value, 10) || 0 };
        const where = mode === "path" ? `${location.origin}/${body.name}/` : `Port ${body.public_port}`;
        if (!(await uiConfirm(`${body.service} öffentlich machen unter ${where}?`, { description: "Jeder mit der Adresse erreicht die Anwendung – ohne Anmeldung am RMM.", okText: "Veröffentlichen" }))) return;
        try { const r = await dreq(`/stacks/${id}/publish`, { method: "POST", body });
          window.notify?.(`Veröffentlicht: ${where}`, "success"); if (r.log?.length) view.lastLog = r.log; drawStack(id, "publish"); }
        catch (e) { window.notify?.(e.message, "error", 9000); }
      });
    } else if (tab === "members") {
      let dir = { users: [], groups: [] };
      try { dir = await dreq("/directory"); } catch { /* ohne Liste */ }
      let members = (st.members || []).map((m) => ({ ...m }));
      const nm = (m) => m.type === "user" ? "👤 " + ((dir.users.find((u) => u.id === m.id) || {}).username || m.id) : "👥 " + ((dir.groups.find((g) => g.id === m.id) || {}).name || m.id);
      const draw = () => {
        pane.innerHTML = `<p style="color:var(--subtext);margin-top:0">Wer diesen Stack sehen und steuern darf. Benutzer mit „Docker verwalten“ dürfen immer alles.</p>
          ${members.length ? members.map((m, i) => `<div style="display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
            <span style="flex:1">${esc(nm(m))}</span><select data-role="${i}" style="width:auto">${Object.entries(ROLE).map(([k, l]) =>
              `<option value="${k}" ${m.role === k ? "selected" : ""}>${l}</option>`).join("")}</select>
            <button class="btn-tool" data-rm="${i}">✕</button></div>`).join("") : `<div style="color:var(--subtext)">Noch keine Mitglieder.</div>`}
          <div style="font-size:11.5px;color:var(--subtext);margin:6px 0">Ansehen = Status + Logs · Bedienen = + Start/Stopp/Konsole · Admin = + Compose, Mitglieder, Löschen</div>
          <div style="display:flex;gap:8px;margin-top:8px"><button class="btn btn-sm" data-add>＋ Hinzufügen</button><button class="btn-primary btn-sm" data-save>Speichern</button></div>`;
        pane.querySelectorAll("[data-role]").forEach((s) => s.addEventListener("change", () => { members[+s.dataset.role].role = s.value; }));
        pane.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { members.splice(+b.dataset.rm, 1); draw(); }));
        pane.querySelector("[data-add]").addEventListener("click", () => {
          const ov = overlay(`<b style="margin-bottom:8px">Mitglieder hinzufügen</b><div data-pick style="flex:1;overflow:auto">${subjectPickerHtml(dir, [], { name: "dkm" })}</div>
            <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px"><button class="btn" data-x>Abbrechen</button><button class="btn-primary" data-ok>Hinzufügen</button></div>`, 440);
          initSubjectPicker(ov.querySelector("[data-pick]"));
          ov.querySelector("[data-x]").onclick = () => ov.remove();
          ov.querySelector("[data-ok]").onclick = () => {
            for (const s of readSubjectPicker(ov.querySelector("[data-pick]"))) if (!members.some((m) => m.type === s.type && m.id === s.id)) members.push({ ...s, role: "viewer" });
            ov.remove(); draw();
          };
        });
        pane.querySelector("[data-save]").addEventListener("click", async () => {
          try { await dreq(`/stacks/${id}/members`, { method: "PATCH", body: { members } }); window.notify?.("Mitglieder gespeichert", "success"); }
          catch (e) { window.notify?.(e.message, "error"); }
        });
      };
      draw();
    } else {
      pane.innerHTML = view.lastLog ? logBox(view.lastLog) : `<div style="color:var(--subtext)">Hier erscheint das Protokoll der letzten Aktion.</div>`;
    }
  }

  // ------------------------------------------------------------ Alle Container
  async function drawAllContainers(target = view.target || "local") {
    view.target = target;
    main.innerHTML = `<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><b style="font-size:15px;flex:1">📦 Alle Container</b>
      <select data-tg style="width:auto">${targets.map((t) => `<option value="${esc(t.target)}" ${t.target === target ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select></div>
      <div data-list>Lade …</div>`;
    main.querySelector("[data-tg]").addEventListener("change", (e) => drawAllContainers(e.target.value));
    const box = main.querySelector("[data-list]");
    try {
      const r = await dreq(`/containers?target=${enc(target)}`);
      box.innerHTML = ctTable(target, r.containers, true);
      bindCtTable(box, () => drawAllContainers(target));
    } catch (e) { box.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------ Images / Volumes / Netzwerke
  async function drawResources(target = view.target || "local") {
    view.target = target;
    main.innerHTML = `<div style="display:flex;gap:8px;align-items:center;margin-bottom:10px"><b style="font-size:15px;flex:1">💿 Images / Volumes / Netzwerke</b>
      <select data-tg style="width:auto">${targets.map((t) => `<option value="${esc(t.target)}" ${t.target === target ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select></div>
      <div style="display:flex;gap:6px;margin-bottom:8px"><input data-img placeholder="Image laden, z. B. nginx:alpine" style="flex:1"><button class="btn btn-sm" data-pull>⬇ Laden</button></div>
      <div data-res>Lade …</div>`;
    main.querySelector("[data-tg]").addEventListener("change", (e) => drawResources(e.target.value));
    main.querySelector("[data-pull]").addEventListener("click", async () => {
      const image = main.querySelector("[data-img]").value.trim(); if (!image) return;
      window.notify?.(`Lade ${image} …`, "info");
      try { await dreq("/images/pull", { method: "POST", body: { target, image } }); window.notify?.(`${image} geladen`, "success"); drawResources(target); }
      catch (e) { window.notify?.(e.message, "error", 9000); }
    });
    const box = main.querySelector("[data-res]");
    try {
      const [im, vo, ne] = await Promise.all([dreq(`/images?target=${enc(target)}`), dreq(`/volumes?target=${enc(target)}`), dreq(`/networks?target=${enc(target)}`)]);
      box.innerHTML = `<h4 style="margin:6px 0">Images (${im.images.length})</h4>
        <table class="data-table" style="width:100%"><tbody>${im.images.map((i) => `<tr><td>${esc(i.tags.join(", "))}</td><td style="font-size:11.5px;color:var(--subtext)">${esc(i.id)}</td>
          <td>${fmtB(i.size)}</td><td style="text-align:right"><button class="btn-tool" data-rmi="${esc(i.id)}" title="Löschen">🗑️</button></td></tr>`).join("")}</tbody></table>
        <h4 style="margin:14px 0 6px">Volumes (${vo.volumes.length})</h4>
        <table class="data-table" style="width:100%"><tbody>${vo.volumes.map((v) => `<tr><td>${esc(v.name)}</td><td style="color:var(--subtext)">${esc(v.project || "")}</td><td>${esc(v.driver || "")}</td></tr>`).join("") || "<tr><td>–</td></tr>"}</tbody></table>
        <h4 style="margin:14px 0 6px">Netzwerke (${ne.networks.length})</h4>
        <table class="data-table" style="width:100%"><tbody>${ne.networks.map((n) => `<tr><td>${esc(n.name)}</td><td style="color:var(--subtext)">${esc(n.project || "")}</td><td>${esc(n.driver || "")}</td></tr>`).join("")}</tbody></table>`;
      box.querySelectorAll("[data-rmi]").forEach((b) => b.addEventListener("click", async () => {
        if (!(await uiConfirm("Image löschen?", { okText: "Löschen", danger: true }))) return;
        try { await dreq(`/images/${enc(target)}/${enc(b.dataset.rmi)}`, { method: "DELETE" }); drawResources(target); }
        catch (e) { window.notify?.(e.message, "error", 8000); }
      }));
    } catch (e) { box.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------ Container-Fenster
  function openContainer(target, cid, after) {
    const base = `/containers/${enc(target)}/${enc(cid)}`;
    const ov = overlay(`
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px"><b data-title style="flex:1;font-size:15px">Container</b>
        <span data-acts style="display:flex;gap:4px"></span><button class="btn-tool" data-x title="Schließen">✕</button></div>
      <div class="tab-bar" style="margin-bottom:8px">${[["logs", "📜 Logs"], ["stats", "📈 Statistik"], ["exec", "⌨ Konsole"], ["inspect", "🔍 Details"]]
        .map(([k, l], i) => `<button class="tab-btn ${i ? "" : "active"}" data-t="${k}">${l}</button>`).join("")}</div>
      <div data-p style="flex:1;min-height:0;overflow:auto"></div>`, 900);
    let timer = null;
    const stop = () => { clearInterval(timer); timer = null; if (typeof closeTty === "function") closeTty(); };
    const close = () => { stop(); ov.remove(); after?.(); };
    ov.querySelector("[data-x]").onclick = close;
    ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
    const p = ov.querySelector("[data-p]");
    let info = {};
    const loadInfo = async () => {
      try { info = (await dreq(`${base}/inspect`)).inspect; } catch (e) { p.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; return; }
      const running = info.State?.Running, paused = info.State?.Paused;
      ov.querySelector("[data-title]").innerHTML = `${stateDot(paused ? "paused" : running ? "running" : "exited")} ${esc((info.Name || cid).replace(/^\//, ""))}`;
      ov.querySelector("[data-acts]").innerHTML = (paused ? [["unpause", "Fortsetzen"]] : running
        ? [["restart", "⟲"], ["stop", "■ Stopp"], ["pause", "⏸"], ["kill", "Kill"]] : [["start", "▶ Start"]])
        .concat([["remove", "🗑"]]).map(([a, l]) => `<button class="btn btn-sm ${a === "remove" || a === "kill" ? "btn-danger" : ""}" data-a="${a}">${l}</button>`).join("");
      ov.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", async () => {
        if (["remove", "kill"].includes(b.dataset.a) && !(await uiConfirm(b.dataset.a === "remove" ? "Container entfernen?" : "Container hart beenden?", { danger: true, okText: "Ja" }))) return;
        try { await dreq(`${base}/action/${b.dataset.a}`, { method: "POST" }); } catch (e) { window.notify?.(e.message, "error"); return; }
        if (b.dataset.a === "remove") return close();
        loadInfo();
      }));
    };
    const tabs = {
      async logs() {
        p.innerHTML = `<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px"><label style="display:flex;gap:6px;align-items:center">
            <input type="checkbox" data-live checked style="width:16px;height:16px;appearance:auto"> live</label>
          <select data-tail style="width:auto"><option>200</option><option selected>500</option><option>2000</option></select></div>
          <pre data-log style="background:#0b0e12;color:#d6e2ee;border-radius:8px;padding:8px;height:420px;overflow:auto;font-size:11.5px;margin:0;white-space:pre-wrap"></pre>`;
        const pre = p.querySelector("[data-log]");
        const get = async () => {
          try { const r = await dreq(`${base}/logs?tail=${p.querySelector("[data-tail]").value}`);
            const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
            pre.textContent = r.log || "(keine Ausgabe)"; if (atEnd) pre.scrollTop = pre.scrollHeight; }
          catch (e) { pre.textContent = e.message; }
        };
        await get(); pre.scrollTop = pre.scrollHeight;
        timer = setInterval(() => { if (p.querySelector("[data-live]")?.checked) get(); }, 2500);
        p.querySelector("[data-tail]").addEventListener("change", get);
      },
      async stats() {
        p.innerHTML = `<div data-s style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px">Lade …</div>`;
        const box = p.querySelector("[data-s]");
        const get = async () => {
          try { const s = await dreq(`${base}/stats`);
            const card = (k, v) => `<div style="border:1px solid var(--border);border-radius:8px;padding:10px"><div style="font-size:11px;color:var(--subtext)">${k}</div><div style="font-size:18px;font-weight:700">${v}</div></div>`;
            box.innerHTML = card("CPU", `${s.cpu} %`) + card("RAM", `${fmtB(s.mem)}${s.mem_limit ? ` / ${fmtB(s.mem_limit)}` : ""}`)
              + card("Netz ↓", fmtB(s.net_rx)) + card("Netz ↑", fmtB(s.net_tx)) + card("Prozesse", s.pids ?? "–"); }
          catch (e) { box.textContent = e.message; }
        };
        await get(); timer = setInterval(get, 3000);
      },
      exec() {
        // Interaktiv (RMM-Server) mit MiniTerm, sonst/auf Wunsch Befehlsmodus
        p.innerHTML = `<div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
            <button class="btn btn-sm ${target === "local" ? "active" : ""}" data-mode="tty" ${target === "local" ? "" : "disabled title=\"nur für Container auf dem RMM-Server\""}>🖥 Interaktiv</button>
            <button class="btn btn-sm ${target === "local" ? "" : "active"}" data-mode="cmd">⌨ Befehlsmodus</button>
            <span data-cst style="font-size:12px;color:var(--subtext)"></span></div><div data-cbox></div>`;
        const cbox = p.querySelector("[data-cbox]");
        p.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
          p.querySelectorAll("[data-mode]").forEach((x) => x.classList.toggle("active", x === b));
          closeTty(); b.dataset.mode === "tty" ? tty(cbox) : cmdMode(cbox);
        }));
        target === "local" ? tty(cbox) : cmdMode(cbox);
      },
      inspect() {
        p.innerHTML = `<pre style="background:var(--panel-2);border-radius:8px;padding:8px;height:420px;overflow:auto;font-size:11.5px;margin:0">${esc(JSON.stringify(info, null, 2))}</pre>`;
      },
    };
    let ttyWs = null, ttyTerm = null;
    const closeTty = () => { try { ttyWs?.close(); } catch { /* egal */ } try { ttyTerm?.dispose(); } catch { /* egal */ } ttyWs = null; ttyTerm = null; };
    async function tty(box) {
      box.innerHTML = `<div data-term style="height:420px;background:#0b0f14;border-radius:8px;overflow:hidden"></div>`;
      const st = p.querySelector("[data-cst]");
      const { MiniTerm } = await import("/js/feature/miniterm.js");
      const dec = new TextDecoder();
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/docker${base}/console`,
                               ["rmm-console", `rmmtoken.${getToken() || ""}`]);
      ws.binaryType = "arraybuffer";
      const term = new MiniTerm(box.querySelector("[data-term]"), {
        onData: (d) => { if (ws.readyState === 1) ws.send(JSON.stringify({ t: "i", d })); },
        onResize: (c, r) => { if (ws.readyState === 1) ws.send(JSON.stringify({ t: "r", c, r })); },
      });
      ttyWs = ws; ttyTerm = term;
      st.textContent = "Verbinde …";
      ws.onopen = () => { st.textContent = "Verbunden – Shell im Container"; term.fit(); ws.send(JSON.stringify({ t: "r", c: term.cols, r: term.rows })); term.focus(); };
      ws.onmessage = (e) => term.write(typeof e.data === "string" ? e.data : dec.decode(e.data, { stream: true }));
      ws.onclose = () => { st.textContent = "Getrennt"; };
    }
    function cmdMode(box) {
      const p = box;
      {
        p.innerHTML = `<pre data-out style="background:#0b0e12;color:#d6e2ee;border-radius:8px;padding:8px;height:360px;overflow:auto;font-size:12px;margin:0 0 6px;white-space:pre-wrap">Befehle laufen mit /bin/sh im Container (z. B. „ls -la /“, „env“, „cat /etc/os-release“).\n</pre>
          <div style="display:flex;gap:6px"><input data-cmd placeholder="Befehl … (Enter)" style="flex:1;font-family:ui-monospace,monospace"><button class="btn-primary btn-sm" data-run>Ausführen</button></div>`;
        const out = p.querySelector("[data-out]"), inp = p.querySelector("[data-cmd]");
        const hist = []; let hi = 0;
        const run = async () => {
          const cmd = inp.value.trim(); if (!cmd) return;
          hist.push(cmd); hi = hist.length; inp.value = "";
          out.textContent += `\n$ ${cmd}\n`;
          try { const r = await dreq(`${base}/exec`, { method: "POST", body: { cmd } });
            out.textContent += (r.output || "") + (r.exit_code ? `[Exit-Code ${r.exit_code}]\n` : ""); }
          catch (e) { out.textContent += `Fehler: ${e.message}\n`; }
          out.scrollTop = out.scrollHeight;
        };
        p.querySelector("[data-run]").addEventListener("click", run);
        inp.addEventListener("keydown", (e) => {
          if (e.key === "Enter") run();
          else if (e.key === "ArrowUp" && hi > 0) { inp.value = hist[--hi]; e.preventDefault(); }
          else if (e.key === "ArrowDown") { inp.value = hi < hist.length - 1 ? hist[++hi] : ""; if (hi >= hist.length - 1) hi = hist.length; }
        });
        inp.focus();
      }
    }
    ov.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => {
      stop(); ov.querySelectorAll("[data-t]").forEach((x) => x.classList.toggle("active", x === b)); tabs[b.dataset.t]();
    }));
    loadInfo().then(() => tabs.logs());
  }

  // ------------------------------------------------------------ Neuer Stack
  body.querySelector("[data-new]").addEventListener("click", async () => {
    const ov = overlay(`
      <div style="display:flex;align-items:center;margin-bottom:8px"><b style="flex:1;font-size:15px">Neuer Stack</b><button class="btn-tool" data-x>✕</button></div>
      <div class="tab-bar" style="margin-bottom:8px"><button class="tab-btn active" data-src="compose">📝 Compose</button>
        <button class="tab-btn" data-src="catalog">🧩 Katalog</button><button class="tab-btn" data-src="hub">🔎 Docker Hub</button></div>
      <div data-srcp style="flex:1;min-height:0;overflow:auto"></div>
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:10px;border-top:1px solid var(--border);padding-top:10px">
        <div style="flex:1;min-width:160px"><label style="font-size:12px;color:var(--subtext)">Name</label><input data-name placeholder="z. B. wiki"></div>
        <div><label style="font-size:12px;color:var(--subtext)">Ziel</label><select data-target style="width:auto">${targets.map((t) =>
          `<option value="${esc(t.target)}" ${t.ok ? "" : "disabled"}>${esc(t.name)}${t.ok ? "" : " (nicht bereit)"}</option>`).join("")}</select></div>
        <label style="display:flex;gap:6px;align-items:center;margin-bottom:6px"><input type="checkbox" data-dep checked style="width:16px;height:16px;appearance:auto"> sofort bereitstellen</label>
        <button class="btn-primary" data-create>Anlegen</button></div>
      <div data-out style="margin-top:8px"></div>`, 860);
    ov.querySelector("[data-x]").onclick = () => ov.remove();
    const sp = ov.querySelector("[data-srcp]");
    let yml = "services:\n  app:\n    image: nginx:alpine\n    restart: unless-stopped\n    ports:\n      - \"8080:80\"\n";
    const editor = () => {
      sp.innerHTML = `<textarea data-yml spellcheck="false" style="width:100%;height:330px;font-family:ui-monospace,monospace;font-size:12px;line-height:1.45;white-space:pre;tab-size:2">${esc(yml)}</textarea>
        <div style="font-size:11.5px;color:var(--subtext);margin-top:4px">Unterstützt: image, ports, environment, volumes (benannt/absolut), restart, command, depends_on, networks, labels … – kein build/env_file.</div>`;
      const ta = sp.querySelector("[data-yml]");
      ta.addEventListener("input", () => { yml = ta.value; });
      ta.addEventListener("keydown", (e) => { if (e.key === "Tab") { e.preventDefault(); ta.setRangeText("  ", ta.selectionStart, ta.selectionEnd, "end"); yml = ta.value; } });
    };
    const useTemplate = (compose, name) => {
      yml = compose; if (name && !ov.querySelector("[data-name]").value) ov.querySelector("[data-name]").value = name;
      ov.querySelectorAll("[data-src]").forEach((x) => x.classList.toggle("active", x.dataset.src === "compose")); editor();
      window.notify?.("Vorlage übernommen – bei Bedarf anpassen und „Anlegen“", "info");
    };
    const srcs = {
      compose: editor,
      async catalog() {
        sp.innerHTML = "Lade …";
        const c = await dreq("/catalog");
        sp.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px">${c.templates.map((t) => `
          <div data-tpl="${esc(t.id)}" style="border:1px solid var(--border);border-radius:8px;padding:10px;cursor:pointer">
            <div style="font-size:20px">${t.icon}</div><b>${esc(t.name)}</b> <span style="font-size:11px;color:var(--subtext)">${esc(t.category)}</span>
            <div style="font-size:12px;color:var(--subtext);margin-top:4px">${esc(t.description)}</div></div>`).join("")}</div>`;
        sp.querySelectorAll("[data-tpl]").forEach((el) => el.addEventListener("click", () => {
          const t = c.templates.find((x) => x.id === el.dataset.tpl); useTemplate(t.compose, t.id);
        }));
      },
      hub() {
        sp.innerHTML = `<div style="display:flex;gap:6px;margin-bottom:8px"><input data-q placeholder="Image suchen, z. B. jellyfin" style="flex:1"><button class="btn btn-sm" data-s>Suchen</button></div><div data-r></div>`;
        const go = async () => {
          const q = sp.querySelector("[data-q]").value.trim(); const box = sp.querySelector("[data-r]");
          if (q.length < 2) return; box.innerHTML = "Suche …";
          try {
            const r = await dreq(`/hub?q=${enc(q)}`);
            box.innerHTML = r.results.length ? r.results.map((x) => `<div style="display:flex;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">
                <div style="flex:1;min-width:0"><b>${esc(x.image)}</b>${x.official ? ` <span style="font-size:11px;color:var(--accent)">offiziell</span>` : ""}
                  <div style="font-size:11.5px;color:var(--subtext)">${esc(x.description)} · ★ ${x.stars}</div></div>
                <input data-port="${esc(x.image)}" type="number" placeholder="Port" style="width:80px">
                <button class="btn btn-sm" data-use="${esc(x.image)}">Übernehmen</button></div>`).join("") : "Nichts gefunden.";
            box.querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", async () => {
              const port = box.querySelector(`[data-port="${CSS.escape(b.dataset.use)}"]`).value;
              const t = await dreq(`/hub/template?image=${enc(b.dataset.use)}&port=${enc(port || 0)}`);
              useTemplate(t.compose, b.dataset.use.split("/").pop());
            }));
          } catch (e) { box.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`; }
        };
        sp.querySelector("[data-s]").addEventListener("click", go);
        sp.querySelector("[data-q]").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      },
    };
    ov.querySelectorAll("[data-src]").forEach((b) => b.addEventListener("click", () => {
      ov.querySelectorAll("[data-src]").forEach((x) => x.classList.toggle("active", x === b)); srcs[b.dataset.src]();
    }));
    editor();
    ov.querySelector("[data-create]").addEventListener("click", async (e) => {
      const name = ov.querySelector("[data-name]").value.trim();
      if (!name) { window.notify?.("Bitte einen Namen eingeben", "error"); return; }
      e.target.disabled = true;
      const out = ov.querySelector("[data-out]");
      out.innerHTML = `<div>⏳ ${ov.querySelector("[data-dep]").checked ? "Lege an und stelle bereit (Images laden kann dauern) …" : "Lege an …"}</div>`;
      try {
        const r = await dreq("/stacks", { method: "POST", body: { name, target: ov.querySelector("[data-target]").value, compose: yml, deploy: ov.querySelector("[data-dep]").checked } });
        view = { kind: "stack", id: r.stack.id, tab: r.log.length ? "log" : "containers", lastLog: r.log };
        ov.remove();
        window.notify?.(r.ok ? `Stack „${name}“ angelegt` : `Stack angelegt, Bereitstellen fehlgeschlagen`, r.ok ? "success" : "error");
        await load(); show();
      } catch (err) { out.innerHTML = `<div style="color:var(--danger)">❌ ${esc(err.message)}</div>`; e.target.disabled = false; }
    });
  });

  body.querySelector("[data-reload]").addEventListener("click", () => { load(); show(); });
  load().then(show);
}

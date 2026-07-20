// apps/permissions.js
// -------------------
// Zentrale Rechte-Verwaltung. Man wählt links ein Subjekt (Benutzer ODER
// Gruppe, jeweils lokal oder aus dem AD) und vergibt rechts feingranulare
// Rechte als TRI-STATE:
//
//   Verbieten (deny)  /  — (keine Einstellung)  /  Erlauben (allow)
//
// Auflösung im Backend (auth.py): 'deny' gewinnt immer, 'admin' ist ein
// Wildcard, client-Rechte sind zusätzlich durch 'access_clients' gegated.
// Beispiele:
//   - nur 'login' allow  -> darf sich anmelden, sieht aber keine Clients/Apps
//   - admin auf Client X + global deny 'use_terminal' -> alles auf X außer Terminal
//   - global admin + 'access_clients' deny auf Client Y -> Y ist versteckt
//
// Es gibt zwei Tabs: "Allgemein" (globale Rechte) und "Clients" (pro Client,
// mit Suchleiste).

import { api } from "../api.js";
import { state } from "../state.js";
import { esc, uiConfirm, uiPrompt } from "../utils.js";

export function renderPermissions(body, win) {
  // ---- lokaler Zustand des Fensters ----
  let subjectKind = "user";      // "user" | "group"
  let subjects = { user: [], group: [] };
  let realms = [];               // AD/LDAP-Realms (für AD-Gruppen-Import)
  let catalog = { labels: {}, general: [], client: [] };
  let selected = null;           // {type, id, name}
  let activeTab = "general";     // "general" | "clients"
  let clientSearch = "";
  let subjSearch = "";

  // Grant-Modell: Map "scope|perm" -> "allow" | "deny". Fehlt der Eintrag =
  // keine Einstellung. dirty = ungespeicherte Änderungen vorhanden.
  let grants = new Map();
  let dirty = false;

  const gk = (scope, perm) => `${scope}|${perm}`;
  const getEffect = (scope, perm) => grants.get(gk(scope, perm)) || "";
  function setEffect(scope, perm, effect) {
    const key = gk(scope, perm);
    if (effect === "allow" || effect === "deny") grants.set(key, effect);
    else grants.delete(key);
    dirty = true;
    updateSaveBar();
  }

  // ---------------------------------------------------------------
  // Grundgerüst
  // ---------------------------------------------------------------
  body.innerHTML = `
    <div style="display:flex;height:100%;min-height:0">
      <!-- LINKS: Subjektliste -->
      <div style="width:250px;border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0">
        <div style="display:flex;gap:4px;padding:8px">
          <button class="tab-btn" id="pm-kind-user">Benutzer</button>
          <button class="tab-btn" id="pm-kind-group">Gruppen</button>
        </div>
        <div style="padding:0 8px 8px">
          <input type="text" id="pm-subj-search" placeholder="Suchen…"
            style="width:100%;padding:6px 8px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:13px" />
        </div>
        <div id="pm-ad-row" style="padding:0 8px 8px;display:none"></div>
        <div id="pm-subj-list" style="flex:1;overflow:auto;padding:0 6px 8px"></div>
      </div>

      <!-- RECHTS: Rechte-Editor -->
      <div style="flex:1;display:flex;flex-direction:column;min-height:0">
        <div id="pm-head" style="padding:10px 14px;border-bottom:1px solid var(--border)">
          <div style="color:var(--subtext);font-size:13px">Wähle links einen Benutzer oder eine Gruppe.</div>
        </div>
        <div class="tab-bar" id="pm-tabs" style="padding:8px 14px 0;gap:6px;display:none">
          <button class="tab-btn active" data-pt="general">Allgemein</button>
          <button class="tab-btn" data-pt="clients">Clients</button>
        </div>
        <div id="pm-content" style="flex:1;overflow:auto;padding:14px"></div>
        <div id="pm-savebar" style="display:none;border-top:1px solid var(--border);padding:10px 14px;align-items:center;gap:12px">
          <span id="pm-dirty" style="font-size:12px;color:var(--subtext)"></span>
          <span style="flex:1"></span>
          <button class="taskbar-btn" id="pm-reset">Verwerfen</button>
          <button class="btn-primary" id="pm-save">Speichern</button>
        </div>
      </div>
    </div>
  `;

  const subjListEl = body.querySelector("#pm-subj-list");
  const headEl = body.querySelector("#pm-head");
  const tabsEl = body.querySelector("#pm-tabs");
  const contentEl = body.querySelector("#pm-content");
  const saveBar = body.querySelector("#pm-savebar");
  const dirtyEl = body.querySelector("#pm-dirty");

  function updateSaveBar() {
    saveBar.style.display = selected ? "flex" : "none";
    dirtyEl.textContent = dirty ? "● Ungespeicherte Änderungen" : "Gespeichert";
    dirtyEl.style.color = dirty ? "var(--warn)" : "var(--subtext)";
  }

  // Segmented Tri-State-Control. Gibt HTML zurück; Klicks werden delegiert.
  function triState(scope, perm) {
    const cur = getEffect(scope, perm);
    const opt = (val, label, color) => {
      const on = cur === val || (val === "" && cur === "");
      return `<button type="button" class="pm-tri" data-scope="${esc(scope)}" data-perm="${esc(perm)}" data-val="${val}"
        style="border:1px solid var(--border);background:${on ? color : "transparent"};
        color:${on ? "#0b0f14" : "var(--subtext)"};font-weight:${on ? 600 : 400};
        padding:2px 8px;font-size:11px;cursor:pointer;min-width:34px">${label}</button>`;
    };
    return `<span style="display:inline-flex;border-radius:6px;overflow:hidden">
      ${opt("deny", "Verbieten", "var(--danger)")}
      ${opt("", "—", "var(--subtext)")}
      ${opt("allow", "Erlauben", "var(--online)")}
    </span>`;
  }

  // Klick-Delegation für alle Tri-State-Buttons.
  contentEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".pm-tri");
    if (!btn) return;
    const scope = btn.dataset.scope;
    const perm = btn.dataset.perm;
    setEffect(scope, perm, btn.dataset.val);
    // NUR das geklickte Tri-State-Control neu zeichnen. Ein vollständiges
    // drawContent() würde die aufgeklappten Client-Accordions (<details>)
    // wieder zuklappen – daher hier gezielt in-place ersetzen.
    const wrap = btn.closest("span");
    if (wrap) wrap.outerHTML = triState(scope, perm);
    else drawContent();
  });

  // ---------------------------------------------------------------
  // Subjektliste
  // ---------------------------------------------------------------
  function subjLabel(s) {
    if (subjectKind === "user") {
      const ad = s.auth_realm ? ' <span style="color:var(--accent);font-size:10px">AD</span>' : "";
      const role = s.role === "admin" ? ' <span style="color:var(--warn);font-size:10px">ADMIN</span>' : "";
      return `${esc(s.display_name || s.username)} <span style="color:var(--subtext);font-size:11px">@${esc(s.username)}</span>${ad}${role}`;
    }
    const ad = s.is_ad_group ? ' <span style="color:var(--accent);font-size:10px">AD</span>' : "";
    return `${esc(s.name)}${ad}`;
  }

  function drawSubjects() {
    body.querySelector("#pm-kind-user").classList.toggle("active", subjectKind === "user");
    body.querySelector("#pm-kind-group").classList.toggle("active", subjectKind === "group");

    // AD-Gruppen-Import nur im Gruppen-Modus und wenn Realms konfiguriert sind.
    const adRow = body.querySelector("#pm-ad-row");
    if (subjectKind === "group" && realms.length) {
      adRow.style.display = "";
      adRow.innerHTML = `
        <div style="display:flex;gap:4px">
          <select id="pm-ad-realm" style="flex:1;min-width:0;padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:12px">
            ${realms.map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("")}
          </select>
          <button class="action-btn" id="pm-ad-load" title="AD-Gruppen aus diesem Realm importieren, um ihnen Rechte zu geben">AD laden</button>
        </div>`;
      adRow.querySelector("#pm-ad-load").addEventListener("click", async () => {
        const realmId = adRow.querySelector("#pm-ad-realm").value;
        const btn = adRow.querySelector("#pm-ad-load");
        btn.disabled = true; const orig = btn.textContent; btn.textContent = "…";
        try {
          const res = await api.importRealmAdGroups(realmId, []); // [] = alle
          subjects.group = await api.getGroups();
          window.notify?.(`${res.count} AD-Gruppe(n) importiert. Du kannst ihnen jetzt Rechte geben.`, "success");
          drawSubjects();
        } catch (e) {
          window.notify?.("AD-Gruppen laden fehlgeschlagen: " + e.message, "error");
        } finally {
          btn.disabled = false; btn.textContent = orig;
        }
      });
    } else {
      adRow.style.display = "none";
      adRow.innerHTML = "";
    }

    const list = subjects[subjectKind] || [];
    const q = subjSearch.toLowerCase();
    const filtered = list.filter((s) => {
      const hay = subjectKind === "user"
        ? `${s.username} ${s.display_name}`.toLowerCase()
        : `${s.name}`.toLowerCase();
      return !q || hay.includes(q);
    });

    const rowHtml = (s) => {
      const id = s.id;
      const name = subjectKind === "user" ? (s.display_name || s.username) : s.name;
      const active = selected && selected.type === subjectKind && selected.id === id;
      return `<div class="pm-subj" data-id="${esc(id)}" data-name="${esc(name)}"
        style="padding:8px 10px;border-radius:6px;cursor:pointer;font-size:13px;margin-bottom:2px;
        ${active ? "background: rgba(var(--accent-2-rgb), 0.20);color: var(--accent-2)" : ""}">${subjLabel(s)}</div>`;
    };

    if (subjectKind === "group") {
      // Verwaltete Gruppen normal; unverwaltete (AD-)Gruppen in einen
      // standardmäßig eingeklappten Ordner.
      const managed = filtered.filter((s) => !s.unmanaged);
      const unmanaged = filtered.filter((s) => s.unmanaged);
      subjListEl.innerHTML = `
        <button class="action-btn" id="pm-new-group" style="width:100%;margin-bottom:8px">➕ Neue Gruppe</button>
        ${managed.length ? managed.map(rowHtml).join("")
          : '<div style="color:var(--subtext);font-size:12px;padding:4px 8px">Keine verwalteten Gruppen.</div>'}
        ${unmanaged.length ? `
          <details style="margin-top:8px">
            <summary style="cursor:pointer;font-size:12px;color:var(--subtext);padding:4px 8px">📁 AD – unverwaltet (${unmanaged.length})</summary>
            <div style="margin-top:4px">${unmanaged.map(rowHtml).join("")}</div>
          </details>` : ""}`;
      subjListEl.querySelector("#pm-new-group")?.addEventListener("click", async () => {
        const name = await uiPrompt("Neue Gruppe", { placeholder: "z.B. Auditor", okText: "Anlegen" });
        if (!name || !name.trim()) return;
        try {
          const g = await api.createGroup({ name: name.trim(), permissions: [] });
          subjects.group = await api.getGroups();
          drawSubjects();
          selectSubject("group", g.id, g.name);
        } catch (e) {
          window.notify?.("Anlegen fehlgeschlagen: " + e.message, "error");
        }
      });
    } else {
      if (!filtered.length) {
        subjListEl.innerHTML = `<div style="color:var(--subtext);font-size:12px;padding:8px">Keine Einträge.</div>`;
        return;
      }
      subjListEl.innerHTML = filtered.map(rowHtml).join("");
    }
    subjListEl.querySelectorAll(".pm-subj").forEach((el) =>
      el.addEventListener("click", () => selectSubject(subjectKind, el.dataset.id, el.dataset.name))
    );
  }

  async function selectSubject(type, id, name) {
    if (dirty && !(await uiConfirm("Ungespeicherte Änderungen verwerfen?", { okText: "Verwerfen", danger: true }))) return;
    selected = { type, id, name };
    grants = new Map();
    dirty = false;
    activeTab = "general";
    tabsEl.style.display = "flex";
    tabsEl.querySelectorAll("[data-pt]").forEach((b) => b.classList.toggle("active", b.dataset.pt === "general"));
    // Kopf: bei Gruppen zusätzlich AD-Kennzeichnung + „Unverwaltet"-Schalter.
    let headExtra = "";
    if (type === "group") {
      const g = (subjects.group || []).find((x) => x.id === id) || {};
      const adBadge = g.is_ad_group ? ' <span style="color:var(--accent);font-size:11px">AD</span>' : "";
      headExtra = `${adBadge}
        <label style="display:inline-flex;align-items:center;gap:6px;margin-left:12px;font-size:12px;color:var(--subtext);cursor:pointer">
          <input type="checkbox" id="pm-unmanaged" ${g.unmanaged ? "checked" : ""} />
          Unverwaltet (AD-Ordner)
        </label>
        <button class="taskbar-btn" id="pm-del-group" style="margin-left:12px;font-size:11px">Gruppe löschen</button>`;
    }
    headEl.innerHTML = `<div style="font-weight:600">${esc(name)}${type === "group" ? headExtra : ""}</div>
      <div style="color:var(--subtext);font-size:12px">${type === "user" ? "Benutzer" : "Gruppe"} · Rechte hier gelten zusätzlich zu Gruppen-Rechten (Verbieten gewinnt)</div>`;
    if (type === "group") {
      headEl.querySelector("#pm-unmanaged")?.addEventListener("change", async (e) => {
        try {
          await api.setGroupUnmanaged(id, e.target.checked);
          const g2 = (subjects.group || []).find((x) => x.id === id);
          if (g2) g2.unmanaged = e.target.checked ? 1 : 0;
          window.notify?.(e.target.checked ? "In AD-Ordner verschoben" : "Aus AD-Ordner geholt", "success");
          drawSubjects();
        } catch (err2) {
          window.notify?.("Fehler: " + err2.message, "error");
          e.target.checked = !e.target.checked;
        }
      });
      headEl.querySelector("#pm-del-group")?.addEventListener("click", async () => {
        if (!(await uiConfirm(`Gruppe „${name}" löschen?`, { okText: "Löschen", danger: true }))) return;
        try {
          await api.deleteGroup(id);
          subjects.group = await api.getGroups();
          selected = null;
          headEl.innerHTML = `<div style="color:var(--subtext);font-size:13px">Wähle links einen Benutzer oder eine Gruppe.</div>`;
          contentEl.innerHTML = "";
          tabsEl.style.display = "none";
          updateSaveBar();
          drawSubjects();
          window.notify?.("Gruppe gelöscht", "success");
        } catch (e) { window.notify?.("Löschen fehlgeschlagen: " + e.message, "error"); }
      });
    }
    contentEl.innerHTML = `<div style="color:var(--subtext)">Lade Rechte…</div>`;
    try {
      const res = await api.getGrants(type, id);
      for (const g of res.grants || []) grants.set(gk(g.scope, g.perm), g.effect);
    } catch (e) {
      contentEl.innerHTML = `<div style="color:var(--danger)">${esc(e.message)}</div>`;
      return;
    }
    drawSubjects();
    drawContent();
    updateSaveBar();
  }

  // ---------------------------------------------------------------
  // Rechte-Editor (Tabs)
  // ---------------------------------------------------------------
  function drawContent() {
    if (!selected) return;
    if (activeTab === "general") drawGeneral();
    else drawClients();
  }

  function permRow(scope, perm) {
    return `<div style="display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid var(--border)">
      <div style="flex:1;font-size:13px">${esc(catalog.labels[perm] || perm)}
        <span style="color:var(--subtext);font-size:11px">(${esc(perm)})</span></div>
      ${triState(scope, perm)}
    </div>`;
  }

  // Presets: setzen mehrere globale Grants auf einmal.
  const VIEW_ONLY_ALLOW = [
    "login", "see_dashboard", "restore_session", "edit_profile_name",
    "access_clients", "see_replay", "see_audit", "see_source", "see_permissions",
  ];
  function applyPreset(kind) {
    if (kind === "admin") {
      // Voller Zugriff: Admin-Wildcard global erlauben.
      setEffect("global", "admin", "allow");
    } else if (kind === "view") {
      // Nur sehen: Lese-Rechte global erlauben, alle übrigen globalen Rechte
      // (inkl. Admin) entfernen. Client-Rechte bleiben unberührt.
      for (const p of catalog.general) {
        setEffect("global", p, VIEW_ONLY_ALLOW.includes(p) ? "allow" : "");
      }
    }
    drawContent();
  }

  function drawGeneral() {
    contentEl.innerHTML = `
      <div style="max-width:640px">
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
          <span style="font-size:12px;color:var(--subtext)">Vorlagen:</span>
          <button class="taskbar-btn" id="pm-preset-admin">👑 Full Admin</button>
          <button class="taskbar-btn" id="pm-preset-view">👁️ Nur sehen</button>
        </div>
        <p style="color:var(--subtext);font-size:13px;margin-top:0">
          Globale Rechte. <b>Erlauben</b> = gewähren, <b>Verbieten</b> = hart entziehen
          (schlägt jede Erlaubnis, auch aus Gruppen), <b>—</b> = keine Einstellung.
          <br>Das Recht <b>Admin</b> ist ein Voll-Zugriff (Wildcard).
        </p>
        ${catalog.general.map((p) => permRow("global", p)).join("")}
      </div>`;
    contentEl.querySelector("#pm-preset-admin")?.addEventListener("click", () => applyPreset("admin"));
    contentEl.querySelector("#pm-preset-view")?.addEventListener("click", () => applyPreset("view"));
  }

  function drawClients() {
    const clients = state.clients || [];
    const q = clientSearch.toLowerCase();
    const filtered = clients.filter((c) =>
      !q || `${c.hostname} ${c.ip || ""}`.toLowerCase().includes(q));

    contentEl.innerHTML = `
      <div>
        <p style="color:var(--subtext);font-size:13px;margin-top:0">
          Client-spezifische Rechte. <b>Clients sehen/zugreifen</b> steuert die
          Sichtbarkeit — ohne dieses Recht (bzw. bei <b>Verbieten</b>) ist der
          Client für das Subjekt versteckt und dort ist nichts möglich.
        </p>
        <input type="text" id="pm-client-search" placeholder="Client suchen…" value="${esc(clientSearch)}"
          style="width:100%;max-width:360px;padding:7px 10px;border-radius:6px;border:1px solid var(--border);background:var(--panel-2);color:var(--text);font-size:13px;margin-bottom:10px" />
        <div id="pm-client-list"></div>
      </div>`;

    const search = contentEl.querySelector("#pm-client-search");
    search.addEventListener("input", () => {
      clientSearch = search.value;
      // nur Liste neu rendern, Fokus behalten
      renderClientList();
    });

    function renderClientList() {
      const q2 = clientSearch.toLowerCase();
      const list = (state.clients || []).filter((c) =>
        !q2 || `${c.hostname} ${c.ip || ""}`.toLowerCase().includes(q2));
      const listEl = contentEl.querySelector("#pm-client-list");
      if (!list.length) {
        listEl.innerHTML = `<div style="color:var(--subtext);font-size:12px">Keine Clients gefunden.</div>`;
        return;
      }
      listEl.innerHTML = list.map((c) => {
        const rows = catalog.client.map((p) => `
          <div style="display:flex;align-items:center;gap:10px;padding:3px 0">
            <div style="flex:1;font-size:12px;color:var(--text)">${esc(catalog.labels[p] || p)}</div>
            ${triState(c.id, p)}
          </div>`).join("");
        return `<details class="panel" style="margin-bottom:8px;padding:8px 12px">
          <summary style="cursor:pointer;font-size:13px;font-weight:600;list-style:none">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${c.online ? "var(--online)" : "var(--subtext)"};margin-right:6px"></span>
            ${esc(c.hostname)} <span style="color:var(--subtext);font-weight:400;font-size:11px">${esc(c.ip || "")}</span>
          </summary>
          <div style="margin-top:8px">${rows}</div>
        </details>`;
      }).join("");
    }
    renderClientList();
  }

  // Tab-Umschalter
  tabsEl.querySelectorAll("[data-pt]").forEach((b) =>
    b.addEventListener("click", () => {
      activeTab = b.dataset.pt;
      tabsEl.querySelectorAll("[data-pt]").forEach((x) => x.classList.toggle("active", x === b));
      drawContent();
    })
  );

  // Speichern / Verwerfen
  body.querySelector("#pm-save").addEventListener("click", async () => {
    if (!selected) return;
    const payload = [];
    for (const [key, effect] of grants.entries()) {
      const [scope, perm] = key.split("|");
      payload.push({ scope, perm, effect });
    }
    try {
      await api.setGrants(selected.type, selected.id, payload);
      dirty = false;
      updateSaveBar();
      window.notify?.("Rechte gespeichert", "success");
    } catch (e) {
      window.notify?.("Speichern fehlgeschlagen: " + e.message, "error");
    }
  });
  body.querySelector("#pm-reset").addEventListener("click", () => {
    if (selected) selectSubject(selected.type, selected.id, selected.name);
  });

  // Subjekt-Art umschalten + Suche
  body.querySelector("#pm-kind-user").addEventListener("click", () => { subjectKind = "user"; drawSubjects(); });
  body.querySelector("#pm-kind-group").addEventListener("click", () => { subjectKind = "group"; drawSubjects(); });
  body.querySelector("#pm-subj-search").addEventListener("input", (e) => { subjSearch = e.target.value; drawSubjects(); });

  // ---------------------------------------------------------------
  // Initial laden
  // ---------------------------------------------------------------
  (async () => {
    try {
      const [cat, users, groups, realmList] = await Promise.all([
        api.getPermissionCatalog(),
        api.getUsers().catch(() => []),
        api.getGroups().catch(() => []),
        api.getRealms().catch(() => []),
      ]);
      catalog = cat;
      subjects.user = users;
      subjects.group = groups;
      realms = realmList || [];
      drawSubjects();
    } catch (e) {
      subjListEl.innerHTML = `<div style="color:var(--danger);font-size:12px;padding:8px">${esc(e.message)}</div>`;
    }
  })();
}

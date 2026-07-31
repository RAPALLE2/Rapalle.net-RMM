// apps/aichat.js
// --------------
// AI-Chatbox: Der Benutzer legt API-Verbindungen an (API-URL, API-Key, Modell)
// und chattet dann über den Backend-Proxy mit dem Modell. Beim Erstellen wählt
// er die Sichtbarkeit: privat, alle Benutzer, oder Freigabe an bestimmte
// Benutzer/Gruppen. Der API-Key wird nur beim Anlegen/Ändern gesendet und ist
// danach nie wieder auslesbar (Backend-Proxy).
import { api } from "../api.js";
import { state } from "../state.js";
import { esc, uiConfirm } from "../utils.js";
import { t } from "../i18n.js";

// =================================================================
// NACHRICHTEN-DARSTELLUNG mit Bildern und Videos
// Antworten der KI werden weiterhin VOLLSTÄNDIG escaped (kein HTML aus dem
// Modell wird ausgeführt). Danach werden nur ERKANNTE Medien-Adressen in
// echte <img>/<video>-Elemente umgewandelt:
//   ![alt](url) und [text](url)   - Markdown
//   nackte URLs auf .png/.jpg/.gif/.webp/.svg/.mp4/.webm/.ogg/.mov
//   data:image/…;base64,…         - direkt eingebettete Bilder
// Erlaubt sind ausschließlich http/https/data:image - damit sind
// javascript:-Adressen und ähnliche Tricks ausgeschlossen.
// =================================================================
const IMG_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|avif)(\?[^\s]*)?$/i;
const VID_EXT = /\.(mp4|webm|ogv|ogg|mov|m4v)(\?[^\s]*)?$/i;

// Aus dem escapten Text zurück in eine echte URL (&amp; -> &) und prüfen.
function safeUrl(escaped) {
  const url = String(escaped).replace(/&amp;/g, "&").replace(/&#39;/g, "'").trim();
  if (/^https?:\/\//i.test(url)) return url;
  if (/^data:image\/(png|jpe?g|gif|webp|svg\+xml|avif);base64,[A-Za-z0-9+/=\s]+$/i.test(url)) return url;
  return null;                       // alles andere (javascript:, file:, …) ablehnen
}

function mediaTag(url, alt = "") {
  const a = esc(alt);
  if (VID_EXT.test(url)) {
    return `<video src="${esc(url)}" controls preload="metadata"
      style="max-width:100%;max-height:320px;border-radius:8px;margin:6px 0;display:block"></video>`;
  }
  // Bilder: anklickbar, öffnet das Original in neuem Tab
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="${a || "Bild öffnen"}">
    <img src="${esc(url)}" alt="${a}" loading="lazy"
      style="max-width:100%;max-height:320px;border-radius:8px;margin:6px 0;display:block" /></a>`;
}

export function renderMessageHtml(text) {
  let out = esc(text ?? "");

  // 1) Markdown-Bilder: ![alt](url)
  out = out.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, rawUrl) => {
    const url = safeUrl(rawUrl);
    return url ? mediaTag(url, alt) : m;
  });

  // 2) Markdown-Links: [text](url) - Medien werden eingebettet, sonst Link
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, rawUrl) => {
    const url = safeUrl(rawUrl);
    if (!url) return m;
    if (IMG_EXT.test(url) || VID_EXT.test(url)) return mediaTag(url, label);
    return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"
      style="color:inherit;text-decoration:underline">${label}</a>`;
  });

  // 3) Nackte URLs (nicht innerhalb bereits erzeugter Tags)
  out = out.replace(/(^|[\s(])((?:https?:\/\/|data:image\/)[^\s<>"')]+)/g, (m, pre, rawUrl) => {
    const url = safeUrl(rawUrl);
    if (!url) return m;
    if (IMG_EXT.test(url) || VID_EXT.test(url) || url.startsWith("data:image/")) {
      return pre + mediaTag(url);
    }
    return `${pre}<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"
      style="color:inherit;text-decoration:underline">${esc(url)}</a>`;
  });

  return out;
}

export function renderAiChat(body, win) {
  let connections = [];
  let subjects = { users: [], groups: [] };
  let currentId = null;
  let messages = [];        // {role:'user'|'assistant', content}
  let busy = false;

  body.innerHTML = `
    <div style="display:flex;height:100%;background:var(--panel)">
      <div style="width:230px;min-width:230px;border-right:1px solid var(--border);display:flex;flex-direction:column">
        <div style="padding:10px 12px;font-weight:700;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:6px">
          🤖 Verbindungen <span style="flex:1"></span>
          <button class="taskbar-btn" id="ai-new" title=t("u_neue_verbindung") style="font-size:12px">＋</button>
        </div>
        <div id="ai-conns" style="flex:1;overflow:auto"></div>
      </div>
      <div style="flex:1;display:flex;flex-direction:column;min-width:0">
        <div id="ai-head" style="padding:8px 14px;border-bottom:1px solid var(--border);font-size:13px;color:var(--subtext)">${t("ai_no_conn_selected")}</div>
        <div id="ai-msgs" style="flex:1;overflow:auto;padding:14px;display:flex;flex-direction:column;gap:10px"></div>
        <div style="display:flex;gap:8px;padding:10px 14px;border-top:1px solid var(--border)">
          <textarea id="ai-input" rows="2" placeholder="Nachricht eingeben… (Enter = senden, Shift+Enter = Zeilenumbruch)"
            style="flex:1;resize:none;background:var(--panel-2);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px;font:inherit;font-size:13px"></textarea>
          <button class="btn-primary" id="ai-send" style="margin:0;width:auto">Senden</button>
        </div>
      </div>
      <div id="ai-editor" style="display:none;position:absolute;inset:0;background:rgba(0,0,0,0.55);align-items:center;justify-content:center;z-index:5"></div>
    </div>
  `;
  // Editor-Overlay braucht position:relative am Wurzel-Element.
  body.firstElementChild.style.position = "relative";

  const connsEl = body.querySelector("#ai-conns");
  const msgsEl = body.querySelector("#ai-msgs");
  const headEl = body.querySelector("#ai-head");
  const inputEl = body.querySelector("#ai-input");
  const sendBtn = body.querySelector("#ai-send");
  const editorEl = body.querySelector("#ai-editor");

  // ---------------- Verbindungs-Liste ----------------
  function drawConns() {
    if (!connections.length) {
      connsEl.innerHTML = `<div style="padding:12px;color:var(--subtext);font-size:12px">
        ${t("ai_no_conns")}</div>`;
      return;
    }
    connsEl.innerHTML = connections.map((c) => `
      <div data-id="${esc(c.id)}" style="padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border);
           background:${c.id === currentId ? "var(--panel-2)" : "transparent"}">
        <div style="font-weight:600;font-size:13px;display:flex;align-items:center;gap:6px">
          ${esc(c.name)}
          ${c.visibility === "all" ? `<span title="Für alle Benutzer">🌐</span>`
            : c.visibility === "shared" ? `<span title=t("u_freigegeben_an_benutzer_gruppen")>👥</span>`
            : `<span title="Privat">🔒</span>`}
        </div>
        <div style="color:var(--subtext);font-size:11px">${esc(c.model)}</div>
        ${c.is_owner ? `<div style="margin-top:4px;display:flex;gap:6px">
          <button class="taskbar-btn" data-edit="${esc(c.id)}" style="font-size:11px;padding:1px 7px">Bearbeiten</button>
          <button class="taskbar-btn" data-del="${esc(c.id)}" style="font-size:11px;padding:1px 7px">Löschen</button>
        </div>` : ""}
      </div>
    `).join("");
    connsEl.querySelectorAll("[data-id]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("[data-edit],[data-del]")) return;
        selectConn(el.dataset.id);
      });
    });
    connsEl.querySelectorAll("[data-edit]").forEach((b) =>
      b.addEventListener("click", () => openEditor(connections.find((c) => c.id === b.dataset.edit))));
    connsEl.querySelectorAll("[data-del]").forEach((b) =>
      b.addEventListener("click", async () => {
        const c = connections.find((x) => x.id === b.dataset.del);
        if (!(await uiConfirm(t("ai_del_conn_q", { name: c.name }), { danger: true }))) return;
        try {
          await api.aiDeleteConnection(c.id);
          if (currentId === c.id) { currentId = null; messages = []; drawChat(); }
          await load();
        } catch (e) { window.notify?.(t("src_delete_fail", { err: e.message }), "error"); }
      }));
  }

  function selectConn(id) {
    if (currentId !== id) { messages = []; }
    currentId = id;
    drawConns();
    drawChat();
  }

  // ---------------- Chat ----------------
  function drawChat() {
    const c = connections.find((x) => x.id === currentId);
    headEl.textContent = c
      ? `${c.name} · Modell: ${c.model}`
      : t("u_keine_verbindung_ausgewahlt");
    if (!c) { msgsEl.innerHTML = ""; return; }
    msgsEl.innerHTML = messages.map((m) => `
      <div style="align-self:${m.role === "user" ? "flex-end" : "flex-start"};max-width:80%;
           background:${m.role === "user" ? "var(--accent, #2f6feb)" : "var(--panel-2)"};
           color:${m.role === "user" ? "#fff" : "var(--text)"};
           border:1px solid var(--border);border-radius:10px;padding:8px 12px;font-size:13px;white-space:pre-wrap;word-break:break-word">${renderMessageHtml(m.content)}</div>
    `).join("") + (busy ? `<div style="color:var(--subtext);font-size:12px">⏳ ${esc(c.model)} denkt nach…</div>` : "");
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  async function send() {
    const text = inputEl.value.trim();
    const c = connections.find((x) => x.id === currentId);
    if (!text || !c || busy) {
      if (!c) window.notify?.(t("u_bitte_zuerst_links_eine_verbindung"), "info");
      return;
    }
    inputEl.value = "";
    messages.push({ role: "user", content: text });
    busy = true; sendBtn.disabled = true;
    drawChat();
    try {
      const res = await api.aiChat(c.id, messages);
      messages.push({ role: "assistant", content: res.reply });
    } catch (e) {
      messages.push({ role: "assistant", content: `⚠ ${t("error")}: ${e.message}` });
    }
    busy = false; sendBtn.disabled = false;
    drawChat();
  }
  sendBtn.addEventListener("click", send);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  // ---------------- Editor (Anlegen/Bearbeiten) ----------------
  function openEditor(existing) {
    const isEdit = !!existing;
    const sh = (existing?.shares) || [];
    const isChecked = (type, id) => sh.some((s) => s.subject_type === type && s.subject_id === id);
    editorEl.style.display = "flex";
    editorEl.innerHTML = `
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px;width:440px;max-height:90%;overflow:auto">
        <h3 style="margin:0 0 12px">${isEdit ? t("u_verbindung_bearbeiten") : t("u_neue_ai_verbindung")}</h3>
        <div class="form-row"><label>Name</label>
          <input id="aie-name" value="${esc(existing?.name || "")}" placeholder="z.B. GPT-4o Firmenkonto" /></div>
        <div class="form-row"><label>API-URL</label>
          <input id="aie-url" value="${esc(existing?.api_url || "")}" placeholder="https://api.openai.com" /></div>
        <p style="color:var(--subtext);font-size:11px;margin:2px 0 8px">
          ${t("ai_api_hint")}</p>
        <div class="form-row"><label>${t("ai_model")}</label>
          <input id="aie-model" value="${esc(existing?.model || "")}" placeholder="z.B. gpt-4o-mini" /></div>
        <div class="form-row"><label>API-Key</label>
          <input id="aie-key" type="password" placeholder="${isEdit ? t("ai_key_keep") : "sk-…"}" /></div>
        <div class="form-row"><label>${t("ai_visibility")}</label>
          <select id="aie-vis">
            <option value="private" ${(existing?.visibility ?? "private") === "private" ? "selected" : ""}>🔒 ${t("ai_vis_private")}</option>
            <option value="all" ${existing?.visibility === "all" ? "selected" : ""}>🌐 ${t("ai_vis_all")}</option>
            <option value="shared" ${existing?.visibility === "shared" ? "selected" : ""}>👥 ${t("ai_vis_shared")}</option>
          </select></div>
        <div id="aie-shares" style="display:${existing?.visibility === "shared" ? "block" : "none"};border:1px solid var(--border);border-radius:8px;padding:8px;max-height:180px;overflow:auto;font-size:12px">
          <div style="font-weight:700;margin-bottom:4px">${t("tab_users")}</div>
          ${subjects.users.map((u) => `<label style="display:flex;gap:6px;align-items:center;cursor:pointer">
            <input type="checkbox" data-stype="user" data-sid="${esc(u.id)}" ${isChecked("user", u.id) ? "checked" : ""}/> ${esc(u.label)}</label>`).join("")}
          <div style="font-weight:700;margin:8px 0 4px">${t("pm_groups")}</div>
          ${subjects.groups.map((g) => `<label style="display:flex;gap:6px;align-items:center;cursor:pointer">
            <input type="checkbox" data-stype="group" data-sid="${esc(g.id)}" ${isChecked("group", g.id) ? "checked" : ""}/> ${esc(g.label)}</label>`).join("")
          || `<div style="color:var(--subtext)">${t("set_no_groups")}</div>`}
        </div>
        <div id="aie-error" class="hidden" style="color:var(--danger);font-size:12px;margin-top:8px"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
          <button class="taskbar-btn" id="aie-cancel">${t("cancel")}</button>
          <button class="btn-primary" id="aie-save" style="margin:0;width:auto">${isEdit ? t("save") : t("ai_create")}</button>
        </div>
      </div>
    `;
    const q = (sel) => editorEl.querySelector(sel);
    q("#aie-vis").addEventListener("change", () => {
      q("#aie-shares").style.display = q("#aie-vis").value === "shared" ? "block" : "none";
    });
    q("#aie-cancel").addEventListener("click", () => { editorEl.style.display = "none"; });
    q("#aie-save").addEventListener("click", async () => {
      const shares = [...editorEl.querySelectorAll("#aie-shares input:checked")]
        .map((cb) => ({ subject_type: cb.dataset.stype, subject_id: cb.dataset.sid }));
      const payload = {
        name: q("#aie-name").value.trim(),
        api_url: q("#aie-url").value.trim(),
        model: q("#aie-model").value.trim(),
        api_key: q("#aie-key").value.trim() || null,
        visibility: q("#aie-vis").value,
        shares,
      };
      try {
        if (isEdit) await api.aiUpdateConnection(existing.id, payload);
        else await api.aiCreateConnection(payload);
        editorEl.style.display = "none";
        await load();
      } catch (e) {
        const err = q("#aie-error");
        err.textContent = e.message;
        err.classList.remove("hidden");
      }
    });
  }
  body.querySelector("#ai-new").addEventListener("click", () => openEditor(null));

  // ---------------- Laden ----------------
  async function load() {
    try {
      const [conns, subj] = await Promise.all([
        api.aiConnections(),
        api.aiShareSubjects().catch(() => ({ users: [], groups: [] })),
      ]);
      connections = conns;
      subjects = subj;
      if (currentId && !connections.some((c) => c.id === currentId)) currentId = null;
      drawConns();
      drawChat();
    } catch (e) {
      connsEl.innerHTML = `<div style="padding:12px;color:var(--danger);font-size:12px">${esc(e.message)}</div>`;
    }
  }
  load();
}

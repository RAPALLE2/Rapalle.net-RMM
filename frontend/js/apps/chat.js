// apps/chat.js
// ------------
// Chat zwischen Dashboard-Benutzern (WhatsApp-artig):
//   - Direktnachrichten und Gruppen (mit Gruppen-Admins)
//   - Ungelesen-Badges, Live-Updates über das Socket-Event "chat:message"
//   - Gruppen-Verwaltung (umbenennen, Mitglieder, Admin-Status, verlassen,
//     löschen) für Gruppen-Admins
// Benötigt das globale Recht 'use_chat' (Server erzwingt es zusätzlich).

import { api } from "../api.js";
import { state } from "../state.js";
import { esc, uiConfirm, uiPrompt } from "../utils.js";
import { dashboardSocket } from "../socket.js";
import { registerCleanup } from "../windowmanager.js";

const fmtTime = (ms) => new Date(ms).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const fmtDay = (ms) => new Date(ms).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });

export function renderChat(body, win) {
  let convs = [];
  let currentId = win?.props?.conversationId || null;
  let messages = [];
  let oldestTs = null;
  let showSettings = false;
  let destroyed = false;

  body.innerHTML = `
    <div style="display:flex;height:100%;background:var(--panel)">
      <div style="width:250px;flex:none;border-right:1px solid var(--border);display:flex;flex-direction:column;min-width:0">
        <div style="display:flex;gap:6px;padding:8px;border-bottom:1px solid var(--border)">
          <button class="btn-primary" id="ch-new-dm" style="flex:1;font-size:12.5px;padding:6px 4px">✉️ Neuer Chat</button>
          <button class="taskbar-btn" id="ch-new-group" style="flex:1;font-size:12.5px">👥 Gruppe</button>
        </div>
        <div id="ch-list" style="flex:1;overflow:auto"></div>
      </div>
      <div id="ch-main" style="flex:1;display:flex;flex-direction:column;min-width:0"></div>
    </div>`;

  const listEl = body.querySelector("#ch-list");
  const mainEl = body.querySelector("#ch-main");

  // ---------------- Unterhaltungs-Liste ----------------

  async function loadConvs(keepScroll = false) {
    try { convs = await api.chatConversations(); } catch (e) {
      listEl.innerHTML = `<div style="padding:12px;color:var(--danger);font-size:13px">${esc(e.message)}</div>`;
      return;
    }
    drawList();
    if (currentId && !convs.find((c) => c.id === currentId)) { currentId = null; drawMain(); }
    if (!keepScroll && currentId) drawMain();
  }

  function drawList() {
    if (!convs.length) {
      listEl.innerHTML = `<div style="padding:14px;color:var(--subtext);font-size:13px">
        Noch keine Unterhaltungen.<br>Starte oben einen neuen Chat.</div>`;
      return;
    }
    listEl.innerHTML = convs.map((c) => {
      const icon = c.type === "group" ? "👥" : "👤";
      const last = c.last_message
        ? `${c.type === "group" ? esc(c.last_message.sender || "?") + ": " : ""}${esc(c.last_message.text)}`
        : "<i>Keine Nachrichten</i>";
      return `
      <div class="ch-conv" data-id="${c.id}" style="display:flex;gap:8px;align-items:center;padding:9px 10px;cursor:pointer;
           border-bottom:1px solid var(--border);${c.id === currentId ? "background:var(--panel-2,#1b2740)" : ""}">
        <span style="font-size:19px;flex:none">${icon}</span>
        <div style="flex:1;min-width:0">
          <div style="display:flex;justify-content:space-between;gap:6px">
            <strong style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(c.name)}</strong>
            ${c.last_message ? `<span style="font-size:10.5px;color:var(--subtext);flex:none">${fmtTime(c.last_message.created_at)}</span>` : ""}
          </div>
          <div style="font-size:11.5px;color:var(--subtext);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${last}</div>
        </div>
        ${c.unread ? `<span style="flex:none;background:var(--accent);color:#fff;border-radius:9px;font-size:10.5px;
             font-weight:700;padding:1px 6px;min-width:17px;text-align:center">${c.unread}</span>` : ""}
      </div>`;
    }).join("");
    listEl.querySelectorAll(".ch-conv").forEach((el) =>
      el.addEventListener("click", () => { currentId = el.dataset.id; showSettings = false; drawList(); drawMain(); }));
  }

  // ---------------- Nachrichten-Bereich ----------------

  async function drawMain() {
    const conv = convs.find((c) => c.id === currentId);
    if (!conv) {
      mainEl.innerHTML = `<div style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--subtext);font-size:14px">
        💬 Wähle links eine Unterhaltung oder starte eine neue.</div>`;
      return;
    }
    mainEl.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;padding:9px 12px;border-bottom:1px solid var(--border)">
        <span style="font-size:18px">${conv.type === "group" ? "👥" : "👤"}</span>
        <div style="flex:1;min-width:0">
          <strong style="font-size:14px">${esc(conv.name)}</strong>
          <div style="font-size:11px;color:var(--subtext)">
            ${conv.type === "group"
              ? `${conv.members.length} Mitglieder${conv.is_admin ? " · du bist Gruppen-Admin" : ""}`
              : "Direktnachricht"}</div>
        </div>
        ${conv.type === "group" ? `<button class="taskbar-btn" id="ch-settings">⚙️ Gruppe</button>` : ""}
        <button class="taskbar-btn" id="ch-del" title="${conv.type === "group" ? (conv.is_admin ? "Gruppe löschen" : "Gruppe verlassen") : "Chat löschen"}">
          ${conv.type === "group" && !conv.is_admin ? "🚪" : "🗑"}</button>
      </div>
      <div id="ch-settings-box" style="display:none;border-bottom:1px solid var(--border);padding:10px 12px;max-height:45%;overflow:auto"></div>
      <div id="ch-msgs" style="flex:1;overflow:auto;padding:10px 12px;display:flex;flex-direction:column;gap:4px"></div>
      <div style="display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--border)">
        <textarea id="ch-input" rows="1" placeholder="Nachricht schreiben…  (Enter = senden, Shift+Enter = Zeile)"
          style="flex:1;resize:none;min-height:36px;max-height:120px"></textarea>
        <button class="btn-primary" id="ch-send" style="flex:none; width: 100px;">Senden ➤</button>
      </div>`;

    const input = mainEl.querySelector("#ch-input");
    mainEl.querySelector("#ch-send").addEventListener("click", send);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });
    mainEl.querySelector("#ch-settings")?.addEventListener("click", () => {
      showSettings = !showSettings; drawSettings(conv);
    });
    mainEl.querySelector("#ch-del").addEventListener("click", async () => {
      if (conv.type === "group" && !conv.is_admin) {
        if (!(await uiConfirm(`Gruppe „${conv.name}“ verlassen?`, { okText: "Verlassen" }))) return;
        await api.chatRemoveMember(conv.id, state.user.id);
      } else {
        const what = conv.type === "group" ? `Gruppe „${conv.name}“ für ALLE löschen?` : "Chat für beide Seiten löschen?";
        if (!(await uiConfirm(what, { okText: "Löschen", danger: true }))) return;
        await api.chatDelete(conv.id);
      }
      currentId = null; loadConvs();
    });

    async function send() {
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      try {
        await api.chatSend(conv.id, text);
        await loadMessages(conv, true);
        loadConvs(true);
      } catch (e) { window.notify?.("Senden fehlgeschlagen: " + e.message, "error"); }
      input.focus();
    }

    if (showSettings) drawSettings(conv);
    await loadMessages(conv, true);
    api.chatRead(conv.id).then(() => loadConvs(true)).catch(() => {});
    input.focus();
  }

  async function loadMessages(conv, jumpToEnd) {
    try { messages = await api.chatMessages(conv.id); } catch { messages = []; }
    oldestTs = messages.length ? messages[0].created_at : null;
    drawMessages(conv, jumpToEnd);
  }

  function drawMessages(conv, jumpToEnd) {
    const box = mainEl.querySelector("#ch-msgs");
    if (!box) return;
    let lastDay = "";
    box.innerHTML = (messages.length ? "" :
      `<div style="color:var(--subtext);font-size:13px;text-align:center;margin-top:20px">Noch keine Nachrichten – schreib die erste! 👋</div>`)
      + messages.map((m) => {
        const mine = m.sender_id === state.user?.id;
        const day = fmtDay(m.created_at);
        const sep = day !== lastDay
          ? `<div style="text-align:center;font-size:11px;color:var(--subtext);margin:8px 0 4px">— ${day} —</div>` : "";
        lastDay = day;
        return `${sep}
        <div style="display:flex;${mine ? "justify-content:flex-end" : ""}">
          <div style="max-width:72%;background:${mine ? "var(--accent)" : "var(--panel-2,#1b2740)"};
               color:${mine ? "#fff" : "var(--text)"};border-radius:12px;
               border-bottom-${mine ? "right" : "left"}-radius:4px;padding:6px 10px">
            ${conv.type === "group" && !mine
              ? `<div style="font-size:11px;font-weight:700;opacity:.85">${esc(m.sender_name || "?")}</div>` : ""}
            <div style="font-size:13.5px;white-space:pre-wrap;overflow-wrap:anywhere">${esc(m.text)}</div>
            <div style="font-size:10px;opacity:.65;text-align:right;margin-top:2px">${fmtTime(m.created_at)}</div>
          </div>
        </div>`;
      }).join("");
    // Ältere Nachrichten nachladen
    if (messages.length >= 50) {
      const more = document.createElement("button");
      more.className = "taskbar-btn";
      more.style.cssText = "align-self:center;margin-bottom:6px;font-size:11.5px";
      more.textContent = "↑ Ältere Nachrichten laden";
      more.addEventListener("click", async () => {
        try {
          const older = await api.chatMessages(conv.id, oldestTs);
          if (older.length) { messages = [...older, ...messages]; oldestTs = messages[0].created_at; drawMessages(conv, false); }
          else more.remove();
        } catch {}
      });
      box.prepend(more);
    }
    if (jumpToEnd) box.scrollTop = box.scrollHeight;
  }

  // ---------------- Gruppen-Einstellungen ----------------

  async function drawSettings(conv) {
    const boxEl = mainEl.querySelector("#ch-settings-box");
    if (!boxEl) return;
    boxEl.style.display = showSettings ? "" : "none";
    if (!showSettings) return;
    const admin = conv.is_admin;
    let allUsers = [];
    try { allUsers = await api.chatUsers(); } catch {}
    const memberIds = new Set(conv.members.map((m) => m.user_id));
    const addable = allUsers.filter((u) => !memberIds.has(u.id));

    boxEl.innerHTML = `
      ${admin ? `<div style="display:flex;gap:6px;margin-bottom:8px">
        <input id="chs-name" value="${esc(conv.name)}" style="flex:1" />
        <button class="taskbar-btn" id="chs-rename">Umbenennen</button>
      </div>` : ""}
      <div style="font-size:12px;color:var(--subtext);margin-bottom:4px">Mitglieder</div>
      ${conv.members.map((m) => `
        <div style="display:flex;align-items:center;gap:8px;padding:3px 0;font-size:13px">
          <span style="flex:1">${esc(m.username)}${m.user_id === state.user?.id ? " (du)" : ""}
            ${m.is_admin ? ` <span style="font-size:10.5px;background:var(--accent);color:#fff;border-radius:6px;padding:1px 5px">Admin</span>` : ""}
          </span>
          ${admin && m.user_id !== state.user?.id ? `
            <button class="taskbar-btn" data-admin="${m.user_id}" data-to="${m.is_admin ? 0 : 1}" style="font-size:11px">
              ${m.is_admin ? "Admin entziehen" : "Zum Admin machen"}</button>
            <button class="taskbar-btn" data-kick="${m.user_id}" style="font-size:11px">Entfernen</button>` : ""}
        </div>`).join("")}
      ${admin && addable.length ? `
        <div style="display:flex;gap:6px;margin-top:8px">
          <select id="chs-add" style="flex:1">${addable.map((u) => `<option value="${u.id}">${esc(u.username)}</option>`).join("")}</select>
          <button class="taskbar-btn" id="chs-add-btn">+ Hinzufügen</button>
        </div>` : ""}`;

    boxEl.querySelector("#chs-rename")?.addEventListener("click", async () => {
      const name = boxEl.querySelector("#chs-name").value.trim();
      if (!name) return;
      try { await api.chatRename(conv.id, name); loadConvs(); } catch (e) { window.notify?.(e.message, "error"); }
    });
    boxEl.querySelector("#chs-add-btn")?.addEventListener("click", async () => {
      try { await api.chatAddMember(conv.id, boxEl.querySelector("#chs-add").value); loadConvs(); }
      catch (e) { window.notify?.(e.message, "error"); }
    });
    boxEl.querySelectorAll("[data-kick]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm("Mitglied aus der Gruppe entfernen?", { okText: "Entfernen", danger: true }))) return;
      try { await api.chatRemoveMember(conv.id, b.dataset.kick); loadConvs(); } catch (e) { window.notify?.(e.message, "error"); }
    }));
    boxEl.querySelectorAll("[data-admin]").forEach((b) => b.addEventListener("click", async () => {
      try { await api.chatSetAdmin(conv.id, b.dataset.admin, b.dataset.to === "1"); loadConvs(); }
      catch (e) { window.notify?.(e.message, "error"); }
    }));
  }

  // ---------------- Neue Unterhaltung ----------------

  body.querySelector("#ch-new-dm").addEventListener("click", async () => {
    let users = [];
    try { users = await api.chatUsers(); } catch (e) { window.notify?.(e.message, "error"); return; }
    if (!users.length) { window.notify?.("Keine anderen Benutzer vorhanden", "info"); return; }
    pickerDialog("Neuer Chat mit …", users, false, async (ids) => {
      if (!ids.length) return;
      const conv = await api.chatCreate({ type: "dm", user_id: ids[0] });
      currentId = conv.id;
      await loadConvs();
      drawMain();
    });
  });

  body.querySelector("#ch-new-group").addEventListener("click", async () => {
    const name = await uiPrompt("Name der neuen Gruppe", { placeholder: "z.B. Technik-Team" });
    if (!name || !name.trim()) return;
    let users = [];
    try { users = await api.chatUsers(); } catch {}
    pickerDialog(`Mitglieder für „${esc(name.trim())}“`, users, true, async (ids) => {
      const conv = await api.chatCreate({ type: "group", name: name.trim(), member_ids: ids });
      currentId = conv.id;
      await loadConvs();
      drawMain();
    });
  });

  // Kleiner Auswahl-Dialog (Einzel- oder Mehrfachauswahl von Benutzern).
  function pickerDialog(title, users, multi, onDone) {
    const back = document.createElement("div");
    back.className = "widget-picker-back";
    back.innerHTML = `
      <div class="widget-picker" style="max-width:340px">
        <div class="wp-head"><strong>${title}</strong><button class="dash-w-btn" data-close>✕</button></div>
        <div class="wp-body" style="max-height:320px;overflow:auto">
          ${users.map((u) => `
            <label style="display:flex;gap:8px;align-items:center;padding:6px 4px;cursor:pointer;font-size:13.5px">
              <input type="${multi ? "checkbox" : "radio"}" name="ch-pick" value="${u.id}" />
              <span>👤 ${esc(u.username)}</span>
            </label>`).join("") || `<div style="color:var(--subtext);font-size:13px">Keine Benutzer.</div>`}
        </div>
        <div style="padding:10px;display:flex;justify-content:flex-end">
          <button class="btn-primary" data-ok>${multi ? "Gruppe erstellen" : "Chat starten"}</button>
        </div>
      </div>`;
    document.body.appendChild(back);
    const close = () => back.remove();
    back.addEventListener("click", (e) => { if (e.target === back) close(); });
    back.querySelector("[data-close]").addEventListener("click", close);
    back.querySelector("[data-ok]").addEventListener("click", async () => {
      const ids = [...back.querySelectorAll("input[name=ch-pick]:checked")].map((i) => i.value);
      close();
      try { await onDone(ids); } catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  // ---------------- Live-Updates ----------------

  const onMsg = (data) => {
    if (destroyed) return;
    if (!(data.member_ids || []).includes(state.user?.id)) return;
    if (data.conversation_id === currentId) {
      const conv = convs.find((c) => c.id === currentId);
      if (conv) { loadMessages(conv, true); api.chatRead(currentId).catch(() => {}); }
    }
    loadConvs(true);
  };
  const onChanged = () => { if (!destroyed) loadConvs(true); };
  dashboardSocket.on("chat:message", onMsg);
  dashboardSocket.on("chat:changed", onChanged);
  if (win?.key) registerCleanup(win.key, () => {
    destroyed = true;
    dashboardSocket.off("chat:message", onMsg);
    dashboardSocket.off("chat:changed", onChanged);
  });

  loadConvs().then(() => { if (currentId) drawMain(); else drawMain(); });
}

// clock.js (Modul "collab", Teilfunktion "clock")
// -----------------------------------------------
// Uhr mit vier Registern: Uhr, Stoppuhr, Timer, Wecker.
// Die Logik (Klingeln, Speichern) steckt in clockcore.js und läuft auch
// bei geschlossenem Fenster weiter; dieses Fenster zeigt nur an und bedient.

import { esc, uiConfirm } from "/js/app/utils.js";
import { registerCleanup } from "/js/app/windowmanager.js";
import * as core from "./clockcore.js";

const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const ZONES = [
  ["UTC", "UTC"], ["London", "Europe/London"], ["New York", "America/New_York"],
  ["Los Angeles", "America/Los_Angeles"], ["Dubai", "Asia/Dubai"], ["Tokio", "Asia/Tokyo"], ["Sydney", "Australia/Sydney"],
];
const pad = (n) => String(n).padStart(2, "0");

export function renderClock(body, win) {
  core.ensureStarted();
  let tab = "clock";
  let raf = null;
  let editAlarm = null;        // null | {} (neu) | alarm

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;padding:12px;box-sizing:border-box;gap:12px">
      <div class="tab-bar" id="ck-tabs">
        <button class="tab-btn active" data-tab="clock">🕒 Uhr</button>
        <button class="tab-btn" data-tab="sw">⏱️ Stoppuhr</button>
        <button class="tab-btn" data-tab="timer">⏳ Timer</button>
        <button class="tab-btn" data-tab="alarm">⏰ Wecker</button>
      </div>
      <div id="ck-body" style="flex:1;overflow:auto;min-height:0"></div>
    </div>`;
  const $ = (s) => body.querySelector(s);
  body.querySelectorAll("#ck-tabs [data-tab]").forEach((b) => b.addEventListener("click", () => {
    tab = b.dataset.tab; editAlarm = null;
    body.querySelectorAll("#ck-tabs [data-tab]").forEach((x) => x.classList.toggle("active", x === b));
    draw();
  }));

  const big = (id, extra = "") =>
    `<div id="${id}" style="font-size:46px;font-weight:300;text-align:center;font-variant-numeric:tabular-nums;letter-spacing:1px;${extra}"></div>`;

  // ------------------------------------------------------------ Uhr
  function drawClock() {
    $("#ck-body").innerHTML = `
      <div style="display:flex;flex-direction:column;align-items:center;gap:6px">
        <svg id="ck-analog" viewBox="0 0 200 200" width="190" height="190" aria-hidden="true">
          <circle cx="100" cy="100" r="94" fill="var(--panel-2)" stroke="var(--border)" stroke-width="2"/>
          ${Array.from({ length: 60 }, (_, i) => {
            const a = (i * 6 * Math.PI) / 180, r1 = i % 5 ? 86 : 80;
            return `<line x1="${100 + Math.sin(a) * r1}" y1="${100 - Math.cos(a) * r1}" x2="${100 + Math.sin(a) * 90}" y2="${100 - Math.cos(a) * 90}"
              stroke="var(--subtext)" stroke-width="${i % 5 ? 1 : 2.5}" stroke-linecap="round"/>`;
          }).join("")}
          <line id="ck-h" x1="100" y1="100" x2="100" y2="52" stroke="var(--text)" stroke-width="5" stroke-linecap="round"/>
          <line id="ck-m" x1="100" y1="100" x2="100" y2="30" stroke="var(--text)" stroke-width="3" stroke-linecap="round"/>
          <line id="ck-s" x1="100" y1="112" x2="100" y2="24" stroke="var(--accent)" stroke-width="1.5" stroke-linecap="round"/>
          <circle cx="100" cy="100" r="4" fill="var(--accent)"/>
        </svg>
        ${big("ck-digital")}
        <div id="ck-date" style="color:var(--subtext);font-size:13px"></div>
      </div>
      <div style="margin-top:14px;border-top:1px solid var(--border);padding-top:8px">
        <div style="font-size:11px;color:var(--subtext);margin-bottom:4px">WELTZEIT</div>
        <div id="ck-world" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:6px"></div>
      </div>`;
  }
  function tickClock() {
    const d = new Date();
    const set = (id, deg) => $(id)?.setAttribute("transform", `rotate(${deg} 100 100)`);
    set("#ck-h", (d.getHours() % 12) * 30 + d.getMinutes() * 0.5);
    set("#ck-m", d.getMinutes() * 6 + d.getSeconds() * 0.1);
    set("#ck-s", d.getSeconds() * 6);
    const dg = $("#ck-digital"); if (dg) dg.textContent = d.toLocaleTimeString("de-DE");
    const dt = $("#ck-date"); if (dt) dt.textContent = d.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    const w = $("#ck-world");
    if (w) w.innerHTML = ZONES.map(([n, z]) => {
      let t = "–";
      try { t = d.toLocaleTimeString("de-DE", { timeZone: z, hour: "2-digit", minute: "2-digit" }); } catch { /* Zone unbekannt */ }
      return `<div style="border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:12px;display:flex;justify-content:space-between">
        <span style="color:var(--subtext)">${esc(n)}</span><b style="font-variant-numeric:tabular-nums">${t}</b></div>`;
    }).join("");
  }

  // ------------------------------------------------------------ Stoppuhr
  function drawStopwatch() {
    const sw = core.getStopwatch();
    $("#ck-body").innerHTML = `
      ${big("ck-sw", "margin:20px 0 14px")}
      <div style="display:flex;gap:8px;justify-content:center">
        <button class="${sw.running ? "btn btn-warn" : "btn-primary"}" id="ck-sw-go" style="min-width:110px">${sw.running ? "⏸ Stopp" : "▶ Start"}</button>
        <button class="btn" id="ck-sw-lap" ${sw.running ? "" : "disabled"}>🏁 Runde</button>
        <button class="btn" id="ck-sw-reset" ${sw.running || !core.stopwatchElapsed(sw) ? "disabled" : ""}>↺ Zurücksetzen</button>
      </div>
      <div id="ck-laps" style="margin-top:16px;font-size:13px;font-variant-numeric:tabular-nums"></div>`;
    $("#ck-sw-go").addEventListener("click", () => { core.stopwatchToggle(); drawStopwatch(); });
    $("#ck-sw-lap").addEventListener("click", () => { core.stopwatchLap(); drawLaps(); });
    $("#ck-sw-reset").addEventListener("click", () => { core.stopwatchReset(); drawStopwatch(); });
    drawLaps();
  }
  function drawLaps() {
    const laps = core.getStopwatch().laps;
    const box = $("#ck-laps"); if (!box) return;
    box.innerHTML = laps.map((t, i) => {
      const prev = laps[i + 1] || 0;
      return `<div style="display:flex;justify-content:space-between;padding:5px 4px;border-bottom:1px solid var(--border)">
        <span style="color:var(--subtext)">Runde ${laps.length - i}</span><span>+${core.fmtDuration(t - prev, true)}</span><b>${core.fmtDuration(t, true)}</b></div>`;
    }).join("");
  }

  // ------------------------------------------------------------ Timer
  function drawTimers() {
    const timers = core.getTimers();
    $("#ck-body").innerHTML = `
      <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-bottom:10px">
        <div class="form-row" style="margin:0;width:70px"><label>Std.</label><input type="number" id="ck-th" min="0" max="99" value="0"></div>
        <div class="form-row" style="margin:0;width:70px"><label>Min.</label><input type="number" id="ck-tm" min="0" max="59" value="5"></div>
        <div class="form-row" style="margin:0;width:70px"><label>Sek.</label><input type="number" id="ck-ts" min="0" max="59" value="0"></div>
        <div class="form-row" style="margin:0;flex:1;min-width:120px"><label>Bezeichnung</label><input type="text" id="ck-tl" placeholder="z. B. Tee"></div>
        <button class="btn-primary" id="ck-tadd">▶ Starten</button>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">
        ${[1, 3, 5, 10, 15, 30, 60].map((m) => `<button class="btn btn-sm" data-quick="${m}">${m < 60 ? m + " min" : "1 h"}</button>`).join("")}
      </div>
      <div id="ck-tlist" style="display:flex;flex-direction:column;gap:8px">
        ${timers.length ? "" : `<div style="color:var(--subtext);font-size:13px">Kein Timer aktiv. Timer laufen auch weiter, wenn das Fenster zu ist.</div>`}
        ${timers.map((t) => `
          <div style="display:flex;align-items:center;gap:10px;border:1px solid var(--border);border-radius:8px;padding:8px 10px">
            <div style="flex:1;min-width:0">
              <div style="font-size:24px;font-variant-numeric:tabular-nums" data-trem="${t.id}"></div>
              <div style="font-size:11.5px;color:var(--subtext)">${esc(t.label || "Timer")} · ${core.fmtDuration(t.duration)}</div>
              <div style="height:4px;background:var(--panel-2);border-radius:3px;margin-top:4px;overflow:hidden">
                <div data-tbar="${t.id}" style="height:100%;background:var(--accent);width:0%"></div></div>
            </div>
            <button class="btn btn-icon" data-tgo="${t.id}" title="${t.running ? "Pause" : "Weiter"}">${t.running ? "⏸" : "▶"}</button>
            <button class="btn btn-icon" data-treset="${t.id}" title="Zurücksetzen">↺</button>
            <button class="btn btn-icon btn-danger" data-tdel="${t.id}" title="Entfernen">✕</button>
          </div>`).join("")}
      </div>`;
    const start = (ms, label) => { if (ms <= 0) return; core.requestDesktopNotifications(); core.addTimer(ms, label); drawTimers(); };
    $("#ck-tadd").addEventListener("click", () => {
      const ms = ((+$("#ck-th").value || 0) * 3600 + (+$("#ck-tm").value || 0) * 60 + (+$("#ck-ts").value || 0)) * 1000;
      start(ms, $("#ck-tl").value.trim());
    });
    body.querySelectorAll("[data-quick]").forEach((b) => b.addEventListener("click", () => start(+b.dataset.quick * 60000, "")));
    body.querySelectorAll("[data-tgo]").forEach((b) => b.addEventListener("click", () => { core.toggleTimer(b.dataset.tgo); drawTimers(); }));
    body.querySelectorAll("[data-treset]").forEach((b) => b.addEventListener("click", () => { core.resetTimer(b.dataset.treset); drawTimers(); }));
    body.querySelectorAll("[data-tdel]").forEach((b) => b.addEventListener("click", () => { core.removeTimer(b.dataset.tdel); drawTimers(); }));
    tickTimers();
  }
  function tickTimers() {
    for (const t of core.getTimers()) {
      const rem = core.timerRemaining(t);
      const el = body.querySelector(`[data-trem="${t.id}"]`);
      if (el) el.textContent = core.fmtDuration(Math.ceil(rem / 1000) * 1000);
      const bar = body.querySelector(`[data-tbar="${t.id}"]`);
      if (bar) bar.style.width = `${t.duration ? 100 - (rem / t.duration) * 100 : 0}%`;
    }
  }

  // ------------------------------------------------------------ Wecker
  function drawAlarms() {
    const alarms = core.getAlarms();
    if (editAlarm) return drawAlarmEdit();
    $("#ck-body").innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
        <span style="font-size:12px;color:var(--subtext)">Wecker klingeln, solange das Dashboard in einem Browser-Tab offen ist.</span>
        <button class="btn-primary" id="ck-anew">＋ Wecker</button>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px">
        ${alarms.length ? alarms.map((a) => `
          <div style="display:flex;align-items:center;gap:12px;border:1px solid var(--border);border-radius:8px;padding:8px 12px;${a.enabled ? "" : "opacity:.55"}">
            <div style="font-size:28px;font-variant-numeric:tabular-nums">${esc(a.time)}</div>
            <div style="flex:1;min-width:0">
              <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.label || "Wecker")}</div>
              <div style="font-size:11.5px;color:var(--subtext)">${a.days?.length ? (a.days.length === 7 ? "täglich" : a.days.map((d) => DAYS[d]).join(", ")) : "einmalig"}</div>
            </div>
            <label title="An/Aus"><input type="checkbox" data-aon="${esc(a.id)}" ${a.enabled ? "checked" : ""}></label>
            <button class="btn btn-icon btn-sm" data-aedit="${esc(a.id)}" title="Bearbeiten">✏️</button>
            <button class="btn btn-icon btn-sm btn-danger" data-adel="${esc(a.id)}" title="Löschen">✕</button>
          </div>`).join("") : `<div style="color:var(--subtext);font-size:13px">Noch kein Wecker gestellt.</div>`}
      </div>`;
    $("#ck-anew").addEventListener("click", () => { editAlarm = {}; drawAlarms(); });
    const save = async (list) => { try { await core.saveAlarms(list); drawAlarms(); } catch (e) { window.notify?.(e.message, "error"); } };
    body.querySelectorAll("[data-aon]").forEach((c) => c.addEventListener("change", () =>
      save(alarms.map((a) => (a.id === c.dataset.aon ? { ...a, enabled: c.checked } : a)))));
    body.querySelectorAll("[data-aedit]").forEach((b) => b.addEventListener("click", () => {
      editAlarm = alarms.find((a) => a.id === b.dataset.aedit); drawAlarms();
    }));
    body.querySelectorAll("[data-adel]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await uiConfirm("Wecker löschen?", { okText: "Löschen", danger: true }))) return;
      save(alarms.filter((a) => a.id !== b.dataset.adel));
    }));
  }
  function drawAlarmEdit() {
    const a = editAlarm;
    const d = new Date(Date.now() + 3600000);
    const days = new Set(a.days || []);
    $("#ck-body").innerHTML = `
      <h3 style="margin-top:0">${a.id ? "Wecker bearbeiten" : "Neuer Wecker"}</h3>
      <div class="form-row"><label>Uhrzeit</label><input type="time" id="ck-at" value="${esc(a.time || `${pad(d.getHours())}:00`)}" style="width:140px"></div>
      <div class="form-row"><label>Bezeichnung</label><input type="text" id="ck-al" value="${esc(a.label || "")}" placeholder="z. B. Meeting"></div>
      <div class="form-row"><label>Wiederholen (keine Auswahl = einmalig)</label>
        <div style="display:flex;gap:4px;flex-wrap:wrap" id="ck-days">
          ${DAYS.map((n, i) => `<button class="btn btn-sm${days.has(i) ? " active" : ""}" data-day="${i}" style="min-width:40px">${n}</button>`).join("")}
        </div></div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="btn-primary" id="ck-asave">Speichern</button>
        <button class="btn" id="ck-acancel">Abbrechen</button>
      </div>`;
    body.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => {
      const i = +b.dataset.day;
      if (days.has(i)) days.delete(i); else days.add(i);
      b.classList.toggle("active", days.has(i));
    }));
    $("#ck-acancel").addEventListener("click", () => { editAlarm = null; drawAlarms(); });
    $("#ck-asave").addEventListener("click", async () => {
      const time = $("#ck-at").value;
      if (!/^\d{2}:\d{2}$/.test(time)) { window.notify?.("Bitte eine Uhrzeit wählen.", "warn"); return; }
      const item = { id: a.id || core.uid(), time, label: $("#ck-al").value.trim(), days: [...days].sort(), enabled: true };
      const list = a.id ? core.getAlarms().map((x) => (x.id === a.id ? item : x)) : [...core.getAlarms(), item];
      core.requestDesktopNotifications();
      try { await core.saveAlarms(list); editAlarm = null; drawAlarms(); }
      catch (e) { window.notify?.(e.message, "error"); }
    });
  }

  // ------------------------------------------------------------ Takt & Umschalten
  function draw() {
    if (tab === "clock") drawClock();
    else if (tab === "sw") drawStopwatch();
    else if (tab === "timer") drawTimers();
    else drawAlarms();
    loop();
  }
  function loop() {
    cancelAnimationFrame(raf);
    const step = () => {
      if (!body.isConnected) return;
      if (tab === "clock") tickClock();
      else if (tab === "sw") { const el = $("#ck-sw"); if (el) el.textContent = core.fmtDuration(core.stopwatchElapsed(), true); }
      else if (tab === "timer") tickTimers();
      raf = requestAnimationFrame(step);
    };
    step();
  }
  // Änderungen aus dem Hintergrund (Timer abgelaufen, Wecker gespeichert) übernehmen.
  const off = core.onClockChange(() => {
    if (tab === "timer") drawTimers();
    else if (tab === "alarm" && !editAlarm) drawAlarms();
  });
  if (win?.key) registerCleanup(win.key, () => { cancelAnimationFrame(raf); off(); });
  draw();
}

// clockcore.js (Modul "collab", Teilfunktion "clock") - init-Skript
// -----------------------------------------------------------------
// Läuft einmal pro Seitenaufruf im Hintergrund, damit Wecker und Timer
// auch dann klingeln, wenn das Uhr-Fenster geschlossen ist.
//
//   Wecker    -> auf dem Server gespeichert (clock_routes.py), pro Benutzer
//   Timer     -> im Browser (localStorage), überstehen ein Neuladen der Seite
//   Stoppuhr  -> im Browser (localStorage), läuft beim Neuladen weiter
//
// Klingeln: Hinweisfenster mit Ton (bis „Stopp“), Wecker mit „Schlummern“,
// dazu eine Desktop-Benachrichtigung, wenn der Browser das erlaubt.

import { getToken } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";
import { moduleFeature } from "/js/app/modules.js";

const LS_TIMERS = "rmm-clock-timers";
const LS_SW = "rmm-clock-stopwatch";
const listeners = new Set();
const firedKeys = new Set();          // "alarmId:YYYY-MM-DD HH:MM" - nicht doppelt klingeln
let alarms = [];
let snoozes = [];                     // [{at, label}]
let tickTimer = null;
let started = false;

// ------------------------------------------------------------ Hilfen
const pad = (n) => String(n).padStart(2, "0");
const lsGet = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* voll/gesperrt */ } };
export const uid = () => Math.random().toString(36).slice(2, 10);
export function onClockChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function changed() { for (const fn of listeners) { try { fn(); } catch (e) { console.error(e); } } }

export function fmtDuration(ms, withMs = false) {
  ms = Math.max(0, ms);
  const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000), s = Math.floor((ms % 60000) / 1000);
  const base = `${h ? h + ":" + pad(m) : pad(m)}:${pad(s)}`;
  return withMs ? `${base},${pad(Math.floor((ms % 1000) / 10))}` : base;
}

async function req(method, body) {
  const token = getToken() || "";
  const res = await fetch("/api/collab/clock/alarms", {
    method, headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* egal */ }
    throw new Error(msg);
  }
  return res.json();
}

// ------------------------------------------------------------ Wecker
export function getAlarms() { return alarms; }
export async function loadAlarms() {
  try { alarms = (await req("GET")).alarms || []; } catch { alarms = []; }
  changed();
  return alarms;
}
export async function saveAlarms(list) {
  const res = await req("PUT", { alarms: list });
  alarms = res.alarms || [];
  changed();
  return alarms;
}

// ------------------------------------------------------------ Timer
export function getTimers() { return lsGet(LS_TIMERS, []); }
function setTimers(t) { lsSet(LS_TIMERS, t); changed(); }
export function addTimer(durationMs, label = "") {
  const t = getTimers();
  t.push({ id: uid(), label, duration: durationMs, remaining: durationMs, endsAt: Date.now() + durationMs, running: true });
  setTimers(t);
}
export function timerRemaining(t) { return t.running ? Math.max(0, t.endsAt - Date.now()) : t.remaining; }
export function toggleTimer(id) {
  setTimers(getTimers().map((t) => {
    if (t.id !== id) return t;
    if (t.running) return { ...t, running: false, remaining: Math.max(0, t.endsAt - Date.now()) };
    const rem = t.remaining > 0 ? t.remaining : t.duration;
    return { ...t, running: true, remaining: rem, endsAt: Date.now() + rem };
  }));
}
export function resetTimer(id) {
  setTimers(getTimers().map((t) => (t.id === id ? { ...t, running: false, remaining: t.duration } : t)));
}
export function removeTimer(id) { setTimers(getTimers().filter((t) => t.id !== id)); }

// ------------------------------------------------------------ Stoppuhr
export function getStopwatch() { return lsGet(LS_SW, { running: false, startedAt: 0, before: 0, laps: [] }); }
export function stopwatchElapsed(sw = getStopwatch()) { return sw.before + (sw.running ? Date.now() - sw.startedAt : 0); }
export function stopwatchToggle() {
  const sw = getStopwatch();
  if (sw.running) { sw.before += Date.now() - sw.startedAt; sw.running = false; }
  else { sw.startedAt = Date.now(); sw.running = true; }
  lsSet(LS_SW, sw); changed();
}
export function stopwatchLap() {
  const sw = getStopwatch();
  if (!sw.running) return;
  sw.laps.unshift(stopwatchElapsed(sw));
  lsSet(LS_SW, sw); changed();
}
export function stopwatchReset() { lsSet(LS_SW, { running: false, startedAt: 0, before: 0, laps: [] }); changed(); }

// ------------------------------------------------------------ Klingeln
let audioCtx = null;
let ringLoop = null;
function beep() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const now = audioCtx.currentTime;
    [0, 0.18, 0.36].forEach((off) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, now + off);
      g.gain.exponentialRampToValueAtTime(0.25, now + off + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, now + off + 0.15);
      o.connect(g).connect(audioCtx.destination);
      o.start(now + off); o.stop(now + off + 0.16);
    });
  } catch { /* kein Audio */ }
}

export function ring(title, text, { snooze = false } = {}) {
  clearInterval(ringLoop);
  beep();
  ringLoop = setInterval(beep, 1200);
  try {
    if ("Notification" in window && Notification.permission === "granted") new Notification(title, { body: text || "" });
  } catch { /* egal */ }
  document.querySelector(".clock-ring")?.remove();
  const ov = document.createElement("div");
  ov.className = "clock-ring";
  ov.style.cssText = "position:fixed;inset:0;z-index:9600;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center";
  ov.innerHTML = `
    <div style="background:var(--panel);border:1px solid var(--accent);border-radius:12px;padding:22px 26px;min-width:280px;text-align:center;box-shadow:0 10px 40px rgba(0,0,0,.5)">
      <div style="font-size:40px">⏰</div>
      <div style="font-size:18px;font-weight:600;margin:6px 0">${esc(title)}</div>
      ${text ? `<div style="color:var(--subtext);font-size:13px;margin-bottom:14px">${esc(text)}</div>` : `<div style="height:10px"></div>`}
      <div style="display:flex;gap:8px;justify-content:center">
        ${snooze ? `<button class="btn" data-a="snooze">💤 Schlummern (5 min)</button>` : ""}
        <button class="btn-primary" data-a="stop">Stopp</button>
      </div>
    </div>`;
  const close = () => { clearInterval(ringLoop); ringLoop = null; ov.remove(); };
  ov.querySelector('[data-a="stop"]').addEventListener("click", close);
  ov.querySelector('[data-a="snooze"]')?.addEventListener("click", () => {
    snoozes.push({ at: Date.now() + 5 * 60000, label: title });
    close();
  });
  document.body.appendChild(ov);
  window.notify?.(`⏰ ${title}${text ? " – " + text : ""}`, "info", 10000);
}

export function requestDesktopNotifications() {
  try { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); } catch { /* egal */ }
}

// ------------------------------------------------------------ Takt
function tick() {
  const now = new Date();
  // Timer
  const timers = getTimers();
  let tChanged = false;
  for (const t of timers) {
    if (t.running && t.endsAt <= now.getTime()) {
      t.running = false; t.remaining = 0; tChanged = true;
      ring("Timer abgelaufen", t.label || `${fmtDuration(t.duration)} sind um`);
    }
  }
  if (tChanged) setTimers(timers);
  // Schlummern
  const due = snoozes.filter((s) => s.at <= now.getTime());
  if (due.length) {
    snoozes = snoozes.filter((s) => s.at > now.getTime());
    due.forEach((s) => ring(s.label, "Schlummern vorbei", { snooze: true }));
  }
  // Wecker (minutengenau)
  const hhmm = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  const day = (now.getDay() + 6) % 7;     // 0 = Montag
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${hhmm}`;
  let once = false;
  for (const a of alarms) {
    if (!a.enabled || a.time !== hhmm) continue;
    if (a.days?.length && !a.days.includes(day)) continue;
    const key = `${a.id}:${stamp}`;
    if (firedKeys.has(key)) continue;
    firedKeys.add(key);
    ring(a.label || "Wecker", `${a.time} Uhr`, { snooze: true });
    if (!a.days?.length) { a.enabled = false; once = true; }    // einmaliger Wecker: danach aus
  }
  if (once) saveAlarms(alarms).catch(() => {});
}

export function ensureStarted() {
  if (started) return;
  started = true;
  tickTimer = setInterval(tick, 1000);
  loadAlarms();
  // Nach dem Aufwachen aus dem Ruhezustand/Tab-Wechsel Wecker neu laden.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadAlarms(); });
}

// init-Einstieg (module.json -> "init")
export function init() {
  if (!moduleFeature("collab", "clock")) return;
  ensureStarted();
}

// crashguard.js
// -------------
// Selbstheilung des Frontends:
//   - Erkennt Abstürze/Einfrieren und schreibt einen Crash-Bericht ins
//     Audit-Log (analog zu den Agents und zum Backend).
//   - Bei wiederholten fatalen Fehlern oder erkanntem Einfrieren werden die
//     offenen Apps geschlossen und die Seite neu geladen, damit sich das
//     Problem NICHT sofort wieder reproduziert.
//
// Erkennung:
//   1. Globale Fehler (window 'error' + 'unhandledrejection') werden gezählt.
//      Häufen sie sich in kurzer Zeit -> „Panik" (Neustart).
//   2. Ein Heartbeat schreibt regelmäßig einen Zeitstempel + „sauber beendet"-
//      Flag in den localStorage. Läuft das Intervall stark verspätet, war der
//      Haupt-Thread blockiert (eingefroren) -> Warnung / bei Wiederholung Panik.
//   3. Beim nächsten Start: fehlt das „sauber beendet"-Flag, ist die letzte
//      Sitzung abgestürzt/eingefroren -> es wird nachträglich protokolliert.

import { api } from "./api.js";

let _cfg = { onPanic: null };
let _key = "rmm-cg:anon";
let _hbTimer = null;
let _lastTick = 0;
let _errTimes = [];
let _freezeStrikes = 0;
let _reportedThisSession = 0;
let _lastReportAt = 0;
let _panicking = false;

const HEARTBEAT_MS = 3000;
const FREEZE_GAP_MS = 9000;      // so viel Verspätung = „eingefroren"
const ERROR_WINDOW_MS = 12000;   // Zeitfenster für die Fehlerzählung
const ERROR_PANIC_COUNT = 6;     // so viele Fehler im Fenster = Panik
const FREEZE_PANIC_STRIKES = 2;  // so oft Einfrieren = Panik
const MAX_REPORTS_PER_SESSION = 25;
const REPORT_THROTTLE_MS = 3000;

function _safe(fn) { try { return fn(); } catch { /* niemals crashen */ } }

// Fehler (gedrosselt) ins Audit-Log melden.
function _report(message, action = null) {
  const now = Date.now();
  if (_reportedThisSession >= MAX_REPORTS_PER_SESSION) return;
  if (!action && now - _lastReportAt < REPORT_THROTTLE_MS) return;
  _lastReportAt = now;
  _reportedThisSession++;
  _safe(() => api.logError(String(message).slice(0, 480), "error", "frontend", action));
}

function _markClean() { _safe(() => localStorage.setItem(_key, JSON.stringify({ clean: true, ts: Date.now() }))); }
function _markAlive() { _safe(() => localStorage.setItem(_key, JSON.stringify({ clean: false, ts: Date.now() }))); }

function _heartbeat() {
  const now = Date.now();
  if (_lastTick) {
    const gap = now - _lastTick;
    // Deutlich verspäteter Tick -> Haupt-Thread war blockiert (eingefroren).
    if (gap > FREEZE_GAP_MS) {
      _freezeStrikes++;
      _report(`Frontend war ~${Math.round(gap / 1000)}s blockiert (eingefroren).`);
      if (_freezeStrikes >= FREEZE_PANIC_STRIKES) return _panic("eingefroren");
    }
  }
  _lastTick = now;
  _markAlive();
}

function _onError(ev) {
  const msg = (ev && (ev.message || (ev.reason && (ev.reason.message || ev.reason)))) || "Unbekannter Fehler";
  const now = Date.now();
  _errTimes = _errTimes.filter((t) => now - t < ERROR_WINDOW_MS);
  _errTimes.push(now);
  _report(msg);
  if (_errTimes.length >= ERROR_PANIC_COUNT) _panic("wiederholte Fehler");
}

// „Panik": offene Apps schließen (damit das Problem nicht sofort wiederkehrt)
// und die Seite neu laden. Der Crash wird als eigene Aktion protokolliert.
function _panic(reason) {
  if (_panicking) return;
  _panicking = true;
  _report(`Frontend-Neustart ausgelöst (${reason}).`, "frontend.crash");
  _safe(() => window.notify?.(
    "Es sind mehrere Fehler aufgetreten. Zur Stabilisierung werden die offenen Apps geschlossen und die Oberfläche neu geladen.",
    "warning", 6000));
  // Kurz warten, damit die Meldung/der Report rausgeht, dann aufräumen + reload.
  setTimeout(() => {
    _safe(() => _cfg.onPanic && _cfg.onPanic());   // Apps schließen + Zustand sichern
    _markClean();                                   // sauberer, gewollter Reload
    _safe(() => location.reload());
  }, 800);
}

export function initCrashGuard({ username, onPanic } = {}) {
  _key = "rmm-cg:" + (username || "anon");
  _cfg.onPanic = onPanic || null;

  // 1) Absturz der VORHERIGEN Sitzung nachträglich protokollieren.
  _safe(() => {
    const raw = localStorage.getItem(_key);
    if (raw) {
      const prev = JSON.parse(raw);
      if (prev && prev.clean === false) {
        api.logError(
          "Vorherige Sitzung wurde nicht sauber beendet (Absturz/Einfrieren erkannt).",
          "error", "frontend", "frontend.crash_recovered");
      }
    }
  });

  // 2) Heartbeat starten und Zustand als „aktiv/nicht sauber" markieren.
  _lastTick = Date.now();
  _markAlive();
  clearInterval(_hbTimer);
  _hbTimer = setInterval(_heartbeat, HEARTBEAT_MS);

  // 3) Sauberes Beenden markieren (normaler Reload/Schließen ist KEIN Absturz).
  window.addEventListener("beforeunload", _markClean);
  window.addEventListener("pagehide", _markClean);

  // 4) Globale Fehler abfangen.
  window.addEventListener("error", _onError);
  window.addEventListener("unhandledrejection", _onError);
}

// notify.js
// ---------
// Zeigt schöne, von oben einfahrende Benachrichtigungen/Fehlermeldungen.
// Eigenschaften (wie gewünscht):
//   - abgerundetes Fenster, das von oben hereinfährt
//   - Box-Shadow in einer Farbe je nach Priorität (info/success/warn/error)
//   - eine Leiste am unteren Rand, die den Ausblende-Countdown darstellt
//   - X rechts zum sofortigen Schließen
//   - bei Hover über die Box wird der Countdown angehalten
//
// Verwendung:  notify("Text", "error")  |  notify("Gespeichert", "success")

const LEVELS = {
  info:    { color: "#4da6ff", icon: "ℹ️" },
  success: { color: "#3ecf8e", icon: "✓" },
  warn:    { color: "#f5a524", icon: "⚠️" },
  error:   { color: "#ff4d6d", icon: "✕" },
};

let container = null;
// Aktive Benachrichtigungen mit "tag": eine neue Meldung gleichen Tags ersetzt
// die vorherige (z.B. "Aktualisiere Client…" -> "Client aktualisiert").
const _tagged = new Map();

function ensureContainer() {
  if (container) return container;
  container = document.createElement("div");
  container.id = "notify-container";
  container.style.cssText = `
    position: fixed; top: 0; left: 50%; transform: translateX(-50%);
    z-index: 5000; display: flex; flex-direction: column; gap: 10px;
    align-items: center; padding-top: 12px; pointer-events: none;
  `;
  document.body.appendChild(container);
  return container;
}

export function notify(message, level = "info", durationMs = 5000, opts = {}) {
  const cfg = LEVELS[level] || LEVELS.info;
  const root = ensureContainer();

  // In der Benachrichtigungs-Zentrale aufzeichnen (dynamischer Import, um
  // Zyklen zu vermeiden). Die zurueckgegebene id wird zum "als gelesen
  // markieren" genutzt, sobald der Nutzer mit dem Toast interagiert.
  let recordId = null;
  let markReadFn = null;
  import("./notifycenter.js").then((nc) => {
    markReadFn = nc.markRead;
    recordId = nc.recordNotification(message, level, opts.source || "app");
  }).catch(() => {});
  const markAsRead = () => { if (recordId && markReadFn) { try { markReadFn(recordId); } catch {} } };

  // Gibt es schon eine Meldung mit demselben Tag? -> sofort entfernen (die neue
  // "aktualisiert die Situation", z.B. Erfolg ersetzt die laufende Fortschritts-Box).
  const tag = opts && opts.tag;
  if (tag && _tagged.has(tag)) {
    try { _tagged.get(tag)(); } catch {}
    _tagged.delete(tag);
  }

  const box = document.createElement("div");
  box.style.cssText = `
    pointer-events: auto; position: relative; overflow: hidden;
    min-width: 320px; max-width: 520px;
    background: var(--panel, #131c2b); color: var(--text, #e8eef7);
    border: 1px solid ${cfg.color}55; border-radius: 14px;
    padding: 14px 40px 16px 16px;
    box-shadow: 0 12px 40px ${cfg.color}55, 0 4px 12px rgba(0,0,0,0.4);
    transform: translateY(-120%); opacity: 0;
    transition: transform 0.35s cubic-bezier(0.2,0.8,0.2,1), opacity 0.35s;
    font-size: 14px; line-height: 1.4;
  `;

  box.innerHTML = `
    <div style="display:flex;gap:10px;align-items:flex-start">
      <span style="color:${cfg.color};font-size:16px;flex-shrink:0">${cfg.icon}</span>
      <span style="flex:1">${escapeHtml(message)}</span>
    </div>
    <button class="notify-close" style="position:absolute;top:10px;right:10px;background:none;
      border:none;color:var(--subtext,#8fa3bd);cursor:pointer;font-size:14px;line-height:1">✕</button>
    <div class="notify-bar" style="position:absolute;bottom:0;left:0;height:3px;width:100%;
      background:${cfg.color};transform-origin:left;transform:scaleX(1)"></div>
  `;

  root.appendChild(box);

  // Einfahren (nächster Frame, damit die Transition greift)
  requestAnimationFrame(() => {
    box.style.transform = "translateY(0)";
    box.style.opacity = "1";
  });

  const bar = box.querySelector(".notify-bar");
  // Countdown per requestAnimationFrame steuern (robust: Hover pausiert, danach
  // läuft der Balken exakt weiter statt zu verschwinden). Bewusst KEINE
  // CSS-Transition auf dem Balken - wir setzen scaleX pro Frame selbst.
  let remaining = durationMs;   // verbleibende Zeit in ms
  let lastTs = null;            // Zeitstempel des letzten Frames
  let rafId = null;
  let paused = false;
  let closed = false;

  function tick(ts) {
    if (paused || closed) return;
    if (lastTs == null) lastTs = ts;
    remaining -= ts - lastTs;
    lastTs = ts;
    const frac = Math.max(0, remaining / durationMs);
    bar.style.transform = `scaleX(${frac})`;
    if (remaining <= 0) { close(); return; }
    rafId = requestAnimationFrame(tick);
  }

  function startCountdown() {
    if (closed) return;
    lastTs = null;
    paused = false;
    rafId = requestAnimationFrame(tick);
  }

  function pauseCountdown() {
    if (paused || closed) return;
    paused = true;
    if (rafId) cancelAnimationFrame(rafId);
  }

  function resumeCountdown() {
    if (!paused || closed) return;
    lastTs = null;             // Zeitbasis zurücksetzen, sonst großer "Sprung"
    paused = false;
    rafId = requestAnimationFrame(tick);
  }

  function close() {
    if (closed) return;
    closed = true;
    if (tag && _tagged.get(tag) === close) _tagged.delete(tag);
    if (rafId) cancelAnimationFrame(rafId);
    box.style.transform = "translateY(-120%)";
    box.style.opacity = "0";
    setTimeout(() => box.remove(), 350);
  }

  // Hover pausiert den Countdown - und gilt als "gelesen" (Interaktion).
  box.addEventListener("mouseenter", () => { pauseCountdown(); markAsRead(); });
  box.addEventListener("mouseleave", resumeCountdown);
  // WegXen gilt ebenfalls als gelesen.
  box.querySelector(".notify-close").addEventListener("click", () => { markAsRead(); close(); });
  // Jede sonstige Interaktion (Klick auf die Box) -> gelesen.
  box.addEventListener("mousedown", markAsRead);

  if (durationMs > 0) startCountdown();
  if (tag) _tagged.set(tag, close);
  return close;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

// Wie notify(), protokolliert Fehler/Warnungen aber ZUSÄTZLICH im Audit-Log
// (best effort - schlägt das Logging fehl, wird die Box trotzdem angezeigt).
export function notifyError(message, level = "error", context = null, durationMs = 8000) {
  const close = notify(message, level, durationMs);
  if (level === "error" || level === "warn") {
    // dynamischer Import, um Zyklen zu vermeiden
    import("./api.js").then(({ api }) => {
      api.logError(message, level, context).catch(() => { /* egal */ });
    }).catch(() => {});
  }
  return close;
}

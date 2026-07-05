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

export function notify(message, level = "info", durationMs = 5000) {
  const cfg = LEVELS[level] || LEVELS.info;
  const root = ensureContainer();

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
      background:${cfg.color};transform-origin:left;transition:transform linear"></div>
  `;

  root.appendChild(box);

  // Einfahren (nächster Frame, damit die Transition greift)
  requestAnimationFrame(() => {
    box.style.transform = "translateY(0)";
    box.style.opacity = "1";
  });

  const bar = box.querySelector(".notify-bar");
  let remaining = durationMs;
  let startTime = Date.now();
  let timer = null;
  let paused = false;

  function startCountdown() {
    startTime = Date.now();
    // Balken von voll (scaleX 1) auf 0 schrumpfen über die verbleibende Zeit
    bar.style.transition = `transform ${remaining}ms linear`;
    requestAnimationFrame(() => { bar.style.transform = "scaleX(0)"; });
    timer = setTimeout(close, remaining);
  }

  function pauseCountdown() {
    if (paused) return;
    paused = true;
    clearTimeout(timer);
    remaining -= Date.now() - startTime;
    // Balken an aktueller Position einfrieren
    const computed = getComputedStyle(bar).transform;
    bar.style.transition = "none";
    bar.style.transform = computed;
  }

  function resumeCountdown() {
    if (!paused) return;
    paused = false;
    startCountdown();
  }

  function close() {
    clearTimeout(timer);
    box.style.transform = "translateY(-120%)";
    box.style.opacity = "0";
    setTimeout(() => box.remove(), 350);
  }

  // Hover pausiert den Countdown
  box.addEventListener("mouseenter", pauseCountdown);
  box.addEventListener("mouseleave", resumeCountdown);
  box.querySelector(".notify-close").addEventListener("click", close);

  if (durationMs > 0) startCountdown();
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

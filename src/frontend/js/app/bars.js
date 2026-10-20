// bars.js
// -------
// Kopfleiste (Topbar) und Taskleiste automatisch ausblenden - pro Benutzer
// einstellbar (Profil -> "Leisten"), getrennt fuer PC und Handy:
//
//   PC     Standard: beide sichtbar. Ausgeblendet erscheinen sie, sobald die Maus
//          an den oberen bzw. unteren Bildschirmrand faehrt, und verschwinden
//          wieder, wenn die Maus die Leiste verlaesst (nicht, solange das
//          Benutzermenue / Startmenue offen ist).
//   Handy  Standard: beide ausgeblendet (mehr Platz). Da es kein Hover gibt,
//          sitzt oben links eine kleine Knopfgruppe: ☰ Seitenleiste,
//          ▔ Kopfleiste, ▁ Taskleiste. Antippen blendet die Leiste ein;
//          Tippen ausserhalb (oder Auswahl eines Fensters/einer App) blendet
//          sie wieder aus.
//
// Gespeichert unter localStorage "rmm_bars:<benutzer>" - persist.js spiegelt
// das (Praefix "rmm_bars:") auf den Server, gilt also in jedem Browser.
//
// Klassen an <html> (Rest macht CSS in mobile.css, Abschnitt "AUTO-AUSBLENDEN"):
//   bars-hide-top / bars-hide-task   Leiste ist im Auto-Modus (aus dem Fluss)
//   bars-show-top / bars-show-task   Leiste ist gerade eingeblendet

const DEFAULTS = {
  desktop: { top: false, task: false },
  mobile: { top: true, task: true },
};
const HIDE_DELAY = 550;     // ms nach dem Verlassen der Leiste (PC)

let _user = null;
let _prefs = clone(DEFAULTS);
let _wired = false;
const _timers = { top: null, task: null };

function clone(o) { return JSON.parse(JSON.stringify(o)); }
const root = () => document.documentElement;
const isMobile = () => root().classList.contains("is-mobile");
const isSolo = () => document.body.classList.contains("solo-mode");
const keyOf = (u) => `rmm_bars:${u}`;
const el = {
  get top() { return document.querySelector("#app .topbar"); },
  get task() { return document.querySelector("#app .taskbar"); },
  get userMenu() { return document.getElementById("user-menu"); },
  get startMenu() { return document.getElementById("start-menu"); },
};

// ------------------------------------------------------------------ Einstellungen
export function getBarPrefs() { return clone(_prefs); }

export function setBarPrefs(next) {
  for (const mode of ["desktop", "mobile"]) {
    for (const bar of ["top", "task"]) {
      if (next?.[mode] && typeof next[mode][bar] === "boolean") _prefs[mode][bar] = next[mode][bar];
    }
  }
  if (_user) {
    try { localStorage.setItem(keyOf(_user), JSON.stringify(_prefs)); } catch {}
    import("./persist.js").then((p) => p.syncToServerSoon()).catch(() => {});
  }
  apply();
}

function load(user) {
  _prefs = clone(DEFAULTS);
  try {
    const raw = localStorage.getItem(keyOf(user));
    if (raw) {
      const saved = JSON.parse(raw);
      for (const mode of ["desktop", "mobile"]) {
        for (const bar of ["top", "task"]) {
          if (typeof saved?.[mode]?.[bar] === "boolean") _prefs[mode][bar] = saved[mode][bar];
        }
      }
    }
  } catch {}
}

// Wird nach dem Login (nach hydrateFromServer) aufgerufen.
export function initBars(username) {
  _user = username || "anon";
  load(_user);
  if (!_wired) wire();
  apply();
}

// ------------------------------------------------------------------ Anwenden
function hidden(bar) {
  if (isSolo()) return false;
  return !!_prefs[isMobile() ? "mobile" : "desktop"][bar];
}

function apply() {
  const r = root();
  for (const bar of ["top", "task"]) {
    const on = hidden(bar);
    if (r.classList.contains(`bars-hide-${bar}`) !== on) r.classList.toggle(`bars-hide-${bar}`, on);
    if (!on && r.classList.contains(`bars-show-${bar}`)) r.classList.remove(`bars-show-${bar}`);
  }
  drawFab();
}

function show(bar) {
  clearTimeout(_timers[bar]);
  if (!root().classList.contains(`bars-hide-${bar}`)) return;
  root().classList.add(`bars-show-${bar}`);
  syncFab();
}

function hide(bar) {
  clearTimeout(_timers[bar]);
  if (!root().classList.contains(`bars-show-${bar}`)) return;
  root().classList.remove(`bars-show-${bar}`);
  if (bar === "top") el.userMenu?.classList.add("hidden");
  syncFab();
}

function toggle(bar) {
  root().classList.contains(`bars-show-${bar}`) ? hide(bar) : show(bar);
}

// Darf die Leiste gerade NICHT verschwinden?
function keepOpen(bar) {
  const b = el[bar];
  if (!b) return false;
  if (bar === "top" && el.userMenu && !el.userMenu.classList.contains("hidden")) return true;
  if (bar === "task" && el.startMenu && !el.startMenu.classList.contains("hidden")) return true;
  if (isMobile()) return false;
  if (b.matches(":hover")) return true;
  const a = document.activeElement;
  return !!(a && a !== document.body && b.contains(a) && a.matches("input, select, textarea"));
}

function scheduleHide(bar, delay = HIDE_DELAY) {
  clearTimeout(_timers[bar]);
  _timers[bar] = setTimeout(() => {
    if (!root().classList.contains(`bars-show-${bar}`)) return;
    if (keepOpen(bar)) { scheduleHide(bar, 400); return; }
    hide(bar);
  }, delay);
}

// ------------------------------------------------------------------ PC: Hover
function makeHotZone(bar) {
  const id = `bars-hot-${bar}`;
  if (document.getElementById(id)) return;
  const z = document.createElement("div");
  z.id = id;
  z.className = `bars-hot bars-hot-${bar}`;
  z.setAttribute("aria-hidden", "true");
  // Nach dem Einblenden laeuft immer ein Ausblende-Timer; solange die Maus auf der
  // Leiste ist (:hover), verlaengert er sich selbst (siehe keepOpen) - so klappt es
  // auch, wenn die Maus schnell weggezogen wird und kein mouseleave kommt.
  z.addEventListener("mouseenter", () => { if (!isMobile()) { show(bar); scheduleHide(bar, 1000); } });
  // Langsames "Anstupsen" am Rand mit Stift/Touch-Laptop: Tippen zeigt die Leiste
  z.addEventListener("pointerdown", (e) => { if (!isMobile()) { e.preventDefault(); show(bar); scheduleHide(bar, 2500); } });
  document.getElementById("app")?.appendChild(z);
}

// ------------------------------------------------------------------ Handy: Knopfgruppe
const ICON = {
  menu: "☰",
  top: `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><rect x="2.5" y="3" width="15" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/><rect x="2.5" y="3" width="15" height="4.5" rx="2" fill="currentColor"/></svg>`,
  task: `<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><rect x="2.5" y="3" width="15" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.6"/><rect x="2.5" y="12.5" width="15" height="4.5" rx="2" fill="currentColor"/></svg>`,
};

function drawFab() {
  let fab = document.getElementById("bars-fab");
  const need = isMobile() && !isSolo() && (hidden("top") || hidden("task"));
  if (root().classList.contains("bars-fab-on") !== need) root().classList.toggle("bars-fab-on", need);
  if (!need) { fab?.remove(); return; }
  if (!fab) {
    fab = document.createElement("div");
    fab.id = "bars-fab";
    fab.className = "bars-fab";
    document.getElementById("app")?.appendChild(fab);
    fab.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-bar]");
      if (!b) return;
      e.stopPropagation();
      if (b.dataset.bar === "menu") {
        document.getElementById("mobile-sidebar-btn")?.click();
        return;
      }
      toggle(b.dataset.bar);
    });
  }
  fab.innerHTML = `
    <button type="button" data-bar="menu" title="Geräte (Seitenleiste)" aria-label="Seitenleiste öffnen">${ICON.menu}</button>
    ${hidden("top") ? `<button type="button" data-bar="top" title="Kopfleiste ein-/ausblenden" aria-label="Kopfleiste ein- oder ausblenden">${ICON.top}</button>` : ""}
    ${hidden("task") ? `<button type="button" data-bar="task" title="Taskleiste ein-/ausblenden" aria-label="Taskleiste ein- oder ausblenden">${ICON.task}</button>` : ""}`;
  // Breite fuer CSS (Logo/Fenstertitel ruecken um so viel nach rechts)
  const n = fab.querySelectorAll("button").length;
  root().style.setProperty("--bars-fab-w", `${n * 34 + (n - 1) * 4 + 10}px`);
  syncFab();
}

function syncFab() {
  const fab = document.getElementById("bars-fab");
  if (!fab) return;
  for (const bar of ["top", "task"]) {
    const b = fab.querySelector(`[data-bar="${bar}"]`);
    if (b) {
      const on = root().classList.contains(`bars-show-${bar}`);
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }
}

// ------------------------------------------------------------------ Verkabelung
function wire() {
  _wired = true;
  makeHotZone("top");
  makeHotZone("task");

  for (const bar of ["top", "task"]) {
    const b = el[bar];
    if (!b) continue;
    b.addEventListener("mouseenter", () => { if (!isMobile()) clearTimeout(_timers[bar]); });
    b.addEventListener("mouseleave", () => { if (!isMobile()) scheduleHide(bar); });
  }

  // Handy: Tippen ausserhalb blendet eingeblendete Leisten wieder aus
  document.addEventListener("pointerdown", (e) => {
    if (!isMobile()) return;
    const t = e.target;
    if (t.closest?.("#bars-fab")) return;
    for (const bar of ["top", "task"]) {
      if (!root().classList.contains(`bars-show-${bar}`)) continue;
      if (t.closest?.(bar === "top" ? ".topbar" : ".taskbar")) continue;
      if (bar === "top" && t.closest?.("#user-menu")) continue;
      if (bar === "task" && t.closest?.("#start-menu, .taskbar-tip")) continue;
      scheduleHide(bar, 0);
    }
  }, true);

  // Handy: Fenster/App gewaehlt -> Taskleiste wieder weg; Menuepunkt -> Kopfleiste weg
  document.addEventListener("click", (e) => {
    if (!isMobile()) return;
    const t = e.target;
    if (t.closest?.(".taskbar-window-btn, #taskbar-minimize-all-btn")) scheduleHide("task", 250);
    if (t.closest?.("#start-menu button, #start-menu [data-app]")) scheduleHide("task", 250);
    if (t.closest?.("#user-menu button")) scheduleHide("top", 200);
  });

  // Startmenue/Benutzermenue geschlossen -> Leiste darf (PC: wenn nicht gehovert) wieder gehen
  const mo = new MutationObserver(() => {
    if (!isMobile()) {
      if (root().classList.contains("bars-show-task") && !el.task?.matches(":hover")) scheduleHide("task");
      if (root().classList.contains("bars-show-top") && !el.top?.matches(":hover")) scheduleHide("top");
    }
  });
  if (el.startMenu) mo.observe(el.startMenu, { attributes: true, attributeFilter: ["class"] });
  if (el.userMenu) mo.observe(el.userMenu, { attributes: true, attributeFilter: ["class"] });

  // Wechsel PC <-> Handy (Drehen, Fenstergroesse) und Einzelfenster-Modus
  // Nur reagieren, wenn sich Modus/Einzelfenster wirklich aendern (apply() setzt selbst
  // Klassen an <html> - sonst gaebe es eine Endlosschleife).
  let sig = `${isMobile()}|${isSolo()}`;
  const onModeChange = () => {
    const now = `${isMobile()}|${isSolo()}`;
    if (now === sig) return;
    sig = now;
    hide("top"); hide("task");
    apply();
  };
  new MutationObserver(onModeChange).observe(root(), { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(onModeChange).observe(document.body, { attributes: true, attributeFilter: ["class"] });
}

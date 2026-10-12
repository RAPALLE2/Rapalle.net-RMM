// mobile.js
// ---------
// Steuert den Handy-/Hochkant-Modus. Rein additiv:
// - setzt html.is-mobile per matchMedia (gleiche Bedingung wie mobile.css)
// - ☰-Button in der Topbar öffnet die Sidebar als Off-Canvas-Drawer
// - Backdrop / Auswahl im Baum / Escape / Zurück-Geste schließt den Drawer
// - Fenster sind auf Mobile per CSS immer Vollbild (mobile.css),
//   hier wird nur sichergestellt, dass ein neu fokussiertes Fenster
//   nicht minimiert "unsichtbar" bleibt.
// - Bildschirmtastatur: fokussiertes Eingabefeld wird in Sicht gescrollt.
//
// KEINE Änderungen an windowmanager/sidebar nötig — Desktop bleibt 1:1.

const MQ = window.matchMedia(
  "(max-width: 900px), ((pointer: coarse) and (orientation: portrait))"
);

function isMobile() { return MQ.matches; }

function applyModeClass() {
  document.documentElement.classList.toggle("is-mobile", isMobile());
  if (!isMobile()) closeDrawer(); // beim Wechsel auf Desktop: Drawer-Zustand weg
}

// ------------------------------------------------------------
// Drawer (Sidebar) öffnen/schließen
// ------------------------------------------------------------
const el = {
  get sidebar()  { return document.getElementById("sidebar"); },
  get backdrop() { return document.getElementById("sidebar-backdrop"); },
  get btn()      { return document.getElementById("mobile-sidebar-btn"); },
};

function openDrawer() {
  if (!isMobile()) return;
  el.sidebar?.classList.add("mobile-open");
  el.backdrop?.classList.add("visible");
  // Zurück-Geste des Handys soll erst den Drawer schließen, nicht die Seite verlassen
  try { history.pushState({ rmmDrawer: true }, ""); _pushedState = true; } catch (_) {}
}

let _pushedState = false;
function closeDrawer(fromPopstate = false) {
  const wasOpen = el.sidebar?.classList.contains("mobile-open");
  el.sidebar?.classList.remove("mobile-open");
  el.backdrop?.classList.remove("visible");
  if (wasOpen && _pushedState && !fromPopstate) {
    _pushedState = false;
    try { history.back(); } catch (_) {}
  } else if (fromPopstate) {
    _pushedState = false;
  }
}

function toggleDrawer() {
  el.sidebar?.classList.contains("mobile-open") ? closeDrawer() : openDrawer();
}

// ------------------------------------------------------------
// Init
// ------------------------------------------------------------
function init() {
  applyModeClass();
  MQ.addEventListener?.("change", applyModeClass);
  window.addEventListener("orientationchange", () => setTimeout(applyModeClass, 60));

  el.btn?.addEventListener("click", toggleDrawer);
  el.backdrop?.addEventListener("click", () => closeDrawer());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });
  window.addEventListener("popstate", (e) => {
    if (el.sidebar?.classList.contains("mobile-open")) closeDrawer(true);
  });

  // Auswahl im Baum => Drawer zu. Delegiert, damit es auch für per JS
  // nachgeladene Baum-Einträge gilt. Auf-/Zuklappen (Caret/Expander) und
  // Kopf-Buttons lassen den Drawer offen.
  el.sidebar?.addEventListener("click", (e) => {
    if (!isMobile()) return;
    const t = e.target;
    if (t.closest(".sidebar-header")) return;                 // ⚙️ / « bleiben offen
    if (t.closest(".tree-caret, .caret, .expander, .fav-caret, .sidebar-fav-header")) return;
    // Navigationsziele: Dashboard-Eintrag, Clients, Favoriten, Tenants/Standorte
    if (t.closest("[data-nav], .tree-client, .tree-node, .fav-item, .sidebar-nav-item")) {
      // kleines Delay, damit der Klick-Handler der Sidebar zuerst feuert
      setTimeout(() => closeDrawer(), 120);
    }
  });

  // "+ Client hinzufügen" öffnet ein Fenster => Drawer zu
  document.getElementById("btn-add-client")
    ?.addEventListener("click", () => setTimeout(() => closeDrawer(), 120));

  // ----------------------------------------------------------
  // Bildschirmtastatur: fokussiertes Feld sichtbar halten
  // ----------------------------------------------------------
  if (window.visualViewport) {
    let t = null;
    window.visualViewport.addEventListener("resize", () => {
      if (!isMobile()) return;
      clearTimeout(t);
      t = setTimeout(() => {
        const a = document.activeElement;
        if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA")) {
          a.scrollIntoView({ block: "center", behavior: "smooth" });
        }
      }, 120);
    });
  }

  // ----------------------------------------------------------
  // Options-Leisten in Fenstern (Terminal/VNC/Guacamole): der ⚙️-Toggle
  // oben links klappt ALLE Optionen des Fensters ein/aus (Klasse
  // "opts-open" am Fenster, Rest macht mobile.css). Delegiert, damit es
  // fuer jedes (auch spaeter geoeffnete) Fenster gilt.
  // ----------------------------------------------------------
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".bar-opts-toggle");
    if (!btn) return;
    btn.closest(".rmm-window")?.classList.toggle("opts-open");
  });

  // ----------------------------------------------------------
  // Bildschirmtastatur = maximaler Platz: sobald ein Eingabefeld in der
  // App fokussiert ist, Topbar/Taskbar/Sidebar ausblenden (html.kbd-open,
  // Rest macht mobile.css). Beim Verlassen wieder einblenden.
  // ----------------------------------------------------------
  const isEditable = (el) => el && (
    el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable ||
    el.closest?.(".xterm")   // xterm.js-Terminals fangen Tastatur ueber ein Hidden-Textarea
  );
  document.addEventListener("focusin", (e) => {
    if (!isMobile()) return;
    if (!e.target.closest?.("#app")) return;        // Login etc. unangetastet
    if (!isEditable(e.target)) return;
    closeDrawer();
    document.documentElement.classList.add("kbd-open");
  });
  document.addEventListener("focusout", () => {
    if (!isMobile()) return;
    // kleines Delay: Fokuswechsel zwischen zwei Feldern nicht flackern lassen
    setTimeout(() => {
      if (!isEditable(document.activeElement)) {
        document.documentElement.classList.remove("kbd-open");
      }
    }, 150);
  });
  // Sicherheitsnetz + Viewport-Fix: --vvh = Hoehe des SICHTBAREN Bereichs
  // (ohne Tastatur). mobile.css setzt die App im kbd-open-Modus exakt auf
  // diese Hoehe -> nichts wird mehr nach oben aus dem Bild geschoben, der
  // Prompt bleibt sichtbar. Zusaetzlich wird die Scroll-Verschiebung, die
  // der Browser beim Tastatur-Oeffnen erzeugt, sofort zurueckgesetzt.
  if (window.visualViewport) {
    const vv = window.visualViewport;
    let lastH = vv.height;
    const applyVvh = () => {
      document.documentElement.style.setProperty("--vvh", `${Math.round(vv.height)}px`);
      if (document.documentElement.classList.contains("kbd-open")) {
        // Browser-Verschiebung (offsetTop/pageYOffset) neutralisieren
        if (window.scrollY || vv.offsetTop) window.scrollTo(0, 0);
      }
    };
    vv.addEventListener("resize", () => {
      applyVvh();
      const h = vv.height;
      if (h > lastH + 80 && !isEditable(document.activeElement)) {
        document.documentElement.classList.remove("kbd-open");
      }
      lastH = h;
    });
    vv.addEventListener("scroll", applyVvh);
    applyVvh();
  }

  // ----------------------------------------------------------
  // Doppeltipp-Zoom auf Buttons/Titelleisten unterbinden
  // (Pinch-Zoom bleibt erlaubt — Barrierefreiheit)
  // ----------------------------------------------------------
  let lastTouch = 0;
  document.addEventListener("touchend", (e) => {
    if (!isMobile()) return;
    if (!e.target.closest("button, .rmm-window-titlebar, .taskbar")) return;
    const now = Date.now();
    if (now - lastTouch < 320) e.preventDefault();
    lastTouch = now;
  }, { passive: false });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}

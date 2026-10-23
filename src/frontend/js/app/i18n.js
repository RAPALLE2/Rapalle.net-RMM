// i18n.js
// -------
// Einfaches Übersetzungssystem. Der Rest des Codes ruft t("schlüssel") auf und
// bekommt den Text in der aktuell eingestellten Sprache zurück.
//
// Sprache wechseln: setLanguage("en") oder setLanguage("de").
// Die aktuelle Sprache wird auch im localStorage gemerkt, damit sie einen
// Seiten-Neuladen übersteht.

// --- Dynamische Uebersetzungen ------------------------------------------------
// Frueher standen alle Texte hier fest im Code. Jetzt kommen sie aus "lang"-Ordnern
// (Kern: backend/data/lang/<locale>.json, je Modul: modules/*/<name>/lang/<locale>.json)
// und werden vom Backend zusammengefuehrt geliefert. Dateiname = Locale, z.B. de_DE.
// Eine neue Datei (z.B. zb_ZB.json) taucht automatisch in der Sprachauswahl auf.
//
// Aktive Sprache als KURZCODE (de, en, zb) - abgeleitet aus der Locale (de_DE -> de).
// "translations" wird zur Laufzeit gefuellt (loadLocale) und als Puffer im localStorage
// gehalten, damit die Oberflaeche sofort uebersetzt ist, bevor der Fetch zurueckkommt.

const translations = {};        // kurzcode -> { key: text }
let _localeOfCode = {};         // kurzcode -> volle Locale (de -> de_DE)
let _available = [];            // [{code: "de_DE", label: "Deutsch"}, ...]

const shortOf = (locale) => (locale || "").split(/[_-]/)[0].toLowerCase();

// Gepufferte Uebersetzungen aus dem localStorage laden (Vorlauf vor dem Fetch).
try {
  const cached = JSON.parse(localStorage.getItem("rmm_lang_cache") || "{}");
  if (cached && typeof cached === "object") {
    for (const [code, obj] of Object.entries(cached)) {
      if (obj && typeof obj === "object") translations[code] = obj;
    }
  }
} catch { /* egal */ }

function _persistCache() {
  try { localStorage.setItem("rmm_lang_cache", JSON.stringify(translations)); } catch { /* egal */ }
}

// Liste der verfuegbaren Sprachen holen (fuer das Dropdown in den Einstellungen).
export async function loadAvailableLanguages() {
  try {
    const r = await fetch("/api/auth/languages");
    if (!r.ok) throw new Error(String(r.status));
    const data = await r.json();
    _available = Array.isArray(data.languages) ? data.languages : [];
    for (const l of _available) _localeOfCode[shortOf(l.code)] = l.code;
  } catch { /* offline o.ae. -> nur was schon bekannt ist */ }
  return _available;
}

export function availableLanguages() { return _available.slice(); }

// Die Strings einer Locale (oder eines Kurzcodes) nachladen und einspielen.
export async function loadLocale(codeOrLocale) {
  const locale = _localeOfCode[shortOf(codeOrLocale)] || codeOrLocale;
  const short = shortOf(locale);
  try {
    const r = await fetch("/api/auth/lang/" + encodeURIComponent(locale));
    if (!r.ok) throw new Error(String(r.status));
    const data = await r.json();
    translations[short] = data.strings || {};
    _localeOfCode[short] = locale;
    _persistCache();
  } catch (e) {
    // Fehlt die Sprache (offline), bleibt der Puffer bzw. der Fallback aktiv.
    if (!translations[short]) translations[short] = translations[short] || {};
  }
  return translations[short];
}

// Beim Start: verfuegbare Sprachen + aktive Sprache (und den Fallback "de")
// sicher laden, DANN die Oberflaeche neu zeichnen (vom Aufrufer).
export async function initLanguages(active) {
  await loadAvailableLanguages();
  const want = active || currentLang;
  await loadLocale("de");                       // Fallback-Sprache immer verfuegbar
  if (shortOf(want) !== "de") await loadLocale(want);
  setLanguage(want);
  return currentLang;
}

let currentLang = shortOf(localStorage.getItem("rmm_lang") || "de");

export function setLanguage(lang) {
  // Akzeptiert Kurzcode ("de") ODER volle Locale ("de_DE").
  currentLang = shortOf(lang) || "de";
  localStorage.setItem("rmm_lang", currentLang);
  if (_localeOfCode[currentLang]) localStorage.setItem("rmm_locale", _localeOfCode[currentLang]);
}

export function getLanguage() {
  return currentLang;
}

export function getLocale() {
  return _localeOfCode[currentLang] || localStorage.getItem("rmm_locale") || currentLang;
}

// Übersetzt einen Schlüssel. Fällt auf Deutsch zurück, dann auf den Schlüssel
// selbst, falls eine Übersetzung fehlt (so sieht man sofort, was noch fehlt).
export function t(key, vars) {
  let text = translations[currentLang]?.[key] ?? translations.de?.[key] ?? key;
  // Platzhalter der Form {name} füllen. Ohne das müsste jede Meldung mit
  // Zahlen oder Namen per String-Verkettung gebaut werden - und genau dabei
  // bleibt erfahrungsgemäß die Hälfte unübersetzt.
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      text = text.split(`{${k}}`).join(String(v));
    }
  }
  return text;
}

// Kleines OS-Symbol (Emoji) je nach Plattform - für die Status-/Client-Anzeige
// Kleines OS-Symbol je nach Plattform/Distribution. Bekommt platform (z.B.
// "Linux", "Windows") und optional release (z.B. "Ubuntu 22.04", "Windows 11").
// Da es keine offiziellen Emojis für jede Distribution gibt, nutzen wir
// charakteristische Symbole, die die Systeme gut unterscheidbar machen.
export function osIcon(platform, release = "") {
  const p = (platform || "").toLowerCase();
  const r = (release || "").toLowerCase();
  const both = p + " " + r;

  // --- Linux-Distributionen anhand des Release-Strings erkennen ---
  if (both.includes("debian")) return "🌀";        // Debian (Spirale ~ Logo)
  if (both.includes("ubuntu")) return "🟠";        // Ubuntu (orange Kreis)
  if (both.includes("proxmox") || both.includes("pve")) return "🧱"; // Proxmox VE
  if (both.includes("raspbian") || both.includes("raspberry")) return "🍓"; // Raspberry Pi OS
  if (both.includes("fedora")) return "🎩";        // Fedora (Hut)
  if (both.includes("arch")) return "🏹";          // Arch (Bogen)
  if (both.includes("centos") || both.includes("red hat") || both.includes("rhel")) return "🎯"; // RHEL/CentOS
  if (both.includes("suse")) return "🦎";          // (open)SUSE (Chamäleon)
  if (both.includes("alpine")) return "🏔️";        // Alpine

  // --- Windows-Versionen ---
  if (p.includes("windows")) {
    if (r.includes("11")) return "🪟";             // Windows 11
    if (r.includes("10")) return "🪟";             // Windows 10
    if (r.includes("server")) return "🖥️";         // Windows Server
    return "🪟";
  }

  // --- macOS ---
  if (p.includes("darwin") || p.includes("mac")) return "🍎";

  // --- Generisches Linux / Fallback ---
  if (p.includes("linux")) return "🐧";
  return "💻";
}

// Menschenlesbarer OS-Name (z.B. "Ubuntu 22.04" statt "Linux") für die Anzeige.
export function osLabel(platform, release = "") {
  const r = (release || "").trim();
  const p = (platform || "").trim();
  // Wenn der Release-String schon den Distributionsnamen enthält, diesen nehmen
  if (r && /ubuntu|debian|proxmox|raspbian|fedora|arch|centos|suse|alpine|windows/i.test(r)) {
    return r;
  }
  return [p, r].filter(Boolean).join(" ") || "?";
}

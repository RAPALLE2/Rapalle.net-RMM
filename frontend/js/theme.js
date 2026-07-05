// theme.js
// --------
// Steuert zwei Aspekte des Aussehens:
//   1. Hell/Dunkel-Modus (Klasse "theme-light" am <body>)
//   2. Akzent-Farbpalette (überschreibt --accent und --accent-2 per inline-Style
//      am <html>-Element, sodass es die Werte aus style.css übersteuert)

// Verfügbare Farbpaletten. Jede hat eine Haupt- (accent) und eine
// Sekundärfarbe (accent2) für Verläufe. "name" wird im Profil angezeigt.
export const ACCENT_PALETTES = {
  teal:    { name: "Türkis",   accent: "#2dd4bf", accent2: "#38bdf8" },
  violet:  { name: "Violett",  accent: "#a78bfa", accent2: "#f472b6" },
  blue:    { name: "Blau",     accent: "#3b82f6", accent2: "#06b6d4" },
  emerald: { name: "Smaragd",  accent: "#34d399", accent2: "#a3e635" },
  amber:   { name: "Bernstein",accent: "#f59e0b", accent2: "#f97316" },
  rose:    { name: "Rosé",     accent: "#fb7185", accent2: "#f472b6" },
  crimson: { name: "Purpur",   accent: "#ef4444", accent2: "#f97316" },
  slate:   { name: "Schiefer", accent: "#94a3b8", accent2: "#64748b" },
};

export function applyTheme(theme) {
  if (theme === "light") {
    document.body.classList.add("theme-light");
  } else {
    document.body.classList.remove("theme-light");
  }
}

// Setzt die Akzent-Farbpalette. Der Schlüssel muss in ACCENT_PALETTES stehen;
// unbekannte Werte fallen auf "teal" zurück.
export function applyAccent(accentKey) {
  const palette = ACCENT_PALETTES[accentKey] || ACCENT_PALETTES.teal;
  const root = document.documentElement;
  root.style.setProperty("--accent", palette.accent);
  root.style.setProperty("--accent-2", palette.accent2);
}

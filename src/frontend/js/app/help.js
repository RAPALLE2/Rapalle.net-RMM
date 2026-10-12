// help.js
// -------
// Ein einziges Fragezeichen-Symbol für die ganze Oberfläche.
//
// Statt lange Erklärtexte unter jede Einstellung zu schreiben (das macht die
// Seiten unruhig und lang), steht neben dem Feature ein kleines "?". Fährt man
// mit der Maus darüber - oder tippt es auf dem Handy an -, erscheint die
// Erklärung als Sprechblase.
//
// Benutzung im HTML-Template:
//     `<label>Backend-Port ${helpDot("Der Port, auf dem …")}</label>`
//
// Der Tooltip selbst wird EINMAL global verkabelt (initHelp() in app.js) und
// funktioniert danach für jedes "?" - auch für welche, die später dazukommen.

import { esc } from "./utils.js";

/**
 * Liefert das HTML für ein Hilfe-Fragezeichen.
 * @param {string} text  Erklärung, die beim Überfahren erscheint.
 */
export function helpDot(text) {
  if (!text) return "";
  return `<span class="help-dot" tabindex="0" role="button"
    aria-label="Hilfe" data-help="${esc(String(text))}">?</span>`;
}

let tipEl = null;

function tip() {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "help-tip";
    document.body.appendChild(tipEl);
  }
  return tipEl;
}

function show(dot) {
  const text = dot.getAttribute("data-help");
  if (!text) return;
  const el = tip();
  el.textContent = text;
  el.style.display = "block";

  // Erst einblenden, dann messen - vorher wäre die Breite 0.
  const r = dot.getBoundingClientRect();
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  let left = r.left + r.width / 2 - w / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  // Bevorzugt darüber; ist oben kein Platz, darunter.
  let top = r.top - h - 9;
  if (top < 8) top = r.bottom + 9;
  el.style.left = left + "px";
  el.style.top = top + "px";
}

function hide() {
  if (tipEl) tipEl.style.display = "none";
}

/** Einmalig beim Start aufrufen. Verkabelt alle "?" der Oberfläche. */
export function initHelp() {
  if (document.body.dataset.helpReady === "1") return;
  document.body.dataset.helpReady = "1";

  // Delegiert: gilt auch für Elemente, die erst später erzeugt werden.
  document.addEventListener("mouseover", (e) => {
    const dot = e.target.closest?.(".help-dot");
    if (dot) show(dot);
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest?.(".help-dot")) hide();
  });
  // Touch/Tastatur: antippen bzw. fokussieren zeigt den Text ebenfalls.
  document.addEventListener("click", (e) => {
    const dot = e.target.closest?.(".help-dot");
    if (dot) {
      e.preventDefault();
      e.stopPropagation();
      if (tipEl && tipEl.style.display === "block" && tipEl.textContent === dot.getAttribute("data-help")) hide();
      else show(dot);
    } else {
      hide();
    }
  });
  document.addEventListener("focusin", (e) => {
    const dot = e.target.closest?.(".help-dot");
    if (dot) show(dot);
  });
  document.addEventListener("focusout", hide);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
}

// ------------------------------------------------------------------
// Erklärtexte einsammeln
// ------------------------------------------------------------------
// Statt jede einzelne Seite umzubauen, wird hier NACH dem Rendern aufgeräumt:
// Ein Erklär-Absatz direkt unter einer Überschrift oder einem Eingabefeld
// wandert in ein "?" neben der Überschrift bzw. der Beschriftung. Die Seite
// wird dadurch deutlich kürzer, die Erklärung bleibt aber erreichbar.
//
// Angefasst werden nur reine Textabsätze: alles mit Knopf, Link, Eingabefeld
// oder einer id (auf die JS zugreifen könnte) bleibt unverändert stehen.

function isPlainHint(el) {
  if (!el || el.tagName !== "P") return false;
  if (el.id) return false;
  // Nur ERKLAERTEXTE anfassen, keine Inhalte. Im ganzen Projekt sind Hinweise
  // an der gedaempften Schriftfarbe erkennbar (color:var(--subtext)) bzw. an
  // der Klasse "hint" - Fliesstext von Nutzern (Tickets, Chat, Notizen) hat
  // das nicht und bleibt deshalb unangetastet.
  const style = el.getAttribute("style") || "";
  if (!style.includes("--subtext") && !el.classList.contains("hint")) return false;
  if (el.querySelector("button, a, input, select, textarea, [id]")) return false;
  const text = (el.textContent || "").trim();
  return text.length >= 15;
}

function attachHelp(target, el) {
  const text = (el.textContent || "").replace(/\s+/g, " ").trim();
  if (!text) return false;
  // Mehrere Absätze am selben Ziel werden zusammengefasst.
  const existing = target.querySelector(":scope > .help-dot");
  if (existing) {
    existing.setAttribute("data-help",
      existing.getAttribute("data-help") + "\n\n" + text);
  } else {
    target.insertAdjacentHTML("beforeend", helpDot(text));
  }
  el.remove();
  return true;
}

/**
 * Wandelt Erklär-Absätze in "?"-Symbole um.
 * @param {HTMLElement} root  Bereich, der aufgeräumt werden soll.
 */
export function condenseHints(root) {
  if (!root) return;

  // 1) Absätze direkt nach einer Überschrift -> "?" an der Überschrift.
  root.querySelectorAll("h3, h4").forEach((h) => {
    let next = h.nextElementSibling;
    while (isPlainHint(next)) {
      const after = next.nextElementSibling;
      attachHelp(h, next);
      next = after;
    }
  });

  // 2) Absätze direkt nach einer Eingabezeile -> "?" an deren Beschriftung.
  root.querySelectorAll(".form-row").forEach((row) => {
    const label = row.querySelector("label");
    if (!label) return;
    let next = row.nextElementSibling;
    while (isPlainHint(next)) {
      const after = next.nextElementSibling;
      attachHelp(label, next);
      next = after;
    }
    // Auch Erklärungen INNERHALB der Zeile einsammeln.
    row.querySelectorAll(":scope > p").forEach((p) => {
      if (isPlainHint(p)) attachHelp(label, p);
    });
  });
}

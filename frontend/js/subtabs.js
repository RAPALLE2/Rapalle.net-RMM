// subtabs.js
// ----------
// Zerlegt eine lange Einstellungsseite in Unterpunkte.
//
// Das Problem: Der Reiter „Allgemein" war eine einzige, sehr lange Liste -
// Serveradressen, Update, Datenbank, Aufnahmen, Fernzugriff … alles
// untereinander. Zum Finden musste man scrollen und raten.
//
// Die Lösung arbeitet NACH dem Rendern auf dem fertigen DOM: Jede Überschrift
// (h3) beginnt einen Unterpunkt, der bis zur nächsten Überschrift reicht. Aus
// den Überschriften entsteht oben eine Leiste; sichtbar ist immer genau ein
// Unterpunkt.
//
// Der Vorteil dieses Wegs: Die einzelnen Seiten müssen nicht umgeschrieben
// werden, alle bereits verkabelten Knöpfe funktionieren weiter (das Verschieben
// von Elementen im DOM löst keine Ereignis-Handler).

import { esc } from "./utils.js";

/**
 * @param {HTMLElement} root      Bereich mit den Überschriften.
 * @param {object}      opts
 * @param {number}      opts.min  Ab so vielen Überschriften wird geteilt.
 * @param {string}      opts.key  Merkt sich den zuletzt gewählten Unterpunkt.
 */
export function buildSubTabs(root, opts = {}) {
  const min = opts.min ?? 3;
  const key = opts.key || "";
  if (!root || root.dataset.subtabbed === "1") return;

  const heads = [...root.querySelectorAll("h3")].filter(
    (h) => (h.textContent || "").trim()
  );
  if (heads.length < min) return;

  // --- 1) Jede Überschrift samt folgender Geschwister einpacken ------------
  // Wichtig: nur GESCHWISTER bis zur nächsten Überschrift. Dadurch bleibt der
  // Abschnitt in seinem ursprünglichen Elternelement - Bereiche, die für
  // Nicht-Admins entfernt werden, verschwinden also weiterhin komplett.
  const sections = [];
  heads.forEach((h, i) => {
    const box = document.createElement("div");
    box.className = "sub-sec";
    box.dataset.subIndex = String(i);
    h.parentNode.insertBefore(box, h);

    // Erst die Ueberschrift selbst (sie steht direkt hinter dem Kasten),
    // danach alles bis zur naechsten Ueberschrift. Ohne den ersten Schritt
    // wuerde die Schleife sofort an der eigenen Ueberschrift abbrechen.
    const isBoundary = (n) =>
      n.nodeType === 1 && (n.tagName === "H3" || !!n.querySelector?.("h3"));
    const nodes = [];
    let n = box.nextSibling;
    if (n) { nodes.push(n); n = n.nextSibling; }   // die Ueberschrift
    while (n) {
      if (isBoundary(n)) break;
      nodes.push(n);
      n = n.nextSibling;
    }
    nodes.forEach((x) => box.appendChild(x));
    // Der Abstand nach oben kommt jetzt vom Umschalten, nicht mehr vom h3.
    h.style.marginTop = "0";
    // Beschriftung ohne das angehaengte "?"-Symbol (help.js) ermitteln.
    const title = [...h.childNodes]
      .filter((n) => n.nodeType === 3 || (n.nodeType === 1 && !n.classList?.contains("help-dot")))
      .map((n) => n.textContent)
      .join("")
      .replace(/\s+/g, " ")
      .trim();
    sections.push({ box, title: title || `Abschnitt ${i + 1}` });
  });

  if (!sections.length) return;
  root.dataset.subtabbed = "1";

  // --- 2) Leiste bauen ----------------------------------------------------
  const nav = document.createElement("div");
  nav.className = "sub-nav";
  nav.style.padding = "0 0 12px";
  nav.innerHTML = sections
    .map((s, i) => `<button type="button" data-sub="${i}">${esc(s.title)}</button>`)
    .join("");
  root.insertBefore(nav, root.firstChild);

  // --- 3) Umschalten ------------------------------------------------------
  let current = 0;
  if (key) {
    const saved = Number(sessionStorage.getItem("rmm-subtab:" + key));
    if (Number.isInteger(saved) && saved >= 0 && saved < sections.length) current = saved;
  }

  function select(i) {
    current = i;
    sections.forEach((s, idx) => { s.box.style.display = idx === i ? "" : "none"; });
    nav.querySelectorAll("[data-sub]").forEach((b) =>
      b.classList.toggle("active", Number(b.dataset.sub) === i)
    );
    if (key) {
      try { sessionStorage.setItem("rmm-subtab:" + key, String(i)); } catch {}
    }
  }

  nav.querySelectorAll("[data-sub]").forEach((b) =>
    b.addEventListener("click", () => select(Number(b.dataset.sub)))
  );

  // --- 4) Immer sichtbare Elemente nach unten holen ------------------------
  // Der Speichern-Knopf gehoert zu ALLEN Unterpunkten: er sichert die Felder
  // der ganzen Seite. Laege er in einem Abschnitt, waere er beim Bearbeiten
  // eines anderen Unterpunkts verschwunden.
  const pinned = opts.pinned || [];
  if (pinned.length) {
    const foot = document.createElement("div");
    foot.className = "sub-foot";
    foot.style.cssText = "margin-top:14px;padding-top:12px;border-top:1px solid var(--border)";
    let any = false;
    pinned.forEach((sel) => {
      root.querySelectorAll(sel).forEach((el) => { foot.appendChild(el); any = true; });
    });
    if (any) root.appendChild(foot);
  }

  select(current);
}

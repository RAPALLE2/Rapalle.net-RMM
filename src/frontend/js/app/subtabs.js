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

  // --- 1b) Leere oder versteckte Abschnitte verwerfen -----------------------
  // Ein Unterpunkt ohne Inhalt (nur die Ueberschrift) oder in einem per
  // display:none ausgeblendeten Bereich ergibt einen Knopf, hinter dem nichts
  // ist - z.B. wenn ein Bereich in ein Modul umgezogen ist. Solche Abschnitte
  // fliegen raus, statt als leerer Reiter stehen zu bleiben.
  const isHiddenInRoot = (el) => {
    for (let n = el; n && n !== root; n = n.parentElement) {
      if (n.style && n.style.display === "none") return true;
    }
    return false;
  };
  const hasContent = (box) => [...box.childNodes].some((n, idx) => {
    if (idx === 0) return false;                                  // die Ueberschrift
    if (n.nodeType === 3) return !!n.textContent.trim();
    if (n.nodeType !== 1) return false;
    if (n.style && n.style.display === "none") return false;
    return true;
  });
  for (let i = sections.length - 1; i >= 0; i--) {
    const { box } = sections[i];
    if (isHiddenInRoot(box) || !hasContent(box)) {
      box.remove();
      sections.splice(i, 1);
    }
  }
  sections.forEach((s, i) => { s.box.dataset.subIndex = String(i); });

  if (!sections.length) return;
  root.dataset.subtabbed = "1";

  // --- 2) Leiste bauen ----------------------------------------------------
  const nav = document.createElement("div");
  nav.className = "sub-nav";
  nav.style.padding = "0 0 12px";
  // Liegen die Abschnitte in einem eigenen, gepolsterten Kasten (z.B.
  // .settings-section), Leiste und Fussbereich aber direkt in root, klebten
  // Unterpunkt-Knoepfe und "Speichern" am Fensterrand. Gleiche Innenabstaende
  // wie der Kasten uebernehmen.
  // Abstand = Summe der Innenabstaende aller Kaesten zwischen root und Abschnitt.
  let padL = 0, padR = 0;
  for (let el = sections[0].box.parentElement; el && el !== root && root.contains(el); el = el.parentElement) {
    const cs = getComputedStyle(el);
    padL += (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.marginLeft) || 0);
    padR += (parseFloat(cs.paddingRight) || 0) + (parseFloat(cs.marginRight) || 0);
  }
  padL = padL > 0 ? padL + "px" : ""; padR = padR > 0 ? padR + "px" : "";
  if (padL || padR) nav.style.padding = `12px ${padR || "0"} 4px ${padL || "0"}`;
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
    foot.style.cssText = "margin-top:14px;padding-top:12px;border-top:1px solid var(--border)"
      + (padL || padR ? `;padding-left:${padL || 0};padding-right:${padR || 0};padding-bottom:16px` : "");
    let any = false;
    pinned.forEach((sel) => {
      root.querySelectorAll(sel).forEach((el) => { foot.appendChild(el); any = true; });
    });
    if (any) root.appendChild(foot);
  }

  select(current);
}

/**
 * Entfernt den Unterpunkt, in dem `node` liegt - samt seines Knopfes in der
 * Leiste. Wird gebraucht, wenn sich erst nach dem Zeichnen herausstellt, dass
 * ein Bereich gar nicht angezeigt werden soll (z.B. Container-Dienste, wenn
 * das RMM nicht im Container laeuft). Ohne das bliebe ein Knopf stehen, hinter
 * dem nichts mehr ist.
 */
export function removeSubSection(node) {
  const box = node?.closest?.(".sub-sec");
  if (!box) { node?.remove?.(); return; }
  const root = box.closest("[data-subtabbed='1']");
  const idx = box.dataset.subIndex;
  box.remove();
  if (!root) return;
  const nav = root.querySelector(".sub-nav");
  const btn = nav?.querySelector(`[data-sub="${idx}"]`);
  const wasActive = btn?.classList.contains("active");
  btn?.remove();
  if (wasActive) nav?.querySelector("[data-sub]")?.click();
}

// touchgestures.js
// ----------------
// Gesten für Fernzugriff und Terminal - an einer Stelle, weil sie überall
// gleich funktionieren sollen: Remote-Screen, VNC, RDP, SSH, Telnet und das
// eingebaute Terminal.
//
//   attachLongPress(el, cb)   Finger halten  -> Rechtsklick
//   attachPinchZoom(box, el)  Zwei Finger    -> zoomen, ein Finger -> schieben
//
// Warum eigene Gesten statt der Browser-Voreinstellungen?
// Auf dem Handy löst ein langer Druck normalerweise das Auswahl-/Teilen-Menü
// des Browsers aus, und Zoomen wirkt auf die ganze Seite statt auf das Bild
// der Gegenstelle. Beides ist beim Fernzugriff im Weg.

/**
 * Langes Drücken als Rechtsklick.
 *
 * @param {HTMLElement} el
 * @param {(x:number, y:number, ev:PointerEvent) => void} onLongPress
 *        Bekommt die Position - so kann der Aufrufer an genau dieser Stelle
 *        ein Kontextmenü öffnen oder einen Rechtsklick an die Gegenstelle
 *        schicken.
 * @param {object} opts  delay (ms, Standard 500), moveTolerance (px)
 */
export function attachLongPress(el, onLongPress, opts = {}) {
  if (!el) return () => {};
  const delay = opts.delay ?? 500;
  const tolerance = opts.moveTolerance ?? 12;

  let timer = null;
  let startX = 0, startY = 0;
  let fired = false;

  const clear = () => {
    if (timer) { clearTimeout(timer); timer = null; }
  };

  const onDown = (e) => {
    // Nur Finger und Stift - mit der Maus gibt es ja eine echte rechte Taste.
    if (e.pointerType === "mouse") return;
    if (e.isPrimary === false) { clear(); return; }   // zweiter Finger -> Zoom
    fired = false;
    startX = e.clientX; startY = e.clientY;
    clear();
    timer = setTimeout(() => {
      fired = true;
      // Kurzes Rütteln als Rückmeldung, dass der Rechtsklick ausgelöst hat.
      try { navigator.vibrate?.(18); } catch {}
      onLongPress(e.clientX, e.clientY, e);
    }, delay);
  };

  const onMove = (e) => {
    if (!timer) return;
    if (Math.abs(e.clientX - startX) + Math.abs(e.clientY - startY) > tolerance) clear();
  };

  const onUp = (e) => {
    clear();
    // Hat der lange Druck ausgelöst, darf daraus KEIN normaler Klick werden -
    // sonst käme auf der Gegenstelle zusätzlich ein Linksklick an.
    if (fired) {
      e.preventDefault();
      e.stopPropagation();
      fired = false;
    }
  };

  el.addEventListener("pointerdown", onDown, { passive: true });
  el.addEventListener("pointermove", onMove, { passive: true });
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointercancel", clear, { passive: true });
  el.addEventListener("pointerleave", clear, { passive: true });
  // Das Auswahl-/Lupenmenü des Browsers unterdrücken.
  el.addEventListener("contextmenu", (e) => {
    if (e.pointerType !== "mouse") e.preventDefault();
  });

  return () => {
    clear();
    el.removeEventListener("pointerdown", onDown);
    el.removeEventListener("pointermove", onMove);
    el.removeEventListener("pointerup", onUp);
    el.removeEventListener("pointercancel", clear);
    el.removeEventListener("pointerleave", clear);
  };
}

/**
 * Zwei Finger zum Zoomen, ein Finger zum Verschieben.
 *
 * Wichtig: Es wird nur die DARSTELLUNG verändert (CSS-transform). Die
 * Gegenstelle bemerkt davon nichts - die Auflösung bleibt gleich. Deshalb
 * muss der Aufrufer beim Umrechnen von Klickpositionen den Faktor
 * berücksichtigen; dafür gibt es `state()`.
 *
 * @param {HTMLElement} box  äußerer Bereich (bekommt overflow:hidden)
 * @param {HTMLElement} el   Bild/Canvas der Gegenstelle
 */
export function attachPinchZoom(box, el, opts = {}) {
  if (!box || !el) return { destroy() {}, state: () => ({ scale: 1, x: 0, y: 0 }) };
  const min = opts.min ?? 1;
  const max = opts.max ?? 6;

  let scale = 1, tx = 0, ty = 0;
  const points = new Map();          // aktive Finger
  let startDist = 0, startScale = 1;
  let startMid = { x: 0, y: 0 };
  let lastMid = null;      // letzter Fingermittelpunkt (zum Mitschieben)

  box.style.overflow = "hidden";
  box.style.touchAction = "none";    // eigene Gesten statt Browser-Scrollen
  el.style.transformOrigin = "0 0";

  const apply = () => {
    // Nicht über den Rand hinausziehen: Ist das Bild kleiner als der Bereich,
    // bleibt es mittig; sonst wird der Rand begrenzt.
    const bw = box.clientWidth, bh = box.clientHeight;
    const w = el.offsetWidth * scale, h = el.offsetHeight * scale;
    tx = w <= bw ? (bw - w) / 2 : Math.min(0, Math.max(bw - w, tx));
    ty = h <= bh ? (bh - h) / 2 : Math.min(0, Math.max(bh - h, ty));
    el.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  };

  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

  const onDown = (e) => {
    if (e.pointerType === "mouse") return;
    points.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (points.size === 2) {
      const [a, b] = [...points.values()];
      startDist = dist(a, b) || 1;
      startScale = scale;
      startMid = mid(a, b);
      lastMid = startMid;
    }
    // Bewusst NICHT mit einem Finger schieben: Der eine Finger gehört der
    // Gegenstelle, damit sich dort Fenster ziehen und Symbole verschieben
    // lassen. Verschoben wird die Ansicht mit ZWEI Fingern - wie in
    // Kartenanwendungen. Sonst müsste man sich zwischen "Ansicht bewegen" und
    // "auf dem entfernten Rechner ziehen" entscheiden.
  };

  const onMove = (e) => {
    if (!points.has(e.pointerId)) return;
    points.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (points.size >= 2) {
      const [a, b] = [...points.values()];
      const next = Math.min(max, Math.max(min, startScale * (dist(a, b) / startDist)));
      // Um den Mittelpunkt zwischen den Fingern zoomen, nicht um die Ecke.
      const r = box.getBoundingClientRect();
      const cx = startMid.x - r.left, cy = startMid.y - r.top;
      const nowMid = mid(a, b);
      tx = cx - ((cx - tx) / scale) * next;
      ty = cy - ((cy - ty) / scale) * next;
      // Wandern beide Finger gemeinsam, verschiebt sich die Ansicht mit -
      // Zoomen und Schieben in einer Bewegung, ohne abzusetzen.
      if (lastMid) {
        tx += nowMid.x - lastMid.x;
        ty += nowMid.y - lastMid.y;
      }
      lastMid = nowMid;
      scale = next;
      apply();
      e.preventDefault?.();
    }
  };

  const onUp = (e) => {
    points.delete(e.pointerId);
    if (points.size < 2) { startDist = 0; lastMid = null; }
  };

  // Doppeltippen: zwischen Originalgröße und zweifacher Vergrößerung wechseln.
  let lastTap = 0;
  const onTap = (e) => {
    if (e.pointerType === "mouse") return;
    const now = Date.now();
    if (now - lastTap < 300) {
      const r = box.getBoundingClientRect();
      const cx = e.clientX - r.left, cy = e.clientY - r.top;
      const next = scale > 1.05 ? 1 : 2;
      tx = cx - ((cx - tx) / scale) * next;
      ty = cy - ((cy - ty) / scale) * next;
      scale = next;
      apply();
    }
    lastTap = now;
  };

  box.addEventListener("pointerdown", onDown, { passive: true });
  box.addEventListener("pointermove", onMove, { passive: false });
  box.addEventListener("pointerup", onUp, { passive: true });
  box.addEventListener("pointercancel", onUp, { passive: true });
  box.addEventListener("pointerup", onTap, { passive: true });
  window.addEventListener("resize", apply);

  apply();

  return {
    /** Aktueller Zoom - zum Umrechnen von Bildschirmpunkten. */
    state: () => ({ scale, x: tx, y: ty }),
    /**
     * Punkt auf dem Schirm -> Punkt im ungezoomten Bild.
     * Genau das braucht der Aufrufer, um einen Klick an die Gegenstelle
     * weiterzugeben.
     */
    toContent(clientX, clientY) {
      const r = box.getBoundingClientRect();
      return {
        x: (clientX - r.left - tx) / scale,
        y: (clientY - r.top - ty) / scale,
      };
    },
    reset() { scale = 1; tx = 0; ty = 0; apply(); },
    destroy() {
      box.removeEventListener("pointerdown", onDown);
      box.removeEventListener("pointermove", onMove);
      box.removeEventListener("pointerup", onUp);
      box.removeEventListener("pointercancel", onUp);
      box.removeEventListener("pointerup", onTap);
      window.removeEventListener("resize", apply);
    },
  };
}

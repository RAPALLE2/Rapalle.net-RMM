// calculator.js (Modul "collab", Teilfunktion "calc")
// ---------------------------------------------------
// Rechner mit drei Registern:
//   Standard   Grundrechenarten, %, ±, Klammern
//   Erweitert  √, x², xʸ, ʸ√x, 1/x, n!, |x|, π, e, sin/cos/tan (+ Umkehr), ln, log, mod, Grad/Bogenmaß
//   Einheiten  Länge, Fläche, Volumen, Masse, Temperatur, Geschwindigkeit, Zeit, Daten, Druck, Energie
//
// Ausdrücke werden mit einem eigenen Parser ausgewertet (kein eval).
// Tastatur: Ziffern, + - * / ^ ( ) . , %  Enter/= rechnen, Backspace, Esc löschen.

import { esc } from "/js/app/utils.js";

// =====================================================================
// Ausdrucks-Parser (rekursiver Abstieg)
//   expr   := term (('+'|'-') term)*
//   term   := factor (('*'|'/'|'mod') factor)*      (auch implizit: 2π, 3(4))
//   factor := ('-'|'+') factor | postfix ('^' factor)?   (rechtsassoziativ, -2^2 = -4)
//   postfix:= primary ('!' | '%')*
//   primary:= Zahl | Konstante | Funktion '(' expr ')' | '(' expr ')'
// =====================================================================
const FUNCS = {
  sqrt: Math.sqrt, cbrt: Math.cbrt, abs: Math.abs, ln: Math.log, log: Math.log10,
  exp: Math.exp, floor: Math.floor, ceil: Math.ceil, round: Math.round,
};
const TRIG = ["sin", "cos", "tan", "asin", "acos", "atan"];

function factorial(n) {
  if (n < 0 || !Number.isInteger(n)) throw new Error("n! nur für natürliche Zahlen");
  if (n > 170) return Infinity;
  let r = 1;
  for (let i = 2; i <= n; i++) r *= i;
  return r;
}

export function evaluate(src, { deg = true } = {}) {
  const s = String(src).replace(/×/g, "*").replace(/÷/g, "/").replace(/−/g, "-").replace(/,/g, ".")
    .replace(/π/g, "pi").replace(/√/g, "sqrt");
  let i = 0;
  const peek = () => { while (s[i] === " ") i++; return s[i]; };
  const word = () => { peek(); const m = /^[a-z]+/i.exec(s.slice(i)); return m ? m[0].toLowerCase() : ""; };
  const toRad = (x) => (deg ? (x * Math.PI) / 180 : x);
  const fromRad = (x) => (deg ? (x * 180) / Math.PI : x);

  function primary() {
    const c = peek();
    if (c === "(") { i++; const v = expr(); if (peek() !== ")") throw new Error("Klammer fehlt"); i++; return v; }
    const num = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(s.slice(i));
    if (num) { i += num[0].length; return parseFloat(num[0]); }
    const w = word();
    if (!w) throw new Error(c ? `Unerwartetes Zeichen „${c}“` : "Ausdruck unvollständig");
    i += w.length;
    if (w === "pi") return Math.PI;
    if (w === "e") return Math.E;
    if (w === "ans") return lastAnswer;
    if (TRIG.includes(w) || FUNCS[w]) {
      if (peek() !== "(") throw new Error(`${w}( erwartet`);
      i++; const x = expr(); if (peek() !== ")") throw new Error("Klammer fehlt"); i++;
      switch (w) {
        case "sin": return Math.sin(toRad(x));
        case "cos": return Math.cos(toRad(x));
        case "tan": return Math.tan(toRad(x));
        case "asin": return fromRad(Math.asin(x));
        case "acos": return fromRad(Math.acos(x));
        case "atan": return fromRad(Math.atan(x));
        default: return FUNCS[w](x);
      }
    }
    throw new Error(`Unbekannt: ${w}`);
  }
  function postfix() {
    let v = primary();
    for (;;) {
      const c = peek();
      if (c === "!") { i++; v = factorial(v); }
      else if (c === "%") { i++; v = v / 100; }
      else return v;
    }
  }
  // Vorzeichen bindet schwaecher als ^ :  -2^2 = -(2^2) = -4,  2^-1 = 0,5
  function factor() {
    const c = peek();
    if (c === "-") { i++; return -factor(); }
    if (c === "+") { i++; return factor(); }
    const b = postfix();
    if (peek() === "^") { i++; return Math.pow(b, factor()); }
    return b;
  }
  function term() {
    let v = factor();
    for (;;) {
      const c = peek();
      if (c === "*") { i++; v *= factor(); }
      else if (c === "/") { i++; const d = factor(); v /= d; }
      else if (word() === "mod") { i += 3; const d = factor(); v = ((v % d) + d) % d; }
      else if (c === "(" || (c && /[a-z.\d]/i.test(c) && word() !== "mod")) { v *= factor(); }   // 2π, 3(4)
      else return v;
    }
  }
  function expr() {
    let v = term();
    for (;;) {
      const c = peek();
      if (c === "+") { i++; v += term(); }
      else if (c === "-") { i++; v -= term(); }
      else return v;
    }
  }
  if (!s.trim()) return 0;
  const v = expr();
  if (peek() !== undefined) throw new Error(`Unerwartetes Zeichen „${peek()}“`);
  if (Number.isNaN(v)) throw new Error("Ungültiges Ergebnis");
  return v;
}
let lastAnswer = 0;

export function formatNumber(v) {
  if (!Number.isFinite(v)) return v > 0 ? "∞" : v < 0 ? "−∞" : "Fehler";
  if (Math.abs(v) >= 1e15 || (Math.abs(v) < 1e-9 && v !== 0)) return v.toExponential(10).replace(/\.?0+e/, "e");
  const r = parseFloat(v.toPrecision(14));
  return r.toLocaleString("de-DE", { maximumFractionDigits: 12 });
}

// =====================================================================
// Einheiten (Faktor zur Basis-Einheit; Temperatur über Funktionen)
// =====================================================================
const UNITS = {
  "Länge": { base: "m", u: { "Millimeter (mm)": 0.001, "Zentimeter (cm)": 0.01, "Meter (m)": 1, "Kilometer (km)": 1000,
    "Zoll (in)": 0.0254, "Fuß (ft)": 0.3048, "Yard (yd)": 0.9144, "Meile (mi)": 1609.344, "Seemeile (sm)": 1852 } },
  "Fläche": { u: { "Quadratmillimeter (mm²)": 1e-6, "Quadratzentimeter (cm²)": 1e-4, "Quadratmeter (m²)": 1, "Ar (a)": 100,
    "Hektar (ha)": 1e4, "Quadratkilometer (km²)": 1e6, "Quadratfuß (ft²)": 0.09290304, "Acre (ac)": 4046.8564224 } },
  "Volumen": { u: { "Milliliter (ml)": 1e-3, "Zentiliter (cl)": 1e-2, "Liter (l)": 1, "Kubikmeter (m³)": 1000,
    "Teelöffel (TL)": 0.005, "Esslöffel (EL)": 0.015, "Tasse (US cup)": 0.2365882365, "Gallone (US gal)": 3.785411784, "Gallone (UK gal)": 4.54609 } },
  "Masse": { u: { "Milligramm (mg)": 1e-6, "Gramm (g)": 1e-3, "Kilogramm (kg)": 1, "Tonne (t)": 1000,
    "Unze (oz)": 0.028349523125, "Pfund (lb)": 0.45359237, "Stone (st)": 6.35029318 } },
  "Temperatur": { temp: true, u: { "Celsius (°C)": null, "Fahrenheit (°F)": null, "Kelvin (K)": null } },
  "Geschwindigkeit": { u: { "Meter/Sekunde (m/s)": 1, "Kilometer/Stunde (km/h)": 1 / 3.6, "Meilen/Stunde (mph)": 0.44704,
    "Knoten (kn)": 0.514444444, "Fuß/Sekunde (ft/s)": 0.3048 } },
  "Zeit": { u: { "Millisekunde (ms)": 0.001, "Sekunde (s)": 1, "Minute (min)": 60, "Stunde (h)": 3600, "Tag (d)": 86400,
    "Woche": 604800, "Jahr (365 d)": 31536000 } },
  "Daten": { u: { "Bit": 0.125, "Byte (B)": 1, "Kilobyte (KB)": 1e3, "Megabyte (MB)": 1e6, "Gigabyte (GB)": 1e9, "Terabyte (TB)": 1e12,
    "Kibibyte (KiB)": 1024, "Mebibyte (MiB)": 1048576, "Gibibyte (GiB)": 1073741824, "Tebibyte (TiB)": 1099511627776 } },
  "Datenrate": { u: { "Bit/s": 1, "Kilobit/s": 1e3, "Megabit/s": 1e6, "Gigabit/s": 1e9, "Byte/s": 8, "Megabyte/s": 8e6 } },
  "Druck": { u: { "Pascal (Pa)": 1, "Hektopascal (hPa)": 100, "Kilopascal (kPa)": 1000, "Bar": 1e5, "Millibar (mbar)": 100,
    "Atmosphäre (atm)": 101325, "psi": 6894.757293168 } },
  "Energie": { u: { "Joule (J)": 1, "Kilojoule (kJ)": 1000, "Kalorie (cal)": 4.184, "Kilokalorie (kcal)": 4184,
    "Wattstunde (Wh)": 3600, "Kilowattstunde (kWh)": 3.6e6 } },
};
function toCelsius(v, u) { return u.startsWith("Fahrenheit") ? (v - 32) * 5 / 9 : u.startsWith("Kelvin") ? v - 273.15 : v; }
function fromCelsius(c, u) { return u.startsWith("Fahrenheit") ? c * 9 / 5 + 32 : u.startsWith("Kelvin") ? c + 273.15 : c; }
export function convert(cat, v, from, to) {
  const c = UNITS[cat];
  if (c.temp) return fromCelsius(toCelsius(v, from), to);
  return (v * c.u[from]) / c.u[to];
}

// =====================================================================
// Oberfläche
// =====================================================================
export function renderCalculator(body) {
  let tab = "std";
  let expr = "";
  let shown = "0";
  let justEvaluated = false;
  let deg = true;
  let inv = false;
  const history = [];

  body.innerHTML = `
    <div class="calc" style="display:flex;flex-direction:column;height:100%;padding:12px;box-sizing:border-box;gap:10px;overflow:auto;font-size:13px">
      <div class="tab-bar" id="cl-tabs">
        <button class="tab-btn active" data-tab="std">Standard</button>
        <button class="tab-btn" data-tab="sci">Erweitert</button>
        <button class="tab-btn" data-tab="unit">Einheiten</button>
      </div>
      <div id="cl-body" style="flex:1;display:flex;flex-direction:column;gap:10px;min-height:0"></div>
    </div>`;
  const $ = (s) => body.querySelector(s);

  body.querySelectorAll("#cl-tabs [data-tab]").forEach((b) => b.addEventListener("click", () => {
    tab = b.dataset.tab;
    body.querySelectorAll("#cl-tabs [data-tab]").forEach((x) => x.classList.toggle("active", x === b));
    draw();
  }));

  // ------------------------------------------------------------ Rechner
  const STD = [
    ["C", "clear", "warn"], ["(", "("], [")", ")"], ["÷", "/", "op"],
    ["7", "7"], ["8", "8"], ["9", "9"], ["×", "*", "op"],
    ["4", "4"], ["5", "5"], ["6", "6"], ["−", "-", "op"],
    ["1", "1"], ["2", "2"], ["3", "3"], ["+", "+", "op"],
    ["±", "neg"], ["0", "0"], [",", "."], ["=", "eval", "primary"],
  ];
  const SCI = () => [
    [inv ? "sin⁻¹" : "sin", inv ? "asin(" : "sin("], [inv ? "cos⁻¹" : "cos", inv ? "acos(" : "cos("], [inv ? "tan⁻¹" : "tan", inv ? "atan(" : "tan("], ["INV", "inv", inv ? "on" : ""], [deg ? "DEG" : "RAD", "deg", "on"],
    ["√x", "sqrt("], ["∛x", "cbrt("], ["x²", "^2"], ["xʸ", "^"], ["ʸ√x", "yroot"],
    ["ln", "ln("], ["log", "log("], ["eˣ", "exp("], ["10ˣ", "10^"], ["1/x", "recip"],
    ["π", "π"], ["e", "e"], ["n!", "!"], ["|x|", "abs("], ["mod", " mod "],
    ["%", "%"], ["⌫", "back"], ["Ans", "ans"], ["(", "("], [")", ")"],
  ];

  function updateDisplay(err) {
    const ex = $("#cl-expr"); const out = $("#cl-out");
    if (!ex) return;
    ex.textContent = expr || " ";
    out.textContent = err || shown;
    out.style.color = err ? "var(--danger)" : "";
    // Live-Vorschau während der Eingabe
    if (!err && !justEvaluated && expr) {
      try { const v = evaluate(expr, { deg }); $("#cl-prev").textContent = "= " + formatNumber(v); }
      catch { $("#cl-prev").textContent = ""; }
    } else $("#cl-prev").textContent = "";
  }

  function press(k) {
    if (k === "clear") { expr = ""; shown = "0"; justEvaluated = false; return updateDisplay(); }
    if (k === "back") { expr = expr.slice(0, -1); justEvaluated = false; return updateDisplay(); }
    if (k === "inv") { inv = !inv; return draw(); }
    if (k === "deg") { deg = !deg; return draw(); }
    if (k === "eval") {
      if (!expr) return;
      try {
        const v = evaluate(expr, { deg });
        history.unshift({ expr, v });
        if (history.length > 30) history.pop();
        lastAnswer = v;
        shown = formatNumber(v);
        expr = String(parseFloat(v.toPrecision(15)));
        justEvaluated = true;
        updateDisplay(); drawHistory();
      } catch (e) { updateDisplay(e.message); }
      return;
    }
    if (k === "neg") { expr = expr ? `-(${expr})` : "-"; justEvaluated = false; return updateDisplay(); }
    if (k === "recip") { expr = expr ? `1/(${expr})` : "1/"; justEvaluated = false; return updateDisplay(); }
    if (k === "yroot") { expr = expr ? `(${expr})^(1/` : "^(1/"; justEvaluated = false; return updateDisplay(); }
    // Nach "=" beginnt eine Ziffer eine neue Rechnung, ein Operator rechnet weiter.
    if (justEvaluated && /^[\d.(πe]|^[a-z]/i.test(k) && !/^ mod $/.test(k)) expr = "";
    justEvaluated = false;
    expr += k;
    updateDisplay();
  }

  function keypad(keys, cols) {
    const h = tab === "sci" ? 34 : 44;          // Erweitert: kompaktere Tasten, damit alles ins Fenster passt
    return `<div style="display:grid;grid-template-columns:repeat(${cols},1fr);gap:${tab === "sci" ? 5 : 6}px">
      ${keys.map(([label, k, kind]) => {
        const cls = kind === "primary" ? "btn-primary" : kind === "warn" ? "btn btn-warn" : kind === "op" || kind === "on" ? "btn active" : "btn";
        return `<button class="${cls}" data-k="${esc(k)}" style="min-height:${h}px;font-size:${tab === "sci" ? 14 : 15}px">${esc(label)}</button>`;
      }).join("")}
    </div>`;
  }

  function drawHistory() {
    const h = $("#cl-hist");
    if (!h) return;
    h.innerHTML = history.length
      ? history.map((x, idx) => `<div data-h="${idx}" style="cursor:pointer;padding:3px 0;text-align:right">
          <span style="color:var(--subtext)">${esc(x.expr)} =</span> <b>${esc(formatNumber(x.v))}</b></div>`).join("")
      : `<div style="color:var(--subtext);text-align:right">Noch keine Rechnungen</div>`;
    h.querySelectorAll("[data-h]").forEach((el) => el.addEventListener("click", () => {
      const x = history[Number(el.dataset.h)]; expr = String(x.v); justEvaluated = true; shown = formatNumber(x.v); updateDisplay();
    }));
  }

  function drawCalc() {
    $("#cl-body").innerHTML = `
      <div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;background:var(--panel-2)">
        <div id="cl-expr" style="font-size:13px;color:var(--subtext);text-align:right;min-height:18px;word-break:break-all;font-family:ui-monospace,monospace"></div>
        <div id="cl-out" style="font-size:30px;font-weight:600;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></div>
        <div id="cl-prev" style="font-size:12px;color:var(--subtext);text-align:right;min-height:16px"></div>
      </div>
      ${tab === "sci" ? keypad(SCI(), 5) : ""}
      ${keypad(STD, 4)}
      <div id="cl-hist" style="flex:1;min-height:40px;overflow:auto;font-size:12px;border-top:1px solid var(--border);padding-top:6px"></div>`;
    $("#cl-body").querySelectorAll("[data-k]").forEach((b) => b.addEventListener("click", () => press(b.dataset.k)));
    updateDisplay(); drawHistory();
  }

  // ------------------------------------------------------------ Einheiten
  let ucat = "Länge";
  function drawUnits() {
    const names = Object.keys(UNITS[ucat].u);
    $("#cl-body").innerHTML = `
      <div class="form-row"><label>Kategorie</label>
        <select id="cu-cat">${Object.keys(UNITS).map((c) => `<option ${c === ucat ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></div>
      <div class="form-row"><label>Wert</label><input type="text" id="cu-val" value="1" inputmode="decimal"></div>
      <div style="display:flex;gap:8px;align-items:flex-end">
        <div class="form-row" style="flex:1;margin:0"><label>Von</label>
          <select id="cu-from">${names.map((n, i) => `<option ${i === (names.length > 2 ? 2 : 0) ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>
        <button class="btn btn-icon" id="cu-swap" title="Tauschen">⇄</button>
        <div class="form-row" style="flex:1;margin:0"><label>Nach</label>
          <select id="cu-to">${names.map((n, i) => `<option ${i === (names.length > 3 ? 3 : 1) ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div>
      </div>
      <div style="border:1px solid var(--border);border-radius:8px;padding:12px;background:var(--panel-2);text-align:right">
        <div id="cu-res" style="font-size:26px;font-weight:600;word-break:break-all"></div>
        <div id="cu-sub" style="font-size:12px;color:var(--subtext)"></div>
      </div>
      <div id="cu-all" style="flex:1;overflow:auto;font-size:12.5px"></div>`;
    const calc = () => {
      const from = $("#cu-from").value, to = $("#cu-to").value;
      let v;
      try { v = evaluate($("#cu-val").value, { deg }); }
      catch (e) { $("#cu-res").textContent = "–"; $("#cu-sub").textContent = e.message; $("#cu-all").innerHTML = ""; return; }
      const r = convert(ucat, v, from, to);
      $("#cu-res").textContent = formatNumber(r);
      $("#cu-sub").textContent = `${formatNumber(v)} ${from} = ${formatNumber(r)} ${to}`;
      $("#cu-all").innerHTML = names.filter((n) => n !== from).map((n) =>
        `<div style="display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-bottom:1px solid var(--border)">
          <span style="color:var(--subtext)">${esc(n)}</span><b>${esc(formatNumber(convert(ucat, v, from, n)))}</b></div>`).join("");
    };
    $("#cu-cat").addEventListener("change", (e) => { ucat = e.target.value; drawUnits(); });
    ["#cu-val", "#cu-from", "#cu-to"].forEach((s) => $(s).addEventListener("input", calc));
    $("#cu-swap").addEventListener("click", () => { const f = $("#cu-from").value; $("#cu-from").value = $("#cu-to").value; $("#cu-to").value = f; calc(); });
    calc();
  }

  function draw() { if (tab === "unit") drawUnits(); else drawCalc(); }

  // ------------------------------------------------------------ Tastatur
  body.tabIndex = 0;
  body.addEventListener("keydown", (e) => {
    if (tab === "unit" || e.target.matches("input, select, textarea")) return;
    const k = e.key;
    if (/^[\d.()+\-*/^%!]$/.test(k)) { press(k); e.preventDefault(); }
    else if (k === ",") { press("."); e.preventDefault(); }
    else if (k === "Enter" || k === "=") { press("eval"); e.preventDefault(); }
    else if (k === "Backspace") { press("back"); e.preventDefault(); }
    else if (k === "Escape" || k === "Delete") { press("clear"); e.preventDefault(); }
  });
  setTimeout(() => body.focus(), 50);
  draw();
}

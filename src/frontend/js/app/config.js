// config.js
// ---------
// Da das Frontend vom selben Python-Backend ausgeliefert wird, auf dem auch
// die API läuft, können wir hier einfach "" (leerer String = gleiche Adresse
// wie die Webseite selbst) verwenden. Läuft das Frontend doch mal von woanders,
// reicht es, hier eine volle URL einzutragen.
export const BACKEND_URL = "";

// ---------------------------------------------------------------------------
// Kanonische Adresse dieser Installation
// ---------------------------------------------------------------------------
// `location.host` ist nur die Adresse, ueber die der Benutzer GERADE zugreift.
// Fuer alles, was auf einem ANDEREN Rechner eingegeben wird - WebDAV-Pfade,
// Netzlaufwerk-Befehle, Installationszeilen - ist das die falsche Quelle: wer
// das Dashboard intern per IP oeffnet, bekaeme eine Adresse, die von aussen
// niemand erreicht.
//
// Das Backend kennt die richtige (Einstellung "Vollstaendige URL" bzw.
// Domain/Host + Port, sonst die Anfrage selbst inkl. Reverse-Proxy-Header).
// Hier wird sie einmal geholt und gemerkt; bis sie da ist, gilt location als
// Rueckfallebene, damit nichts leer bleibt.

let _publicBase = null;      // aufgeloester Wert
let _publicBasePromise = null;

function _fromLocation() {
  const port = window.location.port
    || (window.location.protocol === "https:" ? "443" : "80");
  return {
    base_url: window.location.origin,
    scheme: window.location.protocol.replace(":", ""),
    host: window.location.hostname,
    port: Number(port),
    netloc: window.location.host,
    configured: false,
  };
}

/** Sofort verfuegbarer Wert - solange nichts geladen ist, aus location. */
export function publicBaseNow() {
  return _publicBase || _fromLocation();
}

/**
 * Kanonische Adresse laden (einmal pro Sitzung). Schlaegt der Abruf fehl -
 * altes Backend, kein Netz - wird die Adresse aus location benutzt, statt
 * einen Fehler zu werfen: eine ungenaue Adresse ist besser als eine leere.
 */
export function loadPublicBase() {
  if (_publicBase) return Promise.resolve(_publicBase);
  if (_publicBasePromise) return _publicBasePromise;
  _publicBasePromise = fetch(`${BACKEND_URL}/api/public-base`, {
    headers: { Authorization: `Bearer ${localStorage.getItem("rmm_token") || ""}` },
  })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
    .then((d) => {
      _publicBase = { ...(_fromLocation()), ...d };
      return _publicBase;
    })
    .catch(() => _fromLocation());
  return _publicBasePromise;
}

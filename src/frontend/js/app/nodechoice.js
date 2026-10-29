// nodechoice.js - gemeinsamer Text fuer die Auswahl "Backend oder Node"
// --------------------------------------------------------------------
// Eintraege aus api.nodeChoices(modul): {id, name, installed, active, outdated}.
// Waehlbar ist eine Node nur, wenn das Node-Modul dort installiert UND einzeln
// aktiviert ist (seit Node 0.3.0 ist jedes Modul nach dem Installieren aus).
export function nodeHint(n) {
  if (n.active) return "";
  if (n.outdated) return " (Node veraltet – erst aktualisieren)";
  return n.installed ? " (Modul nicht aktiviert)" : " (Modul nicht installiert)";
}

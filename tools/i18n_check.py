#!/usr/bin/env python3
"""
tools/i18n_check.py
-------------------
Zeigt, wo im Frontend noch deutscher Text fest im Code steht, statt über
t() zu laufen - und ob DE und EN dieselben Schlüssel haben.

    python3 tools/i18n_check.py            # Zusammenfassung
    python3 tools/i18n_check.py --detail   # mit den konkreten Fundstellen
    python3 tools/i18n_check.py datei.js   # nur eine Datei

Die Erkennung ist eine Heuristik (Umlaute + typisch deutsche Wörter). Sie
findet nicht jeden Fall, aber sie macht den Fortschritt messbar - ohne so
eine Zahl weiß man bei 700 Strings nie, wie weit man ist.

WICHTIG (2026-07): Frueher wurden mehrzeilige Template-Literale komplett
uebersprungen. Genau dort steckt aber der meiste Oberflaechentext (die
innerHTML-Bloecke der Apps). Die gemeldete Zahl war deshalb um ein Vielfaches
zu niedrig - 137 statt real ueber 500. Jetzt werden Template-Literale
mitgelesen; t()-Aufrufe und ${...}-Ausdruecke darin werden vorher entfernt,
damit bereits uebersetzte Stellen keine Fehlalarme erzeugen.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "frontend" / "js"

DE_HINT = re.compile(
    r"[äöüÄÖÜß]|\b(und|oder|nicht|keine|kein|wird|werden|wurde|nur|noch|schon"
    r"|bitte|Bitte|Datei|Fehler|Einstellungen|Speichern|Abbrechen|Löschen"
    r"|erforderlich|verfügbar|ausgewählt|Benutzer|Aktualisierung|Verbindung)\b")

# Zeilen, die ohnehin kein UI-Text sind.
SKIP_LINE = re.compile(r"^\s*(//|\*|/\*)")


# ${...} und t("...") innerhalb von Template-Literalen: das ist eingesetzter
# Code bzw. bereits uebersetzter Text und darf nicht als Fundstelle zaehlen.
SUBST = re.compile(r"\$\{[^{}]*\}")
T_CALL = re.compile(r"""t\(\s*["'][^"']*["'][^)]*\)""")


# Interne Schluessel, die absichtlich deutscher Klartext sind und erst bei der
# Anzeige durch eine Nachschlagefunktion uebersetzt werden - z.B.
# `group: "Identität"` im metriccatalog, das groupLabel() aufloest. Solche
# Zeilen sind KEINE Fundstellen, sonst kommt der Zaehler nie auf 0 und man
# gewoehnt sich an einen Rest, in dem echte Funde untergehen.
# Auch in Objektliteralen, die in einer Zeile beginnen:
#   { id: "c.ipAddr", group: "Identität", ... }
# Deshalb ein optionales "{ " am Anfang und die Erkennung von `group:`
# irgendwo in der Zeile.
INTERNAL_KEY = re.compile(
    r'^\s*\{?\s*(?:group|id|scope)\s*:\s*["\']'
    r'|[,{]\s*(?:group|scope)\s*:\s*["\']'
)


def strings_in(src: str):
    """
    Alle String-Literale: einfache Anfuehrungszeichen, doppelte UND
    Template-Literale (auch mehrzeilige). Template-Literale werden Zeile fuer
    Zeile geprueft, nachdem ${...} und t(...) herausgenommen wurden.
    """
    for m in re.finditer(r'"([^"\n]{4,200})"|\'([^\'\n]{4,200})\'', src):
        yield m.start(), (m.group(1) or m.group(2) or "")

    # Template-Literale einzeln, damit die Zeilennummer stimmt.
    for m in re.finditer(r"`([^`\\]*(?:\\.[^`\\]*)*)`", src, re.S):
        body = m.group(1)
        offset = m.start(1)
        pos = 0
        in_comment = False      # Zustand ueber Zeilengrenzen hinweg
        for raw in body.split("\n"):
            # Mehrzeilige HTML-Kommentare: Die Mittelzeilen enthalten weder
            # "<!--" noch "-->", eine reine Zeilenpruefung reicht also nicht.
            # Ohne diese Zustandsverfolgung blieben Erklaerkommentare (z.B. in
            # audioplayer.js) dauerhaft als Fundstelle stehen.
            if in_comment:
                if "-->" in raw:
                    in_comment = False
                    raw = raw.split("-->", 1)[1]
                else:
                    pos += len(raw) + 1
                    continue
            if "<!--" in raw and "-->" not in raw.split("<!--", 1)[1]:
                in_comment = True
                raw = raw.split("<!--", 1)[0]
            cleaned = T_CALL.sub("", SUBST.sub("", raw))
            # HTML-Kommentare sind kein Oberflaechentext - sie standen aber
            # als Fundstelle in der Liste und liessen den Zaehler nie auf 0
            # gehen (z.B. der Erklaerkommentar in tickets.js).
            cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.S)
            if "<!--" in cleaned or "-->" in cleaned:
                continue        # mehrzeiliger Kommentar
            # HTML-Attribute und Tags weglassen, es geht um sichtbaren Text.
            cleaned = re.sub(r"<[^>]*>", " ", cleaned)
            cleaned = cleaned.strip()
            if len(cleaned) >= 4:
                yield offset + pos, cleaned[:200]
            pos += len(raw) + 1


def check_keys() -> None:
    src = (ROOT / "i18n.js").read_text(encoding="utf-8")
    blocks = {}
    for lang in ("de", "en"):
        m = re.search(rf"\n  {lang}: \{{\n(.*?)\n  \}},?\n", src, re.S)
        if not m:
            print(f"  {lang}: Block nicht gefunden!")
            return
        blocks[lang] = set(
            re.findall(r"(?:^|,)\s*([a-zA-Z_][\w]*)\s*:", m.group(1), re.M))
    only_de = sorted(blocks["de"] - blocks["en"])
    only_en = sorted(blocks["en"] - blocks["de"])
    print(f"Schlüssel: de={len(blocks['de'])} en={len(blocks['en'])}")
    print(f"  nur in DE: {only_de or 'keine'}")
    print(f"  nur in EN: {only_en or 'keine'}")


# Aufrufe von t("…") bzw. tr("…"). Das (?<![\w.]) ist wichtig: ohne die
# Absicherung matchte der Ausdruck auch classList.toggle("active") und meldete
# haufenweise CSS-Klassen als fehlende Schluessel.
T_KEY = re.compile(r'(?<![\w.])(?:t|tr)\(\s*"([a-zA-Z_][\w]*)"')


def check_used_keys() -> None:
    """
    Findet Schluessel, die im Code benutzt werden, aber im Sprachpaket fehlen.
    Das ist die wichtigste Absicherung ueberhaupt: t() faellt bei einem
    unbekannten Schluessel still auf den Schluesselnamen zurueck - in der
    Oberflaeche steht dann z.B. "set_db_hint" statt eines Satzes, und niemand
    merkt es, bis ein Benutzer es meldet.
    """
    src = (ROOT / "i18n.js").read_text(encoding="utf-8")
    m = re.search(r"\n  de: \{\n(.*?)\n  \},?\n", src, re.S)
    if not m:
        print("  de-Block nicht gefunden!")
        return
    keys = set(re.findall(r"(?:^|,)\s*([a-zA-Z_][\w]*)\s*:", m.group(1), re.M))
    used: dict = {}
    for f in sorted(ROOT.rglob("*.js")):
        if f.name.startswith("i18n") or "vendor" in f.parts:
            continue
        for mm in T_KEY.finditer(f.read_text(encoding="utf-8")):
            used.setdefault(mm.group(1), set()).add(f.relative_to(ROOT))
    missing = {k: v for k, v in used.items() if k not in keys}
    print(f"Verwendete Schlüssel: {len(used)}   ohne Eintrag: {len(missing) or 'keine'}")
    for k, files in sorted(missing.items()):
        print(f"    {k} -> {', '.join(sorted(str(x) for x in files))}")
    unused = sorted(keys - set(used))
    print(f"  Nie verwendet: {len(unused)}")


# code="…" in agent/agent.py: Der Agent schickt Uebersetzungsschluessel statt
# fertiger Saetze ans Dashboard (siehe _notify_screen_error). Fehlt so ein
# Schluessel im Sprachpaket, faellt t() still auf den Schluesselnamen zurueck -
# im Dashboard stuende dann woertlich "agent_wayland". Das faellt sonst erst im
# Betrieb auf, und zwar genau in der Fehlersituation, die man gerade untersucht.
# Sowohl als Argument   code="agent_x"
# als auch im Dict       "code": "agent_x"
# Die Dict-Form wird beim direkten Aufbau der Nutzlast verwendet und waere
# sonst durch die Pruefung gefallen.
AGENT_CODE = re.compile(r'(?:code=|"code"\s*:\s*)["\']([a-z0-9_]+)["\']')


def check_agent_codes() -> None:
    # ROOT zeigt auf frontend/js -> zwei Ebenen hoch ist die Projektwurzel.
    agent = ROOT.parent.parent / "agent" / "agent.py"
    if not agent.is_file():
        print("  agent/agent.py nicht gefunden - übersprungen.")
        return
    src = (ROOT / "i18n.js").read_text(encoding="utf-8")
    m = re.search(r"\n  de: \{\n(.*?)\n  \},?\n", src, re.S)
    keys = set(re.findall(r"(?:^|,)\s*([a-zA-Z_][\w]*)\s*:", m.group(1), re.M)) if m else set()
    used = set(AGENT_CODE.findall(agent.read_text(encoding="utf-8")))
    missing = sorted(used - keys)
    print(f"Agent-Codes: {len(used)}   ohne Eintrag: {len(missing) or 'keine'}")
    for k in missing:
        print(f"    {k}")


def check_backend_and_agent_dicts() -> None:
    """
    Paritaet der beiden ZUSAETZLICHEN Woerterbuecher pruefen:
      - backend/app/i18n.py  -> TEXTS (Server-Meldungen, Logs, Installer)
      - agent/agent.py       -> _AGENT_TEXTS (Dialoge am Geraet + Agent-Logs)
    Beide sind vom Frontend-Sprachpaket getrennt, weil sie andere Empfaenger
    haben (siehe Kopf von backend/app/i18n.py). Ohne diese Pruefung faellt eine
    fehlende Uebersetzung erst auf dem Zielrechner auf.
    """
    root = ROOT.parent.parent
    for label, path, var in (
        ("Backend", root / "backend" / "app" / "i18n.py", "TEXTS"),
        ("Agent", root / "agent" / "agent.py", "_AGENT_TEXTS"),
    ):
        if not path.is_file():
            print(f"  {label}: {path.name} nicht gefunden - übersprungen.")
            continue
        src = path.read_text(encoding="utf-8")
        m = re.search(var + r"[^=]*= \{.*?\n\}\n", src, re.S)
        if not m:
            print(f"  {label}: {var} nicht gefunden.")
            continue
        ns: dict = {}
        try:
            exec(var + " = " + m.group(0).split("=", 1)[1], ns)  # noqa: S102
            table = ns[var]
        except Exception as exc:
            print(f"  {label}: {var} nicht auswertbar ({exc}).")
            continue
        de, en = set(table.get("de", {})), set(table.get("en", {}))
        only_de, only_en = sorted(de - en), sorted(en - de)
        print(f"{label}: de={len(de)} en={len(en)}")
        print(f"    nur in DE: {', '.join(only_de) if only_de else 'keine'}")
        print(f"    nur in EN: {', '.join(only_en) if only_en else 'keine'}")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detail" in sys.argv

    print("=== Schlüssel-Parität ===")
    check_keys()

    print("\n=== Schlüssel im Code ===")
    check_used_keys()

    print("\n=== Übersetzungs-Codes des Agenten ===")
    check_agent_codes()

    print("\n=== Wörterbücher von Backend und Agent ===")
    check_backend_and_agent_dicts()

    files = [ROOT / a for a in args] if args else sorted(ROOT.rglob("*.js"))
    print("\n=== Fest verdrahteter deutscher Text ===")
    total = 0
    rows = []
    for f in files:
        if f.name == "i18n.js":
            continue
        src = f.read_text(encoding="utf-8")
        hits = []
        for pos, s in strings_in(src):
            line_start = src.rfind("\n", 0, pos) + 1
            line = src[line_start:src.find("\n", pos)]
            if SKIP_LINE.match(line):
                continue
            if INTERNAL_KEY.match(line):
                continue
            # Steht der Text als OBJEKT-SCHLUESSEL da ("Identität": "mg_..."),
            # ist er kein Oberflaechentext, sondern die linke Seite einer
            # Zuordnung auf einen Uebersetzungsschluessel - so aufgebaut z.B.
            # die keys-Tabelle in groupLabel().
            if re.search(re.escape(s) + r'["\']\s*:', line):
                continue
            # Kommentar am Zeilenende (// ...) ist kein Oberflächentext.
            cpos = line.find("//")
            if cpos != -1 and (pos - line_start) > cpos:
                continue
            if s.startswith(("http", "data:", "/api/")):
                continue
            if DE_HINT.search(s):
                hits.append((src.count("\n", 0, pos) + 1, s[:70]))
        if hits:
            rows.append((len(hits), f.relative_to(ROOT), hits))
            total += len(hits)

    for n, rel, hits in sorted(rows, reverse=True):
        print(f"{n:5}  {rel}")
        if detail:
            for ln, s in hits:
                print(f"         {ln:5}: {s}")
    print(f"\nDateien: {len(rows)}   Fundstellen: {total}")


if __name__ == "__main__":
    main()

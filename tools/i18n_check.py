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


def strings_in(src: str):
    """
    Einzeilige String-Literale. Mehrzeilige Template-Literale werden bewusst
    ausgelassen: die enthalten fast immer ganze HTML-Blöcke mit bereits
    übersetzten t()-Aufrufen darin und erzeugen sonst massenhaft Fehlalarme,
    die die Zahl wertlos machen.
    """
    for m in re.finditer(r'"([^"\n]{4,200})"|\'([^\'\n]{4,200})\'', src):
        yield m.start(), (m.group(1) or m.group(2) or "")


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


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detail" in sys.argv

    print("=== Schlüssel-Parität ===")
    check_keys()

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

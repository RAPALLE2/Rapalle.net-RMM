#!/usr/bin/env python3
"""
tools/unblock_routes.py
-----------------------
Loest HTTP-Endpunkte von der Ereignisschleife, die sie unnoetig blockieren.

Das Problem
-----------
Fast alle Endpunkte des Backends sind als 'async def' geschrieben. Das klingt
richtig, ist es hier aber oft nicht: Wer 'async def' schreibt, laeuft DIREKT
in der einen Ereignisschleife, die gleichzeitig alle Agentenverbindungen,
den Healthcheck und jede andere Anfrage bedient.

Solange in so einer Funktion nur 'await' auf echte Nebenlaeufigkeit steht,
ist das genau richtig. Steht darin aber gar kein 'await', sondern nur
synchrone Arbeit - vor allem SQLite-Zugriffe -, dann steht die gesamte
Schleife fuer die Dauer dieser Arbeit still. Bei wenigen Geraeten faellt das
nicht auf. Bei vielen wird daraus genau das Bild, das wir hatten: Der
Healthcheck laeuft in sein Zeitlimit, der Reverse Proxy meldet einen
Zeitueberlauf, Agenten fliegen raus - und der Prozess selbst ist voellig
gesund.

Die Loesung ist in FastAPI eingebaut: Ein Endpunkt, der als gewoehnliches
'def' geschrieben ist, wird automatisch in einem Arbeits-Thread ausgefuehrt.
Die Schleife bleibt frei. Man muss also nur ein Schluesselwort entfernen -
aber eben nur dort, wo es wirklich sicher ist.

Was dieses Werkzeug tut
-----------------------
Es liest die Router mit dem Syntaxbaum (nicht mit Textsuche) und stellt eine
Funktion NUR dann um, wenn ALLE Bedingungen erfuellt sind:

  * Sie ist ein Endpunkt (Dekorator @router.get/post/...).
  * Sie enthaelt kein 'await', kein 'async with', kein 'async for'.
  * Sie fasst nichts an, was an die Schleife gebunden ist: asyncio, sio,
    create_task, ensure_future, run_in_executor, send_to_agent, den
    Socket-Zustand.

Trifft auch nur eines davon nicht zu, bleibt die Funktion unveraendert.
Lieber ein blockierender Endpunkt zu viel als ein kaputter.

Aufruf
------
    python tools/unblock_routes.py --dry-run     # nur anzeigen
    python tools/unblock_routes.py --apply       # umstellen
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete", "head",
                    "options", "api_route"}

# Alles, was an die Ereignisschleife gebunden ist. Taucht einer dieser Namen
# in der Funktion auf, wird sie NICHT angefasst - selbst wenn kein 'await'
# darin steht. Beispiel: ein Aufruf von asyncio.ensure_future() braucht eine
# laufende Schleife und wuerde in einem Arbeits-Thread scheitern.
LOOP_BOUND = {
    "asyncio", "sio", "ensure_future", "create_task", "get_event_loop",
    "new_event_loop", "run_coroutine_threadsafe", "run_in_executor",
    "send_to_agent", "gather", "wait_for", "Future", "state",
    "request_exec", "websocket", "StreamingResponse",
    # 'emit' ist ohne 'await' schon heute verdaechtig - so eine Funktion
    # wandert erst recht nicht in einen Arbeits-Thread.
    "emit", "notifier", "broadcast", "UploadFile", "BackgroundTasks",
}


def route_kind(fn: ast.AsyncFunctionDef) -> str | None:
    for deco in fn.decorator_list:
        node = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(node, ast.Attribute) and node.attr in ROUTE_DECORATORS:
            return node.attr
    return None


def blockers(fn: ast.AsyncFunctionDef) -> set[str]:
    """Was spricht dagegen, diese Funktion umzustellen?"""
    found: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Await):
            found.add("await")
        elif isinstance(node, (ast.AsyncWith, ast.AsyncFor)):
            found.add("async-with/for")
        elif isinstance(node, ast.Name) and node.id in LOOP_BOUND:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in LOOP_BOUND:
            found.add(node.attr)
    return found


def process(path: pathlib.Path, apply: bool) -> tuple[int, int, list[str]]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        print(f"  ! {path.name}: nicht lesbar ({e})")
        return 0, 0, []

    lines = source.splitlines(keepends=True)
    changes: list[tuple[int, str]] = []
    names: list[str] = []
    total = 0

    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        if not route_kind(fn):
            continue
        total += 1
        if blockers(fn):
            continue
        idx = fn.lineno - 1
        line = lines[idx]
        stripped = line.lstrip()
        if not stripped.startswith("async def "):
            continue      # Dekorator mehrzeilig o.ae. - Finger weg
        indent = line[:len(line) - len(stripped)]
        changes.append((idx, indent + stripped[len("async "):]))
        names.append(fn.name)

    if apply and changes:
        for idx, new_line in changes:
            lines[idx] = new_line
        path.write_text("".join(lines), encoding="utf-8")

    return total, len(changes), names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dir", default="app/routers")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    root = pathlib.Path(args.dir)
    if not root.is_dir():
        print(f"Verzeichnis {root} nicht gefunden - bitte aus backend/ aufrufen.")
        return 2

    grand_total = grand_changed = 0
    for path in sorted(root.glob("*.py")):
        total, changed, names = process(path, apply)
        grand_total += total
        grand_changed += changed
        if changed:
            print(f"  {path.name}: {changed}/{total} Endpunkte "
                  f"{'umgestellt' if apply else 'umstellbar'}")

    print()
    print(f"Endpunkte gesamt        : {grand_total}")
    print(f"{'Umgestellt' if apply else 'Umstellbar'}              : {grand_changed}")
    print(f"Bleiben auf der Schleife: {grand_total - grand_changed} "
          f"(brauchen sie auch)")
    if not apply:
        print("\nNichts veraendert. Mit --apply wird umgestellt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
app/memtrim.py - Arbeitsspeicher des Backends klein halten
==========================================================

Beobachtung: Das Backend belegte bei ~20 Agenten bis zu 500 MB, obwohl die
Python-Objekte selbst nur einen Bruchteil davon ausmachen. Ursache ist (fast
immer) nicht ein Leck, sondern der C-Speicherverwalter (glibc malloc):

  * Jeder Thread (Thread-Pool fuer blockierende Aufrufe, SFTP, Proxmox, ...)
    bekommt eine eigene "Arena". Bis zu 8 x CPU-Kerne Arenen, jede haelt
    freigegebenen Speicher fest.
  * Grosse, kurzlebige Puffer (Remote-Screen-Bilder, Datei-Uebertragungen,
    Module-Bundles, Proxy-Antworten) hinterlassen nach dem Freigeben Luecken,
    die glibc nicht an das Betriebssystem zurueckgibt.

Gegenmittel (alles ohne Funktionsverlust):
  1. limit_arenas(): Arenen auf 2 begrenzen (mallopt M_ARENA_MAX) - so frueh wie
     moeglich, bevor Threads entstehen. Entspricht MALLOC_ARENA_MAX=2.
  2. Regelmaessig malloc_trim(0): freie Seiten an das System zurueckgeben.
  3. Thread-Pool begrenzen (Standard waeren bis zu 32 Threads).
  4. Nach dem Start gc.freeze(): die beim Start geladenen Objekte (Module,
     Routen) werden vom Garbage-Collector nicht mehr jedes Mal durchlaufen.

Unter Windows/macOS/musl gibt es mallopt/malloc_trim nicht - dann tut das
Modul still nichts (Punkte 3 und 4 gelten trotzdem).

Einstellbar per Umgebung: RMM_MEMTRIM=0 schaltet alles ab,
RMM_MEMTRIM_INTERVAL (Sekunden, Standard 60), RMM_EXECUTOR_THREADS (Standard 12-16 je nach Kernen).
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import gc
import os
import sys
import threading
import time

_ENABLED = os.getenv("RMM_MEMTRIM", "1") != "0"
_libc = None
STATS = {"trims": 0, "freed_kb": 0, "last_rss_kb": 0, "arena_limit": False, "executor": 0}


def _load_libc():
    global _libc
    if _libc is not None or not sys.platform.startswith("linux"):
        return _libc
    try:
        name = ctypes.util.find_library("c") or "libc.so.6"
        lib = ctypes.CDLL(name)
        if hasattr(lib, "malloc_trim") and hasattr(lib, "mallopt"):
            _libc = lib
    except OSError:
        _libc = None
    return _libc


def rss_kb() -> int:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    try:
        import psutil
        return int(psutil.Process().memory_info().rss / 1024)
    except Exception:
        return 0


def limit_arenas(n: int = 2) -> None:
    """So frueh wie moeglich aufrufen (main.py, vor allen Threads)."""
    if not _ENABLED:
        return
    lib = _load_libc()
    if lib is None:
        return
    try:
        M_ARENA_MAX = -8
        if lib.mallopt(M_ARENA_MAX, int(os.getenv("MALLOC_ARENA_MAX") or n)) == 1:
            STATS["arena_limit"] = True
    except Exception:
        pass


def trim() -> int:
    """Freien Heap an das System zurueckgeben. Rueckgabe: freigegebene KB."""
    lib = _load_libc()
    before = rss_kb()
    gc.collect()
    if lib is not None:
        try:
            lib.malloc_trim(0)
        except Exception:
            pass
    after = rss_kb()
    freed = max(0, before - after)
    STATS["trims"] += 1
    STATS["freed_kb"] += freed
    STATS["last_rss_kb"] = after
    return freed


def _trim_loop() -> None:
    interval = max(15, int(os.getenv("RMM_MEMTRIM_INTERVAL") or 60))
    while True:
        time.sleep(interval)
        try:
            freed = trim()
            if freed > 20 * 1024:
                print(f"[speicher] {freed // 1024} MB an das System zurueckgegeben "
                      f"(jetzt {STATS['last_rss_kb'] // 1024} MB)", flush=True)
        except Exception:
            pass


_started = False


def start(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Beim Backend-Start (Startup-Ereignis) aufrufen."""
    global _started
    if _started or not _ENABLED:
        return
    _started = True
    # Thread-Pool begrenzen: weniger Threads = weniger Stacks und Arenen
    try:
        from concurrent.futures import ThreadPoolExecutor
        dflt = min(16, max(12, (os.cpu_count() or 1) + 4))   # nie weniger als Pythons Standard bei <= 8 Kernen
        n = max(4, int(os.getenv("RMM_EXECUTOR_THREADS") or dflt))
        (loop or asyncio.get_event_loop()).set_default_executor(
            ThreadPoolExecutor(max_workers=n, thread_name_prefix="rmm-pool"))
        STATS["executor"] = n
    except Exception:
        pass
    threading.Thread(target=_trim_loop, name="rmm-memtrim", daemon=True).start()

    def _freeze():
        time.sleep(30)          # Module/Routen sind dann geladen
        try:
            gc.collect()
            gc.freeze()
        except Exception:
            pass
        trim()
    threading.Thread(target=_freeze, name="rmm-gcfreeze", daemon=True).start()

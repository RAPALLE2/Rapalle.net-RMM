# sync.py (Modul "dist") - vorhandene Agent-/Client-Pakete nach /dist spiegeln
# --------------------------------------------------------------------------
# Damit /dist/agent und /dist/client die tatsaechlich gebauten Pakete zeigen,
# ohne die bestehenden Build-Ausgaben zu verschieben: aktuelle Pakete werden
# (nur wenn neuer/fehlend) nach backend/dist/<agent|client> kopiert.
from __future__ import annotations

import shutil
from pathlib import Path

from app.paths import BACKEND_DIR, STORAGE_DIR

from . import store

CLIENT_SOURCES = [STORAGE_DIR / "client", BACKEND_DIR.parent / "client" / "release"]
CLIENT_EXT = (".exe", ".msi", ".zip", ".deb", ".rpm", ".pkg.tar.xz", ".tar.gz", ".tgz", ".tar", ".apk")


def _copy_new(src: Path, dst: Path) -> int:
    n = 0
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if not f.is_file() or not f.name.lower().endswith(CLIENT_EXT):
            continue
        target = dst / f.name
        if not target.exists() or target.stat().st_mtime < f.stat().st_mtime or target.stat().st_size != f.stat().st_size:
            try:
                shutil.copy2(f, target)
                n += 1
            except OSError:
                pass
    return n


def sync_packages() -> int:
    store.ensure_dirs()
    n = 0
    client_dst = store.DIST_DIR / "client"
    for src in CLIENT_SOURCES:
        n += _copy_new(src, client_dst)
    # Agent-Pakete: falls der Agent-Build Dateien unter storage/agent ablegt.
    agent_src = STORAGE_DIR / "agent"
    n += _copy_new(agent_src, store.DIST_DIR / "agent")
    return n

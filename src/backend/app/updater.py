"""
updater.py  -  Server-Update mit Struktur-Migration (NUR Standardbibliothek!)
==========================================================================

Diese Datei laeuft in DREI Rollen:

1. Als Modul im laufenden Backend (``from app import updater``):
   ``start_update(zip_path, ...)`` legt einen Auftrag an, kopiert sich selbst
   nach /tmp und ERSETZT den Backend-Prozess per ``os.execv`` durch den
   Update-Prozess. Das RMM ist damit aus (Ports/DB-Verbindungen zu), die PID
   bleibt aber dieselbe - systemd/Docker merken nichts davon und toeten den
   Updater nicht (ein abgekoppelter Kindprozess wuerde beim Beenden des
   Dienstes von systemd/Docker mit abgeschossen).

2. Als eigenstaendiges Skript (``python updater.py --job <job.json>``):
   macht das eigentliche Update:
     a) wichtige Dateien (DB, .env, ...) zusaetzlich in ein ZIP sichern
     b) alle Daten laut update.json (bzw. Alt-Pfaden) in einen Sicherungsordner
        verschieben
     c) den kompletten alten Code wegraeumen (-> .rmm_update/<job>/old, nicht
        geloescht -> manuelles Zurueckspielen moeglich)
     d) das neue Release-ZIP an seinen Platz legen
     e) die gesicherten Daten an die Orte legen, die die update.json des NEUEN
        Release vorgibt
     f) die richtige Startdatei des neuen Release starten (main_py_backend_location)
   Schlaegt a)-e) fehl, wird jeder Schritt rueckgaengig gemacht und der alte
   Stand wieder gestartet.

3. Vom Kompatibilitaets-Starter ``<root>/backend/main.py`` (alte Ordner-
   struktur): ``legacy_entry(root, args)`` erkennt eine Alt-Installation,
   laedt den passenden Stand von GitHub (offline: nimmt den bereits
   hineinkopierten Code) und migriert. Danach startet er immer
   ``src/backend/main.py`` - eine bestehende Dienst-Konfiguration, die noch
   ``backend/main.py`` startet, funktioniert also weiter.

update.json (src/backend/data/update.json) beschreibt, WO welche Daten im
Release liegen. Die installierte Kopie bekommt zusaetzlich einen Block
"installed" (Stand, verwaltete Eintraege). Siehe docs/UPDATE.md.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

WORK_NAME = ".rmm_update"              # Arbeits-/Sicherungsordner (pro Mount)
RESULT_NAME = "last_result.json"       # <root>/.rmm_update/last_result.json
LAYOUT_MARK = "rmm_layout"             # Kennzeichen einer gueltigen update.json
DEFAULT_REPO = "https://github.com/RAPALLE2/Rapalle.net-RMM.git"
KEEP_JOBS = 2                          # so viele alte Update-Ordner behalten

_LOG_FH = None


# ==========================================================================
# Protokoll
# ==========================================================================

def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [updater] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    if _LOG_FH:
        try:
            _LOG_FH.write(line + "\n")
            _LOG_FH.flush()
        except Exception:
            pass


def _open_log(path: Path) -> None:
    global _LOG_FH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _LOG_FH = open(path, "a", encoding="utf-8")
    except Exception:
        _LOG_FH = None


# ==========================================================================
# Kleine Helfer
# ==========================================================================

def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _exists(p: Path) -> bool:
    return os.path.lexists(str(p))


def _is_real_dir(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _abs(p) -> Path:
    # bewusst ohne resolve(): Symlinks sollen als Link verschoben werden
    return Path(os.path.abspath(str(p)))


def _ismount(p: Path) -> bool:
    try:
        return os.path.ismount(str(p))
    except Exception:
        return False


def _depth(rel: str) -> int:
    return len([x for x in rel.replace("\\", "/").split("/") if x])


def _rel_norm(rel: str) -> str:
    return "/".join(x for x in str(rel).replace("\\", "/").split("/") if x and x != ".")


def result_file(root: Path) -> Path:
    return Path(root) / WORK_NAME / RESULT_NAME


def read_result(root: Path) -> dict:
    return _read_json(result_file(root)) or {}


def write_result(root: Path, data: dict) -> None:
    try:
        _write_json(result_file(root), data)
    except Exception as e:
        log(f"Ergebnis konnte nicht geschrieben werden: {e}")


# ==========================================================================
# Journal: jede Verschiebung wird gemerkt -> vollstaendiges Zurueckrollen
# ==========================================================================

class Journal:
    def __init__(self):
        self.moves: list[tuple[str, str]] = []
        self.created: list[str] = []

    def _mkparents(self, p: Path) -> None:
        missing = []
        cur = p
        while not _exists(cur):
            missing.append(cur)
            if cur.parent == cur:
                break
            cur = cur.parent
        for d in reversed(missing):
            os.mkdir(d)
            self.created.append(str(d))

    def mkdirs(self, p: Path) -> None:
        if not _exists(p):
            self._mkparents(p)

    def mv(self, src: Path, dst: Path) -> None:
        src, dst = Path(src), Path(dst)
        if _exists(dst):
            raise RuntimeError(f"Ziel existiert bereits: {dst}")
        self.mkdirs(dst.parent)
        shutil.move(str(src), str(dst))
        self.moves.append((str(src), str(dst)))

    def rollback(self) -> list[str]:
        errors = []
        for src, dst in reversed(self.moves):
            try:
                if _exists(Path(dst)):
                    if _exists(Path(src)):
                        # Platz belegt (z.B. leerer, neu angelegter Ordner)
                        sp = Path(src)
                        if _is_real_dir(sp) and not any(sp.iterdir()):
                            sp.rmdir()
                        else:
                            errors.append(f"{src} belegt, {dst} bleibt liegen")
                            continue
                    Path(src).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(dst, src)
            except Exception as e:
                errors.append(f"{dst} -> {src}: {e}")
        for d in reversed(self.created):
            try:
                os.rmdir(d)
            except OSError:
                pass
        self.moves.clear()
        self.created.clear()
        return errors


# ==========================================================================
# update.json lesen
# ==========================================================================

def _valid_layout(data) -> bool:
    return isinstance(data, dict) and data.get(LAYOUT_MARK) is True and isinstance(data.get("items"), dict)


def find_release_layout(release_root: Path) -> tuple[dict, str]:
    """Sucht im entpackten Release die update.json (Kennzeichen rmm_layout).
    Liefert (Inhalt, Pfad relativ zum Release-Root)."""
    preferred = release_root / "src" / "backend" / "data" / "update.json"
    cand = [preferred] if preferred.is_file() else []
    if not cand:
        for p in release_root.rglob("update.json"):
            if WORK_NAME in p.parts:
                continue
            cand.append(p)
    for p in cand:
        d = _read_json(p)
        if _valid_layout(d):
            return d, _rel_norm(p.relative_to(release_root).as_posix())
    raise RuntimeError("Release enthaelt keine gueltige update.json - Update abgebrochen")


def _items(layout: dict) -> dict:
    out = {}
    for k, v in (layout.get("items") or {}).items():
        if k.startswith("_") or not isinstance(v, dict) or not v.get("path"):
            continue
        out[k] = v
    return out


# ==========================================================================
# Ort fuer Arbeitsdateien: auf DEMSELBEN Laufwerk/Mount wie die Datei
# (verschieben statt kopieren - wichtig fuer grosse Aufzeichnungen und fuer
# Docker, wo src/ ein Bind-Mount ist)
# ==========================================================================

class Work:
    def __init__(self, root: Path, job_id: str):
        self.root = root
        self.job = job_id
        self.main = root / WORK_NAME / job_id
        self.dirs: set[Path] = {self.main}

    def for_path(self, p: Path) -> Path:
        p = _abs(p)
        cur = p.parent
        while _inside(cur, self.root) and cur != self.root:
            if _ismount(cur):
                d = cur / WORK_NAME / self.job
                self.dirs.add(d)
                return d
            cur = cur.parent
        return self.main

    def rel(self, p: Path) -> str:
        try:
            return _abs(p).relative_to(self.root).as_posix()
        except ValueError:
            return _abs(p).name

    def protected_roots(self) -> list[Path]:
        # alle .rmm_update-Ordner (nicht nur dieser Job)
        out = {self.root / WORK_NAME}
        for d in self.dirs:
            out.add(d.parent)
        return sorted(out)


# ==========================================================================
# Verschieben mit Zusammenfuehren
# ==========================================================================

def merge_move(j: Journal, src: Path, dst: Path, trash: Path, *, src_wins: bool,
               src_wins_except: tuple = (), _top: bool = True) -> None:
    """Fuehrt Ordner src in dst zusammen.
    src_wins=True: bei Konflikt gewinnt src (dst-Datei wandert in den Muell);
    Namen in src_wins_except (nur oberste Ebene) gewinnen trotzdem in dst.
    src_wins=False: dst gewinnt, die src-Datei bleibt einfach liegen."""
    for entry in sorted(os.scandir(src), key=lambda e: e.name):
        s = Path(entry.path)
        t = dst / entry.name
        if not _exists(t):
            j.mv(s, t)
        elif _is_real_dir(s) and _is_real_dir(t):
            merge_move(j, s, t, trash / entry.name, src_wins=src_wins, _top=False)
        else:
            if src_wins and not (_top and entry.name in src_wins_except):
                j.mv(t, _unique(trash / entry.name))
                j.mv(s, t)
            # sonst: src bleibt im Arbeitsordner liegen (nicht verloren)


def _unique(p: Path) -> Path:
    if not _exists(p):
        return p
    i = 1
    while _exists(p.with_name(f"{p.name}.{i}")):
        i += 1
    return p.with_name(f"{p.name}.{i}")


# ==========================================================================
# Wichtige Dateien zusaetzlich in ein ZIP kopieren (VOR jeder Verschiebung)
# ==========================================================================

def backup_important(root: Path, work: Work, release_items: dict, installed_items: dict) -> Path | None:
    files: list[tuple[str, Path]] = []
    for key, spec in release_items.items():
        if not spec.get("backup"):
            continue
        cands = _candidates(key, spec, installed_items)
        for i, rel in enumerate(cands):
            p = root / rel
            if p.is_file():
                files.append((f"{key}/{i}_{p.name}", p))
                for suf in spec.get("companions") or []:
                    c = p.with_name(p.name + suf)
                    if c.is_file():
                        files.append((f"{key}/{i}_{c.name}", c))
    if not files:
        return None
    out = work.main / "backup" / f"important-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, p in files:
            try:
                z.write(p, arc)
            except Exception as e:
                log(f"  Sicherung von {p} fehlgeschlagen: {e}")
    log(f"Wichtige Dateien gesichert: {out} ({len(files)} Dateien)")
    return out


# ==========================================================================
# Daten-Kandidaten (aktueller Ort, Release-Ort, Alt-Orte)
# ==========================================================================

def _candidates(key: str, spec: dict, installed_items: dict) -> list[str]:
    seen, out = set(), []
    first = (installed_items.get(key) or {}).get("path")
    # Reihenfolge = Prioritaet: tatsaechlich installierter Ort, dann Alt-Orte,
    # zuletzt der Release-Ort. Ohne installierte update.json liegt am
    # Release-Ort nur eine (vom alten Updater hineinkopierte) Release-Fassung
    # oder eine leere, frisch angelegte Datei - die echten Daten liegen dann an
    # den Alt-Orten.
    for rel in [first, *(spec.get("legacy") or []), spec.get("path")]:
        if not rel:
            continue
        r = _rel_norm(rel)
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def stage_data(j: Journal, root: Path, work: Work, release_items: dict,
               installed_items: dict, protected: list[Path]) -> dict:
    """Verschiebt alle Daten in die Arbeitsordner.
    Rueckgabe: {key: {"kind", "path": Path (zusammengefuehrt) | None}}"""
    entries = []   # (depth, key, prio, rel)
    specs = dict(release_items)
    # Daten, die das neue Release nicht mehr kennt -> trotzdem sichern
    for k, v in installed_items.items():
        if k not in specs and isinstance(v, dict) and v.get("path"):
            specs[k] = {"path": None, "kind": v.get("kind", "dir"), "orphan": True,
                        "legacy": [v["path"]]}
    for key, spec in specs.items():
        for prio, rel in enumerate(_candidates(key, spec, installed_items)):
            entries.append((_depth(rel), key, prio, rel))
    # tiefste Pfade zuerst: Unterordner (recordings) vor Oberordner (media)
    entries.sort(key=lambda e: (-e[0], e[1], e[2]))

    staged: dict[str, list[tuple[int, Path]]] = {}
    for _, key, prio, rel in entries:
        spec = specs[key]
        p = root / rel
        if not _exists(p):
            continue
        ap = _abs(p)
        if any(ap == pr or _inside(ap, pr) or _inside(pr, ap) for pr in protected):
            log(f"  {key}: {rel} ist geschuetzt bzw. enthaelt Geschuetztes - bleibt liegen")
            continue
        if _ismount(ap):
            log(f"  {key}: {rel} ist ein Mount-Punkt - bleibt an seinem Platz")
            protected.append(ap)
            continue
        kind = spec.get("kind", "dir")
        if kind == "file" and not p.is_file():
            continue
        if kind == "dir" and not _is_real_dir(p):
            continue
        dst = work.for_path(p) / "data" / key / f"{prio}"
        if kind == "file":
            j.mv(p, dst / p.name)
            for suf in spec.get("companions") or []:
                c = p.with_name(p.name + suf)
                if c.is_file():
                    j.mv(c, dst / c.name)
        else:
            j.mv(p, dst / "content")
        staged.setdefault(key, []).append((prio, dst, p.name))
        log(f"  gesichert: {key} <- {rel}")

    # Kandidaten je Schluessel zusammenfuehren
    result = {}
    for key, lst in staged.items():
        spec = specs[key]
        kind = spec.get("kind", "dir")
        lst.sort(key=lambda x: x[0])
        if kind == "file":
            best = lst[0]
            for prio, d, _n in lst[1:]:
                log(f"  {key}: weitere Fassung ({prio}) bleibt als Duplikat in {d}")
            result[key] = {"kind": "file", "dir": best[1], "file": best[1] / best[2],
                           "spec": spec}
        else:
            base = lst[0][1] / "content"
            for prio, d, _n in lst[1:]:
                merge_move(j, d / "content", base, d / "trash", src_wins=False)
            result[key] = {"kind": "dir", "dir": base, "spec": spec}
    return result


# ==========================================================================
# Alten Code wegraeumen
# ==========================================================================

def clear_old(j: Journal, root: Path, work: Work, names: set[str], protected: list[Path],
              keep_rel: set[str]) -> None:
    keep_abs = [_abs(root / r) for r in keep_rel]
    prot = [_abs(p) for p in protected] + keep_abs + [_abs(p) for p in work.protected_roots()]

    def _has_protected_below(p: Path) -> bool:
        return any(_inside(x, p) and x != p for x in prot)

    def _clear(d: Path, only: set[str] | None):
        for entry in sorted(os.scandir(d), key=lambda e: e.name):
            p = _abs(entry.path)
            if entry.name == WORK_NAME:
                continue
            if only is not None and entry.name not in only:
                continue
            if p in prot:
                continue
            if _is_real_dir(p) and (_ismount(p) or _has_protected_below(p)):
                _clear(p, None)   # hineingehen, Ordner selbst bleibt
                continue
            j.mv(p, work.for_path(p) / "old" / work.rel(p))

    _clear(root, names)


# ==========================================================================
# Neues Release einsetzen
# ==========================================================================

def place_release(j: Journal, src: Path, dst: Path, work: Work, protected: list[Path]) -> None:
    prot = [_abs(p) for p in protected] + [_abs(p) for p in work.protected_roots()]
    for entry in sorted(os.scandir(src), key=lambda e: e.name):
        if entry.name == WORK_NAME:
            continue
        s = Path(entry.path)
        t = _abs(dst / entry.name)
        if not _exists(t):
            j.mv(s, t)
        elif _is_real_dir(s) and _is_real_dir(t):
            place_release(j, s, t, work, protected)
        elif t in prot:
            log(f"  {work.rel(t)} ist geschuetzt - Release-Fassung bleibt im Arbeitsordner")
        else:
            j.mv(t, _unique(work.for_path(t) / "trash" / work.rel(t)))
            j.mv(s, t)


# ==========================================================================
# Daten in die neue Struktur legen
# ==========================================================================

def restore_data(j: Journal, root: Path, work: Work, staged: dict) -> dict:
    placed = {}
    order = sorted(staged.items(), key=lambda kv: _depth(kv[1]["spec"].get("path") or "") if kv[1]["spec"].get("path") else 99)
    for key, info in order:
        spec = info["spec"]
        if spec.get("orphan") or not spec.get("path"):
            dst = work.main / "orphaned" / key
            log(f"  {key}: im neuen Release unbekannt -> {dst}")
            continue   # bleibt im Arbeitsordner liegen
        target = _abs(root / _rel_norm(spec["path"]))
        trash = work.for_path(target) / "trash" / "data" / key
        if info["kind"] == "file":
            f: Path = info["file"]
            if _exists(target):
                j.mv(target, _unique(trash / target.name))
            j.mv(f, target)
            for suf in spec.get("companions") or []:
                c = info["dir"] / (f.name + suf)
                tc = target.with_name(target.name + suf)
                if _exists(tc):
                    j.mv(tc, _unique(trash / tc.name))
                if c.is_file():
                    j.mv(c, tc)
        else:
            d: Path = info["dir"]
            if not _exists(target):
                j.mv(d, target)
            elif _is_real_dir(target):
                merge_move(j, d, target, trash, src_wins=True,
                           src_wins_except=tuple(spec.get("release_owned") or ()))
            else:
                j.mv(target, _unique(trash / target.name))
                j.mv(d, target)
        placed[key] = _rel_norm(spec["path"])
        log(f"  wiederhergestellt: {key} -> {placed[key]}")
    return placed


# ==========================================================================
# Hauptablauf
# ==========================================================================

def _extract_zip(zip_path: Path, dest: Path) -> Path:
    if _exists(dest):
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            name = m.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise RuntimeError(f"Unsicherer Pfad im ZIP: {m.filename}")
        z.extractall(dest)
        # Dateirechte (z.B. +x fuer docker-entrypoint.sh) uebernehmen
        for m in z.infolist():
            mode = (m.external_attr >> 16) & 0o777
            if mode:
                try:
                    os.chmod(dest / m.filename, mode)
                except Exception:
                    pass
    entries = [p for p in dest.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]     # GitHub-ZIP: <repo>-<ref>/...
    return dest


def _protected_runtime(root: Path) -> list[Path]:
    """Python-Umgebung schuetzen, falls sie im Projekt liegt (venv)."""
    out = []
    for pref in {sys.prefix, sys.base_prefix, os.path.dirname(os.path.dirname(sys.executable))}:
        p = _abs(pref)
        if _inside(p, root) and p != root:
            out.append(p)
    return out


def run_job(job: dict) -> None:
    root = _abs(job["root"])
    try:
        os.chdir(root)
    except Exception:
        pass
    job_id = job.get("id") or time.strftime("%Y%m%d-%H%M%S")
    work = Work(root, job_id)
    work.main.mkdir(parents=True, exist_ok=True)
    _open_log(work.main / "update.log")
    log(f"=== Update {job_id} gestartet ({job.get('desc') or job.get('ref') or 'lokal'}) ===")
    log(f"Projekt: {root}  Modus: {'lokal' if not job.get('zip') else 'ZIP'}")

    lock = root / WORK_NAME / "update.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        age = time.time() - lock.stat().st_mtime
        if age < 1800:
            raise RuntimeError("Es laeuft bereits ein Update (update.lock)")
        lock.write_text(str(os.getpid()))

    j = Journal()
    result = {"job": job_id, "ref": job.get("ref"), "desc": job.get("desc"),
              "user": job.get("user"), "started": time.time(), "status": "running",
              "log": str(work.main / "update.log"), "work": str(work.main),
              "mode": "local" if not job.get("zip") else "zip", "legacy": bool(job.get("legacy"))}
    write_result(root, result)
    phase = "init"
    try:
        # --- Release bereitstellen ----------------------------------------
        if job.get("zip"):
            phase = "entpacken"
            release_root = _extract_zip(Path(job["zip"]), work.main / "new")
        else:
            release_root = root
        layout, layout_rel = find_release_layout(release_root)
        release_items = _items(layout)
        main_rel = _rel_norm(layout.get("main_py_backend_location") or "src/backend/main.py")
        if not (release_root / main_rel).is_file():
            raise RuntimeError(f"Startdatei {main_rel} fehlt im Release")

        installed = _read_json(Path(job["installed_layout"])) if job.get("installed_layout") else None
        if not _valid_layout(installed):
            installed = _read_json(root / layout_rel) if job.get("zip") else None
        if not _valid_layout(installed) or not installed.get("installed"):
            installed = None
        installed_items = _items(installed) if installed else {}
        log(f"Release-Layout: {layout_rel}, Start: {main_rel}, "
            f"installiertes Layout: {'ja' if installed else 'nein (Alt-Pfade werden gesucht)'}")

        protected = _protected_runtime(root)
        if job.get("zip"):
            protected.append(_abs(job["zip"]))
        keep_rel = set(_rel_norm(x) for x in (layout.get("keep") or []))
        for p in protected:
            log(f"  geschuetzt: {work.rel(p)}")

        # --- a) Sicherung -------------------------------------------------
        phase = "sichern"
        backup_important(root, work, release_items, installed_items)

        # --- b) Daten wegsichern ------------------------------------------
        phase = "daten"
        log("Daten werden gesichert ...")
        staged = stage_data(j, root, work, release_items, installed_items, protected)

        # --- c) alten Code wegraeumen -------------------------------------
        phase = "aufraeumen"
        if job.get("zip"):
            names = set(p.name for p in release_root.iterdir())
            names |= set(layout.get("managed_legacy") or [])
            if installed:
                names |= set((installed.get("installed") or {}).get("managed") or [])
        else:
            # lokal: der neue Code liegt schon da -> nur die alten Code-Ordner
            # wegraeumen, Kompatibilitaets-Starter (backend/main.py) behalten
            names = set(layout.get("legacy_code") or [])
            for sh in layout.get("legacy_shims") or []:
                protected.append(_abs(root / _rel_norm(sh)))
        names -= {WORK_NAME}
        names -= {n for n in keep_rel if "/" not in n}
        log(f"Alter Code wird weggeraeumt: {', '.join(sorted(names))}")
        clear_old(j, root, work, names, protected, keep_rel)

        # --- d) neues Release einsetzen -----------------------------------
        if job.get("zip"):
            phase = "einsetzen"
            log("Neues Release wird eingesetzt ...")
            place_release(j, release_root, root, work, protected + [_abs(root / r) for r in keep_rel])

        # --- e) Daten in neue Struktur ------------------------------------
        phase = "wiederherstellen"
        log("Daten werden in die neue Struktur gelegt ...")
        placed = restore_data(j, root, work, staged)
        if not (root / main_rel).is_file():
            raise RuntimeError(f"Startdatei {main_rel} fehlt nach dem Einsetzen")
    except Exception as e:
        tb = traceback.format_exc()
        log(f"FEHLER in Phase '{phase}': {e}\n{tb}")
        log("Rolle alle Aenderungen zurueck ...")
        errs = j.rollback()
        for x in errs:
            log(f"  Rueckrollen: {x}")
        log("Zurueckgerollt." if not errs else "Zurueckgerollt (mit Warnungen, siehe oben).")
        result.update(status="rolled_back", phase=phase, error=str(e), finished=time.time(),
                      rollback_warnings=errs)
        write_result(root, result)
        try:
            lock.unlink()
        except Exception:
            pass
        raise

    # --- installierte update.json schreiben -------------------------------
    try:
        inst = json.loads(json.dumps(layout))
        if job.get("zip"):
            managed = {p.name for p in release_root.iterdir()}
        else:
            ml = set(layout.get("managed_legacy") or [])
            managed = {p.name for p in root.iterdir() if p.name in ml}
        managed = sorted(m for m in managed if m != WORK_NAME)
        inst["installed"] = {
            "ref": job.get("ref"), "desc": job.get("desc"),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "job": job_id,
            "managed": managed, "data": placed,
        }
        _write_json(root / layout_rel, inst)
    except Exception as e:
        log(f"WARNUNG: installierte update.json nicht geschrieben: {e}")

    # Protokoll zusaetzlich in den Log-Ordner des neuen Release kopieren
    try:
        logs_rel = (release_items.get("logs_location") or {}).get("path")
        if logs_rel:
            d = root / _rel_norm(logs_rel) / "backend"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work.main / "update.log", d / "update.log")
    except Exception:
        pass

    result.update(status="applied", finished=time.time(), main=main_rel)
    write_result(root, result)
    try:
        lock.unlink()
    except Exception:
        pass
    log(f"=== Update fertig. Starte {main_rel} ===")
    job["_start"] = str(root / main_rel)


# ==========================================================================
# Starten
# ==========================================================================

def exec_python(script: Path, args: list[str]) -> None:
    script = Path(script)
    try:
        os.chdir(script.parent)
    except Exception:
        pass
    try:
        sys.stdout.flush(); sys.stderr.flush()
    except Exception:
        pass
    if _LOG_FH:
        try:
            _LOG_FH.close()
        except Exception:
            pass
    os.execv(sys.executable, [sys.executable, str(script)] + list(args))


def helper_main(job_path: str) -> None:
    job = _read_json(Path(job_path)) or {}
    args = job.get("args") or []
    try:
        run_job(job)
    except Exception as e:
        log(f"Update fehlgeschlagen: {e}")
        fb = job.get("fallback")
        if fb and Path(fb).is_file():
            log(f"Starte den bisherigen Stand wieder: {fb}")
            exec_python(Path(fb), args)
        raise
    exec_python(Path(job["_start"]), args)


# ==========================================================================
# Rolle 1: aus dem laufenden Backend heraus
# ==========================================================================

def checkpoint_sqlite(db_file: Path) -> None:
    try:
        if Path(db_file).is_file():
            c = sqlite3.connect(str(db_file), timeout=10)
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
    except Exception as e:
        log(f"WAL-Checkpoint uebersprungen: {e}")


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def work_root(root: Path) -> Path:
    return Path(root) / WORK_NAME


def start_update(*, root: Path, zip_path: Path, ref: str, desc: str, user: str | None,
                 installed_layout: Path, db_file: Path, current_main: Path,
                 args: list[str]) -> None:
    """Ersetzt den laufenden Backend-Prozess durch den Updater (kehrt nicht zurueck)."""
    root = _abs(root)
    job_id = Path(zip_path).parent.name
    job = {
        "id": job_id, "root": str(root), "zip": str(zip_path), "ref": ref, "desc": desc,
        "user": user, "installed_layout": str(installed_layout),
        "fallback": str(current_main), "args": list(args),
    }
    job_file = Path(zip_path).parent / "job.json"
    _write_json(job_file, job)
    helper = Path(tempfile.gettempdir()) / f"rmm_updater_{job_id}.py"
    shutil.copy2(__file__, helper)
    checkpoint_sqlite(db_file)
    try:
        os.chdir(root)
    except Exception:
        pass
    log(f"Backend wird beendet, Update-Prozess uebernimmt ({helper})")
    try:
        sys.stdout.flush(); sys.stderr.flush()
    except Exception:
        pass
    try:
        os.execv(sys.executable, [sys.executable, str(helper), "--job", str(job_file)])
    except Exception as e:
        # Notfall (z.B. execv nicht moeglich): abgekoppelter Prozess
        import subprocess
        log(f"execv nicht moeglich ({e}) - starte Updater abgekoppelt")
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED | NEW_GROUP
        else:
            kw["start_new_session"] = True
        subprocess.Popen([sys.executable, str(helper), "--job", str(job_file)], **kw)
        os._exit(0)


def download(url: str, dest: Path, timeout: int = 180) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "rapalle-rmm-updater"})
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(tmp, dest)
    with zipfile.ZipFile(dest) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"ZIP beschaedigt ({bad})")
    return dest


def prune_old_jobs(root: Path, keep: int = KEEP_JOBS) -> None:
    """Alte Update-Ordner (alter Code, Duplikate, Muell) aufraeumen."""
    bases = {Path(root) / WORK_NAME}
    try:
        for p in Path(root).rglob(WORK_NAME):
            if p.is_dir() and p.name == WORK_NAME:
                bases.add(p)
                if len(bases) > 20:
                    break
    except Exception:
        pass
    for base in bases:
        try:
            jobs = sorted([d for d in base.iterdir() if d.is_dir()], key=lambda d: d.name)
        except Exception:
            continue
        for d in jobs[:-keep] if keep else jobs:
            shutil.rmtree(d, ignore_errors=True)


# ==========================================================================
# Rolle 3: Kompatibilitaets-Starter (alte Ordnerstruktur)
# ==========================================================================

LEGACY_MARKERS = (
    "backend/app", "frontend", "agent", "tools",
    "backend/data.sqlite", "backend/.env", "backend/recordings", "backend/branding",
)


def legacy_needed(root: Path) -> bool:
    return any(_exists(Path(root) / m) for m in LEGACY_MARKERS)


def _legacy_repo(root: Path) -> str:
    for rel in ("backend/data/repo.txt", "backend/repo.txt", "src/backend/data/repo.txt"):
        try:
            u = (Path(root) / rel).read_text(encoding="utf-8").strip()
            if u:
                return u
        except Exception:
            pass
    return DEFAULT_REPO


def _legacy_ref(root: Path) -> str | None:
    for rel in ("backend/data/data.sqlite", "backend/data.sqlite", "src/backend/data/data.sqlite"):
        p = Path(root) / rel
        if not p.is_file():
            continue
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
            row = c.execute("SELECT value FROM settings WHERE key='server_current_commit'").fetchone()
            c.close()
            if row and row[0]:
                return str(row[0])
        except Exception:
            pass
    return None


def _owner_repo(url: str) -> tuple[str, str]:
    import re
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    if not m:
        raise RuntimeError(f"Repo-URL nicht verstanden: {url}")
    return m.group(1), m.group(2)


def legacy_entry(root, args: list[str]) -> None:
    """Wird von <root>/backend/main.py (Kompatibilitaets-Starter) aufgerufen."""
    root = _abs(root)
    new_main = root / "src" / "backend" / "main.py"
    attempt = 0
    while legacy_needed(root):
        attempt += 1
        job_id = new_job_id()
        log(f"Alte Ordnerstruktur erkannt - Migration (Versuch {attempt})")
        job = {"id": job_id, "root": str(root), "legacy": True, "args": list(args),
               "desc": "Migration alte Ordnerstruktur"}
        try:
            owner, repo = _owner_repo(_legacy_repo(root))
            ref = _legacy_ref(root) or "HEAD"
            url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref}"
            log(f"Lade {url}")
            zp = download(url, root / WORK_NAME / job_id / "release.zip")
            job.update(zip=str(zp), ref=ref, desc=f"Migration auf {ref[:12]}")
            run_job(job)
            break
        except Exception as e:
            log(f"Migration mit Download nicht moeglich: {e}")
        # Rueckfall: der neue Code wurde vom alten Updater schon hineinkopiert
        try:
            job = {"id": new_job_id() + "-lokal", "root": str(root), "legacy": True,
                   "args": list(args), "desc": "Migration alte Ordnerstruktur (lokal)"}
            run_job(job)
            break
        except Exception as e:
            log(f"Lokale Migration fehlgeschlagen: {e}")
        wait = min(30 * attempt, 300)
        log(f"Neuer Versuch in {wait}s (es wird nicht aufgegeben)")
        time.sleep(wait)

    # Startdatei laut installierter update.json
    lay = _read_json(root / "src" / "backend" / "data" / "update.json") or {}
    main_rel = _rel_norm(lay.get("main_py_backend_location") or "src/backend/main.py")
    target = root / main_rel if (root / main_rel).is_file() else new_main
    exec_python(target, args)


# ==========================================================================

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--job":
        helper_main(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "--legacy":
        legacy_entry(sys.argv[2], sys.argv[3:])
    else:
        print("Aufruf: updater.py --job <job.json> | --legacy <projekt-root> [args]")
        sys.exit(2)

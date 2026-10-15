"""
routers/update_routes.py
-------------------------
Server-Selbst-Update aus dem GitHub-Repo (Settings -> Update).

Die Repo-URL liegt in backend/repo.txt (z.B.
https://github.com/RAPALLE2/Rapalle.net-RMM.git) und kann über die API geändert
werden.

Endpunkte (nur Admin):
  GET  /api/admin/update/info   -> aktuelle Version, Repo, neuester Commit,
                                   alle Releases (Alpha + Full), Vorschläge
  PUT  /api/admin/update/repo   -> Repo-URL in repo.txt speichern
  POST /api/admin/update/run    -> Update ausführen
        body: { "target": "commit" | "full" | "any" | "custom", "tag": "..."? }

Ablauf eines Updates (app/updater.py, Details: docs/UPDATE.md):
  1. Ziel-Ref bestimmen (Commit-SHA des Default-Branches oder Release-Tag)
  2. ZIP von codeload.github.com nach <root>/.rmm_update/<job>/release.zip
     laden und pruefen (noch im laufenden Backend -> Fehler sieht der Admin)
  3. Backend-Prozess wird per os.execv durch den Updater ersetzt (RMM aus):
     wichtige Dateien sichern, Daten laut update.json wegsichern, alten Code
     KOMPLETT wegraeumen, ZIP einsetzen, Daten an die Orte der NEUEN
     update.json legen
  4. Updater startet die Startdatei des neuen Release (main_py_backend_location)
     - bei einem Fehler wird alles zurueckgerollt und der alte Stand gestartet

Auto-Update: main.py ruft periodisch auto_update_tick() auf. Je nach Kanal
("commit" | "full" | "any") wird geprüft, ob es etwas Neueres gibt, und das
Update automatisch ausgeführt. Ein "zuletzt versuchtes Ziel" (Settings)
verhindert Endlos-Schleifen, falls ein Update nicht greift.
"""

import asyncio
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db, updater
from app.auth import get_current_user, require_perm
from router.admin_routes import require_admin

router = APIRouter(prefix="/api/admin/update", tags=["update"])

# …/backend/app/routers/update_routes.py -> Projekt-Root (…/rmm)
from app.paths import (
    PROJECT_ROOT as _PROJECT_ROOT, REPO_FILE as _REPO_FILE, BACKEND_VERSION_FILE as _VERSION_FILE,
    UPDATE_LAYOUT_FILE as _LAYOUT_FILE, UPDATE_WORK_DIR as _WORK_DIR, DB_FILE as _DB_FILE,
)
_DEFAULT_REPO = "https://github.com/RAPALLE2/Rapalle.net-RMM.git"

# Welche Daten erhalten bleiben und wohin sie wandern, steht NICHT mehr hier,
# sondern in src/backend/data/update.json (Layout des jeweiligen Release).
_update_lock = asyncio.Lock()


def _read_repo_url() -> str:
    try:
        url = _REPO_FILE.read_text(encoding="utf-8").strip()
        if url:
            return url
    except OSError:
        pass
    # repo.txt fehlt -> mit Default anlegen
    try:
        _REPO_FILE.write_text(_DEFAULT_REPO + "\n", encoding="utf-8")
    except OSError:
        pass
    return _DEFAULT_REPO


def _parse_owner_repo(url: str) -> tuple[str, str]:
    """'https://github.com/OWNER/REPO(.git)' -> ('OWNER', 'REPO')"""
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    if not m:
        raise HTTPException(400, f"Repo-URL nicht verstanden: {url}")
    return m.group(1), m.group(2)


def _current_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "unbekannt"


def _http_json(url: str):
    """GitHub-API-Aufruf (synchron; wird im Executor ausgeführt)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "rapalle-rmm-updater",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_alpha(release: dict) -> bool:
    tag = (release.get("tag_name") or "").lower()
    return bool(release.get("prerelease")) or "alpha" in tag or "beta" in tag or "rc" in tag


def _fetch_github_state() -> dict:
    """Holt Default-Branch, neuesten Commit und alle Releases vom Repo."""
    owner, repo = _parse_owner_repo(_read_repo_url())
    base = f"https://api.github.com/repos/{owner}/{repo}"
    info = _http_json(base)
    default_branch = info.get("default_branch") or "main"
    head = _http_json(f"{base}/commits/{default_branch}")
    releases = _http_json(f"{base}/releases?per_page=100")
    rel_list = [{
        "tag": r.get("tag_name"),
        "name": r.get("name") or r.get("tag_name"),
        "alpha": _is_alpha(r),
        "published_at": r.get("published_at"),
    } for r in releases if r.get("tag_name") and not r.get("draft")]
    latest_any = rel_list[0]["tag"] if rel_list else None
    latest_full = next((r["tag"] for r in rel_list if not r["alpha"]), None)
    return {
        "owner": owner, "repo": repo, "default_branch": default_branch,
        "latest_commit": {
            "sha": head.get("sha"),
            "message": ((head.get("commit") or {}).get("message") or "").split("\n")[0][:120],
            "date": ((head.get("commit") or {}).get("committer") or {}).get("date"),
        },
        "releases": rel_list,
        "latest_full_tag": latest_full,
        "latest_any_tag": latest_any,
    }


# ------------------------------------------------------------------
# Update ausführen
# ------------------------------------------------------------------

def _download_release(ref: str, owner: str, repo: str) -> Path:
    """Laedt das ZIP des Refs in einen neuen Job-Ordner und prueft es."""
    job_dir = _WORK_DIR / updater.new_job_id()
    zp = updater.download(f"https://codeload.github.com/{owner}/{repo}/zip/{ref}",
                          job_dir / "release.zip")
    # Gueltiges Release? (update.json mit rmm_layout + Startdatei muessen drin sein)
    try:
        with zipfile.ZipFile(zp) as z:
            names = {n.replace("\\", "/") for n in z.namelist()}
            top = ""
            firsts = {n.split("/", 1)[0] for n in names if "/" in n}
            if len(firsts) == 1:
                top = next(iter(firsts)) + "/"
            lay = None
            for n in sorted(names, key=len):
                if not n.endswith("update.json"):
                    continue
                try:
                    d = json.loads(z.read(n).decode("utf-8"))
                except Exception:
                    continue
                if isinstance(d, dict) and d.get(updater.LAYOUT_MARK) is True:
                    lay = d
                    break
    except zipfile.BadZipFile as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(502, f"Update-ZIP ungueltig: {e}")
    if lay is None:
        # Alter Stand ohne update.json: wuerde Daten an falsche Orte legen.
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(409, "Dieser Stand enthaelt keine update.json (zu alt fuer den "
                                 "neuen Updater) - abgebrochen, nichts veraendert.")
    main_rel = (lay.get("main_py_backend_location") or "src/backend/main.py").strip("/")
    if (top + main_rel) not in names:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(409, f"Startdatei {main_rel} fehlt im Release - abgebrochen.")
    return zp


def _schedule_handover(zp: Path, ref: str, desc: str, username: str | None,
                       delay: float = 1.5) -> None:
    """Uebergibt nach kurzer Pause (Antwort geht noch raus) an den Updater.
    os.execv ersetzt den Backend-Prozess - das RMM ist danach aus."""
    def _go():
        try:
            updater.start_update(
                root=_PROJECT_ROOT, zip_path=zp, ref=ref, desc=desc, user=username,
                installed_layout=_LAYOUT_FILE, db_file=_DB_FILE,
                current_main=Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0].endswith(".py")
                else _PROJECT_ROOT / "src" / "backend" / "main.py",
                args=sys.argv[1:] if sys.argv and sys.argv[0].endswith(".py") else [],
            )
        except Exception as e:
            print(f"[update] Uebergabe an den Updater fehlgeschlagen: {e}")
            try:
                db.add_audit_entry(username, "server.update_failed", details=str(e)[:500])
            except Exception:
                pass
    loop = asyncio.get_event_loop()
    loop.call_later(delay, _go)


def _resolve_target(gh: dict, target: str, tag: str | None) -> tuple[str, str]:
    """Gibt (ref, beschreibung) für das gewünschte Ziel zurück."""
    if target == "commit":
        sha = (gh.get("latest_commit") or {}).get("sha")
        if not sha:
            raise HTTPException(502, "Konnte neuesten Commit nicht ermitteln")
        return sha, f"Commit {sha[:10]}"
    if target == "full":
        t = gh.get("latest_full_tag")
        if not t:
            raise HTTPException(404, "Kein Full-Release im Repo gefunden")
        return t, f"Release {t}"
    if target == "any":
        t = gh.get("latest_any_tag")
        if not t:
            raise HTTPException(404, "Kein Release im Repo gefunden")
        return t, f"Release {t}"
    if target == "custom":
        if not tag or not any(r["tag"] == tag for r in gh.get("releases", [])):
            raise HTTPException(400, "Unbekanntes Release-Tag")
        return tag, f"Release {tag}"
    raise HTTPException(400, "Ungültiges Ziel (commit|full|any|custom)")


async def _run_update(target: str, tag: str | None, username: str | None) -> dict:
    if _update_lock.locked():
        raise HTTPException(409, "Es laeuft bereits ein Update")
    async with _update_lock:
        loop = asyncio.get_event_loop()
        gh = await loop.run_in_executor(None, _fetch_github_state)
        ref, desc = _resolve_target(gh, target, tag)

        zp = await loop.run_in_executor(None, _download_release, ref, gh["owner"], gh["repo"])

        # Angewendeten Stand merken (Basis für den Commit-Kanal und gegen Schleifen).
        # Landet in der DB, die der Updater mitnimmt.
        db.set_setting("server_current_commit", ref)
        db.set_setting("server_last_update_target", ref)
        db.add_audit_entry(username, "server.update_applied", details=desc)
        print(f"[update] {desc} geladen - Updater uebernimmt gleich (Backend wird beendet).")
        _schedule_handover(zp, ref, desc, username)
        return {"ok": True, "applied": desc, "ref": ref,
                "restart": "Backend wird beendet, aktualisiert und neu gestartet"}


def finish_after_start() -> None:
    """Beim Start des Backends aufrufen: Ergebnis des letzten Updates
    auswerten (Audit-Log) und alte Update-Ordner aufraeumen."""
    try:
        res = updater.read_result(_PROJECT_ROOT)
    except Exception:
        res = {}
    st = res.get("status")
    try:
        if st == "applied":
            res["status"] = "ok"
            res["confirmed"] = time.time()
            updater.write_result(_PROJECT_ROOT, res)
            db.add_audit_entry(res.get("user") or "system", "server.update_completed",
                               details=res.get("desc") or res.get("ref") or "")
            print(f"[update] Update erfolgreich abgeschlossen: {res.get('desc')}")
        elif st == "rolled_back" and not res.get("reported"):
            res["reported"] = True
            updater.write_result(_PROJECT_ROOT, res)
            db.add_audit_entry(res.get("user") or "system", "server.update_rolled_back",
                               details=f"{res.get('desc')}: {res.get('error')}"[:500])
            print(f"[update] Letztes Update wurde zurueckgerollt: {res.get('error')}")
    except Exception as e:
        print(f"[update] Update-Ergebnis nicht auswertbar: {e}")
    try:
        updater.prune_old_jobs(_PROJECT_ROOT)
    except Exception:
        pass


# ------------------------------------------------------------------
# API-Endpunkte
# ------------------------------------------------------------------

class RepoBody(BaseModel):
    url: str


class RunBody(BaseModel):
    target: str            # "commit" | "full" | "any" | "custom"
    tag: str | None = None  # nur bei target == "custom"


@router.get("/info")
async def update_info(user: dict = Depends(get_current_user)):
    require_perm(user, "admin_settings")
    loop = asyncio.get_event_loop()
    try:
        gh = await loop.run_in_executor(None, _fetch_github_state)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GitHub nicht erreichbar: {e}")
    return {
        "current_version": _current_version(),
        "current_commit": db.get_setting("server_current_commit", "") or "",
        "repo_url": _read_repo_url(),
        "last_update": _last_update_summary(),
        **gh,
    }


def _last_update_summary() -> dict:
    r = updater.read_result(_PROJECT_ROOT)
    if not r:
        return {}
    return {k: r.get(k) for k in ("status", "desc", "ref", "error", "phase", "started",
                                    "finished", "mode", "legacy", "job")}


@router.get("/status")
def update_status(user: dict = Depends(get_current_user)):
    """Ergebnis + Protokoll-Ende des letzten Updates (fuer die Einstellungen)."""
    require_perm(user, "admin_settings")
    r = updater.read_result(_PROJECT_ROOT)
    tail = ""
    try:
        lp = Path(r.get("log") or "")
        if lp.is_file():
            tail = "".join(lp.read_text(encoding="utf-8", errors="replace").splitlines(True)[-60:])
    except Exception:
        pass
    return {**_last_update_summary(), "log_tail": tail}


@router.put("/repo")
def set_repo(body: RepoBody, user: dict = Depends(get_current_user)):
    require_perm(user, "admin_settings")
    url = body.url.strip()
    _parse_owner_repo(url)   # validiert das Format
    _REPO_FILE.write_text(url + "\n", encoding="utf-8")
    db.add_audit_entry(user["username"], "server.update_repo_set", details=url)
    return {"ok": True, "repo_url": url}


@router.post("/run")
async def run_update(body: RunBody, user: dict = Depends(get_current_user)):
    require_perm(user, "admin_settings")
    try:
        return await _run_update(body.target, body.tag, user["username"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Update fehlgeschlagen: {e}")


# ------------------------------------------------------------------
# Auto-Update (wird periodisch von main.py aufgerufen)
# ------------------------------------------------------------------

async def auto_update_tick() -> None:
    """
    Prüft, ob laut Kanal etwas Neueres verfügbar ist, und spielt es ein.
    Kanäle (Setting 'server_auto_update_channel'):
      commit -> neuester Commit des Default-Branches
      full   -> neuestes Full-Release (keine Alpha/Beta/RC)
      any    -> neuestes Release (Alpha UND Full)
    """
    if db.get_setting("server_auto_update", "0") != "1":
        return
    channel = (db.get_setting("server_auto_update_channel", "full") or "full").lower()
    if channel not in ("commit", "full", "any"):
        channel = "full"

    loop = asyncio.get_event_loop()
    try:
        gh = await loop.run_in_executor(None, _fetch_github_state)
    except Exception as e:
        print(f"[auto-update] GitHub nicht erreichbar: {e}")
        return

    if channel == "commit":
        ref = (gh.get("latest_commit") or {}).get("sha") or ""
        known = db.get_setting("server_current_commit", "") or ""
        if not ref:
            return
        if not known:
            # Erster Lauf: aktuellen Stand nur als Basis merken, nicht updaten.
            db.set_setting("server_current_commit", ref)
            return
        if ref == known:
            return
    else:
        ref = gh.get("latest_full_tag") if channel == "full" else gh.get("latest_any_tag")
        if not ref:
            return
        cur = _current_version().lstrip("vV")
        if ref.lstrip("vV") == cur:
            return

    # Schleifenschutz: dasselbe Ziel nicht mehrfach hintereinander versuchen
    # (z.B. wenn ein Release die Version nicht ändert).
    if (db.get_setting("server_last_update_target", "") or "") == ref:
        return

    print(f"[auto-update] Neues Ziel gefunden ({channel}): {ref} - Update startet.")
    try:
        await _run_update(
            "commit" if channel == "commit" else ("full" if channel == "full" else "any"),
            None, None,
        )
    except Exception as e:
        # Ziel trotzdem als "versucht" markieren, damit keine Endlosschleife entsteht.
        db.set_setting("server_last_update_target", ref)
        print(f"[auto-update] Update fehlgeschlagen: {e}")

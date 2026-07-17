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

Ablauf eines Updates:
  1. Ziel-Ref bestimmen (Commit-SHA des Default-Branches oder Release-Tag)
  2. ZIP von codeload.github.com laden und nach /tmp entpacken
  3. Dateien über das Projektverzeichnis kopieren - AUSGENOMMEN lokale Daten
     (data.sqlite, .env, repo.txt, recordings/, branding/)
  4. Backend startet sich selbst neu (os.execv) -> läuft mit neuem Code weiter

Auto-Update: main.py ruft periodisch auto_update_tick() auf. Je nach Kanal
("commit" | "full" | "any") wird geprüft, ob es etwas Neueres gibt, und das
Update automatisch ausgeführt. Ein "zuletzt versuchtes Ziel" (Settings)
verhindert Endlos-Schleifen, falls ein Update nicht greift.
"""

import asyncio
import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user
from app.routers.admin_routes import require_admin

router = APIRouter(prefix="/api/admin/update", tags=["update"])

# …/backend/app/routers/update_routes.py -> Projekt-Root (…/rmm)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REPO_FILE = _PROJECT_ROOT / "backend" / "repo.txt"
_VERSION_FILE = _PROJECT_ROOT / "backend" / "version.txt"
_DEFAULT_REPO = "https://github.com/RAPALLE2/Rapalle.net-RMM.git"

# Diese Pfade (relativ zum Projekt-Root) werden beim Update NIE überschrieben
# bzw. gelöscht - hier stecken lokale Daten und Konfiguration drin.
_PRESERVE = (
    "backend/data.sqlite",
    "backend/repo.txt",
    "backend/dbconfig.json",
    "backend/recordings",
    "backend/branding",
)


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


def _http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "rapalle-rmm-updater"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


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

def _apply_zip(ref: str, owner: str, repo: str) -> None:
    """Lädt das ZIP des Refs und kopiert es über das Projektverzeichnis."""
    data = _http_bytes(f"https://codeload.github.com/{owner}/{repo}/zip/{ref}")
    tmpdir = Path(tempfile.mkdtemp(prefix="rmm_update_"))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmpdir)
        roots = [p for p in tmpdir.iterdir() if p.is_dir()]
        if not roots:
            raise RuntimeError("Update-ZIP war leer")
        src_root = roots[0]

        preserve_abs = [(_PROJECT_ROOT / p).resolve() for p in _PRESERVE]

        def _preserved(dst: Path) -> bool:
            d = dst.resolve()
            for p in preserve_abs:
                if d == p or str(d).startswith(str(p) + os.sep):
                    return True
            # .env-Dateien generell nie überschreiben (Konfiguration/Token)
            return dst.name == ".env"

        for src in src_root.rglob("*"):
            rel = src.relative_to(src_root)
            dst = _PROJECT_ROOT / rel
            if _preserved(dst):
                continue
            if src.is_dir():
                dst.mkdir(parents=True, exist_ok=True)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _schedule_restart(delay: float = 1.5) -> None:
    """Startet den Backend-Prozess neu (ersetzt sich selbst -> neuer Code)."""
    def _restart():
        print("[update] Starte Backend mit neuem Code neu (os.execv)...")
        try:
            sys.stdout.flush(); sys.stderr.flush()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
    loop = asyncio.get_event_loop()
    loop.call_later(delay, _restart)


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
    loop = asyncio.get_event_loop()
    gh = await loop.run_in_executor(None, _fetch_github_state)
    ref, desc = _resolve_target(gh, target, tag)

    await loop.run_in_executor(None, _apply_zip, ref, gh["owner"], gh["repo"])

    # Angewendeten Stand merken (Basis für den Commit-Kanal und gegen Schleifen).
    db.set_setting("server_current_commit", ref)
    db.set_setting("server_last_update_target", ref)
    db.add_audit_entry(username, "server.update_applied", details=desc)
    print(f"[update] {desc} eingespielt - Neustart folgt.")
    _schedule_restart()
    return {"ok": True, "applied": desc, "ref": ref,
            "restart": "Backend startet in wenigen Sekunden neu"}


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
    require_admin(user)
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
        **gh,
    }


@router.put("/repo")
async def set_repo(body: RepoBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    url = body.url.strip()
    _parse_owner_repo(url)   # validiert das Format
    _REPO_FILE.write_text(url + "\n", encoding="utf-8")
    db.add_audit_entry(user["username"], "server.update_repo_set", details=url)
    return {"ok": True, "repo_url": url}


@router.post("/run")
async def run_update(body: RunBody, user: dict = Depends(get_current_user)):
    require_admin(user)
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

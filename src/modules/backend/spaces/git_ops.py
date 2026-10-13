# git_ops.py (Modul "spaces")
# ---------------------------
# Git in einem Space-Ordner - der Ordner ist ein ganz normales Repository
# (auch per Relay/Netzlaufwerk mit eigenen Git-Programmen nutzbar).
#
#   POST /api/spaces/git/status    {path}                       Branch, Ahead/Behind, geaenderte Dateien
#   POST /api/spaces/git/log       {path, limit}
#   POST /api/spaces/git/diff      {path, file?}
#   POST /api/spaces/git/init      {path}
#   POST /api/spaces/git/clone     {path, url, name?, username?, token?, branch?}
#   POST /api/spaces/git/commit    {path, message, files?}      ohne files = alles
#   POST /api/spaces/git/fetch     {path}
#   POST /api/spaces/git/pull      {path}
#   POST /api/spaces/git/push      {path}
#   POST /api/spaces/git/sync      {path, message?}             commit (falls noetig) + pull --rebase + push
#   POST /api/spaces/git/branches  {path}
#   POST /api/spaces/git/checkout  {path, branch, create?}
#   POST /api/spaces/git/remote    {path, url?, username?, token?}   Remote/Zugang setzen
#
# Zugangstoken: verschluesselt in data/modules/spaces/git.json, nie in .git/config.
# Uebergeben wird er nur waehrend des Aufrufs per GIT_ASKPASS.

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user

from . import access, store, vfs

router = APIRouter(prefix="/api/spaces/git", tags=["spaces-git"])

GIT_FILE = store.META_DIR / "git.json"
TIMEOUT = 300
_lock = threading.RLock()


def git_bin() -> str:
    g = shutil.which("git")
    if not g:
        raise HTTPException(501, "Git ist auf dem Server nicht installiert (Paket 'git'). "
                                 "Im Docker-Image ist es ab dieser Version enthalten.")
    return g


# ------------------------------------------------------------------ Zugangsdaten
def _fernet():
    from cryptography.fernet import Fernet
    from app.config import JWT_SECRET
    key = base64.urlsafe_b64encode(hashlib.sha256(("spaces-git:" + JWT_SECRET).encode()).digest())
    return Fernet(key)


def _creds_all() -> dict:
    try:
        return json.loads(GIT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _creds_key(t: vfs.Target) -> str:
    return f"{t.space_id}:{t.space_rel}"


def get_creds(t: vfs.Target) -> dict:
    c = _creds_all().get(_creds_key(t)) or {}
    tok = ""
    if c.get("token"):
        try:
            tok = _fernet().decrypt(c["token"].encode()).decode()
        except Exception:
            tok = ""
    return {"username": c.get("username", ""), "token": tok}


def set_creds(t: vfs.Target, username: str | None, token: str | None) -> None:
    with _lock:
        data = _creds_all()
        c = data.get(_creds_key(t)) or {}
        if username is not None:
            c["username"] = username
        if token is not None:
            c["token"] = _fernet().encrypt(token.encode()).decode() if token else ""
        data[_creds_key(t)] = c
        GIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = GIT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(GIT_FILE)


# ------------------------------------------------------------------ Ausfuehren
def _askpass_script() -> str:
    d = store.META_DIR / "bin"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "askpass.sh"
    body = '#!/bin/sh\ncase "$1" in\n  *[Uu]sername*) printf "%s" "$RMM_GIT_USER" ;;\n  *) printf "%s" "$RMM_GIT_TOKEN" ;;\nesac\n'
    if not p.exists() or p.read_text() != body:
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def run(args: list[str], cwd: Path, creds: dict | None = None, user: dict | None = None,
        check: bool = True, timeout: int = TIMEOUT) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
                "GIT_AUTHOR_NAME": (user or {}).get("display_name") or (user or {}).get("username") or "RMM",
                "GIT_AUTHOR_EMAIL": f"{(user or {}).get('username', 'rmm')}@rmm.local"})
    env["GIT_COMMITTER_NAME"], env["GIT_COMMITTER_EMAIL"] = env["GIT_AUTHOR_NAME"], env["GIT_AUTHOR_EMAIL"]
    if creds and (creds.get("token") or creds.get("username")):
        env.update({"GIT_ASKPASS": _askpass_script(), "RMM_GIT_USER": creds.get("username") or "git",
                    "RMM_GIT_TOKEN": creds.get("token") or ""})
    try:
        r = subprocess.run([git_bin(), "-c", "core.quotepath=off", "-c", "safe.directory=*", *args],
                           cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"git {args[0]}: Zeitüberschreitung")
    out = (r.stdout or "") + (r.stderr or "")
    if creds and creds.get("token"):
        out = out.replace(creds["token"], "***")
    if check and r.returncode != 0:
        raise HTTPException(400, f"git {args[0]} fehlgeschlagen:\n{out.strip()[-2000:]}")
    return out.strip()


# ------------------------------------------------------------------ Ziel
def _target(user, path: str, need: int = access.WRITE) -> vfs.Target:
    try:
        t = vfs.resolve(user, [p for p in path.split("/") if p])
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "Ordner nicht gefunden")
    if t.virtual is not None:
        raise HTTPException(400, "Bitte einen Ordner innerhalb eines Space wählen")
    if t.level < need:
        raise HTTPException(403, "Keine Schreibrechte in diesem Space")
    return t


def _repo(user, path: str, need: int = access.WRITE) -> tuple[vfs.Target, Path]:
    t = _target(user, path, need)
    p = t.path
    if not (p / ".git").exists():
        raise HTTPException(400, "Dieser Ordner ist kein Git-Repository")
    return t, p


def _clean_url(url: str) -> str:
    """Zugangsdaten aus der URL entfernen (gehoeren in den Zugangsspeicher)."""
    return re.sub(r"^(https?://)[^/@]+@", r"\1", url.strip())


def status_of(p: Path) -> dict:
    raw = run(["status", "--porcelain=v1", "-b"], p, check=False)
    lines = raw.splitlines()
    head = lines[0] if lines and lines[0].startswith("##") else ""
    m = re.match(r"## (?:No commits yet on )?([^.\s]+)(?:\.\.\.(\S+))?(?: \[(.*)\])?", head)
    ahead = behind = 0
    if m and m.group(3):
        a = re.search(r"ahead (\d+)", m.group(3)); b = re.search(r"behind (\d+)", m.group(3))
        ahead, behind = int(a.group(1)) if a else 0, int(b.group(1)) if b else 0
    files = [{"code": ln[:2], "file": ln[3:]} for ln in lines[1:] if ln.strip()]
    remote = run(["remote", "get-url", "origin"], p, check=False)
    return {"branch": m.group(1) if m else "", "upstream": m.group(2) if m else None, "ahead": ahead,
            "behind": behind, "files": files, "clean": not files,
            "remote": remote if remote and "error" not in remote.lower() and "fatal" not in remote.lower() else ""}


# ------------------------------------------------------------------ Routen
class GitBody(BaseModel):
    path: str
    message: str | None = None
    files: list[str] | None = None
    file: str | None = None
    url: str | None = None
    name: str | None = None
    username: str | None = None
    token: str | None = None
    branch: str | None = None
    create: bool = False
    limit: int = 50


@router.get("/available")
def available(user: dict = Depends(get_current_user)):
    g = shutil.which("git")
    ver = ""
    if g:
        try:
            ver = subprocess.run([g, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            pass
    return {"available": bool(g), "version": ver}


@router.post("/status")
def git_status(b: GitBody, user: dict = Depends(get_current_user)):
    t = _target(user, b.path, access.READ)
    p = t.path
    if not (p / ".git").exists():
        return {"repo": False}
    st = status_of(p)
    c = get_creds(t)
    return {"repo": True, **st, "has_token": bool(c["token"]), "username": c["username"],
            "writable": t.level >= access.WRITE}


@router.post("/log")
def git_log(b: GitBody, user: dict = Depends(get_current_user)):
    _, p = _repo(user, b.path, access.READ)
    raw = run(["log", f"-n{max(1, min(b.limit, 500))}", "--date=iso-strict",
               "--pretty=format:%H%x1f%an%x1f%ad%x1f%s"], p, check=False)
    out = []
    for ln in raw.splitlines():
        f = ln.split("\x1f")
        if len(f) == 4:
            out.append({"hash": f[0], "author": f[1], "date": f[2], "subject": f[3]})
    return {"commits": out}


@router.post("/diff")
def git_diff(b: GitBody, user: dict = Depends(get_current_user)):
    _, p = _repo(user, b.path, access.READ)
    args = ["diff", "HEAD", "--stat", "--patch", "--no-color"]
    if b.file:
        args += ["--", b.file]
    out = run(args, p, check=False)
    return {"diff": out[:400_000]}


@router.post("/init")
def git_init(b: GitBody, user: dict = Depends(get_current_user)):
    t = _target(user, b.path)
    p = t.path
    p.mkdir(parents=True, exist_ok=True)
    if (p / ".git").exists():
        raise HTTPException(400, "Ist bereits ein Git-Repository")
    out = run(["init", "-b", b.branch or "main"], p, user=user)
    return {"ok": True, "output": out}


@router.post("/clone")
def git_clone(b: GitBody, user: dict = Depends(get_current_user)):
    if not b.url:
        raise HTTPException(400, "URL fehlt")
    parent = _target(user, b.path)
    if not parent.path.is_dir():
        raise HTTPException(400, "Zielordner existiert nicht")
    url = _clean_url(b.url)
    name = store.safe_segment(b.name or re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]) or "repo")
    dest = parent.path / name
    if dest.exists():
        raise HTTPException(409, f"„{name}“ existiert bereits")
    creds = {"username": b.username or "", "token": b.token or ""}
    args = ["clone", "--progress"]
    if b.branch:
        args += ["--branch", b.branch]
    out = run(args + [url, name], parent.path, creds=creds, user=user, timeout=900)
    sub = vfs.Target(name=name, space_id=parent.space_id, root=parent.root, rel=parent.rel + [name],
                     level=parent.level, root_rel=parent.root_rel)
    if b.token or b.username:
        set_creds(sub, b.username or "", b.token or "")
    return {"ok": True, "name": name, "output": out[-3000:]}


@router.post("/remote")
def git_remote(b: GitBody, user: dict = Depends(get_current_user)):
    t, p = _repo(user, b.path)
    if b.url is not None:
        url = _clean_url(b.url)
        has = run(["remote"], p, check=False).split()
        if url:
            run(["remote", "set-url" if "origin" in has else "add", "origin", url], p)
        elif "origin" in has:
            run(["remote", "remove", "origin"], p)
    if b.username is not None or b.token is not None:
        set_creds(t, b.username, b.token)
    return {"ok": True, **status_of(p)}


@router.post("/commit")
def git_commit(b: GitBody, user: dict = Depends(get_current_user)):
    _, p = _repo(user, b.path)
    if not (b.message or "").strip():
        raise HTTPException(400, "Commit-Nachricht fehlt")
    if b.files:
        run(["add", "--", *b.files], p)
    else:
        run(["add", "-A"], p)
    out = run(["commit", "-m", b.message.strip()], p, user=user)
    return {"ok": True, "output": out, **status_of(p)}


def _remote_op(op: list[str], user, path: str, timeout: int = 600):
    t, p = _repo(user, path)
    out = run(op, p, creds=get_creds(t), user=user, timeout=timeout)
    return {"ok": True, "output": out[-3000:], **status_of(p)}


@router.post("/fetch")
def git_fetch(b: GitBody, user: dict = Depends(get_current_user)):
    return _remote_op(["fetch", "--prune", "origin"], user, b.path)


@router.post("/pull")
def git_pull(b: GitBody, user: dict = Depends(get_current_user)):
    return _remote_op(["pull", "--rebase", "--autostash", "origin"], user, b.path)


@router.post("/push")
def git_push(b: GitBody, user: dict = Depends(get_current_user)):
    t, p = _repo(user, b.path)
    br = status_of(p)["branch"] or "main"
    out = run(["push", "-u", "origin", br], p, creds=get_creds(t), user=user, timeout=600)
    return {"ok": True, "output": out[-3000:], **status_of(p)}


@router.post("/sync")
def git_sync(b: GitBody, user: dict = Depends(get_current_user)):
    """Aenderungen committen (falls vorhanden), holen (rebase) und hochladen."""
    t, p = _repo(user, b.path)
    log = []
    st = status_of(p)
    if not st["clean"]:
        run(["add", "-A"], p)
        log.append(run(["commit", "-m", (b.message or "").strip() or "Sync aus dem RMM"], p, user=user))
    creds = get_creds(t)
    if st["remote"]:
        log.append(run(["pull", "--rebase", "--autostash", "origin", st["branch"] or "main"], p, creds=creds,
                       user=user, check=False, timeout=600))
        log.append(run(["push", "-u", "origin", status_of(p)["branch"] or "main"], p, creds=creds, user=user, timeout=600))
    return {"ok": True, "output": "\n".join(x for x in log if x)[-4000:], **status_of(p)}


@router.post("/branches")
def git_branches(b: GitBody, user: dict = Depends(get_current_user)):
    _, p = _repo(user, b.path, access.READ)
    # for-each-ref statt "branch --format": Ref-Namen enthalten nie Leerzeichen,
    # deshalb ist " " ein sicheres Trennzeichen.
    raw = run(["for-each-ref", "--format=%(HEAD) %(refname)", "refs/heads", "refs/remotes"], p, check=False)
    out = []
    for ln in raw.splitlines():
        head, ref = ln[:1], ln[2:].strip()      # Spalte 1: "*" oder " ", dann Leerzeichen, dann Ref
        if not ref or ref.endswith("/HEAD"):
            continue
        remote = ref.startswith("refs/remotes/")
        name = ref.removeprefix("refs/heads/").removeprefix("refs/remotes/")
        out.append({"name": name, "current": head == "*", "remote": remote})
    return {"branches": out}


@router.post("/checkout")
def git_checkout(b: GitBody, user: dict = Depends(get_current_user)):
    _, p = _repo(user, b.path)
    if not b.branch:
        raise HTTPException(400, "Branch fehlt")
    br = b.branch.removeprefix("origin/")
    args = ["checkout", "-b", br] if b.create else ["checkout", br]
    out = run(args, p, user=user)
    return {"ok": True, "output": out, **status_of(p)}

# store.py (Modul "spaces")
# -------------------------
# Wo liegen die Daten, welche Spaces gibt es, welche Freigaben?
#
# Dateien (echte Ordner, im Container per Bind-Mount dauerhaft):
#   backend/storage/spaces/personal/<user_id>/   persoenlicher Space je Benutzer
#   backend/storage/spaces/teams/<slug>/         Teamspaces
#   backend/storage/spaces/public/<slug>/        Public Spaces
#
# Verwaltungsdaten (backend/data/modules/spaces/):
#   spaces.json   Team- und Public Spaces mit Mitgliedern
#   shares.json   Freigaben (an Benutzer/Gruppen, per Link, Chat-/Ticket-Anhaenge)
#   git.json      Zugangsdaten fuer Git-Remotes (Token verschluesselt)
#
# Space-IDs:  "u:<user_id>"  persoenlich,  "t:<slug>"  Team,  "p:<slug>"  oeffentlich

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from app.paths import MODULES_DATA_DIR, STORAGE_DIR

SPACES_ROOT = STORAGE_DIR / "spaces"
PERSONAL_DIR = SPACES_ROOT / "personal"
TEAMS_DIR = SPACES_ROOT / "teams"
PUBLIC_DIR = SPACES_ROOT / "public"
META_DIR = MODULES_DATA_DIR / "spaces"
SPACES_FILE = META_DIR / "spaces.json"
SHARES_FILE = META_DIR / "shares.json"

_lock = threading.RLock()


def now_ms() -> int:
    return int(time.time() * 1000)


def ensure_dirs() -> None:
    for d in (PERSONAL_DIR, TEAMS_DIR, PUBLIC_DIR, META_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ JSON
def _load(path: Path, default: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else json.loads(json.dumps(default))
    except FileNotFoundError:
        return json.loads(json.dumps(default))
    except Exception as exc:
        print(f"[spaces] {path.name} unlesbar ({exc}) - starte leer")
        return json.loads(json.dumps(default))


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ Namen
_SLUG = re.compile(r"[^a-z0-9._-]+")


def slugify(name: str) -> str:
    s = name.strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = _SLUG.sub("-", s).strip("-.")
    return s[:60] or "space"


def safe_segment(seg: str) -> str:
    """Ein einzelner Datei-/Ordnername - darf niemals selbst ein Pfad sein."""
    seg = (seg or "").strip()
    if seg in ("", ".", "..") or "/" in seg or "\\" in seg or seg.startswith("~") or "\x00" in seg:
        raise PermissionError(f"Ungültiger Name: {seg!r}")
    return seg


# ------------------------------------------------------------------ Spaces
def personal_id(user_id: str) -> str:
    return f"u:{user_id}"


def space_dir(space_id: str) -> Path:
    kind, _, key = space_id.partition(":")
    key = "".join(c for c in key if c.isalnum() or c in "-_.")
    if not key:
        raise PermissionError("Ungültiger Space")
    base = {"u": PERSONAL_DIR, "t": TEAMS_DIR, "p": PUBLIC_DIR}.get(kind)
    if base is None:
        raise PermissionError("Ungültiger Space")
    d = base / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_spaces() -> list[dict]:
    """Team- und Public Spaces (persoenliche Spaces sind implizit)."""
    with _lock:
        return list(_load(SPACES_FILE, {"spaces": {}})["spaces"].values())


def get_space(space_id: str) -> dict | None:
    if space_id.startswith("u:"):
        uid = space_id[2:]
        from app import db
        u = db.get_user_by_id(uid)
        if not u:
            return None
        return {"id": space_id, "kind": "personal", "name": u.get("display_name") or u["username"],
                "username": u["username"], "owner": uid, "members": []}
    with _lock:
        return _load(SPACES_FILE, {"spaces": {}})["spaces"].get(space_id)


def create_space(kind: str, name: str, owner_id: str, members: list[dict] | None = None,
                 description: str = "") -> dict:
    name = name.strip()[:80]
    if not name:
        raise ValueError("Name fehlt")
    prefix = {"team": "t", "public": "p"}[kind]
    with _lock:
        data = _load(SPACES_FILE, {"spaces": {}})
        if any(s["kind"] == kind and s["name"].lower() == name.lower() for s in data["spaces"].values()):
            raise ValueError(f"Es gibt schon einen {'Teamspace' if kind == 'team' else 'Public Space'} „{name}“")
        base = slugify(name)
        slug, n = base, 2
        while f"{prefix}:{slug}" in data["spaces"] or (
                (TEAMS_DIR if kind == "team" else PUBLIC_DIR) / slug).exists():
            slug, n = f"{base}-{n}", n + 1
        sid = f"{prefix}:{slug}"
        sp = {"id": sid, "kind": kind, "name": name, "description": description[:300],
              "owner": owner_id, "members": members or [], "created": now_ms()}
        if kind == "team" and not any(m.get("type") == "user" and m.get("id") == owner_id for m in sp["members"]):
            sp["members"].insert(0, {"type": "user", "id": owner_id, "role": "admin"})
        data["spaces"][sid] = sp
        _save(SPACES_FILE, data)
    space_dir(sid)
    return sp


def update_space(space_id: str, fields: dict) -> dict:
    with _lock:
        data = _load(SPACES_FILE, {"spaces": {}})
        sp = data["spaces"].get(space_id)
        if not sp:
            raise KeyError(space_id)
        if "name" in fields and fields["name"]:
            new = fields["name"].strip()[:80]
            if any(s["id"] != space_id and s["kind"] == sp["kind"] and s["name"].lower() == new.lower()
                   for s in data["spaces"].values()):
                raise ValueError(f"Name „{new}“ ist schon vergeben")
            sp["name"] = new
        if "description" in fields and fields["description"] is not None:
            sp["description"] = fields["description"][:300]
        if "members" in fields and fields["members"] is not None:
            sp["members"] = [{"type": m["type"], "id": m["id"], "role": m.get("role", "member")}
                             for m in fields["members"] if m.get("type") in ("user", "group") and m.get("id")]
        _save(SPACES_FILE, data)
        return sp


def delete_space(space_id: str) -> None:
    with _lock:
        data = _load(SPACES_FILE, {"spaces": {}})
        if data["spaces"].pop(space_id, None) is None:
            raise KeyError(space_id)
        _save(SPACES_FILE, data)
        sh = _load(SHARES_FILE, {"shares": {}})
        sh["shares"] = {k: v for k, v in sh["shares"].items() if v.get("space_id") != space_id}
        _save(SHARES_FILE, sh)
    d = space_dir(space_id)
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ Freigaben
def list_shares() -> list[dict]:
    with _lock:
        return list(_load(SHARES_FILE, {"shares": {}})["shares"].values())


def get_share(share_id: str) -> dict | None:
    with _lock:
        return _load(SHARES_FILE, {"shares": {}})["shares"].get(share_id)


def share_by_token(token: str) -> dict | None:
    if not token:
        return None
    for s in list_shares():
        if s.get("token") and secrets.compare_digest(s["token"], token):
            return s
    return None


def create_share(*, space_id: str, path: str, owner: str, kind: str, users=None, groups=None,
                 write: bool = False, name: str = "", expires: int | None = None) -> dict:
    """kind: "users" (an Benutzer/Gruppen), "link" (jeder Angemeldete mit Link),
    "attachment" (Bild aus Chat/Ticket - wie Link, aber nicht in der Freigabe-Liste)."""
    sh_id = secrets.token_hex(8)
    item = {
        "id": sh_id, "space_id": space_id, "path": path.strip("/"), "owner": owner, "kind": kind,
        "users": list(users or []), "groups": list(groups or []), "write": bool(write) and kind == "users",
        "token": secrets.token_urlsafe(18) if kind in ("link", "attachment") else "",
        "name": (name or Path(path).name or "Freigabe")[:120], "created": now_ms(), "expires": expires,
    }
    with _lock:
        data = _load(SHARES_FILE, {"shares": {}})
        data["shares"][sh_id] = item
        _save(SHARES_FILE, data)
    return item


def update_share(share_id: str, fields: dict) -> dict:
    with _lock:
        data = _load(SHARES_FILE, {"shares": {}})
        sh = data["shares"].get(share_id)
        if not sh:
            raise KeyError(share_id)
        for k in ("users", "groups", "write", "name", "expires"):
            if k in fields and fields[k] is not None:
                sh[k] = fields[k]
        if sh["kind"] != "users":
            sh["write"] = False
        _save(SHARES_FILE, data)
        return sh


def delete_share(share_id: str) -> None:
    with _lock:
        data = _load(SHARES_FILE, {"shares": {}})
        data["shares"].pop(share_id, None)
        _save(SHARES_FILE, data)


def move_share_paths(space_id: str, old: str, new: str) -> None:
    """Nach Umbenennen/Verschieben: Freigaben zeigen weiter auf das richtige Ziel."""
    old, new = old.strip("/"), new.strip("/")
    with _lock:
        data = _load(SHARES_FILE, {"shares": {}})
        hit = False
        for sh in data["shares"].values():
            if sh["space_id"] != space_id:
                continue
            p = sh["path"]
            if p == old or p.startswith(old + "/"):
                sh["path"] = new + p[len(old):]
                hit = True
        if hit:
            _save(SHARES_FILE, data)


def drop_shares_under(space_id: str, path: str) -> None:
    """Nach dem Loeschen: Freigaben auf nicht mehr vorhandene Ziele entfernen."""
    path = path.strip("/")
    with _lock:
        data = _load(SHARES_FILE, {"shares": {}})
        keep = {k: v for k, v in data["shares"].items()
                if not (v["space_id"] == space_id and (v["path"] == path or v["path"].startswith(path + "/")))}
        if len(keep) != len(data["shares"]):
            data["shares"] = keep
            _save(SHARES_FILE, data)


# ------------------------------------------------------------------ Groesse
def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


# ------------------------------------------------------------------ Migration
def migrate_legacy_storage() -> None:
    """
    Frueher war storage/spaces EIN gemeinsamer Relay-Ordner ("Storage"). Alles,
    was dort lose liegt (also nicht personal/teams/public), wandert in den
    Public Space "Storage" - nichts geht verloren.
    """
    ensure_dirs()
    loose = [c for c in SPACES_ROOT.iterdir() if c.name not in ("personal", "teams", "public")]
    if not loose:
        return
    sid = "p:storage"
    with _lock:
        data = _load(SPACES_FILE, {"spaces": {}})
        if sid not in data["spaces"]:
            data["spaces"][sid] = {"id": sid, "kind": "public", "name": "Storage",
                                   "description": "Inhalt des früheren gemeinsamen Relay-Ordners „Storage“.",
                                   "owner": "", "members": [], "created": now_ms()}
            _save(SPACES_FILE, data)
    target = space_dir(sid)
    for c in loose:
        dest = target / c.name
        n = 2
        while dest.exists():
            dest = target / f"{c.stem} ({n}){c.suffix}"
            n += 1
        shutil.move(str(c), str(dest))
    print(f"[spaces] {len(loose)} Einträge aus dem alten Storage-Ordner in den Public Space „Storage“ verschoben")

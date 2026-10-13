# vfs.py (Modul "spaces")
# -----------------------
# Ein virtueller Verzeichnisbaum - genau so sieht ein Benutzer seine Spaces
# im Explorer UND im Relay (WebDAV/FTP/SFTP). Beide rufen dieselben Funktionen,
# dadurch gelten ueberall dieselben Rechte.
#
#   Mein Space/                         eigener Space (get_user_space)
#   Teams/<Teamspace>/                  Teamspaces, in denen man Mitglied ist
#   Öffentlich/<Public Space>/          Public Spaces (use_public_space)
#   Mit mir geteilt/<Freigabe>          Freigaben anderer an mich
#   Alle Spaces/Persönlich|Teams|Öffentlich/<Space>/   (see_all_spaces / edit_all_spaces)
#
# Pfade sind Listen von Segmenten ohne den Bereichsnamen "Spaces".
# Fehler: PermissionError (keine Rechte), FileNotFoundError, NotADirectoryError.

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app import db

from . import access, store

MY, TEAMS, PUBLIC, SHARED, ALL = "Mein Space", "Teams", "Öffentlich", "Mit mir geteilt", "Alle Spaces"
ALL_PERSONAL, ALL_TEAMS, ALL_PUBLIC = "Persönlich", "Teams", "Öffentlich"


@dataclass
class Target:
    virtual: list | None = None          # Liste von Eintraegen, wenn es ein virtueller Ordner ist
    name: str = ""
    space_id: str = ""
    root: Path | None = None             # Wurzel des Zugriffs (Space-Ordner oder freigegebenes Ziel)
    rel: list = field(default_factory=list)
    level: int = access.NONE
    root_rel: str = ""                   # Pfad der Wurzel relativ zum Space (fuer Freigaben)

    @property
    def path(self) -> Path:
        p = self.root
        for seg in self.rel:
            p = p / store.safe_segment(seg)
        rp, rr = p.resolve(), self.root.resolve()
        try:
            rp.relative_to(rr)
        except ValueError:
            raise PermissionError("Pfad außerhalb des Space")
        return p

    @property
    def space_rel(self) -> str:
        """Pfad relativ zum Space-Ordner (fuer Freigaben, Git, Audit)."""
        return "/".join([x for x in [self.root_rel] + list(self.rel) if x])


def _vdir(name: str) -> dict:
    return {"name": name, "is_dir": True, "size": 0, "mtime": 0, "virtual": True}


def _entry(p: Path) -> dict:
    try:
        st = p.stat()
        isdir = p.is_dir()
        return {"name": p.name, "is_dir": isdir, "size": 0 if isdir else int(st.st_size), "mtime": int(st.st_mtime * 1000)}
    except OSError:
        return {"name": p.name, "is_dir": False, "size": 0, "mtime": 0}


def _user_label(uid: str) -> str:
    u = db.get_user_by_id(uid) if uid else None
    return (u or {}).get("username") or "?"


def _unique(names: list[tuple[str, object]]) -> dict[str, object]:
    """Doppelte Anzeigenamen mit (2), (3) ... eindeutig machen."""
    out: dict[str, object] = {}
    for name, obj in names:
        n, i = name, 2
        while n in out:
            n, i = f"{name} ({i})", i + 1
        out[n] = obj
    return out


# ------------------------------------------------------------------ Kataloge
def _teams_for(user, gids) -> dict[str, dict]:
    sps = [s for s in store.list_spaces() if s["kind"] == "team" and access.member_role(user, s, gids)]
    return _unique([(s["name"], s) for s in sorted(sps, key=lambda s: s["name"].lower())])


def _publics() -> dict[str, dict]:
    sps = [s for s in store.list_spaces() if s["kind"] == "public"]
    return _unique([(s["name"], s) for s in sorted(sps, key=lambda s: s["name"].lower())])


def _shared_for(user, gids) -> dict[str, dict]:
    items = []
    for sh in store.list_shares():
        if sh.get("owner") == user["id"] or not access.share_applies(user, sh, gids):
            continue
        items.append((f"{sh['name']} ({_user_label(sh['owner'])})", sh))
    return _unique(sorted(items, key=lambda x: x[0].lower()))


def _all_personal() -> dict[str, dict]:
    out = []
    for u in db.list_users():
        out.append((u["username"], store.get_space(store.personal_id(u["id"]))))
    return _unique(sorted(out, key=lambda x: x[0].lower()))


# ------------------------------------------------------------------ Aufloesen
def _space_target(user, sp: dict, rest: list, gids, name: str) -> Target:
    lv = access.level(user, sp, gids)
    if lv < access.READ:
        raise PermissionError("Kein Zugriff auf diesen Space")
    return Target(name=name, space_id=sp["id"], root=store.space_dir(sp["id"]), rel=rest, level=lv)


def resolve(user, parts: list[str]) -> Target:
    parts = [p for p in parts if p not in ("", ".")]
    gids = access.group_ids(user)
    see_all = access.has(user, "see_all_spaces") or access.has(user, "edit_all_spaces")
    if not parts:
        roots = []
        if access.has(user, "get_user_space"):
            roots.append(_vdir(MY))
        roots.append(_vdir(TEAMS))
        if access.has(user, "use_public_space"):
            roots.append(_vdir(PUBLIC))
        roots.append(_vdir(SHARED))
        if see_all:
            roots.append(_vdir(ALL))
        return Target(virtual=roots, name="Spaces")
    head, rest = parts[0], parts[1:]
    if head == MY:
        if not access.has(user, "get_user_space"):
            raise PermissionError("Recht „Eigenen Space benutzen“ fehlt")
        sp = store.get_space(store.personal_id(user["id"]))
        return _space_target(user, sp, rest, gids, MY)
    if head == TEAMS:
        teams = _teams_for(user, gids)
        if not rest:
            return Target(virtual=[_vdir(n) for n in teams], name=TEAMS)
        sp = teams.get(rest[0])
        if not sp:
            raise FileNotFoundError(rest[0])
        return _space_target(user, sp, rest[1:], gids, rest[0])
    if head == PUBLIC:
        if not access.has(user, "use_public_space") and not see_all:
            raise PermissionError("Recht „Public Spaces benutzen“ fehlt")
        pubs = _publics()
        if not rest:
            return Target(virtual=[_vdir(n) for n in pubs], name=PUBLIC)
        sp = pubs.get(rest[0])
        if not sp:
            raise FileNotFoundError(rest[0])
        return _space_target(user, sp, rest[1:], gids, rest[0])
    if head == SHARED:
        shares = _shared_for(user, gids)
        if not rest:
            out = []
            for n, sh in shares.items():
                p = store.space_dir(sh["space_id"]) / sh["path"]
                e = _entry(p) if p.exists() else {"is_dir": False, "size": 0, "mtime": 0}
                out.append({**e, "name": n, "share_id": sh["id"]})
            return Target(virtual=out, name=SHARED)
        sh = shares.get(rest[0])
        if not sh:
            raise FileNotFoundError(rest[0])
        root = store.space_dir(sh["space_id"])
        for seg in [s for s in sh["path"].split("/") if s]:
            root = root / store.safe_segment(seg)
        if not root.exists():
            raise FileNotFoundError(rest[0])
        if root.is_file() and rest[1:]:
            raise NotADirectoryError(rest[0])
        return Target(name=rest[0], space_id=sh["space_id"], root=root, rel=rest[1:],
                      level=access.WRITE if sh.get("write") else access.READ, root_rel=sh["path"])
    if head == ALL:
        if not see_all:
            raise PermissionError("Recht „Alle Spaces sehen“ fehlt")
        if not rest:
            return Target(virtual=[_vdir(ALL_PERSONAL), _vdir(ALL_TEAMS), _vdir(ALL_PUBLIC)], name=ALL)
        cat = {ALL_PERSONAL: _all_personal, ALL_TEAMS: lambda: _unique(
            [(s["name"], s) for s in store.list_spaces() if s["kind"] == "team"]), ALL_PUBLIC: _publics}.get(rest[0])
        if cat is None:
            raise FileNotFoundError(rest[0])
        catalog = cat()
        if len(rest) == 1:
            return Target(virtual=[_vdir(n) for n in catalog], name=rest[0])
        sp = catalog.get(rest[1])
        if not sp:
            raise FileNotFoundError(rest[1])
        return _space_target(user, sp, rest[2:], gids, rest[1])
    raise FileNotFoundError(head)


def resolve_space_path(user, space_id: str, rel: str) -> Target:
    """Direkter Zugriff ueber Space-ID + Pfad (Notizheft, Musik, Anhaenge, Git)."""
    sp = store.get_space(space_id)
    if not sp:
        raise FileNotFoundError(space_id)
    return _space_target(user, sp, [s for s in rel.split("/") if s], access.group_ids(user), sp["name"])


# ------------------------------------------------------------------ Kontingent
def _check_quota(t: Target, add: int) -> None:
    if not t.space_id.startswith("u:"):
        return
    try:
        from app import modules
        mb = float(modules.setting("spaces", "quota_mb", 0) or 0)
    except Exception:
        mb = 0
    if mb <= 0:
        return
    used = store.dir_size(store.space_dir(t.space_id))
    if used + add > mb * 1024 * 1024:
        raise PermissionError(f"Speicherplatz voll (Grenze {mb:g} MB je persönlichem Space)")


def _need(t: Target, lv: int) -> None:
    if t.virtual is not None:
        raise PermissionError("Hier kann nichts abgelegt werden - bitte einen Space öffnen")
    if t.level < lv:
        raise PermissionError("Nur Lesezugriff" if t.level >= access.READ else "Kein Zugriff")


# ------------------------------------------------------------------ Operationen
def listdir(user, parts: list[str]) -> list[dict]:
    t = resolve(user, parts)
    if t.virtual is not None:
        return t.virtual
    p = t.path
    if not p.exists():
        raise FileNotFoundError(p.name)
    if not p.is_dir():
        raise NotADirectoryError(p.name)
    return [_entry(c) for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))]


def stat(user, parts: list[str]) -> dict | None:
    try:
        t = resolve(user, parts)
    except FileNotFoundError:
        return None
    if t.virtual is not None:
        return {**_vdir(t.name)}
    p = t.path
    if not p.exists():
        return None
    e = _entry(p)
    if not t.rel:
        e["name"] = t.name
    e["writable"] = t.level >= access.WRITE
    return e


def file_path(user, parts: list[str]) -> Path:
    t = resolve(user, parts)
    _need(t, access.READ)
    p = t.path
    if not p.is_file():
        raise FileNotFoundError(p.name)
    return p


def read(user, parts: list[str]) -> bytes:
    return file_path(user, parts).read_bytes()


def write(user, parts: list[str], data: bytes) -> None:
    t = resolve(user, parts)
    _need(t, access.WRITE)
    p = t.path
    if p.is_dir():
        raise IsADirectoryError(p.name)
    old = p.stat().st_size if p.exists() else 0
    _check_quota(t, len(data or b"") - old)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data or b"")


def mkdir(user, parts: list[str]) -> None:
    t = resolve(user, parts)
    _need(t, access.WRITE)
    t.path.mkdir(parents=True, exist_ok=True)


def delete(user, parts: list[str]) -> None:
    t = resolve(user, parts)
    _need(t, access.WRITE)
    if not t.rel:
        raise PermissionError("Der Space selbst kann hier nicht gelöscht werden")
    p = t.path
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    else:
        raise FileNotFoundError(p.name)
    store.drop_shares_under(t.space_id, t.space_rel)


def move(user, src: list[str], dst: list[str]) -> None:
    a, b = resolve(user, src), resolve(user, dst)
    _need(a, access.WRITE)
    _need(b, access.WRITE)
    if not a.rel:
        raise PermissionError("Der Space selbst kann nicht verschoben werden")
    if a.space_id != b.space_id:
        # Zwischen Spaces: kopieren + loeschen (Kontingent des Ziels pruefen)
        size = store.dir_size(a.path) if a.path.is_dir() else a.path.stat().st_size
        _check_quota(b, size)
    pa, pb = a.path, b.path
    if not pa.exists():
        raise FileNotFoundError(pa.name)
    if pb.exists():
        raise FileExistsError(pb.name)
    pb.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pa), str(pb))
    if a.space_id == b.space_id:
        store.move_share_paths(a.space_id, a.space_rel, b.space_rel)
    else:
        store.drop_shares_under(a.space_id, a.space_rel)

"""
relay_storage.py
----------------
Zwei Ordner, die dem SERVER gehoeren und nicht einem Client:

  Storage      Ablage fuer alles, was man einfach irgendwo hinlegen will -
               Installer, Skripte, Notizen, Backups. Nur angemeldet erreichbar.

  Deployment   Dasselbe, aber zusaetzlich oeffentlich abrufbar unter
               /deployment/<datei>. Damit laesst sich ein Bild, eine Datei
               oder ein Skript per Link weitergeben, ohne dafuer erst einen
               Webserver aufzusetzen. Genau dafuer ist der Ordner gedacht -
               was hier liegt, ist (bei eingeschalteter Freigabe) fuer jeden
               mit dem Link sichtbar.

Beide Ordner tauchen im Relay auf derselben Ebene auf wie die Clients und
funktionieren dadurch automatisch ueber WebDAV, FTP und SFTP mit.

Wo liegen die Daten?
--------------------
Unter backend/relay/. Dieses Verzeichnis ist im Container ein Bind-Mount
(siehe docker-compose.yml) und ueberlebt damit Neustarts und Updates.

Sicherheit
----------
Jeder Pfad wird auf den jeweiligen Basisordner festgenagelt (siehe resolve()).
Ein Pfad mit '..' oder ein absoluter Pfad landet nie ausserhalb - das ist
wichtig, weil hier echte Dateisystem-Operationen stattfinden und Teile davon
oeffentlich erreichbar sind.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# backend/relay/
_BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = _BACKEND_DIR / "relay"

# Anzeigename -> Unterordner. Der Anzeigename ist das, was im Explorer,
# im Netzlaufwerk und in FTP/SFTP zu sehen ist.
SECTIONS = {
    "Storage": "storage",
    "Deployment": "deployment",
}

# Wer darf schreiben?
#   Storage    -> 'use_relay' (wer das Relay benutzen darf)
#   Deployment -> zusaetzlich 'manage_settings', denn was hier liegt, kann
#                 oeffentlich abrufbar sein. Lesen darf jeder mit 'use_relay'.
WRITE_PERM = {
    "Storage": "use_relay",
    "Deployment": "manage_settings",
}


def section_of(name: str) -> str | None:
    """Passt dieser Pfadabschnitt auf einen Server-Ordner? Gross-/Kleinschreibung
    egal - Windows-Explorer und FTP-Programme sind da unterschiedlich."""
    n = (name or "").strip().rstrip("/")
    for display in SECTIONS:
        if n.lower() == display.lower():
            return display
    return None


def display_names() -> list[str]:
    return list(SECTIONS.keys())


def base(section: str) -> Path:
    """Basisordner einer Sektion - wird bei Bedarf angelegt."""
    sub = SECTIONS[section]
    p = ROOT / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve(section: str, sub: list[str] | str) -> Path:
    """
    Virtuellen Unterpfad in einen echten Pfad uebersetzen - und dabei
    sicherstellen, dass er INNERHALB des Basisordners bleibt.
    """
    if isinstance(sub, str):
        sub = [p for p in sub.replace("\\", "/").split("/") if p]
    root = base(section).resolve()
    parts: list[str] = []
    for seg in sub:
        seg = (seg or "").strip()
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        # Ein Segment darf niemals selbst ein Pfad sein.
        if "/" in seg or "\\" in seg or seg.startswith("~"):
            raise PermissionError("Ungueltiger Name")
        parts.append(seg)
    target = (root / Path(*parts)).resolve() if parts else root
    try:
        target.relative_to(root)
    except ValueError:
        raise PermissionError("Pfad ausserhalb des Ordners")
    return target


def _entry(p: Path) -> dict:
    try:
        st = p.stat()
        size = 0 if p.is_dir() else int(st.st_size)
        mtime = int(st.st_mtime * 1000)
    except OSError:
        size, mtime = 0, 0
    return {"name": p.name, "is_dir": p.is_dir(), "size": size, "mtime": mtime}


def listdir(section: str, sub: list[str] | str) -> list[dict]:
    target = resolve(section, sub)
    if not target.exists():
        raise FileNotFoundError(target.name)
    if not target.is_dir():
        raise NotADirectoryError(target.name)
    out = [_entry(c) for c in sorted(target.iterdir(),
                                     key=lambda c: (not c.is_dir(), c.name.lower()))]
    return out


def stat_entry(section: str, sub: list[str] | str) -> dict | None:
    target = resolve(section, sub)
    return _entry(target) if target.exists() else None


def read(section: str, sub: list[str] | str) -> bytes:
    target = resolve(section, sub)
    if not target.is_file():
        raise FileNotFoundError(target.name)
    return target.read_bytes()


def write(section: str, sub: list[str] | str, data: bytes) -> None:
    target = resolve(section, sub)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data or b"")


def mkdir(section: str, sub: list[str] | str) -> None:
    resolve(section, sub).mkdir(parents=True, exist_ok=True)


def delete(section: str, sub: list[str] | str) -> None:
    target = resolve(section, sub)
    if target == base(section).resolve():
        raise PermissionError("Der Hauptordner kann nicht geloescht werden")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def move(section: str, src: list[str] | str, dst: list[str] | str) -> None:
    s = resolve(section, src)
    d = resolve(section, dst)
    if s == base(section).resolve():
        raise PermissionError("Der Hauptordner kann nicht verschoben werden")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))


def may_write(user, section: str) -> bool:
    """Darf dieser Benutzer in dieser Sektion schreiben?"""
    from app.auth import user_has_permission, is_super_admin
    if is_super_admin(user):
        return True
    perm = WRITE_PERM.get(section, "use_relay")
    if user_has_permission(user, perm):
        return True
    # 'admin_settings' schliesst 'manage_settings' der Sache nach mit ein.
    if perm == "manage_settings" and user_has_permission(user, "admin_settings"):
        return True
    return False


def may_read(user) -> bool:
    from app.auth import user_has_permission, is_super_admin
    return is_super_admin(user) or user_has_permission(user, "use_relay")

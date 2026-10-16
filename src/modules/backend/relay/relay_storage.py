"""
relay_storage.py
----------------
Zwei Ordner, die dem SERVER gehoeren und nicht einem Client:

  Spaces       Die Spaces des angemeldeten Benutzers (Modul "spaces"): eigener
               Space, Teamspaces, Public Spaces, Freigaben. Wird pro Benutzer
               aufgebaut (virtuell) - siehe src/modules/backend/spaces/vfs.py.
               Frueher gab es hier den gemeinsamen Ordner "Storage"; sein Inhalt
               liegt jetzt im Public Space "Storage".

  Source       Der QUELLCODE des RMM selbst (Projekt-Wurzel). Damit laesst
               sich der Server ueber das Netzlaufwerk bearbeiten - mit dem
               gewohnten Editor statt ueber die Weboberflaeche. Sichtbar nur
               mit 'see_source', schreibbar nur mit 'edit_source'.

  Deployment   (nur mit Modul "deployment") Ablage, zusaetzlich oeffentlich abrufbar unter
               /deployment/<datei>. Damit laesst sich ein Bild, eine Datei
               oder ein Skript per Link weitergeben, ohne dafuer erst einen
               Webserver aufzusetzen. Genau dafuer ist der Ordner gedacht -
               was hier liegt, ist (bei eingeschalteter Freigabe) fuer jeden
               mit dem Link sichtbar.

Beide Ordner tauchen im Relay auf derselben Ebene auf wie die Clients und
funktionieren dadurch automatisch ueber WebDAV, FTP und SFTP mit.

Wo liegen die Daten?
--------------------
Unter src/backend/storage/. Dieses Verzeichnis ist im Container ein Bind-Mount
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
from app.paths import STORAGE_DIR as _STORAGE_DIR, PROJECT_ROOT as _PROJECT_ROOT
ROOT = _STORAGE_DIR

# Anzeigename -> Unterordner. Der Anzeigename ist das, was im Explorer,
# im Netzlaufwerk und in FTP/SFTP zu sehen ist.
SECTIONS = {
    # Virtuell und benutzerabhaengig - wird an das Modul "spaces" weitergereicht.
    "Spaces": None,
    # Virtuell: Websites des Moduls "webserver", an denen der Benutzer mitarbeitet
    "Websites": None,
    "Deployment": "deployment",
    # Source liegt NICHT unter backend/relay/, sondern ist die Projekt-Wurzel
    # selbst (siehe _SPECIAL_BASE). Der Wert hier ist deshalb nur ein Platz-
    # halter, damit die Sektion in allen Schleifen ueber SECTIONS mitlaeuft.
    "Source": None,
}

# Sektionen mit eigenem Basisordner ausserhalb von backend/relay/.
# Die Projekt-Wurzel ist backend/app/relay_storage.py -> drei Ebenen hoch.
_SPECIAL_BASE = {
    "Source": _PROJECT_ROOT,
}

# Wer darf ueberhaupt SEHEN? Fehlt ein Eintrag, reicht 'use_relay'.
# Der Quellcode ist nichts, was jeder Relay-Benutzer sehen soll - deshalb
# dieselbe Berechtigung wie fuer die Source-App im Dashboard.
READ_PERM = {
    "Source": "see_source",
    # Deployment-Ordner nur fuer Leute mit dem Recht "Deployment-Server verwalten"
    # (Modul "deployment"); Super-Admins sehen ihn immer.
    "Deployment": "manage_deployment",
}

# Wer darf schreiben?
#   Storage    -> 'use_relay' (wer das Relay benutzen darf)
#   Deployment -> 'manage_deployment' (Modul "deployment"), denn was hier liegt, kann
#                 oeffentlich abrufbar sein. Lesen darf jeder mit 'use_relay'.
WRITE_PERM = {
    "Deployment": "manage_deployment",
    "Source": "edit_source",
}


# Sektionen, die ein Modul pro Benutzer bereitstellt: Name -> (Modul, Datei).
_VIRTUAL = {"Spaces": ("spaces", "vfs"), "Websites": ("webserver", "site_vfs")}


def _vfs(section: str):
    """Virtuelles Dateisystem einer Modul-Sektion - None, wenn das Modul aus ist."""
    mod = _VIRTUAL.get(section)
    if not mod:
        return None
    try:
        from app import modules
        return modules.use(*mod) if modules.is_enabled(mod[0]) else None
    except Exception:
        return None


def is_virtual(section: str) -> bool:
    return section in _VIRTUAL


def _vparts(sub) -> list[str]:
    if isinstance(sub, str):
        sub = sub.replace("\\", "/").split("/")
    return [p for p in sub if p and p != "."]


def _vcall(section: str, user, fn: str, *args):
    v = _vfs(section)
    if v is None:
        raise FileNotFoundError(section)
    if user is None:
        raise PermissionError("Anmeldung erforderlich")
    return getattr(v, fn)(user, *args)


def section_of(name: str) -> str | None:
    """Passt dieser Pfadabschnitt auf einen Server-Ordner? Gross-/Kleinschreibung
    egal - Windows-Explorer und FTP-Programme sind da unterschiedlich."""
    n = (name or "").strip().rstrip("/")
    for display in SECTIONS:
        if n.lower() == display.lower():
            # Modulgebundene Sektion bei abgeschaltetem Modul = gibt es nicht
            # (gilt so fuer WebDAV, FTP, SFTP und den Explorer gleichermassen).
            return display if _module_ok(display) else None
    return None


# Sektionen, die ein eigenes Modul brauchen (sonst unsichtbar und gesperrt).
_NEEDS_MODULE = {"Deployment": "deployment"}


def _module_ok(section: str) -> bool:
    mid = _NEEDS_MODULE.get(section)
    if not mid:
        return True
    try:
        from app import modules
        return modules.is_enabled(mid)
    except Exception:
        return False


def display_names() -> list[str]:
    """Sichtbare Sektionen - virtuelle/modulgebundene nur, wenn ihr Modul aktiv ist."""
    return [n for n in SECTIONS if (not is_virtual(n) or _vfs(n) is not None) and _module_ok(n)]


def read_perm(section: str) -> str:
    """Berechtigung zum Lesen dieser Sektion."""
    return READ_PERM.get(section, "use_relay")


def base(section: str) -> Path:
    """Basisordner einer Sektion - wird bei Bedarf angelegt."""
    special = _SPECIAL_BASE.get(section)
    if special is not None:
        # Projekt-Wurzel: existiert immer, wird NICHT angelegt.
        return special
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


def listdir(section: str, sub: list[str] | str, user=None) -> list[dict]:
    if is_virtual(section):
        return _vcall(section, user, "listdir", _vparts(sub))
    target = resolve(section, sub)
    if not target.exists():
        raise FileNotFoundError(target.name)
    if not target.is_dir():
        raise NotADirectoryError(target.name)
    out = [_entry(c) for c in sorted(target.iterdir(),
                                     key=lambda c: (not c.is_dir(), c.name.lower()))]
    return out


def stat_entry(section: str, sub: list[str] | str, user=None) -> dict | None:
    if is_virtual(section):
        return _vcall(section, user, "stat", _vparts(sub))
    target = resolve(section, sub)
    return _entry(target) if target.exists() else None


def read(section: str, sub: list[str] | str, user=None) -> bytes:
    if is_virtual(section):
        return _vcall(section, user, "read", _vparts(sub))
    target = resolve(section, sub)
    if not target.is_file():
        raise FileNotFoundError(target.name)
    return target.read_bytes()


def write(section: str, sub: list[str] | str, data: bytes, user=None) -> None:
    if is_virtual(section):
        return _vcall(section, user, "write", _vparts(sub), data)
    target = resolve(section, sub)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data or b"")


def mkdir(section: str, sub: list[str] | str, user=None) -> None:
    if is_virtual(section):
        return _vcall(section, user, "mkdir", _vparts(sub))
    resolve(section, sub).mkdir(parents=True, exist_ok=True)


def delete(section: str, sub: list[str] | str, user=None) -> None:
    if is_virtual(section):
        return _vcall(section, user, "delete", _vparts(sub))
    target = resolve(section, sub)
    if target == base(section).resolve():
        raise PermissionError("Der Hauptordner kann nicht geloescht werden")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def move(section: str, src: list[str] | str, dst: list[str] | str, user=None) -> None:
    if is_virtual(section):
        return _vcall(section, user, "move", _vparts(src), _vparts(dst))
    s = resolve(section, src)
    d = resolve(section, dst)
    if s == base(section).resolve():
        raise PermissionError("Der Hauptordner kann nicht verschoben werden")
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))


def may_write(user, section: str) -> bool:
    """Darf dieser Benutzer in dieser Sektion schreiben?"""
    from app.auth import user_has_permission, is_super_admin
    if not _module_ok(section):
        return False
    if is_virtual(section):
        # Feinere Rechte prueft das Modul selbst (PermissionError je Pfad).
        return _vfs(section) is not None and (is_super_admin(user) or user_has_permission(user, "use_relay"))
    if is_super_admin(user):
        return True
    perm = WRITE_PERM.get(section, "use_relay")
    if user_has_permission(user, perm):
        return True
    # 'admin_settings' schliesst 'manage_settings' der Sache nach mit ein.
    if perm == "manage_settings" and user_has_permission(user, "admin_settings"):
        return True
    return False


def may_read(user, section: str | None = None) -> bool:
    """
    Darf dieser Benutzer lesen? Ohne `section` gilt die allgemeine
    Relay-Berechtigung. MIT Sektion wird zusaetzlich die Sonderberechtigung
    geprueft - 'Source' braucht 'see_source', sonst koennte jeder
    Relay-Benutzer den Quellcode des Servers herunterladen.
    """
    from app.auth import user_has_permission, is_super_admin
    if section is not None and not _module_ok(section):
        return False
    if is_super_admin(user):
        return True
    if not user_has_permission(user, "use_relay"):
        return False
    if section is None:
        return True
    if is_virtual(section):
        return _vfs(section) is not None
    return user_has_permission(user, read_perm(section))


# ------------------------------------------------------------------
# Anmeldung per Geraetetoken (Modul "client")
# ------------------------------------------------------------------
# Die Client-App speichert kein Passwort, sondern einen Geraetetoken
# ("<id>.<geheim>"). Damit sie das Relay als Laufwerk verbinden kann, gilt
# dieser Token beim Relay (WebDAV/FTP/SFTP) als Passwort - aber NUR zusammen
# mit dem passenden Benutzernamen. "Abmelden" unter Profil -> Meine Geraete
# sperrt damit auch das Laufwerk sofort.
def device_user(username: str, password: str):
    import re as _re
    if not _re.fullmatch(r"[0-9a-f]{16}\.[A-Za-z0-9_-]{20,}", password or ""):
        return None
    try:
        from app import db, modules
        if not modules.is_enabled("client"):
            return None
        dev = modules.use("client", "devices").check_token(password)
        if not dev:
            return None
        user = db.get_user_by_id(dev["user_id"])
        name = (username or "").split("\\")[-1].split("@")[0].strip().lower()
        if not user or user["username"].lower() != name:
            return None
        if user.get("auth_realm"):
            from app.auth import sso_enabled
            if not sso_enabled():
                return None
        return user
    except Exception:
        return None

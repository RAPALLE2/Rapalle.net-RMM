# webhost.py (Modul "webserver") - gemeinsamer Hosting-Kern
# ---------------------------------------------------------
# EINE Stelle fuer alles, was "einen Ordner als Webseite ausliefern" heisst.
# Benutzt von:
#   * den Websites des Moduls "webserver"   (rmm.url.de/<website>)
#   * dem Modul "deployment"                (rmm.url.de/deployment) - requires: [webserver]
#
# Ordner-Funktionen (Pfade bleiben IMMER im jeweiligen Wurzelordner):
#   resolve(root, sub) listdir(root, sub) write(root, sub, data) mkdir(root, sub)
#   delete(root, sub) move(root, src, dst) entry(path)
# Ausliefern:
#   serve(root, parts, base, title=..., custom_html=..., listing=True)
#     -> Datei (Range/Streaming via FileResponse), index.html eines Ordners,
#        eigene HTML-Startseite oder automatische Dateiliste.

from __future__ import annotations

import html
import mimetypes
import shutil
import urllib.parse as _up
from pathlib import Path

from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse

INDEX_FILES = ("index.html", "index.htm")


# ------------------------------------------------------------------ Pfade
def resolve(root: Path, sub: list[str] | str) -> Path:
    """Pfad im Wurzelordner - kann ihn nie verlassen (.., ~, absolute Pfade, NUL)."""
    if isinstance(sub, str):
        sub = sub.replace("\\", "/").split("/")
    base = Path(root).resolve()
    parts = []
    for seg in sub:
        seg = (seg or "").strip()
        if seg in ("", "."):
            continue
        if seg == ".." or "/" in seg or "\\" in seg or seg.startswith("~") or "\x00" in seg:
            raise PermissionError("Ungültiger Name")
        parts.append(seg)
    p = (base / Path(*parts)).resolve() if parts else base
    try:
        p.relative_to(base)
    except ValueError:
        raise PermissionError("Pfad außerhalb des Ordners")
    return p


def entry(p: Path) -> dict:
    try:
        st = p.stat()
        d = p.is_dir()
        return {"name": p.name, "is_dir": d, "size": 0 if d else int(st.st_size), "mtime": int(st.st_mtime * 1000)}
    except OSError:
        return {"name": p.name, "is_dir": False, "size": 0, "mtime": 0}


def listdir(root: Path, sub) -> list[dict]:
    p = resolve(root, sub)
    if not p.is_dir():
        raise FileNotFoundError(p.name)
    return [entry(c) for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))]


def write(root: Path, sub, data: bytes) -> None:
    p = resolve(root, sub)
    if p == Path(root).resolve():
        raise PermissionError("Ungültiges Ziel")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data or b"")


def mkdir(root: Path, sub) -> None:
    resolve(root, sub).mkdir(parents=True, exist_ok=True)


def delete(root: Path, sub) -> None:
    p = resolve(root, sub)
    if p == Path(root).resolve():
        raise PermissionError("Der Hauptordner kann nicht gelöscht werden")
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    else:
        raise FileNotFoundError(p.name)


def move(root: Path, src, dst) -> None:
    a, b = resolve(root, src), resolve(root, dst)
    if a == Path(root).resolve():
        raise PermissionError("Der Hauptordner kann nicht verschoben werden")
    if b.exists():
        raise FileExistsError(b.name)
    b.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(a), str(b))


# ------------------------------------------------------------------ Ausliefern
def _listing_page(title: str, base: str, rel: list[str], entries: list[dict]) -> str:
    def url(name: str = "") -> str:
        segs = rel + ([name] if name else [])
        return base + "/" + "/".join(_up.quote(s) for s in segs)
    rows = []
    if rel:
        rows.append(f'<tr><td><a href="{html.escape(url()[:url().rfind("/")] or base)}">⬆ ..</a></td><td></td></tr>')
    for e in entries:
        if e["name"].startswith("."):
            continue
        icon = "📁" if e["is_dir"] else "📄"
        size = "" if e["is_dir"] else f'{e["size"] / 1024:.1f} KB'
        rows.append(f'<tr><td>{icon} <a href="{html.escape(url(e["name"]))}{"/" if e["is_dir"] else ""}">'
                    f'{html.escape(e["name"])}</a></td><td style="text-align:right">{size}</td></tr>')
    where = html.escape("/" + "/".join(rel)) if rel else ""
    return (f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<style>body{font-family:system-ui,sans-serif;max-width:860px;margin:30px auto;padding:0 16px;"
            "background:#0f1318;color:#e6e6e6}a{color:#5cc8ff;text-decoration:none}a:hover{text-decoration:underline}"
            "table{width:100%;border-collapse:collapse}td{padding:7px 4px;border-bottom:1px solid #222}"
            "h1{font-size:20px}small{color:#888}</style>"
            f"<h1>{html.escape(title)} <small>{where}</small></h1><table>{''.join(rows) or '<tr><td>Keine Dateien.</td></tr>'}"
            "</table>")


def serve(root: Path, parts: list[str], base: str, title: str = "Website",
          custom_html: str = "", listing: bool = True):
    """Einen Pfad der Website ausliefern.

    base        Adresse der Website ohne Schraegstrich am Ende, z.B. "/deployment" oder "/meine-seite"
    custom_html eigene Startseite (nur fuer die Wurzel), hat Vorrang vor index.html
    listing     automatische Dateiliste fuer Ordner ohne index.html
    """
    try:
        p = resolve(root, parts)
    except PermissionError:
        return PlainTextResponse("Nicht erlaubt", status_code=403)
    if any(seg.startswith(".") for seg in parts if seg):
        return PlainTextResponse("Nicht gefunden", status_code=404)      # versteckte Dateien nie ausliefern
    if not parts and custom_html.strip():
        return HTMLResponse(custom_html)
    if not p.exists():
        return PlainTextResponse("Nicht gefunden", status_code=404)
    if p.is_dir():
        for name in INDEX_FILES:
            idx = p / name
            if idx.is_file():
                return FileResponse(idx, media_type="text/html")
        if not listing:
            return PlainTextResponse("Nicht gefunden", status_code=404)
        try:
            entries = listdir(root, parts)
        except Exception:
            entries = []
        return HTMLResponse(_listing_page(title, base, [s for s in parts if s], entries))
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json", "image/svg+xml"):
        ctype += "; charset=utf-8" if "charset" not in ctype else ""
    return FileResponse(p, media_type=ctype)


def redirect_to_dir(base: str) -> RedirectResponse:
    """/website -> /website/ (damit relative Links in index.html stimmen)."""
    return RedirectResponse(base + "/", status_code=308)

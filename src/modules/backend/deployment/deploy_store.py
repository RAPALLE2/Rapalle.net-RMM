# deploy_store.py (Modul "deployment")
# ------------------------------------
# Der Deployment-Ordner (backend/storage/deployment) und seine Einstellungen.
# Was hier liegt, ist bei eingeschalteter Freigabe OHNE Anmeldung unter
# /deployment/<datei> abrufbar - genau dafuer ist der Ordner gedacht
# (Installer, Skripte, Logos per Link weitergeben).
#
# Die Einstellungen liegen wie frueher in der DB (deployment_html/_public/_title),
# dadurch bleibt eine bestehende Konfiguration beim Umzug ins Modul erhalten.

from __future__ import annotations

from pathlib import Path

from app import db
from app.paths import STORAGE_DIR

from app.paths import DEPLOYMENT_DIR as DIR  # storage/web/deployment
SETTING_HTML = "deployment_html"
SETTING_PUBLIC = "deployment_public"
SETTING_TITLE = "deployment_title"


def base() -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    return DIR


def is_public() -> bool:
    raw = db.get_setting(SETTING_PUBLIC)
    return str(raw or "0").strip().lower() in ("1", "true", "yes", "on")


def settings() -> dict:
    return {"html": db.get_setting(SETTING_HTML) or "",
            "title": db.get_setting(SETTING_TITLE) or "Deployment",
            "public": is_public()}


# ------------------------------------------------------------------ Dateien
# Die eigentliche Datei-Logik liegt im Hosting-Kern des Moduls "webserver"
# (webhost.py) - der Deployment-Server benutzt sie nur mit seinem Ordner.
def _wh():
    from app import modules
    return modules.use("webserver", "webhost")


def resolve(sub: list[str] | str) -> Path:
    return _wh().resolve(base(), sub)


def entry(p: Path) -> dict:
    return _wh().entry(p)


def listdir(sub) -> list[dict]:
    return _wh().listdir(base(), sub)


def write(sub, data: bytes) -> None:
    _wh().write(base(), sub, data)


def mkdir(sub) -> None:
    _wh().mkdir(base(), sub)


def delete(sub) -> None:
    _wh().delete(base(), sub)


def move(src, dst) -> None:
    _wh().move(base(), src, dst)

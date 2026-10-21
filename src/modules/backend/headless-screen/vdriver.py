# vdriver.py (Modul "headless-screen") - Treiberpaket fuer den virtuellen Bildschirm
# ---------------------------------------------------------------------------------
# Stellt das ZIP mit dem "Indirect Display Driver" (IDD) bereit, das der Agent als
# SYSTEM mit pnputil installiert. Das RMM liefert KEINE Treiber-Binaerdaten mit:
# - Ist in den Modul-Einstellungen eine eigene driver_url gesetzt, wird sie direkt
#   (zusammen mit der SHA256) an den Agenten durchgereicht.
# - Sonst versucht das Modul beim Aktivieren, ein Paket herunterzuladen und unter
#   storage/headless-screen/ abzulegen ("dafuer sind Module da"); der Agent holt es
#   dann vom RMM selbst (/api/headless-screen/driver.zip).
# Ohne verfuegbares Paket wird nichts installiert - der Agent meldet das klar.

from __future__ import annotations

import hashlib
import threading
import urllib.request
from pathlib import Path

from app import db, modules
from app.paths import STORAGE_DIR

DIR = STORAGE_DIR / "headless-screen"
ZIP = DIR / "driver.zip"
SHA = DIR / "driver.zip.sha256"
# Quelle: quartz-/IddSampleDriver-artiges Paket. Per Umgebung/Einstellung ersetzbar;
# Standard leer, damit nie ungewollt eine feste Fremdquelle gezogen wird.
DEFAULT_SOURCE = ""
_lock = threading.Lock()
_state = {"downloading": False, "error": "", "have": False}


def _settings() -> dict:
    try:
        return modules.get_settings("headless-screen") or {}
    except Exception:
        return {}


def local_sha() -> str:
    try:
        return SHA.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def have_local() -> bool:
    return ZIP.is_file() and ZIP.stat().st_size > 0


def package_info() -> dict:
    """Was soll der Agent laden? {url, sha256}. Eigene URL hat Vorrang."""
    st = _settings()
    url = (st.get("driver_url") or "").strip()
    if url:
        return {"url": url, "sha256": (st.get("driver_sha256") or "").strip()}
    if have_local():
        # Relative Server-Adresse: der Agent setzt sie gegen SEINE bekannte
        # Server-URL zusammen und holt sie mit dem Agent-Token. So klappt es auch
        # ohne gesetzte server_url und hinter Reverse-Proxy/Tunnel.
        return {"url": "/api/headless-screen/driver.zip", "sha256": local_sha(),
                "relative": True, "needs_agent_token": True}
    return {"url": "", "sha256": ""}


def status() -> dict:
    st = _settings()
    return {"custom_url": bool((st.get("driver_url") or "").strip()),
            "have_local": have_local(), "downloading": _state["downloading"],
            "error": _state["error"], "source": st.get("driver_url") or DEFAULT_SOURCE}


def ensure_async(ctx=None) -> None:
    """Beim Aktivieren: Paket besorgen, falls keine eigene URL gesetzt ist."""
    st = _settings()
    if (st.get("driver_url") or "").strip() or have_local():
        return
    if not DEFAULT_SOURCE:
        if ctx:
            ctx.log("Kein Treiberpaket hinterlegt – bitte in den Modul-Einstellungen eine "
                    "driver_url angeben oder ein driver.zip in storage/headless-screen/ ablegen.")
        return
    threading.Thread(target=_download, args=(DEFAULT_SOURCE, ctx), daemon=True,
                     name="hs-driver-dl").start()


def _download(url: str, ctx=None) -> None:
    with _lock:
        if _state["downloading"]:
            return
        _state.update(downloading=True, error="")
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=120) as r:
            blob = r.read()
        ZIP.write_bytes(blob)
        SHA.write_text(hashlib.sha256(blob).hexdigest(), encoding="utf-8")
        _state["have"] = True
        if ctx:
            ctx.log(f"Treiberpaket geladen ({len(blob)//1024} KB).")
    except Exception as e:      # noqa: BLE001
        _state["error"] = str(e)
        if ctx:
            ctx.log(f"Treiberpaket konnte nicht geladen werden: {e}")
    finally:
        _state["downloading"] = False

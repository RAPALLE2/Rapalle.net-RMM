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
# Standard-Treiber fuer Leute ohne eigenes Paket: der quelloffene, signierte
# "Virtual Display Driver" (IddSampleDriver-Nachfolger). Statt eine feste - und damit
# alternde - Datei-URL zu hinterlegen, loest das Modul beim Aktivieren automatisch das
# AKTUELLE Release auf (erste .zip-Datei) und laedt es. So funktioniert es fuer
# Nicht-Fachleute ohne jede Eingabe; eine eigene driver_url hat weiterhin Vorrang.
DEFAULT_REPO = "VirtualDrivers/Virtual-Display-Driver"
DEFAULT_PROJECT_URL = "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/latest"
DEFAULT_SOURCE = ""      # wird beim Aktivieren per API ermittelt (resolve_default_url)
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
            "error": _state["error"], "source": st.get("driver_url") or DEFAULT_PROJECT_URL,
            "auto_default": not (st.get("driver_url") or "").strip()}


def resolve_default_url() -> str:
    """URL des Standard-Treibers (aktuelles Release des Open-Source-VDD) ermitteln.
    Nimmt die erste passende .zip-Datei des neuesten Releases. Leerer String bei Fehler."""
    import json
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{DEFAULT_REPO}/releases/latest",
            headers={"User-Agent": "RAPALLE-RMM", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.loads(r.read() or b"{}")
        assets = rel.get("assets") or []
        # Bevorzugt x64/zip-Dateien, die wie ein Treiberpaket aussehen.
        def score(a):
            n = (a.get("name") or "").lower()
            s = 0
            if n.endswith(".zip"):
                s += 5
            if "x64" in n or "amd64" in n:
                s += 2
            if "driver" in n or "vdd" in n or "virtual" in n:
                s += 1
            if "arm" in n:
                s -= 3
            return s
        zips = sorted([a for a in assets if (a.get("name") or "").lower().endswith(".zip")],
                      key=score, reverse=True)
        if zips:
            return zips[0].get("browser_download_url") or ""
    except Exception as e:      # noqa: BLE001
        print(f"[headless-screen] Standard-Treiber nicht aufgelöst: {e}")
    return ""


def ensure_async(ctx=None) -> None:
    """Beim Aktivieren: Paket besorgen, falls keine eigene URL gesetzt ist.
    Ohne eigene URL wird automatisch der quelloffene Standard-Treiber geladen -
    gedacht fuer Leute, die sich nicht auskennen (keine Eingabe noetig)."""
    st = _settings()
    if (st.get("driver_url") or "").strip() or have_local():
        return

    def worker():
        url = DEFAULT_SOURCE or resolve_default_url()
        if not url:
            if ctx:
                ctx.log("Standard-Treiber konnte nicht automatisch geladen werden (kein Internet?). "
                        "Bitte in den Modul-Einstellungen eine driver_url angeben oder ein driver.zip "
                        "in storage/headless-screen/ ablegen.")
            return
        if ctx:
            ctx.log(f"Lade quelloffenen Standard-Bildschirmtreiber: {url}")
        _download(url, ctx)
    threading.Thread(target=worker, daemon=True, name="hs-driver-dl").start()


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

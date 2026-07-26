"""
runtime_env.py
--------------
Erkennt, WIE das Backend läuft:

  - "docker"  -> läuft in einem Container (Docker / Podman / LXC-Image)
  - "native"  -> ganz normal per "python run.py" auf dem Host installiert

Warum das wichtig ist:
Im Container sind einige Dinge NICHT mehr zur Laufzeit änderbar, weil sie
beim Container-Start festgelegt werden (Port-Mapping, gebundene Adresse,
Volumes). Wer dort z.B. PORT in der .env ändert, erreicht das Dashboard
danach nicht mehr - der Container mappt weiterhin den alten Port.
Deshalb blendet die Oberfläche (Settings -> Source) einen Hinweis ein.

Die Erkennung nutzt mehrere Signale, damit sie auch dann stimmt, wenn eines
davon fehlt:
  1. Umgebungsvariable RMM_INSTALL_KIND (wird im Dockerfile gesetzt) - stärkstes Signal
  2. Marker-Datei /app/.docker-install (wird im Dockerfile angelegt)
  3. /.dockerenv (legt Docker selbst an)
  4. "docker"/"containerd"/"kubepods" in /proc/1/cgroup bzw. /proc/self/mountinfo
"""

import os
import platform
import sys
from pathlib import Path

# Marker-Datei, die das Dockerfile anlegt (siehe Dockerfile im Projekt-Root).
_MARKER = Path("/app/.docker-install")


def _cgroup_says_container() -> bool:
    """Sucht in den Kernel-Dateien nach typischen Container-Spuren."""
    for path in ("/proc/1/cgroup", "/proc/self/mountinfo"):
        try:
            text = Path(path).read_text(errors="ignore")
        except Exception:
            continue
        low = text.lower()
        if "docker" in low or "containerd" in low or "kubepods" in low or "/lxc/" in low:
            return True
    return False


def is_docker() -> bool:
    """True, wenn das Backend in einem Container läuft."""
    kind = (os.getenv("RMM_INSTALL_KIND", "") or "").strip().lower()
    if kind == "docker":
        return True
    if kind == "native":
        return False
    if _MARKER.exists() or Path("/.dockerenv").exists():
        return True
    return _cgroup_says_container()


def install_kind() -> str:
    """'docker' oder 'native' - der kurze Schlüssel für die Oberfläche."""
    return "docker" if is_docker() else "native"


def runtime_info() -> dict:
    """
    Alles, was die Oberfläche über die Installationsart wissen muss.

    locked_settings = Einstellungen, die im Container NICHT mehr per .env
    geändert werden dürfen, weil sie am Container hängen.
    """
    from app.config import PORT, HOST, GUACD_HOST, GUACD_PORT

    docker = is_docker()
    return {
        "install_kind": "docker" if docker else "native",
        "is_docker": docker,
        "container_name": os.getenv("HOSTNAME", "") if docker else "",
        "compose_project": os.getenv("COMPOSE_PROJECT_NAME", "") if docker else "",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "port": PORT,
        "host": HOST,
        "guacd": f"{GUACD_HOST}:{GUACD_PORT}",
        # Im Container fest verdrahtet -> Änderungen in der .env wirken nicht
        # bzw. sperren einen aus. Nativ ist alles frei änderbar.
        "locked_settings": ["PORT", "HOST"] if docker else [],
        "data_paths": {
            "database": "backend/data.sqlite",
            "recordings": "backend/recordings",
            "media": "backend/media_files",
            "branding": "backend/branding",
        },
    }

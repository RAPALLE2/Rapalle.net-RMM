"""
app/guacd_manager.py
--------------------
Verwaltet Docker UND den guacd-Container KOMPLETT aus Python heraus - man muss
also keine Docker-Befehle mehr von Hand ausführen.

Kann:
  - prüfen, ob Docker installiert ist und der Daemon läuft
  - Docker bei Bedarf installieren (Linux, offizielles get.docker.com-Skript)
  - das guacd-Image ziehen
  - den guacd-Container anlegen/starten/stoppen/neustarten
  - den Gesamtstatus melden

Alle Docker-Aufrufe laufen über die Docker-CLI (subprocess) - so wird KEINE
zusätzliche Python-Bibliothek gebraucht.

WICHTIG: Installieren von Docker und das Verwalten von Containern erfordert
root-/Administratorrechte. Läuft das Backend ohne diese Rechte, werden die
Fehler sauber zurückgemeldet (statt zu crashen).
"""

import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request

# GUACD_PORT bevorzugt aus der zentralen Config lesen; falls die (noch) nicht
# aktualisiert wurde, direkt aus der Umgebung mit sinnvollem Standard.
try:
    from app.config import GUACD_PORT
except ImportError:
    GUACD_PORT = int(os.getenv("GUACD_PORT", "4822"))

IMAGE = "guacamole/guacd"
CONTAINER_NAME = "rapalle-guacd"
# Der veröffentlichte Port wird bewusst nur an localhost gebunden - guacd hat
# keine eigene Authentifizierung und darf nicht offen im Netz hängen.
_PUBLISH = f"127.0.0.1:{GUACD_PORT}:4822"

_GET_DOCKER_URL = "https://get.docker.com"


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    """Führt einen Befehl aus und gibt (returncode, stdout, stderr) zurück."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", f"Befehl nicht gefunden: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"Zeitüberschreitung: {' '.join(cmd)}"
    except Exception as e:
        return 1, "", str(e)


# ------------------------------------------------------------------
# Zustandsabfragen
# ------------------------------------------------------------------

def docker_cli_installed() -> bool:
    return shutil.which("docker") is not None


def docker_daemon_running() -> bool:
    if not docker_cli_installed():
        return False
    rc, _, _ = _run(["docker", "info"], timeout=15)
    return rc == 0


def image_present() -> bool:
    rc, out, _ = _run(["docker", "images", "-q", IMAGE], timeout=20)
    return rc == 0 and bool(out.strip())


def _container_state() -> str:
    """Rückgabe: 'running', 'stopped' oder 'absent'."""
    rc, out, _ = _run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME], timeout=15
    )
    if rc != 0:
        return "absent"
    return "running" if out.strip() == "true" else "stopped"


def status() -> dict:
    """Gesamtstatus für die Oberfläche."""
    cli = docker_cli_installed()
    daemon = docker_daemon_running() if cli else False
    state = _container_state() if daemon else "absent"
    return {
        "os": platform.system(),
        "docker_cli": cli,
        "docker_daemon": daemon,
        "image": image_present() if daemon else False,
        "container": state,                       # running | stopped | absent
        "container_running": state == "running",
    }


# ------------------------------------------------------------------
# Aktionen
# ------------------------------------------------------------------

def install_docker() -> dict:
    """
    Installiert Docker über das offizielle Skript von get.docker.com (Linux).
    Erfordert root. Auf Windows/macOS nicht automatisierbar -> Hinweis.
    """
    system = platform.system()
    if system != "Linux":
        return {"ok": False, "log": (
            f"Automatische Docker-Installation wird auf {system} nicht unterstützt. "
            "Bitte Docker Desktop manuell installieren: https://docs.docker.com/get-docker/"
        )}

    if os.geteuid() != 0:
        return {"ok": False, "log": (
            "Für die Docker-Installation werden root-Rechte benötigt. "
            "Bitte das Backend mit root/sudo starten oder Docker einmalig manuell "
            "installieren: curl -fsSL https://get.docker.com | sh"
        )}

    log_lines = []
    script_path = None
    try:
        # 1) Installationsskript herunterladen (ohne curl-Abhängigkeit)
        log_lines.append("Lade Docker-Installationsskript von get.docker.com ...")
        fd, script_path = tempfile.mkstemp(prefix="get-docker-", suffix=".sh")
        os.close(fd)
        with urllib.request.urlopen(_GET_DOCKER_URL, timeout=30) as resp:
            open(script_path, "wb").write(resp.read())

        # 2) Skript ausführen (installiert Docker Engine für die jeweilige Distro)
        log_lines.append("Installiere Docker (das kann 1-3 Minuten dauern) ...")
        rc, out, err = _run(["sh", script_path], timeout=600)
        log_lines.append(out[-2000:] if out else "")
        if rc != 0:
            log_lines.append("FEHLER: " + (err[-1000:] if err else f"Skript-Exitcode {rc}"))
            return {"ok": False, "log": "\n".join(l for l in log_lines if l)}
    except Exception as e:
        log_lines.append(f"FEHLER beim Herunterladen/Ausführen: {e}")
        log_lines.append("Alternativ manuell: curl -fsSL https://get.docker.com | sh")
        return {"ok": False, "log": "\n".join(l for l in log_lines if l)}
    finally:
        if script_path:
            try:
                os.unlink(script_path)
            except Exception:
                pass

    # 3) Daemon aktivieren/starten (systemd)
    _run(["systemctl", "enable", "--now", "docker"], timeout=60)
    log_lines.append("Docker installiert.")
    return {"ok": docker_daemon_running(), "log": "\n".join(l for l in log_lines if l)}


def pull_image() -> dict:
    if not docker_daemon_running():
        return {"ok": False, "log": "Docker-Daemon läuft nicht."}
    rc, out, err = _run(["docker", "pull", IMAGE], timeout=600)
    return {"ok": rc == 0, "log": out or err}


def ensure_running() -> dict:
    """
    Stellt sicher, dass der guacd-Container läuft: Image ziehen (falls nötig),
    Container anlegen oder starten. Ohne Docker-Daemon -> Fehler.
    """
    if not docker_daemon_running():
        return {"ok": False, "log": "Docker-Daemon läuft nicht (Docker installiert?)."}

    log = []
    if not image_present():
        log.append(f"Ziehe Image {IMAGE} ...")
        res = pull_image()
        log.append(res["log"])
        if not res["ok"]:
            return {"ok": False, "log": "\n".join(log)}

    state = _container_state()
    if state == "running":
        return {"ok": True, "log": "guacd-Container läuft bereits."}
    if state == "stopped":
        rc, out, err = _run(["docker", "start", CONTAINER_NAME], timeout=60)
        log.append("Starte vorhandenen Container ...")
        return {"ok": rc == 0, "log": "\n".join(log + [out or err])}

    # Nicht vorhanden -> neu anlegen (überlebt Neustarts via --restart)
    log.append("Erstelle und starte guacd-Container ...")
    rc, out, err = _run([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "--restart", "unless-stopped",
        "-p", _PUBLISH,
        IMAGE,
    ], timeout=120)
    return {"ok": rc == 0, "log": "\n".join(log + [out or err])}


def stop() -> dict:
    rc, out, err = _run(["docker", "stop", CONTAINER_NAME], timeout=60)
    return {"ok": rc == 0, "log": out or err}


def restart() -> dict:
    rc, out, err = _run(["docker", "restart", CONTAINER_NAME], timeout=90)
    return {"ok": rc == 0, "log": out or err}


def remove() -> dict:
    _run(["docker", "stop", CONTAINER_NAME], timeout=60)
    rc, out, err = _run(["docker", "rm", CONTAINER_NAME], timeout=60)
    return {"ok": rc == 0, "log": out or err}


def setup() -> dict:
    """
    Komplett-Einrichtung in einem Rutsch: Docker installieren (falls nötig),
    Image ziehen, Container starten. Gibt ein Schritt-für-Schritt-Log zurück.
    """
    log = []
    if not docker_cli_installed():
        log.append("Docker nicht gefunden - starte Installation ...")
        res = install_docker()
        log.append(res["log"])
        if not res["ok"]:
            return {"ok": False, "log": "\n".join(log)}
    elif not docker_daemon_running():
        log.append("Docker installiert, aber Daemon nicht aktiv - starte ihn ...")
        _run(["systemctl", "enable", "--now", "docker"], timeout=60)

    if not docker_daemon_running():
        log.append("Docker-Daemon konnte nicht gestartet werden.")
        return {"ok": False, "log": "\n".join(log)}

    res = ensure_running()
    log.append(res["log"])
    return {"ok": res["ok"], "log": "\n".join(l for l in log if l)}

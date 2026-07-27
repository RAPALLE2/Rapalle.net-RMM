"""
docker_services.py
------------------
Zusatzdienste per Knopfdruck dazuschalten - aber NUR, wenn das Backend selbst
in einem Container laeuft.

Hintergrund: Nativ installiert richtet man guacd oder eine SQL-Datenbank ganz
normal auf dem Server ein. Im Container waere das umstaendlich - man muesste
docker-compose.yml auf dem Host bearbeiten. Deshalb kann das Backend hier zwei
Nachbar-Container selbst starten:

    guacd   Apache guacd fuer RDP/VNC/SSH im Browser
    db      MariaDB als externe Datenbank

Wie es funktioniert
-------------------
Der Container spricht ueber den Docker-Socket (/var/run/docker.sock) mit dem
Docker-Dienst des Hosts und legt die Container als GESCHWISTER an - nicht
verschachtelt. Damit sie erreichbar sind, haengt das Backend sie in sein
EIGENES Netzwerk; angesprochen werden sie dann ueber ihren Containernamen
(z.B. "rapalle-rmm-guacd:4822"), ganz ohne veroeffentlichte Ports.

Voraussetzung: In docker-compose.yml muss der Socket eingehaengt sein:

    volumes:
      - /var/run/docker.sock:/var/run/docker.sock

Sicherheitshinweis: Wer Zugriff auf den Docker-Socket hat, hat faktisch
Rootrechte auf dem Host. Deshalb sind alle Endpunkte hier Admin-only, und der
Socket ist in docker-compose.yml bewusst auskommentiert - er wird erst
eingehaengt, wenn diese Funktion wirklich gewuenscht ist.

Die Zugangsdaten der Datenbank werden beim Anlegen einmal erzeugt und in den
Einstellungen hinterlegt, damit das Dashboard das Datenbank-Formular
vorausfuellen kann.
"""

from __future__ import annotations

import json
import secrets
import socket
import string
import http.client
from typing import Any

from app import db
from app.runtime_env import is_docker

DOCKER_SOCKET = "/var/run/docker.sock"

# Die beiden Dienste, die sich dazuschalten lassen.
SERVICES: dict[str, dict[str, Any]] = {
    "guacd": {
        "label": "Guacamole (guacd)",
        "purpose": "Remote-Desktop im Browser: RDP, VNC, SSH und Telnet.",
        "image": "guacamole/guacd:1.5.5",
        "container": "rapalle-rmm-guacd",
        "port": 4822,
    },
    "db": {
        "label": "SQL-Datenbank (MariaDB)",
        "purpose": "Externe Datenbank statt der lokalen SQLite-Datei.",
        "image": "mariadb:11",
        "container": "rapalle-rmm-db",
        "port": 3306,
        "volume": "rapalle-rmm-dbdata",
        "database": "rapalle_rmm",
        "user": "rmm",
    },
}


# ==========================================================================
# Winziger Docker-API-Client (ueber den Unix-Socket, ohne Zusatzpakete)
# ==========================================================================

class _UnixHTTPConnection(http.client.HTTPConnection):
    """http.client, das statt TCP auf einen Unix-Socket spricht."""

    def __init__(self, path: str, timeout: float = 60):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class DockerError(RuntimeError):
    pass


def _api(method: str, path: str, body: dict | None = None, timeout: float = 60) -> Any:
    """Ein Aufruf gegen die Docker-Engine-API. Gibt JSON oder rohen Text zurueck."""
    conn = _UnixHTTPConnection(DOCKER_SOCKET, timeout=timeout)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status >= 400:
            try:
                msg = json.loads(raw).get("message", raw.decode(errors="replace"))
            except Exception:
                msg = raw.decode(errors="replace")
            raise DockerError(f"Docker: {msg} (HTTP {resp.status})")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw.decode(errors="replace")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def socket_available() -> bool:
    """Ist der Docker-Socket eingehaengt UND ansprechbar?"""
    try:
        _api("GET", "/_ping", timeout=5)
        return True
    except Exception:
        return False


# ==========================================================================
# Eigener Container: Netzwerk ermitteln
# ==========================================================================

def _self_container() -> dict | None:
    """
    Die Beschreibung des EIGENEN Containers. Der Hostname entspricht in Docker
    standardmaessig der (gekuerzten) Container-ID.
    """
    import os
    for ident in (os.getenv("HOSTNAME", ""), "/" + os.getenv("HOSTNAME", "")):
        if not ident.strip("/"):
            continue
        try:
            return _api("GET", f"/containers/{ident.lstrip('/')}/json", timeout=10)
        except Exception:
            continue
    return None


def _self_network() -> str | None:
    """Name des Netzwerks, in dem das Backend haengt (fuer die Nachbarn)."""
    info = _self_container()
    if not info:
        return None
    nets = (info.get("NetworkSettings") or {}).get("Networks") or {}
    # "bridge" nur als letzte Wahl - dort funktioniert die Namensaufloesung nicht.
    for name in nets:
        if name != "bridge":
            return name
    return next(iter(nets), None)


def _host_path_for(container_path: str) -> str | None:
    """
    Zu einem Pfad IM Container den passenden Host-Pfad finden.
    Wird gebraucht, damit guacd seine Aufzeichnungen in denselben Ordner legt,
    den das Backend spaeter ausliest.
    """
    info = _self_container()
    if not info:
        return None
    best = None
    for m in info.get("Mounts") or []:
        dest = (m.get("Destination") or "").rstrip("/")
        if dest and container_path.rstrip("/").startswith(dest):
            rest = container_path.rstrip("/")[len(dest):]
            src = m.get("Source") or ""
            if src:
                best = src + rest
    return best


# ==========================================================================
# Status / Ein- und Ausschalten
# ==========================================================================

def _find_container(name: str) -> dict | None:
    try:
        found = _api("GET",
                     "/containers/json?all=1&filters="
                     + json.dumps({"name": [name]}).replace(" ", ""))
    except Exception:
        return None
    for c in found or []:
        if any(n.lstrip("/") == name for n in c.get("Names", [])):
            return c
    return None


def status() -> dict:
    """Zustand aller Zusatzdienste - Grundlage fuer die Anzeige im Dashboard."""
    available = is_docker() and socket_available()
    out = {
        "is_docker": is_docker(),
        "socket": available,
        "network": _self_network() if available else None,
        "services": [],
    }
    for key, spec in SERVICES.items():
        entry = {
            "key": key,
            "label": spec["label"],
            "purpose": spec["purpose"],
            "image": spec["image"],
            "container": spec["container"],
            "state": "unavailable",
            "running": False,
        }
        if available:
            c = _find_container(spec["container"])
            if c:
                entry["state"] = c.get("State", "unknown")
                entry["running"] = c.get("State") == "running"
            else:
                entry["state"] = "absent"
        out["services"].append(entry)
    return out


def _pull(image: str) -> None:
    """Image holen, falls es lokal fehlt (kann einen Moment dauern)."""
    name, _, tag = image.partition(":")
    _api("POST", f"/images/create?fromImage={name}&tag={tag or 'latest'}", timeout=900)


def _gen_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _create_volume(name: str) -> None:
    try:
        _api("POST", "/volumes/create", {"Name": name})
    except DockerError:
        pass   # existiert bereits


def enable(key: str) -> dict:
    """
    Dienst anlegen und starten. Laeuft er schon, wird er nur gestartet.
    Danach stehen die passenden Werte in den Einstellungen bereit.
    """
    spec = SERVICES.get(key)
    if not spec:
        raise DockerError(f"Unbekannter Dienst: {key}")
    if not is_docker():
        raise DockerError("Diese Funktion gibt es nur im Container-Betrieb.")
    if not socket_available():
        raise DockerError(
            "Der Docker-Socket ist nicht erreichbar. In docker-compose.yml "
            "unter 'volumes' die Zeile '/var/run/docker.sock:/var/run/docker.sock' "
            "aktivieren und 'docker compose up -d' ausfuehren."
        )

    network = _self_network()
    name = spec["container"]
    existing = _find_container(name)

    if not existing:
        _pull(spec["image"])
        cfg: dict[str, Any] = {
            "Image": spec["image"],
            "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
        }
        if network:
            cfg["HostConfig"]["NetworkMode"] = network

        if key == "guacd":
            # Aufzeichnungen sollen im selben Ordner landen wie beim Backend.
            host_rec = _host_path_for("/app/backend/recordings")
            if host_rec:
                cfg["HostConfig"]["Binds"] = [f"{host_rec}:/recordings"]
        elif key == "db":
            password = db.get_setting("docker_db_password") or _gen_password()
            root_pw = db.get_setting("docker_db_root_password") or _gen_password()
            _create_volume(spec["volume"])
            cfg["Env"] = [
                f"MARIADB_ROOT_PASSWORD={root_pw}",
                f"MARIADB_DATABASE={spec['database']}",
                f"MARIADB_USER={spec['user']}",
                f"MARIADB_PASSWORD={password}",
            ]
            cfg["HostConfig"]["Binds"] = [f"{spec['volume']}:/var/lib/mysql"]
            db.set_setting("docker_db_password", password)
            db.set_setting("docker_db_root_password", root_pw)

        _api("POST", f"/containers/create?name={name}", cfg, timeout=120)

    _api("POST", f"/containers/{name}/start", timeout=120)

    # --- Einstellungen vorbelegen -----------------------------------------
    if key == "guacd":
        # Ansprechbar ueber den Containernamen im gemeinsamen Netzwerk.
        db.set_setting("guacd_host", name)
        db.set_setting("guacd_port", str(spec["port"]))
    elif key == "db":
        db.set_setting("docker_db_host", name)
        db.set_setting("docker_db_port", str(spec["port"]))
        db.set_setting("docker_db_user", spec["user"])
        db.set_setting("docker_db_name", spec["database"])

    return {"ok": True, "service": key, "container": name}


def disable(key: str, remove: bool = True) -> dict:
    """
    Dienst stoppen (und den Container entfernen). Daten bleiben erhalten: das
    Datenbank-Volume wird bewusst NICHT geloescht, damit ein versehentliches
    Abschalten nichts vernichtet.
    """
    spec = SERVICES.get(key)
    if not spec:
        raise DockerError(f"Unbekannter Dienst: {key}")
    if not socket_available():
        raise DockerError("Docker-Socket nicht erreichbar.")

    name = spec["container"]
    if not _find_container(name):
        return {"ok": True, "service": key, "note": "war nicht vorhanden"}
    try:
        _api("POST", f"/containers/{name}/stop?t=10", timeout=60)
    except DockerError:
        pass
    if remove:
        _api("DELETE", f"/containers/{name}", timeout=60)
    return {"ok": True, "service": key}


def db_credentials() -> dict:
    """
    Zugangsdaten der dazugeschalteten Datenbank - fuellt das Formular unter
    Einstellungen -> Datenbank vor.
    """
    if not db.get_setting("docker_db_host"):
        return {}
    return {
        "type": "mysql",
        "host": db.get_setting("docker_db_host"),
        "port": int(db.get_setting("docker_db_port") or 3306),
        "user": db.get_setting("docker_db_user") or "rmm",
        "password": db.get_setting("docker_db_password") or "",
        "database": db.get_setting("docker_db_name") or "rapalle_rmm",
    }

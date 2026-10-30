# store.py - Einstellungen und gespeicherte Anmeldung ("Login speichern")
# -----------------------------------------------------------------------
# Einstellungen (Server, Benutzername, TLS-Pruefung, Laufwerk) -> settings.json
# im Datenordner (Windows: %APPDATA%\RapalleClient, Linux: ~/.config/rapalle-client).
# Der GERAETETOKEN liegt moeglichst im sicheren Speicher des Systems
# (secrets_store.py: Windows-Anmeldeinformationen bzw. Linux-Schluesselbund).
# Fehlt der Schluesselbund, wird der Token in device.token (Rechte 0600) abgelegt.
# Ein Passwort wird NIE gespeichert.

from __future__ import annotations

import json
import os
from pathlib import Path

SERVICE = "RAPALLE.net RMM Client"


class Store:
    def __init__(self, data_dir: Path | str):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "settings.json"
        self.settings = self._load()

    # ------------------------------------------------------------- Einstellungen
    def _load(self) -> dict:
        try:
            d = json.loads(self.file.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def get(self, key: str, default=None):
        return self.settings.get(key, default)

    def reload(self) -> None:
        """Neu einlesen (ein anderer App-Prozess - z.B. der Link-Starter - kann geaendert haben)."""
        fresh = self._load()
        if fresh:
            self.settings = fresh

    def set(self, **kw) -> None:
        self.settings.update(kw)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.settings, indent=1), encoding="utf-8")
        tmp.replace(self.file)

    # ------------------------------------------------------------- Geraetetoken
    def _account(self) -> str:
        return f"{self.get('server', '')}|{self.get('username', '')}"

    @staticmethod
    def _keyring():
        from . import secrets_store
        return secrets_store if secrets_store.available() else None

    def save_token(self, token: str) -> str:
        """-> 'keyring' oder 'datei' (wo der Token gelandet ist)."""
        kr = self._keyring()
        if kr and kr.set_secret(self._account(), token):
            self._token_file().unlink(missing_ok=True)
            return "keyring"
        f = self._token_file()
        f.write_text(token, encoding="utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        return "datei"

    def load_token(self) -> str:
        kr = self._keyring()
        if kr:
            tok = kr.get_secret(self._account())
            if tok:
                return tok
        try:
            return self._token_file().read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def clear_token(self) -> None:
        kr = self._keyring()
        if kr:
            kr.delete_secret(self._account())
        self._token_file().unlink(missing_ok=True)
        self.set(device_id="")

    def _token_file(self) -> Path:
        return self.dir / "device.token"


def default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home()) / "RapalleClient"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "rapalle-client"

"""
vpn.py - WireGuard-Tunnel des RMM vollautomatisch verwalten (Windows/Linux)
==========================================================================

Fragt jede Minute GET /api/vpn/my ab (alle aktiven Tunnel, in denen der angemeldete
Benutzer Teilnehmer ist, mit fertiger WireGuard-Datei) und gleicht ab:
  * neuer Tunnel           -> Datei ablegen, Tunnel anlegen und verbinden
  * Datei geaendert        -> neu einrichten (z.B. neuer Endpunkt)
  * Tunnel weg/abgelaufen  -> Verbindung trennen und Tunnel loeschen
Nur Tunnel, die diese App selbst angelegt hat, werden je angefasst (Liste im
App-Speicher). Abmelden entfernt alle davon.

Windows: WireGuard fuer Windows muss installiert sein; das Einrichten als Dienst
         braucht Administratorrechte (einmalige UAC-Abfrage je Aenderung).
Linux:   wireguard-tools; Einrichten per pkexec (Passwortabfrage) bzw. direkt als root.
Android: nicht automatisch moeglich - dort die Datei/den QR-Code im Dashboard (App
         „VPN“) in die WireGuard-App uebernehmen.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"


def wg_exe() -> str | None:
    if IS_WINDOWS:
        p = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "WireGuard", "wireguard.exe")
        return p if os.path.exists(p) else shutil.which("wireguard.exe")
    return shutil.which("wg-quick")


def supported() -> tuple[bool, str]:
    if platform.system() not in ("Windows", "Linux"):
        return False, "Automatik nur unter Windows und Linux"
    if not wg_exe():
        return False, ("WireGuard für Windows installieren (wireguard.com/install)" if IS_WINDOWS
                       else "Paket „wireguard-tools“ installieren")
    return True, ""


def _conf_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "wireguard"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _elevated(script_lines: list[str]) -> tuple[int, str]:
    """Befehle mit Admin-/Root-Rechten ausfuehren (UAC bzw. pkexec).
    Windows: die Befehle kommen in eine temporaere .ps1-Datei, die per UAC-Abfrage mit
    Administratorrechten laeuft (keine Probleme mit Anfuehrungszeichen/Leerzeichen in Pfaden)."""
    if IS_WINDOWS:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".ps1", prefix="rmm-")
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write("$ErrorActionPreference = 'Continue'\r\n")
            f.write("\r\n".join(script_lines) + "\r\n")
            f.write("exit $LASTEXITCODE\r\n")
        ps = ("$p = Start-Process -FilePath powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
              f"-ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File \"{path}\"'); exit $p.ExitCode")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               creationflags=0x08000000)       # CREATE_NO_WINDOW
            out = (p.stdout or "") + (p.stderr or "")
            if "abgebrochen" in out.lower() or "canceled by the user" in out.lower():
                return 1, "Administratorrechte wurden abgelehnt."
            return p.returncode, out
        except Exception as e:  # noqa: BLE001
            return 1, str(e)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    sh = " ; ".join(script_lines)
    cmd = ["sh", "-c", sh] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["pkexec", "sh", "-c", sh]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def _dpapi_machine(data: bytes, name: str) -> bytes:
    """Konfiguration im Format der WireGuard-App verschluesseln (<name>.conf.dpapi): DPAPI mit
    Beschreibung = Tunnelname. CRYPTPROTECT_LOCAL_MACHINE, damit der WireGuard-Manager (SYSTEM)
    die Datei lesen kann - so steht der Tunnel sofort in der App, ohne Manager-Neustart."""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    flags = 0x1 | 0x4                                   # UI_FORBIDDEN | LOCAL_MACHINE
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob_in), ctypes.c_wchar_p(name), None, None, None,
                                                  flags, ctypes.byref(blob_out)):
        raise OSError(ctypes.GetLastError(), "CryptProtectData fehlgeschlagen")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


class _Lock:
    """Nur EIN Einrichten gleichzeitig (Hintergrund-Abgleich und „In PC-App aktivieren“ laufen in
    verschiedenen Prozessen - zwei gleichzeitige Installationen ergaben „Tunnel existiert bereits“)."""

    def __init__(self, data_dir: Path):
        self.path = _conf_dir(data_dir) / ".install.lock"
        self.fd = None

    def __enter__(self):
        import time as _t
        for _ in range(240):                                    # bis 2 min warten
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if _t.time() - self.path.stat().st_mtime > 300:   # liegengebliebene Sperre
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                _t.sleep(0.5)
        raise RuntimeError("Eine andere VPN-Einrichtung läuft noch – bitte gleich erneut versuchen.")

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
        finally:
            try:
                self.path.unlink()
            except OSError:
                pass


def install_locked(data_dir: Path, store, iface: str, conf: str, force: bool = False) -> bool:
    """Einrichten mit Sperre; prueft danach erneut, ob ein anderer Prozess es schon erledigt hat.
    Rueckgabe: True = eingerichtet, False = war schon aktuell."""
    h = hashlib.sha256(conf.encode()).hexdigest()[:16]
    with _Lock(data_dir):
        try:
            store.reload()
        except Exception:
            pass
        managed = dict(store.get("vpn_managed", {}) or {})
        if managed.get(iface) == h and not force:
            return False
        install(data_dir, iface, conf)
        try:
            store.reload()
        except Exception:
            pass
        managed = dict(store.get("vpn_managed", {}) or {})
        managed[iface] = h
        store.set(vpn_managed=managed)
        return True


def uninstall_locked(data_dir: Path, store, iface: str) -> None:
    with _Lock(data_dir):
        uninstall(data_dir, iface)
        try:
            store.reload()
        except Exception:
            pass
        managed = dict(store.get("vpn_managed", {}) or {})
        managed.pop(iface, None)
        store.set(vpn_managed=managed)


def install(data_dir: Path, iface: str, conf: str) -> None:
    path = _conf_dir(data_dir) / f"{iface}.conf"
    path.write_text(conf, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    exe = wg_exe()
    if IS_WINDOWS:
        # Verschluesselt fuer die WireGuard-App (sofort in ihrer Liste), dann mit Adminrechten:
        # alten Dienst nur entfernen, wenn es ihn gibt, warten bis er WIRKLICH weg ist (sonst
        # "Objekt bereits vorhanden"), Datei in die App-Ablage legen und Tunnel starten.
        dp = _conf_dir(data_dir) / f"{iface}.conf.dpapi"
        dp.write_bytes(_dpapi_machine(conf.encode("utf-8"), iface))
        code, out = _elevated([
            f"$exe = '{exe}'",
            f"$n = '{iface}'",
            f"$src = '{dp}'",
            "$d = Join-Path $env:ProgramFiles 'WireGuard\\Data\\Configurations'",
            "New-Item -ItemType Directory -Force -Path $d | Out-Null",
            "function Svc { Get-Service -Name ('WireGuardTunnel$' + $n) -ErrorAction SilentlyContinue }",
            "if (Svc) {",
            "  & $exe /uninstalltunnelservice $n | Out-Null",
            "  for ($i = 0; $i -lt 20 -and (Svc); $i++) { Start-Sleep -Milliseconds 500 }",
            "  if (Svc) {",
            "    # Dienst ist 'zum Loeschen markiert', solange der WireGuard-Manager ihn offen haelt",
            "    Restart-Service -Name 'WireGuardManager' -Force -ErrorAction SilentlyContinue",
            "    for ($i = 0; $i -lt 20 -and (Svc); $i++) { Start-Sleep -Milliseconds 500 }",
            "  }",
            "}",
            "if (Svc) { Write-Output 'Der alte WireGuard-Tunneldienst laesst sich nicht entfernen - bitte Rechner neu starten.'; exit 4 }",
            "Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $d ($n + '.conf')), (Join-Path $d ($n + '.conf.dpapi'))",
            "$dst = Join-Path $d ($n + '.conf.dpapi')",
            "Copy-Item -Force -LiteralPath $src -Destination $dst",
            "& $exe /installtunnelservice $dst | Out-Null",
            "for ($i = 0; $i -lt 20 -and -not (Svc); $i++) { Start-Sleep -Milliseconds 500 }",
            "if (-not (Svc)) { Write-Output 'Der WireGuard-Tunneldienst wurde nicht angelegt.'; exit 3 }",
            "exit 0",
        ])
        try:
            dp.unlink()
        except OSError:
            pass
    else:
        code, out = _elevated([f"install -m 600 '{path}' /etc/wireguard/{iface}.conf",
                               f"wg-quick down {iface} 2>/dev/null", f"wg-quick up {iface}"])
    if code != 0:
        raise RuntimeError(out.strip()[-300:] or f"Fehler {code}")


def uninstall(data_dir: Path, iface: str) -> None:
    exe = wg_exe()
    if exe:
        if IS_WINDOWS:
            _elevated([f"$exe = '{exe}'", f"$n = '{iface}'",
                       "if (Get-Service -Name ('WireGuardTunnel$' + $n) -ErrorAction SilentlyContinue) { & $exe /uninstalltunnelservice $n | Out-Null }",
                       "$d = Join-Path $env:ProgramFiles 'WireGuard\\Data\\Configurations'",
                       f"Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $d '{iface}.conf'), (Join-Path $d '{iface}.conf.dpapi')"])
        else:
            _elevated([f"wg-quick down {iface} 2>/dev/null", f"rm -f /etc/wireguard/{iface}.conf"])
    try:
        (_conf_dir(data_dir) / f"{iface}.conf").unlink()
    except OSError:
        pass


class VpnSync:
    """Hintergrund-Abgleich. get_api() liefert das aktuelle Api-Objekt (oder None)."""

    def __init__(self, store, data_dir: Path, get_api, on_status=None, interval: int = 60, refresh=None):
        self.store, self.data_dir, self.get_api = store, Path(data_dir), get_api
        self.refresh = refresh            # Sitzung erneuern (Geraetetoken), wenn die alte abgelaufen ist
        self.on_status = on_status or (lambda text: None)
        self.interval = interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._failed: dict[str, float] = {}     # "<iface>:<hash>" -> Zeit; fehlgeschlagene nicht minuetlich wiederholen

    def managed(self) -> dict:
        return dict(self.store.get("vpn_managed", {}) or {})          # iface -> hash

    def _set_managed(self, m: dict) -> None:
        self.store.set(vpn_managed=m)

    def start(self):
        if self._thread and self._thread.is_alive():
            self._wake.set()
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rmm-vpn-sync")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def now(self):
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.sync()
            except Exception as e:  # noqa: BLE001
                self.on_status(f"VPN: {e}")
            self._wake.wait(self.interval)
            self._wake.clear()

    def sync(self):
        try:
            self.store.reload()                 # Aenderungen anderer App-Prozesse (Link-Starter) uebernehmen
        except Exception:
            pass
        auto_on = bool(self.store.get("vpn_auto", True))
        if not auto_on and not (self.store.get("vpn_manual") or self.managed()):
            return
        ok, why = supported()
        api = self.get_api()
        if api is None:
            return
        from .api import ApiError
        try:
            data = api.request("GET", "/api/vpn/my")
        except ApiError as e:
            if e.status == 404:
                return                                   # Modul "vpn" ist auf dem Server aus
            if e.status != 401 or not self.refresh:
                raise
            self.refresh()
            data = self.get_api().request("GET", "/api/vpn/my")
        manual = set(self.store.get("vpn_manual", []) or [])          # per Dashboard-Knopf aktiviert
        off = set(self.store.get("vpn_off", []) or [])                 # im VPN-Bereich bewusst getrennt
        want = {t["iface"]: t for t in data.get("tunnels", [])
                if ((auto_on and t.get("auto", True)) or t["iface"] in manual) and t["iface"] not in off}
        gone = manual - {t["iface"] for t in data.get("tunnels", [])}
        if gone:
            self.store.set(vpn_manual=sorted(manual - gone))
        have = self.managed()
        if not ok:
            if want:
                self.on_status(f"VPN: {len(want)} Tunnel verfügbar – {why}")
            return
        changed = False
        for iface in [i for i in have if i not in want]:          # abgelaufen/entfernt/getrennt -> loeschen
            uninstall_locked(self.data_dir, self.store, iface)   # pflegt vpn_managed selbst
            have.pop(iface, None)
        errors = []
        for iface, t in want.items():
            h = hashlib.sha256(t["conf"].encode()).hexdigest()[:16]
            if have.get(iface) == h:
                continue
            import time as _t
            key = f"{iface}:{h}"
            if _t.time() - self._failed.get(key, 0) < 900:      # 15 min Pause (sonst staendige UAC-/Passwort-Abfragen)
                errors.append(f"{t['name']}: Einrichtung fehlgeschlagen – neuer Versuch später")
                continue
            try:
                install_locked(self.data_dir, self.store, iface, t["conf"])   # pflegt vpn_managed selbst
                have[iface] = h
                self._failed.pop(key, None)
            except Exception as e:  # noqa: BLE001
                self._failed[key] = _t.time()
                errors.append(f"{t['name']}: {str(e).strip().splitlines()[-1][:160] if str(e).strip() else e}")
        names = ", ".join(t["name"] for t in want.values())
        self.on_status(("VPN-Fehler: " + "; ".join(errors)) if errors else
                       (f"VPN verbunden: {names}" if want else "Kein VPN-Tunnel aktiv"))

    def remove_all(self):
        for iface in list(self.managed()):
            uninstall(self.data_dir, iface)
        self._set_managed({})
        self.store.set(vpn_manual=[])

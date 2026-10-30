"""
vpnpanel.py - VPN-Bereich IM Hauptfenster der Client-App (kein Extra-Fenster)
=============================================================================

Zeigt alle Tunnel, in denen der angemeldete Benutzer Teilnehmer ist, mit Zustand in Echtzeit:
  * Zustand auf DIESEM Rechner (WireGuard-Dienst laeuft / aus / fehlt) - alle 3 s
  * Liste vom Server (GET /api/vpn/my) - alle 15 s und sofort nach jeder Aktion
Knoepfe je Tunnel: Aktivieren, Deaktivieren, Entfernen.
„In PC-App aktivieren“ im Dashboard landet ebenfalls hier (der Link-Starter reicht die
Anforderung an die laufende App weiter, statt ein eigenes Fenster zu oeffnen).

Damit gibt es genau EINE Stelle, die Tunnel einrichtet (plus Sperre in vpn.install_locked) -
keine doppelten Installationen mehr.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import vpn

REQ = "vpn_request.json"        # Anforderung vom Link-Starter
ALIVE = "app.alive"             # Lebenszeichen der Hauptapp (Link-Starter prueft es)


def local_state(iface: str) -> str:
    """'aktiv' | 'aus' | 'fehlt' | 'über Agent' fuer den WireGuard-Tunnel auf diesem Rechner."""
    try:
        if vpn.siblings(iface) and not _own_exists(iface):
            return "über Agent"
        if vpn.IS_WINDOWS:
            r = subprocess.run(["sc", "query", "WireGuardTunnel$" + iface], capture_output=True, text=True,
                               timeout=10, creationflags=0x08000000)
            if r.returncode != 0:
                return "fehlt"
            return "aktiv" if "RUNNING" in r.stdout else "aus"
        return "aktiv" if Path(f"/sys/class/net/{iface}").exists() else "fehlt"
    except Exception:
        return "?"


def _own_exists(iface: str) -> bool:
    try:
        if vpn.IS_WINDOWS:
            return subprocess.run(["sc", "query", "WireGuardTunnel$" + iface], capture_output=True, timeout=10,
                                  creationflags=0x08000000).returncode == 0
        return Path(f"/sys/class/net/{iface}").exists()
    except Exception:
        return False


def app_alive(data_dir: Path) -> bool:
    try:
        return time.time() - (Path(data_dir) / ALIVE).stat().st_mtime < 8
    except OSError:
        return False


def forward_request(data_dir: Path, tunnel: str) -> None:
    p = Path(data_dir) / REQ
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tunnel": tunnel, "ts": time.time()}), encoding="utf-8")
    tmp.replace(p)


class VpnPanel:
    def __init__(self, gui, parent, data_dir: Path):
        self.gui, self.data_dir = gui, Path(data_dir)
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="x", pady=(4, 6))
        head = ttk.Frame(self.frame)
        head.pack(fill="x")
        ttk.Label(head, text="VPN-Tunnel", style="Sub.TLabel").pack(side="left")
        ttk.Button(head, text="↻", width=3, command=self.refresh_now).pack(side="right")
        self.rows = ttk.Frame(self.frame)
        self.rows.pack(fill="x", pady=(4, 0))
        self.msg = ttk.Label(self.frame, text="", style="Sub.TLabel", wraplength=360, justify="left")
        self.msg.pack(anchor="w", pady=(4, 0))
        self.tunnels: list[dict] = []
        self.states: dict[str, str] = {}
        self.busy: set[str] = set()
        self._last_server = 0.0
        self._alive = True
        self._tick()

    # ------------------------------------------------------------ Hintergrund
    def _tick(self):
        if not self._alive or not self.frame.winfo_exists():
            return
        try:
            (self.data_dir / ALIVE).write_text(str(time.time()))
        except OSError:
            pass
        self._check_request()
        if time.time() - self._last_server > 15:
            self._last_server = time.time()
            threading.Thread(target=self._load_server, daemon=True).start()
        threading.Thread(target=self._load_states, daemon=True).start()
        self.frame.after(3000, self._tick)

    def stop(self):
        self._alive = False
        try:
            (self.data_dir / ALIVE).unlink()
        except OSError:
            pass

    def refresh_now(self):
        self._last_server = 0

    def _load_server(self):
        api = self.gui.api
        if api is None:
            return
        try:
            data = api.request("GET", "/api/vpn/my")
            self.tunnels = data.get("tunnels", [])
        except Exception as e:  # noqa: BLE001
            if getattr(e, "status", None) == 404:
                self.tunnels = []
            else:
                self._ui(lambda: self.msg.config(text=f"VPN: {e}"))
        self._ui(self.draw)

    def _load_states(self):
        st = {t["iface"]: local_state(t["iface"]) for t in self.tunnels}
        if st != self.states:
            self.states = st
            self._ui(self.draw)

    def _ui(self, fn):
        try:
            self.frame.after(0, fn)
        except Exception:
            pass

    # ------------------------------------------------------------ Anzeige
    def draw(self):
        if not self.frame.winfo_exists():
            return
        for w in self.rows.winfo_children():
            w.destroy()
        if not self.tunnels:
            ttk.Label(self.rows, text="Kein VPN-Tunnel für dich freigegeben.", style="Sub.TLabel").pack(anchor="w")
            return
        for t in self.tunnels:
            iface = t["iface"]
            st = "arbeitet …" if iface in self.busy else self.states.get(iface, "…")
            row = ttk.Frame(self.rows)
            row.pack(fill="x", pady=2)
            dot = {"aktiv": "🟢", "über Agent": "🟢", "aus": "⚪", "fehlt": "⚪"}.get(st, "🟡")
            exp = ""
            if t.get("expires"):
                left = int(t["expires"] - time.time())
                exp = f" · noch {left // 3600} h {left % 3600 // 60} min" if left > 0 else " · abgelaufen"
            ttk.Label(row, text=f"{dot} {t['name']}  ({st}{exp})").pack(side="left")
            if iface in self.busy:
                continue
            if st == "über Agent":
                ttk.Label(row, text="(dieser PC ist als Agent im Tunnel)", style="Sub.TLabel").pack(side="right")
            elif st == "aktiv":
                ttk.Button(row, text="Trennen", command=lambda t=t: self.deactivate(t)).pack(side="right")
            else:
                ttk.Button(row, text="Verbinden", command=lambda t=t: self.activate(t)).pack(side="right")

    # ------------------------------------------------------------ Aktionen
    def activate(self, t: dict):
        iface = t["iface"]
        if iface in self.busy:                           # laeuft schon - nicht doppelt einrichten
            return
        manual = set(self.gui.store.get("vpn_manual", []) or [])
        manual.add(iface)
        off = set(self.gui.store.get("vpn_off", []) or [])
        off.discard(iface)
        self.gui.store.set(vpn_manual=sorted(manual), vpn_off=sorted(off))
        self._run(iface, lambda: vpn.install_locked(self.data_dir, self.gui.store, iface, t["conf"], force=True),
                  f"„{t['name']}“ verbunden")

    def deactivate(self, t: dict):
        iface = t["iface"]
        off = set(self.gui.store.get("vpn_off", []) or [])
        off.add(iface)
        self.gui.store.set(vpn_off=sorted(off))

        def work():
            vpn.uninstall_locked(self.data_dir, self.gui.store, iface)
        self._run(iface, work, f"„{t['name']}“ getrennt")

    def _run(self, iface, fn, ok_text):
        self.busy.add(iface)
        self.draw()
        self.msg.config(text="Windows fragt nach Administratorrechten …" if vpn.IS_WINDOWS else "Bitte bestätigen …")

        def work():
            try:
                fn()
                text = ok_text
                note = vpn.LAST_NOTE.pop(iface, "")
                if note:
                    text += f" – {note}"
            except Exception as e:  # noqa: BLE001
                lines = [ln for ln in str(e).strip().splitlines() if ln.strip()]
                text = "Fehler: " + ("\n".join(lines[-8:])[:900] if lines else str(e))
                text += f"\n(Protokoll: {self.data_dir / 'wireguard' / 'vpn.log'})"
            self.busy.discard(iface)
            self.states[iface] = local_state(iface)
            self._ui(lambda: (self.msg.config(text=text), self.draw()))
        threading.Thread(target=work, daemon=True).start()

    def _check_request(self):
        """„In PC-App aktivieren“ aus dem Dashboard (vom Link-Starter weitergereicht)."""
        p = self.data_dir / REQ
        try:
            req = json.loads(p.read_text(encoding="utf-8"))
            p.unlink()
        except (OSError, ValueError):
            return
        # dieselbe Anforderung mehrfach (Doppelklick, Browser oeffnet den Link zweimal) -> nur einmal
        key = req.get("tunnel")
        if key == getattr(self, "_last_req", None) and time.time() - getattr(self, "_last_req_ts", 0) < 30:
            return
        self._last_req, self._last_req_ts = key, time.time()
        try:
            self.gui.root.deiconify()
            self.gui.root.lift()
        except Exception:
            pass

        def work():
            for _ in range(2):
                self._load_server()
                t = next((x for x in self.tunnels if x["tunnel"] == req.get("tunnel")), None)
                if t:
                    self._ui(lambda: self.activate(t))
                    return
                time.sleep(1)
            self._ui(lambda: self.msg.config(text="Der angeforderte VPN-Tunnel wurde nicht gefunden oder ist abgelaufen."))
        threading.Thread(target=work, daemon=True).start()

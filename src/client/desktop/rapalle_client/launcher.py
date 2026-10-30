# launcher.py - rapalle-rmm://launch?s=<server>&t=<startschein> ausfuehren
# ------------------------------------------------------------------------
# Das Betriebssystem startet die App mit dem Link (urlscheme.py). Ablauf:
#   1) Link gehoert zum eingerichteten Server? (sonst Abbruch - fremde Server/Seiten nie)
#   2) mit der gespeicherten Anmeldung den Startschein einloesen (gehoert zum Benutzer)
#   3) shell -> RMM-Terminal im eigenen App-Fenster
#      rdp/vnc/ssh -> /api/client/connect -> ggf. Cloudflare-Tunnel (cloudflared access tcp)
#      -> passende Software lokal starten (mstsc/xfreerdp, VNC-Viewer, ssh im Terminal)
#   4) Tunnel: kleines Fenster "Verbindung aktiv · Trennen"; alle 60 s Meldung an den
#      Server (Lease). Programm beendet bzw. "Trennen" -> Tunnel zu, Lease frei.
from __future__ import annotations

import threading
import time
import tkinter as tk
import urllib.parse
from tkinter import messagebox

import hashlib

from . import browser, features, tunnel, vpn
from .api import Api, ApiError, normalize_server
from .store import Store, default_data_dir

BG, FG, SUB, ACC = "#0b1220", "#e6edf6", "#8fa1ba", "#2dd4bf"
LABEL = {"rdp": "Remotedesktop", "vnc": "VNC", "ssh": "SSH", "shell": "RMM-Terminal", "vpn": "VPN-Tunnel"}


def parse(url: str) -> tuple[str, str]:
    u = urllib.parse.urlparse(url)
    if u.scheme != "rapalle-rmm" or (u.netloc or u.path.strip("/")) != "launch":
        raise ValueError("Unbekannter Link")
    q = dict(urllib.parse.parse_qsl(u.query))
    if not q.get("s") or not q.get("t"):
        raise ValueError("Link unvollständig")
    return q["s"], q["t"]


class Launcher:
    def __init__(self, url: str):
        self.url = url
        self.root = tk.Tk()
        self.root.title("RAPALLE.net RMM")
        self.root.configure(bg=BG)
        self.root.geometry("420x150")
        self.root.resizable(False, False)
        self.title = tk.Label(self.root, text="Verbinde …", bg=BG, fg=FG, font=("Segoe UI", 11, "bold"), anchor="w")
        self.title.pack(fill="x", padx=16, pady=(16, 2))
        self.msg = tk.Label(self.root, text="", bg=BG, fg=SUB, font=("Segoe UI", 9), anchor="w", justify="left",
                            wraplength=390)
        self.msg.pack(fill="x", padx=16)
        self.btn = tk.Button(self.root, text="Abbrechen", command=self.finish, bg="#1b2a42", fg=FG,
                             activebackground="#24324a", relief="flat", padx=14, pady=4)
        self.btn.pack(side="bottom", anchor="e", padx=16, pady=14)
        self.root.protocol("WM_DELETE_WINDOW", self.finish)
        self.store = Store(default_data_dir())
        self.api: Api | None = None
        self.cf = None
        self.prog = None
        self.lease = ""
        self.done = False
        threading.Thread(target=self.work, daemon=True).start()

    # ---------------------------------------------------------- Oberflaeche (thread-sicher)
    def ui(self, fn):
        self.root.after(0, fn)

    def status(self, title=None, msg=None):
        def f():
            if title is not None:
                self.title.config(text=title)
            if msg is not None:
                self.msg.config(text=msg)
        self.ui(f)

    def fail(self, text: str):
        def f():
            messagebox.showerror("RAPALLE.net RMM", text, parent=self.root)
            self.finish()
        self.ui(f)

    # ---------------------------------------------------------- Ablauf
    def work(self):
        try:
            server_link, ticket = parse(self.url)
            server, token = self.store.get("server"), self.store.load_token()
            if not (server and token):
                return self.fail("Die Client-App ist noch nicht angemeldet. Bitte zuerst die App öffnen und anmelden.")
            if normalize_server(server_link) != normalize_server(server):
                return self.fail(f"Der Link gehört zu einem anderen Server ({server_link}).\nEingerichtet ist {server}.")
            self.api = Api(server, verify_tls=not self.store.get("insecure", False))
            self.api.session(token)
            p = self.api.redeem(ticket)
            proto = p["protocol"]
            self.status(f"{LABEL.get(proto, proto)} wird geöffnet …")
            if p.get("vpn_tunnel"):
                t = self.ensure_vpn(p["vpn_tunnel"])
                if proto == "vpn":
                    self.status("VPN-Tunnel aktiv", f"„{t['name']}“ ist in WireGuard eingerichtet und verbunden.")
                    return self.ui(lambda: self.root.after(2500, self.finish))
                self.status(msg="VPN verbunden – warte kurz auf den Tunnel …")
                time.sleep(3)
            if proto == "shell":
                browser.open_app_window(self.api.dashboard_url("terminal", p["client_id"]),
                                        default_data_dir() / "browser", size=(1000, 640))
                return self.ui(lambda: self.root.after(800, self.finish))
            res = self.api.connect(p["client_id"], proto, p.get("host_mode", "local"), p.get("host", ""),
                                   p.get("port_mode", "standard"), p.get("port"), bool(p.get("remember")),
                                   p.get("lifetime", "session"), p.get("user", ""))
            host, port, alias = res["host"], int(res["port"]), ""
            if res.get("tunnel"):
                self.lease = res["lease"]
                self.status(msg=f"Tunnel zu {res['hostname']} wird aufgebaut …")
                self.cf, lport = tunnel.start(default_data_dir(), res["hostname"], res["token_id"], res["token_secret"],
                                              progress=lambda m: self.status(msg=m))
                host, port, alias = "127.0.0.1", lport, res["hostname"]
            text, self.prog = features.start_program(proto, host, port, res.get("user") or p.get("user", ""), alias)
            if not res.get("tunnel"):
                self.status(text, f"{host}:{port}")
                return self.ui(lambda: self.root.after(1500, self.finish))
            life = res.get("lifetime")
            life_txt = ("bleibt dauerhaft offen" if life == 0 else "schließt nach Nutzungsende" if life == "session"
                        else "offen bis " + time.strftime("%H:%M", time.localtime(res.get("expires") or 0)))
            self.status(f"{LABEL[proto]} über RMM-Tunnel aktiv", f"{res['hostname']} → 127.0.0.1:{port}\nTunnel {life_txt}.")
            self.ui(lambda: self.btn.config(text="Trennen"))
            self.watch()
        except (ApiError, ValueError, tunnel.TunnelError, features.FeatureError, KeyError) as e:
            self.fail(str(e))
        except Exception as e:      # noqa: BLE001
            self.fail(f"Fehler: {e}")

    def ensure_vpn(self, tid: str) -> dict:
        """Eigenen Tunnel holen und SOFORT in WireGuard einrichten/aktivieren (auch ohne Automatik)."""
        data = self.api.request("GET", "/api/vpn/my")
        t = next((x for x in data.get("tunnels", []) if x["tunnel"] == tid), None)
        if not t:
            raise ValueError("Der VPN-Tunnel wurde nicht gefunden oder ist abgelaufen.")
        ok, why = vpn.supported()
        if not ok:
            raise ValueError(f"VPN nicht möglich: {why}")
        # ZUERST als "manuell" merken - sonst koennte der Hintergrund-Abgleich der laufenden App den
        # frisch eingerichteten Tunnel wieder entfernen (wenn er nicht automatisch verwaltet wird)
        self.store.reload()
        manual = set(self.store.get("vpn_manual", []) or [])
        manual.add(t["iface"])
        self.store.set(vpn_manual=sorted(manual))
        self.status(msg="VPN-Tunnel wird in WireGuard eingerichtet (Administratorrechte nötig) …")
        vpn.install_locked(default_data_dir(), self.store, t["iface"], t["conf"])
        return t

    def watch(self):
        started, last_ping = time.time(), time.time()
        while not self.done:
            time.sleep(1)
            if self.cf and self.cf.poll() is not None:
                return self.fail("Der Tunnel wurde unterbrochen.")
            # Programm beendet (nicht sofort - manche Programme geben gleich wieder ab)
            if self.prog is not None and self.prog.poll() is not None and time.time() - started > 8:
                return self.ui(self.finish)
            if time.time() - last_ping > 60:
                last_ping = time.time()
                try:
                    self.api.tunnel_ping(self.lease)
                except ApiError as e:
                    if e.status == 404:
                        return self.fail("Der Tunnel wurde auf dem Server geschlossen.")

    def finish(self):
        if self.done:
            return
        self.done = True
        tunnel.stop(self.cf)
        if self.lease and self.api:
            try:
                self.api.tunnel_release(self.lease)
            except Exception:
                pass
        self.root.after(100, self.root.destroy)

    def run(self):
        self.root.mainloop()


def handle(url: str) -> None:
    Launcher(url).run()

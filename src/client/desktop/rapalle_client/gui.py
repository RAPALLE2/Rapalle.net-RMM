# gui.py - Oberflaeche des Desktop-Clients (tkinter = Teil von Python, keine Abhaengigkeiten)
# -------------------------------------------------------------------------------------------
#   Anmeldemaske  Server, Benutzer, Passwort, Verzeichnis (SSO), 2FA-Code, Geraetename
#   Hauptfenster  Dashboard oeffnen, Relay-Laufwerk, Remote-Shell, Fernbild (RDP/VNC),
#                 Autostart, Abmelden
# Netzwerk laeuft in Hintergrund-Threads; Ergebnisse kommen per root.after zurueck.

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import __version__, autostart, browser, features
from .api import Api, ApiError, default_device_name
from .store import Store, default_data_dir

ACCENT = "#2dd4bf"
BG = "#0f172a"
PANEL = "#1e293b"
FG = "#e2e8f0"
SUB = "#94a3b8"


def resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for p in (base / name, base / "resources" / name, Path(__file__).resolve().parent / "resources" / name):
        if p.exists():
            return p
    return base / name


class ClientApp:
    def __init__(self, minimized: bool = False):
        self.store = Store(default_data_dir())
        self.api: Api | None = None
        self.root = tk.Tk()
        self.root.title("RAPALLE.net RMM")
        self.root.geometry("440x600")
        self.root.minsize(400, 520)
        self.root.configure(bg=BG)
        self._icon()
        self._style()
        # Strg+A = alles markieren (Tk-Standard unter Linux waere "Zeilenanfang")
        for cls in ("TEntry", "Entry"):
            self.root.bind_class(cls, "<Control-a>", self._select_all)
            self.root.bind_class(cls, "<Control-A>", self._select_all)
        self.frame = None
        if minimized:
            self.root.iconify()
        self.show_busy("Verbinde …")
        self.root.after(50, self.auto_login)

    # ------------------------------------------------------------- Optik
    def _icon(self):
        try:
            if os.name == "nt" and resource("rmmclient.ico").exists():
                self.root.iconbitmap(default=str(resource("rmmclient.ico")))
            elif resource("rmmclient-64.png").exists():
                self._img = tk.PhotoImage(file=str(resource("rmmclient-64.png")))
                self.root.iconphoto(True, self._img)
        except Exception:
            pass

    def _style(self):
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=("Segoe UI", 10))
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Sub.TLabel", foreground=SUB, font=("Segoe UI", 9))
        st.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        st.configure("TEntry", fieldbackground=PANEL, foreground=FG, insertcolor=FG, bordercolor="#334155", padding=6)
        st.configure("TCombobox", fieldbackground=PANEL, foreground=FG, background=PANEL, padding=5)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.map("TCheckbutton", background=[("active", BG)])
        st.configure("TButton", background=PANEL, foreground=FG, bordercolor="#334155", padding=(12, 9), relief="flat")
        st.map("TButton", background=[("active", "#334155"), ("disabled", BG)], foreground=[("disabled", "#475569")])
        st.configure("Accent.TButton", background=ACCENT, foreground="#0c1420", font=("Segoe UI", 10, "bold"))
        st.map("Accent.TButton", background=[("active", "#5eead4"), ("disabled", "#334155")])
        st.configure("Danger.TButton", foreground="#f87171")

    @staticmethod
    def _select_all(event):
        event.widget.select_range(0, "end")
        event.widget.icursor("end")
        return "break"

    def _new_frame(self):
        if self.frame is not None:
            self.frame.destroy()
        self.frame = ttk.Frame(self.root, padding=22)
        self.frame.pack(fill="both", expand=True)
        return self.frame

    def bg(self, fn, done, *args):
        """fn(*args) im Hintergrund; done(result, error) danach im UI-Thread."""
        def run():
            try:
                res, err = fn(*args), None
            except Exception as e:      # noqa: BLE001 - Meldung geht an den Benutzer
                res, err = None, e
            self.root.after(0, lambda: done(res, err))
        threading.Thread(target=run, daemon=True).start()

    def show_busy(self, text):
        f = self._new_frame()
        ttk.Label(f, text="RAPALLE.net RMM", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text=text, style="Sub.TLabel").pack(anchor="w", pady=(10, 0))

    # ------------------------------------------------------------- Start
    def auto_login(self):
        server, token = self.store.get("server"), self.store.load_token()
        if not (server and token):
            return self.show_login()
        api = Api(server, verify_tls=not self.store.get("insecure", False))

        def done(res, err):
            if err is None:
                self.api = api
                self.show_main()
                if self.store.get("open_on_start", True):
                    self.open_dashboard()
            elif isinstance(err, ApiError) and err.status == 401:
                self.store.clear_token()
                self.show_login(f"Bitte neu anmelden: {err}")
            else:
                self.show_offline(str(err))
        self.bg(api.session, done, token)

    def show_offline(self, msg):
        f = self._new_frame()
        ttk.Label(f, text="Keine Verbindung", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text=msg, style="Sub.TLabel", wraplength=380).pack(anchor="w", pady=(8, 16))
        ttk.Button(f, text="Erneut versuchen", style="Accent.TButton", command=self.auto_login).pack(fill="x")
        ttk.Button(f, text="Anders anmelden", command=self.show_login).pack(fill="x", pady=(8, 0))

    # ------------------------------------------------------------- Anmeldung
    def show_login(self, message: str = ""):
        f = self._new_frame()
        ttk.Label(f, text="RAPALLE.net RMM", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text=f"Client {__version__} – einmal anmelden, danach bleibt die Anmeldung gespeichert.",
                  style="Sub.TLabel", wraplength=390).pack(anchor="w", pady=(2, 12))
        self.v = {k: tk.StringVar(value=val) for k, val in {
            "server": self.store.get("server", ""), "user": self.store.get("username", ""), "pass": "",
            "code": "", "name": self.store.get("device_name") or default_device_name()}.items()}
        self.v_insecure = tk.BooleanVar(value=bool(self.store.get("insecure", False)))

        def field(label, key, show=None):
            ttk.Label(f, text=label, style="Sub.TLabel").pack(anchor="w", pady=(6, 1))
            e = ttk.Entry(f, textvariable=self.v[key], show=show)
            e.pack(fill="x")
            return e

        e_server = field("Server-Adresse", "server")
        e_server.bind("<FocusOut>", lambda e: self.load_realms())
        field("Benutzername", "user")
        e_pass = field("Passwort", "pass", show="•")
        ttk.Label(f, text="Anmelden über", style="Sub.TLabel").pack(anchor="w", pady=(6, 1))
        self.realm_ids, self.cb_realm = ["local"], ttk.Combobox(f, values=["Lokales Konto"], state="readonly")
        self.cb_realm.current(0)
        self.cb_realm.pack(fill="x")
        field("Einmalcode (2FA) – nur wenn aktiviert", "code")
        field("Name dieses Geräts", "name")
        ttk.Checkbutton(f, text="Zertifikat nicht prüfen (selbst signiert)", variable=self.v_insecure).pack(anchor="w", pady=(8, 0))
        self.btn_login = ttk.Button(f, text="Anmelden", style="Accent.TButton", command=self.do_login)
        self.btn_login.pack(fill="x", pady=(14, 0))
        self.lbl_status = ttk.Label(f, text=message, style="Sub.TLabel", wraplength=390)
        self.lbl_status.pack(anchor="w", pady=(10, 0))
        e_pass.bind("<Return>", lambda e: self.do_login())
        (e_pass if self.v["server"].get() and self.v["user"].get() else e_server).focus_set()
        if self.v["server"].get():
            self.load_realms()

    def load_realms(self):
        try:
            api = Api(self.v["server"].get(), verify_tls=not self.v_insecure.get())
        except ApiError:
            return

        def done(realms, err):
            if err or not realms or not self.cb_realm.winfo_exists():
                return
            self.realm_ids = ["local"] + [r["id"] for r in realms]
            self.cb_realm["values"] = ["Lokales Konto"] + [r["name"] for r in realms]
        self.bg(api.realms, done)

    def do_login(self):
        try:
            api = Api(self.v["server"].get(), verify_tls=not self.v_insecure.get())
        except ApiError as e:
            self.lbl_status.config(text=f"Fehler: {e}")
            return
        idx = self.cb_realm.current() if self.cb_realm.current() >= 0 else 0
        realm = self.realm_ids[idx] if idx < len(self.realm_ids) else "local"
        user, pw, code, name = self.v["user"].get().strip(), self.v["pass"].get(), self.v["code"].get().strip(), self.v["name"].get().strip()
        self.btn_login.state(["disabled"])
        self.lbl_status.config(text="Melde an …")

        def work():
            api.login(user, pw, realm, code)
            return api.register_device(name)

        def done(res, err):
            self.btn_login.state(["!disabled"])
            if err:
                msg = str(err)
                self.lbl_status.config(text="Bitte den Einmalcode aus der Authenticator-App eingeben." if msg == "2fa_required" else f"Fehler: {msg}")
                return
            self.store.set(server=api.server, username=user, insecure=bool(self.v_insecure.get()),
                           device_name=name, device_id=res["device"]["id"])
            self.store.save_token(res["device_token"])
            self.api = api
            self.show_main()
            self.open_dashboard()
        self.bg(work, done)

    # ------------------------------------------------------------- Hauptfenster
    def show_main(self):
        f = self._new_frame()
        ttk.Label(f, text="RAPALLE.net RMM", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text=f"Angemeldet als {self.store.get('username')}\n{self.api.server}",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 16))

        def btn(text, cmd, style="TButton"):
            b = ttk.Button(f, text=text, command=cmd, style=style)
            b.pack(fill="x", pady=3)
            return b

        btn("Dashboard öffnen", self.open_dashboard, "Accent.TButton")
        mounted = bool(self.store.get("drive_letter") or self.store.get("linux_mount"))
        self.btn_mount = btn("Relay-Laufwerk trennen" if mounted else "Relay als Laufwerk verbinden", self.toggle_mount)
        btn("Remote-Shell …", lambda: self.choose_client("Remote-Shell", self.open_shell))
        btn("Fernbild (Remotedesktop) …", lambda: self.choose_client("Fernbild per Remotedesktop", lambda c: self.screen(c, "rdp")))
        btn("Fernbild (VNC) …", lambda: self.choose_client("Fernbild per VNC", lambda c: self.screen(c, "vnc")))
        ttk.Separator(f).pack(fill="x", pady=12)
        self.v_auto = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(f, text="Mit der Anmeldung am Computer starten", variable=self.v_auto,
                        command=self.toggle_autostart).pack(anchor="w")
        self.v_open = tk.BooleanVar(value=bool(self.store.get("open_on_start", True)))
        ttk.Checkbutton(f, text="Dashboard beim Start öffnen", variable=self.v_open,
                        command=lambda: self.store.set(open_on_start=bool(self.v_open.get()))).pack(anchor="w", pady=(4, 0))
        btn("Dieses Gerät abmelden", self.logout, "Danger.TButton").pack_configure(pady=(14, 0))
        self.lbl_info = ttk.Label(f, text="", style="Sub.TLabel", wraplength=390)
        self.lbl_info.pack(anchor="w", pady=(10, 0))

    def info(self, text):
        if getattr(self, "lbl_info", None) and self.lbl_info.winfo_exists():
            self.lbl_info.config(text=text)

    # ------------------------------------------------------------- Aktionen
    def _fresh(self, then):
        """Vor jedem Oeffnen eine frische Sitzung holen (Token laeuft so nie ab)."""
        def done(res, err):
            if err:
                if isinstance(err, ApiError) and err.status == 401:
                    self.store.clear_token()
                    return self.show_login(f"Abgemeldet: {err}")
                return messagebox.showerror("RAPALLE.net RMM", str(err))
            then()
        self.bg(self.api.session, done, self.store.load_token())

    def open_dashboard(self):
        self._fresh(lambda: self.info("Dashboard geöffnet in " + browser.open_app_window(
            self.api.dashboard_url(), default_data_dir() / "browser")))

    def open_shell(self, client):
        self._fresh(lambda: browser.open_app_window(self.api.dashboard_url("terminal", client["id"]),
                                                    default_data_dir() / "browser", size=(1000, 640)))

    def screen(self, client, protocol):
        def done(fw, err):
            if err:
                return messagebox.showerror("Fernbild", str(err))
            try:
                msg = features.launch_screen(protocol, fw["host"], int(fw["port"]))
                self.info(f"{msg}  ({fw['host']}:{fw['port']}, nur von diesem Gerät erreichbar)")
            except features.FeatureError as e:
                messagebox.showerror("Fernbild", str(e))
        self.bg(self.api.screen, done, client["id"], protocol)

    def toggle_mount(self):
        mounted = bool(self.store.get("drive_letter") or self.store.get("linux_mount"))
        if mounted:
            fn, args = features.unmount_relay, (self.store,)
        else:
            fn, args = features.mount_relay, (self.api.server, self.store.get("username", ""), self.store.load_token(), self.store)

        def done(msg, err):
            if err:
                return messagebox.showerror("Relay-Laufwerk", str(err))
            self.info(msg)
            now = bool(self.store.get("drive_letter") or self.store.get("linux_mount"))
            self.btn_mount.config(text="Relay-Laufwerk trennen" if now else "Relay als Laufwerk verbinden")
        self.info("Bitte warten …")
        self.bg(fn, done, *args)

    def toggle_autostart(self):
        try:
            autostart.set_enabled(bool(self.v_auto.get()))
        except Exception as e:      # noqa: BLE001
            messagebox.showerror("Autostart", str(e))
            self.v_auto.set(autostart.is_enabled())

    def choose_client(self, title, on_choose):
        def done(clients, err):
            if err:
                return messagebox.showerror(title, str(err))
            clients = sorted([c for c in clients if c.get("online")], key=lambda c: (c.get("hostname") or "").lower())
            if not clients:
                return messagebox.showinfo(title, "Kein Gerät online.")
            win = tk.Toplevel(self.root)
            win.title(f"{title} – Gerät wählen")
            win.configure(bg=BG)
            win.geometry("380x420")
            win.transient(self.root)
            q = tk.StringVar()
            ttk.Entry(win, textvariable=q).pack(fill="x", padx=12, pady=(12, 6))
            lb = tk.Listbox(win, bg=PANEL, fg=FG, selectbackground=ACCENT, selectforeground="#0c1420",
                            highlightthickness=0, borderwidth=0, font=("Segoe UI", 10))
            lb.pack(fill="both", expand=True, padx=12)
            shown: list[dict] = []

            def refill(*_):
                lb.delete(0, "end")
                shown.clear()
                for c in clients:
                    label = f"{c.get('hostname') or c['id']}   ({c.get('platform') or '?'})"
                    if q.get().lower() in label.lower():
                        shown.append(c)
                        lb.insert("end", label)
                if shown:
                    lb.selection_set(0)
            q.trace_add("write", refill)
            refill()

            def ok(*_):
                sel = lb.curselection()
                if sel:
                    c = shown[sel[0]]
                    win.destroy()
                    on_choose(c)
            lb.bind("<Double-Button-1>", ok)
            win.bind("<Return>", ok)
            ttk.Button(win, text="Öffnen", style="Accent.TButton", command=ok).pack(fill="x", padx=12, pady=12)
        self.bg(self.api.clients, done)

    def logout(self):
        if not messagebox.askyesno("Abmelden", "Dieses Gerät abmelden? Die gespeicherte Anmeldung wird gelöscht."):
            return
        dev = self.store.get("device_id")

        def work():
            try:
                features.unmount_relay(self.store)
            except Exception:
                pass
            if dev:
                try:
                    self.api.remove_device(dev)
                except ApiError:
                    pass

        def done(_, __):
            self.store.clear_token()
            self.api = None
            self.show_login("Abgemeldet.")
        self.bg(work, done)

    def run(self):
        self.root.mainloop()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ClientApp(minimized="--minimized" in argv).run()

# commands.py (Modul "console") - Befehle der RMM-Konsole
# -------------------------------------------------------
# Jeder Befehl prueft DIESELBEN Rechte wie der passende Knopf im Dashboard
# (Zugriff auf das Geraet, c_terminal, c_power, c_taskmanager_*, c_explorer_view, manage_agent ...).

from __future__ import annotations

import asyncio
import base64
import time
import uuid

from app import db, modules
from app.auth import can_access_client, is_super_admin, user_has_permission

from .shell import BLUE, BOLD, CYAN, DIM, ESC, GREEN, GREY, RED, RST, YELLOW, CommandError, UsageError

REGISTRY: dict[str, "Command"] = {}


class Command:
    def __init__(self, name, fn, usage="", help="", aliases=(), perm=None, args=(), module=None):
        self.name, self.fn, self.usage, self.help = name, fn, usage or name, help
        self.aliases, self.perm, self.args, self.module = tuple(aliases), perm, tuple(args), module

    def allowed(self, user) -> bool:
        if self.module and not modules.is_enabled(self.module):
            return False
        return not self.perm or is_super_admin(user) or user_has_permission(user, self.perm)

    def complete(self, con, done: list[str], word: str) -> list[str]:
        i = len(done)
        if i >= len(self.args):
            return []
        spec = self.args[i]
        if spec == "client":
            return [c["hostname"] for c in _clients(con.user) if c["hostname"].lower().startswith(word.lower())]
        if spec == "command":
            return [n for n, c in REGISTRY.items() if n.startswith(word) and c.allowed(con.user)]
        if callable(spec):
            return [x for x in spec(con, done) if x.lower().startswith(word.lower())]
        if isinstance(spec, (list, tuple)):
            return [x for x in spec if x.startswith(word)]
        return []

    async def run(self, con, argv):
        await self.fn(con, argv)


def command(name, usage="", help="", aliases=(), perm=None, args=(), module=None):
    def deco(fn):
        REGISTRY[name] = Command(name, fn, usage, help, aliases, perm, args, module)
        return fn
    return deco


# ------------------------------------------------------------------ Hilfen
def _state():
    from app.sockets import state
    return state


def _clients(user) -> list[dict]:
    st = _state()
    out = []
    for c in db.list_clients():
        if can_access_client(user, c["id"]):
            out.append({**c, "online": st.is_online(c["id"]), "m": st.live_metrics.get(c["id"]) or {}})
    return sorted(out, key=lambda x: (x.get("hostname") or "").lower())


def resolve(con, token: str | None) -> dict:
    if not token:
        raise UsageError("Gerät fehlt")
    cl = _clients(con.user)
    t = token.lower()
    exact = [c for c in cl if (c.get("hostname") or "").lower() == t or c["id"] == token]
    if exact:
        return exact[0]
    pre = [c for c in cl if (c.get("hostname") or "").lower().startswith(t) or c["id"].startswith(token)]
    if len(pre) == 1:
        return pre[0]
    if len(pre) > 1:
        raise CommandError(f"„{token}“ ist nicht eindeutig: {', '.join(c['hostname'] for c in pre[:8])}")
    raise CommandError(f"Gerät „{token}“ nicht gefunden (clients zeigt alle)")


def need(con, c: dict, perm: str) -> None:
    if not (is_super_admin(con.user) or user_has_permission(con.user, perm, c["id"])):
        raise CommandError(f"Keine Berechtigung ({perm}) für {c['hostname']}")


def need_online(c: dict) -> None:
    if not c["online"]:
        raise CommandError(f"{c['hostname']} ist offline")


def fmt_b(b) -> str:
    b = float(b or 0)
    for u in ("B", "K", "M", "G", "T"):
        if b < 1024 or u == "T":
            return f"{b:.0f}{u}" if u in ("B", "K") else f"{b:.1f}{u}"
        b /= 1024
    return "?"


def fmt_up(s) -> str:
    s = int(s or 0)
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}T {h}h" if d else f"{h}h {m}m"


def color_pct(p) -> str:
    return RED if p >= 90 else YELLOW if p >= 70 else GREEN


def bar(p, width: int) -> str:
    p = max(0.0, min(100.0, float(p or 0)))
    full = int(round(p / 100 * width))
    return f"{color_pct(p)}{'█' * full}{GREY}{'░' * (width - full)}{RST}"


def pct(a, b) -> float:
    return round(a / b * 100, 1) if b else 0.0


def table(con, rows: list[list[str]], head: list[str]) -> None:
    import re
    vis = lambda s: len(re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s))           # noqa: E731
    widths = [max(vis(str(r[i])) for r in rows + [head]) for i in range(len(head))]
    pad = lambda s, w: s + " " * (w - vis(s))                                # noqa: E731
    con.line(BOLD + "  ".join(pad(h, widths[i]) for i, h in enumerate(head)) + RST)
    for r in rows:
        con.line("  ".join(pad(str(x), widths[i]) for i, x in enumerate(r)))


# ------------------------------------------------------------------ Allgemein
@command("help", "help [befehl]", "Zeigt alle Befehle oder Hilfe zu einem Befehl", aliases=("?",), args=("command",))
async def _help(con, argv):
    if argv:
        c = REGISTRY.get(argv[0].lower())
        if not c or not c.allowed(con.user):
            raise CommandError(f"Unbekannter Befehl: {argv[0]}")
        con.line(f"{BOLD}{c.usage}{RST}\n  {c.help}" + (f"\n  {GREY}auch: {', '.join(c.aliases)}{RST}" if c.aliases else ""))
        return
    con.line(f"{BOLD}Befehle{RST} {GREY}(Tab vervollständigt Befehle und Gerätenamen){RST}")
    for name in sorted(REGISTRY):
        c = REGISTRY[name]
        if c.allowed(con.user):
            con.line(f"  {CYAN}{c.usage:<38}{RST} {c.help}")


@command("clear", "clear", "Bildschirm leeren", aliases=("cls",))
async def _clear(con, argv):
    con.w(f"{ESC}[H{ESC}[2J")


@command("exit", "exit", "Konsole beenden", aliases=("quit", "logout"))
async def _exit(con, argv):
    con.line("Auf Wiedersehen.")
    con.close()


@command("whoami", "whoami", "Wer bin ich, wie verbunden")
async def _whoami(con, argv):
    u = con.user
    con.line(f"{u['username']} ({u.get('role')}) · über {con.via}" + (f" von {con.ip}" if con.ip else ""))


# ------------------------------------------------------------------ Geraete
@command("clients", "clients [filter]", "Geräte auflisten (Filter: Name, online, offline, windows, linux)", aliases=("list", "devices"))
async def _clients_cmd(con, argv):
    f = (argv[0] if argv else "").lower()
    rows = []
    for c in _clients(con.user):
        osn = (c.get("os") or "").lower()
        if f == "online" and not c["online"] or f == "offline" and c["online"]:
            continue
        if f and f not in ("online", "offline") and f not in (c.get("hostname") or "").lower() and f not in osn:
            continue
        m = c["m"] if c["online"] else {}
        rows.append([f"{GREEN}●{RST}" if c["online"] else f"{GREY}○{RST}", c.get("hostname") or "?", (c.get("os") or "")[:28],
                     c.get("ip") or "", f"{m.get('cpuLoad', '–')}%" if m else "–",
                     f"{pct(m.get('memUsed', 0), m.get('memTotal', 0)):.0f}%" if m else "–"])
    if not rows:
        con.line(f"{GREY}Keine Geräte.{RST}")
        return
    table(con, rows, ["", "Name", "System", "IP", "CPU", "RAM"])
    con.line(f"{GREY}{len(rows)} Gerät(e), {sum(1 for r in rows if '●' in r[0])} online{RST}")


@command("info", "info <gerät>", "Details und aktuelle Werte eines Geräts", args=("client",))
async def _info(con, argv):
    c = resolve(con, argv[0] if argv else None)
    m = c["m"] if c["online"] else {}
    con.line(f"{BOLD}{c['hostname']}{RST}  {GREEN + 'online' if c['online'] else GREY + 'offline'}{RST}")
    seen = c.get("last_seen")
    if isinstance(seen, (int, float)) and seen:
        seen = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(seen / 1000 if seen > 1e11 else seen))
    for k, v in (("System", c.get("os")), ("IP", c.get("ip")), ("Agent", c.get("agent_version")), ("ID", c["id"]),
                 ("Zuletzt gesehen", seen)):
        if v:
            con.line(f"  {GREY}{k:<16}{RST}{v}")
    if m:
        con.line(f"  {GREY}{'CPU':<16}{RST}{bar(m.get('cpuLoad', 0), 20)} {m.get('cpuLoad', 0)}%  ({m.get('cpuThreads', '?')} Threads)")
        con.line(f"  {GREY}{'RAM':<16}{RST}{bar(pct(m.get('memUsed', 0), m.get('memTotal', 0)), 20)} {fmt_b(m.get('memUsed'))} / {fmt_b(m.get('memTotal'))}")
        for d in (m.get("disks") or [])[:6]:
            con.line(f"  {GREY}{('Disk ' + str(d.get('mountpoint')))[:15]:<16}{RST}{bar(pct(d.get('used', 0), d.get('total', 0)), 20)} {fmt_b(d.get('used'))} / {fmt_b(d.get('total'))}")
        con.line(f"  {GREY}{'Netz':<16}{RST}↓ {fmt_b(m.get('netIn'))}/s  ↑ {fmt_b(m.get('netOut'))}/s")
        con.line(f"  {GREY}{'Laufzeit':<16}{RST}{fmt_up(m.get('uptime'))}")


# ------------------------------------------------------------------ top (Vollbild)
SPARK = "▁▂▃▄▅▆▇█"


def spark(vals: list[float], width: int) -> str:
    vals = vals[-width:]
    mx = max(vals) if vals and max(vals) > 0 else 1
    return "".join(SPARK[min(7, int(v / mx * 7))] for v in vals)


class _App:
    def __init__(self, con):
        self.con, self.running, self.sort = con, True, "cpu"

    def stop(self):
        self.running = False

    def resize(self):
        pass

    def key(self, k):
        if k in ("p", "s"):
            self.sort = {"cpu": "mem", "mem": "name", "name": "cpu"}[self.sort] if k == "s" else ("mem" if self.sort == "cpu" else "cpu")

    def paint(self, lines: list[str]):
        import re
        out = []
        if not getattr(self, "_cleared", False):           # beim ersten Bild einmal komplett leeren
            out.append(f"{ESC}[2J")
            self._cleared = True
        out.append(f"{ESC}[H")
        lines = lines[:self.con.rows]
        for idx, ln in enumerate(lines):
            vis = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", ln)
            if len(vis) > self.con.cols:                       # grob kuerzen (Farbcodes beachten)
                keep, n, i = [], 0, 0
                while i < len(ln) and n < self.con.cols:
                    m = re.match(r"\x1b\[[0-9;?]*[A-Za-z]", ln[i:])
                    if m:
                        keep.append(m.group(0))
                        i += len(m.group(0))
                    else:
                        keep.append(ln[i])
                        n += 1
                        i += 1
                ln = "".join(keep) + RST
            # KEIN Zeilenumbruch nach der letzten Zeile - sonst rollt das Bild um eine Zeile
            out.append(ln + f"{ESC}[K" + ("\r\n" if idx < len(lines) - 1 else ""))
        out.append(f"{ESC}[J")
        self.con.w("".join(out))


class FleetTop(_App):
    async def run(self):
        while self.running:
            cl = _clients(self.con.user)
            key = {"cpu": lambda c: -(c["m"].get("cpuLoad", -1) if c["online"] else -1),
                   "mem": lambda c: -(pct(c["m"].get("memUsed", 0), c["m"].get("memTotal", 0)) if c["online"] else -1),
                   "name": lambda c: (c.get("hostname") or "").lower()}[self.sort]
            cl.sort(key=key)
            on = sum(1 for c in cl if c["online"])
            bw = max(6, min(20, (self.con.cols - 60) // 2))
            lines = [f"{BOLD}{CYAN} RMM-Übersicht{RST}  {on}/{len(cl)} online   {GREY}{time.strftime('%H:%M:%S')}   Sortierung: {self.sort}{RST}", "",
                     f"{BOLD} {'Name':<20} {'CPU':<{bw + 5}} {'RAM':<{bw + 5}} {'Disk':>5} {'↓/s':>7} {'↑/s':>7} {'Laufzeit':>9}{RST}"]
            for c in cl:
                m = c["m"] if c["online"] else {}
                if not m:
                    lines.append(f" {GREY}○ {(c.get('hostname') or '?')[:18]:<18}  offline{RST}")
                    continue
                mp = pct(m.get("memUsed", 0), m.get("memTotal", 0))
                dp = pct(m.get("diskUsed", 0), m.get("diskTotal", 0))
                lines.append(f" {GREEN}●{RST} {(c.get('hostname') or '?')[:18]:<18} {bar(m.get('cpuLoad', 0), bw)} {m.get('cpuLoad', 0):>3}% "
                             f"{bar(mp, bw)} {mp:>3.0f}% {color_pct(dp)}{dp:>4.0f}%{RST} {fmt_b(m.get('netIn')):>7} {fmt_b(m.get('netOut')):>7} {fmt_up(m.get('uptime')):>9}")
            lines += ["", f"{GREY} q beenden · s Sortierung (CPU / RAM / Name) · top <gerät> für Details{RST}"]
            self.paint(lines)
            for _ in range(20):
                if not self.running:
                    break
                await asyncio.sleep(0.1)


class ClientTop(_App):
    def __init__(self, con, c):
        super().__init__(con)
        self.c, self.procs, self.proc_at, self.net_in, self.net_out = c, [], 0.0, [], []
        self.can_procs = is_super_admin(con.user) or user_has_permission(con.user, "c_taskmanager_view", c["id"])

    async def _load_procs(self):
        from app.sockets import request_proc_list
        try:
            self.procs = await request_proc_list(self.c["id"], timeout_seconds=8)
        except Exception:
            pass

    async def run(self):
        st = _state()
        while self.running:
            m = st.live_metrics.get(self.c["id"]) or {}
            if self.can_procs and time.time() - self.proc_at > 4:
                self.proc_at = time.time()
                asyncio.create_task(self._load_procs())
            self.net_in.append(m.get("netIn") or 0)
            self.net_out.append(m.get("netOut") or 0)
            del self.net_in[:-200], self.net_out[:-200]
            W = self.con.cols
            bw = max(10, W - 30)
            lines = [f"{BOLD}{CYAN} {self.c['hostname']}{RST}  {GREY}{(self.c.get('os') or '')[:40]}  ·  Laufzeit {fmt_up(m.get('uptime'))}"
                     + (f"  ·  Last {m.get('load1')} {m.get('load5')} {m.get('load15')}" if m.get("load1") is not None else "")
                     + f"  ·  {time.strftime('%H:%M:%S')}{RST}"]
            if not st.is_online(self.c["id"]) or not m:
                lines += ["", f"{RED} Gerät offline – keine Live-Werte.{RST}"]
            else:
                lines += ["", f"{BOLD} CPU{RST} {bar(m.get('cpuLoad', 0), bw)} {m.get('cpuLoad', 0):>3}%  {GREY}{m.get('cpuCores', '?')} Kerne / {m.get('cpuThreads', '?')} Threads{RST}"]
                cores = m.get("cpuPerCore") or []
                per_col = max(1, min(4, W // 26))
                cw = max(6, (W // per_col) - 12)
                for i in range(0, len(cores), per_col):
                    lines.append(" " + "  ".join(f"{GREY}C{j:<2}{RST} {bar(cores[j], cw)} {cores[j]:>3}%" for j in range(i, min(i + per_col, len(cores)))))
                mp = pct(m.get("memUsed", 0), m.get("memTotal", 0))
                lines += ["", f"{BOLD} RAM{RST} {bar(mp, bw)} {mp:>3.0f}%  {fmt_b(m.get('memUsed'))}/{fmt_b(m.get('memTotal'))}"
                          + (f" {GREY}Cache {fmt_b(m.get('memCached'))}{RST}" if m.get("memCached") else "")]
                if m.get("swapTotal"):
                    sp = pct(m.get("swapUsed", 0), m.get("swapTotal", 0))
                    lines.append(f"{BOLD} Swap{RST}{bar(sp, bw)} {sp:>3.0f}%  {fmt_b(m.get('swapUsed'))}/{fmt_b(m.get('swapTotal'))}")
                for d in (m.get("disks") or [])[:4]:
                    dp = pct(d.get("used", 0), d.get("total", 0))
                    lines.append(f" {GREY}{str(d.get('mountpoint'))[:4]:<4}{RST}{bar(dp, bw)} {dp:>3.0f}%  {fmt_b(d.get('used'))}/{fmt_b(d.get('total'))}")
                sw = max(10, W - 26)
                lines += ["", f"{BOLD} Netz{RST} ↓ {fmt_b(m.get('netIn')):>7}/s {GREEN}{spark(self.net_in, sw)}{RST}",
                          f"      ↑ {fmt_b(m.get('netOut')):>7}/s {BLUE}{spark(self.net_out, sw)}{RST}"]
                if self.can_procs:
                    room = max(3, self.con.rows - len(lines) - 4)
                    procs = sorted(self.procs, key=lambda p: -(p.get("cpu") or 0) if self.sort == "cpu" else -(p.get("mem") or 0))
                    lines += ["", f"{BOLD} {'PID':>7}  {'Prozess':<28} {'Benutzer':<16} {'CPU%':>6} {'RAM%':>6}{RST}  {GREY}(sortiert nach {'CPU' if self.sort == 'cpu' else 'RAM'}){RST}"]
                    for p in procs[:room]:
                        lines.append(f" {p.get('pid', ''):>7}  {str(p.get('name', ''))[:28]:<28} {str(p.get('username', ''))[-16:]:<16} {p.get('cpu', 0):>6.1f} {p.get('mem', 0):>6.1f}")
            lines += ["", f"{GREY} q beenden · p Prozesse nach CPU/RAM sortieren{RST}"]
            self.paint(lines)
            for _ in range(20):
                if not self.running:
                    break
                await asyncio.sleep(0.1)


@command("top", "top [gerät]", "Live-Anzeige: alle Geräte, oder btop-ähnlich für ein Gerät (q beendet)", aliases=("btop", "htop"), args=("client",))
async def _top(con, argv):
    if not argv:
        await con.run_app(FleetTop(con))
        return
    c = resolve(con, argv[0])
    await con.run_app(ClientTop(con, c))


# ------------------------------------------------------------------ Shell / Befehle
class RemoteShell:
    def __init__(self, con, c):
        self.con, self.c, self.session, self.done = con, c, "con-" + uuid.uuid4().hex, asyncio.Event()

    async def open(self):
        from app.sockets import send_to_agent, term_listeners

        async def out(data):
            self.con.w(data)

        async def ended():
            self.done.set()
        term_listeners[self.session] = {"output": out, "exit": ended}
        ok = await send_to_agent(self.c["id"], "term-open", {"session": self.session, "shell": "auto",
                                                            "cols": self.con.cols, "rows": self.con.rows})
        if not ok:
            term_listeners.pop(self.session, None)
            raise CommandError(f"{self.c['hostname']} ist nicht erreichbar")

    async def send(self, data):
        from app.sockets import send_to_agent
        await send_to_agent(self.c["id"], "term-input", {"session": self.session, "data": data})

    def resize(self, cols, rows):
        from app.sockets import send_to_agent
        asyncio.create_task(send_to_agent(self.c["id"], "term-resize", {"session": self.session, "cols": cols, "rows": rows}))

    def close(self):
        from app.sockets import send_to_agent, term_listeners
        term_listeners.pop(self.session, None)
        asyncio.create_task(send_to_agent(self.c["id"], "term-close", {"session": self.session}))
        self.done.set()

    async def wait(self):
        await self.done.wait()


@command("shell", "shell <gerät>", "Interaktives Terminal auf dem Gerät (Strg+] beendet)", aliases=("ssh", "term"), args=("client",))
async def _shell(con, argv):
    c = resolve(con, argv[0] if argv else None)
    need(con, c, "c_terminal")
    need_online(c)
    con.line(f"{GREY}Verbinde mit {c['hostname']} … (Strg+] beendet die Sitzung){RST}")
    rs = RemoteShell(con, c)
    await rs.open()
    db.add_audit_entry(con.user["username"], "console.shell", target=c["id"], details=f"über {con.via} {con.ip}")
    await con.run_remote(rs)


@command("exec", "exec <gerät> <befehl …>", "Befehl auf dem Gerät ausführen und Ausgabe zeigen", aliases=("run",), args=("client",))
async def _exec(con, argv):
    if len(argv) < 2:
        raise UsageError("Gerät und Befehl angeben")
    c = resolve(con, argv[0])
    need(con, c, "c_terminal")
    need_online(c)
    from app.sockets import request_exec
    cmd = " ".join(argv[1:])
    db.add_audit_entry(con.user["username"], "console.exec", target=c["id"], details=cmd[:200])
    res = await request_exec(c["id"], cmd, timeout_seconds=120)
    if res.get("stdout"):
        con.line(res["stdout"].rstrip())
    if res.get("stderr"):
        con.line(f"{RED}{res['stderr'].rstrip()}{RST}")
    if res.get("code"):
        con.line(f"{GREY}[Exit-Code {res['code']}]{RST}")


@command("ps", "ps <gerät> [filter]", "Prozesse eines Geräts", args=("client",))
async def _ps(con, argv):
    c = resolve(con, argv[0] if argv else None)
    need(con, c, "c_taskmanager_view")
    need_online(c)
    from app.sockets import request_proc_list
    procs = await request_proc_list(c["id"])
    f = (argv[1] if len(argv) > 1 else "").lower()
    rows = [[str(p.get("pid")), str(p.get("name"))[:32], str(p.get("username", ""))[-18:], f"{p.get('cpu', 0):.1f}", f"{p.get('mem', 0):.1f}"]
            for p in sorted(procs, key=lambda p: -(p.get("mem") or 0)) if not f or f in str(p.get("name", "")).lower()]
    table(con, rows, ["PID", "Prozess", "Benutzer", "CPU%", "RAM%"]) if rows else con.line(f"{GREY}Keine Treffer.{RST}")


@command("kill", "kill <gerät> <pid>", "Prozess beenden", args=("client",))
async def _kill(con, argv):
    if len(argv) < 2 or not argv[1].isdigit():
        raise UsageError("Gerät und PID angeben")
    c = resolve(con, argv[0])
    need(con, c, "c_taskmanager_kill")
    need_online(c)
    if not await con.confirm(f"Prozess {argv[1]} auf {c['hostname']} beenden?"):
        return
    from app.sockets import request_proc_kill
    res = await request_proc_kill(c["id"], int(argv[1]))
    db.add_audit_entry(con.user["username"], "console.kill", target=c["id"], details=f"PID {argv[1]}")
    con.line(f"{GREEN}beendet{RST}" if res.get("ok", True) and not res.get("error") else f"{RED}{res.get('error')}{RST}")


async def _power(con, argv, what):
    c = resolve(con, argv[0] if argv else None)
    need(con, c, "c_power")
    need_online(c)
    label = "neu starten" if what == "reboot" else "herunterfahren"
    if not await con.confirm(f"{c['hostname']} wirklich {label}?"):
        return
    win = "windows" in (c.get("os") or "").lower()
    cmd = ("shutdown /r /t 0" if win else "sudo reboot") if what == "reboot" else ("shutdown /s /t 0" if win else "sudo shutdown -h now")
    from app.sockets import request_exec
    db.add_audit_entry(con.user["username"], f"console.{what}", target=c["id"], details=f"über {con.via}")
    try:
        await request_exec(c["id"], cmd, timeout_seconds=15)
    except Exception:
        pass                                       # Verbindung bricht beim Herunterfahren oft ab - normal
    con.line(f"{GREEN}{c['hostname']} wird {'neu gestartet' if what == 'reboot' else 'heruntergefahren'}.{RST}")


@command("reboot", "reboot <gerät>", "Gerät neu starten (mit Rückfrage)", args=("client",))
async def _reboot(con, argv):
    await _power(con, argv, "reboot")


@command("shutdown", "shutdown <gerät>", "Gerät herunterfahren (mit Rückfrage)", args=("client",))
async def _shutdown(con, argv):
    await _power(con, argv, "shutdown")


@command("agent", "agent <gerät> restart|update", "Agent neu starten bzw. aktualisieren", args=("client", ["restart", "update"]))
async def _agent(con, argv):
    if len(argv) < 2 or argv[1] not in ("restart", "update"):
        raise UsageError("Gerät und restart oder update angeben")
    c = resolve(con, argv[0])
    need(con, c, "manage_agent")
    need_online(c)
    from app.sockets import send_to_agent
    ok = await send_to_agent(c["id"], "update-agent", {})
    db.add_audit_entry(con.user["username"], f"console.agent_{argv[1]}", target=c["id"])
    con.line(f"{GREEN}Agent auf {c['hostname']} wird {'neu gestartet' if argv[1] == 'restart' else 'aktualisiert'} "
             f"(kommt in ca. 30 s wieder online).{RST}" if ok else f"{RED}Agent nicht erreichbar{RST}")


# ------------------------------------------------------------------ Dateien
@command("ls", "ls <gerät> [pfad]", "Ordner auf dem Gerät anzeigen", aliases=("dir",), args=("client",))
async def _ls(con, argv):
    c = resolve(con, argv[0] if argv else None)
    need(con, c, "c_explorer_view")
    need_online(c)
    from app.sockets import request_fs_list
    path = argv[1] if len(argv) > 1 else ""
    entries = await request_fs_list(c["id"], path)
    rows = [[f"{BLUE}{e.get('name')}/{RST}" if e.get("is_dir") or e.get("isDir") or e.get("type") == "dir" else str(e.get("name")),
             "" if e.get("is_dir") or e.get("isDir") else fmt_b(e.get("size"))] for e in entries]
    table(con, rows, ["Name", "Größe"]) if rows else con.line(f"{GREY}(leer){RST}")


@command("cat", "cat <gerät> <datei>", "Textdatei auf dem Gerät anzeigen (max. 256 KB)", args=("client",))
async def _cat(con, argv):
    if len(argv) < 2:
        raise UsageError("Gerät und Datei angeben")
    c = resolve(con, argv[0])
    need(con, c, "c_explorer_view")
    need_online(c)
    from app.sockets import request_fs_read
    res = await request_fs_read(c["id"], argv[1])
    if res.get("error"):
        raise CommandError(res["error"])
    raw = base64.b64decode(res.get("data") or res.get("content") or "")
    if len(raw) > 262144:
        raise CommandError("Datei zu groß für die Anzeige (max. 256 KB)")
    con.line(raw.decode("utf-8", errors="replace").rstrip())


# ------------------------------------------------------------------ Module / Verwaltung
@command("modules", "modules", "Aktive Module")
async def _modules(con, argv):
    data = modules.list_all()
    mods = data.get("modules", data) if isinstance(data, dict) else data
    mods = mods.values() if isinstance(mods, dict) else mods
    rows = [[m["id"], m.get("name", ""), m.get("version", "")] for m in mods if isinstance(m, dict) and m.get("enabled")]
    table(con, rows, ["ID", "Name", "Version"])


@command("audit", "audit [anzahl]", "Letzte Einträge im Audit-Protokoll", perm="see_audit")
async def _audit(con, argv):
    n = int(argv[0]) if argv and argv[0].isdigit() else 20
    import datetime as _dt
    fmt_ts = lambda v: _dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v).strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, (int, float)) else str(v or "")[:19]   # noqa: E731
    rows = [[fmt_ts(e.get("ts")), e.get("username") or "", e.get("action") or "", (e.get("details") or "")[:50]]
            for e in db.list_audit_log(limit=min(n, 500))]
    table(con, rows, ["Zeit", "Benutzer", "Aktion", "Details"])


def _stack_names(con, done):
    st = modules.use("docker", "stacks")
    return [s["project"] for s in st.visible(con.user)]


@command("docker", "docker ls | ps <stack> | start|stop|restart <stack>", "Docker-Stacks anzeigen und steuern",
         module="docker", args=(["ls", "ps", "start", "stop", "restart"], _stack_names))
async def _docker(con, argv):
    st = modules.use("docker", "stacks")
    sub = (argv[0] if argv else "ls").lower()
    mine = st.visible(con.user)
    if sub == "ls":
        rows = []
        for s in mine:
            try:
                cs = await st.containers(s)
                run = f"{sum(1 for c in cs if c['state'] == 'running')}/{len(cs)}"
            except Exception:
                run = "?"
            rows.append([s["project"], s["name"], s["target"], run])
        table(con, rows, ["Stack", "Name", "Ziel", "läuft"]) if rows else con.line(f"{GREY}Keine Stacks.{RST}")
        return
    if len(argv) < 2:
        raise UsageError("Stack angeben")
    s = next((x for x in mine if x["project"] == argv[1] or x["name"] == argv[1]), None)
    if not s:
        raise CommandError(f"Stack „{argv[1]}“ nicht gefunden")
    if sub == "ps":
        rows = [[c["name"], c["state"], c["status"] or "", c["image"]] for c in await st.containers(s)]
        table(con, rows, ["Container", "Zustand", "Status", "Image"])
    elif sub in ("start", "stop", "restart"):
        if s["level"] < st.OPERATE:
            raise CommandError("Keine Berechtigung (Rolle „Bedienen“ nötig)")
        for ln in await st.action(s, sub):
            con.line(ln)
        db.add_audit_entry(con.user["username"], f"console.docker_{sub}", details=s["project"])
    else:
        raise UsageError(f"Unbekannt: {sub}")

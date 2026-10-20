# shell.py (Modul "console") - die RMM-Konsole (unabhaengig vom Transport)
# ------------------------------------------------------------------------
# EINE Konsole fuer drei Wege: SSH (sshd.py), Dashboard-Terminal und Client-App (WebSocket,
# console_routes.py). Der Transport liefert Tastendruecke (feed) und nimmt Text an (out).
#
# Zeileneditor: Verlauf (Pfeil hoch/runter), Cursor (links/rechts, Pos1/Ende, Entf),
#   Strg+A/E (Anfang/Ende), Strg+U/K (bis Anfang/Ende loeschen), Strg+W (Wort), Strg+L (leeren),
#   Strg+C (Zeile/Befehl abbrechen), Strg+D (beenden), Tab (Vervollstaendigung).
# Betriebsarten: line (Eingabe) | busy (Befehl laeuft) | confirm (j/n) | app (Vollbild, z.B. top) |
#   remote (Shell auf einem Geraet, Strg+] beendet).

from __future__ import annotations

import asyncio
import shlex
from typing import Callable

ESC = "\x1b"
BOLD, DIM, RST = f"{ESC}[1m", f"{ESC}[2m", f"{ESC}[0m"
RED, GREEN, YELLOW, BLUE, CYAN, GREY = (f"{ESC}[31m", f"{ESC}[32m", f"{ESC}[33m", f"{ESC}[34m", f"{ESC}[36m", f"{ESC}[90m")
KEYS = {f"{ESC}[A": "up", f"{ESC}[B": "down", f"{ESC}[C": "right", f"{ESC}[D": "left",
        f"{ESC}OA": "up", f"{ESC}OB": "down", f"{ESC}OC": "right", f"{ESC}OD": "left",
        f"{ESC}[H": "home", f"{ESC}[F": "end", f"{ESC}OH": "home", f"{ESC}OF": "end",
        f"{ESC}[1~": "home", f"{ESC}[4~": "end", f"{ESC}[3~": "delete", f"{ESC}[5~": "pgup", f"{ESC}[6~": "pgdn"}


def split_keys(data: str) -> list[str]:
    """Eingabe in einzelne Tasten zerlegen (Escape-Folgen bleiben zusammen)."""
    out, i = [], 0
    while i < len(data):
        if data[i] == ESC:
            for seq in sorted(KEYS, key=len, reverse=True):
                if data.startswith(seq, i):
                    out.append(seq)
                    i += len(seq)
                    break
            else:
                out.append(ESC)
                i += 1
        else:
            out.append(data[i])
            i += 1
    return out


class Console:
    def __init__(self, user: dict, out: Callable[[str], None], close: Callable[[], None],
                 cols: int = 80, rows: int = 24, ip: str = "", via: str = "web"):
        self.user, self._out, self._close = user, out, close
        self.cols, self.rows, self.ip, self.via = cols, rows, ip, via
        self.buf, self.cur = "", 0
        self.history: list[str] = []
        self.hpos = 0
        self.mode = "line"
        self.task: asyncio.Task | None = None
        self.app = None                  # Vollbild-App (top): .key(k), .resize(), .stop()
        self.remote = None               # Remote-Shell: .send(data), .close()
        self._confirm: asyncio.Future | None = None
        self.closed = False
        from . import commands
        self.commands = commands.REGISTRY

    # ------------------------------------------------------------ Ausgabe
    def w(self, s: str = "") -> None:
        if not self.closed:
            self._out(s)

    def line(self, s: str = "") -> None:
        self.w(s.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n")

    def prompt_text(self) -> str:
        return f"{GREEN}{self.user['username']}{RST}@{CYAN}rmm{RST}> "

    def redraw(self) -> None:
        self.w("\r" + self.prompt_text() + self.buf + f"{ESC}[K")
        back = len(self.buf) - self.cur
        if back > 0:
            self.w(f"{ESC}[{back}D")

    def prompt(self) -> None:
        self.buf, self.cur, self.hpos, self.mode = "", 0, len(self.history), "line"
        self.redraw()

    async def start(self) -> None:
        from app import modules
        self.line(f"{BOLD}RAPALLE.net RMM – Konsole{RST}  {GREY}(angemeldet als {self.user['username']} über {self.via}){RST}")
        self.line(f"{GREY}Befehle: {BOLD}help{RST}{GREY} · Tab vervollständigt · Strg+C bricht ab · {BOLD}exit{RST}{GREY} beendet{RST}")
        self.line()
        self.prompt()

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = max(20, int(cols)), max(8, int(rows))
        if self.mode == "app" and self.app:
            self.app.resize()
        elif self.mode == "remote" and self.remote:
            self.remote.resize(self.cols, self.rows)

    def close(self) -> None:
        if self.closed:
            return
        if self.app:
            self.app.stop()
        if self.remote:
            self.remote.close()
        if self.task and not self.task.done():
            self.task.cancel()
        self.closed = True
        self._close()

    # ------------------------------------------------------------ Eingabe
    async def feed(self, data: str) -> None:
        if self.closed:
            return
        if self.mode == "remote" and self.remote:
            if "\x1d" in data:                     # Strg+]
                self.remote.close()
                return
            await self.remote.send(data)
            return
        for k in split_keys(data):
            if self.mode == "app" and self.app:
                if k in ("\x03", "q", "Q", ESC):
                    self.app.stop()
                else:
                    self.app.key(k)
                continue
            if self.mode == "confirm":
                if self._confirm and not self._confirm.done():
                    if k in ("j", "J", "y", "Y"):
                        self.line("ja")
                        self._confirm.set_result(True)
                    elif k in ("n", "N", "\r", "\x03", ESC):
                        self.line("nein")
                        self._confirm.set_result(False)
                continue
            if self.mode == "busy":
                if k == "\x03" and self.task and not self.task.done():
                    self.task.cancel()
                continue
            await self._edit(k)

    async def _edit(self, k: str) -> None:
        b, c = self.buf, self.cur
        if k in ("\r", "\n"):
            self.w("\r\n")
            await self.run_line(b)
            return
        if k == "\t":
            return self.complete()
        if k == "\x03":                            # Strg+C
            self.w("^C\r\n")
            return self.prompt()
        if k == "\x04":                            # Strg+D
            if not b:
                self.line("exit")
                return self.close()
            k = "delete"
        if k == "\x0c":                            # Strg+L
            self.w(f"{ESC}[H{ESC}[2J")
            return self.redraw()
        if k in ("\x7f", "\x08"):
            if c > 0:
                self.buf, self.cur = b[:c - 1] + b[c:], c - 1
        elif k == "delete":
            self.buf = b[:c] + b[c + 1:]
        elif k == "left":
            self.cur = max(0, c - 1)
        elif k == "right":
            self.cur = min(len(b), c + 1)
        elif k in ("home", "\x01"):
            self.cur = 0
        elif k in ("end", "\x05"):
            self.cur = len(b)
        elif k == "\x15":                          # Strg+U
            self.buf, self.cur = b[c:], 0
        elif k == "\x0b":                          # Strg+K
            self.buf = b[:c]
        elif k == "\x17":                          # Strg+W
            left = b[:c].rstrip()
            cut = left.rfind(" ") + 1
            self.buf, self.cur = b[:cut] + b[c:], cut
        elif k in ("up", "down"):
            if not self.history:
                return
            self.hpos = max(0, self.hpos - 1) if k == "up" else min(len(self.history), self.hpos + 1)
            self.buf = self.history[self.hpos] if self.hpos < len(self.history) else ""
            self.cur = len(self.buf)
        elif len(k) == 1 and (k >= " " or k == "\t"):
            self.buf, self.cur = b[:c] + k + b[c:], c + 1
        elif len(k) > 1 and not k.startswith(ESC):  # eingefuegter Text
            self.buf, self.cur = b[:c] + k + b[c:], c + len(k)
        self.redraw()

    # ------------------------------------------------------------ Vervollstaendigung
    def complete(self) -> None:
        from . import commands
        left = self.buf[:self.cur]
        try:
            parts = shlex.split(left) if left.strip() else []
        except ValueError:
            parts = left.split()
        if left.endswith(" ") or not parts:
            parts.append("")
        word = parts[-1]
        if len(parts) == 1:
            cands = sorted(n for n, cmd in self.commands.items() if n.startswith(word.lower()) and cmd.allowed(self.user))
        else:
            cmd = self.commands.get(parts[0].lower())
            cands = sorted(cmd.complete(self, parts[1:-1], word)) if cmd and cmd.allowed(self.user) else []
        if not cands:
            self.w("\a")
            return
        common = cands[0]
        for x in cands[1:]:
            while not x.lower().startswith(common.lower()):
                common = common[:-1]
        add = common[len(word):]
        if len(cands) == 1:
            add += " "
        if add:
            q = add if " " not in common else add          # Namen mit Leerzeichen
            self.buf = self.buf[:self.cur] + q + self.buf[self.cur:]
            self.cur += len(q)
            self.redraw()
            return
        width = max(len(x) for x in cands) + 2
        per = max(1, self.cols // width)
        self.w("\r\n")
        for i in range(0, len(cands), per):
            self.line("".join(x.ljust(width) for x in cands[i:i + per]))
        self.redraw()

    # ------------------------------------------------------------ Ausfuehren
    async def run_line(self, text: str) -> None:
        text = text.strip()
        if not text:
            return self.prompt()
        if not self.history or self.history[-1] != text:
            self.history.append(text)
            del self.history[:-200]
        try:
            argv = shlex.split(text)
        except ValueError as e:
            self.line(f"{RED}Eingabe nicht verstanden: {e}{RST}")
            return self.prompt()
        name = argv[0].lower()
        cmd = self.commands.get(name) or next((c for c in self.commands.values() if name in c.aliases), None)
        if not cmd or not cmd.allowed(self.user):
            self.line(f"{RED}Unbekannter Befehl: {argv[0]}{RST}  {GREY}(help zeigt alle Befehle){RST}")
            return self.prompt()
        self.mode = "busy"

        async def runner():
            try:
                await cmd.run(self, argv[1:])
            except asyncio.CancelledError:
                self.line(f"{YELLOW}abgebrochen{RST}")
            except UsageError as e:
                self.line(f"{RED}{e}{RST}  {GREY}Aufruf: {cmd.usage}{RST}")
            except CommandError as e:
                self.line(f"{RED}{e}{RST}")
            except Exception as e:      # noqa: BLE001
                self.line(f"{RED}Fehler: {e}{RST}")
            finally:
                if not self.closed and self.mode in ("busy", "confirm"):
                    self.prompt()
        self.task = asyncio.create_task(runner())

    async def confirm(self, question: str) -> bool:
        self.w(f"{YELLOW}{question} [j/N] {RST}")
        self.mode = "confirm"
        self._confirm = asyncio.get_running_loop().create_future()
        try:
            return await self._confirm
        finally:
            self.mode = "busy"

    # ------------------------------------------------------------ Vollbild / Remote
    async def run_app(self, app) -> None:
        """Vollbild-Anzeige (z.B. top) bis q/Esc/Strg+C."""
        self.app, self.mode = app, "app"
        self.w(f"{ESC}[?25l")                       # Cursor ausblenden
        try:
            await app.run()
        finally:
            self.app = None
            self.w(f"{ESC}[0m{ESC}[H{ESC}[2J{ESC}[?25h")
            self.mode = "busy"

    async def run_remote(self, remote) -> None:
        self.remote, self.mode = remote, "remote"
        try:
            await remote.wait()
        finally:
            self.remote = None
            self.w(f"\r\n{GREY}[Shell beendet]{RST}\r\n")
            self.mode = "busy"


class CommandError(Exception):
    pass


class UsageError(Exception):
    pass

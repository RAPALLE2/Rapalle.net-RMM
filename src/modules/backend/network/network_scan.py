"""
network_scan.py
----------------
Durchsucht das lokale Netzwerk (das /24-Subnetz, in dem der Backend-Rechner
selbst steckt) nach erreichbaren Geräten - das Gegenstück zu "Network Center"
in Synology DSM.

Vorgehen:
1. Eigene lokale IP herausfinden -> daraus das Subnetz ableiten (z.B. 192.168.1.x)
2. Alle 254 möglichen Adressen parallel anpingen ("Ping-Sweep")
3. Für die Antworten die MAC-Adresse aus der System-ARP-Tabelle auslesen
4. Für die Antworten den Hostnamen per Reverse-DNS versuchen aufzulösen

Alles läuft asynchron (asyncio), damit 254 Pings nicht 254x nacheinander
Zeit kosten, sondern weitgehend gleichzeitig ablaufen.
"""

import asyncio
import platform
import re
import socket
import time
import uuid

IS_WINDOWS = platform.system() == "Windows"


def get_local_subnet() -> tuple[str, str]:
    """
    Ermittelt die eigene lokale IP-Adresse und daraus das /24-Subnetz-Präfix.
    Trick: wir "verbinden" testweise einen UDP-Socket zu einer öffentlichen
    Adresse (ohne wirklich Daten zu senden!) - das Betriebssystem wählt dafür
    automatisch die "richtige" lokale Netzwerkkarte aus.
    Rückgabe: (Subnetz-Präfix z.B. "192.168.1.", eigene IP z.B. "192.168.1.42")
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        own_ip = s.getsockname()[0]
    except Exception:
        own_ip = "127.0.0.1"
    finally:
        s.close()

    parts = own_ip.split(".")
    prefix = ".".join(parts[:3]) + "."
    return prefix, own_ip


async def _ping_host(ip: str) -> bool:
    """Pingt eine einzelne IP-Adresse an (plattformabhängiger Befehl)."""
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", "300", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await proc.communicate()
        text = out.decode(errors="ignore")
        if IS_WINDOWS:
            return "TTL=" in text or "ttl=" in text
        return "bytes from" in text or "1 packets received" in text or "1 received" in text
    except Exception:
        return False


async def _read_arp_table() -> dict[str, str]:
    """Liest die System-ARP-Tabelle aus, um zu Pings passende MAC-Adressen zu finden."""
    mapping: dict[str, str] = {}
    try:
        proc = await asyncio.create_subprocess_exec(
            "arp", "-a", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await proc.communicate()
        text = out.decode(errors="ignore")

        if IS_WINDOWS:
            pattern = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})")
        else:
            pattern = re.compile(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-fA-F:]{17})")

        for match in pattern.finditer(text):
            mapping[match.group(1)] = match.group(2).upper()
    except Exception:
        pass  # arp nicht verfügbar -> einfach keine MAC-Adressen anzeigen
    return mapping


async def _reverse_dns(ip: str) -> str | None:
    """Versucht, zu einer IP-Adresse einen Hostnamen zu finden (kann fehlschlagen, ist ok)."""
    loop = asyncio.get_event_loop()
    try:
        # gethostbyaddr ist eine blockierende Funktion -> in einem Thread ausführen,
        # damit sie den restlichen asyncio-Code nicht aufhält.
        hostname, _, _ = await loop.run_in_executor(None, socket.gethostbyaddr, ip)
        return hostname
    except Exception:
        return None


# ------------------------------------------------------------------
# Ziel-Angabe parsen: /24, /16 (z.B. "10.10") oder CIDR
# ------------------------------------------------------------------
def parse_scan_target(spec: str | None) -> tuple[list[str], str]:
    """
    Wandelt eine Ziel-Angabe in eine Liste von /24-Präfixen um.
    Unterstützt:
      "192.168.1." / "192.168.1"   -> ein /24            (254 Adressen)
      "10.10." / "10.10"           -> 256 /24-Netze      (65.024 Adressen)
      "10."  / "10"                -> 65.536 /24-Netze   (bewusst erlaubt,
                                      dauert entsprechend lang)
      "10.10.0.0/16", "192.168.1.0/24", "172.16.0.0/22"  -> CIDR
      leer                          -> eigenes /24
    Rückgabe: (Liste der /24-Präfixe wie "10.10.7.", Beschriftung)
    """
    spec = (spec or "").strip()
    if not spec:
        prefix, _ = get_local_subnet()
        return [prefix], prefix + "x"

    # --- CIDR-Schreibweise ---
    if "/" in spec:
        import ipaddress
        try:
            net = ipaddress.ip_network(spec, strict=False)
        except ValueError as e:
            raise ValueError(f"Ungültige CIDR-Angabe: {e}")
        if net.version != 4:
            raise ValueError("Nur IPv4 wird unterstützt")
        if net.prefixlen < 8:
            raise ValueError("Netz zu groß (mindestens /8)")
        # Alle enthaltenen /24-Blöcke sammeln
        prefixes = []
        if net.prefixlen >= 24:
            first = net.network_address
            prefixes.append(".".join(str(first).split(".")[:3]) + ".")
        else:
            for sub in net.subnets(new_prefix=24):
                prefixes.append(".".join(str(sub.network_address).split(".")[:3]) + ".")
        return prefixes, str(net)

    # --- Punkt-Schreibweise: Anzahl der Oktette entscheidet ---
    parts = [p for p in spec.split(".") if p != ""]
    for p in parts:
        if not p.isdigit() or not (0 <= int(p) <= 255):
            raise ValueError(f"Ungültiges Oktett: {p}")
    if len(parts) == 3:
        pref = ".".join(parts) + "."
        return [pref], pref + "x"
    if len(parts) == 4:
        pref = ".".join(parts[:3]) + "."
        return [pref], pref + "x"
    if len(parts) == 2:                       # z.B. "10.10" -> 10.10.0.x … 10.10.255.x
        return [f"{parts[0]}.{parts[1]}.{i}." for i in range(256)], f"{parts[0]}.{parts[1]}.x.x"
    if len(parts) == 1:                       # z.B. "10" -> /8
        return [f"{parts[0]}.{i}.{j}." for i in range(256) for j in range(256)], f"{parts[0]}.x.x.x"
    raise ValueError("Ziel bitte als 10.10, 192.168.1. oder 10.10.0.0/16 angeben")


async def scan_local_network(subnet_prefix: str | None = None) -> list[dict]:
    """
    Führt den kompletten Scan durch und gibt eine Liste von Geräten zurück:
    [{"ip": ..., "mac": ..., "hostname": ..., "alive": True}, ...]

    subnet_prefix: optionales Subnetz wie "192.168.5." - wenn nicht angegeben,
    wird das eigene Subnetz automatisch ermittelt.
    """
    if subnet_prefix:
        prefix = subnet_prefix if subnet_prefix.endswith(".") else subnet_prefix + "."
        own_ip = None
    else:
        prefix, own_ip = get_local_subnet()

    candidate_ips = [f"{prefix}{i}" for i in range(1, 255)]

    # Maximal 32 Pings gleichzeitig, um das Netzwerk nicht zu überlasten
    semaphore = asyncio.Semaphore(32)
    alive_ips: set[str] = set()

    async def check_one(ip: str) -> None:
        async with semaphore:
            if await _ping_host(ip):
                alive_ips.add(ip)

    await asyncio.gather(*(check_one(ip) for ip in candidate_ips))
    if own_ip:
        alive_ips.add(own_ip)  # der eigene Rechner ist ja auch "im Netz"

    arp_table = await _read_arp_table()

    async def build_device(ip: str) -> dict:
        hostname = await _reverse_dns(ip)
        return {"ip": ip, "mac": arp_table.get(ip), "hostname": hostname, "alive": True}

    devices = await asyncio.gather(*(build_device(ip) for ip in alive_ips))

    # Nach IP-Adresse sortieren (numerisch, nicht alphabetisch, sonst kommt .10 vor .2)
    devices.sort(key=lambda d: tuple(int(part) for part in d["ip"].split(".")))
    return devices


# ==================================================================
# JOB-BASIERTER SCAN (für große Netze wie /16)
# Ein /24 in einem Rutsch zu scannen dauert Sekunden - ein /16 sind 256
# solcher Blöcke. Deshalb läuft ein großer Scan als Hintergrund-Job:
# Das Frontend startet ihn, fragt den Fortschritt ab und bekommt gefundene
# Geräte schon WÄHREND des Scans zu sehen.
#
# "Speed-Up": Die /24-Blöcke werden auf mehrere Worker verteilt, die parallel
# arbeiten - und jeder Worker pingt seinerseits mehrere Adressen gleichzeitig.
# Effektive Nebenläufigkeit = workers * per_subnet.
# ==================================================================
SCAN_SPEEDS = {
    # name: (parallele /24-Blöcke, gleichzeitige Pings je Block)
    "normal": (1, 64),      # schont Netz und CPU
    "fast":   (8, 96),      # Speed-Up an
    "turbo":  (24, 128),    # sehr aggressiv, nur im eigenen LAN sinnvoll
}

# HARTE Obergrenze für gleichzeitige Pings über ALLE Worker hinweg.
# Jeder Ping ist ein eigener Systemprozess; ohne diese Bremse würde Turbo
# (24 x 128) über 3000 Prozesse gleichzeitig starten und je nach Limit
# (ulimit -u) am Betriebssystem scheitern. 512 ist schnell und bleibt sicher.
MAX_TOTAL_PINGS = 512
_ping_gate: asyncio.Semaphore | None = None


def _gate() -> asyncio.Semaphore:
    """Der Semaphore muss im laufenden Event-Loop erzeugt werden."""
    global _ping_gate
    if _ping_gate is None:
        _ping_gate = asyncio.Semaphore(MAX_TOTAL_PINGS)
    return _ping_gate

_scan_jobs: dict[str, dict] = {}


def get_scan_job(job_id: str) -> dict | None:
    return _scan_jobs.get(job_id)


def cancel_scan_job(job_id: str) -> bool:
    job = _scan_jobs.get(job_id)
    if not job or job["status"] not in ("running", "queued"):
        return False
    job["cancel"] = True
    return True


def _prune_scan_jobs(keep: int = 8) -> None:
    """Alte, abgeschlossene Jobs aufräumen, damit der Speicher nicht wächst."""
    done = [(j["started"], jid) for jid, j in _scan_jobs.items()
            if j["status"] in ("done", "error", "cancelled")]
    for _, jid in sorted(done)[:-keep] if len(done) > keep else []:
        _scan_jobs.pop(jid, None)


async def _scan_one_subnet(prefix: str, per_subnet: int, job: dict) -> list[str]:
    """Pingt ein /24 durch und liefert die erreichbaren IPs."""
    sem = asyncio.Semaphore(per_subnet)
    alive: list[str] = []

    async def one(ip: str) -> None:
        if job.get("cancel"):
            return
        # Zwei Bremsen: pro Subnetz (sem) und global über alle Worker (_gate),
        # damit nie mehr als MAX_TOTAL_PINGS Prozesse gleichzeitig laufen.
        async with sem:
            if job.get("cancel"):
                return
            async with _gate():
                if job.get("cancel"):
                    return
                if await _ping_host(ip):
                    alive.append(ip)
                    job["found"] += 1
        job["done_hosts"] += 1

    await asyncio.gather(*(one(f"{prefix}{i}") for i in range(1, 255)))
    return alive


async def run_scan_job(job_id: str, target: str | None, speed: str = "normal") -> None:
    """Hintergrund-Task: scannt alle /24-Blöcke des Ziels und füllt den Job."""
    job = _scan_jobs[job_id]
    try:
        prefixes, label = parse_scan_target(target)
        workers, per_subnet = SCAN_SPEEDS.get(speed, SCAN_SPEEDS["normal"])
        job.update({
            "status": "running", "label": label,
            "total_subnets": len(prefixes), "done_subnets": 0,
            "total_hosts": len(prefixes) * 254, "done_hosts": 0,
            "workers": workers, "per_subnet": per_subnet,
            "max_parallel": min(workers * per_subnet, MAX_TOTAL_PINGS),
        })

        # Die /24-Blöcke gleichmäßig auf die Worker verteilen (Speed-Up).
        queue: asyncio.Queue = asyncio.Queue()
        for pref in prefixes:
            queue.put_nowait(pref)
        alive_ips: list[str] = []

        async def worker() -> None:
            while not queue.empty() and not job.get("cancel"):
                pref = await queue.get()
                try:
                    alive_ips.extend(await _scan_one_subnet(pref, per_subnet, job))
                finally:
                    job["done_subnets"] += 1
                    queue.task_done()

        await asyncio.gather(*(worker() for _ in range(min(workers, len(prefixes)))))

        # Eigene IP ergänzen, wenn das eigene Netz gescannt wurde
        _, own_ip = get_local_subnet()
        if own_ip and any(own_ip.startswith(p) for p in prefixes) and own_ip not in alive_ips:
            alive_ips.append(own_ip)

        arp_table = await _read_arp_table()

        async def build(ip: str) -> dict:
            return {"ip": ip, "mac": arp_table.get(ip),
                    "hostname": await _reverse_dns(ip), "alive": True}

        devices = list(await asyncio.gather(*(build(ip) for ip in alive_ips)))
        devices.sort(key=lambda d: tuple(int(x) for x in d["ip"].split(".")))
        job["devices"] = devices
        job["status"] = "cancelled" if job.get("cancel") else "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
    finally:
        job["finished"] = int(time.time() * 1000)
        _prune_scan_jobs()


def start_scan_job(target: str | None, speed: str = "normal") -> str:
    """Legt einen Job an und startet ihn im Hintergrund. Gibt die Job-ID zurück."""
    # Ziel vorab prüfen, damit Fehleingaben sofort auffallen.
    prefixes, label = parse_scan_target(target)
    job_id = uuid.uuid4().hex[:12]
    _scan_jobs[job_id] = {
        "id": job_id, "status": "queued", "target": target or "", "label": label,
        "speed": speed, "total_subnets": len(prefixes), "done_subnets": 0,
        "total_hosts": len(prefixes) * 254, "done_hosts": 0, "found": 0,
        "devices": [], "error": None, "cancel": False,
        "started": int(time.time() * 1000), "finished": None,
    }
    asyncio.get_event_loop().create_task(run_scan_job(job_id, target, speed))
    return job_id


# Häufige Ports mit Klartext-Namen (für die Portscan-Ausgabe)
COMMON_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP",
    110: "POP3", 135: "RPC", 139: "NetBIOS", 143: "IMAP", 443: "HTTPS",
    445: "SMB", 993: "IMAPS", 995: "POP3S", 1433: "MSSQL", 3306: "MySQL",
    3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 8080: "HTTP-Alt", 8443: "HTTPS-Alt",
}


def parse_port_spec(spec: str) -> list[int]:
    """
    Wandelt eine Port-Angabe wie "22,80,8000-8100" in eine sortierte, eindeutige
    Liste gültiger Ports (1-65535) um. Ungültige Teile werden ignoriert.
    """
    ports: set[int] = set()
    for part in (spec or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            for p in range(max(1, start), min(65535, end) + 1):
                ports.add(p)
        else:
            try:
                p = int(part)
            except ValueError:
                continue
            if 1 <= p <= 65535:
                ports.add(p)
    return sorted(ports)


async def scan_ports(
    ip: str,
    ports: list[int] | None = None,
    concurrency: int = 100,
    timeout: float = 1.0,
) -> list[dict]:
    """
    Prüft für eine IP, welche der angegebenen Ports offen sind.
    Ohne Portliste werden die gängigen Ports (COMMON_PORTS) geprüft.
    Rückgabe: [{"port": 22, "service": "SSH", "open": True}, ...] (nur offene).

    Bei sehr großen Portlisten (z.B. "alle Ports") sorgen höhere Nebenläufigkeit
    und ein kürzeres Timeout dafür, dass der Scan in vertretbarer Zeit fertig wird.
    """
    if ports is None:
        ports = sorted(COMMON_PORTS.keys())

    semaphore = asyncio.Semaphore(concurrency)
    open_ports = []

    async def check_port(port: int):
        async with semaphore:
            try:
                fut = asyncio.open_connection(ip, port)
                reader, writer = await asyncio.wait_for(fut, timeout=timeout)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                open_ports.append({"port": port, "service": COMMON_PORTS.get(port, "?"), "open": True})
            except Exception:
                pass  # Port geschlossen/gefiltert

    await asyncio.gather(*(check_port(p) for p in ports))
    open_ports.sort(key=lambda x: x["port"])
    return open_ports

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


# Häufige Ports mit Klartext-Namen (für die Portscan-Ausgabe)
COMMON_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP",
    110: "POP3", 135: "RPC", 139: "NetBIOS", 143: "IMAP", 443: "HTTPS",
    445: "SMB", 993: "IMAPS", 995: "POP3S", 1433: "MSSQL", 3306: "MySQL",
    3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 8080: "HTTP-Alt", 8443: "HTTPS-Alt",
}


async def scan_ports(ip: str, ports: list[int] | None = None) -> list[dict]:
    """
    Prüft für eine IP, welche der angegebenen Ports offen sind.
    Ohne Portliste werden die gängigen Ports (COMMON_PORTS) geprüft.
    Rückgabe: [{"port": 22, "service": "SSH", "open": True}, ...] (nur offene).
    """
    if ports is None:
        ports = sorted(COMMON_PORTS.keys())

    semaphore = asyncio.Semaphore(100)
    open_ports = []

    async def check_port(port: int):
        async with semaphore:
            try:
                fut = asyncio.open_connection(ip, port)
                reader, writer = await asyncio.wait_for(fut, timeout=1.0)
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

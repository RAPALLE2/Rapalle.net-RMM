"""
node_l2.py  -  NODE-MODUL (wird nur auf Nodes nachgeladen)
==========================================================
Die L2-Bruecke: gibt einem VPN-Benutzer eine ECHTE Adresse aus dem LAN
hinter der Node, statt ihn hinter der Adresse der Node zu verstecken (NAT).

Was dazu noetig ist - und warum es drei Teile sind
--------------------------------------------------
Damit ein Geraet im LAN den Benutzer unter 192.168.1.99 sieht, muss die
Node drei Dinge tun. Nur eines davon reicht nicht:

  1. ARP BEANTWORTEN. Fragt ein LAN-Geraet "Wer hat 192.168.1.99?", muss
     jemand mit einer MAC-Adresse antworten - sonst kommt nie ein Paket an.
     Die Node antwortet mit ihrer eigenen MAC.

  2. EINGEHENDE RAHMEN EINFANGEN. Durch Schritt 1 schickt der Switch die
     Rahmen fuer 192.168.1.99 an die Node. Sie muss den Ethernet-Kopf
     abziehen und das IP-Paket in den Tunnel geben.

  3. AUSGEHENDE PAKETE EINSPEISEN. Was aus dem Tunnel kommt, traegt bereits
     192.168.1.99 als Absender. Die Node packt es in einen Ethernet-Rahmen
     und legt es aufs Kabel - dazu muss sie die MAC des Ziels kennen, also
     selbst ARP sprechen.

Eine fruehere Fassung dieses Moduls konnte nur Schritt 1. Die Adresse war
damit zwar beansprucht, es floss aber kein einziges Datenpaket - die
Bruecke sah funktionsfaehig aus und war es nicht. Jetzt sind alle drei
Schritte da.

Warum das ueberhaupt Aufwand ist
--------------------------------
Ethernet-Rahmen zu lesen und zu schreiben ist keine gewoehnliche
Socket-Operation:

  Linux    AF_PACKET-Rohsocket. Braucht Root bzw. CAP_NET_RAW.
  Windows  Ohne Treiber gar nicht moeglich. Wir setzen Npcap voraus und
           sprechen es ueber seine DLL an (ctypes, kein Fremdpaket).

Beides wird AUSSCHLIESSLICH auf Nodes eingerichtet und im Dashboard
deutlich angekuendigt, bevor es passiert.

Stand der Erprobung
-------------------
Der Linux-Weg (AF_PACKET) ist mit einer nachgebauten Netzwerkkarte in
beiden Richtungen geprueft: ARP-Antwort, Einfangen, Einspeisen, ARP-
Aufloesung mit Warteschlange, Abweisen fremder Absenderadressen.

Der WINDOWS-Weg ueber Npcap ist NICHT auf echter Hardware erprobt - hier
stand kein Windows zur Verfuegung. Die Strukturen und Aufrufe folgen der
libpcap-Schnittstelle, aber der erste Lauf auf einem echten Geraet gehoert
beobachtet. Alle Fehler fuehren dabei zum NAT-Rueckfall, nicht zum Absturz.

Der Rueckfall ist ausdruecklich vorgesehen
------------------------------------------
Schlaegt irgendetwas fehl - kein Root, Treiber abgelehnt, Adapter nicht
ansprechbar -, ist das KEIN Fehlerfall. 'available' bleibt dann False, und
node_vpn.py betreibt den Tunnel per NAT weiter. Der Benutzer kommt genauso
ins Netz, nur mit der Adresse der Node als Absender. Dieses Modul meldet
das ehrlich, statt es zu verschleiern.
"""

from __future__ import annotations

import ctypes
import os
import platform
import struct
import subprocess
import threading
import time

IS_WINDOWS = platform.system().lower().startswith("win")

ETH_P_ALL = 0x0003
ETH_P_IP = 0x0800
ETH_P_ARP = 0x0806
ARP_REQUEST = 1
ARP_REPLY = 2
BROADCAST = b"\xff" * 6

# Wie lange eine gelernte MAC-Adresse gilt. Kurz genug, damit ein
# getauschtes Geraet nicht ewig falsch adressiert wird.
ARP_CACHE_TTL = 120.0
# So lange wird ein Paket zurueckgehalten, waehrend die MAC des Ziels
# ermittelt wird. Danach wird es verworfen - TCP schickt es ohnehin erneut.
ARP_WAIT = 1.5

# Wo Npcap heruntergeladen wird, falls es fehlt. Bewusst die offizielle
# Quelle - ein Netzwerktreiber aus zweiter Hand waere unverantwortlich.
NPCAP_URL = "https://npcap.com/dist/npcap-1.79.exe"


class L2Result:
    """Ergebnis eines Einrichtungsversuchs - immer MIT Begruendung."""

    def __init__(self, ok: bool, reason: str = "", needs_driver: bool = False):
        self.ok = ok
        self.reason = reason
        self.needs_driver = needs_driver

    def as_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason,
                "needs_driver": self.needs_driver}


# ======================================================================
# Die Verbindung zum Netzwerkadapter - zwei Bauarten, eine Schnittstelle
# ======================================================================
# Oben drueber liegt die eigentliche Bruecke und weiss nicht, ob darunter
# AF_PACKET oder Npcap arbeitet. Beide koennen genau vier Dinge:
# oeffnen, einen Rahmen lesen, einen Rahmen schreiben, schliessen.

class _Link:
    """Gemeinsame Schnittstelle beider Bauarten."""

    name = ""
    mac = b"\x00" * 6

    def recv(self, timeout: float = 0.2) -> bytes | None:
        raise NotImplementedError

    def send(self, frame: bytes) -> None:
        raise NotImplementedError

    def set_filter(self, addresses: list[str]) -> None:
        """Optional: nur noch ARP und Verkehr an diese Adressen einfangen."""

    def close(self) -> None:
        raise NotImplementedError


# ----------------------------------------------------------------------
# Linux: AF_PACKET
# ----------------------------------------------------------------------

class _LinuxLink(_Link):
    """
    Rohsocket auf Ethernet-Ebene.

    Ein Wort zur Last: Wir fangen ALLE Rahmen ein, die der Adapter sieht,
    und sortieren in Python. Auf einem geswitchten Netz ist das
    unproblematisch - der Switch schickt uns nur Broadcasts und das, was
    wirklich an unsere MAC gerichtet ist. Und genau das ist nach der
    ARP-Antwort auch der Verkehr fuer die uebernommene Adresse.
    """

    def __init__(self, interface: str = ""):
        import socket as _s
        self._socket_mod = _s
        self.name = interface or _default_interface()
        sock = _s.socket(_s.AF_PACKET, _s.SOCK_RAW, _s.htons(ETH_P_ALL))
        sock.bind((self.name, 0))
        sock.settimeout(0.2)
        self._sock = sock
        self._send_lock = threading.Lock()
        self.mac = bytes(sock.getsockname()[4][:6])

    def recv(self, timeout: float = 0.2) -> bytes | None:
        self._sock.settimeout(timeout)
        try:
            return self._sock.recv(65535)
        except (OSError, self._socket_mod.timeout):
            return None

    def send(self, frame: bytes) -> None:
        with self._send_lock:
            self._sock.send(frame)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


# ----------------------------------------------------------------------
# Windows: Npcap ueber ctypes
# ----------------------------------------------------------------------
# Npcap stellt die klassische libpcap-Schnittstelle bereit (wpcap.dll).
# Wir sprechen sie direkt an, statt ein Fremdpaket wie scapy oder pypcap
# vorauszusetzen - der Agent soll ohne zusaetzliche Python-Pakete
# auskommen.
#
# Zwei Handles statt einem: libpcap ist fuer denselben Handle nicht
# thread-sicher. Ein Handle liest, der andere schreibt. Sich das zu sparen
# waere eine Einladung fuer sporadische, kaum reproduzierbare Abstuerze
# genau dann, wenn viel Verkehr laeuft.

class _pcap_if(ctypes.Structure):
    pass


_pcap_if._fields_ = [
    ("next", ctypes.POINTER(_pcap_if)),
    ("name", ctypes.c_char_p),
    ("description", ctypes.c_char_p),
    ("addresses", ctypes.c_void_p),
    ("flags", ctypes.c_uint),
]


class _pcap_pkthdr(ctypes.Structure):
    # timeval unter Windows: ZWEI 32-BIT-WERTE.
    #
    # Bewusst c_int32 und nicht c_long: c_long ist plattformabhaengig - unter
    # Windows 4 Byte, unter Linux 8. Hier waere es zufaellig richtig, weil
    # dieses Modul nur unter Windows laeuft. Eine Struktur, die nur zufaellig
    # stimmt, ist aber genau die Sorte Fehler, die spaeter niemand findet:
    # Bei falscher Groesse liest man die Paketlaenge aus dem falschen Versatz
    # und bekommt Datenmuell statt einer Fehlermeldung.
    _fields_ = [("ts_sec", ctypes.c_int32), ("ts_usec", ctypes.c_int32),
                ("caplen", ctypes.c_uint32), ("len", ctypes.c_uint32)]


class _bpf_program(ctypes.Structure):
    _fields_ = [("bf_len", ctypes.c_uint), ("bf_insns", ctypes.c_void_p)]


PCAP_ERRBUF_SIZE = 256


def _load_wpcap():
    dll = ctypes.WinDLL("wpcap.dll")
    dll.pcap_findalldevs.argtypes = [ctypes.POINTER(ctypes.POINTER(_pcap_if)),
                                     ctypes.c_char_p]
    dll.pcap_findalldevs.restype = ctypes.c_int
    dll.pcap_freealldevs.argtypes = [ctypes.POINTER(_pcap_if)]
    dll.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_char_p]
    dll.pcap_open_live.restype = ctypes.c_void_p
    dll.pcap_close.argtypes = [ctypes.c_void_p]
    dll.pcap_next_ex.argtypes = [ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.POINTER(_pcap_pkthdr)),
                                 ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
    dll.pcap_next_ex.restype = ctypes.c_int
    dll.pcap_sendpacket.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    dll.pcap_sendpacket.restype = ctypes.c_int
    dll.pcap_compile.argtypes = [ctypes.c_void_p, ctypes.POINTER(_bpf_program),
                                 ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
    dll.pcap_compile.restype = ctypes.c_int
    dll.pcap_setfilter.argtypes = [ctypes.c_void_p, ctypes.POINTER(_bpf_program)]
    dll.pcap_setfilter.restype = ctypes.c_int
    dll.pcap_freecode.argtypes = [ctypes.POINTER(_bpf_program)]
    dll.pcap_geterr.argtypes = [ctypes.c_void_p]
    dll.pcap_geterr.restype = ctypes.c_char_p
    return dll


class _WindowsLink(_Link):
    """Ethernet-Zugriff ueber Npcap."""

    def __init__(self, interface: str = ""):
        self._dll = _load_wpcap()
        self.name = interface or self._pick_device()
        if not self.name:
            raise RuntimeError("Kein passender Netzwerkadapter gefunden")

        errbuf = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
        dev = self.name.encode("utf-8")
        # snaplen 65535, promisc 0 (wir brauchen nur, was an uns geht),
        # to_ms 100 damit die Leseschleife regelmaessig zurueckkehrt und
        # den Stopp-Wunsch bemerkt.
        self._rx = self._dll.pcap_open_live(dev, 65535, 0, 100, errbuf)
        if not self._rx:
            raise RuntimeError(f"Adapter nicht zu oeffnen: "
                               f"{errbuf.value.decode('utf-8', 'replace')}")
        self._tx = self._dll.pcap_open_live(dev, 65535, 0, 100, errbuf)
        if not self._tx:
            self._dll.pcap_close(self._rx)
            raise RuntimeError("Zweiter Adapter-Zugang (Senden) nicht moeglich")

        self._send_lock = threading.Lock()
        self.mac = _windows_mac_for_pcap_device(self.name)
        if self.mac == b"\x00" * 6:
            raise RuntimeError("MAC-Adresse des Adapters nicht ermittelbar")

    def _pick_device(self) -> str:
        """Den Adapter mit der Standardroute waehlen."""
        alldevs = ctypes.POINTER(_pcap_if)()
        errbuf = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
        if self._dll.pcap_findalldevs(ctypes.byref(alldevs), errbuf) != 0:
            return ""
        try:
            wanted = _windows_default_adapter_guid()
            first = ""
            node = alldevs
            while node:
                name = node.contents.name.decode("utf-8", "replace")
                if not first:
                    first = name
                if wanted and wanted.lower() in name.lower():
                    return name
                node = node.contents.next
            return first
        finally:
            self._dll.pcap_freealldevs(alldevs)

    def recv(self, timeout: float = 0.2) -> bytes | None:
        header = ctypes.POINTER(_pcap_pkthdr)()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        rc = self._dll.pcap_next_ex(self._rx, ctypes.byref(header),
                                    ctypes.byref(data))
        if rc != 1:
            return None      # 0 = Zeitablauf, -1/-2 = Fehler bzw. Ende
        length = header.contents.caplen
        return bytes(bytearray(data[:length]))

    def send(self, frame: bytes) -> None:
        with self._send_lock:
            if self._dll.pcap_sendpacket(self._tx, frame, len(frame)) != 0:
                err = self._dll.pcap_geterr(self._tx)
                raise OSError(err.decode("utf-8", "replace") if err else "Sendefehler")

    def set_filter(self, addresses: list[str]) -> None:
        """
        Nur noch Wesentliches einfangen.

        Ohne Filter reicht Npcap jeden Rahmen bis nach Python durch - auf
        einem belebten Netz ist das spuerbar. Der Filter laeuft im Treiber
        und kostet uns nichts.
        """
        if addresses:
            hosts = " or ".join(f"dst host {a}" for a in addresses)
            expr = f"arp or ({hosts})"
        else:
            expr = "arp"
        prog = _bpf_program()
        if self._dll.pcap_compile(self._rx, ctypes.byref(prog),
                                  expr.encode("ascii"), 1, 0xFFFFFFFF) != 0:
            return   # Filter ist eine Optimierung, kein Muss
        try:
            self._dll.pcap_setfilter(self._rx, ctypes.byref(prog))
        finally:
            self._dll.pcap_freecode(ctypes.byref(prog))

    def close(self) -> None:
        for handle in ("_rx", "_tx"):
            try:
                self._dll.pcap_close(getattr(self, handle))
            except Exception:
                pass


# ----------------------------------------------------------------------
# Windows-Hilfen: MAC und Standardadapter ueber iphlpapi
# ----------------------------------------------------------------------

class _IP_ADDR_STRING(ctypes.Structure):
    pass


_IP_ADDR_STRING._fields_ = [
    ("Next", ctypes.POINTER(_IP_ADDR_STRING)),
    ("IpAddress", ctypes.c_char * 16),
    ("IpMask", ctypes.c_char * 16),
    ("Context", ctypes.c_ulong),
]


class _IP_ADAPTER_INFO(ctypes.Structure):
    pass


_IP_ADAPTER_INFO._fields_ = [
    ("Next", ctypes.POINTER(_IP_ADAPTER_INFO)),
    ("ComboIndex", ctypes.c_ulong),
    ("AdapterName", ctypes.c_char * 260),
    ("Description", ctypes.c_char * 132),
    ("AddressLength", ctypes.c_uint),
    ("Address", ctypes.c_ubyte * 8),
    ("Index", ctypes.c_ulong),
    ("Type", ctypes.c_uint),
    ("DhcpEnabled", ctypes.c_uint),
    ("CurrentIpAddress", ctypes.POINTER(_IP_ADDR_STRING)),
    ("IpAddressList", _IP_ADDR_STRING),
    ("GatewayList", _IP_ADDR_STRING),
    ("DhcpServer", _IP_ADDR_STRING),
    ("HaveWins", ctypes.c_int),
    ("PrimaryWinsServer", _IP_ADDR_STRING),
    ("SecondaryWinsServer", _IP_ADDR_STRING),
    ("LeaseObtained", ctypes.c_longlong),
    ("LeaseExpires", ctypes.c_longlong),
]


def _windows_adapters() -> list[dict]:
    """Alle Adapter mit GUID, MAC und Standardgateway."""
    iphlpapi = ctypes.WinDLL("iphlpapi.dll")
    size = ctypes.c_ulong(0)
    iphlpapi.GetAdaptersInfo(None, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetAdaptersInfo(
            ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_INFO)),
            ctypes.byref(size)) != 0:
        return []
    out = []
    node = ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_INFO))
    while node:
        info = node.contents
        gateway = info.GatewayList.IpAddress.decode("ascii", "replace").strip("\x00")
        out.append({
            "guid": info.AdapterName.decode("ascii", "replace"),
            "mac": bytes(info.Address[:info.AddressLength])[:6],
            "gateway": gateway,
            "ip": info.IpAddressList.IpAddress.decode("ascii", "replace").strip("\x00"),
        })
        node = info.Next
    return out


def _windows_default_adapter_guid() -> str:
    """Der Adapter mit einem echten Standardgateway - dort liegt das LAN."""
    for a in _windows_adapters():
        if a["gateway"] and a["gateway"] != "0.0.0.0":
            return a["guid"]
    return ""


def _windows_mac_for_pcap_device(pcap_name: str) -> bytes:
    """
    Ordnet einem pcap-Namen die MAC zu.

    Der pcap-Name sieht aus wie '\\Device\\NPF_{GUID}', die Adapterliste von
    Windows kennt '{GUID}'. Verglichen wird deshalb ueber die GUID.
    """
    for a in _windows_adapters():
        if a["guid"] and a["guid"].lower() in pcap_name.lower():
            return a["mac"]
    return b"\x00" * 6


# ======================================================================
# Voraussetzungen pruefen
# ======================================================================

def probe() -> L2Result:
    """
    Kann diese Node eine L2-Bruecke betreiben? Ohne etwas zu veraendern.

    Wird vom Dashboard aufgerufen, BEVOR dem Benutzer die Option angeboten
    wird - damit dort nicht etwas anwaehlbar ist, das hier ohnehin
    scheitern wuerde.
    """
    if IS_WINDOWS:
        if not _npcap_present():
            return L2Result(False, "Npcap-Treiber fehlt", needs_driver=True)
        try:
            _load_wpcap()
        except Exception as e:
            return L2Result(False, f"wpcap.dll nicht nutzbar: {e}",
                            needs_driver=True)
        return L2Result(True, "Npcap vorhanden")

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return L2Result(False, "Root-Rechte fehlen (CAP_NET_RAW noetig)")
    try:
        import socket as _s
        s = _s.socket(_s.AF_PACKET, _s.SOCK_RAW, _s.htons(ETH_P_ALL))
        s.close()
    except Exception as e:
        return L2Result(False, f"Rohsocket nicht moeglich: {e}")
    return L2Result(True, "AF_PACKET verfuegbar")


def _npcap_present() -> bool:
    for name in ("wpcap.dll", "Packet.dll"):
        try:
            ctypes.WinDLL(name)
        except OSError:
            return False
    return True


# ======================================================================
# Treiberinstallation (nur Windows, nur auf ausdrueckliche Anweisung)
# ======================================================================

def install_driver(download, log=print) -> L2Result:
    """
    Installiert Npcap still im Hintergrund.

    'download' ist eine Funktion (url) -> lokaler Pfad; sie kommt vom
    Agenten, damit hier keine eigene HTTP-Logik entsteht.

    Diese Funktion wird NUR aufgerufen, wenn im Dashboard ausdruecklich
    zugestimmt wurde. Sie installiert nichts von sich aus.
    """
    if not IS_WINDOWS:
        return L2Result(False, "Unter Linux wird kein Treiber installiert - "
                               "es fehlen nur die Rechte (Root/CAP_NET_RAW).")
    if _npcap_present():
        return L2Result(True, "Npcap war bereits vorhanden")
    try:
        installer = download(NPCAP_URL)
    except Exception as e:
        return L2Result(False, f"Download fehlgeschlagen: {e}", needs_driver=True)

    log("[node-l2] Installiere Npcap (still)...")
    try:
        # /S = still. WinPcap-Vertraeglichkeit bleibt AUS: Sie ueberschreibt
        # Systemdateien und bricht andere Werkzeuge auf demselben Rechner.
        res = subprocess.run([installer, "/S"], capture_output=True, timeout=300)
    except Exception as e:
        return L2Result(False, f"Installer nicht startbar: {e}", needs_driver=True)
    finally:
        try:
            os.remove(installer)
        except OSError:
            pass

    if res.returncode != 0:
        return L2Result(False, f"Installer endete mit Code {res.returncode}",
                        needs_driver=True)
    for _ in range(15):
        if _npcap_present():
            return L2Result(True, "Npcap installiert")
        time.sleep(1)
    return L2Result(False, "Npcap installiert, aber noch nicht ansprechbar - "
                           "ein Neustart der Node behebt das meist")


# ======================================================================
# Die Bruecke
# ======================================================================

class L2Bridge:
    """
    Traegt den Verkehr fuer uebernommene LAN-Adressen in beide Richtungen.

    Bewusst eng gefasst: Die Bruecke bearbeitet AUSSCHLIESSLICH Adressen,
    die ihr per claim() genannt wurden. Sie beantwortet kein fremdes ARP
    und leitet keine fremden Rahmen weiter - eine Bruecke, die alles
    durchreicht, waere im LAN kaum von einem Angriff zu unterscheiden.
    """

    def __init__(self, interface: str = "", log=print, send_to_tunnel=None):
        self.interface = interface
        self.log = log
        # Rueckruf (tunnel_id, ip_packet) -> None. Damit gehen eingefangene
        # Pakete in den Tunnel.
        self.send_to_tunnel = send_to_tunnel
        self.available = False
        self.reason = "nicht eingerichtet"
        self.claimed: dict[str, str] = {}      # LAN-Adresse -> Tunnel-ID
        self.link: _Link | None = None
        self.mac = b"\x00" * 6
        self._arp_cache: dict[str, tuple[bytes, float]] = {}
        self._pending: dict[str, list[tuple[bytes, float]]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.rx_frames = 0
        self.tx_frames = 0

    # -- Start / Stopp -------------------------------------------------

    def start(self) -> L2Result:
        state = probe()
        if not state.ok:
            self.available, self.reason = False, state.reason
            self.log(f"[node-l2] Bruecke nicht verfuegbar: {state.reason} "
                     f"- die Tunnel laufen im NAT-Betrieb weiter")
            return state
        try:
            self.link = (_WindowsLink(self.interface) if IS_WINDOWS
                         else _LinuxLink(self.interface))
        except Exception as e:
            self.available, self.reason = False, str(e)
            self.log(f"[node-l2] Start fehlgeschlagen: {e} - NAT-Betrieb")
            return L2Result(False, str(e))

        self.mac = self.link.mac
        self.interface = self.link.name
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="l2-bridge",
                                        daemon=True)
        self._thread.start()
        self.available, self.reason = True, "aktiv"
        self.log(f"[node-l2] Bruecke aktiv auf {self.interface} "
                 f"({_mac_str(self.mac)})")
        return L2Result(True, "aktiv")

    def stop(self) -> None:
        self._stop.set()
        if self.link:
            self.link.close()
        self.available = False
        self.reason = "beendet"

    # -- Adressen uebernehmen -----------------------------------------

    def claim(self, lan_address: str, tunnel_id: str) -> None:
        if not self.available:
            raise RuntimeError(self.reason or "Bruecke nicht aktiv")
        if not lan_address:
            raise ValueError("keine LAN-Adresse angegeben")
        _ip_bytes(lan_address)          # wirft bei Unsinn
        self.claimed[lan_address] = tunnel_id
        if self.link:
            self.link.set_filter(list(self.claimed))
        # Unaufgefordert bekanntgeben, dass wir diese Adresse haben. Damit
        # lernen Switch und Nachbarn sie sofort, statt erst beim naechsten
        # ARP-Durchlauf.
        self._announce(lan_address)
        self.log(f"[node-l2] Adresse {lan_address} uebernommen "
                 f"(Tunnel {tunnel_id})")

    def release(self, tunnel_id: str) -> None:
        for addr, tid in list(self.claimed.items()):
            if tid == tunnel_id:
                self.claimed.pop(addr, None)
                self.log(f"[node-l2] Adresse {addr} freigegeben")
        if self.link:
            self.link.set_filter(list(self.claimed))

    # -- Richtung 1: LAN -> Tunnel -------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self.link.recv(0.2)
            except Exception as e:
                self.log(f"[node-l2] Lesefehler: {e}")
                break
            if not frame or len(frame) < 14:
                self._expire()
                continue
            try:
                self._handle_frame(frame)
            except Exception as e:
                # Ein einzelner kaputter Rahmen darf die Bruecke nie beenden.
                self.log(f"[node-l2] Rahmen verworfen: {e!r}")

    def _handle_frame(self, frame: bytes) -> None:
        ethertype = struct.unpack(">H", frame[12:14])[0]
        if ethertype == ETH_P_ARP:
            self._handle_arp(frame)
            return
        if ethertype != ETH_P_IP or len(frame) < 34:
            return
        # Nur was an UNSERE MAC gerichtet ist - Broadcast-Verkehr gehoert
        # nicht in einen fremden Tunnel.
        if frame[0:6] != self.mac:
            return
        packet = frame[14:]
        dst = _ip_str(packet[16:20])
        tunnel_id = self.claimed.get(dst)
        if not tunnel_id:
            return
        # Absender-MAC merken: spart spaeter eine ARP-Anfrage.
        self._arp_cache[_ip_str(packet[12:16])] = (frame[6:12], time.monotonic())
        self.rx_frames += 1
        if self.send_to_tunnel:
            self.send_to_tunnel(tunnel_id, packet)

    def _handle_arp(self, frame: bytes) -> None:
        if len(frame) < 42:
            return
        arp = frame[14:42]
        opcode = struct.unpack(">H", arp[6:8])[0]
        sender_mac, sender_ip = arp[8:14], _ip_str(arp[14:18])
        target_ip = _ip_str(arp[24:28])

        # Immer mitlernen - auch aus fremden Gespraechen.
        if sender_mac != self.mac:
            self._arp_cache[sender_ip] = (sender_mac, time.monotonic())
            self._flush_pending(sender_ip)

        if opcode != ARP_REQUEST:
            return
        if target_ip not in self.claimed:
            return          # nicht unsere Adresse - schweigen
        self._send_arp(ARP_REPLY, target_ip, sender_ip, sender_mac)

    # -- Richtung 2: Tunnel -> LAN -------------------------------------

    def inject(self, packet: bytes, tunnel_id: str) -> bool:
        """
        Legt ein IP-Paket aus dem Tunnel aufs Kabel.

        Der Absender bleibt unveraendert - genau das ist der Unterschied
        zum NAT-Betrieb. Ist die MAC des Ziels noch unbekannt, wird das
        Paket kurz zurueckgehalten und eine ARP-Anfrage geschickt.
        """
        if not self.available or len(packet) < 20 or (packet[0] >> 4) != 4:
            return False
        src = _ip_str(packet[12:16])
        if self.claimed.get(src) != tunnel_id:
            # Der Tunnel versucht, mit einer fremden Absenderadresse zu
            # senden. Das wird nicht durchgelassen.
            return False
        dst = _ip_str(packet[16:20])
        target = dst if _same_subnet(src, dst) else self._gateway_for(src)
        mac = self._lookup(target)
        if mac is None:
            self._pending.setdefault(target, []).append((packet, time.monotonic()))
            self._send_arp(ARP_REQUEST, src, target, BROADCAST)
            return True
        self._emit(mac, packet)
        return True

    def _emit(self, dst_mac: bytes, packet: bytes) -> None:
        frame = dst_mac + self.mac + struct.pack(">H", ETH_P_IP) + packet
        try:
            self.link.send(frame)
            self.tx_frames += 1
        except Exception as e:
            self.log(f"[node-l2] Senden fehlgeschlagen: {e}")

    # -- ARP-Handwerk ---------------------------------------------------

    def _lookup(self, ip: str) -> bytes | None:
        entry = self._arp_cache.get(ip)
        if entry and time.monotonic() - entry[1] < ARP_CACHE_TTL:
            return entry[0]
        return None

    def _send_arp(self, opcode: int, sender_ip: str, target_ip: str,
                  target_mac: bytes) -> None:
        body = (struct.pack(">HHBBH", 1, ETH_P_IP, 6, 4, opcode)
                + self.mac + _ip_bytes(sender_ip)
                + (b"\x00" * 6 if target_mac == BROADCAST else target_mac)
                + _ip_bytes(target_ip))
        frame = target_mac + self.mac + struct.pack(">H", ETH_P_ARP) + body
        try:
            self.link.send(frame)
        except Exception as e:
            self.log(f"[node-l2] ARP nicht sendbar: {e}")

    def _announce(self, address: str) -> None:
        """Unaufgeforderte ARP-Antwort ('gratuitous ARP')."""
        self._send_arp(ARP_REPLY, address, address, BROADCAST)

    def _flush_pending(self, ip: str) -> None:
        queued = self._pending.pop(ip, [])
        mac = self._lookup(ip)
        if not mac:
            return
        for packet, _ts in queued:
            self._emit(mac, packet)

    def _expire(self) -> None:
        """Zu lange wartende Pakete verwerfen - TCP wiederholt von selbst."""
        now = time.monotonic()
        for ip, queued in list(self._pending.items()):
            keep = [(p, t) for p, t in queued if now - t < ARP_WAIT]
            if keep:
                self._pending[ip] = keep
            else:
                self._pending.pop(ip, None)

    def _gateway_for(self, address: str) -> str:
        """Das Standardgateway - fuer alles ausserhalb des eigenen Netzes."""
        gw = _default_gateway()
        return gw or address

    def stats(self) -> dict:
        return {"available": self.available, "reason": self.reason,
                "interface": self.interface, "mac": _mac_str(self.mac),
                "claimed": dict(self.claimed),
                "rx_frames": self.rx_frames, "tx_frames": self.tx_frames}


# ======================================================================
# Kleine Helfer
# ======================================================================

def _ip_bytes(text: str) -> bytes:
    parts = [int(p) for p in text.split(".")]
    if len(parts) != 4 or any(p < 0 or p > 255 for p in parts):
        raise ValueError(f"Keine IPv4-Adresse: {text}")
    return bytes(parts)


def _ip_str(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def _mac_str(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def _same_subnet(a: str, b: str, bits: int = 24) -> bool:
    """
    Grobe Einschaetzung, ob zwei Adressen im selben Netz liegen.

    Bewusst /24 als Annahme: Die echte Netzmaske der uebernommenen Adresse
    kennt nur das LAN. Liegt man daneben, geht das Paket ueber das Gateway -
    also den Weg, den auch jeder gewoehnliche Rechner nimmt. Der Fehler
    kostet also einen Zwischenschritt, nichts weiter.
    """
    try:
        return _ip_bytes(a)[:bits // 8] == _ip_bytes(b)[:bits // 8]
    except ValueError:
        return False


def _default_interface() -> str:
    """Der Adapter mit der Standardroute - dort liegt das LAN."""
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        pass
    return "eth0"


def _default_gateway() -> str:
    """Adresse des Standardgateways."""
    if IS_WINDOWS:
        try:
            for a in _windows_adapters():
                if a["gateway"] and a["gateway"] != "0.0.0.0":
                    return a["gateway"]
        except Exception:
            pass
        return ""
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    raw = bytes.fromhex(parts[2])[::-1]   # Little Endian
                    return _ip_str(raw)
    except (OSError, ValueError):
        pass
    return ""

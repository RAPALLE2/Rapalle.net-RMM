"""RAPALLE.net RMM Client - das Dashboard als eigene App (Windows, Linux, Android)."""
# 1.6.7: Tunnelstart erst nach dem WireGuard-Manager, kein Parallel-Abgleich manueller Tunnel, Protokoll bei Fehler
# 1.6.6: laufenden Tunnel nie erneut einrichten, verwaiste Adapter entfernen, Doppel-Anforderungen ignorieren
# 1.6.5: Agent + eigener Zugang auf demselben PC erkannt ("über Agent"), Tunnelstart wird geprüft
# 1.6.4: VPN-Bereich im Hauptfenster (Echtzeit), nur EINE Stelle richtet Tunnel ein, Android -> WireGuard-App
# 1.6.3: VPN-Tunnel ohne "existiert bereits"/"Objekt bereits vorhanden" (Sperre, DPAPI, sauberes Neu-Einrichten)
# 1.6.2: keine WireGuard-Fehlerfenster mehr ("kein installierter Dienst"), Tunnel zuverlaessig in der App
# 1.6.1: WireGuard-Tunnel in der WireGuard-App sichtbar, "DLL nicht gefunden" behoben
# 1.6.0: RDP/SSH/VNC + VPN direkt aus dem Dashboard, WireGuard-Datei aktivieren, Netzlaufwerk Windows 11
__version__ = "1.6.7"

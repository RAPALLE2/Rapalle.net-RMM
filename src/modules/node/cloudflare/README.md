# Cloudflare Tunnel – Node-Teil

Wird vom Modul „Cloudflare Tunnel“ automatisch auf eine Node verteilt, sobald eine Route
„RDP/SSH/VNC zu einem Agent“ mit *Tunnel endet auf einer Node* angelegt wird.
Jede Node bekommt einen eigenen Cloudflare-Tunnel (`rmm-node-…`); der Connector läuft als
Container `rmm-cloudflared` mit `network_mode: host`, damit er die Geräte im Netz der Node erreicht.
Nichts von Hand einstellen – Token und Routen pflegt das Modul.

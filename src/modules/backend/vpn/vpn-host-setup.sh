#!/bin/sh
# ============================================================================
# vpn-host-setup.sh - VPN-Host fuer RAPALLE.net RMM vorbereiten (WireGuard)
# ============================================================================
# Aufruf (als root):
#   sh vpn-host-setup.sh                 IN dem Rechner/CT/der VM, die VPN-Host ist (Node oder Backend):
#                                        installiert wireguard-tools, iptables, iproute2 (+ wireguard-go,
#                                        falls kein Kernel-WireGuard), schaltet IP-Weiterleitung dauerhaft ein.
#   sh vpn-host-setup.sh --pve-ct 105    AUF DEM PROXMOX-HOST fuer den Container 105: WireGuard-Kernelmodul
#                                        laden (dauerhaft) und /dev/net/tun in den CT durchreichen.
#                                        Danach CT neu starten und im CT dieses Skript ohne Option ausfuehren.
#   sh vpn-host-setup.sh --docker        AUF DEM DOCKER-HOST, wenn das Backend (im Container) VPN-Host ist:
#                                        Kernelmodul + IP-Weiterleitung auf dem Wirt, Hinweise fuer compose.
#   sh vpn-host-setup.sh --check         nur pruefen, nichts aendern.
# Danach in der VPN-App „Endpunkt testen“ ausfuehren.
set -u
MODE="host"; CTID=""
case "${1:-}" in
  --pve-ct) MODE="pve"; CTID="${2:-}";;
  --docker) MODE="docker";;
  --check)  MODE="check";;
  -h|--help) sed -n '2,20p' "$0"; exit 0;;
esac
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
err()  { printf '  \033[31m✘\033[0m %s\n' "$*"; }
need_root() { [ "$(id -u)" = 0 ] || { err "Bitte als root ausführen (sudo sh $0 $*)"; exit 1; }; }

pkg_install() {
  if command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get update -q >/dev/null 2>&1
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "$@" >/dev/null 2>&1
  elif command -v dnf >/dev/null 2>&1; then dnf install -y "$@" >/dev/null 2>&1
  elif command -v yum >/dev/null 2>&1; then yum install -y "$@" >/dev/null 2>&1
  elif command -v zypper >/dev/null 2>&1; then zypper -n install "$@" >/dev/null 2>&1
  elif command -v pacman >/dev/null 2>&1; then pacman -S --noconfirm "$@" >/dev/null 2>&1
  elif command -v apk >/dev/null 2>&1; then apk add --no-cache "$@" >/dev/null 2>&1
  else return 1; fi
}

kernel_wg() { [ -d /sys/module/wireguard ] || modprobe wireguard 2>/dev/null; [ -d /sys/module/wireguard ]; }

forward_on() {
  if [ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)" = 1 ]; then ok "IP-Weiterleitung ist an"; return; fi
  [ "$MODE" = check ] && { warn "IP-Weiterleitung ist aus"; return; }
  if sysctl -q -w net.ipv4.ip_forward=1 2>/dev/null; then
    mkdir -p /etc/sysctl.d && echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/99-rmm-vpn.conf
    ok "IP-Weiterleitung eingeschaltet (dauerhaft: /etc/sysctl.d/99-rmm-vpn.conf)"
  else
    warn "IP-Weiterleitung lässt sich hier nicht einschalten (Container?). Docker: in docker-compose.yml"
    warn "  sysctls: - net.ipv4.ip_forward=1  und  docker compose up -d. Proxmox-CT: auf dem PVE-Host"
    warn "  'sysctl -w net.ipv4.ip_forward=1' oder CT als privilegiert betreiben."
  fi
}

case "$MODE" in
pve)
  need_root "$@"
  [ -n "$CTID" ] || { err "CT-Nummer fehlt: sh $0 --pve-ct 105"; exit 1; }
  CONF="/etc/pve/lxc/$CTID.conf"
  [ -f "$CONF" ] || { err "$CONF nicht gefunden – läuft das auf dem Proxmox-Host?"; exit 1; }
  echo "Proxmox-Host: CT $CTID für WireGuard vorbereiten"
  if kernel_wg; then ok "WireGuard-Kernelmodul geladen"; else warn "Kernelmodul nicht ladbar – im CT wird wireguard-go benutzt"; fi
  mkdir -p /etc/modules-load.d && echo wireguard > /etc/modules-load.d/rmm-wireguard.conf
  modprobe tun 2>/dev/null; echo tun > /etc/modules-load.d/rmm-tun.conf
  grep -q "c 10:200 rwm" "$CONF" || echo "lxc.cgroup2.devices.allow: c 10:200 rwm" >> "$CONF"
  grep -q "dev/net/tun dev/net/tun" "$CONF" || echo "lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file" >> "$CONF"
  ok "/dev/net/tun wird an CT $CTID durchgereicht"
  echo; echo "Jetzt: 'pct reboot $CTID', danach IM CT:  sh vpn-host-setup.sh"
  echo "UDP-Port (ab 51820) im Router auf die IP des CT weiterleiten."
  ;;
docker)
  need_root "$@"
  echo "Docker-Host vorbereiten (Backend im Container ist VPN-Host)"
  if kernel_wg; then ok "WireGuard-Kernelmodul geladen"; echo wireguard > /etc/modules-load.d/rmm-wireguard.conf 2>/dev/null
  else warn "kein Kernel-WireGuard – der Container nutzt wireguard-go (/dev/net/tun nötig)"; fi
  modprobe tun 2>/dev/null; [ -c /dev/net/tun ] && ok "/dev/net/tun vorhanden" || err "/dev/net/tun fehlt (modprobe tun)"
  forward_on
  echo; echo "In docker-compose.yml (beim RMM schon enthalten): cap_add NET_ADMIN, devices /dev/net/tun,"
  echo "sysctls net.ipv4.ip_forward=1 und ports \"51820-51824:51820-51824/udp\". Dann: docker compose up -d"
  ;;
host|check)
  [ "$MODE" = host ] && need_root "$@"
  echo "VPN-Host prüfen/einrichten"
  MISSING=""
  command -v wg-quick >/dev/null 2>&1 || MISSING="$MISSING wireguard-tools"
  command -v iptables >/dev/null 2>&1 || MISSING="$MISSING iptables"
  command -v ip >/dev/null 2>&1 || MISSING="$MISSING iproute2"
  if kernel_wg; then ok "Kernel-WireGuard verfügbar"
  else
    if [ -c /dev/net/tun ]; then command -v wireguard-go >/dev/null 2>&1 || MISSING="$MISSING wireguard-go"
      warn "kein Kernel-WireGuard – benutze wireguard-go"
    else err "weder Kernel-WireGuard noch /dev/net/tun – Proxmox-CT: auf dem PVE-Host 'sh $0 --pve-ct <CTID>'"; fi
  fi
  if [ -n "$MISSING" ]; then
    if [ "$MODE" = check ]; then warn "fehlt:$MISSING"
    elif pkg_install $MISSING; then ok "installiert:$MISSING"
    else
      # iproute2 heisst bei manchen Distributionen anders
      pkg_install $(echo "$MISSING" | sed 's/iproute2/iproute/') && ok "installiert:$MISSING" || err "Installation fehlgeschlagen:$MISSING"
    fi
  else ok "wireguard-tools, iptables, iproute2 vorhanden"; fi
  forward_on
  if command -v iptables >/dev/null 2>&1; then
    POL=$(iptables -S FORWARD 2>/dev/null | awk '/^-P FORWARD/{print $3}')
    [ "$POL" = DROP ] && warn "FORWARD-Richtlinie ist DROP – das RMM setzt eigene Erlaubnis-Regeln je Tunnel" || ok "FORWARD-Richtlinie: ${POL:-ACCEPT}"
  fi
  echo; echo "Öffentliche IP dieses Anschlusses:"; (curl -fsS -m 8 https://api.ipify.org 2>/dev/null || wget -qO- -T 8 https://api.ipify.org 2>/dev/null || echo "  (nicht ermittelbar)"); echo
  echo "Diese IP (oder DynDNS ohne Cloudflare-Proxy) als Endpunkt eintragen und den UDP-Port im Router weiterleiten."
  ;;
esac

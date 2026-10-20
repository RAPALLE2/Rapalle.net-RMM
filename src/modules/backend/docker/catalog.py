# catalog.py (Modul "docker") - Container-Browser
# -----------------------------------------------
# 1) Katalog: bekannte Anwendungen mit fertiger docker-compose-Vorlage (anpassbar vor dem Anlegen)
# 2) Docker-Hub-Suche: beliebige Images finden -> einfache Vorlage (Image + ein Port)

from __future__ import annotations

import json
import secrets
import ssl
import urllib.parse
import urllib.request


def _pw() -> str:
    return secrets.token_urlsafe(18)


def templates() -> list[dict]:
    """Bei jedem Aufruf neue Zufallspasswoerter in den Vorlagen."""
    db_pw, root_pw = _pw(), _pw()
    return [
        {"id": "nginx", "name": "Nginx", "icon": "🟩", "category": "Web", "port": 80,
         "description": "Schneller Webserver für statische Seiten.",
         "compose": "services:\n  web:\n    image: nginx:alpine\n    restart: unless-stopped\n    ports:\n      - \"8080:80\"\n    volumes:\n      - html:/usr/share/nginx/html\nvolumes:\n  html:\n"},
        {"id": "uptime-kuma", "name": "Uptime Kuma", "icon": "📈", "category": "Monitoring", "port": 3001,
         "description": "Überwacht Websites und Dienste mit Statusseite.",
         "compose": "services:\n  kuma:\n    image: louislam/uptime-kuma:1\n    restart: unless-stopped\n    ports:\n      - \"3001:3001\"\n    volumes:\n      - data:/app/data\nvolumes:\n  data:\n"},
        {"id": "wordpress", "name": "WordPress", "icon": "📝", "category": "Web", "port": 80,
         "description": "WordPress mit MariaDB.",
         "compose": ("services:\n  db:\n    image: mariadb:11\n    restart: unless-stopped\n    environment:\n"
                     f"      MARIADB_DATABASE: wordpress\n      MARIADB_USER: wordpress\n      MARIADB_PASSWORD: {db_pw}\n"
                     f"      MARIADB_ROOT_PASSWORD: {root_pw}\n    volumes:\n      - db:/var/lib/mysql\n"
                     "  wordpress:\n    image: wordpress:6\n    restart: unless-stopped\n    depends_on:\n      - db\n"
                     "    ports:\n      - \"8081:80\"\n    environment:\n      WORDPRESS_DB_HOST: db\n      WORDPRESS_DB_USER: wordpress\n"
                     f"      WORDPRESS_DB_PASSWORD: {db_pw}\n      WORDPRESS_DB_NAME: wordpress\n    volumes:\n      - wp:/var/www/html\n"
                     "volumes:\n  db:\n  wp:\n")},
        {"id": "nextcloud", "name": "Nextcloud", "icon": "☁️", "category": "Dateien", "port": 80,
         "description": "Eigene Cloud für Dateien, Kalender, Kontakte.",
         "compose": ("services:\n  db:\n    image: mariadb:11\n    restart: unless-stopped\n    environment:\n"
                     f"      MARIADB_DATABASE: nextcloud\n      MARIADB_USER: nextcloud\n      MARIADB_PASSWORD: {db_pw}\n"
                     f"      MARIADB_ROOT_PASSWORD: {root_pw}\n    volumes:\n      - db:/var/lib/mysql\n"
                     "  app:\n    image: nextcloud:stable\n    restart: unless-stopped\n    depends_on:\n      - db\n"
                     "    ports:\n      - \"8082:80\"\n    environment:\n      MYSQL_HOST: db\n      MYSQL_DATABASE: nextcloud\n"
                     f"      MYSQL_USER: nextcloud\n      MYSQL_PASSWORD: {db_pw}\n    volumes:\n      - nc:/var/www/html\n"
                     "volumes:\n  db:\n  nc:\n")},
        {"id": "gitea", "name": "Gitea", "icon": "🍵", "category": "Entwicklung", "port": 3000,
         "description": "Leichtgewichtiger Git-Server.",
         "compose": "services:\n  gitea:\n    image: gitea/gitea:1\n    restart: unless-stopped\n    ports:\n      - \"3000:3000\"\n      - \"2222:22\"\n    volumes:\n      - data:/data\nvolumes:\n  data:\n"},
        {"id": "grafana", "name": "Grafana", "icon": "📊", "category": "Monitoring", "port": 3000,
         "description": "Dashboards für Messwerte.",
         "compose": "services:\n  grafana:\n    image: grafana/grafana-oss:latest\n    restart: unless-stopped\n    ports:\n      - \"3002:3000\"\n    volumes:\n      - data:/var/lib/grafana\nvolumes:\n  data:\n"},
        {"id": "adminer", "name": "Adminer", "icon": "🗄️", "category": "Datenbank", "port": 8080,
         "description": "Datenbank-Verwaltung im Browser.",
         "compose": "services:\n  adminer:\n    image: adminer:latest\n    restart: unless-stopped\n    ports:\n      - \"8083:8080\"\n"},
        {"id": "vaultwarden", "name": "Vaultwarden", "icon": "🔐", "category": "Sicherheit", "port": 80,
         "description": "Passwort-Manager (Bitwarden-kompatibel).",
         "compose": "services:\n  vaultwarden:\n    image: vaultwarden/server:latest\n    restart: unless-stopped\n    ports:\n      - \"8084:80\"\n    volumes:\n      - data:/data\nvolumes:\n  data:\n"},
        {"id": "homeassistant", "name": "Home Assistant", "icon": "🏠", "category": "Smart Home", "port": 8123,
         "description": "Hausautomation.",
         "compose": "services:\n  homeassistant:\n    image: ghcr.io/home-assistant/home-assistant:stable\n    restart: unless-stopped\n    ports:\n      - \"8123:8123\"\n    volumes:\n      - config:/config\nvolumes:\n  config:\n"},
        {"id": "pihole", "name": "Pi-hole", "icon": "🕳️", "category": "Netzwerk", "port": 80,
         "description": "Werbeblocker auf DNS-Ebene.",
         "compose": ("services:\n  pihole:\n    image: pihole/pihole:latest\n    restart: unless-stopped\n    ports:\n"
                     "      - \"53:53/tcp\"\n      - \"53:53/udp\"\n      - \"8085:80\"\n    environment:\n"
                     f"      FTLCONF_webserver_api_password: {db_pw}\n    volumes:\n      - etc:/etc/pihole\nvolumes:\n  etc:\n")},
        {"id": "postgres", "name": "PostgreSQL", "icon": "🐘", "category": "Datenbank", "port": 5432,
         "description": "Relationale Datenbank.",
         "compose": f"services:\n  db:\n    image: postgres:16\n    restart: unless-stopped\n    environment:\n      POSTGRES_PASSWORD: {db_pw}\n    volumes:\n      - data:/var/lib/postgresql/data\nvolumes:\n  data:\n"},
        {"id": "redis", "name": "Redis", "icon": "🟥", "category": "Datenbank", "port": 6379,
         "description": "Schneller Schlüssel-Wert-Speicher.",
         "compose": "services:\n  redis:\n    image: redis:7-alpine\n    restart: unless-stopped\n    volumes:\n      - data:/data\nvolumes:\n  data:\n"},
    ]


def hub_search(query: str, limit: int = 25) -> list[dict]:
    """Docker Hub durchsuchen (braucht Internet auf dem RMM-Server)."""
    url = "https://hub.docker.com/v2/search/repositories/?" + urllib.parse.urlencode({"query": query, "page_size": limit})
    req = urllib.request.Request(url, headers={"User-Agent": "RAPALLE.net-RMM"})
    with urllib.request.urlopen(req, timeout=15, context=ssl.create_default_context()) as r:
        data = json.loads(r.read())
    out = []
    for it in data.get("results") or []:
        name = it.get("repo_name") or ""
        out.append({"image": name, "description": (it.get("short_description") or "")[:200],
                    "stars": it.get("star_count") or 0, "official": bool(it.get("is_official")),
                    "pulls": it.get("pull_count") or 0})
    return out


def image_template(image: str, port: int | None) -> str:
    svc = image.rsplit("/", 1)[-1].split(":")[0].replace(".", "-")[:40] or "app"
    lines = ["services:", f"  {svc}:", f"    image: {image}", "    restart: unless-stopped"]
    if port:
        lines += ["    ports:", f'      - "{port}:{port}"']
    return "\n".join(lines) + "\n"

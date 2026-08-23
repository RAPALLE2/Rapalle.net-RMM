# ─────────────────────────────────────────────────────────────────────────────
# RAPALLE.net RMM - Container-Image
#
# Enthält: Python 3.12 + alle Abhängigkeiten aus backend/requirements.txt.
# Der eigentliche Code (backend/ frontend/ agent/) wird per Volume aus dem
# Projektordner eingehängt (siehe docker-compose.yml). Das ist Absicht:
#   - der Source-Editor (Settings -> Source) kann Dateien wirklich speichern,
#   - das Selbst-Update (Settings -> Update) kann den Code austauschen,
#   - Datenbank, Aufzeichnungen und .env überleben jedes Image-Update.
#
# Bauen & starten:   docker compose up -d --build
# Logs ansehen:      docker compose logs -f
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# --- Grundeinstellungen ------------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    TZ=Europe/Berlin

# Der Container muss auch unter einer FREMDEN UID laufen koennen - etwa bei
# "user:" in docker-compose.yml, bei rootless Docker oder in einem
# unprivilegierten LXC, wo Bind-Mounts UID-verschoben ankommen. Dafuer:
#   HOME=/tmp        beschreibbar fuer jede UID (pip/Cache brauchen ein HOME)
#   PYTHONPATH       findet das Paket "app" unabhaengig vom Arbeitsverzeichnis
#   *_CACHE_DIR      keine Schreibversuche in nicht beschreibbare Ordner
ENV HOME=/tmp \
    PYTHONPATH=/app/backend \
    PIP_CACHE_DIR=/tmp/.cache/pip \
    XDG_CACHE_HOME=/tmp/.cache

# Kennzeichnet die Installationsart. Wird von backend/app/runtime_env.py
# gelesen und in der Oberfläche als "Als Docker-Container installiert" angezeigt.
ENV RMM_INSTALL_KIND=docker

# Im Container MUSS an alle Interfaces gebunden werden - sonst ist das
# Dashboard von außerhalb des Containers nicht erreichbar.
ENV HOST=0.0.0.0 \
    PORT=4000

# --- System-Pakete -----------------------------------------------------------
# curl        -> Healthcheck
# tzdata      -> korrekte lokale Zeit in Logs/Audit
# ca-certs    -> HTTPS (GitHub-Update, Webhooks)
# iputils/net -> Netzwerk-Scanner & Speedtest brauchen ping/traceroute
# build-essential + libffi/libssl -> Fallback, falls für bcrypt/cryptography
#                                   kein passendes Wheel existiert
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        tzdata \
        iputils-ping \
        iproute2 \
        net-tools \
        build-essential \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python-Abhängigkeiten ---------------------------------------------------
# Zuerst NUR requirements.txt kopieren: solange sich diese Datei nicht ändert,
# nutzt Docker den Cache und ein Rebuild dauert Sekunden statt Minuten.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r /app/backend/requirements.txt && \
    # Pakete fuer JEDE UID lesbar machen. pip legt je nach umask 0700-Rechte
    # an; laeuft der Container dann unter einer anderen UID, scheitert schon
    # der Import ("ModuleNotFoundError") - obwohl alles installiert ist.
    chmod -R a+rX "$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" /usr/local/bin

# --- Projektcode -------------------------------------------------------------
# Wird beim Start i.d.R. vom Volume überlagert; im Image liegt er trotzdem,
# damit das Image auch ohne Bind-Mount allein lauffähig ist.
COPY backend /app/backend
COPY frontend /app/frontend
COPY agent /app/agent

# Marker-Datei: zweites, unabhängiges Signal für die Docker-Erkennung
# (falls jemand die Umgebungsvariable überschreibt).
RUN echo "docker" > /app/.docker-install \
    # Projektdateien ebenfalls fuer jede UID les- und betretbar machen.
    && chmod -R a+rX /app \
    # Diese Ordner beschreibt das Backend im Betrieb (Datenbank, Aufzeichnungen,
    # Uploads, Branding). Wird kein Volume eingehaengt, muessen sie auch fuer
    # eine fremde UID beschreibbar sein.
    && mkdir -p /app/backend/recordings /app/backend/media_files /app/backend/branding \
    && chmod -R a+rwX /app/backend/recordings /app/backend/media_files \
                      /app/backend/branding /tmp

# Start-Skript: schreibt einen Umgebungsbericht ins Log, prueft die
# Voraussetzungen und startet erst dann run.py. Damit steht bei einem
# Fehlstart IMMER etwas in "docker compose logs".
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

WORKDIR /app/backend

EXPOSE 4000

# Healthcheck: fragt die Login-Seite ab. Container gilt als "unhealthy",
# wenn das Backend nicht mehr antwortet.
# Gesundheitspruefung.
#
# Zwei Aenderungen gegenueber frueher, beide aus einem echten Vorfall:
#   1. Geprueft wird /api/health statt "/". "/" liefert die komplette
#      Dashboard-Seite aus - also Dateizugriffe, die unter Last laenger
#      dauern koennen. Der Endpunkt /api/health fasst weder Datenbank noch
#      Dateien an und antwortet auch dann, wenn der Server gerade viel zu
#      tun hat.
#   2. Zeitlimit 5s -> 15s und 3 -> 5 Versuche. Fuenf Sekunden sind zu
#      knapp: Beim Massen-Update aller Agenten war der Server kurz
#      beschaeftigt, der Healthcheck schlug an, und der Container galt als
#      krank - obwohl das Backend voellig in Ordnung war. Eine
#      Gesundheitspruefung, die bei Last Fehlalarm gibt, richtet mehr
#      Schaden an als gar keine.
HEALTHCHECK --interval=30s --timeout=15s --start-period=90s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null || exit 1

# Absturzberichte von Python (auch bei harten Fehlern) ins Log.
ENV PYTHONFAULTHANDLER=1

# Das Start-Skript uebernimmt: Diagnose -> Pruefungen -> run.py.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

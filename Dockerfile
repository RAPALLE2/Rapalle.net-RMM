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
    pip install -r /app/backend/requirements.txt

# --- Projektcode -------------------------------------------------------------
# Wird beim Start i.d.R. vom Volume überlagert; im Image liegt er trotzdem,
# damit das Image auch ohne Bind-Mount allein lauffähig ist.
COPY backend /app/backend
COPY frontend /app/frontend
COPY agent /app/agent

# Marker-Datei: zweites, unabhängiges Signal für die Docker-Erkennung
# (falls jemand die Umgebungsvariable überschreibt).
RUN echo "docker" > /app/.docker-install

WORKDIR /app/backend

EXPOSE 4000

# Healthcheck: fragt die Login-Seite ab. Container gilt als "unhealthy",
# wenn das Backend nicht mehr antwortet.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

# run.py kümmert sich selbst um fehlende Pakete und startet uvicorn
# (inklusive Auto-Neustart nach Absturz).
CMD ["python", "run.py"]

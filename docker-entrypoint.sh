#!/bin/sh
# ============================================================================
# docker-entrypoint.sh
# ----------------------------------------------------------------------------
# Startet das Backend im Container - und sorgt vor allem dafuer, dass IMMER
# etwas in "docker compose logs" landet.
#
# Warum es das gibt: Startet der Container nicht, sah man bisher oft gar
# nichts. Ursachen dafuer sind typischerweise
#   * ein Fehler VOR der ersten Ausgabe von Python (fehlende Rechte, falscher
#     Pfad, kaputtes Volume),
#   * gepufferte Ausgaben, die beim Absturz verloren gehen,
#   * ein schneller Neustart-Kreislauf ("restart: unless-stopped"), der die
#     Logzeilen sofort ueberrollt.
#
# Dieses Skript schreibt deshalb ZUERST einen Umgebungsbericht (unter welcher
# UID laeuft es, welche Pfade sind lesbar/beschreibbar, ist Python da), prueft
# dann Schritt fuer Schritt die Startvoraussetzungen und startet erst danach
# run.py. Geht etwas schief, bleibt die Meldung stehen und der Neustart wird
# absichtlich verzoegert, damit man sie ueberhaupt lesen kann.
#
# Nuetzliche Schalter (in docker-compose.yml unter "environment"):
#   RMM_KEEP_ALIVE=1   Container nach einem Fehler NICHT beenden, sondern
#                      offen halten - dann ist "docker exec -it ... sh" moeglich.
#   RMM_SKIP_CHECKS=1  Vorabpruefungen ueberspringen und direkt starten.
# ============================================================================

# --- Ausgabe: stdout UND eine Logdatei ------------------------------------
# Manche Oberflaechen (z.B. Synology Container Manager) zeigen im Reiter
# "Protokoll" nichts an. Damit man trotzdem nachlesen kann, was beim Start
# passiert ist, spiegeln wir alles in eine Datei im Projektordner - erreichbar
# ueber die Dateifreigabe oder spaeter im Dashboard unter Settings -> Source.
#
# Umgesetzt durch einen einmaligen Neustart des Skripts durch "tee". Die
# Variable RMM_LOG_WRAPPED verhindert eine Endlosschleife.
if [ "${RMM_LOG_WRAPPED:-0}" != "1" ]; then
    RMM_LOG="${RMM_LOG:-${RMM_APP_DIR:-/app/backend}/startup.log}"
    # Beschreibbar? Sonst nach /tmp ausweichen, sonst ganz ohne Datei.
    if ! ( touch "$RMM_LOG" ) 2>/dev/null; then
        RMM_LOG=/tmp/rmm-startup.log
        touch "$RMM_LOG" 2>/dev/null || RMM_LOG=""
    fi
    if [ -n "$RMM_LOG" ] && command -v tee >/dev/null 2>&1; then
        RMM_LOG_WRAPPED=1
        export RMM_LOG_WRAPPED RMM_LOG
        # Bei jedem Start eine Trennlinie, aber die Historie behalten (-a).
        {
            echo ""
            echo "=========== Neuer Start: $(date 2>/dev/null) ==========="
        } >> "$RMM_LOG" 2>/dev/null
        exec "$0" "$@" 2>&1 | tee -a "$RMM_LOG"
    fi
fi

# Alles auf die Standardausgabe - Docker sammelt nur stdout/stderr ein.
exec 2>&1

RED=''; YEL=''; NC=''
if [ -t 1 ]; then RED='\033[31m'; YEL='\033[33m'; NC='\033[0m'; fi

say()  { echo "[start] $*"; }
warn() { printf "[start] ${YEL}WARNUNG${NC} %s\n" "$*"; }
err()  { printf "[start] ${RED}FEHLER${NC}  %s\n" "$*"; }

# --- Bei einem Fehler nicht wortlos verschwinden ---------------------------
fail() {
    err "$1"
    echo "[start] ------------------------------------------------------------"
    echo "[start] Der Start wurde abgebrochen. Die Zeilen daruber nennen den Grund."
    echo "[start] Hilft das nicht weiter, im Container nachsehen mit:"
    echo "[start]     docker compose exec rmm sh      (laeuft er noch)"
    echo "[start]     docker compose run --rm rmm sh  (wenn nicht)"
    echo "[start] ------------------------------------------------------------"
    if [ "${RMM_KEEP_ALIVE:-0}" = "1" ]; then
        echo "[start] RMM_KEEP_ALIVE=1 -> Container bleibt zum Nachsehen offen."
        while true; do sleep 3600; done
    fi
    # Verzoegern, damit die Meldung im Neustart-Kreislauf lesbar bleibt.
    echo "[start] Beende in 15 Sekunden (verzoegert, damit dieses Log lesbar bleibt)."
    sleep 15
    exit 1
}

echo "[start] ============================================================"
echo "[start] RAPALLE.net RMM - Container startet"
echo "[start] Zeit:        $(date 2>/dev/null || echo unbekannt)"
echo "[start] Benutzer:    uid=$(id -u 2>/dev/null) gid=$(id -g 2>/dev/null) ($(id -un 2>/dev/null || echo '?'))"
echo "[start] Verzeichnis: $(pwd)"
echo "[start] HOME=${HOME:-<leer>}  PYTHONPATH=${PYTHONPATH:-<leer>}"
echo "[start] HOST=${HOST:-<leer>}  PORT=${PORT:-<leer>}"
echo "[start] ============================================================"

if [ "${RMM_SKIP_CHECKS:-0}" = "1" ]; then
    say "RMM_SKIP_CHECKS=1 - Vorabpruefungen uebersprungen."
else

    # --- 1. Python vorhanden? ---------------------------------------------
    if ! command -v python >/dev/null 2>&1; then
        fail "Python ist im Container nicht auffindbar (command -v python schlug fehl)."
    fi
    say "Python:      $(python -V 2>&1) -> $(command -v python)"

    # --- 2. Liegt der Code da, wo er hingehoert? ---------------------------
    # Ueberschreibbar - hilft beim Nachstellen ausserhalb des Containers.
    APP_DIR=${RMM_APP_DIR:-/app/backend}
    if [ ! -d "$APP_DIR" ]; then
        fail "$APP_DIR existiert nicht. Sehr wahrscheinlich zeigt ein Volume in
       docker-compose.yml auf einen falschen oder leeren Ordner."
    fi
    if [ ! -f "$APP_DIR/run.py" ]; then
        err "$APP_DIR/run.py fehlt. Inhalt von $APP_DIR:"
        ls -la "$APP_DIR" 2>&1 | head -30
        fail "Ohne run.py kann das Backend nicht starten (falsches Volume?)."
    fi
    if [ ! -r "$APP_DIR/run.py" ]; then
        err "$APP_DIR/run.py ist NICHT LESBAR fuer uid=$(id -u)."
        ls -la "$APP_DIR/run.py" 2>&1
        fail "Dateirechte. Auf dem HOST ausfuehren:  chmod -R a+rX backend frontend agent
       (unprivilegiertes LXC verschiebt die UIDs der Bind-Mounts - die Dateien
        muessen dann fuer 'andere' les- und betretbar sein)."
    fi
    say "Code:        $APP_DIR/run.py gefunden und lesbar"

    # --- 3. Schreibrechte fuer die Daten ----------------------------------
    for d in "$APP_DIR" "$APP_DIR/recordings" "$APP_DIR/media_files"; do
        [ -d "$d" ] || continue
        if [ -w "$d" ]; then
            say "Schreibbar:  $d"
        else
            warn "$d ist NICHT beschreibbar (uid=$(id -u)) - Datenbank, Uploads
       oder Aufzeichnungen koennen dort spaeter fehlschlagen.
       Abhilfe auf dem Host:  chmod -R a+rwX backend/recordings backend/media_files"
        fi
    done

    # --- 4. Eigenes Paket importierbar? ------------------------------------
    # Genau hier scheiterte es bisher lautlos: fehlende Leserechte auf
    # backend/app oder ein falscher Suchpfad.
    if ! python -c "import sys; sys.path.insert(0, '$APP_DIR'); import app" 2>/tmp/imp.err; then
        err "Das eigene Paket 'app' laesst sich nicht importieren:"
        sed 's/^/[start]   /' /tmp/imp.err
        ls -la "$APP_DIR/app" 2>&1 | head -15
        fail "Meist Dateirechte (chmod -R a+rX) oder ein Volume, das
       backend/app ueberdeckt."
    fi
    say "Import:      Paket 'app' ist importierbar"

    # --- 5. Kernbibliotheken ----------------------------------------------
    # Fehlen sie, versucht run.py sie selbst nachzuinstallieren - hier wird nur
    # frueh gemeldet, was Sache ist.
    MISSING=$(python - <<'PYEOF' 2>/dev/null
import importlib
miss = []
for m in ("fastapi", "uvicorn", "socketio", "dotenv", "jwt", "bcrypt",
          "pydantic", "psutil", "cryptography", "multipart"):
    try:
        importlib.import_module(m)
    except Exception:
        miss.append(m)
print(" ".join(miss))
PYEOF
)
    if [ -n "$MISSING" ]; then
        warn "Fehlende Bibliotheken: $MISSING"
        warn "run.py versucht gleich, sie nachzuinstallieren. Schlaegt das fehl,
       stehen die Gruende unter [bootstrap] im Log."
    else
        say "Bibliotheken: alle Kernpakete vorhanden"
    fi
fi

echo "[start] ------------------------------------------------------------"
say "Starte Backend (Weg 1 von 3):  python -u run.py"
echo "[start] ------------------------------------------------------------"

APP_DIR="${RMM_APP_DIR:-/app/backend}"
cd "$APP_DIR" || fail "Wechsel nach $APP_DIR nicht moeglich."

# ============================================================================
# Startversuche - wenn ein Weg scheitert, wird der naechste probiert.
# Ziel: Der Port antwortet AUF JEDEN FALL, notfalls mit einer Fehlerseite.
# Nichts ist schlimmer als ein Container, der laeuft und einfach schweigt.
# ============================================================================

# -u = ungepuffert. Ohne das gehen die letzten Zeilen bei einem Absturz
# verloren - genau die, die man braucht.
#
# Weg 1 wird WIEDERHOLT versucht. Grund: Der Loop-Waechter im Backend
# beendet den Prozess absichtlich mit Code 75 (EX_TEMPFAIL), wenn die
# Ereignisschleife haengt. Das ist kein Startfehler, sondern eine
# Selbstheilung - dafuer in die Rueckfallebenen zu fallen waere falsch.
# Auch ein normaler Absturz bekommt hier ein paar Anlaeufe, bevor die
# schwaecheren Wege ueberhaupt in Betracht kommen.
RESTARTS=0
MAX_QUICK_RESTARTS="${RMM_MAX_QUICK_RESTARTS:-20}"
while : ; do
    STARTED=$(date +%s)
    python -u run.py
    rc=$?
    RAN=$(( $(date +%s) - STARTED ))

    if [ "$rc" = "0" ]; then
        say "Weg 1 (run.py) reguleaer beendet."
        break
    fi

    # Lief das Backend laenger als zwei Minuten, war es ein Betriebsfehler
    # und kein Startproblem - dann zaehlt der Versuch nicht als Fehlstart.
    if [ "$RAN" -gt 120 ]; then
        RESTARTS=0
    else
        RESTARTS=$((RESTARTS + 1))
    fi

    if [ "$rc" = "75" ]; then
        say "Der Waechter hat ein haengendes Backend beendet (Code 75)."
        say "Sofortiger Neustart - das ist die vorgesehene Selbstheilung."
        sleep 2
        continue
    fi

    err "Weg 1 (run.py) endete mit Exit-Code $rc (nach ${RAN}s, Fehlstart $RESTARTS/$MAX_QUICK_RESTARTS)."
    if [ "$RESTARTS" -ge "$MAX_QUICK_RESTARTS" ]; then
        err "Zu viele schnelle Fehlstarts - die anderen Wege werden probiert."
        break
    fi
    WAIT=$(( RESTARTS < 5 ? 3 : 15 ))
    say "Neuer Versuch in ${WAIT}s."
    sleep "$WAIT"
done

# --- Weg 2: uvicorn direkt ---------------------------------------------------
# Sinnvoll, wenn run.py selbst stolpert (z.B. beim Nachinstallieren von
# Paketen oder in der Absturz-Neustart-Schleife), die App aber importierbar
# waere. Bindet bewusst an 0.0.0.0 - im Container ist alles andere sinnlos.
echo "[start] ------------------------------------------------------------"
say "Versuche Weg 2 von 3:  uvicorn direkt"
echo "[start] ------------------------------------------------------------"
python -u -m uvicorn app.main:socket_app \
    --host "${HOST:-0.0.0.0}" --port "${PORT:-4000}"
rc2=$?
err "Weg 2 (uvicorn) endete mit Exit-Code $rc2."

# --- Weg 3: Diagnose-Server (ZEITLICH BEGRENZT) ------------------------------
# Letzte Rueckfallebene: ein winziger HTTP-Server, der auf demselben Port das
# Startprotokoll ausliefert. Damit sieht man die Ursache direkt im Browser,
# statt nur "Could not connect to server" zu bekommen.
#
# WICHTIG - hier lag eine Falle, die genau das Gegenteil bewirkte:
# Frueher lief dieser Server mit serve_forever(), also ohne Ende. Der
# Container blieb damit dauerhaft "oben" und lieferte 503 aus. Die
# Neustart-Regel von Docker (restart: unless-stopped) reagiert aber NUR auf
# ein Beenden des Containers, nicht auf einen fehlgeschlagenen Healthcheck.
# Ergebnis: Ein einmal gestrandeter Container kam NIE von allein zurueck -
# genau das Verhalten "das Backend startet sich nicht selbst neu".
#
# Deshalb laeuft der Diagnose-Server jetzt nur noch eine begrenzte Zeit.
# Danach endet er, das Skript endet, der Container endet - und Docker
# startet ihn neu. Ein voruebergehender Fehler heilt sich damit von selbst,
# und man hat trotzdem ein paar Minuten Zeit, die Seite anzusehen.
echo "[start] ------------------------------------------------------------"
say "Versuche Weg 3 von 3:  Diagnose-Server auf Port ${PORT:-4000}"
say "Das Backend laeuft NICHT - die Seite zeigt das Startprotokoll."
say "Dieser Server endet nach ${RMM_DIAG_SECONDS:-180}s, damit der Container"
say "neu startet. Mit RMM_KEEP_ALIVE=1 bleibt er zum Nachsehen offen."
echo "[start] ------------------------------------------------------------"

RMM_DIAG_LOG="${RMM_LOG:-$APP_DIR/startup.log}" \
RMM_DIAG_PORT="${PORT:-4000}" \
python -u - <<'PYEOF'
"""
Notfall-Diagnoseserver.

Er tut genau eines: auf dem normalen Port lauschen und das Startprotokoll
anzeigen. So sieht man im Browser sofort, warum das Backend nicht laeuft.
Bewusst nur Standardbibliothek - hier darf nichts mehr fehlschlagen koennen.
"""
import html
import os
import platform
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = os.environ.get("RMM_DIAG_LOG", "")
PORT = int(os.environ.get("RMM_DIAG_PORT", "4000"))


def read_log(limit=200_000):
    try:
        with open(LOG, "r", errors="replace") as fh:
            data = fh.read()
        return data[-limit:]
    except Exception as e:
        return f"(Startprotokoll nicht lesbar: {e})"


def facts():
    out = []
    try:
        out.append(f"Benutzer: uid={os.getuid()} gid={os.getgid()}")
    except Exception:
        pass
    out.append(f"Python:   {sys.version.split()[0]} ({sys.executable})")
    out.append(f"System:   {platform.platform()}")
    out.append(f"Host:     {socket.gethostname()}")
    out.append(f"Ordner:   {os.getcwd()}")
    out.append(f"Log:      {LOG}")
    return "\n".join(out)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<title>RMM - Backend startet nicht</title>
<style>
 body {{ font-family: system-ui, sans-serif; background:#0d1520; color:#e6edf7;
        margin:0; padding:26px; line-height:1.5 }}
 h1 {{ font-size:19px; margin:0 0 4px }}
 .sub {{ color:#8ea2c6; font-size:13px; margin-bottom:18px }}
 pre {{ background:#111c2b; border:1px solid #24354d; border-radius:8px;
        padding:12px; overflow:auto; font-size:12px; max-height:60vh }}
 .box {{ background:#1b2740; border-left:3px solid #f5a524; padding:10px 12px;
         border-radius:6px; margin-bottom:16px; font-size:13px }}
</style></head><body>
<h1>Das RMM-Backend laeuft nicht</h1>
<div class="sub">Dieser Notfall-Server antwortet nur, damit die Ursache sichtbar ist.</div>
<div class="box">Beide Startversuche sind fehlgeschlagen (run.py und uvicorn direkt).
Der Grund steht im Protokoll unten - meist fehlende Pakete oder Dateirechte.</div>
<h2 style="font-size:14px">Umgebung</h2>
<pre>{html.escape(facts())}</pre>
<h2 style="font-size:14px">Startprotokoll</h2>
<pre>{html.escape(read_log())}</pre>
</body></html>"""
        data = body.encode("utf-8")
        # 503: ehrlich - der Dienst ist nicht verfuegbar. Der Healthcheck
        # schlaegt damit weiterhin an, aber der Browser zeigt die Ursache.
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass          # Zugriffe nicht ins Log spammen


# Wie lange die Fehlerseite erreichbar bleibt, bevor der Container neu
# startet. 0 bedeutet "ohne Ende" - nur fuer die Fehlersuche gedacht.
try:
    LIFETIME = int(os.environ.get("RMM_DIAG_SECONDS", "180") or 180)
except ValueError:
    LIFETIME = 180

try:
    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[diag] Diagnose-Server laeuft auf Port {PORT} - Seite im Browser oeffnen.")
    if LIFETIME > 0:
        # Ein Wecker-Thread beendet den Server. shutdown() muss aus einem
        # ANDEREN Thread kommen - aus serve_forever() heraus ginge es nicht.
        import threading

        def _stop():
            time.sleep(LIFETIME)
            print(f"[diag] {LIFETIME}s um - Container wird beendet, damit "
                  f"Docker ihn neu startet.")
            srv.shutdown()

        threading.Thread(target=_stop, daemon=True).start()
    srv.serve_forever()
    print("[diag] Diagnose-Server beendet.")
except Exception as e:
    print(f"[diag] Diagnose-Server konnte nicht starten: {e}")
    raise SystemExit(1)
PYEOF

rc3=$?
err "Auch der Diagnose-Server endete (Exit-Code $rc3)."

if [ "${RMM_KEEP_ALIVE:-0}" = "1" ]; then
    echo "[start] RMM_KEEP_ALIVE=1 -> Container bleibt zum Nachsehen offen."
    echo "[start] ACHTUNG: In dieser Betriebsart startet der Container NICHT"
    echo "[start] von allein neu. Nur zur Fehlersuche verwenden."
    while true; do sleep 3600; done
fi
# Beenden mit einem Fehlercode. Das ist der Punkt, an dem Docker die
# Neustart-Regel anwendet - der Container kommt also von selbst zurueck.
echo "[start] Container wird beendet (Exit-Code $rc)."
echo "[start] Docker startet ihn wegen 'restart: unless-stopped' neu."
echo "[start] Kurze Pause, damit dieses Protokoll lesbar bleibt."
sleep 10
exit $rc

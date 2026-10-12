"""
hostlock.py
-----------
Zugriff auf die konfigurierten Adressen beschränken.

Es wird der Host-Header jeder Anfrage gegen die Liste erlaubter Adressen
geprüft. Steht in den Einstellungen nur eine Domain, kommt ein Aufruf über
die lokale IP nicht mehr durch.

WAS DAS LEISTET - UND WAS NICHT
    Es verhindert versehentliche Zugriffe über den "falschen" Weg,
    DNS-Rebinding und das Auffinden der Oberfläche durch Scanner, die
    einfach IP-Bereiche abklappern.

    Es ist KEINE echte Zugriffskontrolle. Der Host-Header wird vom Client
    geschickt und lässt sich frei setzen - wer den Port erreicht, kommt mit
    einem gefälschten Header weiterhin durch. Wirklich absperren lässt sich
    der Dienst nur per Firewall oder indem er nur auf der gewünschten
    Adresse lauscht. Diese Sperre ist eine Hürde, kein Riegel.

AUSSPERR-SCHUTZ
    Loopback (localhost, 127.0.0.1, ::1) ist IMMER erlaubt. Sonst könnte
    man sich mit einem Tippfehler dauerhaft aus der eigenen Oberfläche
    aussperren - inklusive der Einstellung, mit der man es zurückdrehen
    würde. Vom Server selbst kommt man also immer wieder herein.
"""

import ipaddress
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

from app import db

# Diese Namen kommen immer durch - siehe Aussperr-Schutz oben.
ALWAYS_ALLOWED = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}

# Pfade, die auch bei aktiver Sperre erreichbar bleiben. Ohne die wäre ein
# vorgelagerter Loadbalancer nicht mehr in der Lage, den Dienst als "gesund"
# zu erkennen, und würde ihn aus dem Verkehr nehmen.
EXEMPT_PATHS = ("/api/health", "/health")


def _clean_host(value: str) -> str:
    """
    Aus einem Eintrag den reinen Hostnamen ziehen. Die Einstellungen dürfen
    alles enthalten, was ein Mensch dort hineinschreibt: 'https://rmm.foo.de',
    'rmm.foo.de:4000' oder nur 'rmm.foo.de'.
    """
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" in v:
        v = urlparse(v).netloc or v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    # IPv6 in Klammern behalten, sonst zerlegt der Port-Split die Adresse.
    if v.startswith("["):
        return v.split("]", 1)[0] + "]" if "]" in v else v
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def allowed_hosts() -> set[str]:
    """Alle aktuell erlaubten Adressen aus den Einstellungen."""
    hosts = set(ALWAYS_ALLOWED)
    for key in ("server_url", "server_domain", "server_host"):
        h = _clean_host(db.get_setting(key, "") or "")
        if h:
            hosts.add(h)
    for extra in (db.get_setting("host_lock_extra", "") or "").split(","):
        h = _clean_host(extra)
        if h:
            hosts.add(h)
    return hosts


def is_enabled() -> bool:
    return (db.get_setting("host_lock_enabled", "0") or "0") == "1"


def _request_host(request) -> str:
    """
    Den angefragten Host bestimmen. Hinter einem Reverse Proxy steht der
    echte Name in X-Forwarded-Host - diesem Header wird aber nur vertraut,
    wenn das ausdrücklich eingeschaltet ist. Sonst könnte ihn jeder mit-
    schicken und die Sperre damit aushebeln.
    """
    if (db.get_setting("host_lock_trust_proxy", "0") or "0") == "1":
        fwd = request.headers.get("x-forwarded-host")
        if fwd:
            return _clean_host(fwd.split(",")[0])
    return _clean_host(request.headers.get("host", ""))


def check(request) -> tuple[bool, str]:
    """(erlaubt?, angefragter Host) - auch von den Routen nutzbar."""
    host = _request_host(request)
    if not is_enabled():
        return True, host
    if request.url.path in EXEMPT_PATHS:
        return True, host
    # Reichweite: 'ui' schützt nur die Oberfläche und die API, lässt die
    # Agenten aber in Ruhe. Das ist wichtig, weil Agenten mit der Adresse
    # verbunden sind, mit der sie eingerichtet wurden - meist der IP. Sperrt
    # man auf 'nur Domain' und wählt 'all', verliert man in dem Moment die
    # gesamte Flotte, bis jeder Agent neu eingerichtet ist.
    if (db.get_setting("host_lock_scope", "ui") or "ui") == "ui" \
            and request.url.path.startswith("/socket.io"):
        return True, host
    if not host:
        # Anfragen ohne Host-Header sind HTTP/1.0 oder Bots. Bei aktiver
        # Sperre gibt es keinen Grund, sie durchzulassen.
        return False, host
    allowed = allowed_hosts()
    if host in allowed:
        return True, host
    # Loopback zusätzlich numerisch prüfen (z.B. 127.0.0.5).
    try:
        if ipaddress.ip_address(host.strip("[]")).is_loopback:
            return True, host
    except ValueError:
        pass
    return False, host


class HostLockMiddleware(BaseHTTPMiddleware):
    """
    Prüft jede Anfrage. Die Einstellungen werden bei JEDER Anfrage frisch
    gelesen, damit eine Änderung sofort greift und nicht erst nach einem
    Neustart - gerade beim Zurückdrehen einer zu strengen Einstellung ist
    das der Unterschied zwischen "kurz falsch" und "ausgesperrt".
    """

    async def dispatch(self, request, call_next):
        try:
            ok, host = check(request)
        except Exception as e:
            # Früher wurde hier still durchgelassen. Das ist die schlechteste
            # aller Varianten: die Sperre sieht eingeschaltet aus, wirkt aber
            # nicht, und niemand erfährt davon. Jetzt wird der Fehler laut
            # protokolliert. Durchgelassen wird nur, solange die Sperre AUS
            # ist - ist sie an, wird im Zweifel blockiert.
            print(f"[hostlock] Prüfung fehlgeschlagen: {e!r}")
            try:
                enabled = is_enabled()
            except Exception:
                enabled = False
            if not enabled:
                return await call_next(request)
            return PlainTextResponse(
                "Host-Prüfung derzeit nicht möglich - Zugriff vorsorglich "
                "abgelehnt. Details im Server-Log.", status_code=503)

        if ok:
            return await call_next(request)

        return PlainTextResponse(
            "Zugriff über diese Adresse ist nicht erlaubt.\n\n"
            f"Angefragt: {host or '(kein Host-Header)'}\n"
            "Bitte die in den Einstellungen hinterlegte Adresse verwenden.\n\n"
            "Ausgesperrt? Vom Server selbst ist die Oberfläche immer über\n"
            "http://localhost erreichbar; dort lässt sich die Sperre unter\n"
            "Einstellungen > Allgemein wieder abschalten.",
            status_code=403)


def diagnostics(request) -> dict:
    """
    Was sieht der Server tatsächlich? Ohne diese Auskunft ist eine nicht
    greifende Sperre kaum zu debuggen: man weiß weder, welchen Host-Header
    der Browser schickt, noch welche Adressen gerade erlaubt sind.
    """
    ok, host = check(request)
    return {
        "enabled": is_enabled(),
        "scope": db.get_setting("host_lock_scope", "ui"),
        "trust_proxy": db.get_setting("host_lock_trust_proxy", "0") == "1",
        "raw_host_header": request.headers.get("host", ""),
        "x_forwarded_host": request.headers.get("x-forwarded-host", ""),
        "seen_as": host,
        "allowed": sorted(allowed_hosts()),
        "would_pass": ok,
        "sources": {
            "server_url": db.get_setting("server_url", "") or "",
            "server_domain": db.get_setting("server_domain", "") or "",
            "server_host": db.get_setting("server_host", "") or "",
            "host_lock_extra": db.get_setting("host_lock_extra", "") or "",
        },
    }

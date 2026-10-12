"""
security_policy.py
------------------
Zwei Schutzmechanismen, die der IT-Grundschutz des BSI ausdrücklich verlangt
und die bisher fehlten:

  1. Passwort-Richtlinie  (ORP.4.A8 "Regelung des Passwortgebrauchs",
     ORP.4.A22 "Regelung zur Passwortqualität")
     Mindestlänge und Zeichenklassen werden SERVERSEITIG geprüft. Eine reine
     Prüfung im Browser wäre wirkungslos, weil die API auch direkt ansprechbar
     ist.

  2. Schutz gegen Erraten von Passwörtern (ORP.4.A9 "Identifikation und
     Authentisierung", elementare Gefährdung G 0.34)
     Nach mehreren Fehlversuchen wird das Konto für eine Weile gesperrt.
     Gezählt wird pro Benutzername UND pro Quell-IP, damit weder ein einzelnes
     Konto durchprobiert noch von einer Stelle aus breit gestreut werden kann.

Beides ist über die Einstellungen anpassbar - der Grundschutz schreibt keine
festen Zahlen vor, sondern eine bewusste, dokumentierte Festlegung. Die
Standardwerte orientieren sich an den aktuellen BSI-Empfehlungen: lieber lang
als kompliziert, kein erzwungener regelmäßiger Wechsel.

WICHTIG: Diese Datei macht die Software nicht "BSI-konform" - konform ist
immer eine Organisation mit ihrem Informationsverbund, nicht ein Programm.
Sie sorgt dafür, dass die technischen Anforderungen erfüllbar sind.
Siehe docs/BSI-IT-Grundschutz.md.
"""

from __future__ import annotations

import re
import time
import unicodedata

from app import db

# --- Standardwerte --------------------------------------------------------
# Bewusst dokumentiert, damit im Sicherheitskonzept nachlesbar ist, was gilt.
DEFAULTS = {
    "pw_min_length": 12,        # BSI: Länge wirkt stärker als Sonderzeichen
    "pw_require_classes": 3,    # aus: Kleinbuchstaben, Großbuchstaben, Ziffern, Sonderzeichen
    "pw_block_username": 1,     # Benutzername darf nicht im Passwort stecken
    "login_max_attempts": 5,    # danach Sperre
    "login_lock_minutes": 15,   # Dauer der Sperre
    "login_window_minutes": 15, # Zeitfenster, in dem Fehlversuche zählen
}

# Sehr häufige Passwörter, die trotz erfüllter Regeln nicht taugen.
# Bewusst kurz gehalten: eine echte Sperrliste gehört in die Betriebsdoku.
_TRIVIAL = {
    "passwort", "password", "administrator", "admin", "willkommen",
    "welcome", "qwertz", "qwerty", "123456", "12345678", "letmein",
    "changeme", "geheim", "rapalle", "rmm",
}


def setting(key: str) -> int:
    """Wert aus den Einstellungen, sonst der dokumentierte Standard."""
    try:
        raw = db.get_setting(key)
        if raw is not None and str(raw).strip() != "":
            return int(raw)
    except Exception:
        pass
    return int(DEFAULTS[key])


def policy() -> dict:
    """Die geltende Richtlinie - auch für die Anzeige im Dashboard."""
    return {k: setting(k) for k in DEFAULTS}


# ==========================================================================
# 1. Passwort-Richtlinie
# ==========================================================================

def check_password(password: str, username: str = "") -> list[str]:
    """
    Prüft ein Passwort. Rückgabe: Liste der Verstöße (leer = in Ordnung).

    Absichtlich werden ALLE Verstöße gesammelt statt beim ersten abzubrechen -
    sonst arbeitet man sich in mehreren Anläufen durch die Regeln.
    """
    problems: list[str] = []
    pw = password or ""

    min_len = setting("pw_min_length")
    if len(pw) < min_len:
        problems.append(f"mindestens {min_len} Zeichen (aktuell {len(pw)})")

    classes = 0
    if re.search(r"[a-zäöüß]", pw):
        classes += 1
    if re.search(r"[A-ZÄÖÜ]", pw):
        classes += 1
    if re.search(r"\d", pw):
        classes += 1
    # Alles, was weder Buchstabe noch Ziffer ist, zählt als Sonderzeichen.
    if re.search(r"[^\wÄÖÜäöüß]|_", pw):
        classes += 1

    need = setting("pw_require_classes")
    if classes < need:
        problems.append(
            f"mindestens {need} verschiedene Zeichenarten "
            f"(Klein-, Großbuchstaben, Ziffern, Sonderzeichen) - aktuell {classes}")

    # Vergleich ohne Akzente/Groß-Klein, damit "Pa$$wort" auch auffällt.
    flat = unicodedata.normalize("NFKD", pw).lower()
    flat_simple = (flat.replace("$", "s").replace("0", "o").replace("1", "i")
                       .replace("3", "e").replace("@", "a").replace("!", "i"))
    for bad in _TRIVIAL:
        if bad in flat_simple:
            problems.append(f"enthält ein sehr gebräuchliches Wort ({bad})")
            break

    if setting("pw_block_username") and username:
        u = username.strip().lower()
        if len(u) >= 3 and u in flat:
            problems.append("enthält den Benutzernamen")

    if pw and len(set(pw)) <= 3:
        problems.append("besteht aus zu wenigen verschiedenen Zeichen")

    return problems


def describe_policy() -> str:
    """Einzeiler für Fehlermeldungen und Eingabefelder."""
    return (f"Mindestens {setting('pw_min_length')} Zeichen und "
            f"{setting('pw_require_classes')} verschiedene Zeichenarten "
            f"(Klein-, Großbuchstaben, Ziffern, Sonderzeichen).")


# ==========================================================================
# 2. Schutz gegen Erraten von Passwörtern
# ==========================================================================
# Die Fehlversuche liegen im Arbeitsspeicher: Sie sind kurzlebig, und ein
# Neustart des Backends soll niemanden dauerhaft aussperren. Wer die Sperre
# über Neustarts hinweg braucht, hebt sie in die Datenbank - dann aber mit
# regelmäßigem Aufräumen.
_failed: dict[str, list[float]] = {}
_locked: dict[str, float] = {}


def _key(kind: str, value: str) -> str:
    return f"{kind}:{(value or '').strip().lower()}"


def _prune(key: str, now: float) -> list[float]:
    window = setting("login_window_minutes") * 60
    hits = [t for t in _failed.get(key, []) if now - t < window]
    if hits:
        _failed[key] = hits
    else:
        _failed.pop(key, None)
    return hits


def lock_status(username: str, ip: str = "") -> tuple[bool, int]:
    """
    (gesperrt?, Restsekunden). Wird VOR der Passwortprüfung aufgerufen -
    sonst könnte man die Sperre durch schnelles Weiterprobieren umgehen.
    """
    now = time.time()
    for key in (_key("user", username), _key("ip", ip) if ip else None):
        if not key:
            continue
        until = _locked.get(key)
        if until and until > now:
            return True, int(until - now)
        if until:
            _locked.pop(key, None)
    return False, 0


def note_failure(username: str, ip: str = "") -> tuple[bool, int]:
    """
    Fehlversuch vermerken. Rückgabe: (jetzt gesperrt?, Restsekunden).
    Der Aufrufer schreibt zusätzlich einen Audit-Eintrag - unbefugte
    Anmeldeversuche müssen laut OPS.1.1.5 nachvollziehbar sein.
    """
    now = time.time()
    max_tries = setting("login_max_attempts")
    lock_for = setting("login_lock_minutes") * 60
    locked = False

    for key in (_key("user", username), _key("ip", ip) if ip else None):
        if not key:
            continue
        hits = _prune(key, now)
        hits.append(now)
        _failed[key] = hits
        if len(hits) >= max_tries:
            _locked[key] = now + lock_for
            _failed.pop(key, None)
            locked = True

    return locked, int(lock_for) if locked else 0


def note_success(username: str, ip: str = "") -> None:
    """Nach erfolgreicher Anmeldung ist die Zählung hinfällig."""
    for key in (_key("user", username), _key("ip", ip) if ip else None):
        if key:
            _failed.pop(key, None)
            _locked.pop(key, None)


def unlock(username: str) -> None:
    """Manuelle Entsperrung durch eine Administratorin."""
    _locked.pop(_key("user", username), None)
    _failed.pop(_key("user", username), None)


def locked_accounts() -> list[dict]:
    """Aktuell gesperrte Konten/Adressen - für die Anzeige im Dashboard."""
    now = time.time()
    out = []
    for key, until in list(_locked.items()):
        if until <= now:
            _locked.pop(key, None)
            continue
        kind, _, value = key.partition(":")
        out.append({"kind": kind, "value": value, "seconds_left": int(until - now)})
    return out

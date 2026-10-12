"""
totp.py
-------
Zwei-Faktor-Anmeldung mit Einmalcodes (TOTP, RFC 6238) - kompatibel mit allen
gängigen Apps: Aegis, FreeOTP, Google Authenticator, Microsoft Authenticator,
1Password, Bitwarden …

Warum selbst gebaut statt einer Bibliothek: Das Verfahren ist kurz und klar
beschrieben (HMAC-SHA1 über den Zeitschritt, davon die letzten Stellen), und
für einen so zentralen Sicherheitsbaustein ist eine Abhängigkeit weniger ein
echter Gewinn. Alles hier stammt aus der Standardbibliothek.

Zum QR-Code: Das Erzeugen eines QR-Bildes ist ungleich aufwendiger als TOTP
selbst. Dafür wird das Paket `qrcode` benutzt - fehlt es, funktioniert die
Einrichtung trotzdem: Die App kann das Geheimnis auch von Hand aufnehmen.

Sicherheitshinweise, die in der Umsetzung berücksichtigt sind:
  * Vergleich der Codes zeitkonstant (`hmac.compare_digest`) - sonst ließe
    sich über Antwortzeiten Stelle für Stelle raten.
  * Ein Code gilt nur EINMAL: Bereits benutzte Zeitfenster werden gemerkt,
    damit ein abgefangener Code nicht sofort wiederverwendet werden kann.
  * Ein Fenster Toleranz nach vorn und hinten (±30 s) gegen ungenaue Uhren.
  * Wiederherstellungscodes werden nur als Hash gespeichert - wie Passwörter.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30          # Sekunden je Code
WINDOW = 1           # ± ein Zeitschritt Toleranz
ISSUER = "RAPALLE.net RMM"

# Bereits verbrauchte (Benutzer, Zeitschritt)-Paare. Klein und kurzlebig,
# deshalb im Arbeitsspeicher - nach einem Neustart ist der Schutz kurz weg,
# das ist gegenüber einer Datenbankschreiboperation pro Login vertretbar.
_used: dict[str, set[int]] = {}


# ==========================================================================
# Geheimnis
# ==========================================================================

def new_secret(length: int = 20) -> str:
    """
    Neues Geheimnis als Base32 (so erwarten es die Apps).
    20 Byte = 160 Bit, das entspricht der Empfehlung in RFC 4226.
    """
    return base64.b32encode(os.urandom(length)).decode("ascii").rstrip("=")


def provisioning_uri(secret: str, username: str, issuer: str = ISSUER) -> str:
    """
    Die `otpauth://`-Adresse, die im QR-Code steckt.

    Der Aussteller steht bewusst zweimal drin - einmal im Label, einmal als
    Parameter. Ältere Apps lesen nur das eine, neuere nur das andere.
    """
    label = quote(f"{issuer}:{username}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}"
            f"&issuer={quote(issuer, safe='')}"
            f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}")


# ==========================================================================
# Code berechnen und prüfen
# ==========================================================================

def _code_at(secret: str, counter: int) -> str:
    """Ein Einmalcode für einen bestimmten Zeitschritt (RFC 4226)."""
    padding = "=" * (-len(secret) % 8)
    key = base64.b32decode(secret.upper() + padding, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    # "Dynamic Truncation": die letzten 4 Bit sagen, wo die 4 Bytes anfangen.
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def current_code(secret: str, at: float | None = None) -> str:
    """Der gerade gültige Code - nur für Tests und die Einrichtungsvorschau."""
    return _code_at(secret, int((at if at is not None else time.time()) // PERIOD))


def verify(secret: str, code: str, user_key: str = "", at: float | None = None) -> bool:
    """
    Prüft einen eingegebenen Code.

    user_key: Kennung des Benutzers. Wird sie mitgegeben, gilt jeder Code nur
    einmal - ohne diesen Schutz könnte ein mitgelesener Code innerhalb seiner
    30 Sekunden ein zweites Mal verwendet werden.
    """
    code = (code or "").strip().replace(" ", "")
    if not secret or not code.isdigit() or len(code) != DIGITS:
        return False

    now = int((at if at is not None else time.time()) // PERIOD)
    for step in range(-WINDOW, WINDOW + 1):
        counter = now + step
        if hmac.compare_digest(_code_at(secret, counter), code):
            if user_key:
                seen = _used.setdefault(user_key, set())
                if counter in seen:
                    return False              # schon benutzt
                seen.add(counter)
                # Alte Einträge wegräumen, sonst wächst die Menge ewig.
                _used[user_key] = {c for c in seen if c >= now - WINDOW - 2}
            return True
    return False


# ==========================================================================
# Wiederherstellungscodes
# ==========================================================================
# Für den Fall "Handy weg". Ohne sie müsste jedes Mal eine Administratorin
# eingreifen - und wenn es die letzte Administratorin trifft, wäre niemand
# mehr handlungsfähig.

def new_backup_codes(count: int = 8) -> list[str]:
    """Gut lesbare Einmalcodes, z.B. 'k3f9-2mzq'."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"   # ohne i/l/1/o/0
    out = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        out.append(f"{raw[:4]}-{raw[4:]}")
    return out


def hash_backup_codes(codes: list[str]) -> str:
    """Nur Hashes speichern - die Klartextcodes sieht der Benutzer genau einmal."""
    return ",".join(hashlib.sha256(c.encode()).hexdigest() for c in codes)


def use_backup_code(stored: str, code: str) -> tuple[bool, str]:
    """
    Prüft einen Wiederherstellungscode und entfernt ihn.
    Rückgabe: (passte?, verbleibende Hashes).
    """
    code = (code or "").strip().lower().replace(" ", "")
    if not stored or not code:
        return False, stored or ""
    wanted = hashlib.sha256(code.encode()).hexdigest()
    rest = [h for h in stored.split(",") if h]
    for h in rest:
        if hmac.compare_digest(h, wanted):
            rest.remove(h)
            return True, ",".join(rest)
    return False, stored


# ==========================================================================
# QR-Code
# ==========================================================================

def qr_data_uri(uri: str) -> str | None:
    """
    QR-Code als `data:`-Adresse, direkt in ein <img> einsetzbar.
    Gibt None zurück, wenn das Paket `qrcode` fehlt - dann zeigt die
    Oberfläche das Geheimnis zum Abtippen an.
    """
    try:
        import io
        import qrcode
    except ImportError:
        return None
    try:
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None

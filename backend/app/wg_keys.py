"""
wg_keys.py
==========
Schluesselerzeugung fuer WireGuard.

Das ist alles, was von der frueheren, selbstgeschriebenen
Protokoll-Umsetzung uebrig bleibt - und der einzige Teil davon, der
unstrittig war: Ein X25519-Schluesselpaar zu erzeugen und in Base64
auszugeben ist eindeutig definiert und laesst sich nachpruefen.

Das Protokoll selbst macht jetzt die Referenzumsetzung (wireguard-go bzw.
das Kernelmodul). Siehe den Kopf von vpn.py, warum.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey


def _raw_private(key: X25519PrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())


def _raw_public(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)


def generate() -> tuple[str, str]:
    """(privater, oeffentlicher Schluessel) in Base64 - wie in jeder .conf."""
    key = X25519PrivateKey.generate()
    return (base64.b64encode(_raw_private(key)).decode(),
            base64.b64encode(_raw_public(key)).decode())


def public_from_private(private_b64: str) -> str:
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(_raw_public(key)).decode()


def generate_psk() -> str:
    """
    Zusaetzlicher gemeinsamer Schluessel (PresharedKey).

    Optional, kostet nichts und haertet den Tunnel gegen kuenftige Angriffe
    mit Quantenrechnern.
    """
    return base64.b64encode(os.urandom(32)).decode()

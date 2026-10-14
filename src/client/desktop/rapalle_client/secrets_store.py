# secrets_store.py - Geraetetoken sicher im System ablegen (nur Standardbibliothek)
# -------------------------------------------------------------------------------
#   Windows  Anmeldeinformationsverwaltung (CredWriteW/CredReadW ueber ctypes)
#   Linux    Schluesselbund ueber "secret-tool" (libsecret, GNOME/KDE), falls vorhanden
# Rueckgabe None = nicht verfuegbar -> store.py faellt auf eine Datei (0600) zurueck.

from __future__ import annotations

import os
import shutil
import subprocess

TARGET_PREFIX = "RAPALLE.net RMM Client"


# ------------------------------------------------------------------ Windows
def _win():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredFree.argtypes = [ctypes.c_void_p]
    return ctypes, CREDENTIAL, adv


def _win_set(account: str, secret: str) -> bool:
    ctypes, CREDENTIAL, adv = _win()
    blob = secret.encode("utf-16-le")
    cred = CREDENTIAL()
    cred.Type = 1                      # CRED_TYPE_GENERIC
    cred.TargetName = f"{TARGET_PREFIX}/{account}"
    cred.UserName = account.split("|")[-1] or "rmm"
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.POINTER(ctypes.c_char))
    cred.Persist = 2                   # CRED_PERSIST_LOCAL_MACHINE (nur dieser Benutzer, dieses Geraet)
    return bool(adv.CredWriteW(ctypes.byref(cred), 0))


def _win_get(account: str) -> str | None:
    ctypes, CREDENTIAL, adv = _win()
    p = ctypes.POINTER(CREDENTIAL)()
    if not adv.CredReadW(f"{TARGET_PREFIX}/{account}", 1, 0, ctypes.byref(p)):
        return None
    try:
        c = p.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        adv.CredFree(p)


def _win_del(account: str) -> None:
    ctypes, _, adv = _win()
    adv.CredDeleteW(f"{TARGET_PREFIX}/{account}", 1, 0)


# ------------------------------------------------------------------ Linux (libsecret)
def _st() -> str | None:
    return shutil.which("secret-tool") if os.environ.get("DBUS_SESSION_BUS_ADDRESS") else None


def _run(args, inp=None):
    return subprocess.run(args, input=inp, capture_output=True, text=True, timeout=15)


# ------------------------------------------------------------------ Schnittstelle
def available() -> bool:
    return os.name == "nt" or bool(_st())


def set_secret(account: str, secret: str) -> bool:
    try:
        if os.name == "nt":
            return _win_set(account, secret)
        st = _st()
        if st:
            return _run([st, "store", "--label", TARGET_PREFIX, "service", "rapalle-client", "account", account],
                        secret).returncode == 0
    except Exception:
        pass
    return False


def get_secret(account: str) -> str | None:
    try:
        if os.name == "nt":
            return _win_get(account)
        st = _st()
        if st:
            r = _run([st, "lookup", "service", "rapalle-client", "account", account])
            return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except Exception:
        pass
    return None


def delete_secret(account: str) -> None:
    try:
        if os.name == "nt":
            _win_del(account)
            return
        st = _st()
        if st:
            _run([st, "clear", "service", "rapalle-client", "account", account])
    except Exception:
        pass

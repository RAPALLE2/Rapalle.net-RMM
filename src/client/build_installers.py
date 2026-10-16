#!/usr/bin/env python3
"""
build_installers.py - baut ALLE Pakete des RAPALLE.net RMM Clients
===================================================================
(Gegenstueck zu src/backend/app/build_installers.py fuer den Agenten.)

Ergebnis in src/client/release/  (das Modul "client" bietet sie unter
Profil -> Client-App & Geraete und per Einzeiler zum Download an):

  Windows  RapalleClient-<v>-windows.exe   PyInstaller-Onefile (keine Abhaengigkeiten)
           RapalleClient-<v>-windows.msi   Installer (Programme + Startmenue/Desktop), per GPO/Intune verteilbar
           RapalleClient-<v>-windows.zip   die .exe zum Entpacken (ohne Installation)
  Linux    rapalle-client_<v>_all.deb      Debian/Ubuntu/Mint (Python 3 + Tk vom System)
           rapalle-client-<v>-linux.tar.gz / .tar   fuer alle Distributionen (install.sh liegt bei)
  Android  RapalleClient-<v>-android.apk   native App (Android 6+), dauerhaft signiert

Werkzeuge (Linux-Bausystem, z.B. Ubuntu 24.04):
  exe   wine64 + Windows-Python mit PyInstaller  (--winpython C:\\pfad\\python.exe unter Wine)
        oder direkt auf Windows: python build_installers.py --targets exe,zip,msi
  msi   wixl (Paket "wixl"/msitools)            deb/tar  nur Python
  apk   aapt, javac, dalvik-exchange (dx), zipalign, apksigner, android-sdk-platform-23

Aufruf:
  python3 src/client/build_installers.py                    # alles, was mit den Werkzeugen geht
  python3 src/client/build_installers.py --targets deb,tar,apk
  python3 src/client/build_installers.py --list
  python3 src/client/build_installers.py --tools            # welche Formate gehen hier? (JSON)
  python3 src/client/build_installers.py --out DIR --set-version 1.2.1   # Server-Knopf "neu bauen"
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESKTOP = HERE / "desktop"
ANDROID = HERE / "android"
RES = HERE / "resources"
RELEASE = HERE / "release"          # Standard-Ausgabe (mitgelieferte Pakete); mit --out aenderbar
ALL = ["exe", "msi", "zip", "deb", "tar", "apk"]


def set_version(v: str) -> None:
    """__version__ im Desktop-Client setzen (die Android-App uebernimmt sie beim Bauen)."""
    import re as _re
    if not _re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise SystemExit(f"Ungueltige Version: {v} (Format 1.2.3)")
    p = DESKTOP / "rapalle_client" / "__init__.py"
    s = p.read_text(encoding="utf-8")
    s = _re.sub(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{v}"', s)
    p.write_text(s, encoding="utf-8")


def tools() -> dict:
    """Welche Formate lassen sich auf DIESEM System bauen?"""
    winpy = os.environ.get("RMM_WINPYTHON") or "/opt/winpy/python/python.exe"
    wine = shutil.which("wine64") or shutil.which("wine") or ("/usr/lib/wine/wine64" if Path("/usr/lib/wine/wine64").exists() else None)
    exe_ok = os.name == "nt" or bool(wine and Path(winpy).exists())
    apk_tools = all(shutil.which(t) for t in ("aapt", "javac", "dalvik-exchange", "zipalign", "apksigner"))
    apk_ok = apk_tools and Path("/usr/lib/android-sdk/platforms/android-23/android.jar").exists() \
        and (ANDROID / "signing" / "release.jks").exists()
    return {"exe": exe_ok, "zip": exe_ok, "msi": exe_ok and (os.name == "nt" or bool(shutil.which("wixl"))),
            "deb": True, "tar": True, "apk": apk_ok}


def version() -> str:
    for line in (DESKTOP / "rapalle_client" / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=")[1].strip().strip('"\'')
    return "1.0.0"


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} fehlgeschlagen:\n{(r.stdout + r.stderr)[-3000:]}")
    return r


def desktop_files() -> list[tuple[Path, str]]:
    """(Quelle, Ziel relativ) aller Dateien des Desktop-Clients."""
    out = []
    for p in sorted(DESKTOP.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out.append((p, p.relative_to(DESKTOP).as_posix()))
    return out


# ============================================================== Windows
def build_exe(v: str, winpython: str | None) -> Path:
    out = RELEASE / f"RapalleClient-{v}-windows.exe"
    args = ["-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--name", "RapalleClient",
            "--icon", "rapalle_client/resources/rmmclient.ico",
            "--add-data", "rapalle_client/resources;rapalle_client/resources", "rapalle-client.py"]
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "src"
        shutil.copytree(DESKTOP, work, ignore=shutil.ignore_patterns("__pycache__"))
        if os.name == "nt":
            run([sys.executable, *args], cwd=work)
        else:
            wine = shutil.which("wine64") or shutil.which("wine") or "/usr/lib/wine/wine64"
            py = winpython or os.environ.get("RMM_WINPYTHON") or "/opt/winpy/python/python.exe"
            if not Path(wine).exists() or not Path(py).exists():
                raise RuntimeError("Fuer die .exe unter Linux werden wine64 und ein Windows-Python mit PyInstaller "
                                   "gebraucht (--winpython). Alternativ auf Windows bauen.")
            env = dict(os.environ, WINEDEBUG="-all")
            # Wine braucht eine Pipe als Ausgabe (Datei-Umleitung -> "Invalid handle")
            p = subprocess.Popen([wine, py, *args], cwd=work, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True)
            log = p.communicate(timeout=1800)[0]
            if p.returncode != 0 or not (work / "dist" / "RapalleClient.exe").exists():
                raise RuntimeError("PyInstaller (Wine) fehlgeschlagen:\n" + log[-3000:])
        shutil.copy2(work / "dist" / "RapalleClient.exe", out)
    return out


def build_zip(v: str, exe: Path) -> Path:
    out = RELEASE / f"RapalleClient-{v}-windows.zip"
    readme = ("RAPALLE.net RMM Client\r\n======================\r\n\r\n"
              "RapalleClient.exe starten - keine Installation noetig (Windows 10/11).\r\n"
              "Fuer Startmenue/Desktop-Verknuepfung lieber die .msi verwenden.\r\n")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, "RapalleClient/RapalleClient.exe")
        z.writestr("RapalleClient/LIESMICH.txt", readme)
    return out


def build_msi(v: str, exe: Path) -> Path:
    wixl = shutil.which("wixl")
    if not wixl and os.name != "nt":
        raise RuntimeError("wixl fehlt (apt install wixl)")
    out = RELEASE / f"RapalleClient-{v}-windows.msi"
    ver4 = ".".join((v.split(".") + ["0", "0", "0"])[:3])
    wxs = f"""<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="RAPALLE.net RMM Client" Language="1031" Codepage="1252" Version="{ver4}"
           Manufacturer="RAPALLE.net" UpgradeCode="6B3F0E5A-2C4D-4E8B-9A71-5D2C8F1E0A34">
    <Package InstallerVersion="200" Compressed="yes" InstallScope="perMachine"
             Description="RAPALLE.net RMM Client" Manufacturer="RAPALLE.net" />
    <MajorUpgrade DowngradeErrorMessage="Eine neuere Version ist bereits installiert." />
    <Media Id="1" Cabinet="client.cab" EmbedCab="yes" />
    <Icon Id="AppIcon" SourceFile="rmmclient.ico" />
    <Property Id="ARPPRODUCTICON" Value="AppIcon" />
    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="ProgramFiles64Folder">
        <Directory Id="INSTALLDIR" Name="RAPALLE.net RMM Client">
          <Component Id="MainExe" Guid="7D1A2B3C-4E5F-4A6B-8C7D-9E0F1A2B3C4D" Win64="yes">
            <File Id="RapalleClientExe" Source="RapalleClient.exe" KeyPath="yes" />
          </Component>
        </Directory>
      </Directory>
      <Directory Id="ProgramMenuFolder">
        <Component Id="StartShortcut" Guid="8E2B3C4D-5F6A-4B7C-9D8E-0F1A2B3C4D5E">
          <Shortcut Id="StartMenuShortcut" Name="RAPALLE.net RMM" Target="[INSTALLDIR]RapalleClient.exe"
                    WorkingDirectory="INSTALLDIR" Icon="AppIcon" />
          <RegistryValue Root="HKCU" Key="Software\\RAPALLE.net\\RMMClient" Name="startmenu" Type="integer" Value="1" KeyPath="yes" />
        </Component>
      </Directory>
      <Directory Id="DesktopFolder">
        <Component Id="DesktopShortcut" Guid="9F3C4D5E-6A7B-4C8D-0E9F-1A2B3C4D5E6F">
          <Shortcut Id="DesktopShortcutLnk" Name="RAPALLE.net RMM" Target="[INSTALLDIR]RapalleClient.exe"
                    WorkingDirectory="INSTALLDIR" Icon="AppIcon" />
          <RegistryValue Root="HKCU" Key="Software\\RAPALLE.net\\RMMClient" Name="desktop" Type="integer" Value="1" KeyPath="yes" />
        </Component>
      </Directory>
    </Directory>
    <Feature Id="Main" Level="1" Title="RAPALLE.net RMM Client">
      <ComponentRef Id="MainExe" />
      <ComponentRef Id="StartShortcut" />
      <ComponentRef Id="DesktopShortcut" />
    </Feature>
  </Product>
</Wix>
"""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        shutil.copy2(exe, t / "RapalleClient.exe")
        shutil.copy2(RES / "rmmclient.ico", t / "rmmclient.ico")
        (t / "client.wxs").write_text(wxs, encoding="utf-8")
        run([wixl or "wixl", "-a", "x64", "-o", str(out), "client.wxs"], cwd=t)
    return out


# ============================================================== Linux
LINUX_LAUNCHER = """#!/bin/sh
# Startet den RAPALLE.net RMM Client (Python 3 + Tk vom System)
exec python3 /opt/rapalle-client/rapalle-client.py "$@"
"""
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=RAPALLE.net RMM
Comment=RMM-Dashboard, Relay-Laufwerk, Remote-Shell und Fernbild
Exec=rapalle-client
Icon=rapalle-client
Terminal=false
Categories=Network;RemoteAccess;
StartupWMClass=Tk
"""
TAR_INSTALL = """#!/bin/sh
# Installiert den RAPALLE.net RMM Client fuer den aktuellen Benutzer (ohne root):
#   ~/.local/share/rapalle-client, ~/.local/bin/rapalle-client, Startmenue-Eintrag.
# Systemweit (nach /opt):  sudo ./install.sh --system
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$1" = "--system" ]; then
  DEST=/opt/rapalle-client; BIN=/usr/local/bin; APPS=/usr/share/applications; ICONS=/usr/share/icons/hicolor/256x256/apps
else
  DEST="$HOME/.local/share/rapalle-client"; BIN="$HOME/.local/bin"; APPS="$HOME/.local/share/applications"
  ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"
fi
command -v python3 >/dev/null || { echo "python3 fehlt (z.B. sudo apt install python3 python3-tk)"; exit 1; }
python3 -c "import tkinter" 2>/dev/null || echo "Hinweis: Tk fehlt - bitte python3-tk (Debian/Ubuntu) bzw. python3-tkinter (Fedora) installieren."
mkdir -p "$DEST" "$BIN" "$APPS" "$ICONS"
cp -r "$HERE/app/." "$DEST/"
printf '#!/bin/sh\\nexec python3 "%s/rapalle-client.py" "$@"\\n' "$DEST" > "$BIN/rapalle-client"
chmod +x "$BIN/rapalle-client"
cp "$HERE/rapalle-client.png" "$ICONS/rapalle-client.png"
sed "s|^Exec=.*|Exec=$BIN/rapalle-client|" "$HERE/rapalle-client.desktop" > "$APPS/rapalle-client.desktop"
echo "Installiert. Start ueber das Anwendungsmenue oder: $BIN/rapalle-client"
"""


def _tar_dirs(tf: tarfile.TarFile, name: str) -> None:
    """Fehlende Ordner-Eintraege anlegen (dpkg braucht sie)."""
    seen = tf.__dict__.setdefault("_rmm_dirs", set())
    parts = name.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        d = "/".join(parts[:i])
        if d in ("", ".") or d in seen:
            continue
        seen.add(d)
        ti = tarfile.TarInfo(d)
        ti.type, ti.mode, ti.mtime, ti.uname, ti.gname = tarfile.DIRTYPE, 0o755, int(time.time()), "root", "root"
        tf.addfile(ti)


def _tar_add(tf: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644):
    _tar_dirs(tf, name)
    ti = tarfile.TarInfo(name)
    ti.size, ti.mode, ti.mtime, ti.uname, ti.gname = len(data), mode, int(time.time()), "root", "root"
    tf.addfile(ti, io.BytesIO(data))


def build_tar(v: str) -> list[Path]:
    base = f"rapalle-client-{v}"
    outs = []
    for suffix, mode in ((".tar.gz", "w:gz"), (".tar", "w")):
        out = RELEASE / f"rapalle-client-{v}-linux{suffix}"
        with tarfile.open(out, mode) as tf:
            for src, rel in desktop_files():
                _tar_add(tf, f"{base}/app/{rel}", src.read_bytes(), 0o755 if rel.endswith("rapalle-client.py") else 0o644)
            _tar_add(tf, f"{base}/app/.rapalle-install", b"tar\n")              # Auto-Update: Installationsart
            _tar_add(tf, f"{base}/install.sh", TAR_INSTALL.encode(), 0o755)
            _tar_add(tf, f"{base}/rapalle-client.desktop", DESKTOP_ENTRY.encode())
            _tar_add(tf, f"{base}/rapalle-client.png", (RES / "rmmclient-256.png").read_bytes())
        outs.append(out)
    return outs


def build_deb(v: str) -> Path:
    """.deb ohne dpkg-deb (ar + tar), laeuft also auf jedem Bausystem."""
    out = RELEASE / f"rapalle-client_{v}_all.deb"
    data = io.BytesIO()
    size = 0
    with tarfile.open(fileobj=data, mode="w:gz") as tf:
        for src, rel in desktop_files():
            b = src.read_bytes()
            size += len(b)
            _tar_add(tf, f"./opt/rapalle-client/{rel}", b, 0o755 if rel.endswith("rapalle-client.py") else 0o644)
        _tar_add(tf, "./opt/rapalle-client/.rapalle-install", b"deb\n")      # Auto-Update: Installationsart
        _tar_add(tf, "./usr/bin/rapalle-client", LINUX_LAUNCHER.encode(), 0o755)
        _tar_add(tf, "./usr/share/applications/rapalle-client.desktop", DESKTOP_ENTRY.encode())
        _tar_add(tf, "./usr/share/icons/hicolor/256x256/apps/rapalle-client.png", (RES / "rmmclient-256.png").read_bytes())
    control = (f"Package: rapalle-client\nVersion: {v}\nSection: net\nPriority: optional\nArchitecture: all\n"
               f"Depends: python3 (>= 3.8), python3-tk\n"
               f"Recommends: chromium | chromium-browser | google-chrome-stable | microsoft-edge-stable | firefox | firefox-esr, "
               f"libglib2.0-bin, libsecret-tools\n"
               f"Suggests: freerdp3-x11 | freerdp2-x11 | remmina, tigervnc-viewer\n"
               f"Installed-Size: {max(1, size // 1024)}\nMaintainer: RAPALLE.net <support@rapalle.net>\n"
               f"Description: RAPALLE.net RMM Client\n"
               f" Das RMM-Dashboard als App: gespeicherte Anmeldung, Relay als Laufwerk im\n"
               f" Dateimanager, Remote-Shell im eigenen Fenster und Fernbild per RDP/VNC.\n")
    ctl = io.BytesIO()
    with tarfile.open(fileobj=ctl, mode="w:gz") as tf:
        _tar_add(tf, "./control", control.encode())
        _tar_add(tf, "./postinst", b"#!/bin/sh\nset -e\ncommand -v update-desktop-database >/dev/null && "
                                   b"update-desktop-database -q /usr/share/applications || true\n", 0o755)

    def ar_member(name: str, payload: bytes) -> bytes:
        hdr = f"{name:<16}{int(time.time()):<12}{0:<6}{0:<6}{100644:<8}{len(payload):<10}`\n".encode()
        return hdr + payload + (b"\n" if len(payload) % 2 else b"")

    with open(out, "wb") as f:
        f.write(b"!<arch>\n")
        f.write(ar_member("debian-binary", b"2.0\n"))
        f.write(ar_member("control.tar.gz", ctl.getvalue()))
        f.write(ar_member("data.tar.gz", data.getvalue()))
    return out


# ============================================================== Android
def build_apk(v: str) -> Path:
    jar = Path("/usr/lib/android-sdk/platforms/android-23/android.jar")
    tools = {t: shutil.which(t) for t in ("aapt", "javac", "dalvik-exchange", "zipalign", "apksigner")}
    missing = [t for t, p in tools.items() if not p] + ([] if jar.exists() else ["android-sdk-platform-23"])
    if missing:
        raise RuntimeError("Fehlende Werkzeuge: " + ", ".join(missing) +
                           " (apt install aapt openjdk-17-jdk-headless dalvik-exchange zipalign apksigner android-sdk-platform-23)")
    ks, kspass = ANDROID / "signing" / "release.jks", ANDROID / "signing" / "keystore.pass"
    if not ks.exists():
        raise RuntimeError("Signier-Schluessel fehlt: src/client/android/signing/release.jks")
    out = RELEASE / f"RapalleClient-{v}-android.apk"
    code = int("".join(f"{int(x):02d}" for x in (v.split(".") + ["0", "0"])[:3]))   # 1.0.0 -> 10000
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / "gen").mkdir()
        (t / "classes").mkdir()
        man = (ANDROID / "AndroidManifest.xml").read_text(encoding="utf-8")
        man = man.replace('android:versionCode="1"', f'android:versionCode="{code}"').replace(
            'android:versionName="1.0.0"', f'android:versionName="{v}"')
        (t / "AndroidManifest.xml").write_text(man, encoding="utf-8")
        java = (ANDROID / "src/net/rapalle/rmmclient/MainActivity.java").read_text(encoding="utf-8")
        java = java.replace('static final String VERSION = "1.0.0";', f'static final String VERSION = "{v}";')
        jdir = t / "java/net/rapalle/rmmclient"
        jdir.mkdir(parents=True)
        (jdir / "MainActivity.java").write_text(java, encoding="utf-8")
        run(["aapt", "package", "-f", "-m", "-M", "AndroidManifest.xml", "-S", str(ANDROID / "res"),
             "-A", str(ANDROID / "assets"), "-I", str(jar), "-J", "gen", "-F", "unsigned.apk"], cwd=t)
        run(["javac", "-source", "8", "-target", "8", "-encoding", "UTF-8", "-Xlint:-options", "-bootclasspath", str(jar),
             "-d", "classes", "gen/net/rapalle/rmmclient/R.java", "java/net/rapalle/rmmclient/MainActivity.java"], cwd=t)
        run(["dalvik-exchange", "--dex", "--min-sdk-version=23", "--output=classes.dex", "classes"], cwd=t)
        run(["aapt", "add", "unsigned.apk", "classes.dex"], cwd=t)
        run(["zipalign", "-f", "4", "unsigned.apk", "aligned.apk"], cwd=t)
        run(["apksigner", "sign", "--ks", str(ks), "--ks-key-alias", "rmmclient", "--ks-pass", f"file:{kspass}",
             "--out", str(out), "aligned.apk"], cwd=t)
        run(["apksigner", "verify", str(out)], cwd=t)
    for extra in RELEASE.glob("*.idsig"):
        extra.unlink()
    return out


# ============================================================== main
def main():
    ap = argparse.ArgumentParser(description="Baut die Pakete des RAPALLE.net RMM Clients")
    ap.add_argument("--targets", default=",".join(ALL), help="Komma-Liste aus: " + ",".join(ALL))
    ap.add_argument("--winpython", help="Windows-python.exe (unter Wine) mit PyInstaller")
    ap.add_argument("--list", action="store_true", help="vorhandene Pakete anzeigen")
    ap.add_argument("--keep-old", action="store_true", help="aeltere Versionen in release/ nicht loeschen")
    ap.add_argument("--out", help="Ausgabeordner statt src/client/release")
    ap.add_argument("--set-version", help="Version vorher setzen (z. B. 1.2.1)")
    ap.add_argument("--tools", action="store_true", help="verfuegbare Formate als JSON ausgeben")
    ap.add_argument("--available", action="store_true", help="nur Formate bauen, fuer die Werkzeuge da sind")
    a = ap.parse_args()
    global RELEASE
    if a.tools:
        import json as _json
        print(_json.dumps({"tools": tools(), "version": version()}))
        return
    if a.out:
        RELEASE = Path(a.out).resolve()
    if a.set_version:
        set_version(a.set_version)
    RELEASE.mkdir(parents=True, exist_ok=True)
    if a.list:
        for p in sorted(RELEASE.iterdir()):
            print(f"{p.stat().st_size:>12,}  {p.name}")
        return
    v = version()
    targets = [t.strip() for t in a.targets.split(",") if t.strip()]
    if a.available:
        av = tools()
        skipped = [t for t in targets if not av.get(t)]
        targets = [t for t in targets if av.get(t)]
        if skipped:
            print(f"Übersprungen (Werkzeuge fehlen hier): {', '.join(skipped)}")
    if not a.keep_old:
        # Alte Versionen nur fuer die Formate entfernen, die jetzt neu gebaut werden
        exts = {"exe": "-windows.exe", "zip": "-windows.zip", "msi": "-windows.msi", "deb": "_all.deb",
                "tar": "-linux.tar", "apk": "-android.apk"}
        for p in RELEASE.iterdir():
            if not p.is_file() or v in p.name:
                continue
            if any(t in targets and (p.name.endswith(sfx) or (t == "tar" and p.name.endswith("-linux.tar.gz")))
                   for t, sfx in exts.items()):
                p.unlink()
    print(f"RAPALLE.net RMM Client {v} -> {RELEASE}")
    ok, failed, exe = [], [], RELEASE / f"RapalleClient-{v}-windows.exe"
    steps = [("exe", lambda: build_exe(v, a.winpython)), ("zip", lambda: build_zip(v, exe)),
             ("msi", lambda: build_msi(v, exe)), ("deb", lambda: build_deb(v)), ("tar", lambda: build_tar(v)),
             ("apk", lambda: build_apk(v))]
    for name, fn in steps:
        if name not in targets:
            continue
        if name in ("zip", "msi") and not exe.exists():
            failed.append((name, "braucht zuerst die .exe"))
            continue
        print(f"\n== {name}")
        try:
            res = fn()
            for p in (res if isinstance(res, list) else [res]):
                print(f"  -> {p.name} ({p.stat().st_size:,} Bytes)")
                ok.append(p.name)
        except Exception as e:      # noqa: BLE001 - weiter mit dem naechsten Paket
            print(f"  !! {e}")
            failed.append((name, str(e).splitlines()[0]))
    print(f"\nFertig: {len(ok)} Paket(e)" + (f", fehlgeschlagen: {', '.join(n for n, _ in failed)}" if failed else ""))
    sys.exit(1 if failed and not ok else 0)


if __name__ == "__main__":
    main()

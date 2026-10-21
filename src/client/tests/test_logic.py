# Tests ohne Oberflaeche: Adressen, Befehle je Plattform, Speicher, API-Ablauf.
#   cd src/client && python -m pytest tests
# Fuer den Ende-zu-Ende-Test gegen ein laufendes Backend:
#   RMM_TEST_SERVER=http://127.0.0.1:4000 RMM_TEST_USER=admin RMM_TEST_PASS=... python -m pytest tests
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))

from rapalle_client import features  # noqa: E402
from rapalle_client.api import Api, ApiError, normalize_server  # noqa: E402
from rapalle_client.store import Store  # noqa: E402


def test_normalize_server():
    assert normalize_server("rmm.firma.de") == "https://rmm.firma.de"
    assert normalize_server("http://10.0.0.5:4000/") == "http://10.0.0.5:4000"
    with pytest.raises(ApiError):
        normalize_server("")
    with pytest.raises(ApiError):
        normalize_server("ftp://x")


def test_windows_unc():
    assert features.build_windows_unc("https://rmm.firma.de") == r"\\rmm.firma.de@SSL\dav"
    assert features.build_windows_unc("https://rmm.firma.de:8443") == r"\\rmm.firma.de@SSL@8443\dav"
    assert features.build_windows_unc("http://nas:4000") == r"\\nas@4000\dav"
    cmd = features.build_windows_mount("https://rmm.firma.de", "Z", "anna", "tok")
    assert cmd == ["net", "use", "Z:", r"\\rmm.firma.de@SSL\dav", "tok", "/user:anna", "/persistent:yes"]


def test_linux_uri():
    assert features.build_linux_uri("https://rmm.firma.de", "anna") == "davs://anna@rmm.firma.de/dav/"
    assert features.build_linux_uri("http://nas:4000", "a b") == "dav://a%20b@nas:4000/dav/"


def test_drive_letter():
    assert features.free_drive_letter(set()) == "Z"
    assert features.free_drive_letter({"Z", "Y"}) == "X"


def test_rdp_and_android_uris():
    rdp = features.build_rdp_file("rmm.firma.de", 55001)
    assert "full address:s:rmm.firma.de:55001" in rdp and rdp.endswith("\r\n")
    assert features.android_screen_uri("vnc", "h", 5) == "vnc://h:5"
    assert features.android_screen_uri("rdp", "h", 5).startswith("rdp://full%20address=s:h:5")
    assert features.linux_screen_candidates("rdp", "h", 9)[0][1] == "/v:h:9"


def test_store_token_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Store, "_keyring", staticmethod(lambda: None))   # ohne Schluesselbund -> Datei
    st = Store(tmp_path)
    st.set(server="https://x", username="anna")
    assert st.save_token("abc.def") == "datei"
    assert Store(tmp_path).load_token() == "abc.def"
    if os.name != "nt":
        assert oct((tmp_path / "device.token").stat().st_mode & 0o777) == "0o600"
    st.clear_token()
    assert st.load_token() == ""


def test_dashboard_url():
    from rapalle_client import __version__
    a = Api("https://rmm.firma.de", token="T.O.K")
    assert a.dashboard_url() == f"https://rmm.firma.de/#client-token=T.O.K&client-app={__version__}"
    assert a.dashboard_url("terminal", "c1") == \
        f"https://rmm.firma.de/?solo=terminal&client=c1#client-token=T.O.K&client-app={__version__}"


def test_launch_link_and_ssh():
    from rapalle_client import features, launcher
    assert launcher.parse("rapalle-rmm://launch?s=https%3A%2F%2Frmm.firma.de&t=abc") == ("https://rmm.firma.de", "abc")
    for bad in ("rapalle-rmm://evil?s=x&t=y", "https://rmm.firma.de", "rapalle-rmm://launch?s=x"):
        with pytest.raises(ValueError):
            launcher.parse(bad)
    assert features.build_ssh_command("127.0.0.1", 4022, "root", "rmm-ssh-a.firma.de") == \
        ["ssh", "-p", "4022", "-o", "HostKeyAlias=rmm-ssh-a.firma.de", "root@127.0.0.1"]


@pytest.mark.skipif(not os.environ.get("RMM_TEST_SERVER"), reason="kein Test-Backend angegeben")
def test_end_to_end(tmp_path):
    srv = os.environ["RMM_TEST_SERVER"]
    api = Api(srv)
    api.login(os.environ["RMM_TEST_USER"], os.environ["RMM_TEST_PASS"])
    reg = api.register_device("Pytest-Gerät")
    token = reg["device_token"]
    api2 = Api(srv)
    api2.session(token)
    assert api2.config()["server"]
    devs = api2.devices()
    me = [d for d in devs if d["id"] == reg["device"]["id"]][0]
    assert me["current"] is True
    api2.remove_device(reg["device"]["id"])
    with pytest.raises(ApiError) as e:
        Api(srv).session(token)
    assert e.value.status == 401


def test_browser_command(tmp_path):
    from rapalle_client import browser
    cmd = browser.build_command(("/usr/bin/chromium", "chromium"), "https://x/#client-token=T", tmp_path)
    assert cmd[1] == "--app=https://x/#client-token=T" and f"--user-data-dir={tmp_path}" in cmd
    assert browser.build_command(("/usr/bin/firefox", "firefox"), "https://x/", tmp_path) == ["/usr/bin/firefox", "--new-window", "https://x/"]


def test_packages_built():
    """Die mitgelieferten Pakete muessen alle Formate abdecken (build_installers.py)."""
    rel = Path(__file__).resolve().parents[1] / "release"
    names = " ".join(p.name for p in rel.iterdir()) if rel.is_dir() else ""
    for ext in (".exe", ".msi", ".zip", ".deb", ".tar.gz", ".tar", ".apk"):
        assert ext in names, f"{ext} fehlt in release/"

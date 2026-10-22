# collect.py (Modul "canary") - Server-seitige Infos eines Aufrufs sammeln
# -----------------------------------------------------------------------
# Aus einem HTTP-Request so viel wie moeglich OHNE JavaScript ablesen: echte IP
# (hinter Proxy/Tunnel), Standort per Geo-API (best effort), Browser/OS aus dem
# User-Agent, Referrer, Sprache, DNT u. a. Reine Server-Sicht - eine echte
# Sofort-Weiterleitung kann nicht mehr erfassen.

from __future__ import annotations

import json
import re
import urllib.request

# --- Geo-Cache (IP -> {...}); best effort, nie blockierend lange ---
_geo_cache: dict[str, dict] = {}
GEO_URL = "http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,city,zip,isp,org,as,lat,lon,timezone,mobile,proxy,hosting,query"


def client_ip(request) -> str:
    """Echte Absender-IP - respektiert Cloudflare/Proxy-Header."""
    h = request.headers
    cf = h.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = h.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    xr = h.get("x-real-ip")
    if xr:
        return xr.strip()
    return (request.client.host if request.client else "") or ""


def _is_private(ip: str) -> bool:
    return bool(re.match(r"^(10\.|127\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1|fe80:|fc|fd)", ip or ""))


def geo(ip: str, timeout: float = 3.0) -> dict:
    """Standort/Netz zu einer IP (best effort). Leeres dict bei Fehler/privat."""
    if not ip or _is_private(ip):
        return {}
    if ip in _geo_cache:
        return _geo_cache[ip]
    out = {}
    try:
        req = urllib.request.Request(GEO_URL.format(ip=ip), headers={"User-Agent": "RAPALLE-RMM"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read() or b"{}")
        if j.get("status") == "success":
            out = {"country": j.get("country"), "country_code": j.get("countryCode"),
                   "region": j.get("regionName"), "city": j.get("city"), "zip": j.get("zip"),
                   "isp": j.get("isp"), "org": j.get("org"), "asn": j.get("as"),
                   "lat": j.get("lat"), "lon": j.get("lon"), "timezone": j.get("timezone"),
                   "mobile": j.get("mobile"), "proxy": j.get("proxy"), "hosting": j.get("hosting")}
            out = {k: v for k, v in out.items() if v not in (None, "")}
    except Exception:
        out = {}
    _geo_cache[ip] = out
    if len(_geo_cache) > 5000:
        _geo_cache.clear()
    return out


# --- User-Agent grob zerlegen (ohne Fremdbibliothek) ---
def parse_ua(ua: str) -> dict:
    ua = ua or ""
    low = ua.lower()
    # Betriebssystem
    os_name = "Unbekannt"
    m = re.search(r"windows nt ([\d.]+)", low)
    winmap = {"10.0": "10/11", "6.3": "8.1", "6.2": "8", "6.1": "7"}
    if m:
        os_name = "Windows " + winmap.get(m.group(1), m.group(1))
    elif "android" in low:
        mm = re.search(r"android ([\d.]+)", low)
        os_name = "Android " + (mm.group(1) if mm else "")
    elif "iphone" in low or "ipad" in low:
        mm = re.search(r"os ([\d_]+)", low)
        os_name = "iOS " + (mm.group(1).replace("_", ".") if mm else "")
    elif "mac os x" in low:
        mm = re.search(r"mac os x ([\d_]+)", low)
        os_name = "macOS " + (mm.group(1).replace("_", ".") if mm else "")
    elif "linux" in low:
        os_name = "Linux"
    # Browser
    br = "Unbekannt"
    for name, pat in (("Edge", r"edg/([\d.]+)"), ("Opera", r"opr/([\d.]+)"),
                      ("Samsung Internet", r"samsungbrowser/([\d.]+)"),
                      ("Chrome", r"chrome/([\d.]+)"), ("Firefox", r"firefox/([\d.]+)"),
                      ("Safari", r"version/([\d.]+).*safari")):
        mm = re.search(pat, low)
        if mm:
            br = f"{name} {mm.group(1)}"
            break
    device = "Mobil" if ("mobile" in low or "android" in low or "iphone" in low) else \
             ("Tablet" if "ipad" in low or "tablet" in low else "Desktop")
    is_bot = bool(re.search(r"bot|crawl|spider|slurp|curl|wget|python-requests|headless|monitor|preview", low))
    return {"os": os_name.strip(), "browser": br, "device": device, "bot": is_bot}


def collect(request) -> dict:
    """Alles aus dem Request zusammentragen -> flaches dict fuer den Treffer."""
    ip = client_ip(request)
    h = request.headers
    ua = h.get("user-agent", "")
    info = {
        "ip": ip,
        "ua": ua[:400],
        "referer": (h.get("referer") or "")[:300],
        "lang": (h.get("accept-language") or "")[:120],
        "dnt": h.get("dnt") or "",
        "encoding": (h.get("accept-encoding") or "")[:80],
        "forwarded_for": (h.get("x-forwarded-for") or "")[:200],
        "host": h.get("host") or "",
        **parse_ua(ua),
    }
    g = geo(ip)
    if g:
        info["geo"] = g
    return info

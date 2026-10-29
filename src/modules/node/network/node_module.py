# node_module.py (Node-Teil von Modul "network")
# ---------------------------------------------
# LAN-Scan und Portscan IM NETZ DIESER NODE (statt vom RMM-Server aus).
# network_scan.py ist dieselbe Datei wie im Backend-Modul (nur Standardbibliothek).
#   "net-scan-start"  {target, speed}            -> {job_id}
#   "net-scan-status" {job_id}                   -> Job (Fortschritt, Geraete)
#   "net-scan-cancel" {job_id}                   -> {ok}
#   "net-portscan"    {ip, ports:[..], concurrency, timeout} -> {open:[...]}
#   "net-preview"     {target}                   -> {label, subnets}
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("rmm_node_network_scan", Path(__file__).with_name("network_scan.py"))
ns = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ns)


async def scan_start(ctx, data: dict) -> dict:
    return {"job_id": ns.start_scan_job(data.get("target") or None, data.get("speed") or "normal")}


def scan_status(ctx, data: dict) -> dict:
    job = ns.get_scan_job(str(data.get("job_id") or ""))
    if not job:
        raise RuntimeError("Scan-Job nicht gefunden (evtl. abgelaufen)")
    return {k: v for k, v in job.items() if k != "cancel"}


def scan_cancel(ctx, data: dict) -> dict:
    return {"ok": ns.cancel_scan_job(str(data.get("job_id") or ""))}


async def portscan(ctx, data: dict) -> dict:
    ports = [int(p) for p in (data.get("ports") or []) if 0 < int(p) < 65536] or None   # leer = gaengige Ports
    res = await ns.scan_ports(str(data.get("ip") or ""), ports,
                              concurrency=int(data.get("concurrency") or 200),
                              timeout=float(data.get("timeout") or 0.6))
    return {"open": res}


def preview(ctx, data: dict) -> dict:
    prefixes, label = ns.parse_scan_target(data.get("target") or None)
    return {"label": label, "subnets": len(prefixes), "hosts": len(prefixes) * 254}


OPS = {"net-scan-start": scan_start, "net-scan-status": scan_status, "net-scan-cancel": scan_cancel,
       "net-portscan": portscan, "net-preview": preview}


def start(ctx):
    return {"ready": True}


def stop(ctx):
    return {"stopped": True}

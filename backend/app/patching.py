"""
patching.py
-----------
Kernlogik des Software-Patchings. Der Router bleibt dadurch dünn und die
Auto-Patch-Engine benutzt exakt dieselben Funktionen wie ein manueller
Klick - Abweichungen zwischen "von Hand" und "automatisch" sind sonst
programmiert.

Regel-Auflösung (bewusst genau wie beim Agent-Auto-Update):
    clients.patch_policy = 'off'    -> nie automatisch
                         = 'on'     -> Client-Regel, sonst globale Regel
                         = 'global' -> nur wenn der globale Schalter an ist
Eine Regel mit scope='client' sticht immer die globale Regel.

Stufen, absteigend nach Dringlichkeit:
    security > critical > important > moderate > low > feature > other
"""

import time
import uuid

from app import db

LEVELS = ["security", "critical", "important", "moderate", "low", "feature", "other"]

# Deutsche Bezeichnungen für die Oberfläche. 'other' heißt bewusst nicht
# "Sonstiges", sondern benennt den Grund: winget & Co. liefern schlicht keine
# Einstufung mit. Wer das weiß, stellt seine Regeln anders ein.
LEVEL_LABELS = {
    "security": "Sicherheit",
    "critical": "Kritisch",
    "important": "Wichtig",
    "moderate": "Mittel",
    "low": "Niedrig",
    "feature": "Funktion",
    "other": "Ohne Einstufung",
}

LEVEL_LABELS = {
    "security": "Sicherheit",
    "critical": "Kritisch",
    "important": "Wichtig",
    "moderate": "Mittel",
    "low": "Niedrig",
    "feature": "Funktionsupdate",
    "other": "Sonstige",
}

SOURCE_LABELS = {
    "windows-update": "Windows Update",
    "winget": "winget (Anwendungen)",
    "apt": "APT",
    "dnf": "DNF",
}


def _conn():
    return db._conn


def _now() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------
# Bestand
# ------------------------------------------------------------------

def store_scan(client_id: str, patches: list[dict]) -> dict:
    """
    Ergebnis eines Scans übernehmen. Der Bestand des Clients wird ERSETZT,
    nicht ergänzt - sonst stehen längst installierte Updates ewig herum.

    Manuell ausgeschlossene Einträge ('excluded') überleben den Scan,
    damit ein Ausschluss nicht bei der nächsten Suche wieder auftaucht.
    """
    c = _conn()
    now = _now()
    excluded = {
        (r["source"], r["uid"]) for r in c.execute(
            "SELECT source, uid FROM patches WHERE client_id = ? AND status = 'excluded'",
            (client_id,)).fetchall()
    }
    c.execute("DELETE FROM patches WHERE client_id = ? AND status != 'excluded'",
              (client_id,))

    kept = 0
    for p in patches or []:
        uid = str(p.get("uid") or "").strip()
        source = str(p.get("source") or "").strip()
        if not uid or not source:
            continue
        if (source, uid) in excluded:
            continue
        level = p.get("level") if p.get("level") in LEVELS else "other"
        c.execute(
            "INSERT OR REPLACE INTO patches (id, client_id, uid, name, current_version,"
            " available_version, source, level, size, needs_reboot, status, error,"
            " found_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?)",
            (_new_id(), client_id, uid, str(p.get("name") or uid)[:300],
             str(p.get("current_version") or "")[:80],
             str(p.get("available_version") or "")[:80],
             source, level, int(p.get("size") or 0),
             1 if p.get("needs_reboot") else 0, now, now))
        kept += 1

    c.commit()
    return refresh_summary(client_id, scanned=True)


def refresh_summary(client_id: str, scanned: bool = False) -> dict:
    """
    Zusammenfassung auf den Client schreiben. Die Zahlen landen als Spalten
    in 'clients', weil list_clients() ein SELECT * macht - damit stehen sie
    ohne Zusatzabfrage in jedem Dashboard-Widget zur Verfügung.
    """
    row = _conn().execute(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN level = 'security' THEN 1 ELSE 0 END) AS sec,"
        " SUM(CASE WHEN level = 'critical' THEN 1 ELSE 0 END) AS crit,"
        " SUM(needs_reboot) AS reboot"
        " FROM patches WHERE client_id = ? AND status = 'pending'",
        (client_id,)).fetchone()
    total = row["total"] or 0
    sets = ("patch_count = ?, patch_security = ?, patch_critical = ?, patch_reboot = ?")
    params = [total, row["sec"] or 0, row["crit"] or 0, 1 if (row["reboot"] or 0) else 0]
    if scanned:
        sets += ", patch_last_scan = ?"
        params.append(_now())
    params.append(client_id)
    _conn().execute(f"UPDATE clients SET {sets} WHERE id = ?", params)
    _conn().commit()
    return {"total": total, "security": row["sec"] or 0,
            "critical": row["crit"] or 0, "needs_reboot": bool(row["reboot"])}


def list_patches(client_id: str | None = None, level: str | None = None,
                 status: str = "pending") -> list[dict]:
    sql = "SELECT p.*, c.hostname FROM patches p JOIN clients c ON c.id = p.client_id WHERE 1=1"
    params: list = []
    if client_id:
        sql += " AND p.client_id = ?"
        params.append(client_id)
    if level:
        sql += " AND p.level = ?"
        params.append(level)
    if status:
        sql += " AND p.status = ?"
        params.append(status)
    # Nach Dringlichkeit sortieren, nicht alphabetisch - der Anwender will
    # Sicherheitslücken oben sehen, nicht "Adobe" vor "Windows Update".
    order = " ".join(
        f"WHEN '{lvl}' THEN {i}" for i, lvl in enumerate(LEVELS))
    sql += f" ORDER BY CASE p.level {order} ELSE 99 END, c.hostname, p.name"
    return [dict(r) for r in _conn().execute(sql, params).fetchall()]


def overview() -> dict:
    """Flottenweite Zahlen: nach Stufe, nach Quelle, betroffene Clients."""
    c = _conn()
    by_level = {r["level"]: r["n"] for r in c.execute(
        "SELECT level, COUNT(*) AS n FROM patches WHERE status = 'pending'"
        " GROUP BY level").fetchall()}
    by_source = {r["source"]: r["n"] for r in c.execute(
        "SELECT source, COUNT(*) AS n FROM patches WHERE status = 'pending'"
        " GROUP BY source").fetchall()}
    per_client = [dict(r) for r in c.execute(
        "SELECT c.id, c.hostname, c.patch_count, c.patch_security, c.patch_critical,"
        " c.patch_last_scan, c.patch_policy"
        " FROM clients c WHERE c.active = 1 ORDER BY c.patch_security DESC,"
        " c.patch_count DESC, c.hostname").fetchall()]
    # Online-Status steht nicht in der Datenbank, sondern nur in der
    # Socket-Verbindungsliste - deshalb hier zur Laufzeit ergänzen.
    try:
        from app import sockets
        for entry in per_client:
            entry["online"] = sockets.state.is_online(entry["id"])
    except Exception:
        for entry in per_client:
            entry["online"] = False
    never = sum(1 for c2 in per_client if not c2.get("patch_last_scan"))
    return {
        "by_level": {lvl: by_level.get(lvl, 0) for lvl in LEVELS},
        "by_source": by_source,
        "clients": per_client,
        "total": sum(by_level.values()),
        "affected_clients": sum(1 for c2 in per_client if (c2.get("patch_count") or 0) > 0),
        "never_scanned": never,
    }


# ------------------------------------------------------------------
# Regeln
# ------------------------------------------------------------------

DEFAULT_RULE = {
    "enabled": 0, "levels": "security,critical", "sources": "",
    "window_start": "02:00", "window_end": "05:00", "weekdays": "1,2,3,4,5,6,7",
    "auto_reboot": 0, "exclusions": "", "scan_interval_hours": 24,
}


def get_rule(client_id: str | None = None) -> dict:
    """Regel holen. Ohne client_id die globale."""
    if client_id:
        row = _conn().execute(
            "SELECT * FROM patch_rules WHERE scope = 'client' AND client_id = ?",
            (client_id,)).fetchone()
    else:
        row = _conn().execute(
            "SELECT * FROM patch_rules WHERE scope = 'global'").fetchone()
    if row:
        return dict(row)
    return {"id": None, "scope": "client" if client_id else "global",
            "client_id": client_id, **DEFAULT_RULE}


def save_rule(client_id: str | None, values: dict) -> dict:
    scope = "client" if client_id else "global"
    existing = get_rule(client_id)
    merged = {**DEFAULT_RULE, **{k: v for k, v in existing.items()
                                 if k in DEFAULT_RULE}, **values}
    levels = ",".join(l for l in str(merged["levels"]).split(",") if l in LEVELS)
    now = _now()
    if existing.get("id"):
        _conn().execute(
            "UPDATE patch_rules SET enabled = ?, levels = ?, sources = ?,"
            " window_start = ?, window_end = ?, weekdays = ?, auto_reboot = ?,"
            " exclusions = ?, scan_interval_hours = ?, updated_at = ? WHERE id = ?",
            (int(bool(merged["enabled"])), levels, str(merged["sources"]),
             str(merged["window_start"]), str(merged["window_end"]),
             str(merged["weekdays"]), int(bool(merged["auto_reboot"])),
             str(merged["exclusions"]), int(merged["scan_interval_hours"] or 24),
             now, existing["id"]))
    else:
        _conn().execute(
            "INSERT INTO patch_rules (id, scope, client_id, enabled, levels, sources,"
            " window_start, window_end, weekdays, auto_reboot, exclusions,"
            " scan_interval_hours, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_new_id(), scope, client_id, int(bool(merged["enabled"])), levels,
             str(merged["sources"]), str(merged["window_start"]),
             str(merged["window_end"]), str(merged["weekdays"]),
             int(bool(merged["auto_reboot"])), str(merged["exclusions"]),
             int(merged["scan_interval_hours"] or 24), now, now))
    _conn().commit()
    return get_rule(client_id)


def delete_rule(client_id: str) -> None:
    _conn().execute("DELETE FROM patch_rules WHERE scope = 'client' AND client_id = ?",
                    (client_id,))
    _conn().commit()


def effective_rule(client_id: str) -> dict | None:
    """
    Welche Regel gilt für diesen Client - oder None, wenn nicht automatisch
    gepatcht werden soll. Gleiche Logik wie beim Agent-Auto-Update.
    """
    client = db.get_client(client_id)
    if not client or not client.get("active"):
        return None
    policy = (client.get("patch_policy") or "global").lower()
    if policy == "off":
        return None

    own = _conn().execute(
        "SELECT * FROM patch_rules WHERE scope = 'client' AND client_id = ?",
        (client_id,)).fetchone()
    if own:
        rule = dict(own)
        return rule if rule["enabled"] else None

    if policy == "global" and db.get_setting("patch_auto_enabled", "0") != "1":
        return None
    glob = _conn().execute("SELECT * FROM patch_rules WHERE scope = 'global'").fetchone()
    if not glob:
        return None
    rule = dict(glob)
    if not rule["enabled"]:
        return None
    # policy='on' heißt: dieser Client patcht automatisch, auch wenn der
    # globale Schalter aus ist - solange eine globale Regel definiert ist.
    return rule


def in_window(rule: dict, now: time.struct_time | None = None) -> bool:
    """
    Läuft gerade das Wartungsfenster? Fenster über Mitternacht (z.B. 22:00
    bis 04:00) werden korrekt behandelt - das ist der Normalfall bei
    Wartungsarbeiten und geht mit einem naiven Vergleich schief.
    """
    now = now or time.localtime()
    weekday = str(now.tm_wday + 1)          # 1 = Montag
    allowed = [d.strip() for d in (rule.get("weekdays") or "").split(",") if d.strip()]
    if allowed and weekday not in allowed:
        return False

    def mins(text: str, fallback: int) -> int:
        try:
            h, m = str(text).split(":")
            return int(h) * 60 + int(m)
        except (ValueError, AttributeError):
            return fallback

    start = mins(rule.get("window_start"), 120)
    end = mins(rule.get("window_end"), 300)
    cur = now.tm_hour * 60 + now.tm_min
    if start == end:
        return True                          # Fenster = ganzer Tag
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end         # über Mitternacht


def selectable(client_id: str, rule: dict) -> list[dict]:
    """Welche offenen Patches deckt diese Regel ab?"""
    levels = {l.strip() for l in (rule.get("levels") or "").split(",") if l.strip()}
    sources = {s.strip() for s in (rule.get("sources") or "").split(",") if s.strip()}
    excl = {e.strip().lower() for e in (rule.get("exclusions") or "").split(",") if e.strip()}
    out = []
    for p in list_patches(client_id=client_id, status="pending"):
        if levels and p["level"] not in levels:
            continue
        if sources and p["source"] not in sources:
            continue
        if p["uid"].lower() in excl or p["name"].lower() in excl:
            continue
        out.append(p)
    return out


# ------------------------------------------------------------------
# Verlauf
# ------------------------------------------------------------------

def start_run(client_id: str, trigger: str, actor: str, requested: int) -> str:
    rid = _new_id()
    _conn().execute(
        "INSERT INTO patch_runs (id, client_id, trigger, actor, requested,"
        " installed, failed, needs_reboot, detail, started_at, finished_at)"
        " VALUES (?, ?, ?, ?, ?, 0, 0, 0, '', ?, NULL)",
        (rid, client_id, trigger, actor, requested, _now()))
    _conn().commit()
    return rid


def finish_run(run_id: str, installed: int, failed: int, reboot: bool, detail: str) -> None:
    _conn().execute(
        "UPDATE patch_runs SET installed = ?, failed = ?, needs_reboot = ?,"
        " detail = ?, finished_at = ? WHERE id = ?",
        (installed, failed, 1 if reboot else 0, detail[:2000], _now(), run_id))
    _conn().commit()


def list_runs(client_id: str | None = None, limit: int = 50) -> list[dict]:
    sql = ("SELECT r.*, c.hostname FROM patch_runs r"
           " LEFT JOIN clients c ON c.id = r.client_id")
    params: list = []
    if client_id:
        sql += " WHERE r.client_id = ?"
        params.append(client_id)
    sql += " ORDER BY r.started_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in _conn().execute(sql, params).fetchall()]


def apply_result(client_id: str, items: list[dict], result: dict) -> None:
    """Ergebnis einer Installation in den Bestand zurückschreiben."""
    installed = {str(n).lower() for n in (result.get("installed") or [])}
    failed = {str(f.get("name", "")).lower(): f.get("error", "")
              for f in (result.get("failed") or [])}
    now = _now()
    for it in items:
        name = str(it.get("name") or "").lower()
        uid = it.get("uid")
        if name in failed:
            _conn().execute(
                "UPDATE patches SET status = 'failed', error = ?, updated_at = ?"
                " WHERE client_id = ? AND source = ? AND uid = ?",
                (str(failed[name])[:500], now, client_id, it.get("source"), uid))
        elif name in installed or not failed:
            _conn().execute(
                "UPDATE patches SET status = 'installed', error = '', updated_at = ?"
                " WHERE client_id = ? AND source = ? AND uid = ?",
                (now, client_id, it.get("source"), uid))
    _conn().commit()
    refresh_summary(client_id)


# ------------------------------------------------------------------
# Automatik
# ------------------------------------------------------------------

async def run_auto_cycle() -> dict:
    """
    Ein Durchlauf der Automatik.

    Pro Client wird höchstens EINE Sache erledigt (scannen ODER installieren),
    damit ein zäher Rechner die restliche Flotte nicht blockiert - beim
    nächsten Durchlauf kommt der Rest dran. Fehler eines Clients werden
    protokolliert, brechen den Durchlauf aber nie ab: ein Gerät, das mitten
    im Scan offline geht, darf die anderen nicht anhalten.
    """
    import asyncio
    from app import sockets

    scanned = patched = errors = 0

    for client in db.list_clients():
        cid = client["id"]
        # Nur verbundene Clients - alles andere läuft in einen Timeout.
        if not sockets.state.is_online(cid):
            continue
        rule = effective_rule(cid)
        if not rule:
            continue

        try:
            interval_ms = max(1, int(rule.get("scan_interval_hours") or 24)) * 3600_000
            last_scan = int(client.get("patch_last_scan") or 0)
            if _now() - last_scan > interval_ms:
                result = await sockets.request_patch_scan(cid)
                store_scan(cid, result.get("patches") or [])
                scanned += 1
                continue                     # Installieren beim nächsten Lauf

            if not in_window(rule):
                continue
            items = selectable(cid, rule)
            if not items:
                continue

            payload = [{"uid": p["uid"], "source": p["source"], "name": p["name"]}
                       for p in items]
            run_id = start_run(cid, "auto", "System", len(payload))
            result = await sockets.request_patch_apply(cid, payload)
            installed = result.get("installed") or []
            failed = result.get("failed") or []
            detail = "; ".join(
                [f"OK: {n}" for n in installed[:25]]
                + [f"FEHLER: {f.get('name')} - {str(f.get('error', ''))[:120]}"
                   for f in failed[:25]])
            finish_run(run_id, len(installed), len(failed),
                       bool(result.get("needs_reboot")), detail)
            apply_result(cid, payload, result)
            patched += 1

            db.add_audit_entry(
                "system", "patch.auto_applied", target=client.get("hostname"),
                details=f"{len(installed)} installiert, {len(failed)} fehlgeschlagen")

            # Neustart nur, wenn er ausdrücklich erlaubt wurde. Ein
            # unangekündigter Reboot mitten in der Arbeitszeit richtet mehr
            # Schaden an als ein Update, das eine Nacht länger wartet.
            if result.get("needs_reboot") and rule.get("auto_reboot"):
                try:
                    await sockets.request_exec(cid, _reboot_command(client),
                                               timeout_seconds=30)
                    db.add_audit_entry("system", "patch.auto_reboot",
                                       target=client.get("hostname"),
                                       details="Neustart nach Aktualisierung angefordert")
                except Exception as e:
                    print(f"[patch] Neustart von {cid} fehlgeschlagen: {e}")
        except Exception as e:
            errors += 1
            print(f"[patch] Client {client.get('hostname') or cid}: {e}")

    return {"scanned": scanned, "patched": patched, "errors": errors}


def _reboot_command(client: dict) -> str:
    """Neustart mit Vorlaufzeit, damit am Gerät noch gespeichert werden kann."""
    osr = (client.get("os_release") or "").lower()
    if "windows" in osr:
        return ('shutdown /r /t 120 /c '
                '"Neustart zum Abschluss automatischer Aktualisierungen"')
    return "shutdown -r +2 'Neustart zum Abschluss automatischer Aktualisierungen'"


async def engine():
    """
    Hintergrund-Schleife der Automatik. Alle 10 Minuten - fein genug, um ein
    Wartungsfenster von zwei Stunden sicher zu treffen, grob genug, um die
    Agenten nicht mit Scans zu überziehen.
    """
    import asyncio
    await asyncio.sleep(180)          # Start-Ansturm abwarten
    while True:
        try:
            res = await run_auto_cycle()
            if res["scanned"] or res["patched"] or res["errors"]:
                print(f"[patch] Automatik: {res}")
        except Exception as e:
            print(f"[patch] Automatik fehlgeschlagen: {e}")
        await asyncio.sleep(600)

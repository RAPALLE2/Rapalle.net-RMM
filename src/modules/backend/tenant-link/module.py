# module.py (Modul "tenant-link") - Einbettung durch ein Haupt-RMM erlauben
# -----------------------------------------------------------------------
# Wichtig: Eine Standard-RMM-Instanz setzt KEIN X-Frame-Options/CSP, daher ist das
# Dashboard ohnehin einbettbar und der Autologin laeuft ueber die normale Login-API.
# Dieses Modul dokumentiert/bestaetigt die Freigabe und haelt die erlaubten
# Haupt-RMM-Adressen (Einstellung "parents") fuer die Oberflaeche bereit. Es versucht
# zusaetzlich - nur falls es frueh genug im Start laeuft - eine Middleware zu setzen,
# die frame-ancestors auf die erlaubten Adressen BESCHRAENKT (haertere Sicherheit).
from app import modules

_tried = {"done": False}


def parents() -> list[str]:
    try:
        s = modules.get_settings("tenant-link") or {}
    except Exception:
        s = {}
    raw = (s.get("parents") or "").strip()
    return [p.strip().rstrip("/") for p in raw.replace("\n", ",").split(",") if p.strip()]


def _try_middleware(ctx):
    """Best effort: nur moeglich, solange die App noch nicht gestartet ist."""
    if _tried["done"]:
        return
    _tried["done"] = True
    api = modules._api
    if api is None:
        return
    try:
        @api.middleware("http")
        async def _frame(request, call_next):
            resp = await call_next(request)
            if not modules.is_enabled("tenant-link"):
                return resp
            ps = parents()
            anc = "frame-ancestors 'self'" + ("" if not ps else " " + " ".join(ps))
            csp = resp.headers.get("Content-Security-Policy", "")
            if "frame-ancestors" not in csp:
                resp.headers["Content-Security-Policy"] = (csp + " " + anc).strip()
            resp.headers.pop("X-Frame-Options", None)
            return resp
        ctx.log("Einbettung aktiv gesteuert (frame-ancestors gesetzt)")
    except Exception:
        # App laeuft schon -> Middleware nicht mehr setzbar. Einbettung klappt
        # trotzdem, weil eine Standard-RMM-Instanz kein X-Frame-Options/CSP setzt.
        pass


def on_startup(ctx):
    _try_middleware(ctx)
    ps = parents()
    ctx.log("Mandant-Freigabe aktiv. Einbettung durch: "
            + (", ".join(ps) if ps else "alle Haupt-RMM (keine Einschränkung gesetzt)"))

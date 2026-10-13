# module.py (Modul "spaces")
# --------------------------
# Persoenliche Spaces, Teamspaces und Public Spaces mit Freigaben und Git.
# Beim Start: Ordner anlegen und den frueheren gemeinsamen Relay-Ordner
# "Storage" in den Public Space "Storage" ueberfuehren (store.migrate_legacy_storage).
#
# Andere Module/Teile nutzen die Spaces ueber modules.use("spaces", "vfs") bzw.
# die REST-API /api/spaces (Relay, Explorer, Notizheft, Chat, Tickets, Audioplayer).

from . import store


def on_startup(ctx):
    store.ensure_dirs()
    try:
        store.migrate_legacy_storage()
    except Exception as exc:
        ctx.log(f"Übernahme des alten Storage-Ordners fehlgeschlagen: {exc}")


def on_enable(ctx):
    on_startup(ctx)

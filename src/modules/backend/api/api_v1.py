# api_v1.py (Modul "api") - haengt die REST-API aus src/backend/api ein.
# Der Code der API liegt bewusst NICHT im Modul, sondern in src/backend/api;
# der Core laedt ihn nie selbst - nur dieses Modul, wenn es aktiv ist.
from api.routes import router  # noqa: F401

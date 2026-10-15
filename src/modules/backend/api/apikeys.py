# apikeys.py (Modul "api") - API-Schluessel fuer den Core erreichbar machen
# (users_routes holt sie ueber modules.use("api", "apikeys") beim Anlegen eines API-Kontos).
from api.keys import accounts, ensure_table, generate, is_api_account, revoke_user, verify  # noqa: F401

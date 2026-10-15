"""
REST-API des RAPALLE.net RMM (/api/v1).

Dieser Ordner wird NICHT vom Core geladen. Das Modul "api"
(src/modules/backend/api) haengt die Router ein, wenn es aktiviert ist:
  routes.router        /api/v1/...     die eigentliche API
  routes.admin_router  /api/apimod/... Verwaltung (Tokens, Protokoll) fuer die Modul-Einstellungen

Dateien:
  security.py  Betriebsarten (open | user | secure), Tokens/Sitzungen, Benutzer pro Anfrage
  bridge.py    Bruecke zu ALLEN Dashboard-Routen (gleiche Rechtepruefung wie im Dashboard)
  store.py     Tabellen api_tokens / api_log, Einstellungen, IP-Freigabe
"""

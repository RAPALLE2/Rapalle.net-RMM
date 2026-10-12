# Node-Teil des Beispiel-Moduls

Dieser Ordner wird vom Backend an eine **Node** geschickt, wenn das Modul `example` aktiv ist und im
Backend-Teil (oder über die API `POST /api/nodes/<client>/modules/example`) `deploy` aufgerufen wird.

* `module.json` → Abschnitt `node` beschreibt, **wie** die Node das Modul ausführt (`kind: compose` oder `python`).
* `docker-compose.yml` → wird mit `docker compose -p rmm-example up -d` gestartet.

Mehr dazu: `src/modules/backend/example/GUIDE.md`, Abschnitt „Node-Teil“.

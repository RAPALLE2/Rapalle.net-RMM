# Server-Update & Struktur-Migration

Das Server-Update (Einstellungen → Allgemein → Update, oder Auto-Update) tauscht den
Code **komplett** aus – gelöschte Dateien verschwinden also auch auf dem Server – und
passt alte Ordnerstrukturen automatisch an.

## Ablauf

1. **Laden (Backend läuft noch):** Das ZIP des gewählten Stands wird nach
   `<root>/.rmm_update/<job>/release.zip` geladen und geprüft (enthält eine
   `update.json` mit `"rmm_layout": true` und die Startdatei). Fehler hier ändern nichts.
2. **Übergabe:** Der Backend-Prozess ersetzt sich per `os.execv` durch den Updater
   (`src/backend/app/updater.py`, nur Standardbibliothek, als Kopie in `/tmp`).
   Das RMM ist damit aus. Die Prozess-ID bleibt gleich – systemd, Docker oder die
   Windows-Aufgabenplanung beenden den Updater deshalb nicht.
3. **Sichern:** DB (inkl. `-wal`/`-shm`), `.env`, `repo.txt`, `dbconfig.json`,
   `modules.json`, `agent/.env` werden zusätzlich in
   `.rmm_update/<job>/backup/important-*.zip` kopiert.
4. **Daten wegsichern:** Alle Einträge aus `update.json → items` werden von ihrem
   **aktuellen** Ort in den Arbeitsordner verschoben (große Ordner wie Aufzeichnungen
   werden verschoben, nicht kopiert).
5. **Alten Code wegräumen:** Alles, was zum RMM gehört (Einträge des neuen Release,
   `managed_legacy`, zuletzt installierte Einträge), wandert nach
   `.rmm_update/<job>/old/`. Fremde Dateien im Projektordner, `.git`, `venv`/`.venv`,
   die laufende Python-Umgebung und Mount-Punkte bleiben unangetastet.
6. **Einsetzen:** Das neue Release wird an seinen Platz gelegt.
7. **Daten zurücklegen:** Jede Datei/jeder Ordner kommt an den Ort, den die
   `update.json` des **neuen** Release vorgibt. Release-Dateien in Datenordnern
   (`version.txt`, `requirements.txt`, `update.json`) kommen aus dem Release.
8. **Starten:** `main_py_backend_location` des neuen Release wird gestartet.

Schlägt Schritt 3–7 fehl, wird **jede** Verschiebung rückgängig gemacht und der alte
Stand wieder gestartet. Das Ergebnis steht in `.rmm_update/last_result.json`, im
Audit-Log (`server.update_completed` / `server.update_rolled_back`) und in den
Einstellungen. Protokoll: `.rmm_update/<job>/update.log` und
`src/backend/logs/backend/update.log`. Die letzten 2 Update-Ordner bleiben zum
manuellen Zurückspielen liegen.

## update.json (`src/backend/data/update.json`)

```json
{
  "rmm_layout": true,
  "main_py_backend_location": "src/backend/main.py",
  "keep": [".git", ".env", "venv", ".venv"],
  "managed_legacy": ["src", "backend", "frontend", "..."],
  "items": {
    "db_file_location":    {"path": "src/backend/data/data.sqlite", "kind": "file",
                            "companions": ["-wal", "-shm"], "backup": true,
                            "legacy": ["backend/data.sqlite"]},
    "recordings_location": {"path": "src/backend/media/recordings", "kind": "dir",
                            "legacy": ["backend/recordings"]}
  }
}
```

* `path` – Ort in **diesem** Release. Wird ein Ort in einem späteren Release
  geändert, reicht es, `path` anzupassen und den alten Ort unter `legacy` einzutragen.
* `legacy` – frühere Orte. Ohne installierte `update.json` (Alt-Installation) haben sie
  Vorrang vor `path`; mehrere vorhandene Ordner werden zusammengeführt, bei Dateien
  gewinnt der wichtigste Ort, die anderen bleiben als Duplikat im Arbeitsordner.
* `release_owned` – Dateien in einem Datenordner, die immer aus dem Release kommen.
* Die installierte Kopie bekommt einen Block `installed` (Stand, Zeit, verwaltete
  Einträge, Daten-Orte) – er ist die Grundlage für das nächste Update.

## Alte Installationen (`<root>/backend/main.py`)

Der alte Updater kopiert das neue Release nur drüber und startet danach wieder
`backend/main.py`. Genau dort liegt jetzt ein **Kompatibilitäts-Starter**
(`backend/main.py`, `backend/run.py`): Er erkennt die alte Struktur, lädt den gerade
eingespielten Stand noch einmal sauber von GitHub (offline: nimmt den bereits
hineinkopierten Code) und migriert wie oben. Danach leitet er bei jedem Start nur
noch an `src/backend/main.py` weiter – ein Dienst, der weiterhin `backend/main.py`
startet, muss nicht angepasst werden.

Für Alt-Installationen reicht also: **einmal ganz normal updaten.**

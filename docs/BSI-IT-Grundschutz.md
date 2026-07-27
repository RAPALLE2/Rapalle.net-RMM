# IT-Grundschutz (BSI) — Einordnung und Umsetzungsstand

## Das Wichtigste zuerst

**Eine Software kann nicht „BSI-konform" sein.** Konformität nach IT-Grundschutz
bezieht sich immer auf einen *Informationsverbund* — also eine Organisation mit
ihren Prozessen, Menschen, Räumen und Systemen. Zertifiziert wird nach
*ISO 27001 auf Basis von IT-Grundschutz*, geprüft durch beim BSI zertifizierte
Auditoren. Gegenstand der Prüfung ist nie ein einzelnes Programm.

Für Produkte gibt es andere Verfahren (Common Criteria, BSZ) — die sind
aufwendig, teuer und für ein selbst gehostetes RMM in aller Regel nicht der
richtige Weg.

Was diese Software leisten kann und soll: **die technischen Anforderungen
erfüllbar machen**, damit sie in einem nach IT-Grundschutz abgesicherten
Verbund betrieben werden darf. Genau das dokumentiert diese Datei.

## Grundlagen und aktueller Stand

- BSI-Standards 200-1 (ISMS), 200-2 (Methodik: Basis-, Kern-, Standard-Absicherung),
  200-3 (Risikoanalyse), 200-4 (Business Continuity)
- IT-Grundschutz-Kompendium, rund 100 Bausteine in zehn Schichten
- Seit Januar 2026 löst **IT-Grundschutz++** das bisherige Kompendium ab:
  maschinenlesbares Regelwerk, deutlich gestraffte Anforderungen, mehrjährige
  Übergangsfrist. Bestehende Modellierungen nach Edition 2023/2024/2025 bleiben
  in der Übergangszeit gültig.
- Quelle: <https://www.bsi.bund.de/DE/Themen/Unternehmen-und-Organisationen/Standards-und-Zertifizierung/IT-Grundschutz/it-grundschutz_node.html>

Prüfe vor einem Audit den tagesaktuellen Stand — die Reform läuft.

## Welche Bausteine sind für dieses RMM einschlägig?

Ein RMM ist eine **Administrationsanwendung mit sehr hohem Schutzbedarf**: Wer
es kontrolliert, kontrolliert alle verwalteten Clients. Für die Modellierung
ist das der entscheidende Punkt — der Schutzbedarf vererbt sich nach dem
Maximumprinzip von den verwalteten Systemen auf das RMM, nicht umgekehrt.

| Baustein | Thema | Relevanz |
| --- | --- | --- |
| APP.3.1 | Webanwendungen | Dashboard und API |
| OPS.1.1.2 | Ordnungsgemäße IT-Administration | Kernthema: Fernwartung |
| OPS.1.1.5 | Protokollierung | Audit-Log |
| OPS.1.1.3 | Patch- und Änderungsmanagement | Update-Funktion, Agent-Updates |
| ORP.4 | Identitäts- und Berechtigungsmanagement | Benutzer, Gruppen, Rechte |
| CON.1 | Kryptokonzept | TLS, Passwort-Hashing, gespeicherte Geheimnisse |
| CON.3 | Datensicherung | Datenbank, Aufzeichnungen |
| SYS.1.6 | Containerisierung | Docker-Betrieb |
| DER.1 / DER.2.1 | Erkennung und Behandlung von Sicherheitsvorfällen | Alarme, Audit |
| NET.1.1 | Netzarchitektur | Erreichbarkeit des Servers, Agent-Verbindungen |

## Umsetzungsstand

Legende: **[S]** von der Software geleistet · **[T]** teilweise, Konfiguration
nötig · **[O]** rein organisatorisch, außerhalb der Software

### ORP.4 — Identitäten und Berechtigungen

- **[S]** Rollen- und Rechtekonzept mit feingranularen Rechten je Benutzer,
  Gruppe und Client-Bereich; Trennung globaler und clientbezogener Rechte
- **[S]** Passwort-Hashing mit bcrypt (kein reversibles Verfahren)
- **[S]** Passwort-Richtlinie, serverseitig erzwungen —
  `app/security_policy.py`, einstellbar über die Einstellungen
  (Standard: 12 Zeichen, 3 Zeichenarten, keine Trivialpasswörter,
  Benutzername darf nicht enthalten sein)
- **[S]** Sperre nach mehreren Fehlversuchen, je Konto **und** je Quell-IP
  (Standard: 5 Versuche in 15 Minuten → 15 Minuten Sperre), Audit-Eintrag
- **[S]** Einmalpasswort mit erzwungenem Wechsel beim ersten Login
- **[S]** Anbindung an Active Directory / LDAP (zentrale Identitäten)
- **[S]** **Zwei-Faktor-Anmeldung (TOTP, RFC 6238)** — jeder Benutzer richtet
  sie im Profil per QR-Code ein; Wiederherstellungscodes für den Verlustfall,
  jeder Code nur einmal verwendbar. Erfüllt die Forderung nach starker
  Authentisierung für administrative Zugänge.
- **[O]** **Pflicht zur 2FA** für Administratoren ist organisatorisch
  festzulegen — technisch erzwingt das RMM sie noch nicht.
- **[O]** Regelmäßige Rechteüberprüfung, Vier-Augen-Prinzip bei Adminrechten,
  Aus- und Eintrittsprozesse

### OPS.1.1.5 — Protokollierung

- **[S]** Audit-Log für Anmeldungen (erfolgreich, fehlgeschlagen, gesperrt),
  Rechteänderungen, Skriptausführung, Datei- und Datenbankzugriffe,
  Migration, Update
- **[S]** Aufzeichnung von Fernsitzungen (Replays)
- **[T]** Aufbewahrungsfristen und Auswertung sind zu konfigurieren
- **[T]** **Weiterleitung an ein zentrales Protokollierungssystem (SIEM) fehlt.**
  Bei hohem Schutzbedarf verlangt der Grundschutz, dass Protokolle nicht nur
  dort liegen, wo ein Angreifer sie ändern könnte. Bis dahin: Syslog-Export des
  Hosts nutzen oder das Audit-Log regelmäßig exportieren.
- **[O]** Festlegung, wer Protokolle auswertet, und Beteiligung des
  Personalrats / Datenschutzes (Mitarbeiterüberwachung!)

### OPS.1.1.2 — Ordnungsgemäße IT-Administration

- **[S]** Alle administrativen Aktionen laufen über nachvollziehbare,
  protokollierte Wege; Fernsitzungen können aufgezeichnet werden
- **[S]** Zustimmungsdialog auf dem Client vor dem Zugriff
- **[O]** Getrennte Administrationskonten, dokumentierte Vertretungsregelung,
  Schulung der Administratoren

### APP.3.1 — Webanwendungen

- **[S]** Authentisierung an jeder API-Schnittstelle, serverseitige
  Rechteprüfung (nicht nur in der Oberfläche)
- **[S]** Schutz gegen Pfad-Ausbruch bei Datei- und Archivfunktionen
- **[T]** **TLS wird nicht erzwungen.** Das Backend spricht HTTP; die
  Verschlüsselung muss ein vorgeschalteter Reverse Proxy leisten
  (nginx, Caddy, Traefik) — inklusive HSTS und aktueller Cipher-Suiten nach
  BSI TR-02102-2.
- **[O]** Regelmäßige Prüfung auf Schwachstellen, Penetrationstest vor
  Produktivbetrieb

### CON.1 — Kryptokonzept

- **[S]** bcrypt für Passwörter, Verschlüsselung gespeicherter Relay-Zugangsdaten
- **[T]** `AGENT_TOKEN` und `JWT_SECRET` liegen in der `.env` — Dateirechte
  einschränken, Datei nicht in Sicherungen im Klartext ablegen
- **[O]** Schlüsselwechsel-Konzept, Festlegung der zugelassenen Verfahren
  (Orientierung: BSI TR-02102)

### CON.3 — Datensicherung

- **[S]** Migrations-/Sicherungsfunktion (Settings → Source → Migration):
  vollständiger Stand inklusive Datenbank, Aufzeichnungen, Medien
- **[S]** Konsistenter Datenbankabzug im laufenden Betrieb
- **[O]** **Regelmäßigkeit, Auslagerung und Rücksicherungstest sind
  organisatorisch festzulegen.** Eine Sicherung, die nie zurückgespielt wurde,
  ist keine Sicherung.

### SYS.1.6 — Containerisierung

- **[S]** Image ohne unnötige Dienste, Betrieb unter eigener UID möglich
- **[T]** Der Docker-Socket ist **absichtlich nicht** eingehängt. Wer die
  Zusatzdienste im Dashboard nutzt, hebt damit faktisch die Trennung zum Host
  auf — das ist eine bewusste Risikoentscheidung und gehört dokumentiert.
- **[O]** Image-Herkunft, Signaturprüfung, regelmäßiges Neubauen

### DER.1 / DER.2.1 — Vorfälle erkennen und behandeln

- **[S]** Benachrichtigungen bei fehlgeschlagenen Anmeldungen und weiteren
  Ereignissen (Webhook, E-Mail)
- **[O]** Meldewege, Zuständigkeiten, Wiederanlaufpläne

## Was für eine Zertifizierung zusätzlich nötig ist

Rein organisatorisch — keine Zeile Code hilft hier:

1. Sicherheitsleitlinie und benannte Verantwortliche (ISB)
2. Strukturanalyse: Was gehört zum Informationsverbund?
3. Schutzbedarfsfeststellung für Vertraulichkeit, Integrität, Verfügbarkeit
4. Modellierung: passende Bausteine zuordnen
5. IT-Grundschutz-Check: Soll-Ist-Vergleich je Anforderung
6. Risikoanalyse nach BSI 200-3 für alles über „normal" hinaus
7. Realisierungsplan, Umsetzung, interne Audits
8. Zertifizierungsaudit durch einen zertifizierten Auditor

## Offene technische Punkte

Priorisiert nach Nutzen für einen Grundschutz-konformen Betrieb:

1. **Erzwingbare 2FA-Pflicht** je Rolle (die Funktion selbst ist umgesetzt)
2. **Protokollweiterleitung** an ein SIEM (Syslog/CEF)
3. **Sitzungslaufzeit** und automatische Abmeldung konfigurierbar machen
   (aktuell fest: 12 Stunden Token-Laufzeit)
4. **Erzwungenes HTTPS** inkl. HSTS, wenn kein Proxy davor steht
5. **Signierte Agent-Pakete** (Lieferketten-Sicherheit)

Diese Punkte sind bewusst offen dokumentiert statt stillschweigend übergangen —
ein Audit findet sie ohnehin, und eine ehrliche Lückenliste ist Teil eines
funktionierenden ISMS.

RAPALLE.net RMM — Bilder / Images
==================================

Lege deine eigenen Bilder in genau diesen Ordner (frontend/images/) und
benenne sie EXAKT so wie unten angegeben (Kleinbuchstaben, gleiche Endung).
Das Frontend lädt sie automatisch an den passenden Stellen.

BENÖTIGTE DATEIEN
-----------------

1) logo.png
   - Das Logo oben links in der Kopfleiste (neben dem Text "RAPALLE.net RMM").
   - Empfohlene Höhe: ca. 24-48 Pixel hoch (Breite beliebig).
   - Format: PNG mit transparentem Hintergrund sieht am besten aus.
   - Wenn diese Datei fehlt, wird das Logo einfach ausgeblendet (kein Fehler).

OPTIONAL (für später vorgesehen, aktuell noch nicht eingebunden)
----------------------------------------------------------------

2) favicon.ico
   - Das kleine Symbol im Browser-Tab.
   - Wenn du eins hast: hier ablegen, dann in frontend/index.html im <head>
     diese Zeile ergänzen:
     <link rel="icon" href="/images/favicon.ico" />

3) login-background.jpg
   - Ein Hintergrundbild für den Login-Bildschirm (falls gewünscht).
   - Muss noch im CSS aktiviert werden - sag Bescheid, wenn du das möchtest.

SO BENENNST DU DEINE DATEIEN UM
-------------------------------

Windows:
   - Rechtsklick auf die Datei -> "Umbenennen" -> exakt "logo.png" eintippen.
   - Achte darauf, dass die Dateiendung wirklich .png ist (nicht .png.png).

Linux/Mac:
   - mv dein-bild.png logo.png

WICHTIG
-------
- Die Namen müssen EXAKT stimmen (Groß-/Kleinschreibung zählt unter Linux!).
- Nach dem Ablegen: Browser-Seite neu laden (F5), ggf. mit Strg+F5 den
  Browser-Cache leeren, falls das alte Bild noch angezeigt wird.

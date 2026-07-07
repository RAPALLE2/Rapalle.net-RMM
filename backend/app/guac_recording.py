"""
guac_recording.py
-----------------
Nimmt eine Guacamole-Session (RDP/VNC/SSH über guacd) als Replay auf - im
GLEICHEN .jsonl-Format wie die Agent-Screen-Aufnahmen, damit der vorhandene
Player und der Audit-Link unverändert funktionieren.

Wie es funktioniert (bewusst einfach, "best effort"):
- guacd zeichnet den Desktop als Folge von Bild-Operationen: 'size' (Layer-
  Größe), 'img' + 'blob' + 'end' (ein PNG/JPEG-Kachel-Update an Position x,y).
- Wir dekodieren diese Kacheln mit Pillow und malen sie auf eine Leinwand
  (ein Bild pro Session). So entsteht serverseitig ein Gesamtbild.
- Von dieser Leinwand machen wir HÖCHSTENS ~5 Schnappschüsse pro Sekunde
  (Drosselung) und speichern sie in NIEDRIGSTER Qualität (JPEG q=25, halbe
  Auflösung). Das hält die Dateien klein - Replays sind zur Nachvollziehbarkeit
  gedacht, nicht als HD-Aufnahme.

Nur die Instruktionen, die wir fürs Bild brauchen, werden ausgewertet; alles
andere (Audio, Cursor, sync…) wird ignoriert. Kann Pillow einen Blob nicht
dekodieren, wird er übersprungen - die Aufnahme läuft weiter.

Ist Pillow nicht installiert, ist das Guac-Recording still deaktiviert
(kein Absturz des Tunnels).
"""

from __future__ import annotations

import base64
import io
import time

from app import recording as _rec
from app import db as _db

try:
    from PIL import Image
    _PIL_OK = True
except Exception:
    _PIL_OK = False


def _guac_record_params() -> tuple[float, int, float]:
    """Liest (min_frame_interval, jpeg_quality, downscale) aus den Settings."""
    fps = _db.get_float_setting("guac_record_fps") or 8.0
    fps = max(1.0, min(fps, 30.0))
    quality = _db.get_int_setting("guac_record_quality") or 50
    quality = max(1, min(quality, 95))
    scale = _db.get_float_setting("guac_record_scale") or 0.75
    scale = max(0.1, min(scale, 1.0))
    return (1.0 / fps, quality, scale)


class GuacSessionRecorder:
    """Baut aus dem guacd-Instruktionsstrom gedrosselte Frames und schreibt sie."""

    def __init__(self, client_id: str, client_hostname: str, username: str):
        self.client_id = client_id
        self.hostname = client_hostname
        self.username = username
        self.rec_id: str | None = None
        self.enabled = _PIL_OK

        # Aufnahme-Parameter aus den Einstellungen (einmal beim Start gelesen).
        self._min_interval, self._quality, self._scale = _guac_record_params()

        self._canvas = None            # PIL.Image der Standard-Ebene (Layer 0)
        self._w = 0
        self._h = 0
        self._last_frame_ts = 0.0
        self._dirty = False
        # Zwischenspeicher für laufende img/blob/end-Sequenzen: stream -> dict
        self._streams: dict[str, dict] = {}

    def start(self) -> str | None:
        if not self.enabled:
            return None
        self.rec_id = _rec.start_recording(self.client_id, self.hostname, self.username)
        return self.rec_id

    def stop(self) -> None:
        if not self.enabled or self.rec_id is None:
            return
        # Letzten Stand noch sichern, dann regulär beenden (oder leeres löschen).
        if self._dirty:
            self._emit_frame(force=True)
        _rec.abort_recording(self.client_id)  # löscht nur, falls 0 Frames

    # -- Instruktionsverarbeitung -------------------------------------------

    def feed(self, instruction: list[str]) -> None:
        """Verarbeitet EINE geparste Guacamole-Instruktion (Opcode + Args)."""
        if not self.enabled or not instruction:
            return
        op = instruction[0]
        try:
            if op == "size":
                self._on_size(instruction)
            elif op == "img":
                self._on_img(instruction)
            elif op == "blob":
                self._on_blob(instruction)
            elif op == "end":
                self._on_end(instruction)
            elif op == "sync":
                # guacd markiert mit 'sync' einen fertigen Frame -> guter Moment
                # für einen (gedrosselten) Schnappschuss.
                self._emit_frame()
        except Exception:
            # Ein einzelner kaputter Blob darf die Aufnahme nicht abreißen.
            pass

    def _on_size(self, instr: list[str]) -> None:
        # size,<layer>,<w>,<h>  - nur die Standard-Ebene (Layer "0") interessiert.
        if len(instr) < 4 or instr[1] != "0":
            return
        w, h = int(instr[2]), int(instr[3])
        if w <= 0 or h <= 0:
            return
        new = Image.new("RGB", (w, h), (0, 0, 0))
        if self._canvas is not None:
            new.paste(self._canvas, (0, 0))
        self._canvas, self._w, self._h = new, w, h

    def _on_img(self, instr: list[str]) -> None:
        # img,<stream>,<mask>,<layer>,<mimetype>,<x>,<y>
        if len(instr) < 7:
            return
        stream = instr[1]
        layer = instr[3]
        try:
            x, y = int(instr[5]), int(instr[6])
        except ValueError:
            return
        # Nur Zeichnungen auf die sichtbare Standard-Ebene aufnehmen.
        if layer != "0":
            self._streams.pop(stream, None)
            return
        self._streams[stream] = {"x": x, "y": y, "data": bytearray()}

    def _on_blob(self, instr: list[str]) -> None:
        # blob,<stream>,<base64-daten>
        if len(instr) < 3:
            return
        st = self._streams.get(instr[1])
        if st is None:
            return
        try:
            st["data"].extend(base64.b64decode(instr[2]))
        except Exception:
            self._streams.pop(instr[1], None)

    def _on_end(self, instr: list[str]) -> None:
        # end,<stream>  - Kachel fertig: dekodieren und auf die Leinwand malen.
        if len(instr) < 2:
            return
        st = self._streams.pop(instr[1], None)
        if not st or not st["data"] or self._canvas is None:
            return
        try:
            tile = Image.open(io.BytesIO(bytes(st["data"]))).convert("RGB")
            self._canvas.paste(tile, (st["x"], st["y"]))
            self._dirty = True
        except Exception:
            pass

    # -- Frame-Ausgabe (gedrosselt) -----------------------------------------

    def _emit_frame(self, force: bool = False) -> None:
        if self._canvas is None or not self._dirty:
            return
        now = time.time()
        if not force and (now - self._last_frame_ts) < self._min_interval:
            return  # Drosselung gemäß eingestellter FPS
        self._last_frame_ts = now
        self._dirty = False

        img = self._canvas
        if self._scale and self._scale != 1.0:
            img = img.resize(
                (max(1, int(self._w * self._scale)), max(1, int(self._h * self._scale)))
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self._quality)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        _rec.record_frame(self.client_id, b64, img.width, img.height)

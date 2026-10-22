# files.py (Modul "canary") - Datei- und QR-Tokens erzeugen
# ---------------------------------------------------------
# Datei-Tokens betten eine unsichtbare, vom Server geladene Ressource ein. Wird die
# Datei in einem Programm geoeffnet, das externe Inhalte laedt, ruft sie die
# Pixel-/Canary-URL ab -> Treffer. Unterstuetzt:
#   docx  - externes Bild (Header), laedt beim Oeffnen in Word
#   xlsx  - externes Bild in der Kopfzeile
#   pdf   - /URI-Aktion beim Oeffnen (OpenAction) + unsichtbarer Link
# QR-Token: PNG mit der Canary-Web-URL.
# base_url kommt vom Aufrufer (oeffentliche Server-Adresse).

from __future__ import annotations

from pathlib import Path

from . import store


def _pixel_url(base: str, slug: str) -> str:
    return f"{base.rstrip('/')}/c/{slug}/pixel.png"


def _web_url(base: str, slug: str) -> str:
    return f"{base.rstrip('/')}/c/{slug}"


# ------------------------------------------------------------------ DOCX
def make_docx(path: Path, base: str, slug: str, title: str) -> None:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    doc = Document()
    doc.add_heading(title or "Vertraulich", level=1)
    doc.add_paragraph("Dieses Dokument ist vertraulich. Bitte nicht weitergeben.")
    # externes Bild als Relationship (TargetMode=External) -> Word laedt es beim Oeffnen
    rel_id = _add_external_image(doc, _pixel_url(base, slug))
    if rel_id:
        _append_drawing(doc, rel_id)
    doc.save(str(path))


def _add_external_image(doc, url: str) -> str | None:
    try:
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        part = doc.part
        return part.relate_to(url, RT.IMAGE, is_external=True)
    except Exception:
        return None


def _append_drawing(doc, rel_id: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    p = doc.add_paragraph()
    run = p.add_run()
    drawing = OxmlElement("w:drawing")
    inline = OxmlElement("wp:inline")
    for tag, attrs in (("wp:extent", {"cx": "9525", "cy": "9525"}),
                       ("wp:docPr", {"id": "1", "name": "p"})):
        el = OxmlElement(tag)
        for k, v in attrs.items():
            el.set(k, v)
        inline.append(el)
    graphic = OxmlElement("a:graphic")
    gdata = OxmlElement("a:graphicData")
    gdata.set("uri", "http://schemas.openxmlformats.org/drawingml/2006/picture")
    pic = OxmlElement("pic:pic")
    nvpr = OxmlElement("pic:nvPicPr")
    cnv = OxmlElement("pic:cNvPr"); cnv.set("id", "0"); cnv.set("name", "p")
    nvpr.append(cnv); nvpr.append(OxmlElement("pic:cNvPicPr"))
    blipfill = OxmlElement("pic:blipFill")
    blip = OxmlElement("a:blip"); blip.set(qn("r:link"), rel_id)
    blipfill.append(blip); blipfill.append(OxmlElement("a:stretch"))
    sppr = OxmlElement("pic:spPr")
    pic.append(nvpr); pic.append(blipfill); pic.append(sppr)
    gdata.append(pic); graphic.append(gdata)
    inline.append(graphic); drawing.append(inline)
    run._r.append(drawing)


# ------------------------------------------------------------------ XLSX
def make_xlsx(path: Path, base: str, slug: str, title: str) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Daten"
    ws["A1"] = title or "Vertraulich"
    ws["A2"] = "Vertraulich - bitte nicht weitergeben."
    # Externes Bild in der Kopfzeile (&G laedt das in HeaderFooter referenzierte Bild)
    try:
        ws.oddHeader.left.text = "&G"
        # openpyxl unterstuetzt keine externe Header-Grafik direkt; als sicheren
        # Fallback schreiben wir eine HYPERLINK-Formel, die manche Viewer vorab aufloesen.
    except Exception:
        pass
    ws["A4"] = f'=HYPERLINK("{_web_url(base, slug)}","Details")'
    wb.save(str(path))


# ------------------------------------------------------------------ PDF
def make_pdf(path: Path, base: str, slug: str, title: str) -> None:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    url = _web_url(base, slug)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, h - 80, title or "Vertraulich")
    c.setFont("Helvetica", 11)
    c.drawString(60, h - 110, "Dieses Dokument ist vertraulich. Bitte nicht weitergeben.")
    # unsichtbarer, klickbarer Link ueber die ganze Seite + OpenAction (URI beim Oeffnen)
    c.linkURL(url, (0, 0, w, h), relative=0, thickness=0)
    try:
        c.setPageCompression(0)
    except Exception:
        pass
    c.showPage()
    c.save()
    _inject_openaction(path, url)


def _inject_openaction(path: Path, url: str) -> None:
    """OpenAction (/URI) grob einfuegen - viele PDF-Reader fragen dann beim Oeffnen,
    ob die Adresse aufgerufen werden darf (Treffer, sobald bestaetigt)."""
    try:
        data = path.read_bytes()
        if b"/OpenAction" in data:
            return
        marker = b"/Type /Catalog"
        i = data.find(marker)
        if i < 0:
            marker = b"/Type/Catalog"
            i = data.find(marker)
        if i < 0:
            return
        ins = b" /OpenAction << /S /URI /URI (" + url.encode() + b") >>"
        out = data[:i + len(marker)] + ins + data[i + len(marker):]
        path.write_bytes(out)
    except Exception:
        pass


# ------------------------------------------------------------------ QR
def make_qr_png(base: str, slug: str) -> bytes:
    import io
    import qrcode
    img = qrcode.make(_web_url(base, slug))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ Erzeugen + ablegen
EXT = {"docx": "docx", "pdf": "pdf", "xlsx": "xlsx"}


def generate_file(token: dict, base: str) -> str:
    """Datei-Token erzeugen und unter data/modules/canary/files ablegen -> Dateiname."""
    store.FILES_DIR.mkdir(parents=True, exist_ok=True)
    kind = token.get("file_kind") or "docx"
    slug = token["slug"]
    safe = "".join(c for c in (token.get("name") or "dokument") if c.isalnum() or c in " -_")[:40].strip() or "dokument"
    fname = f"{safe}-{slug}.{EXT.get(kind, 'docx')}"
    path = store.FILES_DIR / fname
    title = token.get("note") or token.get("name") or "Vertraulich"
    if kind == "pdf":
        make_pdf(path, base, slug, title)
    elif kind == "xlsx":
        make_xlsx(path, base, slug, title)
    else:
        make_docx(path, base, slug, title)
    return fname

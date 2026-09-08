"""PDF images + tables-by-default (doc gap fix, M24).

- Tables render as markdown tables with no flag (locked in).
- ``include_images=True`` (PDF + ``store=True``) saves raster figures
  into ``<stem>.files/`` with a ``## Figures`` section.
- Flag-combination errors are in-band JSON, like store+pages.
"""

import json
import zlib

import pytest

from gossamer.agent_tools import WebResearcherToolbox


def _toolbox(tmp_path) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"), domain_delay=0.0, ddgs_delay=0.0
    )


def _assemble(objs) -> bytes:
    buf = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(buf)
    buf += ("xref\n0 {}\n".format(len(objs) + 1)).encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += ("trailer\n<</Size {}/Root 1 0 R>>\n".format(len(objs) + 1)).encode()
    buf += ("startxref\n{}\n%%EOF\n".format(xref_pos)).encode()
    return buf


def _grid_pdf() -> bytes:
    """1-page PDF with a ruled 3x4 text grid (detectable table)."""
    content = ""
    rows = [["Name", "Age", "City"], ["Alice", "30", "Berlin"],
            ["Bob", "25", "Paris"], ["Carol", "41", "Rome"]]
    y = 700
    for r in rows:
        for i, c in enumerate(r):
            content += f"BT /F1 12 Tf {72 + i * 150} {y} Td ({c}) Tj ET\n"
        y -= 30
    for yy in (712, 682, 652, 622, 592):
        content += f"72 {yy} m 522 {yy} l S\n"
    for xx in (72, 222, 372, 522):
        content += f"{xx} 592 m {xx} 712 l S\n"
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids [3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox [0 0 612 792]/Contents 4 0 R/"
        b"Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n"
        + content.encode() + b"endstream\n",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    return _assemble(objs)


def _image_pdf() -> bytes:
    """1-page PDF with one 8x8 RGB raster image + a caption line.

    (pdf-oxide ignores tiny images below its size floor, so the
    fixture uses 8x8 rather than 2x2.)
    """
    raw = bytes(range(8 * 8 * 3))
    comp = zlib.compress(raw)
    content = (b"BT /F1 12 Tf 72 700 Td (Fig caption) Tj ET\n"
               b"q 100 0 0 100 72 500 cm /Im1 Do Q\n")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids [3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox [0 0 612 792]/Contents 4 0 R/"
        b"Resources<</Font<</F1 5 0 R>>/XObject<</Im1 6 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n"
        + content + b"endstream\n",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Type/XObject/Subtype/Image/Width 8/Height 8/"
        b"ColorSpace/DeviceRGB/BitsPerComponent 8/Length "
        + str(len(comp)).encode() + b"/Filter/FlateDecode>>\nstream\n"
        + comp + b"endstream\n",
    ]
    return _assemble(objs)


def test_tables_render_by_default(tmp_path):
    tb = _toolbox(tmp_path)
    md = tb._doc._extract_from_bytes(_grid_pdf(), "grid.pdf")
    assert "| Name | Age | City |" in md
    assert "| Alice | 30 | Berlin |" in md


def test_include_images_end_to_end(tmp_path):
    tb = _toolbox(tmp_path)
    pdf = tmp_path / "fig.pdf"
    pdf.write_bytes(_image_pdf())
    out = tmp_path / "out"
    data = json.loads(tb.extract_document(
        str(pdf), store=True, store_dir=str(out), include_images=True))
    stored = data["stored"]
    assert stored["resources"]["embedded"] == 1
    assert stored["resources"]["files"] == ["fig.files/page1_1.png"]
    assert (out / "fig.files" / "page1_1.png").exists()
    md = (out / "fig.md").read_text(encoding="utf-8")
    assert "## Figures" in md
    assert "./fig.files/page1_1.png" in md
    assert "Fig caption" in md


def test_images_off_by_default(tmp_path):
    tb = _toolbox(tmp_path)
    pdf = tmp_path / "fig.pdf"
    pdf.write_bytes(_image_pdf())
    out = tmp_path / "out"
    data = json.loads(tb.extract_document(
        str(pdf), store=True, store_dir=str(out)))
    assert data["stored"]["resources"]["embedded"] == 0
    assert not (out / "fig.files").exists()


def test_include_images_needs_store(tmp_path):
    tb = _toolbox(tmp_path)
    pdf = tmp_path / "fig.pdf"
    pdf.write_bytes(_image_pdf())
    data = json.loads(tb.extract_document(str(pdf), include_images=True))
    assert "requires store=True" in data["error"]


def test_include_images_rejects_structured(tmp_path):
    tb = _toolbox(tmp_path)
    pdf = tmp_path / "fig.pdf"
    pdf.write_bytes(_image_pdf())
    data = json.loads(tb.extract_document(
        str(pdf), structured=True, store=True, include_images=True))
    assert "structured=True" in data["error"]


def test_include_images_rejects_non_pdf(tmp_path):
    tb = _toolbox(tmp_path)
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    data = json.loads(tb.extract_document(
        str(txt), store=True, store_dir=str(tmp_path / "out"),
        include_images=True))
    assert "only supported for PDF" in data["error"]

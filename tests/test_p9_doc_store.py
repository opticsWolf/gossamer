"""P9: extract_document(store=...) — persist original bytes + extracted md.

When ``store=True`` the tool writes two files — the untouched document
bytes (``<stem><ext>``) and the full extracted text as markdown
(``<stem>.md``) — under ``store_dir`` (default ``stored_documents/``),
and reports the paths/sizes in the result's ``stored`` field. The stored
text is the *full* untruncated content even though the returned body is
budget-truncated. ``store`` cannot be combined with ``pages``.
"""

import json

import pytest

from gossamer import document as doc_mod
from gossamer.agent_tools import WebResearcherToolbox


def _toolbox(tmp_path) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"), domain_delay=0.0, ddgs_delay=0.0
    )


def _minimal_pdf() -> bytes:
    """A tiny but valid 1-page PDF (enough for the text extractor)."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids [3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox [0 0 612 792]/Contents 4 0 R/"
        b"Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length 54>>\nstream\nBT /F1 24 Tf 72 700 Td (Clean Test PDF) Tj ET\n"
        b"endstream\n",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
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


class TestSanitizeFilename:
    def test_collapses_separators_and_dotdirs(self):
        # Path(name).stem keeps the last segment, then sanitizes separators.
        assert doc_mod._sanitize_filename("a/b\\c") == "c"
        assert doc_mod._sanitize_filename("../etc/passwd") == "passwd"
        assert doc_mod._sanitize_filename("report (1).txt") == "report _1"

    def test_fallback_when_no_stem(self):
        assert doc_mod._sanitize_filename("/") == "document"
        assert doc_mod._sanitize_filename("") == "document"


class TestStoreLocalFile:
    def test_store_writes_original_and_markdown(self, tmp_path):
        out = tmp_path / "stored"
        src = tmp_path / "notes.txt"
        body = "hello world " * 40
        src.write_text(body, encoding="utf-8")

        res = json.loads(
            _toolbox(tmp_path).extract_document(
                str(src), store=True, store_dir=str(out)
            )
        )
        stored = res["stored"]
        assert stored["original"].endswith("notes.txt")
        assert stored["markdown"].endswith("notes.md")
        assert (tmp_path / "stored" / "notes.txt").read_bytes() == body.encode()
        assert (tmp_path / "stored" / "notes.md").read_text() == body
        assert stored["original_bytes"] == len(body.encode())
        assert stored["markdown_chars"] == len(body)

    def test_store_false_writes_nothing(self, tmp_path):
        out = tmp_path / "should_not_exist"
        src = tmp_path / "notes.txt"
        src.write_text("just text", encoding="utf-8")

        res = json.loads(
            _toolbox(tmp_path).extract_document(str(src), store_dir=str(out))
        )
        assert res.get("stored") is None
        assert not out.exists()

    def test_default_store_dir_is_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        src = tmp_path / "notes.txt"
        src.write_text("defaults", encoding="utf-8")
        _toolbox(tmp_path).extract_document(str(src), store=True)
        written = tmp_path / "stored_documents" / "notes.md"
        assert written.exists()
        assert written.read_text() == "defaults"


class TestStorePdf:
    def test_store_pdf_writes_bytes_and_heading(self, tmp_path):
        out = tmp_path / "pdf_out"
        src = tmp_path / "doc.pdf"
        src.write_bytes(_minimal_pdf())

        res = json.loads(
            _toolbox(tmp_path).extract_document(
                str(src), store=True, store_dir=str(out)
            )
        )
        stored = res["stored"]
        orig = tmp_path / "pdf_out" / "doc.pdf"
        assert orig.read_bytes()[:5] == b"%PDF-"
        assert orig.read_bytes() == src.read_bytes()
        assert "# Clean Test PDF" in (out / "doc.md").read_text(encoding="utf-8")


class TestStoreInvariants:
    def test_store_cannot_combine_with_pages(self, tmp_path):
        src = tmp_path / "notes.txt"
        src.write_text("x", encoding="utf-8")
        res = json.loads(
            _toolbox(tmp_path).extract_document(
                str(src), pages="1", store=True
            )
        )
        assert "error" in res
        assert "pages" in res["error"].lower()

    def test_stored_text_is_full_not_truncated(self, tmp_path):
        out = tmp_path / "big"
        src = tmp_path / "big.txt"
        body = "report body " * 2000  # ~24k chars, well over the 8000 budget
        src.write_text(body, encoding="utf-8")

        res = json.loads(
            _toolbox(tmp_path).extract_document(
                str(src), store=True, store_dir=str(out)
            )
        )
        # Returned body is budget-truncated...
        assert len(res["content"]) < len(body)
        # ...but the stored markdown is the full document.
        assert (out / "big.md").read_text() == body


class _FakeResourceStore:
    def __init__(self, *a, **k):
        pass

    def extract(self, *, markdown, base_url, out_dir, stem):
        # Simulate an HTML/office doc whose markdown referenced two images.
        rewritten = markdown.replace(
            "https://cdn.example.com/i/logo.png", f"./{stem}.files/logo.png"
        )
        return {
            "markdown": rewritten,
            "dir": f"{out_dir}/{stem}.files",
            "stem": stem,
            "referenced": 2,
            "files": [f"{stem}.files/logo.png"],
            "skipped": [],
        }


def test_store_includes_resources_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_mod, "ResourceStore", _FakeResourceStore)
    tb = _toolbox(tmp_path)
    # A local markdown file with an image ref exercises the store path.
    md_file = tmp_path / "notes.md"
    md_file.write_text(
        "# Notes\n\n![logo](https://cdn.example.com/i/logo.png)\n", encoding="utf-8"
    )
    out = tmp_path / "out"
    data = json.loads(tb.extract_document(str(md_file), store=True, store_dir=str(out)))
    stored = data["stored"]
    assert "resources" in stored
    assert stored["resources"]["referenced"] == 2
    assert stored["resources"]["files"] == ["notes.files/logo.png"]
    # The stored markdown was rewritten to the local resource path.
    md_path = tmp_path / "out" / "notes.md"
    assert "./notes.files/logo.png" in md_path.read_text(encoding="utf-8")
    assert "cdn.example.com" not in md_path.read_text(encoding="utf-8")


def test_store_without_resources_key_when_no_store(tmp_path):
    tb = _toolbox(tmp_path)
    md_file = tmp_path / "notes.md"
    md_file.write_text("# Notes\n", encoding="utf-8")
    data = json.loads(tb.extract_document(str(md_file), store=False))
    assert data.get("stored") is None

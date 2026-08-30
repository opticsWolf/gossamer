"""P2: pdf_oxide / office_oxide are optional (the ``documents`` extra).

The package must import fine without them — HTML research, search, and
token budgeting are unaffected — and only the document-parsing entry
points raise an actionable ``ImportError`` with an install hint.

We simulate a missing extra by patching the module-level class refs to
``None`` — exactly the state the lazy import leaves them in when the
extra isn't installed.
"""

import pytest

from stitch_web_researcher import structured_parser
from stitch_web_researcher.agent_tools import WebResearcherToolbox
from stitch_web_researcher.token_budget import count_tokens

EXTRA_RE = r"stitch-web-researcher\[documents\]"


def _toolbox(tmp_path) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"), domain_delay=0.0, ddgs_delay=0.0
    )


class TestRequireHelpers:
    def test_pdf_helper_raises_install_hint(self, monkeypatch):
        monkeypatch.setattr(structured_parser, "PdfDocument", None)
        with pytest.raises(ImportError, match=EXTRA_RE):
            structured_parser.require_pdf_oxide()

    def test_office_helper_raises_install_hint(self, monkeypatch):
        monkeypatch.setattr(structured_parser, "OfficeDoc", None)
        with pytest.raises(ImportError, match=EXTRA_RE):
            structured_parser.require_office_oxide()

    def test_helpers_return_classes_when_available(self):
        assert structured_parser.require_pdf_oxide() is not None
        assert structured_parser.require_office_oxide() is not None


class TestParseFileGracefulFailure:
    def test_parse_file_pdf_raises_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(structured_parser, "PdfDocument", None)
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(ImportError, match=EXTRA_RE):
            structured_parser.StructuredOxideParser().parse_file(f)

    def test_parse_file_docx_raises_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(structured_parser, "OfficeDoc", None)
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake")
        with pytest.raises(ImportError, match=EXTRA_RE):
            structured_parser.StructuredOxideParser().parse_file(f)


class TestToolboxGracefulFailure:
    def test_extract_from_bytes_raises_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(structured_parser, "PdfDocument", None)
        tb = _toolbox(tmp_path)
        with pytest.raises(ImportError, match="documents"):
            tb._doc._extract_from_bytes(b"", "report.pdf")

    def test_non_document_functionality_unaffected(self, tmp_path, monkeypatch):
        # With both extractors "missing", the toolbox still constructs and
        # non-document helpers keep working.
        monkeypatch.setattr(structured_parser, "PdfDocument", None)
        monkeypatch.setattr(structured_parser, "OfficeDoc", None)
        tb = _toolbox(tmp_path)
        assert tb.domain_delay == 0.0
        assert count_tokens("hello world") > 0

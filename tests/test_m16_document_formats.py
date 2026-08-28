# tests/test_m16_document_formats.py
"""M16 — classify_link must only advertise formats extract_document
can really deliver.

The review found DOCUMENT_EXTENSIONS advertised .doc/.xls/.ppt/.odt/
.ods/.odp/.rtf/.csv/.epub, so the model was sent to extract_document
for formats that raise ValueError. Fix: narrow the advertised set to
pdf + OOXML + plain text (CSV/TXT/MD handlers added), and answer
known-but-unparseable binary formats with an actionable conversion
hint. Tier 3.10 (item 10) extended the deliverable set with JSON,
XML, and RSS/Atom handlers; the invariant below is unchanged.
"""

import json

import pytest

from stitch_web_researcher.agent_tools import (
    ToolboxConfig,
    WebResearcherToolbox,
)
from stitch_web_researcher.structured_parser import (
    DOCUMENT_EXTENSIONS,
    classify_link,
)

# What _extract_from_bytes can genuinely deliver (M16 + Tier 3.10).
SUPPORTED = {
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".csv", ".txt", ".md",
    ".json", ".xml", ".rss", ".atom",
}
LEGACY = [".doc", ".xls", ".ppt", ".odt", ".ods", ".odp", ".rtf", ".epub"]


def _toolbox(tmp_path):
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            cache_dir=str(tmp_path / "cache"),
        )
    )


class TestClassifyLink:
    @pytest.mark.parametrize("ext", sorted(SUPPORTED))
    def test_supported_formats_classify_as_document(self, ext):
        assert classify_link(f"https://example.com/files/report{ext}") == "document"

    @pytest.mark.parametrize("ext", LEGACY)
    def test_unsupported_formats_classify_as_page(self, ext):
        assert classify_link(f"https://example.com/files/legacy{ext}") == "page"

    def test_extension_match_is_case_insensitive(self):
        assert classify_link("https://example.com/files/Report.PDF") == "document"

    def test_extensionless_url_is_page(self):
        assert classify_link("https://example.com/about") == "page"


class TestAdvertiseOnlyWhatYouCanDeliver:
    def test_advertised_set_is_subset_of_extractionsafe_set(self):
        # The M16 invariant: classify_link must never promise a format
        # that _extract_from_bytes raises on.
        assert DOCUMENT_EXTENSIONS <= SUPPORTED


class TestPlainTextExtraction:
    @pytest.mark.parametrize(
        ("ext", "content"),
        [
            ("csv", "a,b\n1,2\n"),
            ("txt", "hello world\n"),
            ("md", "# Title\n\ntext\n"),
        ],
    )
    def test_text_formats_roundtrip(self, tmp_path, ext, content):
        tb = _toolbox(tmp_path)
        path = tmp_path / f"file_{ext}.{ext}"
        # write_bytes: write_text would translate \n to \r\n on Windows.
        path.write_bytes(content.encode("utf-8"))
        data = json.loads(tb.extract_document(str(path)))
        assert data["content"] == content
        assert data["cache_hit"] is False

    def test_utf8_bom_is_stripped(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "bom.csv"
        path.write_bytes(b"\xef\xbb\xbf" + b"a,b\n1,2\n")
        data = json.loads(tb.extract_document(str(path)))
        assert data["content"].startswith("a,b")


class TestActionableErrors:
    @pytest.mark.parametrize("ext", LEGACY)
    def test_known_binary_format_gives_conversion_hint(self, tmp_path, ext):
        tb = _toolbox(tmp_path)
        path = tmp_path / f"legacy{ext}"
        path.write_bytes(b"binary payload")
        data = json.loads(tb.extract_document(str(path)))
        assert f"Unsupported document format: {ext}" in data["error"]
        assert "convert" in data["error"]

    def test_unknown_format_keeps_generic_message(self, tmp_path):
        tb = _toolbox(tmp_path)
        path = tmp_path / "mystery.xyz"
        path.write_bytes(b"??")
        data = json.loads(tb.extract_document(str(path)))
        assert "Unsupported document format: .xyz" in data["error"]

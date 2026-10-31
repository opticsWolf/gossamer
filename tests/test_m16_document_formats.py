# tests/test_m16_document_formats.py
"""M16 — classify_link must only advertise formats extract_document
can really deliver.

The review found DOCUMENT_EXTENSIONS advertised .doc/.xls/.ppt/.odt/
.ods/.odp/.rtf/.csv/.epub, so the model was sent to extract_document
for formats that raise ValueError. Fix: narrow the advertised set to
pdf + OOXML + plain text (CSV/TXT/MD handlers added), and answer
known-but-unparseable binary formats with an actionable conversion
hint. Tier 3.10 (item 10) extended the deliverable set with JSON,
XML, and RSS/Atom handlers; office-oxide >= 0.1.16 added legacy
.doc/.xls/.ppt/.xlsb reads, so those four moved back into the
advertised set (fixtures: Apache POI test-data, Apache-2.0).
The invariant below is unchanged.
"""

import json

import pytest

from gossamer.agent_tools import (
    ToolboxConfig,
    WebResearcherToolbox,
)
from gossamer.structured_parser import (
    DOCUMENT_EXTENSIONS,
    classify_link,
)

# What _extract_from_bytes can genuinely deliver (M16 + Tier 3.10 +
# office-oxide >= 0.1.16 legacy reads).
SUPPORTED = {
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".doc", ".xls", ".ppt", ".xlsb",
    ".csv", ".txt", ".md",
    ".json", ".xml", ".rss", ".atom",
}
LEGACY = [".odt", ".ods", ".odp", ".rtf", ".epub"]


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


class TestLegacyOfficeExtraction:
    # Real legacy binaries (Apache POI test-data, Apache-2.0):
    # Word 6/95 .doc, BIFF8 .xls, BIFF12 .xlsb, PPT.
    LEGACY_FIXTURES = {
        "legacy-word6.doc": "quick brown fox",
        "legacy-provinces.xls": "Provinces in China",
        "legacy-simple.xlsb": "Microsoft Excel 2007 Beta",
        "legacy-textbox.ppt": "Hello, World",
    }

    @pytest.mark.parametrize("fixture,needle", list(LEGACY_FIXTURES.items()))
    def test_legacy_office_extracts_text(self, tmp_path, fixture, needle):
        pytest.importorskip("office_oxide")
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(f"tests/fixtures/{fixture}"))
        assert "error" not in data, data.get("error")
        assert needle in data["content"]

    def test_legacy_xls_tables_as_csv(self, tmp_path):
        pytest.importorskip("office_oxide")
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(
            "tests/fixtures/legacy-provinces.xls", tables_as="csv",
        ))
        assert "error" not in data, data.get("error")
        assert "Provinces in China" in data["content"]
        assert "|" not in data["content"].splitlines()[2]

    def test_legacy_xls_tables_as_json(self, tmp_path):
        pytest.importorskip("office_oxide")
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(
            "tests/fixtures/legacy-provinces.xls", tables_as="json",
        ))
        assert "error" not in data, data.get("error")
        tables = json.loads(data["content"])
        assert tables and tables[0]["sheet"] == "provinces"

    def test_legacy_ppt_pages_still_refused(self, tmp_path):
        pytest.importorskip("office_oxide")
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(
            "tests/fixtures/legacy-textbox.ppt", pages="1-2",
        ))
        assert "Page selection" in data["error"]

    def test_legacy_xls_pages_select_sheets(self, tmp_path):
        pytest.importorskip("office_oxide")
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(
            "tests/fixtures/legacy-provinces.xls", pages="1",
        ))
        assert "error" not in data, data.get("error")
        assert "Provinces in China" in data["content"]


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

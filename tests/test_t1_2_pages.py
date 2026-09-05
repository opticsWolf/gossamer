# tests/test_t1_2_pages.py
"""Tier 1.2 — chunked/resumable reads: page-range selection.

A 400-page PDF used to be a single 8000-char answer with no way to ask
for more. extract_document now accepts a 1-based pages range
('10', '10-20', '10-', '-20') and serves it from the structured
parser's per-page data, cached under a range-specific key so range
reads and whole-document reads do not collide.
"""

import json

import pytest

import gossamer.agent_tools as agent_tools
from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.structured_parser import parse_page_range


def _build_pdf(pages):
    """Build a minimal valid multi-page PDF (one text line per page)."""
    objs = []
    n = len(pages)
    page_ids = [3 + 2 * i for i in range(n)]
    content_ids = [4 + 2 * i for i in range(n)]
    font_id = 3 + 2 * n
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = b" ".join(f"{pid} 0 R".encode() for pid in page_ids)
    objs.append(
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(n).encode() + b" >>"
    )
    for i in range(n):
        objs.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents " + str(content_ids[i]).encode() + b" 0 R "
            b"/Resources << /Font << /F1 " + str(font_id).encode() + b" 0 R >> >> >>"
        )
        stream = b"BT /F1 24 Tf 72 720 Td (" + pages[i].encode() + b") Tj ET"
        objs.append(
            b"<< /Length " + str(len(stream)).encode()
            + b" >> stream\n" + stream + b"\nendstream"
        )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objs) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_pos).encode()
        + b"\n%%EOF"
    )
    return bytes(out)


PDF_BYTES = _build_pdf(
    ["ALPHA page one content", "BETA page two content", "GAMMA page three"]
)


@pytest.fixture()
def pdf_path(tmp_path):
    path = tmp_path / "mini.pdf"
    path.write_bytes(PDF_BYTES)
    return str(path)


def _toolbox(tmp_path):
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False, cache_dir=str(tmp_path / "cache")
        )
    )


class TestParsePageRange:
    def test_single_page(self):
        assert parse_page_range("10") == (10, 10)

    def test_closed_range(self):
        assert parse_page_range("10-20") == (10, 20)

    def test_open_ended_tail(self):
        assert parse_page_range("10-") == (10, None)

    def test_open_ended_head(self):
        assert parse_page_range("-20") == (1, 20)

    def test_whitespace_tolerated(self):
        assert parse_page_range(" 10 - 20 ") == (10, 20)

    @pytest.mark.parametrize(
        "spec", ["abc", "0", "5-2", "1-2-3", "1.5", "-0", "10--20"]
    )
    def test_invalid_specs_raise(self, spec):
        with pytest.raises(ValueError, match="Invalid page range"):
            parse_page_range(spec)


class TestPageRangeExtractionLocal:
    def test_full_read_unchanged_without_pages(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(pdf_path))
        assert "ALPHA" in data["content"] and "GAMMA" in data["content"]
        assert data["page_range"] is None
        assert data["total_pages"] == 0

    def test_single_page(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(pdf_path, pages="2"))
        assert data["content"] == "# BETA page two content"
        assert data["page_range"] == "2"
        assert data["page_start"] == 2
        assert data["page_end"] == 2
        assert data["total_pages"] == 3
        assert "ALPHA" not in data["content"]
        assert "GAMMA" not in data["content"]

    def test_closed_range(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(pdf_path, pages="1-2"))
        assert "ALPHA" in data["content"]
        assert "BETA" in data["content"]
        assert "GAMMA" not in data["content"]
        assert (data["page_start"], data["page_end"]) == (1, 2)

    def test_open_ended_tail_clamps_to_end(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(pdf_path, pages="2-"))
        assert "BETA" in data["content"] and "GAMMA" in data["content"]
        assert (data["page_start"], data["page_end"]) == (2, 3)

    def test_open_ended_head(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(pdf_path, pages="-2"))
        assert "ALPHA" in data["content"] and "BETA" in data["content"]
        assert "GAMMA" not in data["content"]

    def test_end_beyond_document_clamps(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(pdf_path, pages="2-20"))
        assert "BETA" in data["content"] and "GAMMA" in data["content"]
        assert (data["page_start"], data["page_end"]) == (2, 3)

    def test_start_beyond_document_is_an_error(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(pdf_path, pages="5"))
        assert "error" in data
        assert "out of bounds" in data["error"]
        assert "3 page" in data["error"]

    def test_invalid_spec_is_actionable(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(pdf_path, pages="abc"))
        assert "error" in data
        assert "Invalid page range" in data["error"]

    def test_range_reads_are_cached(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        first = json.loads(tb.extract_document(pdf_path, pages="2"))
        second = json.loads(tb.extract_document(pdf_path, pages="2"))
        assert first["cache_hit"] is False
        assert second["cache_hit"] is True
        assert second["content"] == first["content"]

    def test_range_and_full_reads_do_not_collide(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        tb.extract_document(pdf_path, pages="2")
        full = json.loads(tb.extract_document(pdf_path))
        assert "ALPHA" in full["content"] and "GAMMA" in full["content"]
        assert full["cache_hit"] is False  # full read never served from range key

    def test_distinct_ranges_are_distinct_entries(self, pdf_path, tmp_path):
        tb = _toolbox(tmp_path)
        tb.extract_document(pdf_path, pages="2")
        third = json.loads(tb.extract_document(pdf_path, pages="3"))
        assert third["cache_hit"] is False
        assert third["content"] == "# GAMMA page three"

    def test_budget_applies_to_range_reads(self, pdf_path, tmp_path):
        tb = WebResearcherToolbox(
            config=ToolboxConfig(
                respect_robots=False,
                cache_dir=str(tmp_path / "cache"),
                max_markdown_chars=30,
            )
        )
        data = json.loads(tb.extract_document(pdf_path, pages="1-3"))
        assert len(data["content"]) <= 30 + 20  # plus the truncation marker


class TestPageRangeFormatGuards:
    def test_text_format_refuses_page_selection(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_bytes(b"hello world")
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(str(path), pages="1"))
        assert "error" in data
        assert "PDF (pages) and XLSX (sheets)" in data["error"]
        assert ".txt" in data["error"]


class TestPageRangeOverUrl:
    def test_url_range_read_downloads_once_then_caches(
        self, tmp_path, monkeypatch
    ):
        calls = []

        class _FakeResponse:
            content = PDF_BYTES
            url = "https://example.com/big.pdf"
            status_code = 200
            headers = {"content-type": "application/pdf"}

            def raise_for_status(self):
                return None

            def iter_bytes(self, chunk_size=None):
                for i in range(0, len(self.content), 16):
                    yield self.content[i : i + 16]

        class _FakeStream(_FakeResponse):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, headers=None):
                calls.append(url)
                return _FakeResponse()

            def stream(self, method, url, headers=None):
                calls.append(url)
                return _FakeStream()

        monkeypatch.setattr(agent_tools.httpx, "Client", _FakeClient)
        tb = _toolbox(tmp_path)
        url = "https://example.com/big.pdf"
        first = json.loads(tb.extract_document(url, pages="2"))
        second = json.loads(tb.extract_document(url, pages="2"))
        assert first["content"] == "# BETA page two content"
        assert first["total_pages"] == 3
        assert second["cache_hit"] is True
        assert len(calls) == 1  # the second read came from cache

    def test_url_out_of_bounds_errors_without_crash(self, tmp_path, monkeypatch):
        class _FakeResponse:
            content = PDF_BYTES
            url = "https://example.com/big.pdf"
            status_code = 200
            headers = {"content-type": "application/pdf"}

            def raise_for_status(self):
                return None

            def iter_bytes(self, chunk_size=None):
                for i in range(0, len(self.content), 16):
                    yield self.content[i : i + 16]

        class _FakeStream(_FakeResponse):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class _FakeClient:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, headers=None):
                return _FakeResponse()

            def stream(self, method, url, headers=None):
                return _FakeStream()

        monkeypatch.setattr(agent_tools.httpx, "Client", _FakeClient)
        tb = _toolbox(tmp_path)
        data = json.loads(
            tb.extract_document("https://example.com/big.pdf", pages="99")
        )
        assert "error" in data
        assert "out of bounds" in data["error"]


class TestToolRegistryAdvertisesPages:
    def test_extract_document_spec_includes_pages_param(self):
        from gossamer.agent_tools import TOOL_REGISTRY

        spec = next(s for s in TOOL_REGISTRY if s.name == "extract_document")
        param = next(p for p in spec.params if p.name == "pages")
        assert param.required is False
        schema = spec.llm_definition()["function"]["parameters"]
        assert "pages" in schema["properties"]
        assert "pages" not in schema["required"]
        assert "source" in schema["required"]

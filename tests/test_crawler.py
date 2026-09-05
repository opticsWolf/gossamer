"""Tests for the High-Performance LLM Web Researcher."""
import json
import time
from datetime import datetime
from pathlib import Path

import pytest
from gossamer.token_budget import count_tokens
from gossamer import (
    fetch_and_extract,
    batch_research,
    fetch_smart_page,
    WebResearcherToolbox,
    StructuredOxideParser,
    ParsedDocumentPayload,
    DocumentMetadata,
    ExtractedPage,
    ExtractedTable,
)


# ============================================
# P9: local HTTP server (deterministic fetch tests)
# ============================================
# Tests that only need "a page to fetch" run against a local server
# instead of example.com/httpbin.org.  Truly live tests (search
# providers, browser rendering) are marked @pytest.mark.slow and are
# excluded from the default run (pyproject: -m 'not slow').
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ALLOW_PRIVATE_ENV = "GOSSAMER_ALLOW_PRIVATE"


def _page_html(slug: str, port: int) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<title>Local Test Page {slug}</title>
<meta name="description" content="Deterministic local test page {slug}.">
<meta property="og:title" content="OG {slug}">
</head>
<body>
<article>
<h1>Local page {slug}</h1>
<p>Stable content served by the P9 local HTTP fixture.</p>
<p>Second paragraph, for markdown extraction.</p>
<a href="/page-two">second page</a>
<a href="http://127.0.0.1:{port}/page-three">third page</a>
</article>
</body>
</html>
"""


class _LocalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # keep test output clean

    def do_GET(self):
        port = self.server.server_address[1]
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            ctype = "text/plain"
        else:
            slug = self.path.strip("/") or "root"
            body = _page_html(slug, port).encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def local_server():
    """Local HTTP server standing in for the open web (P9)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module", autouse=True)
def _allow_local_origin():
    """The S1 SSRF guard blocks loopback by default; its documented
    escape hatch (see ssrf.py) lets these tests hit the local server."""
    old = os.environ.get(_ALLOW_PRIVATE_ENV)
    os.environ[_ALLOW_PRIVATE_ENV] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(_ALLOW_PRIVATE_ENV, None)
        else:
            os.environ[_ALLOW_PRIVATE_ENV] = old


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Rust Core Tests
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestRustCore:
    """Tests for the Rust core bindings."""

    def test_fetch_single(self, local_server):
        """Test fetching a page (local server, P9)."""
        md, links = fetch_and_extract(f"{local_server}/alpha")
        assert isinstance(md, str)
        assert "alpha" in md.lower()
        assert isinstance(links, list)

    def test_fetch_nonexistent_domain(self):
        """Test error handling for invalid URLs."""
        with pytest.raises(Exception):
            fetch_and_extract("https://this-domain-definitely-does-not-exist-12345.com")

    def test_batch_research(self, local_server):
        """Test batch fetching multiple pages (local server, P9)."""
        urls = [f"{local_server}/alpha", f"{local_server}/beta"]
        results = batch_research(urls)
        assert len(results) == 2

        for url, html_opt, md_opt, links_opt, _prov in results:
            assert isinstance(url, str)
            if md_opt is not None and links_opt is not None:
                assert isinstance(md_opt, str)
                assert isinstance(links_opt, list)
                assert md_opt  # local server always yields markdown
                # Bugfix 5: the raw HTML rides along so batch entries can
                # carry the same metadata single-page reads do.
                assert isinstance(html_opt, str) and html_opt

    @pytest.mark.slow
    def test_fetch_smart_page(self, local_server):
        """Smart fetch (stealth browser when available, static fallback).
        May launch browser_oxide, so marked slow (P9)."""
        md, links, metadata = fetch_smart_page(f"{local_server}/alpha")
        assert isinstance(md, str)
        assert "alpha" in md.lower()
        assert isinstance(links, list)
        assert isinstance(metadata, dict)

    def test_fetch_smart_page_nonexistent(self):
        """Test smart page fetch on nonexistent domain (both smart+static fail)."""
        with pytest.raises(Exception):
            fetch_smart_page("https://this-domain-definitely-does-not-exist-12345.com")

    def test_shared_runtime_reuse(self, local_server):
        """Verify that multiple calls share the same Tokio runtime (no cold-start penalty)."""
        url = f"{local_server}/warm"
        start = time.time()
        for _ in range(3):
            fetch_and_extract(url)
        elapsed = time.time() - start
        # 3 sequential local fetches with shared runtime: well under 15s
        assert elapsed < 15


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Toolbox Tests
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestToolbox:
    """Tests for the Python toolbox layer."""

    @pytest.fixture
    def toolbox(self, tmp_path):
        """Create a fresh toolbox with temporary cache."""
        return WebResearcherToolbox(
            cache_dir=str(tmp_path / "cache"),
            cache_ttl_seconds=60,
            ddgs_delay=0.1,
            domain_delay=0.1,
        )

    @pytest.mark.slow
    def test_search_web(self, toolbox):
        """Test DuckDuckGo search (live provider — slow, P9)."""
        result = toolbox.search_web("rust programming language", max_results=2)
        data = json.loads(result)
        if isinstance(data, dict) and "error" in data:
            pytest.skip(f"live search provider unavailable: {data['error']}")
        assert isinstance(data, list)
        assert 0 < len(data) <= 2
        assert all("url" in r for r in data)

    def test_inspect_html(self, toolbox, local_server):
        """Test HTML page inspection (local server, P9)."""
        result = toolbox.inspect_html_page(f"{local_server}/alpha")
        data = json.loads(result)
        assert "error" not in data
        assert "markdown" in data
        assert "follow_up_links" in data
        assert isinstance(data["markdown"], str)
        assert "alpha" in data["markdown"]
        assert len(data["follow_up_links"]) >= 1

    @pytest.mark.slow
    def test_inspect_html_smart(self, toolbox, local_server):
        """use_smart='browser' (stealth first, static fallback). May launch
        browser_oxide, so marked slow (P9)."""
        result = toolbox.inspect_html_page(f"{local_server}/alpha", use_smart="browser")
        data = json.loads(result)
        assert "error" not in data
        assert "markdown" in data
        assert "fetch_method" in data
        # "browser" when stealth fetch works (needs browser_oxide),
        # "static" when it fell back.
        assert data["fetch_method"] in ("browser", "smart", "static")

    def test_visited_deduplication(self, toolbox, local_server):
        """Repeat visits to fetched URLs are served from cache (C3):
        a successfully fetched URL is re-served with cache_hit=true instead
        of a content-free warning; a failed URL is retried, not blacklisted.
        (local server, P9)"""
        url = f"{local_server}/alpha"
        first = json.loads(toolbox.inspect_html_page(url))
        assert "error" not in first
        result2 = toolbox.inspect_html_page(url)
        data2 = json.loads(result2)
        assert "warning" not in data2
        assert data2["cache_hit"] is True
        assert data2["markdown"] == first["markdown"]

    def test_llm_definitions(self, toolbox):
        """Test LLM tool definitions schema."""
        defs = toolbox.get_llm_definitions()
        assert isinstance(defs, list)
        assert len(defs) >= 5

        names = [d["function"]["name"] for d in defs]
        assert "web_search" in names
        assert "inspect_html_page" in names
        assert "batch_inspect_pages" in names
        assert "extract_document" in names

        for d in defs:
            assert "type" in d
            assert "function" in d
            assert "parameters" in d["function"]

    def test_stats(self, toolbox):
        """Test statistics reporting."""
        stats = json.loads(toolbox.get_stats())
        assert stats["visited_urls_count"] == 0
        assert "cache" in stats
        assert "memory_entries" in stats["cache"]

    def test_reset_visited(self, toolbox, local_server):
        """Test resetting visited URLs (local server, P9)."""
        toolbox.inspect_html_page(f"{local_server}/alpha")
        assert len(toolbox.visited_urls) > 0
        toolbox.reset_visited()
        stats = json.loads(toolbox.get_stats())
        assert stats["visited_urls_count"] == 0

    def test_batch_inspect(self, toolbox, local_server):
        """Test batch page inspection (local server, P9)."""
        urls = [f"{local_server}/alpha", f"{local_server}/beta"]
        result = toolbox.batch_inspect_pages(urls)
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 2
        for item in data:
            assert "url" in item
            assert "error" not in item

    def test_url_validation(self, toolbox):
        """Malformed URLs are refused — as JSON, not as an exception.

        The refusal reaches the model through the tool's normal error
        channel so it can pick another link (bugfix 3)."""
        for bad in ("not-a-valid-url", "ftp://example.com/file.txt"):
            data = json.loads(toolbox.inspect_html_page(bad))
            assert "URL rejected" in data["error"], bad

    def test_caching(self, toolbox, tmp_path, local_server):
        """inspect_html_page caches fetches; repeats are served from cache
        (local server, P9)."""
        url = f"{local_server}/alpha"

        # First call hits the network and populates both cache tiers.
        result1 = json.loads(toolbox.inspect_html_page(url))
        assert url in toolbox.visited_urls
        assert result1["cache_hit"] is False

        cache_files = list(Path(toolbox.cache.cache_path).glob("*.meta"))
        assert len(cache_files) >= 1

        # Bypass the visited-URL guard: the repeat must come from cache.
        toolbox.visited_urls.clear()
        toolbox._domain_last_seen.clear()
        result2 = json.loads(toolbox.inspect_html_page(url))
        assert result2["cache_hit"] is True

    def test_domain_rate_limiting(self, toolbox, local_server):
        """Per-domain rate limiting applies to distinct-URL real fetches
        (local server, P9)."""
        start = time.time()
        toolbox.inspect_html_page(f"{local_server}/rate-a")
        toolbox.inspect_html_page(f"{local_server}/rate-b")
        elapsed = time.time() - start
        # Should take at least domain_delay (0.1s) between requests
        assert elapsed >= 0.08  # small tolerance

    @pytest.mark.slow
    def test_retry_decorator(self):
        """Retry decorator on a live search (slow, P9)."""
        tb = WebResearcherToolbox(ddgs_delay=0.0)
        result = tb.search_web("test query that should work")
        data = json.loads(result)
        assert "error" in data or isinstance(data, list)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Async Tests
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.asyncio
class TestAsyncToolbox:
    """Tests for async variants."""

    @pytest.mark.slow
    async def test_search_web_async(self):
        """Live DuckDuckGo search (slow, P9)."""
        tb = WebResearcherToolbox(ddgs_delay=0.1)
        result = await tb.search_web_async("python programming", max_results=2)
        data = json.loads(result)
        assert "error" in data or isinstance(data, list)

    async def test_inspect_html_async(self, local_server):
        tb = WebResearcherToolbox(domain_delay=0.1)
        result = await tb.inspect_html_page_async(f"{local_server}/alpha")
        data = json.loads(result)
        assert "error" not in data
        assert "markdown" in data
        assert "alpha" in data["markdown"]


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Pydantic Schema Tests
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestPydanticSchemas:
    """Verify Pydantic v2 schemas validate and serialize correctly."""

    def test_document_metadata_minimal(self):
        meta = DocumentMetadata(
            file_name="test.pdf",
            file_size_bytes=1024,
            format="pdf",
        )
        assert meta.file_name == "test.pdf"
        assert meta.page_count == 1  # default
        assert meta.extra_meta == {}

    def test_document_metadata_full(self):
        meta = DocumentMetadata(
            file_name="report.pdf",
            file_size_bytes=42000,
            format="pdf",
            title="Annual Report",
            author="Jane Doe",
            created_at=datetime(2024, 1, 15, 10, 30, 0),
            modified_at=datetime(2024, 1, 16, 14, 0, 0),
            page_count=12,
            extra_meta={"keywords": "finance, annual"},
        )
        assert meta.title == "Annual Report"
        assert meta.page_count == 12
        assert meta.extra_meta["keywords"] == "finance, annual"

    def test_extracted_table_valid(self):
        tbl = ExtractedTable(
            name="Q1_Sales",
            headers=["Month", "Revenue", "Units"],
            rows=[
                ["Jan", "$10k", "100"],
                ["Feb", "$12k", "120"],
            ],
        )
        assert tbl.headers == ["Month", "Revenue", "Units"]
        assert len(tbl.rows) == 2

    def test_extracted_table_empty(self):
        tbl = ExtractedTable(name="empty")
        assert tbl.headers == []
        assert tbl.rows == []

    def test_extracted_table_invalid_rows(self):
        """Rows must be lists of lists."""
        with pytest.raises(Exception):
            ExtractedTable(
                name="bad",
                headers=["A"],
                rows=["not_a_list"],
            )

    def test_extracted_page(self):
        page = ExtractedPage(
            page_number=3,
            raw_text="Hello world",
            markdown="# Hello world",
        )
        assert page.page_number == 3
        assert page.tables == []

    def test_parsed_document_payload(self):
        payload = ParsedDocumentPayload(
            metadata=DocumentMetadata(
                file_name="test.pdf",
                file_size_bytes=5000,
                format="pdf",
                page_count=2,
            ),
            pages=[
                ExtractedPage(
                    page_number=1,
                    raw_text="Page 1 text",
                    markdown="# Page 1",
                ),
                ExtractedPage(
                    page_number=2,
                    raw_text="Page 2 text",
                    markdown="## Page 2",
                    tables=[
                        ExtractedTable(
                            name="summary",
                            headers=["Key", "Value"],
                            rows=[["Total", "42"]],
                        )
                    ],
                ),
            ],
            tables=[
                ExtractedTable(
                    name="summary",
                    headers=["Key", "Value"],
                    rows=[["Total", "42"]],
                )
            ],
        )

        # Verify JSON serialization
        json_str = payload.to_json()
        assert "test.pdf" in json_str
        assert "Page 1" in json_str
        assert "summary" in json_str

        # Verify round-trip via model_validate_json
        round_tripped = ParsedDocumentPayload.model_validate_json(json_str)
        assert round_tripped.metadata.file_name == "test.pdf"
        assert len(round_tripped.pages) == 2

    def test_payload_to_json_indent(self):
        payload = ParsedDocumentPayload(
            metadata=DocumentMetadata(
                file_name="x.pdf", file_size_bytes=100, format="pdf"
            )
        )
        pretty = payload.to_json(indent=4)
        compact = payload.to_json(indent=None)
        assert len(pretty) > len(compact)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# StructuredOxideParser Tests
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestStructuredOxideParser:
    """Verify the StructuredOxideParser entry points and error handling."""

    def test_parse_nonexistent_file(self, tmp_path):
        parser = StructuredOxideParser()
        with pytest.raises(FileNotFoundError):
            parser.parse_file(tmp_path / "nonexistent_report.pdf")

    def test_parse_unsupported_format(self, tmp_path):
        parser = StructuredOxideParser()
        fake_file = tmp_path / "data.xyz"
        fake_file.write_bytes(b"not a real file")
        with pytest.raises(ValueError) as exc_info:
            parser.parse_file(fake_file)
        assert "Unsupported" in str(exc_info.value)

    def test_parse_unsupported_no_suffix(self, tmp_path):
        parser = StructuredOxideParser()
        fake_file = tmp_path / "README"
        fake_file.write_text("hello")
        with pytest.raises(ValueError):
            parser.parse_file(fake_file)

    def test_parser_is_reusable(self):
        """Parser instance can be called multiple times."""
        parser = StructuredOxideParser()
        with pytest.raises(FileNotFoundError):
            parser.parse_file("/no/such/file1.pdf")
        with pytest.raises(FileNotFoundError):
            parser.parse_file("/no/such/file2.pdf")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# XMP Datetime Parser Tests
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestXmpDatetimeParser:
    """Test the _parse_xmp_datetime helper."""

    def test_iso_8601_zulu(self):
        from gossamer.structured_parser import _parse_xmp_datetime

        dt = _parse_xmp_datetime("2024-01-15T10:30:00Z")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_iso_8601_with_tz(self):
        from gossamer.structured_parser import _parse_xmp_datetime

        dt = _parse_xmp_datetime("2024-01-15T10:30:00+05:00")
        assert dt is not None
        assert dt.year == 2024

    def test_date_only(self):
        from gossamer.structured_parser import _parse_xmp_datetime

        dt = _parse_xmp_datetime("2024-01-15")
        assert dt is not None
        assert dt.day == 15

    def test_garbage_returns_none(self):
        from gossamer.structured_parser import _parse_xmp_datetime

        assert _parse_xmp_datetime("not-a-date") is None
        assert _parse_xmp_datetime("") is None
        assert _parse_xmp_datetime(None) is None


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Token Budget Tests
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestTokenBudget:
    """Verify token counting, truncation, and context window packing."""

    def test_count_tokens_basic(self):
        from gossamer.token_budget import count_tokens

        n = count_tokens("Hello world")
        assert n >= 1
        # "Hello world" is 2 tokens with both cl100k_base and o200k_base
        assert n <= 5

    def test_count_tokens_empty(self):
        from gossamer.token_budget import count_tokens

        assert count_tokens("") == 0

    def test_count_tokens_long_text(self):
        from gossamer.token_budget import count_tokens

        long_text = "The quick brown fox jumps over the lazy dog. " * 100
        n = count_tokens(long_text)
        # ~11 tokens per sentence Ã— 100 = ~1100 tokens
        assert 800 < n < 1500

    def test_count_tokens_different_models(self):
        from gossamer.token_budget import count_tokens

        text = "Token counting test across models."
        # Different models may give slightly different counts
        gpt4 = count_tokens(text, "gpt-4o")
        gpt35 = count_tokens(text, "gpt-3.5-turbo")
        claude = count_tokens(text, "claude-3-sonnet")
        # All should be in the same ballpark
        assert 3 <= gpt4 <= 10
        assert 3 <= gpt35 <= 10
        assert 3 <= claude <= 10

    def test_truncate_to_tokens_no_truncation(self):
        from gossamer.token_budget import truncate_to_tokens

        short = "Hello world"
        result = truncate_to_tokens(short, 100)
        assert result == short  # no truncation needed

    def test_truncate_to_tokens_truncates(self):
        from gossamer.token_budget import truncate_to_tokens

        long_text = "The quick brown fox jumps over the lazy dog. " * 50
        result = truncate_to_tokens(long_text, 20)
        assert len(result) < len(long_text)
        assert "[truncated" in result

    def test_truncate_to_tokens_empty(self):
        from gossamer.token_budget import truncate_to_tokens

        assert truncate_to_tokens("", 10) == ""

    def test_truncate_to_tokens_custom_ellipsis(self):
        from gossamer.token_budget import truncate_to_tokens

        long_text = "The quick brown fox jumps over the lazy dog. " * 50
        result = truncate_to_tokens(long_text, 15, ellipsis="\n---END---")
        assert "---END---" in result

    def test_fit_context_window(self):
        from gossamer.token_budget import fit_context_window

        pieces = ["Hello", "world", "this", "is", "a", "test"]
        result = fit_context_window(pieces, 100)
        assert len(result) == 6  # all fit

    def test_fit_context_window_budget_limited(self):
        from gossamer.token_budget import fit_context_window

        pieces = ["The quick brown fox jumps over the lazy dog. " * 10] * 5
        result = fit_context_window(pieces, 50)
        assert len(result) < 5  # budget forces some pieces out

    def test_fit_context_window_empty(self):
        from gossamer.token_budget import fit_context_window

        assert fit_context_window([], 100) == []

    def test_resolve_encoding_known(self):
        from gossamer.token_budget import resolve_encoding

        # M5: gpt-4o is o200k_base — tiktoken's map is now authoritative
        assert resolve_encoding("gpt-4o") == "o200k_base"
        assert resolve_encoding("gpt-4") == "cl100k_base"
        assert resolve_encoding("gpt-3.5-turbo") == "cl100k_base"
        assert resolve_encoding("claude-3-sonnet") == "cl100k_base"

    def test_resolve_encoding_unknown(self):
        from gossamer.token_budget import resolve_encoding

        # Unknown models default to cl100k_base
        assert resolve_encoding("unknown-model-xyz") == "cl100k_base"

    def test_resolve_encoding_prefix_match(self):
        from gossamer.token_budget import resolve_encoding

        # Date-suffixed variants: tiktoken maps gpt-4o-* to o200k_base
        # and gpt-4-1106-preview to cl100k_base (M5)
        assert resolve_encoding("gpt-4o-2024-08-06") == "o200k_base"
        assert resolve_encoding("gpt-4-1106-preview") == "cl100k_base"

    def test_estimate_markdown_tokens(self):
        from gossamer.token_budget import estimate_markdown_tokens

        md = "# Hello\n\nThis is **bold** and *italic*."
        n = estimate_markdown_tokens(md)
        assert n >= 5
        assert n <= 30


class TestToolboxTokenBudget:
    """Verify token budgeting integrates correctly into WebResearcherToolbox."""

    def test_toolbox_default_no_token_limit(self):
        """Default toolbox has max_tokens=0 (char-only truncation)."""
        tb = WebResearcherToolbox()
        assert tb.max_tokens == 0
        assert tb.model_name == "gpt-4o"

    def test_toolbox_with_token_limit(self):
        """Toolbox with max_tokens set uses token-aware truncation."""
        tb = WebResearcherToolbox(max_tokens=100)
        assert tb.max_tokens == 100

    def test_toolbox_custom_model(self):
        """Toolbox respects custom model_name."""
        tb = WebResearcherToolbox(model_name="claude-3-sonnet", max_tokens=50)
        assert tb.model_name == "claude-3-sonnet"

    def test_stats_includes_token_budget(self):
        """get_stats reports token budget fields."""
        tb = WebResearcherToolbox(max_tokens=200, model_name="gpt-4o")
        import json

        stats = json.loads(tb.get_stats())
        assert stats["max_tokens"] == 200
        assert stats["model_name"] == "gpt-4o"

    def test_inspect_returns_token_count(self, local_server):
        """inspect_html_page includes markdown_tokens in output (P9)."""
        tb = WebResearcherToolbox(max_tokens=500, domain_delay=0.1)
        result = tb.inspect_html_page(f"{local_server}/alpha")
        data = json.loads(result)
        assert "error" not in data
        assert "markdown_tokens" in data
        assert isinstance(data["markdown_tokens"], int)
        assert data["markdown_tokens"] <= 500  # respects budget

    def test_inspect_without_token_limit(self, local_server):
        """Without max_tokens, truncation is char-only (P9)."""
        tb = WebResearcherToolbox(max_markdown_chars=100, domain_delay=0.1)
        result = tb.inspect_html_page(f"{local_server}/alpha")
        data = json.loads(result)
        assert "error" not in data
        # char limit + ellipsis suffix ("\n\n... [truncated]" = 17 chars)
        assert len(data["markdown"]) <= 120


# ────────────────────────────────────────────────────────────────
# HTML Structured Parsing
# ────────────────────────────────────────────────────────────────

class TestHTMLStructuredParsing:
    """Tests for StructuredOxideParser.parse_html and inspect_html_structured."""

    def test_parse_html_basic(self):
        """parse_html creates a valid ParsedDocumentPayload."""
        parser = StructuredOxideParser()
        payload = parser.parse_html(
            markdown="# Hello World",
            links=["https://example.com/link"],
            html_metadata={},
            url="https://example.com/page",
        )
        assert isinstance(payload, ParsedDocumentPayload)
        assert len(payload.pages) == 1
        assert payload.pages[0].markdown == "# Hello World"
        assert payload.metadata.format == "html"

    def test_parse_html_with_metadata(self):
        """parse_html merges HTML metadata into DocumentMetadata."""
        html_meta = {
            "meta": {"title": "Test Page", "description": "A test"},
            "opengraph": {"title": "OG Title", "image": "https://img.png"},
            "twitter": {"card": "summary", "title": "TW Title"},
            "jsonld": [{"@type": "Article"}],
        }
        parser = StructuredOxideParser()
        payload = parser.parse_html(
            markdown="Content here",
            links=[],
            html_metadata=html_meta,
            url="https://example.com/article",
        )
        assert payload.metadata.title == "Test Page"
        assert payload.metadata.og_title == "OG Title"
        assert payload.metadata.og_image == "https://img.png"
        assert payload.metadata.twitter_card == "summary"
        assert payload.metadata.twitter_title == "TW Title"
        assert payload.metadata.jsonld == [{"@type": "Article"}]

    def test_parse_html_url_slug(self):
        """parse_html derives file_name from URL path."""
        parser = StructuredOxideParser()
        payload = parser.parse_html(
            markdown="",
            links=[],
            html_metadata={},
            url="https://example.com/docs/guide",
        )
        assert payload.metadata.file_name == "docs/guide"

    def test_parse_html_root_url(self):
        """parse_html handles root URL (no path)."""
        parser = StructuredOxideParser()
        payload = parser.parse_html(
            markdown="",
            links=[],
            html_metadata={},
            url="https://example.com",
        )
        assert payload.metadata.file_name == "example.com"

    def test_parse_html_empty_metadata(self):
        """parse_html handles empty metadata gracefully."""
        parser = StructuredOxideParser()
        payload = parser.parse_html(
            markdown="Some content",
            links=[],
            html_metadata={},
            url="https://example.com",
        )
        assert payload.metadata.format == "html"
        assert len(payload.pages) == 1

    def test_inspect_html_structured_basic(self, tmp_path, local_server):
        """inspect_html_structured returns valid JSON (local server, P9)."""
        tb = WebResearcherToolbox(domain_delay=0.1, cache_dir=str(tmp_path / "cache"))
        result = tb.inspect_html_structured(f"{local_server}/alpha")
        data = json.loads(result)
        assert "metadata" in data
        assert "pages" in data
        assert len(data["pages"]) == 1
        assert data["metadata"]["format"] == "html"
        assert data["metadata"]["title"] == "Local Test Page alpha"

    @pytest.mark.slow
    def test_inspect_html_structured_with_smart(self, tmp_path, local_server):
        """use_smart='browser' (may launch browser_oxide — slow, P9)."""
        tb = WebResearcherToolbox(domain_delay=0.1, cache_dir=str(tmp_path / "cache"))
        result = tb.inspect_html_structured(f"{local_server}/alpha", use_smart="browser")
        data = json.loads(result)
        assert "metadata" in data
        assert "pages" in data

    def test_inspect_html_structured_token_truncation(self, tmp_path, local_server):
        """inspect_html_structured respects the token budget and stays valid JSON.

        This test used to accept unparseable output under a tight budget
        ("truncated output is expected"), which is how bugfix 1 shipped: the
        serialized payload was string-cut. The budget is now applied to the
        page text before serialization, so the reply is always parseable."""
        tb = WebResearcherToolbox(max_tokens=100, domain_delay=0.1, cache_dir=str(tmp_path / "cache"))
        result = tb.inspect_html_structured(f"{local_server}/alpha")
        data = json.loads(result)  # must never raise, however tight the budget
        assert isinstance(data, dict)
        assert count_tokens(result, tb.model_name) <= 100

    def test_inspect_html_structured_already_visited(self, tmp_path):
        """inspect_html_structured warns on already-visited URL."""
        tb = WebResearcherToolbox(domain_delay=0.1, cache_dir=str(tmp_path / "cache"))
        # Via the canonicalizing method (B.1): visited_urls holds identity
        # keys, so spelling variants share the entry.
        tb._mark_visited("https://example.com")
        result = tb.inspect_html_structured("https://example.com/")
        data = json.loads(result)
        assert "warning" in data

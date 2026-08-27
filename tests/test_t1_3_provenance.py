"""Tier 1.3 -- provenance in every research payload.

Every page or document read now carries the facts a citation needs:

* ``fetched_at`` -- when the content was fetched (a cache hit keeps the
  original fetch time; document cache hits keep no timestamp).
* ``http_status`` -- status of the final hop (static engine fetches and
  document downloads; the stealth browser does not surface it).
* ``final_url`` -- the URL after redirects.
* ``content_type`` -- what the server advertised.
* ``content_hash`` -- SHA-256 of the full, untruncated content, so every
  chunked read of one page shares the same hash.
* ``cache_hit`` -- the from-cache flag.

The Rust batch engine's ABI is pinned (M9) and carries no metadata, so
engine-fetched batch entries get only the content hash; cached and
browser-mode batch entries carry full provenance.
"""

import hashlib
import json
from pathlib import Path

from stitch_web_researcher import agent_tools
from stitch_web_researcher.agent_tools import (
    TOOL_REGISTRY,
    ToolboxConfig,
    WebResearcherToolbox,
)

PAGE = "\n\n".join(
    f"Paragraph number {i} carries filler words to stretch the page "
    f"well past the default output budget window."
    for i in range(30)
)
# Same filler under headings: section selection (Tier 1.1) only fires
# when the page splits into more than one section.
HEADED_PAGE = "\n\n".join(
    f"## Section {i}\n\nParagraph number {i} carries filler words to "
    f"stretch the page well past the default output budget window."
    for i in range(30)
)
PROV = (200, "https://prov.example/landed", "text/html; charset=utf-8")
FIXTURE_PDF = str(Path(__file__).parent / "fixtures_mini.pdf")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _toolbox(tmp_path, **config_kwargs) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            domain_delay=0.0,
            cache_dir=str(tmp_path / "cache"),
            **config_kwargs,
        )
    )


def _fake_fetch(page: str = PAGE, prov=PROV):
    """Module-level fetch_html_full stand-in (5-tuple, Tier 1.3)."""

    def fake(url, *args, **kwargs):
        return ("<html></html>", page, [("https://prov.example/link", "L")], 0, prov)

    return fake


class TestStaticProvenance:
    def test_fresh_read_carries_full_provenance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_tools, "fetch_html_full", _fake_fetch())
        tb = _toolbox(tmp_path)
        data = json.loads(tb.inspect_html_page("https://example.com/prov-start"))
        assert data["fetch_method"] == "static"
        assert data["cache_hit"] is False
        assert data["fetched_at"]
        assert data["http_status"] == 200
        # The final URL is what the server really served, not the request.
        assert data["final_url"] == "https://prov.example/landed"
        assert data["content_type"] == "text/html; charset=utf-8"
        assert data["content_hash"] == _sha256(PAGE)

    def test_cache_hit_keeps_original_fetch_time(self, tmp_path, monkeypatch):
        calls = []

        def fake(url, *a, **k):
            calls.append(url)
            return ("<h>", PAGE, [], 0, PROV)

        monkeypatch.setattr(agent_tools, "fetch_html_full", fake)
        tb = _toolbox(tmp_path)
        first = json.loads(tb.inspect_html_page("https://example.com/prov-start"))

        def dead(url, *a, **k):
            raise AssertionError("cache hit must not re-fetch")

        monkeypatch.setattr(agent_tools, "fetch_html_full", dead)
        second = json.loads(tb.inspect_html_page("https://example.com/prov-start"))
        assert calls == ["https://example.com/prov-start"]
        assert second["cache_hit"] is True
        assert second["fetched_at"] == first["fetched_at"]
        assert second["http_status"] == 200
        assert second["final_url"] == "https://prov.example/landed"
        assert second["content_hash"] == first["content_hash"]

    def test_chunked_reads_share_one_hash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_tools, "fetch_html_full", _fake_fetch())
        tb = _toolbox(tmp_path, max_markdown_chars=1000)
        first = json.loads(tb.inspect_html_page("https://example.com/prov-start"))
        second = json.loads(
            tb.inspect_html_page(
                "https://example.com/prov-start", offset=first["next_offset"]
            )
        )
        assert first["has_more"] is True
        assert second["cache_hit"] is True
        assert first["markdown"] != second["markdown"]
        # Same page, same hash, same original fetch time.
        assert first["content_hash"] == second["content_hash"] == _sha256(PAGE)
        assert first["fetched_at"] == second["fetched_at"]

    def test_query_selection_shares_hash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            agent_tools, "fetch_html_full", _fake_fetch(page=HEADED_PAGE)
        )
        tb = _toolbox(tmp_path, max_markdown_chars=1000)
        data = json.loads(
            tb.inspect_html_page(
                "https://example.com/prov-start", query="paragraph number 25"
            )
        )
        assert data["sections_selected"] > 0
        assert data["fetched_at"]
        assert data["content_hash"] == _sha256(HEADED_PAGE)

    def test_meta_without_provenance_still_hashes(self, tmp_path):
        # M8-style dispatch spy: a 4-tuple with no provenance key must
        # not break the payload -- the hash is always derivable.
        def spy(url, use_smart=None):
            return ("spy-md", [], {}, "static")

        tb = _toolbox(tmp_path)
        tb._fetch_html = spy
        data = json.loads(tb.inspect_html_page("https://example.com/prov-spy"))
        assert data["content_hash"] == _sha256("spy-md")
        assert data["fetched_at"] is None
        assert data["http_status"] is None


class TestBrowserProvenance:
    def test_use_smart_reports_best_effort(self, tmp_path, monkeypatch):
        calls = []

        def fake_browser(url):
            calls.append(url)
            return ("browser-md", [("https://prov.example/next", "Next")], {})

        monkeypatch.setattr(agent_tools, "_fetch_with_browser_oxide", fake_browser)
        tb = _toolbox(tmp_path)
        url = "https://example.com/prov-page"
        data = json.loads(tb.inspect_html_page(url, use_smart=True))
        assert calls == [url]
        assert data["fetch_method"] == "browser"
        # Best-effort: the browser layer surfaces no HTTP status or
        # post-redirect URL, so the request URL is reported as-is.
        assert data["http_status"] == 200
        assert data["final_url"] == url
        assert data["content_type"] is None
        assert data["fetched_at"]
        assert data["content_hash"] == _sha256("browser-md")

    def test_stealth_fallback_gets_browser_provenance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            agent_tools, "_fetch_with_browser_oxide", lambda u: ("fb-md", [], {})
        )

        def dead_static(url):
            raise RuntimeError("static down")

        tb = _toolbox(tmp_path)
        tb._static_fetch = dead_static
        data = json.loads(tb.inspect_html_page("https://example.com/prov-fb"))
        assert data["fetch_method"] == "stealth-fallback"
        assert data["http_status"] == 200
        assert data["final_url"] == "https://example.com/prov-fb"
        assert data["content_type"] is None
        assert data["fetched_at"]
        assert data["content_hash"] == _sha256("fb-md")

    def test_browser_mode_fetch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            agent_tools, "_fetch_with_browser_oxide", lambda u: ("bm-md", [], {})
        )
        tb = _toolbox(tmp_path, fetch_mode="browser")
        data = json.loads(tb.inspect_html_page("https://example.com/prov-bm"))
        assert data["fetch_method"] == "browser"
        assert data["http_status"] == 200
        assert data["final_url"] == "https://example.com/prov-bm"
        assert data["content_hash"] == _sha256("bm-md")


class _FakeDocumentClient:
    """httpx.Client stand-in serving fixed bytes with redirect provenance."""

    content: bytes = b"hello doc"
    final_url = "https://doc.example/landed.txt"
    status = 200
    content_type = "text/plain; charset=utf-8"

    class _Response:
        def __init__(self, outer):
            self.content = outer.content
            self.url = outer.final_url
            self.status_code = outer.status
            self.headers = {"content-type": outer.content_type}

        def raise_for_status(self):
            return None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        return self._Response(self)


class TestDocumentProvenance:
    def test_local_document(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_bytes(b"alpha\nbeta\n")
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(str(p)))
        assert data["cache_hit"] is False
        # Local reads have no HTTP leg: only the parse time is recorded.
        assert data["fetched_at"]
        assert data["http_status"] is None
        assert data["final_url"] is None
        assert data["content_hash"] == _sha256("alpha\nbeta\n")
        hit = json.loads(tb.extract_document(str(p)))
        assert hit["cache_hit"] is True
        assert hit["fetched_at"] is None  # the store keeps no timestamp
        assert hit["content_hash"] == data["content_hash"]

    def test_url_document_carries_http_provenance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_tools.httpx, "Client", _FakeDocumentClient)
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document("https://example.com/file.txt"))
        assert data["cache_hit"] is False
        assert data["fetched_at"]
        assert data["http_status"] == 200
        assert data["final_url"] == "https://doc.example/landed.txt"
        assert data["content_type"] == "text/plain; charset=utf-8"
        assert data["content_hash"] == _sha256("hello doc")

    def test_local_page_range_provenance(self, tmp_path):
        tb = _toolbox(tmp_path)
        data = json.loads(tb.extract_document(FIXTURE_PDF, pages="2"))
        assert data["cache_hit"] is False
        assert data["page_range"] == "2"
        assert data["fetched_at"]
        assert data["http_status"] is None
        assert data["content_hash"] == _sha256("# BETA page two content")

    def test_url_page_range_carries_download_provenance(self, tmp_path, monkeypatch):
        raw = Path(FIXTURE_PDF).read_bytes()
        client = type(
            "PdfClient",
            (_FakeDocumentClient,),
            {
                "content": raw,
                "final_url": "https://doc.example/landed.pdf",
                "content_type": "application/pdf",
            },
        )
        monkeypatch.setattr(agent_tools.httpx, "Client", client)
        tb = _toolbox(tmp_path)
        data = json.loads(
            tb.extract_document("https://example.com/file.pdf", pages="1")
        )
        assert data["cache_hit"] is False
        assert data["page_range"] == "1"
        assert data["fetched_at"]
        assert data["http_status"] == 200
        assert data["final_url"] == "https://doc.example/landed.pdf"
        assert data["content_hash"] == _sha256("# ALPHA page one content")


class TestBatchProvenance:
    def test_engine_entries_carry_content_hash(self, tmp_path, monkeypatch):
        def fake_batch(urls, **kwargs):
            return [(u, f"md-of-{u}", []) for u in urls]

        monkeypatch.setattr(agent_tools, "batch_research", fake_batch)
        tb = _toolbox(tmp_path)
        out = json.loads(
            tb.batch_inspect_pages(["https://example.net/batch-1", "https://example.net/batch-2"])
        )
        assert len(out) == 2
        # The engine ABI (M9) carries no metadata: the hash is the one
        # field that is always derivable.
        assert out[0]["content_hash"] == _sha256("md-of-https://example.net/batch-1")
        assert out[0]["fetched_at"] is None
        assert out[0]["http_status"] is None

    def test_cached_entries_serve_stored_provenance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_tools, "fetch_html_full", _fake_fetch())
        tb = _toolbox(tmp_path)
        first = json.loads(tb.inspect_html_page("https://example.net/batch-1"))
        out = json.loads(tb.batch_inspect_pages(["https://example.net/batch-1"]))
        assert out[0]["cache_hit"] is True
        assert out[0]["fetched_at"] == first["fetched_at"]
        assert out[0]["http_status"] == 200
        assert out[0]["final_url"] == "https://prov.example/landed"
        assert out[0]["content_hash"] == _sha256(PAGE)

    def test_browser_mode_entries_get_browser_provenance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            agent_tools, "_fetch_with_browser_oxide", lambda u: ("browser-md", [], {})
        )
        tb = _toolbox(tmp_path, fetch_mode="browser")
        out = json.loads(tb.batch_inspect_pages(["https://example.org/batch-b1"]))
        assert out[0]["fetch_method"] == "browser"
        assert out[0]["http_status"] == 200
        assert out[0]["final_url"] == "https://example.org/batch-b1"
        assert out[0]["content_type"] is None
        assert out[0]["content_hash"] == _sha256("browser-md")


class TestRegistryAdvertisesProvenance:
    def test_inspect_description_mentions_provenance(self):
        spec = next(s for s in TOOL_REGISTRY if s.name == "inspect_html_page")
        assert "provenance" in spec.description
        assert "content_hash" in spec.description

"""Regression tests for the 2026-08 improvement plan.

Covers:
  - batch_inspect_pages actually skips already-visited URLs
  - HTML inspection results are served from the two-tier cache
  - extract_main_content_markdown binding (selector visibility)
  - batch_research accepts max_concurrency
"""

import json
from unittest.mock import patch

import pytest

from stitch_web_researcher.agent_tools import WebResearcherToolbox


def _toolbox(tmp_path):
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
    )


class TestBatchVisitedSkip:
    def test_visited_urls_are_not_refetched(self, tmp_path):
        """Already-visited URLs must never reach the fetch engines."""
        tb = _toolbox(tmp_path)
        url = "https://example.com/batch-skip"
        tb.visited_urls.add(url)

        with patch(
            "stitch_web_researcher.agent_tools.batch_research"
        ) as mock_batch:
            mock_batch.return_value = []
            tb.batch_inspect_pages([url])

        assert mock_batch.call_count == 1
        args, kwargs = mock_batch.call_args
        assert args[0] == [], (
            "already-visited URL was passed to batch_research despite 'skipping'"
        )

    def test_fresh_urls_are_fetched(self, tmp_path):
        tb = _toolbox(tmp_path)
        urls = ["https://example.com/a", "https://example.com/b"]

        with patch(
            "stitch_web_researcher.agent_tools.batch_research"
        ) as mock_batch:
            mock_batch.return_value = []
            tb.batch_inspect_pages(urls)

        args, _ = mock_batch.call_args
        assert sorted(args[0]) == sorted(urls)


class TestInspectionCache:
    def test_second_inspect_hits_cache(self, tmp_path):
        tb = _toolbox(tmp_path)
        url = "https://example.com/cache-hit"

        with patch.object(
            tb,
            "_fetch_html",
            return_value=("hello world", [("https://example.com/x", "x")], {}, "static"),
        ) as mock_fetch:
            first = json.loads(tb.inspect_html_page(url))
            tb.visited_urls.clear()  # visited-guard takes precedence over cache
            second = json.loads(tb.inspect_html_page(url))

        assert mock_fetch.call_count == 1, "second inspect must be served from cache"
        assert second["cache_hit"] is True
        assert first["cache_hit"] is False
        assert second["markdown"] == first["markdown"]
        assert second["follow_up_links"] == first["follow_up_links"]

    def test_cache_respects_current_budget_on_hit(self, tmp_path):
        """Cached entries store untruncated content; budgets re-apply on read."""
        tb = _toolbox(tmp_path)
        url = "https://example.com/budget"

        with patch.object(
            tb,
            "_fetch_html",
            return_value=("x" * 5000, [], {}, "static"),
        ):
            tb.inspect_html_page(url)

        tb.max_markdown_chars = 100  # shrink budget after caching
        tb.visited_urls.clear()      # visited-guard takes precedence over cache
        data = json.loads(tb.inspect_html_page(url))
        assert len(data["markdown"]) <= 100 + len("\n\n... [truncated]")

    def test_structured_inspection_hits_cache(self, tmp_path):
        tb = _toolbox(tmp_path)
        url = "https://example.com/structured-cache"

        with patch.object(
            tb,
            "_fetch_html",
            return_value=("hello", [("https://example.com/x", "x")], {}, "static"),
        ) as mock_fetch:
            first = json.loads(tb.inspect_html_structured(url))
            tb.visited_urls.clear()  # visited-guard takes precedence over cache
            second = json.loads(tb.inspect_html_structured(url))

        assert mock_fetch.call_count == 1
        assert second["metadata"]["format"] == first["metadata"]["format"]
        assert second["pages"] == first["pages"]


class TestMainContentBinding:
    def test_prefers_article_over_body(self):
        from stitch_web_researcher._core import extract_main_content_markdown

        html = (
            "<html><body><nav>menu junk</nav>"
            "<article><h2>Real</h2><p>Content</p></article></body></html>"
        )
        label, md = extract_main_content_markdown(html)

        assert label == "article"
        assert "Real" in md
        assert "menu junk" not in md

    def test_body_fallback_reports_label(self):
        from stitch_web_researcher._core import extract_main_content_markdown

        html = "<html><body><p>plain</p></body></html>"
        label, md = extract_main_content_markdown(html)

        assert label == "body"
        assert "plain" in md


class TestBatchConcurrencyParam:
    def test_batch_research_accepts_max_concurrency(self):
        from stitch_web_researcher._core import batch_research

        out = batch_research(
            ["https://example.com/plan-fixes-smoke"],
            max_links=1,
            max_concurrency=2,
        )
        assert len(out) == 1
        url, md_opt, links_opt = out[0]
        # Either success (md+links) or a clean error string — never a crash.
        assert md_opt is not None

    def test_toolbox_threads_max_concurrency(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb.max_concurrency = 3

        with patch(
            "stitch_web_researcher.agent_tools.batch_research"
        ) as mock_batch:
            mock_batch.return_value = []
            tb.batch_inspect_pages(["https://example.com/x"])

        _, kwargs = mock_batch.call_args
        assert kwargs.get("max_concurrency") == 3


class TestToolboxConfig:
    """P2-#10: config-object construction with legacy kwargs passthrough."""

    def test_config_object_style(self, tmp_path):
        from stitch_web_researcher.agent_tools import ToolboxConfig

        tb = WebResearcherToolbox(
            ToolboxConfig(cache_dir=str(tmp_path / "c"), domain_delay=0.0)
        )
        assert tb.fetch_mode == "auto"
        assert tb.max_concurrency == 8
        assert tb.link_cap == 500

    def test_legacy_kwargs_still_work_with_warning(self, tmp_path):
        with pytest.warns(DeprecationWarning):
            tb = WebResearcherToolbox(cache_dir=str(tmp_path / "c"), domain_delay=0.25)
        assert tb.domain_delay == 0.25

    def test_config_and_kwargs_rejected_together(self):
        from stitch_web_researcher.agent_tools import ToolboxConfig

        with pytest.raises(TypeError):
            WebResearcherToolbox(ToolboxConfig(), max_tokens=5)

    def test_invalid_fetch_mode_rejected_in_config(self):
        from stitch_web_researcher.agent_tools import ToolboxConfig

        with pytest.raises(ValueError, match="fetch_mode"):
            ToolboxConfig(fetch_mode="nope")

    def test_provider_rate_limit_drives_fetch_interval(self, tmp_path):
        from stitch_web_researcher.agent_tools import ToolboxConfig
        from stitch_web_researcher.search_providers import RateLimit

        prov = _FakeProvider(RateLimit(search_interval=0.0, fetch_interval=2.5))
        tb = WebResearcherToolbox(
            ToolboxConfig(
                cache_dir=str(tmp_path / "c"),
                search_providers=[prov],
                ddgs_delay=0.0,
            )
        )
        assert tb._fetch_interval == 2.5


class _FakeProvider:
    """Minimal provider stand-in for config-resolution tests."""

    def __init__(self, rate_limit=None):
        self.rate_limit = rate_limit

    def search(self, query, max_results=5):
        return []


class TestFetchSpacingJitter:
    """Page fetch spacing = base interval + random 0-1s jitter."""

    def _toolbox(self, tmp_path, **cfg):
        from stitch_web_researcher.agent_tools import ToolboxConfig

        return WebResearcherToolbox(
            ToolboxConfig(cache_dir=str(tmp_path / "c"), **cfg)
        )

    def test_gap_is_base_plus_jitter(self, tmp_path, monkeypatch):
        from stitch_web_researcher import agent_tools

        tb = self._toolbox(tmp_path, fetch_delay=0.5)
        sleeps = []
        monkeypatch.setattr(agent_tools.time, "sleep", sleeps.append)
        monkeypatch.setattr(agent_tools.random, "uniform", lambda a, b: 0.7)

        # Simulate a fetch that just happened on this domain.
        domain = "example.com"
        tb._domain_last_seen[domain] = agent_tools.time.time()

        tb._rate_limit_domain("https://example.com/page")

        assert len(sleeps) == 1
        # expected gap: 0.5 base + 0.7 jitter = 1.2s
        assert sleeps[0] == pytest.approx(1.2, abs=0.05)

    def test_no_sleep_when_gap_already_elapsed(self, tmp_path, monkeypatch):
        from stitch_web_researcher import agent_tools

        tb = self._toolbox(tmp_path, fetch_delay=0.5)
        sleeps = []
        monkeypatch.setattr(agent_tools.time, "sleep", sleeps.append)

        # Last fetch long ago -> no wait needed even with max jitter.
        tb._domain_last_seen["example.com"] = agent_tools.time.time() - 10

        tb._rate_limit_domain("https://example.com/page")
        assert sleeps == []

    def test_zero_interval_disables_jitter(self, tmp_path, monkeypatch):
        from stitch_web_researcher import agent_tools

        tb = self._toolbox(tmp_path, fetch_delay=0.0)
        called = []
        monkeypatch.setattr(
            agent_tools.random, "uniform",
            lambda a, b: called.append((a, b)) or 0.0,
        )
        sleeps = []
        monkeypatch.setattr(agent_tools.time, "sleep", sleeps.append)

        tb._domain_last_seen["example.com"] = agent_tools.time.time()
        tb._rate_limit_domain("https://example.com/page")

        assert called == [], "jitter must not apply when interval is 0"
        assert sleeps == []


class TestSearchSpacingJitter:
    """Search call spacing = search_interval + random 0-1s jitter."""

    def test_gap_is_interval_plus_jitter(self, monkeypatch):
        from stitch_web_researcher import agent_tools, search_providers

        prov = _FakeProvider()
        prov._last_search = 0.0
        prov._delay = 1.0
        sleeps = []
        monkeypatch.setattr(search_providers.time, "sleep", sleeps.append)
        monkeypatch.setattr(search_providers.random, "uniform", lambda a, b: 0.4)

        # Simulate a search that just happened.
        prov._last_search = search_providers.time.time()
        search_providers.SearchProvider._enforce_delay(prov)

        assert len(sleeps) == 1
        assert sleeps[0] == pytest.approx(1.4, abs=0.05)
        assert prov._last_search > 0
        del agent_tools  # imported only to mirror module layout

    def test_no_sleep_when_gap_elapsed(self, monkeypatch):
        from stitch_web_researcher import search_providers

        prov = _FakeProvider()
        prov._delay = 1.0
        prov._last_search = search_providers.time.time() - 10
        sleeps = []
        monkeypatch.setattr(search_providers.time, "sleep", sleeps.append)

        search_providers.SearchProvider._enforce_delay(prov)
        assert sleeps == []

    def test_zero_delay_disables_jitter(self, monkeypatch):
        from stitch_web_researcher import search_providers

        prov = _FakeProvider()
        prov._delay = 0.0
        prov._last_search = search_providers.time.time()
        called = []
        monkeypatch.setattr(
            search_providers.random, "uniform",
            lambda a, b: called.append((a, b)) or 0.0,
        )
        sleeps = []
        monkeypatch.setattr(search_providers.time, "sleep", sleeps.append)

        search_providers.SearchProvider._enforce_delay(prov)
        assert called == [], "jitter must not apply when delay is 0"
        assert sleeps == []


class TestPerDomainDelayIsolation:
    """Fetch delays apply ONLY between same-domain fetches; different
    domains never wait on each other (incl. use_smart=True paths, since
    _rate_limit_domain runs before _fetch_html regardless of mode)."""

    def test_cross_domain_fetches_are_never_delayed(self, tmp_path, monkeypatch):
        from stitch_web_researcher import agent_tools

        from stitch_web_researcher.agent_tools import ToolboxConfig

        tb = WebResearcherToolbox(
            ToolboxConfig(cache_dir=str(tmp_path / "c"), fetch_delay=1.0)
        )
        sleeps = []
        monkeypatch.setattr(agent_tools.time, "sleep", sleeps.append)

        tb._rate_limit_domain("https://alpha.com/page")
        tb._rate_limit_domain("https://beta.org/page")
        tb._rate_limit_domain("https://gamma.net/page")

        assert sleeps == [], "different domains must not wait on each other"

    def test_same_domain_repeat_is_delayed(self, tmp_path, monkeypatch):
        from stitch_web_researcher import agent_tools

        from stitch_web_researcher.agent_tools import ToolboxConfig

        tb = WebResearcherToolbox(
            ToolboxConfig(cache_dir=str(tmp_path / "c"), fetch_delay=1.0)
        )
        sleeps = []
        monkeypatch.setattr(agent_tools.time, "sleep", sleeps.append)

        url_a, url_b = "https://alpha.com/a", "https://alpha.com/b"
        tb._rate_limit_domain(url_a)
        # different path, SAME domain -> must be spaced
        tb._rate_limit_domain(url_b)

        assert len(sleeps) == 1
        assert sleeps[0] >= 1.0  # base interval; jitter may add up to 1s

    def test_smart_and_static_fetches_share_one_delay_budget(self, tmp_path, monkeypatch):
        """A smart (browser) fetch followed by a static fetch of the same
        domain is still rate-limited as one domain."""
        from stitch_web_researcher import agent_tools

        from stitch_web_researcher.agent_tools import ToolboxConfig

        tb = WebResearcherToolbox(
            ToolboxConfig(cache_dir=str(tmp_path / "c"), fetch_delay=1.0)
        )
        sleeps = []
        monkeypatch.setattr(agent_tools.time, "sleep", sleeps.append)

        tb._rate_limit_domain("https://alpha.com/a")   # e.g. smart fetch
        tb._rate_limit_domain("https://alpha.com/b")   # static fetch, same domain

        assert len(sleeps) == 1 and sleeps[0] >= 1.0


class TestBatchSameDomainStaggering:
    """Batch engine spaces same-domain starts (gap = _fetch_interval + jitter);
    cross-domain URLs are never delayed relative to each other."""

    def test_same_domain_batch_is_staggered(self, tmp_path):
        import threading
        import time as time_mod
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html><body><h1>ok</h1></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            from stitch_web_researcher.agent_tools import ToolboxConfig

            tb = WebResearcherToolbox(
                ToolboxConfig(
                    cache_dir=str(tmp_path / "c"),
                    fetch_delay=0.5,  # -> domain_gap_ms=500 (+0-1s jitter in Rust)
                    ddgs_delay=0.0,
                )
            )
            port = server.server_address[1]
            urls = [f"http://127.0.0.1:{port}/{i}" for i in range(3)]

            start = time_mod.monotonic()
            out = tb.batch_inspect_pages(urls)
            elapsed = time_mod.monotonic() - start

            results = [r for r in json.loads(out) if "error" not in r]
            assert len(results) == 3
            # Three same-domain starts must be spaced >= ~500ms apart (jitter
            # only adds), so total elapsed must exceed two gaps minus tolerance.
            assert elapsed >= 0.9, (
                f"3 same-domain fetches completed in {elapsed:.2f}s -- "
                "batch engine did not stagger"
            )
        finally:
            server.shutdown()

    def test_domain_gap_passed_to_engine(self, tmp_path):
        from stitch_web_researcher.agent_tools import ToolboxConfig

        tb = WebResearcherToolbox(
            ToolboxConfig(cache_dir=str(tmp_path / "c"), fetch_delay=0.75)
        )
        with patch("stitch_web_researcher.agent_tools.batch_research") as mock_batch:
            mock_batch.return_value = []
            tb.batch_inspect_pages(["https://example.com/x"])

        _, kwargs = mock_batch.call_args
        assert kwargs["domain_gap_ms"] == 750

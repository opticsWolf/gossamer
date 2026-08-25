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

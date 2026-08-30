"""C3 regression: failed fetches must not permanently blacklist URLs.

Before the fix, ``visited_urls.add(url)`` ran before the fetch, so a
transient failure poisoned the URL for the rest of the process lifetime
and the MCP surface had no recovery (``reset_visited`` existed only on
the Python class, never as a tool).
"""

import json
from unittest.mock import patch

import pytest

from stitch_web_researcher.agent_tools import WebResearcherToolbox


def _toolbox(tmp_path) -> WebResearcherToolbox:
    # respect_robots=False: the fetch layer is mocked with fake
    # example.com URLs, so no live robots.txt probe may run (S4).
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )


class TestRetryAfterFailure:
    def test_failed_fetch_is_retryable(self, tmp_path):
        tb = _toolbox(tmp_path)
        url = "https://example.com/flaky"

        with patch.object(
            tb, "_fetch_html", side_effect=RuntimeError("connection reset")
        ) as mock_fetch:
            first = json.loads(tb.inspect_html_page(url))
        assert "error" in first

        # Same toolbox, same URL: must NOT return an "already visited"
        # warning and must actually attempt the fetch again.
        with patch.object(
            tb, "_fetch_html", return_value=("ok", [], {}, "static")
        ):
            second = json.loads(tb.inspect_html_page(url))

        assert "warning" not in second
        assert second["markdown"] == "ok"
        assert mock_fetch.call_count == 1

    def test_visited_only_after_success(self, tmp_path):
        tb = _toolbox(tmp_path)
        url = "https://example.com/fail-then-success"
        with patch.object(
            tb, "_fetch_html", side_effect=RuntimeError("boom")
        ):
            tb.inspect_html_page(url)
        assert url not in tb.visited_urls

        with patch.object(
            tb, "_fetch_html", return_value=("ok", [], {}, "static")
        ):
            tb.inspect_html_page(url)
        assert url in tb.visited_urls

    def test_failed_batch_entry_remains_retryable(self, tmp_path):
        tb = _toolbox(tmp_path)
        ok, bad = "https://example.com/batch-a", "https://example.com/batch-b"

        def fake_batch(
            urls, max_links=500, max_concurrency=8, domain_gap_ms=0, max_bytes=None
        ):
            html = "<html><head><title>t</title></head><body>c</body></html>"
            return [
                (u, html, "content", [("https://example.com/x", "x")]) if u == ok
                else (u, None, "boom", None)
                for u in urls
            ]

        with patch("stitch_web_researcher.agent_tools.batch_research", side_effect=fake_batch):
            first = json.loads(tb.batch_inspect_pages([ok, bad]))
        assert bad not in tb.visited_urls
        assert ok in tb.visited_urls
        assert len(first) == 2  # success entry + error entry
        assert {e.get("url") for e in first} == {ok, bad}

        # Retry the batch: only the previously-failed URL should go to the
        # engine; the visited (successful) one is skipped.
        with patch("stitch_web_researcher.agent_tools.batch_research", side_effect=fake_batch) as mock_batch:
            json.loads(tb.batch_inspect_pages([ok, bad]))
        assert mock_batch.call_args[0][0] == [bad]


class TestRepeatVisitServesCache:
    def test_second_visit_serves_cache_not_warning(self, tmp_path):
        tb = _toolbox(tmp_path)
        url = "https://example.com/repeat"
        with patch.object(
            tb, "_fetch_html", return_value=("hello", [("https://example.com/x", "x")], {}, "static")
        ) as mock_fetch:
            first = json.loads(tb.inspect_html_page(url))
            second = json.loads(tb.inspect_html_page(url))
        assert mock_fetch.call_count == 1
        assert "warning" not in second
        assert second["markdown"] == first["markdown"]
        assert second["cache_hit"] is True

    def test_structured_second_visit_serves_cache_not_warning(self, tmp_path):
        tb = _toolbox(tmp_path)
        url = "https://example.com/repeat-structured"
        # Tier 3.11: the structured path fetches via _fetch_html_with_html
        # (5-tuple with raw HTML; None here, so no tables are extracted).
        with patch.object(
            tb,
            "_fetch_html_with_html",
            return_value=("hello", [], {}, "static", None),
        ) as mock_fetch:
            first = json.loads(tb.inspect_html_structured(url))
            second = json.loads(tb.inspect_html_structured(url))
        assert mock_fetch.call_count == 1
        assert "warning" not in second
        assert second["pages"] == first["pages"]

    def test_visited_but_not_cached_still_warns(self, tmp_path):
        """Dedup still applies when the cache no longer holds the content
        (e.g. cache cleared) — the URL is not re-fetched silently."""
        tb = _toolbox(tmp_path)
        url = "https://example.com/visited-uncached"
        with patch.object(tb, "_fetch_html", return_value=("hello", [], {}, "static")):
            tb.inspect_html_page(url)
        tb.cache.clear()  # visited set survives; cache is gone
        with patch.object(tb, "_fetch_html") as mock_fetch:
            out = json.loads(tb.inspect_html_page(url))
        assert out.get("warning") == "URL already visited"
        mock_fetch.assert_not_called()


class TestRecoveryViaMCP:
    def test_manage_cache_is_an_llm_tool(self, tmp_path):
        tb = _toolbox(tmp_path)
        names = [t["function"]["name"] for t in tb.get_llm_definitions()]
        assert "manage_cache" in names

    def test_manage_cache_tool_registered_on_mcp_server(self):
        import asyncio

        try:
            from stitch_web_researcher.mcp_server import build_server
        except ImportError:
            pytest.skip("mcp not installed")
        server = build_server()
        tools = {t.name for t in asyncio.run(server.list_tools())}
        assert "manage_cache" in tools

    def test_clear_cache_also_clears_visited(self, tmp_path):
        tb = _toolbox(tmp_path)
        url = "https://example.com/clear"
        with patch.object(tb, "_fetch_html", return_value=("hi", [], {}, "static")):
            tb.inspect_html_page(url)
        assert url in tb.visited_urls

        out = json.loads(tb.clear_cache())
        assert out["cache_cleared"] is True
        assert url not in tb.visited_urls

        # After clear_cache the same URL is fetched again (fresh).
        with patch.object(
            tb, "_fetch_html", return_value=("fresh", [], {}, "static")
        ) as mock_fetch:
            data = json.loads(tb.inspect_html_page(url))
        assert data["markdown"] == "fresh"
        assert mock_fetch.call_count == 1

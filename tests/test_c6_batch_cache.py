"""C6 regression: batch_inspect_pages must share the page cache with
single-page inspection.

Before the fix, batches never read or wrote ``self.cache``: pages fetched
in a batch were re-fetched on a later individual inspection, pages already
cached were re-fetched by a batch, URLs skipped ``normalize_url()``
(doubling visited/cache entries), and batch entries had a different shape
(no metadata/cache_hit, undocumented fetch_method "static-batch").
"""

import json
from unittest.mock import patch

from stitch_web_researcher.agent_tools import WebResearcherToolbox

FAKE_MD = "# Batch content"
# Bugfix 5: the engine now returns the raw HTML so batch entries can run
# the same meta-oxide extraction single-page reads do.
FAKE_HTML = (
    "<html><head><title>Fake</title>"
    '<meta name="description" content="Fake description">'
    "</head><body><main><p>fake</p></main></body></html>"
)
FAKE_LINKS = [("https://example.com/child", "Child")]


def _toolbox(tmp_path):
    # respect_robots=False: batch_research/_fetch_html are mocked with fake
    # example.com URLs, so no live robots.txt probe may run (S4).
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )


class TestBatchSharesPageCache:
    def test_batch_fetch_populates_cache(self, tmp_path):
        """A page fetched in a batch is served from cache on later
        single-page inspection — no second fetch."""
        tb = _toolbox(tmp_path)
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=[("https://example.com/a", FAKE_HTML, FAKE_MD, FAKE_LINKS)],
        ):
            first = json.loads(tb.batch_inspect_pages(["https://example.com/a"]))
        assert first[0]["markdown"] == FAKE_MD

        with patch.object(tb._fetch, "_fetch_html", side_effect=AssertionError(
            "re-fetch after batch"
        )) as single:
            second = json.loads(tb.inspect_html_page("https://example.com/a"))
        assert second["cache_hit"] is True
        assert second["markdown"] == FAKE_MD
        assert not single.called

    def test_cached_page_not_refetched_by_batch(self, tmp_path):
        """A page already cached is served from cache by a batch."""
        tb = _toolbox(tmp_path)
        with patch.object(
            tb._fetch, "_fetch_html", return_value=(FAKE_MD, FAKE_LINKS, {}, "static")
        ):
            single = json.loads(tb.inspect_html_page("https://example.com/b"))
        assert single["cache_hit"] is False

        with patch(
            "stitch_web_researcher.fetch.batch_research",
            side_effect=AssertionError("re-fetch of cached page"),
        ) as mock_batch:
            out = json.loads(tb.batch_inspect_pages(["https://example.com/b"]))
        assert out[0]["cache_hit"] is True
        assert out[0]["markdown"] == FAKE_MD
        assert not mock_batch.called

    def test_repeated_batch_is_free(self, tmp_path):
        """The second batch of the same URLs performs zero fetches."""
        tb = _toolbox(tmp_path)
        urls = ["https://example.com/1", "https://example.com/2"]
        fake = [
            ("https://example.com/1", FAKE_HTML, FAKE_MD, FAKE_LINKS),
            ("https://example.com/2", FAKE_HTML, FAKE_MD, FAKE_LINKS),
        ]
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=fake,
        ) as mock_batch:
            tb.batch_inspect_pages(urls)
            second = json.loads(tb.batch_inspect_pages(urls))
        assert mock_batch.call_count == 1
        assert all(e["cache_hit"] is True for e in second)
        assert [e["url"] for e in second] == urls

    def test_normalization_shares_entries(self, tmp_path):
        """Unnormalized input (no scheme) can't create a second
        visited/cache entry for the same page."""
        tb = _toolbox(tmp_path)
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=[("https://example.com/c", FAKE_HTML, FAKE_MD, FAKE_LINKS)],
        ) as mock_batch:
            tb.batch_inspect_pages(["example.com/c"])
        # The engine must have received the normalized form.
        assert mock_batch.call_args[0][0] == ["https://example.com/c"]
        assert "https://example.com/c" in tb.visited_urls

        with patch(
            "stitch_web_researcher.fetch.batch_research",
            side_effect=AssertionError("re-fetch despite normalization"),
        ):
            out = json.loads(tb.batch_inspect_pages(["https://example.com/c"]))
        assert out[0]["cache_hit"] is True


class TestBatchShape:
    def test_entry_shape_matches_single_page(self, tmp_path):
        """Every batch entry carries the single-page result shape."""
        tb = _toolbox(tmp_path)
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=[("https://example.com/s", FAKE_HTML, FAKE_MD, FAKE_LINKS)],
        ):
            out = json.loads(tb.batch_inspect_pages(["https://example.com/s"]))
        entry = out[0]
        for key in ("url", "markdown", "metadata", "cache_hit", "fetch_method",
                    "follow_up_links", "truncated"):
            assert key in entry, f"missing {key}"
        assert entry["fetch_method"] == "static"
        assert entry["cache_hit"] is False

    def test_input_order_preserved(self, tmp_path):
        tb = _toolbox(tmp_path)
        urls = [
            "https://example.com/z",
            "https://example.com/a",
            "https://example.com/m",
        ]
        fake = [(u, FAKE_HTML, FAKE_MD, FAKE_LINKS) for u in urls]
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=fake,
        ):
            out = json.loads(tb.batch_inspect_pages(urls))
        assert [e["url"] for e in out] == urls

    def test_failed_entry_kept_and_rest_cached(self, tmp_path):
        tb = _toolbox(tmp_path)
        ok, bad = "https://example.com/ok", "https://example.com/bad"
        fake = [(ok, FAKE_HTML, FAKE_MD, FAKE_LINKS), (bad, None, "boom", None)]
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=fake,
        ):
            out = json.loads(tb.batch_inspect_pages([ok, bad]))
        by_url = {e["url"]: e for e in out}
        assert "error" in by_url[bad]
        assert by_url[ok]["cache_hit"] is False

        # Retry: only the failed URL is fetched again; the ok one is cached.
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=[(bad, FAKE_HTML, FAKE_MD, FAKE_LINKS)],
        ) as mock_batch:
            retry = json.loads(tb.batch_inspect_pages([ok, bad]))
        assert mock_batch.call_args[0][0] == [bad]
        by_url = {e["url"]: e for e in retry}
        assert by_url[ok]["cache_hit"] is True
        assert by_url[bad]["markdown"] == FAKE_MD

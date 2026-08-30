# tests/test_m10_batch_error.py
"""M10 — batch failures must be explicit, not inferred from slot
emptiness.

The raw batch engine returns (url, md_or_error, links_or_None)
triples, forcing callers to guess which slot carries the error.
BatchEntry / _normalize_batch_results make success vs. failure explicit,
and batch_inspect_pages now branches on the tag. All tests offline
(batch_research is mocked, S4 opt-out).
"""

import json
from unittest.mock import patch

from stitch_web_researcher.agent_tools import (
    BatchEntry,
    WebResearcherToolbox,
    _normalize_batch_results,
)

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
    # respect_robots=False: batch_research is mocked with fake example.com
    # URLs, so no live robots.txt probe may run (S4).
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )


class TestNormalizeBatchResults:
    def test_success_entry(self):
        entries = _normalize_batch_results(
            [("https://example.com/a", FAKE_HTML, FAKE_MD, FAKE_LINKS)]
        )
        assert entries == [
            BatchEntry(
                url="https://example.com/a",
                markdown=FAKE_MD,
                links=FAKE_LINKS,
                html=FAKE_HTML,
            )
        ]
        assert entries[0].ok is True
        assert entries[0].error is None

    def test_empty_markdown_and_links_still_success(self):
        """Empty content is a successful fetch, not a failure."""
        entries = _normalize_batch_results([("https://example.com/a", FAKE_HTML, "", [])])
        assert entries[0].ok is True
        assert entries[0].markdown == ""
        assert entries[0].links == []

    def test_error_entry_with_message(self):
        entries = _normalize_batch_results(
            [("https://example.com/b", None, "DNS failure", None)]
        )
        assert entries[0].ok is False
        assert entries[0].error == "DNS failure"
        assert entries[0].markdown is None
        assert entries[0].links is None

    def test_error_entry_with_empty_message(self):
        entries = _normalize_batch_results([("https://example.com/b", None, "", None)])
        assert entries[0].ok is False
        assert entries[0].error == "Unknown error"

    def test_order_preserved(self):
        entries = _normalize_batch_results(
            [
                ("https://example.com/1", FAKE_HTML, FAKE_MD, FAKE_LINKS),
                ("https://example.com/2", None, "boom", None),
                ("https://example.com/3", FAKE_HTML, FAKE_MD, FAKE_LINKS),
            ]
        )
        assert [e.url for e in entries] == [
            "https://example.com/1",
            "https://example.com/2",
            "https://example.com/3",
        ]


class TestBatchInspectErrorChannel:
    """batch_inspect_pages maps failure triples to explicit error entries."""

    def test_mixed_success_and_failure(self, tmp_path):
        tb = _toolbox(tmp_path)
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=[
                ("https://example.com/ok", FAKE_HTML, FAKE_MD, FAKE_LINKS),
                ("https://example.com/bad", None, "connection refused", None),
            ],
        ):
            out = json.loads(
                tb.batch_inspect_pages(
                    ["https://example.com/ok", "https://example.com/bad"]
                )
            )
        assert out[0]["markdown"] == FAKE_MD
        assert "error" not in out[0]
        assert out[1]["url"] == "https://example.com/bad"
        assert out[1]["error"] == "connection refused"

    def test_failed_url_stays_retryable(self, tmp_path):
        """C3 semantics: a failed batch URL is not marked visited, so a
        later batch retries it."""
        tb = _toolbox(tmp_path)
        urls = ["https://example.com/flaky"]
        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=[("https://example.com/flaky", None, "boom", None)],
        ):
            first = json.loads(tb.batch_inspect_pages(urls))
        assert first[0]["error"] == "boom"

        with patch(
            "stitch_web_researcher.fetch.batch_research",
            return_value=[("https://example.com/flaky", FAKE_HTML, FAKE_MD, FAKE_LINKS)],
        ):
            second = json.loads(tb.batch_inspect_pages(urls))
        assert second[0]["markdown"] == FAKE_MD

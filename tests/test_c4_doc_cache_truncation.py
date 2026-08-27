"""C4 regression: extract_document cache hits must honor the budget.

Before the fix, the fresh-fetch path truncated content but the cache-hit
path returned the stored content verbatim — so a second call (or a call
after shrinking the budget) could return a far larger payload than the
configured max_markdown_chars / max_tokens.
"""

import json
from unittest.mock import patch

from stitch_web_researcher.agent_tools import WebResearcherToolbox

BIG = "report body " * 6000  # ~72k chars, well above the 8000-char default


def _toolbox(tmp_path, **overrides) -> WebResearcherToolbox:
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"), domain_delay=0.0, ddgs_delay=0.0, **overrides
    )


class TestDocumentCacheTruncation:
    def test_cache_hit_respects_current_char_budget(self, tmp_path):
        tb = _toolbox(tmp_path)
        src = str(tmp_path / "report.pdf")
        with patch.object(tb, "_extract_local", return_value=BIG):
            first = json.loads(tb.extract_document(src))

        assert first["cache_hit"] is False
        assert len(first["content"]) <= tb.max_markdown_chars + 21

        tb.max_markdown_chars = 500  # shrink the budget after caching
        second = json.loads(tb.extract_document(src))
        assert second["cache_hit"] is True
        assert len(second["content"]) <= 500 + len("\n\n... [truncated]")
        assert second["content"] != first["content"]

    def test_cache_hit_respects_current_token_budget(self, tmp_path):
        tb = _toolbox(tmp_path, max_tokens=2000, max_markdown_chars=1_000_000)
        src = str(tmp_path / "report.pdf")
        with patch.object(tb, "_extract_local", return_value=BIG):
            first = json.loads(tb.extract_document(src))
        assert first["content_tokens"] <= tb.max_tokens

        tb.max_tokens = 500
        second = json.loads(tb.extract_document(src))
        assert second["cache_hit"] is True
        assert second["content_tokens"] <= 500
        assert second["content_tokens"] < first["content_tokens"]

    def test_fresh_fetch_still_truncates(self, tmp_path):
        tb = _toolbox(tmp_path)
        src = str(tmp_path / "report.pdf")
        with patch.object(tb, "_extract_local", return_value=BIG):
            data = json.loads(tb.extract_document(src))
        assert data["cache_hit"] is False
        assert data["content"].endswith("\n\n... [truncated]")
        assert len(data["content"]) <= tb.max_markdown_chars + 21

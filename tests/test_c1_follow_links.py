"""C1 regression: content-rich pages must still deliver follow-up links.

The bug: markdown was truncated to *exactly* the envelope budget, so the
serialized payload started over budget before any link was added; the
budget loop then dropped every link and returned an over-budget payload.
"""

import json
from unittest.mock import patch

import pytest

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.token_budget import count_tokens

URL = "https://example.com/big"


def _toolbox(tmp_path, **overrides) -> WebResearcherToolbox:
    # respect_robots=False: these tests mock the fetch layer with fake
    # example.com URLs, so no live robots.txt probe may run (S4).
    cfg = ToolboxConfig(
        cache_dir=str(tmp_path / "cache"), respect_robots=False, **overrides
    )
    return WebResearcherToolbox(cfg)


def _make_links(n_pages: int = 240, n_docs: int = 60):
    links = [(f"https://example.com/p{i}", f"page link {i}") for i in range(n_pages)]
    links += [(f"/media/doc{i}.pdf", f"report {i}") for i in range(n_docs)]
    return links


class TestCharBudget:
    def test_content_rich_page_delivers_links_within_budget(self, tmp_path):
        """Repro at defaults (max_markdown_chars=8000), page with 300 links:
        before the fix, 0 links were delivered and the payload was 8201 chars
        (over the 8000 budget). Now at least one link survives AND the
        payload fits the budget."""
        tb = _toolbox(tmp_path)  # defaults: 8000 chars, 0 tokens
        links = _make_links()
        md = "research prose " * 800  # ~11k chars

        with patch.object(
            tb._fetch, "_fetch_html", return_value=(md, links, {}, "static")
        ):
            raw = tb.inspect_html_page(URL)

        data = json.loads(raw)
        assert data["truncated"] is True
        assert data["follow_up_links"], "no links delivered on a content-rich page"
        assert data["delivered_links"] == len(data["follow_up_links"]) >= 1
        assert data["delivered_links"] < data["total_links"]
        assert len(raw) <= tb.max_markdown_chars, (
            f"payload {len(raw)} chars exceeds budget {tb.max_markdown_chars}"
        )

    def test_small_page_loses_nothing(self, tmp_path):
        tb = _toolbox(tmp_path)
        links = _make_links(3, 1)
        with patch.object(
            tb._fetch, "_fetch_html", return_value=("short page\n", links, {}, "static")
        ):
            data = json.loads(tb.inspect_html_page(URL))
        assert len(data["follow_up_links"]) == 4
        assert data["truncated"] is False
        assert data["delivered_links"] == 4


class TestTokenBudget:
    def test_token_budget_delivers_links(self, tmp_path):
        """max_tokens=4000, max_markdown_chars=200000: before the fix,
        0 links were delivered at exactly 4000 markdown tokens."""
        tb = _toolbox(tmp_path, max_tokens=4000, max_markdown_chars=200_000)
        links = _make_links()
        md = "word " * 20_000  # far above a 4000-token budget

        with patch.object(
            tb._fetch, "_fetch_html", return_value=(md, links, {}, "static")
        ):
            raw = tb.inspect_html_page(URL)

        data = json.loads(raw)
        assert data["follow_up_links"], "token budget must not starve links"
        assert count_tokens(raw, tb.model_name) <= tb.max_tokens
        assert data["delivered_links"] >= 1


class TestBudgetReserveConfig:
    def test_ratio_validated(self, tmp_path):
        with pytest.raises(ValueError):
            ToolboxConfig(cache_dir=str(tmp_path), link_budget_ratio=1.0)
        with pytest.raises(ValueError):
            ToolboxConfig(cache_dir=str(tmp_path), link_budget_ratio=-0.1)

    def test_full_reserve_gives_links_max_room(self, tmp_path):
        tb = _toolbox(tmp_path, link_budget_ratio=0.5, max_links=20)
        links = _make_links()
        md = "research prose " * 400  # ~5.6k chars
        with patch.object(
            tb._fetch, "_fetch_html", return_value=(md, links, {}, "static")
        ):
            data = json.loads(tb.inspect_html_page(URL))
        # More reserve -> more links survive than with the 0.25 default.
        tb_default = _toolbox(tmp_path)
        with patch.object(
            tb_default._fetch, "_fetch_html", return_value=(md, links, {}, "static")
        ):
            data_default = json.loads(tb_default.inspect_html_page(URL))
        assert len(data["follow_up_links"]) >= len(data_default["follow_up_links"])

# tests/test_m11_budget_loop.py
"""M11 — the output-budget loop must not re-tokenize the entire
payload on every halving pass.

Before the fix, _build_inspection_result called count_tokens() on the
full serialized JSON up to ~9 times (once per halving). Now it
tokenizes a bounded, constant number of times: the link-less envelope
plus the full payload up front, plus at most a couple of exact
verifications. The final payload must still satisfy both budgets.
"""

import json
from unittest import mock

import stitch_web_researcher.agent_tools as agent_tools
import stitch_web_researcher.fetch as fetch
from stitch_web_researcher.agent_tools import WebResearcherToolbox
from stitch_web_researcher.token_budget import count_tokens


def _links(n):
    return [(f"https://example.com/page/{i}", f"Page {i}") for i in range(n)]


def _toolbox(tmp_path, **kw):
    kw.setdefault("cache_dir", str(tmp_path / "cache"))
    kw.setdefault("respect_robots", False)
    return WebResearcherToolbox(**kw)


def _counting_tokens():
    """Patch agent_tools.count_tokens with a call-counting wrapper."""
    calls = {"n": 0}

    def counting(text, model=None):
        calls["n"] += 1
        return count_tokens(text, model)

    return mock.patch.object(fetch, "count_tokens", counting), calls


class TestBudgetLoopTokenization:
    def test_tokenization_count_is_bounded(self, tmp_path):
        """64 links + a large markdown: the old loop could tokenize the
        whole payload up to ~9 times; the new loop must stay well below
        that."""
        tb = _toolbox(tmp_path, max_tokens=4000, max_markdown_chars=200_000)
        md = "word " * 20_000  # far above a 4000-token budget
        patcher, calls = _counting_tokens()
        with patcher:
            with mock.patch.object(
                tb._fetch, "_fetch_html", return_value=(md, _links(64), {}, "static")
            ):
                raw = tb.inspect_html_page("https://example.com/budget")

        data = json.loads(raw)
        # Correctness invariants (C1): budget respected, links not starved.
        assert count_tokens(raw, tb.model_name) <= tb.max_tokens
        assert data["follow_up_links"], "token budget must not starve links"
        assert data["delivered_links"] >= 1
        # Performance invariant: tokenizations bounded (envelope + full
        # payload + at most a couple of exact verifications, plus the
        # markdown_tokens count on entry).
        assert calls["n"] <= 6, f"too many tokenizations: {calls['n']}"

    def test_no_tokenization_in_loop_when_max_tokens_zero(self, tmp_path):
        """Default config (max_tokens=0): char-only budget; the loop
        adds no tokenizations beyond the markdown_tokens count."""
        tb = _toolbox(tmp_path)
        assert tb.max_tokens == 0
        patcher, calls = _counting_tokens()
        with patcher:
            with mock.patch.object(
                tb._fetch, "_fetch_html",
                return_value=("short\n", _links(8), {}, "static"),
            ):
                raw = tb.inspect_html_page("https://example.com/plain")

        data = json.loads(raw)
        assert data["follow_up_links"]
        assert calls["n"] == 1  # only markdown_tokens
        assert data["markdown_tokens"] == count_tokens("short\n", tb.model_name)

    def test_tight_budget_drops_links_but_stays_in_budget(self, tmp_path):
        """A token budget barely above the link-less envelope: every
        link is dropped, the payload still fits the budget, and the
        truncated flag records the loss."""
        md = "small page\n"
        # Measure the link-less envelope on a pristine toolbox.
        tb_env = _toolbox(tmp_path / "env", max_tokens=10_000, max_markdown_chars=200_000)
        with mock.patch.object(
            tb_env._fetch, "_fetch_html", return_value=(md, _links(0), {}, "static")
        ):
            envelope_raw = tb_env.inspect_html_page("https://example.com/env")
        envelope_tokens = count_tokens(envelope_raw, tb_env.model_name)

        # Tight toolbox: budget that fits the envelope but not the
        # envelope + one link.
        tb = _toolbox(
            tmp_path,
            max_tokens=envelope_tokens + 2,
            max_markdown_chars=200_000,
        )
        with mock.patch.object(
            tb._fetch, "_fetch_html", return_value=(md, _links(16), {}, "static")
        ):
            raw = tb.inspect_html_page("https://example.com/env")

        data = json.loads(raw)
        assert count_tokens(raw, tb.model_name) <= tb.max_tokens
        assert len(raw) <= tb.max_markdown_chars
        assert data["delivered_links"] == 0
        assert data["truncated"] is True

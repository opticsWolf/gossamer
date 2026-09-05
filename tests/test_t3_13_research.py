"""Tier 3.13 -- research orchestration primitive (item 13).

Review item 13 (CODE_REVIEW_2026-08-27): the toolbox is a good set of
verbs but has no research(topic, depth, budget) that plans, fans out,
dedupes, and returns a cited synthesis. Decision: the plan / fan-out /
dedupe / citation scaffolding lives in the toolbox (it is I/O and budget
logic); the prose synthesis stays with the calling agent, which receives
per-source content, provenance, and search metadata to cite. All tests
are deterministic: providers and page fetches are faked, no network.

Note: candidate URLs use example.com paths because the S1 SSRF guard
resolves DNS and this test environment is offline (only the apex
example.com resolves).
"""
from __future__ import annotations

import json

from gossamer.agent_tools import (
    TOOL_REGISTRY,
    ToolboxConfig,
    WebResearcherToolbox,
)


class FakeProvider:
    """Minimal stand-in for a search provider (records every search call)."""

    def __init__(self, name, results=None, exc=None):
        self.name = name
        self._results = results or []
        self._exc = exc
        self.calls = []

    def search(self, query, max_results=5):
        self.calls.append((query, max_results))
        if self._exc is not None:
            raise self._exc
        return [dict(r) for r in self._results][:max_results]


def _toolbox(tmp_path, **config_kwargs):
    # fetch_delay=0.0 pins the fetch interval even when tests swap in a
    # fake provider after construction (the rate-limit resolution prefers
    # the active provider's RateLimit over domain_delay); ddgs_delay=0.0
    # skips the post-search sleep.
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            domain_delay=0.0,
            fetch_delay=0.0,
            ddgs_delay=0.0,
            cache_dir=str(tmp_path / "cache"),
            **config_kwargs,
        )
    )


def _results(*items):
    """(title, url, snippet?) triples or raw dicts -> provider results."""
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append(dict(item))
        else:
            title, url = item[0], item[1]
            snippet = item[2] if len(item) > 2 else f"snippet for {title}"
            out.append({"title": title, "url": url, "snippet": snippet})
    return out


def _fake_fetch(text="# fetched content\n\nbody text", fail_on=()):
    state = {"calls": []}

    def fake(url, use_smart=None):
        state["calls"].append(url)
        if url in fail_on:
            raise RuntimeError(f"connection refused: {url}")
        return (text, [], {}, "static")

    fake.state = state
    return fake


class TestPlanningAndFanout:
    def test_search_dedupe_and_fetch(self, tmp_path):
        """Duplicates and invalid URLs are dropped; the rest are fetched."""
        tb = _toolbox(tmp_path)
        prov = FakeProvider(
            "duckduckgo",
            results=_results(
                ("A1", "https://example.com/a"),
                ("A2", "https://example.com/a/"),  # dup (trailing slash)
                ("B1", "https://example.com/b"),
                ("C1", "ftp://example.com/z"),  # non-http scheme: dropped
                {"title": "no url"},  # missing url: dropped
            ),
        )
        tb.providers = [prov]
        fetch = _fake_fetch()
        tb._fetch._fetch_html = fetch

        result = json.loads(tb.research("topic one", depth=3))

        assert result["topic"] == "topic one"
        assert result["depth"] == 3
        assert [s["url"] for s in result["sources"]] == [
            "https://example.com/a",
            "https://example.com/b",
        ]
        assert result["count"] == 2
        for s in result["sources"]:
            assert s["status"] == "ok"
            assert s["result"]["markdown"] == "# fetched content\n\nbody text"
            assert s["snippet"]  # search snippet carried for citation
        # Search was planned with up to depth*2 candidates.
        assert prov.calls == [("topic one", 6)]
        assert fetch.state["calls"] == [
            "https://example.com/a",
            "https://example.com/b",
        ]

    def test_depth_caps_candidates(self, tmp_path):
        tb = _toolbox(tmp_path)
        results = _results(*[
            (f"P{i}", f"https://example.com/p{i}") for i in range(6)
        ])
        tb.providers = [FakeProvider("duckduckgo", results=results)]
        tb._fetch._fetch_html = _fake_fetch()

        result = json.loads(tb.research("cap", depth=3))

        assert result["depth"] == 3
        assert len(result["sources"]) == 3
        assert [s["url"] for s in result["sources"]] == [
            "https://example.com/p0",
            "https://example.com/p1",
            "https://example.com/p2",
        ]

    def test_depth_hard_cap(self, tmp_path):
        # Large budget: the assertion needs the full (untruncated) JSON.
        tb = _toolbox(tmp_path, max_markdown_chars=200000)
        results = _results(*[
            (f"P{i}", f"https://example.com/p{i}") for i in range(25)
        ])
        prov = FakeProvider("duckduckgo", results=results)
        tb.providers = [prov]
        tb._fetch._fetch_html = _fake_fetch()

        result = json.loads(tb.research("cap", depth=50))

        assert result["depth"] == WebResearcherToolbox._RESEARCH_MAX_PAGES
        assert len(result["sources"]) == WebResearcherToolbox._RESEARCH_MAX_PAGES
        # The search itself is capped at 20 results.
        assert prov.calls[0][1] == 20

    def test_fetch_error_is_isolated(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb.providers = [
            FakeProvider(
                "duckduckgo",
                results=_results(
                    ("A1", "https://example.com/a"),
                    ("B1", "https://example.com/b"),
                    ("C1", "https://example.com/c"),
                ),
            )
        ]
        tb._fetch._fetch_html = _fake_fetch(fail_on={"https://example.com/b"})

        result = json.loads(tb.research("errors", depth=3))

        statuses = [s["status"] for s in result["sources"]]
        assert statuses == ["ok", "error", "ok"]
        assert result["count"] == 2
        assert "connection refused" in result["sources"][1]["error"]

    def test_empty_topic(self, tmp_path):
        tb = _toolbox(tmp_path)
        result = json.loads(tb.research("   "))
        assert "error" in result
        assert "topic" in result["error"]

    def test_search_failure_degrades_to_zero_sources(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb.providers = [FakeProvider("duckduckgo", exc=RuntimeError("down"))]
        fetch = _fake_fetch()
        tb._fetch._fetch_html = fetch

        result = json.loads(tb.research("unreachable", depth=3))

        assert result["sources"] == []
        assert result["count"] == 0
        assert "error" not in result
        assert fetch.state["calls"] == []

    def test_no_providers_configured(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb.providers = []

        result = json.loads(tb.research("nobody home", depth=2))

        assert result["sources"] == []
        assert result["count"] == 0


class TestBudgetsAndCaching:
    def test_response_respects_char_budget(self, tmp_path):
        long_text = "# big\n\n" + ("word " * 3000)
        tb = _toolbox(tmp_path, max_markdown_chars=1200)
        tb.providers = [
            FakeProvider(
                "duckduckgo",
                results=_results(
                    ("A1", "https://example.com/a"),
                    ("B1", "https://example.com/b"),
                ),
            )
        ]
        tb._fetch._fetch_html = _fake_fetch(text=long_text)

        out = tb.research("budget", depth=2)

        assert len(out) <= 1200 + len("\n\n... [truncated]")

    def test_max_tokens_param_is_honoured(self, tmp_path):
        long_text = "# big\n\n" + ("word " * 3000)
        tb = _toolbox(tmp_path)
        tb.providers = [
            FakeProvider(
                "duckduckgo",
                results=_results(("A1", "https://example.com/a")),
            )
        ]
        tb._fetch._fetch_html = _fake_fetch(text=long_text)

        out = tb.research("budget", depth=1, max_tokens=50)

        # 50 tokens is far below the content size, so the response must
        # be truncated well short of the raw source length.
        assert len(out) < len(long_text)
        assert out.startswith("{")

    def test_repeat_run_serves_cached_pages(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb.providers = [
            FakeProvider(
                "duckduckgo",
                results=_results(("A1", "https://example.com/a")),
            )
        ]
        fetch = _fake_fetch()
        tb._fetch._fetch_html = fetch

        r1 = json.loads(tb.research("repeat", depth=1))
        r2 = json.loads(tb.research("repeat", depth=1))

        assert r1["count"] == 1
        assert fetch.state["calls"] == ["https://example.com/a"]  # no re-fetch
        assert r2["count"] == 1
        # Second run still delivers content via the page cache.
        assert r2["sources"][0]["status"] == "ok"
        assert "markdown" in r2["sources"][0]["result"]


class TestDispatch:
    def test_execute_tool_dispatch(self, tmp_path):
        tb = _toolbox(tmp_path)
        tb.providers = [
            FakeProvider(
                "duckduckgo",
                results=_results(("A1", "https://example.com/a")),
            )
        ]
        tb._fetch._fetch_html = _fake_fetch()

        out = tb.execute_tool(
            "web_search", {"query": "dispatch me", "search_only": False}
        )

        result = json.loads(out)
        assert result["topic"] == "dispatch me"
        assert result["count"] == 1

    def test_registry_shape(self):
        spec = next(s for s in TOOL_REGISTRY if s.name == "web_search")
        assert spec.method == "web_search"
        by_name = {p.name: p for p in spec.params}
        # query replaces the old research `topic`; depth/max_tokens carry
        # over from the research parameters.
        assert "query" in by_name and by_name["query"].required
        assert by_name["depth"].default == 5
        assert by_name["max_tokens"].default == 0


class TestResearchProviderPassthrough:
    def test_research_uses_requested_provider(self, tmp_path):
        """web_search provider/max_results reach the research plan (A.8)."""
        tb = _toolbox(tmp_path)
        first = FakeProvider("duckduckgo", results=_results(("A", "https://example.com/a")))
        second = FakeProvider("bing", results=_results(("B", "https://example.com/b")))
        tb.providers = [first, second]
        tb._fetch._fetch_html = _fake_fetch()

        result = json.loads(tb.research("topic", depth=2, provider="bing", max_results=4))

        assert result["provider"] == "bing"
        assert second.calls == [("topic", 4)]
        assert first.calls == []
        assert [s["url"] for s in result["sources"]] == ["https://example.com/b"]

    def test_web_search_research_mode_forwards_provider(self, tmp_path):
        tb = _toolbox(tmp_path)
        prov = FakeProvider("duckduckgo", results=_results(("A", "https://example.com/a")))
        tb.providers = [prov]
        tb._fetch._fetch_html = _fake_fetch()

        result = json.loads(
            tb.web_search("topic", search_only=False, depth=2, provider="duckduckgo")
        )
        assert result["provider"] == "duckduckgo"
        # Explicit max_results (tool default 5) wins over depth*2.
        assert prov.calls == [("topic", 5)]

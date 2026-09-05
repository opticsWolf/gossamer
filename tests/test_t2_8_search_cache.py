"""Tier 2.8 -- search-result caching + cross-provider merge (item 8).

Review item 8 (CODE_REVIEW_2026-08-27): search results were never cached and
providers were strict failover (no dedup/merge). This adds (a) a result-level
cache key so repeat queries within the TTL do not re-query the provider, and
(b) URL dedup within a provider's results plus an optional cross-provider
merge (ToolboxConfig.search_merge). All tests are deterministic; providers are
faked so no network is touched.
"""
from __future__ import annotations

import json

from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox
from gossamer.search import SearchService
from gossamer.mcp_server import _config_from_env


class FakeProvider:
    """Minimal stand-in for a search provider (records every search call)."""

    def __init__(self, name, results=None, exc=None):
        self.name = name
        self._results = results or []
        self._exc = exc
        self.calls = []

    def search(self, query, max_results=5):
        self.calls.append(query)
        if self._exc is not None:
            raise self._exc
        return [dict(r) for r in self._results][:max_results]


def _toolbox(tmp_path, **config_kwargs):
    return WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            domain_delay=0.0,
            cache_dir=str(tmp_path / "cache"),
            **config_kwargs,
        )
    )


class TestResultUrlKey:
    def test_normalization(self):
        f = SearchService._result_url_key
        assert f({"url": "https://Example.com/a#frag"}) == "https://example.com/a"
        assert f({"url": "http://example.com:80/a/"}) == "http://example.com/a"
        assert f({"url": "https://example.com:443/a/"}) == "https://example.com/a"
        assert f({"url": "http://example.com:8080/a"}) == "http://example.com:8080/a"
        assert f({"url": "HTTPS://EXAMPLE.com/A?x=1"}) == "https://example.com/A?x=1"
        assert f({"url": ""}) == ""
        assert f({}) == ""
        assert f({"url": 123}) == ""

    def test_trailing_slash_equivalence(self):
        f = SearchService._result_url_key
        assert (
            f({"url": "https://example.com/path/"})
            == f({"url": "https://example.com/path"})
        )


class TestDedup:
    def test_dedup_by_url_preserving_order(self, tmp_path):
        tb = _toolbox(tmp_path)
        results = [
            {"title": "1", "url": "https://x.com/a"},
            {"title": "2", "url": "https://x.com/a/"},  # dup (trailing slash)
            {"title": "3", "url": "https://x.com/b"},
            {"title": "4"},  # no url, always kept
        ]
        out = tb._search._dedup_results(results)
        assert [r["title"] for r in out] == ["1", "3", "4"]

    def test_no_url_results_all_kept(self, tmp_path):
        tb = _toolbox(tmp_path)
        assert len(tb._search._dedup_results([{"title": "x"}, {"title": "y"}])) == 2


class TestSearchCache:
    def test_second_call_is_cache_hit(self, tmp_path):
        tb = _toolbox(tmp_path)
        prov = FakeProvider(
            "duckduckgo",
            results=[
                {"title": "A1", "url": "https://example.com/a", "snippet": "a1"},
                {"title": "A2", "url": "https://example.com/b", "snippet": "a2"},
            ],
        )
        tb.providers = [prov]
        out1 = tb.search_web("foo bar", max_results=5)
        out2 = tb.search_web("foo bar", max_results=5)
        assert out1 == out2
        assert len(prov.calls) == 1  # second call served from cache
        assert json.loads(out1) == [
            {"title": "A1", "url": "https://example.com/a", "snippet": "a1"},
            {"title": "A2", "url": "https://example.com/b", "snippet": "a2"},
        ]

    def test_cache_key_normalizes_query(self, tmp_path):
        tb = _toolbox(tmp_path)
        prov = FakeProvider("duckduckgo", results=[{"url": "https://example.com/a"}])
        tb.providers = [prov]
        tb.search_web("  Foo   Bar ", max_results=5)
        tb.search_web("foo bar", max_results=5)  # same normalized key
        assert len(prov.calls) == 1

    def test_different_max_results_misses(self, tmp_path):
        tb = _toolbox(tmp_path)
        prov = FakeProvider("duckduckgo", results=[{"url": "https://example.com/a"}])
        tb.providers = [prov]
        tb.search_web("foo", max_results=3)
        tb.search_web("foo", max_results=5)  # different key
        assert len(prov.calls) == 2

    def test_error_not_cached(self, tmp_path):
        tb = _toolbox(tmp_path)
        prov = FakeProvider("duckduckgo", exc=RuntimeError("boom"))
        tb.providers = [prov]
        out1 = tb.search_web("foo")
        out2 = tb.search_web("foo")
        assert "error" in json.loads(out1)
        assert "error" in json.loads(out2)
        assert len(prov.calls) == 2  # error is not cached, so it is retried

    def test_failover_dedups_within_provider(self, tmp_path):
        tb = _toolbox(tmp_path)
        bad = FakeProvider("duckduckgo", exc=RuntimeError("down"))
        good = FakeProvider(
            "bing",
            results=[
                {"title": "B1", "url": "https://example.org/b/"},
                {"title": "B2", "url": "https://example.org/c"},
                {"title": "dup", "url": "https://example.org/c/"},  # dup of B2
            ],
        )
        tb.providers = [bad, good]
        out = tb.search_web("foo")
        assert bad.calls == ["foo"]
        assert good.calls == ["foo"]
        urls = [r["url"] for r in json.loads(out)]
        assert urls.count("https://example.org/c") == 1
        assert len(urls) == 2  # b, c (the dup is removed)


class TestSearchCacheIsolation:
    def test_no_leak_across_toolbox_instances(self, tmp_path):
        """A fresh toolbox must not see another instance's search cache
        (Tier 2.8): the search cache is in-memory and per-instance, so a
        failing provider on a new toolbox still returns an error for a
        query that a *different* toolbox cached successfully -- even when
        both share the same on-disk cache directory."""
        good = FakeProvider(
            "duckduckgo", results=[{"title": "A", "url": "https://a.com"}]
        )
        tb_a = _toolbox(tmp_path)
        tb_a.providers = [good]
        r1 = json.loads(tb_a.search_web("shared query"))
        assert r1[0]["url"] == "https://a.com"

        down = FakeProvider("duckduckgo", exc=RuntimeError("down"))
        tb_b = _toolbox(tmp_path)  # same cache dir, on purpose
        tb_b.providers = [down]
        r2 = json.loads(tb_b.search_web("shared query"))
        assert "error" in r2


class TestMergeMode:
    def test_search_merge_defaults_false(self):
        assert ToolboxConfig(cache_dir="/tmp/x").search_merge is False

    def test_config_propagates_to_toolbox(self, tmp_path):
        tb = _toolbox(tmp_path, search_merge=True)
        assert tb._search_merge is True

    def test_merge_combines_and_dedups(self, tmp_path):
        tb = _toolbox(tmp_path, search_merge=True)
        pa = FakeProvider(
            "duckduckgo",
            results=[
                {"title": "A1", "url": "https://example.com/a"},
                {"title": "A2", "url": "https://example.com/b"},
            ],
        )
        pb = FakeProvider(
            "bing",
            results=[
                {"title": "B1", "url": "https://example.com/b"},  # dup of A2
                {"title": "B2", "url": "https://example.org/c"},
            ],
        )
        tb.providers = [pa, pb]
        out = tb.search_web("foo", max_results=5)
        assert pa.calls and pb.calls  # both providers queried
        urls = [r["url"] for r in json.loads(out)]
        assert urls == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.org/c",
        ]

    def test_merge_stops_at_max_results(self, tmp_path):
        tb = _toolbox(tmp_path, search_merge=True)
        pa = FakeProvider(
            "duckduckgo",
            results=[{"url": f"https://a.com/{i}"} for i in range(5)],
        )
        pb = FakeProvider(
            "bing",
            results=[{"url": f"https://b.com/{i}"} for i in range(5)],
        )
        tb.providers = [pa, pb]
        out = tb.search_web("foo", max_results=3)
        assert len(json.loads(out)) == 3
        assert len(pa.calls) == 1
        assert pb.calls == []  # second provider never needed

    def test_merge_all_fail_returns_error(self, tmp_path):
        tb = _toolbox(tmp_path, search_merge=True)
        tb.providers = [
            FakeProvider("duckduckgo", exc=RuntimeError("x")),
            FakeProvider("bing", exc=RuntimeError("y")),
        ]
        out = tb.search_web("foo")
        assert "error" in json.loads(out)


class TestEnvKnob:
    def test_search_merge_env(self, monkeypatch):
        monkeypatch.delenv("GOSSAMER_SEARCH_MERGE", raising=False)
        assert _config_from_env().search_merge is False
        monkeypatch.setenv("GOSSAMER_SEARCH_MERGE", "1")
        assert _config_from_env().search_merge is True
        monkeypatch.setenv("GOSSAMER_SEARCH_MERGE", "false")
        assert _config_from_env().search_merge is False

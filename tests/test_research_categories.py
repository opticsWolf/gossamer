"""research_categories: keyword -> category -> provider routing.

Covers the classification heuristic and both routing paths used by the
``research_by_category`` tool: the engine fallback (delegates to
``tb.search_web``) and the domain-adapter path (instantiates the adapter and
calls ``search()`` directly). No real network calls are made.
"""

import json

import pytest

from stitch_web_researcher import research_categories as rc
from stitch_web_researcher.research_providers import (
    OpenAlexAdapter,
    OpenMeteoAdapter,
)


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("a 2019 peer reviewed paper on ML", "scholarly"),
        ("find a research paper with a doi", "scholarly"),
        ("citation count for a journal article", "scholarly"),
        ("weather forecast for paris tomorrow", "geo"),
        ("temperature and coordinates in berlin", "geo"),
        ("zip code lookup", "geo"),
        ("latest breaking news today", "general"),
        ("what is the capital city", "general"),
    ],
)
def test_classify_routes_to_expected_category(query, expected):
    assert rc.classify(query).name == expected


def test_general_is_the_fallback():
    # No trigger keywords at all -> general.
    assert rc.classify("xyzzy random words").name == "general"
    assert rc.DEFAULT_CATEGORY.name == "general"
    assert rc.DEFAULT_CATEGORY.is_fallback


def test_word_boundary_guard_does_not_overmatch():
    # "late"/"validate" contain "lat" but must NOT match the geo token.
    assert rc.classify("late breaking news").name == "general"
    assert rc.classify("validate the plan").name == "general"
    # "university" contains no trigger word on its own here; a bare "news"
    # query should stay general.
    assert rc.classify("news").name == "general"


def test_resolve_is_an_alias_for_classify():
    assert rc.resolve is rc.classify


# ---------------------------------------------------------------------------
# search_category() -- engine fallback path
# ---------------------------------------------------------------------------


class _FakeTb:
    """Records the single ``search_web`` call the engine path makes."""

    def __init__(self):
        self.calls = []

    def search_web(self, query, max_results=5, provider=None):
        self.calls.append({"query": query, "max_results": max_results, "provider": provider})
        return json.dumps([{"title": "engine hit", "url": "http://example"}])


def test_search_category_engine_path_delegates_to_search_web():
    tb = _FakeTb()
    out = rc.search_category(tb, "latest breaking news", max_results=3)

    assert out["category"] == "general"
    assert out["provider"] == "duckduckgo"
    assert out["provider_kind"] == "engine"
    assert out["results"] == [{"title": "engine hit", "url": "http://example"}]
    # The toolbox search path received the category's provider + max_results.
    assert tb.calls == [{"query": "latest breaking news", "max_results": 3, "provider": "duckduckgo"}]


# ---------------------------------------------------------------------------
# search_category() -- adapter path (mocked, no network)
# ---------------------------------------------------------------------------


def test_search_category_adapter_path_instantiates_and_searches(monkeypatch):
    seen = {}

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                seen["query"] = query
                seen["max_results"] = max_results
                seen["provider"] = provider
                return [{"source": provider, "title": "mock work"}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    out = rc.search_category(object(), "a citation-heavy paper on graphs", max_results=2)

    assert out["category"] == "scholarly"
    assert out["provider"] == "openalex"
    assert out["provider_kind"] == "adapter"
    assert out["results"] == [{"source": "openalex", "title": "mock work"}]
    assert seen == {"query": "a citation-heavy paper on graphs", "max_results": 2, "provider": "openalex"}


def test_search_category_adapter_failure_is_surfaced_not_raised(monkeypatch):
    def boom(_provider):
        raise RuntimeError("network down")

    monkeypatch.setattr(rc, "_make_adapter", boom)

    out = rc.search_category(object(), "a paper on graphs")
    assert out["category"] == "scholarly"
    assert isinstance(out["results"], dict)
    assert "error" in out["results"]


def test_make_adapter_returns_real_adapter_classes():
    assert isinstance(rc._make_adapter("openalex"), OpenAlexAdapter)
    assert isinstance(rc._make_adapter("open-meteo"), OpenMeteoAdapter)


def test_unknown_adapter_raises():
    with pytest.raises(ValueError):
        rc._make_adapter("does-not-exist")


def test_describe_categories_reflects_every_category():
    # Drift guard: the auto-generated description must mention every category
    # and provider, so adding a CATEGORIES entry can't be silently dropped
    # from the LLM-facing contract.
    text = rc.describe_categories()
    # The description uses provider *display* names (OpenAlex), not ids
    # (openalex) -- the LLM receives a category, never a provider id -- so
    # assert on the display name.
    for c in rc.CATEGORIES:
        assert c.name in text, c.name
        assert rc._display(c.provider) in text, c.provider


def test_facade_research_categories_returns_taxonomy():
    from stitch_web_researcher.agent_tools import WebResearcherToolbox

    tb = WebResearcherToolbox(
        cache_dir="/tmp/x",
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )
    data = json.loads(tb.research_categories())
    assert {d["category"] for d in data} == {"scholarly", "geo", "general"}
    by_name = {d["category"]: d for d in data}
    assert by_name["scholarly"]["provider"] == "openalex"
    assert by_name["geo"]["provider"] == "open-meteo"
    assert by_name["general"]["provider"] == "duckduckgo"


# ---------------------------------------------------------------------------
# facade integration: research_by_category returns JSON
# ---------------------------------------------------------------------------


def test_facade_research_by_category_returns_json(tmp_path, monkeypatch):
    from stitch_web_researcher.agent_tools import WebResearcherToolbox

    tb = WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )

    # Route the scholarly path through a mocked adapter so no network is hit.
    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                return [{"source": provider, "title": "mock"}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    payload = tb.research_by_category("a peer reviewed paper on graphs", max_results=4)
    data = json.loads(payload)

    assert data["category"] == "scholarly"
    assert data["provider"] == "openalex"
    assert data["query"] == "a peer reviewed paper on graphs"
    assert data["results"] == [{"source": "openalex", "title": "mock"}]


def test_research_categories_is_not_an_mcp_tool(tmp_path):
    # The introspection method is callable directly but is NOT part of the
    # MCP surface: execute_tool must reject it (not registered).
    from stitch_web_researcher.agent_tools import TOOL_REGISTRY, WebResearcherToolbox

    assert not any(s.name == "research_categories" for s in TOOL_REGISTRY)
    tb = WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )
    import pytest

    with pytest.raises(ValueError):
        tb.execute_tool("research_categories", {})

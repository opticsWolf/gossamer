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


def test_search_category_default_provider_when_omitted(monkeypatch):
    seen = {}

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                seen["provider"] = provider
                return [{"source": provider}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    out = rc.search_category(object(), "a peer reviewed paper on graphs")
    assert out["category"] == "scholarly"
    assert out["provider"] == "openalex"
    assert seen["provider"] == "openalex"


def test_search_category_explicit_provider_calls_that_source(monkeypatch):
    seen = {}

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                seen["provider"] = provider
                return [{"source": provider}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    out = rc.search_category(
        object(), "a citation on arxiv", provider="crossref", max_results=2
    )
    assert out["category"] == "scholarly"
    assert out["provider"] == "crossref"
    assert seen["provider"] == "crossref"
    assert out["available_providers"] == ["openalex", "crossref", "arxiv"]


def test_search_category_provider_mismatch_with_category_is_rejected():
    # category + provider explicitly given but inconsistent (ecfr is legal,
    # not scholarly) is reported as an error, never raised, and never
    # contacts any adapter. The caller over-determined both values.
    out = rc.search_category(
        object(),
        "a peer reviewed paper on graphs",
        category="scholarly",
        provider="ecfr",
    )
    assert out["category"] == "scholarly"
    assert out["provider"] == "ecfr"
    assert "error" in out
    assert "not available" in out["error"]
    assert out["results"] == []


def test_search_category_provider_only_reverse_resolves_owning_category(monkeypatch):
    # A provider given alone must NOT reclassify the query: its owning
    # category is used directly, so an arxiv call on a non-scholarly query
    # still resolves to scholarly instead of being rejected.
    seen = {}

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                seen["provider"] = provider
                return [{"source": provider}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    out = rc.search_category(
        object(), "late breaking news about markets", provider="arxiv"
    )
    assert out["category"] == "scholarly"
    assert out["provider"] == "arxiv"
    assert "error" not in out
    assert seen["provider"] == "arxiv"
    assert out["available_providers"] == ["openalex", "crossref", "arxiv"]


def test_search_category_unknown_provider_is_rejected_not_raised():
    # A provider that belongs to no category is reported as an error.
    out = rc.search_category(object(), "a peer reviewed paper on graphs", provider="bogus")
    assert out["provider"] == "bogus"
    assert "error" in out
    assert out["results"] == []


def test_search_category_unknown_category_is_rejected_not_raised():
    # An unknown category name is reported as an error, never raised.
    out = rc.search_category(
        object(), "a peer reviewed paper on graphs", category="bogus"
    )
    assert out["category"] == "bogus"
    assert "error" in out
    assert out["results"] == []


def test_search_category_category_and_provider_both_respected(monkeypatch):
    # category + a valid provider within it: both honoured, query untouched.
    seen = {}

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                seen["provider"] = provider
                return [{"source": provider}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    out = rc.search_category(
        object(), "any topic at all", category="legal", provider="ecfr"
    )
    assert out["category"] == "legal"
    assert out["provider"] == "ecfr"
    assert "error" not in out
    assert seen["provider"] == "ecfr"


def test_search_category_does_not_classify_when_category_given(monkeypatch):
    # The classifier must NOT run when a category is given explicitly.
    calls = {"n": 0}
    orig = rc.classify

    def spy(query):
        calls["n"] += 1
        return orig(query)

    monkeypatch.setattr(rc, "classify", spy)

    out = rc.search_category(object(), "any topic", category="legal")
    assert calls["n"] == 0
    assert out["category"] == "legal"


def test_search_category_does_not_classify_when_provider_given(monkeypatch):
    # The classifier must NOT run when a provider is given alone.
    calls = {"n": 0}
    orig = rc.classify

    def spy(query):
        calls["n"] += 1
        return orig(query)

    monkeypatch.setattr(rc, "classify", spy)

    out = rc.search_category(object(), "any topic", provider="arxiv")
    assert calls["n"] == 0
    assert out["category"] == "scholarly"


def test_search_category_classifies_when_nothing_given(monkeypatch):
    # With neither category nor provider, the classifier IS used.
    calls = {"n": 0}
    orig = rc.classify

    def spy(query):
        calls["n"] += 1
        return orig(query)

    monkeypatch.setattr(rc, "classify", spy)

    out = rc.search_category(object(), "AAPL stock quote today")
    assert calls["n"] == 1
    assert out["category"] == "financial"


def test_search_category_legal_routes_to_default_provider(monkeypatch):
    seen = {}

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                seen["provider"] = provider
                return [{"source": provider}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    out = rc.search_category(object(), "section 2 of the code of federal regulations")
    assert out["category"] == "legal"
    assert out["provider"] == "courtlistener"
    assert seen["provider"] == "courtlistener"


def test_search_category_financial_routes_to_default_provider(monkeypatch):
    seen = {}

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                seen["provider"] = provider
                return [{"source": provider}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    out = rc.search_category(object(), "AAPL stock quote today")
    assert out["category"] == "financial"
    assert out["provider"] == "alphavantage"
    assert seen["provider"] == "alphavantage"


def test_every_adapter_category_provider_has_a_registered_factory():
    # Drift guard: every provider named in an adapter category must resolve
    # through the factory, so a category can't list a source that isn't wired.
    for c in rc.CATEGORIES:
        if c.kind != "adapter":
            continue  # engine categories (general) use the toolbox search path
        for p in c.providers:
            assert p in rc._ADAPTER_FACTORIES, p


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
    # The description lists each provider as "DisplayName (id)" so the model
    # sees the friendly name *and* the exact id to pass as provider=<id> --
    # assert on both the display name and the raw id.
    for c in rc.CATEGORIES:
        assert c.name in text, c.name
        for p in c.providers:
            assert rc._display(p) in text, p
            assert f"({p})" in text, p


def test_facade_research_categories_returns_taxonomy():
    from stitch_web_researcher.agent_tools import WebResearcherToolbox

    tb = WebResearcherToolbox(
        cache_dir="/tmp/x",
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )
    data = json.loads(tb.research_categories())
    assert {d["category"] for d in data} == {
        "scholarly", "legal", "financial", "geo", "general",
    }
    by_name = {d["category"]: d for d in data}
    assert by_name["scholarly"]["default_provider"] == "openalex"
    assert by_name["scholarly"]["providers"] == ["openalex", "crossref", "arxiv"]
    assert by_name["legal"]["providers"] == [
        "courtlistener", "ecfr", "federalregister", "eurlex", "german",
    ]
    assert by_name["financial"]["providers"] == ["alphavantage", "yahoo"]
    assert by_name["geo"]["default_provider"] == "open-meteo"
    assert by_name["general"]["default_provider"] == "duckduckgo"
    assert by_name["general"]["provider_kind"] == "engine"


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


def test_facade_research_by_category_provider_skips_classification(tmp_path, monkeypatch):
    # Passing provider= alone must resolve its owning category and NOT
    # reclassify the (non-scholarly) query -- it should still land on arxiv.
    from stitch_web_researcher.agent_tools import WebResearcherToolbox

    tb = WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                return [{"source": provider}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    payload = tb.research_by_category("late breaking markets news", provider="arxiv")
    data = json.loads(payload)
    assert data["category"] == "scholarly"
    assert data["provider"] == "arxiv"
    assert data["query"] == "late breaking markets news"
    assert data["results"] == [{"source": "arxiv"}]


def test_facade_research_by_category_category_and_provider(tmp_path, monkeypatch):
    # category + provider both honoured; query untouched and not reclassified.
    from stitch_web_researcher.agent_tools import WebResearcherToolbox

    tb = WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )

    def fake_make_adapter(provider):
        class _FakeAdapter:
            def search(_self, query, max_results=5):
                return [{"source": provider}]

        return _FakeAdapter()

    monkeypatch.setattr(rc, "_make_adapter", fake_make_adapter)

    payload = tb.research_by_category("any topic", category="legal", provider="ecfr")
    data = json.loads(payload)
    assert data["category"] == "legal"
    assert data["provider"] == "ecfr"
    assert data["query"] == "any topic"
    assert data["results"] == [{"source": "ecfr"}]


def test_facade_research_by_category_bad_category_returns_error(tmp_path):
    # An unknown category is reported as JSON error, never raised.
    from stitch_web_researcher.agent_tools import WebResearcherToolbox

    tb = WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )

    payload = tb.research_by_category("a topic", category="bogus")
    data = json.loads(payload)
    assert data["category"] == "bogus"
    assert "error" in data
    assert data["results"] == []


def test_research_categories_is_an_mcp_tool(tmp_path):
    # The introspection method is registered as an MCP tool so the model can
    # fetch the live taxonomy on demand; execute_tool must dispatch it.
    from stitch_web_researcher.agent_tools import TOOL_REGISTRY, WebResearcherToolbox

    assert any(s.name == "research_categories" for s in TOOL_REGISTRY)
    tb = WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )
    import json

    data = json.loads(tb.execute_tool("research_categories", {}))
    assert {d["category"] for d in data} == {
        "scholarly", "legal", "financial", "geo", "general",
    }
    by_name = {d["category"]: d for d in data}
    assert by_name["scholarly"]["providers"] == ["openalex", "crossref", "arxiv"]
    assert by_name["legal"]["providers"] == [
        "courtlistener", "ecfr", "federalregister", "eurlex", "german",
    ]

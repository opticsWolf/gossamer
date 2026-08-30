"""Offline tests for BrowserOxideSearchProvider (browser_oxide is mocked)."""

import sys
import types
from unittest.mock import MagicMock

import pytest

from stitch_web_researcher.search_providers import (
    BrowserOxideSearchProvider,
    RateLimit,
    resolve_provider_name,
)

SAMPLE_SERP = """
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=xyz">
      Example <b>Result</b> A
    </a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Snippet <em>one</em> text</a>
</div>
<div class="result">
  <h2 class="result__title">
    <a class="result__a" href="https://direct.example.org/b">Direct Result B</a>
  </h2>
  <a class="result__snippet" href="#">Snippet two &amp; more</a>
</div>
"""


@pytest.fixture()
def provider(monkeypatch):
    """Provider whose browser returns a canned SERP."""
    fake_module = types.ModuleType("browser_oxide")
    fake_page = MagicMock()
    fake_page.is_challenge = False
    fake_page.verdict = "pass"
    fake_page.html = SAMPLE_SERP
    fake_browser = MagicMock()
    fake_browser.navigate.return_value = fake_page
    fake_profile = MagicMock()
    fake_module.Browser = MagicMock(return_value=fake_browser)
    fake_module.Profile = fake_profile
    monkeypatch.setitem(sys.modules, "browser_oxide", fake_module)

    p = BrowserOxideSearchProvider(delay=0.0)
    yield p
    p.close()


def test_parse_results_extracts_triples():
    triples = BrowserOxideSearchProvider._parse_results(SAMPLE_SERP)
    assert len(triples) == 2
    title_a, url_a, snippet_a = triples[0]
    assert "Example" in title_a and "Result" in title_a and "<b>" not in title_a
    assert "uddg=" in url_a
    assert "<em>" not in snippet_a


def test_search_unwraps_redirects_and_limits(provider):
    out = provider.search("anything", max_results=5)
    assert len(out) == 2
    first = out[0]
    assert first["url"] == "https://example.com/a"
    assert first["title"] == "Example Result A"
    assert first["snippet"] == "Snippet one text"
    second = out[1]
    assert second["url"] == "https://direct.example.org/b"
    assert second["snippet"] == "Snippet two & more"


def test_search_respects_max_results(provider):
    assert len(provider.search("q", max_results=1)) == 1


def test_challenge_raises(monkeypatch):
    fake_module = types.ModuleType("browser_oxide")
    fake_page = MagicMock()
    fake_page.is_challenge = True
    fake_page.verdict = "edge-block"
    fake_browser = MagicMock()
    fake_browser.navigate.return_value = fake_page
    fake_module.Browser = MagicMock(return_value=fake_browser)
    fake_module.Profile = MagicMock()
    monkeypatch.setitem(sys.modules, "browser_oxide", fake_module)

    p = BrowserOxideSearchProvider(delay=0.0)
    with pytest.raises(RuntimeError, match="challenge"):
        p.search("q")


def test_browser_reused_across_searches(provider):
    provider.search("first")
    provider.search("second")
    browser_obj = type(provider)._get_browser  # sanity: method exists
    assert provider._browser is not None
    assert provider._browser.navigate.call_count >= 2


def test_close_shuts_down_engine():
    fake_module = types.ModuleType("browser_oxide")
    fake_browser = MagicMock()
    fake_module.Browser = MagicMock(return_value=fake_browser)
    # _get_browser also touches Profile.chrome() — the fake must have it when
    # the real package is absent (browser-oxide is an optional [browser] extra).
    fake_module.Profile = MagicMock()
    sys.modules.setdefault("browser_oxide", fake_module)
    try:
        p = BrowserOxideSearchProvider(delay=0.0)
        p._get_browser()
        p.close()
        assert p._browser is None
        # idempotent
        p.close()
    finally:
        if sys.modules.get("browser_oxide") is fake_module:
            del sys.modules["browser_oxide"]


def test_rate_limit_defaults_and_registry():
    # BrowserOxide scrapes the same DuckDuckGo HTML endpoint, so it inherits
    # DuckDuckGo's politeness default (0.5 s + jitter, no server-side quota)
    # rather than the bare RateLimit() defaults.
    p = BrowserOxideSearchProvider()
    assert p.rate_limit.search_interval == 0.5
    assert p.rate_limit.jitter == 0.25
    assert p.rate_limit.fetch_interval == 0.5
    assert p.rate_limit.quota is None
    assert resolve_provider_name("browser") == "browser"
    # M2: aliases resolve to the canonical name.
    assert resolve_provider_name("Browser_Oxide") == "browser"

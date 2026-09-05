"""Integration tests for the browser_oxide stealth search provider.

These hit the live DuckDuckGo HTML endpoint and spin up a real headless
browser engine — slow and environment-dependent. They are marked ``slow``
and excluded from the default run; opt in with:

    pytest -m slow
"""

import pytest

from gossamer.search_providers import BrowserOxideSearchProvider

pytestmark = pytest.mark.slow


def test_browser_oxide_search_end_to_end():
    pytest.importorskip("browser_oxide")

    prov = BrowserOxideSearchProvider(delay=0.0)
    try:
        results = prov.search("rust programming language", max_results=3)
    finally:
        prov.close()

    assert isinstance(results, list)
    assert len(results) <= 3
    for r in results:
        assert "title" in r and "url" in r and "snippet" in r
        assert r["url"].startswith("http")

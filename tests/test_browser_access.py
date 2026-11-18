"""Browser render/search access + cache-strategy compatibility.

These are the regression tests for the dev_browser work:

* the optional stealth search provider is registered when available and
  can be selected by name;
* a recognized-but-unregistered provider no longer discards search results
  in ``research`` mode;
* an explicit ``use_smart`` strategy ignores a cached result produced by a
  different engine, while ``auto`` keeps serving the cache;
* ``batch_inspect_pages`` and the structured HTML path honor the same
  strategy-aware cache rules;
* the MCP input schema keeps registry descriptions/enums.

Everything is offline: browser/static fetches are stubbed at the existing
seams, and ``batch_research`` is replaced with a canned result.
"""

from __future__ import annotations

import json

import pytest

import gossamer.agent_tools as agent_tools
import gossamer.fetch as fetch
from gossamer.agent_tools import ToolboxConfig, WebResearcherToolbox

URL = "https://example.com/browser-access"


def _toolbox(tmp_path, fetch_mode: str = "auto") -> WebResearcherToolbox:
    tb = WebResearcherToolbox(
        config=ToolboxConfig(
            cache_dir=str(tmp_path / "cache"),
            respect_robots=False,
            fetch_mode=fetch_mode,
            ddgs_delay=0.0,
        )
    )
    tb._fetch_interval = 0  # no politeness sleep
    return tb


def _install_spy_fetches(tb) -> list:
    """Replace the two low-level fetch strategies with recording spies."""
    calls: list = []

    def fake_static(url):
        calls.append(("static", url))
        return ("static-md", [], {}, "static")

    def fake_browser(url):
        calls.append(("browser", url))
        return ("browser-md", [], {}, "browser")

    tb._fetch._static_fetch = fake_static
    tb._fetch._browser_fetch = fake_browser
    return calls


class TestBrowserSearchProvider:
    def test_registered_and_selectable_when_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_tools, "browser_oxide_available", lambda: True)
        tb = _toolbox(tmp_path)
        assert "browser" in [p.name for p in tb.providers]
        assert tb._search._resolve_providers("browser")[0].name == "browser"
        assert tb._search._provider_fallback_note("browser") is None

    def test_not_registered_when_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_tools, "browser_oxide_available", lambda: False)
        tb = _toolbox(tmp_path)
        assert "browser" not in [p.name for p in tb.providers]
        note = tb._search._provider_fallback_note("browser")
        assert note and note["reason"] == "provider not registered"

    def test_research_keeps_results_when_provider_falls_back(
        self, tmp_path, monkeypatch
    ):
        tb = _toolbox(tmp_path)
        tb.search_web = lambda *a, **k: json.dumps(
            {
                "results": [
                    {
                        "title": "Example",
                        "url": URL,
                        "snippet": "fallback result",
                    }
                ],
                "provider_fallback": {
                    "requested": "browser",
                    "reason": "provider not registered",
                    "used": ["duckduckgo"],
                },
            }
        )
        monkeypatch.setattr(
            tb._fetch,
            "_inspect_html_page_impl",
            lambda url, use_smart, *a, **k: json.dumps(
                {"url": url, "markdown": "page", "fetch_method": "static"}
            ),
        )
        data = json.loads(tb.research("anything", depth=1, provider="browser"))
        assert data["count"] == 1
        assert data["sources"][0]["url"] == URL
        assert data["provider_fallback"]["requested"] == "browser"


class TestBrowserDependency:
    def test_missing_extra_raises_actionable_error(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path)
        monkeypatch.setattr(fetch, "_browser_oxide_available", False)
        with pytest.raises(RuntimeError, match="browser_oxide is not installed"):
            tb._fetch._browser_fetch(URL)


class TestRenderStrategyCache:
    def test_browser_request_bypasses_cached_static(self, tmp_path):
        tb = _toolbox(tmp_path)
        calls = _install_spy_fetches(tb)

        first = json.loads(tb.inspect_html_page(URL, use_smart="static"))
        assert first["fetch_method"] == "static"
        assert first["cache_hit"] is False

        second = json.loads(tb.inspect_html_page(URL, use_smart="browser"))
        assert second["fetch_method"] == "browser"
        assert second["cache_hit"] is False
        assert calls == [("static", URL), ("browser", URL)]

    def test_static_request_bypasses_cached_browser(self, tmp_path):
        tb = _toolbox(tmp_path)
        calls = _install_spy_fetches(tb)

        json.loads(tb.inspect_html_page(URL, use_smart="browser"))
        second = json.loads(tb.inspect_html_page(URL, use_smart="static"))
        assert second["fetch_method"] == "static"
        assert second["cache_hit"] is False
        assert calls == [("browser", URL), ("static", URL)]

    def test_browser_first_static_fallback_is_reused(self, tmp_path):
        tb = _toolbox(tmp_path)
        calls = _install_spy_fetches(tb)

        def broken_browser(url):
            calls.append(("browser", url))
            raise RuntimeError("stealth unavailable")

        tb._fetch._browser_fetch = broken_browser

        first = json.loads(tb.inspect_html_page(URL, use_smart="browser"))
        assert first["fetch_method"] == "static"
        assert first["cache_hit"] is False

        # The fallback is tagged as browser-first, so a second browser
        # request reuses it instead of retrying the failed engine.
        second = json.loads(tb.inspect_html_page(URL, use_smart="browser"))
        assert second["fetch_method"] == "static"
        assert second["cache_hit"] is True
        assert calls == [("browser", URL), ("static", URL)]

    def test_auto_still_serves_cache(self, tmp_path):
        tb = _toolbox(tmp_path)
        calls = _install_spy_fetches(tb)
        json.loads(tb.inspect_html_page(URL))
        second = json.loads(tb.inspect_html_page(URL))
        assert second["cache_hit"] is True
        assert calls == [("static", URL)]

    def test_global_browser_mode_bypasses_cached_static(self, tmp_path):
        tb = _toolbox(tmp_path, "browser")
        calls = _install_spy_fetches(tb)
        # Simulate a static result cached by an earlier auto/static run.
        tb._fetch._page_cache_put(
            URL, "static-md", [], {"fetch_strategy": "auto"}, "static"
        )
        out = json.loads(tb.inspect_html_page(URL))
        assert out["fetch_method"] == "browser"
        assert out["cache_hit"] is False
        assert calls == [("browser", URL)]

    def test_fetch_meta_carries_strategy_tag(self, tmp_path):
        tb = _toolbox(tmp_path)
        _install_spy_fetches(tb)
        static = tb._fetch._fetch_html(URL, use_smart="static")
        browser = tb._fetch._fetch_html(URL, use_smart="browser")
        assert static[2]["fetch_strategy"] == "static-only"
        assert browser[2]["fetch_strategy"] == "browser-first"


class TestBatchAndStructuredStrategy:
    def test_batch_use_smart_selects_browser_and_bypasses_static_cache(
        self, tmp_path, monkeypatch
    ):
        tb = _toolbox(tmp_path)
        calls = _install_spy_fetches(tb)

        def fake_batch(urls, **kwargs):
            return [
                (
                    URL,
                    "<html><body>batch</body></html>",
                    "batch-md",
                    [],
                    (200, URL, "text/html"),
                )
            ]

        monkeypatch.setattr(fetch, "batch_research", fake_batch)
        first = json.loads(tb.batch_inspect_pages([URL], use_smart="static"))
        assert first[0]["fetch_method"] == "static"
        assert first[0]["cache_hit"] is False

        second = json.loads(tb.batch_inspect_pages([URL], use_smart="browser"))
        assert second[0]["fetch_method"] == "browser"
        assert second[0]["cache_hit"] is False
        assert calls == [("browser", URL)]

    def test_batch_browser_mode_is_sequential(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path)
        calls = _install_spy_fetches(tb)

        def fail_batch(urls, **kwargs):
            raise AssertionError("browser batch must not use the static engine")

        monkeypatch.setattr(fetch, "batch_research", fail_batch)
        out = tb.batch_inspect_pages([URL], use_smart="browser")
        data = json.loads(out)
        assert data[0]["fetch_method"] == "browser"
        assert calls == [("browser", URL)]

    def test_structured_cache_is_strategy_scoped(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path)
        calls = []

        def fake_html(url, use_smart="auto"):
            calls.append(use_smart)
            return (
                "md",
                [],
                {"title": "t"},
                "static",
                "<html><body><p>hi</p></body></html>",
            )

        monkeypatch.setattr(tb._fetch, "_fetch_html_with_html", fake_html)
        tb.inspect_html_structured(URL, use_smart="static")
        tb.inspect_html_structured(URL, use_smart="browser")
        tb.inspect_html_structured(URL, use_smart="static")
        assert calls == ["static", "browser"]


class TestMcpSchemaMetadata:
    def test_schema_preserves_description_and_enum(self):
        from gossamer.mcp_server import build_server

        import asyncio

        server = build_server()
        tools = {t.name: t for t in asyncio.run(server.list_tools())}
        props = tools["inspect_html_page"].input_schema["properties"]
        assert props["use_smart"]["enum"] == ["auto", "browser", "static"]
        assert props["use_smart"]["description"]
        assert props["url"]["description"] == "The URL to inspect"

        search = tools["web_search"].input_schema["properties"]
        assert "browser" in search["provider"]["enum"]
        assert search["provider"]["description"]
        assert search["use_smart"]["enum"] == ["auto", "browser", "static"]

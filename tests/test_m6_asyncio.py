# tests/test_m6_asyncio.py
"""M6 — deprecated asyncio usage.

search_web_async and inspect_html_page_async called
asyncio.get_event_loop() inside a coroutine (deprecated since 3.10,
error-prone on 3.12+). They must use asyncio.get_running_loop()
instead, and search_web_async must not re-import asyncio locally
(F6) — the module-level import in agent_tools is sufficient.
"""

import asyncio
import inspect

from stitch_web_researcher import agent_tools
from stitch_web_researcher.agent_tools import WebResearcherToolbox


def _toolbox() -> WebResearcherToolbox:
    # No live network: respect_robots=False keeps construction offline.
    return WebResearcherToolbox(respect_robots=False)


class TestAsyncDelegation:
    """The async wrappers still delegate to the sync implementations."""

    def test_search_web_async_delegates(self):
        tb = _toolbox()
        calls = []

        def fake_search_web(query, max_results=5, provider=None):
            calls.append((query, max_results, provider))
            return '{"results": []}'

        tb.search_web = fake_search_web
        result = asyncio.run(tb.search_web_async("q", max_results=3))
        assert calls == [("q", 3, None)]
        assert result == '{"results": []}'

    def test_inspect_html_page_async_delegates(self):
        tb = _toolbox()
        calls = []

        def fake_impl(url, use_smart=None, query=None, offset=0, max_chunks=1):
            # Tier 1.2 extended the shared impl with paging parameters
            # (offset, max_chunks); the async wrapper must forward all
            # five positionally, like the sync wrapper.
            calls.append((url, use_smart, query, offset, max_chunks))
            return f"RESULT:{url}"

        tb._inspect_html_page_impl = fake_impl
        result = asyncio.run(tb.inspect_html_page_async("https://example.com"))
        assert calls == [("https://example.com", None, None, 0, 1)]
        assert result == "RESULT:https://example.com"
        result = asyncio.run(
            tb.inspect_html_page_async(
                "https://example.com", use_smart=True, query="my query"
            )
        )
        assert calls[-1] == ("https://example.com", True, "my query", 0, 1)
        result = asyncio.run(
            tb.inspect_html_page_async(
                "https://example.com", offset=100, max_chunks=2
            )
        )
        assert calls[-1] == ("https://example.com", None, None, 100, 2)


class TestNoDeprecatedApi:
    """Source-level regression guard: the deprecated call must be gone."""

    def test_search_web_async_source(self):
        src = inspect.getsource(
            agent_tools.WebResearcherToolbox.search_web_async
        )
        assert "get_running_loop" in src
        assert "get_event_loop" not in src
        # F6: no local re-import of asyncio (module-level import used)
        assert "import asyncio" not in src

    def test_inspect_html_page_async_source(self):
        src = inspect.getsource(
            agent_tools.WebResearcherToolbox.inspect_html_page_async
        )
        assert "get_running_loop" in src
        assert "get_event_loop" not in src

    def test_package_wide_no_get_event_loop(self):
        """No remaining get_event_loop call anywhere in the package."""
        import pathlib

        pkg_dir = pathlib.Path(agent_tools.__file__).parent
        for py in pkg_dir.glob("*.py"):
            text = py.read_text(encoding="utf-8")
            assert "get_event_loop" not in text, f"{py.name} still uses it"

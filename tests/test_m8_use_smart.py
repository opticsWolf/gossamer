# tests/test_m8_use_smart.py
"""M8 — use_smart must actually affect the fetch path.

The review reported that inspect_html_page ignored use_smart (the Rust
core config had no such flag, so every fetch used the core default).
The static/stealth-browser split fixed this: _fetch_html now dispatches
on (fetch_mode, use_smart). These tests pin that dispatch matrix and
verify that inspect_html_page / inspect_html_structured pass use_smart
through to _fetch_html. Fully offline (fetches are spied, no network).
"""

from stitch_web_researcher import agent_tools
from stitch_web_researcher.agent_tools import (
    ToolboxConfig,
    WebResearcherToolbox,
)

URL = "https://example.com/page"


def _toolbox(tmp_path, fetch_mode: str = "auto") -> WebResearcherToolbox:
    tb = WebResearcherToolbox(
        config=ToolboxConfig(
            respect_robots=False,
            fetch_mode=fetch_mode,
            cache_dir=str(tmp_path / "cache"),
        )
    )
    tb._fetch_interval = 0  # no politeness sleep
    return tb


def _install_spy_fetches(tb) -> list:
    """Replace the two low-level fetch strategies with spies."""
    calls = []

    def fake_static(url):
        calls.append(("static", url))
        return ("static-md", [], {}, "static")

    def fake_browser(url):
        calls.append(("browser", url))
        return ("browser-md", [], {}, "browser")

    tb._static_fetch = fake_static
    tb._browser_fetch = fake_browser
    return calls


class TestUseSmartDispatch:
    """The (fetch_mode, use_smart) dispatch matrix in _fetch_html."""

    def test_static_mode_use_smart_none(self, tmp_path):
        tb = _toolbox(tmp_path, "static")
        calls = _install_spy_fetches(tb)
        result = tb._fetch_html(URL)
        assert calls == [("static", URL)]
        assert result[0] == "static-md"

    def test_use_smart_true_prefers_browser(self, tmp_path):
        tb = _toolbox(tmp_path, "auto")
        calls = _install_spy_fetches(tb)
        result = tb._fetch_html(URL, use_smart=True)
        assert calls == [("browser", URL)]
        assert result[0] == "browser-md"
        assert result[3] == "browser"

    def test_use_smart_true_browser_failure_falls_back(self, tmp_path):
        tb = _toolbox(tmp_path, "auto")
        _install_spy_fetches(tb)

        def broken_browser(url):
            raise RuntimeError("stealth unavailable")

        tb._browser_fetch = broken_browser
        result = tb._fetch_html(URL, use_smart=True)
        # Browser failed -> static fallback still delivers content
        assert result[0] == "static-md"

    def test_use_smart_false_forces_static(self, tmp_path):
        for mode in ("auto", "browser"):
            tb = _toolbox(tmp_path, mode)
            calls = _install_spy_fetches(tb)
            result = tb._fetch_html(URL, use_smart=False)
            assert calls == [("static", URL)]
            assert result[3] == "static"

    def test_browser_mode_default_uses_browser(self, tmp_path):
        tb = _toolbox(tmp_path, "browser")
        calls = _install_spy_fetches(tb)
        result = tb._fetch_html(URL)
        assert calls == [("browser", URL)]
        assert result[3] == "browser"

    def test_auto_static_success_skips_browser(self, tmp_path):
        tb = _toolbox(tmp_path, "auto")
        calls = _install_spy_fetches(tb)
        tb._fetch_html(URL)
        assert calls == [("static", URL)]

    def test_auto_static_failure_uses_browser_oxide(self, tmp_path, monkeypatch):
        tb = _toolbox(tmp_path, "auto")
        _install_spy_fetches(tb)

        def broken_static(url):
            raise RuntimeError("static 403")

        tb._static_fetch = broken_static
        stealth_calls = []

        def fake_stealth(url):
            stealth_calls.append(url)
            return ("stealth-md", [], {})

        monkeypatch.setattr(agent_tools, "_fetch_with_browser_oxide", fake_stealth)
        # The dispatch consults the availability flag, not just the function
        # being patched — set it so the test is hermetic (browser-oxide is an
        # optional [browser] extra and may be absent).
        monkeypatch.setattr(agent_tools, "_browser_oxide_available", True)
        result = tb._fetch_html(URL)
        assert stealth_calls == [URL]
        assert result[0] == "stealth-md"
        assert result[3] == "stealth-fallback"


class TestUseSmartPlumbing:
    """The public tools must hand use_smart to _fetch_html (the original
    M8 report: the parameter was documented but had no effect)."""

    @staticmethod
    def _spy(tb) -> list:
        calls = []

        def fake_fetch(url, use_smart=None):
            calls.append(use_smart)
            return (
                "Some markdown content.",
                [("https://example.com/x", "x")],
                {},
                "static",
            )

        tb._fetch_html = fake_fetch
        return calls

    @staticmethod
    def _spy_structured(tb) -> list:
        """Tier 3.11: the structured path uses the 5-tuple seam."""
        calls = []

        def fake_fetch(url, use_smart=None):
            calls.append(use_smart)
            return (
                "Some markdown content.",
                [("https://example.com/x", "x")],
                {},
                "static",
                None,
            )

        tb._fetch_html_with_html = fake_fetch
        return calls

    def test_inspect_html_page_passes_use_smart(self, tmp_path):
        tb = _toolbox(tmp_path, "auto")
        calls = self._spy(tb)
        out = tb.inspect_html_page(URL, use_smart=True)
        assert calls == [True]
        assert "Some markdown content." in out

    def test_inspect_html_structured_passes_use_smart(self, tmp_path):
        tb = _toolbox(tmp_path, "auto")
        calls = self._spy_structured(tb)
        out = tb.inspect_html_structured(URL, use_smart=True)
        assert calls == [True]
        assert "Some markdown content." in out

    def test_inspect_html_page_default_is_none(self, tmp_path):
        tb = _toolbox(tmp_path, "auto")
        calls = self._spy(tb)
        tb.inspect_html_page(URL)
        assert calls == [None]

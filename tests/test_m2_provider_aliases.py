"""M2: provider aliases must select the right provider.

Before the fix ``resolve_provider_name`` returned the *alias* it looked up
("ddg" -> "ddg") and ``_resolve_providers`` matched providers by a
``__class__.__name__``-derived key ("duckduckgo"), so:

  * ``provider="ddg"`` resolved to "ddg", matched no provider, and
    silently fell back to registration order;
  * ``provider="browser"`` could never select ``BrowserOxideSearchProvider``
    (its class-name key "browseroxidesearch" was not in the map);
  * only the canonical spellings happened to line up.

Providers now carry an explicit canonical ``name`` attribute and the map
resolves every accepted alias to that name.
"""

import pytest

from stitch_web_researcher.agent_tools import ToolboxConfig, WebResearcherToolbox
from stitch_web_researcher.search_providers import (
    BingProvider,
    BrowserOxideSearchProvider,
    DuckDuckGoProvider,
    ExaProvider,
    GoogleProvider,
    resolve_provider_name,
)


def _make_toolbox() -> WebResearcherToolbox:
    """Toolbox with one of each constructible provider, in a known
    registration order. Exa is covered separately in
    ``test_exa_selectable_when_configured`` (it needs EXA_API_KEY)."""
    return WebResearcherToolbox(
        ToolboxConfig(
            search_providers=[
                DuckDuckGoProvider(),
                GoogleProvider(),
                BingProvider(),
                BrowserOxideSearchProvider(),
            ]
        )
    )


class TestProviderNameAttribute:
    """Every provider exposes the canonical name the map resolves to."""

    @pytest.mark.parametrize(
        "cls,expected",
        [
            (DuckDuckGoProvider, "duckduckgo"),
            (GoogleProvider, "google"),
            (BingProvider, "bing"),
            (ExaProvider, "exa"),
            (BrowserOxideSearchProvider, "browser"),
        ],
    )
    def test_name_attribute(self, cls, expected):
        assert cls.name == expected


class TestResolveProviderName:
    def test_canonical_names(self):
        assert resolve_provider_name("duckduckgo") == "duckduckgo"
        assert resolve_provider_name("google") == "google"
        assert resolve_provider_name("bing") == "bing"
        assert resolve_provider_name("exa") == "exa"
        assert resolve_provider_name("browser") == "browser"

    def test_aliases_map_to_canonical(self):
        assert resolve_provider_name("ddg") == "duckduckgo"
        assert resolve_provider_name("browser_oxide") == "browser"
        assert resolve_provider_name("browseroxide") == "browser"

    def test_case_and_whitespace_insensitive(self):
        assert resolve_provider_name("DDG") == "duckduckgo"
        assert resolve_provider_name("  Browser  ") == "browser"
        assert resolve_provider_name("DuckDuckGo") == "duckduckgo"

    def test_unknown_returns_none(self):
        assert resolve_provider_name("nope") is None
        assert resolve_provider_name("") is None


class TestResolveProvidersOrdering:
    def test_ddg_alias_selects_duckduckgo(self):
        tb = _make_toolbox()
        order = [p.name for p in tb._search._resolve_providers("ddg")]
        assert order[0] == "duckduckgo"
        # The matched provider is first, the rest preserve registration order.
        assert order[1:] == ["google", "bing", "browser"]

    def test_canonical_duckduckgo(self):
        tb = _make_toolbox()
        assert tb._search._resolve_providers("duckduckgo")[0].name == "duckduckgo"

    def test_browser_selects_browser_oxide(self):
        tb = _make_toolbox()
        order = [p.name for p in tb._search._resolve_providers("browser")]
        assert order[0] == "browser"
        assert isinstance(tb._search._resolve_providers("browser")[0], BrowserOxideSearchProvider)

    def test_browser_oxide_alias_selects_browser_oxide(self):
        tb = _make_toolbox()
        assert tb._search._resolve_providers("browser_oxide")[0].name == "browser"

    def test_each_canonical_selects_its_provider(self):
        tb = _make_toolbox()
        for canonical in ("duckduckgo", "google", "bing", "browser"):
            first = tb._search._resolve_providers(canonical)[0]
            assert first.name == canonical, canonical

    def test_all_providers_present_after_selection(self):
        tb = _make_toolbox()
        selected = tb._search._resolve_providers("bing")
        assert len(selected) == 4
        assert {p.name for p in selected} == {
            "duckduckgo", "google", "bing", "browser"
        }

    def test_none_uses_registration_order(self):
        tb = _make_toolbox()
        order = [p.name for p in tb._search._resolve_providers(None)]
        assert order == ["duckduckgo", "google", "bing", "browser"]

    def test_unknown_name_falls_back_to_all(self):
        tb = _make_toolbox()
        order = [p.name for p in tb._search._resolve_providers("does-not-exist")]
        assert order == ["duckduckgo", "google", "bing", "browser"]

    def test_exa_selectable_when_configured(self):
        # Exa is implemented against the REST API with httpx, so it
        # constructs without any optional install.
        exa = ExaProvider(api_key="k")
        tb = WebResearcherToolbox(
            ToolboxConfig(
                search_providers=[DuckDuckGoProvider(), exa]
            )
        )
        assert tb._search._resolve_providers("exa")[0] is exa

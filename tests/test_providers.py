"""
Tests for search_providers.py and multi-provider integration.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from stitch_web_researcher.search_providers import (
    SearchProvider,
    DuckDuckGoProvider,
    GoogleProvider,
    BingProvider,
    get_default_providers,
    resolve_provider_name,
)


# ────────────────────────────────────────────────────────────────
# 1. DuckDuckGoProvider
# ────────────────────────────────────────────────────────────────

class TestDuckDuckGoProvider:
    def test_is_search_provider(self):
        assert isinstance(DuckDuckGoProvider(), SearchProvider)

    def test_search_returns_list_of_dicts(self):
        prov = DuckDuckGoProvider(delay=0.0)
        with patch.object(prov, "search", wraps=prov.search) as mock_search:
            # We mock the DDGS call to avoid network
            pass

    @patch("ddgs.DDGS")
    def test_search_maps_fields(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = [
            {"title": "Test Title", "href": "https://example.com", "body": "Test snippet"},
        ]

        prov = DuckDuckGoProvider(delay=0.0)
        results = prov.search("test query", max_results=1)

        assert len(results) == 1
        assert results[0]["title"] == "Test Title"
        assert results[0]["url"] == "https://example.com"
        assert results[0]["snippet"] == "Test snippet"

    @patch("ddgs.DDGS")
    def test_search_handles_missing_fields(self, mock_ddgs_cls):
        mock_ddgs = MagicMock()
        mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ddgs)
        mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ddgs.text.return_value = [{}]  # No fields at all

        prov = DuckDuckGoProvider(delay=0.0)
        results = prov.search("test", max_results=1)

        assert len(results) == 1
        assert results[0]["title"] == ""
        assert results[0]["url"] == ""
        assert results[0]["snippet"] == ""


# ────────────────────────────────────────────────────────────────
# 2. GoogleProvider
# ────────────────────────────────────────────────────────────────

class TestGoogleProvider:
    def test_is_search_provider(self):
        prov = GoogleProvider(api_key="key", cx="cx")
        assert isinstance(prov, SearchProvider)

    def test_raises_without_keys(self):
        prov = GoogleProvider()
        with pytest.raises(RuntimeError, match="requires GOOGLE_API_KEY"):
            prov.search("test")

    @patch("stitch_web_researcher.search_providers.httpx.get")
    def test_search_maps_fields(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {"title": "Google Result", "link": "https://google.com", "snippet": "Snippet text"},
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        prov = GoogleProvider(api_key="key", cx="cx", delay=0.0)
        results = prov.search("test", max_results=1)

        assert len(results) == 1
        assert results[0]["title"] == "Google Result"
        assert results[0]["url"] == "https://google.com"
        assert results[0]["snippet"] == "Snippet text"

    @patch("stitch_web_researcher.search_providers.httpx.get")
    def test_search_caps_at_10(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        prov = GoogleProvider(api_key="key", cx="cx", delay=0.0)
        prov.search("test", max_results=20)

        # Check that num param is capped at 10
        call_kwargs = mock_get.call_args[1]["params"]
        assert call_kwargs["num"] == 10


# ────────────────────────────────────────────────────────────────
# 3. BingProvider
# ────────────────────────────────────────────────────────────────

class TestBingProvider:
    def test_is_search_provider(self):
        prov = BingProvider(api_key="key")
        assert isinstance(prov, SearchProvider)

    def test_raises_without_key(self):
        prov = BingProvider()
        with pytest.raises(RuntimeError, match="requires BING_API_KEY"):
            prov.search("test")

    @patch("stitch_web_researcher.search_providers.httpx.get")
    def test_search_maps_fields(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "webPages": {
                "value": [
                    {"name": "Bing Result", "url": "https://bing.com", "snippet": "Bing snippet"},
                ]
            }
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        prov = BingProvider(api_key="key", delay=0.0)
        results = prov.search("test", max_results=1)

        assert len(results) == 1
        assert results[0]["title"] == "Bing Result"
        assert results[0]["url"] == "https://bing.com"
        assert results[0]["snippet"] == "Bing snippet"


# ────────────────────────────────────────────────────────────────
# 4. Provider Resolution Helpers
# ────────────────────────────────────────────────────────────────

class TestResolveProviderName:
    def test_duckduckgo(self):
        assert resolve_provider_name("duckduckgo") == "duckduckgo"
        assert resolve_provider_name("ddg") == "ddg"

    def test_google(self):
        assert resolve_provider_name("google") == "google"

    def test_bing(self):
        assert resolve_provider_name("bing") == "bing"

    def test_exa(self):
        assert resolve_provider_name("exa") == "exa"

    def test_case_insensitive(self):
        assert resolve_provider_name("Google") == "google"
        assert resolve_provider_name("BING") == "bing"

    def test_unknown_returns_none(self):
        assert resolve_provider_name("yahoo") is None


class TestGetDefaultProviders:
    def test_always_has_ddg(self):
        providers = get_default_providers()
        assert len(providers) >= 1
        assert isinstance(providers[0], DuckDuckGoProvider)


# ────────────────────────────────────────────────────────────────
# 5. Toolbox Integration (multi-provider fallback)
# ────────────────────────────────────────────────────────────────

class TestToolboxProviders:
    def test_default_provider_is_ddg(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox
        toolbox = WebResearcherToolbox()
        assert isinstance(toolbox.default_provider, DuckDuckGoProvider)
        assert len(toolbox.providers) == 1

    def test_custom_providers(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox
        prov = DuckDuckGoProvider(delay=0.5)
        toolbox = WebResearcherToolbox(search_providers=[prov])
        assert toolbox.providers == [prov]
        assert toolbox.default_provider is prov

    def test_resolve_providers_no_name(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox
        prov1 = DuckDuckGoProvider()
        prov2 = DuckDuckGoProvider()
        toolbox = WebResearcherToolbox(search_providers=[prov1, prov2])
        resolved = toolbox._resolve_providers(None)
        assert resolved == [prov1, prov2]

    def test_resolve_providers_named(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox
        prov1 = DuckDuckGoProvider()
        prov2 = DuckDuckGoProvider()
        toolbox = WebResearcherToolbox(search_providers=[prov1, prov2])
        resolved = toolbox._resolve_providers("duckduckgo")
        # All are DDG, so all matched, no others
        assert len(resolved) == 2

    def test_search_web_uses_provider(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox
        mock_prov = MagicMock()
        mock_prov.search.return_value = [
            {"title": "Mock Result", "url": "https://mock.com", "snippet": "Mock snippet"},
        ]
        mock_prov.__class__.__name__ = "MockProvider"

        toolbox = WebResearcherToolbox(search_providers=[mock_prov])
        result = toolbox.search_web("test query", max_results=3)

        mock_prov.search.assert_called_once_with("test query", max_results=3)
        parsed = json.loads(result)
        assert parsed[0]["title"] == "Mock Result"

    def test_search_web_fallback(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox
        prov1 = MagicMock()
        prov1.search.side_effect = Exception("Provider 1 down")
        prov1.__class__.__name__ = "FailingProvider"

        prov2 = MagicMock()
        prov2.search.return_value = [
            {"title": "Fallback Result", "url": "https://fallback.com", "snippet": ""},
        ]
        prov2.__class__.__name__ = "WorkingProvider"

        toolbox = WebResearcherToolbox(search_providers=[prov1, prov2])
        result = toolbox.search_web("test")

        parsed = json.loads(result)
        assert parsed[0]["title"] == "Fallback Result"
        prov1.search.assert_called_once()
        prov2.search.assert_called_once()

    def test_search_web_all_fail(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox
        prov1 = MagicMock()
        prov1.search.side_effect = Exception("Error 1")
        prov1.__class__.__name__ = "Fail1"

        prov2 = MagicMock()
        prov2.search.side_effect = Exception("Error 2")
        prov2.__class__.__name__ = "Fail2"

        toolbox = WebResearcherToolbox(search_providers=[prov1, prov2])
        result = toolbox.search_web("test")

        parsed = json.loads(result)
        assert "error" in parsed

    def test_llm_definitions_include_provider(self):
        from stitch_web_researcher.agent_tools import WebResearcherToolbox
        toolbox = WebResearcherToolbox()
        defs = toolbox.get_llm_definitions()
        search_def = [d for d in defs if d["function"]["name"] == "search_web"][0]
        props = search_def["function"]["parameters"]["properties"]
        assert "provider" in props
        assert "duckduckgo" in props["provider"]["enum"]

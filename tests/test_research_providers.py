"""Tests for the unified ResourceAdapter interface and the domain adapters.

Covers the cross-cutting behaviour the base owns (politeness, jitter, quota,
retry-skip-quota, auth injection, header retuning) and the two concrete
domain adapters (OpenAlex scholarly, Open-Meteo geo). Search providers keep
their existing coverage in tests/test_providers.py and test_m3_retry.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from stitch_web_researcher.search_providers import (
    DuckDuckGoProvider,
    QuotaExhaustedError,
    RateLimit,
    RateState,
    ResourceAdapter,
    SearchProvider,
)
from stitch_web_researcher.research_providers import (
    OpenAlexAdapter,
    OpenMeteoAdapter,
    _parse_lat_lon,
)


# ────────────────────────────────────────────────────────────────
# Base-class contract (politeness / quota / retry / auth)
# ────────────────────────────────────────────────────────────────


class _ProbeAdapter(ResourceAdapter):
    """Minimal concrete adapter that counts admitted calls."""

    name = "probe"
    domain = "test"

    def __init__(self, rl):
        self._last_search = 0.0
        self._init_rate_limit(rl)
        self.calls = 0

    def _search_impl(self, query, max_results=5):
        self._enforce_delay()
        self.calls += 1
        return [{"title": "t", "url": "https://example.com", "snippet": "s"}]


class TestResourceAdapterContract:
    def test_search_provider_is_a_resource_adapter(self):
        assert isinstance(DuckDuckGoProvider(), ResourceAdapter)
        assert isinstance(DuckDuckGoProvider(), SearchProvider)

    def test_base_is_abstract(self):
        with pytest.raises(TypeError):
            ResourceAdapter()

    def test_no_delay_no_sleep(self):
        prov = _ProbeAdapter(RateLimit(search_interval=0.0))
        prov._last_search = 0.0
        with patch("stitch_web_researcher.search_providers.time.sleep") as sleep:
            prov._enforce_delay()
            sleep.assert_not_called()

    def test_jitter_added_to_gap(self):
        prov = _ProbeAdapter(RateLimit(search_interval=0.1, jitter=0.5, quota=None))
        prov._last_search = 0.0
        with patch("stitch_web_researcher.search_providers.time") as t, patch(
            "stitch_web_researcher.search_providers.random"
        ) as rnd:
            t.time.return_value = 0.0
            rnd.uniform.return_value = 0.5  # max jitter
            prov._enforce_delay()
        # gap = 0.1 + 0.5 = 0.6; elapsed 0 -> sleep(0.6)
        t.sleep.assert_called_once_with(0.6)
        assert rnd.uniform.call_args[0] == (0.0, 0.5)

    def test_quota_stops_after_cap(self):
        prov = _ProbeAdapter(RateLimit(search_interval=0.0, quota=2, quota_window="day"))
        prov.search("q")
        prov.search("q")
        assert prov.calls == 2
        with pytest.raises(QuotaExhaustedError):
            prov.search("q")
        assert prov.calls == 2  # no provider call once exhausted

    def test_quota_resets_when_period_stale(self):
        prov = _ProbeAdapter(RateLimit(search_interval=0.0, quota=2, quota_window="day"))
        prov._quota_used = 2
        prov._quota_period = "1999-01-01"  # not today
        prov._last_search = 0.0
        prov.search("q")  # stale period -> reset -> admit
        assert prov.calls == 1
        assert prov._quota_used == 1
        assert prov._quota_period != "1999-01-01"

    def test_monthly_quota_window(self):
        prov = _ProbeAdapter(RateLimit(search_interval=0.0, quota=5, quota_window="month"))
        prov._quota_used = 5
        prov._quota_period = "2026-07"  # previous month
        prov._last_search = 0.0
        from datetime import datetime, timezone

        prov._enforce_delay()  # rolls into current month -> reset
        assert prov._quota_used == 1
        assert prov._quota_period == datetime.now(timezone.utc).strftime("%Y-%m")

    def test_retry_does_not_retry_quota(self):
        prov = _ProbeAdapter(RateLimit(search_interval=0.0, quota=1, quota_window="day"))
        prov._last_search = 0.0
        prov.search("q")  # admits the single allowed call
        with pytest.raises(QuotaExhaustedError):
            prov.search("q")  # exhausted -> immediate, not retried 3x
        assert prov.calls == 1

    def test_inject_auth_default_is_noop(self):
        prov = _ProbeAdapter(RateLimit())
        url, params, headers = prov.inject_auth("https://x/y", {"a": 1}, {"h": 2})
        assert (url, params, headers) == ("https://x/y", {"a": 1}, {"h": 2})

    def test_parse_headers_default_empty(self):
        prov = _ProbeAdapter(RateLimit())
        assert prov.parse_headers(200, {}) == RateState()

    def test_fetch_default_not_implemented(self):
        class _NoFetch(ResourceAdapter):
            name = "nofetch"

            def _search_impl(self, query, max_results=5):
                return []

        with pytest.raises(NotImplementedError):
            _NoFetch().fetch("x")

    def test_search_provider_fetch_not_implemented(self):
        with pytest.raises(NotImplementedError):
            DuckDuckGoProvider(delay=0.0).fetch("whatever")


# ────────────────────────────────────────────────────────────────
# OpenAlexAdapter (scholarly)
# ────────────────────────────────────────────────────────────────


class TestOpenAlexAdapter:
    def test_metadata(self):
        prov = OpenAlexAdapter(delay=0.0)
        assert prov.name == "openalex"
        assert prov.domain == "scholarly"
        assert prov.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "id": "W123",
                    "title": ["A Paper"],
                    "doi": "doi:10.1/x",
                    "publication_date": "2020-01-01",
                    "cited_by_count": 5,
                    "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                }
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        prov = OpenAlexAdapter(delay=0.0, email="me@example.org")
        results = prov.search("quantum", max_results=1)

        assert results[0]["title"] == "A Paper"
        assert results[0]["id"] == "W123"
        assert results[0]["doi"] == "doi:10.1/x"
        assert results[0]["authors"] == "Ada Lovelace"
        assert results[0]["citations"] == 5

    def test_inject_auth_sets_polite_email_ua(self):
        prov = OpenAlexAdapter(delay=0.0, email="me@example.org")
        url, params, headers = prov.inject_auth("https://api.openalex.org/works", {}, {})
        assert "email=me@example.org" in headers["User-Agent"]
        assert "email=me@example.org" in headers["Contact-Agent"]
        assert url == "https://api.openalex.org/works"

    def test_inject_auth_adds_api_key_when_set(self):
        prov = OpenAlexAdapter(delay=0.0, api_key="secret")
        _, params, _ = prov.inject_auth("https://x", {}, {})
        assert params["api_key"] == "secret"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "W999", "title": ["Single"], "doi": "doi:10/y"}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = OpenAlexAdapter(delay=0.0).fetch("W999")
        assert out[0]["id"] == "W999"
        assert out[0]["title"] == "Single"


# ────────────────────────────────────────────────────────────────
# OpenMeteoAdapter (geo)
# ────────────────────────────────────────────────────────────────


class TestOpenMeteoAdapter:
    def test_metadata_and_default_quota(self):
        # No-arg construction applies the full default policy incl. the
        # 10,000/day quota; a delay override replaces the whole RateLimit.
        prov = OpenMeteoAdapter()
        assert prov.name == "open-meteo"
        assert prov.domain == "geo"
        assert prov.requires_key is False
        assert prov.rate_limit.quota == 10000
        assert prov.rate_limit.quota_window == "day"
        assert prov.rate_limit.search_interval == 1.0
        assert prov.rate_limit.jitter == 0.5

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_geocodes(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"name": "Berlin", "country": "Germany",
                 "latitude": 52.5, "longitude": 13.4, "url": "https://example.com/berlin"}
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = OpenMeteoAdapter(delay=0.0).search("Berlin", max_results=1)
        assert out[0]["title"] == "Berlin, Germany"
        assert out[0]["id"] == "52.5,13.4"
        assert out[0]["url"] == "https://example.com/berlin"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_forecast(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"current": {"temperature_2m": 12.3}}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = OpenMeteoAdapter(delay=0.0).fetch(
            "52.52,13.405", params={"current": "temperature_2m"}
        )
        assert out[0]["source"] == "open-meteo"
        assert "temperature_2m=12.3" in out[0]["snippet"]
        assert mock_get.call_args[1]["params"]["latitude"] == 52.52
        assert mock_get.call_args[1]["params"]["longitude"] == 13.405

    def test_parse_lat_lon_variants(self):
        assert _parse_lat_lon("52.52,13.405") == (52.52, 13.405)
        assert _parse_lat_lon((52.5, 13.4)) == (52.5, 13.4)
        assert _parse_lat_lon([1.0, 2.0]) == (1.0, 2.0)


# ────────────────────────────────────────────────────────────────
# Hierarchy
# ────────────────────────────────────────────────────────────────


class TestHierarchy:
    def test_adapters_are_resource_adapters(self):
        assert isinstance(OpenAlexAdapter(delay=0.0), ResourceAdapter)
        assert isinstance(OpenMeteoAdapter(delay=0.0), ResourceAdapter)

    def test_openalex_is_not_a_search_provider(self):
        # Domain adapters sit beside SearchProvider, not under it.
        assert not isinstance(OpenAlexAdapter(delay=0.0), SearchProvider)

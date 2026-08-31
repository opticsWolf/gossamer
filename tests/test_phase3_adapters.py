"""Phase 3 adapters: NASA, NVD, Zenodo, Software Heritage, Congress, Yahoo
Finance, Overpass, US Census.

Each adapter is tested offline: ``httpx.get`` is patched and the mock response
carries a ``json()`` payload + a no-op ``raise_for_status``. Auth/param-shape
tests construct the adapter and call ``inject_auth`` directly (no network).
"""

from unittest.mock import MagicMock, patch

import pytest

from stitch_web_researcher.research_providers import (
    CensusAdapter,
    CongressAdapter,
    NASAAdapter,
    NvdAdapter,
    OverpassAdapter,
    SoftwareHeritageAdapter,
    YahooFinanceAdapter,
    ZenodoAdapter,
)


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


# ── NASA ────────────────────────────────────────────────────────────────
class TestNASAAdapter:
    def test_metadata_keyless_geo(self):
        a = NASAAdapter(delay=0.0)
        assert a.name == "nasa"
        assert a.domain == "geo"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses_neo(self, mock_get):
        mock_get.return_value = _resp(
            {
                "near_earth_objects": {
                    "2024-01-01": [
                        {
                            "neo_reference_id": "2235437",
                            "object_name": "(308694) 2005 YU55",
                            "object_type": "NEA",
                            "is_hazardous": True,
                            "nasa_jpl_url": "https://cneos.jpl.nasa.gov/doc/ids/2235437.html",
                            "estimated_diameter": {"meters": {"estimated_diameter_max": 716.0}},
                            "close_approach_data": [
                                {
                                    "close_approach_date": "2024-01-01 21:41",
                                    "closest_approach_distance": {"kilometers": "25619043.1"},
                                }
                            ],
                        }
                    ]
                }
            }
        )
        a = NASAAdapter(delay=0.0)
        out = a.search("2024-01-01", max_results=5)
        assert len(out) == 1
        assert out[0]["id"] == "2235437"
        assert out[0]["title"] == "(308694) 2005 YU55"
        assert out[0]["published"] == "2024-01-01 21:41"
        assert out[0]["fields"]["nasa"]["is_hazardous"] is True
        # DEMO_KEY is the default key when none is supplied.
        assert mock_get.call_args.kwargs["params"]["api_key"] == "DEMO_KEY"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_single_neo(self, mock_get):
        mock_get.return_value = _resp(
            {"near_earth_objects": {"_single": [{"neo_reference_id": "2235437", "object_name": "X"}]}}
        )
        a = NASAAdapter(delay=0.0)
        out = a.fetch("2235437")
        assert out[0]["id"] == "2235437"
        assert mock_get.call_args.args[0].endswith("/neo/ws/neo/2235437")

    def test_inject_auth_uses_stored_key(self):
        a = NASAAdapter(delay=0.0, api_key="REALKEY")
        _, params, _ = a.inject_auth("https://api.nasa.gov/x", {}, {})
        assert params["api_key"] == "REALKEY"


# ── NVD ─────────────────────────────────────────────────────────────────
class TestNvdAdapter:
    def test_metadata(self):
        a = NvdAdapter(delay=0.0)
        assert a.name == "nvd"
        assert a.domain == "tech"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_cve_id_shaped_uses_cveid(self, mock_get):
        mock_get.return_value = _resp(
            {
                "vulnerabilityJsonDocument": [
                    {
                        "cve": {
                            "id": "CVE-2021-44228",
                            "descriptions": [{"lang": "en", "value": "Log4Shell"}],
                            "cveMetadata": {"cvssData": {"baseScore": 10.0, "vectorString": "CVSS:3.1/AV:N"}},
                        }
                    }
                ]
            }
        )
        a = NvdAdapter(delay=0.0)
        out = a.search("CVE-2021-44228", max_results=5)
        assert out[0]["id"] == "CVE-2021-44228"
        assert out[0]["fields"]["nvd"]["base_score"] == 10.0
        assert mock_get.call_args.kwargs["params"]["cveId"] == "CVE-2021-44228"
        # v2 JSON endpoints require a jsonp callback.
        assert "jsonp" in mock_get.call_args.kwargs["params"]

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_text_uses_searchquery(self, mock_get):
        mock_get.return_value = _resp({"vulnerabilityJsonDocument": []})
        a = NvdAdapter(delay=0.0)
        a.search("log4j", max_results=5)
        assert mock_get.call_args.kwargs["params"]["searchQuery"] == "log4j"
        assert "cveId" not in mock_get.call_args.kwargs["params"]

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_uses_apikey_when_set(self, mock_get):
        mock_get.return_value = _resp(
            {"vulnerabilityJsonDocument": [{"cve": {"id": "CVE-2021-44228"}}]}
        )
        a = NvdAdapter(delay=0.0, api_key="MYKEY")
        out = a.fetch("CVE-2021-44228")
        assert out[0]["id"] == "CVE-2021-44228"
        assert mock_get.call_args.kwargs["params"]["apikey"] == "MYKEY"

    def test_fetch_empty_returns_empty_list(self,):
        with patch("stitch_web_researcher.research_providers.httpx.get") as mock_get:
            mock_get.return_value = _resp({"vulnerabilityJsonDocument": []})
            a = NvdAdapter(delay=0.0)
            assert a.fetch("CVE-0000-0000") == []


# ── Zenodo ───────────────────────────────────────────────────────────────
class TestZenodoAdapter:
    def test_metadata(self):
        a = ZenodoAdapter(delay=0.0)
        assert a.name == "zenodo"
        assert a.domain == "scholarly"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_get.return_value = _resp(
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "1234567",
                            "metadata": {
                                "title": "A Dataset",
                                "publication_date": "2021-01-01",
                                "authors": [{"name": "Doe, Jane"}],
                                "resource_type": "softwareapplication",
                                "open_access": {"status": "t"},
                                "description": "<p>Hello world</p>",
                            },
                        }
                    ]
                }
            }
        )
        a = ZenodoAdapter(delay=0.0)
        out = a.search("machine learning", max_results=5)
        assert out[0]["id"] == "1234567"
        assert out[0]["title"] == "A Dataset"
        assert out[0]["authors"] == "Doe, Jane"
        assert out[0]["fields"]["zenodo"]["open_access"] is True
        assert "<p>" not in out[0]["snippet"]  # tags stripped

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses(self, mock_get):
        mock_get.return_value = _resp(
            {
                "_id": "1234567",
                "metadata": {"title": "A Dataset", "authors": ["Jane", {"name": "John"}]},
            }
        )
        a = ZenodoAdapter(delay=0.0)
        out = a.fetch("1234567")
        assert out[0]["title"] == "A Dataset"
        assert out[0]["authors"] == "Jane, John"
        assert mock_get.call_args.args[0].endswith("/record/1234567")

    def test_inject_auth_access_token(self):
        a = ZenodoAdapter(delay=0.0, api_key="TOK")
        _, params, _ = a.inject_auth("https://zenodo.org/api/records/search", {}, {})
        assert params["access_token"] == "TOK"


# ── Software Heritage ────────────────────────────────────────────────────
class TestSoftwareHeritageAdapter:
    def test_metadata(self):
        a = SoftwareHeritageAdapter(delay=0.0)
        assert a.name == "softwareheritage"
        assert a.domain == "tech"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_get.return_value = _resp(
            {
                "matches": [
                    {
                        "id": "p:github.com/user/repo",
                        "type": "project",
                        "label": "user/repo",
                        "url": "https://archive.softwareheritage.org/s/p:github.com/user/repo/",
                    }
                ]
            }
        )
        a = SoftwareHeritageAdapter(delay=0.0)
        out = a.search("myrepo", max_results=5)
        assert out[0]["id"] == "p:github.com/user/repo"
        assert out[0]["title"] == "user/repo"
        assert out[0]["fields"]["softwareheritage"]["type"] == "project"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_accepts_url_and_id(self, mock_get):
        mock_get.return_value = _resp(
            {
                "id": "s:abc123def",
                "type": "commit",
                "meta": {"name": "abc123", "date": "2021-01-01"},
            }
        )
        a = SoftwareHeritageAdapter(delay=0.0)
        out = a.fetch("https://archive.softwareheritage.org/s/s:abc123def/")
        assert out[0]["id"] == "s:abc123def"
        assert mock_get.call_args.args[0].endswith("/source/sid/s:abc123def")

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_accepts_bare_sid(self, mock_get):
        mock_get.return_value = _resp({"id": "d:deadbeef", "type": "directory"})
        a = SoftwareHeritageAdapter(delay=0.0)
        a.fetch("d:deadbeef")
        assert mock_get.call_args.args[0].endswith("/source/sid/d:deadbeef")


# ── Congress (legal) ─────────────────────────────────────────────────────
class TestCongressAdapter:
    def test_metadata_requires_key(self):
        a = CongressAdapter(delay=0.0)
        assert a.name == "congress"
        assert a.domain == "legal"
        assert a.requires_key is True

    def test_inject_auth_requires_key(self):
        a = CongressAdapter(delay=0.0, api_key="DATAKEY")
        _, params, _ = a.inject_auth("https://api.data.gov/congress/v1/members/search", {}, {})
        assert params["api_key"] == "DATAKEY"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_get.return_value = _resp(
            {
                "results": [
                    {
                        "cgi_id": "A000370",
                        "display_name": "Adam Schiff",
                        "url": "https://congress.gov/members/A000370",
                        "party": "Democratic",
                        "state": "CA",
                        "chamber": "House",
                        "title": "Representative",
                    }
                ]
            }
        )
        a = CongressAdapter(delay=0.0, api_key="DATAKEY")
        out = a.search("schiff", max_results=5)
        assert out[0]["id"] == "A000370"
        assert out[0]["title"] == "Adam Schiff"
        assert out[0]["fields"]["congress"]["party"] == "Democratic"
        assert mock_get.call_args.kwargs["params"]["api_key"] == "DATAKEY"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses(self, mock_get):
        mock_get.return_value = _resp(
            {"cgi_id": "A000370", "display_name": "Adam Schiff", "party": "Democratic"}
        )
        a = CongressAdapter(delay=0.0, api_key="DATAKEY")
        out = a.fetch("A000370")
        assert out[0]["id"] == "A000370"
        assert mock_get.call_args.args[0].endswith("/members/A000370")


# ── Yahoo Finance ────────────────────────────────────────────────────────
class TestYahooFinanceAdapter:
    def test_metadata(self):
        a = YahooFinanceAdapter(delay=0.0)
        assert a.name == "yahoo"
        assert a.domain == "financial"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_get.return_value = _resp(
            {
                "quoteCollection": {
                    "quotes": [
                        {
                            "symbol": "AAPL",
                            "shortName": "Apple Inc.",
                            "exchange": "NMS",
                            "quoteType": "EQUITY",
                            "marketCap": 3_000_000_000_000,
                        }
                    ]
                }
            }
        )
        a = YahooFinanceAdapter(delay=0.0)
        out = a.search("AAPL", max_results=5)
        assert out[0]["id"] == "AAPL"
        assert out[0]["title"] == "Apple Inc."
        assert out[0]["fields"]["yahoo"]["exchange"] == "NMS"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses(self, mock_get):
        mock_get.return_value = _resp(
            {
                "chart": {
                    "result": [
                        {
                            "meta": {
                                "symbol": "AAPL",
                                "fullExchangeName": "NasdaqGS",
                                "currency": "USD",
                                "regularMarketPrice": 175.5,
                                "previousClose": 174.0,
                            }
                        }
                    ]
                }
            }
        )
        a = YahooFinanceAdapter(delay=0.0)
        out = a.fetch("AAPL")
        assert out[0]["id"] == "AAPL"
        assert out[0]["fields"]["yahoo"]["currency"] == "USD"
        assert mock_get.call_args.args[0].endswith("/v8/finance/chart/AAPL")


# ── Overpass ─────────────────────────────────────────────────────────────
class TestOverpassAdapter:
    def test_metadata(self):
        a = OverpassAdapter(delay=0.0)
        assert a.name == "overpass"
        assert a.domain == "geo"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses_elements(self, mock_get):
        mock_get.return_value = _resp(
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 123,
                        "lat": 52.5,
                        "lon": 13.4,
                        "tags": {"name": "Brandenburg Gate", "amenity": "monument"},
                    }
                ]
            }
        )
        a = OverpassAdapter(delay=0.0)
        out = a.search("node[amenity=monument]", max_results=5)
        assert out[0]["id"] == "123"
        assert out[0]["title"] == "Brandenburg Gate"
        assert out[0]["fields"]["overpass"]["lat"] == 52.5
        # A bare query is wrapped in [out:json].
        data = mock_get.call_args.kwargs["params"]["data"]
        assert data.startswith("[out:json]")

    def test_empty_query_raises(self):
        a = OverpassAdapter(delay=0.0)
        with pytest.raises(ValueError):
            a.search("", max_results=5)


# ── US Census ────────────────────────────────────────────────────────────
class TestCensusAdapter:
    def test_metadata_requires_key(self):
        a = CensusAdapter(delay=0.0)
        assert a.name == "census"
        assert a.domain == "geo"
        assert a.requires_key is True

    def test_inject_auth_sets_key(self):
        a = CensusAdapter(delay=0.0, api_key="CKEY")
        _, params, _ = a.inject_auth("https://api.census.gov/data/2019/acs/acs1", {}, {})
        assert params["key"] == "CKEY"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_dict_spec(self, mock_get):
        mock_get.return_value = _resp(
            [["b01003_001e", "state"], ["331210334", "04"], ["2180366", "01"]]
        )
        a = CensusAdapter(delay=0.0, api_key="CKEY")
        out = a.search(
            {"dataset": "2019/acs/acs1", "get": "B01003_001E", "for": "state:*"},
            max_results=5,
        )
        assert len(out) == 2
        assert out[0]["id"] == "04"
        assert out[0]["raw"] == '{"b01003_001e": "331210334", "state": "04"}'
        assert mock_get.call_args.args[0].endswith("/data/2019/acs/acs1")
        assert mock_get.call_args.kwargs["params"]["key"] == "CKEY"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_string_spec(self, mock_get):
        mock_get.return_value = _resp([["POP", "state"], ["100", "04"]])
        a = CensusAdapter(delay=0.0, api_key="CKEY")
        out = a.search("2019/pep/natstprc?get=POP&for=state:*", max_results=5)
        assert out[0]["id"] == "04"
        assert mock_get.call_args.args[0].endswith("/data/2019/pep/natstprc")

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_by_state_id(self, mock_get):
        mock_get.return_value = _resp([["POP", "state"], ["100", "04"], ["200", "06"]])
        a = CensusAdapter(delay=0.0, api_key="CKEY")
        out = a.fetch("04", {"dataset": "2019/pep/natstprc", "get": "POP", "for": "state:*"})
        assert out[0]["id"] == "04"

    def test_search_missing_dataset_raises(self):
        a = CensusAdapter(delay=0.0)
        with pytest.raises(ValueError):
            a.search("?", max_results=5)

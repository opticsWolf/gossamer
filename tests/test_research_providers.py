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
    ArxivAdapter,
    CrossrefAdapter,
    DoajAdapter,
    FredAdapter,
    GitHubAdapter,
    OpenAlexAdapter,
    OpenLibraryAdapter,
    OpenMeteoAdapter,
    PubmedAdapter,
    WorldBankAdapter,
    _parse_lat_lon,
    _rate_state_from_headers,
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
                    "title": "A Paper",
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
        mock_resp.json.return_value = {"id": "W999", "title": "Single", "doi": "doi:10/y"}
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
# CrossrefAdapter (scholarly)
# ────────────────────────────────────────────────────────────────


class TestCrossrefAdapter:
    def test_metadata(self):
        prov = CrossrefAdapter(delay=0.0)
        assert prov.name == "crossref"
        assert prov.domain == "scholarly"
        assert prov.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {
                "items": [
                    {
                        "DOI": "10.1/x",
                        "title": ["Crossref Paper"],
                        "URL": "https://doi.org/10.1/x",
                        "published": {"date-parts": [[2020, 1, 1]]},
                        "author": [{"family": "Hopper"}, {"name": "Lovelace"}],
                        "abstract": "An abstract",
                    }
                ]
            }
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = CrossrefAdapter(delay=0.0, email="me@example.org").search("q", max_results=1)
        assert out[0]["id"] == "10.1/x"
        assert out[0]["title"] == "Crossref Paper"
        assert out[0]["doi"] == "10.1/x"
        assert out[0]["authors"] == "Hopper, Lovelace"
        assert "An abstract" in out[0]["snippet"]

    def test_inject_auth_sets_polite_email(self):
        prov = CrossrefAdapter(delay=0.0, email="me@example.org")
        _, _, headers = prov.inject_auth("https://api.crossref.org/works", {}, {})
        assert "email=me@example.org" in headers["User-Agent"]
        assert headers["Accept"] == "application/json"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "message": {"DOI": "10.1/x", "title": ["Single"], "URL": "https://x"}
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = CrossrefAdapter(delay=0.0).fetch("10.1/x")
        assert out[0]["id"] == "10.1/x"
        assert out[0]["title"] == "Single"

    def test_parse_headers_maps_ratelimit(self):
        state = _rate_state_from_headers(
            {"X-RateLimit-Remaining": "42", "X-Rate-Limit-Limit": "100"}
        )
        assert state.remaining == 42
        assert state.rps == 100.0


# ────────────────────────────────────────────────────────────────
# ArxivAdapter (scholarly)
# ────────────────────────────────────────────────────────────────


ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query</title>
  <entry>
    <title>  arXiv Paper  </title>
    <id>http://arxiv.org/abs/1234.5678v1</id>
    <published>2019-01-01T00:00:00Z</published>
    <updated>2019-02-01T00:00:00Z</updated>
    <summary>An abstract
summary.</summary>
    <author><name>Ada</name></author>
    <author><name>Grace</name></author>
    <category term="math" scheme="http://www.arxiv.org/schemas/atom"/>
    <category term="cs.AI" scheme="http://www.arxiv.org/schemas/atom"/>
    <link rel="alternate" type="text/html" href="http://arxiv.org/abs/1234.5678v1"/>
    <link rel="related" title="pdf" href="http://arxiv.org/pdf/1234.5678v1"/>
    <link rel="related" title="doi" href="https://doi.org/10.1000/xyz"/>
    <arxiv:primary_category term="cs.AI"/>
    <arxiv:doi>10.1000/xyz</arxiv:doi>
    <arxiv:comment>6 pages</arxiv:comment>
  </entry>
</feed>"""

ERROR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>ArXiv Query</title>
    <id>http://arxiv.org/api/abc</id>
    <updated>2020-01-01T00:00:00Z</updated>
    <summary>Error incorrect id format for 1234.5678</summary>
  </entry>
</feed>"""


class TestArxivAdapter:
    def test_metadata(self):
        prov = ArxivAdapter()
        assert prov.name == "arxiv"
        assert prov.domain == "scholarly"
        # Responsible-use ceiling: 1 req / 3 s.
        assert prov.rate_limit.search_interval == 3.0

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = ATOM_FEED
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = ArxivAdapter(delay=0.0).search("quantum", max_results=1)
        r = out[0]
        assert r["title"] == "arXiv Paper"  # whitespace collapsed
        assert r["id"] == "1234.5678v1"      # bare id, not the abs URL
        assert r["url"] == "http://arxiv.org/abs/1234.5678v1"
        assert r["authors"] == "Ada, Grace"
        assert r["doi"] == "10.1000/xyz"     # from <arxiv:doi>
        assert r["published"] == "2019-01-01T00:00:00Z"
        assert r["snippet"] == "An abstract summary."  # newlines collapsed
        assert r["fields"]["arxiv"]["primary_category"] == "cs.AI"
        # The request used the documented Atom query param, not "query".
        assert mock_get.call_args[1]["params"]["search_query"] == "quantum"
        assert mock_get.call_args[1]["headers"]["User-Agent"] == ArxivAdapter._ARXIV_UA

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_accepts_abs_url(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = ATOM_FEED
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = ArxivAdapter(delay=0.0).fetch("https://arxiv.org/abs/1234.5678v1")
        assert out[0]["id"] == "1234.5678v1"
        assert out[0]["url"] == "http://arxiv.org/abs/1234.5678v1"
        # fetch uses id_list (the version-safe documented form), not search_query.
        assert mock_get.call_args[1]["params"]["id_list"] == "1234.5678v1"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_accepts_bare_id(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = ATOM_FEED
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = ArxivAdapter(delay=0.0).fetch("arxiv.org/abs/1707.06376")
        # The abs URL / version suffix is stripped to the bare id for id_list.
        assert mock_get.call_args[1]["params"]["id_list"] == "1707.06376"
        assert out[0]["title"] == "arXiv Paper"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_bad_id_returns_note(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = ERROR_FEED
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = ArxivAdapter(delay=0.0).fetch("1234.5678")
        assert out[0]["title"] == ""
        assert "Error" in out[0]["snippet"]


# ────────────────────────────────────────────────────────────────
# PubmedAdapter (scholarly)
# ────────────────────────────────────────────────────────────────


class TestPubmedAdapter:
    def test_metadata_keyless_rate(self):
        prov = PubmedAdapter()
        assert prov.name == "pubmed"
        assert prov.rate_limit.search_interval == 0.33

    def test_key_adds_api_key(self):
        prov = PubmedAdapter(delay=0.0, api_key="secret")
        _, params, _ = prov.inject_auth("https://x", {}, {})
        assert params["api_key"] == "secret"
        assert params["email"]  # email always present

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_returns_ids(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "esearchresult": {"idlist": ["111", "222"]}
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = PubmedAdapter(delay=0.0).search("cancer", max_results=2)
        assert [o["id"] for o in out] == ["111", "222"]
        assert out[0]["url"].endswith("/111/")

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = (
            "<PubmedArticleSet><PubmedArticle><MedlineCitation>"
            "<PMID>111</PMID><Article><ArticleTitle>A PubMed "
            "Article</ArticleTitle><AuthorList><Author><LastName>Hopper"
            "</LastName><ForeName>Grace</ForeName></Author></AuthorList>"
            "</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
        )
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = PubmedAdapter(delay=0.0).fetch("111")
        assert out[0]["id"] == "111"
        assert out[0]["title"] == "A PubMed Article"
        assert out[0]["authors"] == "Hopper Grace"
        # efetch retrieval is done in XML (JSON mode returns a bare id).
        assert mock_get.call_args[1]["params"]["retmode"] == "xml"


# ────────────────────────────────────────────────────────────────
# DoajAdapter (scholarly)
# ────────────────────────────────────────────────────────────────


class TestDoajAdapter:
    def test_metadata(self):
        prov = DoajAdapter(delay=0.0)
        assert prov.name == "doaj"
        assert prov.domain == "scholarly"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {
                    "id": "abc",
                    "bibjson": {
                        "title": "DOAJ Paper",
                        "author": [{"name": "A. Author"}],
                        "identifier": [{"id": "10.1/abc", "type": "doi"}],
                    },
                }
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = DoajAdapter(delay=0.0).search("open access", max_results=1)
        assert out[0]["id"] == "abc"
        assert out[0]["doi"] == "10.1/abc"
        assert out[0]["authors"] == "A. Author"
        # The query is sent as a path segment, not a query parameter.
        assert "/search/articles/" in mock_get.call_args[0][0]
        assert "query=" not in mock_get.call_args[0][0]


# ────────────────────────────────────────────────────────────────
# OpenLibraryAdapter (library)
# ────────────────────────────────────────────────────────────────


class TestOpenLibraryAdapter:
    def test_metadata(self):
        prov = OpenLibraryAdapter(delay=0.0)
        assert prov.name == "openlibrary"
        assert prov.domain == "library"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "docs": [
                {
                    "key": "/books/OL1",
                    "title": "A Book",
                    "author": ["Author One"],
                    "first_publish_year": 1999,
                }
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = OpenLibraryAdapter(delay=0.0).search("book", max_results=1)
        assert out[0]["id"] == "/books/OL1"
        assert out[0]["url"] == "https://openlibrary.org/books/OL1"
        assert out[0]["authors"] == "Author One"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_normalises_id(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "key": "/books/OL1",
            "title": "A Book",
            "authors": [{"name": "Author Two"}],
            "publish_date": "1999",
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = OpenLibraryAdapter(delay=0.0).fetch("OL1")
        assert out[0]["id"] == "/books/OL1"
        assert out[0]["authors"] == "Author Two"
        assert out[0]["published"] == "1999"
        assert mock_get.call_args[0][0] == "https://openlibrary.org/books/OL1.json"


# ────────────────────────────────────────────────────────────────
# WorldBankAdapter (financial)
# ────────────────────────────────────────────────────────────────


class TestWorldBankAdapter:
    def test_metadata(self):
        prov = WorldBankAdapter(delay=0.0)
        assert prov.name == "worldbank"
        assert prov.domain == "financial"

    def test_search_returns_unavailable_note(self):
        # /v2/search was retired; search returns a structured note and makes
        # no network call.
        out = WorldBankAdapter(delay=0.0).search("gdp", max_results=1)
        assert out[0]["source"] == "worldbank"
        assert "unavailable" in out[0]["title"].lower()

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_uses_data_endpoint(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"page": 1, "per_page": 20},
            [
                {
                    "indicator": {"id": "NY.GDP.MCAP.CD", "value": "GDP, nominal"},
                    "date": "2023",
                    "value": 1.0,
                }
            ],
        ]
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = WorldBankAdapter(delay=0.0).fetch("NY.GDP.MCAP.CD")
        # Requested the canonical country-all indicator endpoint.
        assert mock_get.call_args[0][0] == "https://api.worldbank.org/v2/country/all/indicator/NY.GDP.MCAP.CD"
        assert out[0]["id"] == "NY.GDP.MCAP.CD"
        assert out[0]["title"] == "GDP, nominal"
        assert "2023:1.0" in out[0]["snippet"]


# ────────────────────────────────────────────────────────────────
# FredAdapter (financial)
# ────────────────────────────────────────────────────────────────


class TestFredAdapter:
    def test_metadata(self):
        prov = FredAdapter(delay=0.0)
        assert prov.name == "fred"
        assert prov.domain == "financial"

    def test_key_injected(self):
        prov = FredAdapter(delay=0.0, api_key="k")
        _, params, _ = prov.inject_auth("https://x", {}, {})
        assert params["api_key"] == "k"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses_observations(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "observation": [{"date": "2020-01-01", "value": "42"}]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = FredAdapter(delay=0.0).fetch("GDP")
        assert out[0]["id"] == "GDP"
        assert "2020-01-01=42" in out[0]["snippet"]


# ────────────────────────────────────────────────────────────────
# GitHubAdapter (tech)
# ────────────────────────────────────────────────────────────────


class TestGitHubAdapter:
    def test_metadata(self):
        prov = GitHubAdapter(delay=0.0)
        assert prov.name == "github"
        assert prov.domain == "tech"

    def test_inject_auth_sets_bearer(self):
        prov = GitHubAdapter(delay=0.0, api_key="tok")
        _, _, headers = prov.inject_auth("https://api.github.com", {}, {})
        assert headers["Authorization"] == "Bearer tok"
        assert headers["Accept"] == "application/vnd.github+json"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {
                    "id": 123,
                    "full_name": "o/r",
                    "html_url": "https://github.com/o/r",
                    "description": "A repo",
                    "language": "Rust",
                    "stargazers_count": 10,
                }
            ]
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        out = GitHubAdapter(delay=0.0).search("rust", max_results=1)
        assert out[0]["id"] == "123"
        assert out[0]["title"] == "o/r"
        assert out[0]["fields"]["github"]["language"] == "Rust"

    def test_parse_headers_maps_ratelimit(self):
        state = _rate_state_from_headers(
            {
                "X-RateLimit-Limit": "5000",
                "X-RateLimit-Remaining": "4999",
                "X-RateLimit-Reset": "1700000000",
            }
        )
        assert state.rps == 5000.0
        assert state.remaining == 4999
        assert state.reset_seconds == 1700000000


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

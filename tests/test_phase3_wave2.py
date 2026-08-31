

from unittest.mock import MagicMock, patch

import pytest

from stitch_web_researcher.research_providers import (
    AlphaVantageAdapter,
    BioRxivAdapter,
    CourtListenerAdapter,
    EcfrAdapter,
    EurlexAdapter,
    FederalRegisterAdapter,
    GermanGovAdapter,
    ChemRxivAdapter,
)


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


# ── Wave 2: CourtListener, eCFR, Federal Register, EUR-Lex, German gov ─────

class TestCourtListenerAdapter:
    def test_metadata_keyless_legal(self):
        a = CourtListenerAdapter(delay=0.0)
        assert a.name == "courtlistener"
        assert a.domain == "legal"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses_cluster(self, mock_get):
        mock_get.return_value = _resp(
            {
                "results": [
                    {
                        "cluster_id": 10599950,
                        "caseName": "Smith v. Smith",
                        "caseNameFull": "Smith v. Smith, No. 1263/23",
                        "court": "Court of Special Appeals of Maryland",
                        "court_citation_string": "Md. Ct. Spec. App.",
                        "dateFiled": "2025-06-06",
                        "docketNumber": "1263/23",
                        "absolute_url": "/opinion/10599950/smith-v-smith/",
                        "citation": [],
                        "neutralCite": "",
                        "citeCount": 3,
                    }
                ]
            }
        )
        a = CourtListenerAdapter(delay=0.0)
        out = a.search("smith", max_results=5)
        assert len(out) == 1
        assert out[0]["id"] == "10599950"
        assert out[0]["title"] == "Smith v. Smith"
        assert out[0]["url"] == "https://www.courtlistener.com/opinion/10599950/smith-v-smith/"
        assert out[0]["fields"]["court"] == "Court of Special Appeals of Maryland"
        assert out[0]["fields"]["cite_count"] == 3
        # Free-text query is passed through as the QL string.
        assert mock_get.call_args.kwargs["params"]["q"] == "smith"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses_cluster(self, mock_get):
        mock_get.return_value = _resp(
            {
                "cluster_id": 10599950,
                "caseName": "Smith v. Smith",
                "court": "Md. Ct. Spec. App.",
                "absolute_url": "/opinion/10599950/",
            }
        )
        a = CourtListenerAdapter(delay=0.0)
        out = a.fetch("10599950")
        assert out[0]["id"] == "10599950"
        assert mock_get.call_args.args[0].endswith("/cluster/10599950/")


class TestEcfrAdapter:
    def test_metadata(self):
        a = EcfrAdapter(delay=0.0)
        assert a.name == "ecfr"
        assert a.domain == "legal"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parse_cfr_citation(self, mock_get):
        mock_get.return_value = _resp(
            {
                "title": 21,
                "part": 113,
                "title_title": "FOOD AND DRUG ADMINISTRATION",
                "part_title": "PHYSICAL ENTRY REQUIREMENTS",
                "section_numbers": ["113"],
                "sections": {
                    "113": {
                        "label": "113",
                        "section_number": "113",
                        "content": "<p>Requirements for ...</p>",
                    }
                },
            }
        )
        a = EcfrAdapter(delay=0.0)
        out = a.search("21 CFR 113", max_results=5)
        assert len(out) == 1
        assert out[0]["id"] == "21/113"
        assert out[0]["fields"]["part"] == "113"
        assert "FOOD AND DRUG ADMINISTRATION" in out[0]["title"]
        # Citation parsed into title/part path.
        assert mock_get.call_args.args[0].endswith("/21/113")

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_slash_citation(self, mock_get):
        mock_get.return_value = _resp(
            {"title": 21, "part": 113, "sections": {"113": {"label": "113", "content": "x"}}}
        )
        a = EcfrAdapter(delay=0.0)
        out = a.fetch("21/113")
        assert out[0]["id"] == "21/113"
        assert mock_get.call_args.args[0].endswith("/21/113")


class TestFederalRegisterAdapter:
    def test_metadata_requires_key(self):
        a = FederalRegisterAdapter(delay=0.0)
        assert a.name == "federalregister"
        assert a.domain == "legal"
        assert a.requires_key is True

    def test_inject_auth_sets_data_key(self):
        a = FederalRegisterAdapter(delay=0.0, api_key="DKEY")
        _, params, _ = a.inject_auth("https://api.federalregister.gov/v1/documents.json", {}, {})
        assert params["api_key"] == "DKEY"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses_documents(self, mock_get):
        mock_get.return_value = _resp(
            {
                "documents": [
                    {
                        "document_number": "2024-12345",
                        "title": "Guidance on ...",
                        "html_url": "https://www.federalregister.gov/d/2024-12345",
                        "doc_date": "2024-06-01",
                        "abstract": "<p>Some abstract text.</p>",
                        "document_type": "RULE",
                        "type": "Rule",
                        "agency": {"name": "Food and Drug Administration"},
                    }
                ]
            }
        )
        a = FederalRegisterAdapter(delay=0.0, api_key="DKEY")
        out = a.search("guidance", max_results=5)
        assert out[0]["id"] == "2024-12345"
        assert out[0]["fields"]["agency"] == "Food and Drug Administration"
        assert "<p>" not in out[0]["snippet"]  # tags stripped
        assert mock_get.call_args.kwargs["params"]["api_key"] == "DKEY"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses_document(self, mock_get):
        mock_get.return_value = _resp(
            {
                "document_number": "2024-12345",
                "title": "Guidance on ...",
                "html_url": "https://www.federalregister.gov/d/2024-12345",
                "agency": {"name": "FDA"},
            }
        )
        a = FederalRegisterAdapter(delay=0.0, api_key="DKEY")
        out = a.fetch("2024-12345")
        assert out[0]["id"] == "2024-12345"
        assert mock_get.call_args.args[0].endswith("/documents/2024-12345.json")


class TestEurlexAdapter:
    def test_metadata(self):
        a = EurlexAdapter(delay=0.0)
        assert a.name == "eurlex"
        assert a.domain == "legal"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses_hits(self, mock_get):
        mock_get.return_value = _resp(
            {
                "response": {
                    "results": [
                        {
                            "docid": "ETXTINXT:32016X0526(01.02.002",
                            "title": "Regulation (EU, Euratom) No 883/2016",
                            "language": "en",
                            "publicationDate": "2016-04-27",
                            "uri": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0883",
                        }
                    ]
                }
            }
        )
        a = EurlexAdapter(delay=0.0)
        out = a.search("regulation", max_results=5)
        assert out[0]["id"] == "ETXTINXT:32016X0526(01.02.002"
        assert out[0]["fields"]["language"] == "en"
        assert mock_get.call_args.kwargs["params"]["format"] == "json"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_scopes_to_docid(self, mock_get):
        mock_get.return_value = _resp({"response": {"results": [{"docid": "CELEX:32016R0883", "title": "Reg 883"}]}})
        a = EurlexAdapter(delay=0.0)
        out = a.fetch("CELEX:32016R0883")
        assert out[0]["title"] == "Reg 883"
        assert mock_get.call_args.kwargs["params"]["q"] == "docid:CELEX:32016R0883"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_empty_when_no_hits(self, mock_get):
        mock_get.return_value = _resp({"response": {"results": []}})
        a = EurlexAdapter(delay=0.0)
        assert a.fetch("NOPE") == []


class TestGermanGovAdapter:
    def test_metadata(self):
        a = GermanGovAdapter(delay=0.0)
        assert a.name == "german"
        assert a.domain == "legal"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses_entries(self, mock_get):
        mock_get.return_value = _resp(
            [
                {
                    "id": "12345",
                    "titel": "Verordnung über ...",
                    "art": "Verordnung",
                    "datum": "2024-05-30",
                    "suchbegriffe": ["Verbraucherschutz"],
                    "url": "https://www.de.gov.de/id/12345",
                }
            ]
        )
        a = GermanGovAdapter(delay=0.0)
        out = a.search("Verbraucherschutz", max_results=5)
        assert out[0]["id"] == "12345"
        assert out[0]["title"] == "Verordnung über ..."
        assert out[0]["fields"]["art"] == "Verordnung"
        assert mock_get.call_args.kwargs["params"]["suchbegriff"] == "Verbraucherschutz"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses_entry(self, mock_get):
        mock_get.return_value = _resp({"aktenseite": {"id": "12345", "titel": "Vo. ..."}})
        a = GermanGovAdapter(delay=0.0)
        out = a.fetch("12345")
        assert out[0]["id"] == "12345"
        assert mock_get.call_args.args[0].endswith("/aktenseiten/12345")


# ── Wave 2: bioRxiv, ChemRxiv, Alpha Vantage ──────────────────────────────

class TestBioRxivAdapter:
    def test_metadata(self):
        a = BioRxivAdapter(delay=0.0)
        assert a.name == "biorxiv"
        assert a.domain == "scholarly"
        assert a.requires_key is False

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_recent_defaults_to_most_recent(self, mock_get):
        mock_get.return_value = _resp(
            {
                "collection": [
                    {
                        "doi": "10.1101/2024.07.17.603927",
                        "title": "Reward history cues",
                        "authors": "Ramamurthy, D. L.; Rodriguez, L.",
                        "date": "2024-08-01",
                        "category": "neuroscience",
                        "version": "2",
                        "type": "new results",
                        "license": "cc_by_nc_nd",
                        "abstract": "<p>Prior reward is a potent cue.</p>",
                    }
                ]
            }
        )
        a = BioRxivAdapter(delay=0.0)
        out = a.search("neuroscience", max_results=2)
        assert out[0]["id"] == "10.1101/2024.07.17.603927"
        assert out[0]["fields"]["category"] == "neuroscience"
        assert "<p>" not in out[0]["snippet"]
        # Non-date, non-DOI query -> most-recent-N interval (2).
        assert mock_get.call_args.args[0].endswith("/biorxiv/2/0/json")

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_date_interval(self, mock_get):
        mock_get.return_value = _resp({"collection": []})
        a = BioRxivAdapter(delay=0.0)
        a.search("2024-08-01/2024-08-02", max_results=2)
        assert mock_get.call_args.args[0].endswith("/biorxiv/2024-08-01/2024-08-02/0/json")

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_by_doi(self, mock_get):
        mock_get.return_value = _resp(
            {
                "collection": [
                    {
                        "doi": "10.1101/2024.07.17.603927",
                        "title": "Reward history cues",
                        "category": "neuroscience",
                    }
                ]
            }
        )
        a = BioRxivAdapter(delay=0.0)
        out = a.fetch("10.1101/2024.07.17.603927")
        assert out[0]["id"] == "10.1101/2024.07.17.603927"
        assert mock_get.call_args.args[0].endswith("/biorxiv/10.1101/2024.07.17.603927/na/json")

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_non_doi_returns_empty(self, mock_get):
        a = BioRxivAdapter(delay=0.0)
        assert a.fetch("not-a-doi") == []
        assert not mock_get.called

    def test_medrxiv_server(self):
        a = BioRxivAdapter(delay=0.0, server="medrxiv")
        assert a.server == "medrxiv"


class TestChemRxivAdapter:
    def test_metadata_requires_key(self):
        a = ChemRxivAdapter(delay=0.0)
        assert a.name == "chemrxiv"
        assert a.domain == "scholarly"
        assert a.requires_key is True

    def test_inject_auth_sets_bearer(self):
        a = ChemRxivAdapter(delay=0.0, api_key="TKN")
        _, _, headers = a.inject_auth("https://chemrxiv.org/engage/api-gateway/chemrxiv/assets/orp/item/search", {}, {})
        assert headers["Authorization"] == "Bearer TKN"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses_items(self, mock_get):
        mock_get.return_value = _resp(
            {
                "data": [
                    {
                        "id": 5349151,
                        "title": "Catalytic asymmetric synthesis",
                        "doi": "10.26434/chemrxiv.2020-abcde",
                        "authors": [{"name": "Doe, Jane"}, {"name": "Smith, John"}],
                        "published_on": "2020-07-01",
                        "topics": [{"name": "Organic Chemistry"}],
                        "url": "https://chemrxiv.org/engage/chemrxiv/public-article-details/5349151",
                    }
                ],
                "meta": {"total_count": 1},
            }
        )
        a = ChemRxivAdapter(delay=0.0, api_key="TKN")
        out = a.search("catalysis", max_results=5)
        assert out[0]["id"] == "5349151"
        assert out[0]["authors"] == "Doe, Jane, Smith, John"
        assert out[0]["fields"]["doi"] == "10.26434/chemrxiv.2020-abcde"
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer TKN"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses_item(self, mock_get):
        mock_get.return_value = _resp({"data": {"id": 5349151, "title": "Catalytic"}})
        a = ChemRxivAdapter(delay=0.0, api_key="TKN")
        out = a.fetch("5349151")
        assert out[0]["title"] == "Catalytic"
        assert mock_get.call_args.args[0].endswith("/item/5349151")


class TestAlphaVantageAdapter:
    def test_metadata_requires_key(self):
        a = AlphaVantageAdapter(delay=0.0)
        assert a.name == "alphavantage"
        assert a.domain == "financial"
        assert a.requires_key is True

    def test_inject_auth_sets_apikey(self):
        a = AlphaVantageAdapter(delay=0.0, api_key="K")
        _, params, _ = a.inject_auth("https://www.alphavantage.co/query", {}, {})
        assert params["apikey"] == "K"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_search_parses_data(self, mock_get):
        mock_get.return_value = _resp(
            {
                "data": [
                    {
                        "symbol": "AAPL",
                        "companyName": "Apple Inc.",
                        "instrument_type": "EQUITY",
                        "ticker": "AAPL",
                        "sector": "Technology",
                    }
                ]
            }
        )
        a = AlphaVantageAdapter(delay=0.0, api_key="K")
        out = a.search("apple", max_results=5)
        assert out[0]["id"] == "AAPL"
        assert out[0]["title"] == "Apple Inc."
        assert out[0]["fields"]["sector"] == "Technology"
        assert mock_get.call_args.kwargs["params"]["function"] == "SEARCH"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_parses_daily(self, mock_get):
        mock_get.return_value = _resp(
            {
                "Meta Data": {"1. symbol": "AAPL", "4. last refreshed": "2024-01-02"},
                "Time Series (Daily)": {
                    "2024-01-02": {
                        "1. open": "185",
                        "2. high": "186",
                        "3. low": "184",
                        "4. close": "185.5",
                        "5. volume": "10000000",
                    }
                },
            }
        )
        a = AlphaVantageAdapter(delay=0.0, api_key="K")
        out = a.fetch("AAPL")
        assert out[0]["id"] == "AAPL"
        assert out[0]["fields"]["close"] == "185.5"
        assert mock_get.call_args.kwargs["params"]["function"] == "TIME_SERIES_DAILY"

    @patch("stitch_web_researcher.research_providers.httpx.get")
    def test_fetch_no_data_surfaces_note(self, mock_get):
        mock_get.return_value = _resp({"notes": "Takeaway: no data for the requested symbol."})
        a = AlphaVantageAdapter(delay=0.0, api_key="K")
        out = a.fetch("NOTASYMBOL")
        assert len(out) == 1
        assert "no data" in out[0]["snippet"]

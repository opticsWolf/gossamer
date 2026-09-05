

from unittest.mock import MagicMock, patch

import pytest

from gossamer.research_providers import (
    AlphaVantageAdapter,
    BioRxivAdapter,
    CourtListenerAdapter,
    EcfrAdapter,
    FederalRegisterAdapter,
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

    @patch("gossamer.research_providers.httpx.get")
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

    @patch("gossamer.research_providers.httpx.get")
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
        assert mock_get.call_args.args[0].endswith("/clusters/10599950/")

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_401_is_actionable(self, mock_get):
        # Cluster detail needs a token; search stays keyless.
        denied = MagicMock()
        denied.status_code = 401
        denied.raise_for_status.side_effect = AssertionError("must not raise")
        mock_get.return_value = denied
        a = CourtListenerAdapter(delay=0.0)
        with pytest.raises(RuntimeError, match="GOSSAMER_COURTLISTENER_KEY"):
            a.fetch("10599950")

class TestEcfrAdapter:
    def test_metadata(self):
        a = EcfrAdapter(delay=0.0)
        assert a.name == "ecfr"
        assert a.domain == "legal"
        assert a.requires_key is False

    @staticmethod
    def _titles_response():
        return _resp(
            {
                "titles": [
                    {
                        "number": 21,
                        "name": "Food and Drugs",
                        "latest_issue_date": "2026-08-31",
                    }
                ]
            }
        )

    @staticmethod
    def _structure_response():
        return _resp(
            {
                "identifier": "21",
                "label": "Title 21—Food and Drugs",
                "type": "title",
                "children": [
                    {
                        "identifier": "113",
                        "label": "Part 113—Physical Entry Requirements",
                        "label_description": "Requirements for physical entry.",
                        "type": "part",
                        "children": [
                            {
                                "identifier": "113.3",
                                "label": "§ 113.3 Definitions",
                                "type": "section",
                            }
                        ],
                    }
                ],
            }
        )

    @patch("gossamer.research_providers.httpx.get")
    def test_search_parse_cfr_citation(self, mock_get):
        mock_get.side_effect = [
            self._titles_response(),
            self._structure_response(),
        ]
        a = EcfrAdapter(delay=0.0)
        out = a.search("21 CFR 113", max_results=5)
        assert len(out) == 1
        assert out[0]["id"] == "21/113"
        assert out[0]["fields"]["part"] == "113"
        assert "113.3" in out[0]["snippet"]
        urls = [c.args[0] for c in mock_get.call_args_list]
        assert urls[0].endswith("/titles.json")
        assert "/structure/2026-08-31/title-21.json" in urls[1]

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_slash_citation(self, mock_get):
        mock_get.side_effect = [
            self._titles_response(),
            self._structure_response(),
        ]
        a = EcfrAdapter(delay=0.0)
        out = a.fetch("21/113")
        assert out[0]["id"] == "21/113"
        assert out[0]["url"] == "https://www.ecfr.gov/current/title-21/part-113"

    @patch("gossamer.research_providers.httpx.get")
    def test_unknown_part_raises(self, mock_get):
        mock_get.side_effect = [
            self._titles_response(),
            self._structure_response(),
        ]
        a = EcfrAdapter(delay=0.0)
        with pytest.raises(ValueError, match="no part"):
            a.fetch("21/999")

class TestFederalRegisterAdapter:
    def test_metadata_keyless(self):
        a = FederalRegisterAdapter(delay=0.0)
        assert a.name == "federalregister"
        assert a.domain == "legal"
        assert a.requires_key is False

    def test_inject_auth_sends_no_key(self):
        # An empty api_key triggers a redirect loop; never send one.
        a = FederalRegisterAdapter(delay=0.0)
        _, params, _ = a.inject_auth("https://www.federalregister.gov/api/v1/documents.json", {}, {})
        assert "api_key" not in params

    @patch("gossamer.research_providers.httpx.get")
    def test_search_parses_documents(self, mock_get):
        # Live envelope nests hits under "results".
        mock_get.return_value = _resp(
            {
                "count": 1,
                "results": [
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
        a = FederalRegisterAdapter(delay=0.0)
        out = a.search("guidance", max_results=5)
        assert out[0]["id"] == "2024-12345"
        assert out[0]["fields"]["agency"] == "Food and Drug Administration"
        assert "<p>" not in out[0]["snippet"]  # tags stripped
        assert "api_key" not in mock_get.call_args.kwargs["params"]
        assert mock_get.call_args.args[0].startswith("https://www.federalregister.gov/api/v1/")

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_parses_document(self, mock_get):
        mock_get.return_value = _resp(
            {
                "document_number": "2024-12345",
                "title": "Guidance on ...",
                "html_url": "https://www.federalregister.gov/d/2024-12345",
                "agency": {"name": "FDA"},
            }
        )
        a = FederalRegisterAdapter(delay=0.0)
        out = a.fetch("2024-12345")
        assert out[0]["id"] == "2024-12345"
        assert mock_get.call_args.args[0].endswith("/documents/2024-12345.json")

# ── Wave 2: bioRxiv, ChemRxiv, Alpha Vantage ──────────────────────────────

class TestBioRxivAdapter:
    def test_metadata(self):
        a = BioRxivAdapter(delay=0.0)
        assert a.name == "biorxiv"
        assert a.domain == "scholarly"
        assert a.requires_key is False

    @patch("gossamer.research_providers.httpx.get")
    def test_search_free_text_raises_actionable_error(self, mock_get):
        # The API is date/DOI-addressed; free text used to hit an endpoint
        # that always answers empty. Fail fast instead (no retry burn).
        a = BioRxivAdapter(delay=0.0)
        with pytest.raises(ValueError, match="date/DOI-addressed"):
            a.search("neuroscience", max_results=2)
        assert not mock_get.called

    @patch("gossamer.research_providers.httpx.get")
    def test_search_doi_lookup(self, mock_get):
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
        out = a.search("10.1101/2024.07.17.603927", max_results=2)
        assert out[0]["id"] == "10.1101/2024.07.17.603927"
        assert out[0]["fields"]["category"] == "neuroscience"
        assert "<p>" not in out[0]["snippet"]

    @patch("gossamer.research_providers.httpx.get")
    def test_search_date_interval(self, mock_get):
        mock_get.return_value = _resp({"collection": []})
        a = BioRxivAdapter(delay=0.0)
        a.search("2024-08-01/2024-08-02", max_results=2)
        assert mock_get.call_args.args[0].endswith("/biorxiv/2024-08-01/2024-08-02/0/json")

    @patch("gossamer.research_providers.httpx.get")
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

    @patch("gossamer.research_providers.httpx.get")
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

    @patch("gossamer.research_providers.httpx.get")
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

    @patch("gossamer.research_providers.httpx.get")
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

    @patch("gossamer.research_providers.httpx.get")
    def test_search_parses_best_matches(self, mock_get):
        # Live SYMBOL_SEARCH shape: bestMatches with numbered keys.
        mock_get.return_value = _resp(
            {
                "bestMatches": [
                    {
                        "1. symbol": "AAPL",
                        "2. name": "Apple Inc.",
                        "3. type": "Equity",
                        "4. region": "United States",
                        "8. currency": "USD",
                        "9. matchScore": "1.0000",
                    }
                ]
            }
        )
        a = AlphaVantageAdapter(delay=0.0, api_key="K")
        out = a.search("apple", max_results=5)
        assert out[0]["id"] == "AAPL"
        assert out[0]["title"] == "Apple Inc."
        assert out[0]["fields"]["currency"] == "USD"
        assert mock_get.call_args.kwargs["params"]["function"] == "SEARCH"

    @patch("gossamer.research_providers.httpx.get")
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

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_no_data_surfaces_note(self, mock_get):
        mock_get.return_value = _resp({"notes": "Takeaway: no data for the requested symbol."})
        a = AlphaVantageAdapter(delay=0.0, api_key="K")
        out = a.fetch("NOTASYMBOL")
        assert len(out) == 1
        assert "no data" in out[0]["snippet"]

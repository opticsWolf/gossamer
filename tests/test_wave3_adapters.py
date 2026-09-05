"""Wave-3 adapters: verified replacements & Eurozone coverage.

Every mock payload below mirrors a response shape verified live during
research (see docs/PROVIDER_ALTERNATIVES_*.md) — these tests pin the parse
against the real wire format, not an assumed one. No network is touched.
"""

from unittest.mock import MagicMock, patch

from gossamer.research_providers import (
    BisAdapter,
    BundesbankAdapter,
    CoinGeckoAdapter,
    EurostatAdapter,
    FrankfurterAdapter,
    GovInfoAdapter,
    HudocAdapter,
    OldpAdapter,
)


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def _text_resp(text):
    r = MagicMock()
    r.text = text
    r.raise_for_status.return_value = None
    return r


class TestOldpAdapter:
    def test_metadata(self):
        a = OldpAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("oldp", "legal", False)

    @patch("gossamer.research_providers.httpx.get")
    def test_search_parses_cases(self, mock_get):
        mock_get.return_value = _resp(
            {
                "count": 1,
                "results": [
                    {
                        "id": 521973,
                        "slug": "vg-bremen-2026-08-26-2-k-134324",
                        "court": {"id": 367, "name": "Verwaltungsgericht Bremen"},
                        "file_number": "2 K 1343/24",
                        "date": "2026-08-26",
                        "ecli": "ECLI:DE:VGBRE:2026:0826.2K1343.24.00",
                        "decision_type": "Beschluss",
                        "snippets": ["Mietminderung wegen ..."],
                    }
                ],
            }
        )
        out = OldpAdapter(delay=0.0).search("Mietminderung", max_results=2)
        assert out[0]["id"] == "521973"
        assert "Bremen" in out[0]["title"]
        assert out[0]["fields"]["file_number"] == "2 K 1343/24"
        assert out[0]["url"].endswith("/case/vg-bremen-2026-08-26-2-k-134324")
        assert mock_get.call_args.args[0].endswith("/cases/search/")

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_case(self, mock_get):
        mock_get.return_value = _resp(
            {"id": 1, "slug": "x", "court": {"name": "BGH"},
             "file_number": "1 StR 1/24", "date": "2024-01-01"}
        )
        out = OldpAdapter(delay=0.0).fetch("1")
        assert out[0]["fields"]["court"] == "BGH"
        assert mock_get.call_args.args[0].endswith("/cases/1/")


class TestHudocAdapter:
    def test_metadata(self):
        a = HudocAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("hudoc", "legal", False)

    @patch("gossamer.research_providers.httpx.get")
    def test_search_parses_columns(self, mock_get):
        mock_get.return_value = _resp(
            {
                "resultcount": 2725,
                "results": [
                    {"columns": {
                        "itemid": "001-100038",
                        "docname": "CASE OF A. v. THE NETHERLANDS",
                        "appno": "4900/06",
                        "kpdate": "2010-01-01T00:00:00",
                        "ecli": "ECLI:CE:ECHR:2010:0101JUD00490006",
                    }}
                ],
            }
        )
        out = HudocAdapter(delay=0.0).search("privacy", max_results=2)
        assert out[0]["id"] == "001-100038"
        assert out[0]["fields"]["appno"] == "4900/06"
        assert out[0]["published"] == "2010-01-01"
        params = mock_get.call_args.kwargs["params"]
        assert "contentsitename:ECHR" in params["query"]
        assert "(privacy)" in params["query"]

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_by_itemid(self, mock_get):
        mock_get.return_value = _resp(
            {"results": [{"columns": {"itemid": "001-1", "docname": "X"}}]}
        )
        out = HudocAdapter(delay=0.0).fetch("001-1")
        assert out[0]["id"] == "001-1"
        assert "itemid=" in mock_get.call_args.kwargs["params"]["query"]


class TestGovInfoAdapter:
    def test_metadata(self):
        a = GovInfoAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("govinfo", "legal", False)

    @patch("gossamer.research_providers.httpx.post")
    def test_search_posts_historical(self, mock_post):
        mock_post.return_value = _resp(
            {
                "count": 1,
                "results": [
                    {
                        "title": "A Bill",
                        "packageId": "BILLS-119hr1",
                        "granuleId": "",
                        "dateIssued": "2025-01-03",
                        "collectionCode": "BILLS",
                        "download": {"txtLink": "https://api.govinfo.gov/x.txt"},
                    }
                ],
            }
        )
        out = GovInfoAdapter(delay=0.0).search("water", max_results=2)
        assert out[0]["id"] == "BILLS-119hr1"
        assert out[0]["fields"]["collection"] == "BILLS"
        body = mock_post.call_args.kwargs["json"]
        assert body["historical"] is True
        assert body["query"] == "water"


class TestFrankfurterAdapter:
    def test_metadata(self):
        a = FrankfurterAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("frankfurter", "financial", False)

    @patch("gossamer.research_providers.httpx.get")
    def test_pair_search(self, mock_get):
        # Live v2 shape: a list of single-quote objects.
        mock_get.return_value = _resp(
            [{"date": "2026-09-05", "base": "USD", "quote": "EUR", "rate": 0.86006}]
        )
        out = FrankfurterAdapter(delay=0.0).search("USD/EUR", max_results=2)
        assert out[0]["id"] == "USD/EUR"
        assert out[0]["fields"]["rate"] == 0.86006
        assert mock_get.call_args.kwargs["params"]["base"] == "USD"

    def test_bad_currency_raises(self):
        import pytest

        with pytest.raises(ValueError):
            FrankfurterAdapter(delay=0.0).search("not money", max_results=2)


class TestEurostatAdapter:
    def test_metadata(self):
        a = EurostatAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("eurostat", "financial", False)

    @patch("gossamer.research_providers.httpx.get")
    def test_unpacks_json_stat(self, mock_get):
        # Minimal JSON-stat mirroring the live nama_10_gdp response.
        mock_get.return_value = _resp(
            {
                "version": "2.0",
                "label": "GDP",
                "id": ["freq", "geo", "time"],
                "size": [1, 1, 2],
                "dimension": {
                    "freq": {"category": {"index": {"A": 0}, "label": {"A": "Annual"}}},
                    "geo": {"category": {"index": {"DE": 0}, "label": {"DE": "Germany"}}},
                    "time": {"category": {"index": {"2022": 0, "2023": 1},
                                           "label": {"2022": "2022", "2023": "2023"}}},
                },
                "value": {"0": 100.0, "1": 200.0},
            }
        )
        out = EurostatAdapter(delay=0.0).search(
            "nama_10_gdp?freq=A&geo=DE", max_results=5
        )
        assert len(out) == 2
        assert out[1]["fields"]["value"] == 200.0
        assert out[1]["fields"]["geo"] == "DE"
        assert "2023" in out[1]["title"]


class TestBundesbankAdapter:
    def test_metadata(self):
        a = BundesbankAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("bundesbank", "financial", False)

    @patch("gossamer.research_providers.httpx.get")
    def test_parses_generic_obs(self, mock_get):
        mock_get.return_value = _text_resp(
            '<message:GenericData xmlns:message="http://example.org/msg" '
            'xmlns:generic="http://example.org/data">'
            '<generic:Series><generic:Obs>'
            '<generic:ObsDimension value="2024-01-02"/>'
            '<generic:ObsValue value="1.0956"/>'
            "</generic:Obs></generic:Series></message:GenericData>"
        )
        out = BundesbankAdapter(delay=0.0).search(
            "BBEX3/D.USD.EUR.BB.AC.000?startPeriod=2024-01-02&endPeriod=2024-01-02",
            max_results=5,
        )
        assert out[0]["fields"]["value"] == "1.0956"
        assert out[0]["fields"]["date"] == "2024-01-02"
        assert mock_get.call_args.args[0].endswith("/data/BBEX3/D.USD.EUR.BB.AC.000")


class TestBisAdapter:
    def test_metadata(self):
        a = BisAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("bis", "financial", False)

    @patch("gossamer.research_providers.httpx.get")
    def test_parses_structure_specific(self, mock_get):
        mock_get.return_value = _text_resp(
            '<message:StructureSpecificData xmlns:message="http://example.org/m" '
            'xmlns:ns1="urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=BIS:WS_CBPOL(1.0)">'
            '<ns1:DataSet><ns1:Series FREQ="M" REF_AREA="XM">'
            '<ns1:Obs TIME_PERIOD="2024-01" OBS_VALUE="4.50"/>'
            "</ns1:Series></ns1:DataSet></message:StructureSpecificData>"
        )
        out = BisAdapter(delay=0.0).search("WS_CBPOL/M.XM.EUR", max_results=5)
        assert out[0]["fields"]["value"] == "4.50"
        assert out[0]["fields"]["date"] == "2024-01"


class TestCoinGeckoAdapter:
    def test_metadata(self):
        a = CoinGeckoAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("coingecko", "financial", False)

    @patch("gossamer.research_providers.httpx.get")
    def test_search_parses_coins(self, mock_get):
        mock_get.return_value = _resp(
            {"coins": [{"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC",
                        "market_cap_rank": 1}]}
        )
        out = CoinGeckoAdapter(delay=0.0).search("bitcoin", max_results=2)
        assert out[0]["id"] == "bitcoin"
        assert out[0]["fields"]["market_cap_rank"] == 1

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_parses_markets(self, mock_get):
        mock_get.return_value = _resp(
            [{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
              "current_price": 79603, "market_cap": 1598425806209,
              "price_change_percentage_24h": 1.5}]
        )
        out = CoinGeckoAdapter(delay=0.0).fetch("bitcoin")
        assert out[0]["fields"]["current_price_usd"] == 79603

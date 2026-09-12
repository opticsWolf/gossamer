"""Wave-4 patent adapters (EPO OPS, KIPRIS, PatentsView) + Lens aggregator
+ keyless Google Patents lookup.

EPO/KIPRIS/PatentsView/Lens are key-gated; Google Patents resolves
publication numbers without a key (its search page is robots-Disallowed,
so there is no search scraper — only detail lookup). These tests pin
request construction (URLs, auth placement, params) and parsing against
the offices' documented response shapes with mocked HTTP. Live paths are
covered by key-gated smoke tests, not the offline suite.
"""

from unittest.mock import MagicMock, patch

import pytest

import httpx

from gossamer.research_providers import (
    EpoOpsAdapter,
    GooglePatentsAdapter,
    KiprisAdapter,
    LensAdapter,
    PatentsViewAdapter,
)


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def _xml_resp(text):
    r = MagicMock()
    r.text = text
    r.raise_for_status.return_value = None
    return r


EPO_XML = """<ops:world-patent-data xmlns:ops="http://ops.epo.org">
  <ops:biblio-search><ops:search-result>
    <exchange-documents xmlns="http://www.epo.org/exchange">
      <exchange-document>
        <bibliographic-data>
          <publication-reference><document-id document-id-type="epodoc">
            <doc-number>EP1234567</doc-number><kind>A1</kind><date>20240101</date>
          </document-id></publication-reference>
          <invention-title lang="en">Quantum widget</invention-title>
          <parties><applicants><applicant><applicant-name>
            <name>ACME Corp</name>
          </applicant-name></applicant></applicants></parties>
        </bibliographic-data>
      </exchange-document>
    </exchange-documents>
  </ops:search-result></ops:biblio-search>
</ops:world-patent-data>"""


class TestEpoOpsAdapter:
    def test_metadata_requires_key(self):
        a = EpoOpsAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("epo", "patent", True)

    def test_search_without_keys_raises_actionable(self):
        with pytest.raises(RuntimeError, match="GOSSAMER_EPO_KEY"):
            EpoOpsAdapter(delay=0.0).search("ti=quantum", max_results=2)

    @patch("gossamer.research_providers.httpx.post")
    @patch("gossamer.research_providers.httpx.get")
    def test_search_oauth_then_cql(self, mock_get, mock_post):
        mock_post.return_value = _resp({"access_token": "TOK", "expires_in": 1200})
        mock_get.return_value = _xml_resp(EPO_XML)
        out = EpoOpsAdapter(delay=0.0, api_key="K", api_secret="S").search(
            "ti=quantum", max_results=2
        )
        # OAuth client-credentials grant.
        assert mock_post.call_args.args[0].endswith("/auth/accesstoken")
        assert mock_post.call_args.kwargs["data"]["grant_type"] == "client_credentials"
        # CQL search with bearer token + Range.
        assert mock_get.call_args.args[0].endswith("/published-data/search")
        assert mock_get.call_args.kwargs["params"]["q"] == "ti=quantum"
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer TOK"
        assert out[0]["id"] == "EP1234567A1"
        assert out[0]["title"] == "[en] Quantum widget"
        assert "ACME Corp" in out[0]["snippet"]

    @patch("gossamer.research_providers.httpx.post")
    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_by_epodoc(self, mock_get, mock_post):
        mock_post.return_value = _resp({"access_token": "TOK", "expires_in": 1200})
        mock_get.return_value = _xml_resp(EPO_XML)
        out = EpoOpsAdapter(delay=0.0, api_key="K", api_secret="S").fetch("EP1234567A1")
        assert out[0]["id"] == "EP1234567A1"
        assert mock_get.call_args.args[0].endswith(
            "/publication/epodoc/EP1234567A1"
        )


KIPRIS_XML = """<response><body><items><item>
<applicationNumber>1020240000001</applicationNumber>
<inventionTitle>Quantum device</inventionTitle>
<applicantName>ACME</applicantName>
<applicationStatus>pending</applicationStatus>
</item></items></body></response>"""


class TestKiprisAdapter:
    def test_metadata_requires_key(self):
        a = KiprisAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("kipris", "patent", True)

    def test_search_without_key_raises_actionable(self):
        with pytest.raises(RuntimeError, match="GOSSAMER_KIPRIS_KEY"):
            KiprisAdapter(delay=0.0).search("quantum", max_results=2)

    @patch("gossamer.research_providers.httpx.get")
    def test_search_word_lookup(self, mock_get):
        mock_get.return_value = _xml_resp(KIPRIS_XML)
        out = KiprisAdapter(delay=0.0, api_key="K").search("quantum", max_results=2)
        assert out[0]["id"] == "1020240000001"
        assert out[0]["title"] == "Quantum device"
        url = mock_get.call_args.args[0]
        assert url.endswith("/patUtliInfoSearchService/getWordSearch")
        assert mock_get.call_args.kwargs["params"]["serviceKey"] == "K"
        assert mock_get.call_args.kwargs["params"]["word"] == "quantum"


class TestPatentsViewAdapter:
    def test_metadata_requires_key(self):
        a = PatentsViewAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("patentsview", "patent", True)

    def test_search_without_key_raises_actionable(self):
        with pytest.raises(RuntimeError, match="GOSSAMER_PATENTSVIEW_API_KEY"):
            PatentsViewAdapter(delay=0.0).search("quantum", max_results=2)

    @patch("gossamer.research_providers.httpx.get")
    def test_search_sends_key_header_and_json_query(self, mock_get):
        import json as _json

        mock_get.return_value = _resp(
            {
                "error": False,
                "count": 1,
                "total_hits": 42,
                "patents": [
                    {
                        "patent_number": "12345678",
                        "patent_title": "Quantum widget",
                        "patent_date": "2024-01-02",
                        "assignee_organization": "ACME",
                    }
                ],
            }
        )
        out = PatentsViewAdapter(delay=0.0, api_key="K").search("quantum", max_results=2)
        assert mock_get.call_args.args[0].endswith("/api/v1/patents/")
        assert mock_get.call_args.kwargs["headers"]["X-Api-Key"] == "K"
        params = mock_get.call_args.kwargs["params"]
        assert _json.loads(params["q"]) == {"_text_all": {"patent_title": "quantum"}}
        assert out[0]["id"] == "12345678"
        assert out[0]["fields"]["assignee"] == "ACME"

    @patch("gossamer.research_providers.httpx.get")
    def test_api_error_raises(self, mock_get):
        mock_get.return_value = _resp({"error": "bad query"})
        with pytest.raises(RuntimeError, match="bad query"):
            PatentsViewAdapter(delay=0.0, api_key="K").search("quantum")

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_by_number(self, mock_get):
        mock_get.return_value = _resp(
            {"patent_number": "12345678", "patent_title": "Quantum widget"}
        )
        out = PatentsViewAdapter(delay=0.0, api_key="K").fetch("12345678")
        assert out[0]["id"] == "12345678"
        assert mock_get.call_args.args[0].endswith("/api/v1/patents/12345678/")


LENS_SEARCH = {
    "data": [
        {
            "lens_id": "186-488-232-022-055",
            "jurisdiction": "US",
            "doc_number": "20130227762",
            "kind": "A1",
            "date_published": "2013-02-28",
            "doc_key": "US_20130227762_A1_20130228",
            "biblio": {
                "invention_title": [{"text": "Quantum widget", "lang": "EN"}],
                "parties": {
                    "applicants": [{"extracted_name": {"value": "ACME Corp"}}],
                    "inventors": [{"extracted_name": {"value": "Smith J"}}],
                },
            },
            "abstract": [{"text": "A quantum widget for testing.", "lang": "EN"}],
            "legal_status": {"patent_status": "ACTIVE"},
        }
    ],
    "results": 1,
    "total": 1,
}

LENS_SINGLE = LENS_SEARCH["data"][0]


class TestLensAdapter:
    def test_metadata_requires_key(self):
        a = LensAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("lens", "patent", True)

    def test_search_without_key_raises_actionable(self):
        with pytest.raises(RuntimeError, match="GOSSAMER_LENS_API_KEY"):
            LensAdapter(delay=0.0).search("quantum", max_results=2)

    def test_search_empty_query_raises(self):
        with pytest.raises(ValueError, match="text query"):
            LensAdapter(delay=0.0, api_key="K").search("  ", max_results=2)

    def test_build_text_query_shapes(self):
        assert LensAdapter._build_text_query("186-488-232-022-055") == {
            "terms": {"lens_id": ["186-488-232-022-055"]}
        }
        assert LensAdapter._build_text_query("US7654321") == {
            "terms": {"ids": ["US7654321"]}
        }
        q = LensAdapter._build_text_query("quantum widget")
        should = q["bool"]["should"]
        got = {tuple(sorted(m["match"].items())) for m in should}
        assert got == {
            (("title", "quantum widget"),),
            (("abstract", "quantum widget"),),
            (("claim", "quantum widget"),),
        }

    @patch("gossamer.research_providers.httpx.post")
    def test_search_posts_bearer_and_bool_query(self, mock_post):
        mock_post.return_value = _resp(LENS_SEARCH)
        out = LensAdapter(delay=0.0, api_key="K").search("quantum", max_results=2)
        assert mock_post.call_args.args[0].endswith("/patent/search")
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer K"
        body = mock_post.call_args.kwargs["json"]
        assert body["size"] == 2
        assert "title" in str(body["query"])
        assert out[0]["id"] == "186-488-232-022-055"
        assert out[0]["title"] == "Quantum widget"
        assert out[0]["url"] == "https://www.lens.org/lens/patent/186-488-232-022-055"
        assert out[0]["published"] == "2013-02-28"
        assert "ACME Corp" in out[0]["snippet"]
        assert out[0]["fields"]["jurisdiction"] == "US"
        assert out[0]["fields"]["legal_status"] == "ACTIVE"
        assert "raw" in out[0]

    @patch("gossamer.research_providers.httpx.post")
    def test_search_lens_id_uses_terms(self, mock_post):
        mock_post.return_value = _resp(LENS_SEARCH)
        LensAdapter(delay=0.0, api_key="K").search("186-488-232-022-055")
        body = mock_post.call_args.kwargs["json"]
        assert body["query"] == {"terms": {"lens_id": ["186-488-232-022-055"]}}

    @patch("gossamer.research_providers.httpx.post")
    def test_search_dict_passthrough(self, mock_post):
        mock_post.return_value = _resp(LENS_SEARCH)
        q = {"bool": {"must": [{"term": {"jurisdiction": "CN"}}]}}
        LensAdapter(delay=0.0, api_key="K").search({"query": q}, max_results=3)
        body = mock_post.call_args.kwargs["json"]
        assert body["query"] == q
        assert body["size"] == 3

    @patch("gossamer.research_providers.httpx.post")
    def test_search_bare_dict_becomes_query(self, mock_post):
        mock_post.return_value = _resp(LENS_SEARCH)
        q = {"term": {"jurisdiction": "DE"}}
        LensAdapter(delay=0.0, api_key="K").search(q)
        body = mock_post.call_args.kwargs["json"]
        assert body["query"] == q

    @patch("gossamer.research_providers.httpx.post")
    def test_api_error_envelope_raises(self, mock_post):
        mock_post.return_value = _resp({"error": "bad key"})
        with pytest.raises(RuntimeError, match="Lens error"):
            LensAdapter(delay=0.0, api_key="K").search("quantum")
        mock_post.return_value = _resp({"message": "Unauthorized", "status": 401})
        with pytest.raises(RuntimeError, match="Unauthorized"):
            LensAdapter(delay=0.0, api_key="K").search("quantum")

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_by_lens_id(self, mock_get):
        mock_get.return_value = _resp(LENS_SINGLE)
        out = LensAdapter(delay=0.0, api_key="K").fetch("186-488-232-022-055")
        assert out[0]["id"] == "186-488-232-022-055"
        assert out[0]["title"] == "Quantum widget"
        assert mock_get.call_args.args[0].endswith("/patent/186-488-232-022-055")

    @patch("gossamer.research_providers.httpx.post")
    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_pub_number_falls_back_to_ids_search(self, mock_get, mock_post):
        err = RuntimeError("not found")
        err.response = MagicMock(status_code=404)
        mock_get.side_effect = err
        mock_post.return_value = _resp(LENS_SEARCH)
        out = LensAdapter(delay=0.0, api_key="K").fetch("US20130227762A1")
        assert out[0]["id"] == "186-488-232-022-055"
        body = mock_post.call_args.kwargs["json"]
        assert body["query"] == {"terms": {"ids": ["US20130227762A1"]}}

    def test_fetch_empty_id_raises(self):
        with pytest.raises(ValueError, match="Lens ID"):
            LensAdapter(delay=0.0, api_key="K").fetch("  ")


GP_HTML = """<html><head>
<link rel="canonical" href="https://patents.google.com/patent/US5000575A/en">
<meta name="DC.title" content="Method of fabricating gradient index optical films">
<meta name="DC.date" content="1989-09-06" scheme="dateSubmitted">
<meta name="DC.date" content="1991-03-19" scheme="issue">
<meta name="citation_patent_application_number" content="US:07/403,649">
<meta name="citation_pdf_url" content="https://patentimages.storage.googleapis.com/f4/95/ec/bea32afc772968/US5000575.pdf">
<meta name="citation_patent_number" content="US:5000575">
<meta name="DC.contributor" content="William H. Southwell" scheme="inventor">
<meta name="DC.contributor" content="Randolph L. Hall" scheme="inventor">
<meta name="DC.contributor" content="Rockwell International Corp" scheme="assignee">
<meta name="DC.relation" content="US:3892490" scheme="references">
<meta name="DC.relation" content="US:4555767" scheme="references">
</head><body>
<h2>Info</h2><dl><dt>Publication number</dt>
<dd itemprop="publicationNumber">US5000575A</dd>
<meta itemprop="kindCode" content="A">
<section itemprop="abstract" itemscope><h2>Abstract</h2>
<div class="abstract">A method is provided for monitoring &amp; controlling deposition.</div>
<section itemprop="claims" itemscope><h2>Claims (<span itemprop="count">2</span>)</h2>
</body></html>"""


def _html_resp(html):
    r = MagicMock()
    r.text = html
    r.raise_for_status.return_value = None
    return r


def _http_404(url):
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        "not found", request=req, response=httpx.Response(404, request=req)
    )


class TestGooglePatentsAdapter:
    def test_metadata_keyless(self):
        a = GooglePatentsAdapter(delay=0.0)
        assert (a.name, a.domain, a.requires_key) == ("google-patents", "patent", False)

    def test_normalize_number(self):
        assert GooglePatentsAdapter._normalize_number("US 5,000,575 A") == "US5000575A"
        assert GooglePatentsAdapter._normalize_number("wo2024/155532a1") == "WO2024155532A1"
        assert GooglePatentsAdapter._normalize_number("  ") == ""

    def test_search_empty_raises_actionable(self):
        with pytest.raises(ValueError, match="publication number"):
            GooglePatentsAdapter(delay=0.0).search("  ")

    def test_fetch_empty_raises(self):
        with pytest.raises(ValueError, match="publication number"):
            GooglePatentsAdapter(delay=0.0).fetch(",,,")

    @patch("gossamer.research_providers.httpx.get")
    def test_search_resolves_number_and_parses_biblio(self, mock_get):
        mock_get.return_value = _html_resp(GP_HTML)
        out = GooglePatentsAdapter(delay=0.0).search("US 5,000,575 A")
        assert mock_get.call_args.args[0].endswith("/patent/US5000575A/en")
        assert len(out) == 1
        rec = out[0]
        assert rec["source"] == "google-patents"
        assert rec["id"] == "US5000575A"
        assert rec["title"] == "Method of fabricating gradient index optical films"
        assert rec["url"] == "https://patents.google.com/patent/US5000575A/en"
        assert rec["published"] == "1991-03-19"
        assert "Southwell" in rec["snippet"]
        assert rec["fields"]["kind"] == "A"
        assert rec["fields"]["application_number"] == "US:07/403,649"
        assert rec["fields"]["filing_date"] == "1989-09-06"
        assert rec["fields"]["inventors"] == "William H. Southwell, Randolph L. Hall"
        assert rec["fields"]["assignee"] == "Rockwell International Corp"
        assert rec["fields"]["pdf_url"].endswith("US5000575.pdf")
        assert rec["fields"]["claims_count"] == 2
        assert rec["fields"]["refs_count"] == 2
        assert rec["fields"]["abstract"].startswith("A method is provided for monitoring & controlling")
        assert "raw" in rec

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_falls_back_to_bare_path(self, mock_get):
        mock_get.side_effect = [
            _http_404("https://patents.google.com/patent/US5000575A/en"),
            _html_resp(GP_HTML),
        ]
        out = GooglePatentsAdapter(delay=0.0).fetch("US5000575A")
        assert out[0]["id"] == "US5000575A"
        assert mock_get.call_args.args[0].endswith("/patent/US5000575A")

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_unknown_number_returns_empty(self, mock_get):
        mock_get.side_effect = lambda url, **kw: (_ for _ in ()).throw(_http_404(url))
        assert GooglePatentsAdapter(delay=0.0).fetch("XX0000000Z") == []

    @patch("gossamer.research_providers.httpx.get")
    def test_fetch_soft_404_returns_empty(self, mock_get):
        mock_get.return_value = _html_resp("<html><body>nothing here</body></html>")
        assert GooglePatentsAdapter(delay=0.0).fetch("US5000575A") == []

"""Wave-4 patent adapters (EPO OPS, KIPRIS, PatentsView) + Lens aggregator.

All four are key-gated (there is no keyless patent API left), so these tests
pin request construction (URLs, auth placement, params) and parsing against
the offices' documented response shapes with mocked HTTP. Live paths are
covered by key-gated smoke tests, not the offline suite.
"""

from unittest.mock import MagicMock, patch

import pytest

from gossamer.research_providers import (
    EpoOpsAdapter,
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

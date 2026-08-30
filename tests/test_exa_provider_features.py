"""Exa provider feature coverage (httpx REST implementation).

Exa is implemented directly against `POST https://api.exa.ai/v1/search`
with ``httpx`` -- no third-party SDK. These tests mock the single
``httpx.post`` call and cover:

- the base interface (toolbox path): ``{title, url, snippet}`` + richer
  fields when the API returns them;
- the SDK-like ``search(query, type=..., contents=..., ...)`` surface and
  the snake_case -> camelCase REST translation;
- constructor defaults merged with per-call overrides;
- politeness / quota / retry behaviour inherited from the base class;
- the missing-key guard.
"""

from unittest.mock import MagicMock, patch

import pytest

from stitch_web_researcher.search_providers import (
    ExaProvider,
    QuotaExhaustedError,
)

POST_TARGET = "stitch_web_researcher.search_providers.httpx.post"

RESULT_1 = {
    "title": "T1",
    "url": "https://example.com/1",
    "highlights": ["hl1"],
    "publishedDate": "2024-01-01",
    "author": "A. Author",
    "linkingDomains": ["example.com"],
    "text": "full extracted text",
}
RESULT_2 = {"title": "T2", "url": "https://example.com/2", "highlights": ["hl2"]}
OK_BODY = {"results": [RESULT_1, RESULT_2]}


def _mock_response(body=OK_BODY, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = body
    resp.raise_for_status.return_value = None
    return resp


def _posted_json(call):
    return call.kwargs["json"]


# ─────────────────────────────────────────────────────────────────
# Base interface (toolbox path)
# ─────────────────────────────────────────────────────────────────
class TestBaseInterface:
    def test_default_request_body_uses_highlights(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            ExaProvider(api_key="k")._search_impl("q", 2)
        assert _posted_json(post.call_args) == {
            "query": "q",
            "numResults": 2,
            "type": "auto",
            "contents": {"highlights": True},
        }

    def test_base_result_shape_has_title_url_snippet(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            out = ExaProvider(api_key="k")._search_impl("q", 5)
        assert out[0]["title"] == "T1"
        assert out[0]["url"] == "https://example.com/1"
        assert out[0]["snippet"] == ["hl1"]  # highlights -> snippet
        # dedup key used by the toolbox
        assert out[1]["url"] == "https://example.com/2"

    def test_base_result_carries_rich_fields_when_present(self):
        with patch(POST_TARGET, return_value=_mock_response()):
            out = ExaProvider(api_key="k")._search_impl("q", 5)
        assert out[0]["text"] == "full extracted text"
        assert out[0]["publishedDate"] == "2024-01-01"
        assert out[0]["author"] == "A. Author"
        assert out[0]["linkingDomains"] == ["example.com"]

    def test_snippet_is_empty_when_no_highlights(self):
        body = {"results": [{"title": "T", "url": "https://e.com/1", "text": "x"}]}
        with patch(POST_TARGET, return_value=_mock_response(body)):
            out = ExaProvider(api_key="k")._search_impl("q", 1)
        assert out[0]["snippet"] == ""
        assert out[0]["text"] == "x"


# ─────────────────────────────────────────────────────────────────
# SDK-like rich search surface
# ─────────────────────────────────────────────────────────────────
class TestRichSearch:
    def test_search_types_supported(self):
        for t in ExaProvider.SEARCH_TYPES:
            with patch(POST_TARGET, return_value=_mock_response()) as post:
                ExaProvider(api_key="k").search("q", type=t)
            assert _posted_json(post.call_args)["type"] == t

    def test_snake_case_params_translate_to_camel_case(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            ExaProvider(api_key="k").search(
                "q",
                num_results=7,
                include_domains=["arxiv.org", "ar5iv.org"],
                exclude_domains=["spam.com"],
                start_published_date="2024-01-01",
                end_published_date="2024-12-31",
                system_prompt="be terse",
                output_schema={"type": "text", "description": "sum"},
                additional_queries=["a", "b"],
                text_filters={"authorName": "Jane"},
                result_filters={"resultCount": 5},
            )
        body = _posted_json(post.call_args)
        assert body["numResults"] == 7
        assert body["includeDomains"] == ["arxiv.org", "ar5iv.org"]
        assert body["excludeDomains"] == ["spam.com"]
        assert body["startPublishedDate"] == "2024-01-01"
        assert body["endPublishedDate"] == "2024-12-31"
        assert body["systemPrompt"] == "be terse"
        assert body["outputSchema"] == {"type": "text", "description": "sum"}
        assert body["additionalQueries"] == ["a", "b"]
        assert body["textFilters"] == {"authorName": "Jane"}
        assert body["resultFilters"] == {"resultCount": 5}

    def test_category_and_moderation_and_contents(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            ExaProvider(api_key="k").search(
                "q",
                category="publication",
                moderation=True,
                contents={"summary": {"query": "key points"}},
            )
        body = _posted_json(post.call_args)
        assert body["category"] == "publication"
        assert body["moderation"] is True
        assert body["contents"] == {"summary": {"query": "key points"}}

    def test_contents_text_sets_no_snippet(self):
        body = {"results": [{"title": "T", "url": "https://e.com/1", "text": "x"}]}
        with patch(POST_TARGET, return_value=_mock_response(body)) as post:
            out = ExaProvider(api_key="k").search("q", contents={"text": True})
        assert _posted_json(post.call_args)["contents"] == {"text": True}
        assert out[0]["text"] == "x" and out[0]["snippet"] == ""

    def test_constructor_defaults_mixed_with_per_call_override(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            prov = ExaProvider(
                api_key="k", search_type="semantic", include_domains=["x.com"]
            )
            prov.search("q", max_results=1)  # per-call override of numResults
        body = _posted_json(post.call_args)
        assert body["type"] == "semantic"
        assert body["includeDomains"] == ["x.com"]
        assert body["numResults"] == 1

    def test_per_call_overrides_constructor_contents(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            prov = ExaProvider(api_key="k", contents={"highlights": True})
            prov.search("q", contents={"text": True})
        assert _posted_json(post.call_args)["contents"] == {"text": True}

    def test_num_results_overrides_max_results(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            ExaProvider(api_key="k").search("q", max_results=5, num_results=9)
        assert _posted_json(post.call_args)["numResults"] == 9


# ─────────────────────────────────────────────────────────────────
# Auth / error handling
# ─────────────────────────────────────────────────────────────────
class TestAuthAndErrors:
    def test_key_only_in_bearer_header_never_in_body(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            ExaProvider(api_key="SECRET").search("q")
        headers = post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer SECRET"
        assert "SECRET" not in str(post.call_args.kwargs["json"])

    def test_missing_key_raises_runtime_error(self):
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            with pytest.raises(RuntimeError, match="EXA_API_KEY"):
                ExaProvider(api_key="")._search_impl("q")
        post.assert_not_called()

    def test_http_error_propagates(self):
        resp = _mock_response(status=429, body={"error": "rate limited"})
        resp.raise_for_status.side_effect = Exception("429")
        with patch(POST_TARGET, return_value=resp):
            with pytest.raises(Exception, match="429"):
                ExaProvider(api_key="k")._search_impl("q")

    def test_empty_results_yields_empty_list(self):
        with patch(POST_TARGET, return_value=_mock_response({"results": []})):
            out = ExaProvider(api_key="k")._search_impl("q", 5)
        assert out == []


# ─────────────────────────────────────────────────────────────────
# Politeness / quota / retry (inherited from base)
# ─────────────────────────────────────────────────────────────────
class TestQuotaAndRetry:
    def test_quota_exhausted_is_not_retried(self):
        from datetime import datetime, timezone

        prov = ExaProvider(api_key="k")
        prov._last_search = 0.0
        prov._quota_used = 1000  # _EXA_RATE_LIMIT quota is 1000/month
        prov._quota_period = datetime.now(timezone.utc).strftime(
            "%Y-%m"
        )  # current month -> no reset
        with patch(POST_TARGET, return_value=_mock_response()) as post:
            with pytest.raises(QuotaExhaustedError):
                prov.search("q")  # exhausted -> immediate, not retried 3x
            post.assert_not_called()

    def test_transient_failure_is_retried_then_succeeds(self):
        calls = {"n": 0}

        def flaky_post(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                resp = MagicMock()
                resp.raise_for_status.side_effect = Exception("boom")
                return resp
            return _mock_response()

        with patch(POST_TARGET, side_effect=flaky_post):
            # search() is the @retry-wrapped entry point; _search_impl is not.
            out = ExaProvider(api_key="k", delay=0.0).search("q")
        assert calls["n"] == 2
        assert out[0]["url"] == "https://example.com/1"

    def test_default_rate_limit_unchanged(self):
        from stitch_web_researcher.search_providers import _EXA_RATE_LIMIT

        assert ExaProvider(api_key="k").rate_limit == _EXA_RATE_LIMIT

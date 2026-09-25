"""DOI normalization and OpenAlex OA-location resolution tests."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gossamer.open_access import OpenAccessLocator, normalize_doi
from gossamer.research_providers import OpenAlexAdapter
from gossamer.search_providers import ProviderRateLimitError


@pytest.mark.parametrize(
    "value,expected",
    [
        ("10.1234/Example.DOI", "10.1234/example.doi"),
        ("doi:10.1234/Example.DOI", "10.1234/example.doi"),
        ("https://doi.org/10.1234/Example.DOI", "10.1234/example.doi"),
        ("http://dx.doi.org/10.1234/Example.DOI", "10.1234/example.doi"),
    ],
)
def test_normalize_doi_forms(value, expected):
    assert normalize_doi(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "not-a-doi", "10.123/short-prefix", "https://example.com/10.1234/x",
     "https://doi.org/10.1234/x?tracking=1"],
)
def test_normalize_doi_rejects_invalid_inputs(value):
    with pytest.raises(ValueError):
        normalize_doi(value)


def test_openalex_fetch_by_doi_uses_filter_and_returns_raw_record():
    work = {
        "id": "https://openalex.org/W123",
        "title": "A paper",
        "doi": "https://doi.org/10.1234/example",
        "publication_date": "2024-01-01",
    }
    response = MagicMock()
    response.json.return_value = {"results": [work]}
    response.raise_for_status.return_value = None

    with patch("gossamer.research_providers.httpx.get", return_value=response) as get:
        records = OpenAlexAdapter(
            delay=0, email="reader@example.org", api_key="test-key",
        ).fetch_by_doi("10.1234/example")

    request = get.call_args
    assert request.args[0] == "https://api.openalex.org/works"
    assert request.kwargs["params"]["filter"] == "doi:https://doi.org/10.1234/example"
    assert request.kwargs["params"]["per_page"] == 1
    assert request.kwargs["params"]["mailto"] == "reader@example.org"
    assert request.kwargs["params"]["api_key"] == "test-key"
    assert records[0]["id"]
    assert json.loads(records[0]["raw"]) == work


def _work_record(*, is_oa=True, best=None, locations=None):
    work = {
        "id": "https://openalex.org/W123",
        "title": "Optical thin-film design",
        "doi": "https://doi.org/10.1234/example",
        "publication_date": "2024-01-01",
        "url": "https://openalex.org/W123",
        "open_access": {"is_oa": is_oa, "oa_status": "gold" if is_oa else "closed"},
        "best_oa_location": best,
        "locations": locations or [],
    }
    return {
        "id": work["id"],
        "title": work["title"],
        "doi": work["doi"],
        "published": work["publication_date"],
        "url": work["url"],
        "raw": json.dumps(work),
    }


def test_locator_prefers_best_oa_location_and_preserves_sources():
    best = {
        "pdf_url": "https://publisher.example/paper.pdf",
        "landing_page_url": "https://publisher.example/paper",
        "is_oa": True,
        "license": "cc-by",
        "version": "publishedVersion",
        "source": {"display_name": "Journal of Coatings", "id": "S1"},
    }
    repository = {
        "pdf_url": "https://repository.example/paper.pdf",
        "landing_page_url": "https://repository.example/record",
        "is_oa": True,
        "license": "cc-by-nc",
        "version": "acceptedVersion",
        "source": {"display_name": "University Repository", "id": "S2"},
    }
    work = _work_record(is_oa=True, best=best, locations=[best, repository])

    locator = OpenAccessLocator(adapter_factory=lambda: _FakeAdapter([work]))
    result = locator.locate("doi:10.1234/example")

    assert result["status"] == "open_access"
    assert result["doi"] == "10.1234/example"
    assert result["work"]["title"] == "Optical thin-film design"
    assert [item["url"] for item in result["candidates"]] == [
        "https://publisher.example/paper.pdf",
        "https://repository.example/paper.pdf",
    ]
    assert result["candidates"][0]["is_best"] is True
    assert result["candidates"][0]["license"] == "cc-by"
    assert result["candidates"][1]["source"] == "University Repository"


def test_locator_returns_landing_page_when_no_pdf_url():
    location = {
        "pdf_url": None,
        "landing_page_url": "https://repository.example/record",
        "is_oa": True,
        "source": {"display_name": "Repository"},
    }
    result = OpenAccessLocator(
        adapter_factory=lambda: _FakeAdapter([
            _work_record(is_oa=True, best=location),
        ])
    ).locate("10.1234/example")

    assert result["status"] == "open_access"
    assert result["candidates"][0]["kind"] == "landing_page"


def test_locator_distinguishes_not_found_closed_and_open_without_location():
    locator_empty = OpenAccessLocator(adapter_factory=lambda: _FakeAdapter([]))
    assert locator_empty.locate("10.1234/example")["status"] == "not_found"

    closed = _work_record(is_oa=False)
    result_closed = OpenAccessLocator(
        adapter_factory=lambda: _FakeAdapter([closed]),
    ).locate("10.1234/example")
    assert result_closed["status"] == "closed_access"
    assert result_closed["candidates"] == []

    no_location = _work_record(is_oa=True)
    result_no_location = OpenAccessLocator(
        adapter_factory=lambda: _FakeAdapter([no_location]),
    ).locate("10.1234/example")
    assert result_no_location["status"] == "open_access_no_location"


def test_locator_classifies_provider_and_rate_limit_errors():
    class FailingAdapter:
        def fetch_by_doi(self, _doi):
            raise RuntimeError("upstream down")

    class RateLimitedAdapter:
        def fetch_by_doi(self, _doi):
            raise ProviderRateLimitError("openalex", 429, retry_after=10)

    failed = OpenAccessLocator(adapter_factory=FailingAdapter).locate("10.1234/example")
    limited = OpenAccessLocator(adapter_factory=RateLimitedAdapter).locate("10.1234/example")
    assert failed["error"]["code"] == "provider_error"
    assert limited["error"]["code"] == "rate_limited"


def test_locator_rejects_malformed_provider_raw_record():
    work = {"id": "W1", "raw": "not json"}
    result = OpenAccessLocator(
        adapter_factory=lambda: _FakeAdapter([work]),
    ).locate("10.1234/example")
    assert result["error"]["code"] == "invalid_provider_record"


def test_locator_skips_unpaywall_without_email():
    calls = []

    def fetcher(doi, email):
        calls.append((doi, email))
        return {"is_oa": True}

    closed = _work_record(is_oa=False)
    result = OpenAccessLocator(
        adapter_factory=lambda: _FakeAdapter([closed]),
        unpaywall_fetcher=fetcher,
    ).locate("10.1234/example")

    assert result["status"] == "closed_access"
    assert calls == []


def test_locator_uses_unpaywall_for_closed_openalex_record():
    closed = _work_record(is_oa=False)
    payload = {
        "doi": "10.1234/example",
        "title": "Fallback paper",
        "is_oa": True,
        "oa_status": "gold",
        "best_oa_location": {
            "url_for_pdf": "https://repo.example/fallback.pdf",
            "url_for_landing_page": "https://repo.example/record",
            "license": "cc-by",
            "version": "publishedVersion",
            "host_type": "repository",
        },
        "oa_locations": [],
    }

    result = OpenAccessLocator(
        adapter_factory=lambda: _FakeAdapter([closed]),
        unpaywall_email="reader@example.org",
        unpaywall_fetcher=lambda _doi, _email: payload,
    ).locate("10.1234/example")

    assert result["status"] == "open_access"
    assert result["provider"] == "unpaywall"
    assert result["sources"] == ["openalex", "unpaywall"]
    assert result["candidates"][0]["url"] == "https://repo.example/fallback.pdf"
    assert result["candidates"][0]["kind"] == "pdf"


def test_locator_falls_back_when_openalex_fails():
    class FailingAdapter:
        def fetch_by_doi(self, _doi):
            raise RuntimeError("upstream down")

    payload = {
        "doi": "10.1234/example",
        "title": "Rescued paper",
        "is_oa": True,
        "best_oa_location": {"url": "https://repo.example/record", "host_type": "repository"},
        "oa_locations": [],
    }

    result = OpenAccessLocator(
        adapter_factory=FailingAdapter,
        unpaywall_email="reader@example.org",
        unpaywall_fetcher=lambda _doi, _email: payload,
    ).locate("10.1234/example")

    assert result["status"] == "open_access"
    assert result["provider"] == "unpaywall"
    assert result["provider_errors"][0]["provider"] == "openalex"


class _FakeAdapter:
    def __init__(self, records):
        self.records = records

    def fetch_by_doi(self, doi):
        assert doi == "10.1234/example"
        return self.records

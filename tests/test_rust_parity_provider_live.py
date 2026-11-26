import pytest

from gossamer.research_providers import (
    CrossrefAdapter,
    GitHubAdapter,
    NASAAdapter,
    NvdAdapter,
    OpenAlexAdapter,
    SemanticScholarAdapter,
)
from gossamer.search_providers import _service_provider_request_plan


OPENALEX_POLICY = {
    "headers": [
        {
            "key": "Contact-Agent",
            "template": "{ua} (mailto:{email})",
            "when": "email",
        },
        {
            "key": "User-Agent",
            "template": "{ua} (mailto:{email})",
            "when": "email",
        },
        {"key": "User-Agent", "template": "{ua}"},
    ],
    "params": [
        {"key": "mailto", "from": "email", "when": "email"},
        {"key": "api_key", "from": "api_key", "when": "api_key"},
    ],
}


def test_request_plan_empty_policy():
    out = _service_provider_request_plan(
        "https://example.test", {"q": "x"}, {"Accept": "application/json"}, {}, {}
    )
    assert out == {
        "url": "https://example.test",
        "params": {"q": "x"},
        "headers": {"Accept": "application/json"},
    }


def test_request_plan_openalex_policy():
    out = _service_provider_request_plan(
        "https://api.openalex.org/works",
        {"filter": "x"},
        {"Accept": "application/json"},
        OPENALEX_POLICY,
        {"ua": "gossamer/1", "email": "dev@example.test", "api_key": ""},
    )
    assert out["headers"] == {
        "Accept": "application/json",
        "Contact-Agent": "gossamer/1 (mailto:dev@example.test)",
        "User-Agent": "gossamer/1 (mailto:dev@example.test)",
    }
    assert out["params"] == {"filter": "x", "mailto": "dev@example.test"}


def test_request_plan_overwrite_and_setdefault():
    out = _service_provider_request_plan(
        "https://example.test",
        {"q": "caller"},
        {"User-Agent": "caller"},
        {
            "headers": [
                {"key": "User-Agent", "value": "rust", "mode": "overwrite"}
            ],
            "params": [
                {"key": "q", "value": "default", "mode": "setdefault"}
            ],
        },
        {},
    )
    assert out["headers"]["User-Agent"] == "rust"
    assert out["params"]["q"] == "caller"


def test_request_plan_rejects_non_object():
    with pytest.raises(TypeError, match="params must be an object, got list"):
        _service_provider_request_plan("https://example.test", [], {}, {}, {})


def test_openalex_inject_auth_uses_policy():
    adapter = OpenAlexAdapter(
        email="dev@example.test", api_key="", delay=0.0, fetch_delay=0.0
    )
    url, params, headers = adapter.inject_auth(
        "https://api.openalex.org/works", {"q": "x"}, {"Accept": "application/json"}
    )
    assert url == "https://api.openalex.org/works"
    assert params == {"q": "x", "mailto": "dev@example.test"}
    assert headers["User-Agent"].startswith("gossamer/")
    assert headers["Contact-Agent"].endswith("(mailto:dev@example.test)")


def test_semanticscholar_inject_auth_uses_policy():
    adapter = SemanticScholarAdapter(api_key="secret", delay=0.0)
    _, params, headers = adapter.inject_auth("https://x", {"q": "x"}, {})
    assert params == {"q": "x"}
    assert headers == {"x-api-key": "secret"}


def test_crossref_inject_auth_uses_policy():
    adapter = CrossrefAdapter(email="me@example.org", delay=0.0)
    _, params, headers = adapter.inject_auth("https://x", {"query": "x"}, {})
    assert params == {"query": "x"}
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"].endswith("?email=me@example.org")
    assert headers == {"User-Agent": headers["User-Agent"], "Contact-Agent": headers["Contact-Agent"], "Accept": "application/json"}


def test_github_inject_auth_uses_policy():
    adapter = GitHubAdapter(api_key="secret", delay=0.0)
    _, params, headers = adapter.inject_auth("https://x", {"q": "x"}, {})
    assert params == {"q": "x"}
    assert headers == {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer secret",
    }


def test_nvd_inject_auth_uses_policy():
    adapter = NvdAdapter(api_key="secret", delay=0.0)
    _, params, headers = adapter.inject_auth(
        "https://x", {"apiKey": "old", "q": "x"}, {}
    )
    assert params == {"apiKey": "secret", "q": "x"}
    assert headers == {}


def test_nasa_inject_auth_uses_policy():
    adapter = NASAAdapter(api_key="DEMO_KEY", delay=0.0)
    _, params, headers = adapter.inject_auth("https://x", {}, {})
    assert params == {"api_key": "DEMO_KEY"}
    assert headers == {}

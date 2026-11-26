import pytest

from gossamer.research_providers import OpenAlexAdapter
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

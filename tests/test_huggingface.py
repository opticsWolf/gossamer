"""Tests for the HuggingFace Hub adapters (models + papers, read-only).

Offline throughout (``httpx.get`` is patched); the live shape is pinned by
the vendored fixtures in ``tests/test_rust_parity_huggingface.py``. One
slow-marked test hits the real Hub API.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gossamer.research_categories import (
    _ADAPTER_FACTORIES,
    classify,
)
from gossamer.research_providers import (
    HuggingFaceAdapter,
    HuggingFacePapersAdapter,
)
from gossamer.search_providers import ProviderRateLimitError

FIX = Path(__file__).parent / "fixtures"
MODELS_SEARCH = json.loads((FIX / "huggingface_models_search.json").read_text())
MODEL_FETCH = json.loads((FIX / "huggingface_model_fetch.json").read_text())
PAPERS_SEARCH = json.loads((FIX / "huggingface_papers_search.json").read_text())

CARD_MD = "# Demo model\n\nA tiny test card.\n"


def _json_response(payload, status=200):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _text_response(text, status=200):
    response = MagicMock()
    response.status_code = status
    response.text = text
    response.raise_for_status.return_value = None
    return response


def _error_response(status):
    response = MagicMock()
    response.status_code = status
    response.raise_for_status.side_effect = _http_error(status)
    return response


def _http_error(status):
    import httpx

    request = httpx.Request("GET", "https://huggingface.co/api/models")
    return httpx.HTTPStatusError("err", request=request,
                                 response=MagicMock(status_code=status))


# ── metadata / auth ─────────────────────────────────────────────


def test_metadata_and_optional_token():
    adapter = HuggingFaceAdapter(delay=0.0)
    assert adapter.name == "huggingface"
    assert adapter.domain == "scholarly"
    assert adapter.requires_key is False
    _, _, headers = HuggingFaceAdapter(
        delay=0.0, api_token="hf-test",
    ).inject_auth("https://example.org", {}, {})
    assert headers["Authorization"] == "Bearer hf-test"


def test_token_is_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("GOSSAMER_HF_TOKEN", "env-token")
    _, _, headers = HuggingFaceAdapter(delay=0.0).inject_auth(
        "https://example.org", {}, {},
    )
    assert headers["Authorization"] == "Bearer env-token"


def test_papers_adapter_is_search_only_metadata():
    adapter = HuggingFacePapersAdapter(delay=0.0)
    assert adapter.name == "huggingface-papers"
    assert adapter.domain == "scholarly"
    assert adapter.requires_key is False
    with pytest.raises(NotImplementedError, match="search-only"):
        adapter.fetch("2006.11239")


# ── models search ───────────────────────────────────────────────


@patch("gossamer.research_providers.httpx.get")
def test_models_search_params_and_records(mock_get):
    mock_get.return_value = _json_response(MODELS_SEARCH)

    records = HuggingFaceAdapter(delay=0.0).search("stable diffusion", max_results=5)

    request = mock_get.call_args
    assert request.args[0] == "https://huggingface.co/api/models"
    assert request.kwargs["params"] == {
        "search": "stable diffusion",
        "sort": "downloads",
        "direction": "-1",
        "limit": 5,
    }
    assert len(records) == 2
    first = records[0]
    assert first["source"] == "huggingface"
    assert first["id"] == "stabilityai/stable-diffusion-xl-base-1.0"
    assert first["url"].startswith("https://huggingface.co/")
    assert "downloads" in first["snippet"]
    assert first["fields"]["huggingface"]["license"] == "openrail++"
    assert "2307.01952" in first["fields"]["huggingface"]["arxiv_ids"]
    assert json.loads(first["raw"])["id"] == first["id"]


@patch("gossamer.research_providers.httpx.get")
def test_models_search_zero_limit_skips_http(mock_get):
    assert HuggingFaceAdapter(delay=0.0).search("x", max_results=0) == []
    mock_get.assert_not_called()


@patch("gossamer.research_providers.httpx.get")
def test_models_search_non_list_response_rejected(mock_get):
    mock_get.return_value = _json_response({"models": []})
    with pytest.raises(ValueError, match="non-list"):
        HuggingFaceAdapter(delay=0.0).search("x")


@patch("gossamer.research_providers.httpx.get")
def test_models_search_429_keyless_names_token(mock_get):
    mock_get.return_value = _error_response(429)
    with pytest.raises(ProviderRateLimitError, match="GOSSAMER_HF_TOKEN"):
        HuggingFaceAdapter(delay=0.0).search("x")


# ── models fetch (record + card) ────────────────────────────────


@patch("gossamer.research_providers.httpx.get")
def test_model_fetch_returns_record_and_card(mock_get):
    mock_get.side_effect = [
        _json_response(MODEL_FETCH),
        _text_response(CARD_MD),
    ]
    (record,) = HuggingFaceAdapter(delay=0.0).fetch(
        "stabilityai/stable-diffusion-xl-base-1.0",
    )
    api_call = mock_get.call_args_list[0]
    assert api_call.args[0].endswith("/models/stabilityai/stable-diffusion-xl-base-1.0")
    card_call = mock_get.call_args_list[1]
    assert card_call.args[0].endswith("/raw/main/README.md")
    assert record["card"] == CARD_MD
    assert record["card_url"].endswith("/raw/main/README.md")
    assert record["card_truncated"] is False
    assert len(record["fields"]["huggingface"]["siblings"]) > 10


@patch("gossamer.research_providers.httpx.get")
def test_model_fetch_accepts_hub_url_and_falls_back_to_master(mock_get):
    mock_get.side_effect = [
        _json_response(MODEL_FETCH),
        _text_response("", status=404),
        _text_response(CARD_MD),
    ]
    (record,) = HuggingFaceAdapter(delay=0.0).fetch(
        "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0",
    )
    assert mock_get.call_args_list[2].args[0].endswith("/raw/master/README.md")
    assert record["card"] == CARD_MD


@patch("gossamer.research_providers.httpx.get")
def test_model_fetch_truncates_long_cards(mock_get):
    mock_get.side_effect = [
        _json_response(MODEL_FETCH),
        _text_response("c" * (HuggingFaceAdapter.CARD_MAX_CHARS + 10)),
    ]
    (record,) = HuggingFaceAdapter(delay=0.0).fetch("org/model")
    assert len(record["card"]) == HuggingFaceAdapter.CARD_MAX_CHARS
    assert record["card_truncated"] is True


@patch("gossamer.research_providers.httpx.get")
def test_model_fetch_gated_repo_keeps_metadata_card_empty(mock_get):
    mock_get.side_effect = [_json_response(MODEL_FETCH), _error_response(401)]
    (record,) = HuggingFaceAdapter(delay=0.0).fetch("org/gated")
    assert record["card"] == ""
    assert record["id"] == MODEL_FETCH["id"]


@patch("gossamer.research_providers.httpx.get")
def test_model_fetch_rejects_bad_ids_without_http(mock_get):
    adapter = HuggingFaceAdapter(delay=0.0)
    with pytest.raises(ValueError, match="org/model"):
        adapter.fetch("not-a-model-id")
    with pytest.raises(ValueError, match="huggingface-papers"):
        adapter.fetch("https://huggingface.co/papers/2006.11239")
    mock_get.assert_not_called()


@patch("gossamer.research_providers.httpx.get")
def test_model_fetch_sends_bearer_token(mock_get):
    mock_get.side_effect = [_json_response(MODEL_FETCH), _text_response(CARD_MD)]
    HuggingFaceAdapter(delay=0.0, api_token="hf-test").fetch("org/model")
    for call in mock_get.call_args_list:
        assert call.kwargs["headers"]["Authorization"] == "Bearer hf-test"


# ── papers search ───────────────────────────────────────────────


@patch("gossamer.research_providers.httpx.get")
def test_papers_search_records_carry_arxiv_id(mock_get):
    mock_get.return_value = _json_response(PAPERS_SEARCH)
    records = HuggingFacePapersAdapter(delay=0.0).search("diffusion", max_results=3)
    request = mock_get.call_args
    assert request.args[0] == "https://huggingface.co/api/papers/search"
    assert request.kwargs["params"] == {"q": "diffusion", "limit": 3}
    first = records[0]
    assert first["source"] == "huggingface-papers"
    assert first["arxiv_id"] == first["id"] == "2006.11239"
    assert first["url"] == "https://huggingface.co/papers/2006.11239"
    assert "Denoising Diffusion" in first["title"]
    assert first["authors"].startswith("Jonathan Ho")
    assert json.loads(first["raw"])["paper"]["id"] == "2006.11239"


# ── wiring ──────────────────────────────────────────────────────


def test_categories_wire_both_providers():
    category = classify("hugging face model card for stable diffusion")
    assert category.name == "scholarly"
    assert "huggingface" in category.providers
    assert "huggingface-papers" in category.providers
    assert "huggingface" in _ADAPTER_FACTORIES
    assert "huggingface-papers" in _ADAPTER_FACTORIES
    assert _ADAPTER_FACTORIES["huggingface"].endswith("HuggingFaceAdapter")


# ── live (network; never gate CI) ──────────────────────────────


@pytest.mark.slow
def test_huggingface_live_search_fetch_papers():
    adapter = HuggingFaceAdapter(delay=1.0)
    records = adapter.search("stable diffusion", max_results=2)
    assert records and records[0]["source"] == "huggingface"
    assert "/" in records[0]["id"]
    (fetched,) = adapter.fetch(records[0]["id"])
    assert fetched["id"] == records[0]["id"]
    assert fetched["card_url"].endswith("README.md")
    papers = HuggingFacePapersAdapter(delay=1.0).search("diffusion", max_results=2)
    assert papers and papers[0]["arxiv_id"]

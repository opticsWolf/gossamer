from gossamer.research_categories import _service_provider_factory_path, _service_unknown_provider_message, _make_adapter


def test_provider_factory_table():
    assert _service_provider_factory_path("openalex") == "gossamer.research_providers.OpenAlexAdapter"
    assert _service_provider_factory_path("huggingface-papers") == "gossamer.research_providers.HuggingFacePapersAdapter"
    assert _service_provider_factory_path("unknown") is None
    assert _service_unknown_provider_message("unknown") == "no adapter registered for provider 'unknown'"
    try:
        _make_adapter("unknown")
    except ValueError as exc:
        assert str(exc) == "no adapter registered for provider 'unknown'"
    else:
        raise AssertionError("expected ValueError")

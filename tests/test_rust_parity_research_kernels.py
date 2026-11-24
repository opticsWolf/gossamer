from gossamer import research_providers as r, search

def test_json_fallback():
    assert r._json_fallback(None) == (None, None)
    assert r._json_fallback("x") == ("x", None)
    assert r._service_json_fallback(5) == ("5", "5")
    assert r._service_json_fallback((1, 2)) == ("(1, 2)", None)
    assert r._service_json_fallback({"a": 1}) == ("{'a': 1}", '{"a": 1}')

def test_provider_indices():
    names = ["google", "duckduckgo", "bing"]
    assert search._service_provider_indices("ddg", names) == [1, 0, 2]
    assert search._service_provider_indices(None, names) == [0, 1, 2]

from gossamer import search as s

def test_dedup_results():
    rows = [
        {"url": "https://WWW.Example.com/a?x=1"},
        {"url": "https://example.com/a?x=1"},
        {"url": "not a url"},
    ]
    assert len(s._service_dedup_results(rows)) == 2

def test_fallback_note():
    assert s._service_fallback_note("ddg", ["google", "duckduckgo"]) is None
    assert s._service_fallback_note("bing", ["google"]) == {
        "requested": "bing", "reason": "provider not registered", "used": ["google"]
    }

def test_resolve_order():
    assert s._service_resolve_order("ddg", ["google", "duckduckgo"]) == ["duckduckgo", "google"]
    assert s._service_resolve_order(None, ["google", "duckduckgo"]) == ["google", "duckduckgo"]

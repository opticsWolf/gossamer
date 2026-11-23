from gossamer import research_providers as r

def test_normalize_research():
    rows = [
        {"url": "https://WWW.Example.com/a?x=1", "title": "A"},
        {"url": "https://example.com/a?x=1", "title": "dup"},
        {"title": "no url"},
    ]
    assert len(r._service_normalize_research(rows)) == 2
    assert len(r._service_normalize_research(rows, 1)) == 1

def test_research_order():
    assert r._service_research_order(["google", "duckduckgo"], "ddg") == ["duckduckgo", "google"]

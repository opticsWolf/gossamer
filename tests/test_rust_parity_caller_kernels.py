from gossamer import agent_tools, search, search_providers

def test_inspect_request():
    out = agent_tools._service_inspect_request("https://x.test", "auto", None, -1, 0, False, "dir")
    assert out["offset"] == 0 and out["max_chunks"] == 1 and out["route"] == "store"

def test_extract_validation():
    assert agent_tools._service_extract_validation("bad", False, False, False)["error"].startswith("tables_as")
    assert agent_tools._service_extract_validation("json", True, False, False)["error"].startswith("include_images=True")
    assert agent_tools._service_extract_validation("json", True, True, True)["error"].startswith("include_images cannot")
    assert agent_tools._service_extract_validation("json", False, False, False) is None

def test_batch_plan():
    out = agent_tools._service_batch_plan(
        ["https://WWW.Example.com/a", "https://example.com/a", "https://example.com/b"],
        ["https://example.com/b"], [], "auto",
    )
    assert out["pending"] == ["https://example.com/a"]
    assert out["cached"] == ["https://example.com/b"]
    assert agent_tools._service_batch_plan(None, [], [], "auto")["error"].startswith("Batch inspection")

def test_rate_limit_normalization():
    out = search_providers._service_normalize_rate_limit(2.0, 1.0, 0.5)
    assert out["search_interval"] == 2.0 and out["fetch_interval"] == 1.0 and out["jitter"] == 0.5
    out = search_providers._service_normalize_rate_limit(None, None, None)
    assert out["search_interval"] == 1.0 and out["quota"] is None

def test_search_routes_and_cache_key():
    assert search._service_web_search_route(True) == "search_web"
    assert search._service_web_search_route(False) == "research"
    a = search._service_search_cache_key("  Rust  LANG ", 5, "ddg", False)
    b = search._service_search_cache_key("rust lang", 5, "duckduckgo", False)
    assert a == b

from gossamer import agent_tools

def test_check_sources_plan():
    out = agent_tools._service_check_sources_plan("status", [
        "https://WWW.Example.com/a", {"href": "https://example.com/a"}, 5, {"url": "https://example.com/b"}
    ])
    assert out == {"urls": ["https://WWW.Example.com/a", "https://example.com/b"], "probe": "head"}

def test_check_sources_errors():
    assert agent_tools._service_check_sources_plan("bad", [], "list")["error"].startswith("Unknown check_sources")
    assert agent_tools._service_check_sources_plan("status", 5, "int")["error"].startswith("urls must be")

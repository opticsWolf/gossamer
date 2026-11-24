import json
from gossamer import agent_tools

def test_inspect_route():
    assert agent_tools._service_inspect_route(True, "dir") == "structured"
    assert agent_tools._service_inspect_route(False, "dir") == "store"
    assert agent_tools._service_inspect_route(False, None) == "markdown"

def test_tables_as_error():
    assert agent_tools._service_tables_as_error() == {
        "error": "tables_as must be 'markdown', 'csv' or 'json'"
    }

def test_tool_arguments():
    out = agent_tools._service_tool_arguments("web_search", {"query": "rust"})
    assert out["query"] == "rust"
    assert out["max_results"] == 5
    assert agent_tools._service_tool_arguments("bogus", {}) is None

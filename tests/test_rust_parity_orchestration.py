from gossamer import agent_tools, cli, document

def test_payload_skeleton():
    out = document._service_payload_skeleton("# hi", "https://x.test", {}, 5)
    assert out["pages"][0]["markdown"] == "# hi"

def test_tool_envelope():
    assert agent_tools._service_tool_envelope("web_search", {"query": "x"}, {"ok": True}) == {
        "tool": "web_search", "arguments": {"query": "x"}, "result": {"ok": True}
    }

def test_route():
    assert cli._service_route(" INSPECT ") == "inspect"
    assert cli._service_route("bogus") is None

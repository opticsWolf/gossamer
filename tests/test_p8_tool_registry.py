"""P8: one tool registry drives every surface.

``TOOL_REGISTRY`` (agent_tools) is the only place a tool is defined; the
LLM function-calling definitions, the MCP server tools, and the
``execute_tool`` dispatcher all derive from it, so the three entry
points cannot drift.
"""

import inspect

import pytest

from stitch_web_researcher.agent_tools import (
    TOOL_REGISTRY,
    WebResearcherToolbox,
)

REGISTRY_NAMES = {spec.name for spec in TOOL_REGISTRY}


def _toolbox(tmp_path) -> WebResearcherToolbox:
    # respect_robots=False: nothing here performs real fetches, but the
    # toolbox must stay network-free by construction (S4).
    return WebResearcherToolbox(
        cache_dir=str(tmp_path / "cache"),
        domain_delay=0.0,
        ddgs_delay=0.0,
        respect_robots=False,
    )


class TestRegistryShape:
    def test_registry_lists_all_tools(self):
        # P8: search_web+research -> web_search; extract_document_structured
        # + inspect_html_structured folded into their base tools; the cache
        # trio + get_stats replaced by manage_cache. 7 tools total.
        assert REGISTRY_NAMES == {
            "web_search",
            "inspect_html_page",
            "batch_inspect_pages",
            "extract_document",
            "discover_resources",
            "crawl",
            "manage_cache",
        }

    def test_every_spec_method_exists(self, tmp_path):
        tb = _toolbox(tmp_path)
        for spec in TOOL_REGISTRY:
            assert callable(getattr(tb, spec.method)), spec.method

    def test_spec_params_exist_on_methods(self, tmp_path):
        """Registry parameter names must match the method signatures —
        execute_tool(**kwargs) would otherwise raise TypeError."""
        tb = _toolbox(tmp_path)
        for spec in TOOL_REGISTRY:
            sig = inspect.signature(getattr(tb, spec.method))
            for p in spec.params:
                assert p.name in sig.parameters, (spec.name, p.name)


class TestLlmDefinitions:
    def test_definitions_match_registry(self, tmp_path):
        tb = _toolbox(tmp_path)
        defs = tb.get_llm_definitions()
        assert [d["function"]["name"] for d in defs] == [
            spec.name for spec in TOOL_REGISTRY
        ]
        by_name = {d["function"]["name"]: d["function"]["parameters"] for d in defs}
        for spec in TOOL_REGISTRY:
            params = by_name[spec.name]
            assert set(params["properties"]) == {p.name for p in spec.params}
            assert set(params["required"]) == {
                p.name for p in spec.params if p.required
            }

    def test_returned_definitions_are_copies(self, tmp_path):
        """Callers may mutate the returned list without poisoning the
        registry (preserves the old deepcopy contract)."""
        tb = _toolbox(tmp_path)
        defs = tb.get_llm_definitions()
        defs[0]["function"]["name"] = "mutated"
        again = tb.get_llm_definitions()
        assert again[0]["function"]["name"] == TOOL_REGISTRY[0].name


class TestExecuteTool:
    def test_dispatch_matches_direct_calls(self, tmp_path):
        tb = _toolbox(tmp_path)
        # manage_cache dispatches to the real cache methods (same return
        # shapes the old dedicated tools returned).
        assert tb.execute_tool("manage_cache") == tb.prune_cache()
        assert tb.execute_tool("manage_cache", {"action": "reset"}) == tb.reset_visited()
        assert (
            "cache_cleared" in tb.execute_tool("manage_cache", {"action": "clear"})
        )

    def test_defaults_come_from_registry(self, tmp_path):
        """Omitted optional parameters use the registry defaults (the
        same values every surface advertises)."""
        tb = _toolbox(tmp_path)
        # search_web requires query; max_results/provider must fall back
        # to the registry defaults, so only query is missing.
        with pytest.raises(TypeError, match="query"):
            tb.execute_tool("web_search")

    def test_unknown_tool_raises_with_valid_names(self, tmp_path):
        tb = _toolbox(tmp_path)
        with pytest.raises(ValueError, match="Unknown tool") as exc:
            tb.execute_tool("no_such_tool")
        for spec in TOOL_REGISTRY:
            assert spec.name in str(exc.value)


class TestMcpSurface:
    def test_mcp_tools_match_registry(self):
        import asyncio

        try:
            from stitch_web_researcher.mcp_server import build_server
        except ImportError:
            pytest.skip("mcp not installed")
        server = build_server()
        tools = {t.name for t in asyncio.run(server.list_tools())}
        assert tools == REGISTRY_NAMES

    def test_mcp_required_params_match_registry(self):
        import asyncio

        try:
            from stitch_web_researcher.mcp_server import build_server
        except ImportError:
            pytest.skip("mcp not installed")
        server = build_server()
        tools = {t.name: t for t in asyncio.run(server.list_tools())}
        for spec in TOOL_REGISTRY:
            schema = tools[spec.name].input_schema
            assert set(schema.get("required", [])) == {
                p.name for p in spec.params if p.required
            }
            assert set(schema["properties"]) == {p.name for p in spec.params}

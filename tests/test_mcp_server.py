"""Tests for the MCP server layer.

Skipped entirely when the optional `mcp` dependency is not installed.
"""

import json

import pytest

mcp = pytest.importorskip("mcp")

from stitch_web_researcher import mcp_server  # noqa: E402

EXPECTED_TOOLS = {
    "web_search",
    "inspect_html_page",
    "batch_inspect_pages",
    "extract_document",
    "discover_resources",
    "focused_discovery",
    "manage_cache",
    # research_by_category is a category-aware overlay; it also returns the
    # live taxonomy when called with no query.
    "research_by_category",
    "export_citations",
    # check_sources probes source reachability without full fetches (Plan ws2).
    "check_sources",
}


def _run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("STITCH_DOMAIN_DELAY", "0")
    monkeypatch.setenv("STITCH_DDGS_DELAY", "0")
    # S4: MCP tests mock the fetch layer with fake example.com URLs; no
    # live robots.txt probe may run (robots compliance is covered in
    # tests/test_s4_robots.py).
    monkeypatch.setenv("STITCH_RESPECT_ROBOTS", "0")
    monkeypatch.setattr(mcp_server, "reset_toolbox", mcp_server.reset_toolbox)
    mcp_server.reset_toolbox()
    yield mcp_server.build_server()
    mcp_server.reset_toolbox()


class TestRegistration:
    def test_all_tools_registered(self, server):
        tools = _run(server.list_tools())
        assert {t.name for t in tools} == EXPECTED_TOOLS

    def test_every_tool_has_description_and_schema(self, server):
        for t in _run(server.list_tools()):
            assert t.description, f"{t.name} lacks a description"
            assert "properties" in t.input_schema
            required = t.input_schema.get("required", [])
            if t.name == "web_search":
                assert "query" in required


class TestToolCalls:
    def test_inspect_page_via_mcp(self, server):
        from unittest.mock import patch

        tb = mcp_server.get_toolbox()
        with patch.object(
            tb._fetch, "_fetch_html",
            return_value=("hello", [("https://example.com/x", "x")], {}, "static"),
        ):
            result = _run(
                server.call_tool(
                    "inspect_html_page", {"url": "https://example.com/mcp-test"}
                )
            )
        text = result[0][0].text if isinstance(result, tuple) else result.content[0].text
        data = json.loads(text)
        assert data["markdown"] == "hello"
        assert data["fetch_method"] == "static"


class TestEnvConfig:
    def test_env_overrides_flow_into_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "c"))
        monkeypatch.setenv("STITCH_MAX_TOKENS", "1234")
        monkeypatch.setenv("STITCH_MODEL_NAME", "claude-3-sonnet")
        monkeypatch.setenv("STITCH_MAX_CONCURRENCY", "3")

        config = mcp_server._config_from_env()
        assert config.max_tokens == 1234
        assert config.model_name == "claude-3-sonnet"
        assert config.max_concurrency == 3

    def test_cache_max_bytes_env_knob(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "c"))
        monkeypatch.setenv("STITCH_CACHE_MAX_BYTES", "5242880")
        assert mcp_server._config_from_env().cache_max_bytes == 5242880

    def test_cache_max_bytes_defaults_unlimited(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "c"))
        monkeypatch.delenv("STITCH_CACHE_MAX_BYTES", raising=False)
        assert mcp_server._config_from_env().cache_max_bytes == 0

    def test_guard_env_knobs_flow_into_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "c"))
        monkeypatch.setenv("STITCH_GUARD_ENABLED", "1")
        monkeypatch.setenv("STITCH_GUARD_MODE", "block")
        monkeypatch.setenv("STITCH_GUARD_SCOPES", "all")
        monkeypatch.setenv("STITCH_GUARD_THRESHOLD", "0.55")
        monkeypatch.setenv("STITCH_GUARD_MAX_CHUNKS", "12")

        g = mcp_server._config_from_env().guard
        assert g is not None
        assert g.enabled and g.mode == "block"
        assert g.threshold == 0.55
        assert g.max_chunks == 12
        assert "search_results" in g.scopes  # "all" shorthand

    def test_guard_env_off_by_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "c"))
        for var in (
            "STITCH_GUARD_ENABLED",
            "STITCH_GUARD_MODE",
            "STITCH_GUARD_SCOPES",
            "STITCH_GUARD_THRESHOLD",
            "STITCH_GUARD_MAX_CHUNKS",
        ):
            monkeypatch.delenv(var, raising=False)
        assert mcp_server._config_from_env().guard is None

    def test_singleton_is_reused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "c"))
        mcp_server.reset_toolbox()
        assert mcp_server.get_toolbox() is mcp_server.get_toolbox()
        mcp_server.reset_toolbox()

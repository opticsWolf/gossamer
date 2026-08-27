"""Tests for the MCP server layer.

Skipped entirely when the optional `mcp` dependency is not installed.
"""

import json

import pytest

mcp = pytest.importorskip("mcp")

from stitch_web_researcher import mcp_server  # noqa: E402

EXPECTED_TOOLS = {
    "search_web",
    "inspect_html_page",
    "batch_inspect_pages",
    "extract_document",
    "extract_document_structured",
    "inspect_html_structured",
    "clear_cache",
    "reset_visited",
    "get_stats",
}


def _run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("STITCH_DOMAIN_DELAY", "0")
    monkeypatch.setenv("STITCH_DDGS_DELAY", "0")
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
            if t.name == "search_web":
                assert "query" in required


class TestToolCalls:
    def test_get_stats_roundtrip(self, server):
        result = _run(server.call_tool("get_stats", {}))
        text = result[0][0].text if isinstance(result, tuple) else result.content[0].text
        data = json.loads(text)
        assert data["visited_urls_count"] == 0
        assert "hit_rate" in data["cache"]

    def test_inspect_page_via_mcp(self, server):
        from unittest.mock import patch

        tb = mcp_server.get_toolbox()
        with patch.object(
            tb,
            "_fetch_html",
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

    def test_singleton_is_reused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STITCH_CACHE_DIR", str(tmp_path / "c"))
        mcp_server.reset_toolbox()
        assert mcp_server.get_toolbox() is mcp_server.get_toolbox()
        mcp_server.reset_toolbox()
